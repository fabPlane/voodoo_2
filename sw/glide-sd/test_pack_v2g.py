#!/usr/bin/env python3
"""Host tests for V2GL packing (no FPGA)."""
from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import pack_v2g

BANK0 = struct.pack("<II", 0x21C, 0x00110000) + struct.pack("<II", 0x148, 0xFF00)
BANK1 = struct.pack("<II", 0x200, 1)


class PackV2gTest(unittest.TestCase):
    def test_header_and_slots(self) -> None:
        img = pack_v2g.pack([(BANK0, 0), (BANK1, 0)])
        magic, ver, slots, _ = struct.unpack_from("<IIII", img, 0)
        self.assertEqual(magic, pack_v2g.MAGIC)
        self.assertEqual(ver, 1)
        self.assertEqual(slots, 2)
        off0, count0, loop0, _ = struct.unpack_from("<IIII", img, 16)
        self.assertEqual(off0, pack_v2g.TABLE)
        self.assertEqual(count0, 2)
        self.assertEqual(loop0, 0)
        recs = img[pack_v2g.TABLE :]
        self.assertEqual(recs, BANK0 + BANK1)

    def test_fat_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "demo.bin"
            src.write_bytes(BANK0)
            pack_v2g.write_fat(root / "card", pack_v2g.pack([(BANK0, 0)]), [src])
            play = (root / "card" / "glide" / "PLAY.V2G").read_bytes()
            self.assertEqual(play[:4], b"V2GL")
            self.assertTrue((root / "card" / "glide" / "INDEX.TXT").exists())
            self.assertTrue((root / "card" / "glide" / "traces" / "demo.bin").exists())


if __name__ == "__main__":
    unittest.main()
