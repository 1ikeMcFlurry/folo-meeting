"""Unit tests for the host-side BLE screenshot stream protocol."""

from __future__ import annotations

import asyncio
import os
import struct
import sys
import tempfile
import types
import unittest
import warnings
import zlib
from pathlib import Path
from unittest import mock


# The protocol/client contract is tested on builders without BLE or a display.  Supply
# only the external modules needed to import the client when the host does not have
# those optional desktop dependencies installed.
try:
    import bleak  # noqa: F401
except ImportError:
    bleak_module = types.ModuleType("bleak")
    bleak_module.BleakScanner = object
    bleak_module.BleakClient = object
    sys.modules["bleak"] = bleak_module

try:
    import tkinter  # noqa: F401
except ImportError:
    tkinter_module = types.ModuleType("tkinter")
    tkinter_module.Tk = type("Tk", (), {})
    tkinter_module.Toplevel = type("Toplevel", (), {})
    tkinter_module.ttk = types.SimpleNamespace()
    tkinter_module.filedialog = types.SimpleNamespace()
    tkinter_module.messagebox = types.SimpleNamespace()
    tkinter_module.simpledialog = types.SimpleNamespace()
    sys.modules["tkinter"] = tkinter_module

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_capture_protocol import (
    Ack,
    CaptureAssembler,
    CaptureProtocolError,
    Complete,
    rgb565le_to_image,
)
import ble_card_client
from ble_card_client import (
    CAPTURE_UUID,
    NOTIFY_UUID,
    SC_OP_CANCEL,
    TYPE_CAPTURE,
    App,
    BleWorker,
    _save_capture_image,
    load_avatar_names,
)


WIDTH = 240
HEIGHT = 320
TOTAL_LEN = WIDTH * HEIGHT * 2
CAPTURE_ID = 0x1234
MAX_DEVICE_DATA_PAYLOAD = 226


def meta(capture_id: int = CAPTURE_ID, seq: int = 0, *, width: int = WIDTH,
         height: int = HEIGHT, pixel_format: int = 1, total_len: int = TOTAL_LEN) -> bytes:
    return struct.pack("<BHHHHBI", 0x01, capture_id, seq, width, height, pixel_format, total_len)


def data(capture_id: int, seq: int, x: int, y: int, width: int, height: int,
         region_offset: int, pixels: bytes) -> bytes:
    return struct.pack("<BHHHHHHI", 0x02, capture_id, seq, x, y, width, height, region_offset) + pixels


def end(capture_id: int, seq: int, packet_count: int, total_len: int, crc32: int) -> bytes:
    return struct.pack("<BHHHII", 0x03, capture_id, seq, packet_count, total_len, crc32)


class TestRgb565(unittest.TestCase):
    def test_primary_colors(self):
        raw = struct.pack("<4H", 0xF800, 0x07E0, 0x001F, 0xFFFF)
        im = rgb565le_to_image(raw, 2, 2)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            actual = list(im.getdata())
        self.assertEqual(actual, [
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])

    def test_rejects_wrong_sized_input(self):
        with self.assertRaises(CaptureProtocolError):
            rgb565le_to_image(b"\x00\x00", 2, 2)


class TestNamedAvatarHostContract(unittest.TestCase):
    def test_host_has_no_avatar_file_upload_path(self):
        source = Path(ble_card_client.__file__).read_text(encoding="utf-8")
        self.assertNotIn("convert_to_png_avatar", source)
        self.assertNotIn("self.img_mode", source)
        self.assertNotIn('value="avatar"', source)
        self.assertIn('"img_mode":"fullscreen"', source)

    def test_host_loads_dynamic_firmware_avatar_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "avatar_catalog.json"
            catalog.write_text('{"version":1,"avatars":["default","staff_a","staff_b"]}',
                               encoding="utf-8")
            self.assertEqual(("default", "staff_a", "staff_b"), load_avatar_names(catalog))


