"""Independent validation of city plans against an immutable game snapshot.

No solver helpers are imported. Geometry, ownership, aura, migration records and
the inventory-only D bound are recomputed before a plan can be published.
"""
from collections import Counter, defaultdict
import heapq
import math

RESOURCE_JOBS = frozenset(("伐木", "采矿", "种植"))
WORK_JOBS = frozenset((*RESOURCE_JOBS, "锻造", "工艺", "炼丹", "研究", "操练", "建造"))
PRIORITY_JOBS = ("研究", "操练")


class CityValidationError(ValueError):
    pass


def _fail(message):
    raise CityValidationError("规划结果校验失败：" + message)


def _integer(value, label, minimum=0):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value != int(value) or value < minimum):
        _fail(label + "必须是有限整数，且不小于 " + str(minimum))
    return int(value)


def _number(value, label, minimum=0):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum):
        _fail(label + "必须是非负有限数值")
    return float(value)


def _identity(value, label):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or str(value) == "":
        _fail(label + "编号无效")
    return str(value)


def _rows(value, label):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        _fail(label + "必须是对象列表")
    return value


def _by_id(rows, field, label):
    result = {}
    for row in _rows(rows, label):
        key = _identity(row.get(field), label)
        if key in result:
            _fail(label + "编号重复：" + key)
        result[key] = row
    return result


def _ids(value, label):
    if not isinstance(value, list):
        _fail(label + "必须是编号列表")
    keys = [_identity(v, label) for v in value]
    if len(set(keys)) != len(keys):
        _fail(label + "含重复编号")
    return set(keys)


def _point(row, label):
    if not isinstance(row, dict):
        _fail(label + "缺少坐标")
    return (_integer(row.get("x"), label + "X", -100000),
            _integer(row.get("y"), label + "Y", -100000))


def _footprint(row, source):
    x, y = _point(row, "建筑")
    w = _integer(source.get("width"), "建筑宽", 1)
    h = _integer(source.get("height"), "建筑高", 1)
    left, top = x - (w - 1) // 2, y - (h - 1) // 2
    return {(left + dx, top + dy) for dx in range(w) for dy in range(h)}


def _js_round(value):
    floor = math.floor(value)
    return floor + int(value - floor >= 0.5)


def _scaled(raw, tier):
    return _js_round((10 * raw) * (1 + tier * 0.15))


