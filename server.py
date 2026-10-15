"""Local city planning service; all game access remains read-only."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
from pathlib import Path
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

import game_data
from planning_defaults import build_defaults

APP_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("game-reader")


class Application:
    def __init__(self, game_dir: Path):
        self.game_dir = Path(game_dir).resolve()
        self.snapshots = {}
        self.jobs = {}
        self.lock = threading.RLock()

    def load(self, key):
        snapshot = game_data.load_snapshot(self.game_dir, key)
        with self.lock:
            self.snapshots[snapshot["id"]] = snapshot
            while len(self.snapshots) > 8:
                del self.snapshots[next(iter(self.snapshots))]
        return {**snapshot, "planningDefaults": build_defaults(snapshot)}

    def start(self, snapshot_id, options):
        if not isinstance(options, dict):
            raise ValueError("规划选项必须是对象。")
        with self.lock:
            if snapshot_id not in self.snapshots:
                raise ValueError("快照已失效，请重新读取存档。")
            if any(job["state"] in ("running", "queued", "cancelling") for job in self.jobs.values()):
                raise RuntimeError("已有规划正在运行，请先停止或等待完成。")
            snapshot = self.snapshots[snapshot_id]
            job_id, cancel = uuid.uuid4().hex, threading.Event()
            job = {"id": job_id, "state": "queued", "progress": {}, "result": None,
                   "error": None, "createdAt": time.time(), "cancel": cancel}
            self.jobs[job_id] = job
            for old_id in list(self.jobs)[:-12]:
                if self.jobs[old_id]["state"] not in ("running", "queued", "cancelling"):
                    del self.jobs[old_id]

        def report(update):
            with self.lock:
                job["progress"] = update if isinstance(update, dict) else {"message": str(update)}

        def run():
            with self.lock:
                job["state"] = "running"
            try:
                from city_planner import solve
                from city_validation import validate_result
                result = solve(snapshot, options, progress=report, cancel=cancel)
                if result.get("layout"):
                    validate_result(snapshot, options, result)
                with self.lock:
                    job["result"] = result
                    job["state"] = "cancelled" if cancel.is_set() else "done"
            except Exception as exc:
                LOG.exception("Planning failed")
                with self.lock:
                    job["error"], job["state"] = str(exc), "error"

        threading.Thread(target=run, name=f"city-{job_id[:8]}", daemon=True).start()
        return {"jobId": job_id}

    def get_job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError("找不到此规划任务。")
            return {k: v for k, v in self.jobs[job_id].items() if k != "cancel"}

    def cancel_job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError("找不到此规划任务。")
            job = self.jobs[job_id]
            if job["state"] in ("running", "queued", "cancelling"):
                job["cancel"].set()
                job["state"] = "cancelling"
        return self.get_job(job_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "CityPlanner/3.0"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        LOG.info(fmt, *args)

    def send_content(self, data, content_type, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value, code=200):
        self.send_content(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                          "application/json; charset=utf-8", code)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/api/health":
                return self.send_json({"app": "game-data-reader", "version": "3.0",
                                       "mode": "city-planner", "readOnly": True,
                                       "pid": os.getpid(), "gameDir": str(self.app.game_dir)})
            if path == "/api/saves":
                return self.send_json({"saves": game_data.list_saves(self.app.game_dir)})
            if path == "/api/assets":
                manifest = APP_DIR / "extracted_assets" / "manifest.json"
                return self.send_json(json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {})
            if path.startswith("/api/jobs/"):
                return self.send_json(self.app.get_job(path.rsplit("/", 1)[-1]))
            # Old query parameters never restore plans from the discarded planner.
            if path in ("/", "/index.html", "/city.html"):
                return self.send_content((APP_DIR / "web" / "index.html").read_bytes(),
                                         "text/html; charset=utf-8")
            if path in ("/city.js", "/city.css"):
                target = APP_DIR / "web" / path.lstrip("/")
                return self.send_content(target.read_bytes(),
                                         ("text/javascript" if path.endswith(".js") else "text/css") + "; charset=utf-8")
            if path.startswith("/assets/"):
                root = (APP_DIR / "extracted_assets").resolve()
                target = (root / unquote(path[len("/assets/"):])).resolve()
                if target.is_relative_to(root) and target.is_file():
                    return self.send_content(target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            return self.send_json({"error": "接口或页面不存在。"}, 404)
        except KeyError as exc:
            self.send_json({"error": str(exc)}, 404)
        except (ValueError, OSError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception:
            LOG.exception("Read failed")
            self.send_json({"error": "读取失败，请查看本地服务日志。"}, 500)

    def do_POST(self):
        try:
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host", ""):
                return self.send_json({"error": "只允许本地读取服务发起请求。"}, 403)
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return self.send_json({"error": "跨站请求被拒绝。"}, 403)
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 2_000_000:
                return self.send_json({"error": "请求过大。"}, 413)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("请求必须是对象。")
            if path == "/api/solve":
                return self.send_json(self.app.start(data.get("snapshotId"), data.get("options", {})), 202)
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                return self.send_json(self.app.cancel_job(path.split("/")[3]))
            if path != "/api/load":
                return self.send_json({"error": "接口不存在。"}, 404)
            if not isinstance(data.get("key"), str):
                raise ValueError("请提供有效的存档 key。")
            return self.send_json(self.app.load(data["key"]))
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, 409)
        except (ValueError, KeyError, TypeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception:
            LOG.exception("Read failed")
            self.send_json({"error": "读取失败，请查看本地服务日志。"}, 500)


def main():
    parser = argparse.ArgumentParser(description="主城规划工具：只读存档，不操作游戏")
    parser.add_argument("--game-dir", type=Path, default=APP_DIR.parent)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.app = Application(args.game_dir)
    url = f"http://127.0.0.1:{args.port}/"
    LOG.info("游戏内容读取服务已启动：%s；不操作游戏进程或写入存档。", url)
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
