import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import export_feishu as export

TASK = "a" * 32
URL = "https://example.feishu.cn/docx/abc123"
ARTIFACTS = {"Summarization": {"Summarization": {"ParagraphSummary": "讨论测试安排。", "ParagraphTitle": "例会"}}}


class FeishuExportTests(unittest.TestCase):
    def test_default_title_uses_recording_time_and_topic(self):
        self.assertEqual(export.document_title(ARTIFACTS, started_at="2026-09-09T14:25:30+08:00"),
                         "2026-09-09 14:25｜例会")

    def test_historical_export_does_not_invent_a_recording_time(self):
        self.assertEqual(export.document_title({}, prepared_at=datetime(2026, 9, 9, 18, 0)),
                         "导出 2026-09-09 18:00｜未命名会议")

    def test_custom_title_takes_priority(self):
        self.assertEqual(export.document_title(ARTIFACTS, title="  客户需求评审  "), "客户需求评审")
        with self.assertRaises(ValueError):
            export.document_title({}, title="  ")

    def test_custom_title_is_escaped_in_file_and_never_passed_as_shell_argument(self):
        title = '评审 & <测试> %PATH% "引号"'
        folder = export.prepare(TASK, ARTIFACTS, Path(self.temp.name), title=title)
        def check_cli(cwd, *args):
            self.assertNotIn(title, args)
            if args[0] == "+create":
                data = (cwd / "create.md").read_text(encoding="utf-8")
                self.assertIn("&amp; &lt;测试&gt;", data)
                return {} if "--dry-run" in args else {"url": URL}
            return self.fetched()
        with patch.object(export, "cli", side_effect=check_cli), patch.object(export, "emit"):
            export.publish(folder)
        self.assertFalse((folder / "create.md").exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = export.prepare(TASK, ARTIFACTS, Path(self.temp.name))

    def receipt(self):
        return json.loads((self.folder / "receipt.json").read_text(encoding="utf-8"))

    def fetched(self):
        return {"ok": True, "title": self.receipt()["title"],
                "content": (self.folder / "minutes.md").read_text(encoding="utf-8")}

    def test_provider_text_cannot_become_remote_media_or_markup(self):
        text = export.plain('<img href="https://example.org/x"> ![x](https://example.org/y)')
        self.assertNotIn("<img", text.replace("\\<img", ""))
        self.assertIn("\\[x\\]", text)

    def test_configured_secret_is_rejected_before_markdown_escaping(self):
        with patch.dict(export.os.environ, {"ALIBABA_CLOUD_ACCESS_KEY_SECRET": "secret_with_underscores"}):
            with self.assertRaises(ValueError):
                export.render_minutes(TASK, {"Summarization": {"Summarization": {"ParagraphSummary": "secret_with_underscores"}}})

    def test_preflight_failure_keeps_prepared_state(self):
        with patch.object(export, "cli", side_effect=RuntimeError("preflight")):
            with self.assertRaises(RuntimeError):
                export.publish(self.folder)
        self.assertEqual(self.receipt()["status"], "prepared")

    def test_uncertain_create_is_not_repeated(self):
        with patch.object(export, "cli", side_effect=[{"ok": True}, TimeoutError()]) as cli:
            with self.assertRaises(TimeoutError):
                export.publish(self.folder)
            self.assertEqual(cli.call_count, 2)
        self.assertEqual(self.receipt()["status"], "creating")
        with patch.object(export, "cli") as cli:
            with self.assertRaises(RuntimeError):
                export.publish(self.folder)
            cli.assert_not_called()

    def test_successful_create_is_fetched_and_repeated_export_only_fetches(self):
        with patch.object(export, "cli", side_effect=[{"ok": True}, {"url": URL}, self.fetched()]), patch.object(export, "emit"):
            self.assertEqual(export.publish(self.folder), URL)
        with patch.object(export, "cli", return_value=self.fetched()) as cli, patch.object(export, "emit"):
            export.publish(self.folder)
            self.assertEqual(cli.call_count, 1)
            self.assertEqual(cli.call_args.args[1], "+fetch")

    def test_readback_checks_summary_content_not_just_headings(self):
        wrong = {"content": TASK + " 摘要 待办事项 内容错误"}
        with patch.object(export, "cli", side_effect=[{}, {"url": URL}, wrong]):
            with self.assertRaises(RuntimeError):
                export.publish(self.folder)
        self.assertEqual(self.receipt()["status"], "created")

    def test_readback_checks_requested_title(self):
        wrong = self.fetched()
        wrong["title"] = "错误标题"
        with patch.object(export, "cli", side_effect=[{}, {"url": URL}, wrong]):
            with self.assertRaisesRegex(RuntimeError, "title differs"):
                export.publish(self.folder)
        self.assertEqual(self.receipt()["status"], "created")
        self.assertFalse(self.receipt()["title_verified"])


if __name__ == "__main__":
    unittest.main()
