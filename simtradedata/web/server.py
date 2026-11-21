"""Loopback HTTP UI: JSON endpoints, static assets, fixed update intents."""

import argparse
import json
import mimetypes
import os
import sys
import uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from simtradedata.web.jobs import JobManager
from simtradedata.web.plans import validate_request
from simtradedata.web.source_health import SourceHealth

STATIC_DIR = Path(__file__).parent / "static"


def handler_for(manager, health=None, restart_enabled=False):
    instance_id = uuid.uuid4().hex
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, data):
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                if parsed.path == "/api/service":
                    self.respond(200, {"instance_id": instance_id, "restart_supported": restart_enabled})
                elif parsed.path == "/api/source-health" and health is not None:
                    self.respond(200, health.snapshot())
                elif parsed.path == "/api/jobs":
                    self.respond(200, {"jobs": manager.list_jobs()})
                elif parsed.path == "/api/recommendation":
                    args = parse_qs(parsed.query)
                    symbols = args.get("symbols", [""])[0]
                    self.respond(200, manager.recommendation(args.get("dataset", ["daily"])[0], symbols.split(",") if symbols else []))
                elif parsed.path == "/api/overview":
                    args = parse_qs(parsed.query)
                    today = date.today().isoformat()
                    request = validate_request({"dataset": "daily", "mode": "incremental",
                                                "start_date": args.get("start_date", [(date.today() - timedelta(days=90)).isoformat()])[0],
                                                "end_date": args.get("end_date", [today])[0],
                                                "symbols": args.get("symbols", [""])[0].split(",") if args.get("symbols", [""])[0] else []})
                    self.respond(200, manager.overview(request["start_date"], request["end_date"], request["symbols"]))
                else:
                    path = (STATIC_DIR / ("index.html" if parsed.path == "/" else parsed.path.lstrip("/"))).resolve()
                    if not path.is_relative_to(STATIC_DIR.resolve()) or not path.is_file():
                        self.respond(404, {"error": "页面不存在；请先构建管理界面"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(path.read_bytes())
            except (BrokenPipeError, ConnectionResetError):
                pass  # Page reload can close an in-flight request.
            except (ValueError, TypeError) as error:
                self.respond(400, {"error": str(error)})
            except Exception as error:
                self.respond(503, {"error": f"数据读取失败：{error}"})

        def do_POST(self):
            port = self.server.server_address[1]
            allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
            if port == 80:
                allowed_hosts.update({"localhost", "127.0.0.1"})
            hosts = self.headers.get_all("Host", [])
            origins = self.headers.get_all("Origin", [])
            if (len(hosts) != 1 or hosts[0] not in allowed_hosts
                    or self.headers.get_all("X-SimTrade-UI", []) != ["1"]
                    or (origins and origins != [f"http://{hosts[0]}"])):
                self.respond(403, {"error": "仅接受管理页面发起的操作"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 16384 or self.headers.get_content_type() != "application/json":
                    raise ValueError("请求体应为小于 16KB 的 JSON")
                body = json.loads(self.rfile.read(length))
                if self.path == "/api/service/restart" and restart_enabled:
                    if body != {}:
                        raise ValueError("重启不接受自定义命令或参数")
                    with manager.lock:
                        if manager.active:
                            raise RuntimeError("请先完成或取消更新任务，再重启管理台")
                        if manager.restarting:
                            raise RuntimeError("管理台正在重启，请稍候")
                        manager.restarting = True
                        self.server.restart_requested = True
                    try:
                        self.respond(202, {"instance_id": instance_id, "status": "restarting"})
                    finally:
                        self.server.shutdown()
                elif self.path == "/api/source-health/check" and health is not None:
                    if body != {}:
                        raise ValueError("数据源检查不接受自定义地址或命令")
                    self.respond(202, health.check())
                elif self.path == "/api/plans":
                    self.respond(200, manager.preview(body))
                elif self.path == "/api/jobs":
                    if not isinstance(body, dict) or set(body) != {"plan_id"} or not isinstance(body["plan_id"], str):
                        raise ValueError("请先生成更新预览")
                    self.respond(202, manager.start(body["plan_id"]))
                elif self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
                    manager.cancel(self.path.split("/")[3])
                    self.respond(200, {"ok": True})
                else:
                    self.respond(404, {"error": "未知操作"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except RuntimeError as error:
                self.respond(409, {"error": str(error)})
            except (ValueError, TypeError, KeyError) as error:
                self.respond(400, {"error": str(error)})
            except Exception as error:
                self.respond(500, {"error": str(error)})
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    manager = JobManager(args.data_dir)
    health = SourceHealth(args.data_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(manager, health, restart_enabled=True))
    server.restart_requested = False
    health.start()
    print(f"数据管理：http://127.0.0.1:{args.port} · 数据目录：{manager.data_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        health.stop()
        if manager.active:
            manager.cancel(manager.active)
        server.server_close()
    if server.restart_requested:
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
