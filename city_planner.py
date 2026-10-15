"""Explicit city-planning policies over immutable schema-2 game snapshots.

Geometry, item assignment and fixed-emitter refinement are separate searches.
Only an attained independent inventory upper bound proves global D optimality.
Other objectives are reported as best-known, including migration counts.
"""
from __future__ import annotations

import copy
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent / "vendor"
if _VENDOR.is_dir():
    sys.path.insert(0, str(_VENDOR))
from ortools.sat.python import cp_model

RESOURCE_JOBS = frozenset(("伐木", "采矿", "种植"))
PRIORITY_JOBS = ("研究", "操练")
CORE_OFFSETS = sorted(((x, y) for x in range(-3, 4) for y in range(-3, 4)
                       if abs(x) + abs(y) <= 3 and max(abs(x), abs(y)) > 1),
                      key=lambda p: (abs(p[0]) + abs(p[1]), p[1], p[0]))


class PlanningError(ValueError):
    pass


def _integer(value, label, low=0, high=10000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not low <= value <= high:
        raise PlanningError(f"{label}必须是 {low} 到 {high} 的整数。")
    return int(value)


def _scaled(value, tier=0):
    """Game Math.round((10*raw)*(1+tier*.15)), in integer tenths."""
    value = (10 * float(value)) * (1 + tier * .15)
    floor = math.floor(value)
    return floor + int(value - floor >= .5)


def _footprint(b, x=None, y=None):
    x, y = b["x"] if x is None else x, b["y"] if y is None else y
    left, top = x - (b["width"] - 1) // 2, y - (b["height"] - 1) // 2
    return [(xx, yy) for xx in range(left, left + b["width"]) for yy in range(top, top + b["height"])]


def _cancelled(cancel):
    return bool(cancel and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()))


