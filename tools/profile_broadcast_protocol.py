"""TRAE-CARD v3 fast profile Manufacturer Data frame builder.

The returned frames include the two-byte company ID (b"HB"). Android/WinRT
advertiser APIs take ``frame[2:]`` as their 24-byte manufacturer payload.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass

FRAME_LEN = 26
SIGN_LEN = 18
HDR_COMMON = 0x34
HDR_NICKNAME = 0x35
NICK_CHUNK = 11
NICK_MAX = 47

FIELD_TOKEN = 1 << 0
FIELD_TOKEN_MAX = 1 << 1
FIELD_TIME = 1 << 2
FIELD_AVATAR = 1 << 3


def _check_txn(txn_id: int) -> None:
    if not 0 <= txn_id <= 0xFFFF:
        raise ValueError("txn_id must be 0..65535")


def _sign(body: bytes, device_secret: str) -> bytes:
    if len(body) != SIGN_LEN:
        raise ValueError("signed body must be exactly 18 bytes")
    if not device_secret:
        raise ValueError("DeviceSecret is required")
    try:
        key = device_secret.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("DeviceSecret must be ASCII") from exc
    return body + hmac.new(key, body, hashlib.sha256).digest()[:8]


def build_common(*, txn_id: int, device_secret: str, token: int | None = None,
                 token_max: int | None = None, time_epoch: int | None = None,
                 avatar_id: int | None = None) -> bytes:
    """Build one complete common-fields Manufacturer Data value."""
    _check_txn(txn_id)
    fields = 0
    values = [token, token_max]
    for name, value in zip(("token", "token_max"), values):
        if value is not None and not 0 <= value <= 0xFFFFFF:
            raise ValueError(f"{name} must be 0..16777215")
    if token is not None:
        fields |= FIELD_TOKEN
    if token_max is not None:
        if token_max < 1:
            raise ValueError("token_max must be >=1")
        fields |= FIELD_TOKEN_MAX
    if time_epoch is not None:
        if not 1 <= time_epoch <= 0xFFFFFFFF:
            raise ValueError("time_epoch must be 1..4294967295")
        fields |= FIELD_TIME
    if avatar_id is not None:
        if not 1 <= avatar_id <= 255:
            raise ValueError("avatar_id must be 1..255")
        fields |= FIELD_AVATAR
    if not fields:
        raise ValueError("at least one common field is required")

    def u24(value: int | None) -> bytes:
        return struct.pack("<I", value or 0)[:3]

    body = (b"HB" + bytes([HDR_COMMON]) + struct.pack("<H", txn_id) + bytes([fields])
            + u24(token) + u24(token_max) + struct.pack("<I", time_epoch or 0)
            + bytes([avatar_id or 0, 0]))
    frame = _sign(body, device_secret)
    assert len(frame) == FRAME_LEN
    return frame


def build_nickname(*, txn_id: int, nickname: str, device_secret: str) -> list[bytes]:
    """Build one to five signed nickname fragments."""
    _check_txn(txn_id)
    raw = nickname.encode("utf-8")
    if not 1 <= len(raw) <= NICK_MAX or b"\0" in raw:
        raise ValueError("nickname UTF-8 length must be 1..47 bytes without NUL")
    count = (len(raw) + NICK_CHUNK - 1) // NICK_CHUNK
    frames = []
    for index in range(count):
        chunk = raw[index * NICK_CHUNK:(index + 1) * NICK_CHUNK].ljust(NICK_CHUNK, b"\0")
        frag = ((count - 1) << 4) | index
        body = (b"HB" + bytes([HDR_NICKNAME]) + struct.pack("<H", txn_id)
                + bytes([frag, len(raw)]) + chunk)
        frame = _sign(body, device_secret)
        assert len(frame) == FRAME_LEN
        frames.append(frame)
    return frames


def rotation(common: bytes | None, nickname_frames: list[bytes], rounds: int = 8) -> list[bytes]:
    """Return the exact 100 ms round-robin sequence; max 6 packets x 8 = 4.8 s."""
    if rounds < 1:
        raise ValueError("rounds must be positive")
    unique = ([common] if common is not None else []) + list(nickname_frames)
    if not unique or len(unique) > 6 or any(len(frame) != FRAME_LEN for frame in unique):
        raise ValueError("rotation requires 1..6 valid frames")
    return unique * rounds


def android_payload(frame: bytes) -> bytes:
    if len(frame) != FRAME_LEN or frame[:2] != b"HB":
        raise ValueError("invalid v3 frame")
    return frame[2:]
