"""No provider calls and no local audio files: all audio fixtures stay in RAM."""
import asyncio
import hashlib
import io
import json
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import cloud_capture as cloud
import tingwu_trial as tingwu


class AudioTests(unittest.TestCase):
    def test_hour_stream_is_read_in_bounded_chunks(self):
        class VirtualPcm:
            remaining = 3600 * tingwu.BYTES_PER_SECOND
            largest_read = 0

            def read(self, count):
                self.largest_read = max(self.largest_read, count)
                n = min(count, self.remaining)
                self.remaining -= n
                return b"\0" * n

        source = VirtualPcm()
        total = sum(len(x) for x in tingwu.pcm_frames(source, 3600))
        self.assertEqual(total, 115200000)
        self.assertLessEqual(source.largest_read, 3200)

    def test_short_final_frame_is_preserved(self):
        pcm = bytes(range(256)) * 13 + b"\x00\x01"
        self.assertEqual(b"".join(tingwu.pcm_frames(io.BytesIO(pcm), 1)), pcm)
        with self.assertRaises(ValueError):
            list(tingwu.pcm_frames(io.BytesIO(b"\0" * 7), 1))

    def test_wav_input_rejected_before_sending_container_bytes(self):
        with self.assertRaises(ValueError):
            list(tingwu.pcm_frames(io.BytesIO(b"RIFF" + b"\0" * 40), 1))

    def test_cloud_wav_header_and_samples_are_exact(self):
        output = io.BytesIO()
        writer = cloud.PcmWave(output)
        pcm = bytes(range(256)) * 20
        writer.append(pcm[:3200])
        writer.append(pcm[3200:])
        writer.close()
        output.seek(0)
        with wave.open(output) as result:
            self.assertEqual((result.getnchannels(), result.getsampwidth(), result.getframerate()), (1, 2, 16000))
            self.assertEqual(result.getnframes(), len(pcm) // 2)
            self.assertEqual(result.readframes(result.getnframes()), pcm)
        self.assertEqual(writer.digest.hexdigest(), hashlib.sha256(pcm).hexdigest())

    def test_bad_frame_and_duration_limits(self):
        writer = cloud.PcmWave(io.BytesIO())
        for frame in (b"", b"1", b"0" * 6402):
            with self.assertRaises(ValueError):
                writer.append(frame)
        writer.audio_bytes = cloud.MAX_AUDIO_BYTES
        with self.assertRaises(ValueError):
            writer.append(b"00")
        writer.close()

    def test_stream_failure_still_finishes_cloud_task(self):
        class Provider:
            stopped = False

            def create(self):
                return {"TaskId": "test123", "MeetingJoinUrl": "wss://invalid.example"}

            def stop(self, task_id):
                self.stopped = task_id == "test123"

        class FailedStream:
            shutdown_called = False

            def __init__(self, **kwargs):
                pass

            def start(self):
                raise TimeoutError()

            def shutdown(self):
                FailedStream.shutdown_called = True

        provider = Provider()
        with patch("nls.NlsRealtimeMeeting", FailedStream), patch.object(tingwu, "emit"):
            with self.assertRaises(TimeoutError):
                tingwu.run_stream(provider, io.BytesIO(b"00"), 1)
        self.assertTrue(provider.stopped)
        self.assertTrue(FailedStream.shutdown_called)


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.states = {}
        self.buffers = []
        original = cloud.PcmWave

        def memory_writer(unused_path):
            buf = io.BytesIO()
            self.buffers.append(buf)
            return original(buf)

        self.patches = [patch.object(cloud, "PcmWave", memory_writer),
                        patch.object(cloud, "save_state", lambda root, sid, state: self.states.update({sid: dict(state)}))]
        for p in self.patches:
            p.start()
        app = web.Application()
        app[cloud.RUNTIME] = cloud.Runtime(Path("unused-memory-only"), "t" * 32)
        app.router.add_get("/v1/recordings", cloud.capture)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for p in reversed(self.patches):
            p.stop()

    async def start(self):
        ws = await self.client.ws_connect("/v1/recordings", headers={"Authorization": "Bearer " + "t" * 32})
        await ws.send_json({"type": "start", "format": "pcm_s16le", "sample_rate": 16000, "channels": 1})
        reply = await ws.receive_json()
        self.assertEqual(reply["type"], "ready")
        return ws, reply["session_id"]

    async def test_unauthorized_request_creates_no_recording(self):
        response = await self.client.get("/v1/recordings")
        self.assertEqual(response.status, 401)
        self.assertFalse(self.states)

    async def test_upload_finishes_only_after_explicit_stop(self):
        ws, sid = await self.start()
        pcm = b"\x00\x01" * 1600
        await ws.send_bytes(pcm)
        await ws.send_json({"type": "stop"})
        result = await ws.receive_json()
        self.assertEqual(result["type"], "captured")
        self.assertEqual(result["audio_bytes"], len(pcm))
        self.assertEqual(result["sha256_pcm"], hashlib.sha256(pcm).hexdigest())
        self.assertEqual(self.states[sid]["status"], "captured")
        self.assertEqual(self.buffers[0].getvalue()[44:], pcm)
        await ws.close()

    async def test_disconnect_marks_incomplete_and_preserves_received_audio(self):
        ws, sid = await self.start()
        await ws.send_bytes(b"\0" * 640)
        await ws.close()
        await asyncio.sleep(0)
        self.assertEqual(self.states[sid]["status"], "interrupted")
        self.assertEqual(self.states[sid]["audio_bytes"], 640)

    async def test_invalid_format_does_not_open_audio_sink(self):
        ws = await self.client.ws_connect("/v1/recordings", headers={"Authorization": "Bearer " + "t" * 32})
        await ws.send_json({"type": "start", "format": "mp3"})
        await ws.receive()
        self.assertEqual(len(self.buffers), 0)
        await ws.close()


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_write_is_not_retried_automatically(self):
        for status in ("drive_upload_inflight", "minute_create_inflight", "interrupted"):
            with patch.object(Path, "read_text", return_value=json.dumps({"status": status})), \
                 patch.object(cloud, "lark") as command:
                with self.assertRaises(ValueError):
                    await cloud.submit_feishu(Path("unused"), "a" * 32)
                command.assert_not_called()

    async def test_processing_result_is_not_reported_as_ready(self):
        state = {"status": "minute_created", "file_token": "box", "minute_token": "obctest"}
        reply = {"ok": False, "data": {"minutes": [{"minute_token": "obctest", "status": "processing", "error": "pending"}]}}
        with patch.object(Path, "read_text", return_value=json.dumps(state)), \
             patch.object(cloud, "lark", return_value=reply), patch.object(cloud, "save_state"):
            result = await cloud.submit_feishu(Path("unused"), "a" * 32)
        self.assertEqual(result["status"], "minute_created")

    async def test_empty_summary_is_not_reported_as_generated(self):
        state = {"status": "minute_created", "file_token": "box", "minute_token": "obctest"}
        reply = {"ok": True, "data": {"minutes": [{"minute_token": "obctest", "artifacts": {"summary": "", "todos": []}}]}}
        with patch.object(Path, "read_text", return_value=json.dumps(state)), \
             patch.object(cloud, "lark", return_value=reply), patch.object(cloud, "save_state"):
            result = await cloud.submit_feishu(Path("unused"), "a" * 32)
        self.assertEqual(result["status"], "minute_created")


if __name__ == "__main__":
    unittest.main()
