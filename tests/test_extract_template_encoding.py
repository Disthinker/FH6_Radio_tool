from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from fh6_radio_tool.order_tools import parse_extract_template


def write_silent_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(b"\x00\x00" * 2 * 16)


class ExtractTemplateEncodingTest(unittest.TestCase):
    def test_utf16_extract_txt_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "R9_Tracks_CU1.assets[0]"
            folder.mkdir(parents=True)
            (folder / "R9_Tracks_CU1.assets[0].txt").write_text("sound_0.wav\n", encoding="utf-16")
            write_silent_wav(folder / "sound_0.wav")

            records = parse_extract_template(root)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].extracted_name, "sound_0.wav")
            self.assertEqual(records[0].frames, 16)


if __name__ == "__main__":
    unittest.main()
