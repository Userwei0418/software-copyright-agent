import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .manual_plan import PlanningFact


DIAGRAM_PLAN_RULES_VERSION = "diagram-plan-v1"


@dataclass(frozen=True)
class DiagramNode:
    key: str
    label: str
    kind: str
    fact_id: str
    evidence_ids: tuple


@dataclass(frozen=True)
class DiagramEdge:
    key: str
    source: str
    target: str
    label: str
    kind: str
    fact_id: str
    evidence_ids: tuple
    source_locator: dict


@dataclass(frozen=True)
class DiagramDefinition:
    key: str
    title: str
    status: str
    nodes: tuple
    edges: tuple
    missing_information: tuple


@dataclass(frozen=True)
class DiagramPlan:
    diagrams: tuple
    ready_diagrams: int
    needs_evidence_diagrams: int
    validation: dict


class DiagramPlanBuilder:
    def build(self, facts: Iterable[PlanningFact]) -> DiagramPlan:
        by_key: Dict[str, PlanningFact] = {fact.key: fact for fact in facts}
        architecture = self._architecture(by_key)
        workflow = self._workflow(by_key)
        diagrams = (architecture, workflow)
        validation = self._validate(diagrams)
        return DiagramPlan(
            diagrams,
            sum(item.status == "ready" for item in diagrams),
            sum(item.status == "needs_evidence" for item in diagrams),
            validation,
        )

    def _architecture(self, facts: Dict[str, PlanningFact]) -> DiagramDefinition:
        nodes: List[DiagramNode] = []
        for fact_key, kind in (
            ("runtime.entrypoints", "entrypoint"),
            ("project.modules", "module"),
            ("data.storage", "storage"),
        ):
            fact = facts.get(fact_key)
            if fact is None:
                continue
            for index, label in enumerate(self._labels(fact.value)):
                nodes.append(DiagramNode(
                    "{0}-{1}".format(kind, self._slug(label, index)), label, kind,
                    fact.id, fact.evidence_ids,
                ))
        missing = []
        if not any(node.kind == "module" for node in nodes):
            missing.append("模块节点")
        missing.append("有代码证据的模块依赖关系")
        return DiagramDefinition(
            "system_architecture", "系统总体架构图", "needs_evidence",
            tuple(nodes), (), tuple(missing),
        )

    def _workflow(self, facts: Dict[str, PlanningFact]) -> DiagramDefinition:
        fact = facts.get("workflow.transitions")
        if fact is None or not isinstance(fact.value, list):
            return DiagramDefinition(
                "core_business_flow", "核心业务流程图", "needs_evidence",
                (), (), ("显式状态转换边",),
            )
        states = sorted({str(edge[key]) for edge in fact.value
                         if isinstance(edge, dict) for key in ("from", "to") if key in edge})
        nodes = tuple(DiagramNode(
            "state-{0}".format(self._slug(state, index)), state, "state",
            fact.id, fact.evidence_ids,
        ) for index, state in enumerate(states))
        node_by_label = {node.label: node.key for node in nodes}
        edges = []
        for index, item in enumerate(fact.value):
            if not isinstance(item, dict) or item.get("from") not in node_by_label \
                    or item.get("to") not in node_by_label:
                continue
            edges.append(DiagramEdge(
                "transition-{0}".format(index + 1), node_by_label[item["from"]],
                node_by_label[item["to"]], "状态转换", "transition",
                fact.id, fact.evidence_ids,
                {"relative_path": item.get("source"), "line": item.get("line")},
            ))
        status = "ready" if nodes and edges else "needs_evidence"
        missing = () if status == "ready" else ("显式状态转换边",)
        return DiagramDefinition(
            "core_business_flow", "核心业务流程图", status,
            nodes, tuple(edges), missing,
        )

    @staticmethod
    def _labels(value: object) -> List[str]:
        if not isinstance(value, list):
            return []
        labels = []
        for item in value:
            if isinstance(item, str):
                labels.append(item)
            elif isinstance(item, dict):
                label = item.get("name") or item.get("kind")
                if label:
                    labels.append(str(label))
        return list(dict.fromkeys(labels))

    @staticmethod
    def _slug(value: str, fallback: int) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or str(fallback + 1)

    @staticmethod
    def _validate(diagrams: tuple) -> dict:
        errors = []
        for diagram in diagrams:
            node_keys = [node.key for node in diagram.nodes]
            edge_keys = [edge.key for edge in diagram.edges]
            if len(node_keys) != len(set(node_keys)):
                errors.append("{0}:duplicate_node_key".format(diagram.key))
            if len(edge_keys) != len(set(edge_keys)):
                errors.append("{0}:duplicate_edge_key".format(diagram.key))
            known = set(node_keys)
            for edge in diagram.edges:
                if edge.source not in known or edge.target not in known:
                    errors.append("{0}:dangling_edge:{1}".format(diagram.key, edge.key))
                if not edge.evidence_ids:
                    errors.append("{0}:edge_without_evidence:{1}".format(diagram.key, edge.key))
        return {"passed": not errors, "errors": errors}
