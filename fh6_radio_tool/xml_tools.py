from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import AudioInfo, SegmentMarkers, StationInfo, TrackPatch
from .metadata_tools import TrackMetadata
from .segment_tools import MARKER_ORDER


# Markers that control normal track playback and race/post-race music state.
# These are safe to regenerate for replacement songs.
PLAYBACK_MARKERS = [
    "TrackStart",
    "TrackDrop",
    "TrackLoopStart",
    "TrackLoopEnd",
    "PostDrop",
    "PostRaceLoopStart",
    "PostRaceLoopEnd",
    "End",
]

# Markers related to DJ voice / stinger timing.
# Do not auto-fill these: forcing them to 0 can make free-roam DJ transitions
# cut to another song or overlap audio after the DJ finishes talking.
CONTROL_MARKERS = ["DJSegment", "StingerStart", "DJStart"]

# Markers where -1 is a meaningful disabled/no-loop sentinel for FH radio XML.
# Keep this small and explicit so normal playback markers such as End are never
# accidentally written as negative values.
NEGATIVE_SENTINEL_MARKERS = {"TrackLoopEnd", "PostRaceLoopEnd", "DJSegment", "StingerStart", "DJStart"}


def parse_xml(xml_path: Path) -> ET.ElementTree:
    if not xml_path.exists():
        raise FileNotFoundError(f"XML 文件不存在: {xml_path}")
    return ET.parse(xml_path)


def get_radio_stations(tree: ET.ElementTree) -> list[ET.Element]:
    root = tree.getroot()
    stations_node = root.find("RadioStations")
    if stations_node is None:
        raise ValueError("未找到 RadioStations 节点")
    return list(stations_node.findall("RadioStation"))


def find_station(tree: ET.ElementTree, station_name: str) -> ET.Element:
    for station in get_radio_stations(tree):
        if station.get("Name") == station_name:
            return station
    names = [s.get("Name", "<unnamed>") for s in get_radio_stations(tree)]
    raise ValueError(f"未找到电台: {station_name}. 可选电台: {names}")


def get_track_sample_list(station: ET.Element) -> ET.Element:
    for sample_list in station.findall("SampleList"):
        if sample_list.get("Type") == "Track":
            return sample_list
    raise ValueError(f"电台 {station.get('Name')} 未找到 SampleList Type='Track'")


def get_track_samples(station: ET.Element) -> list[ET.Element]:
    sample_list = get_track_sample_list(station)
    return list(sample_list.findall("Sample"))


def station_info_from_node(station: ET.Element) -> StationInfo:
    banks_node = station.find("Banks")
    banks = []
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
            markers = SegmentMarkers({
                "TrackStart": 0,
                "End": max(0, audio.sample_length - 1),
            })

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
    """Remove marker fields for the given names before writing replacements.

    v3.7.8 only clears playback/race markers by default.  DJ/Stinger
    markers are station transition controls; if the user did not explicitly
    set them, we preserve the original XML values instead of forcing 0 or -1.
    """
    known = set(marker_names or MARKER_ORDER)

    for name in known:
        sample.attrib.pop(name, None)

    for marker in list(sample.findall("Marker")):
        if marker.get("Name") in known:
            sample.remove(marker)


def _safe_marker_positions(markers: SegmentMarkers, audio: AudioInfo) -> dict[str, int]:
    """Return a conservative marker set for track/race playback.

    No-loop songs should keep TrackLoopEnd/PostRaceLoopEnd at -1 instead of
    silently regenerating them as End.  This prevents the common user mistake
    where the UI shows LoopEnd disabled but generated XML still contains a
    full-length loop marker copied from defaults.
    """
    end_sample = max(0, int(audio.sample_length) - 1)
    positions: dict[str, int] = {}

    def clamp(value: object, default: int, *, allow_negative_sentinel: bool = False) -> int:
        try:
            value_i = int(value)
        except Exception:
            value_i = int(default)
        if allow_negative_sentinel and value_i == -1:
            return -1
        return max(0, min(value_i, end_sample))

    raw = dict(markers.positions or {})

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
        positions[name] = clamp(raw.get(name, defaults[name]), defaults[name], allow_negative_sentinel=name in NEGATIVE_SENTINEL_MARKERS)

    for start_name, end_name in [
        ("TrackLoopStart", "TrackLoopEnd"),
        ("PostRaceLoopStart", "PostRaceLoopEnd"),
    ]:
        if positions[end_name] >= 0 and positions[end_name] < positions[start_name]:
            positions[end_name] = positions[start_name]

    if positions["End"] < positions["TrackStart"]:
        positions["End"] = positions["TrackStart"]

    return positions


