"""Send raw PCM stdin to the cloud collector, using bounded RAM only."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout
from tingwu_trial import BYTES_PER_SECOND, FRAME_BYTES


async def run(url, seconds):
    token = os.environ.get("MEETING_TRIAL_INGRESS_TOKEN", "")
    if len(token) < 32:
        raise ValueError("Missing cloud ingress token")
    total = 0
    digest = hashlib.sha256()
    async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=15)) as client:
        async with client.ws_connect(url, headers={"Authorization": "Bearer " + token},
                                     heartbeat=20, max_msg_size=8192) as ws:
            await ws.send_json({"type": "start", "format": "pcm_s16le", "sample_rate": 16000, "channels": 1})
            reply = await asyncio.wait_for(ws.receive_json(), 30)
            if reply.get("type") != "ready":
                raise RuntimeError("Cloud capture did not start")
            print(json.dumps(reply), flush=True)
            started = time.monotonic()
            while total < seconds * BYTES_PER_SECOND:
                count = min(FRAME_BYTES, seconds * BYTES_PER_SECOND - total)
                frame = await asyncio.wait_for(asyncio.to_thread(sys.stdin.buffer.read, count), 25)
                if not frame:
                    break
                if len(frame) % 2 or (total == 0 and frame[:4] in (b"RIFF", b"RF64")):
                    raise ValueError("Expected aligned raw PCM S16LE, not WAV")
                await ws.send_bytes(frame)
                digest.update(frame)
                total += len(frame)
                await asyncio.sleep(max(0, started + total / BYTES_PER_SECOND - time.monotonic()))
            await ws.send_json({"type": "stop"})
            result = await asyncio.wait_for(ws.receive_json(), 30)
            if (result.get("type") != "captured" or result.get("audio_bytes") != total
                    or result.get("sha256_pcm") != digest.hexdigest()):
                raise RuntimeError("Cloud audio checksum/length verification failed")
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="wss://cloud-host/v1/recordings")
    parser.add_argument("--max-seconds", type=int, default=300)
    args = parser.parse_args()
    if urlsplit(args.url).scheme != "wss" or not 1 <= args.max_seconds <= 3600:
        parser.error("Use WSS and 1..3600 seconds")
    try:
        asyncio.run(run(args.url, args.max_seconds))
    except Exception as exc:
        print(json.dumps({"event": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