class TestCaptureAssembler(unittest.TestCase):
    def setUp(self) -> None:
        self.assembler = CaptureAssembler()

    def _accept_meta(self) -> None:
        self.assertEqual(self.assembler.feed(meta()), Ack(CAPTURE_ID, 0))

    def _feed_full_canvas(self, pixels: bytes, payload_size: int = MAX_DEVICE_DATA_PAYLOAD) -> tuple[int, int]:
        """Feed one full device region in ATT-sized packets and return END identity."""
        self.assertEqual(len(pixels), TOTAL_LEN)
        self.assertEqual(payload_size & 1, 0)
        self.assertLessEqual(payload_size + 17, 244)
        seq = 1
        packet_count = 0
        for region_offset in range(0, TOTAL_LEN, payload_size):
            payload = pixels[region_offset:region_offset + payload_size]
            self.assertEqual(
                self.assembler.feed(data(
                    CAPTURE_ID, seq, 0, 0, WIDTH, HEIGHT, region_offset, payload)),
                Ack(CAPTURE_ID, seq),
            )
            seq = (seq + 1) & 0xFFFF
            packet_count += 1
        return seq, packet_count

    def test_accepts_expected_meta(self):
        self._accept_meta()

    def test_rejects_meta_with_unsupported_shape_format_or_length(self):
        for packet in (
            meta(width=239),
            meta(height=319),
            meta(pixel_format=2),
            meta(total_len=TOTAL_LEN - 2),
        ):
            with self.subTest(packet=packet):
                with self.assertRaises(CaptureProtocolError):
                    CaptureAssembler().feed(packet)

    def test_places_data_using_region_coordinates_and_offset(self):
        self._accept_meta()
        pixels = struct.pack("<4H", 0xF800, 0x07E0, 0x001F, 0xFFFF)
        first = data(CAPTURE_ID, 1, 2, 1, 2, 2, 0, pixels[:4])
        second = data(CAPTURE_ID, 2, 2, 1, 2, 2, 4, pixels[4:])

        self.assertEqual(self.assembler.feed(first), Ack(CAPTURE_ID, 1))
        self.assertEqual(self.assembler.feed(second), Ack(CAPTURE_ID, 2))

        canvas = self.assembler.canvas
        self.assertEqual(canvas[((1 * WIDTH + 2) * 2):((1 * WIDTH + 4) * 2)], pixels[:4])
        self.assertEqual(canvas[((2 * WIDTH + 2) * 2):((2 * WIDTH + 4) * 2)], pixels[4:])

    def test_acknowledges_immediately_previous_data_without_rewriting_canvas(self):
        self._accept_meta()
        original = data(CAPTURE_ID, 1, 0, 0, 1, 1, 0, struct.pack("<H", 0xF800))
        duplicate = data(CAPTURE_ID, 1, 0, 0, 1, 1, 0, struct.pack("<H", 0x001F))

        self.assertEqual(self.assembler.feed(original), Ack(CAPTURE_ID, 1))
        self.assertEqual(self.assembler.feed(duplicate), Ack(CAPTURE_ID, 1))
        self.assertEqual(self.assembler.canvas[:2], struct.pack("<H", 0xF800))

    def test_rejects_wrong_capture_id(self):
        self._accept_meta()
        with self.assertRaises(CaptureProtocolError):
            self.assembler.feed(data(CAPTURE_ID + 1, 1, 0, 0, 1, 1, 0, b"\x00\x00"))

    def test_rejects_sequence_gap(self):
        self._accept_meta()
        with self.assertRaises(CaptureProtocolError):
            self.assembler.feed(data(CAPTURE_ID, 2, 0, 0, 1, 1, 0, b"\x00\x00"))

    def test_rejects_out_of_bounds_region(self):
        self._accept_meta()
        with self.assertRaises(CaptureProtocolError):
            self.assembler.feed(data(CAPTURE_ID, 1, WIDTH - 1, HEIGHT - 1, 2, 1, 0, b"\x00\x00\x00\x00"))

    def test_rejects_incomplete_coverage_at_end(self):
        self._accept_meta()
        pixels = b"\x00\x00"
        self.assembler.feed(data(CAPTURE_ID, 1, 0, 0, 1, 1, 0, pixels))
        with self.assertRaises(CaptureProtocolError):
            self.assembler.feed(end(CAPTURE_ID, 2, 1, TOTAL_LEN, zlib.crc32(pixels)))

    def test_rejects_crc_mismatch_at_end(self):
        self._accept_meta()
        pixels = bytes((index % 251 for index in range(TOTAL_LEN)))
        end_seq, packet_count = self._feed_full_canvas(pixels)
        with self.assertRaises(CaptureProtocolError):
            self.assembler.feed(end(CAPTURE_ID, end_seq, packet_count & 0xFFFF, TOTAL_LEN, 0))

    def test_completes_fragmented_full_canvas_at_device_max_data_payload(self):
        self._accept_meta()
        pixels = bytes((index % 251 for index in range(TOTAL_LEN)))
        end_seq, packet_count = self._feed_full_canvas(pixels)

        result = self.assembler.feed(end(
            CAPTURE_ID, end_seq, packet_count & 0xFFFF, TOTAL_LEN, zlib.crc32(pixels)))

        self.assertIsInstance(result, Complete)
        self.assertEqual(result.capture_id, CAPTURE_ID)
        self.assertEqual(result.seq, end_seq)
        self.assertEqual(result.data, pixels)

    def test_completes_across_sequence_and_packet_count_wrap(self):
        self._accept_meta()
        pixels = bytes((index % 251 for index in range(TOTAL_LEN)))
        end_seq, packet_count = self._feed_full_canvas(pixels, payload_size=2)

        result = self.assembler.feed(end(
            CAPTURE_ID, end_seq, packet_count & 0xFFFF, TOTAL_LEN, zlib.crc32(pixels)))

        self.assertIsInstance(result, Complete)
        self.assertEqual(result.seq, 11265)
        self.assertEqual(result.packet_count, packet_count & 0xFFFF)
        self.assertEqual(result.data, pixels)

    def test_cancel_discards_partial_capture_and_allows_another_meta(self):
        self._accept_meta()
        self.assembler.cancel()
        self.assertEqual(self.assembler.feed(meta(capture_id=0x5678)), Ack(0x5678, 0))


