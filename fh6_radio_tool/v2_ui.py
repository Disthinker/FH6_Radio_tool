from __future__ import annotations

import hashlib
import csv
import json
import os
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QProgressBar, QSlider, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QHBoxLayout, QWidget, QScrollArea, QStackedWidget, QInputDialog, QSizePolicy
)

from .async_tools import BackgroundTask
from .bank_tools import choose_banks, make_bank_plan, write_bank_plan_outputs
from .ffmpeg_tools import AudioNormalizationReport, describe_audio_normalization_report, find_ffmpeg, run_ffmpeg_normalize, safe_stem
from .metadata_tools import guess_display_artist_from_filename
from .models import AudioInfo, SegmentMarkers
from .order_tools import (
    FMOD_EXTRACT_TEMPLATE_DIR_NAME, FMOD_READY_WAV_DIR_NAME, TRACK_ORDER_FILE_NAME,
    create_fmod_rebuild_workspace, ensure_track_order_file, import_fmod_extract_folder,
    parse_extract_template, read_track_order, write_track_order, validate_selected_replacements,
    write_fmod_sound_inventory, write_replacement_plan, station_sample_rows,
)
from .project_tools import (
    _patch_xml_by_track_order, ensure_project_dirs, project_backup_dir,
    project_output_dir, project_work_dir,
)
from .segment_tools import MARKER_ORDER, SEGMENTS_FILE_NAME, load_segments, markers_from_json, markers_to_json, save_segments
from .v2_deploy_tools import create_backup_snapshot, ensure_initial_state_snapshot, restore_initial_state, restore_snapshot
from .v2_game_tools import resolve_fmod_bank_root, scan_game_root, write_scan_report
from .v2_loop_tools import analyze_loop_candidates, markers_from_candidate
from .loop_engine.scene_preview import build_scene_preview_plan
from .fmod_automation import (
    collect_rebuilt_banks as fmod_collect_rebuilt_banks,
    bank_contains_fsb_audio, bank_preflight_message, is_pywinauto_available, pywinauto_status,
    launch_and_optionally_trigger, launch_trigger_and_wait, layout_from_exe,
    prepare_extract_job, prepare_rebuild_job, copy_banks_to_tool_bank_dir,
)
from .v2_state_store import APP_VERSION, StateStore, TrackProfile
from .marker_import_tools import MarkerImportRow, normalize_match_text, read_marker_import_file, write_marker_import_template, IMPORT_COLUMNS, EXPORT_COLUMNS
from .marker_normalization import normalize_track_markers_for_prepared_audio
from .wav_preview_player import WavPreviewPlayer
from .waveform_tools import DEFAULT_WAVEFORM_BINS, load_or_build_waveform
from .waveform_widget import WaveformWidget
from .wav_tools import list_audio_candidates, natural_key, read_wav_info, validate_wav
from .xml_tools import find_station, get_track_samples, list_station_infos, parse_xml, station_info_from_node, write_xml
from .runtime_tools import runtime_root, bundled_resource_root, is_frozen_app


# Temporary station-slot profile data derived from the full-radio developer
# matching test.  These values describe the difference between RadioInfo XML
# song entries and FMOD audio slots that can actually be rebuilt/replaced.
# The runtime will prefer a fresh work/dev_all_station_match_test summary when
# available, but these defaults let normal users see the limitation before they
# run the development diagnostic once.
KNOWN_MULTI_TRACK_COMPLETE_STATIONS = {"Horizon Bass Arena"}

# v3.0.33: FH6 main-menu/press-start music is a fixed known bank.
# Users should only choose the replacement audio; the tool resolves this bank
# automatically from the selected game root / FMODBanks tree.
MAIN_MENU_PRESS_START_BANK = "GLB_RadioPressStart.assets.bank"
COMPACT_PROGRESS_PREFIX = "__FH6_COMPACT_PROGRESS__|"


def local_logical_cpu_count() -> int:
    try:
        return max(1, int(os.cpu_count() or 1))
    except Exception:
        return 1


def recommended_safe_thread_count() -> int:
    logical = local_logical_cpu_count()
    if logical <= 2:
        return 1
    if logical <= 4:
        return max(1, logical - 1)
    return max(1, logical - 2)


def dev_bank_role_from_key(bank_key: str) -> str:
    key = (bank_key or "").lower()
    if "_tracks_" in key and key.startswith("r"):
        return "radio_tracks"
    if key == "glb_radio_3d.assets" or key.startswith("glb_radio_3d"):
        return "glb_radio_3d"
    if key.startswith("glb_radiopressstart"):
        return "press_start"
    if key.startswith("glb_videoplayer"):
        return "video_player"
    if key.startswith("glb_snapshots"):
        return "snapshot"
    if "dj" in key or "stinger" in key or "jingle" in key:
        return "dj_or_stinger_hint"
    if "cutscene" in key or "cinematic" in key or "showcase" in key:
        return "cinematic"
    if "music" in key or "radio" in key:
        return "music_hint"
    return "other"

# Temporary station-slot profile data derived from earlier diagnostics.
# v3.0.17 changes the station model from "one station = one CU1 bank" to
# "one station = all same-station R*_Tracks_* banks".  This means R1/R2 Disk
# banks are supplementary track banks, not fallbacks or XML-only rows.  Keep
# only special stations that still need player-facing limits here.
KNOWN_STATION_SLOT_PROFILES: dict[str, dict[str, object]] = {
    # R1 has 36 XML rows, but two pairs are duplicate radio/ID aliases for
    # the Disk-bank songs.  Real in-game radio display/playback follows the
    # non-ID rows; the *_ID rows behave like internal aliases and should not be
    # exposed as normal player-facing replacement slots.
    "Horizon Pulse": {
        "xml_tracks": 36,
        "fmod_audio_slots": 34,
        "non_replaceable_slots": [31, 33],
        "status": "multi_track_disk_non_id_rows",
        "source": "built_in_r1_disk_duplicate_profile_v3024",
    },
    "Streamer Mode": {
        "xml_tracks": 82,
        "fmod_audio_slots": 32,
        "non_replaceable_slots": [6, 10, 17, 24, 25, 27, 30, 32, 33, 34, 35, 36, 37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 77, 78, 79, 80, 81],
        "status": "special",
        "source": "built_in_dev_match_2026_05",
    },
}


def _sound_variant_suffix(sound_name: str) -> str:
    """Return player-facing duplicate suffix marker for FH6 radio SoundName.

    Some RadioInfo rows appear twice with the same DisplayName/Artist.  In the
    cases verified so far, the plain SoundName row is the normal radio row,
    while *_ID / *_LI / *_FI / *_DJMontage rows are internal, shortened, or
    special-context aliases.  If both exist, hide the suffixed alias from the
    normal replacement UI so users do not replace a row that the in-game radio
    may not actually use.
    """
    text = str(sound_name or "")
    lower = text.lower()
    for suffix in ("_id", "_li", "_fi", "_djmontage"):
        if lower.endswith(suffix):
            return suffix[1:]
    return ""


def _duplicate_variant_hidden_slots(rows: list[dict]) -> list[int]:
    """Hide duplicate *_ID/*_LI/*_FI rows when a plain row with same title exists.

    This is intentionally data-driven instead of station-specific.  R1 proved
    that *_ID aliases should not be exposed when the plain row exists.  Streamer
    Mode contains similar duplicate pairs such as EIYAA/EIYAA_LI and
    EnchantingStranger/EnchantingStranger_ID.
    """
    from collections import defaultdict

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows or []:
        title = str(row.get("original_display_name") or row.get("display_name") or "").strip().casefold()
        artist = str(row.get("original_artist") or row.get("artist") or "").strip().casefold()
        if not title:
            continue
        groups[(title, artist)].append(row)

    hidden: set[int] = set()
    for _key, items in groups.items():
        if len(items) < 2:
            continue
        plain = [r for r in items if not _sound_variant_suffix(str(r.get("sound_name") or ""))]
        variants = [r for r in items if _sound_variant_suffix(str(r.get("sound_name") or ""))]
        if not plain or not variants:
            continue
        # Keep all plain rows visible, hide only special aliases.  Do not hide a
        # suffixed row when it is the only row for that song, because some FH6
        # songs only exist as *_FI or *_LI in their station XML.
        for row in variants:
            try:
                hidden.add(int(row.get("slot_index")))
            except Exception:
                pass
    return sorted(hidden)



def _parse_slot_list_from_dev_message(message: str, key: str) -> list[int]:
    text = str(message or "")
    if key not in text:
        return []
    try:
        part = text.split(key, 1)[1]
        part = part.split(";", 1)[0]
    except Exception:
        return []
    out: list[int] = []
    for token in part.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except Exception:
            pass
    return out


class MainThreadTaskBridge(QObject):
    """Forward worker-thread signals back to the GUI thread.

    PySide can execute plain Python callbacks in the sender thread when a signal
    is connected directly to a local function.  Any QMessageBox, widget enable/
    disable, cursor change, or progress-bar update must be handled on the main
    Qt thread.  This bridge lives in the GUI thread and exposes queued slots so
    BackgroundTask never touches UI objects directly or indirectly.
    """

    progress_ready = Signal(int, str)
    success_ready = Signal(object)
    failure_ready = Signal(str)
    finished_ready = Signal()

    @Slot(int, str)
    def on_progress(self, value: int, message: str = "") -> None:
        self.progress_ready.emit(value, message)

    @Slot(object)
    def on_success(self, result: object) -> None:
        self.success_ready.emit(result)

    @Slot(str)
    def on_failure(self, trace_text: str) -> None:
        self.failure_ready.emit(trace_text)

    @Slot()
    def on_finished(self) -> None:
        self.finished_ready.emit()


def track_key_for_path(path: Path) -> str:
    path = Path(path)
    try:
        st = path.stat()
        raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        raw = str(path)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def set_item(table: QTableWidget, row: int, col: int, text: object, *, data: object | None = None) -> None:
    item = QTableWidgetItem(str(text if text is not None else ""))
    if data is not None:
        item.setData(Qt.UserRole, data)
    table.setItem(row, col, item)


def set_check_item(table: QTableWidget, row: int, col: int, checked: bool = False, *, data: object | None = None) -> None:
    item = QTableWidgetItem("")
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
    item.setTextAlignment(Qt.AlignCenter)
    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    if data is not None:
        item.setData(Qt.UserRole, data)
    table.setItem(row, col, item)


def format_sample_time(sample: int, samplerate: int) -> str:
    if samplerate <= 0:
        return str(sample)
    sec = max(0.0, float(sample) / float(samplerate))
    m = int(sec // 60)
    s = sec - m * 60
    return f"{int(sample)} ({m:02d}:{s:05.2f})"


def safe_default_marker_values(max_sample: int) -> dict[str, int]:
    """Player-safe default markers for normal custom songs.

    The UI intentionally keeps loop end fields at -1 until the user applies a
    loop candidate or imports markers.  End stays locked to the real audio end
    so a mistaken LoopEnd value will not shorten the whole track.
    """
    end = max(0, int(max_sample))
    return {
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
        "End": end,
    }


NO_LOOP_SENTINEL_MARKERS = {
    "TrackLoopEnd",
    "PostRaceLoopEnd",
    "DJSegment",
    "StingerStart",
    "DJStart",
}


def marker_values_for_save(markers: dict[str, int]) -> dict[str, int]:
    """Persist safe marker values, including explicit -1 no-loop sentinels.

    For normal custom songs users often want TrackLoopEnd/PostRaceLoopEnd and
    DJ/Stinger markers to stay disabled.  Older code dropped all negative
    values before saving, so the XML writer later regenerated LoopEnd as End,
    which made the UI say "no loop" while the generated XML still contained
    a full-length loop.  Keep -1 for the specific markers where -1 means
    "disabled / do not trigger".
    """
    result: dict[str, int] = {}
    for name, value in markers.items():
        value_i = int(value)
        if value_i >= 0 or (name in NO_LOOP_SENTINEL_MARKERS and value_i == -1):
            result[name] = value_i
    return result


def marker_source_info_for_profile(
    profile: TrackProfile,
    normalize_report: AudioNormalizationReport | None = None,
) -> AudioInfo | None:
    sample_rate = int(profile.sample_rate or 0)
    sample_length = int(profile.sample_length or 0)
    if sample_rate > 0 and sample_length > 0:
        return AudioInfo(
            path=Path(profile.source_path),
            filename=profile.filename or Path(profile.source_path).name,
            samplerate=sample_rate,
            channels=2,
            bits_per_sample=16,
            frames=sample_length,
            duration_sec=sample_length / sample_rate,
        )

    source = Path(profile.source_path)
    if source.suffix.lower() in (".wav", ".wave"):
        try:
            return read_wav_info(source)
        except Exception:
            pass

    if normalize_report is not None:
        estimated_length = normalize_report.source_sample_length_estimate()
        if estimated_length and normalize_report.source_sample_rate:
            return AudioInfo(
                path=source,
                filename=profile.filename or source.name,
                samplerate=int(normalize_report.source_sample_rate),
                channels=int(normalize_report.source_channels or 2),
                bits_per_sample=16,
                frames=int(estimated_length),
                duration_sec=float(normalize_report.source_duration_sec or 0.0),
            )
    return None


def app_icon_path() -> Path | None:
    """Find the bundled application icon for the window/taskbar."""
    candidates = [
        runtime_root() / "resources" / "app.ico",
        bundled_resource_root() / "resources" / "app.ico",
        Path(__file__).resolve().parents[1] / "resources" / "app.ico",
    ]
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            pass
    return None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        icon_path = app_icon_path()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowTitle(f"FH6 Radio Tool v{APP_VERSION}")
        # v2.4.7: use a more reasonable initial height.  v2.4.6 enlarged
        # the log area but also made the whole window too tall on some
        # displays.  Keep the window roomy enough while allowing users to
        # shrink it.
        self.resize(1420, 780)
        self.setMinimumSize(1050, 560)
        # v2.4.3: revert to the earlier native/system font look.  Avoid
        # forcing Microsoft YaHei UI everywhere because it looked bulky on
        # some Windows systems and made the UI visually inconsistent.
        # v2.4.5: keep the native system font and use spacing/size rather
        # than heavy font overrides.  This avoids the bulky, inconsistent look
        # reported on Windows while keeping the workflow clean and readable.
        self.setStyleSheet("""
            QGroupBox { margin-top: 6px; padding-top: 8px; }
            QLabel#SectionTitle { padding: 0px 0px 2px 0px; font-weight: 500; }
            QLabel#StepTitle { font-size: 15px; font-weight: 600; padding: 4px 0px; }
            QLabel#CompactHint { color: #555; padding-top: 2px; }
            QPushButton { padding: 4px 10px; min-height: 26px; }
            QPushButton#PrimaryAction { padding: 6px 14px; min-height: 32px; font-weight: 500; }
            QPushButton#DangerAction { padding: 6px 14px; min-height: 32px; font-weight: 500; }
            QPushButton#BackupAction { padding: 4px 10px; min-height: 26px; }
            QPushButton#SmallAction { padding: 3px 8px; min-height: 24px; }
            QPushButton#SideTab { padding: 5px 12px; min-height: 30px; font-weight: 500; text-align: left; }
            QPushButton#LogTab { padding: 5px 3px; min-width: 32px; max-width: 40px; min-height: 92px; font-weight: 500; }
            QLineEdit, QComboBox { min-height: 25px; }
            QTableWidget { gridline-color: #dddddd; }
            QHeaderView::section { padding: 4px 6px; }
        """)

        self.store = StateStore(project_work_dir() / "fh6_radio_tool_v2.sqlite3")
        self.player = WavPreviewPlayer(self)
        self.current_xml: Path | None = None
        self.station_infos = []
        self.audio_paths: list[Path] = []
        self._populating_music_table = False
        self.loop_candidates = []
        self.current_loop_audio: Path | None = None
        self.xml_candidates: list[Path] = []
        self._busy = False
        self._task_thread: QThread | None = None
        self._task_worker: BackgroundTask | None = None
        self._task_bridge: MainThreadTaskBridge | None = None
        self._task_title = ""
        self._task_started_at = 0.0
        self._compact_progress_lines: list[str] = []
        self._last_compact_progress = ""
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(1000)
        self._heartbeat_timer.timeout.connect(self._on_task_heartbeat)

        self.game_root_edit = QLineEdit(str(self.store.get_setting("game_root", "")))
        self.music_dir_edit = QLineEdit(str(self.store.get_setting("music_dir", "")))
        self.xml_edit = QLineEdit(str(self.store.get_setting("xml_path", "")))
        self.xml_edit.setReadOnly(True)
        self.extract_dir_edit = QLineEdit(str(project_work_dir() / FMOD_EXTRACT_TEMPLATE_DIR_NAME))
        self.bank_root_edit = QLineEdit(str(self.store.get_setting("auto_bank_root", "")))
        self.bank_root_edit.setReadOnly(True)
        self.bank_root_edit.setPlaceholderText("扫描游戏根目录后自动定位 media/Audio/FMODBanks")
        self.fmod_tool_edit = QLineEdit(str(self.store.get_setting("fmod_tool", "")))
        # v3.0.37: one-click / package workflows always use automatic Fmod Bank Tools
        # control.  The old user-toggle caused a confusing legacy path where unchecking
        # it made the app ask for setup_env.bat/pywinauto even in the portable EXE.
        # Keep the QCheckBox object only for backward compatibility with saved UI code,
        # but hide it and force it on internally.
        self.auto_fmod_click_check = QCheckBox("自动控制 Fmod Bank Tools（已内置启用）")
        self.auto_fmod_click_check.setChecked(True)
        self.auto_fmod_click_check.setEnabled(False)
        self.auto_fmod_click_check.setVisible(False)
        self.store.set_setting("fmod_auto_click", True)

        # v3.0.33: optional main-menu / press-start music replacement workflow.
        # The target bank is fixed and known: GLB_RadioPressStart.assets.bank.
        # Users only choose the replacement audio; the tool resolves the bank
        # automatically from the selected game root / FMODBanks tree.  This is
        # intentionally separate from the radio XML slot workflow because the
        # main-menu music is not a RadioInfo XML station row.
        self.main_menu_bank_edit = QLineEdit(MAIN_MENU_PRESS_START_BANK)
        self.main_menu_bank_edit.setReadOnly(True)
        self.main_menu_bank_edit.setPlaceholderText("自动从游戏目录定位 GLB_RadioPressStart.assets.bank")
        self.main_menu_audio_edit = QLineEdit(str(self.store.get_setting("main_menu_audio_path", "")))
        self.main_menu_audio_edit.setPlaceholderText("选择要作为主菜单音乐的新音频文件")
        self.main_menu_mode_combo = QComboBox()
        self.main_menu_mode_combo.addItem("自动替换该 bank 内唯一音乐音频", "single")

        self.ui_language_combo = QComboBox()
        self.ui_language_combo.addItem("中文 / Chinese", "zh")
        self.ui_language_combo.addItem("English / 英文", "en")
        saved_ui_lang = str(self.store.get_setting("ui_language", "zh") or "zh")
        ui_idx = self.ui_language_combo.findData(saved_ui_lang)
        if ui_idx >= 0:
            self.ui_language_combo.setCurrentIndex(ui_idx)

        self.game_language_combo = QComboBox()
        for label, code in (
            ("自动选择", "auto"),
            ("简体中文 / Chinese (CN)", "cn"),
            ("English (EN)", "en"),
            ("繁體中文 / Chinese Traditional (TW)", "tw"),
            ("日本語 (JA)", "ja"),
            ("한국어 (KO)", "ko"),
            ("Deutsch (DE)", "de"),
            ("Français (FR)", "fr"),
            ("Español (ES)", "es"),
            ("Italiano (IT)", "it"),
            ("Português (PT)", "pt"),
            ("Русский (RU)", "ru"),
        ):
            self.game_language_combo.addItem(label, code)
        saved_game_lang = str(self.store.get_setting("game_language", "auto") or "auto")
        lang_idx = self.game_language_combo.findData(saved_game_lang)
        if lang_idx >= 0:
            self.game_language_combo.setCurrentIndex(lang_idx)

        self.station_combo = QComboBox()
        self.station_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.station_combo.setMinimumContentsLength(34)
        self.station_combo.setMinimumWidth(420)
        self.station_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slot_table = QTableWidget(0, 10)
        self.slot_table.setHorizontalHeaderLabels([
            "替换\n全选", "Slot", "原曲名", "Artist", "SoundName", "SampleLength", "SampleRate", "已分配新曲", "Markers", "状态"
        ])
        self.slot_table.horizontalHeader().setStretchLastSection(True)
        self.slot_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.slot_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.slot_table.horizontalHeader().sectionClicked.connect(self.on_slot_header_clicked)

        self.music_table = QTableWidget(0, 8)
        self.music_table.setHorizontalHeaderLabels(["选择\n全选", "文件名", "Artist", "格式", "采样率", "时长", "已保存设置", "路径"])
        self.music_table.horizontalHeader().setStretchLastSection(True)
        self.music_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.music_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.music_table.horizontalHeader().sectionClicked.connect(self.on_music_header_clicked)

        self.loop_audio_combo = QComboBox()
        self.candidate_table = QTableWidget(0, 5)
        self.candidate_table.setHorizontalHeaderLabels(["#", "LoopStart", "LoopEnd", "Score", "来源"])
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        self.candidate_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.candidate_table.setSelectionMode(QTableWidget.SingleSelection)

        self.candidate_combo = QComboBox()
        self.candidate_summary_label = QLabel("尚未分析候选。")
        self.candidate_summary_label.setWordWrap(True)
        self.preview_scenario_combo = QComboBox()
        self.preview_scenario_combo.addItem("漫游模式：Track Loop 循环", "roam_loop")
        self.preview_scenario_combo.addItem("比赛开始：TrackDrop/TrackStart → TrackLoop", "race_start")
        self.preview_scenario_combo.addItem("比赛进行：TrackLoop 循环", "race_loop")
        self.preview_scenario_combo.addItem("冲线：PostDrop 前后预览", "finish")
        self.preview_scenario_combo.addItem("冲线后：PostRaceLoop 循环", "post_loop")
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.waveform = WaveformWidget(self)
        self.position_label = QLabel("0 / 0")
        self.marker_target_combo = QComboBox()
        self.preview_seconds_spin = QSpinBox()
        self.preview_seconds_spin.setRange(1, 30)
        self.preview_seconds_spin.setValue(5)
        self._updating_slider = False
        self._slider_dragging = False

        self.marker_spins: dict[str, QSpinBox] = {}
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("运行日志会显示在这里。")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.compact_progress_label = QLabel("等待任务。")
        self.compact_progress_label.setObjectName("CompactHint")
        self.compact_progress_label.setWordWrap(True)
        self.compact_progress_label.setMinimumHeight(36)
        self.compact_progress_label.setMaximumHeight(60)

        central = QWidget()
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        # v2.6.0: move the large setup block out of the always-visible workflow.
        # Users configure paths once, then hide it from the Settings menu so the
        # main area is reserved for replacement / loop / output work.
        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)
        main_layout.addWidget(self._build_setup_strip())

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.step_title_label = QLabel()
        self.step_title_label.setObjectName("StepTitle")
        self.btn_prev_step = QPushButton("上一步")
        self.btn_prev_step.setMinimumWidth(96)
        self.btn_prev_step.clicked.connect(self.go_prev_step)
        self.btn_next_step = QPushButton("下一步")
        self.btn_next_step.setMinimumWidth(96)
        self.btn_next_step.setObjectName("PrimaryAction")
        self.btn_next_step.clicked.connect(self.go_next_step)
        nav.addWidget(self.step_title_label)
        nav.addStretch(1)
        nav.addWidget(self.btn_prev_step)
        nav.addWidget(self.btn_next_step)
        main_layout.addLayout(nav)

        self.steps = QStackedWidget()
        self.steps.addWidget(self._build_assign_tab())
        self.steps.addWidget(self._build_loop_tab())
        self.steps.addWidget(self._build_deploy_tab())
        self.steps.currentChanged.connect(self.update_step_navigation)
        main_layout.addWidget(self.steps, 1)

        # v2.6.1: keep the right sidebar useful even before any operation.
        # It is split into a short guide and the runtime log, and can be hidden
        # from a persistent side tab to give the workflow more horizontal space.
        self.log_panel = QWidget()
        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(6, 0, 0, 0)
        log_layout.setSpacing(6)
        self.guide_box = QGroupBox("快速教程 / Quick Guide")
        guide_layout = QVBoxLayout(self.guide_box)
        guide_layout.setContentsMargins(8, 8, 8, 8)
        self.guide_label = QLabel()
        self.guide_label.setWordWrap(True)
        self.guide_label.setTextFormat(Qt.PlainText)
        guide_layout.addWidget(self.guide_label)
        self.log_runtime_box = QGroupBox("运行日志 / Runtime Log")
        runtime_layout = QVBoxLayout(self.log_runtime_box)
        runtime_layout.setContentsMargins(8, 8, 8, 8)
        self.log_title_label = QLabel("日志 / Log")
        self.log_title_label.setVisible(False)
        self.log_box.setMinimumWidth(280)
        self.log_box.setMinimumHeight(120)
        runtime_layout.addWidget(self.log_box, 1)
        runtime_layout.addWidget(self.progress)
        runtime_layout.addWidget(self.compact_progress_label)
        self.log_panel.setMinimumWidth(310)
        log_layout.addWidget(self.guide_box)
        log_layout.addWidget(self.log_runtime_box, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(main_panel)
        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([930, 350])
        root_layout.addWidget(splitter, 1)
        self.btn_toggle_log_tab = QPushButton("日志\nLog")
        self.btn_toggle_log_tab.setObjectName("LogTab")
        self.btn_toggle_log_tab.setToolTip("显示/隐藏右侧教程与日志栏 / Show or hide the guide and log sidebar")
        self.btn_toggle_log_tab.clicked.connect(self.toggle_log_panel)
        root_layout.addWidget(self.btn_toggle_log_tab)
        self.setCentralWidget(central)
        self._build_top_menu()
        self._auto_hide_setup_if_ready()
        self.update_step_navigation()

        self.station_combo.currentIndexChanged.connect(self.reload_slots)
        self.music_table.itemSelectionChanged.connect(self.on_music_selection_changed)
        self.music_table.itemChanged.connect(self.on_music_table_item_changed)
        self.loop_audio_combo.currentIndexChanged.connect(self.on_loop_audio_changed)
        self.candidate_combo.currentIndexChanged.connect(self.on_candidate_combo_changed)
        self.candidate_table.itemSelectionChanged.connect(self.on_candidate_table_selection_changed)
        self.seek_slider.sliderPressed.connect(self.begin_seek_drag)
        self.seek_slider.sliderMoved.connect(self.on_seek_slider_moved)
        self.seek_slider.sliderReleased.connect(self.finish_seek_drag)
        self.waveform.seekRequested.connect(self.seek_to_sample)
        self.player.positionChanged.connect(self.on_player_position_changed)
        self.marker_target_combo.currentIndexChanged.connect(self.refresh_waveform_markers)
        self.ui_language_combo.currentIndexChanged.connect(self.on_ui_language_changed)
        self.game_language_combo.currentIndexChanged.connect(self.on_game_language_changed)
        self.apply_ui_language()
        self.apply_table_layout()

        # Restore last paths when possible.
        if self.xml_edit.text().strip():
            self.load_xml(Path(self.xml_edit.text().strip()), quiet=True)
        if self.music_dir_edit.text().strip():
            self.scan_music_dir(quiet=True)

    def _build_setup_strip(self) -> QWidget:
        self.setup_strip = QWidget()
        strip = QVBoxLayout(self.setup_strip)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(4)
        self.btn_toggle_setup_tab = QPushButton("设置 / Settings · 显示或隐藏路径设置")
        self.btn_toggle_setup_tab.setObjectName("SideTab")
        self.btn_toggle_setup_tab.setToolTip("显示/隐藏路径设置 / Show or hide path settings")
        self.btn_toggle_setup_tab.clicked.connect(self.toggle_setup_panel)
        strip.addWidget(self.btn_toggle_setup_tab)
        strip.addWidget(self._build_path_group(), 1)
        return self.setup_strip

    def _build_path_group(self) -> QWidget:
        self.path_box = QGroupBox("路径设置 / Path Settings")
        grid = QGridLayout(self.path_box)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 0)

        self.btn_game = QPushButton("选择游戏目录")
        self.btn_game.clicked.connect(self.browse_game_root)
        self.btn_music = QPushButton("选择音乐目录")
        self.btn_music.clicked.connect(self.browse_music_dir)
        self.btn_backup = QPushButton("创建备份点")
        self.btn_backup.setObjectName("BackupAction")
        self.btn_backup.clicked.connect(self.backup_current_game_files)
        self.btn_restore = QPushButton("恢复备份/初始状态")
        self.btn_restore.setObjectName("DangerAction")
        self.btn_restore.clicked.connect(self.restore_from_manifest)
        for b in (self.btn_game, self.btn_music, self.btn_backup, self.btn_restore):
            b.setMinimumWidth(144)

        self.lbl_game_root = QLabel("游戏根目录")
        self.lbl_music_dir = QLabel("音乐目录")
        self.lbl_xml = QLabel("当前 XML")
        self.lbl_ui_language = QLabel("界面语言 / UI Language")
        self.lbl_game_language = QLabel("游戏语言 / Game XML")
        self.ui_language_combo.setMaximumWidth(190)
        self.game_language_combo.setMaximumWidth(230)

        grid.addWidget(self.lbl_game_root, 0, 0)
        grid.addWidget(self.game_root_edit, 0, 1)
        grid.addWidget(self.btn_game, 0, 2)
        grid.addWidget(self.btn_backup, 0, 3)

        grid.addWidget(self.lbl_music_dir, 1, 0)
        grid.addWidget(self.music_dir_edit, 1, 1)
        grid.addWidget(self.btn_music, 1, 2)
        grid.addWidget(self.btn_restore, 1, 3)

        grid.addWidget(self.lbl_xml, 2, 0)
        grid.addWidget(self.xml_edit, 2, 1, 1, 3)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        lang_row.addWidget(self.lbl_ui_language)
        lang_row.addWidget(self.ui_language_combo)
        lang_row.addSpacing(18)
        lang_row.addWidget(self.lbl_game_language)
        lang_row.addWidget(self.game_language_combo)
        lang_row.addStretch(1)
        self.btn_hide_setup = QPushButton("完成设置并隐藏")
        self.btn_hide_setup.setMinimumWidth(150)
        self.btn_hide_setup.clicked.connect(lambda: (self.path_box.setVisible(False), self.update_setup_toggle_text()))
        lang_row.addWidget(self.btn_hide_setup)
        grid.addLayout(lang_row, 3, 0, 1, 4)
        return self.path_box

    def _build_top_menu(self) -> None:
        # v2.6.2: remove the separate top Settings menu. The persistent setup
        # strip button now serves as the single settings entry point, which is
        # easier to discover and avoids duplicated controls.
        self.menuBar().setVisible(False)
        self.action_toggle_setup = None
        self.action_show_log = None

    def update_setup_toggle_text(self) -> None:
        if not hasattr(self, 'btn_toggle_setup_tab'):
            return
        en = self.ui_lang() == "en"
        shown = self.path_box.isVisible() if hasattr(self, 'path_box') else True
        if en:
            self.btn_toggle_setup_tab.setText("Settings · hide path setup" if shown else "Settings · show path setup")
        else:
            self.btn_toggle_setup_tab.setText("设置 / Settings · 隐藏路径设置" if shown else "设置 / Settings · 显示路径设置")

    def toggle_setup_panel(self) -> None:
        self.path_box.setVisible(not self.path_box.isVisible())
        self.update_setup_toggle_text()

    def dev_thread_hint_text(self) -> str:
        logical = local_logical_cpu_count()
        safe = recommended_safe_thread_count()
        current = self._dev_max_threads() if hasattr(self, "dev_thread_spin") else safe
        if self.ui_lang() == "en":
            return f"Local logical threads: {logical}; recommended safe limit: {safe}; developer tasks and Fmod Bank Tools CPUThreads will use at most: {current}. Fmod GUI launches are kept serial to avoid opening multiple Bank Tools instances."
        return f"本机逻辑线程: {logical}；建议安全上限: {safe}；当前开发任务和 Fmod Bank Tools CPUThreads 最多使用: {current}。Fmod GUI 启动会自动串行，避免同时打开多个 Bank Tools。"

    def _dev_max_threads(self) -> int:
        try:
            value = int(self.dev_thread_spin.value())
        except Exception:
            value = int(self.store.get_setting("dev_max_threads", recommended_safe_thread_count()) or recommended_safe_thread_count())
        return max(1, min(local_logical_cpu_count(), value))

    def _fmod_cpu_threads(self) -> int:
        """Thread limit used for external Fmod Bank Tools config.ini."""
        return self._dev_max_threads()

    def on_dev_thread_count_changed(self, value: int) -> None:
        value = max(1, min(local_logical_cpu_count(), int(value)))
        self.store.set_setting("dev_max_threads", value)
        safe = recommended_safe_thread_count()
        if hasattr(self, "dev_thread_hint"):
            self.dev_thread_hint.setText(self.dev_thread_hint_text())
        if value > safe:
            self.log(self.log_text(
                f"[DEV][THREAD][WARN] 当前设置 {value} 高于建议安全上限 {safe}。长时间全 bank 扫描可能影响系统响应。",
                f"[DEV][THREAD][WARN] Current setting {value} is above the recommended safe limit {safe}. A long full-bank scan may affect system responsiveness.",
            ))

    def _dev_radioinfo_xml_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for p in self.xml_candidates or []:
            try:
                pp = Path(p)
                if pp.exists():
                    candidates.append(pp)
            except Exception:
                pass
        if self.current_xml and Path(self.current_xml).exists():
            candidates.append(Path(self.current_xml))
        game_root_text = self.game_root_edit.text().strip() if hasattr(self, "game_root_edit") else ""
        if game_root_text:
            root = Path(game_root_text)
            search_roots = [root / "media" / "Audio", root]
            for base in search_roots:
                if not base.exists():
                    continue
                try:
                    for p in base.rglob("RadioInfo*.xml"):
                        candidates.append(p)
                except Exception:
                    pass
        # Keep deterministic order and de-duplicate by resolved path when possible.
        out: list[Path] = []
        seen: set[str] = set()
        for p in candidates:
            try:
                key = str(p.resolve()).lower()
            except Exception:
                key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return sorted(out, key=lambda x: x.as_posix().lower())

    def _dev_filter_extractable_banks(self, banks: list[Path], report, label: str) -> tuple[list[Path], list[dict[str, object]]]:
        banks = [Path(b) for b in banks]
        max_workers = self._dev_max_threads()
        report(4, COMPACT_PROGRESS_PREFIX + self.ui_text(
            f"预检查 bank：0/{len(banks)}，最大线程 {max_workers}。",
            f"Prechecking banks: 0/{len(banks)}, max threads {max_workers}.",
        ))
        rows: list[dict[str, object]] = []
        extractable: list[Path] = []
        if not banks:
            return extractable, rows
        def check_one(bank: Path) -> dict[str, object]:
            has_fsb = False
            error = ""
            try:
                has_fsb = bool(bank_contains_fsb_audio(bank))
            except Exception as exc:
                error = str(exc)
            try:
                st = bank.stat()
                size = int(st.st_size)
            except Exception:
                size = -1
            return {
                "bank_name": bank.name,
                "bank_path": str(bank),
                "bank_key": bank.stem.lower(),
                "size_bytes": size,
                "size_mb": f"{(size / 1024 / 1024):.3f}" if size >= 0 else "",
                "precheck_has_fsb": 1 if has_fsb else 0,
                "precheck_error": error,
            }
        done_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_map = {ex.submit(check_one, b): b for b in banks}
            for fut in as_completed(future_map):
                bank = future_map[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    row = {"bank_name": bank.name, "bank_path": str(bank), "bank_key": bank.stem.lower(), "size_bytes": -1, "size_mb": "", "precheck_has_fsb": 0, "precheck_error": str(exc)}
                rows.append(row)
                if int(row.get("precheck_has_fsb") or 0):
                    extractable.append(bank)
                done_count += 1
                if done_count == 1 or done_count % 50 == 0 or done_count == len(banks):
                    report(4 + int(7 * done_count / max(1, len(banks))), COMPACT_PROGRESS_PREFIX + self.ui_text(
                        f"预检查 bank：{done_count}/{len(banks)}，可提取 {len(extractable)}。",
                        f"Prechecking banks: {done_count}/{len(banks)}, extractable {len(extractable)}.",
                    ))
        extractable.sort(key=lambda p: p.as_posix().lower())
        rows.sort(key=lambda r: str(r.get("bank_path", "")).lower())
        return extractable, rows

    def dev_extract_all_banks_and_generate_tables(self):
        self.dev_search_unmatched_soundnames()

    def _auto_hide_setup_if_ready(self) -> None:
        ready = bool(self.game_root_edit.text().strip() and self.music_dir_edit.text().strip() and self.xml_edit.text().strip())
        if ready:
            self.path_box.setVisible(False)
        self.update_setup_toggle_text()

    def guide_text(self) -> str:
        if self.ui_lang() == "en":
            return (
                "1. Choose the game folder and your music folder once.\n"
                "2. Select the radio slots and the same number of music files.\n"
                "3. Set loop points, then generate a mod package or replace in game.\n"
                "Tip: the one-click mode will back up original XML/bank files first."
            )
        return (
            "1. 首次使用只需选择游戏目录和音乐目录。\n"
            "2. 在步骤 1 勾选要替换的槽位，并勾选等量音乐。\n"
            "3. 在步骤 2 设置循环点，最后生成输出包或一键替换。\n"
            "提示：一键替换会先备份原始 XML/bank 文件。"
        )

    def update_log_toggle_text(self) -> None:
        if not hasattr(self, 'btn_toggle_log_tab'):
            return
        en = self.ui_lang() == "en"
        shown = self.log_panel.isVisible() if hasattr(self, 'log_panel') else True
        if en:
            self.btn_toggle_log_tab.setText("H\ni\nd\ne\n\nL\no\ng" if shown else "S\nh\no\nw\n\nL\no\ng")
        else:
            self.btn_toggle_log_tab.setText("隐\n藏\n日\n志" if shown else "显\n示\n日\n志")
        if hasattr(self, 'guide_label'):
            self.guide_label.setText(self.guide_text())

    def toggle_log_panel(self) -> None:
        if not hasattr(self, 'log_panel'):
            return
        self.log_panel.setVisible(not self.log_panel.isVisible())
        self.update_log_toggle_text()

    def step_names(self) -> list[str]:
        if self.ui_lang() == "en":
            return ["Step 1 · Select radio and songs", "Step 2 · Set loop points", "Step 3 · Generate or replace"]
        return ["步骤 1 · 选择电台与歌曲", "步骤 2 · 设置循环点", "步骤 3 · 生成或替换"]

    def update_step_navigation(self) -> None:
        idx = self.steps.currentIndex() if hasattr(self, 'steps') else 0
        names = self.step_names()
        if hasattr(self, 'step_title_label'):
            self.step_title_label.setText(names[idx] if 0 <= idx < len(names) else "")
        if hasattr(self, 'btn_prev_step'):
            self.btn_prev_step.setEnabled(idx > 0)
        if hasattr(self, 'btn_next_step'):
            self.btn_next_step.setEnabled(idx < 2)
            self.btn_next_step.setText("下一步" if self.ui_lang() != "en" else "Next")
        if hasattr(self, 'btn_prev_step'):
            self.btn_prev_step.setText("上一步" if self.ui_lang() != "en" else "Back")

    def go_prev_step(self) -> None:
        idx = self.steps.currentIndex()
        if idx > 0:
            self.steps.setCurrentIndex(idx - 1)

    def go_next_step(self) -> None:
        idx = self.steps.currentIndex()
        if idx < self.steps.count() - 1:
            self.steps.setCurrentIndex(idx + 1)

    def _build_assign_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        station_bar = QHBoxLayout()
        station_bar.setSpacing(8)
        self.lbl_station = QLabel("目标电台")
        station_bar.addWidget(self.lbl_station)
        station_bar.addWidget(self.station_combo, 1)
        station_bar.addStretch(1)
        self.btn_assign = QPushButton("应用选择替换")
        self.btn_assign.setMinimumWidth(150)
        self.btn_assign.setObjectName("PrimaryAction")
        self.btn_assign.clicked.connect(self.assign_checked_or_current_music_to_slots)
        self.btn_clear_assignment = QPushButton("取消已选替换")
        self.btn_clear_assignment.setMinimumWidth(150)
        self.btn_clear_assignment.setObjectName("SmallAction")
        self.btn_clear_assignment.clicked.connect(self.clear_selected_assignment)
        station_bar.addWidget(self.btn_assign)
        station_bar.addWidget(self.btn_clear_assignment)
        outer.addLayout(station_bar)

        self.station_profile_label = QLabel("")
        self.station_profile_label.setWordWrap(True)
        self.station_profile_label.setObjectName("HintText")
        self.station_profile_label.setVisible(False)
        outer.addWidget(self.station_profile_label)

        left_card = QGroupBox()
        left = QVBoxLayout(left_card)
        left.setSpacing(4)
        left.setContentsMargins(8, 6, 8, 8)
        self.left_title = QLabel("勾选需要替换的游戏原曲槽位")
        self.left_title.setObjectName("SectionTitle")
        self.left_title.setAlignment(Qt.AlignCenter)
        left.addWidget(self.left_title)
        self.slot_table.setMinimumHeight(250)
        left.addWidget(self.slot_table, 1)

        right_card = QGroupBox()
        right = QVBoxLayout(right_card)
        right.setSpacing(4)
        right.setContentsMargins(8, 6, 8, 8)
        self.right_title = QLabel("勾选自己的音乐文件")
        self.right_title.setObjectName("SectionTitle")
        self.right_title.setAlignment(Qt.AlignCenter)
        right.addWidget(self.right_title)
        self.music_table.setMinimumHeight(250)
        right.addWidget(self.music_table, 1)

        self.assign_splitter = QSplitter(Qt.Horizontal)
        self.assign_splitter.addWidget(left_card)
        self.assign_splitter.addWidget(right_card)
        self.assign_splitter.setChildrenCollapsible(False)
        self.assign_splitter.setStretchFactor(0, 1)
        self.assign_splitter.setStretchFactor(1, 1)
        self.assign_splitter.setSizes([620, 620])
        outer.addWidget(self.assign_splitter, 1)

        self.assign_hint = QLabel("提示：点击表格第一列标题可全选/取消；左侧勾选 N 个槽位，右侧勾选 N 首音乐，然后点“应用选择替换”。")
        self.assign_hint.setObjectName("CompactHint")
        self.assign_hint.setWordWrap(True)
        outer.addWidget(self.assign_hint)
        return w

    def _build_loop_tab(self) -> QWidget:
        # v2.6.1: reorganize the loop page around the actual workflow.
        # Keep audio selection separate, put analysis and candidate-apply actions
        # inside the candidate area, and move play/stop controls below the seek
        # bar where users expect playback controls.
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(6, 6, 6, 6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setSpacing(8)

        top_box = QGroupBox("选择音频")
        top = QGridLayout(top_box)
        top.setHorizontalSpacing(8)
        top.setVerticalSpacing(6)
        self.lbl_loop_audio = QLabel("音频文件")
        top.addWidget(self.lbl_loop_audio, 0, 0)
        top.addWidget(self.loop_audio_combo, 0, 1, 1, 5)
        top.setColumnStretch(1, 1)
        outer.addWidget(top_box)

        candidate_box = QGroupBox("Loop 候选与场景试听")
        candidate_grid = QGridLayout(candidate_box)
        candidate_grid.setContentsMargins(12, 10, 12, 10)
        candidate_grid.setHorizontalSpacing(8)
        candidate_grid.setVerticalSpacing(8)
        candidate_grid.setColumnStretch(2, 1)

        self.btn_analyze_loop = QPushButton("批量分析全部歌曲")
        self.btn_analyze_loop.setMinimumWidth(150)
        self.btn_analyze_loop.setObjectName("PrimaryAction")
        self.btn_analyze_loop.clicked.connect(self.analyze_all_loop_audio)
        self.btn_analyze_current_loop = QPushButton("分析当前")
        self.btn_analyze_current_loop.setMinimumWidth(95)
        self.btn_analyze_current_loop.clicked.connect(self.analyze_current_loop_audio)

        self.lbl_candidate = QLabel("候选段落")
        self.lbl_candidate.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_preview_seconds = QLabel("试听秒数")
        self.lbl_preview_seconds.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.preview_seconds_spin.setFixedWidth(72)
        self.btn_preview_candidate = QPushButton("试听候选")
        self.btn_preview_candidate.setMinimumWidth(110)
        self.btn_preview_candidate.clicked.connect(self.preview_selected_candidate)

        analyze_wrap = QHBoxLayout()
        analyze_wrap.setSpacing(6)
        analyze_wrap.addWidget(self.btn_analyze_loop)
        analyze_wrap.addWidget(self.btn_analyze_current_loop)
        candidate_grid.addLayout(analyze_wrap, 0, 0)
        candidate_grid.addWidget(self.lbl_candidate, 0, 1)
        candidate_grid.addWidget(self.candidate_combo, 0, 2, 1, 4)
        candidate_grid.addWidget(self.lbl_preview_seconds, 0, 6)
        candidate_grid.addWidget(self.preview_seconds_spin, 0, 7)
        candidate_grid.addWidget(self.btn_preview_candidate, 0, 8)

        self.lbl_preview_scene = QLabel("场景试听")
        self.lbl_preview_scene.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.btn_preview_scene = QPushButton("试听场景")
        self.btn_preview_scene.setMinimumWidth(110)
        self.btn_preview_scene.clicked.connect(self.preview_selected_scenario)
        candidate_grid.addWidget(self.lbl_preview_scene, 1, 1)
        candidate_grid.addWidget(self.preview_scenario_combo, 1, 2, 1, 6)
        candidate_grid.addWidget(self.btn_preview_scene, 1, 8)

        self.candidate_summary_label.setWordWrap(True)
        self.candidate_summary_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        candidate_grid.addWidget(self.candidate_summary_label, 2, 2, 1, 7)

        self.btn_apply_track = QPushButton("填入 Track Loop")
        self.btn_apply_track.setMinimumWidth(145)
        self.btn_apply_track.clicked.connect(lambda: self.apply_selected_candidate("track"))
        self.btn_apply_post = QPushButton("填入 Post Loop")
        self.btn_apply_post.setMinimumWidth(145)
        self.btn_apply_post.clicked.connect(lambda: self.apply_selected_candidate("post"))
        self.btn_apply_both = QPushButton("同时填入 Track/Post")
        self.btn_apply_both.setMinimumWidth(180)
        self.btn_apply_both.clicked.connect(lambda: self.apply_selected_candidate("both"))
        apply_wrap = QHBoxLayout()
        apply_wrap.setSpacing(8)
        apply_wrap.addStretch(1)
        apply_wrap.addWidget(self.btn_apply_track)
        apply_wrap.addWidget(self.btn_apply_post)
        apply_wrap.addWidget(self.btn_apply_both)
        candidate_grid.addLayout(apply_wrap, 3, 2, 1, 7)
        outer.addWidget(candidate_box)

        seek_box = QGroupBox("进度条试听与手动微调")
        seek_layout = QVBoxLayout(seek_box)
        seek_layout.setSpacing(8)
        self.waveform.setToolTip("Waveform preview: click or drag to seek. Markers use sample positions.")
        seek_layout.addWidget(self.waveform)
        seek_layout.addWidget(self.seek_slider)
        playback_row = QHBoxLayout()
        playback_row.addWidget(self.position_label)
        playback_row.addStretch(1)
        self.btn_play_pause = QPushButton("播放 / 继续")
        self.btn_play_pause.setMinimumWidth(125)
        self.btn_play_pause.clicked.connect(self.play_or_resume_audio)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setText("Reset")
        self.btn_stop.setMinimumWidth(95)
        self.btn_stop.clicked.connect(self.reset_player_to_start)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setMinimumWidth(80)
        self.btn_pause.clicked.connect(self.player.pause)
        playback_row.addWidget(self.btn_play_pause)
        playback_row.addWidget(self.btn_pause)
        playback_row.addWidget(self.btn_stop)
        seek_layout.addLayout(playback_row)

        marker_row = QHBoxLayout()
        self.marker_target_combo.addItems(MARKER_ORDER)
        self.lbl_marker_target = QLabel("目标 Marker")
        marker_row.addWidget(self.lbl_marker_target)
        marker_row.addWidget(self.marker_target_combo)
        self.btn_jump_marker = QPushButton("跳到 Marker")
        self.btn_jump_marker.clicked.connect(self.jump_to_selected_marker)
        self.btn_write_marker = QPushButton("当前点写入 Marker")
        self.btn_write_marker.clicked.connect(self.write_current_sample_to_marker)
        marker_row.addWidget(self.btn_jump_marker)
        marker_row.addWidget(self.btn_write_marker)
        for label, delta in (("-10000", -10000), ("-1000", -1000), ("+1000", 1000), ("+10000", 10000)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, d=delta: self.nudge_selected_marker(d))
            marker_row.addWidget(btn)
        marker_row.addStretch(1)
        seek_layout.addLayout(marker_row)
        outer.addWidget(seek_box)

        marker_box = QGroupBox("Marker 参数")
        marker_outer = QVBoxLayout(marker_box)
        marker_outer.setContentsMargins(10, 8, 10, 8)
        marker_outer.setSpacing(7)

        marker_rows = [
            ["TrackStart", "TrackDrop", "PostDrop"],
            ["TrackLoopStart", "TrackLoopEnd"],
            ["PostRaceLoopStart", "PostRaceLoopEnd"],
            ["DJSegment", "StingerStart", "DJStart"],
            ["End"],
        ]
        for names in marker_rows:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            for name in names:
                label = QLabel(name)
                label.setMinimumWidth(125)
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                spin = QSpinBox()
                spin.setRange(-1, 2_147_483_647)
                spin.setValue(-1)
                spin.setMinimumWidth(135)
                spin.setMaximumWidth(170)
                spin.valueChanged.connect(lambda _value, _name=name: self.refresh_waveform_markers())
                self.marker_spins[name] = spin
                row_layout.addWidget(label)
                row_layout.addWidget(spin)
                row_layout.addSpacing(12)
            row_layout.addStretch(1)
            marker_outer.addLayout(row_layout)

        marker_action_box = QGroupBox("Marker 操作")
        marker_action_grid = QGridLayout(marker_action_box)
        marker_action_grid.setContentsMargins(8, 8, 8, 8)
        marker_action_grid.setHorizontalSpacing(8)
        marker_action_grid.setVerticalSpacing(8)
        for col in range(3):
            marker_action_grid.setColumnStretch(col, 1)

        self.btn_apply_safe_markers = QPushButton("应用无循环")
        self.btn_apply_safe_markers.clicked.connect(self.apply_safe_markers_to_current_audio)
        self.btn_apply_safe_markers_all = QPushButton("全部无循环")
        self.btn_apply_safe_markers_all.clicked.connect(self.apply_safe_markers_to_all_audio)
        self.btn_save_loop_profile = QPushButton("保存当前")
        self.btn_save_loop_profile.clicked.connect(self.save_current_loop_profile)
        self.btn_save_all_loop_profiles = QPushButton("保存全部")
        self.btn_save_all_loop_profiles.clicked.connect(self.save_all_loop_profiles)
        self.btn_import_markers = QPushButton("导入 Marker")
        self.btn_import_markers.clicked.connect(self.import_marker_profiles_from_file)
        self.btn_export_marker_template = QPushButton("导出模板")
        self.btn_export_marker_template.clicked.connect(self.export_marker_import_template_dialog)

        marker_buttons = [
            self.btn_apply_safe_markers,
            self.btn_apply_safe_markers_all,
            self.btn_save_loop_profile,
            self.btn_save_all_loop_profiles,
            self.btn_import_markers,
            self.btn_export_marker_template,
        ]
        for btn in marker_buttons:
            btn.setMinimumWidth(120)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        marker_action_grid.addWidget(self.btn_apply_safe_markers, 0, 0)
        marker_action_grid.addWidget(self.btn_apply_safe_markers_all, 0, 1)
        marker_action_grid.addWidget(self.btn_save_loop_profile, 0, 2)
        marker_action_grid.addWidget(self.btn_save_all_loop_profiles, 1, 0)
        marker_action_grid.addWidget(self.btn_import_markers, 1, 1)
        marker_action_grid.addWidget(self.btn_export_marker_template, 1, 2)
        marker_outer.addWidget(marker_action_box)
        outer.addWidget(marker_box)
        outer.addStretch(1)

        scroll.setWidget(content)
        root_layout.addWidget(scroll, 1)
        return root

    def _build_deploy_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        self.final_box = QGroupBox("生成与安装 / 普通玩家只需要这里")
        grid = QGridLayout(self.final_box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self.btn_fmod_tool = QPushButton("选择 Fmod Bank Tools")
        self.btn_fmod_tool.setMinimumWidth(190)
        self.btn_fmod_tool.clicked.connect(self.browse_fmod_tool)
        self.btn_package = QPushButton("生成 Mod 包（不覆盖）")
        self.btn_package.setMinimumWidth(210)
        self.btn_package.setObjectName("PrimaryAction")
        self.btn_package.clicked.connect(self.generate_mod_output_package)
        self.btn_one_click = QPushButton("一键替换到游戏（自动备份）")
        self.btn_one_click.setMinimumWidth(230)
        self.btn_one_click.setObjectName("DangerAction")
        self.btn_one_click.clicked.connect(self.one_click_replace_game_files)

        self.lbl_auto_bank = QLabel("自动定位 Bank 目录")
        self.lbl_auto_bank.setVisible(False)
        self.bank_root_edit.setVisible(False)
        self.lbl_fmod_tool = QLabel("Fmod Bank Tools exe")
        self.fmod_auto_note_label = QLabel("一键流程会自动控制 Fmod Bank Tools，无需手动勾选额外选项。")
        self.fmod_auto_note_label.setWordWrap(True)
        self.fmod_auto_note_label.setObjectName("CompactHint")
        grid.addWidget(self.lbl_fmod_tool, 0, 0)
        grid.addWidget(self.fmod_tool_edit, 0, 1)
        grid.addWidget(self.btn_fmod_tool, 0, 2)
        grid.addWidget(self.fmod_auto_note_label, 1, 1, 1, 2)
        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_package)
        action_row.addWidget(self.btn_one_click)
        action_row.addStretch(1)
        grid.addLayout(action_row, 2, 0, 1, 3)
        outer.addWidget(self.final_box)

        self.main_menu_music_box = QGroupBox("可选：主菜单音乐替换 / Optional main menu music")
        menu_grid = QGridLayout(self.main_menu_music_box)
        menu_grid.setContentsMargins(12, 10, 12, 10)
        menu_grid.setHorizontalSpacing(8)
        menu_grid.setVerticalSpacing(8)
        menu_grid.setColumnStretch(1, 1)

        self.lbl_main_menu_bank = QLabel("目标 bank（自动定位）")
        self.btn_main_menu_bank = QPushButton("选择 bank")
        self.btn_main_menu_bank.setMinimumWidth(120)
        self.btn_main_menu_bank.clicked.connect(self.browse_main_menu_bank)
        self.btn_main_menu_bank.setVisible(False)
        self.btn_main_menu_auto_bank = QPushButton("自动查找")
        self.btn_main_menu_auto_bank.setMinimumWidth(120)
        self.btn_main_menu_auto_bank.clicked.connect(self.auto_detect_main_menu_bank)
        self.btn_main_menu_auto_bank.setVisible(False)

        self.lbl_main_menu_audio = QLabel("新主菜单音乐")
        self.btn_main_menu_audio = QPushButton("选择音频")
        self.btn_main_menu_audio.setMinimumWidth(120)
        self.btn_main_menu_audio.clicked.connect(self.browse_main_menu_audio)

        self.lbl_main_menu_mode = QLabel("替换方式")
        self.lbl_main_menu_mode.setVisible(False)
        self.main_menu_mode_combo.setVisible(False)
        self.main_menu_hint = QLabel("说明：主菜单音乐固定为 GLB_RadioPressStart.assets.bank。只需选择新音乐；工具会从游戏目录自动定位该 bank，并替换其中唯一音乐音频；不修改电台 XML。")
        self.main_menu_hint.setObjectName("CompactHint")
        self.main_menu_hint.setWordWrap(True)

        self.btn_main_menu_package = QPushButton("生成主菜单 Mod 包（不覆盖）")
        self.btn_main_menu_package.setMinimumWidth(220)
        self.btn_main_menu_package.clicked.connect(self.generate_main_menu_music_package)
        self.btn_main_menu_one_click = QPushButton("一键替换主菜单音乐（自动备份）")
        self.btn_main_menu_one_click.setMinimumWidth(250)
        self.btn_main_menu_one_click.setObjectName("DangerAction")
        self.btn_main_menu_one_click.clicked.connect(self.one_click_replace_main_menu_music)

        menu_grid.addWidget(self.lbl_main_menu_bank, 0, 0)
        menu_grid.addWidget(self.main_menu_bank_edit, 0, 1, 1, 3)
        menu_grid.addWidget(self.lbl_main_menu_audio, 1, 0)
        menu_grid.addWidget(self.main_menu_audio_edit, 1, 1, 1, 2)
        menu_grid.addWidget(self.btn_main_menu_audio, 1, 3)
        menu_grid.addWidget(self.main_menu_hint, 2, 0, 1, 4)
        menu_action_row = QHBoxLayout()
        menu_action_row.setSpacing(12)
        menu_action_row.addStretch(1)
        menu_action_row.addWidget(self.btn_main_menu_package)
        menu_action_row.addWidget(self.btn_main_menu_one_click)
        menu_action_row.addStretch(1)
        menu_grid.addLayout(menu_action_row, 3, 0, 1, 4)
        outer.addWidget(self.main_menu_music_box)

        self.dev_mode_box = QGroupBox("开发者模式 / Audio research developer mode")
        dev_grid = QGridLayout(self.dev_mode_box)
        dev_grid.setContentsMargins(12, 10, 12, 10)
        dev_grid.setHorizontalSpacing(8)
        dev_grid.setVerticalSpacing(8)
        dev_grid.setColumnStretch(1, 1)

        self.lbl_dev_threads = QLabel("最大 CPU 线程")
        self.dev_thread_spin = QSpinBox()
        self.dev_thread_spin.setRange(1, local_logical_cpu_count())
        saved_threads = self.store.get_setting("dev_max_threads", recommended_safe_thread_count())
        try:
            saved_threads_i = int(saved_threads)
        except Exception:
            saved_threads_i = recommended_safe_thread_count()
        self.dev_thread_spin.setValue(max(1, min(local_logical_cpu_count(), saved_threads_i)))
        self.dev_thread_spin.setMaximumWidth(100)
        self.dev_thread_spin.valueChanged.connect(self.on_dev_thread_count_changed)
        self.dev_thread_hint = QLabel(self.dev_thread_hint_text())
        self.dev_thread_hint.setObjectName("CompactHint")
        self.dev_thread_hint.setWordWrap(True)

        self.btn_dev_full_audio_scan = QPushButton("一键 Extract 全部 Bank 并生成统计/映射表")
        self.btn_dev_full_audio_scan.setMinimumWidth(290)
        self.btn_dev_full_audio_scan.clicked.connect(self.dev_extract_all_banks_and_generate_tables)
        self.btn_dev_menu_scan = QPushButton("扫描主菜单/前端音乐 Bank")
        self.btn_dev_menu_scan.setMinimumWidth(220)
        self.btn_dev_menu_scan.clicked.connect(self.dev_scan_menu_music_banks)
        self.dev_mode_hint = QLabel("用于研究 DJ、stinger、音效和 XML→bank 关系。该模式只 Extract/统计，不写 XML、不 Rebuild、不覆盖游戏文件。Fmod Bank Tools Extract 仍会串行执行；预检查、统计和 CSV 生成会在不超过上方设置的线程数内自动分配。")
        self.dev_mode_hint.setObjectName("CompactHint")
        self.dev_mode_hint.setWordWrap(True)

        dev_grid.addWidget(self.lbl_dev_threads, 0, 0)
        dev_grid.addWidget(self.dev_thread_spin, 0, 1)
        dev_grid.addWidget(self.dev_thread_hint, 0, 2, 1, 2)
        dev_grid.addWidget(self.btn_dev_full_audio_scan, 1, 0, 1, 2)
        dev_grid.addWidget(self.btn_dev_menu_scan, 1, 2, 1, 2)
        dev_grid.addWidget(self.dev_mode_hint, 2, 0, 1, 4)
        outer.addWidget(self.dev_mode_box)

        self.final_hint = QLabel("推荐：选择游戏目录和音乐目录 → 勾选槽位和音乐 → 应用选择替换 → 设置 Marker → 生成 Mod 包或一键替换。主菜单音乐可在上方单独替换，只需选择新音乐文件。")
        self.final_hint.setObjectName("CompactHint")
        self.final_hint.setWordWrap(True)
        outer.addWidget(self.final_hint)
        outer.addStretch(1)
        return w

    def ui_lang(self) -> str:
        try:
            return str(self.ui_language_combo.currentData() or "zh")
        except Exception:
            return "zh"

    def on_ui_language_changed(self) -> None:
        lang = self.ui_lang()
        self.store.set_setting("ui_language", lang)
        self.apply_ui_language()

    def on_game_language_changed(self) -> None:
        code = str(self.game_language_combo.currentData() or "auto")
        self.store.set_setting("game_language", code)
        if self.xml_candidates:
            selected = self._select_xml_for_game_language(self.xml_candidates, self.current_xml)
            if selected and Path(selected) != self.current_xml:
                self.xml_edit.setText(str(selected))
                self.load_xml(Path(selected))
                self.log(f"[LANG] 已根据游戏语言选择 XML: {selected}")
        elif self.game_root_edit.text().strip() and not self._busy:
            # The manual XML button has been removed in v2.4.8.  If the user
            # changes the game language before a scan result is cached, rescan
            # the game root and select the matching XML automatically.
            self.scan_game_root()

    def apply_ui_language(self) -> None:
        """Apply the main Chinese/English labels immediately.

        This does not try to translate every debug log line, but it covers the
        normal-player workflow and the most visible buttons/tables.
        """
        en = self.ui_lang() == "en"
        self.setWindowTitle(f"FH6 Radio Tool v{APP_VERSION} - {'Radio replacement tool' if en else '电台替换工具'}")
        if hasattr(self, 'path_box'):
            self.path_box.setTitle("Path Settings / configure once" if en else "路径设置 / 只需配置一次")
        if hasattr(self, 'step_title_label'):
            self.update_step_navigation()
        if getattr(self, 'action_toggle_setup', None) is not None:
            self.action_toggle_setup.setText("Show/hide setup panel / 显示或隐藏路径设置" if en else "显示/隐藏路径设置 / Show or hide setup")
        if getattr(self, 'action_show_log', None) is not None:
            self.action_show_log.setText("Show/hide log panel / 显示或隐藏日志栏" if en else "显示/隐藏日志栏 / Show or hide log panel")
        pairs = {
            'lbl_game_root': ("Game root", "游戏根目录"),
            'lbl_music_dir': ("Music folder", "音乐目录"),
            'lbl_xml': ("Current XML", "当前 XML"),
            'lbl_ui_language': ("UI Language / 界面语言", "界面语言 / UI Language"),
            'lbl_game_language': ("Game language / XML", "游戏语言 / Game XML"),
            'lbl_station': ("Target radio", "目标电台"),
            'left_title': ("Game slots to replace", "勾选需要替换的游戏原曲槽位"),
            'right_title': ("Your music files", "勾选自己的音乐文件"),
            'lbl_auto_bank': ("Auto-detected bank folder", "自动定位 Bank 目录"),
            'lbl_fmod_tool': ("Fmod Bank Tools exe", "Fmod Bank Tools exe"),
            'lbl_main_menu_bank': ("Target bank (auto)", "目标 bank（自动定位）"),
            'lbl_main_menu_audio': ("New main menu music", "新主菜单音乐"),
            'lbl_main_menu_mode': ("Replacement mode", "替换方式"),
            'lbl_loop_audio': ("Audio file", "音频文件"),
            'lbl_candidate': ("Candidate", "候选段落"),
            'lbl_preview_seconds': ("Preview seconds", "试听秒数"),
            'lbl_preview_scene': ("Scene preview", "场景试听"),
            'lbl_marker_target': ("Target Marker", "目标 Marker"),
            'log_title_label': ("Log", "日志 / Log"),
            'lbl_dev_threads': ("Max CPU threads", "最大 CPU 线程"),
        }
        for attr, (en_text, zh_text) in pairs.items():
            obj = getattr(self, attr, None)
            if obj is not None:
                obj.setText(en_text if en else zh_text)
        buttons = {
            'btn_game': ("Choose game folder", "选择游戏目录"),
            'btn_music': ("Choose music folder", "选择音乐目录"),
            'btn_backup': ("Create backup point", "创建备份点"),
            'btn_restore': ("Restore backup / initial state", "恢复备份/初始状态"),
            'btn_clear_assignment': ("Cancel selected", "取消已选替换"),
            'btn_assign': ("Apply replacement", "应用选择替换"),
            'btn_fmod_tool': ("Choose Fmod Bank Tools", "选择 Fmod Bank Tools"),
            'btn_package': ("Generate mod package (no overwrite)", "生成 Mod 包（不覆盖）"),
            'btn_one_click': ("One-click replace (auto backup)", "一键替换到游戏（自动备份）"),
            'btn_main_menu_bank': ("Choose bank", "选择 bank"),
            'btn_main_menu_auto_bank': ("Auto-detect", "自动查找"),
            'btn_main_menu_audio': ("Choose audio", "选择音频"),
            'btn_main_menu_package': ("Generate main menu package", "生成主菜单 Mod 包（不覆盖）"),
            'btn_main_menu_one_click': ("One-click main menu replace", "一键替换主菜单音乐（自动备份）"),
            'btn_hide_setup': ("Done, hide setup", "完成设置并隐藏"),
            'btn_prev_step': ("Back", "上一步"),
            'btn_next_step': ("Next", "下一步"),
            'btn_analyze_loop': ("Analyze all songs", "批量分析全部歌曲"),
            'btn_analyze_current_loop': ("Analyze current", "分析当前"),
            'btn_preview_candidate': ("Preview loop", "试听候选"),
            'btn_preview_scene': ("Preview scene", "试听场景"),
            'btn_apply_track': ("Use as Track Loop", "填入 Track Loop"),
            'btn_apply_post': ("Use as Post Loop", "填入 Post Loop"),
            'btn_apply_both': ("Use as Track/Post", "同时填入 Track/Post"),
            'btn_play_pause': ("Play / Resume", "播放 / 继续"),
            'btn_stop': ("Stop", "停止"),
            'btn_jump_marker': ("Jump to Marker", "跳到 Marker"),
            'btn_write_marker': ("Write current point", "当前点写入 Marker"),
            'btn_save_loop_profile': ("Save current", "保存当前"),
            'btn_save_all_loop_profiles': ("Save all", "保存全部"),
            'btn_apply_safe_markers': ("No Loop / Safe", "应用无循环"),
            'btn_apply_safe_markers_all': ("No Loop / Safe to All", "全部无循环"),
            'btn_import_markers': ("Import markers", "导入 Marker"),
            'btn_export_marker_template': ("Export template", "导出模板"),
            'btn_dev_full_audio_scan': ("Extract all banks and generate statistics/mapping tables", "一键 Extract 全部 Bank 并生成统计/映射表"),
            'btn_dev_menu_scan': ("Scan main-menu/frontend music banks", "扫描主菜单/前端音乐 Bank"),
        }
        buttons["btn_pause"] = ("Pause", "暂停")
        buttons["btn_stop"] = ("Reset", "回到起点")
        buttons["btn_export_marker_template"] = ("Export markers", "导出 Marker")
        for attr, (en_text, zh_text) in buttons.items():
            obj = getattr(self, attr, None)
            if obj is not None:
                obj.setText(en_text if en else zh_text)
        if hasattr(self, 'main_menu_music_box'):
            self.main_menu_music_box.setTitle("Optional: main menu music replacement" if en else "可选：主菜单音乐替换 / Optional main menu music")
        if hasattr(self, 'main_menu_hint'):
            self.main_menu_hint.setText(
                f"Note: main menu music is fixed to {MAIN_MENU_PRESS_START_BANK}. Choose only your replacement audio; the tool auto-locates this bank from the game folder and replaces its single music entry. Radio XML is not modified."
                if en else
                f"说明：主菜单音乐固定为 {MAIN_MENU_PRESS_START_BANK}。只需选择新音乐；工具会从游戏目录自动定位该 bank，并替换其中唯一音乐音频；不修改电台 XML。"
            )
        if hasattr(self, 'final_hint'):
            self.final_hint.setText(
                "Recommended radio workflow: choose game/music folders → select slots and music → apply replacements → set markers → generate a mod package or one-click replace. Main menu music can be replaced separately above; only a replacement audio file is needed."
                if en else
                "推荐：选择游戏目录和音乐目录 → 勾选槽位和音乐 → 应用选择替换 → 设置 Marker → 生成 Mod 包或一键替换。主菜单音乐可在上方单独替换，只需选择新音乐文件。"
            )
        if hasattr(self, 'dev_mode_box'):
            self.dev_mode_box.setTitle("Developer mode / Audio research" if en else "开发者模式 / Audio research developer mode")
        if hasattr(self, 'compact_progress_label') and not getattr(self, '_busy', False) and not getattr(self, '_compact_progress_lines', []):
            self.compact_progress_label.setText("Idle." if en else "等待任务。")
        if hasattr(self, 'dev_thread_hint'):
            self.dev_thread_hint.setText(self.dev_thread_hint_text())
        if hasattr(self, 'dev_mode_hint'):
            self.dev_mode_hint.setText(
                "For researching DJ, stinger, SFX, and XML→bank relationships. This mode only extracts and generates statistics; it does not write XML, rebuild banks, or overwrite game files. If previous scan data is found, the tool will ask whether to delete it and retest or reuse the existing cache. Fmod Bank Tools Extract is kept serial; precheck/statistics/CSV stages are automatically scheduled within the CPU thread limit above."
                if en else
                "用于研究 DJ、stinger、音效和 XML→bank 关系。该模式只 Extract/统计，不写 XML、不 Rebuild、不覆盖游戏文件。如果发现已有扫描记录，工具会询问是否删除旧记录并重新测试，或复用已有缓存。Fmod Bank Tools Extract 仍会串行执行；预检查、统计和 CSV 生成会在不超过上方设置的线程数内自动分配。"
            )
        self._refresh_station_combo_labels()
        if hasattr(self, 'main_menu_mode_combo'):
            current = self.main_menu_mode_combo.currentData()
            self.main_menu_mode_combo.blockSignals(True)
            self.main_menu_mode_combo.clear()
            if en:
                self.main_menu_mode_combo.addItem("Replace the only music entry", "single")
            else:
                self.main_menu_mode_combo.addItem("自动替换该 bank 内唯一音乐音频", "single")
            idx = self.main_menu_mode_combo.findData(current)
            if idx >= 0:
                self.main_menu_mode_combo.setCurrentIndex(idx)
            self.main_menu_mode_combo.blockSignals(False)
        if hasattr(self, 'preview_scenario_combo'):
            current = self.preview_scenario_combo.currentData()
            self.preview_scenario_combo.blockSignals(True)
            self.preview_scenario_combo.clear()
            if en:
                scenarios = [
                    ("Free roam: Track Loop", "roam_loop"),
                    ("Race start: TrackDrop/TrackStart to TrackLoop", "race_start"),
                    ("Race loop: TrackLoop", "race_loop"),
                    ("Finish: preview around PostDrop", "finish"),
                    ("Post-race: PostRaceLoop", "post_loop"),
                ]
            else:
                scenarios = [
                    ("漫游模式：Track Loop 循环", "roam_loop"),
                    ("比赛开始：TrackDrop/TrackStart → TrackLoop", "race_start"),
                    ("比赛进行：TrackLoop 循环", "race_loop"),
                    ("冲线：PostDrop 前后预览", "finish"),
                    ("冲线后：PostRaceLoop 循环", "post_loop"),
                ]
            for text, data in scenarios:
                self.preview_scenario_combo.addItem(text, data)
            idx = self.preview_scenario_combo.findData(current)
            self.preview_scenario_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.preview_scenario_combo.blockSignals(False)

        if hasattr(self, 'guide_box'):
            self.guide_box.setTitle("Quick Guide / 快速教程" if en else "快速教程 / Quick Guide")
        if hasattr(self, 'log_runtime_box'):
            self.log_runtime_box.setTitle("Runtime Log / 运行日志" if en else "运行日志 / Runtime Log")
        self.update_setup_toggle_text()
        self.update_log_toggle_text()
        if hasattr(self, 'auto_fmod_click_check'):
            self.auto_fmod_click_check.setChecked(True)
            self.auto_fmod_click_check.setEnabled(False)
            self.auto_fmod_click_check.setVisible(False)
        if hasattr(self, 'fmod_auto_note_label'):
            self.fmod_auto_note_label.setText(
                "One-click workflows automatically control Fmod Bank Tools; no extra checkbox is needed." if en
                else "一键流程会自动控制 Fmod Bank Tools，无需手动勾选额外选项。"
            )
        self.slot_table.setHorizontalHeaderLabels(
            ["Replace\nAll", "Slot", "Original", "Artist", "SoundName", "SampleLength", "SampleRate", "Assigned music", "Markers", "Status"]
            if en else
            ["替换\n全选", "Slot", "原曲名", "Artist", "SoundName", "SampleLength", "SampleRate", "已分配新曲", "Markers", "状态"]
        )
        self.music_table.setHorizontalHeaderLabels(
            ["Select\nAll", "File name", "Artist", "Format", "Sample rate", "Duration", "Saved settings", "Path"]
            if en else
            ["选择\n全选", "文件名", "Artist", "格式", "采样率", "时长", "已保存设置", "路径"]
        )
        self.apply_table_layout()
        if hasattr(self, 'station_profile_label'):
            self._update_station_profile_label()
        if hasattr(self, 'assign_hint'):
            self.assign_hint.setText(
                "Tip: click the first column header to select/clear all. Check N game slots and N music files, then click Apply replacement."
                if en else
                "提示：点击表格第一列标题可全选/取消；左侧勾选 N 个槽位，右侧勾选 N 首音乐，然后点“应用选择替换”。"
            )
        if hasattr(self, 'final_box'):
            self.final_box.setTitle("Build and install / normal users only need this" if en else "生成与安装 / 普通玩家只需要这里")
        if hasattr(self, 'final_hint'):
            self.final_hint.setText(
                "Flow: choose game/music folders → select slots and music → apply replacement → set markers → generate package or one-click replace."
                if en else
                "推荐：选择游戏目录和音乐目录 → 勾选槽位和音乐 → 应用选择替换 → 设置 Marker → 生成 Mod 包或一键替换。"
            )

    def _select_xml_for_game_language(self, candidates: list[Path], fallback: Path | None = None) -> Path | None:
        if not candidates:
            return fallback
        code = str(self.game_language_combo.currentData() or "auto").lower()
        if code == "auto":
            return fallback or candidates[0]
        aliases = {
            "cn": ["cn", "zh_cn", "chs", "sc", "simplified"],
            "tw": ["tw", "tc", "cht", "zh_tw", "traditional"],
            "en": ["en", "us", "english"],
            "ja": ["ja", "jp", "japanese"],
            "ko": ["ko", "kr", "korean"],
            "de": ["de", "german"],
            "fr": ["fr", "french"],
            "es": ["es", "spanish"],
            "it": ["it", "italian"],
            "pt": ["pt", "br", "portuguese"],
            "ru": ["ru", "russian"],
        }
        wants = aliases.get(code, [code])
        scored: list[tuple[int, Path]] = []
        for p in candidates:
            n = p.name.lower().replace('-', '_')
            stem = p.stem.lower().replace('-', '_')
            score = 100
            for w in wants:
                if stem == f"radioinfo_{w}" or stem.endswith(f"_{w}"):
                    score = min(score, 0)
                elif f"_{w}_" in f"_{stem}_" or w in stem.split('_'):
                    score = min(score, 10)
                elif w in n:
                    score = min(score, 25)
            if code == "en" and stem == "radioinfo":
                score = min(score, 5)
            scored.append((score, p))
        scored.sort(key=lambda x: (x[0], len(str(x[1])), str(x[1]).lower()))
        return scored[0][1] if scored and scored[0][0] < 100 else (fallback or candidates[0])


    def ui_text(self, zh: str, en: str | None = None) -> str:
        """Return UI text according to the current interface language."""
        if self.ui_lang() == "en":
            return en if en is not None else zh
        return zh

    def automation_component_warning_text(self, context_zh: str, context_en: str) -> tuple[str, str]:
        """User-facing message when pywinauto is unavailable.

        v3.0.37 removes the obsolete auto-control checkbox and fixes the legacy path that asked Nexus EXE users
        to run setup_env.bat even though the portable package intentionally does
        not contain setup_env.bat.
        """
        ok, detail = pywinauto_status()
        if ok:
            return "", ""
        if is_frozen_app():
            zh = (
                f"{context_zh}需要自动控制 Fmod Bank Tools。\n\n"
                f"当前便携 EXE 的自动控制组件不可用：{detail}\n\n"
                "这个 Nexus/便携 EXE 包不会包含 setup_env.bat；运行 v2 旧版 setup_env.bat 也不会修复当前 EXE。\n"
                "请使用 v3.0.37 或更新版本重新下载/重新打包；不要使用旧 v2 环境修复 EXE。"
            )
            en = (
                f"{context_en} requires automatic control of Fmod Bank Tools.\n\n"
                f"The automation component bundled in this portable EXE is not available: {detail}\n\n"
                "This Nexus/portable EXE package does not include setup_env.bat, and running an old v2 setup_env.bat will not repair the current EXE.\n"
                "Please use v3.0.37 or a newer rebuilt package; do not repair this EXE with an old v2 environment."
            )
        else:
            zh = (
                f"{context_zh}需要自动控制 Fmod Bank Tools。\n\n"
                f"当前 Python 环境缺少自动控制组件：{detail}\n\n"
                "这是开发者源码运行环境的依赖缺失。请在当前 v3 源码环境中安装 requirements.txt 后重新启动工具；不要使用 v2 旧环境。"
            )
            en = (
                f"{context_en} requires automatic control of Fmod Bank Tools.\n\n"
                f"The current Python environment is missing the automation component: {detail}\n\n"
                "This is a developer/source-environment dependency issue. Install requirements.txt in the current v3 source environment, then restart the tool. Do not reuse an old v2 environment."
            )
        return zh, en

    def _fmod_auto_click_enabled(self) -> bool:
        """Return the forced Fmod GUI automation setting.

        v3.0.37 removes the old user-facing auto-control checkbox from the
        normal workflow.  One-click and package generation require GUI
        automation, so allowing users to disable it only created a dead-end
        legacy warning about setup_env.bat/pywinauto.
        """
        try:
            if hasattr(self, "auto_fmod_click_check"):
                self.auto_fmod_click_check.blockSignals(True)
                self.auto_fmod_click_check.setChecked(True)
                self.auto_fmod_click_check.setEnabled(False)
                self.auto_fmod_click_check.setVisible(False)
                self.auto_fmod_click_check.blockSignals(False)
            self.store.set_setting("fmod_auto_click", True)
        except Exception:
            pass
        return True

    def _rough_runtime_translate(self, text: str) -> str:
        """Small runtime/log translator for common messages.

        File paths, exceptions, FMOD technical names and bank filenames are kept
        unchanged so users can still report useful logs.
        """
        out = str(text)
        replacements = [
            ("运行日志会显示在这里。", "Runtime logs will be shown here."),
            ("开始。预计耗时：", " started. Estimated time: "),
            ("开始。", " started."),
            ("失败：", " failed: "),
            ("游戏扫描完成", "Game scan completed"),
            ("音乐扫描完成", "Music scan completed"),
            ("XML 已加载", "XML loaded"),
            ("找到 XML", "Found XML"),
            ("已根据游戏语言选择 XML", "Selected XML by game language"),
            ("自动定位 Bank 目录", "Auto-detected bank folder"),
            ("批量智能替换完成", "Batch replacement completed"),
            ("Loop 分析完成，请在候选栏选择、场景试听，必要时用进度条手动微调", "Loop analysis completed. Select a candidate, use scene preview, and fine tune manually if needed"),
            ("试听候选衔接", "Preview loop transition"),
            ("试听场景", "Preview scene"),
            ("已保存当前音频设置", "Saved current audio settings"),
            ("已导入 Fmod Extract 模板", "Imported Fmod Extract template"),
            ("已生成 Mod 输出包", "Generated mod output package"),
            ("已完成完整替换流程", "Completed full replacement workflow"),
            ("初始状态 manifest", "Initial-state manifest"),
            ("本次备份点 manifest", "Snapshot manifest"),
            ("已恢复初始状态文件", "Restored initial-state files"),
            ("已恢复备份点文件", "Restored backup-point files"),
            ("任务正在进行", "Task in progress"),
            ("缺少路径", "Missing path"),
            ("缺少选择", "Missing selection"),
            ("缺少电台", "Missing radio station"),
            ("没有候选", "No candidate"),
            ("没有可备份文件", "No files to back up"),
            ("备份完成", "Backup completed"),
            ("恢复完成", "Restore completed"),
            ("生成完成", "Generation completed"),
            ("，", ", "),
            ("：", ": "),
            ("。", "."),
        ]
        for zh, en in replacements:
            out = out.replace(zh, en)
        return out

    def log_text(self, zh: str, en: str | None = None) -> str:
        if self.ui_lang() == "en":
            return en if en is not None else self._rough_runtime_translate(zh)
        return zh

    def info_box(self, title_zh: str, message_zh: str, title_en: str | None = None, message_en: str | None = None) -> None:
        QMessageBox.information(self, self.ui_text(title_zh, title_en), self.ui_text(message_zh, message_en))

    def warn_box(self, title_zh: str, message_zh: str, title_en: str | None = None, message_en: str | None = None) -> None:
        QMessageBox.warning(self, self.ui_text(title_zh, title_en), self.ui_text(message_zh, message_en))

    def error_box(self, title_zh: str, message_zh: str, title_en: str | None = None, message_en: str | None = None) -> None:
        QMessageBox.critical(self, self.ui_text(title_zh, title_en), self.ui_text(message_zh, message_en))

    def question_box(self, title_zh: str, message_zh: str, title_en: str | None = None, message_en: str | None = None) -> bool:
        answer = QMessageBox.question(
            self,
            self.ui_text(title_zh, title_en),
            self.ui_text(message_zh, message_en),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _non_empty_paths(self, paths: list[Path]) -> list[Path]:
        out: list[Path] = []
        for p in paths:
            try:
                if p.is_dir():
                    if any(p.iterdir()):
                        out.append(p)
                elif p.exists():
                    out.append(p)
            except Exception:
                if p.exists():
                    out.append(p)
        return out

    def _dev_full_scan_record_paths(self) -> list[Path]:
        work = project_work_dir()
        return [
            work / "dev_all_station_bank_sound_scan",
            work / "dev_fmod_extract_cache" / safe_stem("all_extractable_banks_v3_batched", 80),
        ]

    def ask_dev_existing_scan_action(self, paths: list[Path]) -> str:
        """Return delete/reuse/cancel for existing developer scan data."""
        shown = "\n".join(f"- {p}" for p in paths[:6])
        if len(paths) > 6:
            shown += f"\n- ... +{len(paths) - 6} more"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(self.ui_text("发现已有开发者扫描记录", "Existing developer scan data found"))
        box.setText(self.ui_text(
            "检测到之前的开发者 Extract/统计记录。\n\n"
            f"{shown}\n\n"
            "选择“删除并重新测试”会清空旧缓存并重新 Extract 全部 bank；\n"
            "选择“保留并继续”会尽量复用已有 Extract 缓存，只重新生成统计/映射表。",
            "Previous developer Extract/statistics data was found.\n\n"
            f"{shown}\n\n"
            "Choose 'Delete and retest' to clear the old cache and Extract all banks again;\n"
            "choose 'Keep and continue' to reuse the existing Extract cache when possible and regenerate the reports.",
        ))
        delete_btn = box.addButton(self.ui_text("删除并重新测试", "Delete and retest"), QMessageBox.YesRole)
        reuse_btn = box.addButton(self.ui_text("保留并继续", "Keep and continue"), QMessageBox.NoRole)
        cancel_btn = box.addButton(self.ui_text("取消", "Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(reuse_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked == delete_btn:
            return "delete"
        if clicked == reuse_btn:
            return "reuse"
        return "cancel"

    def confirm_box(self, title_zh: str, message_zh: str, title_en: str | None = None, message_en: str | None = None) -> bool:
        """Compatibility wrapper for confirmation dialogs used by developer tools."""
        return self.question_box(title_zh, message_zh, title_en, message_en)

    def ensure_idle_for_action(self, action_zh: str = "当前操作", action_en: str | None = None) -> bool:
        """Prevent synchronous UI actions from running while a background task is active.

        This is important for backup/restore while music scanning is still reading
        metadata and writing SQLite rows.  Starting backup during that window could
        previously make the UI look frozen or hit database/file-state races.
        """
        if self._busy:
            self.info_box(
                "任务正在进行",
                f"{action_zh}需要等待当前任务完成后再执行。请先等待扫描/生成流程结束。",
                "Task in progress",
                f"{action_en or action_zh} must wait until the current task finishes. Please wait for scanning or generation to finish.",
            )
            return False
        return True

    def backup_display_name(self, manifest_path: Path) -> str:
        try:
            data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            typ = data.get("snapshotType", "snapshot")
            created = data.get("createdAt", "")
            label = data.get("label", "")
            count = len(data.get("entries", []) or [])
            if typ == "initial_state":
                root = data.get("gameRoot", "")
                base = self.ui_text("初始状态", "Initial state")
                if root:
                    return f"{base} · {created} · {Path(root).name or root} · {count} files"
                return f"{base} · {created} · {count} files"
            label_map = {
                "manual_backup_point": self.ui_text("手动备份点", "Manual backup point"),
                "before_one_click_replace": self.ui_text("一键替换前", "Before one-click replacement"),
                "v2_xml": self.ui_text("XML 输出前", "Before XML output"),
                "package_xml_stage": self.ui_text("生成输出包前", "Before package output"),
                "one_click_xml_stage": self.ui_text("一键替换 XML 阶段", "One-click XML stage"),
            }
            label_text = label_map.get(label, label or typ)
            return f"{created} · {label_text} · {count} files"
        except Exception:
            return Path(manifest_path).name

    def list_backup_manifests(self) -> list[Path]:
        root = project_backup_dir()
        paths = []
        if root.exists():
            paths.extend(root.glob("*/backup_manifest.json"))
            paths.extend(root.glob("_state_backups/initial/*/initial_state_manifest.json"))
        def key(p: Path):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get("createdAt", "")
            except Exception:
                return str(p)
        return sorted({p.resolve(): p for p in paths}.values(), key=key, reverse=True)

    def _on_task_heartbeat(self) -> None:
        if not self._busy:
            return
        elapsed = int(max(0, time.monotonic() - self._task_started_at))
        title = self._task_title or self.ui_text("任务", "Task")
        status = self.ui_text(
            f"{title} 正在运行... {elapsed}s。界面仍可响应；Extract/Rebuild 运行时请不要关闭 Fmod Bank Tools。",
            f"{title} running... {elapsed}s. UI is responsive; do not close Fmod Bank Tools while Extract/Rebuild is running.",
        )
        self.statusBar().showMessage(status)
        if hasattr(self, "compact_progress_label") and self._last_compact_progress:
            elapsed_line = self.ui_text(f"已运行 {elapsed}s。", f"Elapsed: {elapsed}s.")
            lines = list(self._compact_progress_lines[-2:])
            if not lines or lines[-1] != elapsed_line:
                self.compact_progress_label.setText("\n".join((lines + [elapsed_line])[-3:]))
        self.progress.repaint()

    def txt_path(self, edit: QLineEdit) -> Path | None:
        text = edit.text().strip().strip('"')
        return Path(text) if text else None

    def _compact_progress_text(self, value: int, message: str) -> str:
        msg = str(message or "").strip()
        if msg.startswith(COMPACT_PROGRESS_PREFIX):
            msg = msg[len(COMPACT_PROGRESS_PREFIX):].strip()
        pct = max(0, min(100, int(value)))
        return f"[{pct:3d}%] {msg}" if msg else f"[{pct:3d}%]"

    def _set_compact_progress(self, value: int, message: str) -> None:
        if not hasattr(self, "compact_progress_label"):
            return
        line = self._compact_progress_text(value, message)
        if not line.strip():
            return
        if not self._compact_progress_lines or self._compact_progress_lines[-1] != line:
            self._compact_progress_lines.append(line)
            self._compact_progress_lines = self._compact_progress_lines[-3:]
        self._last_compact_progress = line
        self.compact_progress_label.setText("\n".join(self._compact_progress_lines[-3:]))

    def log(self, text: str) -> None:
        self.log_box.appendPlainText(self.log_text(str(text)))

    def set_progress(self, value: int, message: str = "") -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, int(value))))
        if message:
            progress_only = str(message).startswith(COMPACT_PROGRESS_PREFIX)
            clean_message = str(message)[len(COMPACT_PROGRESS_PREFIX):] if progress_only else str(message)
            self._set_compact_progress(value, clean_message)
            if not progress_only:
                self.log(clean_message)

    def run_background_task(self, title: str, job, on_success=None, *, estimated: str = "") -> None:
        """Run a long operation in a QThread while all UI work stays on GUI thread."""
        if self._busy:
            self.info_box("任务正在进行", "已有耗时任务正在执行，请等待当前任务完成。", "Task in progress", "A long task is already running. Please wait for it to finish.")
            return
        self._busy = True
        self._task_title = title
        self._task_started_at = time.monotonic()
        # Do not set the global wait cursor.  The Windows busy cursor made
        # users think the app was frozen during external Extract/Rebuild.
        # Keep the central widget enabled.  Earlier builds disabled the whole
        # window during one-click Extract/Rebuild; on Windows this made the app
        # look frozen even though the worker thread was still running.  `_busy`
        # prevents starting another long task, while the UI stays paintable and
        # draggable.
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._compact_progress_lines = []
        self._last_compact_progress = ""
        if hasattr(self, "compact_progress_label"):
            self.compact_progress_label.setText(self.ui_text("正在启动任务...", "Starting task..."))
        self.progress.setFormat(f"{title} - %p%")
        self._heartbeat_timer.start()
        self.statusBar().showMessage(self.ui_text(f"{title} 正在运行... 你可以移动窗口并查看进度。", f"{title} running... You can move the window and read progress below."))
        if estimated:
            self.log(f"[TASK] {title} 开始。预计耗时：{estimated}")
        else:
            self.log(f"[TASK] {title} 开始。")

        # Do not parent QThread to MainWindow while cleaning up from queued
        # signals; keep explicit references until thread.finished.
        thread = QThread()
        worker = BackgroundTask(job)
        bridge = MainThreadTaskBridge()
        worker.moveToThread(thread)
        self._task_thread = thread
        self._task_worker = worker
        self._task_bridge = bridge

        def finish_ui(ok: bool) -> None:
            # This function is only called from bridge signals, hence GUI thread.
            self._busy = False
            self._heartbeat_timer.stop()
            self.statusBar().clearMessage()
            self.progress.setFormat("%p%")
            # Cursor is intentionally left as the normal system cursor.
            if ok:
                self.progress.setRange(0, 100)
                self.progress.setValue(100)
                if hasattr(self, "compact_progress_label"):
                    self.compact_progress_label.setText(self.ui_text("任务完成。", "Task finished."))
            else:
                if hasattr(self, "compact_progress_label"):
                    self.compact_progress_label.setText(self.ui_text("任务失败，请查看日志。", "Task failed. See the log."))

        def handle_success(result):
            finish_ui(True)
            if on_success:
                on_success(result)

        def handle_failed(trace_text: str):
            finish_ui(False)
            self.log(f"[ERROR] {title} 失败：\n{trace_text}")
            last = trace_text.strip().splitlines()[-1] if trace_text.strip() else "未知错误"
            self.error_box(f"{title} 失败", last, f"{title} failed", last)

        def clear_refs():
            if self._task_thread is thread:
                self._task_thread = None
            if self._task_worker is worker:
                self._task_worker = None
            if self._task_bridge is bridge:
                self._task_bridge = None

        # Worker signals are delivered to bridge slots in the bridge object's
        # thread (the GUI thread).  Then bridge signals call UI handlers safely.
        worker.progress.connect(bridge.on_progress, Qt.QueuedConnection)
        worker.succeeded.connect(bridge.on_success, Qt.QueuedConnection)
        worker.failed.connect(bridge.on_failure, Qt.QueuedConnection)
        worker.finished.connect(bridge.on_finished, Qt.QueuedConnection)

        bridge.progress_ready.connect(self.set_progress)
        bridge.success_ready.connect(handle_success)
        bridge.failure_ready.connect(handle_failed)
        bridge.finished_ready.connect(thread.quit)

        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(clear_refs)
        thread.started.connect(worker.run)
        thread.start()

    def browse_game_root(self):
        p = QFileDialog.getExistingDirectory(self, self.ui_text("选择游戏根目录", "Choose game root folder"))
        if p:
            self.game_root_edit.setText(p)
            self.store.set_setting("game_root", p)
            self.scan_game_root()

    def browse_music_dir(self):
        p = QFileDialog.getExistingDirectory(self, self.ui_text("选择音乐目录", "Choose music folder"))
        if p:
            self.music_dir_edit.setText(p)
            self.store.set_setting("music_dir", p)
            self.scan_music_dir()

    def browse_xml(self):
        p, _ = QFileDialog.getOpenFileName(self, self.ui_text("选择 RadioInfo XML", "Choose RadioInfo XML"), "", "XML (*.xml);;All files (*.*)")
        if p:
            self.xml_edit.setText(p)
            self.load_xml(Path(p))

    def browse_extract_dir(self):
        p = QFileDialog.getExistingDirectory(self, self.ui_text("选择 Fmod Bank Tools Extract 的 Wav Output Directory", "Choose Fmod Bank Tools Wav Output Directory"))
        if p:
            self.extract_dir_edit.setText(p)

    def browse_bank_root(self):
        p = QFileDialog.getExistingDirectory(self, self.ui_text("选择 bank 文件所在目录", "Choose bank folder"))
        if p:
            self.bank_root_edit.setText(p)
            self.store.set_setting("bank_root", p)

    def browse_fmod_tool(self):
        p, _ = QFileDialog.getOpenFileName(self, self.ui_text("选择 Fmod Bank Tools 可执行文件", "Choose Fmod Bank Tools executable"), "", "Executable (*.exe);;All files (*.*)")
        if p:
            self.fmod_tool_edit.setText(p)
            self.store.set_setting("fmod_tool", p)

    def browse_main_menu_bank(self):
        start = ""
        existing = self.main_menu_bank_edit.text().strip() if hasattr(self, "main_menu_bank_edit") else ""
        if existing:
            start = str(Path(existing).parent)
        else:
            try:
                start = str(self._current_bank_root())
            except Exception:
                start = ""
        p, _ = QFileDialog.getOpenFileName(
            self,
            self.ui_text("选择主菜单/前端音乐 bank", "Choose main menu/frontend music bank"),
            start,
            "FMOD Bank (*.bank);;All files (*.*)",
        )
        if p:
            self.main_menu_bank_edit.setText(p)
            self.store.set_setting("main_menu_bank_path", p)

    def browse_main_menu_audio(self):
        start = ""
        existing = self.main_menu_audio_edit.text().strip() if hasattr(self, "main_menu_audio_edit") else ""
        if existing:
            start = str(Path(existing).parent)
        elif self.music_dir_edit.text().strip():
            start = self.music_dir_edit.text().strip()
        p, _ = QFileDialog.getOpenFileName(
            self,
            self.ui_text("选择新的主菜单音乐", "Choose new main menu music"),
            start,
            "Audio (*.wav *.flac *.mp3 *.ogg *.m4a);;WAV (*.wav);;All files (*.*)",
        )
        if p:
            self.main_menu_audio_edit.setText(p)
            self.store.set_setting("main_menu_audio_path", p)

    def _score_main_menu_bank_candidate(self, path: Path) -> tuple[int, str]:
        name = path.name.lower().replace('-', '_')
        score = 0
        # Prefer banks that are likely to hold long frontend/menu music and avoid
        # metadata/master/string/dialogue banks.  The selected bank is still shown
        # to the user before any Extract/Rebuild operation runs.
        preferred = ["mainmenu", "main_menu", "frontend", "front_end", "menu", "title", "boot", "intro", "festival", "opening"]
        for idx, token in enumerate(preferred):
            if token in name:
                score -= 120 - idx * 5
        if "music" in name:
            score -= 35
        if "amb" in name or "ambience" in name:
            score -= 10
        for bad in ("master", "strings", "vo_", "voice", "dialog", "speech", "radio", "tracks", "stinger", "sfx", "ui_sfx"):
            if bad in name:
                score += 100
        if name.endswith(".assets.bank"):
            score -= 5
        score += len(str(path)) // 25
        return (score, path.as_posix().lower())

    def auto_detect_main_menu_bank(self):
        try:
            bank_root = self._current_bank_root()
        except Exception as exc:
            self.show_error("自动查找主菜单 bank 失败", exc)
            return
        keywords = ["mainmenu", "main_menu", "frontend", "front_end", "menu", "title", "boot", "intro", "festival", "opening", "music"]
        try:
            all_banks = sorted(Path(bank_root).rglob("*.bank"), key=lambda p: p.as_posix().lower())
        except Exception as exc:
            self.show_error("自动查找主菜单 bank 失败", exc)
            return
        candidates = []
        for p in all_banks:
            name = p.name.lower().replace('-', '_')
            if any(k in name for k in keywords) and not any(bad in name for bad in ("master", "strings", "vo_", "voice", "dialog", "speech")):
                candidates.append(p)
        extractable = [p for p in candidates if bank_contains_fsb_audio(p)]
        ranked = sorted(extractable or candidates, key=self._score_main_menu_bank_candidate)
        if not ranked:
            self.warn_box(
                "未找到候选 bank",
                "没有在 FMODBanks 中找到名称包含 menu/frontend/title/boot/festival/music 的候选 bank。请手动选择之前确认过的主菜单音乐 bank。",
                "No candidate bank found",
                "No bank name containing menu/frontend/title/boot/festival/music was found in FMODBanks. Please choose the confirmed main-menu music bank manually.",
            )
            return
        labels = [f"{p.name}  —  {p.parent}" for p in ranked[:30]]
        choice, ok = QInputDialog.getItem(
            self,
            self.ui_text("选择主菜单音乐 bank", "Choose main menu music bank"),
            self.ui_text("请选择候选 bank：", "Choose a candidate bank:"),
            labels,
            0,
            False,
        )
        if ok and choice:
            idx = labels.index(choice)
            selected = ranked[idx]
            self.main_menu_bank_edit.setText(str(selected))
            self.store.set_setting("main_menu_bank_path", str(selected))
            self.log(f"[MENU][OK] 已选择主菜单候选 bank: {selected}")

    def _refresh_station_combo_width(self) -> None:
        """Keep the target-radio combo readable after first XML/game scan.

        The first game-root scan can populate the combo before Qt has settled the
        wizard layout.  Recomputing a modest minimum width prevents the first
        session from showing a collapsed or clipped target-radio selector.
        """
        try:
            min_width = 420
            metrics = self.station_combo.fontMetrics()
            for i in range(self.station_combo.count()):
                min_width = max(min_width, metrics.horizontalAdvance(self.station_combo.itemText(i)) + 80)
            self.station_combo.setMinimumWidth(min(760, min_width))
            self.station_combo.updateGeometry()
            if self.station_combo.parentWidget():
                self.station_combo.parentWidget().updateGeometry()
        except Exception:
            pass

    def scan_game_root(self):
        root = self.txt_path(self.game_root_edit)
        if not root:
            self.warn_box("缺少路径", "请先选择游戏根目录。", "Missing path", "Please choose the game root folder first.")
            return

        def job(report):
            report(5, f"[SCAN] 正在扫描游戏根目录: {root}")
            result = scan_game_root(root)
            report(85, "写入扫描报告。")
            write_scan_report(result, project_work_dir() / "v2_game_scan_report.json")
            report(100, "游戏扫描完成。")
            return result

        def done(result):
            self.xml_candidates = [Path(p) for p in result.xml_candidates]
            self.log(f"[OK] 找到 XML: {len(result.xml_candidates)}，bank: {len(result.bank_candidates)}，电台: {result.station_count}")
            for w in result.warnings:
                self.log(f"[WARN] {w}")
            selected_xml = self._select_xml_for_game_language(self.xml_candidates, result.selected_xml)
            if selected_xml:
                self.xml_edit.setText(str(selected_xml))
                self.load_xml(Path(selected_xml))
                self.log(f"[OK] 根据游戏语言选择 XML: {selected_xml}")
            self.store.set_setting("game_root", str(root))
            if result.bank_root:
                self.bank_root_edit.setText(str(result.bank_root))
                self.store.set_setting("auto_bank_root", str(result.bank_root))
                self.store.set_setting("bank_root", str(result.bank_root))  # legacy compatibility
                self.log(f"[OK] 自动定位 Bank 目录: {result.bank_root}")
            self.set_progress(100, "[OK] 游戏扫描完成。")
            self._auto_hide_setup_if_ready()

        self.run_background_task("扫描游戏根目录", job, done, estimated="小型目录数秒；完整游戏目录通常 10–60 秒。")

    def load_xml(self, xml_path: Path, quiet: bool = False):
        old_xml = self.current_xml
        try:
            tree = parse_xml(xml_path)
            # Set current_xml before populating the station combo so duplicate/alias
            # filtering used by _station_combo_label sees the same XML as reload_slots.
            self.current_xml = Path(xml_path)
            self.station_infos = list_station_infos(tree)
            self.station_combo.blockSignals(True)
            self.station_combo.clear()
            for st in self.station_infos:
                self.station_combo.addItem(self._station_combo_label(st), st.name)
            self.station_combo.blockSignals(False)
            self._refresh_station_combo_width()
            QTimer.singleShot(0, self._refresh_station_combo_width)
            self.store.set_setting("xml_path", str(xml_path))
            if not quiet:
                self.log(f"[OK] XML 已加载: {xml_path}")
            self.reload_slots()
        except Exception as exc:
            self.current_xml = old_xml
            if quiet:
                self.log(f"[WARN] 上次 XML 无法加载: {exc}")
            else:
                self.show_error("加载 XML 失败", exc)

    def current_station_name(self) -> str | None:
        data = self.station_combo.currentData()
        return str(data) if data else None

    def _dynamic_station_slot_profiles(self) -> dict[str, dict[str, object]]:
        """Load station slot profiles generated by developer diagnostics.

        v3.0.26 keeps this parser compatible with both the old summary fields
        and the new unified station profile report.  The new report is based on
        the same visible-slot filtering used by the normal UI and replacement
        path, so it should no longer mark a station as limited merely because
        the raw XML contains hidden *_ID/*_LI aliases.
        """
        def to_int(value, default: int = 0) -> int:
            try:
                return int(str(value or "").strip())
            except Exception:
                return default

        def parse_slots(text: object) -> list[int]:
            out: list[int] = []
            raw = str(text or "")
            for token in raw.replace(";", ",").replace("，", ",").split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    out.append(int(token))
                except Exception:
                    pass
            return out

        profiles: dict[str, dict[str, object]] = {}
        candidates = [
            project_work_dir() / "station_slot_profiles.csv",
            project_work_dir() / "dev_all_station_match_test" / "dev_all_station_match_summary.csv",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        station = str(row.get("station") or "").strip()
                        if not station:
                            continue
                        xml_tracks = to_int(row.get("xml_entries") or row.get("xml_tracks"))
                        visible_slots = to_int(row.get("visible_replaceable_slots"), -1)
                        records = to_int(row.get("records"), 0)
                        message = str(row.get("message") or "")
                        old_xml_only = _parse_slot_list_from_dev_message(message, "xml_only=")
                        old_fatal = _parse_slot_list_from_dev_message(message, "fatal=")
                        hidden = parse_slots(row.get("hidden_slots") or row.get("hidden_alias_slot_indices") or row.get("hidden_alias_slots_list"))
                        hidden_count = to_int(row.get("hidden_alias_slots"), len(hidden))
                        unmatched_visible = parse_slots(row.get("unmatched_visible_slots"))
                        status = str(row.get("status") or "").strip() or "unknown"
                        if "streamer" in station.lower():
                            status = "special"
                        elif visible_slots >= 0:
                            if not unmatched_visible and status not in {"special", "error", "exception", "extract_failed"}:
                                status = "ok"
                            elif unmatched_visible:
                                status = "limited"
                        else:
                            hidden = sorted(set(old_xml_only + old_fatal))
                            visible_slots = max(0, xml_tracks - len(hidden)) if xml_tracks else records
                            if hidden or (xml_tracks and records and records < xml_tracks):
                                status = "limited"
                            elif status not in {"special", "error", "exception", "extract_failed"}:
                                status = "ok"
                        profiles[station] = {
                            "xml_tracks": xml_tracks,
                            "fmod_audio_slots": visible_slots if visible_slots >= 0 else records,
                            "non_replaceable_slots": hidden,
                            "status": status,
                            "source": str(path),
                            "hidden_alias_slots": hidden_count,
                        }
            except Exception as exc:
                self.log(f"[WARN] 读取电台槽位画像失败: {path}: {exc}")
        return profiles

    def _station_slot_profile(self, station_name: str | None = None, xml_tracks: int | None = None) -> dict[str, object] | None:
        station = station_name or self.current_station_name()
        if not station:
            return None
        # v3.0.26: R1/R2 stale developer summaries made before multi-Track-bank
        # support must not hide Disk-bank rows.  Do not bypass the whole profile
        # path, though, because duplicate-alias filtering still needs to run for
        # stations such as R2.  Instead, ignore only dynamic summaries for known
        # complete multi-bank stations.
        built_in = KNOWN_STATION_SLOT_PROFILES.get(str(station))
        dynamic = None if str(station) in KNOWN_MULTI_TRACK_COMPLETE_STATIONS else self._dynamic_station_slot_profiles().get(str(station))
        base = built_in or dynamic
        if not base:
            # Even otherwise-normal stations can contain duplicate internal alias
            # rows.  Hide only the suffixed duplicate rows when a plain row with
            # the same DisplayName/Artist exists.
            try:
                if self.current_xml and station:
                    rows_for_dup = station_sample_rows(self.current_xml, str(station))
                    dup_hidden = _duplicate_variant_hidden_slots(rows_for_dup)
                    if dup_hidden:
                        xml_n0 = int(xml_tracks or len(rows_for_dup) or 0)
                        return {
                            "xml_tracks": xml_n0,
                            "fmod_audio_slots": max(0, xml_n0 - len(dup_hidden)),
                            "non_replaceable_slots": dup_hidden,
                            "status": "duplicate_alias_filtered",
                            "source": "dynamic_duplicate_variant_filter",
                        }
            except Exception:
                pass
            return None
        profile = dict(base)
        if xml_tracks is not None:
            profile["xml_tracks"] = int(xml_tracks)
        try:
            non = sorted({int(x) for x in profile.get("non_replaceable_slots", [])})
        except Exception:
            non = []
        # Add data-driven duplicate alias filtering, e.g. *_ID / *_LI rows when
        # a plain DisplayName/Artist row exists.  This keeps normal UI focused on
        # the rows the in-game radio actually uses, while retaining diagnostics
        # in the XML reports.
        try:
            if self.current_xml and station:
                rows_for_dup = station_sample_rows(self.current_xml, str(station))
                non = sorted(set(non) | set(_duplicate_variant_hidden_slots(rows_for_dup)))
        except Exception:
            pass
        profile["non_replaceable_slots"] = non
        try:
            xml_n = int(profile.get("xml_tracks") or (xml_tracks or 0))
            fmod_n = int(profile.get("fmod_audio_slots") or max(0, xml_n - len(non)))
        except Exception:
            xml_n = int(xml_tracks or 0)
            fmod_n = max(0, xml_n - len(non))
        profile["xml_tracks"] = xml_n
        profile["fmod_audio_slots"] = fmod_n
        if str(profile.get("status") or "") == "ok" and not non and fmod_n >= xml_n:
            return None
        return profile

    def _station_slot_profile_text(self, profile: dict[str, object] | None) -> str:
        # Keep the main UI simple for normal players.  The detailed XML-vs-FMOD
        # difference is still written to diagnostic CSV files, but the visible
        # UI should only show the usable slot count.
        return ""

    def _is_slot_replaceable(self, slot: int, profile: dict[str, object] | None = None) -> bool:
        if not profile:
            return True
        try:
            non = {int(x) for x in profile.get("non_replaceable_slots", [])}
        except Exception:
            non = set()
        return int(slot) not in non

    def _slot_row_replaceable(self, row_index: int) -> bool:
        item = self.slot_table.item(row_index, 1)
        if not item:
            return True
        try:
            slot = int(item.text())
        except Exception:
            return True
        profile = self._station_slot_profile(self.current_station_name(), self.slot_table.rowCount())
        return self._is_slot_replaceable(slot, profile)

    def _refresh_station_combo_labels(self) -> None:
        if not hasattr(self, "station_combo"):
            return
        if not getattr(self, "station_infos", None):
            return
        current = self.station_combo.currentData()
        self.station_combo.blockSignals(True)
        self.station_combo.clear()
        for st in self.station_infos:
            self.station_combo.addItem(self._station_combo_label(st), st.name)
        if current is not None:
            idx = self.station_combo.findData(current)
            if idx >= 0:
                self.station_combo.setCurrentIndex(idx)
        self.station_combo.blockSignals(False)
        self._refresh_station_combo_width()

    def _station_combo_label(self, station_info) -> str:
        profile = self._station_slot_profile(getattr(station_info, "name", ""), int(getattr(station_info, "track_slot_count", 0) or 0))
        if profile:
            xml_n = int(profile.get("xml_tracks") or getattr(station_info, "track_slot_count", 0) or 0)
            fmod_n = int(profile.get("fmod_audio_slots") or max(0, xml_n - len(profile.get("non_replaceable_slots", []))))
            suffix = f"{fmod_n} slots" if self.ui_lang() == "en" else f"槽位 {fmod_n}"
            return f"{station_info.name}  ({suffix})"
        suffix = f"{station_info.track_slot_count} tracks" if self.ui_lang() == "en" else f"槽位 {station_info.track_slot_count}"
        return f"{station_info.name}  ({suffix})"

    def _update_station_profile_label(self, station: str | None = None, xml_tracks: int | None = None) -> None:
        if not hasattr(self, "station_profile_label"):
            return
        profile = self._station_slot_profile(station or self.current_station_name(), xml_tracks)
        text = self._station_slot_profile_text(profile)
        self.station_profile_label.setText(text)
        self.station_profile_label.setVisible(bool(text))

    def reload_slots(self):
        self.slot_table.setRowCount(0)
        if not self.current_xml:
            return
        station = self.current_station_name()
        if not station:
            return
        try:
            from .order_tools import station_sample_rows
            rows = station_sample_rows(self.current_xml, station)
            profile_info = self._station_slot_profile(station, len(rows))
            self._update_station_profile_label(station, len(rows))
            # Hide XML-only / non-replaceable rows from the normal player-facing
            # list.  The diagnostic reports still keep those XML entries, but
            # the interactive replacement UI should only expose real FMOD audio
            # slots that can be safely replaced.
            visible_rows = [r for r in rows if self._is_slot_replaceable(int(r["slot_index"]), profile_info)]
            assignments = self.store.get_assignments(station)
            profiles = {p.track_key: p for p in self.store.list_track_profiles()}
            self.slot_table.setRowCount(len(visible_rows))
            for i, row in enumerate(visible_rows):
                slot = int(row["slot_index"])
                key = assignments.get(slot)
                profile = profiles.get(key) if key else None
                marker_text = ""
                if profile and profile.markers:
                    marker_text = ", ".join(f"{k}={v}" for k, v in profile.markers.items() if k in ("TrackLoopStart", "TrackLoopEnd", "PostRaceLoopStart", "PostRaceLoopEnd"))
                status = "已勾选替换" if profile else "保持原样"
                set_check_item(self.slot_table, i, 0, bool(profile), data=slot)
                set_item(self.slot_table, i, 1, slot, data=slot)
                set_item(self.slot_table, i, 2, row["original_display_name"])
                set_item(self.slot_table, i, 3, row["original_artist"])
                set_item(self.slot_table, i, 4, row["sound_name"])
                set_item(self.slot_table, i, 5, row["sample_length"])
                set_item(self.slot_table, i, 6, row["sample_rate"])
                set_item(self.slot_table, i, 7, profile.filename if profile else "")
                set_item(self.slot_table, i, 8, marker_text)
                set_item(self.slot_table, i, 9, status)
        except Exception as exc:
            self.show_error("刷新槽位失败", exc)

    def scan_music_dir(self, quiet: bool = False):
        folder = self.txt_path(self.music_dir_edit)
        self.audio_paths = []
        self.music_table.setRowCount(0)
        self.loop_audio_combo.clear()
        if not folder:
            return

        def scan_job(report=None):
            if report:
                report(5, f"[MUSIC] 正在扫描音乐目录: {folder}")
            store = StateStore(self.store.db_path)
            audio_paths = list_audio_candidates(folder)
            profiles = {p.track_key: p for p in store.list_track_profiles()}
            rows = []
            total = max(1, len(audio_paths))
            for i, path in enumerate(audio_paths):
                key = track_key_for_path(path)
                profile = profiles.get(key)
                fmt = path.suffix.lower().lstrip(".")
                sr = ""
                dur = ""
                has_profile = bool(profile)
                try:
                    if path.suffix.lower() in (".wav", ".wave"):
                        info = read_wav_info(path)
                        sr = str(info.samplerate)
                        dur = f"{info.duration_sec:.2f}s"
                        if not profile:
                            name, artist = guess_display_artist_from_filename(path.name)
                            store.save_track_profile(TrackProfile(
                                track_key=key,
                                source_path=str(path),
                                filename=path.name,
                                display_name=name,
                                artist=artist,
                                sample_rate=info.samplerate,
                                sample_length=info.sample_length,
                                markers=marker_values_for_save(safe_default_marker_values(max(0, info.sample_length - 1))),
                            ))
                            has_profile = True
                    elif not profile:
                        name, artist = guess_display_artist_from_filename(path.name)
                        store.save_track_profile(TrackProfile(key, str(path), path.name, name, artist))
                        has_profile = True
                except Exception as exc:
                    dur = f"读取失败: {exc}"
                artist_text = (profile.artist if profile and profile.artist else guess_display_artist_from_filename(path.name)[1])
                rows.append((path, key, fmt, sr, dur, has_profile, artist_text))
                if report and (i % max(1, total // 20) == 0 or i + 1 == total):
                    report(5 + int(90 * (i + 1) / total), f"[MUSIC] 读取音频信息 {i + 1}/{total}: {path.name}")
            store.set_setting("music_dir", str(folder))
            if report:
                report(100, "音乐扫描完成。")
            return audio_paths, rows

        def populate(result):
            audio_paths, rows = result
            self.audio_paths = list(audio_paths)
            self._populating_music_table = True
            self.music_table.setRowCount(len(rows))
            for i, (path, key, fmt, sr, dur, has_profile, artist_text) in enumerate(rows):
                set_check_item(self.music_table, i, 0, False, data=key)
                set_item(self.music_table, i, 1, path.name, data=key)
                artist_item = QTableWidgetItem(str(artist_text or ""))
                artist_item.setData(Qt.UserRole, key)
                self.music_table.setItem(i, 2, artist_item)
                set_item(self.music_table, i, 3, fmt)
                set_item(self.music_table, i, 4, sr)
                set_item(self.music_table, i, 5, dur)
                set_item(self.music_table, i, 6, "是" if has_profile else "否")
                set_item(self.music_table, i, 7, str(path))
                self.loop_audio_combo.addItem(path.name, str(path))
            self._populating_music_table = False
            self.store.set_setting("music_dir", str(folder))
            if not quiet:
                self.log(f"[OK] 音乐扫描完成: {len(self.audio_paths)} 首")
            self._auto_hide_setup_if_ready()

        if quiet:
            try:
                populate(scan_job(None))
            except Exception as exc:
                self.log(f"[WARN] 上次音乐目录无法扫描: {exc}")
            return

        self.run_background_task("扫描音乐目录", scan_job, populate, estimated="几十首音乐通常数秒；大量 WAV/网络盘可能 10–60 秒。")

    def apply_table_layout(self) -> None:
        """Keep the replacement tables compact and aligned."""
        try:
            self.slot_table.setColumnWidth(0, 54)
            self.slot_table.setColumnWidth(1, 56)
            self.slot_table.setColumnWidth(2, 140)
            self.slot_table.setColumnWidth(3, 110)
            self.slot_table.setColumnWidth(5, 105)
            self.slot_table.setColumnWidth(6, 85)
            self.music_table.setColumnWidth(0, 54)
            self.music_table.setColumnWidth(1, 170)
            self.music_table.setColumnWidth(2, 115)
            self.music_table.setColumnWidth(3, 70)
            self.music_table.setColumnWidth(4, 82)
            self.music_table.setColumnWidth(5, 82)
            self.music_table.setColumnWidth(6, 92)
            self.music_table.setColumnWidth(7, 220)
            self.slot_table.verticalHeader().setDefaultSectionSize(30)
            self.music_table.verticalHeader().setDefaultSectionSize(30)
            self.slot_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
            self.music_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
            self.slot_table.setAlternatingRowColors(False)
            self.music_table.setAlternatingRowColors(False)
        except Exception:
            pass

    def on_slot_header_clicked(self, section: int) -> None:
        if section == 0:
            self.toggle_all_slot_checks()

    def on_music_header_clicked(self, section: int) -> None:
        if section == 0:
            self.toggle_all_music_checks()

    def checked_table_rows(self, table: QTableWidget, check_col: int = 0) -> list[int]:
        rows: list[int] = []
        for r in range(table.rowCount()):
            item = table.item(r, check_col)
            if item and item.checkState() == Qt.Checked:
                rows.append(r)
        return rows

    def selected_table_rows(self, table: QTableWidget) -> list[int]:
        rows = {idx.row() for idx in table.selectionModel().selectedRows()} if table.selectionModel() else set()
        if not rows:
            rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows and table.currentRow() >= 0:
            rows = {table.currentRow()}
        return sorted(rows)

    def selected_slot_rows(self) -> list[int]:
        checked = self.checked_table_rows(self.slot_table, 0)
        rows = checked if checked else self.selected_table_rows(self.slot_table)
        return [r for r in rows if self._slot_row_replaceable(r)]

    def selected_music_rows(self) -> list[int]:
        checked = self.checked_table_rows(self.music_table, 0)
        return checked if checked else self.selected_table_rows(self.music_table)

    def set_all_slot_checks(self, checked: bool) -> None:
        for r in range(self.slot_table.rowCount()):
            item = self.slot_table.item(r, 0)
            if item and self._slot_row_replaceable(r):
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def set_all_music_checks(self, checked: bool) -> None:
        for r in range(self.music_table.rowCount()):
            item = self.music_table.item(r, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def toggle_all_slot_checks(self) -> None:
        total = sum(1 for r in range(self.slot_table.rowCount()) if self._slot_row_replaceable(r))
        checked = len(self.checked_table_rows(self.slot_table, 0))
        self.set_all_slot_checks(not (total > 0 and checked == total))

    def toggle_all_music_checks(self) -> None:
        total = self.music_table.rowCount()
        checked = len(self.checked_table_rows(self.music_table, 0))
        self.set_all_music_checks(not (total > 0 and checked == total))

    def slot_info_from_row(self, row: int) -> tuple[int, str, str, str]:
        slot_item = self.slot_table.item(row, 1)
        if not slot_item:
            raise ValueError(f"无效 Slot 行: {row}")
        slot = int(slot_item.text())
        sound = self.slot_table.item(row, 4).text() if self.slot_table.item(row, 4) else ""
        old_name = self.slot_table.item(row, 2).text() if self.slot_table.item(row, 2) else ""
        old_artist = self.slot_table.item(row, 3).text() if self.slot_table.item(row, 3) else ""
        return slot, sound, old_name, old_artist

    def music_path_and_key_from_row(self, row: int) -> tuple[Path, str]:
        path_item = self.music_table.item(row, 7)
        key_item = self.music_table.item(row, 1) or self.music_table.item(row, 0)
        if not path_item or not key_item:
            raise ValueError(f"无效音乐行: {row}")
        return Path(path_item.text()), str(key_item.data(Qt.UserRole))

    def selected_slot_index(self) -> int | None:
        row = self.slot_table.currentRow()
        if row < 0:
            return None
        item = self.slot_table.item(row, 1)
        if not item:
            return None
        return int(item.text())

    def selected_music_path_and_key(self) -> tuple[Path, str] | None:
        row = self.music_table.currentRow()
        if row < 0:
            return None
        path_item = self.music_table.item(row, 7)
        key_item = self.music_table.item(row, 1) or self.music_table.item(row, 0)
        if not path_item or not key_item:
            return None
        return Path(path_item.text()), str(key_item.data(Qt.UserRole))

    def assign_checked_or_current_music_to_slots(self):
        """Unified single/batch assignment button for normal users."""
        slot_rows = self.selected_slot_rows()
        music_rows = self.selected_music_rows()
        if len(slot_rows) == 1 and len(music_rows) == 1:
            self.slot_table.setCurrentCell(slot_rows[0], 1)
            self.music_table.setCurrentCell(music_rows[0], 1)
            return self.assign_selected_music_to_slot()
        return self.batch_assign_selected_music_to_slots()

    def assign_selected_music_to_slot(self):
        station = self.current_station_name()
        slot = self.selected_slot_index()
        sel = self.selected_music_path_and_key()
        if not station or slot is None or sel is None:
            self.warn_box("缺少选择", "请同时选择目标电台 Slot 和音乐文件。", "Missing selection", "Please select both a target radio slot and a music file.")
            return
        if not self._is_slot_replaceable(slot, self._station_slot_profile(station, self.slot_table.rowCount())):
            self.warn_box(
                "槽位不可替换",
                "该行是仅 XML 条目或没有独立 FMOD 音频，不能替换。",
                "Slot is not replaceable",
                "This row is XML-only or has no independent FMOD audio slot, so it cannot be replaced.",
            )
            return
        try:
            path, key = sel
            row = self.slot_table.currentRow()
            sound, old_name, old_artist = self.slot_info_from_row(row)[1:]
            self._save_one_assignment(station, slot, path, key, sound, old_name, old_artist, "manual")
            self.log(f"[OK] {station} slot {slot} <- {path.name}")
            self.reload_slots()
        except Exception as exc:
            self.show_error("分配失败", exc)

    def _save_one_assignment(self, station: str, slot: int, path: Path, key: str, sound: str, old_name: str, old_artist: str, confidence: str) -> None:
        profile = self.store.load_track_profile(key)
        if not profile:
            name, artist = guess_display_artist_from_filename(path.name)
            profile = TrackProfile(key, str(path), path.name, name, artist)
            self.store.save_track_profile(profile)
        self.store.save_assignment(station, slot, key, sound, old_name, old_artist, confidence)

    def batch_assign_selected_music_to_slots(self):
        """Assign selected songs to selected slots in visible order.

        This is intentionally deterministic: the topmost selected slot receives
        the topmost selected music row.  It avoids guessing users' preference by
        filename unless a later version adds explicit matching strategies.
        """
        station = self.current_station_name()
        slot_rows = self.selected_slot_rows()
        music_rows = self.selected_music_rows()
        if not station:
            self.warn_box("缺少电台", "请先选择目标电台。", "Missing radio station", "Please choose a target radio station first.")
            return
        if not slot_rows or not music_rows:
            self.warn_box("缺少选择", "请在左侧选择要替换的 Slot，并在右侧选择等量音乐。", "Missing selection", "Please select slots on the left and the same number of music files on the right.")
            return
        if len(slot_rows) != len(music_rows):
            self.warn_box("数量不一致", f"已选择 {len(slot_rows)} 个 Slot，但选择了 {len(music_rows)} 首音乐。请保持数量一致。", "Count mismatch", f"Selected {len(slot_rows)} slot(s), but selected {len(music_rows)} music file(s). Please select the same number.")
            return
        try:
            pairs: list[str] = []
            for slot_row, music_row in zip(slot_rows, music_rows):
                slot, sound, old_name, old_artist = self.slot_info_from_row(slot_row)
                path, key = self.music_path_and_key_from_row(music_row)
                self._save_one_assignment(station, slot, path, key, sound, old_name, old_artist, "batch_order")
                pairs.append(f"slot {slot} <- {path.name}")
            self.log("[OK] 批量智能替换完成：\n" + "\n".join(pairs))
            self.reload_slots()
        except Exception as exc:
            self.show_error("批量替换失败", exc)

    def clear_selected_assignment(self):
        station = self.current_station_name()
        slot_rows = self.selected_slot_rows()
        if not station or not slot_rows:
            return
        cleared = []
        for row in slot_rows:
            try:
                slot = self.slot_info_from_row(row)[0]
                self.store.clear_assignment(station, slot)
                cleared.append(slot)
            except Exception:
                pass
        self.log(f"[OK] 已清除 {station} slots {cleared} 的分配。")
        self.reload_slots()

    def on_music_selection_changed(self):
        sel = self.selected_music_path_and_key()
        if not sel:
            return
        path, _ = sel
        idx = self.loop_audio_combo.findData(str(path))
        if idx >= 0:
            self.loop_audio_combo.setCurrentIndex(idx)

    def on_music_table_item_changed(self, item: QTableWidgetItem):
        """Persist the editable Artist column from the user's music list."""
        if self._populating_music_table or item is None or item.column() != 2:
            return
        try:
            row = item.row()
            key_item = self.music_table.item(row, 1) or self.music_table.item(row, 0)
            path_item = self.music_table.item(row, 7)
            if not key_item or not path_item:
                return
            key = str(key_item.data(Qt.UserRole))
            path = Path(path_item.text())
            profile = self.store.load_track_profile(key)
            display, guessed_artist = guess_display_artist_from_filename(path.name)
            if not profile:
                profile = TrackProfile(key, str(path), path.name, display, guessed_artist)
            profile = replace(profile, source_path=str(path), filename=path.name, artist=item.text().strip())
            self.store.save_track_profile(profile)
        except Exception as exc:
            self.log(f"[WARN] 保存 Artist 失败: {exc}")

    def on_loop_audio_changed(self):
        text = self.loop_audio_combo.currentData()
        if not text:
            return
        path = Path(str(text))
        self.current_loop_audio = path
        self.loop_candidates = []
        self.candidate_table.setRowCount(0)
        self.candidate_combo.blockSignals(True)
        self.candidate_combo.clear()
        self.candidate_combo.blockSignals(False)
        self.candidate_summary_label.setText("尚未分析候选。")
        self.seek_slider.setRange(0, 0)
        self.position_label.setText("0 / 0")
        self.waveform.clear_waveform(self.ui_text("加载 WAV 后显示波形。", "Load a WAV file to show waveform."))
        try:
            if path.suffix.lower() in (".wav", ".wave"):
                info = read_wav_info(path)
                self.player.set_source(path)
                max_sample = min(2_147_483_647, max(0, info.sample_length - 1))
                self.seek_slider.setRange(0, max_sample)
                self.position_label.setText(f"0 / {format_sample_time(max_sample, info.samplerate)}")
                self.load_waveform_for_current_audio(path, info)
                profile = self.store.load_track_profile(track_key_for_path(path))
                defaults = safe_default_marker_values(max_sample)
                markers = dict(defaults)
                if profile and profile.markers:
                    markers.update({k: int(v) for k, v in profile.markers.items()})
                if profile and profile.loop_candidates:
                    self.loop_candidates = []
                    from .v2_loop_tools import LoopCandidate
                    for c in profile.loop_candidates:
                        try:
                            self.loop_candidates.append(LoopCandidate(int(c.get("loop_start", 0)), int(c.get("loop_end", 0)), float(c.get("score", 0)), str(c.get("source", "saved")), str(c.get("label", "已保存候选"))))
                        except Exception:
                            pass
                    self.refresh_candidate_views()
                for name, spin in self.marker_spins.items():
                    spin.setMaximum(max_sample)
                    spin.setValue(int(markers.get(name, -1)))
                self.refresh_waveform_markers()
            else:
                self.waveform.clear_waveform(self.ui_text("非 WAV 文件暂不显示波形；生成/转换后仍可使用预览。", "Waveform preview is available for WAV files."))
        except Exception as exc:
            self.log(f"[WARN] 无法载入试听音频: {exc}")

    def analyze_current_loop_audio(self):
        path = self.current_loop_audio
        if not path:
            self.warn_box("未选择音频", "请先选择一个音频。", "No audio selected", "Please select an audio file first.")
            return
        if path.suffix.lower() not in (".wav", ".wave"):
            self.warn_box("需要 WAV", "当前内置 Loop Engine 要求 16-bit PCM WAV。非 WAV 可先用生成流程转码。", "WAV required", "The built-in Loop Engine currently requires 16-bit PCM WAV. Non-WAV files can be converted through the generation workflow first.")
            return

        def job(report):
            report(1, f"[LOOP] 正在分析: {path.name}")
            return analyze_loop_candidates(path, top_n=8, progress_callback=report)

        def done(result):
            candidates, msg = result
            self.loop_candidates = candidates
            self.refresh_candidate_views()
            self.log(msg)
            self.set_progress(100, "[OK] Loop 分析完成，请在候选栏选择、场景试听，必要时用进度条手动微调。")
            if not candidates:
                self.info_box("没有候选", "自动分析未找到可靠候选。可以使用进度条试听并手动写入 Marker。", "No candidates", "Automatic analysis did not find reliable candidates. Use the progress slider and write markers manually.")

        self.run_background_task(
            "自动分析 Loop 候选",
            job,
            done,
            estimated="普通 3–5 分钟歌曲约 5–60 秒；PyMusicLooper 补充阶段最长约 45 秒。",
        )

    def analyze_all_loop_audio(self):
        paths = list(self.audio_paths)
        if not paths:
            self.warn_box("没有音乐", "请先选择并扫描音乐目录。", "No music", "Please choose and scan a music folder first.")
            return
        db_path = self.store.db_path
        current_path = self.current_loop_audio

        def job(report):
            store = StateStore(db_path)
            loop_work = project_work_dir() / "loop_analysis_wav"
            loop_work.mkdir(parents=True, exist_ok=True)
            total = max(1, len(paths))
            ok = 0
            skipped = []
            last_current_candidates = None
            ffmpeg = None
            for i, source in enumerate(paths, start=1):
                report(max(1, int((i - 1) * 95 / total)), f"[LOOP] 批量分析 {i}/{total}: {source.name}")
                key = track_key_for_path(source)
                profile = store.load_track_profile(key)
                display, artist = guess_display_artist_from_filename(source.name)
                if profile is None:
                    profile = TrackProfile(key, str(source), source.name, display, artist)
                analysis_path = source
                try:
                    if source.suffix.lower() not in (".wav", ".wave") or not validate_wav(source).ok:
                        if ffmpeg is None:
                            ffmpeg = find_ffmpeg(None)
                        analysis_path = loop_work / f"{safe_stem(source.name)}.wav"
                        run_ffmpeg_normalize(source, analysis_path, ffmpeg)
                    info = read_wav_info(analysis_path)
                    # Batch mode intentionally suppresses the verbose per-stage analyzer messages.
                    # The analyzer can emit many lines for every song (Smart Match, PyMusicLooper,
                    # fallback matcher, scan steps).  For normal players this makes the log noisy
                    # and hard to read, so batch mode only logs one compact line per track plus
                    # the final summary while still updating the progress bar continuously.
                    def compact_loop_progress(p, m, i=i, total=total):
                        progress_value = min(99, int((i - 1) * 95 / total + max(0, min(100, int(p))) * 0.95 / total))
                        report(progress_value, "")

                    candidates, msg = analyze_loop_candidates(analysis_path, top_n=8, progress_callback=compact_loop_progress)
                    saved_markers = dict(safe_default_marker_values(max(0, info.sample_length - 1)))
                    if profile.markers:
                        saved_markers.update({k: int(v) for k, v in profile.markers.items()})
                    profile = replace(
                        profile,
                        source_path=str(source),
                        filename=source.name,
                        display_name=profile.display_name or display,
                        artist=profile.artist or artist,
                        sample_rate=info.samplerate,
                        sample_length=info.sample_length,
                        markers=marker_values_for_save(saved_markers),
                        loop_candidates=[c.to_json() for c in candidates],
                    )
                    store.save_track_profile(profile)
                    if current_path and Path(current_path) == source:
                        last_current_candidates = candidates
                    ok += 1
                except Exception as exc:
                    skipped.append(f"{source.name}: {exc}")
            report(100, f"[LOOP] 批量分析完成：成功 {ok}/{len(paths)}。")
            return {"ok": ok, "total": len(paths), "skipped": skipped, "current_candidates": last_current_candidates}

        def done(result):
            self.log(f"[OK] Loop 批量分析完成：成功 {result['ok']}/{result['total']} 首。")
            for line in result.get("skipped", [])[:20]:
                self.log(f"[LOOP][WARN] {line}")
            if len(result.get("skipped", [])) > 20:
                self.log(f"[LOOP][WARN] 另有 {len(result['skipped']) - 20} 首失败，已省略显示。")
            if self.current_loop_audio:
                self.on_loop_audio_changed()
            self.reload_slots()
            self.set_progress(100, "[OK] 已批量分析并保存全部音频设置。")

        self.run_background_task(
            "批量分析全部歌曲 Loop",
            job,
            done,
            estimated="会逐首分析当前音乐目录中的所有歌曲；大量或非 WAV 音频可能需要数分钟。",
        )

    def refresh_candidate_views(self):
        samplerate = self.player.samplerate if self.player else 48000
        self.candidate_table.blockSignals(True)
        self.candidate_combo.blockSignals(True)
        self.candidate_table.setRowCount(len(self.loop_candidates))
        self.candidate_combo.clear()
        for i, c in enumerate(self.loop_candidates):
            label = f"#{i + 1}  {format_sample_time(c.loop_start, samplerate)} → {format_sample_time(c.loop_end, samplerate)}  score={c.score:.4f}  {c.source}"
            self.candidate_combo.addItem(label, i)
            set_item(self.candidate_table, i, 0, i + 1, data=i)
            set_item(self.candidate_table, i, 1, c.loop_start)
            set_item(self.candidate_table, i, 2, c.loop_end)
            set_item(self.candidate_table, i, 3, f"{c.score:.4f}")
            set_item(self.candidate_table, i, 4, c.source)
        self.candidate_combo.blockSignals(False)
        self.candidate_table.blockSignals(False)
        if self.loop_candidates:
            self.candidate_combo.setCurrentIndex(0)
            self.candidate_table.selectRow(0)
            best = self.loop_candidates[0]
            self.candidate_summary_label.setText(f"已找到 {len(self.loop_candidates)} 个候选。当前首选：{format_sample_time(best.loop_start, samplerate)} → {format_sample_time(best.loop_end, samplerate)}，score={best.score:.4f}。")
        else:
            self.candidate_summary_label.setText("没有可用候选；可以直接用进度条试听并手动写入 Marker。")

    def selected_candidate(self):
        idx = self.candidate_combo.currentData()
        if idx is not None:
            try:
                idx = int(idx)
                if 0 <= idx < len(self.loop_candidates):
                    return self.loop_candidates[idx]
            except Exception:
                pass
        row = self.candidate_table.currentRow()
        if row < 0 and self.loop_candidates:
            row = 0
        if row < 0 or row >= len(self.loop_candidates):
            return None
        return self.loop_candidates[row]

    def on_candidate_combo_changed(self):
        idx = self.candidate_combo.currentData()
        if idx is None:
            return
        try:
            idx = int(idx)
        except Exception:
            return
        if 0 <= idx < self.candidate_table.rowCount():
            self.candidate_table.blockSignals(True)
            self.candidate_table.selectRow(idx)
            self.candidate_table.blockSignals(False)

    def on_candidate_table_selection_changed(self):
        row = self.candidate_table.currentRow()
        if row < 0:
            return
        if self.candidate_combo.currentIndex() != row and row < self.candidate_combo.count():
            self.candidate_combo.blockSignals(True)
            self.candidate_combo.setCurrentIndex(row)
            self.candidate_combo.blockSignals(False)

    def marker_values_for_ui(self) -> dict[str, int]:
        return {name: int(spin.value()) for name, spin in self.marker_spins.items()}

    def current_marker_values(self) -> dict[str, int]:
        return {name: int(spin.value()) for name, spin in self.marker_spins.items() if spin.value() >= 0}

    def refresh_waveform_markers(self) -> None:
        if not hasattr(self, "waveform"):
            return
        self.waveform.set_markers(self.marker_values_for_ui(), self.selected_marker_name())

    def load_waveform_for_current_audio(self, path: Path, info: AudioInfo) -> None:
        try:
            data = load_or_build_waveform(
                path,
                project_work_dir() / "waveform_cache",
                bins=DEFAULT_WAVEFORM_BINS,
            )
            peaks = [float(x) for x in data.get("peaks", [])]
            total = int(data.get("total_frames") or info.sample_length)
            samplerate = int(data.get("samplerate") or info.samplerate)
            self.waveform.set_waveform(peaks, total, samplerate)
            self.waveform.set_position(self.player.current_sample())
            self.refresh_waveform_markers()
        except Exception as exc:
            self.waveform.clear_waveform(self.ui_text("无法生成波形，仍可使用普通进度条。", "Waveform unavailable; slider preview still works."))
            self.log(f"[WAVEFORM][WARN] {path.name}: {exc}")

    def update_position_ui(self, sample: int) -> None:
        sr = max(1, int(self.player.samplerate or 48000))
        max_sample = max(0, int(self.player.total_frames) - 1)
        sample = max(0, min(int(sample), max_sample))
        self._updating_slider = True
        try:
            if self.seek_slider.maximum() != max_sample:
                self.seek_slider.setRange(0, min(2_147_483_647, max_sample))
            self.seek_slider.setValue(max(0, min(sample, self.seek_slider.maximum())))
            self.position_label.setText(f"{format_sample_time(sample, sr)} / {format_sample_time(max_sample, sr)}")
            self.waveform.set_position(sample)
        finally:
            self._updating_slider = False

    def play_or_resume_audio(self) -> None:
        self.player.play()

    def reset_player_to_start(self) -> None:
        self.player.stop()
        self.update_position_ui(0)

    def begin_seek_drag(self) -> None:
        self._slider_dragging = True

    def finish_seek_drag(self) -> None:
        self._slider_dragging = False
        self.seek_to_sample(int(self.seek_slider.value()))

    def seek_to_sample(self, sample: int) -> None:
        sample = max(0, min(int(sample), max(0, int(self.player.total_frames) - 1)))
        self.player.seek_sample(sample)
        self.update_position_ui(sample)

    def _validate_preview_range(self, start: int, end: int, *, loop: bool, loop_start: int | None, label: str) -> bool:
        sr = max(1, int(self.player.samplerate or 48000))
        total_last = max(0, int(self.player.total_frames) - 1)
        start = max(0, min(int(start), total_last))
        end = max(0, min(int(end), total_last))
        min_frames = max(1, int(sr * 0.30))
        if end <= start or (end - start) < min_frames:
            self.warn_box(
                "试听区间无效",
                f"{label} 的试听区间太短或起止点无效。请确认 Start 小于 End，且片段至少 0.3 秒。",
                "Invalid preview range",
                f"{label} has an invalid or too short preview range. Start must be before End and the segment must be at least 0.3 seconds.",
            )
            return False
        if loop and loop_start is not None and not (0 <= int(loop_start) < end):
            self.warn_box(
                "循环起点无效",
                f"{label} 的 LoopStart 必须小于试听终点，且不能为负数。它可以位于预览片段之前，用来模拟真实循环跳转。",
                "Invalid loop start",
                f"{label} LoopStart must be non-negative and before the preview end. It may be earlier than the preview window to simulate a real loop jump.",
            )
            return False
        return True

    def preview_selected_candidate(self):
        cand = self.selected_candidate()
        if not cand:
            self.warn_box("缺少候选", "请先分析并选择一个 Loop 候选。", "Missing candidate", "Please analyze and select a Loop candidate first.")
            return
        sr = max(1, int(self.player.samplerate or 48000))
        seconds = int(self.preview_seconds_spin.value())
        start = max(cand.loop_start, cand.loop_end - seconds * sr)
        if not self._validate_preview_range(start, cand.loop_end, loop=True, loop_start=cand.loop_start, label="Loop 候选"):
            return
        self.player.play_range(start, cand.loop_end, loop=True, loop_start_sample=cand.loop_start)
        self.set_progress(0, f"试听候选衔接：{format_sample_time(start, sr)} → {format_sample_time(cand.loop_end, sr)}")

    def preview_selected_scenario(self):
        if not self.current_loop_audio:
            return
        markers = self.current_marker_values()
        sr = max(1, int(self.player.samplerate or 48000))
        seconds = int(self.preview_seconds_spin.value())
        key = str(self.preview_scenario_combo.currentData() or "roam_loop")
        try:
            plan = build_scene_preview_plan(key, markers, int(self.player.total_frames), sr, preview_seconds=seconds)
            if not self._validate_preview_range(plan.start_sample, plan.end_sample, loop=plan.loop, loop_start=plan.loop_start_sample, label=plan.description):
                return
            self.player.play_range(plan.start_sample, plan.end_sample, loop=plan.loop, loop_start_sample=plan.loop_start_sample)
            self.set_progress(0, f"试听场景：{plan.description}  {format_sample_time(plan.start_sample, sr)} → {format_sample_time(plan.end_sample, sr)}")
        except Exception as exc:
            self.show_error("场景试听失败", exc)

    def on_player_position_changed(self, sample: int):
        if self._updating_slider or self._slider_dragging:
            return
        self.update_position_ui(int(sample))

    def on_seek_slider_moved(self, value: int):
        sr = max(1, int(self.player.samplerate or 48000))
        max_sample = max(0, int(self.player.total_frames) - 1)
        self.position_label.setText(f"{format_sample_time(value, sr)} / {format_sample_time(max_sample, sr)}")
        self.waveform.set_position(int(value))

    def seek_to_slider(self):
        self.seek_to_sample(int(self.seek_slider.value()))

    def selected_marker_name(self) -> str:
        text = self.marker_target_combo.currentText().strip()
        return text if text else "TrackLoopStart"

    def jump_to_selected_marker(self):
        name = self.selected_marker_name()
        spin = self.marker_spins.get(name)
        if spin and spin.value() >= 0:
            self.player.seek_sample(int(spin.value()))
            self.log(f"[SEEK] 跳到 {name}={spin.value()}")

    def write_current_sample_to_marker(self):
        name = self.selected_marker_name()
        spin = self.marker_spins.get(name)
        if not spin:
            return
        sample = self.player.current_sample()
        spin.setValue(int(sample))
        self.log(f"[MARKER] {name} <- {sample}")

    def nudge_selected_marker(self, delta: int):
        name = self.selected_marker_name()
        spin = self.marker_spins.get(name)
        if not spin:
            return
        base = self.player.current_sample() if spin.value() < 0 else int(spin.value())
        value = max(0, min(int(base + delta), spin.maximum()))
        spin.setValue(value)
        self.player.seek_sample(value)
        self.log(f"[NUDGE] {name} = {value} ({delta:+d})")

    def apply_selected_candidate(self, mode: str):
        path = self.current_loop_audio
        cand = self.selected_candidate()
        if not path or not cand:
            self.warn_box("缺少候选", "请先分析并选择一个 Loop 候选。", "Missing candidate", "Please analyze and select a Loop candidate first.")
            return
        try:
            info = read_wav_info(path)
            markers = markers_from_candidate(info, cand, mode=mode)
            current = {name: spin.value() for name, spin in self.marker_spins.items() if spin.value() >= 0}
            current.update(markers.positions)
            for name, spin in self.marker_spins.items():
                spin.setValue(int(current.get(name, -1)))
            self.log(f"[OK] 已把候选 {cand.loop_start}..{cand.loop_end} 填入 {mode}。")
        except Exception as exc:
            self.show_error("填入候选失败", exc)

    def apply_safe_markers_to_current_audio(self):
        path = self.current_loop_audio
        if not path:
            self.warn_box("未选择音频", "请先选择一个音频。", "No audio selected", "Please select an audio file first.")
            return
        try:
            if path.suffix.lower() in (".wav", ".wave"):
                info = read_wav_info(path)
                max_sample = max(0, info.sample_length - 1)
            else:
                profile = self.store.load_track_profile(track_key_for_path(path))
                max_sample = max(0, int((profile.sample_length if profile else 0) or 0) - 1)
            markers = safe_default_marker_values(max_sample)
            for name, spin in self.marker_spins.items():
                spin.setMaximum(max(spin.maximum(), max_sample))
                spin.setValue(int(markers.get(name, -1)))
            self.log("[MARKER] 已应用安全无循环 Marker。End 保持为歌曲真实结尾，LoopEnd/DJ/Stinger 保持 -1。")
        except Exception as exc:
            self.show_error("应用安全 Marker 失败", exc)

    def apply_safe_markers_to_all_audio(self):
        paths = list(self.audio_paths)
        if not paths:
            self.warn_box("没有音乐", "请先选择并扫描音乐目录。", "No music", "Please choose and scan a music folder first.")
            return
        if not self.question_box(
            "确认批量应用安全 Marker",
            "将对当前音乐目录中的全部歌曲应用安全无循环 Marker：\n"
            "TrackStart/Drop/LoopStart=0，LoopEnd=-1，DJ/Stinger=-1，End=歌曲真实结尾。\n\n"
            "这会覆盖这些歌曲已经保存的 Marker 设置。是否继续？",
            "Apply safe markers to all?",
            "This will apply No Loop / Safe Markers to all scanned songs and overwrite their saved marker settings. Continue?",
        ):
            return
        saved = 0
        for path in paths:
            try:
                key = track_key_for_path(path)
                profile = self.store.load_track_profile(key)
                display, artist = guess_display_artist_from_filename(path.name)
                info = None
                if path.suffix.lower() in (".wav", ".wave"):
                    try:
                        info = read_wav_info(path)
                    except Exception:
                        info = None
                max_sample = max(0, (info.sample_length - 1) if info else int((profile.sample_length if profile else 0) or 0) - 1)
                if profile is None:
                    profile = TrackProfile(key, str(path), path.name, display, artist)
                profile = replace(
                    profile,
                    source_path=str(path),
                    filename=path.name,
                    display_name=profile.display_name or display,
                    artist=profile.artist or artist,
                    sample_rate=info.samplerate if info else profile.sample_rate,
                    sample_length=info.sample_length if info else profile.sample_length,
                    markers=marker_values_for_save(safe_default_marker_values(max_sample)),
                )
                self.store.save_track_profile(profile)
                if path == self.current_loop_audio:
                    self._sync_profile_to_segments_json(profile)
                saved += 1
            except Exception as exc:
                self.log(f"[MARKER][WARN] {path.name}: {exc}")
        if self.current_loop_audio:
            self.on_loop_audio_changed()
        self.reload_slots()
        self.log(f"[OK] 已对 {saved} 首歌曲应用安全无循环 Marker。")

    def save_current_loop_profile(self):
        path = self.current_loop_audio
        if not path:
            return
        try:
            key = track_key_for_path(path)
            profile = self.store.load_track_profile(key)
            info = read_wav_info(path) if path.suffix.lower() in (".wav", ".wave") else None
            markers = marker_values_for_save({name: int(spin.value()) for name, spin in self.marker_spins.items()})
            candidates = [c.to_json() for c in self.loop_candidates]
            if profile is None:
                display, artist = guess_display_artist_from_filename(path.name)
                profile = TrackProfile(key, str(path), path.name, display, artist)
            profile = replace(
                profile,
                markers=markers,
                loop_candidates=candidates,
                sample_rate=info.samplerate if info else profile.sample_rate,
                sample_length=info.sample_length if info else profile.sample_length,
            )
            self.store.save_track_profile(profile)
            self._sync_profile_to_segments_json(profile)
            self.log(f"[OK] 已保存当前音频设置: {path.name}")
            self.reload_slots()
        except Exception as exc:
            self.show_error("保存音频设置失败", exc)


    def save_all_loop_profiles(self):
        paths = list(self.audio_paths)
        if not paths:
            self.warn_box("没有音乐", "请先选择并扫描音乐目录。", "No music", "Please choose and scan a music folder first.")
            return
        current = self.current_loop_audio
        try:
            if current:
                self.save_current_loop_profile()
            saved = 0
            for path in paths:
                key = track_key_for_path(path)
                profile = self.store.load_track_profile(key)
                display, artist = guess_display_artist_from_filename(path.name)
                info = None
                if path.suffix.lower() in (".wav", ".wave"):
                    try:
                        info = read_wav_info(path)
                    except Exception:
                        info = None
                max_sample = max(0, (info.sample_length - 1) if info else int(profile.sample_length or 0) - 1)
                defaults = safe_default_marker_values(max_sample)
                markers = dict(defaults)
                if profile and profile.markers:
                    markers.update({k: int(v) for k, v in profile.markers.items()})
                if profile is None:
                    profile = TrackProfile(key, str(path), path.name, display, artist)
                profile = replace(
                    profile,
                    source_path=str(path),
                    filename=path.name,
                    display_name=profile.display_name or display,
                    artist=profile.artist or artist,
                    sample_rate=info.samplerate if info else profile.sample_rate,
                    sample_length=info.sample_length if info else profile.sample_length,
                    markers=marker_values_for_save(markers),
                )
                self.store.save_track_profile(profile)
                saved += 1
            self.log(f"[OK] 已保存全部音频设置：{saved} 首")
            self.reload_slots()
        except Exception as exc:
            self.show_error("保存全部音频设置失败", exc)


    def _find_audio_path_for_marker_row(self, row: MarkerImportRow, sample_length_index: dict[int, list[Path]]) -> Path | None:
        """Match an import row to a scanned audio file.

        Matching priority:
        1) Stable exported context: station + slot, sound name, source path.
        2) Filename / MatchName / DisplayName against current music file stem.
        3) SampleLength when it uniquely identifies one scanned WAV file.
        """
        station_key = normalize_match_text(row.station)
        if row.slot_index is not None:
            for assignment in self.store.list_assignments(row.station or None):
                if int(assignment.get("slot_index") or -999) != int(row.slot_index):
                    continue
                if station_key and normalize_match_text(assignment.get("station_name")) != station_key:
                    continue
                profile = self.store.load_track_profile(str(assignment.get("track_key") or ""))
                if profile and profile.source_path:
                    return Path(profile.source_path)

        sound_keys = {
            normalize_match_text(row.sound_name),
            normalize_match_text(row.original_sound_name),
        }
        sound_keys.discard("")
        if sound_keys:
            matches: list[Path] = []
            for assignment in self.store.list_assignments(row.station or None):
                if normalize_match_text(assignment.get("original_sound_name")) not in sound_keys:
                    continue
                profile = self.store.load_track_profile(str(assignment.get("track_key") or ""))
                if profile and profile.source_path:
                    matches.append(Path(profile.source_path))
            unique = {str(p): p for p in matches}
            if len(unique) == 1:
                return next(iter(unique.values()))

        if row.source_audio_path:
            p = Path(row.source_audio_path)
            if p.exists():
                return p

        if not self.audio_paths:
            return None
        name_keys = [row.filename, row.match_name, row.display_name]
        normalized_to_path: dict[str, Path] = {}
        for path in self.audio_paths:
            normalized_to_path.setdefault(normalize_match_text(path.name), path)
            normalized_to_path.setdefault(normalize_match_text(path.stem), path)
        for key in name_keys:
            norm = normalize_match_text(key)
            if norm and norm in normalized_to_path:
                return normalized_to_path[norm]
        if row.sample_length > 0:
            matches = sample_length_index.get(int(row.sample_length), [])
            if len(matches) == 1:
                return matches[0]
        return None

    def _build_sample_length_index(self) -> dict[int, list[Path]]:
        index: dict[int, list[Path]] = {}
        for path in self.audio_paths:
            try:
                if path.suffix.lower() in (".wav", ".wave"):
                    info = read_wav_info(path)
                    index.setdefault(int(info.sample_length), []).append(path)
            except Exception:
                continue
        return index

    def import_marker_profiles_from_file(self) -> None:
        if not self.audio_paths and not self.store.list_assignments():
            self.warn_box("缺少音乐", "请先选择并扫描音乐目录，然后再导入 Marker 参数。", "Missing music", "Please choose and scan the music folder before importing markers.")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Marker 参数文件",
            str(project_work_dir()),
            "Marker import (*.csv *.json);;CSV (*.csv);;JSON (*.json);;All files (*.*)",
        )
        if not file_path:
            return
        try:
            rows = read_marker_import_file(Path(file_path))
            sample_index = self._build_sample_length_index()
            imported = 0
            unmatched: list[str] = []
            for row in rows:
                path = self._find_audio_path_for_marker_row(row, sample_index)
                if path is None:
                    unmatched.append(row.match_name or row.display_name or row.filename or f"row {row.source_row}")
                    continue
                key = track_key_for_path(path)
                old = self.store.load_track_profile(key)
                if old is None:
                    for candidate in self.store.list_track_profiles():
                        try:
                            if Path(candidate.source_path) == path or candidate.filename == path.name:
                                old = candidate
                                key = candidate.track_key
                                break
                        except Exception:
                            continue
                try:
                    info = read_wav_info(path) if path.suffix.lower() in (".wav", ".wave") else None
                except Exception:
                    info = None
                display, artist = guess_display_artist_from_filename(path.name)
                profile = old or TrackProfile(key, str(path), path.name, display, artist)
                merged_markers = dict(profile.markers or {})
                for marker_name, marker_value in row.markers.items():
                    if marker_value is None:
                        merged_markers[marker_name] = -1
                    else:
                        merged_markers[marker_name] = int(marker_value)
                profile = replace(
                    profile,
                    source_path=str(path),
                    filename=path.name,
                    display_name=profile.display_name or row.display_name or display,
                    artist=profile.artist or row.artist or artist,
                    sample_rate=info.samplerate if info else (row.sample_rate or profile.sample_rate),
                    sample_length=info.sample_length if info else (row.sample_length or profile.sample_length),
                    markers=marker_values_for_save(merged_markers),
                    notes=(profile.notes + "\n" if profile.notes else "") + f"Imported markers from {Path(file_path).name}, row {row.source_row}",
                )
                self.store.save_track_profile(profile)
                self._sync_profile_to_segments_json(profile)
                imported += 1
            self.scan_music_dir(quiet=True)
            self.reload_slots()
            if self.current_loop_audio:
                self.on_loop_audio_changed()
            msg = f"已导入 {imported} 首音乐的 Marker 参数。"
            if unmatched:
                msg += f"\n未匹配 {len(unmatched)} 行：" + ", ".join(unmatched[:12])
                if len(unmatched) > 12:
                    msg += " ..."
            self.log("[OK] " + msg.replace("\n", " | "))
            self.info_box("导入完成", msg, "Import completed", self._rough_runtime_translate(msg))
        except Exception as exc:
            self.show_error("导入 Marker 参数失败", exc)

    def _station_export_context(self) -> tuple[dict[str, str], dict[tuple[str, int], dict[str, object]]]:
        station_banks: dict[str, str] = {}
        sample_rows: dict[tuple[str, int], dict[str, object]] = {}
        if not self.current_xml:
            return station_banks, sample_rows
        try:
            for station_info in self.station_infos:
                station = str(getattr(station_info, "name", "") or "")
                if not station:
                    continue
                station_banks[station] = "|".join(str(x) for x in getattr(station_info, "banks", []) or [])
                for row in station_sample_rows(self.current_xml, station):
                    try:
                        sample_rows[(station, int(row.get("slot_index", -1)))] = row
                    except Exception:
                        continue
        except Exception as exc:
            self.log(f"[EXPORT][WARN] Failed to read station context: {exc}")
        return station_banks, sample_rows

    def _markers_for_export_profile(self, profile: TrackProfile, path: Path | None) -> dict[str, int]:
        markers = dict(profile.markers or {})
        if path and self.current_loop_audio and Path(self.current_loop_audio) == Path(path):
            markers.update(self.marker_values_for_ui())
        return marker_values_for_save(markers)

    def _marker_export_row(
        self,
        profile: TrackProfile,
        *,
        assignment: dict[str, object] | None = None,
        station_banks: dict[str, str] | None = None,
        sample_rows: dict[tuple[str, int], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        station_banks = station_banks or {}
        sample_rows = sample_rows or {}
        assignment = assignment or {}
        station = str(assignment.get("station_name") or "")
        slot_raw = assignment.get("slot_index")
        slot_index: int | str = int(slot_raw) if slot_raw not in (None, "") else ""
        source_path = Path(profile.source_path) if profile.source_path else Path(profile.filename)
        xml_row = sample_rows.get((station, int(slot_index))) if slot_index != "" else None
        sound_name = str((xml_row or {}).get("sound_name") or assignment.get("original_sound_name") or "")
        title = profile.display_name or str(assignment.get("original_display_name") or "") or Path(profile.filename).stem
        artist = profile.artist or str(assignment.get("original_artist") or "")
        sample_rate = int(profile.sample_rate or 0)
        sample_length = int(profile.sample_length or 0)
        try:
            if source_path.suffix.lower() in (".wav", ".wave") and source_path.exists():
                info = read_wav_info(source_path)
                sample_rate = int(info.samplerate)
                sample_length = int(info.sample_length)
        except Exception:
            pass
        duration = (float(sample_length) / float(sample_rate)) if sample_rate > 0 and sample_length > 0 else 0.0
        markers = self._markers_for_export_profile(profile, source_path)
        row: dict[str, object] = {col: "" for col in EXPORT_COLUMNS}
        row.update({
            "station": station,
            "radio": station,
            "station_name": station,
            "slot_index": slot_index,
            "sound_name": sound_name,
            "original_sound_name": sound_name,
            "bank_name": station_banks.get(station, ""),
            "target_bank": station_banks.get(station, ""),
            "title": title,
            "display_name": title,
            "artist": artist,
            "filename": profile.filename or source_path.name,
            "source_audio_path": str(source_path),
            "sample_rate": sample_rate,
            "sample_length": sample_length,
            "duration_sec": f"{duration:.3f}" if duration else "",
            "marker_unit": "samples",
            "MatchName": Path(profile.filename or source_path.name).stem,
            "Filename": profile.filename or source_path.name,
            "DisplayName": title,
            "Artist": artist,
            "SampleRate": sample_rate,
            "SampleLength": sample_length,
        })
        for marker_name in MARKER_ORDER:
            if marker_name in markers:
                row[marker_name] = int(markers[marker_name])
        return row

    def export_marker_import_template_dialog(self) -> None:
        default = project_output_dir() / "marker_export.csv"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Marker",
            str(default),
            "CSV (*.csv)",
        )
        if not file_path:
            return
        try:
            rows: list[dict[str, object]] = []
            station_banks, sample_rows = self._station_export_context()
            assignments = self.store.list_assignments()
            profile_by_key = {p.track_key: p for p in self.store.list_track_profiles()}
            for assignment in assignments:
                profile = profile_by_key.get(str(assignment.get("track_key") or ""))
                if profile:
                    rows.append(self._marker_export_row(profile, assignment=assignment, station_banks=station_banks, sample_rows=sample_rows))
            if not rows:
                for path in self.audio_paths:
                    key = track_key_for_path(path)
                    profile = self.store.load_track_profile(key)
                    if not profile:
                        display, artist = guess_display_artist_from_filename(path.name)
                        profile = TrackProfile(key, str(path), path.name, display, artist)
                    rows.append(self._marker_export_row(profile, station_banks=station_banks, sample_rows=sample_rows))
            write_marker_import_template(Path(file_path), rows, fieldnames=EXPORT_COLUMNS)
            self.info_box(
                "已导出 Marker",
                f"已导出 {len(rows)} 行 Marker 配置：\n{file_path}\n\nMarker 单位：samples。空单元格在导入时会保留原值；填写 CLEAR 可清除字段。",
                "Markers exported",
                f"Exported {len(rows)} marker row(s):\n{file_path}\n\nMarker unit: samples. Blank cells preserve existing values on import; use CLEAR to clear a field.",
            )
            return
            self.info_box("已导出 Marker", f"已导出 Marker：\n{file_path}", "Markers exported", f"Markers exported:\n{file_path}")
        except Exception as exc:
            self.show_error("导出 Marker 失败", exc)

    def _sync_profile_to_segments_json(self, profile: TrackProfile):
        # Compatibility sidecar for existing v1 XML generation path.
        work = project_work_dir()
        seg_path = work / SEGMENTS_FILE_NAME
        data = load_segments(seg_path)
        data.setdefault("version", 2)
        data.setdefault("items", {})
        data["items"][profile.filename] = {
            "sample_rate": profile.sample_rate,
            "sample_length": profile.sample_length,
            "markers": profile.markers or {},
            "loop_candidates": profile.loop_candidates or [],
            "notes": profile.notes,
        }
        save_segments(seg_path, data)


    def _current_fmod_tool_path(self) -> Path:
        tool = self.txt_path(self.fmod_tool_edit)
        if not tool:
            raise ValueError("请先选择 Fmod Bank Tools exe 路径。")
        self.store.set_setting("fmod_tool", str(tool))
        self.store.set_setting("fmod_auto_click", True)
        self._fmod_auto_click_enabled()
        return tool

    def _current_bank_root(self) -> Path:
        """Resolve the game FMODBanks directory from the game root automatically.

        v2.3.1 deliberately stops exposing a separate editable Bank 根目录.
        The user configures the game root once; bank discovery then prefers
        <game>/media/Audio/FMODBanks and falls back to scanning inside the game
        root.  The read-only line edit is only a status display.
        """
        game_root = self.txt_path(self.game_root_edit)
        if not game_root:
            raise ValueError("请先在首页选择游戏根目录。工具会自动从游戏根目录定位 media/Audio/FMODBanks。")
        if not game_root.exists():
            raise FileNotFoundError(f"游戏根目录不存在: {game_root}")

        bank_names: list[str] = []
        try:
            if self.current_xml and self.current_station_name():
                station = station_info_from_node(find_station(parse_xml(self.current_xml), self.current_station_name()))
                bank_names = list(station.banks)
        except Exception:
            bank_names = []

        bank_root = resolve_fmod_bank_root(game_root, bank_names=bank_names)
        if not bank_root or not bank_root.exists():
            raise FileNotFoundError(
                "未能从游戏根目录自动定位 FMODBanks。请确认游戏根目录选择的是 FH6 安装根目录，"
                "并且其下存在 media/Audio/FMODBanks。"
            )
        self.bank_root_edit.setText(str(bank_root))
        self.store.set_setting("game_root", str(game_root))
        self.store.set_setting("auto_bank_root", str(bank_root))
        self.store.set_setting("bank_root", str(bank_root))  # legacy compatibility for old reports
        return bank_root

    def _current_station_info(self):
        if not self.current_xml:
            raise ValueError("请先加载 RadioInfo XML。")
        station_name = self.current_station_name()
        if not station_name:
            raise ValueError("请先选择目标电台。")
        return station_info_from_node(find_station(parse_xml(self.current_xml), station_name))

    def _station_bank_tokens(self) -> list[str]:
        """Tokens used to keep FMOD work scoped to the selected radio station.

        v3.0.20: include both zero-padded and non-padded variants.  Some XML
        station numbers may be stored as "01" while bank files are named
        R1_Tracks_*.  Older code produced only r01 in that case, so R1_Tracks_Disk
        was not discovered and the tail slots still looked unmatched.
        """
        tokens: list[str] = []

        def add_token(value: object) -> None:
            text = str(value or "").strip().lower().replace('-', '_')
            if not text:
                return
            raw = text.lstrip('r')
            if raw:
                tokens.append(f"r{raw}")
                try:
                    n = int(raw)
                    tokens.append(f"r{n}")
                    tokens.append(f"r{n:02d}")
                except Exception:
                    pass

        try:
            st = self._current_station_info()
            add_token(st.number)
            name_l = (st.name or "").lower()
            for part in name_l.replace('-', '_').replace(' ', '_').split('_'):
                if part.startswith('r') and any(ch.isdigit() for ch in part):
                    add_token(part)
        except Exception:
            pass
        # Also inspect visible sound names; FH sound names often contain HZ6_R5_...
        for r in range(self.slot_table.rowCount()):
            item = self.slot_table.item(r, 4)
            if not item:
                continue
            text = item.text().lower().replace('-', '_')
            for part in text.split('_'):
                if part.startswith('r') and any(ch.isdigit() for ch in part):
                    add_token(part)
        out: list[str] = []
        for t in tokens:
            t = t.strip().lower()
            if t and t not in out:
                out.append(t)
        return out

    def _bank_matches_station_tokens(self, path: Path, tokens: list[str]) -> bool:
        if not tokens:
            return True
        stem = Path(path).stem.lower().replace('-', '_').replace('.', '_')
        parts = [p for p in stem.split('_') if p]
        for token in tokens:
            if token in parts or stem.startswith(token + '_') or ('_' + token + '_') in ('_' + stem + '_'):
                return True
        return False

    def _preferred_cu1_assets_bank_for_station(self, bank_root: Path, tokens: list[str]) -> Path | None:
        """Prefer the single FH radio audio bank used in normal replacement flow.

        In the game files each radio can have several R* bank files, but the
        actual audio container normally follows `R*_Tracks_CU1.assets.bank`.
        Extracting every R5-related bank wastes a lot of time.  This helper
        searches the current FMODBanks tree and returns one matching CU1.assets
        audio bank when available.
        """
        root = Path(bank_root)
        if not root.exists():
            return None
        normalized_tokens = [t.lower().replace('-', '_').replace('.', '_') for t in tokens if t]
        scored: list[tuple[int, Path]] = []
        try:
            files = list(root.rglob("*.bank"))
        except OSError:
            files = []
        for f in files:
            name = f.name.lower().replace('-', '_').replace('.', '_')
            if "tracks" not in name or "cu1" not in name or "assets" not in name:
                continue
            if normalized_tokens and not any(t in name.split('_') or name.startswith(t + '_') or f"_{t}_" in f"_{name}_" for t in normalized_tokens):
                continue
            # Prefer the explicit CU1.assets naming pattern, but do not hide the
            # actual Fmod preflight result.  prepare_extract_job will only launch
            # Fmod Bank Tools with banks that have extractable SNDH/FSB offsets;
            # metadata-only CU1 banks will automatically fall back to another
            # same-station audio bank.
            score = 0
            if name.endswith("tracks_cu1_assets_bank") or "_tracks_cu1_assets_bank" in name:
                score -= 500
            if "disk" in name:
                score += 1000
            if "master" in name or "strings" in name:
                score += 1000
            score += len(str(f)) // 20
            scored.append((score, f))
        if not scored:
            return None
        scored.sort(key=lambda x: (x[0], str(x[1]).lower()))
        return scored[0][1]

    def _find_bank_by_stem(self, bank_root: Path, bank_stem: str) -> Path | None:
        """Find a bank by stem/name under the game's FMODBanks directory."""
        wanted = str(bank_stem or "").lower().replace(".assets.bank", "").replace(".bank", "")
        if not wanted:
            return None
        try:
            candidates = list(Path(bank_root).rglob("*.bank"))
        except OSError:
            return None
        for bank in candidates:
            stem = bank.name.lower().replace(".assets.bank", "").replace(".bank", "")
            if stem == wanted or bank.stem.lower() == wanted or bank.name.lower() == wanted + ".assets.bank":
                return bank
        # A second pass allows GLB_Radio_3D -> GLB_Radio_3D.assets.bank.
        for bank in candidates:
            name = bank.name.lower()
            if wanted in name and name.endswith(".bank"):
                return bank
        return None

    def _supplemental_bank_paths_for_station(self, station_name: str | None, bank_root: Path) -> list[Path]:
        """Return extra cross-bank audio sources confirmed by diagnostics.

        FH6 can expose some RadioInfo rows whose actual audio is not in the
        station's main R*_Tracks bank.  Supplemental banks are only added when a
        station profile explicitly provides a verified mapping.  Unconfirmed
        candidates must stay in developer search reports, not normal replacement.
        """
        profile = self._station_slot_profile(station_name, None)
        names = [] if not profile else list(profile.get("supplemental_bank_names", []) or [])
        out: list[Path] = []
        for name in names:
            bank = self._find_bank_by_stem(bank_root, str(name))
            if bank and bank.exists() and bank not in out:
                out.append(bank)
        return out

    def _sort_track_banks_for_station(self, paths: list[Path]) -> list[Path]:
        """Return deterministic same-station Track bank order.

        v3.0.17: a station can be split across multiple R*_Tracks_* banks
        (for example R1_Tracks_CU1 + R1_Tracks_Disk).  CU1 remains first, but
        Disk/other Tracks banks are kept as supplementary audio containers.
        """
        def score(path: Path):
            name = path.name.lower()
            s = 100
            if "tracks" in name:
                s -= 50
            if "cu1" in name:
                s -= 30
            if "disk" in name:
                s -= 20
            if "stinger" in name or "dj" in name or name.startswith("vo_"):
                s += 1000
            return (s, name)
        return sorted(dict.fromkeys(Path(p) for p in paths), key=score)

    def _same_station_track_bank_paths(self, bank_root: Path, tokens: list[str]) -> list[Path]:
        """Discover all same-station R*_Tracks_* bank files under FMODBanks.

        The bank directory can contain both .assets.bank and .bank files for the
        same logical bank.  Prefer .assets.bank when both are present, but keep
        a plain .bank when it is the only available copy.
        """
        root = Path(bank_root)
        if not root.exists():
            return []
        try:
            files = list(root.rglob("*.bank"))
        except OSError:
            files = []
        found: list[Path] = []
        for p in files:
            name = p.name.lower().replace('-', '_')
            if "tracks" not in name:
                continue
            if "stinger" in name or "dj" in name or name.startswith("vo_"):
                continue
            if tokens and not self._bank_matches_station_tokens(p, tokens):
                continue
            found.append(p)

        # Deduplicate R1_Tracks_Disk.assets.bank vs R1_Tracks_Disk.bank.
        # Keep the .assets.bank copy when present because that is the file the
        # game usually stores beside the RadioInfo bank references.
        by_key: dict[str, Path] = {}
        for p in found:
            n = p.name.lower()
            key = n.replace('.assets.bank', '').replace('.bank', '')
            old = by_key.get(key)
            if old is None:
                by_key[key] = p
            else:
                if p.name.lower().endswith('.assets.bank') and not old.name.lower().endswith('.assets.bank'):
                    by_key[key] = p
        return self._sort_track_banks_for_station(list(by_key.values()))

    def _selected_bank_paths_for_current_station(self) -> list[Path]:
        if not self.current_xml:
            raise ValueError("请先加载 RadioInfo XML。")
        station_name = self.current_station_name()
        if not station_name:
            raise ValueError("请先选择目标电台。")
        bank_root = self._current_bank_root()
        station = self._current_station_info()
        candidates = choose_banks(station.banks, bank_root=bank_root)
        paths = [Path(c.bank_path) for c in candidates if c.selected and c.exists and c.bank_path]
        missing = [c for c in candidates if c.selected and not c.exists]
        if missing:
            self.log("[FMOD][WARN] 这些 XML 中声明的 Track bank 未在自动定位的 FMODBanks 目录找到：\n" + "\n".join(f"  - {c.name}: {c.bank_path}" for c in missing))
        tokens = self._station_bank_tokens()

        # v3.0.20: Do not rely only on the Track banks declared in RadioInfo.
        # FH radio stations can split songs across multiple same-station banks
        # such as R4_Tracks_CU1, R4_Tracks_CU2 and R4_Tracks_Disk.  XML may list
        # only a subset, and older logic treated Disk as a fallback instead of a
        # supplementary source.  Discover every same-station R*_Tracks_* bank
        # directly from FMODBanks, with robust R01/R1 token handling.
        same_station_tracks = self._same_station_track_bank_paths(bank_root, tokens)
        for p in same_station_tracks:
            if p not in paths:
                paths.append(p)

        if not paths:
            raise ValueError("没有找到可用于 Extract/Rebuild 的 Track bank。请检查游戏根目录、media/Audio/FMODBanks 或 RadioInfo XML 中的 bank 名称。")

        scoped = [p for p in paths if self._bank_matches_station_tokens(p, tokens)]
        selected = self._sort_track_banks_for_station(scoped or paths)

        supplements = self._supplemental_bank_paths_for_station(station_name, bank_root)
        for sup in supplements:
            if sup not in selected:
                selected.append(sup)

        if scoped:
            self.log("[FMOD] 已选择当前电台全部 Track 音频 bank：" + ", ".join(p.name for p in selected))
        else:
            self.log("[FMOD][WARN] 未能从 bank 文件名中匹配当前电台 token，保留 XML 声明的 Track bank：" + ", ".join(p.name for p in selected))
        if supplements:
            self.log("[FMOD] 已加入跨 bank 补充音频源：" + ", ".join(p.name for p in supplements))
        return selected

    def _fmod_auto_dir(self) -> Path:
        d = project_output_dir() / "fmod_bank_automation"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _prepare_fmod_extract_job(self):
        tool = self._current_fmod_tool_path()
        bank_root = self._current_bank_root()
        banks = self._selected_bank_paths_for_current_station()
        station_tokens = self._station_bank_tokens()
        manifest = self._fmod_auto_dir() / "extract_manifest.json"
        return prepare_extract_job(tool, banks, manifest, search_root=bank_root, preferred_tokens=station_tokens, cpu_threads=self._fmod_cpu_threads())

    def prepare_fmod_extract(self):
        try:
            tool = self._current_fmod_tool_path()
            bank_root = self._current_bank_root()
            banks = self._selected_bank_paths_for_current_station()
            station_tokens = self._station_bank_tokens()
            manifest = self._fmod_auto_dir() / "extract_manifest.json"
        except Exception as exc:
            self.show_error("准备 Fmod Extract 失败", exc)
            return

        def job(report):
            report(5, "[FMOD][EXTRACT] 正在准备外部工具目录。")
            res = prepare_extract_job(tool, banks, manifest, search_root=bank_root, preferred_tokens=station_tokens, cpu_threads=self._fmod_cpu_threads())
            if not res.ok:
                raise RuntimeError(res.message)
            report(100, "Fmod Extract 准备完成。")
            return res

        def done(res):
            self.log(f"[FMOD][EXTRACT] {res.message}\nmanifest: {res.manifest_path}")
            if res.layout:
                self.log(f"[FMOD][EXTRACT] bank dir: {res.layout.bank_dir}\nconfig: {res.layout.config_path}")
            self.info_box("Extract 已准备", "已复制 bank 并写入 Fmod Bank Tools config.ini。\n现在可以在 Fmod Bank Tools 中点击 Extract，或使用“启动并尝试 Extract”。", "Extract prepared", "Bank files were copied and Fmod Bank Tools config.ini was written.\nYou can now click Extract in Fmod Bank Tools or use Start and try Extract.")

        self.run_background_task("准备 Fmod Extract", job, done, estimated="复制 bank 文件，通常数秒；大 bank/机械硬盘可能更久。")

    def run_fmod_extract(self):
        try:
            tool = self._current_fmod_tool_path()
            bank_root = self._current_bank_root()
            banks = self._selected_bank_paths_for_current_station()
            station_tokens = self._station_bank_tokens()
            manifest = self._fmod_auto_dir() / "extract_manifest.json"
            auto_click = self._fmod_auto_click_enabled()
        except Exception as exc:
            self.show_error("启动 Fmod Extract 失败", exc)
            return

        def job(report):
            report(5, "[FMOD][EXTRACT] 准备 bank 和 config.ini。")
            prep = prepare_extract_job(tool, banks, manifest, search_root=bank_root, preferred_tokens=station_tokens, cpu_threads=self._fmod_cpu_threads())
            if not prep.ok:
                raise RuntimeError(prep.message)
            report(55, prep.message)
            res = launch_and_optionally_trigger(tool, "extract", auto_trigger=auto_click)
            report(100, res.message)
            return prep, res

        def done(result):
            prep, res = result
            self.log(f"[FMOD][EXTRACT] {prep.message}")
            self.log(f"[FMOD][EXTRACT] {res.message}")
            self.info_box("Fmod Extract 已启动", res.message, "Fmod Extract started", res.message)

        self.run_background_task("启动并尝试 Fmod Extract", job, done, estimated="启动外部程序约数秒；若启用自动点击，最多等待窗口约 12 秒。")

    def _fmod_rebuild_workspace(self) -> Path:
        out_ready = project_output_dir() / FMOD_READY_WAV_DIR_NAME
        if out_ready.exists():
            return out_ready
        work_ready = project_work_dir() / FMOD_READY_WAV_DIR_NAME
        if work_ready.exists():
            return work_ready
        raise FileNotFoundError("未找到 fmod_ready_wav 工作区。请先使用“仅生成 Mod 输出包”或“一键替换到游戏”。")

    def _prepare_fmod_rebuild_job(self):
        tool = self._current_fmod_tool_path()
        wav_workspace = self._fmod_rebuild_workspace()
        manifest = self._fmod_auto_dir() / "rebuild_manifest.json"
        return prepare_rebuild_job(tool, wav_workspace, manifest, cpu_threads=self._fmod_cpu_threads())

    def prepare_fmod_rebuild(self):
        try:
            tool = self._current_fmod_tool_path()
            wav_workspace = self._fmod_rebuild_workspace()
            manifest = self._fmod_auto_dir() / "rebuild_manifest.json"
        except Exception as exc:
            self.show_error("准备 Fmod Rebuild 失败", exc)
            return

        def job(report):
            report(5, "[FMOD][REBUILD] 正在复制 fmod_ready_wav 到外部工具目录。")
            res = prepare_rebuild_job(tool, wav_workspace, manifest, cpu_threads=self._fmod_cpu_threads())
            report(100, "Fmod Rebuild 准备完成。")
            return res

        def done(res):
            self.log(f"[FMOD][REBUILD] {res.message}\nmanifest: {res.manifest_path}")
            if res.layout:
                self.log(f"[FMOD][REBUILD] wav dir: {res.layout.wav_dir}\nbuild dir: {res.layout.rebuild_dir}\nconfig: {res.layout.config_path}")
            self.info_box("Rebuild 已准备", "已复制 fmod_ready_wav 到 Fmod Bank Tools 的 wav 目录并写入 config.ini。\n现在可以在 Fmod Bank Tools 中点击 Rebuild，或使用“启动并尝试 Rebuild”。", "Rebuild prepared", "fmod_ready_wav was copied to the Fmod Bank Tools wav folder and config.ini was written.\nYou can now click Rebuild in Fmod Bank Tools or use Start and try Rebuild.")

        self.run_background_task("准备 Fmod Rebuild", job, done, estimated="复制 wav 工作区，通常数秒到数十秒。")

    def run_fmod_rebuild(self):
        try:
            tool = self._current_fmod_tool_path()
            wav_workspace = self._fmod_rebuild_workspace()
            manifest = self._fmod_auto_dir() / "rebuild_manifest.json"
            auto_click = self._fmod_auto_click_enabled()
        except Exception as exc:
            self.show_error("启动 Fmod Rebuild 失败", exc)
            return

        def job(report):
            report(5, "[FMOD][REBUILD] 准备 wav 工作区和 config.ini。")
            prep = prepare_rebuild_job(tool, wav_workspace, manifest, cpu_threads=self._fmod_cpu_threads())
            report(55, prep.message)
            res = launch_and_optionally_trigger(tool, "rebuild", auto_trigger=auto_click)
            report(100, res.message)
            return prep, res

        def done(result):
            prep, res = result
            self.log(f"[FMOD][REBUILD] {prep.message}")
            self.log(f"[FMOD][REBUILD] {res.message}")
            self.info_box("Fmod Rebuild 已启动", res.message, "Fmod Rebuild started", res.message)

        self.run_background_task("启动并尝试 Fmod Rebuild", job, done, estimated="启动外部程序约数秒；若启用自动点击，最多等待窗口约 12 秒。")

    def collect_fmod_rebuild_outputs(self):
        try:
            tool = self._current_fmod_tool_path()
            layout = layout_from_exe(tool)
            output_dir = project_output_dir() / "fmod_rebuilt_banks"
        except Exception as exc:
            self.show_error("收集 Rebuild 输出失败", exc)
            return

        def job(report):
            report(5, "[FMOD][COLLECT] 正在收集 build 目录中的 .bank。")
            files = fmod_collect_rebuilt_banks(layout, output_dir)
            report(100, "收集完成。")
            return layout, output_dir, files

        def done(result):
            layout2, output_dir2, files = result
            if files:
                self.log("[FMOD][COLLECT] 已收集 Rebuild 输出：\n" + "\n".join(str(p) for p in files))
                self.info_box("收集完成", f"已收集 {len(files)} 个 .bank 到：\n{output_dir2}", "Collection completed", f"Collected {len(files)} .bank file(s) to:\n{output_dir2}")
            else:
                self.log(f"[FMOD][COLLECT][WARN] 未在 {layout2.rebuild_dir} 找到 .bank 输出。")
                self.warn_box("没有输出", f"未在 Fmod Bank Tools build 目录找到 .bank：\n{layout2.rebuild_dir}", "No output", f"No .bank file was found in the Fmod Bank Tools build folder:\n{layout2.rebuild_dir}")

        self.run_background_task("收集 Fmod Rebuild 输出", job, done, estimated="复制 bank 文件，通常数秒。")

    def dev_test_all_station_matching(self):
        """Temporary developer diagnostic: extract every station and test XML->FMOD mapping.

        v3.0.26: this report now reuses the same visible-slot/duplicate-alias
        filtering model used by the normal UI and replacement flow.  The old
        report counted raw RadioInfo XML rows, so stations like R1 looked broken
        even after multi-Track-bank + alias filtering made them replaceable.
        """
        if not self.current_xml:
            self.warn_box("缺少 XML", "请先扫描游戏目录并加载 RadioInfo XML。", "Missing XML", "Please scan the game folder and load RadioInfo XML first.")
            return
        if not self.question_box(
            "确认开发测试",
            "该测试会逐个电台启动 Fmod Bank Tools 执行 Extract，并生成全电台匹配报告。\n\n"
            "不会写 XML，不会 Rebuild，也不会覆盖游戏文件，但可能耗时很长。是否继续？",
            "Confirm developer test",
            "This test launches Fmod Bank Tools and runs Extract for each radio station, then writes a full mapping report.\n\n"
            "It will not write XML, Rebuild, or overwrite game files, but it may take a long time. Continue?",
        ):
            return
        try:
            current_xml = Path(self.current_xml)
            bank_root = self._current_bank_root()
            tool = self._current_fmod_tool_path()
            auto_click = self._fmod_auto_click_enabled()
        except Exception as exc:
            self.show_error("开发测试前置检查失败", exc)
            return

        def make_tokens(st, sample_rows):
            tokens: list[str] = []

            def add_token(value: object) -> None:
                text = str(value or "").strip().lower().replace('-', '_')
                if not text:
                    return
                raw = text.lstrip('r')
                if not raw:
                    return
                tokens.append(f"r{raw}")
                try:
                    n = int(raw)
                    tokens.append(f"r{n}")
                    tokens.append(f"r{n:02d}")
                except Exception:
                    pass

            try:
                if getattr(st, "number", None):
                    add_token(st.number)
                name_l = str(getattr(st, "name", "") or "").lower().replace('-', '_').replace(' ', '_')
                for part in name_l.split('_'):
                    if part.startswith('r') and any(ch.isdigit() for ch in part):
                        add_token(part)
            except Exception:
                pass
            for row in sample_rows:
                text = str(row.get("sound_name", "")).lower().replace('-', '_')
                for part in text.split('_'):
                    if part.startswith('r') and any(ch.isdigit() for ch in part):
                        add_token(part)
            out: list[str] = []
            for t in tokens:
                t = t.strip().lower()
                if t and t not in out:
                    out.append(t)
            return out

        def choose_station_banks(st, sample_rows):
            tokens = make_tokens(st, sample_rows)
            candidates = choose_banks(st.banks, bank_root=bank_root)
            paths = [Path(c.bank_path) for c in candidates if c.selected and c.exists and c.bank_path]
            same_station_tracks = self._same_station_track_bank_paths(bank_root, tokens)
            for p in same_station_tracks:
                if p not in paths:
                    paths.append(p)
            scoped = []
            for p in paths:
                stem = p.stem.lower().replace('-', '_').replace('.', '_')
                parts = [x for x in stem.split('_') if x]
                if not tokens or any(t in parts or stem.startswith(t + '_') or f"_{t}_" in f"_{stem}_" for t in tokens):
                    scoped.append(p)
            selected = self._sort_track_banks_for_station(scoped or paths)
            return selected, tokens

        def bank_key_from_record(rec) -> str:
            rel = str(getattr(rec, "txt_relpath", "") or getattr(rec, "original_relpath", "") or "").replace("\\", "/")
            top = rel.split("/", 1)[0] if rel else str(getattr(rec, "txt_stem", "") or "")
            return top.split("[", 1)[0] or "unknown"

        def summarize_records_by_bank(records) -> str:
            counts: dict[str, int] = {}
            for rec in records:
                key = bank_key_from_record(rec)
                counts[key] = counts.get(key, 0) + 1
            return ";".join(f"{k}:{counts[k]}" for k in sorted(counts, key=natural_key))

        def visible_slot_set(station_name: str, sample_rows: list[dict]) -> tuple[set[int], list[int], str]:
            xml_slots = {int(r.get("slot_index", -1)) for r in sample_rows}
            hidden: set[int] = set(_duplicate_variant_hidden_slots(sample_rows))
            profile = KNOWN_STATION_SLOT_PROFILES.get(station_name)
            profile_status = ""
            if profile:
                profile_status = str(profile.get("status") or "")
                for v in profile.get("non_replaceable_slots", []) or []:
                    try:
                        hidden.add(int(v))
                    except Exception:
                        pass
            visible = {s for s in xml_slots if s >= 0 and s not in hidden}
            return visible, sorted(hidden), profile_status

        def job(report):
            out_root = project_work_dir() / "dev_all_station_match_test"
            if out_root.exists():
                shutil.rmtree(out_root, ignore_errors=True)
            out_root.mkdir(parents=True, exist_ok=True)
            tree = parse_xml(current_xml)
            stations = list_station_infos(tree)
            summary_rows: list[dict[str, object]] = []
            profile_rows: list[dict[str, object]] = []
            total = max(1, len(stations))
            for idx, st in enumerate(stations, start=1):
                station_name = st.name
                safe_station = safe_stem(station_name, 80)
                station_dir = out_root / f"{idx:02d}_{safe_station}"
                station_dir.mkdir(parents=True, exist_ok=True)
                report(5 + int(90 * idx / total), f"[DEV] {idx}/{total} Extract + 匹配检查: {station_name}")
                try:
                    sample_rows = station_sample_rows(current_xml, station_name)
                    visible_slots, hidden_slots, profile_status = visible_slot_set(station_name, sample_rows)
                    banks, tokens = choose_station_banks(st, sample_rows)
                    if not banks:
                        summary_rows.append({
                            "station": station_name, "status": "error", "xml_tracks": len(sample_rows),
                            "xml_entries": len(sample_rows), "visible_replaceable_slots": len(visible_slots),
                            "hidden_alias_slots": len(hidden_slots), "hidden_slots": ",".join(map(str, hidden_slots)),
                            "records": 0, "matched": 0, "unmatched": len(visible_slots), "xml_only_skipped": 0,
                            "matched_visible_slots": 0, "unmatched_visible_slots": ",".join(map(str, sorted(visible_slots))),
                            "banks": "", "track_bank_count": 0, "track_banks": "", "audio_records_by_bank": "",
                            "message": "no bank candidates",
                        })
                        profile_rows.append(summary_rows[-1].copy())
                        continue
                    manifest = station_dir / "extract_manifest.json"
                    prep = prepare_extract_job(tool, banks, manifest, search_root=bank_root, preferred_tokens=tokens, cpu_threads=self._fmod_cpu_threads())
                    if not prep.ok:
                        summary_rows.append({
                            "station": station_name, "status": "error", "xml_tracks": len(sample_rows),
                            "xml_entries": len(sample_rows), "visible_replaceable_slots": len(visible_slots),
                            "hidden_alias_slots": len(hidden_slots), "hidden_slots": ",".join(map(str, hidden_slots)),
                            "records": 0, "matched": 0, "unmatched": len(visible_slots), "xml_only_skipped": 0,
                            "matched_visible_slots": 0, "unmatched_visible_slots": ",".join(map(str, sorted(visible_slots))),
                            "banks": ";".join(p.name for p in banks), "track_bank_count": len(banks), "track_banks": ";".join(p.name for p in banks), "audio_records_by_bank": "",
                            "message": prep.message,
                        })
                        profile_rows.append(summary_rows[-1].copy())
                        continue
                    res = launch_trigger_and_wait(tool, "extract", auto_trigger=auto_click, timeout_sec=900)
                    if not res.ok or res.layout is None:
                        summary_rows.append({
                            "station": station_name, "status": "extract_failed", "xml_tracks": len(sample_rows),
                            "xml_entries": len(sample_rows), "visible_replaceable_slots": len(visible_slots),
                            "hidden_alias_slots": len(hidden_slots), "hidden_slots": ",".join(map(str, hidden_slots)),
                            "records": 0, "matched": 0, "unmatched": len(visible_slots), "xml_only_skipped": 0,
                            "matched_visible_slots": 0, "unmatched_visible_slots": ",".join(map(str, sorted(visible_slots))),
                            "banks": ";".join(p.name for p in banks), "track_bank_count": len(banks), "track_banks": ";".join(p.name for p in banks), "audio_records_by_bank": "",
                            "message": res.message,
                        })
                        profile_rows.append(summary_rows[-1].copy())
                        continue
                    template = station_dir / FMOD_EXTRACT_TEMPLATE_DIR_NAME
                    if template.exists():
                        shutil.rmtree(template, ignore_errors=True)
                    shutil.copytree(res.layout.wav_dir, template)
                    track_order = station_dir / TRACK_ORDER_FILE_NAME
                    ensure_track_order_file(track_order, current_xml, station_name, [], None, template)
                    rows = read_track_order(track_order)
                    records = parse_extract_template(template)
                    write_fmod_sound_inventory(station_dir / "fmod_sound_inventory.csv", template)
                    write_replacement_plan(station_dir / "replacement_plan.csv", rows, {}, range(len(rows)), template)

                    visible_rows = [r for r in rows if int(r.slot_index) in visible_slots]
                    hidden_rows = [r for r in rows if int(r.slot_index) in set(hidden_slots)]
                    unmatched_visible_rows = [r for r in visible_rows if r.bank_index < 0 or not r.original_wav_relpath]
                    matched_visible = len(visible_rows) - len(unmatched_visible_rows)
                    raw_unmatched = [r for r in rows if r.bank_index < 0 or not r.original_wav_relpath]
                    raw_xml_only = [r for r in raw_unmatched if records and int(r.slot_index) >= len(records)]

                    if "streamer" in station_name.lower() or profile_status == "special":
                        status = "special"
                    elif not unmatched_visible_rows:
                        status = "ok"
                    else:
                        status = "needs_mapping"
                    message_parts = []
                    if hidden_slots:
                        message_parts.append("hidden_alias=" + ",".join(map(str, hidden_slots)))
                    if unmatched_visible_rows:
                        message_parts.append("unmatched_visible=" + ",".join(str(r.slot_index) for r in unmatched_visible_rows))
                    if raw_xml_only:
                        message_parts.append("raw_xml_only=" + ",".join(str(r.slot_index) for r in raw_xml_only))
                    row = {
                        "station": station_name,
                        "status": status,
                        "xml_tracks": len(rows),
                        "xml_entries": len(rows),
                        "visible_replaceable_slots": len(visible_rows),
                        "hidden_alias_slots": len(hidden_rows),
                        "hidden_slots": ",".join(map(str, hidden_slots)),
                        "records": len(records),
                        "matched": matched_visible,
                        "unmatched": len(unmatched_visible_rows),
                        "xml_only_skipped": len(raw_xml_only),
                        "matched_visible_slots": matched_visible,
                        "unmatched_visible_slots": ",".join(str(r.slot_index) for r in unmatched_visible_rows),
                        "banks": ";".join(str(p.name) for p in banks),
                        "track_bank_count": len(banks),
                        "track_banks": ";".join(str(p.name) for p in banks),
                        "audio_records_by_bank": summarize_records_by_bank(records),
                        "message": "; ".join(message_parts),
                    }
                    summary_rows.append(row)
                    profile_rows.append(row.copy())
                except Exception as exc:
                    row = {
                        "station": station_name, "status": "exception", "xml_tracks": 0,
                        "xml_entries": 0, "visible_replaceable_slots": 0, "hidden_alias_slots": 0, "hidden_slots": "",
                        "records": 0, "matched": 0, "unmatched": 0, "xml_only_skipped": 0,
                        "matched_visible_slots": 0, "unmatched_visible_slots": "",
                        "banks": "", "track_bank_count": 0, "track_banks": "", "audio_records_by_bank": "",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    summary_rows.append(row)
                    profile_rows.append(row.copy())
            summary_path = out_root / "dev_all_station_match_summary.csv"
            profile_path = project_work_dir() / "station_slot_profiles.csv"
            track_profile_path = out_root / "station_track_bank_profile.csv"
            fields = [
                "station", "status", "xml_tracks", "xml_entries", "visible_replaceable_slots",
                "hidden_alias_slots", "hidden_slots", "records", "matched", "unmatched", "xml_only_skipped",
                "matched_visible_slots", "unmatched_visible_slots", "banks", "track_bank_count", "track_banks",
                "audio_records_by_bank", "message",
            ]
            for path, rows_to_write in [(summary_path, summary_rows), (profile_path, profile_rows), (track_profile_path, profile_rows)]:
                with path.open("w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows_to_write)
            return str(summary_path)

        def done(path):
            self.log(f"[DEV][OK] 全电台匹配测试完成：{path}")
            # Refresh station labels/status using the newly generated profile CSV.
            if self.current_xml:
                self.load_xml(self.current_xml, quiet=True)
            self.info_box(
                "开发测试完成",
                f"已生成全电台匹配报告：\n{path}\n\n另已生成 station_track_bank_profile.csv。\n该功能是临时开发诊断，确认问题修复后会移除。",
                "Developer test finished",
                f"Full station mapping report generated:\n{path}\n\nstation_track_bank_profile.csv was also generated.\nThis is a temporary diagnostic feature and will be removed after the issue is fixed.",
            )

        self.run_background_task(
            "开发测试：全电台匹配",
            job,
            done,
            estimated="会逐个电台 Extract，可能需要较长时间。仅用于开发诊断。",
        )

    def _dev_collect_unmatched_xml_rows(self, station_name: str) -> list[dict]:
        """Return XML rows that are not shown as normal replaceable slots.

        These rows are useful for locating special/event/cinematic music that
        appears in RadioInfo XML but is not stored in the main station bank.
        """
        if not self.current_xml:
            return []
        rows = station_sample_rows(self.current_xml, station_name)
        profile = self._station_slot_profile(station_name, len(rows))
        slots: set[int] = set()
        if profile:
            for key in ("non_replaceable_slots", "xml_only_slots", "unmatched_slots"):
                for v in profile.get(key, []) or []:
                    try:
                        slots.add(int(v))
                    except Exception:
                        pass
            fmod_n = int(profile.get("fmod_audio_slots") or 0)
            if fmod_n > 0 and len(rows) > fmod_n:
                slots.update(range(fmod_n, len(rows)))
        if not slots:
            # Fallback: compare current visible table slots with XML rows.
            visible = set()
            try:
                for r in range(self.slot_table.rowCount()):
                    item = self.slot_table.item(r, 1)
                    if item is not None:
                        visible.add(int(item.data(Qt.UserRole)))
            except Exception:
                visible = set()
            slots = {int(x.get("slot_index", -1)) for x in rows if int(x.get("slot_index", -1)) not in visible}
        return [row for row in rows if int(row.get("slot_index", -1)) in slots]

    @staticmethod
    def _dev_text_hit_score(target: str, hay: str) -> int:
        import re
        target = str(target or "").strip()
        hay_l = str(hay or "").lower()
        if not target:
            return 0
        target_l = target.lower()
        score = 0
        if target_l and target_l in hay_l:
            score += 1000
        parts = [p for p in re.split(r"[^0-9a-zA-Z]+", target_l) if len(p) >= 3 and p not in {"hz6", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "id"}]
        for part in parts:
            if part in hay_l:
                score += 25
        return score

    def _dev_record_bank_key(self, rec) -> str:
        """Best-effort bank/folder key for a Fmod ExtractRecord."""
        rel = str(getattr(rec, "txt_relpath", "") or getattr(rec, "original_relpath", "") or "").replace("\\", "/")
        top = rel.split("/", 1)[0] if rel else str(getattr(rec, "txt_stem", "") or "")
        top = top.split("[", 1)[0]
        return top.lower()

    def _dev_extract_bank_set_cached(self, tool: Path, banks: list[Path], cache_name: str, report, label: str) -> tuple[bool, Path | None, str]:
        """Extract a large bank set with bounded batches and persistent cache.

        The earlier all-at-once developer scan asked Fmod Bank Tools to extract
        1000+ banks in one run.  In practice the GUI often produced only the
        first bank before our completion detector considered the output stable.
        This helper now processes banks in small batches, closes the GUI after
        every batch, merges every extracted wav/txt tree into one cache, and
        writes bank_extract_status.csv so we can verify which banks actually
        produced audio.
        """
        banks = [Path(b) for b in banks]
        cache_root = project_work_dir() / "dev_fmod_extract_cache" / safe_stem(cache_name, 80)
        extract_dir = cache_root / "wav_extract"
        manifest_path = cache_root / "manifest.json"
        status_path = cache_root / "bank_extract_status.csv"
        batch_size = 20
        sig = []
        for b in banks:
            try:
                st = b.stat()
                sig.append({"path": str(b.resolve()), "name": b.name, "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)})
            except OSError:
                sig.append({"path": str(b), "name": b.name, "size": -1, "mtime_ns": -1})
        expected_manifest = {"version": 3, "batch_size": batch_size, "count": len(banks), "banks": sig}
        try:
            if manifest_path.exists() and extract_dir.exists() and status_path.exists():
                old = json.loads(manifest_path.read_text(encoding="utf-8"))
                if old == expected_manifest and parse_extract_template(extract_dir):
                    return True, extract_dir, f"已复用开发 Extract 缓存：{extract_dir}。"
        except Exception:
            pass

        if cache_root.exists():
            shutil.rmtree(cache_root, ignore_errors=True)
        cache_root.mkdir(parents=True, exist_ok=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        if not banks:
            return False, None, "没有可 Extract 的 bank。"

        def bank_key_from_path(p: Path) -> str:
            return p.stem.lower()

        def extracted_keys_from_tree(root: Path) -> set[str]:
            keys: set[str] = set()
            try:
                for child in root.iterdir():
                    name = child.name.split("[", 1)[0].lower()
                    if child.is_dir() and (list(child.rglob("*.wav")) or list(child.rglob("*.txt"))):
                        keys.add(name)
                    elif child.is_file() and child.suffix.lower() in {".wav", ".txt"}:
                        keys.add(child.stem.split("[", 1)[0].lower())
            except Exception:
                pass
            return keys

        def merge_extract_tree(src_root: Path, dst_root: Path) -> None:
            dst_root.mkdir(parents=True, exist_ok=True)
            for item in src_root.iterdir():
                dst = dst_root / item.name
                if item.is_dir():
                    if dst.exists():
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(item, dst)
                elif item.is_file():
                    shutil.copy2(item, dst)

        report(3, COMPACT_PROGRESS_PREFIX + self.ui_text(
            f"准备 Extract：{len(banks)} 个 bank，每批 {batch_size} 个。",
            f"Preparing Extract: {len(banks)} banks, {batch_size} per batch.",
        ))
        status_rows: list[dict[str, object]] = []
        total_batches = (len(banks) + batch_size - 1) // batch_size
        layout_for_status = layout_from_exe(tool)
        for batch_idx in range(total_batches):
            batch = banks[batch_idx * batch_size:(batch_idx + 1) * batch_size]
            start_percent = 3 + int(55 * batch_idx / max(1, total_batches))
            report(start_percent, COMPACT_PROGRESS_PREFIX + self.ui_text(
                f"Extract 批次 {batch_idx + 1}/{total_batches}：{len(batch)} 个 bank，请不要关闭 Fmod Bank Tools。",
                f"Extract batch {batch_idx + 1}/{total_batches}: {len(batch)} banks. Do not close Fmod Bank Tools.",
            ))
            prep = prepare_extract_job(tool, batch, cache_root / f"extract_manifest_batch_{batch_idx + 1:04d}.json", clean_bank_dir=True, search_root=None, preferred_tokens=[], cpu_threads=self._fmod_cpu_threads())
            if not prep.ok:
                for b in batch:
                    status_rows.append({"bank_name": b.name, "bank_path": str(b), "batch_id": batch_idx + 1, "status": "prepare_failed", "sound_count": 0, "extract_output_dir": "", "cache_used": 0, "error": prep.message})
                continue
            res = launch_trigger_and_wait(tool, "extract", auto_trigger=self._fmod_auto_click_enabled(), timeout_sec=2400)
            report(min(95, start_percent + 2), COMPACT_PROGRESS_PREFIX + self.ui_text(
                f"Extract 批次 {batch_idx + 1}/{total_batches} 已结束，正在扫描输出文件。",
                f"Extract batch {batch_idx + 1}/{total_batches} finished; scanning output files.",
            ))
            batch_out = cache_root / f"batch_{batch_idx + 1:04d}_wav"
            if batch_out.exists():
                shutil.rmtree(batch_out, ignore_errors=True)
            if res.layout is not None and res.layout.wav_dir.exists():
                try:
                    shutil.copytree(res.layout.wav_dir, batch_out)
                except Exception:
                    batch_out.mkdir(parents=True, exist_ok=True)
            keys = extracted_keys_from_tree(batch_out)
            if batch_out.exists():
                merge_extract_tree(batch_out, extract_dir)
            batch_records = parse_extract_template(batch_out) if batch_out.exists() else []
            key_sound_count: dict[str, int] = {}
            for rec in batch_records:
                key = self._dev_record_bank_key(rec)
                key_sound_count[key] = key_sound_count.get(key, 0) + 1
            report(min(97, start_percent + 3), COMPACT_PROGRESS_PREFIX + self.ui_text(
                f"正在记录批次 {batch_idx + 1}/{total_batches} 的 Extract 状态。",
                f"Writing Extract status for batch {batch_idx + 1}/{total_batches}.",
            ))
            for b in batch:
                key = bank_key_from_path(b)
                sound_count = key_sound_count.get(key, 0)
                if key in keys or sound_count:
                    st = "extracted"
                    err = ""
                elif res.ok:
                    st = "no_output_detected"
                    err = "Fmod Extract completed, but no wav/txt folder matching this bank was found."
                else:
                    st = "extract_failed"
                    err = res.message
                status_rows.append({"bank_name": b.name, "bank_path": str(b), "batch_id": batch_idx + 1, "status": st, "sound_count": sound_count, "extract_output_dir": str(batch_out), "cache_used": 0, "error": err})

        report(88, COMPACT_PROGRESS_PREFIX + self.ui_text(
            "所有 Extract 批次完成，正在写入状态表。",
            "All Extract batches completed; writing status table.",
        ))
        with status_path.open("w", encoding="utf-8-sig", newline="") as f:
            fields = ["bank_name", "bank_path", "batch_id", "status", "sound_count", "extract_output_dir", "cache_used", "error"]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(status_rows)

        report(90, COMPACT_PROGRESS_PREFIX + self.ui_text(
            "正在解析合并后的 wav/txt 输出。",
            "Parsing merged wav/txt output.",
        ))
        records = parse_extract_template(extract_dir)
        unique_keys = {self._dev_record_bank_key(r) for r in records}
        manifest_path.write_text(json.dumps(expected_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if len(banks) > 1 and len(unique_keys) <= 1:
            return False, extract_dir, f"全 Bank Extract 结果异常：目标 bank={len(banks)}，但只解析到 {len(unique_keys)} 个 bank。请查看 {status_path}。"
        return True, extract_dir, f"分批 Extract 完成：目标 bank={len(banks)}，解析到含音频 bank={len(unique_keys)}，记录数={len(records)}。状态报告：{status_path}。"

    def dev_search_unmatched_soundnames(self):
        """Temporary developer diagnostic: one-shot all-station / all-bank SoundName search.

        This replaces the older current-station-only search.  It performs a
        single batched Extract for all extractable banks, parses the resulting
        wav/txt tree once, then matches every RadioInfo XML entry from every
        station against the full FMOD audio inventory.  It does not write XML,
        does not Rebuild, and does not overwrite game files.
        """
        if not self.current_xml:
            self.warn_box("缺少 XML", "请先选择或扫描 RadioInfo XML。", "Missing XML", "Please select or scan RadioInfo XML first.")
            return
        try:
            bank_root = self._current_bank_root()
            tool = self._current_fmod_tool_path()
        except Exception as exc:
            self.show_error("开发测试前置检查失败", exc)
            return

        msg = (
            "将对所有电台执行一次大规模 SoundName / 音频匹配扫描。\n\n"
            "工具会把所有可提取 bank 分批交给 Fmod Bank Tools 执行 Extract，完成后合并并缓存所有音频信息，"
            "并匹配 RadioInfo XML 里所有电台的所有曲目条目。\n\n"
            "不会写 XML，不会 Rebuild，不会覆盖游戏文件。首次运行可能耗时较长，之后重复运行会复用缓存。"
        )
        msg_en = (
            "This will run a large all-station SoundName/audio matching scan.\n\n"
            "All extractable banks will be extracted in bounded batches and cached. The tool will then parse the combined extract tree and match every RadioInfo XML song entry from every station.\n\n"
            "It will not write XML, Rebuild, or overwrite game files. The first run may take a long time; later runs reuse the cache."
        )
        if not self.confirm_box("确认全电台音频扫描", msg, "Confirm all-station audio scan", msg_en):
            return

        existing_records = self._non_empty_paths(self._dev_full_scan_record_paths())
        existing_action = "none"
        if existing_records:
            existing_action = self.ask_dev_existing_scan_action(existing_records)
            if existing_action == "cancel":
                return

        def job(report):
            out_root = project_work_dir() / "dev_all_station_bank_sound_scan"
            cache_root_to_reset = project_work_dir() / "dev_fmod_extract_cache" / safe_stem("all_extractable_banks_v3_batched", 80)
            if existing_action == "delete":
                report(1, COMPACT_PROGRESS_PREFIX + self.ui_text(
                    "正在删除旧开发者扫描记录并准备重新 Extract。",
                    "Deleting old developer scan data and preparing a fresh Extract.",
                ))
                if cache_root_to_reset.exists():
                    shutil.rmtree(cache_root_to_reset, ignore_errors=True)
            elif existing_action == "reuse":
                report(1, COMPACT_PROGRESS_PREFIX + self.ui_text(
                    "保留已有开发者缓存；如果匹配当前 bank，将复用并重新生成表格。",
                    "Keeping existing developer cache; matching bank cache will be reused while reports are regenerated.",
                ))
            if out_root.exists():
                shutil.rmtree(out_root, ignore_errors=True)
            out_root.mkdir(parents=True, exist_ok=True)

            report(1, COMPACT_PROGRESS_PREFIX + self.ui_text(
                "正在收集 XML 和 bank 文件。",
                "Collecting XML and bank files.",
            ))
            xml_paths = self._dev_radioinfo_xml_candidates()
            if not xml_paths and self.current_xml:
                xml_paths = [Path(self.current_xml)]
            stations_by_xml: dict[str, list[object]] = {}
            target_rows: list[dict[str, object]] = []
            for xml_path in xml_paths:
                try:
                    tree = parse_xml(Path(xml_path))
                    stations = list_station_infos(tree)
                    stations_by_xml[str(xml_path)] = stations
                except Exception as exc:
                    report(5, f"[DEV][XML][WARN] 跳过无法解析的 XML: {xml_path} ({exc})")
                    continue
                for st in stations:
                    station_name = str(st.name)
                    for row in station_sample_rows(Path(xml_path), station_name):
                        target_rows.append({
                            "xml_file": Path(xml_path).name,
                            "xml_path": str(xml_path),
                            "station": station_name,
                            "slot_index": int(row.get("slot_index", -1)),
                            "sound_name": str(row.get("sound_name", "")),
                            "display_name": str(row.get("original_display_name", "")),
                            "artist": str(row.get("original_artist", "")),
                            "sample_length": int(row.get("sample_length") or 0),
                            "sample_rate": int(row.get("sample_rate") or 0),
                        })

            try:
                all_banks = sorted(Path(bank_root).rglob("*.bank"), key=lambda p: p.as_posix().lower())
            except Exception:
                all_banks = []
            precheck_pool = [p for p in all_banks if ("strings" not in p.name.lower() and "master" not in p.name.lower())]
            candidate_banks, bank_precheck_rows = self._dev_filter_extractable_banks(precheck_pool, report, "all bank developer scan")

            rows_out: list[dict[str, object]] = []
            station_summary: dict[str, dict[str, object]] = {}
            for t in target_rows:
                station_summary.setdefault(str(t["station"]), {
                    "station": str(t["station"]),
                    "xml_entries": 0,
                    "matched_targets": 0,
                    "name_hit_targets": 0,
                    "length_candidate_targets": 0,
                    "no_candidate_targets": 0,
                })
                station_summary[str(t["station"])] ["xml_entries"] = int(station_summary[str(t["station"])] ["xml_entries"]) + 1

            ok, extract_dir, message = self._dev_extract_bank_set_cached(tool, candidate_banks, "all_extractable_banks_v3_batched", report, "all extractable banks / all stations")
            # Surface the batch extraction status next to the scan reports.
            try:
                cache_status = project_work_dir() / "dev_fmod_extract_cache" / safe_stem("all_extractable_banks_v3_batched", 80) / "bank_extract_status.csv"
                if cache_status.exists():
                    shutil.copy2(cache_status, out_root / "bank_extract_status.csv")
            except Exception:
                pass
            records = []
            text_blob_by_key: dict[str, str] = {}
            if not ok or extract_dir is None:
                rows_out.append({
                    "xml_file": "", "station": "", "target_slot": "", "target_sound_name": "", "target_display_name": "",
                    "candidate_rank": "", "candidate_bank": "", "candidate_sound_file": "", "candidate_frames": "",
                    "candidate_samplerate": "", "candidate_duration_sec": "", "length_diff": "", "text_score": "",
                    "confidence": "extract_failed", "reason": message,
                })
            else:
                report(63, COMPACT_PROGRESS_PREFIX + self.ui_text(
                    "Extract 完成，正在生成音频清单。",
                    "Extract completed; generating audio inventory.",
                ))
                records = parse_extract_template(extract_dir)
                try:
                    for txt in extract_dir.rglob("*.txt"):
                        key = txt.relative_to(extract_dir).as_posix().split("/", 1)[0].split("[", 1)[0].lower()
                        prev = text_blob_by_key.get(key, "")
                        text_blob_by_key[key] = (prev + "\n" + txt.read_text(encoding="utf-8-sig", errors="ignore"))[:300000]
                except Exception:
                    pass

                # Full FMOD audio inventory from the one-shot extract.
                inv_path = out_root / "fmod_all_bank_audio_inventory.csv"
                inv_fields = ["bank_key", "txt_relpath", "subsound_index", "sound_file", "wav_relpath", "frames", "samplerate", "duration_sec"]
                with inv_path.open("w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=inv_fields)
                    writer.writeheader()
                    for rec in records:
                        writer.writerow({
                            "bank_key": self._dev_record_bank_key(rec),
                            "txt_relpath": rec.txt_relpath,
                            "subsound_index": rec.subsound_index,
                            "sound_file": rec.extracted_name,
                            "wav_relpath": rec.original_relpath,
                            "frames": rec.frames,
                            "samplerate": rec.samplerate,
                            "duration_sec": f"{float(rec.duration_sec or 0):.3f}",
                        })

                def _music_bank_role(bank_key: str) -> str:
                    return dev_bank_role_from_key(bank_key)

                def _is_music_candidate_record(rec) -> bool:
                    key = self._dev_record_bank_key(rec).lower()
                    dur = float(rec.duration_sec or 0)
                    if dur < 30.0 or dur > 900.0:
                        return False
                    excluded_tokens = [
                        "vo_", "vopr_", "satnav", "dialogue", "speech", "crowd", "traffic",
                        "surface", "collision", "passby", "engine", "vehicle", "car_", "cars_",
                        "footstep", "foley", "ambience_emitters_nonpersistent"
                    ]
                    if any(tok in key for tok in excluded_tokens):
                        return False
                    role = _music_bank_role(key)
                    if role != "other":
                        return True
                    return dur >= 90.0 and ("song" in key or "track" in key or "music" in key)

                music_records = [rec for rec in records if _is_music_candidate_record(rec)]
                music_bank_stats: dict[str, dict[str, object]] = {}
                for rec in music_records:
                    key = self._dev_record_bank_key(rec)
                    row = music_bank_stats.setdefault(key, {
                        "bank_key": key, "role": _music_bank_role(key), "sound_count": 0,
                        "long_audio_count": 0, "min_duration_sec": 999999.0,
                        "max_duration_sec": 0.0, "total_duration_sec": 0.0,
                    })
                    dur = float(rec.duration_sec or 0)
                    row["sound_count"] = int(row["sound_count"]) + 1
                    row["long_audio_count"] = int(row["long_audio_count"]) + (1 if dur >= 60 else 0)
                    row["min_duration_sec"] = min(float(row["min_duration_sec"]), dur)
                    row["max_duration_sec"] = max(float(row["max_duration_sec"]), dur)
                    row["total_duration_sec"] = float(row["total_duration_sec"]) + dur
                music_inv_path = out_root / "music_bank_inventory.csv"
                with music_inv_path.open("w", encoding="utf-8-sig", newline="") as f:
                    fields_m = ["bank_key", "role", "sound_count", "long_audio_count", "min_duration_sec", "max_duration_sec", "avg_duration_sec"]
                    writer = csv.DictWriter(f, fieldnames=fields_m)
                    writer.writeheader()
                    for row in sorted(music_bank_stats.values(), key=lambda r: (str(r["role"]), str(r["bank_key"]))):
                        count = max(1, int(row["sound_count"]))
                        writer.writerow({
                            "bank_key": row["bank_key"],
                            "role": row["role"],
                            "sound_count": row["sound_count"],
                            "long_audio_count": row["long_audio_count"],
                            "min_duration_sec": f"{float(row['min_duration_sec']):.3f}",
                            "max_duration_sec": f"{float(row['max_duration_sec']):.3f}",
                            "avg_duration_sec": f"{float(row['total_duration_sec']) / count:.3f}",
                        })

                def _station_number_for_name(name: str, xml_path: str = "") -> str:
                    station_lists = []
                    if xml_path and xml_path in stations_by_xml:
                        station_lists.append(stations_by_xml.get(xml_path) or [])
                    station_lists.extend(stations_by_xml.values())
                    for station_list in station_lists:
                        for st in station_list:
                            if str(getattr(st, "name", "")) == str(name):
                                return str(getattr(st, "number", "") or "")
                    return ""

                def _target_priority_score(target: dict[str, object], rec) -> tuple[int, int, int, int]:
                    target_len = int(target.get("sample_length") or 0)
                    frames = int(rec.frames or 0)
                    diff = abs(target_len - frames) if target_len and frames else 999999999
                    key = self._dev_record_bank_key(rec).lower()
                    st_no = _station_number_for_name(str(target.get("station", "")), str(target.get("xml_path", ""))).lower()
                    role = _music_bank_role(key)
                    bank_score = 0
                    if st_no and key.startswith(f"r{st_no}_tracks"):
                        bank_score = 500
                    elif role == "glb_radio_3d":
                        bank_score = 300
                    elif role in {"press_start", "cinematic", "video_player"}:
                        bank_score = 220
                    elif role == "radio_tracks":
                        bank_score = 80
                    elif role == "music_hint":
                        bank_score = 50
                    length_score = max(0, 200 - int(diff / max(1, 44100)))
                    return (bank_score + length_score, -diff, frames, int(rec.subsound_index or -1))

                # Focused shortlist for entries that are known XML-only/hidden by station profiles
                # or have suspicious _ID aliases.  This report is designed for manual listening,
                # not for automatic replacement.
                shortlist_targets: list[dict[str, object]] = []
                seen_short = set()
                for t in target_rows:
                    station = str(t.get("station", ""))
                    slot = int(t.get("slot_index") or -1)
                    sound_name = str(t.get("sound_name") or "")
                    prof = self._station_slot_profile(station, None)
                    hidden_slots = set(int(x) for x in (prof or {}).get("non_replaceable_slots", []) if str(x).lstrip('-').isdigit())
                    suspicious = slot in hidden_slots or sound_name.endswith("_ID") or "_ID" in sound_name
                    if suspicious and (station, slot, sound_name) not in seen_short:
                        shortlist_targets.append(t)
                        seen_short.add((station, slot, sound_name))
                short_path = out_root / "missing_track_candidate_shortlist.csv"
                short_fields = [
                    "station", "target_slot", "target_sound_name", "target_display_name", "target_sample_length",
                    "candidate_rank", "candidate_bank", "candidate_role", "candidate_sound_file",
                    "candidate_frames", "candidate_duration_sec", "length_diff", "length_diff_sec",
                    "review_priority", "note"
                ]
                with short_path.open("w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=short_fields)
                    writer.writeheader()
                    for t in shortlist_targets:
                        target_len = int(t.get("sample_length") or 0)
                        candidates = sorted(music_records, key=lambda r: _target_priority_score(t, r), reverse=True)[:20]
                        for rank, rec in enumerate(candidates, start=1):
                            frames = int(rec.frames or 0)
                            diff = abs(target_len - frames) if target_len and frames else 0
                            key = self._dev_record_bank_key(rec)
                            writer.writerow({
                                "station": t.get("station", ""),
                                "target_slot": t.get("slot_index", ""),
                                "target_sound_name": t.get("sound_name", ""),
                                "target_display_name": t.get("display_name", ""),
                                "target_sample_length": target_len,
                                "candidate_rank": rank,
                                "candidate_bank": key,
                                "candidate_role": _music_bank_role(key),
                                "candidate_sound_file": rec.original_relpath or rec.extracted_name,
                                "candidate_frames": frames,
                                "candidate_duration_sec": f"{float(rec.duration_sec or 0):.3f}",
                                "length_diff": diff,
                                "length_diff_sec": f"{diff / float(rec.samplerate or 44100):.3f}",
                                "review_priority": _target_priority_score(t, rec)[0],
                                "note": "manual_preview_only_not_auto_mapping",
                            })

                # v3.1.3: do not scan every record for every XML row.  Build
                # searchable indexes once, then query a small candidate set per target.
                import bisect
                import re

                report(80, COMPACT_PROGRESS_PREFIX + self.ui_text(
                    "正在建立 XML→bank 快速匹配索引。",
                    "Building fast XML→bank matching indexes.",
                ))
                token_re = re.compile(r"[0-9a-zA-Z]+")
                ignored_tokens = {"hz6", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "id"}

                def _target_tokens(text: object) -> list[str]:
                    return [p for p in token_re.findall(str(text or "").lower()) if len(p) >= 3 and p not in ignored_tokens]

                query_tokens: set[str] = set()
                for t in target_rows:
                    query_tokens.update(_target_tokens(t.get("sound_name", "")))

                bank_tokens_by_key: dict[str, set[str]] = {}
                for bank_key, blob in text_blob_by_key.items():
                    # The old matcher searched a huge bank text blob once for every
                    # target/record pair.  Tokenizing once preserves the useful hint
                    # signal while avoiding minutes of repeated substring scanning.
                    # Keep only tokens that can actually be queried by XML targets.
                    bank_tokens_by_key[bank_key] = {
                        tok for tok in token_re.findall(str(blob or "").lower())
                        if tok in query_tokens
                    }

                indexed_records: list[dict[str, object]] = []
                token_to_record_ids: dict[str, set[int]] = {}
                exact_to_record_ids: dict[str, set[int]] = {}
                frame_pairs: list[tuple[int, int]] = []
                for rec_id, rec in enumerate(records):
                    key = self._dev_record_bank_key(rec)
                    small_hay = " ".join([
                        key,
                        str(rec.txt_relpath or ""),
                        str(rec.extracted_name or ""),
                        str(rec.extracted_stem or ""),
                        str(rec.original_relpath or ""),
                    ]).lower()
                    rec_tokens_all = set(token_re.findall(small_hay))
                    rec_tokens = {tok for tok in rec_tokens_all if tok in query_tokens}
                    bank_tokens = bank_tokens_by_key.get(key, set())
                    search_tokens = rec_tokens | bank_tokens
                    indexed_records.append({
                        "rec": rec,
                        "key": key,
                        "small_hay": small_hay,
                        "tokens": search_tokens,
                        "frames": int(rec.frames or 0),
                    })
                    for tok in search_tokens:
                        if len(tok) >= 3:
                            token_to_record_ids.setdefault(tok, set()).add(rec_id)
                    # Exact hits are limited to record-local text; large bank text is
                    # handled by token hits to avoid O(XML * bank_text) behavior.
                    for tok in rec_tokens:
                        exact_to_record_ids.setdefault(tok, set()).add(rec_id)
                    frames = int(rec.frames or 0)
                    if frames > 0:
                        frame_pairs.append((frames, rec_id))
                frame_pairs.sort(key=lambda x: x[0])
                frame_values = [x[0] for x in frame_pairs]

                def _length_candidate_ids(target_len: int) -> set[int]:
                    if target_len <= 0 or not frame_pairs:
                        return set()
                    tolerance = max(44100 * 3, int(max(1, target_len) * 0.02))
                    lo = bisect.bisect_left(frame_values, target_len - tolerance)
                    hi = bisect.bisect_right(frame_values, target_len + tolerance)
                    return {rec_id for _frames, rec_id in frame_pairs[lo:hi]}

                def _score_record_for_target(target: dict[str, object], rec_id: int, parts: list[str]) -> tuple[int, int, str, object, int] | None:
                    meta = indexed_records[rec_id]
                    rec = meta["rec"]
                    key = str(meta["key"])
                    target_name = str(target.get("sound_name", "") or "").strip().lower()
                    target_len = int(target.get("sample_length") or 0)
                    frames = int(meta.get("frames") or 0)
                    length_diff = abs(target_len - frames) if target_len and frames else 999999999
                    length_ok = length_diff <= max(44100 * 3, int(max(1, target_len) * 0.02))
                    tokens = meta.get("tokens") or set()
                    text_score = 0
                    # Keep a strong exact bonus only for local record fields; token
                    # hits from bank-level txt still contribute as weaker hints.
                    if target_name and target_name in str(meta.get("small_hay", "")):
                        text_score += 1000
                    for part in parts:
                        if part in tokens:
                            text_score += 25
                    if not text_score and not length_ok:
                        return None
                    return (text_score, -length_diff, key, rec, length_diff)

                total_targets = max(1, len(target_rows))
                for idx, target in enumerate(target_rows, start=1):
                    if idx == 1 or idx % 200 == 0 or idx == len(target_rows):
                        report(82 + int(15 * idx / total_targets), COMPACT_PROGRESS_PREFIX + self.ui_text(
                            f"快速匹配 XML 曲目 {idx}/{len(target_rows)}。",
                            f"Fast matching XML tracks {idx}/{len(target_rows)}.",
                        ))
                    target_name = str(target.get("sound_name", "") or "").strip().lower()
                    parts = _target_tokens(target_name)
                    candidate_ids: set[int] = set()
                    if target_name:
                        # Exact token seeding handles names embedded in filenames; full
                        # substring scoring is applied below only to seeded records.
                        for tok in _target_tokens(target_name):
                            candidate_ids.update(exact_to_record_ids.get(tok, set()))
                    for part in parts:
                        candidate_ids.update(token_to_record_ids.get(part, set()))
                    candidate_ids.update(_length_candidate_ids(int(target.get("sample_length") or 0)))

                    best = []
                    for rec_id in candidate_ids:
                        scored = _score_record_for_target(target, rec_id, parts)
                        if scored is not None:
                            best.append(scored)
                    best.sort(key=lambda x: (x[0], x[1]), reverse=True)
                    summary = station_summary.get(str(target["station"]))
                    if not best:
                        if summary is not None:
                            summary["no_candidate_targets"] = int(summary["no_candidate_targets"]) + 1
                        rows_out.append({
                            "xml_file": target.get("xml_file", ""),
                            "station": target["station"],
                            "target_slot": target["slot_index"],
                            "target_sound_name": target["sound_name"],
                            "target_display_name": target["display_name"],
                            "candidate_rank": "",
                            "candidate_bank": "",
                            "candidate_sound_file": "",
                            "candidate_frames": "",
                            "candidate_samplerate": "",
                            "candidate_duration_sec": "",
                            "length_diff": "",
                            "text_score": "",
                            "confidence": "no_candidate",
                            "reason": "no indexed name or length candidate in cached all-bank extract",
                        })
                        continue
                    if summary is not None:
                        summary["matched_targets"] = int(summary["matched_targets"]) + 1
                        if best[0][0] >= 1000:
                            summary["name_hit_targets"] = int(summary["name_hit_targets"]) + 1
                        elif best[0][0] > 0:
                            summary["length_candidate_targets"] = int(summary["length_candidate_targets"]) + 1
                        else:
                            summary["length_candidate_targets"] = int(summary["length_candidate_targets"]) + 1
                    for rank, (text_score, _negdiff, key, rec, length_diff) in enumerate(best[:8], start=1):
                        confidence = "name_hit" if text_score >= 1000 else ("text_hint" if text_score else "length_candidate")
                        rows_out.append({
                            "xml_file": target.get("xml_file", ""),
                            "station": target["station"],
                            "target_slot": target["slot_index"],
                            "target_sound_name": target["sound_name"],
                            "target_display_name": target["display_name"],
                            "candidate_rank": rank,
                            "candidate_bank": key,
                            "candidate_sound_file": rec.original_relpath or rec.extracted_name,
                            "candidate_frames": rec.frames,
                            "candidate_samplerate": rec.samplerate,
                            "candidate_duration_sec": f"{float(rec.duration_sec or 0):.3f}",
                            "length_diff": length_diff,
                            "text_score": text_score,
                            "confidence": confidence,
                            "reason": "candidate from indexed cached all-bank extract",
                        })

            report(82, COMPACT_PROGRESS_PREFIX + self.ui_text(
                "正在写入 CSV 统计表和 XML→bank 映射表。",
                "Writing CSV statistics and XML→bank mapping tables.",
            ))
            report_path = out_root / "dev_all_station_soundname_search.csv"
            fields = ["xml_file", "station", "target_slot", "target_sound_name", "target_display_name", "candidate_rank", "candidate_bank", "candidate_sound_file", "candidate_frames", "candidate_samplerate", "candidate_duration_sec", "length_diff", "text_score", "confidence", "reason"]
            with report_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows_out)

            target_path = out_root / "target_all_station_soundnames.csv"
            with target_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["xml_file", "xml_path", "station", "slot_index", "sound_name", "display_name", "artist", "sample_length", "sample_rate"])
                writer.writeheader()
                writer.writerows(target_rows)

            summary_path = out_root / "dev_all_station_soundname_summary.csv"
            s_fields = ["station", "xml_entries", "matched_targets", "name_hit_targets", "length_candidate_targets", "no_candidate_targets"]
            with summary_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=s_fields)
                writer.writeheader()
                writer.writerows(station_summary.values())

            # Developer-facing XML -> bank mapping table.  This is a research aid,
            # not an automatic replacement source of truth.
            xml_map_path = out_root / "xml_to_bank_mapping.csv"
            map_fields = [
                "xml_file", "station", "slot_index", "sound_name", "display_name",
                "artist", "sample_length", "candidate_rank", "candidate_bank",
                "candidate_bank_role", "candidate_sound_file", "candidate_frames",
                "candidate_duration_sec", "length_diff", "text_score", "confidence", "note"
            ]
            target_by_key = {
                (str(t.get("xml_file", "")), str(t.get("station", "")), str(t.get("slot_index", "")), str(t.get("sound_name", ""))): t
                for t in target_rows
            }
            with xml_map_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=map_fields)
                writer.writeheader()
                for r in rows_out:
                    key = (str(r.get("xml_file", "")), str(r.get("station", "")), str(r.get("target_slot", "")), str(r.get("target_sound_name", "")))
                    t = target_by_key.get(key, {})
                    bank_key = str(r.get("candidate_bank", ""))
                    writer.writerow({
                        "xml_file": r.get("xml_file", ""),
                        "station": r.get("station", ""),
                        "slot_index": r.get("target_slot", ""),
                        "sound_name": r.get("target_sound_name", ""),
                        "display_name": r.get("target_display_name", ""),
                        "artist": t.get("artist", ""),
                        "sample_length": t.get("sample_length", ""),
                        "candidate_rank": r.get("candidate_rank", ""),
                        "candidate_bank": bank_key,
                        "candidate_bank_role": dev_bank_role_from_key(bank_key),
                        "candidate_sound_file": r.get("candidate_sound_file", ""),
                        "candidate_frames": r.get("candidate_frames", ""),
                        "candidate_duration_sec": r.get("candidate_duration_sec", ""),
                        "length_diff": r.get("length_diff", ""),
                        "text_score": r.get("text_score", ""),
                        "confidence": r.get("confidence", ""),
                        "note": r.get("reason", ""),
                    })

            # All-bank statistics table: includes banks that failed precheck, banks
            # that extracted but produced no wav/txt, and banks that produced audio.
            status_by_path: dict[str, dict[str, object]] = {}
            try:
                status_path = out_root / "bank_extract_status.csv"
                if status_path.exists():
                    with status_path.open("r", encoding="utf-8-sig", newline="") as f:
                        for row in csv.DictReader(f):
                            status_by_path[str(row.get("bank_path", "")).lower()] = dict(row)
            except Exception:
                status_by_path = {}
            sound_stats: dict[str, dict[str, object]] = {}
            for rec in records or []:
                key = self._dev_record_bank_key(rec)
                row = sound_stats.setdefault(key, {"sound_count": 0, "long_audio_count": 0, "min_duration_sec": 999999.0, "max_duration_sec": 0.0, "total_duration_sec": 0.0})
                dur = float(getattr(rec, "duration_sec", 0) or 0)
                row["sound_count"] = int(row["sound_count"]) + 1
                row["long_audio_count"] = int(row["long_audio_count"]) + (1 if dur >= 30.0 else 0)
                row["min_duration_sec"] = min(float(row["min_duration_sec"]), dur)
                row["max_duration_sec"] = max(float(row["max_duration_sec"]), dur)
                row["total_duration_sec"] = float(row["total_duration_sec"]) + dur
            precheck_by_path = {str(r.get("bank_path", "")).lower(): r for r in bank_precheck_rows}
            all_stats_path = out_root / "all_bank_extract_statistics.csv"
            stat_fields = [
                "bank_name", "bank_key", "bank_role", "bank_path", "size_mb",
                "precheck_has_fsb", "extract_status", "sound_count", "long_audio_count",
                "min_duration_sec", "max_duration_sec", "avg_duration_sec", "error"
            ]
            with all_stats_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=stat_fields)
                writer.writeheader()
                for b in all_banks:
                    path_key = str(b).lower()
                    pre = precheck_by_path.get(path_key, {})
                    status = status_by_path.get(path_key, {})
                    key = b.stem.lower()
                    stat = sound_stats.get(key, {})
                    count = int(stat.get("sound_count", 0) or 0)
                    total = float(stat.get("total_duration_sec", 0.0) or 0.0)
                    min_d = float(stat.get("min_duration_sec", 0.0) or 0.0) if count else 0.0
                    max_d = float(stat.get("max_duration_sec", 0.0) or 0.0) if count else 0.0
                    try:
                        size_mb = f"{(b.stat().st_size / 1024 / 1024):.3f}"
                    except Exception:
                        size_mb = str(pre.get("size_mb", ""))
                    writer.writerow({
                        "bank_name": b.name,
                        "bank_key": key,
                        "bank_role": dev_bank_role_from_key(key),
                        "bank_path": str(b),
                        "size_mb": size_mb,
                        "precheck_has_fsb": pre.get("precheck_has_fsb", ""),
                        "extract_status": status.get("status", "not_extractable_or_skipped"),
                        "sound_count": count,
                        "long_audio_count": int(stat.get("long_audio_count", 0) or 0),
                        "min_duration_sec": f"{min_d:.3f}",
                        "max_duration_sec": f"{max_d:.3f}",
                        "avg_duration_sec": f"{(total / count):.3f}" if count else "0.000",
                        "error": status.get("error", pre.get("precheck_error", "")),
                    })

            report(98, COMPACT_PROGRESS_PREFIX + self.ui_text(
                "报告生成完成，正在收尾。",
                "Reports generated; finishing up.",
            ))
            return str(report_path)

        def done(path):
            self.log(f"[DEV][OK] 全电台全 Bank 音频扫描完成：{path}")
            self.info_box(
                "全电台扫描完成",
                f"已生成报告：\n{path}\n\n同目录还包含 target_all_station_soundnames.csv、fmod_all_bank_audio_inventory.csv、bank_extract_status.csv、all_bank_extract_statistics.csv、xml_to_bank_mapping.csv、music_bank_inventory.csv、missing_track_candidate_shortlist.csv 和 dev_all_station_soundname_summary.csv。",
                "All-station scan finished",
                f"Report generated:\n{path}\n\nThe same folder also contains target_all_station_soundnames.csv, fmod_all_bank_audio_inventory.csv, bank_extract_status.csv, all_bank_extract_statistics.csv, xml_to_bank_mapping.csv, music_bank_inventory.csv, missing_track_candidate_shortlist.csv, and dev_all_station_soundname_summary.csv.",
            )

        self.run_background_task("开发测试：全电台全 Bank 音频扫描", job, done, estimated="首次会分批 Extract 所有可提取 bank，之后会复用缓存。仅用于开发诊断。")

    def dev_scan_menu_music_banks(self):
        """Temporary developer diagnostic: scan likely menu/frontend/title music banks."""
        try:
            bank_root = self._current_bank_root()
            tool = self._current_fmod_tool_path()
        except Exception as exc:
            self.show_error("开发测试前置检查失败", exc)
            return
        msg = "将扫描 menu/frontend/title/boot/loading/festival 等疑似主菜单或前端音乐 bank。候选 bank 会一次性 Extract 并缓存，不会写 XML，不会 Rebuild，不会覆盖游戏。"
        if not self.confirm_box("确认扫描菜单音乐 Bank", msg, "Confirm menu music bank scan", msg):
            return
        keywords = ["menu", "frontend", "front", "ui", "shell", "title", "boot", "intro", "loading", "festival", "music"]

        def job(report):
            out_root = project_work_dir() / "dev_menu_music_bank_scan"
            if out_root.exists():
                shutil.rmtree(out_root, ignore_errors=True)
            out_root.mkdir(parents=True, exist_ok=True)
            try:
                banks = sorted(Path(bank_root).rglob("*.bank"), key=lambda p: p.as_posix().lower())
            except Exception:
                banks = []
            candidates = [p for p in banks if any(k in p.name.lower() for k in keywords)]
            extractable_candidates = [p for p in candidates if bank_contains_fsb_audio(p)]
            rows: list[dict[str, object]] = []
            ok, extract_dir, extract_message = self._dev_extract_bank_set_cached(tool, extractable_candidates, "menu_music_candidate_banks", report, "menu/frontend candidate banks")
            records_by_key: dict[str, list[object]] = {}
            if ok and extract_dir is not None:
                report(90, "[DEV][MENU] 正在解析菜单候选 bank 的一次性 Extract 输出。")
                for rec in parse_extract_template(extract_dir):
                    records_by_key.setdefault(self._dev_record_bank_key(rec), []).append(rec)
            total = max(1, len(candidates))
            for i, bank in enumerate(candidates, start=1):
                report(90 + int(9 * i / total), f"[DEV][MENU] 汇总: {bank.name}")
                preflight = bank_preflight_message(bank)
                extractable = bank_contains_fsb_audio(bank)
                key = bank.stem.lower()
                records = records_by_key.get(key, [])
                sound_count = len(records)
                durations = [float(getattr(r, "duration_sec", 0) or 0) for r in records]
                long_audio_count = sum(1 for d in durations if d >= 20.0)
                avg_duration = sum(durations) / len(durations) if durations else 0.0
                message = extract_message if extractable else preflight
                rows.append({
                    "bank_name": bank.name,
                    "bank_path": str(bank),
                    "extractable": int(bool(extractable)),
                    "sound_count": sound_count,
                    "long_audio_count": long_audio_count,
                    "avg_duration_sec": f"{avg_duration:.3f}",
                    "reason": ",".join(k for k in keywords if k in bank.name.lower()),
                    "message": message,
                })
            report_path = out_root / "dev_menu_music_bank_candidates.csv"
            fields = ["bank_name", "bank_path", "extractable", "sound_count", "long_audio_count", "avg_duration_sec", "reason", "message"]
            with report_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            return str(report_path)

        def done(path):
            self.log(f"[DEV][OK] 菜单/前端音乐 bank 扫描完成：{path}")
            self.info_box("扫描完成", f"已生成报告：\n{path}", "Scan finished", f"Report generated:\n{path}")

        self.run_background_task("开发测试：扫描菜单音乐 Bank", job, done, estimated="首次会一次性 Extract 候选 bank，之后会复用缓存。仅用于开发诊断。")

    def generate_bank_tool_plan(self):
        if not self.current_xml:
            self.warn_box("缺少 XML", "请先选择或扫描 RadioInfo XML。", "Missing XML", "Please select or scan the RadioInfo XML first.")
            return
        station = self.current_station_name()
        if not station:
            self.warn_box("缺少电台", "请先选择目标电台。", "Missing radio station", "Please choose a target radio station first.")
            return
        try:
            bank_root = self._current_bank_root()
        except Exception as exc:
            self.show_error("自动定位 Bank 目录失败", exc)
            return
        fmod_tool = self.txt_path(self.fmod_tool_edit)
        self.store.set_setting("fmod_tool", str(fmod_tool) if fmod_tool else "")
        try:
            extract_template = project_work_dir() / FMOD_EXTRACT_TEMPLATE_DIR_NAME
            txts = sorted(extract_template.rglob("*.txt")) if extract_template.exists() else []
            bank_txt = txts[0] if txts else None
            music_dir = project_output_dir() / FMOD_READY_WAV_DIR_NAME
            if not music_dir.exists():
                music_dir = project_work_dir() / "v2_prepared_audio"
            out_dir = project_output_dir() / "bank_tool_plan"
            plan = make_bank_plan(
                xml_path=self.current_xml,
                station_name=station,
                bank_root=bank_root,
                music_dir=music_dir,
                out_dir=out_dir,
                bank_txt_path=bank_txt,
                tool_path=fmod_tool,
            )
            json_path, txt_path, bat_path = write_bank_plan_outputs(plan, out_dir)
            self.log(f"[BANK] 已生成外部 Fmod Bank Tools 接入预览：\n{txt_path}\n{bat_path}")
            if plan.errors:
                self.log("[BANK][WARN] 预览仍有错误，请打开 bank_plan.txt 检查。")
            self.info_box("Bank 命令预览已生成", f"已生成：\n{txt_path}\n{bat_path}", "Bank command preview generated", f"Generated:\n{txt_path}\n{bat_path}")
        except Exception as exc:
            self.show_error("生成 Bank 工具预览失败", exc)

    def import_extract_template(self):
        src = self.txt_path(self.extract_dir_edit)
        if not src:
            return
        target = project_work_dir() / FMOD_EXTRACT_TEMPLATE_DIR_NAME

        def job(report):
            report(5, f"[FMOD] 正在导入 Extract 模板: {src}")
            result = import_fmod_extract_folder(src, target)
            report(100, "Extract 模板导入完成。")
            return result

        def done(result):
            self.extract_dir_edit.setText(str(result))
            self.log(f"[OK] 已导入 Fmod Extract 模板: {result}")

        self.run_background_task("导入 Fmod Extract 模板", job, done, estimated="取决于 wav 数量和磁盘速度，通常数秒到数十秒。")

    def _prepare_audio_for_generation(self, source: Path, slot_index: int, dst_dir: Path) -> tuple[AudioInfo, AudioNormalizationReport]:
        dst_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_stem(f"slot_{slot_index:02d}_{source.stem}", 90)
        dst = dst_dir / f"{stem}.wav"
        ffmpeg = find_ffmpeg(None)
        normalize_report = run_ffmpeg_normalize(source, dst, ffmpeg)
        info = read_wav_info(dst)
        audio = AudioInfo(
            path=dst,
            filename=dst.name,
            samplerate=info.samplerate,
            channels=info.channels,
            bits_per_sample=info.bits_per_sample,
            frames=info.frames,
            duration_sec=info.duration_sec,
        )
        return audio, normalize_report

    def _filter_xml_only_unmatched_rows(self, rows, assigned_slots, extract_template: Path, report=None, station: str | None = None):
        """Skip XML rows that have no matching FMOD audio in the extracted bank.

        Some stations expose extra XML rows such as *_ID variants or regional
        metadata-only entries while the actual FMOD bank contains fewer playable
        sound slots.  Replacing those rows would only change XML names and could
        not replace audio.  Treat trailing unmatched rows as XML-only and remove
        them from the current replacement run, while keeping a clear diagnostic
        warning.
        """
        assigned = {int(x) for x in assigned_slots}
        records = parse_extract_template(extract_template) if extract_template and Path(extract_template).exists() else []
        record_count = len(records)
        profile = self._station_slot_profile(station, len(rows)) if station else None
        disabled_slots = {int(x) for x in (profile or {}).get("non_replaceable_slots", [])}
        if not records and not disabled_slots:
            return list(rows), assigned, []

        filtered = []
        skipped = []
        for row in rows:
            slot = int(row.slot_index)
            is_selected = slot in assigned
            unmatched = bool(row.bank_index < 0 or not row.original_wav_relpath or (row.match_method or "") in {"unmatched", "length_no_match", "length_missing_xml"})
            looks_profile_disabled = is_selected and slot in disabled_slots
            looks_xml_only = is_selected and unmatched and records and slot >= record_count
            if looks_profile_disabled or looks_xml_only:
                assigned.discard(slot)
                skipped.append(row)
                reason = "station_slot_profile_disabled" if looks_profile_disabled else "auto_skipped_xml_only_no_fmod_audio"
                note = (row.notes + " | " if row.notes else "") + reason
                row = replace(
                    row,
                    audio_filename="",
                    display_name=row.original_display_name or "",
                    artist=row.original_artist or "",
                    notes=note,
                )
            filtered.append(row)

        if skipped and report:
            preview = ", ".join(f"slot {r.slot_index}" for r in skipped[:8])
            more = "..." if len(skipped) > 8 else ""
            report(50, f"[WARN] 检测到 {len(skipped)} 个仅 XML/无独立 FMOD 音频槽位，已从本次替换中跳过：{preview}{more}。")
        return filtered, assigned, skipped

    def _mapping_rows_from_track_rows(self, rows):
        return [
            {
                "slot_index": row.slot_index,
                "target_wav": row.original_wav_relpath,
                "audio_filename": row.audio_filename,
                "display_name": row.display_name,
                "artist": row.artist,
            }
            for row in rows
            if row.audio_filename
        ]

    def _build_v2_outputs_sync(self, current_xml: Path, station: str, assignments: dict[int, str], db_path: Path, report, *, backup_label: str = "v2_xml"):
        """Build patched XML and fmod_ready_wav in a worker thread.

        This mirrors generate_v2_outputs(), but is factored so the one-click
        pipeline can run Extract -> build -> Rebuild -> deploy without returning
        to the GUI between each stage.
        """
        store = StateStore(db_path)
        out, bak, work = ensure_project_dirs()
        report(5, "[V2] 创建 XML 备份快照。")
        snapshot = create_backup_snapshot([current_xml], bak, label=backup_label)

        report(15, "[V2] 准备已分配音频；非 WAV 会调用 ffmpeg 转码。")
        prepared_dir = work / "v2_prepared_audio"
        if prepared_dir.exists():
            shutil.rmtree(prepared_dir, ignore_errors=True)
        prepared_dir.mkdir(parents=True, exist_ok=True)

        profiles_by_key = {p.track_key: p for p in store.list_track_profiles()}
        audio_by_filename: dict[str, AudioInfo] = {}
        markers_by_filename: dict[str, SegmentMarkers] = {}
        marker_json_by_filename: dict[str, dict[str, int]] = {}
        raw_marker_json_by_filename: dict[str, dict[str, int]] = {}
        marker_source_info_by_filename: dict[str, AudioInfo | None] = {}
        slot_to_profile_and_audio: dict[int, tuple[TrackProfile, AudioInfo]] = {}

        total = max(1, len(assignments))
        for idx, (slot, key) in enumerate(sorted(assignments.items()), start=1):
            profile = profiles_by_key.get(key)
            if not profile:
                raise ValueError(f"slot {slot} 的 profile 丢失: {key}")
            report(15 + int(25 * idx / total), f"[V2] 准备音频 {idx}/{total}: slot {slot} <- {Path(profile.source_path).name}")
            info, normalize_report = self._prepare_audio_for_generation(Path(profile.source_path), slot, prepared_dir)
            audio_by_filename[info.filename] = info
            for line in describe_audio_normalization_report(normalize_report):
                report(15 + int(25 * idx / total), f"[AUDIO] slot {slot}: {line}")
            marker_data = dict(profile.markers or {})
            marker_data.setdefault("TrackStart", 0)
            marker_data.setdefault("TrackDrop", 0)
            marker_data.setdefault("PostDrop", 0)
            marker_data.setdefault("TrackLoopStart", 0)
            marker_data.setdefault("TrackLoopEnd", -1)
            marker_data.setdefault("PostRaceLoopStart", 0)
            marker_data.setdefault("PostRaceLoopEnd", -1)
            marker_data.setdefault("DJSegment", -1)
            marker_data.setdefault("StingerStart", -1)
            marker_data.setdefault("DJStart", -1)
            source_marker_info = marker_source_info_for_profile(profile, normalize_report)
            marker_source_info_by_filename[info.filename] = source_marker_info
            raw_marker_json_by_filename[info.filename] = dict(marker_data)
            normalized_markers = normalize_track_markers_for_prepared_audio(
                marker_data,
                source_marker_info,
                info,
                source_sample_length=profile.sample_length or None,
                source_sample_rate=profile.sample_rate or None,
                marker_unit="samples",
                label=f"slot {slot}/{info.filename}",
            )
            marker_json_by_filename[info.filename] = markers_to_json(normalized_markers.markers)
            markers_by_filename[info.filename] = normalized_markers.markers
            for line in normalized_markers.log_lines:
                report(15 + int(25 * idx / total), line)
            slot_to_profile_and_audio[slot] = (profile, info)

        report(45, "[V2] 构造精确 slot 映射。")
        track_order_path = work / TRACK_ORDER_FILE_NAME
        extract_template = work / FMOD_EXTRACT_TEMPLATE_DIR_NAME
        ensure_track_order_file(track_order_path, current_xml, station, [], None, extract_template if extract_template.exists() else None)
        rows = read_track_order(track_order_path)
        patched_rows = []
        mapping_rows = []
        for row in rows:
            # v2.7.7: clear stale replacement metadata from previous runs/stations.
            # Keep FMOD Extract mapping fields, but apply only the current selected
            # assignments to XML and rebuild WAVs. This prevents in-game custom
            # titles from being shifted or reused from an earlier run.
            base_notes = row.notes or ""
            row = replace(
                row,
                audio_filename="",
                display_name=row.original_display_name or "",
                artist=row.original_artist or "",
                notes=base_notes,
            )
            if row.slot_index in slot_to_profile_and_audio:
                profile, info = slot_to_profile_and_audio[row.slot_index]
                display = profile.display_name or Path(profile.filename).stem
                artist = profile.artist or "User"
                row = replace(
                    row,
                    audio_filename=info.filename,
                    display_name=display,
                    artist=artist,
                    notes=(base_notes + " | " if base_notes else "") + "v2_manual_assignment_current_run",
                )
                mapping_rows.append({
                    "slot_index": row.slot_index,
                    "target_wav": row.original_wav_relpath,
                    "audio_filename": info.filename,
                    "display_name": display,
                    "artist": artist,
                })
            patched_rows.append(row)
        patched_rows, effective_assigned_slots, skipped_xml_only_rows = self._filter_xml_only_unmatched_rows(
            patched_rows, assignments.keys(), extract_template, report, station=station
        )
        if assignments and not effective_assigned_slots:
            raise ValueError(
                "当前选择的槽位都属于仅 XML/无独立 FMOD 音频槽位，不能安全替换。"
                "请改选列表中状态为可替换的音频槽位。"
            )
        mapping_rows = self._mapping_rows_from_track_rows(patched_rows)
        write_track_order(track_order_path, patched_rows)
        try:
            mapping_report = work / "current_assignment_mapping.csv"
            import csv as _csv
            with mapping_report.open("w", encoding="utf-8-sig", newline="") as _f:
                _writer = _csv.DictWriter(_f, fieldnames=["slot_index", "target_wav", "audio_filename", "display_name", "artist"])
                _writer.writeheader()
                _writer.writerows(mapping_rows)
        except Exception:
            pass

        report(50, "[V2] 校验 XML 槽位与 FMOD 音频映射。")
        inventory_report = work / "fmod_sound_inventory.csv"
        replacement_plan = work / "replacement_plan.csv"
        validation_report = work / "replacement_validation.txt"
        try:
            write_fmod_sound_inventory(inventory_report, extract_template if extract_template.exists() else None)
            write_replacement_plan(replacement_plan, patched_rows, audio_by_filename, effective_assigned_slots, extract_template if extract_template.exists() else None)
            # Bank candidate / extract-template sanity report.  This catches the
            # common Disk/Stinger/DJ-bank mistake before users get a package
            # where XML names are correct but no playable song audio changed.
            bank_report = work / "bank_candidate_report.csv"
            records = parse_extract_template(extract_template) if extract_template.exists() else []
            probable_count = sum(1 for r in records if r.duration_sec >= 20.0)
            assigned_count = len(effective_assigned_slots)
            with bank_report.open("w", encoding="utf-8-sig", newline="") as _f:
                _fields = ["template_dir", "record_count", "probable_song_count", "xml_track_count", "assigned_count", "risk", "reason"]
                _writer = csv.DictWriter(_f, fieldnames=_fields)
                risk = "ok"
                reason = ""
                if records and probable_count < max(1, min(len(patched_rows), assigned_count)):
                    risk = "high"
                    reason = "probable song count is lower than selected replacements; this may be a Disk/Stinger/DJ bank"
                elif records and probable_count < max(1, len(patched_rows) // 2):
                    risk = "medium"
                    reason = "probable song count is much lower than XML track count"
                _writer.writeheader()
                _writer.writerow({
                    "template_dir": str(extract_template),
                    "record_count": len(records),
                    "probable_song_count": probable_count,
                    "xml_track_count": len(patched_rows),
                    "assigned_count": assigned_count,
                    "risk": risk,
                    "reason": reason,
                })
                if risk == "high":
                    raise ValueError(
                        "当前 Extract 模板中可疑歌曲数量少于本次要替换的槽位数量，可能选到了 Disk/Stinger/DJ bank。"
                        f"已停止生成，详情见：{bank_report}"
                    )
        except Exception as exc:
            report(50, f"[WARN] 写入替换诊断报告失败: {exc}")
            if isinstance(exc, ValueError):
                raise
        selected_errors = validate_selected_replacements(
            patched_rows, audio_by_filename, effective_assigned_slots, extract_template if extract_template.exists() else None
        )
        fatal_errors = [e for e in selected_errors if not str(e).startswith("WARN:")]
        validation_report.write_text("\n".join(selected_errors) + ("\n" if selected_errors else "OK\n"), encoding="utf-8")
        warn_count = len(selected_errors) - len(fatal_errors)
        if warn_count:
            report(51, f"[WARN] 有 {warn_count} 个低置信度 FMOD 映射，详情见 work/replacement_plan.csv。")
        if fatal_errors:
            short = "\n".join(fatal_errors[:8])
            raise ValueError(
                "替换计划校验失败：你选择的部分歌曲槽位没有成功匹配到 FMOD 音频。\n"
                "为避免出现‘游戏里显示新歌名但实际仍播放原曲’，本次生成已停止，未写入 XML，也不会 Rebuild。\n\n"
                f"前几项错误：\n{short}\n\n"
                f"完整诊断：{replacement_plan}\n{inventory_report}\n{validation_report}"
            )

        fmod_ready = out / FMOD_READY_WAV_DIR_NAME
        if extract_template.exists():
            report(55, "[V2] 生成 fmod_ready_wav 并匹配音量；这一步可能耗时较长。")
            create_fmod_rebuild_workspace(out, extract_template, patched_rows, audio_by_filename, progress_callback=report)
            fmod_ready = out / FMOD_READY_WAV_DIR_NAME
        else:
            raise FileNotFoundError("未找到 Extract 模板；一键替换需要先成功 Extract 并导入模板。")

        report(86, "[V2] 复核最终 WAV SampleLength 并写入 XML。")
        final_audio_by_filename: dict[str, AudioInfo] = dict(audio_by_filename)
        final_markers_by_filename: dict[str, SegmentMarkers] = dict(markers_by_filename)
        sample_report_rows: list[dict[str, object]] = []
        for row in patched_rows:
            if not row.audio_filename or row.slot_index not in effective_assigned_slots:
                continue
            ready_path = fmod_ready / row.original_wav_relpath if row.original_wav_relpath else None
            prepared_info = audio_by_filename.get(row.audio_filename)
            marker_source_info = marker_source_info_by_filename.get(row.audio_filename)
            if ready_path and ready_path.exists():
                ready_info = read_wav_info(ready_path)
                final_info = AudioInfo(
                    path=ready_info.path,
                    filename=row.audio_filename,
                    samplerate=ready_info.samplerate,
                    channels=ready_info.channels,
                    bits_per_sample=ready_info.bits_per_sample,
                    frames=ready_info.frames,
                    duration_sec=ready_info.duration_sec,
                )
                final_audio_by_filename[row.audio_filename] = final_info
                marker_data = dict(raw_marker_json_by_filename.get(row.audio_filename, marker_json_by_filename.get(row.audio_filename, {})))
                final_marker_result = normalize_track_markers_for_prepared_audio(
                    marker_data,
                    marker_source_info,
                    final_info,
                    source_sample_length=(marker_source_info.sample_length if marker_source_info else None),
                    source_sample_rate=(marker_source_info.samplerate if marker_source_info else None),
                    marker_unit="samples",
                    label=f"slot {row.slot_index}/{row.audio_filename}",
                )
                final_markers_by_filename[row.audio_filename] = final_marker_result.markers
                for line in final_marker_result.log_lines:
                    report(86, line)
                sample_report_rows.append({
                    "slot_index": row.slot_index,
                    "audio_filename": row.audio_filename,
                    "target_wav": row.original_wav_relpath,
                    "source_sample_length": "" if marker_source_info is None else marker_source_info.sample_length,
                    "prepared_wav_sample_length": "" if prepared_info is None else prepared_info.sample_length,
                    "final_wav_sample_length": final_info.sample_length,
                    "xml_sample_length": final_info.sample_length,
                    "end_marker": max(0, final_info.sample_length - 1),
                    "marker_scale": f"{final_marker_result.scale:.9f}",
                    "diff_source_vs_prepared": "" if marker_source_info is None else final_info.sample_length - marker_source_info.sample_length,
                    "marker_warnings": " | ".join(final_marker_result.warnings),
                    "status": "ok",
                })
            else:
                sample_report_rows.append({
                    "slot_index": row.slot_index,
                    "audio_filename": row.audio_filename,
                    "target_wav": row.original_wav_relpath,
                    "source_sample_length": "" if marker_source_info is None else marker_source_info.sample_length,
                    "prepared_wav_sample_length": "",
                    "final_wav_sample_length": "",
                    "xml_sample_length": "",
                    "end_marker": "",
                    "marker_scale": "",
                    "diff_source_vs_prepared": "",
                    "marker_warnings": "",
                    "status": "missing_final_wav",
                })
                raise FileNotFoundError(f"最终 Rebuild WAV 不存在：{ready_path}")

        final_report = work / "final_wav_samplelength_report.csv"
        with final_report.open("w", encoding="utf-8-sig", newline="") as _f:
            _fields = [
                "slot_index", "audio_filename", "target_wav",
                "source_sample_length", "prepared_wav_sample_length", "final_wav_sample_length",
                "xml_sample_length", "end_marker", "marker_scale",
                "diff_source_vs_prepared", "marker_warnings", "status",
            ]
            _writer = csv.DictWriter(_f, fieldnames=_fields)
            _writer.writeheader()
            _writer.writerows(sample_report_rows)

        output_xml = out / current_xml.name
        xml_sync_report: list[str] = []
        _patch_xml_by_track_order(
            current_xml,
            station,
            output_xml,
            patched_rows,
            final_audio_by_filename,
            final_markers_by_filename,
            xml_sync_report=xml_sync_report,
        )
        xml_sync_report_path = work / "xml_marker_sync_validation.txt"
        xml_sync_report_path.write_text("\n".join(xml_sync_report) + ("\n" if xml_sync_report else "OK\n"), encoding="utf-8")
        report(87, f"[XML] Marker/SampleLength node sync entries: {len(xml_sync_report)}; report: {xml_sync_report_path}")
        self._generate_patched_radioinfo_siblings(current_xml, station, patched_rows, final_audio_by_filename, final_markers_by_filename, out, report)

        metadata_validation = work / "xml_metadata_validation.csv"
        validation_rows: list[dict[str, object]] = []
        validation_errors: list[str] = []
        tree_check = parse_xml(output_xml)
        samples_check = get_track_samples(find_station(tree_check, station))
        for row in patched_rows:
            if row.slot_index not in effective_assigned_slots:
                continue
            sample = samples_check[row.slot_index]
            expected_audio = final_audio_by_filename.get(row.audio_filename)
            expected_title = row.display_name or Path(row.audio_filename).stem
            expected_artist = row.artist or "User"
            expected_len = "" if expected_audio is None else str(expected_audio.sample_length)
            checks = {
                "display_ok": sample.get("DisplayName", "") == expected_title,
                "artist_ok": sample.get("Artist", "") == expected_artist,
                "sample_length_ok": sample.get("SampleLength", "") == expected_len,
                "end_ok": sample.get("End", "") == str(max(0, int(expected_len or 1) - 1)),
            }
            status = "ok" if all(checks.values()) else "error"
            if status != "ok":
                validation_errors.append(f"slot {row.slot_index}: XML metadata validation failed")
            validation_rows.append({
                "slot_index": row.slot_index,
                "expected_title": expected_title,
                "actual_title": sample.get("DisplayName", ""),
                "expected_artist": expected_artist,
                "actual_artist": sample.get("Artist", ""),
                "expected_sample_length": expected_len,
                "actual_sample_length": sample.get("SampleLength", ""),
                "expected_end": str(max(0, int(expected_len or 1) - 1)),
                "actual_end": sample.get("End", ""),
                "status": status,
            })
        with metadata_validation.open("w", encoding="utf-8-sig", newline="") as _f:
            _fields = ["slot_index", "expected_title", "actual_title", "expected_artist", "actual_artist", "expected_sample_length", "actual_sample_length", "expected_end", "actual_end", "status"]
            _writer = csv.DictWriter(_f, fieldnames=_fields)
            _writer.writeheader()
            _writer.writerows(validation_rows)
        if validation_errors:
            raise ValueError("XML 显示名 / Artist / SampleLength 写入复核失败，已停止生成。详情见：" + str(metadata_validation))

        manifest_path = store.export_manifest(work / "v2_project_manifest.json")
        summary = {
            "version": APP_VERSION,
            "station": station,
            "xml": str(output_xml),
            "assignedSlots": sorted(effective_assigned_slots),
            "trackOrder": str(track_order_path),
            "backup": snapshot.manifest_path,
            "fmodReadyWav": str(fmod_ready),
            "manifest": str(manifest_path),
        }
        (out / "v2_generation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        report(82, f"[OK] v2 输出完成: {out}")
        return out, snapshot.manifest_path, fmod_ready, output_xml

    def _resolve_game_bank_paths_by_names(self, bank_root: Path, bank_names: list[str]) -> list[Path]:
        """Resolve bank filenames back to their original game paths under FMODBanks."""
        resolved: list[Path] = []
        missing: list[str] = []
        root = Path(bank_root)
        for name in bank_names:
            direct = root / name
            if direct.exists():
                resolved.append(direct)
                continue
            matches = list(root.rglob(name))
            if matches:
                resolved.append(matches[0])
            else:
                missing.append(name)
        if missing:
            raise FileNotFoundError("无法在游戏 FMODBanks 中找到准备 Extract 的音频 bank：" + ", ".join(missing))
        return resolved

    def _modified_bank_names_from_replacement_plan(self, plan_path: Path) -> list[str]:
        """Return bank filenames that actually receive replacement WAVs.

        Cross-bank workflows may Extract multiple banks only to build a mapping,
        while the current assignment changes just one of them.  Fmod Bank Tools
        may then rebuild only the modified bank.  Deploy and Rebuild completion
        checks must therefore expect modified banks, not every bank used for
        Extract.
        """
        plan_path = Path(plan_path)
        if not plan_path.exists():
            return []
        names: list[str] = []
        try:
            with plan_path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    if str(row.get("selected", "")).strip().lower() not in {"true", "1", "yes", "y"}:
                        continue
                    if str(row.get("status", "")).strip().lower() not in {"ok", ""}:
                        continue
                    if not str(row.get("audio_filename") or "").strip():
                        continue
                    rel = str(row.get("resolved_wav_relpath") or row.get("target_wav") or "").strip()
                    if not rel:
                        continue
                    first = rel.replace("\\", "/").split("/")[0]
                    if not first:
                        continue
                    # Extract folders use names like R1_Tracks_CU1.assets[0].
                    if "[" in first:
                        first = first.split("[", 1)[0]
                    if not first.lower().endswith(".bank"):
                        first = first + ".bank"
                    if first not in names:
                        names.append(first)
        except Exception as exc:
            self.log(f"[WARN] 无法读取实际修改 bank 列表: {plan_path}: {exc}")
        return names

    def _bank_stem_from_rebuild_name(self, bank_name: str) -> str:
        text = str(bank_name or "")
        low = text.lower()
        if low.endswith(".assets.bank"):
            return text[:-5]  # keep .assets
        if low.endswith(".bank"):
            return text[:-5]
        return text

    def _subset_fmod_ready_workspace_for_bank(self, fmod_ready: Path, bank_name: str, subset_root: Path) -> Path:
        """Create a one-bank wav workspace for safer sequential Rebuild.

        Fmod Bank Tools accepts a WavDir containing extracted bank folders such
        as R1_Tracks_CU1.assets[0].  For multi-bank stations, rebuilding all
        banks in one GUI run makes it harder to know which bank failed or was
        actually emitted.  This helper copies only the folders belonging to one
        target bank into a temporary WavDir so Rebuild is performed bank-by-bank.
        """
        fmod_ready = Path(fmod_ready)
        subset_root = Path(subset_root)
        if subset_root.exists():
            shutil.rmtree(subset_root, ignore_errors=True)
        subset_root.mkdir(parents=True, exist_ok=True)
        stem = self._bank_stem_from_rebuild_name(bank_name).lower()
        copied = 0
        for child in sorted(fmod_ready.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            cname = child.name.lower()
            base = cname.split("[", 1)[0]
            if base == stem or base.startswith(stem + "["):
                shutil.copytree(child, subset_root / child.name)
                copied += 1
        if copied == 0:
            raise FileNotFoundError(f"无法为 {bank_name} 创建单 bank Rebuild 工作区；在 {fmod_ready} 中找不到对应 wav 目录。")
        return subset_root

    def _rebuild_modified_banks_one_by_one(self, tool: Path, fmod_ready: Path, modified_bank_names: list[str], original_bank_paths: list[Path], fmod_dir: Path, out: Path, report, *, auto_click: bool, phase_label: str) -> Path:
        """Rebuild modified banks sequentially and collect outputs.

        This follows the safer user-requested order: each physical bank is
        rebuilt in isolation.  It avoids a multi-bank Rebuild silently skipping a
        bank or mixing CU/Disk outputs, and the log clearly shows which bank is
        being processed.
        """
        rebuilt_out = out / "fmod_rebuilt_banks"
        if rebuilt_out.exists():
            shutil.rmtree(rebuilt_out, ignore_errors=True)
        rebuilt_out.mkdir(parents=True, exist_ok=True)
        game_by_name = {Path(p).name: Path(p) for p in original_bank_paths}
        if not modified_bank_names:
            raise RuntimeError("没有需要 Rebuild 的 bank。")
        for idx, bank_name in enumerate(modified_bank_names, start=1):
            if bank_name not in game_by_name:
                raise FileNotFoundError(f"无法在原游戏 bank 列表中找到 {bank_name}，已停止。")
            report(84 + int(8 * (idx - 1) / max(1, len(modified_bank_names))), f"[{phase_label}] Rebuild {idx}/{len(modified_bank_names)}: {bank_name}")
            layout = layout_from_exe(tool)
            copy_banks_to_tool_bank_dir([game_by_name[bank_name]], layout, clean_bank_dir=True)
            subset = self._subset_fmod_ready_workspace_for_bank(fmod_ready, bank_name, fmod_dir / f"rebuild_wav_{idx:02d}_{safe_stem(bank_name, 80)}")
            rebuild_manifest = fmod_dir / f"{phase_label.lower()}_rebuild_{idx:02d}.json"
            prep_rebuild = prepare_rebuild_job(tool, subset, rebuild_manifest, cpu_threads=self._fmod_cpu_threads())
            if not prep_rebuild.layout:
                raise RuntimeError(f"Fmod Rebuild 工作目录准备失败：{bank_name}")
            rebuild_res = launch_trigger_and_wait(
                tool,
                "rebuild",
                auto_trigger=auto_click,
                expected_bank_names=[bank_name],
                timeout_sec=1200,
            )
            if not rebuild_res.ok:
                raise RuntimeError("Fmod Rebuild 未完成：" + bank_name + "。" + rebuild_res.message + "\n已停止覆盖游戏文件。")
            copied = fmod_collect_rebuilt_banks(prep_rebuild.layout, rebuilt_out, clean_output_dir=False)
            if not (rebuilt_out / bank_name).exists():
                raise FileNotFoundError(f"Rebuild 输出缺少 {bank_name}，已停止覆盖游戏文件。")
        return rebuilt_out

    def _generate_patched_radioinfo_siblings(self, current_xml: Path, station: str, patched_rows, audio_by_filename, markers_by_filename, out_dir: Path, report=None) -> list[Path]:
        """Patch all RadioInfo_*.xml siblings with the same current assignment.

        Users often select one UI language XML while the game is actually using
        another RadioInfo_*.xml.  In that case bank audio is replaced correctly,
        but in-game titles remain original because the active language XML was
        never updated.  To make one-click replacement safer for normal players,
        write the same DisplayName/Artist/SampleLength/Marker update to every
        sibling RadioInfo_*.xml in the Audio directory.  The selected XML is
        still the source of the station/song structure; siblings that do not
        contain the station or fail to patch are skipped with a log warning.
        """
        current_xml = Path(current_xml)
        out_dir = Path(out_dir) / "patched_language_xmls"
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        patched: list[Path] = []
        try:
            siblings = sorted(current_xml.parent.glob("RadioInfo_*.xml"), key=lambda x: x.name.lower())
        except Exception:
            siblings = [current_xml]
        if current_xml not in siblings:
            siblings.append(current_xml)
        for xml_path in siblings:
            try:
                dst = out_dir / xml_path.name
                _patch_xml_by_track_order(xml_path, station, dst, patched_rows, audio_by_filename, markers_by_filename)
                patched.append(dst)
            except Exception as exc:
                if report:
                    report(87, f"[XML][WARN] 跳过语言 XML {xml_path.name}: {exc}")
        if report and patched:
            report(88, "[XML] 已同步写入语言 XML: " + ", ".join(p.name for p in patched))
        return patched

    def _deploy_rebuilt_outputs_to_game(self, rebuilt_bank_dir: Path, original_bank_paths: list[Path], output_xml: Path, original_xml: Path, report) -> dict:
        """Backup original game files, then copy patched XML and rebuilt banks over them."""
        rebuilt_bank_dir = Path(rebuilt_bank_dir)
        output_xml = Path(output_xml)
        original_xml = Path(original_xml)
        if not output_xml.exists():
            raise FileNotFoundError(f"待部署 XML 不存在: {output_xml}")
        expected = {p.name: p for p in original_bank_paths}
        replacements: list[tuple[Path, Path]] = [(output_xml, original_xml)]
        # Deploy patched sibling RadioInfo_*.xml files as well, so the active
        # in-game language XML cannot remain stale while the bank changed.
        sibling_dir = output_xml.parent / "patched_language_xmls"
        if sibling_dir.exists():
            for patched_xml in sorted(sibling_dir.glob("RadioInfo_*.xml"), key=lambda p: p.name.lower()):
                target_xml = original_xml.parent / patched_xml.name
                if target_xml.exists() and target_xml != original_xml:
                    replacements.append((patched_xml, target_xml))
        missing: list[str] = []
        for bank_name, original in expected.items():
            rebuilt = rebuilt_bank_dir / bank_name
            if not rebuilt.exists():
                missing.append(bank_name)
            else:
                replacements.append((rebuilt, original))
        if missing:
            raise FileNotFoundError("Rebuild 输出缺少这些 bank，已停止覆盖游戏文件：" + ", ".join(missing))

        targets = [dst for _src, dst in replacements]
        game_root = self.txt_path(self.game_root_edit)
        report(93, "[BACKUP] 正在确保初始状态备份存在（仅首次复制这些 XML/bank 文件）。")
        initial_snapshot = ensure_initial_state_snapshot(
            targets, project_backup_dir(), game_root=game_root, label="initial_game_state"
        )
        report(94, "[BACKUP] 正在创建本次修改前备份点。")
        snapshot = create_backup_snapshot(targets, project_backup_dir(), label="before_one_click_replace")

        deployed = []
        for src, dst in replacements:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            deployed.append(str(dst))

        manifest = project_output_dir() / "one_click_replace_manifest.json"
        data = {
            "version": APP_VERSION,
            "createdAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "initialStateManifest": initial_snapshot.manifest_path,
            "backupManifest": snapshot.manifest_path,
            "rebuiltBankDir": str(rebuilt_bank_dir),
            "deployedFiles": deployed,
        }
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def _current_main_menu_bank_path(self) -> Path:
        """Resolve the known FH6 press-start/main-menu music bank automatically."""
        target_name = MAIN_MENU_PRESS_START_BANK
        try:
            bank_root = self._current_bank_root()
        except Exception as exc:
            raise ValueError(
                f"请先选择并扫描游戏根目录，以便自动定位 {target_name}。"
            ) from exc

        matches: list[Path] = []
        try:
            direct = Path(bank_root) / target_name
            if direct.exists() and direct.is_file():
                matches.append(direct)
            if not matches:
                target_lower = target_name.lower()
                for p in Path(bank_root).rglob("*.bank"):
                    if p.name.lower() == target_lower and p.is_file():
                        matches.append(p)
        except Exception as exc:
            raise FileNotFoundError(f"自动搜索主菜单音乐 bank 失败: {bank_root}") from exc

        if not matches:
            raise FileNotFoundError(
                f"未能在 FMODBanks 中找到主菜单音乐 bank：{target_name}\n"
                f"当前 Bank 目录：{bank_root}\n"
                "请确认游戏根目录选择正确，且游戏文件完整。"
            )

        selected = sorted(matches, key=lambda p: (len(p.parts), p.as_posix().lower()))[0]
        if hasattr(self, "main_menu_bank_edit"):
            self.main_menu_bank_edit.setText(str(selected))
        self.store.set_setting("main_menu_bank_path", str(selected))
        return selected

    def _current_main_menu_audio_path(self) -> Path:
        text = self.main_menu_audio_edit.text().strip() if hasattr(self, "main_menu_audio_edit") else ""
        if not text:
            raise ValueError("请先选择新的主菜单音乐音频文件。")
        p = Path(text)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"新的主菜单音乐不存在: {p}")
        self.store.set_setting("main_menu_audio_path", str(p))
        return p

    def _main_menu_mode(self) -> str:
        # v3.0.33: this bank is known to contain a single music entry, so the
        # player-facing workflow no longer exposes bank/mode choices.
        mode = "single"
        self.store.set_setting("main_menu_replace_mode", mode)
        return mode

    def _bank_name_from_extract_record(self, rec) -> str:
        first = str(getattr(rec, "original_relpath", "") or "").replace("\\", "/").split("/", 1)[0]
        if "[" in first:
            first = first.split("[", 1)[0]
        if first and not first.lower().endswith(".bank"):
            first += ".bank"
        return first

    def _resolve_menu_original_banks(self, selected_bank: Path, bank_names: list[str]) -> list[Path]:
        selected_bank = Path(selected_bank)
        names = [str(n) for n in bank_names if str(n).strip()]
        if not names:
            names = [selected_bank.name]
        roots: list[Path] = []
        try:
            roots.append(self._current_bank_root())
        except Exception:
            pass
        if selected_bank.parent.exists():
            roots.append(selected_bank.parent)
        # Deduplicate roots while preserving order.
        unique_roots: list[Path] = []
        seen_roots: set[str] = set()
        for r in roots:
            key = str(Path(r).resolve() if Path(r).exists() else Path(r)).lower()
            if key not in seen_roots:
                seen_roots.add(key)
                unique_roots.append(Path(r))

        resolved: list[Path] = []
        missing: list[str] = []
        for name in names:
            if selected_bank.name.lower() == name.lower() and selected_bank.exists():
                resolved.append(selected_bank)
                continue
            found = None
            for root in unique_roots:
                direct = root / name
                if direct.exists():
                    found = direct
                    break
                try:
                    matches = list(root.rglob(name))
                except Exception:
                    matches = []
                if matches:
                    found = sorted(matches, key=lambda p: p.as_posix().lower())[0]
                    break
            if found is None:
                missing.append(name)
            else:
                resolved.append(found)
        if missing:
            raise FileNotFoundError("无法定位主菜单音乐 Rebuild 对应的原始 bank：" + ", ".join(missing))
        return resolved

    def _copy_fmod_extract_output(self, src_dir: Path, dst_dir: Path) -> Path:
        src_dir = Path(src_dir)
        dst_dir = Path(dst_dir)
        if not src_dir.exists():
            raise FileNotFoundError(f"Fmod Extract 输出目录不存在: {src_dir}")
        if dst_dir.exists():
            shutil.rmtree(dst_dir, ignore_errors=True)
        shutil.copytree(src_dir, dst_dir)
        return dst_dir

    def _prepare_main_menu_rebuild_workspace(self, template_dir: Path, replacement_audio: Path, mode: str, report) -> tuple[Path, list[dict[str, object]], list[str]]:
        """Create a Fmod Rebuild wav workspace for the fixed press-start bank.

        v3.0.33 uses the known FH6 main-menu bank
        GLB_RadioPressStart.assets.bank.  The player only chooses the
        replacement audio.  The bank is expected to contain one music record; if
        a future game build exposes multiple records, we keep the operation safe
        by replacing the longest record and recording that choice in the plan.
        """
        template_dir = Path(template_dir)
        replacement_audio = Path(replacement_audio)
        records = [r for r in parse_extract_template(template_dir) if getattr(r, "original_relpath", "")]
        records = [r for r in records if (template_dir / r.original_relpath).exists()]
        if not records:
            raise RuntimeError("Fmod Extract 输出中没有找到可替换的 wav/txt 记录。请确认目标 bank 确实包含主菜单音乐音频。")

        long_records = [r for r in records if float(getattr(r, "duration_sec", 0) or 0) >= 20.0]
        if mode == "all_long":
            selected = long_records or [max(records, key=lambda r: (float(getattr(r, "duration_sec", 0) or 0), int(getattr(r, "frames", 0) or 0)))]
        elif mode == "single":
            if len(records) == 1:
                selected = records
            else:
                selected = [max(long_records or records, key=lambda r: (float(getattr(r, "duration_sec", 0) or 0), int(getattr(r, "frames", 0) or 0)))]
                report(45, f"[MENU][WARN] {MAIN_MENU_PRESS_START_BANK} 解析到 {len(records)} 条音频记录；已安全选择时长最长的一条。")
        else:
            selected = [max(long_records or records, key=lambda r: (float(getattr(r, "duration_sec", 0) or 0), int(getattr(r, "frames", 0) or 0)))]

        workspace = project_work_dir() / "main_menu_fmod_ready_wav"
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        shutil.copytree(template_dir, workspace)

        target_rows: list[dict[str, object]] = []
        modified_bank_names: list[str] = []
        for rec in selected:
            bank_name = self._bank_name_from_extract_record(rec)
            if bank_name and bank_name not in modified_bank_names:
                modified_bank_names.append(bank_name)
            target = workspace / rec.original_relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg = find_ffmpeg(None)
            normalize_report = run_ffmpeg_normalize(replacement_audio, target, ffmpeg)
            converted = True
            for line in describe_audio_normalization_report(normalize_report):
                report(45, f"[MENU][AUDIO] {line}")
            target_rows.append({
                "bank_name": bank_name,
                "txt_relpath": getattr(rec, "txt_relpath", ""),
                "subsound_index": int(getattr(rec, "subsound_index", -1) or -1),
                "original_relpath": getattr(rec, "original_relpath", ""),
                "original_extracted_name": getattr(rec, "extracted_name", ""),
                "original_duration_sec": f"{float(getattr(rec, 'duration_sec', 0) or 0):.3f}",
                "original_frames": int(getattr(rec, "frames", 0) or 0),
                "replacement_audio": str(replacement_audio),
                "converted_with_ffmpeg": int(converted),
            })

        plan_path = project_work_dir() / "main_menu_replacement_plan.csv"
        fields = ["bank_name", "txt_relpath", "subsound_index", "original_relpath", "original_extracted_name", "original_duration_sec", "original_frames", "replacement_audio", "converted_with_ffmpeg"]
        with plan_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(target_rows)
        report(55, "[MENU] 已生成主菜单音乐 Rebuild 工作区；替换目标: " + ", ".join(str(r["original_relpath"]) for r in target_rows))
        return workspace, target_rows, modified_bank_names

    def _deploy_main_menu_rebuilt_banks_to_game(self, rebuilt_bank_dir: Path, original_bank_paths: list[Path], report) -> dict:
        rebuilt_bank_dir = Path(rebuilt_bank_dir)
        replacements: list[tuple[Path, Path]] = []
        missing: list[str] = []
        for original in original_bank_paths:
            original = Path(original)
            rebuilt = rebuilt_bank_dir / original.name
            if not rebuilt.exists():
                missing.append(original.name)
            else:
                replacements.append((rebuilt, original))
        if missing:
            raise FileNotFoundError("主菜单音乐 Rebuild 输出缺少这些 bank，已停止覆盖游戏文件：" + ", ".join(missing))

        targets = [dst for _src, dst in replacements]
        game_root = self.txt_path(self.game_root_edit)
        report(90, "[MENU][BACKUP] 正在确保主菜单音乐 bank 初始状态备份存在。")
        initial_snapshot = ensure_initial_state_snapshot(
            targets, project_backup_dir(), game_root=game_root, label="initial_main_menu_music_state"
        )
        report(92, "[MENU][BACKUP] 正在创建本次主菜单音乐替换前备份点。")
        snapshot = create_backup_snapshot(targets, project_backup_dir(), label="before_main_menu_music_replace")

        deployed = []
        for src, dst in replacements:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            deployed.append(str(dst))

        manifest = project_output_dir() / "one_click_main_menu_music_replace_manifest.json"
        data = {
            "version": APP_VERSION,
            "createdAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "mode": "one_click_main_menu_music_replace",
            "initialStateManifest": initial_snapshot.manifest_path,
            "backupManifest": snapshot.manifest_path,
            "rebuiltBankDir": str(rebuilt_bank_dir),
            "deployedFiles": deployed,
        }
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def _run_main_menu_music_pipeline(self, *, deploy: bool):
        try:
            tool = self._current_fmod_tool_path()
            selected_bank = self._current_main_menu_bank_path()
            replacement_audio = self._current_main_menu_audio_path()
            mode = self._main_menu_mode()
            auto_click = self._fmod_auto_click_enabled()
        except Exception as exc:
            self.show_error("主菜单音乐替换前置检查失败", exc)
            return

        if not is_pywinauto_available():
            msg_zh, msg_en = self.automation_component_warning_text("主菜单音乐替换", "Main-menu music replacement")
            self.warn_box("需要自动控制组件", msg_zh, "Automation component required", msg_en)
            return

        mode_text = f"自动替换 {MAIN_MENU_PRESS_START_BANK} 内唯一音乐音频"
        if deploy:
            title_zh = "确认一键替换主菜单音乐"
            msg_zh = (
                "即将执行：Extract 主菜单 bank → 替换音频 → Rebuild → 备份 → 覆盖游戏 bank。\n\n"
                f"目标 bank：\n{selected_bank}\n\n新音频：\n{replacement_audio}\n\n方式：{mode_text}\n\n"
                "此功能不会修改 RadioInfo XML；会先备份原始 bank。如果 Rebuild 输出不完整，将不会覆盖游戏文件。是否继续？"
            )
            title_en = "Confirm main menu music replacement"
            msg_en = (
                "The tool will run: Extract main-menu bank → replace audio → Rebuild → backup → overwrite game bank.\n\n"
                f"Target bank:\n{selected_bank}\n\nNew audio:\n{replacement_audio}\n\nMode: {mode}\n\n"
                "RadioInfo XML will not be modified. Original bank files will be backed up first. If Rebuild output is incomplete, game files will not be overwritten. Continue?"
            )
        else:
            title_zh = "确认生成主菜单 Mod 包"
            msg_zh = (
                "即将执行：Extract 主菜单 bank → 替换音频 → Rebuild → 收集到 output。\n\n"
                f"目标 bank：\n{selected_bank}\n\n新音频：\n{replacement_audio}\n\n方式：{mode_text}\n\n"
                "此模式不会覆盖游戏文件。是否继续？"
            )
            title_en = "Confirm main menu package generation"
            msg_en = (
                "The tool will run: Extract main-menu bank → replace audio → Rebuild → collect files to output.\n\n"
                f"Target bank:\n{selected_bank}\n\nNew audio:\n{replacement_audio}\n\nMode: {mode}\n\n"
                "This mode will not overwrite game files. Continue?"
            )
        if not self.question_box(title_zh, msg_zh, title_en, msg_en):
            return

        def job(report):
            out, _bak, work = ensure_project_dirs()
            fmod_dir = self._fmod_auto_dir()
            report(3, "[MENU] 准备 Fmod Extract 工作目录。")
            extract_manifest = fmod_dir / "main_menu_extract_manifest.json"
            prep_extract = prepare_extract_job(tool, [selected_bank], extract_manifest, search_root=selected_bank.parent, cpu_threads=self._fmod_cpu_threads())
            if not prep_extract.layout or not prep_extract.ok:
                raise RuntimeError(prep_extract.message or "主菜单 Fmod Extract 工作目录准备失败。")
            actual_bank_names = [Path(p).name for p in (prep_extract.output_files or [])]
            if not actual_bank_names:
                raise RuntimeError("Fmod Extract 没有准备任何含音频的主菜单 bank，已停止。")
            report(8, "[MENU] 实际处理 bank: " + ", ".join(actual_bank_names))
            report(10, "[MENU] 启动 Fmod Bank Tools 并执行 Extract。")
            extract_res = launch_trigger_and_wait(tool, "extract", auto_trigger=auto_click, timeout_sec=900)
            if not extract_res.ok:
                raise RuntimeError("主菜单 Fmod Extract 未完成。" + extract_res.message)

            report(35, "[MENU] 导入主菜单 Extract 输出并准备替换音频。")
            template = self._copy_fmod_extract_output(prep_extract.layout.wav_dir, work / "main_menu_extract_template")
            menu_ready, target_rows, modified_bank_names = self._prepare_main_menu_rebuild_workspace(template, replacement_audio, mode, report)
            if not modified_bank_names:
                modified_bank_names = actual_bank_names
            original_banks = self._resolve_menu_original_banks(selected_bank, modified_bank_names)

            report(70, "[MENU] 本次实际需要 Rebuild 的 bank: " + ", ".join(modified_bank_names))
            rebuilt_out = self._rebuild_modified_banks_one_by_one(
                tool, menu_ready, modified_bank_names, original_banks, fmod_dir, out, report,
                auto_click=auto_click, phase_label="MENU"
            )
            rebuilt_files = [rebuilt_out / name for name in modified_bank_names if (rebuilt_out / name).exists()]
            if len(rebuilt_files) != len(modified_bank_names):
                missing = [name for name in modified_bank_names if not (rebuilt_out / name).exists()]
                raise FileNotFoundError("主菜单音乐 Rebuild 输出缺少以下 bank，已停止：" + ", ".join(missing))

            deploy_result = {}
            if deploy:
                deploy_result = self._deploy_main_menu_rebuilt_banks_to_game(rebuilt_out, original_banks, report)

            package_manifest = out / ("main_menu_music_one_click_manifest.json" if deploy else "main_menu_music_package_manifest.json")
            data = {
                "version": APP_VERSION,
                "mode": "one_click_main_menu_music_replace" if deploy else "package_only_main_menu_music",
                "selectedBank": str(selected_bank),
                "actualBanks": [str(p) for p in original_banks],
                "replacementAudio": str(replacement_audio),
                "replaceMode": mode,
                "targetRecords": target_rows,
                "extractTemplate": str(template),
                "fmodReadyWav": str(menu_ready),
                "rebuiltBankDir": str(rebuilt_out),
                "rebuiltBanks": [str(p) for p in rebuilt_files],
                "deploy": deploy_result,
            }
            package_manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            report(100, "[MENU] 主菜单音乐替换流程完成。")
            return data | {"packageManifest": str(package_manifest), "outputDir": str(out)}

        def done(result):
            if deploy:
                self.log("[MENU][OK] 已一键替换主菜单音乐：\n" + json.dumps(result, ensure_ascii=False, indent=2))
                self.info_box(
                    "主菜单音乐替换完成",
                    "已完成 Extract、替换、Rebuild、备份并覆盖主菜单音乐 bank。\n"
                    f"备份 manifest：\n{result['deploy'].get('backupManifest', '')}\n\n"
                    "如果游戏中效果异常，可在本工具中使用“恢复备份/初始状态”。",
                    "Main menu music replacement completed",
                    "Extract, replacement, Rebuild, backup, and game bank overwrite completed.\n"
                    f"Backup manifest:\n{result['deploy'].get('backupManifest', '')}\n\n"
                    "If the in-game result is wrong, use Restore backup / initial state in this tool.",
                )
            else:
                self.log("[MENU][OK] 已生成主菜单音乐 Mod 输出包：\n" + json.dumps(result, ensure_ascii=False, indent=2))
                self.info_box(
                    "主菜单 Mod 包生成完成",
                    "已完成 Extract、替换和 Rebuild。没有覆盖游戏文件。\n\n"
                    f"重打包 bank：\n{result['rebuiltBankDir']}\n\nmanifest：\n{result['packageManifest']}",
                    "Main menu package generated",
                    "Extract, replacement, and Rebuild completed. Game files were not overwritten.\n\n"
                    f"Rebuilt bank:\n{result['rebuiltBankDir']}\n\nManifest:\n{result['packageManifest']}",
                )

        self.run_background_task(
            "一键替换主菜单音乐" if deploy else "生成主菜单音乐 Mod 包",
            job,
            done,
            estimated="取决于目标 bank 大小和 Fmod Rebuild 速度，通常数分钟。",
        )

    def generate_main_menu_music_package(self):
        self._run_main_menu_music_pipeline(deploy=False)

    def one_click_replace_main_menu_music(self):
        self._run_main_menu_music_pipeline(deploy=True)

    def generate_mod_output_package(self):
        """Full Extract -> build -> Rebuild pipeline, but do not overwrite game files."""
        if not self.current_xml:
            self.warn_box("缺少 XML", "请先选择或扫描 RadioInfo XML。", "Missing XML", "Please select or scan the RadioInfo XML first.")
            return
        station = self.current_station_name()
        if not station:
            self.warn_box("缺少电台", "请先选择目标电台。", "Missing radio station", "Please choose a target radio station first.")
            return
        assignments = self.store.get_assignments(station)
        if not assignments:
            self.warn_box("没有分配", "请至少给一个 Slot 分配新音乐。", "No assignment", "Please assign new music to at least one slot.")
            return
        try:
            tool = self._current_fmod_tool_path()
            bank_root = self._current_bank_root()
            banks = self._selected_bank_paths_for_current_station()
            station_tokens = self._station_bank_tokens()
            auto_click = self._fmod_auto_click_enabled()
            current_xml = Path(self.current_xml)
            db_path = self.store.db_path
        except Exception as exc:
            self.show_error("生成 Mod 输出包前置检查失败", exc)
            return

        if not is_pywinauto_available():
            msg_zh, msg_en = self.automation_component_warning_text("生成完整 Mod 输出包", "Generating a complete mod output package")
            self.warn_box("需要自动控制组件", msg_zh, "Automation component required", msg_en)
            return

        bank_list = "\n".join(f"- {p}" for p in banks)
        if not self.question_box(
            "确认仅生成输出包",
            "即将执行：Extract → 生成 XML/音频 → Rebuild → 收集到 output。\n\n"
            f"目标 XML：\n{current_xml}\n\n目标 bank：\n{bank_list}\n\n"
            "此模式不会覆盖游戏文件，只会把修改后的 XML 和重打包 bank 放到 output 目录。是否继续？",
            "Confirm package generation",
            "The tool will run: Extract → generate XML/audio → Rebuild → collect files to output.\n\n"
            f"Target XML:\n{current_xml}\n\nTarget bank:\n{bank_list}\n\n"
            "This mode will not overwrite game files. It only places the modified XML and rebuilt banks in the output folder. Continue?",
        ):
            return

        def job(report):
            out, bak, work = ensure_project_dirs()
            fmod_dir = self._fmod_auto_dir()

            report(2, "[PACKAGE] 准备 Fmod Extract 工作目录。")
            extract_manifest = fmod_dir / "package_extract_manifest.json"
            prep_extract = prepare_extract_job(tool, banks, extract_manifest, search_root=bank_root, preferred_tokens=station_tokens, cpu_threads=self._fmod_cpu_threads())
            if not prep_extract.layout or not prep_extract.ok:
                raise RuntimeError(prep_extract.message or "Fmod Extract 工作目录准备失败。")
            actual_bank_names = [Path(p).name for p in (prep_extract.output_files or [])]
            if not actual_bank_names:
                raise RuntimeError("Fmod Extract 没有准备任何含音频的 bank，已停止。")
            prepared_in_tool = sorted(prep_extract.layout.bank_dir.glob("*.bank"))
            if not prepared_in_tool:
                raise RuntimeError(f"Fmod Bank Tools 的 bank 目录为空：{prep_extract.layout.bank_dir}。已停止启动 Extract，避免 No bank files found。")
            report(6, "[PACKAGE] 实际处理音频 bank: " + ", ".join(actual_bank_names))
            report(7, f"[PACKAGE] Fmod Bank Tools 工作目录: {prep_extract.layout.root_dir}; bank 文件数={len(prepared_in_tool)}")

            report(8, "[PACKAGE] 启动 Fmod Bank Tools 并执行 Extract。")
            extract_res = launch_trigger_and_wait(tool, "extract", auto_trigger=auto_click, timeout_sec=900)
            if not extract_res.ok:
                raise RuntimeError(
                    "Fmod Extract 未完成。" + extract_res.message +
                    "\n已停止生成输出包。请检查 Fmod Bank Tools 的 wav 输出目录是否已经生成 txt 清单和 sound_*.wav 文件。"
                )

            report(28, "[PACKAGE] 导入刚刚 Extract 得到的 wav/txt 模板。")
            template = import_fmod_extract_folder(prep_extract.layout.wav_dir, work)

            report(36, "[PACKAGE] 生成 patched XML 与 fmod_ready_wav。")
            out_dir, xml_backup, fmod_ready, output_xml = self._build_v2_outputs_sync(
                current_xml, station, assignments, db_path, report, backup_label="package_xml_stage"
            )

            modified_bank_names = self._modified_bank_names_from_replacement_plan(work / "replacement_plan.csv") or actual_bank_names
            actual_game_banks = self._resolve_game_bank_paths_by_names(bank_root, modified_bank_names)
            report(84, "[PACKAGE] 本次实际需要 Rebuild 的 bank: " + ", ".join(modified_bank_names))
            rebuilt_out = self._rebuild_modified_banks_one_by_one(
                tool, fmod_ready, modified_bank_names, actual_game_banks, fmod_dir, out, report,
                auto_click=auto_click, phase_label="PACKAGE"
            )
            rebuilt_files = [rebuilt_out / name for name in modified_bank_names if (rebuilt_out / name).exists()]
            if len(rebuilt_files) != len(modified_bank_names):
                missing = [name for name in modified_bank_names if not (rebuilt_out / name).exists()]
                raise FileNotFoundError("Rebuild 输出缺少以下 bank，已停止生成 manifest：" + ", ".join(missing))

            package_manifest = out / "mod_output_package_manifest.json"
            data = {
                "version": APP_VERSION,
                "mode": "package_only_no_game_overwrite",
                "station": station,
                "xml": str(output_xml),
                "rebuiltBankDir": str(rebuilt_out),
                "rebuiltBanks": [str(p) for p in rebuilt_files],
                "extractTemplate": str(template),
                "fmodReadyWav": str(fmod_ready),
                "xmlBackup": xml_backup,
            }
            package_manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            report(100, "[PACKAGE] Mod 输出包已生成。")
            return data | {"packageManifest": str(package_manifest), "outputDir": str(out_dir)}

        def done(result):
            self.log("[PACKAGE][OK] 已生成 Mod 输出包：\n" + json.dumps(result, ensure_ascii=False, indent=2))
            self.info_box(
                "输出包生成完成",
                "已完成 Extract、生成和 Rebuild。没有覆盖游戏文件。\n\n"
                f"修改后的 XML：\n{result['xml']}\n\n"
                f"重打包 bank：\n{result['rebuiltBankDir']}\n\n"
                f"manifest：\n{result['packageManifest']}",
                "Output package generated",
                "Extract, generation, and Rebuild completed. Game files were not overwritten.\n\n"
                f"Modified XML:\n{result['xml']}\n\n"
                f"Rebuilt bank:\n{result['rebuiltBankDir']}\n\n"
                f"Manifest:\n{result['packageManifest']}",
            )

        self.run_background_task(
            "生成 Mod 输出包",
            job,
            done,
            estimated="完整流程取决于 bank 大小、歌曲数量和 Fmod Rebuild 速度，通常数分钟；大电台可能 10 分钟以上。",
        )

    def one_click_replace_game_files(self):
        if not self.current_xml:
            self.warn_box("缺少 XML", "请先选择或扫描 RadioInfo XML。", "Missing XML", "Please select or scan the RadioInfo XML first.")
            return
        station = self.current_station_name()
        if not station:
            self.warn_box("缺少电台", "请先选择目标电台。", "Missing radio station", "Please choose a target radio station first.")
            return
        assignments = self.store.get_assignments(station)
        if not assignments:
            self.warn_box("没有分配", "请至少给一个 Slot 分配新音乐。", "No assignment", "Please assign new music to at least one slot.")
            return
        try:
            tool = self._current_fmod_tool_path()
            bank_root = self._current_bank_root()
            banks = self._selected_bank_paths_for_current_station()
            station_tokens = self._station_bank_tokens()
            auto_click = self._fmod_auto_click_enabled()
            current_xml = Path(self.current_xml)
            db_path = self.store.db_path
        except Exception as exc:
            self.show_error("一键替换前置检查失败", exc)
            return

        if not is_pywinauto_available():
            msg_zh, msg_en = self.automation_component_warning_text("一键替换", "One-click replacement")
            self.warn_box("需要自动控制组件", msg_zh, "Automation component required", msg_en)
            return

        bank_list = "\n".join(f"- {p}" for p in banks)
        if not self.question_box(
            "确认一键替换游戏文件",
            "即将执行完整流程：Extract → 生成 XML/音频 → Rebuild → 备份 → 覆盖游戏文件。\n\n"
            f"目标 XML：\n{current_xml}\n\n目标 bank：\n{bank_list}\n\n"
            "工具会先备份原始文件；如果 Rebuild 输出不完整，将不会覆盖游戏文件。\n是否继续？",
            "Confirm one-click replacement",
            "The tool will run the full workflow: Extract → generate XML/audio → Rebuild → backup → overwrite game files.\n\n"
            f"Target XML:\n{current_xml}\n\nTarget bank:\n{bank_list}\n\n"
            "Original files will be backed up first. If Rebuild output is incomplete, game files will not be overwritten. Continue?",
        ):
            return

        def job(report):
            out, bak, work = ensure_project_dirs()
            fmod_dir = self._fmod_auto_dir()

            report(2, "[ONE-CLICK] 准备 Fmod Extract 工作目录。")
            extract_manifest = fmod_dir / "one_click_extract_manifest.json"
            prep_extract = prepare_extract_job(tool, banks, extract_manifest, search_root=bank_root, preferred_tokens=station_tokens, cpu_threads=self._fmod_cpu_threads())
            if not prep_extract.layout or not prep_extract.ok:
                raise RuntimeError(prep_extract.message or "Fmod Extract 工作目录准备失败。")
            actual_bank_names = [Path(p).name for p in (prep_extract.output_files or [])]
            if not actual_bank_names:
                raise RuntimeError("Fmod Extract 没有准备任何含音频的 bank，已停止。")
            prepared_in_tool = sorted(prep_extract.layout.bank_dir.glob("*.bank"))
            if not prepared_in_tool:
                raise RuntimeError(f"Fmod Bank Tools 的 bank 目录为空：{prep_extract.layout.bank_dir}。已停止启动 Extract，避免 No bank files found。")
            report(6, "[ONE-CLICK] 实际处理音频 bank: " + ", ".join(actual_bank_names))
            report(7, f"[ONE-CLICK] Fmod Bank Tools 工作目录: {prep_extract.layout.root_dir}; bank 文件数={len(prepared_in_tool)}")

            report(8, "[ONE-CLICK] 启动 Fmod Bank Tools 并执行 Extract。")
            extract_res = launch_trigger_and_wait(tool, "extract", auto_trigger=auto_click, timeout_sec=900)
            if not extract_res.ok:
                raise RuntimeError(
                    "Fmod Extract 未完成。" + extract_res.message +
                    "\n请检查 Fmod Bank Tools 的 wav 输出目录是否已经生成 txt 清单和 sound_*.wav 文件。"
                )

            report(28, "[ONE-CLICK] 导入刚刚 Extract 得到的 wav/txt 模板。")
            template = import_fmod_extract_folder(prep_extract.layout.wav_dir, work)

            report(36, "[ONE-CLICK] 生成 patched XML 与 fmod_ready_wav。")
            out_dir, xml_backup, fmod_ready, output_xml = self._build_v2_outputs_sync(
                current_xml, station, assignments, db_path, report, backup_label="one_click_xml_stage"
            )

            modified_bank_names = self._modified_bank_names_from_replacement_plan(work / "replacement_plan.csv") or actual_bank_names
            actual_game_banks = self._resolve_game_bank_paths_by_names(bank_root, modified_bank_names)
            report(84, "[ONE-CLICK] 本次实际需要 Rebuild/覆盖的 bank: " + ", ".join(modified_bank_names))
            rebuilt_out = self._rebuild_modified_banks_one_by_one(
                tool, fmod_ready, modified_bank_names, actual_game_banks, fmod_dir, out, report,
                auto_click=auto_click, phase_label="ONE-CLICK"
            )

            deploy = self._deploy_rebuilt_outputs_to_game(rebuilt_out, actual_game_banks, output_xml, current_xml, report)
            report(100, "[ONE-CLICK] 一键替换完成。")
            return {
                "outputDir": str(out_dir),
                "extractTemplate": str(template),
                "fmodReadyWav": str(fmod_ready),
                "rebuiltBankDir": str(rebuilt_out),
                "xmlBackup": xml_backup,
                "deploy": deploy,
            }

        def done(result):
            self.log("[ONE-CLICK][OK] 已完成完整替换流程：\n" + json.dumps(result, ensure_ascii=False, indent=2))
            self.info_box(
                "一键替换完成",
                "已完成 Extract、生成、Rebuild、备份并覆盖游戏文件。\n"
                f"备份 manifest：\n{result['deploy']['backupManifest']}\n\n"
                "如果游戏中效果异常，可在本工具中使用“恢复备份/初始状态”。",
                "One-click replacement completed",
                "Extract, generation, Rebuild, backup, and game-file replacement completed.\n"
                f"Backup manifest:\n{result['deploy']['backupManifest']}\n\n"
                "If the in-game result is wrong, use Restore backup / initial state in this tool.",
            )

        self.run_background_task(
            "一键执行替换",
            job,
            done,
            estimated="完整流程取决于 bank 大小、歌曲数量和 Fmod Rebuild 速度，通常数分钟；大电台可能 10 分钟以上。",
        )

    def generate_v2_outputs(self):
        if not self.current_xml:
            self.warn_box("缺少 XML", "请先选择或扫描 RadioInfo XML。", "Missing XML", "Please select or scan the RadioInfo XML first.")
            return
        station = self.current_station_name()
        if not station:
            self.warn_box("缺少电台", "请先选择目标电台。", "Missing radio station", "Please choose a target radio station first.")
            return
        assignments = self.store.get_assignments(station)
        if not assignments:
            self.warn_box("没有分配", "请至少给一个 Slot 分配新音乐。", "No assignment", "Please assign new music to at least one slot.")
            return

        current_xml = Path(self.current_xml)
        db_path = self.store.db_path

        def prepare_audio(source: Path, slot_index: int, dst_dir: Path) -> tuple[AudioInfo, AudioNormalizationReport]:
            dst_dir.mkdir(parents=True, exist_ok=True)
            stem = safe_stem(f"slot_{slot_index:02d}_{source.stem}", 90)
            dst = dst_dir / f"{stem}.wav"
            ffmpeg = find_ffmpeg(None)
            normalize_report = run_ffmpeg_normalize(source, dst, ffmpeg)
            info = read_wav_info(dst)
            audio = AudioInfo(
                path=dst,
                filename=dst.name,
                samplerate=info.samplerate,
                channels=info.channels,
                bits_per_sample=info.bits_per_sample,
                frames=info.frames,
                duration_sec=info.duration_sec,
            )
            return audio, normalize_report

        def job(report):
            store = StateStore(db_path)
            out, bak, work = ensure_project_dirs()
            report(5, "[V2] 创建备份快照。")
            snapshot = create_backup_snapshot([current_xml], bak, label="v2_xml")

            report(15, "[V2] 准备已分配音频；非 WAV 会调用 ffmpeg 转码。")
            prepared_dir = work / "v2_prepared_audio"
            if prepared_dir.exists():
                shutil.rmtree(prepared_dir, ignore_errors=True)
            prepared_dir.mkdir(parents=True, exist_ok=True)

            profiles_by_key = {p.track_key: p for p in store.list_track_profiles()}
            audio_by_filename: dict[str, AudioInfo] = {}
            markers_by_filename: dict[str, SegmentMarkers] = {}
            raw_marker_json_by_filename: dict[str, dict[str, int]] = {}
            marker_source_info_by_filename: dict[str, AudioInfo | None] = {}
            slot_to_profile_and_audio: dict[int, tuple[TrackProfile, AudioInfo]] = {}

            total = max(1, len(assignments))
            for idx, (slot, key) in enumerate(sorted(assignments.items()), start=1):
                profile = profiles_by_key.get(key)
                if not profile:
                    raise ValueError(f"slot {slot} 的 profile 丢失: {key}")
                report(15 + int(25 * idx / total), f"[V2] 准备音频 {idx}/{total}: slot {slot} <- {Path(profile.source_path).name}")
                info, normalize_report = prepare_audio(Path(profile.source_path), slot, prepared_dir)
                audio_by_filename[info.filename] = info
                for line in describe_audio_normalization_report(normalize_report):
                    report(15 + int(25 * idx / total), f"[AUDIO] slot {slot}: {line}")
                marker_data = dict(profile.markers or {})
                marker_data.setdefault("TrackStart", 0)
                marker_data.setdefault("TrackDrop", 0)
                marker_data.setdefault("PostDrop", 0)
                marker_data.setdefault("TrackLoopStart", 0)
                marker_data.setdefault("TrackLoopEnd", -1)
                marker_data.setdefault("PostRaceLoopStart", 0)
                marker_data.setdefault("PostRaceLoopEnd", -1)
                marker_data.setdefault("DJSegment", -1)
                marker_data.setdefault("StingerStart", -1)
                marker_data.setdefault("DJStart", -1)
                source_marker_info = marker_source_info_for_profile(profile, normalize_report)
                marker_source_info_by_filename[info.filename] = source_marker_info
                raw_marker_json_by_filename[info.filename] = dict(marker_data)
                normalized_markers = normalize_track_markers_for_prepared_audio(
                    marker_data,
                    source_marker_info,
                    info,
                    source_sample_length=profile.sample_length or None,
                    source_sample_rate=profile.sample_rate or None,
                    marker_unit="samples",
                    label=f"slot {slot}/{info.filename}",
                )
                markers_by_filename[info.filename] = normalized_markers.markers
                for line in normalized_markers.log_lines:
                    report(15 + int(25 * idx / total), line)
                slot_to_profile_and_audio[slot] = (profile, info)

            report(45, "[V2] 构造精确 slot 映射。")
            track_order_path = work / TRACK_ORDER_FILE_NAME
            extract_template = work / FMOD_EXTRACT_TEMPLATE_DIR_NAME
            ensure_track_order_file(track_order_path, current_xml, station, [], None, extract_template if extract_template.exists() else None)
            rows = read_track_order(track_order_path)
            patched_rows = []
            mapping_rows = []
            for row in rows:
                # v2.7.7: clear stale replacement metadata from previous runs/stations.
                # Keep FMOD Extract mapping fields, but apply only the current selected
                # assignments to XML and rebuild WAVs. This prevents in-game custom
                # titles from being shifted or reused from an earlier run.
                base_notes = row.notes or ""
                row = replace(
                    row,
                    audio_filename="",
                    display_name=row.original_display_name or "",
                    artist=row.original_artist or "",
                    notes=base_notes,
                )
                if row.slot_index in slot_to_profile_and_audio:
                    profile, info = slot_to_profile_and_audio[row.slot_index]
                    display = profile.display_name or Path(profile.filename).stem
                    artist = profile.artist or "User"
                    row = replace(
                        row,
                        audio_filename=info.filename,
                        display_name=display,
                        artist=artist,
                        notes=(base_notes + " | " if base_notes else "") + "v2_manual_assignment_current_run",
                    )
                    mapping_rows.append({
                        "slot_index": row.slot_index,
                        "target_wav": row.original_wav_relpath,
                        "audio_filename": info.filename,
                        "display_name": display,
                        "artist": artist,
                    })
                patched_rows.append(row)
            patched_rows, effective_assigned_slots, skipped_xml_only_rows = self._filter_xml_only_unmatched_rows(
                patched_rows, assignments.keys(), extract_template, report, station=station
            )
            mapping_rows = self._mapping_rows_from_track_rows(patched_rows)
            write_track_order(track_order_path, patched_rows)
            try:
                mapping_report = work / "current_assignment_mapping.csv"
                import csv as _csv
                with mapping_report.open("w", encoding="utf-8-sig", newline="") as _f:
                    _writer = _csv.DictWriter(_f, fieldnames=["slot_index", "target_wav", "audio_filename", "display_name", "artist"])
                    _writer.writeheader()
                    _writer.writerows(mapping_rows)
            except Exception:
                pass

            report(50, "[V2] 校验 XML 槽位与 FMOD 音频映射。")
            inventory_report = work / "fmod_sound_inventory.csv"
            replacement_plan = work / "replacement_plan.csv"
            validation_report = work / "replacement_validation.txt"
            try:
                write_fmod_sound_inventory(inventory_report, extract_template if extract_template.exists() else None)
                write_replacement_plan(replacement_plan, patched_rows, audio_by_filename, effective_assigned_slots, extract_template if extract_template.exists() else None)
            except Exception as exc:
                report(50, f"[WARN] 写入替换诊断报告失败: {exc}")
            selected_errors = validate_selected_replacements(
                patched_rows, audio_by_filename, effective_assigned_slots, extract_template if extract_template.exists() else None
            )
            fatal_errors = [e for e in selected_errors if not str(e).startswith("WARN:")]
            validation_report.write_text("\n".join(selected_errors) + ("\n" if selected_errors else "OK\n"), encoding="utf-8")
            warn_count = len(selected_errors) - len(fatal_errors)
            if warn_count:
                report(51, f"[WARN] 有 {warn_count} 个低置信度 FMOD 映射，详情见 work/replacement_plan.csv。")
            if fatal_errors:
                short = "\n".join(fatal_errors[:8])
                raise ValueError(
                    "替换计划校验失败：你选择的部分歌曲槽位没有成功匹配到 FMOD 音频。\n"
                    "为避免出现‘游戏里显示新歌名但实际仍播放原曲’，本次生成已停止，未写入 XML，也不会 Rebuild。\n\n"
                    f"前几项错误：\n{short}\n\n"
                    f"完整诊断：{replacement_plan}\n{inventory_report}\n{validation_report}"
                )

            report(55, "[V2] 写入 XML。")
            output_xml = out / current_xml.name
            _patch_xml_by_track_order(current_xml, station, output_xml, patched_rows, audio_by_filename, markers_by_filename)

            fmod_ready = out / FMOD_READY_WAV_DIR_NAME
            if extract_template.exists():
                report(70, "[V2] 生成 fmod_ready_wav 并匹配音量；这一步可能耗时较长。")
                create_fmod_rebuild_workspace(out, extract_template, patched_rows, audio_by_filename, progress_callback=report)
            else:
                fmod_ready = out / FMOD_READY_WAV_DIR_NAME

            final_audio_by_filename: dict[str, AudioInfo] = dict(audio_by_filename)
            final_markers_by_filename: dict[str, SegmentMarkers] = dict(markers_by_filename)
            if fmod_ready.exists():
                for row in patched_rows:
                    if not row.audio_filename or row.slot_index not in effective_assigned_slots:
                        continue
                    ready_path = fmod_ready / row.original_wav_relpath if row.original_wav_relpath else None
                    if not ready_path or not ready_path.exists():
                        continue
                    ready_info = read_wav_info(ready_path)
                    final_info = AudioInfo(
                        path=ready_info.path,
                        filename=row.audio_filename,
                        samplerate=ready_info.samplerate,
                        channels=ready_info.channels,
                        bits_per_sample=ready_info.bits_per_sample,
                        frames=ready_info.frames,
                        duration_sec=ready_info.duration_sec,
                    )
                    final_audio_by_filename[row.audio_filename] = final_info
                    marker_source_info = marker_source_info_by_filename.get(row.audio_filename)
                    marker_data = raw_marker_json_by_filename.get(row.audio_filename, {})
                    final_marker_result = normalize_track_markers_for_prepared_audio(
                        marker_data,
                        marker_source_info,
                        final_info,
                        source_sample_length=(marker_source_info.sample_length if marker_source_info else None),
                        source_sample_rate=(marker_source_info.samplerate if marker_source_info else None),
                        marker_unit="samples",
                        label=f"slot {row.slot_index}/{row.audio_filename}",
                    )
                    final_markers_by_filename[row.audio_filename] = final_marker_result.markers
                    for line in final_marker_result.log_lines:
                        report(86, line)

            xml_sync_report: list[str] = []
            _patch_xml_by_track_order(
                current_xml,
                station,
                output_xml,
                patched_rows,
                final_audio_by_filename,
                final_markers_by_filename,
                xml_sync_report=xml_sync_report,
            )
            xml_sync_report_path = work / "xml_marker_sync_validation.txt"
            xml_sync_report_path.write_text("\n".join(xml_sync_report) + ("\n" if xml_sync_report else "OK\n"), encoding="utf-8")
            report(87, f"[XML] Marker/SampleLength node sync entries: {len(xml_sync_report)}; report: {xml_sync_report_path}")

            manifest_path = store.export_manifest(work / "v2_project_manifest.json")
            summary = {
                "version": APP_VERSION,
                "station": station,
                "xml": str(output_xml),
                "assignedSlots": sorted(effective_assigned_slots),
                "trackOrder": str(track_order_path),
                "backup": snapshot.manifest_path,
                "fmodReadyWav": str(fmod_ready),
                "manifest": str(manifest_path),
            }
            (out / "v2_generation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            report(100, f"[OK] v2 输出完成: {out}")
            return out, snapshot.manifest_path, fmod_ready

        def done(result):
            out, backup_manifest, fmod_ready = result
            self.log(f"[BACKUP] {backup_manifest}")
            if not Path(fmod_ready).exists():
                self.log("[WARN] 未导入 Fmod Extract 模板，因此本次只生成 XML 和项目 manifest，不生成完整 fmod_ready_wav。")
            self.set_progress(100, f"[OK] v2 输出完成: {out}")
            self.info_box("生成完成", f"v2 输出已生成：\n{out}", "Generation completed", f"v2 output generated:\n{out}")

        self.run_background_task("生成 v2 输出包", job, done, estimated="取决于替换歌曲数量、转码和音量匹配；通常几十秒，批量替换可能数分钟。")

    def export_manifest(self):
        try:
            path = self.store.export_manifest(project_work_dir() / "v2_project_manifest.json")
            self.log(f"[OK] manifest 已导出: {path}")
        except Exception as exc:
            self.show_error("导出 manifest 失败", exc)

    def backup_current_game_files(self):
        """Create a manual backup point and ensure the initial-state baseline exists."""
        if not self.ensure_idle_for_action("创建备份点", "Create backup point"):
            return
        try:
            files: list[Path] = []
            if self.current_xml:
                files.append(Path(self.current_xml))
            else:
                xml = self.txt_path(self.xml_edit)
                if xml:
                    files.append(xml)
            try:
                files.extend(self._selected_bank_paths_for_current_station())
            except Exception as exc:
                self.log(f"[BACKUP][WARN] 未能自动加入 bank 备份：{exc}")
            files = [p for p in files if p and p.exists()]
            if not files:
                self.warn_box("没有可备份文件", "请先选择游戏目录并加载 RadioInfo XML。", "No files to back up", "Please select the game folder and load a RadioInfo XML first.")
                return
            game_root = self.txt_path(self.game_root_edit)
            initial = ensure_initial_state_snapshot(files, project_backup_dir(), game_root=game_root, label="initial_game_state")
            snapshot = create_backup_snapshot(files, project_backup_dir(), label="manual_backup_point")
            self.log(f"[BACKUP][OK] 初始状态 manifest：{initial.manifest_path}")
            self.log(f"[BACKUP][OK] 本次备份点 manifest：{snapshot.manifest_path}")
            self.info_box(
                "备份完成",
                "已完成两层备份：\n"
                "1. 初始状态备份：只在首次遇到某个 XML/bank 时保存，用于恢复到最早状态。\n"
                "2. 本次备份点：用于恢复到这次操作前的状态。\n\n"
                f"初始状态：{self.backup_display_name(Path(initial.manifest_path))}\n"
                f"本次备份点：{self.backup_display_name(Path(snapshot.manifest_path))}",
                "Backup completed",
                "Two backup layers were created:\n"
                "1. Initial state: captured only once for each XML/bank file, used to return to the earliest state.\n"
                "2. Backup point: used to return to the state before this operation.\n\n"
                f"Initial state: {self.backup_display_name(Path(initial.manifest_path))}\n"
                f"Backup point: {self.backup_display_name(Path(snapshot.manifest_path))}",
            )
        except Exception as exc:
            self.show_error("备份当前游戏文件失败", exc)

    def _clear_tool_side_replacement_state_after_restore(self, restored_files: list[str] | None = None) -> int:
        """Clear stale in-tool replacement state after a game-file restore.

        Restoring XML/bank files changes the game files on disk, but the tool keeps
        pending slot assignments in its SQLite work database.  If those assignments
        remain checked, the next one-click replacement will apply them again and
        make it look as if restore did not really work.
        """
        cleared = 0
        try:
            cleared = self.store.clear_all_assignments()
        except Exception as exc:
            self.log(f"[RESTORE][WARN] 未能清空工具内替换计划: {exc}")

        work = project_work_dir()
        stale_names = [
            "current_assignment_mapping.csv",
            "replacement_plan.csv",
            "replacement_validation.txt",
            "xml_metadata_validation.csv",
            "final_wav_samplelength_report.csv",
            "bank_candidate_report.csv",
            "v2_project_manifest.json",
            "v2_generation_summary.json",
        ]
        stale_dirs = [
            "v2_prepared_audio",
            FMOD_READY_WAV_DIR_NAME,
        ]
        for name in stale_names:
            try:
                p = work / name
                if p.exists():
                    p.unlink()
            except Exception as exc:
                self.log(f"[RESTORE][WARN] 未能删除旧诊断文件 {name}: {exc}")
        for name in stale_dirs:
            try:
                p = work / name
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
            except Exception as exc:
                self.log(f"[RESTORE][WARN] 未能清理旧工作目录 {name}: {exc}")

        try:
            self.reload_slots()
        except Exception as exc:
            self.log(f"[RESTORE][WARN] 恢复后刷新槽位列表失败: {exc}")
        self.log(self.log_text(
            f"[RESTORE][STATE] 已清空工具内待替换计划：{cleared} 条；已清理旧 mapping/生成缓存。",
            f"[RESTORE][STATE] Cleared pending in-tool replacement assignments: {cleared}; stale mapping/generation cache cleaned.",
        ))
        return cleared

    def restore_from_manifest(self):
        """Restore either the initial game state or a selected backup manifest."""
        if not self.ensure_idle_for_action("恢复备份", "Restore backup"):
            return

        manifests = self.list_backup_manifests()
        choices = [self.ui_text("恢复初始状态（推荐）", "Restore initial state (recommended)")]
        path_by_label: dict[str, Path] = {}
        for p in manifests:
            label = self.backup_display_name(p)
            # Ensure labels stay unique in QInputDialog.
            if label in path_by_label:
                label = f"{label} · {p.parent.name}"
            choices.append(label)
            path_by_label[label] = p
        choices.append(self.ui_text("手动选择 manifest 文件...", "Choose manifest file manually..."))

        choice, ok = QInputDialog.getItem(
            self,
            self.ui_text("恢复备份/初始状态", "Restore backup / initial state"),
            self.ui_text("请选择要恢复的备份：", "Choose a backup to restore:"),
            choices,
            0,
            False,
        )
        if not ok or not choice:
            return

        if choice == choices[0]:
            try:
                game_root = self.txt_path(self.game_root_edit)
                restored = restore_initial_state(project_backup_dir(), game_root=game_root)
                self.log(self.log_text("[RESTORE][OK] 已恢复初始状态文件:\n", "[RESTORE][OK] Restored initial-state files:\n") + "\n".join(restored))
                cleared = self._clear_tool_side_replacement_state_after_restore(restored)
                self.info_box(
                    "恢复完成",
                    f"已恢复初始状态，共 {len(restored)} 个文件。\n已清空工具内待替换计划 {cleared} 条，避免下次一键替换再次写回旧修改。",
                    "Restore completed",
                    f"Restored initial state. {len(restored)} file(s) restored.\nCleared {cleared} pending in-tool replacement assignment(s), so old changes will not be applied again on the next one-click replacement.",
                )
            except Exception as exc:
                self.show_error(self.ui_text("恢复初始状态失败", "Failed to restore initial state"), exc)
            return

        if choice == choices[-1]:
            p, _ = QFileDialog.getOpenFileName(
                self,
                self.ui_text("选择备份 manifest", "Choose backup manifest"),
                str(project_backup_dir()),
                "JSON (*.json);;All files (*.*)",
            )
            if not p:
                return
            manifest_path = Path(p)
        else:
            manifest_path = path_by_label.get(choice)
            if manifest_path is None:
                return

        if not self.question_box(
            "确认恢复",
            f"即将恢复：\n{self.backup_display_name(manifest_path)}\n\n这会覆盖当前游戏 XML/bank 文件。是否继续？",
            "Confirm restore",
            f"Restore this backup?\n{self.backup_display_name(manifest_path)}\n\nThis will overwrite the current game XML/bank files. Continue?",
        ):
            return
        try:
            restored = restore_snapshot(manifest_path)
            self.log(self.log_text("[RESTORE][OK] 已恢复备份点文件:\n", "[RESTORE][OK] Restored backup-point files:\n") + "\n".join(restored))
            cleared = self._clear_tool_side_replacement_state_after_restore(restored)
            self.info_box(
                "恢复完成",
                f"已恢复 {len(restored)} 个文件。\n已清空工具内待替换计划 {cleared} 条，避免下次一键替换再次写回旧修改。",
                "Restore completed",
                f"Restored {len(restored)} file(s).\nCleared {cleared} pending in-tool replacement assignment(s), so old changes will not be applied again on the next one-click replacement.",
            )
        except Exception as exc:
            self.show_error(self.ui_text("恢复备份点失败", "Failed to restore backup point"), exc)

    def show_error(self, title: str, exc: Exception):
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.log(f"[ERROR] {title}: {exc}\n{detail}")
        self.error_box(title, str(exc), title, str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
