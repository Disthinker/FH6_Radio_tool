from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from fh6_radio_tool.models import AudioInfo, SegmentMarkers, TrackPatch
from fh6_radio_tool.xml_tools import patch_station_samples


def _minimal_radioinfo() -> ET.ElementTree:
    xml = """
    <Radio Version="2">
      <RadioStations>
        <RadioStation Name="Horizon Opus" Number="9">
          <SampleList Type="Track">
            <Sample SoundName="HZ6_R9_BenjaminRimmer_Soaring" SampleLength="7196050" SampleRate="48000" DisplayName="Soaring" Artist="Benjamin Rimmer" IsXCloudModeSafe="true">
              <Marker Name="TrackStart" Position="0" />
              <Marker Name="DJDrop" Position="2243077" />
              <Marker Name="TrackDrop" Position="3441397" />
              <Marker Name="TrackLoopStart" Position="3441397" />
              <Marker Name="TrackLoopEnd" Position="7013049" />
              <Marker Name="DJSegment" Position="4741231" />
              <Marker Name="PostDrop" Position="6034933" />
              <Marker Name="PostRaceLoopStart" Position="6034933" />
              <Marker Name="PostRaceLoopEnd" Position="6895920" />
              <Marker Name="StingerStart" Position="7144615" />
              <Marker Name="DJStart" Position="7145615" />
              <Marker Name="End" Position="7196049" />
            </Sample>
          </SampleList>
        </RadioStation>
      </RadioStations>
    </Radio>
    """
    return ET.ElementTree(ET.fromstring(xml))


def _marker(sample: ET.Element, name: str) -> int:
    for marker in sample.findall("Marker"):
        if marker.get("Name") == name:
            return int(marker.get("Position", "0"))
    raise AssertionError(f"missing marker {name}")


def _patched_sample(markers: dict[str, int]) -> ET.Element:
    audio = AudioInfo(
        path=Path("replacement.wav"),
        filename="replacement.wav",
        samplerate=48000,
        channels=2,
        bits_per_sample=16,
        frames=9_421_686,
        duration_sec=9_421_686 / 48000,
    )
    patch = TrackPatch(
        slot_index=0,
        audio=audio,
        display_name="Replacement",
        artist="User",
        sound_name="",
        markers=SegmentMarkers(markers),
    )
    tree = patch_station_samples(_minimal_radioinfo(), "Horizon Opus", [patch])
    station = tree.getroot().find("RadioStations").find("RadioStation")
    return station.find("SampleList").find("Sample")


class DjStingerMarkerTests(unittest.TestCase):
    def test_legacy_minus_one_controls_auto_generate_safe_markers(self) -> None:
        sample = _patched_sample(
            {
                "TrackStart": 0,
                "TrackDrop": 0,
                "TrackLoopStart": 0,
                "TrackLoopEnd": -1,
                "PostDrop": 0,
                "PostRaceLoopStart": 0,
                "PostRaceLoopEnd": -1,
                "DJSegment": -1,
                "StingerStart": -1,
                "DJStart": -1,
                "End": 9_421_685,
            }
        )

        self.assertEqual(sample.get("SampleLength"), "9421686")
        self.assertEqual(_marker(sample, "End"), 9_421_685)
        self.assertGreater(_marker(sample, "DJDrop"), 0)
        self.assertNotEqual(_marker(sample, "DJDrop"), 2_243_077)
        self.assertGreater(_marker(sample, "DJSegment"), 0)
        self.assertGreater(_marker(sample, "StingerStart"), 0)
        self.assertEqual(_marker(sample, "DJStart") - _marker(sample, "StingerStart"), 1000)
        self.assertLess(_marker(sample, "DJStart"), _marker(sample, "End"))

    def test_manual_djstart_repairs_stinger_offset(self) -> None:
        sample = _patched_sample({"TrackStart": 0, "DJStart": 5_000_000, "StingerStart": 123})

        self.assertEqual(_marker(sample, "DJStart"), 5_000_000)
        self.assertEqual(_marker(sample, "StingerStart"), 4_999_000)

    def test_advanced_disable_writes_minus_one(self) -> None:
        sample = _patched_sample({"TrackStart": 0, "DJSegment": -2, "DJStart": -2})

        self.assertEqual(_marker(sample, "DJSegment"), -1)
        self.assertEqual(_marker(sample, "StingerStart"), -1)
        self.assertEqual(_marker(sample, "DJStart"), -1)


if __name__ == "__main__":
    unittest.main()
