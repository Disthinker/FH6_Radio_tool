from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fh6_radio_tool.audio_cache_tools import (
    normalize_user_gain_db,
    prepared_audio_cache_key,
    prepared_cache_matches_gain,
    prepared_cache_matches_source,
    source_audio_signature,
)


class AudioCacheToolsTest(unittest.TestCase):
    def test_signature_matches_unchanged_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "track.wav"
            source.write_bytes(b"abc")

            loudness = source_audio_signature(source)

            self.assertTrue(prepared_cache_matches_source(loudness, source))

    def test_signature_rejects_changed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "track.wav"
            source.write_bytes(b"abc")
            loudness = source_audio_signature(source)

            source.write_bytes(b"abcd")
            os.utime(source, None)

            self.assertFalse(prepared_cache_matches_source(loudness, source))

    def test_missing_signature_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "track.wav"
            source.write_bytes(b"abc")

            self.assertFalse(prepared_cache_matches_source({"prepared_basis": "assignment"}, source))

    def test_nonzero_gain_uses_distinct_cache_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "track.wav"
            source.write_bytes(b"abc")

            self.assertEqual(prepared_audio_cache_key(source, 0.0), prepared_audio_cache_key(source, 0.004))
            self.assertNotEqual(prepared_audio_cache_key(source, 0.0), prepared_audio_cache_key(source, 1.5))
            self.assertTrue(prepared_cache_matches_gain({"user_gain_db": 1.5}, 1.5))
            self.assertFalse(prepared_cache_matches_gain({"user_gain_db": 1.5}, 0.0))

    def test_user_gain_is_clamped_for_safety(self) -> None:
        self.assertEqual(normalize_user_gain_db(99), 6.0)
        self.assertEqual(normalize_user_gain_db(-99), -6.0)
        self.assertEqual(normalize_user_gain_db("1.234"), 1.23)


if __name__ == "__main__":
    unittest.main()
