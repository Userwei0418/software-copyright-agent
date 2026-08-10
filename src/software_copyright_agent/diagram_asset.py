import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable, Tuple


OVERLAY_SCHEMA_VERSION = 1
ALLOWED_ACTIONS = {
    "node.move", "node.resize", "node.style", "node.label", "node.hide",
    "edge.route", "edge.style", "edge.label",
}


class DiagramAssetError(ValueError):
    pass


@dataclass(frozen=True)
class OverlayResult:
    diagram: dict
    operations: tuple
    conflicts: tuple
    semantic_fingerprint: str


class DiagramOverlayEngine:
    def prepare(self, diagram: dict, operations: Iterable[dict]) -> OverlayResult:
        targets = self._targets(diagram)
        normalized = []
        conflicts = []
        for index, operation in enumerate(operations):
            item = self._normalize(operation)
            target = targets.get(item["target"])
            if target is None:
                conflicts.append(self._conflict(index, item, "target_missing"))
            else:
                expected = item.get("expected_target_fingerprint")
                actual = self._fingerprint(target)
                if expected is not None and expected != actual:
                    conflicts.append(self._conflict(index, item, "target_changed"))
                if expected is None:
                    item["expected_target_fingerprint"] = actual
            normalized.append(item)
        rendered = self._apply(diagram, normalized, conflicts)
        return OverlayResult(
            rendered, tuple(normalized), tuple(conflicts),
            self.semantic_fingerprint(diagram),
        )

    def rebase(self, old_diagram: dict, new_diagram: dict,
               operations: Iterable[dict]) -> OverlayResult:
        del old_diagram  # Expected target fingerprints carry the auditable old baseline.
        return self.prepare(new_diagram, operations)

    @staticmethod
    def semantic_fingerprint(diagram: dict) -> str:
        semantic = {
            "key": diagram.get("key"),
            "nodes": sorted((node.get("key"), node.get("label"), node.get("kind"))
                            for node in diagram.get("nodes", [])),
            "edges": sorted((edge.get("key"), edge.get("source"), edge.get("target"),
                             edge.get("kind")) for edge in diagram.get("edges", [])),
        }
        return hashlib.sha256(json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _targets(diagram: dict) -> dict:
        return {item["key"]: item for category in ("nodes", "edges")
                for item in diagram.get(category, [])}

    @staticmethod
    def _normalize(operation: dict) -> dict:
        if not isinstance(operation, dict):
            raise DiagramAssetError("Overlay operation must be an object")
        action, target = operation.get("action"), operation.get("target")
        if action not in ALLOWED_ACTIONS:
            raise DiagramAssetError("Unsupported overlay action: {0}".format(action))
        if not isinstance(target, str) or not target:
            raise DiagramAssetError("Overlay target is required")
        payload = operation.get("payload", {})
        if not isinstance(payload, dict):
            raise DiagramAssetError("Overlay payload must be an object")
        item = {"action": action, "target": target, "payload": deepcopy(payload)}
        if "expected_target_fingerprint" in operation:
            item["expected_target_fingerprint"] = operation["expected_target_fingerprint"]
        return item

    def _apply(self, diagram: dict, operations: list, conflicts: list) -> dict:
        result = deepcopy(diagram)
        targets = self._targets(result)
        conflicted = {item["operation_index"] for item in conflicts}
        for index, operation in enumerate(operations):
            if index in conflicted:
                continue
            target = targets[operation["target"]]
            action, payload = operation["action"], operation["payload"]
            visual = target.setdefault("visual_override", {})
            if action.endswith(".label"):
                target["display_label"] = str(payload.get("value", ""))
            elif action == "node.hide":
                visual["hidden"] = bool(payload.get("value", True))
            else:
                visual[action.split(".", 1)[1]] = deepcopy(payload)
        result["overlay_schema_version"] = OVERLAY_SCHEMA_VERSION
        return result

    @staticmethod
    def _fingerprint(target: dict) -> str:
        semantic = {key: target.get(key) for key in
                    ("key", "label", "kind", "source", "target", "fact_id")}
        return hashlib.sha256(json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _conflict(index: int, operation: dict, reason: str) -> dict:
        return {"operation_index": index, "action": operation["action"],
                "target": operation["target"], "reason": reason}
