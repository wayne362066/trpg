import unittest

from trpg_platform.llm import CodexAppServerClient


class CodexClientTests(unittest.TestCase):
    def test_parse_json_accepts_plain_and_fenced_output(self):
        payload = {"narration": "ok", "changes": []}
        self.assertEqual(CodexAppServerClient._parse_json('{"narration":"ok","changes":[]}'), payload)
        self.assertEqual(
            CodexAppServerClient._parse_json("```json\n{\"narration\":\"ok\",\"changes\":[]}\n```"),
            payload,
        )

    def test_text_extracts_nested_message_content(self):
        value = {"content": [{"type": "output_text", "text": "第一段"}, {"text": "第二段"}]}
        self.assertEqual(CodexAppServerClient._text(value), "第一段第二段")


if __name__ == "__main__":
    unittest.main()
