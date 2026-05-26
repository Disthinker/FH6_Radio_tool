from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .metadata_tools import TrackMetadata
from .models import AudioInfo, SegmentMarkers, StationInfo, TrackPatch
from .segment_tools import ADVANCED_DISABLE_SENTINEL, CONTROL_AUTO_MARKERS, MARKER_ORDER


PLAYBACK_MARKERS = [
    "TrackStart",
    "DJDrop",
    "TrackDrop",
    "TrackLoopStart",
    "TrackLoopEnd",
    "PostDrop",
    "PostRaceLoopStart",
    "PostRaceLoopEnd",
    "End",
]

CONTROL_MARKERS = ["DJSegment", "StingerStart", "DJStart"]
NEGATIVE_SENTINEL_MARKERS = {"TrackLoopEnd", "PostRaceLoopEnd", "DJSegment", "StingerStart", "DJStart"}
DJ_STINGER_OFFSET_SAMPLES = 1000


def parse_xml(xml_path: Path) -> ET.ElementTree:
    xml_path = Path(xml_path)
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file does not exist: {xml_path}")
    return ET.parse(xml_path)


def get_radio_stations(tree: ET.ElementTree) -> list[ET.Element]:
    stations_node = tree.getroot().find("RadioStations")
    if stations_node is None:
        raise ValueError("RadioStations node not found")
    return list(stations_node.findall("RadioStation"))


def find_station(tree: ET.ElementTree, station_name: str) -> ET.Element:
    for station in get_radio_stations(tree):
        if station.get("Name") == station_name:
            return station
    names = [s.get("Name", "<unnamed>") for s in get_radio_stations(tree)]
    raise ValueError(f"Radio station not found: {station_name}. Available: {names}")


def get_track_sample_list(station: ET.Element) -> ET.Element:
    for sample_list in station.findall("SampleList"):
        if sample_list.get("Type") == "Track":
            return sample_list
    raise ValueError(f"Station {station.get('Name')} has no SampleList Type='Track'")


def get_track_samples(station: ET.Element) -> list[ET.Element]:
    return list(get_track_sample_list(station).findall("Sample"))


def station_info_from_node(station: ET.Element) -> StationInfo:
    banks_node = station.find("Banks")
    banks: list[str] = []
    if banks_node is not None:
        banks = [b.get("Name", "") for b in banks_node.findall("Bank") if b.get("Name")]

    samples = get_track_samples(station)
    sample_rates = sorted(
        {
            int(s.get("SampleRate"))
            for s in samples
            if s.get("SampleRate") and s.get("SampleRate", "").isdigit()
        }
    )

    return StationInfo(
        name=station.get("Name", ""),
        number=station.get("Number"),
        banks=banks,
        track_slot_count=len(samples),
        sample_rates=sample_rates,
    )


def list_station_infos(tree: ET.ElementTree) -> list[StationInfo]:
    return [station_info_from_node(s) for s in get_radio_stations(tree)]


def safe_slug(text: str, max_len: int = 48) -> str:
    text = Path(text).stem
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "Track")[:max_len]


def build_track_patches(
    station: StationInfo,
    audios: list[AudioInfo],
    artist: str = "User",
    sound_prefix: str = "HZ6_USER",
    markers_by_filename: dict[str, SegmentMarkers] | None = None,
    metadata_by_filename: dict[str, TrackMetadata] | None = None,
) -> list[TrackPatch]:
    patches: list[TrackPatch] = []
    station_token = f"R{station.number}" if station.number else safe_slug(station.name)
    markers_by_filename = markers_by_filename or {}
    metadata_by_filename = metadata_by_filename or {}

    for idx, audio in enumerate(audios, start=1):
        slug = safe_slug(audio.filename)
        sound_name = f"{sound_prefix}_{station_token}_{idx:02d}_{slug}"
        markers = markers_by_filename.get(audio.filename)
        if markers is None:
            markers = SegmentMarkers({"TrackStart": 0, "End": max(0, audio.sample_length - 1)})

        metadata = metadata_by_filename.get(audio.filename)
        display_name = metadata.display_name if metadata else Path(audio.filename).stem
        display_artist = metadata.artist if metadata else artist

        patches.append(
            TrackPatch(
                slot_index=idx - 1,
                audio=audio,
                display_name=display_name,
                artist=display_artist,
                sound_name=sound_name,
                markers=markers,
            )
        )
    return patches


