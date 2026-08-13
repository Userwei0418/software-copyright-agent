import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from .service import utc_now
from .storage import Database, encode_json


QUICK_STAGES = (
    ("scan", "扫描项目", "读取源码、过滤依赖并建立证据索引"),
    ("confirm", "确认项目信息", "写入软件名称与版本号"),
    ("source_plan", "规划源码材料", "筛选具有代表性的代码文件"),
    ("code_preview", "代码分页预检", "平衡抽样并形成 60 页代码版式"),
    ("source_docx", "生成源码文档", "装配并逐页检查源代码 Word"),
    ("screenshots", "处理界面截图", "导入、视觉解读并自动采用真实截图"),
    ("manual", "生成软件说明书", "研究证据、撰写正文并并行绘制图表"),
    ("finalize", "装配双文档", "生成终稿并完成最终逐页检查"),
    ("delivery", "交付完成", "两个 Word 文档已进入我的资产"),
)


class QuickStartError(ValueError):
    pass


class QuickStartService:
    """Durable unattended orchestration built from the accepted manual workflows."""

    def __init__(self, database: Database, *, scan, inspection, confirmation, source,
                 screenshots, pipeline, workflow, documents, qa, settings) -> None:
        self._database = database
        self._scan = scan
        self._inspection = inspection
        self._confirmation = confirmation
        self._source = source
        self._screenshots = screenshots
        self._pipeline = pipeline
        self._workflow = workflow
        self._documents = documents
        self._qa = qa
        self._settings = settings
        self._locks: dict[str, threading.Lock] = {}
        # Source-material and manual-document branches update different stages of
        # the same durable run.  Serialize only the short JSON checkpoint writes;
        # model calls, rendering and document generation remain genuinely parallel.
        self._state_locks: dict[str, threading.RLock] = {}

    def create(self, config: dict) -> dict:
        normalized = self._normalize(config)
        run_id, now = str(uuid4()), utc_now()
        stages = [{"key": key, "title": title, "description": description,
                   "status": "pending", "attempt": 0, "message": "等待执行",
                   "started_at": None, "finished_at": None, "events": []}
                  for key, title, description in QUICK_STAGES]
        self._database.initialize()
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO quick_start_runs(id,status,current_stage,config_json,stages_json,
                outputs_json,created_at,updated_at) VALUES(?,'queued','scan',? ,?,'{}',?,?)""",
                (run_id, encode_json(normalized), encode_json(stages), now, now),
            )
        self._dispatch(run_id)
        return self.get(run_id)

    def retry(self, run_id: str) -> dict:
        run = self.get(run_id)
        if run["status"] not in {"failed", "waiting_for_user"}:
            raise QuickStartError("当前快速任务不需要重试")
        stages = run["stages"]
        # A project may have been removed from My Assets after this run stored its
        # scan checkpoint.  Reusing that checkpoint would try to write a dangling
        # task id and surface a raw SQLite FOREIGN KEY error.  In that case only,
        # reopen the full quick pipeline from scan using the saved input paths.
        if not run.get("task_id"):
            for stage in stages:
                stage.update(status="pending", attempt=0, message="等待执行",
                             started_at=None, finished_at=None)
                stage.pop("output", None)
                stage["events"] = []
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE quick_start_runs SET status='queued',safe_error_message=NULL,
                finished_at=NULL,current_stage=?,stages_json=?,outputs_json='{}',
                manual_job_id=CASE WHEN task_id IS NULL THEN NULL ELSE manual_job_id END,
                updated_at=? WHERE id=?""",
                (("scan" if not run.get("task_id") else run["current_stage"]),
                 encode_json(stages), utc_now(), run_id),
            )
        self._dispatch(run_id)
        return self.get(run_id)

    def discard(self, run_id: str) -> None:
        """Remove only the orchestration record, never project or generated assets."""
        run = self.get(run_id)
        if run["status"] in {"queued", "running"}:
            raise QuickStartError("后台流程仍在执行，需等待停止后再清空")
        with self._database.connect() as connection:
            connection.execute("DELETE FROM quick_start_runs WHERE id=?", (run_id,))

    def list(self, limit: int = 20) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM quick_start_runs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, limit)),),
            ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def get(self, run_id: str) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM quick_start_runs WHERE id=?", (run_id,),
            ).fetchone()
        if row is None:
            raise QuickStartError("快速任务不存在")
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json") or "{}")
        result["stages"] = json.loads(result.pop("stages_json") or "[]")
        result["outputs"] = json.loads(result.pop("outputs_json") or "{}")
        job_id = result.get("manual_job_id")
        if job_id:
            try:
                result["manual_job"] = self._pipeline.get(job_id)
            except Exception:
                result["manual_job"] = None
        else:
            result["manual_job"] = None
        return result

    def recover_interrupted(self) -> int:
        self._database.initialize()
        now = utc_now()
        message = "上次应用退出时自动流程被中断；已保留阶段结果，可从失败处重试"
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id,stages_json,current_stage FROM quick_start_runs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                stages = json.loads(row["stages_json"] or "[]")
                for stage in stages:
                    if stage["key"] == row["current_stage"] and stage["status"] == "running":
                        stage.update(status="failed", message=message, finished_at=now)
                connection.execute(
                    """UPDATE quick_start_runs SET status='failed',stages_json=?,
                    safe_error_message=?,finished_at=?,updated_at=? WHERE id=?""",
                    (encode_json(stages), message, now, now, row["id"]),
                )
        return len(rows)

    def _dispatch(self, run_id: str) -> None:
        lock = self._locks.setdefault(run_id, threading.Lock())
        if lock.locked():
            return
        threading.Thread(target=self._run_locked, args=(run_id, lock),
                         name="quick-start-" + run_id[:8], daemon=True).start()

    def _run_locked(self, run_id: str, lock: threading.Lock) -> None:
        with lock:
            try:
                self._execute(run_id)
            except Exception as error:
                self._fail(run_id, str(error))

    def _execute(self, run_id: str) -> None:
        run = self.get(run_id)
        config, task_id = run["config"], run.get("task_id")
        self._set_run(run_id, status="running", started=True)

        if not task_id:
            result = self._stage(run_id, "scan", lambda: self._scan.execute(
                Path(config["project_path"])))
            task_id = result.get("task_id") if isinstance(result, dict) else result.task_id
            self._set_run(run_id, task_id=task_id)
        else:
            self._skip_completed(run_id, "scan", "已复用此前扫描结果")

        self._stage(run_id, "confirm", lambda: self._confirm_metadata(
            task_id, config["software_name"], config["version"]))

        settings = self._settings.get()
        settings.update({
            "manual_model_id": config["manual_model_id"],
            "diagram_model_id": config["diagram_model_id"],
            "vision_model_id": config["vision_model_id"],
            "generation_concurrency": config["concurrency"],
            "source_strategy": config["source_strategy"],
        })
        self._settings.save(settings)

        def source_branch() -> dict:
            self._stage(run_id, "source_plan", lambda: self._source.build_source_plan(
                task_id, config["source_strategy"]))
            self._stage(run_id, "code_preview", lambda: self._source.build_code_preview(task_id))

            def source_document() -> dict:
                self._source.build_source_document(task_id)
                capability = self._source.source_document_qa_capability(task_id)
                if not capability.get("available"):
                    raise QuickStartError(
                        capability.get("message") or "当前设备无法逐页检查源码文档"
                    )
                return self._source.run_source_document_qa(task_id)

            snapshot = self._stage(run_id, "source_docx", source_document)
            if snapshot["source_document"]["quality"]["status"] != "passed":
                raise QuickStartError("源代码文档逐页检查未通过")
            return snapshot

        def manual_branch() -> dict:
            # One quick run owns exactly one manual job. Establish its
            # research-backed profile independently of source DOCX pagination.
            current = self.get(run_id)
            job_id = current.get("manual_job_id")
            if not job_id:
                job = self._pipeline.create(task_id, config["manual_model_id"])
                job_id = job["id"]
                self._set_run(run_id, manual_job_id=job_id)
            context = self._workflow.prepare_context(job_id)
            self._reopen_screenshots_if_profile_changed(
                run_id, context["project_profile"].get("fingerprint"))

            def screenshot_stage() -> dict:
                profile_context = self._workflow.prepare_context(job_id)
                assets = self._screenshots.list_assets(task_id)
                if assets:
                    imported = {"imported_count": 0, "reused_count": len(assets)}
                else:
                    imported = self._screenshots.import_folder(
                        task_id, Path(config["screenshot_folder"]),
                        config["recursive_screenshots"])
                    assets = self._screenshots.list_assets(task_id)
                pending = [item["id"] for item in assets
                           if item["analysis_status"] not in {"completed", "running", "queued"}]
                pending += [item["id"] for item in assets
                            if item["analysis_status"] == "pending"]
                pending = sorted(set(pending))
                if pending:
                    self._screenshots.analyze_many(
                        task_id, pending, config["vision_model_id"])
                assets = self._screenshots.list_assets(task_id)
                failed = [item for item in assets if item["analysis_status"] == "failed"]
                for _ in range(config["retry_limit"]):
                    if not failed:
                        break
                    for item in failed:
                        self._screenshots.retry_analysis(
                            task_id, item["id"], config["vision_model_id"])
                    failed = [item for item in self._screenshots.list_assets(task_id)
                              if item["analysis_status"] == "failed"]
                if failed:
                    raise QuickStartError("仍有 {0} 张截图解读失败：{1}".format(
                        len(failed), "、".join(item["title"] for item in failed[:4])))
                reviewed, reused = [], []
                for index, item in enumerate(self._screenshots.list_assets(task_id), 1):
                    interpretation = item.get("interpretation")
                    if item["analysis_status"] != "completed" or not interpretation:
                        continue
                    if (item.get("adoption_status") == "adopted"
                            and item.get("review_status") == "reviewed"
                            and item.get("sensitive_status") == "confirmed_safe"):
                        reused.append(item)
                        continue
                    reviewed.append(self._screenshots.review(
                        task_id, item["id"], interpretation, adopted=True,
                        group_title=interpretation.get("suggested_group") or "界面说明",
                        sort_order=interpretation.get("suggested_order") or index,
                        sensitive_status="confirmed_safe",
                    ))
                if not reviewed and not reused:
                    raise QuickStartError("截图文件夹中没有可自动采用的真实截图")
                return {"imported": imported.get("imported_count", 0),
                        "reused": len(reused), "adopted": len(reviewed),
                        "profile_version": profile_context["project_profile"]["version"],
                        "profile_fingerprint": profile_context["project_profile"].get(
                            "fingerprint")}

            self._stage(run_id, "screenshots", screenshot_stage)

            def manual_stage() -> dict:
                result = self._workflow.run_existing(job_id)
                if result.get("awaiting_assets"):
                    raise QuickStartError("说明书仍有图表或截图资产未完成，已保留结果，可一键重试")
                return {"job_id": job_id,
                        "awaiting_assets": result.get("awaiting_assets", False)}

            # Retrying the whole stage used to create v2/v3 jobs; this always
            # resumes the exact job bound above.
            return self._stage(run_id, "manual", manual_stage, retry_limit=0)

        # The two deliverables share the immutable scan and confirmed metadata,
        # then advance independently.  A failure in one branch does not cancel
        # the other, so its successfully produced artifacts remain available.
        branch_results, branch_errors = {}, []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="quick-deliverable") as executor:
            futures = {
                executor.submit(source_branch): "source",
                executor.submit(manual_branch): "manual",
            }
            for future in as_completed(futures):
                branch = futures[future]
                try:
                    branch_results[branch] = future.result()
                except Exception as error:
                    branch_errors.append((branch, error))
        if branch_errors:
            labels = {"source": "源码文档", "manual": "软件说明书"}
            raise QuickStartError("；".join(
                "{0}分支：{1}".format(labels[branch], error)
                for branch, error in branch_errors
            ))
        source_snapshot = branch_results["source"]
        job_id = branch_results["manual"]["job_id"]

        def finalize_stage() -> dict:
            documents = self._documents.list(job_id)
            candidate = next((item for item in documents
                              if item["document_kind"] == "formal_candidate"), None)
            if candidate is None:
                candidate = self._documents.assemble(job_id)
                qa_result = self._qa.execute(job_id, candidate["version"])
            else:
                try:
                    qa_result = self._qa.execute(job_id, candidate["version"])
                except Exception:
                    qa_result = {"qa_run": {"passed": False}}
            qa_summary = qa_result["qa_run"].get("summary") or {}
            # Manual QA publishes failed_check_count/failed_checks. Older quick
            # orchestration only looked for blocker_count, so a permissive
            # "finalize with warnings" run could accidentally treat a real QA
            # blocker as a warning. Warnings may continue; blockers never do.
            qa_blockers = int(
                qa_summary.get("failed_check_count")
                or qa_summary.get("blocker_count")
                or len(qa_summary.get("failed_checks") or [])
            )
            if qa_blockers > 0:
                raise QuickStartError("说明书质量检查仍有阻断问题，不能自动定稿")
            if not qa_result["qa_run"].get("passed") and not config["finalize_with_warnings"]:
                raise QuickStartError("说明书质量检查存在警告，当前配置不允许自动定稿")
            final = self._documents.finalize(job_id, candidate["version"])
            final_qa = self._qa.execute(job_id, final["version"])
            # QA updates status, preview and quality metadata in place. Return
            # that refreshed document rather than the pre-QA assembly snapshot;
            # otherwise Quick Start can say delivery is complete while its
            # cached output still reports not_checked and no preview PDF.
            final = final_qa["document"]
            final_summary = final_qa["qa_run"].get("summary") or {}
            final_blockers = int(
                final_summary.get("failed_check_count")
                or final_summary.get("blocker_count")
                or len(final_summary.get("failed_checks") or [])
            )
            if final_blockers > 0 or not final_qa["qa_run"].get("passed"):
                raise QuickStartError("终稿逐页检查出现阻断问题，已保留文档但未标记交付完成")
            return {"manual_document": final, "manual_quality": final_qa["qa_run"],
                    "source_document": source_snapshot["source_document"]}
        outputs = self._stage(run_id, "finalize", finalize_stage)
        self._set_run(run_id, outputs=outputs)
        self._stage(run_id, "delivery", lambda: {"ready": True})
        self._set_run(run_id, status="completed", finished=True, current_stage="delivery")

    def _confirm_metadata(self, task_id: str, name: str, version: str) -> dict:
        inspection = self._inspection.inspect(task_id)
        pending = {item["field_key"]: item for item in inspection["confirmations"]
                   if item["status"] == "pending"}
        for key, value in (("project.name", name), ("project.version", version)):
            if key in pending:
                self._confirmation.answer(task_id, key, value)
        refreshed = self._inspection.inspect(task_id)
        remaining = [item for item in refreshed["confirmations"]
                     if item["required"] and item["status"] == "pending"]
        for item in remaining:
            candidates = item.get("candidates") or []
            if candidates:
                self._confirmation.answer(task_id, item["field_key"], str(candidates[0]))
        remaining = [item for item in self._inspection.inspect(task_id)["confirmations"]
                     if item["required"] and item["status"] == "pending"]
        if remaining:
            raise QuickStartError("仍需人工确认：" + "、".join(
                item["question"] for item in remaining[:4]))
        # Quick Start values are an explicit user decision made before scanning.
        # They must win even when package metadata contains another reliable value.
        for key, value in (("project.name", name), ("project.version", version)):
            self._override_confirmed_fact(task_id, key, value)
        return {"software_name": name, "version": version}

    def _override_confirmed_fact(self, task_id: str, key: str, value: str) -> None:
        now, evidence_id, fact_id = utc_now(), str(uuid4()), str(uuid4())
        with self._database.connect() as connection:
            task = connection.execute(
                "SELECT snapshot_id FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            if task is None or not task["snapshot_id"]:
                raise QuickStartError("项目尚未形成扫描快照")
            active = connection.execute(
                """SELECT value_json,source FROM facts WHERE task_id=? AND fact_key=?
                AND status IN ('candidate','confirmed') ORDER BY created_at DESC LIMIT 1""",
                (task_id, key),
            ).fetchone()
            if active is not None and active["source"] == "user" and json.loads(
                    active["value_json"]) == value:
                return
            connection.execute(
                """UPDATE facts SET status='superseded' WHERE task_id=? AND fact_key=?
                AND status IN ('candidate','confirmed')""", (task_id, key),
            )
            connection.execute(
                """INSERT INTO evidence(id,snapshot_id,kind,relative_path,locator_json,
                excerpt,content_hash,extractor,confidence,sensitivity,created_at)
                VALUES(?,?,'user_confirmation',NULL,?,NULL,NULL,'quick_start',1.0,'normal',?)""",
                (evidence_id, task["snapshot_id"], encode_json({"field_key": key,
                 "source": "quick_start"}), now),
            )
            connection.execute(
                """INSERT INTO facts(id,task_id,fact_key,value_json,status,source,confidence,
                evidence_ids_json,created_at,confirmed_at)
                VALUES(?,?,?,?,'confirmed','user',1.0,?,?,?)""",
                (fact_id, task_id, key, encode_json(value), encode_json([evidence_id]), now, now),
            )

    def _stage(self, run_id: str, key: str, action: Callable[[], object],
               retry_limit: Optional[int] = None):
        run = self.get(run_id)
        existing = next(item for item in run["stages"] if item["key"] == key)
        if existing["status"] == "completed":
            return existing.get("output") or {}
        if retry_limit is None:
            retry_limit = int(run["config"].get("retry_limit", 0))
        self._set_run(run_id, current_stage=key)
        last_error = None
        for attempt in range(retry_limit + 1):
            message = "正在执行" if attempt == 0 else "上次失败，正在自动重试"
            self._update_stage(run_id, key, "running", message, increment=True)
            try:
                result = action()
                output = self._jsonable(result)
                self._update_stage(run_id, key, "completed", "已完成", output=output)
                return result
            except Exception as error:
                last_error = error
                self._update_stage(run_id, key, "failed", str(error))
        raise last_error

    def _skip_completed(self, run_id: str, key: str, message: str) -> None:
        run = self.get(run_id)
        stage = next(item for item in run["stages"] if item["key"] == key)
        if stage["status"] != "completed":
            self._update_stage(run_id, key, "completed", message, output={})

    def _reopen_screenshots_if_profile_changed(
            self, run_id: str, profile_fingerprint: Optional[str]) -> None:
        """Migrate older quick runs whose screenshots preceded research profiling."""
        lock = self._state_locks.setdefault(run_id, threading.RLock())
        with lock:
            run, now = self.get(run_id), utc_now()
            stages = run["stages"]
            stage = next(item for item in stages if item["key"] == "screenshots")
            recorded = (stage.get("output") or {}).get("profile_fingerprint")
            if stage["status"] != "completed" or recorded == profile_fingerprint:
                return
            stage.update(status="pending", message=(
                "项目画像已稳定，正在重新核对旧截图解读；有效结果将直接复用"
            ), finished_at=None)
            stage.setdefault("events", []).append({
                "at": now, "status": "pending", "attempt": stage.get("attempt", 0),
                "message": "检测到旧流程截图早于项目画像；仅重核截图，不新建说明书版本",
            })
            with self._database.connect() as connection:
                connection.execute(
                    "UPDATE quick_start_runs SET stages_json=?,updated_at=? WHERE id=?",
                    (encode_json(stages), now, run_id),
                )

    def _update_stage(self, run_id: str, key: str, status: str, message: str,
                      *, increment: bool = False, output: Optional[dict] = None) -> None:
        lock = self._state_locks.setdefault(run_id, threading.RLock())
        with lock:
            run, now = self.get(run_id), utc_now()
            stages = run["stages"]
            for stage in stages:
                if stage["key"] != key:
                    continue
                stage["status"], stage["message"] = status, message[:1000]
                events = stage.setdefault("events", [])
                if (not events or events[-1].get("status") != status
                        or events[-1].get("message") != message[:1000]):
                    events.append({"at": now, "status": status,
                                   "message": message[:1000],
                                   "attempt": int(stage.get("attempt") or 0) +
                                   (1 if increment else 0)})
                    stage["events"] = events[-30:]
                if increment:
                    stage["attempt"] = int(stage.get("attempt") or 0) + 1
                    stage["started_at"] = now
                    stage["finished_at"] = None
                if status in {"completed", "failed"}:
                    stage["finished_at"] = now
                if output is not None:
                    stage["output"] = output
            with self._database.connect() as connection:
                connection.execute(
                    "UPDATE quick_start_runs SET stages_json=?,updated_at=? WHERE id=?",
                    (encode_json(stages), now, run_id),
                )

    def _fail(self, run_id: str, message: str) -> None:
        now = utc_now()
        with self._database.connect() as connection:
            connection.execute(
                """UPDATE quick_start_runs SET status='failed',safe_error_message=?,
                finished_at=?,updated_at=? WHERE id=?""",
                (message[:1000], now, now, run_id),
            )

    def _set_run(self, run_id: str, *, status: Optional[str] = None,
                 task_id: Optional[str] = None, manual_job_id: Optional[str] = None,
                 current_stage: Optional[str] = None, outputs: Optional[dict] = None,
                 started: bool = False, finished: bool = False) -> None:
        fields, values, now = ["updated_at=?"], [utc_now()], utc_now()
        for column, value in (("status", status), ("task_id", task_id),
                              ("manual_job_id", manual_job_id),
                              ("current_stage", current_stage)):
            if value is not None:
                fields.append(column + "=?"); values.append(value)
        if outputs is not None:
            fields.append("outputs_json=?"); values.append(encode_json(outputs))
        if started:
            fields.append("started_at=COALESCE(started_at,?)"); values.append(now)
        if finished:
            fields.append("finished_at=?"); values.append(now)
        values.append(run_id)
        with self._database.connect() as connection:
            connection.execute("UPDATE quick_start_runs SET " + ",".join(fields) + " WHERE id=?",
                               tuple(values))

    @staticmethod
    def _normalize(config: dict) -> dict:
        required = ("project_path", "software_name", "version", "screenshot_folder",
                    "manual_model_id", "diagram_model_id", "vision_model_id")
        normalized = {key: str(config.get(key) or "").strip() for key in required}
        if any(not normalized[key] for key in required):
            raise QuickStartError("快速开始配置不完整")
        project, screenshots = Path(normalized["project_path"]).expanduser(), Path(
            normalized["screenshot_folder"]).expanduser()
        if not project.exists():
            raise QuickStartError("项目目录或 ZIP 不存在")
        if not screenshots.is_dir():
            raise QuickStartError("截图文件夹不存在")
        normalized.update({
            "project_path": str(project.resolve()),
            "screenshot_folder": str(screenshots.resolve()),
            "source_strategy": config.get("source_strategy", "standard"),
            "concurrency": max(1, min(10, int(config.get("concurrency", 3)))),
            "retry_limit": max(0, min(5, int(config.get("retry_limit", 2)))),
            "recursive_screenshots": bool(config.get("recursive_screenshots", True)),
            "finalize_with_warnings": bool(config.get("finalize_with_warnings", True)),
            "sensitive_confirmed": bool(config.get("sensitive_confirmed", False)),
            "auto_adopt_confirmed": bool(config.get("auto_adopt_confirmed", False)),
        })
        if normalized["source_strategy"] not in {"standard", "relaxed", "maximum"}:
            raise QuickStartError("源码策略无效")
        if not normalized["sensitive_confirmed"]:
            raise QuickStartError("请先确认截图文件夹不含敏感信息")
        if not normalized["auto_adopt_confirmed"]:
            raise QuickStartError("请先授权系统自动采用截图解读结果")
        return normalized

    @classmethod
    def _jsonable(cls, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): cls._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._jsonable(item) for item in value]
        if hasattr(value, "__dict__"):
            return cls._jsonable(value.__dict__)
        return str(value)
