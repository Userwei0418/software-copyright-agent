import unittest

from software_copyright_agent.diagram_asset import DiagramAssetError, DiagramOverlayEngine


def sample_diagram():
    return {
        "key": "system_architecture",
        "nodes": [
            {"key": "module-service", "label": "demo.service", "kind": "module",
             "fact_id": "fact-service"},
            {"key": "module-storage", "label": "demo.storage", "kind": "module",
             "fact_id": "fact-storage"},
        ],
        "edges": [
            {"key": "dependency-1", "source": "module-service",
             "target": "module-storage", "kind": "dependency", "fact_id": "fact-edge"},
        ],
    }


class DiagramOverlayEngineTests(unittest.TestCase):
    def test_manual_operations_are_normalized_and_applied_without_mutating_semantics(self) -> None:
        original = sample_diagram()
        result = DiagramOverlayEngine().prepare(original, [
            {"action": "node.move", "target": "module-service",
             "payload": {"x": 120, "y": 80}},
            {"action": "node.label", "target": "module-service",
             "payload": {"value": "业务服务"}},
            {"action": "edge.route", "target": "dependency-1",
             "payload": {"points": [[100, 120], [240, 120]]}},
        ])
        service = result.diagram["nodes"][0]
        self.assertEqual(service["label"], "demo.service")
        self.assertEqual(service["display_label"], "业务服务")
        self.assertEqual(service["visual_override"]["move"], {"x": 120, "y": 80})
        self.assertEqual(result.diagram["edges"][0]["visual_override"]["route"]["points"][0],
                         [100, 120])
        self.assertFalse(result.conflicts)
        self.assertNotIn("visual_override", original["nodes"][0])
        self.assertTrue(all(item["expected_target_fingerprint"] for item in result.operations))

    def test_rebase_reports_changed_and_missing_targets_without_applying_them(self) -> None:
        engine = DiagramOverlayEngine()
        prepared = engine.prepare(sample_diagram(), [
            {"action": "node.move", "target": "module-service", "payload": {"x": 10}},
            {"action": "node.style", "target": "module-storage",
             "payload": {"fillColor": "#ffffff"}},
        ])
        changed = sample_diagram()
        changed["nodes"][0]["label"] = "demo.application_service"
        changed["nodes"] = changed["nodes"][:1]
        rebased = engine.rebase(sample_diagram(), changed, prepared.operations)
        self.assertEqual({item["reason"] for item in rebased.conflicts},
                         {"target_changed", "target_missing"})
        self.assertNotIn("visual_override", rebased.diagram["nodes"][0])

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(DiagramAssetError):
            DiagramOverlayEngine().prepare(sample_diagram(), [
                {"action": "shell.execute", "target": "module-service", "payload": {}}
            ])


if __name__ == "__main__":
    unittest.main()
