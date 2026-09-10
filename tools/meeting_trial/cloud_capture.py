"""Cloud-only PCM collector. Device uploads incrementally; Feishu runs after stop.

This proof of concept intentionally requires an explicitly configured cloud data
directory. Do not run the capture service on the developer's Windows computer.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

from aiohttp import WSMsgType, web

RATE = 16000
MAX_FRAME = 6400
MAX_AUDIO_BYTES = 6 * 3600 * RATE * 2
RETENTION_SECONDS = 7 * 86400
SESSION_ID = re.compile(r"[0-9a-f]{32}")


@dataclass
class Runtime:
    root: Path
    ingress_token: str
    busy: bool = False


RUNTIME = web.AppKey("meeting_trial_runtime", Runtime)


class PcmWave:
    def __init__(self, output):
        self.wav = wave.open(output, "wb")
        self.wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        self.audio_bytes = 0
        self.digest = hashlib.sha256()

    def append(self, data: bytes):
        if not data or len(data) > MAX_FRAME or len(data) % 2:
            raise ValueError("Expected 1..6400 bytes of aligned PCM S16LE")
        if self.audio_bytes + len(data) > MAX_AUDIO_BYTES:
            raise ValueError("Feishu trial maximum duration is six hours")
        self.wav.writeframesraw(data)
        self.digest.update(data)
        self.audio_bytes += len(data)

    def close(self):
        self.wav.close()


def save_state(root: Path, sid: str, state: dict):
    if not SESSION_ID.fullmatch(sid):
        raise ValueError("Invalid session ID")
    target = root / (sid + ".json")
    temporary = root / (sid + ".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def cli_prefix():
    native = shutil.which("lark-cli")
    if native:
        return [native]
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not npx:
        raise RuntimeError("Install the official Feishu CLI on the cloud host")
    return [npx, "--yes", "@larksuite/cli@latest"]


async def lark(root: Path, *args):
    process = await asyncio.create_subprocess_exec(
        *cli_prefix(), *args, "--as", "user", cwd=root,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=900)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("CLI timeout; check cloud state before retrying writes")
    for raw in (stdout, stderr):
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            continue
        if "ok" in value:
            return value
    raise RuntimeError("Unrecognized CLI response; do not automatically repeat writes")


def require_ok(response):
    if response.get("ok") is not True:
        error = response.get("error", {})
        raise RuntimeError("Feishu command failed: " + str(error.get("subtype", "unknown")))
    return response["data"]


async def submit_feishu(root: Path, sid: str):
    """Persist stage IDs. Never blindly repeat a potentially committed create."""
    if not SESSION_ID.fullmatch(sid):
        raise ValueError("Invalid session ID")
    state = json.loads((root / (sid + ".json")).read_text(encoding="utf-8"))
    if state["status"] not in ("captured", "drive_uploaded", "minute_created", "ready"):
        raise ValueError("Session incomplete or write outcome uncertain; inspect it first")
    if state["status"] == "ready":
        return state
    if not state.get("file_token"):
        state["status"] = "drive_upload_inflight"
        save_state(root, sid, state)
        data = require_ok(await lark(root, "drive", "+upload", "--file", sid + ".wav"))
        state.update(file_token=data["file_token"], status="drive_uploaded")
        save_state(root, sid, state)
    if not state.get("minute_token"):
        state["status"] = "minute_create_inflight"
        save_state(root, sid, state)
        data = require_ok(await lark(root, "minutes", "+upload", "--file-token", state["file_token"]))
        token = data.get("minute_token")
        if not token:
            from urllib.parse import urlsplit
            token = urlsplit(data["minute_url"]).path.rstrip("/").rsplit("/", 1)[-1]
        state.update(minute_token=token, minute_url=data["minute_url"], status="minute_created")
        state["feishu_audio_cleanup_required"] = True
        save_state(root, sid, state)
    reply = await lark(root, "minutes", "+detail", "--minute-tokens", state["minute_token"],
                       "--summary", "--todo", "--chapter", "--wait-ready")
    for item in reply.get("data", {}).get("minutes", []):
        if item.get("minute_token") != state["minute_token"]:
            continue
        artifacts = item.get("artifacts", {})
        summary = artifacts.get("summary") if isinstance(artifacts, dict) else None
        if (not item.get("error") and item.get("status") != "processing"
                and isinstance(summary, str) and summary.strip()):
            state.update(status="ready", artifacts=item["artifacts"])
            save_state(root, sid, state)
    return state


async def capture(request):
    runtime = request.app[RUNTIME]
    expected = "Bearer " + runtime.ingress_token
    if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
        raise web.HTTPUnauthorized()
    if runtime.busy:
        raise web.HTTPServiceUnavailable(text="One trial recording at a time")
    ws = web.WebSocketResponse(max_msg_size=8192, heartbeat=20, receive_timeout=30)
    runtime.busy = True
    sid = uuid.uuid4().hex
    root = runtime.root
    state = {"session_id": sid, "status": "capturing", "created_at": time.time(),
             "expires_at": time.time() + RETENTION_SECONDS, "audio_bytes": 0,
             "format": "pcm_s16le", "sample_rate": RATE, "channels": 1}
    writer = None
    completed = False
    try:
        await ws.prepare(request)
        start = await ws.receive_json()
        if start != {"type": "start", "format": "pcm_s16le", "sample_rate": RATE, "channels": 1}:
            raise ValueError("Unsupported audio format")
        writer = PcmWave(str(root / (sid + ".wav")))
        save_state(root, sid, state)
        await ws.send_json({"type": "ready", "session_id": sid, "max_frame_bytes": MAX_FRAME})
        async for message in ws:
            if time.time() - state["created_at"] > 6 * 3600:
                raise ValueError("Maximum trial wall time exceeded")
            if message.type == WSMsgType.BINARY:
                writer.append(message.data)
            elif message.type == WSMsgType.TEXT:
                if message.json() != {"type": "stop"}:
                    raise ValueError("Expected stop")
                if not writer.audio_bytes:
                    raise ValueError("Empty recording")
                completed = True
                break
            elif message.type == WSMsgType.ERROR:
                raise RuntimeError("WebSocket failed")
        writer.close()
        state.update(status="captured" if completed else "interrupted",
                     audio_bytes=writer.audio_bytes, sha256_pcm=writer.digest.hexdigest())
        save_state(root, sid, state)
        if completed:
            await ws.send_json({"type": "captured", "session_id": sid,
                                "audio_bytes": writer.audio_bytes, "sha256_pcm": state["sha256_pcm"]})
        return ws
    except Exception as exc:
        state["status"] = "interrupted"
        state["error_type"] = type(exc).__name__
        save_state(root, sid, state)
        if ws.prepared and not ws.closed:
            await ws.close(code=1008, message=b"Recording incomplete")
        return ws
    finally:
        if writer:
            writer.close()
        runtime.busy = False


async def retention(app):
    root = app[RUNTIME].root
    async def sweep():
        while True:
            for path in root.glob("*.json"):
                if not SESSION_ID.fullmatch(path.stem):
                    continue
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                    if time.time() >= state["expires_at"]:
                        (root / (path.stem + ".wav")).unlink(missing_ok=True)
                        if not state.get("source_audio_deleted"):
                            state["source_audio_deleted"] = True
                            save_state(root, path.stem, state)
                except (OSError, ValueError, KeyError):
                    print("retention_check_failed", file=sys.stderr)
            await asyncio.sleep(60)
    task = asyncio.create_task(sweep())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud-data-dir", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8765)
    submit = sub.add_parser("submit")
    submit.add_argument("session_id")
    args = parser.parse_args()
    if os.environ.get("MEETING_TRIAL_CLOUD_HOST") != "1":
        parser.error("Set MEETING_TRIAL_CLOUD_HOST=1 only on the selected cloud host")
    if os.name == "nt":
        parser.error("This cloud prototype targets Linux; do not capture audio on this Windows PC")
    root = args.cloud_data_dir.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.command == "submit":
        state = asyncio.run(submit_feishu(root, args.session_id))
        print(json.dumps(state, ensure_ascii=False))
        return
    secret = os.environ.get("MEETING_TRIAL_INGRESS_TOKEN", "")
    if len(secret) < 32:
        parser.error("Configure a random ingress token with at least 32 characters")
    app = web.Application(client_max_size=8192)
    app[RUNTIME] = Runtime(root, secret)
    app.router.add_get("/v1/recordings", capture)
    app.cleanup_ctx.append(retention)
    # A TLS reverse proxy on the cloud host exposes this as WSS to the ESP32.
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
