import copy
import unittest

from validation import effective, validate_layout


class IndependentValidationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "bounds": {"xmin": -4, "xmax": 8, "ymin": -4, "ymax": 5},
            "items": [{"id": "18:1", "qi": 10, "elements": [1, 2, 3, 4, 5]}],
            "buildings": [
                {"id": 7, "template": 3, "x": 6, "y": 0, "width": 2, "height": 2},
                {"id": 10, "template": 66, "x": 0, "y": 0, "width": 3, "height": 3},
                {"id": 11, "template": 67, "x": 2, "y": 0, "width": 1, "height": 1, "tier": 0}],
            "facilities": [{"id": "f1", "template": 1, "ownerId": 7, "element": 2, "x": 3, "y": 0}],
            "facilityGroups": [{"template": 1, "ownerId": 7, "element": 2, "count": 1}],
            "resources": []}
        self.options = {"maxPedestals": 1}
        self.layout = {"tribulation": {"x": 0, "y": 0},
                       "pedestals": [{"x": 2, "y": 0, "tier": 0, "itemId": "18:1"}],
                       "facilities": copy.deepcopy(self.snapshot["facilities"]), "D": 10, "S": 3}

    def test_valid_layout(self):
        validate_layout(self.snapshot, self.options, self.layout)

    def test_game_ordered_binary_float_rounding(self):
        for raw, tier, expected in ((4.6, 5, 8.1), (5, 1, 5.7), (1, 1, 1.2), (3, 7, 6.1)):
            with self.subTest(raw=raw, tier=tier):
                self.assertEqual(effective(raw, tier), expected)

    def test_rejects_wrong_element_even_if_reported_score_matches(self):
        self.layout["facilities"][0]["element"] = 3
        self.layout["S"] = 4
        with self.assertRaisesRegex(ValueError, "游戏模板"):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_rejects_nonexistent_tier(self):
        self.layout["pedestals"][0]["tier"] = 1
        with self.assertRaisesRegex(ValueError, "等阶配额"):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_rejects_nonfinite_score(self):
        self.layout["D"] = float("nan")
        with self.assertRaises(ValueError):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_rejects_parent_range_violation(self):
        self.layout["facilities"][0]["x"] = 2
        self.layout["facilities"][0]["y"] = 2
        with self.assertRaisesRegex(ValueError, "所属建筑范围"):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_rejects_overlap(self):
        self.layout["pedestals"][0]["x"] = 0
        with self.assertRaisesRegex(ValueError, "重叠"):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_rejects_fractional_coordinate(self):
        self.layout["tribulation"]["x"] = 0.1
        with self.assertRaisesRegex(ValueError, "有限整数"):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_rejects_facility_count_above_owner_capacity(self):
        self.options["facilityRequests"] = [{"template": 1, "ownerId": 7, "count": 3}]
        self.layout["facilities"] += [
            dict(self.layout["facilities"][0], id="f2", y=1),
            dict(self.layout["facilities"][0], id="f3", y=2)]
        self.layout["S"] = 9
        with self.assertRaisesRegex(ValueError, "设施总数 3 超过上限 2"):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_capacity_is_shared_by_different_templates_of_one_owner(self):
        second = dict(self.snapshot["facilities"][0], id="f2", template=2, element=0, y=1)
        self.snapshot["facilities"].append(second)
        self.snapshot["facilityGroups"].append({"template": 2, "ownerId": 7, "element": 0, "count": 1})
        self.options["facilityRequests"] = [{"template": 1, "ownerId": 7, "count": 1},
                                            {"template": 2, "ownerId": 7, "count": 2}]
        self.layout["facilities"] += [dict(second), dict(second, id="f3", y=2)]
        self.layout["S"] = 5
        with self.assertRaisesRegex(ValueError, "设施总数 3 超过上限 2"):
            validate_layout(self.snapshot, self.options, self.layout)


if __name__ == "__main__":
    unittest.main()
