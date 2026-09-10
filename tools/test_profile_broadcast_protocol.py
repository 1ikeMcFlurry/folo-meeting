import hashlib
import hmac
import unittest

from profile_broadcast_protocol import (
    HDR_COMMON, HDR_NICKNAME, android_payload, build_common, build_nickname, rotation,
)


class ProfileBroadcastProtocolTest(unittest.TestCase):
    KEY = "TESTPK0123456789"

    def test_common_is_one_signed_packet(self):
        frame = build_common(txn_id=0x1234, device_secret=self.KEY, token=40000,
                             token_max=80000, time_epoch=0x65010203, avatar_id=7)
        self.assertEqual(26, len(frame))
        self.assertEqual(b"HB", frame[:2])
        self.assertEqual(HDR_COMMON, frame[2])
        self.assertEqual(24, len(android_payload(frame)))
        self.assertEqual(hmac.new(self.KEY.encode(), frame[:18], hashlib.sha256).digest()[:8], frame[18:])

    def test_nickname_boundaries(self):
        self.assertEqual(1, len(build_nickname(txn_id=1, nickname="abc", device_secret=self.KEY)))
        frames = build_nickname(txn_id=2, nickname="a" * 47, device_secret=self.KEY)
        self.assertEqual(5, len(frames))
        self.assertTrue(all(frame[2] == HDR_NICKNAME for frame in frames))
        self.assertEqual([0x40, 0x41, 0x42, 0x43, 0x44], [frame[5] for frame in frames])

    def test_max_rotation_fits_4800_ms(self):
        common = build_common(txn_id=1, device_secret=self.KEY, token=1)
        nick = build_nickname(txn_id=2, nickname="a" * 47, device_secret=self.KEY)
        sequence = rotation(common, nick, 8)
        self.assertEqual(48, len(sequence))
        self.assertLessEqual(len(sequence) * 100, 4800)

    def test_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            build_common(txn_id=1, device_secret=self.KEY)
        with self.assertRaises(ValueError):
            build_common(txn_id=1, device_secret=self.KEY, avatar_id=0)
        with self.assertRaises(ValueError):
            build_nickname(txn_id=1, nickname="a" * 48, device_secret=self.KEY)


if __name__ == "__main__":
    unittest.main()