class ImmediateFuture:
    """Completed future returned by the headless worker's synchronous submit fake."""

    def __init__(self, exception=None):
        self._exception = exception

    def exception(self):
        return self._exception

    def add_done_callback(self, callback):
        callback(self)


class DeferredFuture:
    def __init__(self):
        self._exception = None
        self._callbacks = []

    def exception(self):
        return self._exception

    def add_done_callback(self, callback):
        self._callbacks.append(callback)

    def complete(self, exception=None):
        self._exception = exception
        for callback in self._callbacks:
            callback(self)


class DeferredSubmit:
    def __init__(self):
        self.pending = []

    def __call__(self, coro):
        future = DeferredFuture()
        self.pending.append((coro, future))
        return future

    def complete_next(self):
        coro, future = self.pending.pop(0)
        try:
            asyncio.run(coro)
        except Exception as exc:
            future.complete(exc)
        else:
            future.complete()


def make_headless_worker(events, writes):
    worker = BleWorker.__new__(BleWorker)
    worker.on_event = lambda kind, payload: events.append((kind, payload))
    worker.client = types.SimpleNamespace(mtu_size=247)
    worker._devices = []
    worker._disconnect_reported = False
    worker._init_capture_state()

    async def fake_write_frame(msg_type, payload, response=True):
        writes.append((msg_type, bytes(payload), response))

    def submit(coro):
        try:
            asyncio.run(coro)
        except Exception as exc:  # mirror run_coroutine_threadsafe's result surface
            return ImmediateFuture(exc)
        return ImmediateFuture()

    worker._write_frame = fake_write_frame
    worker.submit = submit
    return worker


