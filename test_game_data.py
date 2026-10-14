"""Parser regressions using disposable fixtures, never real save writes."""

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    from .game_data import GameDataError, building_cells, list_saves, load_snapshot, _worker_range, _read_workers
except ImportError:
    from game_data import GameDataError, building_cells, list_saves, load_snapshot, _worker_range, _read_workers


def array(width, height, depth):
    return {"c2array": True, "size": [width, height, depth],
            "data": [[[0 for _ in range(depth)] for _ in range(height)] for _ in range(width)]}


class GameDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lingqi-parser-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        jz = array(68, 38, 1)
        for template, name, width in ((5, "书院", 2), (66, "渡劫台", 3), (67, "置物台", 1)):
            for field, value in ((1, name), (3, "功能"), (5, 7), (8, width), (9, width)):
                jz["data"][template][field][0] = value
        lw = array(3, 13, 1)
        lw["data"][1][1][0] = "库存灵物"
        lw["data"][2][1][0] = "已摆灵物"
        ss = array(2, 41, 1)
        ss["data"][1][1][0] = "锻炉"
        ss["data"][1][4][0] = "锻造"
        ss["data"][1][37][0] = 1
        self.worker_tables = {name: array(75, 30, 1)["data"]
                              for name in ("zy", "sx", "wp", "tf", "buff", "jn", "tz")}
        self.worker_tables["zy"][1][1][0] = "屠夫"
        self.worker_tables["sx"][68][4][0] = "百分比"
        self.worker_tables["tf"][36][7][0] = 68
        self.worker_tables["tf"][36][10][0] = 3
        with zipfile.ZipFile(self.root / "package.nw", "w") as archive:
            for name, table in (("jz", jz), ("lw", lw), ("ss", ss)):
                archive.writestr(name + ".json", json.dumps(table))
            for name, data in self.worker_tables.items():
                archive.writestr(name + ".json", json.dumps({"c2array": True, "size": [75, 30, 1], "data": data}))
        self.save = array(201, 60, 22)
        data = self.save["data"]
        data[2][1][0] = 3
        for slot, template, x, y in ((1, 66, 23, 12), (2, 67, 21, 12), (3, 5, 17, 16)):
            for field, value in ((0, slot), (1, template), (2, x + 10000), (3, y + 10000)):
                data[slot][field][1] = value
        data[2][5][1] = 1  # zero-based tier, aura multiplier 1.15
        data[2][8][1] = 9  # ordinary level must not be used as the tier
        data[2][9][1] = 1
        for storage, template, qi in ((18, 1, 99), (19, 2, 1)):
            data[1][1][storage] = template
            data[1][10][storage] = qi
            for field, value in enumerate((1.1, 2.2, 3.3, 4.4, 5.5), 5):
                data[1][field][storage] = value
        # field0 deliberately stays zero: activity is based on field1.
        data[1][12][19] = 2
        data[1][1][21] = 1
        data[1][2][21] = 3
        data[171][13][6] = 1
        self.write_save("backup", 1, 2000)

    def write_save(self, directory, slot, timestamp):
        folder = self.root / directory
        folder.mkdir(exist_ok=True)
        (folder / f"ea{slot}save.dat").write_text(json.dumps(self.save), encoding="utf-8")
        info = array(6, 26, 1)
        for field, value in ((0, 1), (5, "18年 第75天 0:00"), (6, "42小时[/color]"), (7, timestamp)):
            info["data"][slot][field][0] = value
        (folder / "saveinfo.dat").write_text(json.dumps(info), encoding="utf-8")

    def test_parse_merges_inventory_and_placed_without_field0_assumption(self):
        snapshot = load_snapshot(self.root, "backup/ea1save.dat")
        self.assertEqual([i["id"] for i in snapshot["items"]], ["18:1", "19:1"])
        self.assertEqual(snapshot["counts"]["items"], 2)
        self.assertEqual(snapshot["bounds"], {"xmin": 17, "xmax": 34, "ymin": 1, "ymax": 18})
        self.assertEqual(snapshot["facilities"][0]["ownerId"], 3)
        self.assertEqual(snapshot["facilities"][0]["jobType"], "锻造")
        self.assertEqual(snapshot["facilityGroups"][0]["jobType"], "锻造")
        self.assertEqual(snapshot["facilityGroups"][0]["key"], "1:3")
        self.assertEqual(snapshot["facilityGroups"][0]["ownerName"], "书院")
        self.assertEqual(snapshot["facilityGroups"][0]["ownerCapacity"], 2)
        self.assertEqual(snapshot["buildings"][2]["category"], "功能")
        self.assertEqual(snapshot["buildings"][2]["facilityCapacity"], 2)
        self.assertEqual(snapshot["playTime"], "42小时")

    def test_resource_owner_capacity_uses_tier_not_ordinary_level(self):
        package = self.root / "package.nw"
        with zipfile.ZipFile(package, "r") as archive:
            tables = {name: archive.read(name) for name in archive.namelist()}
        jz = json.loads(tables["jz.json"])
        jz["data"][5][3][0] = "资源"
        tables["jz.json"] = json.dumps(jz).encode("utf-8")
        with zipfile.ZipFile(package, "w") as archive:
            for name, value in tables.items():
                archive.writestr(name, value)
        self.save["data"][3][5][1] = 2
        self.save["data"][3][8][1] = 9
        self.write_save("backup", 1, 2000)
        snapshot = load_snapshot(self.root, "backup/ea1save.dat")
        self.assertEqual(snapshot["buildings"][2]["facilityCapacity"], 12)
        self.assertEqual(snapshot["facilityGroups"][0]["ownerCapacity"], 12)

    def test_aura_uses_tier_and_separately_stored_qi(self):
        snapshot = load_snapshot(self.root, "backup/ea1save.dat")
        pedestal = next(b for b in snapshot["buildings"] if b["template"] == 67)
        self.assertEqual((pedestal["tier"], pedestal["level"]), (1, 9))
        self.assertEqual(snapshot["baseline"]["D"], 1.2)
        self.assertEqual(snapshot["baseline"]["S"], 1.3)
        self.assertEqual(snapshot["baseline"]["byElement"], [1.3, 0, 0, 0, 0])

    def test_game_multiplication_order_not_decimal_or_reassociated(self):
        # Values verified against the game's generated JS expressions. The first
        # case rejects raw*multiplier*10; the second rejects Decimal/epsilon fixes.
        for raw, tier, expected in ((4.6, 5, 8.1), (5, 1, 5.7), (1, 1, 1.2), (3, 7, 6.1)):
            with self.subTest(raw=raw, tier=tier):
                self.save["data"][1][10][19] = raw
                self.save["data"][1][5][19] = raw
                self.save["data"][2][5][1] = tier
                self.write_save("backup", 1, 2000)
                snapshot = load_snapshot(self.root, "backup/ea1save.dat")
                self.assertEqual(snapshot["baseline"]["D"], expected)
                self.assertEqual(snapshot["baseline"]["S"], expected)

    def test_footprints_even_anchor_and_odd_center(self):
        self.assertEqual(building_cells(4, 5, 2, 2), [
            {"x": 4, "y": 5}, {"x": 5, "y": 5}, {"x": 4, "y": 6}, {"x": 5, "y": 6}])
        cells = building_cells(4, 5, 3, 3)
        self.assertEqual((cells[0], cells[-1]), ({"x": 3, "y": 4}, {"x": 5, "y": 6}))

    def test_sort_uses_embedded_time_not_later_file_mtime(self):
        self.write_save("savedir", 1, 1000)
        os.utime(self.root / "savedir" / "ea1save.dat", (2000000000, 2000000000))
        self.assertEqual(list_saves(self.root)[0]["key"], "backup/ea1save.dat")

    def test_rejects_unapproved_paths(self):
        for key in ("../ea1save.dat", "backup/../ea1save.dat", "backup/ea4save.dat", "C:/other/ea1save.dat"):
            with self.subTest(key=key), self.assertRaises(GameDataError):
                load_snapshot(self.root, key)

    def test_rejects_inconsistent_binding(self):
        self.save["data"][1][12][19] = 999
        self.write_save("backup", 1, 2000)
        with self.assertRaises(GameDataError):
            load_snapshot(self.root, "backup/ea1save.dat")

    def make_worker(self, slot, x=22, y=13, state="自动"):
        data = self.save["data"]
        for field, value in ((0, slot), (1, f"人物{slot}"), (5, "潘金莲"), (6, 1), (7, 10000 + x),
                             (8, 10000 + y), (9, state)):
            data[slot][field][2] = value

    def test_workers_use_real_identity_and_exclude_unrecruited_and_removed(self):
        for slot in range(1, 5):
            self.make_worker(slot)
        data = self.save["data"]
        data[2][55][2] = 1
        data[3][53][2] = 1
        data[4][7][2], data[4][8][2] = 30012, 30015
        data[1][1][12], data[1][21][12] = 36, 3
        self.write_save("backup", 1, 2000)
        snapshot = load_snapshot(self.root, "backup/ea1save.dat")
        self.assertEqual([w["id"] for w in snapshot["workers"]], ["w1", "w4"])
        worker, external = snapshot["workers"]
        self.assertEqual((worker["appearance"], worker["asset"]), ("潘金莲", "character:潘金莲"))
        self.assertEqual((worker["profession"], worker["currentTaskType"], worker["suggestedJobType"]),
                         ("屠夫", None, "锻造"))
        self.assertEqual((worker["rangePixels"], worker["rangeTiles"]), (163, 1.63))
        self.assertFalse(worker["suggestionIsActualTask"])
        self.assertTrue(worker["selectable"])
        self.assertFalse(external["inMainCity"])
        self.assertFalse(external["selectable"])
        self.assertIsNone(external["x"])
        self.assertEqual(snapshot["defaultWorkerAssignments"], [{"workerId": "w1", "jobType": "锻造"}])
        self.assertEqual(snapshot["workerConstraintVersion"], 1)

    def test_worker_range_combines_talent_pill_and_equipment_without_affix_level(self):
        self.make_worker(1)
        data = self.save["data"]
        data[1][1][12], data[1][21][12] = 36, 3  # talent68 = 9
        data[1][2][16], data[1][1][16] = 2, 1
        for field, value in ((1, 2), (3, 1), (6, 15), (8, 100)):
            data[2][field][17] = value
        self.worker_tables["wp"][2][8][0] = 68  # pill68 = 8.5
        data[1][16][2] = 3
        for field, value in ((1, 3), (3, 2), (6, 99), (9, 68), (10, 50), (15, 100), (16, 200)):
            data[3][field][9] = value  # affix68=7; affix50=14; level99 ignored
        radius, evidence = _worker_range(data, 1, self.worker_tables, [])
        self.assertEqual(radius, 200)  # floor(150 * 1.245) + 14
        self.assertEqual(evidence["components"]["equipment68"], 7)
        self.assertEqual(evidence["components"]["internalPill68"], 8.5)
        self.assertEqual(evidence["status"], "exact")

    def test_worker_suggestion_preserves_ambiguous_nearest_facility_jobs(self):
        self.make_worker(1, 27, 10)
        facilities = [{"x": 26, "y": 10, "jobType": "锻造"},
                      {"x": 27, "y": 11, "jobType": "采矿"}]
        worker = _read_workers(self.save["data"], self.worker_tables, facilities)[0]
        self.assertIsNone(worker["suggestedJobType"])
        self.assertEqual(set(worker["candidateJobTypes"]), {"锻造", "采矿"})

    def test_unhandled_runtime_buff_makes_range_unknown_not_base_radius(self):
        self.make_worker(1)
        self.worker_tables["buff"][1][3][0] = 68
        worker = _read_workers(self.save["data"], self.worker_tables, [])[0]
        self.assertIsNone(worker["rangePixels"])
        self.assertFalse(worker["selectable"])
        self.assertEqual(worker["rangeEvidence"]["status"], "unknown")
        self.assertTrue(worker["rangeEvidence"]["reasons"])


if __name__ == "__main__":
    unittest.main()
