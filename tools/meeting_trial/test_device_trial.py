import json
import os
import time
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import device_trial as trial


class DeviceControlTests(unittest.TestCase):
    def test_duration_supports_long_meetings_and_rejects_invalid_limits(self):
        created = {"MeetingJoinUrl": "wss://trial.aliyuncs.com/"}
        for seconds in (1800, 3600, 7200, 86400):
            self.assertEqual(json.loads(trial.start_message(created, seconds, device_wifi=True))["seconds"], seconds)
        for seconds in (0, -1, 86401, True, 1.5):
            with self.assertRaises(ValueError):
                trial.start_message(created, seconds, device_wifi=True)

    def test_old_firmware_rejects_long_meeting_before_cloud_task_creation(self):
        service, port = Mock(), Mock()
        with patch.object(trial, "read_events", return_value=iter([{"event": "meeting_ready", "wifi_saved": True}])):
            with self.assertRaises(ValueError):
                trial.run_device(service, port, 3600)
        service.create.assert_not_called()

    def test_24_hour_completion_exceeds_signed_32_bit_audio_count(self):
        size = 86400 * 32000
        self.assertGreater(size, 2**31)
        self.assertTrue(trial.stream_is_complete({"complete": True, "audio_bytes": size,
                                                 "captured_bytes": size, "end_reason": "duration"}, 86400))

    def test_saved_device_wifi_is_used_without_sending_environment_password(self):
        with patch.dict(os.environ, {"MEETING_WIFI_SSID": "old-network", "MEETING_WIFI_PASSWORD": "old-password"}, clear=True):
            data = json.loads(trial.start_message({"MeetingJoinUrl": "wss://trial.aliyuncs.com/"}, 60, device_wifi=True))
        self.assertTrue(data["use_device_wifi"])
        self.assertNotIn("ssid", data)
        self.assertNotIn("password", data)

    def test_ap_setup_does_not_create_cloud_task(self):
        service, port = Mock(), Mock()
        with patch.object(trial, "emit"), patch.object(trial, "read_events", return_value=iter([{"event": "meeting_ready", "wifi_setup": True}])):
            with self.assertRaises(RuntimeError):
                trial.run_device(service, port, 60)
        service.create.assert_not_called()

    def test_idle_query_recovers_a_missing_completion_event(self):
        port = Mock()
        port.read_until.return_value = b'{"event":"stream_finished","task_id":"current"}\n'
        with patch.object(trial.time, "monotonic", side_effect=[0, 11, 11, 11]):
            result = next(trial.read_events(port, 100, query_status=True))
        port.write.assert_called_once_with(b"?\n")
        self.assertEqual(result["task_id"], "current")

    def test_replayed_previous_meeting_cannot_finish_current_meeting(self):
        service, port = Mock(), Mock()
        service.create.return_value = {"TaskId": "current", "MeetingJoinUrl": "wss://trial.aliyuncs.com/"}
        replies = [iter([{"event": "meeting_ready"}]), iter([
            {"event": "stream_finished", "task_id": "previous", "complete": True, "audio_bytes": 1920000},
            {"event": "stream_finished", "task_id": "current", "complete": False, "audio_bytes": 320000},
        ])]
        with patch.dict(os.environ, {"MEETING_WIFI_SSID": "test"}), patch.object(trial, "emit"), patch.object(trial, "read_events", side_effect=replies):
            task_id, complete = trial.run_device(service, port, 60)
        self.assertEqual(task_id, "current")
        self.assertFalse(complete)
        service.stop.assert_called_once_with("current")

    def test_user_stop_accepts_short_recording_only_when_every_frame_was_sent(self):
        for reason in ("button", "serial"):
            event = {"complete": True, "audio_bytes": 320000,
                     "captured_bytes": 320000, "end_reason": reason}
            self.assertTrue(trial.stream_is_complete(event, 60))
            event["captured_bytes"] += 640
            self.assertFalse(trial.stream_is_complete(event, 60))

    def test_duration_completion_rejects_short_or_failed_stream(self):
        for event in (
            {"complete": True, "audio_bytes": 320000, "end_reason": "duration"},
            {"complete": False, "audio_bytes": 1920000},
            {"complete": True, "audio_bytes": 0, "end_reason": "button"},
            {"complete": True, "audio_bytes": 1920000, "end_reason": "error"},
        ):
            self.assertFalse(trial.stream_is_complete(event, 60))
        self.assertTrue(trial.stream_is_complete({"complete": True, "audio_bytes": 1920000}, 60))

    def test_device_does_not_report_summary_ready_for_empty_or_incomplete_meeting(self):
        for summary, complete, expected in (("", True, False), ("summary", False, False), ("summary", True, True)):
            service, port = Mock(), Mock()
            service.result.return_value = {"TaskStatus": "COMPLETED"}
            artifacts = {"Summarization": {"Summarization": {"ParagraphSummary": summary}}}
            with patch.object(trial, "emit"), patch.object(trial, "emit_artifacts", return_value=artifacts), patch.object(trial, "notify_summary") as notify:
                trial.wait_for_result(service, port, "abc", complete, 0)
            notify.assert_called_once_with(port, expected)

    def test_serial_timeout_preserves_partial_transcript_line(self):
        port = Mock()
        port.read_until.side_effect = [b'{"event":"trans', b'cript","result":"ok"}\n']
        self.assertEqual(next(trial.read_events(port, time.monotonic() + 2)),
                         {"event": "transcript", "result": "ok"})

    def test_environment_file_does_not_evaluate_shell_content(self):
        with patch.object(Path, "read_text", return_value="MEETING_WIFI_SSID=会议室\nMEETING_WIFI_PASSWORD='$(touch nope)'"), patch.dict(os.environ, {}, clear=True):
            trial.load_config(Path("unused"))
            self.assertEqual(os.environ["MEETING_WIFI_PASSWORD"], "$(touch nope)")
            self.assertEqual(os.environ["MEETING_WIFI_SSID"], "会议室")

    def test_wifi_ssid_limit_is_utf8_bytes(self):
        with patch.dict(os.environ, {"MEETING_WIFI_SSID": "中" * 11}, clear=True):
            with self.assertRaises(ValueError):
                trial.start_message({"MeetingJoinUrl": "wss://trial.aliyuncs.com/"}, 60)

    def test_device_configuration_excludes_long_lived_cloud_keys(self):
        with patch.dict(os.environ, {"MEETING_WIFI_SSID": "test", "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "private-test-value"}):
            data = trial.start_message({"MeetingJoinUrl": "wss://trial.aliyuncs.com/?ticket=test"}, 60)
        self.assertNotIn(b"private-test-value", data)
        self.assertNotIn(b"ALIBABA_CLOUD", data)
        self.assertEqual(json.loads(data)["seconds"], 60)

    def test_unexpected_provider_url_rejected(self):
        with patch.dict(os.environ, {"MEETING_WIFI_SSID": "test"}):
            with self.assertRaises(ValueError):
                trial.start_message({"MeetingJoinUrl": "wss://aliyuncs.com.example.org/"}, 60)

    def test_old_firmware_cannot_create_cloud_task(self):
        service, port = Mock(), Mock()
        with patch.object(trial, "read_events", return_value=iter([])):
            with self.assertRaises(RuntimeError):
                trial.run_device(service, port, 60)
        service.create.assert_not_called()

    def test_cloud_task_stopped_when_serial_write_fails(self):
        service, port = Mock(), Mock()
        service.create.return_value = {"TaskId": "abc", "MeetingJoinUrl": "wss://trial.aliyuncs.com/"}
        port.write.side_effect = [1, OSError("disconnected"), 1]
        with patch.dict(os.environ, {"MEETING_WIFI_SSID": "test"}), patch.object(trial, "emit"), patch.object(trial, "read_events", return_value=iter([{"event": "meeting_ready"}])):
            with self.assertRaises(OSError):
                trial.run_device(service, port, 60)
        service.stop.assert_called_once_with("abc")


if __name__ == "__main__":
    unittest.main()
