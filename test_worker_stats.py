"""Synthetic formula regressions; never read/write a live game save."""
import gc
import json
import math
import unittest
import weakref

try:
    from .worker_stats import (WorkerEvaluator, calculate_worker_stats,
                               equipment_attribute, js_round, skill_base)
except ImportError:
    from worker_stats import (WorkerEvaluator, calculate_worker_stats,
                              equipment_attribute, js_round, skill_base)


def array(x, y, z=1):
    return [[[0 for _ in range(z)] for _ in range(y)] for _ in range(x)]


class WorkerStatTests(unittest.TestCase):
    def setUp(self):
        self.data = array(12, 60, 18)
        self.tables = {name: array(121, 40) for name in ("sx", "wp", "tf", "zy", "tz", "jn", "buff")}
        for attr in range(1, 121):
            self.put("sx", attr, 1, f"属性 {attr}")
            kind = "百分比"
            if attr in (1, 2, 3, 49, 104, 105, 106):
                kind = "小数"
            if 40 <= attr <= 48 or attr in (50, 65, 66, 67, 69):
                kind = "整数"
            if 77 <= attr <= 80 or 90 <= attr <= 103:
                kind = "倍率"
            self.put("sx", attr, 4, kind)
        self.put("zy", 1, 1, "职业")
        self.put("zy", 1, 16, 68)
        self.data[1][6][2] = 1
        self.data[1][1][4] = 1

    def put(self, table, row, col, value):
        self.tables[table][row][col][0] = value

    def item(self, item, template, slot=None, storage=9, attrs=(), level=0, tier=1, quality=100,
             category="普通", set_id=0, part=0, set_quality=1):
        self.put("wp", template, 1, f"物品 {template}")
        self.put("wp", template, 4, category)
        self.put("wp", template, 6, slot or 1)
        self.put("wp", template, 16, set_id)
        self.put("wp", template, 17, part)
        for index, (attr, coefficient) in enumerate(attrs):
            self.put("wp", template, 8 + index, attr)
            self.put("wp", template, 11 + index, coefficient)
        for field, value in ((1, template), (3, tier), (6, level), (8, quality), (23, set_quality)):
            self.data[item][field][storage] = value
        if slot:
            self.data[1][15 + slot][2] = item

    def evaluator(self):
        return WorkerEvaluator(self.data, self.tables, 1)

    def test_nonlinear_gear_recomputes_primary_and_skill(self):
        # Independent hand calculation from expressions 5202/5207/5255.
        self.data[1][11][2], self.data[1][14][2] = 65, 162.5
        self.data[1][32][2] = 39
        self.item(2, 2, 1, attrs=((41, 100), (2, 100)), level=35, quality=241)
        equipped = self.evaluator()
        self.assertEqual(skill_base(39), 170)
        self.assertAlmostEqual(equipped.attribute(2), 24.48)
        self.assertEqual(equipped.equipment(41), 58)
        self.assertEqual(equipped.attribute(41), 345)

    def test_current_equipment_both_inner_pill_paths_and_inactive_pill_ignored(self):
        self.item(2, 2, 5, attrs=((68, 100),), category="内丹")
        self.item(3, 3, storage=17, attrs=((68, 100),), category="内丹")
        self.item(4, 4, storage=17, attrs=((68, 100),), level=100, category="内丹")
        self.data[1][2][16], self.data[1][1][16] = 3, 1
        self.data[1][4][16], self.data[1][3][16] = 4, 0
        self.item(5, 5, 6, attrs=((68, 100),), category="宝物")
        equipped = self.evaluator()
        self.assertEqual(equipped.attribute(68), 16.5)
        self.assertEqual(equipped.attribute(50), 174)
        stats = calculate_worker_stats(self.data, self.tables, 1)
        self.assertEqual([e["category"] for e in stats["equipment"]], ["内丹", "宝物"])
        self.assertEqual(len(stats["sources"]["innerPills"]), 1)

    def test_saved_equipment_is_read_even_outside_usual_category_slot(self):
        self.item(2, 2, 1, attrs=((68, 100),), category="内丹")
        self.assertEqual(self.evaluator().attribute(68), 5.5)
        self.assertEqual(calculate_worker_stats(self.data, self.tables, 1)["equipment"][0]["slot"], 1)

    def test_inner_pill_state_precedes_reference_in_first_second_and_last_slots(self):
        try:
            from .game_data import _worker_range
        except ImportError:
            from game_data import _worker_range
        for slot, item in ((1, 2), (2, 3), (8, 4)):
            self.item(item, item, storage=17, attrs=((68, 100),), category="内丹")
            self.data[1][2 * slot][16] = item
        self.data[1][1][16] = 1   # slot 1 active; its following odd field is zero
        self.data[1][3][16] = 0   # slot 2 inactive; its following odd field is one
        self.data[1][5][16] = 1
        self.data[1][15][16] = 1  # slot 8 active; following field 17 is zero
        self.data[1][17][16] = 0
        evaluator = self.evaluator()
        self.assertEqual([slot for slot, _, _ in evaluator.pills], [1, 8])
        self.assertEqual(evaluator.attribute(68), 11)
        self.assertEqual(evaluator.attribute(50), 166)
        radius, evidence = _worker_range(self.data, 1, self.tables, [])
        self.assertEqual(radius, 166)
        self.assertEqual(evidence["status"], "exact")

    def test_set_threshold_counts_distinct_parts_and_highest_quality(self):
        self.put("tz", 1, 1, "测试套装")
        self.put("tz", 1, 3, 56)
        self.put("tz", 1, 4, 100)
        self.put("tz", 1, 5, 90)
        self.put("tz", 1, 6, 20)
        self.item(2, 2, 1, set_id=1, part=1, set_quality=2)
        self.item(3, 3, 2, set_id=1, part=1, set_quality=5)
        self.assertEqual(self.evaluator().attribute(56), 0)
        self.item(4, 4, 3, set_id=1, part=2, set_quality=3)
        evaluator = self.evaluator()
        self.assertEqual(evaluator.set_info(1), (2, 8, 3))
        self.assertEqual(evaluator.attribute(56), 20)
        self.assertEqual(evaluator.attribute(90), 1)  # only 2 unique parts, no 4-piece bonus

    def test_four_piece_multiplier_and_two_piece_are_not_counted_four_times(self):
        self.put("tz", 1, 1, "套装")
        for col, value in ((3, 56), (4, 100), (5, 90), (6, 20)):
            self.put("tz", 1, col, value)
        for index in range(1, 5):
            self.item(index + 1, index + 1, index, set_id=1, part=index, set_quality=1)
        self.assertEqual(self.evaluator().attribute(56), 10)
        self.assertEqual(self.evaluator().attribute(90), 1.02)

    def test_gear_changes_passive_via_passive_bonus(self):
        self.put("zy", 1, 21, 1)
        for col, value in ((1, "职业被动"), (3, "被动"), (11, 72), (37, 100)):
            self.put("jn", 1, col, value)
        self.item(2, 2, 1, attrs=((15, 100),))
        self.assertEqual(self.evaluator().attribute(72), 10.55)

    def test_talent_growth_is_recalculated_into_primary(self):
        self.put("tf", 1, 1, "成长")
        self.put("tf", 1, 7, 104)
        self.put("tf", 1, 10, 3)
        self.data[1][1][12], self.data[1][21][12] = 1, 2
        self.data[1][10][2], self.data[1][13][2] = 40, 100
        self.assertEqual(self.evaluator().attribute(1), 14.6)

    def test_secondary_ignores_normal_item_level_and_coefficient(self):
        self.assertEqual(equipment_attribute(70, 99, 1, 100, 999, "百分比", True), 2.75)
        self.assertEqual(equipment_attribute(41, 99, 1, 100, 999, "整数", True), 4)
        self.assertEqual(equipment_attribute(50, 99, 1, 100, 999, "整数", True), 5.5)

    def test_display_units_and_skill_job_order(self):
        stats = calculate_worker_stats(self.data, self.tables, 1, expected_range=150)
        rows = {r["id"]: r for r in stats["rows"]}
        self.assertEqual(stats["status"], "exact")
        self.assertEqual(rows[46]["jobType"], "建造")
        self.assertEqual(rows[47]["jobType"], "研究")
        self.assertEqual(rows[48]["jobType"], "操练")
        self.assertEqual((rows[49]["value"], rows[49]["unit"]), (1, "秒/次"))
        self.assertEqual((rows[50]["value"], rows[50]["unit"]), (1.5, "格"))
        self.assertEqual((rows[52]["value"], rows[52]["unit"]), (1.5, "×"))
        self.assertNotIn(69, rows)
        self.assertEqual(rows[65]["unit"], "点/次")

    def test_unsupported_dependency_is_null_and_warns_without_hiding_other_rows(self):
        self.put("sx", 70, 12, 123)
        stats = calculate_worker_stats(self.data, self.tables, 1)
        rows = {r["id"]: r for r in stats["rows"]}
        self.assertEqual(stats["status"], "partial")
        self.assertIsNone(rows[49]["value"])
        self.assertIsNone(rows[70]["value"])
        self.assertIsNotNone(rows[40]["value"])
        self.assertTrue(any("全局加成" in warning for warning in stats["warnings"]))

    def test_cycle_and_missing_tables_are_partial_not_recursion_or_import_failure(self):
        self.put("zy", 1, 21, 1)
        for col, value in ((1, "被动"), (3, "被动"), (11, 15), (37, 100)):
            self.put("jn", 1, col, value)
        # Add an affected passive so attribute 15 enters the dependency chain.
        self.put("jn", 1, 12, 72)
        self.put("jn", 1, 38, 100)
        stats = calculate_worker_stats(self.data, self.tables, 1)
        self.assertTrue(any("循环依赖" in message for message in stats["warnings"]))
        self.assertEqual(calculate_worker_stats(self.data, {}, 1)["status"], "partial")

    def test_range_cross_check_fails_closed_and_input_is_immutable(self):
        before = json.dumps((self.data, self.tables), ensure_ascii=False)
        stats = calculate_worker_stats(self.data, self.tables, 1, expected_range=999)
        self.assertEqual(before, json.dumps((self.data, self.tables), ensure_ascii=False))
        self.assertEqual(stats["status"], "partial")
        self.assertIsNone(next(r["value"] for r in stats["rows"] if r["id"] == 50))

    def test_output_contains_only_current_saved_attribute_values(self):
        self.item(2, 2, 1, attrs=((40, 100),))
        stats = calculate_worker_stats(self.data, self.tables, 1)
        for row in stats["rows"]:
            self.assertIn("value", row)
            self.assertTrue({"bare", "equipped", "lowerIsBetter"}.isdisjoint(row))
        self.assertNotIn("retainedInBare", stats["equipment"][0])
        self.assertNotIn("barePreserves", stats["sources"])
        self.assertEqual(stats["sources"]["dependencies"]["rawRangePixels"], 150)
        self.assertFalse(stats["sources"]["runtimeBuffsIncluded"])

    def test_js_round_halves_and_interval_floor(self):
        self.assertEqual([js_round(x) for x in (0.5, 1.5, -0.5, -1.5)], [1, 2, 0, -1])
        self.item(2, 2, 1, attrs=((70, 100),))
        self.assertEqual(self.evaluator().attribute(49), 0.97)

    def test_cached_evaluators_do_not_retain_raw_saves_after_use(self):
        evaluator = self.evaluator()
        evaluator.attribute(40)
        reference = weakref.ref(evaluator)
        del evaluator
        gc.collect()
        self.assertIsNone(reference())


if __name__ == "__main__":
    unittest.main()
