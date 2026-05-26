from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from fh6_radio_tool.v2_loop_tools import (
    NATIVE_LOOPFINDER_SOURCE,
    LoopCandidate,
    _candidates_from_loopfinder_payload,
    _dedupe_and_rank_candidates,
    analyze_loop_candidates,
)
from fh6_radio_tool.wav_tools import read_wav_info


def _write_silent_wav(path: Path, frames: int = 48_000, samplerate: int = 48_000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(b"\x00\x00\x00\x00" * frames)


class LoopFinderIntegrationTests(unittest.TestCase):
    def test_native_payload_preserves_details(self) -> None:
        payload = {
            "ok": True,
            "dll": "C:/tool/loopfinder.dll",
            "elapsed_sec": 1.25,
            "input": "song.wav",
            "candidates": [
                {
                    "loop_start": 12_000,
                    "loop_end": 240_000,
                    "score": 0.87,
                    "note_diff": 0.01,
                    "loudness_diff": 0.2,
                }
            ],
        }

        candidates = _candidates_from_loopfinder_payload(payload)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source, NATIVE_LOOPFINDER_SOURCE)
        self.assertEqual(candidates[0].details["note_diff"], 0.01)
        self.assertEqual(candidates[0].to_json()["details"]["dll"], "C:/tool/loopfinder.dll")

    def test_missing_native_engine_falls_back_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.wav"
            _write_silent_wav(path)
            fallback = [LoopCandidate(100, 10_000, 0.9, "pymusiclooper", "fallback")]

            with mock.patch("fh6_radio_tool.v2_loop_tools.is_loopfinder_available", return_value=(False, "missing dll")):
                with mock.patch("fh6_radio_tool.v2_loop_tools.run_pymusiclooper_candidates", return_value=fallback):
                    candidates, message = analyze_loop_candidates(path, top_n=1)

        self.assertEqual(candidates, fallback)
        self.assertIn("Seamless LoopFinder unavailable", message)

    def test_source_priority_avoids_mixed_score_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.wav"
            _write_silent_wav(path)
            info = read_wav_info(path)

            ranked = _dedupe_and_rank_candidates(
                [
                    LoopCandidate(1_000, 20_000, 0.99, "builtin"),
                    LoopCandidate(130_000, 180_000, 0.80, "pymusiclooper"),
                    LoopCandidate(260_000, 330_000, 0.70, NATIVE_LOOPFINDER_SOURCE),
                ],
                info,
                top_n=3,
            )

        self.assertEqual([c.source for c in ranked], [NATIVE_LOOPFINDER_SOURCE, "pymusiclooper", "builtin"])

    def test_low_score_native_does_not_outrank_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.wav"
            _write_silent_wav(path)
            info = read_wav_info(path)

            ranked = _dedupe_and_rank_candidates(
                [
                    LoopCandidate(1_000, 20_000, 0.30, NATIVE_LOOPFINDER_SOURCE),
                    LoopCandidate(130_000, 180_000, 0.80, "pymusiclooper"),
                ],
                info,
                top_n=2,
            )

        self.assertEqual([c.source for c in ranked], ["pymusiclooper", NATIVE_LOOPFINDER_SOURCE])


if __name__ == "__main__":
    unittest.main()
