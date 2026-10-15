"""Reader-only HTTP contract; no game saves are changed."""
import http.client
import json
from pathlib import Path
import threading
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import server


class ReaderServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.http.app = server.Application(Path("."))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def request(self, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=3)
        try:
            connection.request("GET" if payload is None else "POST", path,
                               body=None if payload is None else json.dumps(payload),
                               headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            connection.close()

    def test_health_preserves_read_only_game_access(self):
        status, body = self.request("/api/health")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["app"], "game-data-reader")
        self.assertEqual(data["mode"], "city-planner")
        self.assertTrue(data["readOnly"])
        self.assertNotIn("workerConstraintVersion", data)
        self.assertTrue(hasattr(self.http.app, "jobs"))

    def test_reads_save_list_and_snapshot_without_planner_mutation(self):
        snapshot = {"schemaVersion": 2, "id": "read-test", "items": [], "workers": []}
        with patch.object(server.game_data, "list_saves", return_value=[{"key": "backup/ea1save.dat"}]) as listing:
            status, body = self.request("/api/saves")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["saves"][0]["key"], "backup/ea1save.dat")
            listing.assert_called_once_with(self.http.app.game_dir)
        with patch.object(server.game_data, "load_snapshot", return_value=snapshot) as reader:
            status, body = self.request("/api/load", {"key": "backup/ea1save.dat"})
            self.assertEqual(status, 200)
            result = json.loads(body)
            self.assertEqual({key: result[key] for key in snapshot}, snapshot)
            self.assertIn("planningDefaults", result)
            reader.assert_called_once_with(self.http.app.game_dir, "backup/ea1save.dat")
        self.assertNotIn("baseline", snapshot)

    def test_removed_planning_routes_are_unavailable(self):
        for path, payload in (("/api/import", {}),
                              ("/api/plans/load", {"name": "old.json"}),
                              ("/api/plans", None),
                              ("/api/jobs/old", None), ("/app.js", None),
                              ("/pedestal-planner.js", None)):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, payload)[0], 404)

    def test_old_bookmark_does_not_load_discarded_plan(self):
        with patch.object(server.game_data, "load_snapshot") as reader:
            status, body = self.request("/city.html?mode=priority&plan=old.json")
            self.assertEqual(status, 200)
            self.assertIn("<html", body)
            reader.assert_not_called()

    def test_unknown_snapshot_is_rejected(self):
        status, body = self.request("/api/solve", {"snapshotId": "missing", "options": {}})
        self.assertEqual(status, 400)
        self.assertIn("快照", body)

    def test_job_does_not_publish_a_layout_that_fails_independent_validation(self):
        app = server.Application(Path("."))
        app.snapshots["fixture"] = {"id": "fixture"}
        checker = Mock(side_effect=ValueError("占地冲突"))
        modules = {"city_planner": SimpleNamespace(solve=lambda *a, **k: {"layout": {"forged": True}}),
                   "city_validation": SimpleNamespace(validate_result=checker)}
        with patch.dict(sys.modules, modules):
            job_id = app.start("fixture", {})["jobId"]
            end = time.monotonic() + 2
            while app.get_job(job_id)["state"] in ("queued", "running") and time.monotonic() < end:
                time.sleep(.01)
            result = app.get_job(job_id)
        self.assertEqual(result["state"], "error")
        self.assertIsNone(result["result"])
        self.assertIn("占地冲突", result["error"])
        checker.assert_called_once()

    def test_cancel_preserves_returned_feasible_result_and_rejects_parallel_job(self):
        app = server.Application(Path("."))
        app.snapshots["fixture"] = {"id": "fixture"}
        entered = threading.Event()

        def solve(snapshot, options, progress, cancel):
            entered.set()
            cancel.wait(2)
            return {"status": "cancelled", "layout": {"valid": True}}

        checker = Mock()
        with patch.dict(sys.modules, {"city_planner": SimpleNamespace(solve=solve),
                                       "city_validation": SimpleNamespace(validate_result=checker)}):
            job_id = app.start("fixture", {})["jobId"]
            self.assertTrue(entered.wait(1))
            with self.assertRaises(RuntimeError):
                app.start("fixture", {})
            app.cancel_job(job_id)
            end = time.monotonic() + 2
            while app.get_job(job_id)["state"] != "cancelled" and time.monotonic() < end:
                time.sleep(.01)
            self.assertEqual(app.get_job(job_id)["state"], "cancelled")
            self.assertTrue(app.get_job(job_id)["result"]["layout"])
        checker.assert_called_once()

    def test_malformed_load_request_and_cross_origin_are_rejected(self):
        with patch.object(server.game_data, "load_snapshot") as reader:
            for payload in ([], {}, {"key": 123}):
                self.assertEqual(self.request("/api/load", payload)[0], 400)
            self.assertEqual(self.request("/api/load", {"key": "backup/ea1save.dat"},
                                          {"Origin": "https://unrelated.invalid"})[0], 403)
            reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
