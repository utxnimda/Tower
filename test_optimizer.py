import copy
import threading
import unittest
from unittest.mock import patch

from optimizer import (_assignment, _assign_facilities, _covers, _current_layout, _extract, _make_model,
                       _prepare, _scaled, evaluate, solve)
from validation import validate_result


def sample():
    return {
        "bounds": {"xmin": -3, "xmax": 6, "ymin": -3, "ymax": 3},
        "items": [{"id": "19:1", "qi": 10, "elements": [1, 2, 3, 4, 5], "placedBuildingId": 11},
                  {"id": "19:2", "qi": 7.5, "elements": [8, 2, 3, 4, 5]}],
        "buildings": [
            {"id": 7, "template": 3, "x": 5, "y": 0, "width": 2, "height": 2},
            {"id": 10, "template": 66, "x": 0, "y": 0, "width": 3, "height": 3},
            {"id": 11, "template": 67, "x": 2, "y": 0, "width": 1, "height": 1, "tier": 1,
             "itemId": "19:1"}],
        "facilities": [{"id": "f1", "template": 1, "ownerId": 7, "element": 0, "x": 3, "y": 0}],
        "facilityGroups": [{"template": 1, "ownerId": 7, "element": 0, "name": "金设施", "count": 1}],
        "resources": []}


def staffed_sample():
    snapshot = sample()
    snapshot["workerConstraintVersion"] = 1
    snapshot["facilities"][0]["jobType"] = "伐木"
    snapshot["facilityGroups"][0]["jobType"] = "伐木"
    snapshot["workers"] = [
        {"id": "w1", "name": "所选人员", "x": -2, "y": 0, "inMainCity": True,
         "selectable": True, "rangePixels": 150, "suggestedJobType": "伐木", "settingState": "自动"},
        {"id": "w2", "name": "保留原位", "x": -3, "y": -3, "inMainCity": True,
         "selectable": True, "rangePixels": 200, "suggestedJobType": None, "settingState": "自动"},
        {"id": "w3", "name": "城外人员", "x": 0, "y": 0, "inMainCity": False,
         "selectable": False, "rangePixels": 500, "suggestedJobType": None}]
    snapshot["defaultWorkerAssignments"] = [{"workerId": "w1", "jobType": "伐木"}]
    return snapshot


class EvaluationTests(unittest.TestCase):
    def test_center_not_footprint_and_js_rounding(self):
        snapshot = sample()
        metrics = evaluate(snapshot)
        self.assertEqual(metrics["D"], 11.5)
        self.assertEqual(metrics["S"], 1.2)
        snapshot["buildings"][-1]["x"] = 3
        snapshot["buildings"][-1]["y"] = 1
        self.assertEqual(evaluate(snapshot)["D"], 0)
        self.assertEqual(_scaled(1, 1), 12)
        self.assertEqual(_scaled(1.01, 1), 12)

    def test_independent_qi_not_element_sum(self):
        snapshot = sample()
        snapshot["items"][0]["qi"] = 3.2
        snapshot["items"][0]["elements"] = [100] * 5
        self.assertEqual(evaluate(snapshot)["D"], 3.7)

    def test_game_ordered_binary_float_rounding(self):
        for raw, tier, expected in ((4.6, 5, 81), (5, 1, 57), (1, 1, 12), (3, 7, 61)):
            with self.subTest(raw=raw, tier=tier):
                self.assertEqual(_scaled(raw, tier), expected)

    def test_assignment_uses_each_inventory_item_once(self):
        assignment, score = _assignment([[10, 9, 0], [10, 1, 0]])
        self.assertEqual(assignment, [1, 0])
        self.assertEqual(score, 19)


