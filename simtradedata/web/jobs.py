"""One constrained update process at a time, with durable task summaries."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from simtradedata.web.coverage import inspect_coverage
from simtradedata.web.operations import EVENT_PREFIX
from simtradedata.web.plans import build_plan


def now():
    return datetime.now(timezone.utc).isoformat()


def summary(coverage):
    return [{"key": row["key"], "records": row["records"], "securities": row["securities"],
             "missing_dates": len(row["missing_dates"]), "null_fields": row["null_fields"]}
            for row in coverage["datasets"]]


class JobManager:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.state_dir = self.data_dir / "ui-jobs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.plans = {}
        self.cache = {}
        history = self.state_dir / "history.json"
        self.jobs = json.loads(history.read_text()) if history.exists() else []
        for job in self.jobs:
            if job["status"] in {"running", "cancelling"}:
                job.update(status="interrupted", stage="interrupted", ended_at=now())

    def _save(self):
        temporary = self.state_dir / "history.json.tmp"
        temporary.write_text(json.dumps(self.jobs[:30], ensure_ascii=False, indent=2))
        temporary.replace(self.state_dir / "history.json")

    def overview(self, start, end, symbols=None):
        key = (start, end, tuple(symbols or []))
        with self.lock:
            if self.active:
                value = self.cache.get(key)
                if value:
                    return {**value[1], "busy": True}
                return {"busy": True, "datasets": [], "start_date": start, "end_date": end,
                        "detail": "更新中，请等待任务完成后读取此范围"}
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 10:
                return cached[1]
            data = inspect_coverage(self.data_dir, start, end, symbols)
            self.cache[key] = (time.monotonic(), data)
            if len(self.cache) > 30:
                self.cache.pop(next(iter(self.cache)))
            return data

    def preview(self, request):
        with self.lock:
            if self.active:
                raise RuntimeError("已有更新任务运行，请完成或取消后再创建任务")
            plan = build_plan(self.data_dir, request)
            plan_id = uuid.uuid4().hex
            self.plans = {key: value for key, value in self.plans.items() if time.monotonic() - value[0] < 600}
            self.plans[plan_id] = (time.monotonic(), plan)
            if len(self.plans) > 30:
                self.plans.pop(next(iter(self.plans)))
            return {key: value for key, value in plan.items() if key != "before"} | {"plan_id": plan_id}

    def start(self, plan_id):
        with self.lock:
            if self.active:
                raise RuntimeError("已有更新任务运行")
            cached = self.plans.pop(plan_id, None)
            if not cached or time.monotonic() - cached[0] > 600:
                raise ValueError("更新预览已过期，请重新预览")
            plan = build_plan(self.data_dir, cached[1]["request"])
            job_id = uuid.uuid4().hex
            request_file = self.state_dir / f"{job_id}.json"
            request_file.write_text(json.dumps(plan["request"]))
            job = {"id": job_id, "request": plan["request"], "status": "running", "stage": "prepare",
                   "started_at": now(), "ended_at": None, "completed": 0, "total": plan["unit_count"],
                   "message": "准备后台任务", "logs": [], "before": summary(plan["before"]), "after": None}
            self.active = job_id
            self.jobs.insert(0, job)
            self.jobs = self.jobs[:30]
            key = (plan["request"]["start_date"], plan["request"]["end_date"], tuple(plan["request"]["symbols"]))
            self.cache[key] = (time.monotonic(), plan["before"])
            root = Path(__file__).resolve().parents[2]
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", "-m", "simtradedata.web.operations", str(self.data_dir), str(request_file)],
                    cwd=self.data_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", "")},
                    start_new_session=os.name != "nt",
                )
            except Exception:
                self.active = None
                job.update(status="failed", ended_at=now(), message="后台进程启动失败")
                self._save()
                raise
            self._save()
            threading.Thread(target=self._watch, args=(job, self.process), daemon=True).start()
            return json.loads(json.dumps(job))

    def _watch(self, job, process):
        try:
            for line in process.stdout:
                line = line.rstrip()
                with self.lock:
                    if line.startswith(EVENT_PREFIX):
                        event = json.loads(line[len(EVENT_PREFIX):])
                        if event.get("stage") != job["stage"]:
                            for field in ["downloaded_bytes", "total_bytes", "bytes_per_second"]:
                                job.pop(field, None)
                        job.update({key: event[key] for key in ["stage", "message", "completed", "total", "written", "result",
                                                              "downloaded_bytes", "total_bytes", "bytes_per_second"] if key in event})
                    else:
                        job["logs"].append(line[-2000:])
                        job["logs"] = job["logs"][-300:]
            code = process.wait()
            with self.lock:
                job["status"] = "cancelled" if job["status"] == "cancelling" else ("completed" if code == 0 else "failed")
                job.update(ended_at=now(), exit_code=code)
                try:
                    request = job["request"]
                    job["after"] = summary(inspect_coverage(self.data_dir, request["start_date"], request["end_date"], request["symbols"]))
                except Exception as error:
                    job["logs"].append(f"更新后覆盖检查失败：{error}")
                    if job["status"] == "completed":
                        job["status"] = "failed"
                self.active, self.process = None, None
                self.cache.clear()
                self._save()
        except Exception as error:
            with self.lock:
                job.update(status="failed", ended_at=now(), message=str(error))
                self.active, self.process = None, None
                self._save()

    def cancel(self, job_id):
        with self.lock:
            if self.active != job_id or self.process is None:
                raise ValueError("该任务已经结束")
            job = next(row for row in self.jobs if row["id"] == job_id)
            job.update(status="cancelling", message="正在取消，已提交的数据保留")
            if self.process.poll() is None:
                if os.name == "nt":
                    self.process.terminate()
                else:
                    os.killpg(self.process.pid, signal.SIGTERM)
            self._save()

    def list_jobs(self):
        with self.lock:
            return json.loads(json.dumps(self.jobs))
