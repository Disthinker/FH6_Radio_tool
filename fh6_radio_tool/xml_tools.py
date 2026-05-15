from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import AudioInfo, SegmentMarkers, StationInfo, TrackPatch
from .metadata_tools import TrackMetadata
from .segment_tools import MARKER_ORDER


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


def _set_marker(sample: ET.Element, name: str, position: int) -> None:
    for marker in sample.findall("Marker"):
        if marker.get("Name") == name:
            marker.set("Position", str(position))
            return
    ET.SubElement(sample, "Marker", {"Name": name, "Position": str(position)})


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

        if "IsXCloudModeSafe" in sample.attrib:
            sample.set("IsXCloudModeSafe", "true")

        for marker_name in MARKER_ORDER:
            value = patch.markers.positions.get(marker_name)
            if value is not None and int(value) >= 0:
                _set_marker(sample, marker_name, int(value))

    return new_tree


def write_xml(tree: ET.ElementTree, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
