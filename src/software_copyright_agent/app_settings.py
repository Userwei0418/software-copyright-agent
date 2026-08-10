import json

from .service import utc_now
from .storage import Database


DEFAULTS = {
    "manual_model_id": None,
    "diagram_model_id": None,
    "temperature": 0.3,
    "max_output_tokens": 8192,
    "source_strategy": "standard",
    "auto_preview": True,
}


class AppSettingsService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self) -> dict:
        self._database.initialize()
        with self._database.connect() as connection:
            rows = connection.execute("SELECT key, value_json FROM app_settings").fetchall()
        result = dict(DEFAULTS)
        for row in rows:
            if row["key"] in result:
                result[row["key"]] = json.loads(row["value_json"])
        return result

    def save(self, values: dict) -> dict:
        merged = dict(DEFAULTS)
        merged.update(values)
        if merged["source_strategy"] not in {"standard", "relaxed", "maximum"}:
            raise ValueError("Invalid source strategy")
        if not isinstance(merged["temperature"], (int, float)) or not 0 <= merged["temperature"] <= 2:
            raise ValueError("Temperature must be between 0 and 2")
        if not isinstance(merged["max_output_tokens"], int) or not 1024 <= merged["max_output_tokens"] <= 32768:
            raise ValueError("Max output tokens must be between 1024 and 32768")
        if not isinstance(merged["auto_preview"], bool):
            raise ValueError("auto_preview must be boolean")
        self._database.initialize()
        now = utc_now()
        with self._database.connect() as connection:
            verified_ids = {row[0] for row in connection.execute(
                "SELECT id FROM model_configs WHERE enabled = 1 AND verified_at IS NOT NULL"
            ).fetchall()}
            for key in ("manual_model_id", "diagram_model_id"):
                if merged[key] is not None and merged[key] not in verified_ids:
                    raise ValueError("Default model must be verified and enabled")
            for key, value in merged.items():
                connection.execute(
                    """INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                    updated_at=excluded.updated_at""",
                    (key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), now),
                )
        return merged