def _inventory_bound(tiers, items):
    """Independent min-cost flow, with one item per available core pedestal."""
    tiers = sorted(tiers, reverse=True)[:16]
    n, m = len(tiers), len(items)
    if n == 0:
        return 0
    if n > m:
        _fail("灵物不足以填满核心灵台")
    weights = [[_scaled(item["qi"], tier) for item in items] for tier in tiers]
    ceiling = max(max(row) for row in weights)
    sink, source = n + m + 1, n + m
    graph = [[] for _ in range(n + m + 2)]

    def edge(a, b, cost):
        graph[a].append([b, len(graph[b]), 1, cost])
        graph[b].append([a, len(graph[a]) - 1, 0, -cost])

    for r in range(n):
        edge(source, r, 0)
        for c in range(m):
            edge(r, n + c, ceiling - weights[r][c])
    for c in range(m):
        edge(n + c, sink, 0)
    potential, total = [0] * len(graph), 0
    for _ in range(n):
        distance = [math.inf] * len(graph)
        previous = [None] * len(graph)
        distance[source] = 0
        queue = [(0, source)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != distance[node]:
                continue
            for index, (dest, _, capacity, price) in enumerate(graph[node]):
                if not capacity:
                    continue
                candidate = cost + price + potential[node] - potential[dest]
                if candidate < distance[dest]:
                    distance[dest], previous[dest] = candidate, (node, index)
                    heapq.heappush(queue, (candidate, dest))
        if previous[sink] is None:
            _fail("灵物匹配上界计算失败")
        for node, cost in enumerate(distance):
            if cost != math.inf:
                potential[node] += cost
        node = sink
        while node != source:
            prior, index = previous[node]
            link = graph[prior][index]
            total += link[3]
            link[2] -= 1
            graph[node][link[1]][2] += 1
            node = prior
    return n * ceiling - total


def _context(snapshot, options):
    buildings = _by_id(snapshot.get("buildings"), "id", "存档建筑")
    old_facilities = _by_id(snapshot.get("facilities"), "id", "存档设施")
    people = _by_id([w for w in _rows(snapshot.get("workers", []), "存档人物") if w.get("inMainCity")], "id", "主城人物")
    items = _by_id(snapshot.get("items"), "id", "灵物")
    for item in items.values():
        _number(item.get("qi"), "灵物灵气")
        if not isinstance(item.get("elements"), list) or len(item["elements"]) != 5:
            _fail("灵物五行必须包含五个数值")
        for value in item["elements"]:
            _number(value, "灵物五行")
    target = next((b for b in buildings.values() if b["template"] == 66), None)
    if target is None or (target.get("width"), target.get("height")) != (3, 3):
        _fail("缺少已有的 3×3 渡劫台")
    current_pads = sorted((b for b in buildings.values() if b["template"] == 67), key=lambda b: (-b["tier"], str(b["id"])))
    count = _integer(options.get("maxPedestals", len(current_pads)), "灵台数量")
    limit = snapshot.get("pedestalLimit", {})
    maximum = _integer(limit.get("maximum"), "已确认灵台上限") if limit.get("status") == "verified" else len(current_pads)
    if count > maximum or count > len(items):
        _fail("灵台数量超过已确认上限或灵物数量")
    locks = _ids(options.get("lockedBuildingIds", []), "锁定建筑")
    facility_locks = _ids(options.get("lockedFacilityIds", []), "锁定设施")
    worker_locks = _ids(options.get("lockedWorkerIds", []), "锁定人员")
    if not locks <= set(buildings):
        _fail("锁定了不存在的建筑")
    if not facility_locks <= set(old_facilities) or not worker_locks <= set(people):
        _fail("锁定了不存在的设施或非主城人员")
    # Game-state restrictions override a user's general permission to rearrange.
    locks.update(bid for bid, building in buildings.items() if building.get("canMove") is False)
    if locks & {str(b["id"]) for b in current_pads[count:]}:
        _fail("不能收起锁定的灵台")
    moving = options.get("allowMainBuildingMoves", True)
    if not isinstance(moving, bool):
        _fail("主建筑可搬选项必须为布尔值")
    mode, strategy = options.get("mode", "fresh"), options.get("priorityStrategy", "focus")
    if mode not in ("fresh", "migration") or strategy not in ("focus", "balanced", "total"):
        _fail("规划模式或重点策略无效")
    groups, existing = {}, defaultdict(list)
    for group in _rows(snapshot.get("facilityGroups", []), "存档设施分组"):
        key = (_integer(group.get("template"), "设施模板", 1), _integer(group.get("ownerId"), "归属建筑", 1))
        if key in groups:
            _fail("存档设施分组重复")
        groups[key] = group
    for f in old_facilities.values():
        key = (f["template"], f["ownerId"])
        existing[key].append(f)
        groups.setdefault(key, f)
    requests = {}
    for row in _rows(options.get("facilityRequests", []), "设施请求"):
        key = (_integer(row.get("template"), "设施模板", 1), _integer(row.get("ownerId"), "归属建筑", 1))
        if key in requests or key not in groups:
            _fail("设施请求重复或不是已读取的类型/归属组合")
        requests[key] = row
    facilities = {}
    for key, group in groups.items():
        request = requests.get(key, {})
        n = _integer(request.get("count", len(existing[key])), "设施数量")
        minimum = _integer(request.get("minWorkers", 1), "每设施最少人数")
        owner = buildings.get(str(key[1]))
        if owner is None or owner.get("width") not in (2, 3) or owner.get("height") not in (2, 3):
            _fail("设施主建筑尺寸不在已确认范围")
        job = group.get("jobType")
        element = _integer(group.get("element"), "设施五行")
        if job not in WORK_JOBS or element > 4:
            _fail("设施工种或五行无效")
        for index in range(n):
            old = existing[key][index] if index < len(existing[key]) else None
            fid = str(old["id"]) if old else f"new:{key[0]}:{key[1]}:{index + 1}"
            if fid in facilities:
                _fail("生成的设施编号重复")
            facilities[fid] = {"template": key[0], "ownerId": key[1], "jobType": job,
                               "element": element, "minWorkers": 1 if job in RESOURCE_JOBS else minimum}
    for bid, n in Counter(str(f["ownerId"]) for f in facilities.values()).items():
        if n > _integer(buildings[bid].get("facilityCapacity"), "主建筑设施容量"):
            _fail("设施数量超过同一主建筑的总容量")
    if not facility_locks <= set(facilities):
        _fail("不能删除锁定的设施")
    selected = {}
    jobs = {f["jobType"] for f in facilities.values()}
    for row in _rows(options.get("workerAssignments", []), "人员分工"):
        wid, job = _identity(row.get("workerId"), "人物"), row.get("jobType")
        if wid in selected or wid not in people or not isinstance(job, str) or job not in jobs:
            _fail("人员重复、不在主城或工种没有设施")
        if _number(people[wid].get("rangePixels"), "人物真实工作范围") <= 0:
            _fail("人物工作范围未确认")
        selected[wid] = job
    requirements = {}
    for row in _rows(options.get("jobRequirements", []), "工种人数要求"):
        job = row.get("jobType")
        if not isinstance(job, str) or job not in WORK_JOBS or job in requirements:
            _fail("工种人数要求重复或未知")
        allocation_modes = ("exclusive",) if job in RESOURCE_JOBS else ("station", "exclusive")
        if row.get("allocation", allocation_modes[0]) not in allocation_modes:
            _fail("主要分工要求无效")
        n = _integer(row.get("workerCount"), "工种人数")
        minimum = _integer(row.get("minFacilitiesPerWorker", 0), "每人最少设施数")
        if n != sum(value == job for value in selected.values()):
            _fail("所选人数不符合工种人数要求")
        requirements[job] = minimum
    return {"buildings": buildings, "oldFacilities": old_facilities, "people": people, "items": items,
            "target": target, "currentPads": current_pads, "padCount": count, "locks": locks,
            "facilityLocks": facility_locks, "workerLocks": worker_locks,
            "moving": moving, "strategy": strategy, "facilities": facilities,
            "selected": selected, "requirements": requirements}


def _check_moves(snapshot, layout, reported):
    expected = []
    old_b = {str(b["id"]): b for b in snapshot["buildings"]}
    old_f = {str(f["id"]): f for f in snapshot["facilities"]}
    old_w = {str(w["id"]): w for w in snapshot.get("workers", []) if w.get("inMainCity")}
    keep_b, keep_f = set(), set()
    for kind, rows, olds, field in (("building", layout["buildings"], old_b, "buildingId"),
                                    ("pedestal", layout["pedestals"], old_b, "buildingId"),
                                    ("facility", layout["facilities"], old_f, "id"),
                                    ("worker", layout["workers"], old_w, "workerId")):
        for row in rows:
            identity = str(row[field]) if row.get(field) is not None else None
            old = olds.get(identity)
            if kind in ("building", "pedestal"):
                keep_b.add(identity)
            elif kind == "facility":
                keep_f.add(identity)
            before, after = _point(old, "原位置") if old else None, _point(row, "新位置")
            if before != after:
                expected.append((kind, identity or str(row["id"]), "move" if old else "create", before, after))
            if kind == "pedestal" and old and old.get("itemId") != row["itemId"]:
                expected.append(("item", str(row["itemId"]), "replace", identity,
                                 None if old.get("itemId") is None else str(old["itemId"])))
            if kind == "worker" and old and old.get("currentTaskType") != row.get("jobType"):
                expected.append(("job", identity, "assign", old.get("currentTaskType"), row.get("jobType")))
    for identity, old in old_b.items():
        if identity not in keep_b:
            expected.append(("pedestal", identity, "remove", _point(old, "原灵台"), None))
    for identity, old in old_f.items():
        if identity not in keep_f:
            expected.append(("facility", identity, "remove", _point(old, "原设施"), None))
    actual = []
    for row in _rows(reported, "迁移列表"):
        kind, action = row.get("kind"), row.get("action")
        identity = _identity(row.get("id"), "迁移对象")
        if kind == "item" and action == "replace":
            actual.append((kind, identity, action, _identity(row.get("buildingId"), "换物灵台"),
                           None if row.get("previousItemId") is None else _identity(row["previousItemId"], "原灵物")))
        elif kind == "job" and action == "assign":
            before, after = row.get("previousJobType"), row.get("jobType")
            if (before is not None and (not isinstance(before, str) or before not in WORK_JOBS)) or (after is not None and (not isinstance(after, str) or after not in WORK_JOBS)):
                _fail("人员岗位变更记录含未知工种")
            actual.append((kind, identity, action, before, after))
        elif kind in ("building", "pedestal", "facility", "worker") and action in ("move", "create", "remove"):
            before = None if row.get("from") is None else _point(row["from"], "迁移原位置")
            after = None if row.get("to") is None else _point(row["to"], "迁移新位置")
            actual.append((kind, identity, action, before, after))
        else:
            _fail("迁移对象类型或动作无效")
    if Counter(actual) != Counter(expected):
        _fail("迁移清单遗漏、重复或与实际前后位置/灵物更换不符")
    return len(expected)


def validate_result(snapshot, options, result):
    """Raise on any disagreement; stamp only a complete successful plan."""
    if not isinstance(result, dict) or result.get("status") not in ("feasible", "invalid", "unknown", "cancelled"):
        _fail("结果状态无效")
    result.pop("validation", None)
    if result["status"] != "feasible":
        if result.get("layout") is not None or result.get("metrics") is not None or result.get("moves"):
            _fail("未取得可行结果却返回了布局或指标")
        if any(result.get("proof", {}).get(k) for k in ("DProven", "globalOptimal", "priorityProven", "migrationProven")):
            _fail("无可行布局却声称最优")
        return result
    if not isinstance(snapshot, dict) or not isinstance(options, dict):
        _fail("缺少快照或选项")
    c = _context(snapshot, options)
    layout = result.get("layout")
    if not isinstance(layout, dict):
        _fail("缺少布局")
    buildings = _by_id(layout.get("buildings"), "buildingId", "方案建筑")
    pads = _by_id(layout.get("pedestals"), "id", "方案灵台")
    facilities = _by_id(layout.get("facilities"), "id", "方案设施")
    workers = _by_id(layout.get("workers"), "workerId", "方案人员")
    wanted_buildings = {key for key, b in c["buildings"].items() if b["template"] != 67}
    if set(buildings) != wanted_buildings:
        _fail("方案必须完整保留所有主建筑及渡劫台")
    if set(facilities) != set(c["facilities"]) or set(workers) != set(c["people"]):
        _fail("设施数量/身份或主城人员与请求不符")
    if set(pads) != {f"p{i + 1}" for i in range(c["padCount"])}:
        _fail("灵台数量或编号不符")
    bounds = snapshot.get("bounds", {})
    xmin, xmax, ymin, ymax = [_integer(bounds.get(k), "已解锁范围", -100000) for k in ("xmin", "xmax", "ymin", "ymax")]
    if xmin > xmax or ymin > ymax:
        _fail("已解锁范围无效")
    occupied = {}

    def place(cells, label):
        for point in cells:
            if not (xmin <= point[0] <= xmax and ymin <= point[1] <= ymax):
                _fail(label + "超出已解锁区域")
            if point in occupied:
                _fail(label + "与" + occupied[point] + "占格重叠")
            occupied[point] = label

    for resource in snapshot.get("resources", []):
        point = _point(resource, "自然资源")
        if xmin <= point[0] <= xmax and ymin <= point[1] <= ymax:
            place({point}, "自然资源")
    for bid, row in buildings.items():
        source = c["buildings"][bid]
        if (not c["moving"] or bid in c["locks"]) and _point(row, "建筑") != _point(source, "原建筑"):
            _fail("锁定或禁止搬迁的主建筑被移动")
        place(_footprint(row, source), "建筑 " + bid)
    used_items, tiers = set(), []
    for index in range(c["padCount"]):
        row = pads[f"p{index + 1}"]
        old = c["currentPads"][index] if index < len(c["currentPads"]) else None
        bid = None if row.get("buildingId") is None else _identity(row["buildingId"], "灵台建筑")
        if bid != (str(old["id"]) if old else None):
            _fail("灵台冒用了不存在或未选择的建筑身份")
        tier = _integer(row.get("tier"), "灵台等阶")
        if tier != (old["tier"] if old else 0):
            _fail("灵台等阶不符合实际库存；新增灵台只能为零阶")
        if bid in c["locks"] and _point(row, "灵台") != _point(old, "原灵台"):
            _fail("锁定灵台被移动")
        iid = _identity(row.get("itemId"), "灵物")
        if iid not in c["items"] or iid in used_items:
            _fail("灵物不存在或被重复摆放")
        used_items.add(iid)
        tiers.append(tier)
        place({_point(row, "灵台")}, "灵台 " + str(index + 1))
    for fid, row in facilities.items():
        meta = c["facilities"][fid]
        if fid in c["facilityLocks"] and _point(row, "设施") != _point(c["oldFacilities"][fid], "原设施"):
            _fail("锁定设施被移动")
        for field in ("template", "ownerId", "element", "minWorkers"):
            if _integer(row.get(field), "设施" + field) != meta[field]:
                _fail("设施类型、归属、五行或最低人数被篡改")
        if row.get("jobType") != meta["jobType"]:
            _fail("设施工种与游戏数据不一致")
        owner_id = str(meta["ownerId"])
        owner, source = buildings[owner_id], c["buildings"][owner_id]
        ox, oy = _point(owner, "主建筑")
        x, y = _point(row, "设施")
        left, top = ox - (source["width"] - 1) // 2, oy - (source["height"] - 1) // 2
        if not (left - 3 <= x <= left + source["width"] + 2 and top - 3 <= y <= top + source["height"] + 2):
            _fail("设施超出所属主建筑的实际矩形范围")
        place({(x, y)}, "设施 " + fid)
    ownership = defaultdict(set)
    for wid, row in workers.items():
        source, job = c["people"][wid], c["selected"].get(wid)
        if wid in c["workerLocks"] and _point(row, "人员") != _point(source, "原人员"):
            _fail("锁定人员被移动")
        ids = _ids(row.get("facilityIds"), "人员主要负责设施")
        if job is None:
            if row.get("fixed") is not True or row.get("jobType") != source.get("currentTaskType") or ids or _point(row, "人员") != _point(source, "原人员"):
                _fail("未选择的人员被移动或被赋予主要分工")
        else:
            if row.get("fixed") is not False or row.get("jobType") != job:
                _fail("人物工种或固定状态与请求不符")
            if job in RESOURCE_JOBS:
                if len(ids) < c["requirements"].get(job, 0):
                    _fail("资源人员主要负责设施数不足")
            elif len(ids) != 1:
                _fail("非资源人员必须且只能主要负责一个设施")
            for fid in ids:
                if fid not in facilities or facilities[fid]["jobType"] != job:
                    _fail("分配了不存在或不同工种的设施")
                fx, fy = _point(facilities[fid], "设施")
                wx, wy = _point(row, "人员")
                if 10000 * ((fx - wx) ** 2 + (fy - wy) ** 2) > source["rangePixels"] ** 2:
                    _fail("人员主要负责设施超出真实工作范围")
                ownership[fid].add(wid)
        place({_point(row, "人员")}, "人员 " + wid)
    flex = 0
    for fid, row in facilities.items():
        primary = _ids(row.get("primaryWorkerIds"), "设施主要人员")
        if primary != ownership[fid]:
            _fail("设施与人员的主要分工双向记录不一致")
        if row["jobType"] in RESOURCE_JOBS:
            if len(primary) != 1:
                _fail("同一资源设施必须且只能计入一名主要负责人")
        elif len(primary) < row["minWorkers"]:
            _fail("非资源设施的主要工作人员不足")
        expected_assist = set()
        if row["jobType"] in RESOURCE_JOBS:
            fx, fy = _point(row, "设施")
            for wid, job in c["selected"].items():
                if job == row["jobType"] and wid not in primary:
                    wx, wy = _point(workers[wid], "人员")
                    if 10000 * ((fx - wx) ** 2 + (fy - wy) ** 2) <= c["people"][wid]["rangePixels"] ** 2:
                        expected_assist.add(wid)
        if _ids(row.get("assistingWorkerIds"), "设施协助人员") != expected_assist:
            _fail("协助列表遗漏、重复计主要负责人或不在同工种真实范围内")
        flex += len(expected_assist)

    def aura(point, element=None):
        total = 0
        for pad in pads.values():
            if abs(pad["x"] - point[0]) + abs(pad["y"] - point[1]) <= 3:
                item = c["items"][str(pad["itemId"])]
                total += _scaled(item["qi"] if element is None else item["elements"][element], pad["tier"])
        return total / 10

    target = buildings[str(c["target"]["id"])]
    d = aura(_point(target, "渡劫台"))
    values = {fid: aura(_point(row, "设施"), row["element"]) for fid, row in facilities.items()}
    metrics = result.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("priorityStrategy") != c["strategy"]:
        _fail("缺少指标或重点策略不符")

    def equal(field, expected):
        if not math.isclose(_number(metrics.get(field), "指标 " + field), expected, rel_tol=0, abs_tol=1e-8):
            _fail(field + "与独立复算值不符")

    reported_values = _by_id(metrics.get("facilityValues"), "facilityId", "设施收益")
    if set(reported_values) != set(values):
        _fail("设施收益列表遗漏或重复")
    for fid, value in values.items():
        report = reported_values[fid]
        if report.get("jobType") != facilities[fid]["jobType"] or not math.isclose(_number(report.get("value"), "设施收益"), value, rel_tol=0, abs_tol=1e-8):
            _fail("设施收益或工种与独立复算不符")
    best = {job: max(v for fid, v in values.items() if facilities[fid]["jobType"] == job)
            for job in PRIORITY_JOBS if any(f["jobType"] == job for f in facilities.values())}
    reported_best = metrics.get("priorityByJob")
    if not isinstance(reported_best, dict) or set(reported_best) != set(best):
        _fail("重点工种指标缺失或伪造")
    for job, value in best.items():
        row = reported_best[job]
        if not isinstance(row, dict):
            _fail("重点设施记录无效")
        fid = _identity(row.get("facilityId"), "重点设施")
        if fid not in facilities or facilities[fid]["jobType"] != job or values[fid] != value or not math.isclose(_number(row.get("value"), "重点收益"), value, rel_tol=0, abs_tol=1e-8):
            _fail("重点设施必须实际达到该工种最高单设施收益")
    priority = list(best.values()) if c["strategy"] == "focus" else [v for fid, v in values.items() if facilities[fid]["jobType"] in PRIORITY_JOBS]
    equal("D", d)
    equal("S", sum(values.values()))
    equal("priorityMinimum", min(priority, default=0))
    equal("priorityTotal", sum(priority))
    if _integer(metrics.get("flexCoverage"), "协助覆盖数量") != flex:
        _fail("协助覆盖数量与独立复算不符")
    move_count = _check_moves(snapshot, layout, result.get("moves"))
    if _integer(metrics.get("moveCount"), "迁移数量") != move_count:
        _fail("迁移数量与实际清单不符")
    upper = _inventory_bound(tiers, list(c["items"].values())) / 10
    proof = result.get("proof")
    if not isinstance(proof, dict) or not math.isclose(_number(proof.get("DUpperBound"), "灵气上界"), upper, rel_tol=0, abs_tol=1e-8):
        _fail("灵气上界与独立库存匹配不符")
    if not isinstance(proof.get("DProven"), bool) or proof["DProven"] != math.isclose(d, upper, rel_tol=0, abs_tol=1e-8):
        _fail("渡劫台最优证明标志与上界不符")
    if any(proof.get(k) is not False for k in ("globalOptimal", "priorityProven", "migrationProven")):
        _fail("当前候选搜索不能宣称完整目标全局最优")
    result["validation"] = {"passed": True, "status": "passed", "geometry": True, "workerAssignments": True,
                            "resourceAssists": True, "metrics": True, "moves": True,
                            "buildingCount": len(buildings), "pedestalCount": len(pads),
                            "facilityCount": len(facilities), "workerCount": len(workers),
                            "selectedWorkerCount": len(c["selected"]), "assistingRelationships": flex,
                            "DUpperBound": upper,
                            "facilityEnvironment": [{"facilityId": fid, "elementValue": value,
                                                     "environmentBonusPercent": _js_round(100 * (value / 2)) / 100}
                                                    for fid, value in values.items()]}
    return result
