import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def resolved_partitions():
    current = 0x9000
    result = {}
    with (ROOT / "partitions.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.reader(line for line in stream if not line.lstrip().startswith("#")):
            if not row or not row[0].strip():
                continue
            name, kind, _subtype, offset, size = (item.strip() for item in row[:5])
            size_value = int(size, 0)
            if offset:
                start = int(offset, 0)
            else:
                alignment = 0x10000 if kind == "app" else 0x1000
                start = (current + alignment - 1) & ~(alignment - 1)
            result[name] = (start, start + size_value)
            current = start + size_value
    return result


class PartitionLayoutTest(unittest.TestCase):
    def test_avatar_growth_preserves_deployed_audio_filesystem(self):
        partitions = resolved_partitions()
        self.assertEqual((0x37A000, 0x3FA000), partitions["audio"])
        self.assertEqual((0x3FA000, 0x4FA000), partitions["imgava"])


if __name__ == "__main__":
    unittest.main()
