"""Independent checks applied to every published optimizer layout."""
from collections import Counter
import math

WORK_JOBS = {"伐木", "采矿", "种植", "锻造", "工艺", "炼丹", "研究", "建造", "操练"}


def _worker_context(snapshot, options):
    """Resolve identities independently of the optimizer; never trust layout radii."""
    if "workers" not in snapshot and not snapshot.get("workerConstraintVersion"):
        return None
    people = {str(w["id"]): w for w in snapshot.get("workers", [])}
    if len(people) != len(snapshot.get("workers", [])):
        raise ValueError("人员校验失败：存档人物编号重复")
    requests = options.get("workerAssignments")
    if requests is None:
        requests = snapshot.get("defaultWorkerAssignments")
    if requests is None:
        requests = [{"workerId": w["id"], "jobType": w["suggestedJobType"]}
                    for w in people.values() if w.get("inMainCity") and w.get("selectable")
                    and w.get("suggestedJobType")]
    if not isinstance(requests, list):
        raise ValueError("人员校验失败：人员分工必须为列表")
    chosen = {}
    for request in requests:
        worker_id, job = str(request.get("workerId")), request.get("jobType")
        person = people.get(worker_id)
        if worker_id in chosen or not person or not person.get("inMainCity") or not person.get("selectable"):
            raise ValueError("人员校验失败：人物重复、不存在或不是可规划的主城人员")
        if job not in WORK_JOBS:
            raise ValueError("人员校验失败：未知工种 " + str(job))
        radius = person.get("rangePixels")
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
            raise ValueError(f"人员校验失败：{person.get('name', worker_id)} 的真实工作范围未确定")
        chosen[worker_id] = job
    main = {key: p for key, p in people.items() if p.get("inMainCity")}
    return {"people": people, "main": main, "chosen": chosen}


def worker_coverage(snapshot, layout, *, baseline=False):
    """Independent geometric coverage, separate from aura and production rates."""
    people = {str(w["id"]): w for w in snapshot.get("workers", [])}
    facilities = layout.get("facilities", snapshot.get("facilities", []))
    meta = {(int(f["template"]), int(f["ownerId"])): f.get("jobType")
            for f in snapshot.get("facilities", [])}
    meta.update({(int(g["template"]), int(g["ownerId"])): g.get("jobType")
                 for g in snapshot.get("facilityGroups", [])})
    workers = layout.get("workers", [])
    eligible = []
    by_job = Counter()
    unknown = 0
    for w in workers:
        person = people.get(str(w.get("workerId", w.get("id"))))
        if not person:
            continue
        job = w.get("jobType")
        if baseline and not job:
            unknown += 1
        if job not in WORK_JOBS or (not baseline and w.get("fixed")):
            continue
        radius = person.get("rangePixels")
        if not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
            continue
        eligible.append((w, person, radius))
        by_job[job] += 1
    coverage = []
    for f in facilities:
        job = meta.get((int(f["template"]), int(f["ownerId"])))
        sources = [str(p["id"]) for w, p, radius in eligible
                   if w["jobType"] == job
                   and ((w["x"] - f["x"]) * 100) ** 2 + ((w["y"] - f["y"]) * 100) ** 2 <= radius ** 2]
        coverage.append({"facilityId": f["id"], "jobType": job, "workerIds": sources})
    uncovered = [c["facilityId"] for c in coverage if not c["workerIds"]]
    warnings = []
    if baseline and unknown:
        warnings.append(f"{unknown} 名人员未保存明确执行工种；自动状态只显示站位和范围，不推断当前工作覆盖。")
    return {"validated": False, "coveredFacilities": len(coverage) - len(uncovered),
            "totalFacilities": len(coverage), "uncoveredFacilityIds": uncovered,
            "workerCount": len(eligible), "byJobType": dict(by_job),
            "facilityCoverage": coverage, "warnings": warnings}


def footprint(building):
    w, h = int(building.get("width", 1)), int(building.get("height", 1))
    x, y = int(building["x"]), int(building["y"])
    x0, y0 = x - (1 if w == 3 else 0), y - (1 if h == 3 else 0)
    return {(x0 + dx, y0 + dy) for dx in range(w) for dy in range(h)}


def effective(value, tier):
    # Match game Math.round((10 * raw) * (1 + tier * 0.15)) / 10 exactly.
    scaled = (10 * float(value)) * (1 + tier * 0.15)
    integer = math.floor(scaled)
    return (integer + int(scaled - integer >= 0.5)) / 10


