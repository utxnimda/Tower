"""Read-only local companion for the game. Never controls the game process."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

APP_DIR = Path(__file__).resolve().parent
VENDOR = APP_DIR / "vendor"
if VENDOR.is_dir():
    sys.path.insert(0, str(VENDOR))

import game_data
import optimizer

LOG = logging.getLogger("lingqi")


class Application:
    def __init__(self, game_dir: Path):
        self.game_dir = game_dir
        self.snapshots = {}
        self.jobs = {}
        self.lock = threading.RLock()

    def load(self, key):
        snapshot = game_data.load_snapshot(self.game_dir, key)
        # Two independent evaluators are kept deliberately: a parser regression
        # must not silently become the optimizer's definition of correctness.
        score = optimizer.evaluate(snapshot)
        old = snapshot.get("baseline", {})
        for field in ("D", "S"):
            if field in old and abs(float(old[field]) - float(score[field])) > 1e-6:
                raise ValueError(f"存档评估交叉校验失败：{field} ({old[field]} / {score[field]})")
        snapshot["baseline"] = {**old, **score}
        if "workers" in snapshot:
            from validation import worker_coverage
            snapshot["baseline"]["workers"] = [
                {"workerId": w["id"], "name": w["name"], "jobType": w.get("currentTaskType"),
                 "x": w["x"], "y": w["y"], "rangePixels": w.get("rangePixels"), "fixed": True}
                for w in snapshot["workers"] if w.get("inMainCity")]
            snapshot["baseline"]["workerCoverage"] = worker_coverage(snapshot, snapshot["baseline"], baseline=True)
        with self.lock:
            self.snapshots[snapshot["id"]] = snapshot
            while len(self.snapshots) > 8:
                del self.snapshots[next(iter(self.snapshots))]
        return snapshot

    def start(self, snapshot_id, options):
        with self.lock:
            if snapshot_id not in self.snapshots:
                raise ValueError("存档快照已失效，请重新读取存档。")
            if any(j["state"] in ("queued", "running", "cancelling") for j in self.jobs.values()):
                raise RuntimeError("已有计算正在进行，请等待完成或先取消。")
            snapshot = self.snapshots[snapshot_id]
            job_id = uuid.uuid4().hex
            cancel = threading.Event()
            job = {"id": job_id, "state": "queued", "progress": {}, "result": None,
                   "error": None, "createdAt": time.time(), "updatedAt": time.time(),
                   "cancel": cancel, "snapshotId": snapshot_id}
            self.jobs[job_id] = job
            for obsolete in list(self.jobs)[:-12]:
                if self.jobs[obsolete]["state"] not in ("queued", "running", "cancelling"):
                    del self.jobs[obsolete]

        def progress(update=None, **kwargs):
            if isinstance(update, str):
                update = {"message": update}
            update = {**(update or {}), **kwargs}
            with self.lock:
                job["progress"] = {**job["progress"], **update}
                job["updatedAt"] = time.time()

        def run():
            with self.lock:
                job["state"] = "running"
            try:
                result = optimizer.solve(snapshot, options, progress, cancel)
                # A separate full geometry / score check is performed before
                # publishing any candidate to the browser.
                from validation import validate_result
                validate_result(snapshot, options, result)
                with self.lock:
                    job["result"] = result
                    job["state"] = "cancelled" if cancel.is_set() else "done"
            except Exception as exc:
                LOG.error("Solver failed: %s\n%s", exc, traceback.format_exc())
                with self.lock:
                    job["error"] = str(exc)
                    job["state"] = "error"
            finally:
                with self.lock:
                    job["updatedAt"] = time.time()

        threading.Thread(target=run, name=f"layout-{job_id[:8]}", daemon=True).start()
        return {"jobId": job_id}

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError("找不到此计算任务。")
            return {k: v for k, v in self.jobs[job_id].items() if k != "cancel"}


class Handler(BaseHTTPRequestHandler):
    server_version = "LingqiPlanner/1.2"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        LOG.info(fmt, *args)

    def send_json(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/api/health":
                return self.send_json({"app": "lingqi-planner", "version": "1.2", "workerConstraintVersion": 1, "workerStatsVersion": 1, "readOnly": True,
                                       "pid": os.getpid(), "gameDir": str(self.app.game_dir)})
            if path == "/api/saves":
                return self.send_json({"saves": game_data.list_saves(self.app.game_dir)})
            if path.startswith("/api/jobs/"):
                return self.send_json(self.app.job(path.rsplit("/", 1)[-1]))
            if path.startswith("/api/"):
                return self.send_json({"error": "未知接口。"}, 404)
            root = (APP_DIR / "web").resolve()
            relative = unquote(path).lstrip("/") or "index.html"
            target = (root / relative).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                return self.send_json({"error": "文件不存在。"}, 404)
            data = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
                content_type += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except KeyError as exc:
            self.send_json({"error": str(exc)}, 404)
        except Exception as exc:
            LOG.exception("GET failed")
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        try:
            # Bind to loopback and reject browser requests from unrelated sites.
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlsplit(origin).netloc != host:
                return self.send_json({"error": "只允许本工具页面发起请求。"}, 403)
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return self.send_json({"error": "跨站请求被拒绝。"}, 403)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 1_000_000:
                return self.send_json({"error": "请求过大。"}, 413)
            data = json.loads(self.rfile.read(length) or b"{}")
            path = urlsplit(self.path).path
            if path == "/api/load":
                return self.send_json(self.app.load(data.get("key", "")))
            if path == "/api/solve":
                return self.send_json(self.app.start(data.get("snapshotId"), data.get("options", {})), 202)
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                with self.app.lock:
                    job = self.app.jobs[job_id]
                    if job["state"] in ("queued", "running", "cancelling"):
                        job["cancel"].set()
                        job["state"] = "cancelling"
                return self.send_json(self.app.job(job_id))
            return self.send_json({"error": "未知接口。"}, 404)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, 409)
        except (ValueError, KeyError, TypeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            LOG.exception("POST failed")
            self.send_json({"error": str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser(description="灵气布局规划器：只读存档，不操作游戏")
    parser.add_argument("--game-dir", type=Path, default=APP_DIR.parent)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.app = Application(args.game_dir.resolve())
    url = f"http://127.0.0.1:{args.port}/"
    LOG.info("只读规划器已启动：%s；不会操作游戏进程或写入存档。", url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        for job in server.app.jobs.values():
            job["cancel"].set()
        server.server_close()


if __name__ == "__main__":
    main()
