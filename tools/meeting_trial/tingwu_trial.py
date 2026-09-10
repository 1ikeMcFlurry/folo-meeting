"""Provider trial: stream PCM from stdin/HTTPS, never save audio locally."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from urllib.parse import urlsplit
from urllib.request import urlopen

RATE = 16000
BYTES_PER_SECOND = RATE * 2
FRAME_BYTES = 3200


def task_parameters(app_key: str, task_key: str) -> dict:
    return {
        "AppKey": app_key,
        "Input": {
            "Format": "pcm", "SampleRate": RATE, "SourceLanguage": "cn",
            "TaskKey": task_key, "ProgressiveCallbacksEnabled": False,
        },
        "Parameters": {
            "Transcription": {
                "OutputLevel": 2, "DiarizationEnabled": True,
                "Diarization": {"SpeakerCount": 0},
            },
            "SummarizationEnabled": True,
            "Summarization": {"Types": ["Paragraph"]},
            "MeetingAssistanceEnabled": True,
            "MeetingAssistance": {"Types": ["Actions"]},
        },
    }


def pcm_frames(source, max_seconds: int):
    """Bounded reads; reject a WAV container and truncated 16-bit samples."""
    remaining = max_seconds * BYTES_PER_SECOND
    first = True
    while remaining:
        frame = source.read(min(FRAME_BYTES, remaining))
        if not frame:
            return
        if first and frame[:4] in (b"RIFF", b"RF64"):
            raise ValueError("Input must be raw PCM S16LE, not a WAV container")
        first = False
        if len(frame) % 2:
            extra = source.read(1)
            if not extra:
                raise ValueError("Truncated 16-bit PCM sample")
            frame += extra
        remaining -= len(frame)
        yield frame


def emit(event: str, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


class Tingwu:
    def __init__(self):
        from aliyunsdkcore.client import AcsClient
        names = ("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "TINGWU_APP_KEY")
        missing = [key for key in names if not os.environ.get(key)]
        if missing:
            raise ValueError("Missing environment variables: " + ", ".join(missing))
        self.app_key = os.environ["TINGWU_APP_KEY"]
        self.client = AcsClient(os.environ[names[0]], os.environ[names[1]], "cn-beijing",
                                auto_retry=False, connect_timeout=15, timeout=30)

    def call(self, method: str, path: str, body=None, query=None):
        from aliyunsdkcore.request import CommonRequest
        req = CommonRequest()
        req.set_accept_format("json")
        req.set_domain("tingwu.cn-beijing.aliyuncs.com")
        req.set_version("2023-09-30")
        req.set_protocol_type("https")
        req.set_method(method)
        req.set_uri_pattern(path)
        for key, value in (query or {}).items():
            req.add_query_param(key, value)
        if body is not None:
            req.add_header("Content-Type", "application/json")
            req.set_content(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        token = os.environ.get("ALIBABA_CLOUD_SECURITY_TOKEN")
        if token:
            req.add_header("x-acs-security-token", token)
        response = json.loads(self.client.do_action_with_exception(req))
        if response.get("Code") not in (None, "0", 0, "SUCCESS", "Success"):
            raise RuntimeError("Tingwu API error: " + str(response["Code"]))
        return response["Data"]

    def create(self):
        return self.call("PUT", "/openapi/tingwu/v2/tasks",
                         task_parameters(self.app_key, "meeting-trial-" + uuid.uuid4().hex),
                         {"type": "realtime"})

    def stop(self, task_id: str):
        return self.call("PUT", "/openapi/tingwu/v2/tasks",
                         {"AppKey": self.app_key, "Input": {"TaskId": task_id}},
                         {"type": "realtime", "operation": "stop"})

    def result(self, task_id: str):
        if not task_id.isalnum():
            raise ValueError("Invalid task ID")
        return self.call("GET", "/openapi/tingwu/v2/tasks/" + task_id)


def run_stream(service: Tingwu, source, max_seconds: int):
    import nls
    nls.enableTrace(False)
    created = service.create()
    task_id, url = created["TaskId"], created["MeetingJoinUrl"]
    emit("task_created", task_id=task_id)
    # MeetingJoinUrl is a signed capability. Do not log or persist it.
    failed = threading.Event()

    def on_error(message, *args):
        failed.set()
        emit("stream_error", task_id=task_id)

    def on_text(message, *args):
        try:
            obj = json.loads(message)
            payload = obj.get("payload", {})
            emit("transcript", index=payload.get("index"), text=payload.get("result"),
                 speaker_id=payload.get("speaker_id"), audio_ms=payload.get("time"))
        except (TypeError, ValueError):
            failed.set()

    stream = nls.NlsRealtimeMeeting(url=url, on_sentence_end=on_text,
                                   on_result_changed=on_text, on_error=on_error)
    sent = 0
    started = False
    try:
        if stream.start() is False:
            raise RuntimeError("Stream did not start")
        started = True
        start = time.monotonic()
        for frame in pcm_frames(source, max_seconds):
            if failed.is_set():
                raise RuntimeError("Provider rejected the stream")
            result = stream.send_audio(frame)
            if result is False:
                raise RuntimeError("Audio send failed")
            sent += len(frame)
            delay = start + sent / BYTES_PER_SECOND - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    finally:
        try:
            if started:
                stream.stop()
        finally:
            try:
                stream.shutdown()
            finally:
                # Closing the audio channel alone does not finish the meeting task.
                service.stop(task_id)
                emit("task_stopped", task_id=task_id, audio_bytes=sent)
    if failed.is_set():
        raise RuntimeError("Stream failed; task ID can be used to inspect partial results")
    return task_id


def fetch_artifacts(data: dict):
    artifacts = {}
    for kind in ("Summarization", "MeetingAssistance"):
        url = data.get("Result", {}).get(kind)
        if not url:
            continue
        parsed = urlsplit(url)
        if parsed.scheme == "http" and (parsed.hostname or "").endswith(".aliyuncs.com"):
            url = parsed._replace(scheme="https").geturl()
        if urlsplit(url).scheme != "https":
            raise ValueError("Result download requires HTTPS")
        with urlopen(url, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Result is too large for this short trial")
        content = json.loads(raw)
        artifacts[kind] = content
    return artifacts


def emit_artifacts(data: dict):
    artifacts = fetch_artifacts(data)
    for kind, content in artifacts.items():
        emit("artifact", kind=kind, content=content)
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stream = sub.add_parser("stream")
    source = stream.add_mutually_exclusive_group(required=True)
    source.add_argument("--pcm-stdin", action="store_true")
    source.add_argument("--pcm-url", help="HTTPS raw PCM, not a WAV/MP3 URL")
    stream.add_argument("--max-seconds", type=int, default=300)
    stream.add_argument("--wait-seconds", type=int, default=300)
    result = sub.add_parser("result")
    result.add_argument("task_id")
    stop = sub.add_parser("stop")
    stop.add_argument("task_id")
    args = parser.parse_args()
    service = Tingwu()
    if args.command == "stop":
        service.stop(args.task_id)
        emit("task_stopped", task_id=args.task_id)
        return
    if args.command == "result":
        data = service.result(args.task_id)
        # Result URLs may be signed. Emit only status and available product names.
        emit("result_status", task_id=args.task_id, status=data.get("TaskStatus"),
             result_types=list(data.get("Result", {})))
        if data.get("TaskStatus") == "COMPLETED":
            emit_artifacts(data)
        return
    if not 1 <= args.max_seconds <= 3600 or not 0 <= args.wait_seconds <= 1800:
        parser.error("Trial limit: 1..3600 audio seconds and 0..1800 wait seconds")
    if args.pcm_url and urlsplit(args.pcm_url).scheme != "https":
        parser.error("Audio URL must use HTTPS")
    ctx = urlopen(args.pcm_url, timeout=30) if args.pcm_url else nullcontext(sys.stdin.buffer)
    with ctx as source:
        task_id = run_stream(service, source, args.max_seconds)
    deadline = time.monotonic() + args.wait_seconds
    while True:
        data = service.result(task_id)
        status = data.get("TaskStatus")
        emit("result_status", task_id=task_id, status=status,
             result_types=list(data.get("Result", {})))
        if status == "COMPLETED":
            emit_artifacts(data)
            break
        if status in ("FAILED", "INVALID"):
            raise RuntimeError("Tingwu task failed")
        if time.monotonic() >= deadline:
            break
        time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SDK exceptions can contain request headers or signed URLs.
        emit("failed", error_type=type(exc).__name__)
        sys.exit(1)
