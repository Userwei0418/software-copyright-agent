from dataclasses import dataclass
from typing import Dict, Iterable, List


MANUAL_PLAN_RULES_VERSION = "manual-plan-v1"
FACT_MISSING_LABELS = {
    "project.name": "软件名称",
    "project.version": "软件版本",
    "project.modules": "模块划分",
    "tech.languages": "开发语言",
    "tech.frameworks": "框架与主要依赖",
    "project.purpose": "软件用途",
    "project.target_users": "目标用户",
    "project.background": "建设背景",
    "project.scope": "适用范围",
    "runtime.operating_systems": "支持的操作系统",
    "runtime.minimum_hardware": "最低硬件配置",
    "deployment.method": "部署方式",
    "deployment.dependencies": "部署外部依赖",
    "architecture.boundary": "系统边界",
    "architecture.external_actors": "外部参与者",
    "project.module_details": "模块职责、输入输出和异常处理",
    "data.entities": "核心数据实体",
    "data.storage": "存储结构与字段",
    "data.lifecycle": "状态转换和数据生命周期",
    "interfaces.catalog": "主要接口清单",
    "interfaces.contracts": "请求响应约束",
    "interfaces.errors": "错误处理和重试策略",
    "security.controls": "权限和敏感数据策略",
    "reliability.recovery": "故障恢复机制",
    "operations.audit_backup": "日志审计和备份策略",
    "ui.screenshots": "真实界面截图",
    "ui.operations": "典型操作步骤",
    "ui.error_recovery": "界面校验、异常和恢复方式",
    "testing.strategy": "测试策略和验收结果",
    "deployment.install_upgrade": "安装升级步骤",
    "operations.maintenance": "监控与维护办法",
}


@dataclass(frozen=True)
class PlanningFact:
    id: str
    key: str
    value: object
    confidence: float
    evidence_ids: tuple


@dataclass(frozen=True)
class ManualSection:
    key: str
    title: str
    purpose: str
    status: str
    generation_mode: str
    fact_ids: tuple
    evidence_ids: tuple
    missing_information: tuple
    subsections: tuple
    diagram_keys: tuple = ()


@dataclass(frozen=True)
class ManualPlan:
    sections: tuple
    ready_sections: int
    needs_evidence_sections: int
    missing_information: tuple
    diagram_requirements: tuple


class ManualPlanBuilder:
    def build(self, facts: Iterable[PlanningFact]) -> ManualPlan:
        by_key: Dict[str, PlanningFact] = {fact.key: fact for fact in facts}
        modules = self._string_list(by_key.get("project.modules"))
        sections = (
            self._section(
                "overview", "软件概述", "说明软件身份、建设背景、适用范围和读者对象。",
                by_key, ("project.name", "project.version", "project.purpose",
                         "project.target_users", "project.background", "project.scope"),
                (),
                ("软件简介", "建设目标", "适用范围", "术语与读者对象"),
            ),
            self._section(
                "environment", "运行环境", "记录开发、部署和运行所需软硬件环境。",
                by_key, ("tech.languages", "tech.frameworks", "runtime.operating_systems",
                         "runtime.minimum_hardware", "deployment.method",
                         "deployment.dependencies"),
                (),
                ("开发环境", "运行环境", "部署依赖"),
            ),
            self._section(
                "architecture", "总体设计", "解释系统边界、分层结构、模块职责和协作关系。",
                by_key, ("project.modules", "tech.frameworks", "tech.languages",
                         "architecture.boundary", "architecture.external_actors"),
                (),
                ("设计原则", "总体架构", "模块划分", "模块协作"),
                ("system_architecture",),
            ),
            self._section(
                "functional_design", "功能设计", "逐模块描述功能、输入、处理、输出和异常。",
                by_key, ("project.modules", "project.module_details"),
                (),
                tuple("{0}模块".format(module) for module in modules) or ("核心功能模块",),
                ("core_business_flow",),
            ),
            self._section(
                "data_design", "数据设计", "说明核心实体、持久化结构、状态和数据生命周期。",
                by_key, ("data.entities", "data.storage", "data.lifecycle"),
                (),
                ("数据模型", "存储设计", "数据生命周期与一致性"),
            ),
            self._section(
                "interface_design", "接口设计", "描述内部接口、外部接口及其校验和错误语义。",
                by_key, ("interfaces.catalog", "interfaces.contracts", "interfaces.errors"),
                (),
                ("内部接口", "外部接口", "参数校验与错误处理"),
            ),
            self._section(
                "security_reliability", "安全性与可靠性", "说明权限、安全边界、容错和恢复机制。",
                by_key, ("security.controls", "reliability.recovery",
                         "operations.audit_backup"),
                (),
                ("安全设计", "异常处理", "恢复与审计"),
            ),
            self._section(
                "user_guide", "用户界面与操作说明", "按真实界面说明典型操作和结果状态。",
                by_key, ("ui.screenshots", "ui.operations", "ui.error_recovery"),
                (),
                ("界面总览", "典型操作", "结果与异常处理"),
            ),
            self._section(
                "testing_maintenance", "测试、部署与维护", "记录验证方法、验收标准和维护方式。",
                by_key, ("tech.languages", "tech.frameworks", "testing.strategy",
                         "deployment.install_upgrade", "operations.maintenance"),
                (),
                ("测试与验收", "安装部署", "升级维护"),
            ),
        )
        missing = tuple(dict.fromkeys(
            item for section in sections for item in section.missing_information
        ))
        diagrams = (
            {"key": "system_architecture", "title": "系统总体架构图", "section_key": "architecture"},
            {"key": "core_business_flow", "title": "核心业务流程图", "section_key": "functional_design"},
        )
        return ManualPlan(
            sections,
            sum(section.status == "ready" for section in sections),
            sum(section.status == "needs_evidence" for section in sections),
            missing,
            diagrams,
        )

    @staticmethod
    def _string_list(fact: PlanningFact) -> List[str]:
        if fact is None or not isinstance(fact.value, list):
            return []
        return [str(item) for item in fact.value if str(item).strip()]

    @staticmethod
    def _section(key: str, title: str, purpose: str,
                 facts: Dict[str, PlanningFact], fact_keys: tuple,
                 missing: tuple, subsections: tuple, diagram_keys: tuple = ()) -> ManualSection:
        selected = tuple(facts[item] for item in fact_keys if item in facts)
        missing_fact_keys = tuple(
            FACT_MISSING_LABELS.get(item, item) for item in fact_keys if item not in facts
        )
        unresolved = missing_fact_keys + missing
        evidence_ids = tuple(dict.fromkeys(
            evidence_id for fact in selected for evidence_id in fact.evidence_ids
        ))
        return ManualSection(
            key, title, purpose,
            "ready" if not unresolved else "needs_evidence",
            "model_assisted",
            tuple(fact.id for fact in selected), evidence_ids, unresolved,
            subsections, diagram_keys,
        )
