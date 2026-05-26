from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from fh6_radio_tool.volume_reference_tools import ensure_volume_reference_wav


class VolumeReferenceToolsTest(unittest.TestCase):
    def test_reference_wav_is_short_stereo_48k_pcm16(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = ensure_volume_reference_wav(Path(tmp) / "reference.wav")

            with wave.open(str(path), "rb") as wf:
                self.assertEqual(wf.getframerate(), 48000)
                self.assertEqual(wf.getnchannels(), 2)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertGreater(wf.getnframes(), 48000)
                self.assertLess(wf.getnframes(), 48000 * 2)


if __name__ == "__main__":
    unittest.main()
