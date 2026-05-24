from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ScenePreviewPlan:
    scenario: str
    start_sample: int
    end_sample: int
    loop: bool
    loop_start_sample: int | None
    description: str

    def to_json(self) -> dict:
        return asdict(self)


def _clamp(v: int, total_last: int) -> int:
    return max(0, min(int(v), max(0, int(total_last))))


def build_scene_preview_plan(scenario: str, markers: dict[str, int], total_frames: int, samplerate: int, preview_seconds: int = 5) -> ScenePreviewPlan:
    total_last = max(0, int(total_frames) - 1)
    sr = max(1, int(samplerate))
    seconds = max(1, int(preview_seconds))

    def get(name: str, fallback: int) -> int:
        return _clamp(markers.get(name, fallback), total_last)

    if scenario == "roam_loop":
        ls = get("TrackLoopStart", get("TrackStart", 0))
        le = get("TrackLoopEnd", total_last)
        start = max(ls, le - seconds * sr)
        return ScenePreviewPlan(scenario, start, le, True, ls, "漫游模式：TrackLoop 循环")
    if scenario == "race_start":
        ls = get("TrackLoopStart", get("TrackStart", 0))
        le = get("TrackLoopEnd", min(total_last, ls + seconds * sr))
        start = get("TrackDrop", get("TrackStart", 0))
        return ScenePreviewPlan(scenario, start, le, True, ls, "比赛开始：TrackDrop/TrackStart 进入 TrackLoop")
    if scenario == "race_loop":
        ls = get("TrackLoopStart", 0)
        le = get("TrackLoopEnd", total_last)
        return ScenePreviewPlan(scenario, ls, le, True, ls, "比赛进行：TrackLoop 循环")
    if scenario == "finish":
        # In many FH radio XMLs PostDrop can be earlier than TrackLoopEnd in the
        # source file.  The old preview tried to play from TrackLoopEnd to
        # PostDrop + N seconds; when PostDrop < TrackLoopEnd that collapsed into
        # a one-sample range, so playback sounded like a click and jumped to the
        # end.  Use a stable local window instead: prefer PostDrop when it is a
        # valid marker, otherwise preview around TrackLoopEnd.
        tle = get("TrackLoopEnd", total_last)
        raw_pd = int(markers.get("PostDrop", -1))
        if 0 <= raw_pd <= total_last:
            center = _clamp(raw_pd, total_last)
            start = max(0, center - seconds * sr)
            end = min(total_last, center + seconds * sr)
            if end <= start:
                end = min(total_last, start + seconds * sr)
            return ScenePreviewPlan(scenario, start, max(start + 1, end), False, None, "冲线：PostDrop 前后预览")
        start = max(0, tle - seconds * sr)
        end = min(total_last, tle + seconds * sr)
        return ScenePreviewPlan(scenario, start, max(start + 1, end), False, None, "冲线：TrackLoopEnd 前后预览")
    ps = get("PostRaceLoopStart", get("PostDrop", get("TrackLoopStart", 0)))
    pe = get("PostRaceLoopEnd", total_last)
    return ScenePreviewPlan(scenario, ps, pe, True, ps, "冲线后：PostRaceLoop 循环")