def _explicit_control_marker_positions(markers: SegmentMarkers, audio: AudioInfo) -> dict[str, int]:
    """Return DJ/Stinger markers only when the user explicitly set them.

    Preserve -1 as an explicit disabled value.  This is important for normal
    custom songs where DJSegment/DJStart/StingerStart should not be inherited
    from the original song and accidentally trigger early transitions.
    """
    end_sample = max(0, int(audio.sample_length) - 1)
    raw = dict(markers.positions or {})
    positions: dict[str, int] = {}

    for name in CONTROL_MARKERS:
        if name not in raw:
            continue
        try:
            value = int(raw[name])
        except Exception:
            continue
        if value == -1:
            positions[name] = -1
            continue
        if value < 0:
            continue
        positions[name] = max(0, min(value, end_sample))

    return positions

def _set_marker(sample: ET.Element, name: str, position: int) -> None:
    """Write a marker in both supported forms.

    Some RadioInfo files store markers as Sample attributes, e.g.

        <Sample TrackStart="0" End="123456" ... />

    Older versions of this tool only wrote child Marker nodes, e.g.

        <Marker Name="End" Position="123456" />

    If the game reads the attribute form, the old End attribute would remain
    unchanged and custom music could stop early even though the generated child
    marker looked correct.  Therefore we now update both forms.
    """
    value = str(int(position))

    # Attribute form used by many RadioInfo samples.
    sample.set(name, value)

    # Child-node form kept for compatibility with files/tools that use it.
    for marker in sample.findall("Marker"):
        if marker.get("Name") == name:
            marker.set("Position", value)
            return
    ET.SubElement(sample, "Marker", {"Name": name, "Position": value})


def patch_station_samples(
    tree: ET.ElementTree,
    station_name: str,
    patches: list[TrackPatch],
) -> ET.ElementTree:
    new_root = copy.deepcopy(tree.getroot())
    new_tree = ET.ElementTree(new_root)

    station = find_station(new_tree, station_name)
    samples = get_track_samples(station)

    if len(patches) > len(samples):
        raise ValueError(f"patch 数量 {len(patches)} 超出电台槽位数 {len(samples)}")

    for patch in patches:
        sample = samples[patch.slot_index]

        # 关键兼容规则：
        # Fmod Bank Tools 是在原 bank 内替换音频资源，通常不会创建新的 SoundName。
        # 因此这里必须保留 XML 模板中的原 SoundName，否则游戏会指向 bank 中不存在的资源，
        # 表现为电台没有变化、静音，或仍播放旧内容。
        #
        # sample.set("SoundName", patch.sound_name)  # 禁止默认改名
        sample.set("SampleLength", str(patch.audio.sample_length))
        sample.set("SampleRate", str(patch.audio.samplerate))
        sample.set("DisplayName", patch.display_name)
        sample.set("Artist", patch.artist)

        # v3.6.3 重要修复：
        # End / TrackStart / TrackLoopStart 等不仅要写 Marker 子节点，
        # 也必须写回 Sample 属性。否则游戏可能继续使用旧属性值。

        if "IsXCloudModeSafe" in sample.attrib:
            sample.set("IsXCloudModeSafe", "true")

        # v3.7.8 稳定性修复：
        # 只自动重写普通播放 / 比赛态 Marker。
        # DJSegment / DJStart / StingerStart 属于 DJ/转场控制点，
        # 未手动设置时保留原 XML，避免漫游模式 DJ 后切歌或叠歌。
        _clear_known_markers(sample, PLAYBACK_MARKERS)
        safe_positions = _safe_marker_positions(patch.markers, patch.audio)

        for marker_name in PLAYBACK_MARKERS:
            _set_marker(sample, marker_name, safe_positions[marker_name])

        for marker_name, value in _explicit_control_marker_positions(patch.markers, patch.audio).items():
            _set_marker(sample, marker_name, value)

    return new_tree


def write_xml(tree: ET.ElementTree, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
