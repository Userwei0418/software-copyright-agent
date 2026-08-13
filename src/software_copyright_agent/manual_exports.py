import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .service import utc_now
from .storage import Database


class ManualExportError(ValueError):
    pass


class ManualExportService:
    """Verifies native desktop exports and keeps a durable local receipt."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def record(self, job_id: str, version: int, export_kind: str,
               destination: str, size_bytes: int, sha256: str) -> dict:
        if export_kind not in {"review", "formal"}:
            raise ManualExportError("导出类型无效")
        if version < 1 or size_bytes < 1:
            raise ManualExportError("导出文件元数据无效")
        target = Path(destination).expanduser().resolve()
        if target.suffix.lower() != ".docx" or not target.is_file():
            raise ManualExportError("导出文件未实际落盘")
        actual_size = target.stat().st_size
        if actual_size != size_bytes:
            raise ManualExportError("导出文件大小校验失败")
        actual_sha256 = self._digest(target)
        if actual_sha256 != sha256:
            raise ManualExportError("导出文件完整性校验失败")

        self._database.initialize()
        with self._database.connect() as connection:
            document = connection.execute(
                """SELECT id,sha256,qa_json FROM manual_document_artifacts
                WHERE job_id=? AND version=?""", (job_id, version),
            ).fetchone()
            if document is None:
                raise ManualExportError("导出的说明书版本不存在")
            if not document["sha256"] or document["sha256"] != actual_sha256:
                raise ManualExportError("导出内容与已装配说明书不一致")
            document_kind = json.loads(document["qa_json"] or "{}").get(
                "document_kind", "formal_candidate"
            )
            if export_kind == "formal" and document_kind != "final_document":
                raise ManualExportError("审阅稿不能登记为终稿，请先由人工生成终稿")
            record_id, created_at = str(uuid4()), utc_now()
            connection.execute(
                """INSERT INTO manual_export_records(
                id,document_artifact_id,job_id,document_version,export_kind,
                destination_path,size_bytes,sha256,verified,created_at
                ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                (record_id, document["id"], job_id, version, export_kind,
                 str(target), actual_size, actual_sha256, created_at),
            )
        return {"id": record_id, "job_id": job_id, "document_version": version,
                "export_kind": export_kind, "destination_path": str(target),
                "size_bytes": actual_size, "sha256": actual_sha256,
                "verified": True, "created_at": created_at}

    def list(self, job_id: str) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id,job_id,document_version,export_kind,destination_path,
                size_bytes,sha256,verified,created_at FROM manual_export_records
                WHERE job_id=? ORDER BY created_at DESC""", (job_id,),
            ).fetchall()
        return [{**dict(row), "verified": bool(row["verified"])} for row in rows]

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