def prepare(snapshot, options):
    """Validate user requests and resolve IDs; never edit the input snapshot."""
    if not isinstance(snapshot, dict) or not isinstance(options, dict):
        raise PlanningError("需要游戏快照和规划选项。")
    bounds = snapshot["bounds"]
    if any(not isinstance(bounds.get(k), int) for k in ("xmin", "xmax", "ymin", "ymax")):
        raise PlanningError("解锁区域坐标无效。")
    mode = options.get("mode", "fresh")
    strategy = options.get("priorityStrategy", "focus")
    if mode not in ("fresh", "migration") or strategy not in ("focus", "balanced", "total"):
        raise PlanningError("未知规划模式或重点策略。")
    moving_main = options.get("allowMainBuildingMoves", True)
    if not isinstance(moving_main, bool):
        raise PlanningError("allowMainBuildingMoves 必须是布尔值。")
    buildings = copy.deepcopy(snapshot.get("buildings", []))
    by_building = {str(b["id"]): b for b in buildings}
    locks = options.get("lockedBuildingIds", [])
    if not isinstance(locks, list) or len({str(v) for v in locks}) != len(locks) or any(str(b) not in by_building for b in locks):
        raise PlanningError("锁定建筑编号无效。")
    locks = {str(b) for b in locks}
    locks.update(str(b["id"]) for b in buildings if b.get("canMove") is False)
    facility_locks = options.get("lockedFacilityIds", [])
    source_facility_ids = {str(f["id"]) for f in snapshot.get("facilities", [])}
    if not isinstance(facility_locks, list) or len({str(v) for v in facility_locks}) != len(facility_locks) or any(str(fid) not in source_facility_ids for fid in facility_locks):
        raise PlanningError("锁定设施编号无效。")
    facility_locks = {str(fid) for fid in facility_locks}
    tribulations = [b for b in buildings if b["template"] == 66]
    if not tribulations or (tribulations[0]["width"], tribulations[0]["height"]) != (3, 3):
        raise PlanningError("需要存档中已有的 3×3 渡劫台。")
    target = tribulations[0]
    current_pads = sorted((b for b in buildings if b["template"] == 67), key=lambda b: (-b["tier"], str(b["id"])))
    count = _integer(options.get("maxPedestals", len(current_pads)), "灵台数量", 0, 64)
    limit = snapshot.get("pedestalLimit", {})
    maximum = limit.get("maximum") if limit.get("status") == "verified" else len(current_pads)
    if count > maximum:
        raise PlanningError(f"灵台数量超过已确认上限 {maximum}。")
    items = copy.deepcopy(snapshot.get("items", []))
    if count > len(items):
        raise PlanningError("现有灵物数量不足以放满所选灵台。")
    if len({str(i["id"]) for i in items}) != len(items):
        raise PlanningError("灵物编号重复。")
    for item in items:
        if len(item.get("elements", [])) != 5 or any(not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0 for n in [item.get("qi"), *item["elements"]]):
            raise PlanningError("灵物属性必须为非负有限数值。")
    pads = []
    for i in range(count):
        old = current_pads[i] if i < len(current_pads) else None
        pads.append({"id": f"p{i + 1}", "buildingId": old["id"] if old else None,
                     "tier": old["tier"] if old else 0,
                     "original": old})
    if any(str(b["id"]) in locks for b in current_pads[count:]):
        raise PlanningError("减少灵台数量会收起锁定的灵台。")
    groups = {(int(g["template"]), int(g["ownerId"])): dict(g) for g in snapshot.get("facilityGroups", [])}
    existing = defaultdict(list)
    for f in snapshot.get("facilities", []):
        key = (int(f["template"]), int(f["ownerId"]))
        existing[key].append(f)
        groups.setdefault(key, {**f, "count": 0})
    requests = options.get("facilityRequests", [])
    if not isinstance(requests, list):
        raise PlanningError("设施数量配置必须是列表。")
    requested = {}
    for row in requests:
        key = (_integer(row.get("template"), "设施模板", 1), _integer(row.get("ownerId"), "归属建筑", 1))
        if key in requested or key not in groups:
            raise PlanningError("设施配置重复或不是存档中已读取的设施类型与归属组合。")
        requested[key] = row
    facilities = []
    for key, group in groups.items():
        request = requested.get(key, {})
        count_f = _integer(request.get("count", len(existing[key])), "设施数量", 0, 200)
        minimum = _integer(request.get("minWorkers", 1), "每设施最少人数", 0, 100)
        owner = by_building.get(str(key[1]))
        if owner is None or owner["width"] not in (2, 3) or owner["height"] not in (2, 3):
            raise PlanningError("设施归属建筑尺寸不在已核实的 2/3 格范围内。")
        for index in range(count_f):
            old = existing[key][index] if index < len(existing[key]) else None
            facilities.append({"id": old["id"] if old else f"new:{key[0]}:{key[1]}:{index + 1}",
                               "template": key[0], "ownerId": key[1], "name": group.get("name", "设施"),
                               "jobType": group["jobType"], "element": group["element"],
                               "minWorkers": 1 if group["jobType"] in RESOURCE_JOBS else minimum,
                               "original": old})
    capacity = Counter(str(f["ownerId"]) for f in facilities)
    if not facility_locks.issubset({str(f["id"]) for f in facilities}):
        raise PlanningError("减少设施数量会收起锁定的设施。")
    for owner_id, total in capacity.items():
        if total > by_building[owner_id]["facilityCapacity"]:
            raise PlanningError(f"{by_building[owner_id]['name']} 的设施总数 {total} 超过容量 {by_building[owner_id]['facilityCapacity']}。")
    people = {str(w["id"]): copy.deepcopy(w) for w in snapshot.get("workers", []) if w.get("inMainCity")}
    worker_locks = options.get("lockedWorkerIds", [])
    if not isinstance(worker_locks, list) or len({str(v) for v in worker_locks}) != len(worker_locks) or any(str(wid) not in people for wid in worker_locks):
        raise PlanningError("锁定人物编号无效或人物不在主城。")
    worker_locks = {str(wid) for wid in worker_locks}
    assignments = options.get("workerAssignments", [])
    if not isinstance(assignments, list):
        raise PlanningError("人员分工必须是列表。")
    selected = {}
    jobs = {f["jobType"] for f in facilities}
    for row in assignments:
        wid, job = str(row.get("workerId")), row.get("jobType")
        person = people.get(wid)
        if wid in selected or person is None or job not in jobs:
            raise PlanningError("人员重复、未在主城，或被分配到没有设施的工种。")
        radius = person.get("rangePixels")
        if not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
            raise PlanningError(f"{person['name']} 的工作范围无法确认。")
        selected[wid] = {**person, "jobType": job}
    requirements = {}
    raw_requirements = options.get("jobRequirements", [])
    if not isinstance(raw_requirements, list):
        raise PlanningError("工种人数配置必须是列表。")
    for row in raw_requirements:
        job = row.get("jobType")
        if not isinstance(job, str) or job in requirements:
            raise PlanningError("工种配置重复或无效。")
        n = _integer(row.get("workerCount"), "工种人数", 0, 100)
        minimum = _integer(row.get("minFacilitiesPerWorker", 0), "每人最少设施", 0, 200)
        allowed_allocations = ("exclusive",) if job in RESOURCE_JOBS else ("station", "exclusive")
        if row.get("allocation", allowed_allocations[0]) not in allowed_allocations:
            raise PlanningError("资源工种需要 exclusive 分工，其他工种需要 station 主要岗位。")
        if n != sum(w["jobType"] == job for w in selected.values()):
            raise PlanningError(f"{job} 的所选人物数与配置人数不一致。")
        requirements[job] = {"workerCount": n, "minFacilitiesPerWorker": minimum}
    for job in jobs:
        n = sum(w["jobType"] == job for w in selected.values())
        targets = [f for f in facilities if f["jobType"] == job]
        if n == 0 and job in RESOURCE_JOBS:
            raise PlanningError(f"{job} 仍有设施，但没有选择工作人员。")
        if job in RESOURCE_JOBS:
            if n * requirements.get(job, {}).get("minFacilitiesPerWorker", 0) > len(targets):
                raise PlanningError(f"{job} 的设施数不足以满足每人独立负责的最低数量。")
        elif n < sum(f["minWorkers"] for f in targets):
            raise PlanningError(f"{job} 人数不足：每位人员只能主要服务一处设施。")
    return {"snapshot": snapshot, "options": options, "bounds": bounds, "buildings": buildings,
            "target": target, "pads": pads, "items": items, "facilities": facilities,
            "people": people, "selected": selected, "requirements": requirements,
            "locks": locks, "facilityLocks": facility_locks, "workerLocks": worker_locks,
            "movingMain": moving_main, "mode": mode, "strategy": strategy}


