import copy
import itertools
import unittest

from city_validation import CityValidationError, _inventory_bound, validate_result


class CityValidationTests(unittest.TestCase):
    def setUp(self):
        buildings = [
            {"id": 1, "template": 66, "width": 3, "height": 3, "tier": 0, "x": 10, "y": 10, "facilityCapacity": 0},
            {"id": 2, "template": 7, "width": 2, "height": 2, "tier": 1, "x": 3, "y": 6, "facilityCapacity": 8},
            {"id": 3, "template": 5, "width": 3, "height": 3, "tier": 0, "x": 16, "y": 16, "facilityCapacity": 2},
            {"id": 4, "template": 67, "width": 1, "height": 1, "tier": 1, "x": 10, "y": 7, "itemId": "i1"},
            {"id": 5, "template": 67, "width": 1, "height": 1, "tier": 0, "x": 14, "y": 12, "itemId": "i2"},
            {"id": 6, "template": 6, "width": 3, "height": 3, "tier": 0, "x": 16, "y": 5, "facilityCapacity": 2},
        ]
        forest_xy = [(2, 3), (3, 3), (4, 3), (4, 4), (5, 3), (6, 3), (6, 4)]
        facilities = [{"id": f"f{i}", "template": 51, "ownerId": 2, "jobType": "伐木", "element": 1, "x": x, "y": y}
                      for i, (x, y) in enumerate(forest_xy, 1)]
        facilities += [{"id": "f8", "template": 1, "ownerId": 3, "jobType": "研究", "element": 2, "x": 14, "y": 14},
                       {"id": "f9", "template": 1, "ownerId": 3, "jobType": "研究", "element": 2, "x": 14, "y": 16},
                       {"id": "f10", "template": 11, "ownerId": 6, "jobType": "操练", "element": 4, "x": 14, "y": 3}]
        groups = [{"template": 51, "ownerId": 2, "jobType": "伐木", "element": 1, "count": 7},
                  {"template": 1, "ownerId": 3, "jobType": "研究", "element": 2, "count": 2},
                  {"template": 11, "ownerId": 6, "jobType": "操练", "element": 4, "count": 1}]
        worker_data = [("w1", 3, 4, "伐木", ["f1", "f2", "f3", "f4"]),
                       ("w2", 5, 4, "伐木", ["f5", "f6", "f7"]),
                       ("w3", 13, 14, "研究", ["f8"]), ("w4", 14, 13, "研究", ["f8"]),
                       ("w5", 13, 16, "研究", ["f9"]), ("w6", 13, 3, "操练", ["f10"]),
                       ("w7", 1, 1, None, [])]
        people = [{"id": wid, "name": wid, "x": x, "y": y, "rangePixels": 150,
                   "inMainCity": True, "currentTaskType": job} for wid, x, y, job, _ in worker_data]
        self.snapshot = {"bounds": {"xmin": 1, "xmax": 20, "ymin": 1, "ymax": 20},
                         "buildings": buildings, "facilities": facilities, "facilityGroups": groups,
                         "workers": people, "resources": [],
                         "pedestalLimit": {"status": "verified", "maximum": 2},
                         "items": [{"id": "i1", "qi": 3, "elements": [1, 2, 4, 0, 6]},
                                   {"id": "i2", "qi": 2, "elements": [0, 3, 5, 0, 4]}]}
        self.options = {"maxPedestals": 2, "mode": "fresh", "priorityStrategy": "focus",
                        "facilityRequests": [{"template": g["template"], "ownerId": g["ownerId"], "count": g["count"]} for g in groups],
                        "workerAssignments": [{"workerId": wid, "jobType": job} for wid, _, _, job, _ in worker_data if job],
                        "jobRequirements": [{"jobType": "伐木", "workerCount": 2, "minFacilitiesPerWorker": 3}]}
        workers = [{"workerId": wid, "x": x, "y": y, "jobType": job, "fixed": job is None,
                    "facilityIds": ids} for wid, x, y, job, ids in worker_data]
        actual_facilities = copy.deepcopy(facilities)
        for f in actual_facilities:
            f.update(minWorkers=1, primaryWorkerIds=[w["workerId"] for w in workers if f["id"] in w["facilityIds"]],
                     assistingWorkerIds=["w2"] if f["id"] in ("f3", "f4") else [])
        layout = {"buildings": [{"buildingId": b["id"], "x": b["x"], "y": b["y"]} for b in buildings if b["template"] != 67],
                  "pedestals": [{"id": "p1", "buildingId": 4, "tier": 1, "x": 10, "y": 7, "itemId": "i1"},
                                {"id": "p2", "buildingId": 5, "tier": 0, "x": 14, "y": 12, "itemId": "i2"}],
                  "facilities": actual_facilities, "workers": workers}
        self.result = {"status": "feasible", "layout": layout, "moves": [],
                       "metrics": {"D": 3.5, "S": 5, "priorityStrategy": "focus", "priorityMinimum": 0, "priorityTotal": 5,
                                   "priorityByJob": {"研究": {"facilityId": "f8", "value": 5}, "操练": {"facilityId": "f10", "value": 0}},
                                   "facilityValues": [{"facilityId": f["id"], "jobType": f["jobType"], "value": 5 if f["id"] == "f8" else 0} for f in facilities],
                                   "flexCoverage": 2, "moveCount": 0},
                       "proof": {"DUpperBound": 5.5, "DProven": False, "globalOptimal": False,
                                 "priorityProven": False, "migrationProven": False}}

    def validate(self):
        return validate_result(self.snapshot, self.options, self.result)

    def test_valid_shared_resource_coverage_and_multiple_people_per_study_facility(self):
        self.validate()
        report = self.result["validation"]
        self.assertTrue(report["passed"])
        self.assertEqual(report["assistingRelationships"], 2)
        self.assertEqual(report["facilityCount"], 10)
        self.assertEqual(next(f for f in report["facilityEnvironment"] if f["facilityId"] == "f8")["environmentBonusPercent"], 2.5)

    def test_reject_missing_duplicate_or_unknown_identity(self):
        for key, change in (("buildings", lambda a: a.pop()), ("buildings", lambda a: a.append(dict(a[0]))),
                            ("workers", lambda a: a.pop()), ("facilities", lambda a: a[0].update(id="fake")),
                            ("pedestals", lambda a: a[0].update(buildingId=999))):
            with self.subTest(key=key):
                result = copy.deepcopy(self.result)
                change(result["layout"][key])
                with self.assertRaises(CityValidationError):
                    validate_result(self.snapshot, self.options, result)

    def test_all_occupied_tiles_and_natural_resources_are_checked(self):
        self.result["layout"]["pedestals"][0].update(x=9, y=9)
        with self.assertRaisesRegex(CityValidationError, "占格重叠"):
            self.validate()
        self.result["layout"]["pedestals"][0].update(x=10, y=7)
        self.snapshot["resources"] = [{"x": 3, "y": 4}]
        with self.assertRaisesRegex(CityValidationError, "占格重叠"):
            self.validate()

    def test_real_building_footprint_cannot_extend_beyond_unlock(self):
        self.result["layout"]["buildings"][0].update(x=1, y=1, width=1, height=1)
        with self.assertRaisesRegex(CityValidationError, "已解锁"):
            self.validate()

    def test_locked_buildings_and_disabled_main_moves(self):
        self.result["layout"]["buildings"][2]["x"] = 17
        for option in ({"lockedBuildingIds": [3]}, {"allowMainBuildingMoves": False}):
            with self.subTest(option=option):
                with self.assertRaisesRegex(CityValidationError, "禁止搬迁"):
                    validate_result(self.snapshot, {**self.options, **option}, self.result)

    def test_locked_facility_and_worker_cannot_move(self):
        self.options["lockedFacilityIds"] = ["f1"]
        self.result["layout"]["facilities"][0]["y"] = 2
        with self.assertRaisesRegex(CityValidationError, "锁定设施"):
            self.validate()
        self.result["layout"]["facilities"][0]["y"] = 3
        self.options["lockedWorkerIds"] = ["w1"]
        self.result["layout"]["workers"][0]["y"] = 5
        with self.assertRaisesRegex(CityValidationError, "锁定人员"):
            self.validate()

    def test_game_move_restriction_overrides_allow_main_moves(self):
        self.snapshot["buildings"][2]["canMove"] = False
        self.options["allowMainBuildingMoves"] = True
        self.options["lockedBuildingIds"] = []
        self.result["layout"]["buildings"][2]["x"] = 17
        with self.assertRaisesRegex(CityValidationError, "禁止搬迁"):
            self.validate()

    def test_unique_items_and_real_pedestal_tier(self):
        for patch in ({"itemId": "i2"}, {"tier": 2}, {"tier": True}):
            with self.subTest(patch=patch):
                result = copy.deepcopy(self.result)
                result["layout"]["pedestals"][0].update(patch)
                with self.assertRaises(CityValidationError):
                    validate_result(self.snapshot, self.options, result)

    def test_capacity_is_shared_and_facility_metadata_is_authoritative(self):
        self.options["facilityRequests"][0]["count"] = 9
        with self.assertRaisesRegex(CityValidationError, "总容量"):
            self.validate()
        self.options["facilityRequests"][0]["count"] = 7
        self.result["layout"]["facilities"][0]["ownerId"] = 3
        with self.assertRaisesRegex(CityValidationError, "归属"):
            self.validate()

    def test_owner_range_uses_the_moved_building_rectangle(self):
        self.result["layout"]["facilities"][0].update(x=8, y=3)
        with self.assertRaisesRegex(CityValidationError, "矩形范围"):
            self.validate()

    def test_resource_facility_has_only_one_primary_owner(self):
        self.result["layout"]["workers"][1]["facilityIds"].append("f3")
        self.result["layout"]["facilities"][2]["primaryWorkerIds"].append("w2")
        with self.assertRaisesRegex(CityValidationError, "只能计入一名"):
            self.validate()

    def test_shared_tiles_cannot_leave_one_worker_below_minimum(self):
        self.result["layout"]["workers"][0]["facilityIds"] = ["f1", "f2"]
        self.result["layout"]["workers"][1]["facilityIds"] += ["f3", "f4"]
        with self.assertRaisesRegex(CityValidationError, "设施数不足"):
            self.validate()

    def test_worker_must_reach_own_facility_not_only_some_other_worker(self):
        self.result["layout"]["workers"][0]["facilityIds"] = ["f2", "f3", "f4", "f5"]
        self.result["layout"]["workers"][1]["facilityIds"] = ["f1", "f6", "f7"]
        with self.assertRaisesRegex(CityValidationError, "真实工作范围"):
            self.validate()

    def test_resource_assists_are_exact_geometric_relationships(self):
        for ids in ([], ["w1", "w2"], ["w2", "w2"], ["w3"]):
            with self.subTest(ids=ids):
                result = copy.deepcopy(self.result)
                result["layout"]["facilities"][2]["assistingWorkerIds"] = ids
                with self.assertRaises(CityValidationError):
                    validate_result(self.snapshot, self.options, result)

    def test_nonresource_worker_serves_one_main_facility(self):
        self.result["layout"]["workers"][2]["facilityIds"].append("f9")
        with self.assertRaisesRegex(CityValidationError, "只能主要负责一个"):
            self.validate()

    def test_station_requirement_for_nonresource_workers(self):
        self.options["jobRequirements"].append({"jobType": "研究", "workerCount": 3, "minFacilitiesPerWorker": 1, "allocation": "station"})
        self.assertTrue(self.validate()["validation"]["passed"])

    def test_minimum_workers_per_nonresource_facility(self):
        self.options["facilityRequests"][1]["minWorkers"] = 2
        for row in self.result["layout"]["facilities"]:
            if row["jobType"] == "研究":
                row["minWorkers"] = 2
        with self.assertRaisesRegex(CityValidationError, "主要工作人员不足"):
            self.validate()

    def test_zero_workers_is_supported_for_nonresource_facilities(self):
        self.options["facilityRequests"][1]["minWorkers"] = 0
        self.options["workerAssignments"] = [w for w in self.options["workerAssignments"] if w["jobType"] != "研究"]
        for row in self.result["layout"]["facilities"]:
            if row["jobType"] == "研究":
                row.update(minWorkers=0, primaryWorkerIds=[])
        for row in self.result["layout"]["workers"]:
            if row["jobType"] == "研究":
                row.update(fixed=True, facilityIds=[])
        self.assertTrue(self.validate()["validation"]["passed"])

    def test_metrics_priority_and_optimality_are_recomputed(self):
        for section, patch in (("metrics", {"D": 100}), ("metrics", {"S": 99}),
                               ("metrics", {"priorityTotal": 10}), ("metrics", {"flexCoverage": 99}),
                               ("proof", {"DUpperBound": 99}), ("proof", {"DProven": True}),
                               ("proof", {"globalOptimal": True})):
            with self.subTest(section=section, patch=patch):
                result = copy.deepcopy(self.result)
                result[section].update(patch)
                with self.assertRaises(CityValidationError):
                    validate_result(self.snapshot, self.options, result)

    def test_priority_claim_must_point_to_an_actual_best_facility(self):
        self.result["metrics"]["priorityByJob"]["研究"]["facilityId"] = "f9"
        with self.assertRaisesRegex(CityValidationError, "最高单设施"):
            self.validate()

    def test_complete_migration_before_after_is_required(self):
        self.result["layout"]["buildings"][2]["x"] = 17
        self.result["moves"] = [{"kind": "building", "id": 3, "action": "move", "from": {"x": 16, "y": 16}, "to": {"x": 17, "y": 16}}]
        self.result["metrics"]["moveCount"] = 1
        self.assertTrue(self.validate()["validation"]["passed"])
        for change in (lambda rows: rows.clear(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                       lambda rows: rows[0]["from"].update(x=15)):
            with self.subTest(change=change):
                result = copy.deepcopy(self.result)
                change(result["moves"])
                with self.assertRaisesRegex(CityValidationError, "迁移清单"):
                    validate_result(self.snapshot, self.options, result)

    def test_invalid_numeric_options_and_duplicate_requirements_are_rejected(self):
        for value in (True, "2", 1.5, -1, float("nan"), float("inf")):
            with self.subTest(value=value):
                options = copy.deepcopy(self.options)
                options["jobRequirements"][0]["workerCount"] = value
                with self.assertRaises(CityValidationError):
                    validate_result(self.snapshot, options, self.result)
        self.options["jobRequirements"].append(dict(self.options["jobRequirements"][0]))
        with self.assertRaisesRegex(CityValidationError, "重复"):
            self.validate()

    def test_job_change_is_recorded_even_without_moving_person(self):
        self.snapshot["workers"][0]["currentTaskType"] = None
        self.result["moves"] = [{"kind": "job", "id": "w1", "action": "assign", "previousJobType": None, "jobType": "伐木"}]
        self.result["metrics"]["moveCount"] = 1
        self.assertTrue(self.validate()["validation"]["passed"])
        self.result["moves"] = []
        with self.assertRaisesRegex(CityValidationError, "迁移清单"):
            self.validate()

    def test_item_replacement_without_moving_pedestals_is_not_omitted(self):
        self.result["layout"]["pedestals"][0]["itemId"] = "i2"
        self.result["layout"]["pedestals"][1]["itemId"] = "i1"
        metrics = self.result["metrics"]
        metrics.update(D=2.3, S=4, priorityTotal=4, moveCount=2)
        metrics["priorityByJob"]["研究"]["value"] = 4
        next(f for f in metrics["facilityValues"] if f["facilityId"] == "f8")["value"] = 4
        self.result["moves"] = [{"kind": "item", "id": "i2", "action": "replace", "buildingId": 4, "previousItemId": "i1"},
                                {"kind": "item", "id": "i1", "action": "replace", "buildingId": 5, "previousItemId": "i2"}]
        self.assertTrue(self.validate()["validation"]["passed"])
        self.result["moves"].pop()
        with self.assertRaisesRegex(CityValidationError, "迁移清单"):
            self.validate()

    def test_new_resource_facility_has_stable_identity_and_create_record(self):
        self.options["facilityRequests"][0]["count"] = 8
        new_id = "new:51:2:8"
        self.result["layout"]["facilities"].append({"id": new_id, "template": 51, "ownerId": 2, "element": 1,
                                                    "jobType": "伐木", "minWorkers": 1, "x": 5, "y": 5,
                                                    "primaryWorkerIds": ["w2"], "assistingWorkerIds": []})
        self.result["layout"]["workers"][1]["facilityIds"].append(new_id)
        self.result["metrics"]["facilityValues"].append({"facilityId": new_id, "jobType": "伐木", "value": 0})
        self.result["metrics"]["moveCount"] = 1
        self.result["moves"] = [{"kind": "facility", "id": new_id, "action": "create", "from": None, "to": {"x": 5, "y": 5}}]
        self.assertTrue(self.validate()["validation"]["passed"])

    def test_removed_facility_requires_a_record_and_cannot_be_locked(self):
        self.options["facilityRequests"][0]["count"] = 6
        self.options["jobRequirements"][0]["minFacilitiesPerWorker"] = 2
        self.result["layout"]["facilities"] = [f for f in self.result["layout"]["facilities"] if f["id"] != "f7"]
        self.result["layout"]["workers"][1]["facilityIds"].remove("f7")
        self.result["metrics"]["facilityValues"] = [f for f in self.result["metrics"]["facilityValues"] if f["facilityId"] != "f7"]
        self.result["metrics"]["moveCount"] = 1
        self.result["moves"] = [{"kind": "facility", "id": "f7", "action": "remove", "from": {"x": 6, "y": 4}}]
        self.assertTrue(self.validate()["validation"]["passed"])
        self.options["lockedFacilityIds"] = ["f7"]
        with self.assertRaisesRegex(CityValidationError, "删除锁定"):
            self.validate()

    def test_additional_pedestal_uses_zero_tier_and_confirmed_cap(self):
        self.options["maxPedestals"] = 3
        self.snapshot["pedestalLimit"]["maximum"] = 3
        self.snapshot["items"].append({"id": "i3", "qi": 1, "elements": [0] * 5})
        self.result["layout"]["pedestals"].append({"id": "p3", "buildingId": None, "tier": 0, "itemId": "i3", "x": 10, "y": 13})
        self.result["metrics"].update(D=4.5, moveCount=1)
        self.result["proof"]["DUpperBound"] = 6.5
        self.result["moves"] = [{"kind": "pedestal", "id": "p3", "action": "create", "from": None, "to": {"x": 10, "y": 13}}]
        self.assertTrue(self.validate()["validation"]["passed"])
        self.result["layout"]["pedestals"][-1]["tier"] = 1
        with self.assertRaisesRegex(CityValidationError, "实际库存"):
            self.validate()

    def test_nonfeasible_result_cannot_reuse_a_success_stamp(self):
        result = {"status": "unknown", "layout": None, "validation": {"passed": True}}
        validate_result(self.snapshot, self.options, result)
        self.assertNotIn("validation", result)
        result["proof"] = {"DProven": True}
        with self.assertRaises(CityValidationError):
            validate_result(self.snapshot, self.options, result)

    def test_inventory_bound_matches_exhaustive_assignment_without_solver(self):
        items = [{"qi": value} for value in (0.15, 0.9, 1.3, 2.6)]
        tiers = [2, 0, 1]
        def rounded(value):
            whole = int(value)
            return whole + (value - whole >= 0.5)
        brute = max(sum(rounded((10 * items[item]["qi"]) * (1 + tier * .15)) for tier, item in zip(tiers, order))
                    for order in itertools.permutations(range(4), 3))
        self.assertEqual(_inventory_bound(tiers, items), brute)


if __name__ == "__main__":
    unittest.main()