class ConstraintTests(unittest.TestCase):
    def test_tier_inventory_not_upgraded(self):
        snapshot = sample()
        data = _prepare(snapshot, {"maxPedestals": 2, "newPedestalTier": 0})
        self.assertEqual([p["tier"] for p in data["pads"]], [1, 0])

    def test_zero_request_explicitly_removes_group(self):
        data = _prepare(sample(), {"facilityRequests": [{"template": 1, "ownerId": 7, "count": 0}]})
        self.assertFalse(data["groups"])
        self.assertFalse(data["fixedFacilities"])
        self.assertTrue(any("收起" in w for w in data["warnings"]))

    def test_omitted_request_preserves_position(self):
        data = _prepare(sample(), {"facilityRequests": []})
        self.assertEqual(data["fixedFacilities"][0]["x"], 3)
        self.assertIn((3, 0), data["area"] - data["free"])

    def test_rect_and_fixed_parent_limit(self):
        data = _prepare(sample(), {})
        self.assertTrue(all(x >= 2 for x, y in data["groups"][0]["candidates"]))
        self.assertNotIn((5, 0), data["free"])

    def test_parent_capacity_rejects_excess_before_solving(self):
        snapshot = sample()
        snapshot["buildings"][0]["facilityCapacity"] = 2
        result = solve(snapshot, {"facilityRequests": [{"template": 1, "ownerId": 7, "count": 3}]})
        self.assertEqual(result["diagnostics"]["status"], "infeasible")
        self.assertIn("设施总数为 3", result["diagnostics"]["message"])
        self.assertIn("允许的 2 个", result["diagnostics"]["message"])

    def test_multiple_templates_share_one_parent_capacity(self):
        snapshot = sample()
        snapshot["buildings"][0]["facilityCapacity"] = 2
        snapshot["facilityGroups"].append({"template": 2, "ownerId": 7, "element": 1, "count": 0})
        requests = [{"template": 1, "ownerId": 7, "count": 1}, {"template": 2, "ownerId": 7, "count": 1}]
        self.assertEqual(sum(g["count"] for g in _prepare(snapshot, {"facilityRequests": requests})["groups"]), 2)
        requests[1]["count"] = 2
        with self.assertRaisesRegex(ValueError, "设施总数为 3"):
            _prepare(snapshot, {"facilityRequests": requests})

    def test_fixed_facilities_consume_parent_capacity(self):
        snapshot = sample()
        snapshot["buildings"][0]["facilityCapacity"] = 2
        snapshot["facilityGroups"].append({"template": 2, "ownerId": 7, "element": 1, "count": 0})
        # Template 1 is omitted, so its existing one facility remains fixed.
        with self.assertRaisesRegex(ValueError, "设施总数为 3"):
            _prepare(snapshot, {"facilityRequests": [{"template": 2, "ownerId": 7, "count": 2}]})
        data = _prepare(snapshot, {"facilityRequests": [{"template": 1, "ownerId": 7, "count": 0},
                                                       {"template": 2, "ownerId": 7, "count": 2}]})
        self.assertFalse(data["fixedFacilities"])
        self.assertEqual(data["groups"][0]["count"], 2)

    def test_capacity_fallback_follows_category_and_tier(self):
        snapshot = sample()
        snapshot["buildings"][0].update(category="资源", tier=1)
        request = {"template": 1, "ownerId": 7, "count": 8}
        self.assertEqual(_prepare(snapshot, {"facilityRequests": [request]})["groups"][0]["count"], 8)
        request["count"] = 9
        with self.assertRaisesRegex(ValueError, "允许的 8 个"):
            _prepare(snapshot, {"facilityRequests": [request]})
        snapshot["buildings"][0]["category"] = "生产"
        request["count"] = 4
        with self.assertRaisesRegex(ValueError, "允许的 3 个"):
            _prepare(snapshot, {"facilityRequests": [request]})

    def test_infeasible_is_not_heuristic_failure(self):
        result = solve(sample(), {"facilityRequests": [{"template": 1, "ownerId": 7, "count": 400}]})
        self.assertIsNone(result["strict"])
        self.assertEqual(result["diagnostics"]["status"], "infeasible")

    def test_cancel_never_claims_an_optimum_without_solution(self):
        cancelled = threading.Event()
        cancelled.set()
        result = solve(sample(), {"seconds": 1}, cancel_event=cancelled)
        self.assertIsNone(result["strict"])
        self.assertEqual(result["diagnostics"]["status"], "cancelled")

    def test_solver_layout_and_balanced_phase(self):
        snapshot = sample()
        options = {"maxPedestals": 1, "seconds": 5, "delta": 5, "weight": 1,
                   "facilityRequests": [{"template": 1, "ownerId": 7, "count": 1}]}
        result = solve(snapshot, options)
        self.assertIsNotNone(result["strict"])
        self.assertEqual(result["strict"]["D"], 11.5)
        self.assertTrue(result["strict"]["DProvenOptimal"])
        validate_result(snapshot, options, result)
        self.assertIsNotNone(result["balanced"])
        self.assertGreaterEqual(result["balanced"]["D"], 6.5)
        self.assertGreaterEqual(result["balanced"]["score"], 12.7)
        self.assertEqual(snapshot, sample(), "Solving must not mutate parsed save data")

    def test_equal_exchange_score_prefers_more_facility_benefit(self):
        snapshot = sample()
        snapshot["buildings"][-1]["tier"] = 0
        snapshot["items"][0].update(qi=10, elements=[0, 0, 0, 0, 0])
        snapshot["items"][1].update(qi=9, elements=[10, 0, 0, 0, 0])
        result = solve(snapshot, {"maxPedestals": 1, "seconds": 5, "delta": 1, "weight": 10})
        self.assertEqual(result["strict"]["D"], 10)
        self.assertEqual(result["balanced"]["D"], 9)
        self.assertEqual(result["balanced"]["S"], 10)
        self.assertTrue(result["balanced"]["DMaximumProven"])
        self.assertFalse(result["balanced"]["DProvenOptimal"])