def _clear_known_markers(sample: ET.Element, marker_names: list[str] | set[str] | None = None) -> None:
    known = set(marker_names or MARKER_ORDER)
    for name in known:
        sample.attrib.pop(name, None)
    for marker in list(sample.findall("Marker")):
        if marker.get("Name") in known:
            sample.remove(marker)


def _sample_length(sample: ET.Element) -> int:
    try:
        return int(sample.get("SampleLength", "0") or 0)
    except Exception:
        return 0


def _sample_marker_positions(sample: ET.Element) -> dict[str, int]:
    positions: dict[str, int] = {}
    for name in MARKER_ORDER:
        raw = _get_marker_value(sample, name)
        if raw == "":
            continue
        try:
            positions[name] = int(float(raw))
        except Exception:
            continue
    return positions


def _scale_template_marker(template: dict[str, int], template_length: int, audio: AudioInfo, name: str) -> int | None:
    value = template.get(name)
    if value is None or value < 0 or template_length <= 0:
        return None
    end_sample = max(0, int(audio.sample_length) - 1)
    scaled = int(round((float(value) / float(template_length)) * float(audio.sample_length)))
    return max(0, min(scaled, end_sample))


def _bounded_tail_samples(raw_tail: int | None, audio: AudioInfo) -> int:
    final_length = max(0, int(audio.sample_length))
    if final_length <= 2:
        return 1
    sample_rate = max(1, int(audio.samplerate or 48000))
    fallback = 2 * sample_rate
    tail = int(raw_tail if raw_tail and raw_tail > 0 else fallback)
    min_tail = min(sample_rate, max(1, final_length // 4))
    max_tail = min(5 * sample_rate, max(1, final_length - 2))
    if min_tail > max_tail:
        min_tail = max_tail
    return max(min_tail, min(tail, max_tail))


def _clamp_position(value: int, audio: AudioInfo) -> int:
    end_sample = max(0, int(audio.sample_length) - 1)
    return max(0, min(int(value), end_sample))


def _auto_djdrop(raw: dict[str, int], template: dict[str, int], template_length: int, audio: AudioInfo, positions: dict[str, int]) -> int:
    if int(raw.get("DJDrop", -1)) >= 0:
        return _clamp_position(int(raw["DJDrop"]), audio)

    scaled = _scale_template_marker(template, template_length, audio, "DJDrop")
    if scaled is None:
        scaled = _scale_template_marker(template, template_length, audio, "TrackDrop")
    if scaled is None:
        scaled = int(positions.get("TrackStart", 0))

    # If the user deliberately set a non-zero TrackDrop, keep DJDrop no later
    # than that point.  A default TrackDrop=0 is treated as "no authored value"
    # so the original slot template can still provide a useful post-DJ entry.
    track_drop = int(raw.get("TrackDrop", -1))
    if track_drop > 0:
        scaled = min(scaled, int(positions.get("TrackDrop", track_drop)))
    return _clamp_position(int(scaled), audio)


def _safe_marker_positions(
    markers: SegmentMarkers,
    audio: AudioInfo,
    template_sample: ET.Element | None = None,
) -> dict[str, int]:
    end_sample = max(0, int(audio.sample_length) - 1)
    raw = dict(markers.positions or {})
    positions: dict[str, int] = {}
    template = _sample_marker_positions(template_sample) if template_sample is not None else {}
    template_length = _sample_length(template_sample) if template_sample is not None else 0

    def clamp(value: object, default: int, *, allow_negative_sentinel: bool = False) -> int:
        try:
            value_i = int(value)
        except Exception:
            value_i = int(default)
        if allow_negative_sentinel and value_i == -1:
            return -1
        if allow_negative_sentinel and value_i == ADVANCED_DISABLE_SENTINEL:
            return ADVANCED_DISABLE_SENTINEL
        return max(0, min(value_i, end_sample))

    start_default = clamp(raw.get("TrackStart", 0), 0)
    end_default = clamp(raw.get("End", end_sample), end_sample)
    if end_default < start_default:
        end_default = start_default

    defaults = {
        "TrackStart": start_default,
        "TrackDrop": start_default,
        "TrackLoopStart": start_default,
        "TrackLoopEnd": -1,
        "PostDrop": start_default,
        "PostRaceLoopStart": start_default,
        "PostRaceLoopEnd": -1,
        "End": end_default,
    }

    for name in PLAYBACK_MARKERS:
        if name == "DJDrop":
            continue
        positions[name] = clamp(
            raw.get(name, defaults[name]),
            defaults[name],
            allow_negative_sentinel=name in NEGATIVE_SENTINEL_MARKERS,
        )
    positions["DJDrop"] = _auto_djdrop(raw, template, template_length, audio, positions)

    for start_name, end_name in [
        ("TrackLoopStart", "TrackLoopEnd"),
        ("PostRaceLoopStart", "PostRaceLoopEnd"),
    ]:
        if positions[end_name] >= 0 and positions[end_name] < positions[start_name]:
            positions[end_name] = positions[start_name]

    if positions["End"] < positions["TrackStart"]:
        positions["End"] = positions["TrackStart"]
    return positions


def _auto_djsegment(raw: dict[str, int], template: dict[str, int], template_length: int, audio: AudioInfo) -> int:
    if int(raw.get("DJSegment", -1)) == ADVANCED_DISABLE_SENTINEL:
        return -1
    if int(raw.get("DJSegment", -1)) >= 0:
        return _clamp_position(int(raw["DJSegment"]), audio)

    final_length = max(1, int(audio.sample_length))
    scaled = _scale_template_marker(template, template_length, audio, "DJSegment")
    if scaled is None:
        scaled = int(round(final_length * 0.5))
    low = int(round(final_length * 0.25))
    high = int(round(final_length * 0.75))
    if low > high:
        low = high = _clamp_position(scaled, audio)
    return _clamp_position(max(low, min(int(scaled), high)), audio)


def _auto_dj_start(template: dict[str, int], template_length: int, audio: AudioInfo) -> int:
    end_sample = max(0, int(audio.sample_length) - 1)
    template_djstart = template.get("DJStart")
    tail = None
    if template_djstart is not None and template_djstart >= 0 and template_length > 0:
        tail = template_length - template_djstart
    bounded_tail = _bounded_tail_samples(tail, audio)
    if end_sample <= DJ_STINGER_OFFSET_SAMPLES + 1:
        return max(1, end_sample)
    return max(DJ_STINGER_OFFSET_SAMPLES, min(end_sample - 1, end_sample - bounded_tail))


def _control_marker_positions(
    markers: SegmentMarkers,
    audio: AudioInfo,
    template_sample: ET.Element | None = None,
) -> tuple[dict[str, int], list[str]]:
    end_sample = max(0, int(audio.sample_length) - 1)
    raw = dict(markers.positions or {})
    positions: dict[str, int] = {}
    warnings: list[str] = []
    template = _sample_marker_positions(template_sample) if template_sample is not None else {}
    template_length = _sample_length(template_sample) if template_sample is not None else 0

    positions["DJSegment"] = _auto_djsegment(raw, template, template_length, audio)

    raw_stinger = int(raw.get("StingerStart", -1))
    raw_djstart = int(raw.get("DJStart", -1))
    if raw_stinger == ADVANCED_DISABLE_SENTINEL or raw_djstart == ADVANCED_DISABLE_SENTINEL:
        positions["StingerStart"] = -1
        positions["DJStart"] = -1
        return positions, warnings

    if raw_djstart >= 0:
        djstart = _clamp_position(raw_djstart, audio)
    elif raw_stinger >= 0:
        djstart = _clamp_position(raw_stinger + DJ_STINGER_OFFSET_SAMPLES, audio)
    else:
        djstart = _auto_dj_start(template, template_length, audio)

    if end_sample > 0 and djstart >= end_sample:
        warnings.append("DJStart reached End; moved before final sample")
        djstart = max(1, end_sample - 1)

    stinger = max(0, djstart - DJ_STINGER_OFFSET_SAMPLES)
    if raw_stinger >= 0 and raw_djstart >= 0 and raw_djstart - raw_stinger != DJ_STINGER_OFFSET_SAMPLES:
        warnings.append("StingerStart adjusted so DJStart-StingerStart=1000")
    if stinger >= djstart:
        stinger = max(0, djstart - 1)
    positions["StingerStart"] = stinger
    positions["DJStart"] = djstart
    return positions, warnings


def _set_marker(sample: ET.Element, name: str, position: int) -> None:
    value = str(int(position))
    sample.set(name, value)
    for marker in sample.findall("Marker"):
        if marker.get("Name") == name:
            marker.set("Position", value)
            return
    ET.SubElement(sample, "Marker", {"Name": name, "Position": value})


def _get_marker_value(sample: ET.Element, name: str) -> str:
    value = sample.get(name)
    if value is not None:
        return value
    for marker in sample.findall("Marker"):
        if marker.get("Name") == name:
            return marker.get("Position", "")
    return ""


def _set_duration_like_fields(sample: ET.Element, audio: AudioInfo) -> None:
    for field in ["DurationSamples", "TotalSamples", "EndSample", "FrameCount"]:
        if field in sample.attrib:
            sample.set(field, str(audio.sample_length))
    if "Duration" in sample.attrib:
        raw = sample.get("Duration", "")
        try:
            numeric = float(raw)
        except Exception:
            numeric = 0.0
        if numeric > 1000:
            sample.set("Duration", str(audio.sample_length))
        else:
            sample.set("Duration", f"{audio.duration_sec:.6f}")


def patch_station_samples(
    tree: ET.ElementTree,
    station_name: str,
    patches: list[TrackPatch],
    sync_report: list[str] | None = None,
) -> ET.ElementTree:
    new_root = copy.deepcopy(tree.getroot())
    new_tree = ET.ElementTree(new_root)
    station = find_station(new_tree, station_name)
    samples = get_track_samples(station)

    if len(patches) > len(samples):
        raise ValueError(f"Patch count {len(patches)} exceeds station slot count {len(samples)}")

    for patch in patches:
        primary = samples[patch.slot_index]
        sound_name = primary.get("SoundName", "")
        target_samples = [primary]
        if sound_name:
            for candidate in samples:
                if candidate is not primary and candidate.get("SoundName", "") == sound_name:
                    target_samples.append(candidate)

        safe_positions = _safe_marker_positions(patch.markers, patch.audio, primary)
        control_positions, control_warnings = _control_marker_positions(patch.markers, patch.audio, primary)

        for sample in target_samples:
            # Keep SoundName from the original XML; rebuilt banks reuse original
            # sound resources instead of creating new names.
            sample.set("SampleLength", str(patch.audio.sample_length))
            sample.set("SampleRate", str(patch.audio.samplerate))
            sample.set("DisplayName", patch.display_name)
            sample.set("Artist", patch.artist)
            _set_duration_like_fields(sample, patch.audio)

            if "IsXCloudModeSafe" in sample.attrib:
                sample.set("IsXCloudModeSafe", "true")

            _clear_known_markers(sample, PLAYBACK_MARKERS)
            for marker_name in PLAYBACK_MARKERS:
                _set_marker(sample, marker_name, safe_positions[marker_name])
            for marker_name, value in control_positions.items():
                _set_marker(sample, marker_name, value)

        if sync_report is not None:
            sync_report.append(
                f"slot {patch.slot_index}: sound_name={sound_name or '<empty>'}, synced_nodes={len(target_samples)}"
            )
            for warning in control_warnings:
                sync_report.append(f"WARN: slot {patch.slot_index}: {warning}")

    return new_tree


def validate_station_patch_consistency(
    tree: ET.ElementTree,
    station_name: str,
    patches: list[TrackPatch],
) -> list[str]:
    station = find_station(tree, station_name)
    samples = get_track_samples(station)
    warnings: list[str] = []
    key_markers = [
        "DJDrop",
        "TrackDrop",
        "PostDrop",
        "TrackLoopStart",
        "TrackLoopEnd",
        "PostRaceLoopStart",
        "PostRaceLoopEnd",
        "DJSegment",
        "StingerStart",
        "DJStart",
        "End",
    ]

    for patch in patches:
        if patch.slot_index >= len(samples):
            warnings.append(f"slot {patch.slot_index}: missing XML sample after patch")
            continue
        primary = samples[patch.slot_index]
        sound_name = primary.get("SoundName", "")
        related = [s for s in samples if sound_name and s.get("SoundName", "") == sound_name] or [primary]
        expected_len = str(patch.audio.sample_length)
        expected_end = str(max(0, patch.audio.sample_length - 1))
        baseline = {name: _get_marker_value(primary, name) for name in key_markers}

        for sample in related:
            if sample.get("SampleLength", "") != expected_len:
                warnings.append(
                    f"slot {patch.slot_index}: SampleLength mismatch for sound_name={sound_name}: "
                    f"{sample.get('SampleLength', '')} != {expected_len}"
                )
            if _get_marker_value(sample, "End") != expected_end:
                warnings.append(
                    f"slot {patch.slot_index}: End mismatch for sound_name={sound_name}: "
                    f"{_get_marker_value(sample, 'End')} != {expected_end}"
                )
            for marker_name in key_markers:
                value = _get_marker_value(sample, marker_name)
                if value != baseline.get(marker_name, ""):
                    warnings.append(
                        f"slot {patch.slot_index}: duplicate marker mismatch {marker_name} "
                        f"for sound_name={sound_name}: {value} != {baseline.get(marker_name, '')}"
                    )
    return warnings


def write_xml(tree: ET.ElementTree, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
