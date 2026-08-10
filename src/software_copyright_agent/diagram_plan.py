import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .manual_plan import PlanningFact


DIAGRAM_PLAN_RULES_VERSION = "diagram-plan-v1"
MAX_ARCHITECTURE_MODULES = 12


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
    metadata: dict


@dataclass(frozen=True)
class DiagramPlan:
    diagrams: tuple
    ready_diagrams: int
    needs_evidence_diagrams: int
    validation: dict


class DiagramPlanBuilder:
    def build(self, facts: Iterable[PlanningFact],
              evidence_by_path: Dict[str, tuple] = None) -> DiagramPlan:
        by_key: Dict[str, PlanningFact] = {fact.key: fact for fact in facts}
        evidence_by_path = evidence_by_path or {}
        architecture = self._architecture(by_key, evidence_by_path)
        workflow = self._workflow(by_key, evidence_by_path)
        diagrams = (architecture, workflow)
        validation = self._validate(diagrams)
        return DiagramPlan(
            diagrams,
            sum(item.status == "ready" for item in diagrams),
            sum(item.status == "needs_evidence" for item in diagrams),
            validation,
        )

    def _architecture(self, facts: Dict[str, PlanningFact],
                      evidence_by_path: Dict[str, tuple]) -> DiagramDefinition:
        nodes: List[DiagramNode] = []
        architecture_modules = facts.get("architecture.modules")
        module_fact_key = "architecture.modules" if architecture_modules else "project.modules"
        module_fact = facts.get(module_fact_key)
        module_labels = self._labels(module_fact.value) if module_fact else []
        dependency_fact = facts.get("architecture.dependencies")
        dependency_values = dependency_fact.value if (
            dependency_fact is not None and isinstance(dependency_fact.value, list)
        ) else []
        degrees = {label: 0 for label in module_labels}
        for item in dependency_values:
            if not isinstance(item, dict):
                continue
            for key in ("source_module", "target_module"):
                label = str(item.get(key))
                if label in degrees:
                    degrees[label] += 1
        selected_modules = sorted(module_labels, key=lambda label: (-degrees[label], label))[
            :MAX_ARCHITECTURE_MODULES
        ]
        if module_fact is not None:
            for index, label in enumerate(selected_modules):
                nodes.append(DiagramNode(
                    "module-{0}".format(self._slug(label, index)), label, "module",
                    module_fact.id, module_fact.evidence_ids,
                ))
        node_by_label = {node.label: node.key for node in nodes if node.kind == "module"}
        edges = []
        selected_dependencies = []
        if dependency_fact is not None:
            selected_dependencies = [item for item in dependency_values
                                     if isinstance(item, dict)
                                     and str(item.get("source_module")) in node_by_label
                                     and str(item.get("target_module")) in node_by_label]
            selected_dependencies.sort(key=lambda item: (
                -(degrees[str(item.get("source_module"))] +
                  degrees[str(item.get("target_module"))]),
                str(item.get("source_module")), str(item.get("target_module")),
                int(item.get("line") or 0),
            ))
            parent = {label: label for label in selected_modules}

            def find(label: str) -> str:
                while parent[label] != label:
                    parent[label] = parent[parent[label]]
                    label = parent[label]
                return label

            forest_dependencies = []
            for item in selected_dependencies:
                source_label = str(item.get("source_module"))
                target_label = str(item.get("target_module"))
                source_root, target_root = find(source_label), find(target_label)
                if source_root == target_root:
                    continue
                parent[source_root] = target_root
                forest_dependencies.append(item)
            for index, item in enumerate(forest_dependencies):
                source = node_by_label[str(item.get("source_module"))]
                target = node_by_label[str(item.get("target_module"))]
                edges.append(DiagramEdge(
                    "dependency-{0}".format(index + 1), source, target,
                    "内部导入", "dependency", dependency_fact.id,
                    evidence_by_path.get("dependency:" + str(item.get("source")),
                                         dependency_fact.evidence_ids),
                    {"relative_path": item.get("source"), "line": item.get("line")},
                ))
        missing = []
        if not any(node.kind == "module" for node in nodes):
            missing.append("模块节点")
        if not edges:
            missing.append("有代码证据的模块依赖关系")
        status = "ready" if any(node.kind == "module" for node in nodes) and edges \
            else "needs_evidence"
        metadata = {
            "selection": "highest_internal_degree_spanning_forest_v1",
            "source_module_count": len(module_labels),
            "source_edge_count": len(dependency_values),
            "selected_module_count": len(selected_modules),
            "selected_module_edge_count_before_limit": len(selected_dependencies),
            "selected_edge_count": len(edges),
            "module_limit": MAX_ARCHITECTURE_MODULES,
            "unconnected_context": {
                "entrypoints": self._labels(facts["runtime.entrypoints"].value)
                if "runtime.entrypoints" in facts else [],
                "storage": self._labels(facts["data.storage"].value)
                if "data.storage" in facts else [],
            },
        }
        return DiagramDefinition(
            "system_architecture", "系统总体架构图", status,
            tuple(nodes), tuple(edges), tuple(missing), metadata,
        )

    def _workflow(self, facts: Dict[str, PlanningFact],
                  evidence_by_path: Dict[str, tuple]) -> DiagramDefinition:
        fact = facts.get("workflow.transitions")
        if fact is None or not isinstance(fact.value, list):
            return DiagramDefinition(
                "core_business_flow", "核心业务流程图", "needs_evidence",
                (), (), ("显式状态转换边",), {},
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
                fact.id, evidence_by_path.get(
                    "transition:" + str(item.get("source")), fact.evidence_ids
                ),
                {"relative_path": item.get("source"), "line": item.get("line")},
            ))
        status = "ready" if nodes and edges else "needs_evidence"
        missing = () if status == "ready" else ("显式状态转换边",)
        return DiagramDefinition(
            "core_business_flow", "核心业务流程图", status,
            nodes, tuple(edges), missing,
            {"source_transition_count": len(fact.value)},
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