def _assignment(values):
    """Exact rectangular maximum-weight matching, rows <= columns."""
    n = len(values)
    if n == 0:
        return []
    m = len(values[0])
    if n > m:
        raise PlanningError("可用灵物不足。")
    u, v, p, way = [0] * (n + 1), [0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minimum, used = [float("inf")] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = -values[i0 - 1][j - 1] - u[i0] - v[j]
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
    return result


def _d_upper(data, pads=None):
    pads = data["pads"][:16] if pads is None else pads
    matrix = [[_scaled(i["qi"], p["tier"]) for i in data["items"]] for p in pads]
    match = _assignment(matrix)
    return sum(matrix[r][col] for r, col in enumerate(match))


def _priority(model, values, facilities, strategy, max_value):
    priority = [values[i] for i, f in enumerate(facilities) if f["jobType"] in PRIORITY_JOBS]
    if not priority:
        return []
    if strategy == "focus":
        focus = []
        for job in PRIORITY_JOBS:
            members = [values[i] for i, f in enumerate(facilities) if f["jobType"] == job]
            if members:
                v = model.NewIntVar(0, max_value, "best_" + job)
                model.AddMaxEquality(v, members)
                focus.append(v)
        lowest = model.NewIntVar(0, max_value, "focus_minimum")
        model.AddMinEquality(lowest, focus)
        return [lowest, sum(focus)]
    if strategy == "balanced":
        lowest = model.NewIntVar(0, max_value, "priority_minimum")
        model.AddMinEquality(lowest, priority)
        return [lowest, sum(priority)]
    return [sum(priority)]


def _solver(seconds, cancel):
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.01, seconds)
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 7
    return solver


class _Progress(cp_model.CpSolverSolutionCallback):
    def __init__(self, cancel):
        super().__init__()
        self.cancel = cancel

    def on_solution_callback(self):
        if _cancelled(self.cancel):
            self.StopSearch()


def _lex(model, objectives, seconds, cancel, extract, progress=None):
    deadline, best, states = time.monotonic() + seconds, None, []
    for index, objective in enumerate(objectives or [0]):
        if _cancelled(cancel):
            break
        remaining = deadline - time.monotonic()
        if remaining <= .02:
            break
        model.Maximize(objective)
        solver = _solver(remaining / max(1, len(objectives) - index), cancel)
        status = solver.Solve(model, _Progress(cancel))
        states.append(solver.StatusName(status))
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        best = extract(solver)
        value = int(solver.Value(objective)) if not isinstance(objective, int) else objective
        model.Add(objective == value)
        # Preserve a complete feasible witness across lexicographic stages.
        # Otherwise a later short solve can spend its budget rediscovering one.
        model.ClearHints()
        for variable_index, variable_value in enumerate(solver.ResponseProto().solution):
            model.AddHint(model.GetIntVarFromProtoIndex(variable_index), variable_value)
        if progress:
            progress({"phase": "search", "objective": index + 1, "status": solver.StatusName(status), "value": value})
    return best, states