class TestBleCaptureClient(unittest.TestCase):
    def test_connect_subscribes_status_then_capture_and_rolls_back_capture_failure(self):
        events = []

        class FakeClient:
            instance = None

            def __init__(self, address, disconnected_callback=None):
                self.address = address
                self.disconnected_callback = disconnected_callback
                self.mtu_size = 247
                self.notify_calls = []
                self.disconnect_calls = 0
                FakeClient.instance = self

            async def connect(self):
                return None

            async def start_notify(self, uuid, callback):
                self.notify_calls.append((uuid, callback))
                if uuid == CAPTURE_UUID:
                    raise RuntimeError("capture CCCD failed")

            async def disconnect(self):
                self.disconnect_calls += 1

        worker = BleWorker.__new__(BleWorker)
        worker.on_event = lambda kind, payload: events.append((kind, payload))
        worker.client = None
        worker._devices = []
        worker._disconnect_reported = True
        worker._init_capture_state()

        with mock.patch.object(ble_card_client, "BleakClient", FakeClient):
            with self.assertRaisesRegex(RuntimeError, "capture CCCD failed"):
                asyncio.run(worker._connect("AA:BB"))

        client = FakeClient.instance
        self.assertEqual([call[0] for call in client.notify_calls], [NOTIFY_UUID, CAPTURE_UUID])
        self.assertEqual(client.disconnect_calls, 1)
        self.assertIsNone(worker.client)
        self.assertNotIn("connected", [kind for kind, _ in events])

    def test_complete_notification_posts_capture_complete_without_gui_work(self):
        events = []
        writes = []
        worker = make_headless_worker(events, writes)
        worker._capture_active = True
        worker._capture_started_at = 10.0

        pixels = bytes((index % 251 for index in range(TOTAL_LEN)))
        worker._on_capture_notify(None, meta())
        seq = 1
        packet_count = 0
        for region_offset in range(0, TOTAL_LEN, MAX_DEVICE_DATA_PAYLOAD):
            payload = pixels[region_offset:region_offset + MAX_DEVICE_DATA_PAYLOAD]
            worker._on_capture_notify(
                None,
                data(CAPTURE_ID, seq, 0, 0, WIDTH, HEIGHT, region_offset, payload),
            )
            seq = (seq + 1) & 0xFFFF
            packet_count += 1
        worker._on_capture_notify(
            None,
            end(CAPTURE_ID, seq, packet_count, TOTAL_LEN, zlib.crc32(pixels)),
        )

        complete_events = [payload for kind, payload in events if kind == "capture_complete"]
        self.assertEqual(len(complete_events), 1)
        self.assertEqual(complete_events[0].data, pixels)
        self.assertEqual(writes[-1][0], TYPE_CAPTURE)
        self.assertEqual(writes[-1][1], bytes([0x02, 0x34, 0x12, seq & 0xFF, seq >> 8]))

    def test_cancel_sends_capture_identity_then_clears_active_assembly(self):
        events = []
        writes = []
        worker = make_headless_worker(events, writes)
        worker._capture_active = True
        worker._capture_started_at = 10.0
        worker._on_capture_notify(None, meta())

        worker.cancel_capture()

        self.assertEqual(
            writes[-1],
            (TYPE_CAPTURE, bytes([SC_OP_CANCEL, 0x34, 0x12]), True),
        )
        self.assertIsNone(worker._capture_assembler.capture_id)
        self.assertFalse(worker._capture_active)
        self.assertIn("capture_cancelled", [kind for kind, _ in events])

    def test_disconnect_clears_active_assembly(self):
        events = []
        writes = []
        worker = make_headless_worker(events, writes)
        worker._capture_active = True
        worker._capture_started_at = 10.0
        worker._on_capture_notify(None, meta())
        connected_client = worker.client

        worker._mark_disconnected(connected_client)

        self.assertIsNone(worker._capture_assembler.capture_id)
        self.assertFalse(worker._capture_active)
        self.assertIsNone(worker.client)
        self.assertEqual([kind for kind, _ in events].count("disconnected"), 1)

    def test_protocol_error_waits_for_cancel_write_before_terminal_gui_event(self):
        events = []
        writes = []
        worker = make_headless_worker(events, writes)
        app = make_headless_app()
        app._begin_capture_ui()

        def sink(kind, payload):
            events.append((kind, payload))
            if kind in ("capture_error", "capture_cancelled"):
                app._handle(kind, payload)

        worker.on_event = sink
        worker._capture_active = True
        worker._capture_started_at = 10.0
        worker._capture_assembler.feed(meta())
        deferred = DeferredSubmit()
        worker.submit = deferred

        worker._on_capture_notify(
            None,
            data(CAPTURE_ID, 2, 0, 0, 1, 1, 0, b"\x00\x00"),
        )
        early_terminal_events = [
            kind for kind, _ in events if kind in ("capture_error", "capture_cancelled")
        ]
        early_active = worker._capture_active
        assembler_was_cleared = worker._capture_assembler.capture_id is None
        early_bulk_states = [button.cget("state") for button in app._bulk_buttons]
        early_start_state = app.btn_capture_start.cget("state")

        deferred.complete_next()

        self.assertEqual(early_terminal_events, [])
        self.assertTrue(early_active)
        self.assertTrue(assembler_was_cleared)
        self.assertEqual(early_bulk_states, ["disabled"] * 8)
        self.assertEqual(early_start_state, "disabled")
        self.assertEqual(
            writes[-1],
            (TYPE_CAPTURE, bytes([SC_OP_CANCEL, 0x34, 0x12]), True),
        )
        self.assertFalse(worker._capture_active)
        self.assertEqual(
            [kind for kind, _ in events if kind == "capture_error"],
            ["capture_error"],
        )
        self.assertFalse(app._capture_active)
        self.assertEqual([button.cget("state") for button in app._bulk_buttons],
                         ["normal", "normal", "normal", "normal", "normal",
                          "normal", "disabled", "normal"])
        self.assertEqual(app.btn_capture_start.cget("state"), "normal")

    def test_protocol_error_cancel_write_failure_emits_error_then_restores(self):
        events = []
        writes = []
        worker = make_headless_worker(events, writes)
        worker._capture_active = True
        worker._capture_started_at = 10.0
        worker._capture_assembler.feed(meta())
        deferred = DeferredSubmit()
        worker.submit = deferred

        async def failing_write(msg_type, payload, response=True):
            writes.append((msg_type, bytes(payload), response))
            raise OSError("link lost")

        worker._write_frame = failing_write
        worker._on_capture_notify(
            None,
            data(CAPTURE_ID, 2, 0, 0, 1, 1, 0, b"\x00\x00"),
        )
        self.assertTrue(worker._capture_active)
        self.assertNotIn("capture_error", [kind for kind, _ in events])

        deferred.complete_next()

        self.assertFalse(worker._capture_active)
        errors = [payload for kind, payload in events if kind == "capture_error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("CANCEL 发送失败: link lost", errors[0])


class FakeWidget:
    def __init__(self, state="normal"):
        self.options = {"state": state, "value": 0}

    def cget(self, name):
        return self.options[name]

    def config(self, **kwargs):
        self.options.update(kwargs)

    def __getitem__(self, name):
        return self.options[name]

    def __setitem__(self, name, value):
        self.options[name] = value


class FakeVar:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class FakeCaptureWorker:
    def __init__(self):
        self.finishes = []
        self.cancels = []

    def finish_capture(self, capture_id):
        self.finishes.append(capture_id)

    def cancel_capture(self, capture_id=None):
        self.cancels.append(capture_id)


def make_headless_app():
    app = App.__new__(App)
    app.connected = True
    app._capture_active = False
    app._capture_started_at = None
    app._capture_bulk_states = None
    app.btn_capture_start = FakeWidget()
    app.btn_capture_cancel = FakeWidget("disabled")
    app.capture_prog = FakeWidget()
    app.capture_status_var = FakeVar()
    app._bulk_buttons = [
        FakeWidget("normal"), FakeWidget("normal"), FakeWidget("normal"),
        FakeWidget("normal"), FakeWidget("normal"), FakeWidget("normal"),
        FakeWidget("disabled"), FakeWidget("normal"),
    ]
    app.worker = FakeCaptureWorker()
    app._log = lambda _message: None
    return app


class TestCaptureGuiState(unittest.TestCase):
    def test_capture_disables_every_non_capture_ble_command(self):
        app = make_headless_app()

        source = Path(ble_card_client.__file__).read_text(encoding="utf-8")
        bulk = source.split("self._bulk_buttons =", 1)[1].split("]", 1)[0]
        for name in (
            "btn_scan", "btn_send_json", "btn_sync_time", "btn_read_game", "btn_clear_game",
            "btn_send_avatar", "btn_send_img", "btn_send_score",
        ):
            self.assertIn(f"self.{name}", bulk)

        self.assertTrue(app._begin_capture_ui())

        self.assertEqual(
            [button.cget("state") for button in app._bulk_buttons],
            ["disabled"] * 8,
        )

    def test_success_keeps_bulk_actions_disabled_until_finish_write_completes(self):
        app = make_headless_app()
        self.assertTrue(app._begin_capture_ui())
        complete = types.SimpleNamespace(
            capture_id=CAPTURE_ID,
            data=b"pixels",
            width=WIDTH,
            height=HEIGHT,
        )

        with mock.patch.object(ble_card_client, "rgb565le_to_image", return_value=mock.Mock()), \
                mock.patch.object(ble_card_client, "_save_capture_image", return_value="capture.jpg"):
            app._handle_capture_complete(complete)

        self.assertEqual(app.worker.finishes, [CAPTURE_ID])
        self.assertTrue(app._capture_active)
        self.assertEqual([button.cget("state") for button in app._bulk_buttons],
                         ["disabled"] * 8)

        app._handle("capture_finished", CAPTURE_ID)
        self.assertFalse(app._capture_active)
        self.assertEqual([button.cget("state") for button in app._bulk_buttons],
                         ["normal", "normal", "normal", "normal", "normal",
                          "normal", "disabled", "normal"])
        self.assertEqual(app.btn_capture_start.cget("state"), "normal")
        self.assertEqual(app.btn_capture_cancel.cget("state"), "disabled")

    def test_cancelled_save_uses_fake_worker_and_restores_on_cancel_completion(self):
        app = make_headless_app()
        app._begin_capture_ui()
        complete = types.SimpleNamespace(
            capture_id=CAPTURE_ID,
            data=b"pixels",
            width=WIDTH,
            height=HEIGHT,
        )

        with mock.patch.object(ble_card_client, "rgb565le_to_image", return_value=mock.Mock()), \
                mock.patch.object(ble_card_client, "_save_capture_image", return_value=None):
            app._handle_capture_complete(complete)

        self.assertEqual(app.worker.cancels, [CAPTURE_ID])
        self.assertTrue(app._capture_active)
        app._handle("capture_cancelled", CAPTURE_ID)
        self.assertFalse(app._capture_active)


class TestSaveCaptureImage(unittest.TestCase):
    def test_cancelled_dialog_does_not_save(self):
        image = mock.Mock()
        dialog = types.SimpleNamespace(asksaveasfilename=lambda **_kwargs: "")

        self.assertIsNone(_save_capture_image(image, dialog=dialog))
        image.save.assert_not_called()

    def test_saves_jpeg_with_committed_dialog_options(self):
        calls = []
        image = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "capture.jpg")

            class FakeDialog:
                @staticmethod
                def asksaveasfilename(**kwargs):
                    calls.append(kwargs)
                    return path

            self.assertEqual(_save_capture_image(image, dialog=FakeDialog), path)
            saved_path = image.save.call_args.args[0]
            self.assertEqual(os.path.dirname(saved_path), tmp)
            self.assertNotEqual(saved_path, path)
            self.assertEqual(image.save.call_args.args[1:], ("JPEG",))
            self.assertEqual(image.save.call_args.kwargs, {"quality": 90})
            self.assertEqual(calls[0]["defaultextension"], ".jpg")
            self.assertRegex(calls[0]["initialfile"], r"^trae-card-\d{8}-\d{6}\.jpg$")
            self.assertEqual(calls[0]["filetypes"], [("JPEG 图片", "*.jpg *.jpeg")])

    def test_failed_save_preserves_existing_destination_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "capture.jpg")
            Path(path).write_bytes(b"original")

            class PartialImage:
                @staticmethod
                def save(target, image_format, quality):
                    self.assertEqual((image_format, quality), ("JPEG", 90))
                    Path(target).write_bytes(b"partial")
                    raise OSError("disk full")

            dialog = types.SimpleNamespace(asksaveasfilename=lambda **_kwargs: path)
            with self.assertRaisesRegex(OSError, "disk full"):
                _save_capture_image(PartialImage(), dialog=dialog)
            self.assertEqual(Path(path).read_bytes(), b"original")
            self.assertEqual(os.listdir(tmp), ["capture.jpg"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
