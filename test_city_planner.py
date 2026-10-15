import copy
import itertools
import threading
import unittest

from city_planner import CORE_OFFSETS, PlanningError, _assignment, _d_upper, _improve_resource_positions, _moves, _place_items, _remap_pedestal_identities, evaluate, prepare, solve


def snapshot(pads=0, forests=0):
    buildings = [{"id": 1, "template": 66, "name": "渡劫台", "x": 6, "y": 6,
                  "width": 3, "height": 3, "tier": 0, "facilityCapacity": 2}]
    if forests:
        buildings.append({"id": 2, "template": 3, "name": "伐木屋", "x": 3, "y": 3,
                          "width": 2, "height": 2, "tier": 2, "facilityCapacity": 12})
    for i in range(pads):
        dx, dy = CORE_OFFSETS[i % 16]
        buildings.append({"id": i + 10, "template": 67, "name": "置物台", "x": 6 + dx, "y": 6 + dy,
                          "width": 1, "height": 1, "tier": 0, "facilityCapacity": 2, "itemId": f"i{i}"})
    facilities = [{"id": f"f{i}", "template": 1, "ownerId": 2, "jobType": "伐木", "element": 1,
                   "name": "林地", "x": 1 + i % 4, "y": 1 + i // 4} for i in range(forests)]
    return {"schemaVersion": 2, "bounds": {"xmin": 1, "xmax": 12, "ymin": 1, "ymax": 12},
            "buildings": buildings, "items": [{"id": f"i{i}", "qi": i + 1, "elements": [1, 2, 3, 4, 5]} for i in range(max(20, pads))],
            "facilities": facilities, "facilityGroups": ([{"template": 1, "ownerId": 2, "jobType": "伐木", "element": 1, "name": "林地", "count": forests}] if forests else []),
            "resources": [], "workers": [], "pedestalLimit": {"status": "verified", "maximum": 27}}


def forestry_options(n=7):
    return {"seconds": 4, "maxPedestals": 0, "allowMainBuildingMoves": True,
            "facilityRequests": [{"template": 1, "ownerId": 2, "count": n}],
            "workerAssignments": [{"workerId": "w1", "jobType": "伐木"}, {"workerId": "w2", "jobType": "伐木"}],
            "jobRequirements": [{"jobType": "伐木", "workerCount": 2, "minFacilitiesPerWorker": 3, "allocation": "exclusive"}]}


class CityPlannerTests(unittest.TestCase):
    def forest(self, n=7):
        data = snapshot(forests=n)
        data["workers"] = [{"id": f"w{i}", "name": f"人员{i}", "x": i, "y": 10,
                            "inMainCity": True, "rangePixels": 150} for i in (1, 2)]
        return data

    def test_matching_is_exact_against_small_exhaustion(self):
        weights = [[5, 10, 2, 7], [8, 6, 9, 3], [1, 8, 5, 12]]
        match = _assignment(weights)
        self.assertEqual(sum(row[j] for row, j in zip(weights, match)),
                         max(sum(weights[i][j] for i, j in enumerate(p)) for p in itertools.permutations(range(4), 3)))

    def test_core_capacity_and_inventory_upper_bound(self):
        self.assertEqual(len(CORE_OFFSETS), 16)
        self.assertEqual(set(CORE_OFFSETS), {(x, y) for x in range(-8, 9) for y in range(-8, 9)
                                          if abs(x) + abs(y) <= 3 and max(abs(x), abs(y)) > 1})
        data = prepare(snapshot(16), {"maxPedestals": 16})
        self.assertEqual(_d_upper(data) / 10, sum(range(5, 21)))

    def test_two_workers_seven_facilities_have_unique_three_plus_four_allocation(self):
        source, options = self.forest(), forestry_options()
        original = copy.deepcopy(source)
        result = solve(source, options)
        self.assertEqual(result["status"], "feasible", result)
        layout = result["layout"]
        allocations = [w["facilityIds"] for w in layout["workers"]]
        self.assertEqual(sorted(map(len, allocations)), [3, 4])
        self.assertEqual(len(set(sum(allocations, []))), 7)
        positions = {w["workerId"]: w for w in layout["workers"]}
        for f in layout["facilities"]:
            self.assertEqual(len(f["primaryWorkerIds"]), 1)
            w = positions[f["primaryWorkerIds"][0]]
            self.assertLessEqual(10000 * ((w["x"] - f["x"]) ** 2 + (w["y"] - f["y"]) ** 2), 150 ** 2)
        self.assertEqual(source, original)

    def test_two_workers_five_facilities_cannot_claim_three_each(self):
        result = solve(self.forest(5), forestry_options(5))
        self.assertEqual(result["status"], "invalid")
        self.assertIn("最低数量", result["diagnostics"]["errors"][0])

    def test_nonresource_workers_cannot_count_for_multiple_primary_facilities(self):
        source = self.forest(3)
        for f in source["facilities"]:
            f["jobType"], f["element"] = "研究", 2
        source["facilityGroups"][0]["jobType"] = "研究"
        options = forestry_options(3)
        for w in options["workerAssignments"]:
            w["jobType"] = "研究"
        options["jobRequirements"] = [{"jobType": "研究", "workerCount": 2}]
        result = solve(source, options)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("只能主要服务一处", result["diagnostics"]["errors"][0])

    def test_full_core_attains_proven_D_without_claiming_global_all_objectives(self):
        source = snapshot(16)
        result = solve(source, {"maxPedestals": 16, "seconds": 3})
        self.assertEqual(result["status"], "feasible", result)
        self.assertTrue(result["proof"]["DProven"])
        self.assertEqual(result["metrics"]["D"], sum(range(5, 21)))
        self.assertFalse(result["proof"]["globalOptimal"])
        self.assertEqual(len({p["itemId"] for p in result["layout"]["pedestals"]}), 16)

    def test_unselected_workers_and_locked_main_building_remain_at_saved_positions(self):
        source = snapshot()
        source["workers"] = [{"id": "w1", "name": "未选", "x": 1, "y": 1, "inMainCity": True, "rangePixels": 150}]
        result = solve(source, {"maxPedestals": 0, "seconds": 2, "mode": "migration", "lockedBuildingIds": [1]})
        self.assertEqual(result["status"], "feasible", result)
        self.assertEqual(result["layout"]["buildings"][0], {"buildingId": 1, "x": 6, "y": 6})
        worker = result["layout"]["workers"][0]
        self.assertEqual((worker["x"], worker["y"], worker["fixed"], worker["facilityIds"]), (1, 1, True, []))

    def test_focus_balanced_and_total_measure_the_requested_facilities(self):
        source = snapshot(1)
        source["items"][0]["elements"] = [0, 0, 8, 0, 4]
        layout = {"buildings": [{"buildingId": 1, "x": 6, "y": 6}],
                  "pedestals": [{"itemId": "i0", "x": 3, "y": 3, "tier": 0}],
                  "facilities": [{"id": "r1", "jobType": "研究", "element": 2, "x": 3, "y": 4},
                                 {"id": "r2", "jobType": "研究", "element": 2, "x": 10, "y": 10},
                                 {"id": "t1", "jobType": "操练", "element": 4, "x": 4, "y": 3}]}
        focus, balanced, total = [evaluate(source, layout, strategy) for strategy in ("focus", "balanced", "total")]
        self.assertEqual((focus["priorityMinimum"], focus["priorityTotal"]), (4, 12))
        self.assertEqual((balanced["priorityMinimum"], balanced["priorityTotal"]), (0, 12))
        self.assertEqual(total["priorityTotal"], 12)

    def test_cancellation_and_invalid_options(self):
        event = threading.Event()
        event.set()
        self.assertEqual(solve(snapshot(), {"maxPedestals": 0}, cancel=event)["status"], "cancelled")
        for n in (-1, 1.5, True, 65):
            self.assertEqual(solve(snapshot(), {"maxPedestals": n})["status"], "invalid")

    def test_facility_and_selected_worker_locks_survive_all_stages(self):
        source = self.forest(1)
        source["workers"] = [source["workers"][0]]
        source["workers"][0].update(x=2, y=1)
        options = {"seconds": 2, "maxPedestals": 0, "lockedFacilityIds": ["f0"],
                   "lockedWorkerIds": ["w1"], "workerAssignments": [{"workerId": "w1", "jobType": "伐木"}],
                   "jobRequirements": [{"jobType": "伐木", "workerCount": 1, "minFacilitiesPerWorker": 1}]}
        result = solve(source, options)
        self.assertEqual(result["status"], "feasible", result)
        self.assertEqual((result["layout"]["facilities"][0]["x"], result["layout"]["facilities"][0]["y"]), (1, 1))
        worker = result["layout"]["workers"][0]
        self.assertEqual((worker["x"], worker["y"], worker["facilityIds"]), (2, 1, ["f0"]))
        options["facilityRequests"] = [{"template": 1, "ownerId": 2, "count": 0}]
        self.assertEqual(solve(source, options)["status"], "invalid")

    def test_nonresource_zero_minimum_can_be_unstaffed(self):
        source = self.forest(1)
        source["workers"] = []
        source["facilities"][0]["jobType"] = "研究"
        source["facilityGroups"][0]["jobType"] = "研究"
        options = {"seconds": 2, "maxPedestals": 0,
                   "facilityRequests": [{"template": 1, "ownerId": 2, "count": 1, "minWorkers": 0}],
                   "jobRequirements": [{"jobType": "研究", "workerCount": 0, "allocation": "station"}]}
        result = solve(source, options)
        self.assertEqual(result["status"], "feasible", result)
        self.assertEqual(result["layout"]["facilities"][0]["primaryWorkerIds"], [])

    def test_resource_assistance_improves_without_changing_primary_owners_or_aura(self):
        source = self.forest(2)
        source["buildings"][1].update(x=3, y=5)
        for f, x in zip(source["facilities"], (2, 4)):
            f.update(x=x, y=2)
        for w, x in zip(source["workers"], (1, 5)):
            w.update(x=x, y=2)
        options = forestry_options(2)
        options["jobRequirements"][0]["minFacilitiesPerWorker"] = 1
        data = prepare(source, options)
        layout = {"buildings": [{"buildingId": b["id"], "x": b["x"], "y": b["y"]} for b in source["buildings"]],
                  "pedestals": [], "facilities": copy.deepcopy(source["facilities"]),
                  "workers": [{"workerId": w["id"], "x": w["x"], "y": w["y"], "jobType": "伐木", "fixed": False,
                               "facilityIds": [f"f{i}"]} for i, w in enumerate(source["workers"])]}
        for i, f in enumerate(layout["facilities"]):
            f.update(primaryWorkerIds=[f"w{i + 1}"], assistingWorkerIds=[])
        before = evaluate(source, layout)
        _improve_resource_positions(data, layout)
        after = evaluate(source, layout)
        self.assertEqual((after["D"], after["S"]), (before["D"], before["S"]))
        self.assertEqual(after["flexCoverage"], 2)
        self.assertEqual([w["facilityIds"] for w in layout["workers"]], [["f0"], ["f1"]])
        self.assertEqual(len({(w["x"], w["y"]) for w in layout["workers"]}), 2)
        jobs = [m for m in _moves(source, layout) if m["kind"] == "job"]
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(m["previousJobType"] is None and m["jobType"] == "伐木" for m in jobs))

    def test_item_fallback_uses_outer_element_values_without_sacrificing_D(self):
        source = snapshot(2)
        source["items"][0]["elements"][1] = 999999
        data = prepare(source, {"maxPedestals": 2})
        layout = {"buildings": [{"buildingId": 1, "x": 6, "y": 6}],
                  "pedestals": [{"id": "p1", "buildingId": 10, "tier": 0, "x": 3, "y": 6},
                                {"id": "p2", "buildingId": 11, "tier": 0, "x": 1, "y": 1}],
                  "facilities": [{"id": "forest", "jobType": "伐木", "element": 1, "x": 2, "y": 1}]}
        cancelled = threading.Event()
        cancelled.set()
        result, states = _place_items(data, layout, 1, cancelled, None)
        self.assertEqual(states, [])
        self.assertEqual(result["pedestals"][0]["itemId"], "i19")
        self.assertEqual(result["pedestals"][1]["itemId"], "i0")
        self.assertEqual(evaluate(source, result)["D"], 20)

    def test_equal_tier_identity_matching_reduces_moves_without_changing_aura(self):
        source = snapshot(2)
        data = prepare(source, {"maxPedestals": 2, "mode": "migration"})
        original = source["buildings"][1:]
        layout = {"buildings": [{"buildingId": 1, "x": 6, "y": 6}], "facilities": [], "workers": [],
                  "pedestals": [{"id": f"p{i + 1}", "buildingId": original[i]["id"], "tier": 0,
                                 "x": original[1 - i]["x"], "y": original[1 - i]["y"],
                                 "itemId": original[1 - i]["itemId"]} for i in range(2)]}
        before = evaluate(source, layout)
        self.assertEqual(len(_moves(source, layout)), 4)
        _remap_pedestal_identities(data, layout)
        self.assertEqual(_moves(source, layout), [])
        self.assertEqual(evaluate(source, layout), before)


if __name__ == "__main__":
    unittest.main()
