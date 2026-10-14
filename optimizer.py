"""Read-only aura evaluation and bounded, proof-aware layout optimization.

Coordinates are game anchors: size 3 is centred, size 2 extends right/down.
The tribulation platform receives the aura at its centre, not its footprint.
Nothing in this module reads or writes a game save.
"""
from __future__ import annotations

import math
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

_VENDOR = Path(__file__).with_name("vendor")
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

_WORK_TYPES = {"伐木", "采矿", "种植", "锻造", "工艺", "炼丹", "研究", "建造", "操练"}


def _worker_mode(snapshot):
    return "workers" in snapshot or _int(snapshot.get("workerConstraintVersion")) >= 1


def _worker_row(worker, job_type=None, fixed=False):
    return {"workerId": str(worker.get("id", worker.get("workerId"))), "name": worker.get("name", ""),
            "jobType": job_type, "x": worker["x"], "y": worker["y"],
            "rangePixels": worker.get("rangePixels"), "fixed": fixed}


def _covers(worker, cell, job_type):
    if worker.get("fixed") or not job_type or worker.get("jobType") != job_type:
        return False
    radius = float(worker.get("rangePixels") or 0)
    return ((worker["x"] - cell[0]) ** 2 + (worker["y"] - cell[1]) ** 2) * 10000 <= radius * radius


def _int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _scaled(value, tier=0):
    """Game expressions 3793..3798, preserving binary-float operation order."""
    scaled = (10 * float(value or 0)) * (1 + tier * 0.15)
    integer = math.floor(scaled)
    return integer + int(scaled - integer >= 0.5)


