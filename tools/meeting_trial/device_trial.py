"""Control an ESP32 direct-to-Tingwu trial; USB never carries audio."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import serial
from tingwu_trial import Tingwu, emit, emit_artifacts

MAX_RECORDING_SECONDS = 86400

CONFIG_KEYS = {
    "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "ALIBABA_CLOUD_SECURITY_TOKEN", "TINGWU_APP_KEY",
    "MEETING_WIFI_SSID", "MEETING_WIFI_PASSWORD",
}


def load_config(path: Path):
    # Explicit file only: no shell evaluation, interpolation, or credential search.
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in CONFIG_KEYS:
            raise ValueError(f"Invalid config entry at line {number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            os.environ[key] = value


def start_message(created: dict, seconds: int, device_wifi=False) -> bytes:
    ssid = os.environ.get("MEETING_WIFI_SSID", "")
    password = os.environ.get("MEETING_WIFI_PASSWORD", "")
    if not device_wifi and (not 1 <= len(ssid.encode("utf-8")) <= 32 or len(password.encode("utf-8")) > 63):
        raise ValueError("Invalid or missing Wi-Fi configuration")
    url = created["MeetingJoinUrl"]
    parsed = urlsplit(url)
    if parsed.scheme != "wss" or not (parsed.hostname or "").endswith(".aliyuncs.com"):
        raise ValueError("Unexpected provider streaming URL")
    if parsed.username or parsed.port not in (None, 443) or type(seconds) is not int or not 1 <= seconds <= MAX_RECORDING_SECONDS:
        raise ValueError("Invalid trial parameters")
    data = {"cmd": "start", "url": url, "task_id": created.get("TaskId", "0" * 32),
            "seconds": seconds, "epoch": int(time.time()), "use_device_wifi": device_wifi}
    if not device_wifi:
        data.update(ssid=ssid, password=password)
    body = json.dumps(data,
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) >= 4096:
        raise ValueError("Device configuration is too large")
    return body + b"\n"


def read_events(port, deadline, query_status=False):
    pending = bytearray()
    discard = False
    next_query = time.monotonic() + 10
    while time.monotonic() < deadline:
        if query_status and time.monotonic() >= next_query:
            port.write(b"?\n")
            next_query = time.monotonic() + 10
        raw = port.read_until(b"\n", 10000)
        if not raw:
            continue
        if len(pending) + len(raw) > 10000:
            pending.clear()
            discard = True
        elif not discard:
            pending.extend(raw)
        if not raw.endswith(b"\n"):
            continue
        line = bytes(pending)
        pending.clear()
        if discard:
            discard = False
            continue
        try:
            data = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("event"), str):
            yield data


def stream_is_complete(event, seconds):
    sent = event.get("audio_bytes")
    if event.get("complete") is not True or not isinstance(sent, int) or isinstance(sent, bool):
        return False
    if not 0 < sent <= seconds * 32000 or sent % 640:
        return False
    if event.get("captured_bytes", sent) != sent:
        return False
    if event.get("end_reason", "duration") in {"button", "serial", "setup"}:
        return True
    return event.get("end_reason", "duration") == "duration" and sent == seconds * 32000


def notify_summary(port, ready):
    command = {"cmd": "summary", "status": "ready" if ready else "unavailable"}
    port.write((json.dumps(command) + "\n").encode())
    port.flush()
    for event in read_events(port, time.monotonic() + 5):
        if event["event"] == "summary_updated":
            emit("device_summary_status", ui_state=event.get("ui_state"), elapsed_ms=event.get("elapsed_ms"))
            return
    emit("device_summary_status_unconfirmed")


def wait_for_result(service, port, task_id, complete, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        data = service.result(task_id)
        status = data.get("TaskStatus")
        emit("result_status", task_id=task_id, status=status, recording_complete=complete)
        if status == "COMPLETED":
            artifacts = emit_artifacts(data)
            summary = artifacts.get("Summarization", {}).get("Summarization", {}).get("ParagraphSummary")
            ready = complete and isinstance(summary, str) and bool(summary.strip())
            notify_summary(port, ready)
            return artifacts if ready else None
        if status in {"FAILED", "INVALID"} or time.monotonic() >= deadline:
            notify_summary(port, False)
            return
        time.sleep(10)


def run_device(service, port, seconds, metadata=None):
    # Fail before creating a cloud task if this is still the normal badge firmware.
    port.write(b'\n{"cmd":"ping"}\n')
    for event in read_events(port, time.monotonic() + 5):
        if event["event"] == "meeting_ready":
            if event.get("wifi_setup") is True:
                emit("setup_required", instruction="请先连接设备屏幕上的热点，完成 AP 配网后再开始录音。")
                raise RuntimeError("Device Wi-Fi setup is active")
            device_wifi = event.get("wifi_saved") is True
            # Old firmware advertises no limit and only accepts five minutes.
            # Reject before creating a cloud task it cannot run.
            device_limit = event.get("max_recording_seconds", 300)
            if type(device_limit) is not int or not 1 <= device_limit <= MAX_RECORDING_SECONDS or seconds > device_limit:
                raise ValueError("Requested duration exceeds this device firmware limit")
            break
    else:
        raise RuntimeError("Device is not running the meeting trial firmware")
    # Validate credentials/network configuration before creating a billable task.
    start_message({"MeetingJoinUrl": "wss://trial.aliyuncs.com/"}, seconds, device_wifi=device_wifi)
    created = service.create()
    task_id = created["TaskId"]
    emit("task_created", task_id=task_id)
    complete = False
    try:
        port.write(start_message(created, seconds, device_wifi=device_wifi))
        port.flush()
        for event in read_events(port, time.monotonic() + seconds + 100, query_status=True):
            # Only known fields are emitted. Never echo arbitrary device logs/config.
            name = event["event"]
            if name == "recording" and metadata is not None:
                metadata.setdefault("started_at", datetime.now().astimezone().isoformat(timespec="seconds"))
            if name == "transcript":
                emit("transcript", final=event.get("final"), text=event.get("result"),
                     speaker_id=event.get("speaker_id"), index=event.get("index"))
            elif name in {"recording", "recording_progress", "wifi_connected", "wss_connected", "stt_started",
                          "wifi_disconnected", "provider_error", "stream_finished", "invalid_config",
                          "init_failed", "mic_stalled", "capture_error", "provider_json_error"}:
                emit(name, complete=event.get("complete"), audio_bytes=event.get("audio_bytes"),
                     heap=event.get("heap"), min_heap=event.get("min_heap"),
                     reason=event.get("reason"), code=event.get("code"),
                     end_reason=event.get("end_reason"), captured_bytes=event.get("captured_bytes"),
                     elapsed_ms=event.get("elapsed_ms"), level=event.get("level"),
                     max_send_ms=event.get("max_send_ms"), send_calls=event.get("send_calls"),
                     queue_peak=event.get("queue_peak"))
            if name == "stream_finished":
                if event.get("task_id") != task_id:
                    continue # Never accept a replay from a previous meeting.
                complete = stream_is_complete(event, seconds)
                break
            if name in {"invalid_config", "init_failed", "mic_stalled"}:
                break
    finally:
        try:
            port.write(b"!\n")
        finally:
            service.stop(task_id)
            emit("task_stopped", task_id=task_id)
    if not complete:
        emit("incomplete_recording", task_id=task_id)
    return task_id, complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name(".env"))
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--wait-seconds", type=int, default=300)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--feishu", action="store_true", help="After a complete recording, export text minutes via the user's Feishu CLI")
    parser.add_argument("--title", help="Custom Feishu document title; default is recording time and cloud topic")
    args = parser.parse_args()
    load_config(args.config)
    if args.title is not None:
        from export_feishu import document_title
        document_title({}, args.title)  # Validate before creating a cloud task.
    if args.check_config:
        emit("config_status", configured={key: bool(os.environ.get(key)) for key in sorted(CONFIG_KEYS)})
        return
    if not 1 <= args.seconds <= MAX_RECORDING_SECONDS or not 0 <= args.wait_seconds <= 1800:
        parser.error("Recording: 1..86400 seconds (endurance unverified); result wait: 0..1800 seconds")
    service = Tingwu()
    port = serial.Serial()
    port.port, port.baudrate, port.timeout, port.write_timeout = args.port, 115200, 0.5, 5
    port.dtr = port.rts = False
    port.open()
    try:
        time.sleep(0.2)
        port.reset_input_buffer()
        metadata = {}
        task_id, complete = run_device(service, port, args.seconds, metadata=metadata)
        try:
            artifacts = wait_for_result(service, port, task_id, complete, args.wait_seconds)
        except Exception:
            # Preserve the failure while making it clear on the device that the
            # microphone has stopped and the result needs another query.
            try:
                notify_summary(port, False)
            except Exception:
                pass
            raise
        if args.feishu and artifacts:
            from export_feishu import prepare, publish
            publish(prepare(task_id, artifacts, title=args.title, started_at=metadata.get("started_at")))
    finally:
        port.close()


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        emit("failed", error_type=type(exc).__name__)
        sys.exit(1)
