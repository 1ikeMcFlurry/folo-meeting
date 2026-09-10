"""Strict host-side reassembly for the BLE RGB565 screen-capture stream."""

from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib

from PIL import Image


WIDTH = 240
HEIGHT = 320
PIXEL_FORMAT_RGB565_LE = 0x01
TOTAL_LEN = WIDTH * HEIGHT * 2

_META = 0x01
_DATA = 0x02
_END = 0x03
_META_LEN = 14
_DATA_HEADER_LEN = 17
_END_LEN = 15


class CaptureProtocolError(ValueError):
    """A capture notification violates the committed wire protocol."""


@dataclass(frozen=True)
class Ack:
    """The capture identity the host must ACK through the control channel."""

    capture_id: int
    seq: int


@dataclass(frozen=True)
class Complete:
    """A validated final canvas; ``capture_id`` and ``seq`` still require an ACK."""

    capture_id: int
    seq: int
    data: bytes
    width: int
    height: int
    packet_count: int

    @property
    def ack(self) -> Ack:
        return Ack(self.capture_id, self.seq)


class CaptureAssembler:
    """Reassemble one stop-and-wait screenshot stream from raw notifications."""

    def __init__(self) -> None:
        self.cancel()

    @property
    def canvas(self) -> bytes:
        """The current row-major RGB565 canvas (primarily useful for diagnostics)."""
        return bytes(self._canvas)

    @property
    def capture_id(self) -> int | None:
        return self._capture_id

    @property
    def received_bytes(self) -> int:
        return self._received_bytes

    @property
    def total_len(self) -> int:
        return self._total_len

    def cancel(self) -> None:
        """Discard any partial or completed stream and accept a fresh META packet."""
        self._capture_id: int | None = None
        self._expected_seq = 0
        self._last_ack: Ack | None = None
        self._canvas = bytearray()
        self._coverage = bytearray()
        self._covered_pixels = 0
        self._received_bytes = 0
        self._data_packet_count = 0
        self._total_len = 0
        self._completed = False

    def feed(self, data: bytes | bytearray | memoryview) -> Ack | Complete:
        """Consume one raw notification and return its control-channel response."""
        try:
            packet = bytes(data)
        except (TypeError, ValueError) as exc:
            raise CaptureProtocolError("notification must be bytes-like") from exc
        if not packet:
            raise CaptureProtocolError("empty notification")

        kind = packet[0]
        if kind == _META:
            return self._feed_meta(packet)
        if kind == _DATA:
            return self._feed_data(packet)
        if kind == _END:
            return self._feed_end(packet)
        raise CaptureProtocolError("unknown notification kind")

    def _feed_meta(self, packet: bytes) -> Ack:
        if len(packet) != _META_LEN:
            raise CaptureProtocolError("META has an invalid length")
        _, capture_id, seq, width, height, pixel_format, total_len = struct.unpack("<BHHHHBI", packet)
        if capture_id == 0:
            raise CaptureProtocolError("META capture ID must be nonzero")
        if (width, height, pixel_format, total_len) != (
            WIDTH, HEIGHT, PIXEL_FORMAT_RGB565_LE, TOTAL_LEN
        ):
            raise CaptureProtocolError("unsupported META parameters")

        if self._capture_id is None:
            if seq != 0:
                raise CaptureProtocolError("META sequence must be zero")
            self._capture_id = capture_id
            self._expected_seq = 1
            self._last_ack = Ack(capture_id, seq)
            self._canvas = bytearray(total_len)
            self._coverage = bytearray((WIDTH * HEIGHT + 7) // 8)
            self._total_len = total_len
            return self._last_ack

        if capture_id != self._capture_id:
            raise CaptureProtocolError("unexpected capture ID")
        if self._last_ack is not None and seq == self._last_ack.seq:
            return self._last_ack
        raise CaptureProtocolError("unexpected META sequence")

    def _feed_data(self, packet: bytes) -> Ack:
        if len(packet) <= _DATA_HEADER_LEN:
            raise CaptureProtocolError("DATA must contain pixel bytes")
        _, capture_id, seq, x, y, width, height, region_offset = struct.unpack(
            "<BHHHHHHI", packet[:_DATA_HEADER_LEN]
        )
        pixels = packet[_DATA_HEADER_LEN:]
        if len(pixels) & 1:
            raise CaptureProtocolError("DATA payload must contain whole RGB565 pixels")
        if width == 0 or height == 0 or x >= WIDTH or y >= HEIGHT:
            raise CaptureProtocolError("DATA region is invalid")
        if width > WIDTH - x or height > HEIGHT - y:
            raise CaptureProtocolError("DATA region is out of bounds")
        region_len = width * height * 2
        if region_offset & 1 or region_offset >= region_len or region_offset + len(pixels) > region_len:
            raise CaptureProtocolError("DATA region offset is invalid")

        duplicate = self._check_sequence(capture_id, seq)
        if duplicate:
            return self._last_ack  # type: ignore[return-value]
        if self._completed:
            raise CaptureProtocolError("capture is already complete")

        pixel_offset = region_offset // 2
        pixel_count = len(pixels) // 2
        rows = list(self._region_rows(x, y, width, pixel_offset, pixel_count))
        for canvas_pixel, count, _ in rows:
            for pixel in range(canvas_pixel, canvas_pixel + count):
                if self._is_covered(pixel):
                    raise CaptureProtocolError("DATA overlaps already received pixels")
        for canvas_pixel, count, source_pixel in rows:
            source_byte = source_pixel * 2
            canvas_byte = canvas_pixel * 2
            byte_count = count * 2
            self._canvas[canvas_byte:canvas_byte + byte_count] = pixels[source_byte:source_byte + byte_count]
            for pixel in range(canvas_pixel, canvas_pixel + count):
                self._mark_covered(pixel)

        self._covered_pixels += pixel_count
        self._received_bytes += len(pixels)
        self._data_packet_count += 1
        return self._advance_ack(capture_id, seq)

    def _feed_end(self, packet: bytes) -> Ack | Complete:
        if len(packet) != _END_LEN:
            raise CaptureProtocolError("END has an invalid length")
        _, capture_id, seq, packet_count, total_len, advertised_crc = struct.unpack("<BHHHII", packet)
        if total_len != TOTAL_LEN:
            raise CaptureProtocolError("END has an invalid total length")

        duplicate = self._check_sequence(capture_id, seq)
        if duplicate:
            return self._last_ack  # type: ignore[return-value]
        if self._completed:
            raise CaptureProtocolError("capture is already complete")
        if packet_count != (self._data_packet_count & 0xFFFF):
            raise CaptureProtocolError("END packet count does not match DATA packets")
        if self._received_bytes != self._total_len:
            raise CaptureProtocolError("END arrived before all canvas bytes")
        if self._covered_pixels != WIDTH * HEIGHT or not self._all_pixels_covered():
            raise CaptureProtocolError("END arrived before full canvas coverage")
        if zlib.crc32(self._canvas) & 0xFFFFFFFF != advertised_crc:
            raise CaptureProtocolError("END CRC32 does not match canvas")

        self._completed = True
        self._advance_ack(capture_id, seq)
        return Complete(capture_id, seq, bytes(self._canvas), WIDTH, HEIGHT, packet_count)

    def _check_sequence(self, capture_id: int, seq: int) -> bool:
        if self._capture_id is None:
            raise CaptureProtocolError("META is required before DATA or END")
        if capture_id != self._capture_id:
            raise CaptureProtocolError("unexpected capture ID")
        if seq == self._expected_seq:
            return False
        if self._last_ack is not None and seq == self._last_ack.seq:
            return True
        raise CaptureProtocolError("unexpected packet sequence")

    def _advance_ack(self, capture_id: int, seq: int) -> Ack:
        ack = Ack(capture_id, seq)
        self._last_ack = ack
        self._expected_seq = (seq + 1) & 0xFFFF
        return ack

    @staticmethod
    def _region_rows(x: int, y: int, width: int, pixel_offset: int, pixel_count: int):
        remaining = pixel_count
        source_pixel = 0
        region_pixel = pixel_offset
        while remaining:
            region_row, region_column = divmod(region_pixel, width)
            count = min(remaining, width - region_column)
            canvas_pixel = (y + region_row) * WIDTH + x + region_column
            yield canvas_pixel, count, source_pixel
            remaining -= count
            source_pixel += count
            region_pixel += count

    def _is_covered(self, pixel: int) -> bool:
        return bool(self._coverage[pixel >> 3] & (1 << (pixel & 7)))

    def _mark_covered(self, pixel: int) -> None:
        self._coverage[pixel >> 3] |= 1 << (pixel & 7)

    def _all_pixels_covered(self) -> bool:
        return all(self._is_covered(pixel) for pixel in range(WIDTH * HEIGHT))


def rgb565le_to_image(data: bytes | bytearray | memoryview, width: int, height: int) -> Image.Image:
    """Convert a row-major little-endian RGB565 buffer to an unrotated RGB image."""
    if width <= 0 or height <= 0:
        raise CaptureProtocolError("image dimensions must be positive")
    try:
        raw = bytes(data)
    except (TypeError, ValueError) as exc:
        raise CaptureProtocolError("RGB565 data must be bytes-like") from exc
    if len(raw) != width * height * 2:
        raise CaptureProtocolError("RGB565 data length does not match image dimensions")

    rgb = bytearray(width * height * 3)
    for pixel_index in range(width * height):
        value = raw[pixel_index * 2] | raw[pixel_index * 2 + 1] << 8
        red = value >> 11
        green = value >> 5 & 0x3F
        blue = value & 0x1F
        rgb_index = pixel_index * 3
        rgb[rgb_index] = (red << 3) | (red >> 2)
        rgb[rgb_index + 1] = (green << 2) | (green >> 4)
        rgb[rgb_index + 2] = (blue << 3) | (blue >> 2)
    return Image.frombytes("RGB", (width, height), bytes(rgb))