def _cells(building):
    if building.get("cells"):
        result = set()
        for cell in building["cells"]:
            if isinstance(cell, dict):
                result.add((_int(cell["x"]), _int(cell["y"])))
            else:
                result.add((_int(cell[0]), _int(cell[1])))
        return result
    x, y = _int(building["x"]), _int(building["y"])
    w, h = _int(building.get("width"), 1), _int(building.get("height"), 1)
    # Even footprints use their upper-left anchor; odd footprints are centred.
    x0, y0 = x - (w // 2 if w % 2 else 0), y - (h // 2 if h % 2 else 0)
    return {(x0 + dx, y0 + dy) for dx in range(w) for dy in range(h)}


def _group_key(row):
    return (str(row.get("template")), str(row.get("ownerId")))


def _current_layout(snapshot):
    buildings = snapshot.get("buildings", [])
    main = next((b for b in buildings if _int(b.get("template")) == 66), None)
    by_building = {str(i.get("placedBuildingId")): i["id"] for i in snapshot.get("items", [])
                   if i.get("placedBuildingId") is not None}
    pads = []
    for b in buildings:
        if _int(b.get("template")) == 67:
            pads.append({"x": b["x"], "y": b["y"], "tier": _int(b.get("tier")),
                         "itemId": b.get("itemId") or by_building.get(str(b.get("id"))),
                         "buildingId": b.get("id")})
    result = {"tribulation": {"x": main["x"], "y": main["y"]} if main else None,
              "pedestals": pads, "facilities": [dict(f) for f in snapshot.get("facilities", [])]}
    if _worker_mode(snapshot):
        result["workers"] = [_worker_row(w, job_type=w.get("currentTaskType"), fixed=True)
                             for w in snapshot.get("workers", []) if w.get("inMainCity")]
    return result


def evaluate(snapshot, layout=None):
    """Evaluate exactly the supplied layout, or the current parsed save.

    byElement is five totals; facilityValues contains each building's own value.
    Unknown/unoccupied pedestals contribute zero and are listed in warnings.
    """
    is_baseline = layout is None
    layout = _current_layout(snapshot) if is_baseline else layout
    item_by_id = {str(i["id"]): i for i in snapshot.get("items", [])}
    pads, warnings = [], []
    for pad in layout.get("pedestals", []):
        item = item_by_id.get(str(pad.get("itemId")))
        if item is None:
            if pad.get("itemId"):
                warnings.append("未知灵物: " + str(pad["itemId"]))
            continue
        tier = _int(pad.get("tier"))
        pads.append((_int(pad["x"]), _int(pad["y"]), _scaled(item.get("qi"), tier),
                     [_scaled(v, tier) for v in item.get("elements", [0] * 5)]))

    def aura(x, y, element=None):
        return sum(q if element is None else es[element]
                   for px, py, q, es in pads if abs(px - x) + abs(py - y) <= 3)

    centre = layout.get("tribulation")
    d = aura(_int(centre["x"]), _int(centre["y"])) if centre else 0
    totals, values = [0] * 5, []
    for f in layout.get("facilities", []):
        e = _int(f.get("element"), -1)
        if e not in range(5):
            continue
        val = aura(_int(f["x"]), _int(f["y"]), e)
        totals[e] += val
        values.append({"id": f.get("id"), "template": f.get("template"),
                       "ownerId": f.get("ownerId"), "element": e,
                       "x": f["x"], "y": f["y"], "value": val / 10})
    s = sum(totals)
    result = {"D": d / 10, "S": s / 10, "average": s / 10 / len(values) if values else 0,
              "byElement": [v / 10 for v in totals], "facilityValues": values, "warnings": warnings}
    if _worker_mode(snapshot):
        coverage = []
        for f in layout.get("facilities", []):
            ids = [w["workerId"] for w in layout.get("workers", []) if _covers(w, (f["x"], f["y"]), f.get("jobType"))]
            coverage.append({"facilityId": f.get("id"), "jobType": f.get("jobType"), "workerIds": ids})
        result["workerCoverage"] = {"validated": False, "totalFacilities": len(coverage),
                                     "coveredFacilities": sum(bool(c["workerIds"]) for c in coverage),
                                     "uncoveredFacilityIds": [c["facilityId"] for c in coverage if not c["workerIds"]],
                                     "facilityCoverage": coverage,
                                     "workerCount": sum(not w.get("fixed") for w in layout.get("workers", [])),
                                     "byJobType": dict(Counter(w["jobType"] for w in layout.get("workers", []) if not w.get("fixed"))),
                                     "warnings": []}
        if is_baseline:
            # Saved explicit tasks describe current operation, not a proposed reassignment.
            # Only this baseline path may count an unchanged worker with a known task.
            try:
                from .validation import worker_coverage
            except ImportError:
                from validation import worker_coverage
            result["workerCoverage"] = worker_coverage(snapshot, layout, baseline=True)
    return result


def _assignment(weights):
    """Rectangular maximum-weight assignment (Hungarian), rows <= columns."""
    n = len(weights)
    if not n:
        return [], 0
    m = len(weights[0])
    if n > m:
        return None, None
    u, v, p, way = [0] * (n + 1), [0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minimum, used = [float("inf")] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            row = weights[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = -row[j - 1] - u[i0] - v[j]
                    if cur < minimum[j]:
                        minimum[j], way[j] = cur, j0
                    if minimum[j] < delta:
                        delta, j1 = minimum[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [None] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result, sum(weights[i][j] for i, j in enumerate(result))


def _prepare(snapshot, options):
    bounds = snapshot.get("bounds", {})
    rect = options.get("rect") or {"xmin": bounds.get("xmin", 0), "ymin": bounds.get("ymin", 0),
                                  "width": bounds.get("xmax", 17) - bounds.get("xmin", 0) + 1,
                                  "height": bounds.get("ymax", 17) - bounds.get("ymin", 0) + 1}
    xmin, ymin = _int(rect.get("xmin")), _int(rect.get("ymin"))
    width, height = _int(rect.get("width")), _int(rect.get("height"))
    if width < 3 or height < 3 or width * height > 2500:
        raise ValueError("规划区域至少为 3×3，最多 2500 格。")
    if bounds and (xmin < bounds.get("xmin", xmin) or ymin < bounds.get("ymin", ymin)
                   or xmin + width - 1 > bounds.get("xmax", xmin + width - 1)
                   or ymin + height - 1 > bounds.get("ymax", ymin + height - 1)):
        raise ValueError("规划区域超出了存档主城边界。")
    area = {(x, y) for x in range(xmin, xmin + width) for y in range(ymin, ymin + height)}
    excluded = set(map(str, options.get("excludeItemIds", [])))
    items = [dict(i) for i in snapshot.get("items", []) if str(i["id"]) not in excluded]
    if len({str(i["id"]) for i in items}) != len(items):
        raise ValueError("库存灵物 ID 不唯一。")
    buildings = snapshot.get("buildings", [])
    by_id = {str(b.get("id")): b for b in buildings}
    existing_pads = [b for b in buildings if _int(b.get("template")) == 67]
    maximum = _int(options.get("maxPedestals"), len(existing_pads))
    if not 0 <= maximum <= 100:
        raise ValueError("最大灵台数量必须在 0 到 100 之间。")
    # Higher-tier platforms weakly improve every nonnegative aura field.
    if any(float(i.get("qi", 0)) < 0 or any(float(v) < 0 for v in i.get("elements", [])) for i in items):
        raise ValueError("检测到负灵气值；当前求解器的安全边界要求灵气非负。")
    pad_specs = [{"tier": _int(b.get("tier")), "buildingId": b.get("id")} for b in existing_pads]
    if maximum > len(pad_specs):
        tier = _int(options.get("newPedestalTier"))
        if not 0 <= tier <= 20:
            raise ValueError("新灵台等阶应在 0 到 20 之间。")
        pad_specs += [{"tier": tier} for _ in range(maximum - len(pad_specs))]
    pad_specs = sorted(pad_specs, key=lambda b: -b["tier"])[:min(maximum, len(items))]
    requests = options.get("facilityRequests")
    if requests is None:
        requests = [{"template": g[0], "ownerId": g[1], "count": count}
                    for g, count in Counter(_group_key(f) for f in snapshot.get("facilities", [])).items()]
    request_by_key = {}
    for r in requests:
        key = _group_key(r)
        if key in request_by_key:
            raise ValueError("同一父建筑的设施请求重复。")
        count = _int(r.get("count"))
        if count < 0 or count > 500:
            raise ValueError("设施数量应在 0 到 500 之间。")
        request_by_key[key] = count
    facilities_by_key = defaultdict(list)
    for f in snapshot.get("facilities", []):
        facilities_by_key[_group_key(f)].append(f)
    meta = {_group_key(g): g for g in snapshot.get("facilityGroups", [])}
    fixed_facilities = [dict(f, fixed=True) for f in snapshot.get("facilities", [])
                        if _group_key(f) not in request_by_key]
    warnings = []
    worker_enabled = _worker_mode(snapshot)
    selected_workers, fixed_workers = [], []
    if worker_enabled:
        source_workers = snapshot.get("workers", [])
        by_worker_id = {str(w["id"]): w for w in source_workers}
        if len(by_worker_id) != len(source_workers):
            raise ValueError("存档人员 ID 不唯一。")
        assignments = options.get("workerAssignments")
        if assignments is None:
            assignments = snapshot.get("defaultWorkerAssignments")
            if assignments is None:
                assignments = [{"workerId": w["id"], "jobType": w["suggestedJobType"]} for w in source_workers
                               if w.get("inMainCity") and w.get("selectable") and w.get("suggestedJobType")]
            warnings.append("未指定人员分工，使用存档解析器的规划建议；建议岗位不代表角色当前实际执行工作。")
        if not isinstance(assignments, list):
            raise ValueError("人员分工应为 workerAssignments 列表。")
        selected_ids = set()
        for assignment in assignments:
            worker_id, job = str(assignment.get("workerId")), assignment.get("jobType")
            if worker_id in selected_ids:
                raise ValueError("同一人员不能重复分配工种: " + worker_id)
            worker = by_worker_id.get(worker_id)
            if worker is None or not worker.get("inMainCity") or not worker.get("selectable"):
                raise ValueError("只能选择已招募、已部署在主城且可操作的人员: " + worker_id)
            if job not in _WORK_TYPES:
                raise ValueError("不支持的人员工种: " + str(job))
            radius = worker.get("rangePixels")
            if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
                raise ValueError("人员实际工作射程缺失或无效: " + worker_id)
            selected_workers.append(_worker_row(worker, job_type=job, fixed=False))
            selected_ids.add(worker_id)
        fixed_workers = [_worker_row(w, fixed=True) for w in source_workers
                         if w.get("inMainCity") and str(w["id"]) not in selected_ids]
    # The game shares one capacity across ALL facility templates of an owner.
    # Unrequested facilities remain in place and consume the same capacity.
    owner_totals = Counter(str(f.get("ownerId")) for f in fixed_facilities)
    for (_, owner_id), count in request_by_key.items():
        owner_totals[owner_id] += count
    for owner_id, count in owner_totals.items():
        owner = by_id.get(owner_id)
        if owner is None:
            raise ValueError("找不到设施父建筑: " + owner_id)
        tier = _int(owner.get("tier"))
        capacity = owner.get("facilityCapacity")
        if capacity is None:
            capacity = tier * 4 + 4 if owner.get("category") == "资源" else tier + 2
        capacity = _int(capacity)
        if count > capacity:
            name = owner.get("name", "建筑")
            raise ValueError(f"{name}（父建筑 {owner_id}）的设施总数为 {count}，超过当前等阶允许的 {capacity} 个；"
                             "同一父建筑的全部设施类型合计占用此上限，保留原位的设施也计算在内。")
    blocked = set()
    for b in buildings:
        if _int(b.get("template")) not in (66, 67):
            blocked.update(_cells(b))
    blocked.update((_int(r["x"]), _int(r["y"])) for r in snapshot.get("resources", []))
    for f in fixed_facilities:
        cell = (_int(f["x"]), _int(f["y"]))
        owner = by_id.get(str(f.get("ownerId")))
        if owner is None:
            raise ValueError("保留设施的父建筑不存在: " + str(f.get("id")))
        def reach(dimension):
            return (-4, 4) if dimension == 3 else (-3, 4) if dimension == 2 else (-3, 3)
        lx, hx = reach(_int(owner.get("width"), 1))
        ly, hy = reach(_int(owner.get("height"), 1))
        if not (owner["x"] + lx <= cell[0] <= owner["x"] + hx and owner["y"] + ly <= cell[1] <= owner["y"] + hy):
            raise ValueError("保留设施原位置超出其父建筑范围: " + str(f.get("id")))
        if cell in blocked:
            raise ValueError("保留设施原位置与固定建筑、资源或其他设施重叠: " + str(f.get("id")))
        if bounds and not (bounds["xmin"] <= cell[0] <= bounds["xmax"] and bounds["ymin"] <= cell[1] <= bounds["ymax"]):
            raise ValueError("保留设施原位置超出主城边界: " + str(f.get("id")))
        blocked.add(cell)
    for worker in fixed_workers:
        cell = (_int(worker["x"]), _int(worker["y"]))
        if cell in blocked:
            raise ValueError("未参与规划的人员原站位与固定建筑、设施、资源或另一人员重叠: " + worker["workerId"])
        if bounds and not (bounds["xmin"] <= cell[0] <= bounds["xmax"] and bounds["ymin"] <= cell[1] <= bounds["ymax"]):
            raise ValueError("未参与规划的人员原站位超出主城边界: " + worker["workerId"])
        blocked.add(cell)
    free = area - blocked
    groups = []
    for key, count in request_by_key.items():
        old = facilities_by_key.get(key, [])
        info = meta.get(key) or (old[0] if old else None)
        if info is None:
            raise ValueError("找不到设施组信息: " + ":".join(key))
        if count == 0:
            if old:
                warnings.append(f"{info.get('name', key[0])} / 父建筑 {key[1]}：原有 {len(old)} 个设施将收起。")
            continue
        owner = by_id.get(key[1])
        if owner is None:
            raise ValueError("找不到设施父建筑: " + key[1])
        size_x, size_y = _int(owner.get("width"), 3), _int(owner.get("height"), 3)
        lx, hx = (-4, 4) if size_x == 3 else (-3, 4) if size_x == 2 else (-3, 3)
        ly, hy = (-4, 4) if size_y == 3 else (-3, 4) if size_y == 2 else (-3, 3)
        ox, oy = _int(owner["x"]), _int(owner["y"])
        candidates = {(x, y) for x, y in free if ox + lx <= x <= ox + hx and oy + ly <= y <= oy + hy}
        element = _int(info.get("element"), -1)
        if element not in range(5):
            raise ValueError("设施五行类型未知: " + ":".join(key))
        if count > len(candidates):
            raise ValueError(f"{info.get('name', key[0])} 需要 {count} 格，但父建筑范围内仅有 {len(candidates)} 个可用格。")
        groups.append({"key": key, "count": count, "element": element, "old": old,
                       "name": info.get("name", key[0]), "template": info.get("template"),
                       "ownerId": info.get("ownerId"), "jobType": info.get("jobType"), "candidates": candidates})
    if worker_enabled:
        available_jobs = {w["jobType"] for w in selected_workers}
        for facility in list(groups) + fixed_facilities:
            job = facility.get("jobType")
            if not job or job not in _WORK_TYPES:
                raise ValueError("设施的实际工作类型缺失或未知: " + str(facility.get("name", facility.get("id", ""))))
            if job not in available_jobs:
                raise ValueError(f"{job}设施需要所选的同工种人员覆盖；当前未分配{job}人员（未参与规划的人员不会自动代替该工种）。")
    if 9 + sum(g["count"] for g in groups) + len(selected_workers) > len(free):
        raise ValueError("规划区域没有足够空间容纳渡劫台、指定设施和所选人员。")
    centres = []
    for x, y in sorted(free):
        footprint = {(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        if footprint <= free:
            candidates = {p for p in free - footprint if abs(p[0] - x) + abs(p[1] - y) <= 3}
            centres.append(((x, y), footprint, candidates))
    if not centres:
        raise ValueError("规划区域没有可摆放 3×3 渡劫台的空地。")
    if len(existing_pads) > maximum:
        warnings.append(f"现有 {len(existing_pads)} 座灵台，方案最多使用 {maximum} 座，其余需收起。")
    if maximum > len(existing_pads):
        warnings.append(f"上限超过现有灵台数；最多新增 {maximum - len(existing_pads)} 座等阶 {_int(options.get('newPedestalTier'))} 灵台。")
    return {"area": area, "free": free, "items": items, "pads": pad_specs,
            "centres": centres, "groups": groups, "fixedFacilities": fixed_facilities,
            "warnings": warnings, "rect": rect, "workerEnabled": worker_enabled,
            "workers": selected_workers, "fixedWorkers": fixed_workers}


def _aura_at(pads, items, point, element):
    x, y = point
    return sum(_scaled(items[p["itemId"]]["elements"][element], p["tier"])
               for p in pads if abs(p["x"] - x) + abs(p["y"] - y) <= 3)


def _worker_positions(data, worker, available):
    """Safe pruning for a sole worker of a job, including fixed facilities."""
    if sum(w["jobType"] == worker["jobType"] for w in data["workers"]) != 1:
        return sorted(available)
    groups = [g for g in data["groups"] if g["jobType"] == worker["jobType"]]
    fixed = [f for f in data["fixedFacilities"] if f.get("jobType") == worker["jobType"]]
    needed = sum(g["count"] for g in groups) + len(fixed)
    if not needed:
        return sorted(available)
    possible = set().union(*(g["candidates"] & available for g in groups)) if groups else set()
    possible.update((f["x"], f["y"]) for f in fixed)
    choices = []
    for cell in sorted(available):
        placed = dict(worker, x=cell[0], y=cell[1])
        if any(not _covers(placed, (f["x"], f["y"]), worker["jobType"]) for f in fixed):
            continue
        if sum(p != cell and _covers(placed, p, worker["jobType"]) for p in possible) >= needed:
            choices.append(cell)
    return choices


def _facility_from_group(group, number, cell):
    old = group["old"][number] if number < len(group["old"]) else {}
    return {"id": old.get("id", f"planned:{group['key'][0]}:{group['key'][1]}:{number}"),
            "template": group["template"], "ownerId": group["ownerId"], "element": group["element"],
            "name": group["name"], "x": cell[0], "y": cell[1], "fixed": False,
            "jobType": group.get("jobType")}


def _joint_support_plan(data, centre, pads, seconds=.35, hint=None):
    """Small CP model: with aura sources fixed, place staff and facilities jointly."""
    from ortools.sat.python import cp_model
    if data.get("cancelled", lambda: False)():
        return None
    occupied = {(centre[0] + dx, centre[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    occupied.update((p["x"], p["y"]) for p in pads)
    available = data["free"] - occupied
    if len(data["workers"]) + sum(g["count"] for g in data["groups"]) > len(available):
        return None
    model = cp_model.CpModel()
    by_cell, worker_vars, facility_vars = defaultdict(list), [], []
    for wi, worker in enumerate(data["workers"]):
        choices = []
        for cell in _worker_positions(data, worker, available):
            var = model.NewBoolVar(f"w{wi}_{cell}")
            choices.append(var)
            by_cell[cell].append(var)
            worker_vars.append((wi, cell, var))
        if not choices:
            return None
        model.AddExactlyOne(choices)
    worker_cover = {}
    def covering(job, cell):
        key = job, cell
        if key not in worker_cover:
            worker_cover[key] = [var for wi, p, var in worker_vars
                                 if _covers(dict(data["workers"][wi], x=p[0], y=p[1]), cell, job)]
        return worker_cover[key]
    items = {i["id"]: i for i in data["items"]}
    terms = []
    for gi, group in enumerate(data["groups"]):
        choices = []
        for cell in sorted(group["candidates"] & available):
            cover = covering(group["jobType"], cell)
            if not cover:
                continue
            var = model.NewBoolVar(f"f{gi}_{cell}")
            model.Add(sum(cover) >= var)
            by_cell[cell].append(var)
            choices.append(var)
            facility_vars.append((gi, cell, var))
            terms.append(_aura_at(pads, items, cell, group["element"]) * var)
        if len(choices) < group["count"]:
            return None
        model.Add(sum(choices) == group["count"])
    for fixed in data["fixedFacilities"]:
        cover = covering(fixed.get("jobType"), (fixed["x"], fixed["y"]))
        if not cover:
            return None
        model.Add(sum(cover) >= 1)
    for variables in by_cell.values():
        if len(variables) > 1:
            model.AddAtMostOne(variables)
    model.Maximize(sum(terms))
    if hint:
        staff_hints = {w["workerId"]: (w["x"], w["y"]) for w in hint.get("workers", [])}
        facility_hints = {(_group_key(f), (f["x"], f["y"])) for f in hint.get("facilities", [])}
        for wi, cell, var in worker_vars:
            model.AddHint(var, int(staff_hints.get(data["workers"][wi]["workerId"]) == cell))
        for gi, cell, var in facility_vars:
            model.AddHint(var, int((data["groups"][gi]["key"], cell) in facility_hints))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.01, seconds)
    solver.parameters.num_search_workers = 2
    solver.parameters.random_seed = 1331
    state = solver.Solve(model)
    if state not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    workers = [dict(w) for w in data["fixedWorkers"]]
    for wi, cell, var in worker_vars:
        if solver.Value(var):
            workers.append(dict(data["workers"][wi], x=cell[0], y=cell[1]))
    facilities, seen = [dict(f) for f in data["fixedFacilities"]], Counter()
    for gi, cell, var in facility_vars:
        if solver.Value(var):
            facilities.append(_facility_from_group(data["groups"][gi], seen[gi], cell))
            seen[gi] += 1
    return {"facilities": facilities, "workers": workers}


def _assign_facilities(data, centre, pads, workers=None, seconds=.35):
    if data["workerEnabled"] and workers is None:
        return _joint_support_plan(data, centre, pads, seconds=seconds)
    occupied = {(centre[0] + dx, centre[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    occupied.update((p["x"], p["y"]) for p in pads)
    if data["workerEnabled"]:
        selected = [w for w in workers if not w.get("fixed")]
        cells = {(w["x"], w["y"]) for w in selected}
        if len(cells) != len(selected) or cells & occupied or not cells <= data["free"]:
            return None
        if any(not any(_covers(w, (f["x"], f["y"]), f.get("jobType")) for w in selected) for f in data["fixedFacilities"]):
            return None
        occupied.update(cells)
    rows = [(g, j) for g in data["groups"] for j in range(g["count"])]
    cells = sorted(set().union(*(g["candidates"] for g in data["groups"])) - occupied) if rows else []
    if len(rows) > len(cells):
        return None
    items = {i["id"]: i for i in data["items"]}
    forbidden = -10**12
    aura = {(c, e): _aura_at(pads, items, c, e) for c in cells for e in {g["element"] for g, _ in rows}}
    weights = [[aura[c, g["element"]] if c in g["candidates"] and
                (not data["workerEnabled"] or any(_covers(w, c, g.get("jobType")) for w in workers))
                else forbidden for c in cells] for g, _ in rows]
    assignment, _ = _assignment(weights)
    if assignment is None or any(weights[i][j] == forbidden for i, j in enumerate(assignment)):
        return None
    facilities = [dict(f) for f in data["fixedFacilities"]]
    for (g, n), j in zip(rows, assignment):
        facilities.append(_facility_from_group(g, n, cells[j]))
    return {"facilities": facilities, "workers": workers or []}


def _initial_layout(snapshot, data, end, cancelled):
    """Constructive search only; never used as a certificate of optimal S."""
    k, items = len(data["pads"]), data["items"]
    cap = min(k, max(len(candidates) for _, _, candidates in data["centres"]))
    rows = [[_scaled(i.get("qi"), p["tier"]) for i in items] + [0] * cap for p in data["pads"][:cap]]
    chosen, upper = _assignment(rows)
    chosen = chosen or []
    upper = upper or 0
    potentials = defaultdict(lambda: [0.0] * 5)
    for g in data["groups"]:
        for c in g["candidates"]:
            potentials[c][g["element"]] += g["count"] / max(1, len(g["candidates"]))
    for f in data["fixedFacilities"]:
        if _int(f.get("element"), -1) in range(5):
            potentials[f["x"], f["y"]][_int(f["element"])] += 1
    def score_centre(c):
        return (min(k, len(c[2])), sum(sum(v) / (1 + abs(p[0] - c[0][0]) + abs(p[1] - c[0][1])) for p, v in potentials.items()))
    candidates = sorted(data["centres"], key=score_centre, reverse=True)
    best, rng = None, random.Random(671331)
    construct_end = time.monotonic() + max(0, end - time.monotonic()) * .4
    for centre, footprint, nearby in candidates:
        if time.monotonic() >= construct_end or cancelled():
            break
        count = min(k, len(nearby))
        if count != cap:
            selection, _ = _assignment([[ _scaled(i.get("qi"), p["tier"]) for i in items] + [0] * count
                                        for p in data["pads"][:count]])
        else:
            selection = chosen
        positions = sorted(nearby)
        selected = [(n, idx) for n, idx in enumerate(selection or []) if idx < len(items)]
        if selected:
            weights = []
            for n, idx in selected:
                es = [_scaled(v, data["pads"][n]["tier"]) for v in items[idx]["elements"]]
                weights.append([sum(sum(es[e] * values[e] for e in range(5))
                                    for c, values in potentials.items() if abs(c[0] - p[0]) + abs(c[1] - p[1]) <= 3)
                                for p in positions])
            assignment, _ = _assignment(weights)
        else:
            assignment = []
        for trial in range(2):
            if time.monotonic() >= construct_end or cancelled():
                break
            if trial and assignment:
                rng.shuffle(assignment)
            pads = [dict(data["pads"][n], x=positions[j][0], y=positions[j][1], itemId=items[idx]["id"])
                    for (n, idx), j in zip(selected, assignment or [])]
            support = _assign_facilities(data, centre, pads, seconds=min(.4, max(.01, construct_end - time.monotonic())))
            if support is None:
                continue
            layout = {"tribulation": {"x": centre[0], "y": centre[1]}, "pedestals": pads, **support}
            layout.update(evaluate(snapshot, layout))
            if best is None or (layout["D"], layout["S"]) > (best["D"], best["S"]):
                best = layout
    if best:
        best = _improve_layout(snapshot, data, best, end, cancelled)
    return best, upper


def _improve_layout(snapshot, data, initial, end, cancelled, delta_units=0, weight=None):
    """Local changes, each followed by an exact facility matching; no proof claim."""
    best, rng = initial, random.Random(26931331)
    centre = (initial["tribulation"]["x"], initial["tribulation"]["y"])
    footprint = {(centre[0] + dx, centre[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    available = sorted(data["free"] - footprint)
    floor_d = _scaled(initial["D"]) - delta_units
    item_by_id = {i["id"]: i for i in data["items"]}
    def quality(layout):
        d, s = _scaled(layout["D"]), _scaled(layout["S"])
        return (d, s) if weight is None else (weight * d + s, s)
    iterations = 0
    while time.monotonic() < end and not cancelled() and (best["pedestals"] or data["workerEnabled"]):
        iterations += 1
        pads = [dict(p) for p in best["pedestals"]]
        workers = [dict(w) for w in best.get("workers", [])]
        p = rng.randrange(len(pads)) if pads else 0
        move = iterations % (5 if data["workerEnabled"] else 3)
        replan_staff = data["workerEnabled"] and move == 4
        if move == 0 and len(pads) > 1:
            q = rng.randrange(len(pads))
            pads[p]["itemId"], pads[q]["itemId"] = pads[q]["itemId"], pads[p]["itemId"]
        elif move == 1 and pads:
            occupied = {(pad["x"], pad["y"]) for pad in pads}
            positions = [c for c in available if c not in occupied]
            if weight is None and abs(pads[p]["x"] - centre[0]) + abs(pads[p]["y"] - centre[1]) <= 3:
                positions = [c for c in positions if abs(c[0] - centre[0]) + abs(c[1] - centre[1]) <= 3]
            if not positions:
                continue
            cell = rng.choice(positions)
            pads[p]["x"], pads[p]["y"] = cell
        elif move == 2 and pads:
            used = {pad["itemId"] for pad in pads}
            choices = [item for item in data["items"] if item["id"] not in used]
            if not choices:
                continue
            pads[p]["itemId"] = rng.choice(choices)["id"]
        elif move == 3 and data["workers"]:
            movable = [w for w in workers if not w.get("fixed")]
            worker = rng.choice(movable)
            occupied = {(pad["x"], pad["y"]) for pad in pads}
            occupied.update((w["x"], w["y"]) for w in workers if w["workerId"] != worker["workerId"])
            choices = _worker_positions(data, worker, set(available) - occupied)
            if not choices:
                continue
            cell = rng.choice(choices)
            worker["x"], worker["y"] = cell
        d = sum(_scaled(item_by_id[pad["itemId"]]["qi"], pad["tier"]) for pad in pads
                if abs(pad["x"] - centre[0]) + abs(pad["y"] - centre[1]) <= 3)
        if d < floor_d or (weight is None and d < _scaled(best["D"])):
            continue
        if replan_staff:
            support = _joint_support_plan(data, centre, pads, seconds=min(.25, max(.01, end - time.monotonic())), hint=best)
        else:
            support = _assign_facilities(data, centre, pads, workers=workers)
        if support is None:
            continue
        candidate = {"tribulation": dict(best["tribulation"]), "pedestals": pads, **support}
        candidate.update(evaluate(snapshot, candidate))
        if quality(candidate) > quality(best):
            best = candidate
    return best


def _make_model(data, d_upper):
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    points = sorted(data["free"])
    point_index = {p: i for i, p in enumerate(points)}
    items, specs, n, k = data["items"], data["pads"], len(data["items"]), len(data["pads"])
    centre_points = [c[0] for c in data["centres"]]
    centre = model.NewIntVar(0, len(centre_points) - 1, "tribulation_position")
    xx, yy = [c[0] for c in centre_points], [c[1] for c in centre_points]
    tx, ty = model.NewIntVar(min(xx), max(xx), "tribulation_x"), model.NewIntVar(min(yy), max(yy), "tribulation_y")
    model.AddElement(centre, xx, tx)
    model.AddElement(centre, yy, ty)
    offset = max(max(abs(x), abs(y)) for x, y in points) + 100
    coords = points + [(offset + p, offset) for p in range(k)]
    xs, ys = [p[0] for p in coords], [p[1] for p in coords]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) + 1
    positions, indices, active, values, pad_x, pad_y, unique_items, d_values = [], [], [], [], [], [], [], []
    for p, spec in enumerate(specs):
        chosen = model.NewBoolVar(f"pad_{p}_active")
        pos = model.NewIntVarFromDomain(cp_model.Domain.FromValues(list(range(len(points))) + [len(points) + p]), f"pad_{p}_position")
        idx = model.NewIntVar(0, n, f"pad_{p}_item")
        model.Add(pos < len(points)).OnlyEnforceIf(chosen)
        model.Add(pos == len(points) + p).OnlyEnforceIf(chosen.Not())
        model.Add(idx >= 1).OnlyEnforceIf(chosen)
        model.Add(idx == 0).OnlyEnforceIf(chosen.Not())
        uniq = model.NewIntVar(1, n + k, f"pad_{p}_unique_item")
        model.Add(uniq == idx).OnlyEnforceIf(chosen)
        model.Add(uniq == n + p + 1).OnlyEnforceIf(chosen.Not())
        px, py = model.NewIntVar(min(xs), max(xs), f"pad_{p}_x"), model.NewIntVar(min(ys), max(ys), f"pad_{p}_y")
        model.AddElement(pos, xs, px)
        model.AddElement(pos, ys, py)
        val = []
        for e in range(6):
            table = [0] + [_scaled(i.get("qi") if e == 5 else i["elements"][e], spec["tier"]) for i in items]
            av = model.NewIntVar(0, max(table), f"pad_{p}_attribute_{e}")
            model.AddElement(idx, table, av)
            val.append(av)
        dx, dy = model.NewIntVar(0, span, f"pad_{p}_trib_dx"), model.NewIntVar(0, span, f"pad_{p}_trib_dy")
        model.AddAbsEquality(dx, px - tx)
        model.AddAbsEquality(dy, py - ty)
        outside_x, outside_y = model.NewBoolVar(f"pad_{p}_outside_x"), model.NewBoolVar(f"pad_{p}_outside_y")
        model.Add(dx >= 2).OnlyEnforceIf(outside_x)
        model.Add(dx <= 1).OnlyEnforceIf(outside_x.Not())
        model.Add(dy >= 2).OnlyEnforceIf(outside_y)
        model.Add(dy <= 1).OnlyEnforceIf(outside_y.Not())
        model.AddBoolOr([outside_x, outside_y]).OnlyEnforceIf(chosen)
        hit = model.NewBoolVar(f"pad_{p}_hits_tribulation")
        model.Add(dx + dy <= 3).OnlyEnforceIf(hit)
        model.Add(dx + dy >= 4).OnlyEnforceIf(hit.Not())
        dv = model.NewIntVar(0, max([0] + [_scaled(i.get("qi"), spec["tier"]) for i in items]), f"pad_{p}_D")
        model.Add(dv == val[5]).OnlyEnforceIf(hit)
        model.Add(dv == 0).OnlyEnforceIf(hit.Not())
        positions.append(pos); indices.append(idx); active.append(chosen); unique_items.append(uniq)
        values.append(val); pad_x.append(px); pad_y.append(py); d_values.append(dv)
        if p and specs[p - 1]["tier"] == spec["tier"]:
            # Break identical-platform permutations; identities do not affect mechanics.
            model.Add(positions[p - 1] < pos)
    if positions:
        model.AddAllDifferent(positions)
        model.AddAllDifferent(unique_items)
    D = model.NewIntVar(0, d_upper, "D_tenths")
    model.Add(D == sum(d_values))
    facility_vars, worker_vars, by_cell, requirements = [], [], defaultdict(list), defaultdict(set)
    for gidx, group in enumerate(data["groups"]):
        choices = []
        for cell in sorted(group["candidates"]):
            y = model.NewBoolVar(f"facility_{gidx}_{cell[0]}_{cell[1]}")
            choices.append(y)
            by_cell[cell].append(y)
            requirements[cell].add(group["element"])
            facility_vars.append((gidx, cell, y))
        model.Add(sum(choices) == group["count"])
    if data["workerEnabled"]:
        for wi, worker in enumerate(data["workers"]):
            choices = []
            for cell in _worker_positions(data, worker, data["free"]):
                y = model.NewBoolVar(f"worker_{wi}_{cell[0]}_{cell[1]}")
                choices.append(y)
                by_cell[cell].append(y)
                worker_vars.append((wi, cell, y))
            model.Add(sum(choices) == 1)
        cover_cache = {}
        def covering(job, cell):
            key = job, cell
            if key not in cover_cache:
                cover_cache[key] = [y for wi, p, y in worker_vars
                                    if _covers(dict(data["workers"][wi], x=p[0], y=p[1]), cell, job)]
            return cover_cache[key]
        for gi, cell, y in facility_vars:
            model.Add(sum(covering(data["groups"][gi]["jobType"], cell)) >= y)
        for f in data["fixedFacilities"]:
            model.Add(sum(covering(f.get("jobType"), (f["x"], f["y"]))) >= 1)
    for cell, ys_here in by_cell.items():
        occupied = model.NewBoolVar(f"facility_occupied_{cell[0]}_{cell[1]}")
        model.Add(sum(ys_here) == occupied)
        for pos in positions:
            model.Add(pos != point_index[cell]).OnlyEnforceIf(occupied)
        forbidden = [(i, 1) for i, c in enumerate(centre_points) if abs(c[0] - cell[0]) <= 1 and abs(c[1] - cell[1]) <= 1]
        if forbidden:
            model.AddForbiddenAssignments([centre, occupied], forbidden)
    for f in data["fixedFacilities"]:
        e = _int(f.get("element"), -1)
        if e in range(5):
            requirements[_int(f["x"]), _int(f["y"])].add(e)
    aura, aura_bounds = {}, {}
    for cell, elements in requirements.items():
        contributions = {e: [] for e in elements}
        for p, spec in enumerate(specs):
            # Both coordinates can be outside the planning rectangle for fixed facilities.
            distance_bound = max(abs(x - cell[0]) for x in xs) + max(abs(y - cell[1]) for y in ys)
            dx = model.NewIntVar(0, distance_bound, f"hit_{p}_{cell}_dx")
            dy = model.NewIntVar(0, distance_bound, f"hit_{p}_{cell}_dy")
            model.AddAbsEquality(dx, pad_x[p] - cell[0])
            model.AddAbsEquality(dy, pad_y[p] - cell[1])
            hit = model.NewBoolVar(f"hit_{p}_{cell}")
            model.Add(dx + dy <= 3).OnlyEnforceIf(hit)
            model.Add(dx + dy >= 4).OnlyEnforceIf(hit.Not())
            for e in elements:
                bound = max([0] + [_scaled(i["elements"][e], spec["tier"]) for i in items])
                v = model.NewIntVar(0, bound, f"contribution_{p}_{cell}_{e}")
                model.Add(v == values[p][e]).OnlyEnforceIf(hit)
                model.Add(v == 0).OnlyEnforceIf(hit.Not())
                contributions[e].append(v)
        for e in elements:
            bound = sum(max([0] + [_scaled(i["elements"][e], spec["tier"]) for i in items]) for spec in specs)
            a = model.NewIntVar(0, bound, f"aura_{cell}_{e}")
            model.Add(a == sum(contributions[e]))
            aura[cell, e], aura_bounds[cell, e] = a, bound
    terms, s_upper = [], 0
    for gidx, cell, y in facility_vars:
        e = data["groups"][gidx]["element"]
        b = aura_bounds[cell, e]
        term = model.NewIntVar(0, b, f"S_{gidx}_{cell}")
        model.Add(term == aura[cell, e]).OnlyEnforceIf(y)
        model.Add(term == 0).OnlyEnforceIf(y.Not())
        terms.append(term)
        s_upper += b
    for f in data["fixedFacilities"]:
        key = ((_int(f["x"]), _int(f["y"])), _int(f.get("element"), -1))
        if key in aura:
            terms.append(aura[key]); s_upper += aura_bounds[key]
    S = model.NewIntVar(0, s_upper, "S_tenths")
    model.Add(S == sum(terms))
    return {"model": model, "D": D, "S": S, "centre": centre, "centres": centre_points,
            "positions": positions, "points": points, "indices": indices,
            "facilityVars": facility_vars, "workerVars": worker_vars, "active": active, "sUpper": s_upper}


def _extract(snapshot, data, ctx, solver):
    centre = ctx["centres"][solver.Value(ctx["centre"])]
    pads = []
    for p, spec in enumerate(data["pads"]):
        idx = solver.Value(ctx["indices"][p])
        if idx:
            cell = ctx["points"][solver.Value(ctx["positions"][p])]
            pads.append(dict(spec, x=cell[0], y=cell[1], itemId=data["items"][idx - 1]["id"]))
    facilities, seen = [dict(f) for f in data["fixedFacilities"]], Counter()
    for gidx, cell, y in ctx["facilityVars"]:
        if solver.Value(y):
            g, n = data["groups"][gidx], seen[gidx]
            seen[gidx] += 1
            facilities.append(_facility_from_group(g, n, cell))
    workers = [dict(w) for w in data["fixedWorkers"]]
    for wi, cell, y in ctx["workerVars"]:
        if solver.Value(y):
            workers.append(dict(data["workers"][wi], x=cell[0], y=cell[1]))
    layout = {"tribulation": {"x": centre[0], "y": centre[1]}, "pedestals": pads,
              "facilities": facilities, "workers": workers}
    layout.update(evaluate(snapshot, layout))
    if _scaled(layout["D"]) != solver.Value(ctx["D"]) or _scaled(layout["S"]) != solver.Value(ctx["S"]):
        raise RuntimeError("独立评估与求解器目标不一致，已拒绝输出此方案。")
    if data["workerEnabled"] and layout["workerCoverage"]["uncoveredFacilityIds"]:
        raise RuntimeError("求解器方案中存在未被同工种人员覆盖的设施，已拒绝输出。")
    return layout


def _hint(data, ctx, layout):
    if layout is None:
        return
    model = ctx["model"]
    model.ClearHints()
    centre = (layout["tribulation"]["x"], layout["tribulation"]["y"])
    if centre not in ctx["centres"]:
        return
    model.AddHint(ctx["centre"], ctx["centres"].index(centre))
    points = {c: i for i, c in enumerate(ctx["points"])}
    items = {str(i["id"]): n + 1 for n, i in enumerate(data["items"])}
    by_tier = defaultdict(list)
    for pad in layout["pedestals"]:
        by_tier[pad["tier"]].append(pad)
    for pads in by_tier.values():
        pads.sort(key=lambda p: points[(p["x"], p["y"])])
    for p, spec in enumerate(data["pads"]):
        available = by_tier[spec["tier"]]
        pad = available.pop(0) if available else None
        model.AddHint(ctx["indices"][p], items[str(pad["itemId"])] if pad else 0)
        model.AddHint(ctx["positions"][p], points[pad["x"], pad["y"]] if pad else len(points) + p)
        model.AddHint(ctx["active"][p], int(pad is not None))
    selected = {(_group_key(f), (f["x"], f["y"])) for f in layout["facilities"]}
    for gidx, cell, y in ctx["facilityVars"]:
        model.AddHint(y, int((data["groups"][gidx]["key"], cell) in selected))
    worker_positions = {w["workerId"]: (w["x"], w["y"]) for w in layout.get("workers", [])}
    for wi, cell, y in ctx["workerVars"]:
        model.AddHint(y, int(worker_positions.get(data["workers"][wi]["workerId"]) == cell))


def _decorate(layout, *, status, d_proven, s_proven=False, d_upper=None, upper=None, phase="strict", warnings=()):
    if layout is None:
        return None
    result = dict(layout)
    result.update({"status": status, "DMaximumProven": bool(d_proven),
                   "DProvenOptimal": bool(d_proven and d_upper is not None and abs(layout["D"] - d_upper) < 1e-8),
                   "objectiveProvenOptimal": bool(s_proven),
                   "DUpperBound": d_upper, "upperBound": upper, "mode": phase,
                   "warnings": list(dict.fromkeys(list(result.get("warnings", [])) + list(warnings)))})
    result["DGap"] = max(0, round(d_upper - layout["D"], 6)) if d_upper is not None else None
    result["objectiveGap"] = max(0, round(upper - layout["S"], 6)) if upper is not None and phase == "strict" else None
    if s_proven:
        result["proof"] = "已证明严格模式下灵气最大且设施收益最优。" if phase == "strict" else "已证明允许损失范围内的加权目标最优。"
    elif d_proven:
        result["proof"] = "渡劫台灵气最高值已获证明；设施摆放为限时内找到的最好方案。" if phase == "strict" else "渡劫台理论最高值已获证明；此权衡方案尚未证明全局最优。"
    else:
        result["proof"] = "当前可行方案；渡劫台最高灵气和设施最优收益均尚未获证明。"
    return result


def solve(snapshot, options, progress=None, cancel_event=None):
    """Return baseline, strict, balanced and explicit bounds/status diagnostics.

    `seconds` is the total requested search budget (model construction may add time).
    `progress` receives small dictionaries. Cancellation returns the best retained plan.
    """
    started = time.monotonic()
    cancelled = (cancel_event.is_set if cancel_event is not None else lambda: False)
    def report(phase, message, **extra):
        if progress:
            progress(dict(phase=phase, message=message, elapsed=round(time.monotonic() - started, 2), **extra))
    baseline = _current_layout(snapshot)
    baseline.update(evaluate(snapshot))
    baseline.update(status="baseline", proof="按存档灵物、灵台等阶与中心格规则复算。")
    diagnostics = {"warnings": [], "phases": [], "rule": "center_manhattan_3", "workers": 2,
                   "scope": "S 包含全部目标设施及未请求移动而保留原位的设施。"}
    result = {"baseline": baseline, "strict": None, "balanced": None, "diagnostics": diagnostics}
    try:
        data = _prepare(snapshot, options)
    except ValueError as exc:
        diagnostics.update(status="infeasible", message=str(exc), elapsed=round(time.monotonic() - started, 2))
        return result
    diagnostics["warnings"] = data["warnings"]
    data["cancelled"] = cancelled
    diagnostics.update(workerConstraints=data["workerEnabled"], selectedWorkers=len(data["workers"]),
                       fixedWorkers=len(data["fixedWorkers"]))
    seconds = max(1, min(1800, float(options.get("seconds", 30))))
    deadline = started + seconds
    delta = max(0.0, float(options.get("delta", 0)))
    weight = max(0.0, float(options.get("weight", 10)))
    if not math.isfinite(delta) or not math.isfinite(weight) or weight > 100000:
        raise ValueError("权重和允许损失必须是有限非负数，权重不超过 100000。")
    # Do not round a requested maximum loss upwards: that could violate its promise.
    delta_units = math.floor(delta * 10 + 1e-8)
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        cp_model = None
        if data["workerEnabled"]:
            diagnostics.update(status="error", code="missing_ortools",
                               message="人员覆盖联合求解需要 OR-Tools，当前依赖不可用。请通过启动脚本安装依赖后重试。",
                               elapsed=round(time.monotonic() - started, 2))
            return result
    report("initial", "正在建立合法初始方案并计算灵气上界。")
    initial, d_upper = _initial_layout(snapshot, data, min(deadline, started + min(15, seconds * .25)), cancelled)
    d_proven = initial is not None and _scaled(initial["D"]) == d_upper
    best = initial
    diagnostics.update(DUpperBound=d_upper / 10, inventoryCount=len(data["items"]),
                       pedestalLimit=len(data["pads"]), freeCells=len(data["free"]),
                       possibleTribulationCentres=len(data["centres"]))
    if best:
        report("initial", "已找到合法方案。", D=best["D"], S=best["S"], DProvenOptimal=d_proven)
    if cp_model is None:
        result["strict"] = _decorate(best, status="feasible" if best else "unknown", d_proven=d_proven,
                                      d_upper=d_upper / 10, warnings=data["warnings"])
        diagnostics.update(status="feasible" if best else "unknown", message="OR-Tools 不可用；仅完成初始方案搜索。",
                           elapsed=round(time.monotonic() - started, 2))
        return result
    if cancelled() or time.monotonic() >= deadline:
        status = "cancelled" if cancelled() else "feasible" if best else "unknown"
        result["strict"] = _decorate(best, status=status, d_proven=d_proven, d_upper=d_upper / 10, warnings=data["warnings"])
        diagnostics.update(status=status, elapsed=round(time.monotonic() - started, 2))
        return result
    report("model", "正在建立包含设施归属与位置约束的整数模型。")
    ctx = _make_model(data, d_upper)
    model = ctx["model"]

    def run_phase(name, objective, duration, hint):
        model.Maximize(objective)
        _hint(data, ctx, hint)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 2
        solver.parameters.max_time_in_seconds = max(.01, duration)
        solver.parameters.random_seed = 1331
        done = threading.Event()
        def watcher():
            while not done.wait(.15):
                if cancelled():
                    solver.StopSearch()
                    return
        thread = threading.Thread(target=watcher, daemon=True)
        thread.start()
        report(name, "正在求解。", seconds=round(max(0, duration), 1))
        try:
            state = solver.Solve(model)
        finally:
            done.set()
            thread.join(timeout=.25)
        name_state = solver.StatusName(state).lower()
        # UNKNOWN can be returned during presolve with a placeholder zero bound.
        bound = solver.BestObjectiveBound() if state in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
        diagnostics["phases"].append({"phase": name, "status": name_state, "upperBoundRaw": bound,
                                      "wallSeconds": round(solver.WallTime(), 3)})
        layout = _extract(snapshot, data, ctx, solver) if state in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
        report(name, "阶段结束。", status=name_state, D=layout["D"] if layout else None,
               S=layout["S"] if layout else None)
        return layout, state, bound

    # First establish the maximum D. An achieved matching bound is also a proof.
    if not d_proven and not cancelled():
        remaining = max(.01, deadline - time.monotonic())
        found, state, bound = run_phase("maximize_D", ctx["D"], remaining * (.6 if best else 1), best)
        if found is not None and (best is None or (found["D"], found["S"]) > (best["D"], best["S"])):
            best = found
        if best:
            d_proven = state == cp_model.OPTIMAL or _scaled(best["D"]) == d_upper
            if state == cp_model.OPTIMAL:
                d_upper = _scaled(best["D"])
            elif bound is not None and state == cp_model.FEASIBLE:
                d_upper = min(d_upper, max(_scaled(best["D"]), math.floor(bound + 1e-6)))
        elif state == cp_model.INFEASIBLE:
            diagnostics.update(status="infeasible", message="已证明约束无解。", elapsed=round(time.monotonic() - started, 2))
            return result
    strict, strict_optimal, strict_bound = best, False, None
    # If D is not certified, optimize S only at the best known D and label honestly.
    if best and not cancelled() and time.monotonic() < deadline:
        strict_d = _scaled(best["D"])
        lock = model.NewBoolVar("strict_D_lock")
        model.Add(ctx["D"] == strict_d).OnlyEnforceIf(lock)
        model.AddAssumption(lock)
        remaining = max(.01, deadline - time.monotonic())
        found, state, bound = run_phase("strict_S", ctx["S"], remaining * (.55 if delta_units and d_proven else 1), best)
        if found is not None and found["S"] > strict["S"]:
            strict = found
        strict_optimal = d_proven and state == cp_model.OPTIMAL
        strict_bound = bound / 10 if bound is not None and state in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None
        model.ClearAssumptions()
        model.Add(lock == 0)
    status = "cancelled" if cancelled() else "optimal" if strict_optimal else "feasible" if strict else "unknown"
    result["strict"] = _decorate(strict, status=status, d_proven=d_proven, s_proven=strict_optimal,
                                  d_upper=d_upper / 10, upper=strict_bound, warnings=data["warnings"])
    if delta_units and d_proven and strict and not cancelled() and time.monotonic() < deadline:
        model.Add(ctx["D"] >= _scaled(strict["D"]) - delta_units)
        # Thousandth precision for a UI exchange-rate value, and S as tie-break.
        from fractions import Fraction
        ratio = Fraction(str(weight)).limit_denominator(1000)
        report("balanced_initial", "正在改进允许损失范围内的权衡初始方案。")
        balanced_hint = _improve_layout(snapshot, data, strict,
                                        min(deadline, time.monotonic() + min(8, (deadline - time.monotonic()) * .35)),
                                        cancelled, delta_units, float(ratio))
        # Lexicographic secondary S resolves exactly equal exchange scores.
        tie_scale = ctx["sUpper"] + 1
        score = (ratio.numerator * ctx["D"] + ratio.denominator * ctx["S"]) * tie_scale + ctx["S"]
        found, state, bound = run_phase("balanced", score, max(.01, deadline - time.monotonic()), balanced_hint)
        balanced = balanced_hint
        def quality(l):
            return (ratio.numerator * _scaled(l["D"]) + ratio.denominator * _scaled(l["S"]), l["S"])
        if found is not None and quality(found) >= quality(balanced):
            balanced = found
        balanced = _decorate(balanced, status="cancelled" if cancelled() else "optimal" if state == cp_model.OPTIMAL else "feasible",
                             d_proven=True, s_proven=state == cp_model.OPTIMAL, d_upper=d_upper / 10,
                             upper=math.floor(bound / tie_scale) / (10 * ratio.denominator) if bound is not None and state in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None,
                             phase="balanced", warnings=data["warnings"])
        balanced.update(DLoss=round(strict["D"] - balanced["D"], 1), weight=float(ratio),
                        score=float(ratio) * balanced["D"] + balanced["S"], allowedLoss=delta_units / 10)
        balanced["objectiveGap"] = max(0, round(balanced["upperBound"] - balanced["score"], 6)) if balanced["upperBound"] is not None else None
        result["balanced"] = balanced
        if balanced["D"] == strict["D"] and balanced["S"] > strict["S"]:
            strict = {k: v for k, v in balanced.items() if k not in ("DLoss", "weight", "score", "allowedLoss")}
            strict_optimal = state == cp_model.OPTIMAL
            result["strict"] = _decorate(strict, status="cancelled" if cancelled() else "optimal" if strict_optimal else "feasible",
                                          d_proven=True, s_proven=strict_optimal, d_upper=d_upper / 10,
                                          upper=strict["S"] if strict_optimal else strict_bound, warnings=data["warnings"])
    elif delta_units and not d_proven:
        diagnostics["warnings"].append("渡劫台最高灵气尚未获证明，未将“相对最高值允许损失”的权衡方案伪称为有效最优方案。")
    diagnostics.update(status="cancelled" if cancelled() else "optimal" if strict_optimal else "feasible" if strict else "unknown",
                       DProvenOptimal=d_proven, DUpperBound=d_upper / 10, elapsed=round(time.monotonic() - started, 2))
    return result
