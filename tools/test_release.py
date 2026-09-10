import tempfile
import unittest
from pathlib import Path

from scripts import release


class ReleaseAvatarValidationTests(unittest.TestCase):
    def test_rejects_stale_flash_args_that_omit_avatar_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / "imgava.bin").write_bytes(b"avatar-pack")
            (build / "avatar_catalog.json").write_text("{}", encoding="utf-8")
            (build / "flash_args").write_text(
                "0x0 bootloader/bootloader.bin\n"
                "0x10000 trae_card.bin\n"
                "0x8000 partition_table/partition-table.bin\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "imgava.bin"):
                release.validate_avatar_release_inputs(build)


if __name__ == "__main__":
    unittest.main()
