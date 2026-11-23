"""One current task in memory; no persisted update history or logs."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
import tempfile
import re
from datetime import datetime, timezone
from pathlib import Path

from simtradedata.web.coverage import inspect_coverage
from simtradedata.web.operations import EVENT_PREFIX
from simtradedata.web.plans import build_plan
from simtradedata.web.cache_state import migrate_financial_markers
from simtradedata.web.recommendations import recommend


def now():
    return datetime.now(timezone.utc).isoformat()


def summary(coverage):
    return [{"key": row["key"], "records": row["records"], "securities": row["securities"],
             "missing_dates": len(row["missing_dates"]), "null_fields": row["null_fields"],
             **({"categories": [{**category, "missing_dates": len(category["missing_dates"])} for category in row["categories"]]} if "categories" in row else {})}
            for row in coverage["datasets"]]


class JobManager:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        self.restarting = False
        self.process = None
        self.plans = {}
        self.cache = {}
        self.jobs = []
        self.etf_catalogue = None
        migrate_financial_markers(self.data_dir)
        for path in [self.data_dir / "calendar_ranges.json", self.data_dir / "downloads" / "completed_scopes.json", self.data_dir / "downloads" / "hsjday.inventory.json"]:
            path.unlink(missing_ok=True)
        old = self.data_dir / "ui-jobs"
        if old.exists():
            for path in old.iterdir():
                if path.is_file() and (path.name in {"history.json", "history.json.tmp"} or re.fullmatch(r"[a-f0-9]{32}\.json", path.name)):
                    path.unlink()
            if not any(old.iterdir()):
                old.rmdir()

    def overview(self, start, end, symbols=None, include_pcf=True):
        key = (start, end, tuple(symbols or []))
        if not include_pcf:
            key += (False,)
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
            data = inspect_coverage(self.data_dir, start, end, symbols, include_pcf=include_pcf)
            self.cache[key] = (time.monotonic(), data)
            if len(self.cache) > 30:
                self.cache.pop(next(iter(self.cache)))
            return data

    def _include_etfs(self, request, result, start=None):
        if self.etf_catalogue is None or request["dataset"] != "daily" or request["symbols"] or request["mode"] != "auto":
            return result
        catalogue = self.etf_catalogue.get()
        if catalogue['running'] or catalogue['error'] or catalogue['warnings']:
            if start is not None:
                request['start_date'] = start
            result.update(work_pending=True, status='ready', checking_etfs=catalogue['running'])
            return result
        from simtradedata.web.etf_catalogue import daily_etf_gaps
        import duckdb
        database = self.data_dir / 'cn.duckdb'
        if not database.exists():
            return result
        candidates = [row['symbol'] for row in catalogue['etfs'] if row.get('local_daily') or row.get('listing_info', {}).get('state') not in {'pre_listing','not_established'}]
        from simtradedata.web.completeness import calendar_covers
        if candidates and not calendar_covers(self.data_dir, request['start_date'], request['end_date']):
            result.update(work_pending=True, status='ready', needs_calendar=True)
            return result
        with duckdb.connect(str(database), read_only=True) as conn:
            gaps = daily_etf_gaps(conn, candidates, start or request['start_date'], request['end_date'])
        if gaps:
            result.update(work_pending=True, status='ready')
            result.setdefault('update_summary', {})['pending_etfs'] = len(gaps)
            if start is not None:
                request['start_date'] = min(request['start_date'], min(first for first, _ in gaps.values()))
        return result

    def preview(self, request):
        with self.lock:
            if self.active:
                raise RuntimeError("已有更新任务运行，请完成或取消后再创建任务")
            plan = build_plan(self.data_dir, request)
            plan = self._include_etfs(plan["request"], plan)
            plan_id = uuid.uuid4().hex
            self.plans = {key: value for key, value in self.plans.items() if time.monotonic() - value[0] < 600}
            self.plans[plan_id] = (time.monotonic(), plan)
            if len(self.plans) > 30:
                self.plans.pop(next(iter(self.plans)))
            return {key: value for key, value in plan.items() if key != "before"} | {"plan_id": plan_id}

    def recommendation(self, dataset, symbols):
        with self.lock:
            if self.active:
                raise RuntimeError("更新任务运行中，完成后自动检测下一段缺口")
            result = recommend(self.data_dir, dataset, symbols)
            return self._include_etfs(result['request'], result, result['scope_start'])

    def start(self, plan_id):
        with self.lock:
            if self.restarting:
                raise RuntimeError("管理台正在重启，请稍候")
            if self.active:
                raise RuntimeError("已有更新任务运行")
            cached = self.plans.pop(plan_id, None)
            if not cached or time.monotonic() - cached[0] > 600:
                raise ValueError("更新预览已过期，请重新预览")
            plan = self._include_etfs(cached[1]["request"], build_plan(self.data_dir, cached[1]["request"]))
            if not plan.get("work_pending", True):
                return {"status": "up_to_date", "message": "当前范围已同步，无需重复更新"}
            return self._launch(plan)

    def start_export(self, body):
        if not isinstance(body, dict) or set(body) != {'market'} or body['market'] not in {'cn', 'us'}:
            raise ValueError('导出只接受 cn 或 us 市场')
        plan = {'request': {'dataset': 'export', 'mode': 'full', 'market': body['market'],
                           'start_date': '1990-01-01', 'end_date': datetime.now().date().isoformat(), 'symbols': []},
                'before': {'datasets': []}, 'unit_count': 7}
        return self._launch(plan)

    def _launch(self, plan):
        with self.lock:
            if self.restarting or self.active:
                raise RuntimeError('请先完成或取消当前任务')
            job_id = uuid.uuid4().hex
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="simtrade-update-", delete=False) as temporary:
                json.dump(plan["request"], temporary)
                request_file = Path(temporary.name)
            job = {"id": job_id, "request": plan["request"], "status": "running", "stage": "prepare",
                   "started_at": now(), "ended_at": None, "completed": 0, "total": plan["unit_count"],
                   "message": "准备后台任务", "overall_completed": 0, "overall_total": plan["unit_count"] if plan["request"]["dataset"] in {"fundamentals", "pcf"} or plan["request"]["mode"] == "repair" else None,
                   "unit_label": "季度" if plan["request"]["dataset"] == "fundamentals" else "份清单" if plan["request"]["dataset"] == "pcf" else "个证券" if plan["request"]["mode"] == "repair" else "处理单元", "logs": [], "before": summary(plan["before"]), "after": None}
            if plan.get("needs_calendar"):
                for row in job["before"]:
                    if row["key"] == "pcf":
                        row["missing_dates"] = None
            self.active = job_id
            self.jobs = [job]
            key = (plan["request"]["start_date"], plan["request"]["end_date"], tuple(plan["request"]["symbols"]))
            if plan['request']['dataset'] != 'export':
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
                request_file.unlink(missing_ok=True)
                raise
            threading.Thread(target=self._watch, args=(job, self.process, request_file), daemon=True).start()
            return json.loads(json.dumps(job))

    def _watch(self, job, process, request_file):
        try:
            for line in process.stdout:
                line = line.rstrip()
                with self.lock:
                    if line.startswith(EVENT_PREFIX):
                        event = json.loads(line[len(EVENT_PREFIX):])
                        if event.get("stage") != job["stage"] or ("current_unit" in event and event["current_unit"] != job.get("current_unit")):
                            for field in ["downloaded_bytes", "total_bytes", "bytes_per_second"]:
                                job.pop(field, None)
                        job.update({key: event[key] for key in ["stage", "message", "completed", "total", "written", "result",
                                                              "downloaded_bytes", "total_bytes", "bytes_per_second", "overall_completed", "overall_total", "current_unit"] if key in event})
                    else:
                        job["logs"].append(line[-2000:])
                        job["logs"] = job["logs"][-300:]
            code = process.wait()
            with self.lock:
                if job['request']['dataset'] == 'export':
                    from simtradedata.web.exports import cleanup_export
                    cleanup_export(self.data_dir, job['request']['market'])
                job["status"] = "cancelled" if job["status"] == "cancelling" else ("completed" if code == 0 else "failed")
                job.update(ended_at=now(), exit_code=code)
                try:
                    request = job["request"]
                    job["after"] = [] if request['dataset'] == 'export' else summary(inspect_coverage(self.data_dir, request["start_date"], request["end_date"], request["symbols"], include_pcf=request["dataset"] == "pcf"))
                except Exception as error:
                    job["logs"].append(f"更新后覆盖检查失败：{error}")
                    if job["status"] == "completed":
                        job["status"] = "failed"
                if request['dataset'] == 'export':
                    job['message'] = '回测数据包已生成' if job['status'] == 'completed' else '导出未完成，原有数据包保留'
                elif job['status'] == 'completed':
                    job['message'] = '更新已完成，已有有效数据保留。'
                elif job['status'] == 'failed':
                    job['message'] = '更新已结束，部分单元仍需核验。'
                elif job['status'] == 'cancelled':
                    job['message'] = '更新已取消，已保存的数据保留。'
                if self.etf_catalogue is not None:
                    self.etf_catalogue.mark_not_trading(job.get('result', {}).get('not_trading', []))
                self.active, self.process = None, None
                self.cache.clear()
        except Exception as error:
            with self.lock:
                job.update(status="failed", ended_at=now(), message=str(error))
                self.active, self.process = None, None
        finally:
            request_file.unlink(missing_ok=True)

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

    def list_jobs(self):
        with self.lock:
            return json.loads(json.dumps(self.jobs))
