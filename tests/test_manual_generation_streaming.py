import unittest

from software_copyright_agent.manual_generation import ManualGenerationService


class ManualGenerationStreamingTests(unittest.TestCase):
    def test_provider_stream_deltas_are_normalized(self) -> None:
        self.assertEqual(
            ManualGenerationService._stream_text_delta(
                "chat_completions", {"choices": [{"delta": {"content": "甲"}}]},
            ), "甲",
        )
        self.assertEqual(
            ManualGenerationService._stream_text_delta(
                "responses", {"type": "response.output_text.delta", "delta": "乙"},
            ), "乙",
        )
        self.assertEqual(
            ManualGenerationService._stream_text_delta(
                "messages", {"type": "content_block_delta",
                             "delta": {"type": "text_delta", "text": "丙"}},
            ), "丙",
        )
        self.assertEqual(
            ManualGenerationService._stream_text_delta(
                "ollama_chat", {"message": {"content": "丁"}, "done": False},
            ), "丁",
        )


if __name__ == "__main__":
    unittest.main()
