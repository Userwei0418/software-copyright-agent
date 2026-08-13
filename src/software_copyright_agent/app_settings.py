import json

from .service import utc_now
from .storage import Database


DOCUMENT_STYLE_PROMPT = (
    "以中国软件著作权登记材料中的技术说明书为成文目标，使用正式、客观、准确、克制的第三人称中文技术表达。"
    "每章先用一段概述说明本章对象在系统中的定位、目的和边界，再按“组成与职责—处理流程与数据交互—"
    "结果与异常恢复”的逻辑展开；功能模块先用连贯段落说明它解决什么问题、由哪些部分组成、如何与其他模块协作，"
    "再补充确有比较价值的表格或并列条目。正文段落应信息完整、长短均衡、衔接自然，避免连续短句、名词堆砌、"
    "条目替代论述和同义重复。术语、模块名称、接口名称及主语保持前后一致，优先写清职责、输入、处理、输出、状态变化"
    "和失败恢复。禁止营销口号、空泛评价、模板化套话、写作过程说明、面向用户的提醒，以及证据不能支持的测试、验收或上线结论。"
)

LEGACY_DOCUMENT_STYLE_PROMPTS = {
    "采用正式、克制、面向软件著作权审阅的中文技术文风。先用连贯段落说明模块目的、边界与协作，再补充必要的结构化信息；避免口号、模板腔、重复概述和碎片化短句。",
}


DEFAULTS = {
    "manual_model_id": None,
    "diagram_model_id": None,
    "vision_model_id": None,
    "temperature": 0.3,
    "max_output_tokens": 8192,
    "source_strategy": "standard",
    "auto_preview": True,
    "generation_concurrency": 3,
    "document_style_prompt": DOCUMENT_STYLE_PROMPT,
    "diagram_style_prompt": (
        "生成前先在内部判断图表类型、阅读方向、分组层级、主流程和最少必要连线。"
        "采用专业、清晰、留白均衡的企业技术图风格；节点使用稳定语义标识，主流程方向明确，"
        "优先短正交连线，回路和跨层关系走外侧通道，避免交叉、穿越节点、标签重叠与过度装饰。"
    ),
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
        if result["document_style_prompt"] in LEGACY_DOCUMENT_STYLE_PROMPTS:
            result["document_style_prompt"] = DOCUMENT_STYLE_PROMPT
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
        if (not isinstance(merged["generation_concurrency"], int)
                or not 1 <= merged["generation_concurrency"] <= 10):
            raise ValueError("Generation concurrency must be between 1 and 10")
        for key in ("document_style_prompt", "diagram_style_prompt"):
            if not isinstance(merged[key], str) or len(merged[key]) > 12000:
                raise ValueError("Advanced style prompts must be text under 12000 characters")
        self._database.initialize()
        now = utc_now()
        with self._database.connect() as connection:
            available_ids = {row[0] for row in connection.execute(
                "SELECT id FROM model_configs WHERE enabled = 1"
            ).fetchall()}
            for key in ("manual_model_id", "diagram_model_id"):
                if merged[key] is not None and merged[key] not in available_ids:
                    raise ValueError("Default model must be configured and enabled")
            if merged["vision_model_id"] is not None:
                row = connection.execute(
                    "SELECT settings_json,verified_at FROM model_configs WHERE id=? AND enabled=1",
                    (merged["vision_model_id"],),
                ).fetchone()
                settings = json.loads(row["settings_json"] or "{}") if row else {}
                if (row is None or not row["verified_at"]
                        or settings.get("supports_vision") is not True
                        or not (settings.get("vision_capability_verification") or {}).get(
                            "passed")):
                    raise ValueError("Default screenshot model must pass the real-image capability test")
            for key, value in merged.items():
                connection.execute(
                    """INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                    updated_at=excluded.updated_at""",
                    (key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), now),
                )
        return merged

    def effective_concurrency(self, model_config_id: str) -> int:
        configured = int(self.get()["generation_concurrency"])
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT settings_json,base_url FROM model_configs WHERE id = ?",
                (model_config_id,),
            ).fetchone()
        settings = json.loads(row["settings_json"] or "{}") if row else {}
        default_limit = 10 if row and "api.senseaudio.cn" in row["base_url"] else 3
        provider_limit = settings.get("max_concurrency", default_limit)
        if not isinstance(provider_limit, int):
            provider_limit = 3
        return max(1, min(10, configured, provider_limit))


def style_prompt(database: Database, key: str) -> str:
    """Read a style override while preserving a deterministic default for tools/tests."""
    if key not in {"document_style_prompt", "diagram_style_prompt"}:
        raise ValueError("Unknown style prompt")
    if database is None:
        return DEFAULTS[key]
    return str(AppSettingsService(database).get().get(key) or DEFAULTS[key]).strip()