def _geometry(data, seconds, cancel, progress, *, fixed=None, core=True):
    model = cp_model.CpModel()
    b = data["bounds"]
    width, height = b["xmax"] - b["xmin"] + 1, b["ymax"] - b["ymin"] + 1
    intervals_x, intervals_y, positions, move_terms = [], [], {}, []
    fixed_buildings = {str(v["buildingId"]): v for v in fixed["buildings"]} if fixed else {}
    fixed_pads = {v["id"]: v for v in fixed["pedestals"]} if fixed else {}

    def object_position(key, w=1, h=1, old=None, pinned=None):
        if w > width or h > height:
            raise PlanningError("对象占地大于已解锁区域。")
        left = model.NewIntVar(b["xmin"], b["xmax"] - w + 1, key + "_left")
        top = model.NewIntVar(b["ymin"], b["ymax"] - h + 1, key + "_top")
        x = model.NewIntVar(b["xmin"], b["xmax"], key + "_x")
        y = model.NewIntVar(b["ymin"], b["ymax"], key + "_y")
        model.Add(x == left + (w - 1) // 2)
        model.Add(y == top + (h - 1) // 2)
        intervals_x.append(model.NewFixedSizeIntervalVar(left, w, key + "_ix"))
        intervals_y.append(model.NewFixedSizeIntervalVar(top, h, key + "_iy"))
        positions[key] = (x, y)
        if pinned:
            model.Add(x == pinned["x"])
            model.Add(y == pinned["y"])
        if old:
            if b["xmin"] <= old["x"] <= b["xmax"] and b["ymin"] <= old["y"] <= b["ymax"]:
                model.AddHint(x, old["x"])
                model.AddHint(y, old["y"])
        if old and fixed and data["mode"] == "migration":
            changed_x, changed_y, moved = [model.NewBoolVar(key + tag) for tag in ("_mx", "_my", "_m")]
            model.Add(x == old["x"]).OnlyEnforceIf(changed_x.Not())
            model.Add(x != old["x"]).OnlyEnforceIf(changed_x)
            model.Add(y == old["y"]).OnlyEnforceIf(changed_y.Not())
            model.Add(y != old["y"]).OnlyEnforceIf(changed_y)
            model.AddMaxEquality(moved, [changed_x, changed_y])
            move_terms.append(moved)
        return x, y

    for building in data["buildings"]:
        if building["template"] == 67:
            continue
        key = "b:" + str(building["id"])
        pinned = fixed_buildings.get(str(building["id"])) if fixed else (building if not data["movingMain"] or str(building["id"]) in data["locks"] else None)
        object_position(key, building["width"], building["height"], building, pinned)
    tx, ty = positions["b:" + str(data["target"]["id"])]
    interchangeable = {}
    for i, pad in enumerate(data["pads"]):
        original = pad["original"]
        pinned = fixed_pads.get(pad["id"]) if fixed else (original if str(pad["buildingId"]) in data["locks"] else None)
        x, y = object_position(pad["id"], old=original, pinned=pinned)
        if core and fixed is None and i < 16:
            dx, dy = model.NewIntVar(-3, 3, "core_dx"), model.NewIntVar(-3, 3, "core_dy")
            model.Add(dx == x - tx)
            model.Add(dy == y - ty)
            model.AddAllowedAssignments([dx, dy], CORE_OFFSETS)
            # Equal-tier unlocked pads are physically interchangeable in both
            # modes. Migration rematches identities after item placement, so
            # this symmetry break cannot force unnecessary real-world moves.
            if pinned is None:
                order = y * width + x
                if pad["tier"] in interchangeable:
                    model.Add(order > interchangeable[pad["tier"]])
                interchangeable[pad["tier"]] = order
    for index, resource in enumerate(data["snapshot"].get("resources", [])):
        if b["xmin"] <= resource["x"] <= b["xmax"] and b["ymin"] <= resource["y"] <= b["ymax"]:
            object_position("r:" + str(index), pinned=resource)
    for wid, person in data["people"].items():
        object_position("w:" + wid, old=person,
                        pinned=person if wid not in data["selected"] or wid in data["workerLocks"] else None)
    for facility in data["facilities"]:
        fx, fy = object_position(facility["id"], old=facility["original"],
                                pinned=facility["original"] if str(facility["id"]) in data["facilityLocks"] else None)
        owner = next(v for v in data["buildings"] if v["id"] == facility["ownerId"])
        ox, oy = positions["b:" + str(owner["id"])]
        model.Add(fx >= ox - (owner["width"] - 1) // 2 - 3)
        model.Add(fx <= ox - (owner["width"] - 1) // 2 + owner["width"] - 1 + 3)
        model.Add(fy >= oy - (owner["height"] - 1) // 2 - 3)
        model.Add(fy <= oy - (owner["height"] - 1) // 2 + owner["height"] - 1 + 3)
    model.AddNoOverlap2D(intervals_x, intervals_y)
    worker_assignments, covers, flexible = {}, {}, []
    for wid, person in data["selected"].items():
        wx, wy = positions["w:" + wid]
        own = []
        for f in data["facilities"]:
            if f["jobType"] != person["jobType"]:
                continue
            fx, fy = positions[f["id"]]
            dx, dy = model.NewIntVar(-width, width, "wdx"), model.NewIntVar(-height, height, "wdy")
            model.Add(dx == wx - fx)
            model.Add(dy == wy - fy)
            radius_square = math.floor(person["rangePixels"] ** 2 / 10000)
            assigned = model.NewBoolVar("assigned")
            if fixed:
                square_x, square_y = model.NewIntVar(0, width * width, "wx2"), model.NewIntVar(0, height * height, "wy2")
                model.AddMultiplicationEquality(square_x, [dx, dx])
                model.AddMultiplicationEquality(square_y, [dy, dy])
                near = model.NewBoolVar("near")
                model.Add(square_x + square_y <= radius_square).OnlyEnforceIf(near)
                model.Add(square_x + square_y > radius_square).OnlyEnforceIf(near.Not())
                model.Add(assigned <= near)
                covers[wid, f["id"]] = near
                if f["jobType"] in RESOURCE_JOBS:
                    flexible.append(near - assigned)
            else:
                radius = min(max(width, height), math.isqrt(radius_square))
                allowed = [(xx, yy) for xx in range(-radius, radius + 1) for yy in range(-radius, radius + 1)
                           if xx * xx + yy * yy <= radius_square]
                model.AddAllowedAssignments([dx, dy], allowed).OnlyEnforceIf(assigned)
            worker_assignments[wid, f["id"]] = assigned
            own.append(assigned)
        if person["jobType"] in RESOURCE_JOBS:
            model.Add(sum(own) >= data["requirements"].get(person["jobType"], {}).get("minFacilitiesPerWorker", 0))
        else:
            model.Add(sum(own) == 1)
    for f in data["facilities"]:
        candidates = [v for (wid, fid), v in worker_assignments.items() if fid == f["id"]]
        if f["jobType"] in RESOURCE_JOBS:
            model.Add(sum(candidates) == 1)
        else:
            model.Add(sum(candidates) >= f["minWorkers"])

    def aura_cover(x, y, px, py):
        dx, dy = model.NewIntVar(0, width, "adx"), model.NewIntVar(0, height, "ady")
        model.AddAbsEquality(dx, x - px)
        model.AddAbsEquality(dy, y - py)
        yes = model.NewBoolVar("aura")
        model.Add(dx + dy <= 3).OnlyEnforceIf(yes)
        model.Add(dx + dy > 3).OnlyEnforceIf(yes.Not())
        return yes

    values = []
    maximum_value = max(1, len(data["pads"]))
    if fixed:
        max_attribute = max((sum(_scaled(i["elements"][e], p["tier"]) for p in fixed["pedestals"] for i in data["items"] if i["id"] == p["itemId"]) for e in range(5)), default=0)
        maximum_value = max(1, max_attribute)
        item_by_id = {i["id"]: i for i in data["items"]}
        for f in data["facilities"]:
            x, y = positions[f["id"]]
            index = model.NewIntVar(0, width * height - 1, "tile")
            model.Add(index == (y - b["ymin"]) * width + x - b["xmin"])
            tiles = [sum(_scaled(item_by_id[p["itemId"]]["elements"][f["element"]], p["tier"])
                         for p in fixed["pedestals"] if abs(p["x"] - xx) + abs(p["y"] - yy) <= 3)
                     for yy in range(b["ymin"], b["ymax"] + 1) for xx in range(b["xmin"], b["xmax"] + 1)]
            value = model.NewIntVar(0, maximum_value, "facility_aura")
            model.AddElement(index, tiles, value)
            values.append(value)
        priority = _priority(model, values, data["facilities"], data["strategy"], maximum_value)
        objectives = [*priority, sum(values), sum(flexible)]
    else:
        # First find a complete legal witness, without hundreds of aura-edge
        # decision variables. Compactness is merely a seed heuristic; all
        # reported elemental objectives are evaluated/optimized afterward.
        distances = []
        for f in data["facilities"]:
            fx, fy = positions[f["id"]]
            dx, dy = model.NewIntVar(0, width, "seed_dx"), model.NewIntVar(0, height, "seed_dy")
            model.AddAbsEquality(dx, fx - tx)
            model.AddAbsEquality(dy, fy - ty)
            distances.append((dx + dy) * (10 if f["jobType"] in PRIORITY_JOBS else 1))
        for pad in data["pads"][16:]:
            px, py = positions[pad["id"]]
            dx, dy = model.NewIntVar(0, width, "pad_dx"), model.NewIntVar(0, height, "pad_dy")
            model.AddAbsEquality(dx, px - tx)
            model.AddAbsEquality(dy, py - ty)
            distances.append(dx + dy)
        seed_score = -sum(distances)
        if not core:
            hits = sum(aura_cover(tx, ty, *positions[p["id"]]) for p in data["pads"])
            seed_score += hits * ((width + height) * (10 * len(data["facilities"]) + len(data["pads"])) + 1)
        objectives = [0, seed_score]
    if data["mode"] == "migration" and fixed:
        objectives.append(-sum(move_terms))

    def extract(solver):
        def xy(key):
            x, y = positions[key]
            return {"x": solver.Value(x), "y": solver.Value(y)}
        result = {"buildings": [{"buildingId": v["id"], **xy("b:" + str(v["id"]))} for v in data["buildings"] if v["template"] != 67],
                  "pedestals": [{k: v for k, v in p.items() if k != "original"} | xy(p["id"]) for p in data["pads"]],
                  "facilities": [{k: v for k, v in f.items() if k != "original"} | xy(f["id"]) for f in data["facilities"]],
                  "workers": []}
        if fixed:
            for p in result["pedestals"]:
                p["itemId"] = fixed_pads[p["id"]]["itemId"]
        for wid, person in data["people"].items():
            selected = data["selected"].get(wid)
            result["workers"].append({"workerId": wid, "jobType": selected["jobType"] if selected else person.get("currentTaskType"),
                                      **xy("w:" + wid), "fixed": selected is None,
                                      "facilityIds": [fid for (worker, fid), v in worker_assignments.items() if worker == wid and solver.Value(v)]})
        for f in result["facilities"]:
            f["primaryWorkerIds"] = [wid for (wid, fid), v in worker_assignments.items() if fid == f["id"] and solver.Value(v)]
            f["assistingWorkerIds"] = [w["workerId"] for w in result["workers"]
                                       if w["workerId"] in data["selected"] and w["jobType"] == f["jobType"]
                                       and w["workerId"] not in f["primaryWorkerIds"]
                                       and 10000 * ((w["x"] - f["x"]) ** 2 + (w["y"] - f["y"]) ** 2)
                                       <= data["selected"][w["workerId"]]["rangePixels"] ** 2] if f["jobType"] in RESOURCE_JOBS else []
        return result
    return _lex(model, objectives, seconds, cancel, extract, progress)


def _place_items(data, layout, seconds, cancel, progress):
    pads, items = layout["pedestals"], data["items"]
    if not pads:
        return layout, []
    centre = next(b for b in layout["buildings"] if b["buildingId"] == data["target"]["id"])
    hits = [abs(p["x"] - centre["x"]) + abs(p["y"] - centre["y"]) <= 3 for p in pads]
    upper = _d_upper(data, [p for p, hit in zip(pads, hits) if hit])
    model = cp_model.CpModel()
    selected, qi, elements = [], [], []
    for p in pads:
        choice = model.NewIntVar(0, len(items) - 1, "item")
        selected.append(choice)
        attributes = []
        for e in range(6):
            table = [_scaled(i["qi"] if e == 5 else i["elements"][e], p["tier"]) for i in items]
            v = model.NewIntVar(0, max(table), "item_attribute")
            model.AddElement(choice, table, v)
            attributes.append(v)
        qi.append(attributes[5])
        elements.append(attributes[:5])
    model.AddAllDifferent(selected)
    model.Add(sum(v for v, hit in zip(qi, hits) if hit) == upper)
    facilities = layout["facilities"]
    values = [sum(elements[j][f["element"]] for j, p in enumerate(pads) if abs(f["x"] - p["x"]) + abs(f["y"] - p["y"]) <= 3) for f in facilities]
    max_value = max(1, sum(max(_scaled(v, p["tier"]) for i in items for v in i["elements"]) for p in pads))
    objectives = [*_priority(model, values, facilities, data["strategy"], max_value), sum(values)]
    if data["mode"] == "migration":
        retained = []
        by_id = {i["id"]: j for j, i in enumerate(items)}
        for j, p in enumerate(data["pads"]):
            old = p["original"]
            if old and old.get("itemId") in by_id:
                yes = model.NewBoolVar("retained_item")
                model.Add(selected[j] == by_id[old["itemId"]]).OnlyEnforceIf(yes)
                model.Add(selected[j] != by_id[old["itemId"]]).OnlyEnforceIf(yes.Not())
                retained.append(yes)
        objectives.append(sum(retained))
    # The fallback is a useful complete candidate even when item CP times out.
    # Integer W exceeds every possible secondary sum, so no elemental gain
    # can trade away even one tenth of D in this exact matching.
    focus_ids = set()
    if data["strategy"] == "focus":
        for job in PRIORITY_JOBS:
            candidates = [f for f in facilities if f["jobType"] == job]
            if candidates:
                best = max(candidates, key=lambda f: sum(abs(f["x"] - p["x"]) + abs(f["y"] - p["y"]) <= 3 for p in pads))
                focus_ids.add(best["id"])
    secondary = []
    for p in pads:
        covered = [f for f in facilities if abs(f["x"] - p["x"]) + abs(f["y"] - p["y"]) <= 3]
        secondary.append([sum(_scaled(item["elements"][f["element"]], p["tier"])
                              * (10 if f["id"] in focus_ids or (data["strategy"] != "focus" and f["jobType"] in PRIORITY_JOBS) else 1)
                              for f in covered) for item in items])
    weight = sum(max(row, default=0) for row in secondary) + 1
    matrix = [[(_scaled(item["qi"], p["tier"]) * weight if hits[j] else 0) + secondary[j][i]
               for i, item in enumerate(items)] for j, p in enumerate(pads)]
    match = _assignment(matrix)
    fallback = copy.deepcopy(layout)
    for j, col in enumerate(match):
        model.AddHint(selected[j], col)
        fallback["pedestals"][j]["itemId"] = items[col]["id"]

    def extract(solver):
        result = copy.deepcopy(layout)
        for j, p in enumerate(result["pedestals"]):
            p["itemId"] = items[solver.Value(selected[j])]["id"]
        return result
    result, states = _lex(model, objectives, seconds, cancel, extract, progress)
    return result or fallback, states


def evaluate(snapshot, layout, strategy="focus"):
    items = {i["id"]: i for i in snapshot["items"]}
    source_buildings = {b["id"]: b for b in snapshot["buildings"]}
    centre = next((b for b in layout["buildings"] if source_buildings[b["buildingId"]]["template"] == 66), None)
    pads = layout["pedestals"]
    d = sum(_scaled(items[p["itemId"]]["qi"], p["tier"]) for p in pads if centre and abs(p["x"] - centre["x"]) + abs(p["y"] - centre["y"]) <= 3)
    values, best = [], {}
    for f in layout["facilities"]:
        value = sum(_scaled(items[p["itemId"]]["elements"][f["element"]], p["tier"]) for p in pads if abs(p["x"] - f["x"]) + abs(p["y"] - f["y"]) <= 3)
        values.append({"facilityId": f["id"], "jobType": f["jobType"], "value": value / 10})
        if f["jobType"] in PRIORITY_JOBS and (f["jobType"] not in best or best[f["jobType"]]["value"] < value / 10):
            best[f["jobType"]] = {"facilityId": f["id"], "value": value / 10}
    priority_values = [v["value"] for v in values if v["jobType"] in PRIORITY_JOBS]
    focus_values = [v["value"] for v in best.values()]
    target = focus_values if strategy == "focus" else priority_values
    return {"D": d / 10, "S": round(sum(v["value"] for v in values), 10),
            "priorityStrategy": strategy, "priorityMinimum": min(target, default=0),
            "priorityTotal": round(sum(target), 10), "priorityByJob": best,
            "facilityValues": values,
            "flexCoverage": sum(len(f.get("assistingWorkerIds", [])) for f in layout["facilities"] if f["jobType"] in RESOURCE_JOBS)}


def _moves(snapshot, layout):
    moves = []
    old_buildings = {b["id"]: b for b in snapshot["buildings"]}
    old_facilities = {f["id"]: f for f in snapshot["facilities"]}
    old_workers = {w["id"]: w for w in snapshot.get("workers", []) if w.get("inMainCity")}
    present_buildings, present_facilities = set(), set()
    for kind, values, olds, field in (("building", layout["buildings"], old_buildings, "buildingId"),
                                     ("pedestal", layout["pedestals"], old_buildings, "buildingId"),
                                     ("facility", layout["facilities"], old_facilities, "id"),
                                     ("worker", layout["workers"], old_workers, "workerId")):
        for value in values:
            identity = value.get(field)
            old = olds.get(identity)
            if kind in ("building", "pedestal"):
                present_buildings.add(identity)
            if kind == "facility":
                present_facilities.add(identity)
            action = "create" if old is None else "move" if (old["x"], old["y"]) != (value["x"], value["y"]) else None
            if action:
                moves.append({"kind": kind, "id": identity or value.get("id"), "action": action,
                              "from": {"x": old["x"], "y": old["y"]} if old else None,
                              "to": {"x": value["x"], "y": value["y"]}})
            if kind == "pedestal" and old and old.get("itemId") != value["itemId"]:
                moves.append({"kind": "item", "id": value["itemId"], "action": "replace", "buildingId": identity,
                              "previousItemId": old.get("itemId")})
            if kind == "worker" and old and old.get("currentTaskType") != value.get("jobType"):
                moves.append({"kind": "job", "id": identity, "action": "assign",
                              "previousJobType": old.get("currentTaskType"), "jobType": value.get("jobType")})
    for identity, old in old_buildings.items():
        if identity not in present_buildings:
            moves.append({"kind": "pedestal", "id": identity, "action": "remove", "from": {"x": old["x"], "y": old["y"]}})
    for identity, old in old_facilities.items():
        if identity not in present_facilities:
            moves.append({"kind": "facility", "id": identity, "action": "remove", "from": {"x": old["x"], "y": old["y"]}})
    return moves


def _remap_pedestal_identities(data, layout):
    """Exact move/item-change minimum for interchangeable equal-tier pads.

    Locations, tiers and items are invariant, hence every aura value remains
    identical. Locked buildings retain their identity and position.
    """
    if data["mode"] != "migration":
        return layout
    tiers = defaultdict(list)
    for index, p in enumerate(data["pads"]):
        if str(p["buildingId"]) not in data["locks"]:
            tiers[p["tier"]].append(index)
    previous = copy.deepcopy(layout["pedestals"])
    for indices in tiers.values():
        values = []
        for index in indices:
            old = data["pads"][index]["original"]
            values.append([-(int((old["x"], old["y"]) != (previous[j]["x"], previous[j]["y"]))
                             + int(old.get("itemId") != previous[j]["itemId"])) if old else 0 for j in indices])
        for row, column in enumerate(_assignment(values)):
            index = indices[row]
            chosen = previous[indices[column]]
            layout["pedestals"][index] = {**chosen, "id": data["pads"][index]["id"],
                                          "buildingId": data["pads"][index]["buildingId"]}
    return layout


def _improve_resource_positions(data, layout):
    """Cheap legal coordinate ascent; keeps all primary assignments and aura."""
    source_buildings = {b["id"]: b for b in data["buildings"]}
    occupied = set()
    for b in layout["buildings"]:
        occupied.update(_footprint(source_buildings[b["buildingId"]], b["x"], b["y"]))
    for key in ("pedestals", "facilities", "workers"):
        occupied.update((obj["x"], obj["y"]) for obj in layout[key])
    occupied.update((r["x"], r["y"]) for r in data["snapshot"].get("resources", []))
    by_facility = {f["id"]: f for f in layout["facilities"]}
    bounds = data["bounds"]

    def covers(person, x, y, f):
        return 10000 * ((x - f["x"]) ** 2 + (y - f["y"]) ** 2) <= person["rangePixels"] ** 2

    for _ in range(2):
        for worker in layout["workers"]:
            wid = worker["workerId"]
            person = data["selected"].get(wid)
            if person is None or person["jobType"] not in RESOURCE_JOBS or wid in data["workerLocks"]:
                continue
            main = [by_facility[fid] for fid in worker["facilityIds"]]
            others = [f for f in layout["facilities"] if f["jobType"] == person["jobType"] and f["id"] not in worker["facilityIds"]]
            current = worker["x"], worker["y"]
            original = data["people"][wid]

            def quality(x, y):
                return (sum(covers(person, x, y, f) for f in others),
                        int((x, y) == (original["x"], original["y"])) if data["mode"] == "migration" else 0,
                        -abs(x - current[0]) - abs(y - current[1]))
            best, score = current, quality(*current)
            occupied.remove(current)
            for x in range(bounds["xmin"], bounds["xmax"] + 1):
                for y in range(bounds["ymin"], bounds["ymax"] + 1):
                    if (x, y) in occupied or not all(covers(person, x, y, f) for f in main):
                        continue
                    candidate = quality(x, y)
                    if candidate > score:
                        best, score = (x, y), candidate
            worker["x"], worker["y"] = best
            occupied.add(best)
    for f in layout["facilities"]:
        if f["jobType"] not in RESOURCE_JOBS:
            continue
        f["assistingWorkerIds"] = [w["workerId"] for w in layout["workers"]
                                   if w["workerId"] in data["selected"] and w["jobType"] == f["jobType"]
                                   and f["id"] not in w["facilityIds"]
                                   and covers(data["selected"][w["workerId"]], w["x"], w["y"], f)]
    return layout


def solve(snapshot, options, progress=None, cancel=None):
    start = time.monotonic()
    try:
        data = prepare(snapshot, options)
    except (PlanningError, KeyError, TypeError, ValueError) as exc:
        return {"status": "invalid", "layout": None, "metrics": None, "proof": {"DProven": False, "globalOptimal": False}, "moves": [], "diagnostics": {"errors": [str(exc)]}}
    seconds = options.get("seconds", 30)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 600:
        return {"status": "invalid", "layout": None, "diagnostics": {"errors": ["计算时长必须是 1 到 600 秒。"]}}
    upper = _d_upper(data) / 10
    if _cancelled(cancel):
        return {"status": "cancelled", "layout": None, "diagnostics": {"errors": []}}
    if progress:
        progress({"phase": "geometry", "message": "寻找建筑、灵台、设施与人员的合法共同布局", "DUpperBound": upper})
    layout, geometry_states = _geometry(data, seconds * .42, cancel, progress)
    fallback_states = []
    if layout is None and not _cancelled(cancel):
        # A forced full core may be blocked. Relax it instead of falsely
        # declaring the entire city problem infeasible.
        layout, fallback_states = _geometry(data, seconds * .18, cancel, progress, core=False)
    if layout is None:
        return {"status": "cancelled" if _cancelled(cancel) else "unknown", "layout": None,
                "metrics": None, "proof": {"DProven": False, "DUpperBound": upper, "globalOptimal": False}, "moves": [],
                "diagnostics": {"errors": ["限时内未找到完整合法布局；这不等于已证明无解。"], "geometry": geometry_states, "fallback": fallback_states, "elapsedSeconds": time.monotonic() - start}}
    remaining = max(.01, seconds - (time.monotonic() - start))
    if progress:
        progress({"phase": "items", "message": "在合法布局内分配唯一灵物并优化重点设施"})
    layout, item_states = _place_items(data, layout, remaining * .65, cancel, progress)
    layout = _remap_pedestal_identities(data, layout)
    metrics = evaluate(snapshot, layout, data["strategy"])
    remaining = seconds - (time.monotonic() - start)
    support_states = []
    if remaining > .1 and not _cancelled(cancel):
        if progress:
            progress({"phase": "refine", "message": "固定已选灵台与灵物，继续优化设施和人员"})
        candidate, support_states = _geometry(data, remaining, cancel, progress, fixed=layout)
        if candidate:
            candidate_metrics = evaluate(snapshot, candidate, data["strategy"])
            def quality(m, candidate_layout):
                return (m["D"], *((m["priorityMinimum"],) if data["strategy"] != "total" else ()), m["priorityTotal"], m["S"], m["flexCoverage"],
                        *((-len(_moves(snapshot, candidate_layout)),) if data["mode"] == "migration" else ()))
            if quality(candidate_metrics, candidate) >= quality(metrics, layout):
                layout, metrics = candidate, candidate_metrics
    layout = _improve_resource_positions(data, layout)
    layout = _remap_pedestal_identities(data, layout)
    metrics = evaluate(snapshot, layout, data["strategy"])
    moves = _moves(snapshot, layout)
    metrics["moveCount"] = len(moves)
    return {"status": "feasible", "layout": layout, "metrics": metrics, "moves": moves,
            "proof": {"DProven": abs(metrics["D"] - upper) < 1e-8, "DUpperBound": upper,
                      "globalOptimal": False, "priorityProven": False, "migrationProven": False,
                      "scope": "candidate-and-fixed-emitter-refinement",
                      "description": "渡劫灵气仅在达到库存与实际灵台等阶的匹配上界时证明最优；重点、其他设施、协助覆盖和搬迁数为限时候选结果。"},
            "diagnostics": {"geometry": geometry_states, "fallback": fallback_states, "items": item_states,
                            "refinement": support_states, "elapsedSeconds": time.monotonic() - start,
                            "cancelled": _cancelled(cancel), "warnings": ["未选人物保留原位；资源额外覆盖表示可协助范围，不代表同时满速生产。"]}}