def validate_layout(snapshot, options, layout):
    if not layout or not layout.get("tribulation"):
        return
    bounds = snapshot["bounds"]
    workforce = _worker_context(snapshot, options)
    def integer(value, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value):
            raise ValueError(f"结果校验失败：{label} 必须为有限整数")
        return int(value)
    rect = options.get("rect") or {"xmin": bounds["xmin"], "ymin": bounds["ymin"],
                                   "width": bounds["xmax"] - bounds["xmin"] + 1,
                                   "height": bounds["ymax"] - bounds["ymin"] + 1}
    def in_city(p):
        return bounds["xmin"] <= p[0] <= bounds["xmax"] and bounds["ymin"] <= p[1] <= bounds["ymax"]
    def in_rect(p):
        return rect["xmin"] <= p[0] < rect["xmin"] + rect["width"] and rect["ymin"] <= p[1] < rect["ymin"] + rect["height"]
    occupied = set()
    for b in snapshot["buildings"]:
        if int(b["template"]) not in (66, 67):
            occupied.update(footprint(b))
    occupied.update((int(r["x"]), int(r["y"])) for r in snapshot.get("resources", []))
    def place(cells, name, fixed=False):
        if any(not in_city(p) for p in cells):
            raise ValueError(f"结果校验失败：{name} 超出已解锁区域")
        if not fixed and any(not in_rect(p) for p in cells):
            raise ValueError(f"结果校验失败：{name} 超出规划矩形")
        if cells & occupied:
            raise ValueError(f"结果校验失败：{name} 与其他建筑或设施重叠")
        occupied.update(cells)
    if workforce is not None:
        for worker_id, person in workforce["main"].items():
            if worker_id not in workforce["chosen"]:
                position = (integer(person["x"], "固定人员X"), integer(person["y"], "固定人员Y"))
                place({position}, "保持原位的人员 " + person.get("name", worker_id), fixed=True)
    trib = layout["tribulation"]
    integer(trib["x"], "渡劫台X")
    integer(trib["y"], "渡劫台Y")
    place(footprint({**trib, "width": 3, "height": 3}), "渡劫台")
    items = {i["id"]: i for i in snapshot["items"]}
    excluded = set(options.get("excludeItemIds", []))
    used = set()
    pads = layout.get("pedestals", [])
    cap = int(options.get("maxPedestals", sum(b["template"] == 67 for b in snapshot["buildings"])))
    if len(pads) > cap:
        raise ValueError("结果校验失败：置物台数量超过上限")
    available_tiers = Counter(int(b.get("tier", 0)) for b in snapshot["buildings"] if b["template"] == 67)
    new_count = max(0, cap - sum(available_tiers.values()))
    available_tiers[int(options.get("newPedestalTier", 0))] += new_count
    for p in pads:
        item_id = p["itemId"]
        if item_id not in items or item_id in used or item_id in excluded:
            raise ValueError("结果校验失败：灵物不存在、被排除或被重复使用")
        used.add(item_id)
        integer(p["x"], "置物台X")
        integer(p["y"], "置物台Y")
        tier = integer(p.get("tier", 0), "置物台等阶")
        available_tiers[tier] -= 1
        if available_tiers[tier] < 0:
            raise ValueError("结果校验失败：使用了不存在的置物台等阶配额")
        place({(p["x"], p["y"])}, "置物台")
    requests = options.get("facilityRequests")
    if requests is None:
        requests = [{"template": g["template"], "ownerId": g["ownerId"], "count": g["count"]}
                    for g in snapshot.get("facilityGroups", [])]
    requested = {(int(r["template"]), int(r["ownerId"])): int(r["count"]) for r in requests}
    expected = Counter((int(f["template"]), int(f["ownerId"])) for f in snapshot["facilities"])
    expected.update({k: v - expected[k] for k, v in requested.items()})
    actual = Counter((int(f["template"]), int(f["ownerId"])) for f in layout.get("facilities", []))
    if +expected != +actual:
        raise ValueError(f"结果校验失败：设施数量不符，期望 {dict(expected)}，得到 {dict(actual)}")
    owners = {int(b["id"]): b for b in snapshot["buildings"]}
    owner_counts = Counter(int(f["ownerId"]) for f in layout.get("facilities", []))
    for owner_id, count in owner_counts.items():
        owner = owners.get(owner_id)
        if owner is None:
            raise ValueError("结果校验失败：设施所属建筑不存在")
        tier = int(owner.get("tier", 0))
        normal_capacity = tier * 4 + 4 if owner.get("category") == "资源" else tier + 2
        capacity = int(owner.get("facilityCapacity", normal_capacity))
        if count > capacity:
            raise ValueError(f"结果校验失败：所属建筑 {owner_id} 的设施总数 {count} 超过上限 {capacity}")
    original = {str(f["id"]): f for f in snapshot["facilities"]}
    elements = {(int(f["template"]), int(f["ownerId"])): int(f["element"]) for f in snapshot["facilities"]}
    elements.update({(int(g["template"]), int(g["ownerId"])): int(g["element"]) for g in snapshot.get("facilityGroups", [])})
    facility_jobs = {(int(f["template"]), int(f["ownerId"])): f.get("jobType") for f in snapshot["facilities"]}
    facility_jobs.update({(int(g["template"]), int(g["ownerId"])): g.get("jobType") for g in snapshot.get("facilityGroups", [])})
    for f in layout.get("facilities", []):
        key = (int(f["template"]), int(f["ownerId"]))
        integer(f["x"], "设施X")
        integer(f["y"], "设施Y")
        if key not in elements or integer(f["element"], "设施五行") != elements[key]:
            raise ValueError("结果校验失败：设施属性与游戏模板不一致")
        if workforce is not None and (facility_jobs.get(key) not in WORK_JOBS
                                     or (f.get("jobType") is not None and f["jobType"] != facility_jobs[key])):
            raise ValueError("人员校验失败：设施工种与游戏模板不一致")
        fixed = key not in requested
        if fixed:
            old = original.get(str(f.get("id")))
            if old is None or (old["x"], old["y"]) != (f["x"], f["y"]):
                raise ValueError("结果校验失败：未参与规划的设施被移动")
        owner = owners.get(int(f["ownerId"]))
        if owner is None:
            raise ValueError("结果校验失败：设施所属建筑不存在")
        w, h = int(owner["width"]), int(owner["height"])
        min_x = owner["x"] - (4 if w == 3 else 3)
        max_x = owner["x"] + (4 if w >= 2 else 3)
        min_y = owner["y"] - (4 if h == 3 else 3)
        max_y = owner["y"] + (4 if h >= 2 else 3)
        if not (min_x <= f["x"] <= max_x and min_y <= f["y"] <= max_y):
            raise ValueError("结果校验失败：设施超出所属建筑范围")
        place({(f["x"], f["y"])}, f.get("name", "设施"), fixed)
    def field(x, y, element=None):
        return sum(effective(items[p["itemId"]]["qi"] if element is None else items[p["itemId"]]["elements"][element], int(p.get("tier", 0)))
                   for p in pads if abs(x-p["x"]) + abs(y-p["y"]) <= 3)
    d = field(trib["x"], trib["y"])
    s = sum(field(f["x"], f["y"], int(f["element"])) for f in layout.get("facilities", []))
    for key, calculated in (("D", d), ("S", s)):
        if not math.isfinite(float(layout[key])) or abs(float(layout[key]) - calculated) > 1e-5:
            raise ValueError(f"结果校验失败：{key} 报告 {layout[key]}，独立复算 {calculated}")
    if workforce is not None:
        workers = layout.get("workers")
        if not isinstance(workers, list):
            raise ValueError("人员校验失败：方案缺少人员站位")
        seen = set()
        for w in workers:
            worker_id = str(w.get("workerId"))
            if worker_id in seen or worker_id not in workforce["main"]:
                raise ValueError("人员校验失败：重复人物或加入了非主城人员")
            seen.add(worker_id)
            person = workforce["main"][worker_id]
            position = (integer(w["x"], "人员X"), integer(w["y"], "人员Y"))
            if worker_id in workforce["chosen"]:
                if w.get("fixed") or w.get("jobType") != workforce["chosen"][worker_id]:
                    raise ValueError("人员校验失败：工种与指定分工不一致")
                if w.get("rangePixels") != person["rangePixels"]:
                    raise ValueError("人员校验失败：工作范围与存档人物属性不一致")
                place({position}, "人员 " + person.get("name", worker_id))
            elif not w.get("fixed") or w.get("jobType") is not None or position != (person["x"], person["y"]):
                raise ValueError("人员校验失败：未参与规划的人员被移动或被分配工种")
        if seen != set(workforce["main"]):
            raise ValueError("人员校验失败：方案遗漏主城人员")
        report = worker_coverage(snapshot, layout)
        if report["uncoveredFacilityIds"]:
            missing = ", ".join(str(i) for i in report["uncoveredFacilityIds"][:12])
            raise ValueError("人员校验失败：有设施不在同工种人员的真实工作范围内：" + missing)
        report["validated"] = True
        return report


def validate_result(snapshot, options, result):
    if not isinstance(result, dict):
        raise ValueError("求解器未返回有效结果")
    for key in ("strict", "balanced"):
        coverage = validate_layout(snapshot, options, result.get(key))
        if coverage is not None:
            result[key]["workerCoverage"] = coverage
    strict, balanced = result.get("strict"), result.get("balanced")
    if strict and balanced and strict.get("tribulation") and balanced.get("tribulation"):
        if balanced["D"] + float(options.get("delta", 0)) < strict["D"] - 1e-6:
            raise ValueError("结果校验失败：折中方案超过允许的渡劫灵气损失")
