from __future__ import annotations

import hashlib
import csv
import json
import shutil
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QProgressBar, QSlider, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QHBoxLayout, QWidget, QScrollArea, QStackedWidget
)

from .async_tools import BackgroundTask
from .bank_tools import choose_banks, make_bank_plan, write_bank_plan_outputs
from .ffmpeg_tools import find_ffmpeg, run_ffmpeg_normalize, safe_stem
from .metadata_tools import guess_display_artist_from_filename
from .models import AudioInfo, SegmentMarkers
from .order_tools import (
    FMOD_EXTRACT_TEMPLATE_DIR_NAME, FMOD_READY_WAV_DIR_NAME, TRACK_ORDER_FILE_NAME,
    create_fmod_rebuild_workspace, ensure_track_order_file, import_fmod_extract_folder,
    read_track_order, write_track_order,
)
from .project_tools import (
    _patch_xml_by_track_order, ensure_project_dirs, project_backup_dir,
    project_output_dir, project_work_dir,
)
from .segment_tools import MARKER_ORDER, SEGMENTS_FILE_NAME, load_segments, markers_from_json, markers_to_json, save_segments
from .v2_deploy_tools import create_backup_snapshot, restore_snapshot
from .v2_game_tools import resolve_fmod_bank_root, scan_game_root, write_scan_report
from .v2_loop_tools import analyze_loop_candidates, markers_from_candidate
from .loop_engine.scene_preview import build_scene_preview_plan
from .fmod_automation import (
    collect_rebuilt_banks as fmod_collect_rebuilt_banks,
    bank_contains_fsb_audio, bank_preflight_message, is_pywinauto_available,
    launch_and_optionally_trigger, launch_trigger_and_wait, layout_from_exe,
    prepare_extract_job, prepare_rebuild_job,
)
from .v2_state_store import APP_VERSION, StateStore, TrackProfile
from .marker_import_tools import MarkerImportRow, normalize_match_text, read_marker_import_file, write_marker_import_template, IMPORT_COLUMNS
from .wav_preview_player import WavPreviewPlayer
from .wav_tools import list_audio_candidates, natural_key, read_wav_info, validate_wav
from .xml_tools import find_station, list_station_infos, parse_xml, station_info_from_node, write_xml




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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"FH6 Radio Tool v{APP_VERSION} - Simple One-Click Mode")
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
        self.auto_fmod_click_check = QCheckBox("自动控制 Fmod Bank Tools（推荐；安装环境会自动安装 pywinauto）")
        self.auto_fmod_click_check.setChecked(bool(self.store.get_setting("fmod_auto_click", True)))

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
        self.position_label = QLabel("0 / 0")
        self.marker_target_combo = QComboBox()
        self.preview_seconds_spin = QSpinBox()
        self.preview_seconds_spin.setRange(1, 30)
        self.preview_seconds_spin.setValue(5)
        self._updating_slider = False

        self.marker_spins: dict[str, QSpinBox] = {}
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("运行日志会显示在这里。")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)

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
        self.seek_slider.sliderReleased.connect(self.seek_to_slider)
        self.seek_slider.sliderMoved.connect(self.on_seek_slider_moved)
        self.player.positionChanged.connect(self.on_player_position_changed)
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
        self.btn_backup = QPushButton("备份当前游戏文件")
        self.btn_backup.setObjectName("BackupAction")
        self.btn_backup.clicked.connect(self.backup_current_game_files)
        self.btn_restore = QPushButton("恢复默认文件")
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
        self.station_combo.setMaximumWidth(360)
        station_bar.addWidget(self.lbl_station)
        station_bar.addWidget(self.station_combo)
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

        self.btn_analyze_loop = QPushButton("分析 Loop")
        self.btn_analyze_loop.setMinimumWidth(110)
        self.btn_analyze_loop.setObjectName("PrimaryAction")
        self.btn_analyze_loop.clicked.connect(self.analyze_current_loop_audio)

        self.lbl_candidate = QLabel("候选段落")
        self.lbl_candidate.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_preview_seconds = QLabel("试听秒数")
        self.lbl_preview_seconds.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.preview_seconds_spin.setFixedWidth(72)
        self.btn_preview_candidate = QPushButton("试听候选")
        self.btn_preview_candidate.setMinimumWidth(110)
        self.btn_preview_candidate.clicked.connect(self.preview_selected_candidate)

        candidate_grid.addWidget(self.btn_analyze_loop, 0, 0)
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
        seek_layout.addWidget(self.seek_slider)
        playback_row = QHBoxLayout()
        playback_row.addWidget(self.position_label)
        playback_row.addStretch(1)
        self.btn_play_pause = QPushButton("播放 / 继续")
        self.btn_play_pause.setMinimumWidth(125)
        self.btn_play_pause.clicked.connect(lambda: self.player.play())
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setMinimumWidth(80)
        self.btn_stop.clicked.connect(lambda: self.player.stop())
        playback_row.addWidget(self.btn_play_pause)
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
                self.marker_spins[name] = spin
                row_layout.addWidget(label)
                row_layout.addWidget(spin)
                row_layout.addSpacing(12)
            row_layout.addStretch(1)
            marker_outer.addLayout(row_layout)

        marker_import_row = QHBoxLayout()
        self.btn_import_markers = QPushButton("批量导入 Marker")
        self.btn_import_markers.clicked.connect(self.import_marker_profiles_from_file)
        self.btn_export_marker_template = QPushButton("导出 Marker")
        self.btn_export_marker_template.clicked.connect(self.export_marker_import_template_dialog)
        marker_import_row.addStretch(1)
        marker_import_row.addWidget(self.btn_import_markers)
        marker_import_row.addWidget(self.btn_export_marker_template)
        marker_outer.addLayout(marker_import_row)
        outer.addWidget(marker_box)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_save_loop_profile = QPushButton("保存当前音频设置")
        self.btn_save_loop_profile.clicked.connect(self.save_current_loop_profile)
        bottom.addWidget(self.btn_save_loop_profile)
        outer.addLayout(bottom)
        outer.addStretch(1)

        scroll.setWidget(content)
        root_layout.addWidget(scroll, 1)
        return root

    def _build_deploy_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        self.final_box = QGroupBox("最终操作 / 普通玩家只需要这里")
        grid = QGridLayout(self.final_box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self.btn_fmod_tool = QPushButton("选择 Fmod Bank Tools")
        self.btn_fmod_tool.setMinimumWidth(190)
        self.btn_fmod_tool.clicked.connect(self.browse_fmod_tool)
        self.btn_package = QPushButton("仅生成 Mod 输出包")
        self.btn_package.setMinimumWidth(210)
        self.btn_package.setObjectName("PrimaryAction")
        self.btn_package.clicked.connect(self.generate_mod_output_package)
        self.btn_one_click = QPushButton("一键替换到游戏")
        self.btn_one_click.setMinimumWidth(210)
        self.btn_one_click.setObjectName("DangerAction")
        self.btn_one_click.clicked.connect(self.one_click_replace_game_files)

        self.lbl_auto_bank = QLabel("自动定位 Bank 目录")
        self.lbl_auto_bank.setVisible(False)
        self.bank_root_edit.setVisible(False)
        self.lbl_fmod_tool = QLabel("Fmod Bank Tools exe")
        grid.addWidget(self.lbl_fmod_tool, 0, 0)
        grid.addWidget(self.fmod_tool_edit, 0, 1)
        grid.addWidget(self.btn_fmod_tool, 0, 2)
        grid.addWidget(self.auto_fmod_click_check, 1, 1, 1, 2)
        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_package)
        action_row.addWidget(self.btn_one_click)
        action_row.addStretch(1)
        grid.addLayout(action_row, 2, 0, 1, 3)
        outer.addWidget(self.final_box)

        self.final_hint = QLabel("推荐：选择游戏目录和音乐目录 → 勾选槽位/音乐 → 应用选择替换 → 设置 Loop → 仅生成输出包或一键替换。")
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
        self.setWindowTitle(f"FH6 Radio Tool v{APP_VERSION} - {'Simple One-Click Mode' if en else '简洁一键模式'}")
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
            'lbl_loop_audio': ("Audio file", "音频文件"),
            'lbl_candidate': ("Candidate", "候选段落"),
            'lbl_preview_seconds': ("Preview seconds", "试听秒数"),
            'lbl_preview_scene': ("Scene preview", "场景试听"),
            'lbl_marker_target': ("Target Marker", "目标 Marker"),
            'log_title_label': ("Log", "日志 / Log"),
        }
        for attr, (en_text, zh_text) in pairs.items():
            obj = getattr(self, attr, None)
            if obj is not None:
                obj.setText(en_text if en else zh_text)
        buttons = {
            'btn_game': ("Choose game folder", "选择游戏目录"),
            'btn_music': ("Choose music folder", "选择音乐目录"),
            'btn_backup': ("Backup game files", "备份当前游戏文件"),
            'btn_restore': ("Restore defaults", "恢复默认文件"),
            'btn_clear_assignment': ("Cancel selected", "取消已选替换"),
            'btn_assign': ("Apply replacement", "应用选择替换"),
            'btn_fmod_tool': ("Choose Fmod Bank Tools", "选择 Fmod Bank Tools"),
            'btn_package': ("Generate mod package", "仅生成 Mod 输出包"),
            'btn_one_click': ("One-click replace", "一键替换到游戏"),
            'btn_hide_setup': ("Done, hide setup", "完成设置并隐藏"),
            'btn_prev_step': ("Back", "上一步"),
            'btn_next_step': ("Next", "下一步"),
            'btn_analyze_loop': ("Analyze Loop", "分析 Loop"),
            'btn_preview_candidate': ("Preview loop", "试听候选"),
            'btn_preview_scene': ("Preview scene", "试听场景"),
            'btn_apply_track': ("Use as Track Loop", "填入 Track Loop"),
            'btn_apply_post': ("Use as Post Loop", "填入 Post Loop"),
            'btn_apply_both': ("Use as Track/Post", "同时填入 Track/Post"),
            'btn_play_pause': ("Play / Resume", "播放 / 继续"),
            'btn_stop': ("Stop", "停止"),
            'btn_jump_marker': ("Jump to Marker", "跳到 Marker"),
            'btn_write_marker': ("Write current point", "当前点写入 Marker"),
            'btn_save_loop_profile': ("Save audio settings", "保存当前音频设置"),
            'btn_import_markers': ("Import markers", "批量导入 Marker"),
            'btn_export_marker_template': ("Export markers", "导出 Marker"),
        }
        for attr, (en_text, zh_text) in buttons.items():
            obj = getattr(self, attr, None)
            if obj is not None:
                obj.setText(en_text if en else zh_text)
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
            self.auto_fmod_click_check.setText(
                "Auto-control Fmod Bank Tools (recommended; setup installs pywinauto)" if en
                else "自动控制 Fmod Bank Tools（推荐；安装环境会自动安装 pywinauto）"
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
        if hasattr(self, 'assign_hint'):
            self.assign_hint.setText(
                "Tip: click the first column header to select/clear all. Check N game slots and N music files, then click Apply replacement."
                if en else
                "提示：点击表格第一列标题可全选/取消；左侧勾选 N 个槽位，右侧勾选 N 首音乐，然后点“应用选择替换”。"
            )
        if hasattr(self, 'final_box'):
            self.final_box.setTitle("Final actions / normal users only need this" if en else "最终操作 / 普通玩家只需要这里")
        if hasattr(self, 'final_hint'):
            self.final_hint.setText(
                "Flow: choose game/music folders → check slots/files → apply replacement → set loop points → generate package or one-click replace."
                if en else
                "推荐：选择游戏目录和音乐目录 → 勾选槽位/音乐 → 应用选择替换 → 设置 Loop → 仅生成输出包或一键替换。"
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

    def _on_task_heartbeat(self) -> None:
        if not self._busy:
            return
        elapsed = int(max(0, time.monotonic() - self._task_started_at))
        title = self._task_title or "Task"
        self.statusBar().showMessage(f"{title} running... {elapsed}s. UI is responsive; do not close Fmod Bank Tools while Extract/Rebuild is running.")
        self.progress.repaint()

    def txt_path(self, edit: QLineEdit) -> Path | None:
        text = edit.text().strip().strip('"')
        return Path(text) if text else None

    def log(self, text: str) -> None:
        self.log_box.appendPlainText(str(text))

    def set_progress(self, value: int, message: str = "") -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, int(value))))
        if message:
            self.log(message)

    def run_background_task(self, title: str, job, on_success=None, *, estimated: str = "") -> None:
        """Run a long operation in a QThread while all UI work stays on GUI thread."""
        if self._busy:
            QMessageBox.information(self, "任务正在进行", "已有耗时任务正在执行，请等待当前任务完成。")
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
        self.progress.setFormat(f"{title} - %p%")
        self._heartbeat_timer.start()
        self.statusBar().showMessage(f"{title} running... You can move the window and read progress below.")
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

        def handle_success(result):
            finish_ui(True)
            if on_success:
                on_success(result)

        def handle_failed(trace_text: str):
            finish_ui(False)
            self.log(f"[ERROR] {title} 失败：\n{trace_text}")
            last = trace_text.strip().splitlines()[-1] if trace_text.strip() else "未知错误"
            QMessageBox.critical(self, f"{title} 失败", last)

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
        p = QFileDialog.getExistingDirectory(self, "选择游戏根目录")
        if p:
            self.game_root_edit.setText(p)
            self.store.set_setting("game_root", p)
            self.scan_game_root()

    def browse_music_dir(self):
        p = QFileDialog.getExistingDirectory(self, "选择音乐目录")
        if p:
            self.music_dir_edit.setText(p)
            self.store.set_setting("music_dir", p)
            self.scan_music_dir()

    def browse_xml(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 RadioInfo XML", "", "XML (*.xml);;All files (*.*)")
        if p:
            self.xml_edit.setText(p)
            self.load_xml(Path(p))

    def browse_extract_dir(self):
        p = QFileDialog.getExistingDirectory(self, "选择 Fmod Bank Tools Extract 的 Wav Output Directory")
        if p:
            self.extract_dir_edit.setText(p)

    def browse_bank_root(self):
        p = QFileDialog.getExistingDirectory(self, "选择 bank 文件所在目录")
        if p:
            self.bank_root_edit.setText(p)
            self.store.set_setting("bank_root", p)

    def browse_fmod_tool(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 Fmod Bank Tools 可执行文件", "", "Executable (*.exe);;All files (*.*)")
        if p:
            self.fmod_tool_edit.setText(p)
            self.store.set_setting("fmod_tool", p)

    def scan_game_root(self):
        root = self.txt_path(self.game_root_edit)
        if not root:
            QMessageBox.warning(self, "缺少路径", "请先选择游戏根目录。")
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
        try:
            tree = parse_xml(xml_path)
            self.station_infos = list_station_infos(tree)
            self.station_combo.blockSignals(True)
            self.station_combo.clear()
            for st in self.station_infos:
                self.station_combo.addItem(f"{st.name}  ({st.track_slot_count} tracks)", st.name)
            self.station_combo.blockSignals(False)
            self.current_xml = Path(xml_path)
            self.store.set_setting("xml_path", str(xml_path))
            if not quiet:
                self.log(f"[OK] XML 已加载: {xml_path}")
            self.reload_slots()
        except Exception as exc:
            if quiet:
                self.log(f"[WARN] 上次 XML 无法加载: {exc}")
            else:
                self.show_error("加载 XML 失败", exc)

    def current_station_name(self) -> str | None:
        data = self.station_combo.currentData()
        return str(data) if data else None

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
            assignments = self.store.get_assignments(station)
            profiles = {p.track_key: p for p in self.store.list_track_profiles()}
            self.slot_table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                key = assignments.get(int(row["slot_index"]))
                profile = profiles.get(key) if key else None
                marker_text = ""
                if profile and profile.markers:
                    marker_text = ", ".join(f"{k}={v}" for k, v in profile.markers.items() if k in ("TrackLoopStart", "TrackLoopEnd", "PostRaceLoopStart", "PostRaceLoopEnd"))
                status = "已勾选替换" if profile else "保持原样"
                set_check_item(self.slot_table, i, 0, bool(profile), data=row["slot_index"])
                set_item(self.slot_table, i, 1, row["slot_index"], data=row["slot_index"])
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
                                markers={"TrackStart": 0, "End": max(0, info.sample_length - 1)},
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
        return checked if checked else self.selected_table_rows(self.slot_table)

    def selected_music_rows(self) -> list[int]:
        checked = self.checked_table_rows(self.music_table, 0)
        return checked if checked else self.selected_table_rows(self.music_table)

    def set_all_slot_checks(self, checked: bool) -> None:
        for r in range(self.slot_table.rowCount()):
            item = self.slot_table.item(r, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def set_all_music_checks(self, checked: bool) -> None:
        for r in range(self.music_table.rowCount()):
            item = self.music_table.item(r, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def toggle_all_slot_checks(self) -> None:
        total = self.slot_table.rowCount()
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
            QMessageBox.warning(self, "缺少选择", "请同时选择目标电台 Slot 和音乐文件。")
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
            QMessageBox.warning(self, "缺少电台", "请先选择目标电台。")
            return
        if not slot_rows or not music_rows:
            QMessageBox.warning(self, "缺少选择", "请在左侧选择要替换的 Slot，并在右侧选择等量音乐。")
            return
        if len(slot_rows) != len(music_rows):
            QMessageBox.warning(self, "数量不一致", f"已选择 {len(slot_rows)} 个 Slot，但选择了 {len(music_rows)} 首音乐。请保持数量一致。")
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
        try:
            if path.suffix.lower() in (".wav", ".wave"):
                info = read_wav_info(path)
                self.player.set_source(path)
                max_sample = min(2_147_483_647, max(0, info.sample_length - 1))
                self.seek_slider.setRange(0, max_sample)
                self.position_label.setText(f"0 / {format_sample_time(max_sample, info.samplerate)}")
                profile = self.store.load_track_profile(track_key_for_path(path))
                markers = profile.markers if profile and profile.markers else {"TrackStart": 0, "End": max_sample}
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
        except Exception as exc:
            self.log(f"[WARN] 无法载入试听音频: {exc}")

    def analyze_current_loop_audio(self):
        path = self.current_loop_audio
        if not path:
            QMessageBox.warning(self, "未选择音频", "请先选择一个音频。")
            return
        if path.suffix.lower() not in (".wav", ".wave"):
            QMessageBox.warning(self, "需要 WAV", "当前 v2.2.1 内置 Loop Engine 要求 16-bit PCM WAV。非 WAV 可先用生成流程转码。")
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
                QMessageBox.information(self, "没有候选", "自动分析未找到可靠候选。可以使用进度条试听并手动写入 Marker。")

        self.run_background_task(
            "自动分析 Loop 候选",
            job,
            done,
            estimated="普通 3–5 分钟歌曲约 5–60 秒；PyMusicLooper 补充阶段最长约 45 秒。",
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

    def current_marker_values(self) -> dict[str, int]:
        return {name: int(spin.value()) for name, spin in self.marker_spins.items() if spin.value() >= 0}

    def preview_selected_candidate(self):
        cand = self.selected_candidate()
        if not cand:
            QMessageBox.warning(self, "缺少候选", "请先分析并选择一个 Loop 候选。")
            return
        sr = max(1, int(self.player.samplerate or 48000))
        seconds = int(self.preview_seconds_spin.value())
        start = max(cand.loop_start, cand.loop_end - seconds * sr)
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
            self.player.play_range(plan.start_sample, plan.end_sample, loop=plan.loop, loop_start_sample=plan.loop_start_sample)
            self.set_progress(0, f"试听场景：{plan.description}  {format_sample_time(plan.start_sample, sr)} → {format_sample_time(plan.end_sample, sr)}")
        except Exception as exc:
            self.show_error("场景试听失败", exc)

    def on_player_position_changed(self, sample: int):
        if self._updating_slider:
            return
        sr = max(1, int(self.player.samplerate or 48000))
        max_sample = max(0, int(self.player.total_frames) - 1)
        self._updating_slider = True
        try:
            if self.seek_slider.maximum() != max_sample:
                self.seek_slider.setRange(0, min(2_147_483_647, max_sample))
            self.seek_slider.setValue(max(0, min(int(sample), self.seek_slider.maximum())))
            self.position_label.setText(f"{format_sample_time(sample, sr)} / {format_sample_time(max_sample, sr)}")
        finally:
            self._updating_slider = False

    def on_seek_slider_moved(self, value: int):
        sr = max(1, int(self.player.samplerate or 48000))
        max_sample = max(0, int(self.player.total_frames) - 1)
        self.position_label.setText(f"{format_sample_time(value, sr)} / {format_sample_time(max_sample, sr)}")

    def seek_to_slider(self):
        self.player.seek_sample(int(self.seek_slider.value()))

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
            QMessageBox.warning(self, "缺少候选", "请先分析并选择一个 Loop 候选。")
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

    def save_current_loop_profile(self):
        path = self.current_loop_audio
        if not path:
            return
        try:
            key = track_key_for_path(path)
            profile = self.store.load_track_profile(key)
            info = read_wav_info(path) if path.suffix.lower() in (".wav", ".wave") else None
            markers = {name: int(spin.value()) for name, spin in self.marker_spins.items() if spin.value() >= 0}
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


    def _find_audio_path_for_marker_row(self, row: MarkerImportRow, sample_length_index: dict[int, list[Path]]) -> Path | None:
        """Match an import row to a scanned audio file.

        Matching priority:
        1) Filename / MatchName / DisplayName against current music file stem.
        2) SampleLength when it uniquely identifies one scanned WAV file.
        """
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
        if not self.audio_paths:
            QMessageBox.warning(self, "缺少音乐", "请先选择并扫描音乐目录，然后再导入 Marker 参数。")
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
                try:
                    info = read_wav_info(path) if path.suffix.lower() in (".wav", ".wave") else None
                except Exception:
                    info = None
                display, artist = guess_display_artist_from_filename(path.name)
                profile = old or TrackProfile(key, str(path), path.name, display, artist)
                profile = replace(
                    profile,
                    source_path=str(path),
                    filename=path.name,
                    display_name=row.display_name or profile.display_name or display,
                    artist=row.artist or profile.artist or artist,
                    sample_rate=info.samplerate if info else (row.sample_rate or profile.sample_rate),
                    sample_length=info.sample_length if info else (row.sample_length or profile.sample_length),
                    markers={name: int(row.markers.get(name, -1)) for name in MARKER_ORDER},
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
            QMessageBox.information(self, "导入完成", msg)
        except Exception as exc:
            self.show_error("导入 Marker 参数失败", exc)

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
            rows = []
            for path in self.audio_paths:
                row = {col: "" for col in IMPORT_COLUMNS}
                row["MatchName"] = path.stem
                row["Filename"] = path.name
                row["DisplayName"] = path.stem
                try:
                    if path.suffix.lower() in (".wav", ".wave"):
                        info = read_wav_info(path)
                        row["SampleRate"] = info.samplerate
                        row["SampleLength"] = info.sample_length
                        row["TrackStart"] = 0
                        row["End"] = max(0, info.sample_length - 1)
                except Exception:
                    pass
                rows.append(row)
            write_marker_import_template(Path(file_path), rows)
            QMessageBox.information(self, "已导出 Marker", f"已导出 Marker：\n{file_path}")
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
        self.store.set_setting("fmod_auto_click", bool(self.auto_fmod_click_check.isChecked()))
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
        """Tokens used to keep FMOD work scoped to the selected radio station."""
        tokens: list[str] = []
        try:
            st = self._current_station_info()
            if st.number:
                raw = str(st.number).strip()
                tokens.append(f"r{raw.lower().lstrip('r')}")
            name_l = (st.name or "").lower()
            for part in name_l.replace('-', '_').replace(' ', '_').split('_'):
                if part.startswith('r') and any(ch.isdigit() for ch in part):
                    tokens.append(part)
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
                    tokens.append(part)
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
        preferred = self._preferred_cu1_assets_bank_for_station(bank_root, tokens)
        if preferred is not None:
            if bank_contains_fsb_audio(preferred):
                self.log("[FMOD] 已优先选择当前电台的单个 CU1 音频 bank：" + preferred.name)
            else:
                self.log(
                    "[FMOD][WARN] 找到当前电台 CU1 bank，但预检查显示 Fmod Bank Tools 可能无法从中提取 FSB："
                    + bank_preflight_message(preferred)
                    + "。准备阶段会自动寻找同电台可提取音频 bank。"
                )
            return [preferred]

        if not paths:
            raise ValueError("没有找到可用于 Extract/Rebuild 的 Track bank。请检查游戏根目录、media/Audio/FMODBanks 或 RadioInfo XML 中的 bank 名称。")

        scoped = [p for p in paths if self._bank_matches_station_tokens(p, tokens)]
        if scoped:
            if len(scoped) != len(paths):
                self.log("[FMOD] 已按当前电台过滤 bank，仅处理：" + ", ".join(p.name for p in scoped))
            # As a safety net, avoid extracting many same-station banks when one
            # obvious Track/CU1/assets bank is present in the XML list.
            cu1 = [p for p in scoped if "tracks" in p.name.lower() and "cu1" in p.name.lower() and "assets" in p.name.lower()]
            if cu1:
                self.log("[FMOD] XML bank 列表中找到 CU1 音频 bank，仅处理：" + cu1[0].name)
                return [cu1[0]]
            return scoped

        # If XML bank list contains no station token, keep the original set but make it visible.
        self.log("[FMOD][WARN] 未能从 bank 文件名中匹配当前电台 token，保留 XML 声明的 Track bank。")
        return paths

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
        return prepare_extract_job(tool, banks, manifest, search_root=bank_root, preferred_tokens=station_tokens)

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
            res = prepare_extract_job(tool, banks, manifest, search_root=bank_root, preferred_tokens=station_tokens)
            if not res.ok:
                raise RuntimeError(res.message)
            report(100, "Fmod Extract 准备完成。")
            return res

        def done(res):
            self.log(f"[FMOD][EXTRACT] {res.message}\nmanifest: {res.manifest_path}")
            if res.layout:
                self.log(f"[FMOD][EXTRACT] bank dir: {res.layout.bank_dir}\nconfig: {res.layout.config_path}")
            QMessageBox.information(self, "Extract 已准备", "已复制 bank 并写入 Fmod Bank Tools config.ini。\n现在可以在 Fmod Bank Tools 中点击 Extract，或使用“启动并尝试 Extract”。")

        self.run_background_task("准备 Fmod Extract", job, done, estimated="复制 bank 文件，通常数秒；大 bank/机械硬盘可能更久。")

    def run_fmod_extract(self):
        try:
            tool = self._current_fmod_tool_path()
            bank_root = self._current_bank_root()
            banks = self._selected_bank_paths_for_current_station()
            station_tokens = self._station_bank_tokens()
            manifest = self._fmod_auto_dir() / "extract_manifest.json"
            auto_click = bool(self.auto_fmod_click_check.isChecked())
        except Exception as exc:
            self.show_error("启动 Fmod Extract 失败", exc)
            return

        def job(report):
            report(5, "[FMOD][EXTRACT] 准备 bank 和 config.ini。")
            prep = prepare_extract_job(tool, banks, manifest, search_root=bank_root, preferred_tokens=station_tokens)
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
            QMessageBox.information(self, "Fmod Extract 已启动", res.message)

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
        return prepare_rebuild_job(tool, wav_workspace, manifest)

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
            res = prepare_rebuild_job(tool, wav_workspace, manifest)
            report(100, "Fmod Rebuild 准备完成。")
            return res

        def done(res):
            self.log(f"[FMOD][REBUILD] {res.message}\nmanifest: {res.manifest_path}")
            if res.layout:
                self.log(f"[FMOD][REBUILD] wav dir: {res.layout.wav_dir}\nbuild dir: {res.layout.rebuild_dir}\nconfig: {res.layout.config_path}")
            QMessageBox.information(self, "Rebuild 已准备", "已复制 fmod_ready_wav 到 Fmod Bank Tools 的 wav 目录并写入 config.ini。\n现在可以在 Fmod Bank Tools 中点击 Rebuild，或使用“启动并尝试 Rebuild”。")

        self.run_background_task("准备 Fmod Rebuild", job, done, estimated="复制 wav 工作区，通常数秒到数十秒。")

    def run_fmod_rebuild(self):
        try:
            tool = self._current_fmod_tool_path()
            wav_workspace = self._fmod_rebuild_workspace()
            manifest = self._fmod_auto_dir() / "rebuild_manifest.json"
            auto_click = bool(self.auto_fmod_click_check.isChecked())
        except Exception as exc:
            self.show_error("启动 Fmod Rebuild 失败", exc)
            return

        def job(report):
            report(5, "[FMOD][REBUILD] 准备 wav 工作区和 config.ini。")
            prep = prepare_rebuild_job(tool, wav_workspace, manifest)
            report(55, prep.message)
            res = launch_and_optionally_trigger(tool, "rebuild", auto_trigger=auto_click)
            report(100, res.message)
            return prep, res

        def done(result):
            prep, res = result
            self.log(f"[FMOD][REBUILD] {prep.message}")
            self.log(f"[FMOD][REBUILD] {res.message}")
            QMessageBox.information(self, "Fmod Rebuild 已启动", res.message)

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
                QMessageBox.information(self, "收集完成", f"已收集 {len(files)} 个 .bank 到：\n{output_dir2}")
            else:
                self.log(f"[FMOD][COLLECT][WARN] 未在 {layout2.rebuild_dir} 找到 .bank 输出。")
                QMessageBox.warning(self, "没有输出", f"未在 Fmod Bank Tools build 目录找到 .bank：\n{layout2.rebuild_dir}")

        self.run_background_task("收集 Fmod Rebuild 输出", job, done, estimated="复制 bank 文件，通常数秒。")

    def generate_bank_tool_plan(self):
        if not self.current_xml:
            QMessageBox.warning(self, "缺少 XML", "请先选择或扫描 RadioInfo XML。")
            return
        station = self.current_station_name()
        if not station:
            QMessageBox.warning(self, "缺少电台", "请先选择目标电台。")
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
            QMessageBox.information(self, "Bank 命令预览已生成", f"已生成：\n{txt_path}\n{bat_path}")
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

    def _prepare_audio_for_generation(self, source: Path, slot_index: int, dst_dir: Path) -> AudioInfo:
        dst_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_stem(f"slot_{slot_index:02d}_{source.stem}", 90)
        dst = dst_dir / f"{stem}.wav"
        validation = validate_wav(source)
        if validation.ok and validation.info is not None:
            shutil.copy2(source, dst)
        else:
            ffmpeg = find_ffmpeg(None)
            run_ffmpeg_normalize(source, dst, ffmpeg)
        info = read_wav_info(dst)
        return AudioInfo(
            path=dst,
            filename=dst.name,
            samplerate=info.samplerate,
            channels=info.channels,
            bits_per_sample=info.bits_per_sample,
            frames=info.frames,
            duration_sec=info.duration_sec,
        )

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
        slot_to_profile_and_audio: dict[int, tuple[TrackProfile, AudioInfo]] = {}

        total = max(1, len(assignments))
        for idx, (slot, key) in enumerate(sorted(assignments.items()), start=1):
            profile = profiles_by_key.get(key)
            if not profile:
                raise ValueError(f"slot {slot} 的 profile 丢失: {key}")
            report(15 + int(25 * idx / total), f"[V2] 准备音频 {idx}/{total}: slot {slot} <- {Path(profile.source_path).name}")
            info = self._prepare_audio_for_generation(Path(profile.source_path), slot, prepared_dir)
            audio_by_filename[info.filename] = info
            marker_data = dict(profile.markers or {})
            marker_data.setdefault("TrackStart", 0)
            marker_data.setdefault("End", max(0, info.sample_length - 1))
            markers_by_filename[info.filename] = markers_from_json(marker_data, info)
            slot_to_profile_and_audio[slot] = (profile, info)

        report(45, "[V2] 构造精确 slot 映射。")
        track_order_path = work / TRACK_ORDER_FILE_NAME
        extract_template = work / FMOD_EXTRACT_TEMPLATE_DIR_NAME
        ensure_track_order_file(track_order_path, current_xml, station, [], None, extract_template if extract_template.exists() else None)
        rows = read_track_order(track_order_path)
        patched_rows = []
        for row in rows:
            if row.slot_index in slot_to_profile_and_audio:
                profile, info = slot_to_profile_and_audio[row.slot_index]
                display = profile.display_name or Path(profile.filename).stem
                artist = profile.artist or "User"
                row = replace(row, audio_filename=info.filename, display_name=display, artist=artist, notes=(row.notes + " | " if row.notes else "") + "v2_manual_assignment")
            patched_rows.append(row)
        write_track_order(track_order_path, patched_rows)

        report(55, "[V2] 写入 XML。")
        output_xml = out / current_xml.name
        _patch_xml_by_track_order(current_xml, station, output_xml, patched_rows, audio_by_filename, markers_by_filename)

        fmod_ready = out / FMOD_READY_WAV_DIR_NAME
        if extract_template.exists():
            report(70, "[V2] 生成 fmod_ready_wav 并匹配音量；这一步可能耗时较长。")
            create_fmod_rebuild_workspace(out, extract_template, patched_rows, audio_by_filename, progress_callback=report)
            fmod_ready = out / FMOD_READY_WAV_DIR_NAME
        else:
            raise FileNotFoundError("未找到 Extract 模板；一键替换需要先成功 Extract 并导入模板。")

        manifest_path = store.export_manifest(work / "v2_project_manifest.json")
        summary = {
            "version": APP_VERSION,
            "station": station,
            "xml": str(output_xml),
            "assignedSlots": sorted(assignments.keys()),
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

    def _deploy_rebuilt_outputs_to_game(self, rebuilt_bank_dir: Path, original_bank_paths: list[Path], output_xml: Path, original_xml: Path, report) -> dict:
        """Backup original game files, then copy patched XML and rebuilt banks over them."""
        rebuilt_bank_dir = Path(rebuilt_bank_dir)
        output_xml = Path(output_xml)
        original_xml = Path(original_xml)
        if not output_xml.exists():
            raise FileNotFoundError(f"待部署 XML 不存在: {output_xml}")
        expected = {p.name: p for p in original_bank_paths}
        replacements: list[tuple[Path, Path]] = [(output_xml, original_xml)]
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
        report(94, "[DEPLOY] 正在备份原始 XML 和 bank 文件。")
        snapshot = create_backup_snapshot(targets, project_backup_dir(), label="one_click_replace")

        deployed = []
        for src, dst in replacements:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            deployed.append(str(dst))

        manifest = project_output_dir() / "one_click_replace_manifest.json"
        data = {
            "version": APP_VERSION,
            "createdAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "backupManifest": snapshot.manifest_path,
            "rebuiltBankDir": str(rebuilt_bank_dir),
            "deployedFiles": deployed,
        }
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def generate_mod_output_package(self):
        """Full Extract -> build -> Rebuild pipeline, but do not overwrite game files."""
        if not self.current_xml:
            QMessageBox.warning(self, "缺少 XML", "请先选择或扫描 RadioInfo XML。")
            return
        station = self.current_station_name()
        if not station:
            QMessageBox.warning(self, "缺少电台", "请先选择目标电台。")
            return
        assignments = self.store.get_assignments(station)
        if not assignments:
            QMessageBox.warning(self, "没有分配", "请至少给一个 Slot 分配新音乐。")
            return
        try:
            tool = self._current_fmod_tool_path()
            bank_root = self._current_bank_root()
            banks = self._selected_bank_paths_for_current_station()
            station_tokens = self._station_bank_tokens()
            auto_click = bool(self.auto_fmod_click_check.isChecked())
            current_xml = Path(self.current_xml)
            db_path = self.store.db_path
        except Exception as exc:
            self.show_error("生成 Mod 输出包前置检查失败", exc)
            return

        if not auto_click or not is_pywinauto_available():
            QMessageBox.warning(
                self,
                "需要自动控制组件",
                "生成完整 Mod 输出包需要自动控制 Fmod Bank Tools。请先运行“安装环境.bat”安装 pywinauto，\n"
                "并保持“自动控制 Fmod Bank Tools”处于勾选状态。",
            )
            return

        bank_list = "\n".join(f"- {p}" for p in banks)
        answer = QMessageBox.question(
            self,
            "确认仅生成输出包",
            "即将执行：Extract → 生成 XML/音频 → Rebuild → 收集到 output。\n\n"
            f"目标 XML：\n{current_xml}\n\n目标 bank：\n{bank_list}\n\n"
            "此模式不会覆盖游戏文件，只会把修改后的 XML 和重打包 bank 放到 output 目录。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def job(report):
            out, bak, work = ensure_project_dirs()
            fmod_dir = self._fmod_auto_dir()

            report(2, "[PACKAGE] 准备 Fmod Extract 工作目录。")
            extract_manifest = fmod_dir / "package_extract_manifest.json"
            prep_extract = prepare_extract_job(tool, banks, extract_manifest, search_root=bank_root, preferred_tokens=station_tokens)
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
                    "\n已停止生成输出包。请确认 pywinauto 已安装，并且 Fmod Bank Tools 可以正常 Extract。"
                )

            report(28, "[PACKAGE] 导入刚刚 Extract 得到的 wav/txt 模板。")
            template = import_fmod_extract_folder(prep_extract.layout.wav_dir, work)

            report(36, "[PACKAGE] 生成 patched XML 与 fmod_ready_wav。")
            out_dir, xml_backup, fmod_ready, output_xml = self._build_v2_outputs_sync(
                current_xml, station, assignments, db_path, report, backup_label="package_xml_stage"
            )

            report(84, "[PACKAGE] 准备 Fmod Rebuild 工作目录。")
            rebuild_manifest = fmod_dir / "package_rebuild_manifest.json"
            prep_rebuild = prepare_rebuild_job(tool, fmod_ready, rebuild_manifest)
            if not prep_rebuild.layout:
                raise RuntimeError("Fmod Rebuild 工作目录准备失败。")

            report(88, "[PACKAGE] 启动 Fmod Bank Tools 并执行 Rebuild。")
            rebuild_res = launch_trigger_and_wait(
                tool,
                "rebuild",
                auto_trigger=auto_click,
                expected_bank_names=actual_bank_names,
                timeout_sec=1200,
            )
            if not rebuild_res.ok:
                raise RuntimeError(
                    "Fmod Rebuild 未完成。" + rebuild_res.message +
                    "\n已停止生成输出包。请检查 Fmod Bank Tools build 目录和日志。"
                )

            report(94, "[PACKAGE] 收集 Rebuild 输出到 output。")
            rebuilt_out = out / "fmod_rebuilt_banks"
            rebuilt_files = fmod_collect_rebuilt_banks(prep_rebuild.layout, rebuilt_out, clean_output_dir=True)
            if not rebuilt_files:
                raise RuntimeError("Fmod Rebuild build 目录没有生成 .bank，已停止生成输出包。")

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
            QMessageBox.information(
                self,
                "输出包生成完成",
                "已完成 Extract、生成和 Rebuild。没有覆盖游戏文件。\n\n"
                f"修改后的 XML：\n{result['xml']}\n\n"
                f"重打包 bank：\n{result['rebuiltBankDir']}\n\n"
                f"manifest：\n{result['packageManifest']}",
            )

        self.run_background_task(
            "生成 Mod 输出包",
            job,
            done,
            estimated="完整流程取决于 bank 大小、歌曲数量和 Fmod Rebuild 速度，通常数分钟；大电台可能 10 分钟以上。",
        )

    def one_click_replace_game_files(self):
        if not self.current_xml:
            QMessageBox.warning(self, "缺少 XML", "请先选择或扫描 RadioInfo XML。")
            return
        station = self.current_station_name()
        if not station:
            QMessageBox.warning(self, "缺少电台", "请先选择目标电台。")
            return
        assignments = self.store.get_assignments(station)
        if not assignments:
            QMessageBox.warning(self, "没有分配", "请至少给一个 Slot 分配新音乐。")
            return
        try:
            tool = self._current_fmod_tool_path()
            bank_root = self._current_bank_root()
            banks = self._selected_bank_paths_for_current_station()
            station_tokens = self._station_bank_tokens()
            auto_click = bool(self.auto_fmod_click_check.isChecked())
            current_xml = Path(self.current_xml)
            db_path = self.store.db_path
        except Exception as exc:
            self.show_error("一键替换前置检查失败", exc)
            return

        if not auto_click or not is_pywinauto_available():
            QMessageBox.warning(
                self,
                "需要自动控制组件",
                "一键替换需要自动控制 Fmod Bank Tools。请先运行“安装环境.bat”安装 pywinauto，\n"
                "并保持“自动控制 Fmod Bank Tools”处于勾选状态。这样可以避免外部工具停在 No bank files found 或等待状态。",
            )
            return

        bank_list = "\n".join(f"- {p}" for p in banks)
        answer = QMessageBox.question(
            self,
            "确认一键替换游戏文件",
            "即将执行完整流程：Extract → 生成 XML/音频 → Rebuild → 备份 → 覆盖游戏文件。\n\n"
            f"目标 XML：\n{current_xml}\n\n目标 bank：\n{bank_list}\n\n"
            "工具会先备份原始文件；如果 Rebuild 输出不完整，将不会覆盖游戏文件。\n是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def job(report):
            out, bak, work = ensure_project_dirs()
            fmod_dir = self._fmod_auto_dir()

            report(2, "[ONE-CLICK] 准备 Fmod Extract 工作目录。")
            extract_manifest = fmod_dir / "one_click_extract_manifest.json"
            prep_extract = prepare_extract_job(tool, banks, extract_manifest, search_root=bank_root, preferred_tokens=station_tokens)
            if not prep_extract.layout or not prep_extract.ok:
                raise RuntimeError(prep_extract.message or "Fmod Extract 工作目录准备失败。")
            actual_bank_names = [Path(p).name for p in (prep_extract.output_files or [])]
            if not actual_bank_names:
                raise RuntimeError("Fmod Extract 没有准备任何含音频的 bank，已停止。")
            prepared_in_tool = sorted(prep_extract.layout.bank_dir.glob("*.bank"))
            if not prepared_in_tool:
                raise RuntimeError(f"Fmod Bank Tools 的 bank 目录为空：{prep_extract.layout.bank_dir}。已停止启动 Extract，避免 No bank files found。")
            actual_game_banks = self._resolve_game_bank_paths_by_names(bank_root, actual_bank_names)
            report(6, "[ONE-CLICK] 实际处理音频 bank: " + ", ".join(actual_bank_names))
            report(7, f"[ONE-CLICK] Fmod Bank Tools 工作目录: {prep_extract.layout.root_dir}; bank 文件数={len(prepared_in_tool)}")

            report(8, "[ONE-CLICK] 启动 Fmod Bank Tools 并执行 Extract。")
            extract_res = launch_trigger_and_wait(tool, "extract", auto_trigger=auto_click, timeout_sec=900)
            if not extract_res.ok:
                raise RuntimeError(
                    "Fmod Extract 未完成。" + extract_res.message +
                    "\n若需要真正一键自动执行，请安装 pywinauto，或使用带 CLI 的 Fmod Bank Tools fork。"
                )

            report(28, "[ONE-CLICK] 导入刚刚 Extract 得到的 wav/txt 模板。")
            template = import_fmod_extract_folder(prep_extract.layout.wav_dir, work)

            report(36, "[ONE-CLICK] 生成 patched XML 与 fmod_ready_wav。")
            out_dir, xml_backup, fmod_ready, output_xml = self._build_v2_outputs_sync(
                current_xml, station, assignments, db_path, report, backup_label="one_click_xml_stage"
            )

            report(84, "[ONE-CLICK] 准备 Fmod Rebuild 工作目录。")
            rebuild_manifest = fmod_dir / "one_click_rebuild_manifest.json"
            prep_rebuild = prepare_rebuild_job(tool, fmod_ready, rebuild_manifest)
            if not prep_rebuild.layout:
                raise RuntimeError("Fmod Rebuild 工作目录准备失败。")

            report(88, "[ONE-CLICK] 启动 Fmod Bank Tools 并执行 Rebuild。")
            rebuild_res = launch_trigger_and_wait(
                tool,
                "rebuild",
                auto_trigger=auto_click,
                expected_bank_names=actual_bank_names,
                timeout_sec=1200,
            )
            if not rebuild_res.ok:
                raise RuntimeError(
                    "Fmod Rebuild 未完成。" + rebuild_res.message +
                    "\n已停止覆盖游戏文件。请检查 Fmod Bank Tools build 目录和日志。"
                )

            report(92, "[ONE-CLICK] 收集 Rebuild 输出。")
            rebuilt_out = out / "fmod_rebuilt_banks"
            rebuilt_files = fmod_collect_rebuilt_banks(prep_rebuild.layout, rebuilt_out, clean_output_dir=True)
            if not rebuilt_files:
                raise RuntimeError("Fmod Rebuild build 目录没有生成 .bank，已停止覆盖游戏文件。")

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
            QMessageBox.information(
                self,
                "一键替换完成",
                "已完成 Extract、生成、Rebuild、备份并覆盖游戏文件。\n"
                f"备份 manifest：\n{result['deploy']['backupManifest']}\n\n"
                "如果游戏中效果异常，可在本工具中使用“从备份 manifest 恢复”。",
            )

        self.run_background_task(
            "一键执行替换",
            job,
            done,
            estimated="完整流程取决于 bank 大小、歌曲数量和 Fmod Rebuild 速度，通常数分钟；大电台可能 10 分钟以上。",
        )

    def generate_v2_outputs(self):
        if not self.current_xml:
            QMessageBox.warning(self, "缺少 XML", "请先选择或扫描 RadioInfo XML。")
            return
        station = self.current_station_name()
        if not station:
            QMessageBox.warning(self, "缺少电台", "请先选择目标电台。")
            return
        assignments = self.store.get_assignments(station)
        if not assignments:
            QMessageBox.warning(self, "没有分配", "请至少给一个 Slot 分配新音乐。")
            return

        current_xml = Path(self.current_xml)
        db_path = self.store.db_path

        def prepare_audio(source: Path, slot_index: int, dst_dir: Path) -> AudioInfo:
            dst_dir.mkdir(parents=True, exist_ok=True)
            stem = safe_stem(f"slot_{slot_index:02d}_{source.stem}", 90)
            dst = dst_dir / f"{stem}.wav"
            validation = validate_wav(source)
            if validation.ok and validation.info is not None:
                shutil.copy2(source, dst)
            else:
                ffmpeg = find_ffmpeg(None)
                run_ffmpeg_normalize(source, dst, ffmpeg)
            info = read_wav_info(dst)
            return AudioInfo(
                path=dst,
                filename=dst.name,
                samplerate=info.samplerate,
                channels=info.channels,
                bits_per_sample=info.bits_per_sample,
                frames=info.frames,
                duration_sec=info.duration_sec,
            )

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
            slot_to_profile_and_audio: dict[int, tuple[TrackProfile, AudioInfo]] = {}

            total = max(1, len(assignments))
            for idx, (slot, key) in enumerate(sorted(assignments.items()), start=1):
                profile = profiles_by_key.get(key)
                if not profile:
                    raise ValueError(f"slot {slot} 的 profile 丢失: {key}")
                report(15 + int(25 * idx / total), f"[V2] 准备音频 {idx}/{total}: slot {slot} <- {Path(profile.source_path).name}")
                info = prepare_audio(Path(profile.source_path), slot, prepared_dir)
                audio_by_filename[info.filename] = info
                marker_data = dict(profile.markers or {})
                marker_data.setdefault("TrackStart", 0)
                marker_data.setdefault("End", max(0, info.sample_length - 1))
                markers_by_filename[info.filename] = markers_from_json(marker_data, info)
                slot_to_profile_and_audio[slot] = (profile, info)

            report(45, "[V2] 构造精确 slot 映射。")
            track_order_path = work / TRACK_ORDER_FILE_NAME
            extract_template = work / FMOD_EXTRACT_TEMPLATE_DIR_NAME
            ensure_track_order_file(track_order_path, current_xml, station, [], None, extract_template if extract_template.exists() else None)
            rows = read_track_order(track_order_path)
            patched_rows = []
            for row in rows:
                if row.slot_index in slot_to_profile_and_audio:
                    profile, info = slot_to_profile_and_audio[row.slot_index]
                    display = profile.display_name or Path(profile.filename).stem
                    artist = profile.artist or "User"
                    row = replace(row, audio_filename=info.filename, display_name=display, artist=artist, notes=(row.notes + " | " if row.notes else "") + "v2_manual_assignment")
                patched_rows.append(row)
            write_track_order(track_order_path, patched_rows)

            report(55, "[V2] 写入 XML。")
            output_xml = out / current_xml.name
            _patch_xml_by_track_order(current_xml, station, output_xml, patched_rows, audio_by_filename, markers_by_filename)

            fmod_ready = out / FMOD_READY_WAV_DIR_NAME
            if extract_template.exists():
                report(70, "[V2] 生成 fmod_ready_wav 并匹配音量；这一步可能耗时较长。")
                create_fmod_rebuild_workspace(out, extract_template, patched_rows, audio_by_filename, progress_callback=report)
            else:
                fmod_ready = out / FMOD_READY_WAV_DIR_NAME

            manifest_path = store.export_manifest(work / "v2_project_manifest.json")
            summary = {
                "version": APP_VERSION,
                "station": station,
                "xml": str(output_xml),
                "assignedSlots": sorted(assignments.keys()),
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
            QMessageBox.information(self, "生成完成", f"v2 输出已生成：\n{out}")

        self.run_background_task("生成 v2 输出包", job, done, estimated="取决于替换歌曲数量、转码和音量匹配；通常几十秒，批量替换可能数分钟。")

    def export_manifest(self):
        try:
            path = self.store.export_manifest(project_work_dir() / "v2_project_manifest.json")
            self.log(f"[OK] manifest 已导出: {path}")
        except Exception as exc:
            self.show_error("导出 manifest 失败", exc)

    def backup_current_game_files(self):
        """Create a manual backup snapshot for the current XML and selected station banks."""
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
                QMessageBox.warning(self, "没有可备份文件", "请先扫描游戏目录并加载 RadioInfo XML。")
                return
            snapshot = create_backup_snapshot(files, project_backup_dir(), label="manual_game_backup")
            self.log(f"[BACKUP][OK] 已备份 {len(files)} 个文件：{snapshot.manifest_path}")
            QMessageBox.information(self, "备份完成", f"已备份 {len(files)} 个文件。\nmanifest：\n{snapshot.manifest_path}")
        except Exception as exc:
            self.show_error("备份当前游戏文件失败", exc)

    def restore_from_manifest(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 backup_manifest.json", str(project_backup_dir()), "JSON (*.json);;All files (*.*)")
        if not p:
            return
        try:
            restored = restore_snapshot(Path(p))
            self.log("[OK] 已恢复文件:\n" + "\n".join(restored))
            QMessageBox.information(self, "恢复完成", f"已恢复 {len(restored)} 个文件。")
        except Exception as exc:
            self.show_error("恢复失败", exc)

    def show_error(self, title: str, exc: Exception):
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.log(f"[ERROR] {title}: {exc}\n{detail}")
        QMessageBox.critical(self, title, str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
