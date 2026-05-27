from __future__ import annotations

import unittest

from fh6_radio_tool.loop_engine.scene_preview import build_scene_preview_plan


MARKERS = {
    "TrackStart": 10,
    "TrackDrop": 20,
    "TrackLoopStart": 100,
    "TrackLoopEnd": 300,
    "PostDrop": 400,
    "PostRaceLoopStart": 500,
    "PostRaceLoopEnd": 700,
}


class ScenePreviewPlanTest(unittest.TestCase):
    def plan(self, scenario: str):
        return build_scene_preview_plan(scenario, MARKERS, total_frames=1000, samplerate=10, preview_seconds=5)

    def test_free_roam_is_normal_playback_without_loop(self) -> None:
        plan = self.plan("roam_loop")
        self.assertEqual(plan.start_sample, 10)
        self.assertEqual(plan.end_sample, 60)
        self.assertFalse(plan.loop)
        self.assertIsNone(plan.loop_start_sample)

    def test_race_start_still_enters_track_loop(self) -> None:
        plan = self.plan("race_start")
        self.assertEqual(plan.start_sample, 20)
        self.assertEqual(plan.end_sample, 300)
        self.assertTrue(plan.loop)
        self.assertEqual(plan.loop_start_sample, 100)

    def test_race_loop_starts_before_loop_end_for_fast_seam_check(self) -> None:
        plan = self.plan("race_loop")
        self.assertEqual(plan.start_sample, 250)
        self.assertEqual(plan.end_sample, 300)
        self.assertTrue(plan.loop)
        self.assertEqual(plan.loop_start_sample, 100)

    def test_finish_starts_directly_at_postdrop(self) -> None:
        plan = self.plan("finish")
        self.assertEqual(plan.start_sample, 400)
        self.assertEqual(plan.end_sample, 450)
        self.assertFalse(plan.loop)

    def test_post_race_starts_before_post_loop_end_for_fast_seam_check(self) -> None:
        plan = self.plan("post_loop")
        self.assertEqual(plan.start_sample, 650)
        self.assertEqual(plan.end_sample, 700)
        self.assertTrue(plan.loop)
        self.assertEqual(plan.loop_start_sample, 500)


if __name__ == "__main__":
    unittest.main()
