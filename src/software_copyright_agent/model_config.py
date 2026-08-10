import json
from dataclasses import dataclass

from .service import utc_now
from .storage import Database


@dataclass(frozen=True)
class ModelConfigInput:
    id: str
    name: str
    protocol_id: str
    base_url: str
    model_name: str
    credential_ref: str = None


class ModelConfigService:
    PROTOCOLS = {"openai_compatible", "anthropic", "ollama"}

    def __init__(self, database: Database) -> None:
        self._database = database

    def list(self) -> list:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT id, name, protocol_id, base_url, model_name, credential_ref,
                settings_json, enabled, verified_at, created_at, updated_at FROM model_configs
                ORDER BY enabled DESC, updated_at DESC"""
            ).fetchall()
        return [self._public(row) for row in rows]

    def upsert(self, value: ModelConfigInput) -> dict:
        if value.protocol_id not in self.PROTOCOLS:
            raise ValueError("Unsupported model protocol")
        if not value.id or not value.name.strip() or not value.model_name.strip():
            raise ValueError("Model id, name and model name are required")
        base_url = value.base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Model base URL must use HTTP or HTTPS")
        now = utc_now()
        self._database.initialize()
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO model_configs(id, name, protocol_id, base_url, model_name,
                credential_ref, settings_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                protocol_id=excluded.protocol_id, base_url=excluded.base_url,
                model_name=excluded.model_name, credential_ref=excluded.credential_ref,
                updated_at=excluded.updated_at""",
                (value.id, value.name.strip(), value.protocol_id, base_url,
                 value.model_name.strip(), value.credential_ref,
                 json.dumps({}, separators=(",", ":")), now, now),
            )
            row = connection.execute(
                """SELECT id, name, protocol_id, base_url, model_name, credential_ref,
                settings_json, enabled, verified_at, created_at, updated_at FROM model_configs WHERE id = ?""",
                (value.id,),
            ).fetchone()
        return self._public(row)

    def mark_verified(self, config_id: str) -> dict:
        now = utc_now()
        self._database.initialize()
        with self._database.connect() as connection:
            changed = connection.execute(
                "UPDATE model_configs SET verified_at = ?, updated_at = ? WHERE id = ?",
                (now, now, config_id),
            ).rowcount
            if not changed:
                raise ValueError("Model config not found")
            row = connection.execute(
                """SELECT id, name, protocol_id, base_url, model_name, credential_ref,
                settings_json, enabled, verified_at, created_at, updated_at FROM model_configs WHERE id = ?""",
                (config_id,),
            ).fetchone()
        return self._public(row)

    def delete(self, config_id: str) -> None:
        self._database.initialize()
        with self._database.connect() as connection:
            if not connection.execute(
                "DELETE FROM model_configs WHERE id = ?", (config_id,)
            ).rowcount:
                raise ValueError("Model config not found")
            connection.execute(
                """UPDATE app_settings SET value_json = 'null', updated_at = ?
                WHERE key IN ('manual_model_id', 'diagram_model_id') AND value_json = ?""",
                (utc_now(), json.dumps(config_id, ensure_ascii=False, separators=(",", ":"))),
            )

    def set_endpoint_mode(self, config_id: str, endpoint_mode: str) -> dict:
        if endpoint_mode not in {"messages", "chat_completions", "responses", "ollama_chat"}:
            raise ValueError("Unsupported endpoint mode")
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT settings_json FROM model_configs WHERE id = ?", (config_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Model config not found")
            settings = json.loads(row["settings_json"])
            settings["endpoint_mode"] = endpoint_mode
            connection.execute(
                "UPDATE model_configs SET settings_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(settings, separators=(",", ":")), utc_now(), config_id),
            )
            updated = connection.execute(
                """SELECT id, name, protocol_id, base_url, model_name, credential_ref,
                settings_json, enabled, verified_at, created_at, updated_at
                FROM model_configs WHERE id = ?""", (config_id,)
            ).fetchone()
        return self._public(updated)

    @staticmethod
    def _public(row) -> dict:
        settings = json.loads(row["settings_json"])
        return {
            "id": row["id"], "name": row["name"],
            "protocol_id": row["protocol_id"], "base_url": row["base_url"],
            "model_name": row["model_name"],
            "endpoint_mode": settings.get("endpoint_mode"),
            "provider_id": row["credential_ref"] or row["id"],
            "has_credential": bool(row["credential_ref"]),
            "enabled": bool(row["enabled"]), "verified_at": row["verified_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