class WorkerConstraintTests(unittest.TestCase):
    def test_missing_ortools_is_reported_before_worker_initial_search(self):
        with patch.dict("sys.modules", {"ortools": None, "ortools.sat": None,
                                         "ortools.sat.python": None, "ortools.sat.python.cp_model": None}):
            result = solve(staffed_sample(), {"seconds": 1})
        self.assertEqual(result["diagnostics"]["status"], "error")
        self.assertEqual(result["diagnostics"]["code"], "missing_ortools")
        self.assertIn("OR-Tools", result["diagnostics"]["message"])
        self.assertIsNone(result["strict"])

    def test_baseline_keeps_known_task_without_counting_fixed_staff_in_plans(self):
        snapshot = staffed_sample()
        snapshot["workers"][0].update(x=3, y=-1, currentTaskType="伐木")
        baseline = _current_layout(snapshot)
        self.assertEqual(baseline["workers"][0]["jobType"], "伐木")
        self.assertEqual(evaluate(snapshot)["workerCoverage"]["coveredFacilities"], 1)
        # The same unchanged person must not silently serve a proposed layout.
        self.assertEqual(evaluate(snapshot, baseline)["workerCoverage"]["coveredFacilities"], 0)
        cancelled = threading.Event()
        cancelled.set()
        result = solve(snapshot, {"workerAssignments": [{"workerId": "w1", "jobType": "伐木"}]}, cancel_event=cancelled)
        self.assertEqual(result["baseline"]["workers"][0]["jobType"], "伐木")
        self.assertEqual(result["baseline"]["workerCoverage"]["coveredFacilities"], 1)
        self.assertFalse(result["baseline"]["workerCoverage"]["validated"])

    def test_euclidean_diagonal_and_individual_range(self):
        worker = {"x": 0, "y": 0, "jobType": "伐木", "rangePixels": 150, "fixed": False}
        self.assertTrue(_covers(worker, (1, 1), "伐木"))
        self.assertFalse(_covers(worker, (2, 0), "伐木"))
        self.assertFalse(_covers(worker, (1, 0), "采矿"))
        worker["rangePixels"] = 200
        self.assertTrue(_covers(worker, (2, 0), "伐木"))
        worker["fixed"] = True
        self.assertFalse(_covers(worker, (1, 0), "伐木"))

    def test_fixed_people_block_cells_and_selected_origin_is_released(self):
        data = _prepare(staffed_sample(), {"workerAssignments": [{"workerId": "w1", "jobType": "伐木"}]})
        self.assertNotIn((-3, -3), data["free"])
        self.assertIn((-2, 0), data["free"])
        self.assertEqual([w["workerId"] for w in data["fixedWorkers"]], ["w2"])

    def test_rejects_duplicate_and_undeployed_people(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            _prepare(staffed_sample(), {"workerAssignments": [{"workerId": "w1", "jobType": "伐木"}] * 2})
        with self.assertRaisesRegex(ValueError, "已部署在主城"):
            _prepare(staffed_sample(), {"workerAssignments": [{"workerId": "w3", "jobType": "伐木"}]})

    def test_missing_staff_is_infeasible_and_defaults_are_labelled_suggestions(self):
        result = solve(staffed_sample(), {"workerAssignments": []})
        self.assertEqual(result["diagnostics"]["status"], "infeasible")
        self.assertIn("未分配伐木人员", result["diagnostics"]["message"])
        self.assertTrue(any("不代表" in w for w in _prepare(staffed_sample(), {})["warnings"]))

    def test_joint_assignment_moves_staff_instead_of_rejecting_scattered_plan(self):
        data = _prepare(staffed_sample(), {})
        pads = [{"x": 2, "y": 0, "tier": 1, "itemId": "19:1"}]
        original_workers = data["fixedWorkers"] + data["workers"]
        self.assertIsNone(_assign_facilities(data, (0, 0), pads, workers=original_workers))
        support = _assign_facilities(data, (0, 0), pads, seconds=1)
        self.assertIsNotNone(support)
        selected = next(w for w in support["workers"] if not w["fixed"])
        self.assertTrue(all(_covers(selected, (f["x"], f["y"]), f["jobType"]) for f in support["facilities"]))
        self.assertNotEqual((selected["x"], selected["y"]), (2, 0))

    def test_full_cp_model_places_and_covers_fixed_facility(self):
        from ortools.sat.python import cp_model
        snapshot = staffed_sample()
        options = {"maxPedestals": 0, "facilityRequests": [],
                   "workerAssignments": [{"workerId": "w1", "jobType": "伐木"}]}
        data = _prepare(snapshot, options)
        ctx = _make_model(data, 0)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 2
        solver.parameters.max_time_in_seconds = 2
        self.assertIn(solver.Solve(ctx["model"]), (cp_model.OPTIMAL, cp_model.FEASIBLE))
        layout = _extract(snapshot, data, ctx, solver)
        result = {"strict": layout}
        validate_result(snapshot, options, result)
        self.assertTrue(result["strict"]["workerCoverage"]["validated"])
        self.assertEqual(result["strict"]["workerCoverage"]["coveredFacilities"], 1)
        fixed = next(w for w in layout["workers"] if w["workerId"] == "w2")
        self.assertEqual((fixed["x"], fixed["y"]), (-3, -3))

    def test_same_worker_can_cover_different_owners_and_user_cannot_override_range(self):
        snapshot = staffed_sample()
        snapshot["buildings"].append({"id": 8, "template": 4, "x": 5, "y": -3, "width": 1, "height": 1})
        snapshot["facilityGroups"].append({"template": 2, "ownerId": 8, "element": 1,
                                           "name": "第二父建筑设施", "count": 1, "jobType": "伐木"})
        options = {"maxPedestals": 1, "seconds": 5, "workerAssignments": [{"workerId": "w1", "jobType": "伐木", "rangePixels": 9999}],
                   "facilityRequests": [{"template": 1, "ownerId": 7, "count": 1}, {"template": 2, "ownerId": 8, "count": 1}]}
        result = solve(snapshot, options)
        self.assertIsNotNone(result["strict"])
        validate_result(snapshot, options, result)
        self.assertEqual(result["strict"]["workerCoverage"]["coveredFacilities"], 2)
        selected = next(w for w in result["strict"]["workers"] if not w["fixed"])
        self.assertEqual(selected["rangePixels"], 150)
        self.assertEqual(result["strict"]["D"], 11.5)
        self.assertTrue(result["strict"]["DProvenOptimal"])


if __name__ == "__main__":
    unittest.main()
