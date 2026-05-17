from __future__ import annotations

import sys
import traceback
import json
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QSlider,
    QSpinBox, QDoubleSpinBox, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, QWidget, QSizePolicy
)

from .core import InvalidAudioError
from .models import SegmentMarkers
from .project_tools import prepare_project_outputs, regenerate_xml_only, project_backup_dir, project_output_dir, project_work_dir
from .segment_tools import (
    MARKER_DESCRIPTIONS, MARKER_ORDER, SEGMENTS_FILE_NAME, load_segments,
    markers_from_json, markers_to_json, sample_to_seconds, save_audio_markers,
    seconds_to_sample,
)
from .simple_tools import infer_station_name
from .metadata_tools import METADATA_FILE_NAME, ensure_metadata_file_for_music_dir_to_path
from .order_tools import FMOD_EXTRACT_TEMPLATE_DIR_NAME, FMOD_REBUILD_WORKSPACE_DIR_NAME, FMOD_READY_WAV_DIR_NAME, TRACK_ORDER_FILE_NAME, import_fmod_extract_folder
from .wav_tools import list_audio_candidates, natural_key, read_wav_info, validate_wav
from .wav_preview_player import WavPreviewPlayer
from .xml_tools import list_station_infos, parse_xml



class PrepareWorker(QObject):
    finished = Signal(object)
    invalid_audio = Signal(object)
    failed = Signal(object)
    progress = Signal(int, str)

    def __init__(self, xml: Path, music: Path, station: str):
        super().__init__()
        self.xml = Path(xml)
        self.music = Path(music)
        self.station = station

    def run(self):
        try:
            def report(value: int, message: str):
                self.progress.emit(int(value), str(message))

            report(1, "Starting generation...")
            result = prepare_project_outputs(
                self.xml,
                self.music,
                self.station,
                progress_callback=report,
            )
            self.finished.emit(result)
        except InvalidAudioError as exc:
            self.invalid_audio.emit(exc)
        except Exception as exc:
            self.failed.emit(exc)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FH6 Radio Tool v3.7.4 - Bilingual UI")
        self.resize(1280, 940)

        self.station_infos = []
        self.current_audio_info = None
        self.marker_spins: dict[str, QSpinBox] = {}
        self.lang = "zh"
        self.marker_desc_labels: dict[str, QLabel] = {}
        self.prepare_thread: QThread | None = None
        self.prepare_worker: PrepareWorker | None = None
        self.is_generating = False
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(1000)
        self.progress_timer.timeout.connect(self.on_generate_heartbeat)
        self.progress_elapsed = 0
        self.current_progress_message = ""

        # v3.6.4:
        # Use a custom PCM16 WAV preview player instead of QMediaPlayer.
        # QMediaPlayer may end/seek WAV playback early on some Windows backends,
        # which breaks marker editing.  WavPreviewPlayer streams real WAV frames
        # and reports positions directly in sample units.
        self.player = WavPreviewPlayer(self)

        self.xml_edit = QLineEdit()
        self.music_dir_edit = QLineEdit()
        self.output_label = QLabel(f"输出目录：{project_output_dir()}    备份目录：{project_backup_dir()}")
        self.station_combo = QComboBox()
        self.station_summary_label = QLabel("目标电台：请先选择 XML")

        self.audio_table = QTableWidget(0, 8)
        self.audio_table.setHorizontalHeaderLabels(["文件名", "后续动作", "当前状态", "采样率", "声道", "位深", "时长(s)", "问题"])
        self.audio_table.horizontalHeader().setStretchLastSection(True)

        self.calib_audio_combo = QComboBox()
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_label = QLabel("0.000s / sample 0")
        self.duration_label = QLabel("duration: -")
        self.sample_rate_label = QLabel("sample rate: -")
        self.marker_target_combo = QComboBox()
        self.marker_target_combo.addItems(MARKER_ORDER)

        self.sample_spin = QSpinBox()
        self.sample_spin.setRange(0, 2_147_483_647)
        self.seconds_spin = QDoubleSpinBox()
        self.seconds_spin.setRange(0, 100000)
        self.seconds_spin.setDecimals(6)
        self.seconds_spin.setSingleStep(0.1)

        self.guide_text = QPlainTextEdit()
        self.guide_text.setReadOnly(True)
        self.guide_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)

        self._build_layout()
        self.player.positionChanged.connect(self.on_player_position_changed)
        self.load_session()


    def txt(self, zh: str, en: str) -> str:
        return en if getattr(self, "lang", "zh") == "en" else zh

    def set_language_from_combo(self, index: int):
        self.lang = "en" if index == 1 else "zh"
        self.apply_language()
        self.save_session()

    def apply_language(self):
        """Refresh visible UI text after switching language."""
        self.setWindowTitle(self.txt(
            "FH6 Radio Tool v3.7.4 - 双语界面",
            "FH6 Radio Tool v3.7.4 - Bilingual UI",
        ))

        if hasattr(self, "sidebar"):
            self.sidebar.setTitle(self.txt("操作向导", "Guide"))
        if hasattr(self, "path_box"):
            self.path_box.setTitle(self.txt("1. 选择文件", "1. Select files"))
        if hasattr(self, "station_box"):
            self.station_box.setTitle(self.txt("2. 准备", "2. Prepare"))
        if hasattr(self, "mismatch_box"):
            self.mismatch_box.setTitle(self.txt("3. 校准与生成", "3. Mapping and generation"))
        if hasattr(self, "calib_box"):
            self.calib_box.setTitle(self.txt("5. 试听与段落设置", "5. Preview and markers"))

        widget_texts = {
            "language_label": ("语言 / Language:", "Language / 语言:"),
            "music_dir_label": ("音乐文件夹:", "Music folder:"),
            "station_label": ("目标电台:", "Target station:"),
            "audio_result_label": ("4. 音频校验结果", "4. Audio validation"),
            "preview_audio_label": ("试听音频:", "Preview audio:"),
            "write_current_label": ("当前位置写入:", "Write current position to:"),
            "seconds_label": ("秒数:", "Seconds:"),
            "sample_label": ("采样点:", "Sample:"),
            "log_title_label": ("6. 日志", "6. Log"),
        }
        for attr, (zh, en) in widget_texts.items():
            if hasattr(self, attr):
                getattr(self, attr).setText(self.txt(zh, en))

        button_texts = {
            "btn_xml": ("选择 XML", "Select XML"),
            "btn_music": ("选择音乐文件夹", "Select music folder"),
            "btn_auto": ("自动匹配电台", "Auto-select station"),
            "btn_validate": ("校验音频", "Validate audio"),
            "btn_metadata": ("① 歌名表", "① Metadata CSV"),
            "btn_import_extract": ("② 导入 Extract", "② Import Extract"),
            "btn_order": ("查看映射", "View mapping"),
            "btn_write": ("③ 最终生成", "③ Generate"),
            "btn_refresh": ("刷新可试听 WAV", "Refresh WAV list"),
            "btn_play": ("播放", "Play"),
            "btn_pause": ("暂停", "Pause"),
            "btn_stop": ("停止", "Stop"),
            "btn_set": ("写入 Marker", "Set marker"),
            "btn_save": ("保存段落设置", "Save markers"),
            "btn_save_write": ("保存并重写 XML", "Save and rewrite XML"),
            "btn_sec_to_sample": ("秒 → 采样点", "Seconds → Sample"),
            "btn_sample_to_sec": ("采样点 → 秒", "Sample → Seconds"),
        }
        for attr, (zh, en) in button_texts.items():
            if hasattr(self, attr):
                getattr(self, attr).setText(self.txt(zh, en))

        if hasattr(self, "mismatch_tip"):
            self.mismatch_tip.setText(self.txt(
                "导入 Extract 后，工具会自动完成映射、替换和音量匹配。",
                "After importing Extract output, the tool automatically handles mapping, replacement, and loudness matching.",
            ))
        if hasattr(self, "generate_progress_label") and not self.is_generating:
            self.generate_progress_label.setText(self.txt("等待生成。", "Ready to generate."))

        # Tooltips
        tips = {
            "btn_auto": (
                "根据音乐文件夹名尝试选择对应电台；选错时可手动改下拉框。",
                "Try to select the station from the music folder name. You can still change it manually.",
            ),
            "btn_validate": (
                "检查采样率、声道、位深；不合规音频最终会自动规范化为 *_fh6_norm.wav。",
                "Check sample rate, channels, and bit depth. Invalid files will be normalized when generating.",
            ),
            "btn_metadata": (
                "生成 output/track_metadata.csv。",
                "Create output/track_metadata.csv.",
            ),
            "btn_import_extract": (
                "选择 Fmod Bank Tools 的 Wav Output Directory，生成已替换且音量匹配的 fmod_ready_wav。",
                "Select the Wav Output Directory from Fmod Bank Tools Extract. The tool will create fmod_ready_wav.",
            ),
            "btn_order": (
                "查看 work/track_order.csv。通常只用于确认；自动校准失败时才手动修正。",
                "View work/track_order.csv. Usually only needed for debugging.",
            ),
            "btn_write": (
                "生成 XML、fmod_ready_wav 和说明。",
                "Generate XML and fmod_ready_wav.",
            ),
        }
        for attr, (zh, en) in tips.items():
            if hasattr(self, attr):
                getattr(self, attr).setToolTip(self.txt(zh, en))

        self.audio_table.setHorizontalHeaderLabels(
            self.txt(
                ["文件名", "后续动作", "当前状态", "采样率", "声道", "位深", "时长(s)", "问题"],
                ["Filename", "Action", "Status", "Sample rate", "Channels", "Bit depth", "Duration(s)", "Issues"],
            )
        )

        marker_desc_en = {
            "TrackStart": "Song start",
            "TrackDrop": "Race high point",
            "TrackLoopStart": "Race loop start",
            "TrackLoopEnd": "Race loop end",
            "PostDrop": "Finish/post-race drop",
            "PostRaceLoopStart": "Post-race loop start",
            "PostRaceLoopEnd": "Post-race loop end",
            "DJSegment": "DJ insert point",
            "StingerStart": "Stinger start",
            "DJStart": "DJ start",
            "End": "Song end",
        }
        for name, label in self.marker_desc_labels.items():
            label.setText(self.txt(MARKER_DESCRIPTIONS.get(name, ""), marker_desc_en.get(name, name)))
            if name in self.marker_spins:
                self.marker_spins[name].setToolTip(label.text())

        self.output_label.setText(self.txt(
            f"输出目录：{project_output_dir()}    备份目录：{project_backup_dir()}",
            f"Output: {project_output_dir()}    Backup: {project_backup_dir()}",
        ))

        self.update_guide()
        self.on_station_changed()


    def session_path(self) -> Path:
        return project_work_dir() / "ui_session.json"

    def save_session(self):
        try:
            project_work_dir().mkdir(parents=True, exist_ok=True)
            data = {
                "lang": self.lang,
                "xml": self.xml_edit.text().strip(),
                "music_dir": self.music_dir_edit.text().strip(),
                "station": self.station_combo.currentData() or "",
            }
            self.session_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def load_session(self):
        try:
            path = self.session_path()
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))

            lang = data.get("lang", "zh")
            if hasattr(self, "language_combo"):
                self.language_combo.setCurrentIndex(1 if lang == "en" else 0)

            xml_text = data.get("xml") or ""
            music_text = data.get("music_dir") or ""
            station = data.get("station") or ""

            if xml_text and Path(xml_text).exists():
                self.xml_edit.setText(xml_text)
                self.load_xml(Path(xml_text))

            if music_text and Path(music_text).exists():
                self.music_dir_edit.setText(music_text)
                self.output_label.setText(self.txt(
                    f"输出目录：{project_output_dir()}    备份目录：{project_backup_dir()}",
                    f"Output: {project_output_dir()}    Backup: {project_backup_dir()}",
                ))
                self.refresh_calibration_audio_list()

            if station:
                for i in range(self.station_combo.count()):
                    if self.station_combo.itemData(i) == station:
                        self.station_combo.setCurrentIndex(i)
                        break

            self.update_guide("start")
            self.log(self.txt("[OK] 已恢复上次选择的路径", "[OK] Restored last selected paths"))
        except Exception as exc:
            self.log(self.txt(f"[WARN] 恢复上次会话失败: {exc}", f"[WARN] Failed to restore previous session: {exc}"))


    def set_generate_progress(self, value: int, message: str):
        value = max(0, min(100, int(value)))
        self.current_progress_message = message
        if hasattr(self, "generate_progress_bar"):
            self.generate_progress_bar.show()
            self.generate_progress_bar.setValue(value)
        if hasattr(self, "generate_progress_label"):
            self.generate_progress_label.show()
            self.generate_progress_label.setText(self.txt(
                f"正在生成：{message}",
                f"Generating: {message}",
            ))

    def on_generate_heartbeat(self):
        if not self.is_generating:
            return
        self.progress_elapsed += 1
        if hasattr(self, "generate_progress_bar"):
            current = self.generate_progress_bar.value()
            # Keep the bar animated without jumping ahead of real work too much.
            # Real per-track callbacks update the value during fmod_ready_wav creation.
            if current < 88 and self.progress_elapsed % 5 == 0:
                self.generate_progress_bar.setValue(current + 1)
        if hasattr(self, "generate_progress_label"):
            dots = "." * ((self.progress_elapsed % 3) + 1)
            msg = self.current_progress_message or self.txt("后台任务仍在工作", "Background task is still running")
            self.generate_progress_label.setText(self.txt(
                f"正在生成：{msg}{dots} 已用时 {self.progress_elapsed}s",
                f"Generating: {msg}{dots} Elapsed {self.progress_elapsed}s",
            ))

    def reset_generate_progress(self):
        self.progress_elapsed = 0
        self.current_progress_message = ""
        if hasattr(self, "generate_progress_bar"):
            self.generate_progress_bar.setValue(0)
            self.generate_progress_bar.hide()
        if hasattr(self, "generate_progress_label"):
            self.generate_progress_label.hide()

    def set_busy(self, busy: bool):
        self.is_generating = busy
        for attr in [
            "btn_xml", "btn_music", "btn_auto", "btn_validate", "btn_metadata",
            "btn_import_extract", "btn_order", "btn_write", "btn_refresh",
            "btn_set", "btn_save", "btn_save_write",
        ]:
            if hasattr(self, attr):
                getattr(self, attr).setEnabled(not busy)
        if hasattr(self, "station_combo"):
            self.station_combo.setEnabled(not busy)
        if hasattr(self, "language_combo"):
            self.language_combo.setEnabled(not busy)

        if busy:
            self.progress_elapsed = 0
            self.set_generate_progress(1, self.txt("准备开始", "Starting"))
            self.progress_timer.start()
        else:
            self.progress_timer.stop()

        self.setWindowTitle(self.txt(
            "FH6 Radio Tool v3.7.4 - 正在生成..." if busy else "FH6 Radio Tool v3.7.4 - 双语界面",
            "FH6 Radio Tool v3.7.4 - Generating..." if busy else "FH6 Radio Tool v3.7.4 - Bilingual UI",
        ))

    def _build_layout(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)

        self.sidebar = QGroupBox()
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.addWidget(self.guide_text)
        self.sidebar.setMaximumWidth(430)
        self.sidebar.setMinimumWidth(370)
        root_layout.addWidget(self.sidebar, stretch=0)

        main_panel = QWidget()
        layout = QVBoxLayout(main_panel)
        root_layout.addWidget(main_panel, stretch=1)

        self.path_box = QGroupBox()
        grid = QGridLayout(self.path_box)

        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.addItems(["中文", "English"])
        self.language_combo.currentIndexChanged.connect(self.set_language_from_combo)
        grid.addWidget(self.language_label, 0, 0)
        grid.addWidget(self.language_combo, 0, 1)

        self.btn_xml = QPushButton()
        self.btn_xml.clicked.connect(self.choose_xml)
        grid.addWidget(QLabel("RadioInfo_CN.xml:"), 1, 0)
        grid.addWidget(self.xml_edit, 1, 1)
        grid.addWidget(self.btn_xml, 1, 2)

        self.music_dir_label = QLabel()
        self.btn_music = QPushButton()
        self.btn_music.clicked.connect(self.choose_music_dir)
        grid.addWidget(self.music_dir_label, 2, 0)
        grid.addWidget(self.music_dir_edit, 2, 1)
        grid.addWidget(self.btn_music, 2, 2)
        grid.addWidget(self.output_label, 3, 0, 1, 3)
        layout.addWidget(self.path_box)

        self.station_box = QGroupBox()
        sgrid = QGridLayout(self.station_box)
        self.station_combo.currentIndexChanged.connect(self.on_station_changed)
        self.station_label = QLabel()
        sgrid.addWidget(self.station_label, 0, 0)
        sgrid.addWidget(self.station_combo, 0, 1, 1, 4)
        sgrid.addWidget(self.station_summary_label, 1, 0, 1, 5)

        self.btn_auto = QPushButton()
        self.btn_auto.clicked.connect(self.auto_select_station)

        self.btn_validate = QPushButton()
        self.btn_validate.clicked.connect(self.validate_music_folder)

        self.btn_metadata = QPushButton()
        self.btn_metadata.clicked.connect(self.ensure_metadata_table)

        sgrid.addWidget(self.btn_auto, 2, 0, 1, 2)
        sgrid.addWidget(self.btn_validate, 2, 2)
        sgrid.addWidget(self.btn_metadata, 2, 3, 1, 2)
        layout.addWidget(self.station_box)

        self.mismatch_box = QGroupBox()
        mgrid = QGridLayout(self.mismatch_box)

        self.mismatch_tip = QLabel()
        self.mismatch_tip.setWordWrap(True)
        mgrid.addWidget(self.mismatch_tip, 0, 0, 1, 4)

        self.btn_import_extract = QPushButton()
        self.btn_import_extract.clicked.connect(self.import_fmod_extract_assets)

        self.btn_order = QPushButton()
        self.btn_order.clicked.connect(self.show_track_order_info)

        self.btn_write = QPushButton()
        self.btn_write.clicked.connect(self.run_prepare)

        mgrid.addWidget(self.btn_import_extract, 1, 0, 1, 2)
        mgrid.addWidget(self.btn_order, 1, 2)
        mgrid.addWidget(self.btn_write, 1, 3)

        self.generate_progress_label = QLabel()
        self.generate_progress_label.setWordWrap(True)
        self.generate_progress_bar = QProgressBar()
        self.generate_progress_bar.setRange(0, 100)
        self.generate_progress_bar.setValue(0)
        self.generate_progress_bar.setTextVisible(True)
        self.generate_progress_bar.hide()
        self.generate_progress_label.hide()
        mgrid.addWidget(self.generate_progress_label, 2, 0, 1, 4)
        mgrid.addWidget(self.generate_progress_bar, 3, 0, 1, 4)

        layout.addWidget(self.mismatch_box)

        self.audio_result_label = QLabel()
        layout.addWidget(self.audio_result_label)
        layout.addWidget(self.audio_table, stretch=1)

        self.calib_box = QGroupBox()
        cgrid = QGridLayout(self.calib_box)
        self.btn_refresh = QPushButton()
        self.btn_refresh.clicked.connect(self.refresh_calibration_audio_list)
        self.calib_audio_combo.currentIndexChanged.connect(self.on_calibration_audio_changed)
        self.preview_audio_label = QLabel()
        cgrid.addWidget(self.preview_audio_label, 0, 0)
        cgrid.addWidget(self.calib_audio_combo, 0, 1, 1, 4)
        cgrid.addWidget(self.btn_refresh, 0, 5)

        self.btn_play = QPushButton()
        self.btn_play.clicked.connect(self.play_audio)
        self.btn_pause = QPushButton()
        self.btn_pause.clicked.connect(self.player.pause)
        self.btn_stop = QPushButton()
        self.btn_stop.clicked.connect(self.player.stop)
        cgrid.addWidget(self.btn_play, 1, 0)
        cgrid.addWidget(self.btn_pause, 1, 1)
        cgrid.addWidget(self.btn_stop, 1, 2)
        cgrid.addWidget(self.position_label, 1, 3)
        cgrid.addWidget(self.duration_label, 1, 4)
        cgrid.addWidget(self.sample_rate_label, 1, 5)
        self.position_slider.sliderMoved.connect(self.seek_player)
        cgrid.addWidget(self.position_slider, 2, 0, 1, 6)

        row = 3
        for idx, name in enumerate(MARKER_ORDER):
            label = QLabel(name + ":")
            spin = QSpinBox()
            spin.setRange(-1, 2_147_483_647)
            self.marker_spins[name] = spin
            desc = QLabel()
            desc.setWordWrap(True)
            self.marker_desc_labels[name] = desc
            col = 0 if idx % 2 == 0 else 3
            if idx % 2 == 0 and idx != 0:
                row += 1
            cgrid.addWidget(label, row, col)
            cgrid.addWidget(spin, row, col + 1)
            cgrid.addWidget(desc, row, col + 2)

        row += 1
        self.write_current_label = QLabel()
        cgrid.addWidget(self.write_current_label, row, 0)
        cgrid.addWidget(self.marker_target_combo, row, 1)
        self.btn_set = QPushButton()
        self.btn_set.clicked.connect(self.set_marker_from_current_position)
        self.btn_save = QPushButton()
        self.btn_save.clicked.connect(self.save_current_segments)
        self.btn_save_write = QPushButton()
        self.btn_save_write.clicked.connect(self.save_segments_and_write_xml)
        cgrid.addWidget(self.btn_set, row, 2)
        cgrid.addWidget(self.btn_save, row, 3)
        cgrid.addWidget(self.btn_save_write, row, 4, 1, 2)

        row += 1
        self.seconds_label = QLabel()
        cgrid.addWidget(self.seconds_label, row, 0)
        cgrid.addWidget(self.seconds_spin, row, 1)
        self.btn_sec_to_sample = QPushButton()
        self.btn_sec_to_sample.clicked.connect(self.convert_seconds_to_sample)
        cgrid.addWidget(self.btn_sec_to_sample, row, 2)
        self.sample_label = QLabel()
        cgrid.addWidget(self.sample_label, row, 3)
        cgrid.addWidget(self.sample_spin, row, 4)
        self.btn_sample_to_sec = QPushButton()
        self.btn_sample_to_sec.clicked.connect(self.convert_sample_to_seconds)
        cgrid.addWidget(self.btn_sample_to_sec, row, 5)
        layout.addWidget(self.calib_box)

        self.log_title_label = QLabel()
        layout.addWidget(self.log_title_label)
        layout.addWidget(self.log_box, stretch=1)
        self.setCentralWidget(root)

        self.apply_language()

    def update_guide(self, stage: str = ""):
        xml_ok = bool(self.xml_edit.text().strip())
        music_ok = bool(self.music_dir_edit.text().strip())
        station_ok = self.station_combo.currentIndex() >= 0

        if self.lang == "en":
            status = [
                f"XML: {'✓ selected' if xml_ok else 'not selected'}",
                f"Music: {'✓ selected' if music_ok else 'not selected'}",
                f"Station: {'✓ selected' if station_ok else 'not selected'}",
            ]

            next_step = "Select RadioInfo_CN.xml"
            if xml_ok and not music_ok:
                next_step = "Select the music folder"
            elif xml_ok and music_ok and not station_ok:
                next_step = "Select target station"
            elif stage == "metadata":
                next_step = "Edit output/track_metadata.csv"
            elif stage == "import_extract":
                next_step = "Click ③ Generate"
            elif stage == "generated":
                next_step = "Rebuild bank with output/fmod_ready_wav"
            elif xml_ok and music_ok and station_ok:
                next_step = "Validate audio → ① Metadata CSV → ② Import Extract → ③ Generate"

            lines = [
                "Workflow",
                "1. Select RadioInfo_CN.xml",
                "2. Select your music folder",
                "3. Select station and validate audio",
                "4. Click ① Metadata CSV",
                "   Edit output/track_metadata.csv",
                "   Only edit display_name / artist",
                "5. Use Fmod Bank Tools to Extract the original bank",
                "6. Click ② Import Extract",
                "   Select Fmod WAV output directory",
                "7. Click ③ Generate",
                "",
                "After generation",
                "XML: output/RadioInfo_CN.xml",
                "WAV: output/fmod_ready_wav",
                "In Fmod Bank Tools:",
                "Wav Output Directory = fmod_ready_wav",
                "",
                "The tool automatically handles",
                "✓ SampleLength mapping",
                "✓ sound_x.wav parallel replacement",
                "✓ loudness matching",
                "",
                "Marker quick guide",
                "TrackStart: song start",
                "TrackDrop: race chorus/drop",
                "TrackLoopStart/End: race loop",
                "PostDrop: finish/post-race drop",
                "PostRaceLoopStart/End: post-race loop",
                "DJSegment / DJStart: DJ voice position",
                "StingerStart: short transition cue",
                "End: song end",
                "",
                "Preview note",
                "Slider uses real WAV sample positions.",
                "Preview uses the built-in WAV player.",
                "",
                "Tip",
                "Only set markers you are confident about.",
                "Loop end should return naturally to loop start.",
                "",
                "Current status",
                *status,
                "",
                f"Next step: {next_step}",
            ]
        else:
            status = [
                f"XML：{'✓ 已选' if xml_ok else '未选'}",
                f"音乐：{'✓ 已选' if music_ok else '未选'}",
                f"电台：{'✓ 已选' if station_ok else '未选'}",
            ]

            next_step = "选择 RadioInfo_CN.xml"
            if xml_ok and not music_ok:
                next_step = "选择音乐文件夹"
            elif xml_ok and music_ok and not station_ok:
                next_step = "选择目标电台"
            elif stage == "metadata":
                next_step = "编辑 output/track_metadata.csv"
            elif stage == "import_extract":
                next_step = "点击 ③ 最终生成"
            elif stage == "generated":
                next_step = "用 output/fmod_ready_wav 重构 bank"
            elif xml_ok and music_ok and station_ok:
                next_step = "校验音频 → ① 歌名表 → ② 导入 Extract → ③ 最终生成"

            lines = [
                "完整流程",
                "1. 选择 RadioInfo_CN.xml",
                "2. 选择音乐文件夹",
                "3. 选择电台并校验音频",
                "4. 点 ① 歌名表",
                "   编辑 output/track_metadata.csv",
                "   只改 display_name / artist",
                "5. 用 Fmod Bank Tools Extract 原 bank",
                "6. 点 ② 导入 Extract",
                "   选择 Fmod 的 wav 输出目录",
                "7. 点 ③ 最终生成",
                "",
                "生成后使用",
                "XML：output/RadioInfo_CN.xml",
                "WAV：output/fmod_ready_wav",
                "在 Fmod Bank Tools 中：",
                "Wav Output Directory 选 fmod_ready_wav",
                "",
                "工具会自动完成",
                "✓ SampleLength 映射",
                "✓ sound_x.wav 平行替换",
                "✓ 音量匹配",
                "",
                "段落字段速查",
                "TrackStart：歌曲正式开始",
                "TrackDrop：比赛中高潮/副歌",
                "TrackLoopStart/End：比赛中循环段",
                "PostDrop：冲线或赛后高潮",
                "PostRaceLoopStart/End：赛后循环段",
                "DJSegment / DJStart：DJ 语音位置",
                "StingerStart：短转场音效点",
                "End：歌曲结束点",
                "",
                "试听提示",
                "进度条按 WAV 真实采样点定位。",
                "试听使用内置 WAV 播放器。",
                "",
                "设置建议",
                "只填有把握的点；不确定就先保留默认。",
                "循环段应能自然从 End 接回 Start。",
                "",
                "当前状态",
                *status,
                "",
                f"下一步：{next_step}",
            ]

        self.guide_text.setPlainText("\n".join(lines))

    def log(self, text: str):
        self.log_box.appendPlainText(text)

    def xml_path(self) -> Path | None:
        text = self.xml_edit.text().strip()
        return Path(text) if text else None

    def music_dir(self) -> Path | None:
        text = self.music_dir_edit.text().strip()
        return Path(text) if text else None

    def segments_path(self) -> Path | None:
        # v2.6：所有脚本生成/编辑文件统一放在 output/。
        return project_output_dir() / SEGMENTS_FILE_NAME


    def choose_xml(self):
        path, _ = QFileDialog.getOpenFileName(self, self.txt("选择 RadioInfo_CN.xml", "Select RadioInfo_CN.xml"), "", "XML Files (*.xml);;All Files (*)")
        if path:
            self.xml_edit.setText(path)
            self.load_xml(Path(path))
            self.update_guide("xml")
            self.save_session()

    def choose_music_dir(self):
        path = QFileDialog.getExistingDirectory(self, self.txt("选择音乐文件夹", "Select music folder"))
        if path:
            self.music_dir_edit.setText(path)
            self.output_label.setText(self.txt(f"输出目录：{project_output_dir()}    备份目录：{project_backup_dir()}", f"Output: {project_output_dir()}    Backup: {project_backup_dir()}"))
            self.refresh_calibration_audio_list()
            self.auto_select_station(silent=True)
            self.update_guide("music")
            self.save_session()

    def load_xml(self, path: Path):
        try:
            tree = parse_xml(path)
            self.station_infos = list_station_infos(tree)
            self.station_combo.blockSignals(True)
            self.station_combo.clear()
            for info in self.station_infos:
                rates = ",".join(str(x) for x in info.sample_rates) or "unknown"
                self.station_combo.addItem(self.txt(f"{info.name} | 槽位 {info.track_slot_count} | SR {rates}", f"{info.name} | Slots {info.track_slot_count} | SR {rates}"), info.name)
            self.station_combo.blockSignals(False)
            self.log(f"[OK] 已加载 XML: {path}")
            self.log(f"[OK] 识别到 {len(self.station_infos)} 个电台")
            self.on_station_changed()
            self.auto_select_station(silent=True)
        except Exception as exc:
            self.show_error("加载 XML 失败", exc)

    def current_station_info(self):
        idx = self.station_combo.currentIndex()
        if idx < 0 or idx >= len(self.station_infos):
            return None
        return self.station_infos[idx]

    def on_station_changed(self):
        info = self.current_station_info()
        if info is None:
            self.station_summary_label.setText(self.txt("目标电台：请先选择 XML", "Target station: please select XML first"))
            return
        banks = ", ".join(info.banks)
        rates = ", ".join(str(x) for x in info.sample_rates) or "unknown"
        self.station_summary_label.setText(self.txt(
            f"电台：{info.name} | Track槽位={info.track_slot_count} | SampleRate={rates} | Banks=[{banks}]",
            f"Station: {info.name} | Track slots={info.track_slot_count} | SampleRate={rates} | Banks=[{banks}]",
        ))
        self.save_session()

    def auto_select_station(self, silent: bool = False):
        xml = self.xml_path()
        music = self.music_dir()
        if not xml or not music or not self.station_infos:
            return
        try:
            infer = infer_station_name(xml, music)
            for i in range(self.station_combo.count()):
                if self.station_combo.itemData(i) == infer.station_name:
                    self.station_combo.setCurrentIndex(i)
                    if not silent:
                        self.log(f"[OK] 自动选择电台: {infer.station_name} ({infer.reason})")
                    for w in infer.warnings:
                        self.log(f"[WARN] {w}")
                    return
        except Exception as exc:
            if not silent:
                self.log(f"[WARN] 自动匹配电台失败: {exc}")

    def validate_music_folder(self):
        folder = self.music_dir()
        if folder is None or not folder.exists():
            return
        try:
            candidates = list_audio_candidates(folder)
        except Exception as exc:
            self.show_error("校验音频失败", exc)
            return

        self.audio_table.setRowCount(0)
        ok_count = 0
        need_norm_count = 0
        for path in candidates:
            r = validate_wav(path)
            row = self.audio_table.rowCount()
            self.audio_table.insertRow(row)
            if r.info is not None:
                sr, ch, bits, duration = str(r.info.samplerate), str(r.info.channels), str(r.info.bits_per_sample), f"{r.info.duration_sec:.2f}"
            else:
                sr = ch = bits = duration = "-"
            if r.ok:
                status, action = "OK", self.txt("原地使用", "Use as-is")
                ok_count += 1
            else:
                status, action = self.txt("需要规范化", "Normalize"), "FFmpeg -> *_fh6_norm.wav"
                need_norm_count += 1
            problems = "; ".join(r.errors + r.warnings)
            for col, value in enumerate([path.name, action, status, sr, ch, bits, duration, problems]):
                item = QTableWidgetItem(value)
                if col in (1, 2):
                    item.setTextAlignment(Qt.AlignCenter)
                self.audio_table.setItem(row, col, item)

        info = self.current_station_info()
        if info and len(candidates) > info.track_slot_count:
            self.log(f"[WARN] 候选音频={len(candidates)}，超过当前电台槽位={info.track_slot_count}，生成时会丢弃后 {len(candidates) - info.track_slot_count} 个。")
        self.log(f"[OK] 音频校验完成：候选={len(candidates)}, 合规={ok_count}, 需规范化={need_norm_count}")

    def ensure_metadata_table(self):
        folder = self.music_dir()
        if folder is None or not folder.exists():
            QMessageBox.warning(self, self.txt("缺少音乐文件夹", "Missing music folder"), self.txt("请先选择音乐文件夹", "Please select a music folder first"))
            return

        info = self.current_station_info()
        slot_limit = info.track_slot_count if info else None

        try:
            path = ensure_metadata_file_for_music_dir_to_path(
                folder,
                project_output_dir() / METADATA_FILE_NAME,
                slot_limit=slot_limit,
            )
            self.log(f"[OK] 已生成/刷新歌名表: {path}")
            self.log("  请编辑 output/track_metadata.csv 的 display_name 与 artist 列，然后点击“③ 最终生成 txt 与 XML”。")
            self.update_guide("metadata")
            QMessageBox.information(
                self,
                self.txt("歌名表已生成", "Metadata CSV created"),
                self.txt(
                    f"已生成/刷新：\n{path}\n\n请编辑 display_name 与 artist 列，然后点击“③ 最终生成”。",
                    f"Created/refreshed:\n{path}\n\nEdit the display_name and artist columns, then click ③ Generate.",
                )
            )
        except Exception as exc:
            self.show_error("生成歌名表失败", exc)

    def import_fmod_extract_assets(self):
        folder = self.music_dir()
        if folder is None:
            QMessageBox.warning(self, self.txt("缺少音乐文件夹", "Missing music folder"), self.txt("请先选择音乐文件夹", "Please select a music folder first"))
            return

        path = QFileDialog.getExistingDirectory(
            self,
            self.txt("选择 Fmod Bank Tools Extract 后的 Wav Output Directory", "Select the Wav Output Directory from Fmod Bank Tools Extract")
        )
        if not path:
            return

        try:
            dst = import_fmod_extract_folder(Path(path), project_work_dir())
            self.log(f"[OK] 已导入 Fmod Extract 模板目录: {dst}")
            self.log("  下一次点击“③ 最终生成 txt 与 XML”时，导入后可最终生成 fmod_ready_wav。")
            self.update_guide("import_extract")
            QMessageBox.information(
                self,
                self.txt("导入完成", "Import complete"),
                self.txt(
                    "Extract 已导入。\n\n下一步：点击“③ 最终生成”。",
                    "Extract imported.\n\nNext: click ③ Generate.",
                )
            )
        except Exception as exc:
            self.show_error("导入 Fmod Extract 模板失败", exc)

    def show_track_order_info(self):
        folder = self.music_dir()
        if folder is None:
            QMessageBox.warning(self, self.txt("缺少音乐文件夹", "Missing music folder"), self.txt("请先选择音乐文件夹", "Please select a music folder first"))
            return
        path = project_work_dir() / TRACK_ORDER_FILE_NAME
        self.log(f"[INFO] 槽位映射表: {path}")
        QMessageBox.information(
            self,
            self.txt("映射表", "Mapping table"),
            self.txt(
                f"映射表位置：\n{path}\n\n普通用户通常不需要修改它。",
                f"Mapping table location:\n{path}\n\nMost users do not need to edit this file.",
            )
        )

    def run_prepare(self):
        if self.is_generating:
            QMessageBox.information(
                self,
                self.txt("正在生成", "Generating"),
                self.txt("当前正在生成，请等待完成。", "Generation is already running. Please wait."),
            )
            return

        xml, music, station = self.xml_path(), self.music_dir(), self.station_combo.currentData()
        if not xml:
            QMessageBox.warning(self, self.txt("缺少 XML", "Missing XML"), self.txt("请先选择 RadioInfo_CN.xml", "Please select RadioInfo_CN.xml first"))
            return
        if not music:
            QMessageBox.warning(self, self.txt("缺少音乐文件夹", "Missing music folder"), self.txt("请先选择音乐文件夹", "Please select a music folder first"))
            return
        if not station:
            QMessageBox.warning(self, self.txt("缺少电台", "Missing station"), self.txt("请先选择目标电台", "Please select a target station first"))
            return

        self.save_session()
        self.set_busy(True)
        self.log(self.txt(
            "[INFO] 开始后台生成。音频复制和音量匹配可能需要一些时间，窗口不会再卡死。",
            "[INFO] Background generation started. Copying and loudness matching may take some time; the window should remain responsive.",
        ))

        self.prepare_thread = QThread(self)
        self.prepare_worker = PrepareWorker(xml, music, station)
        self.prepare_worker.moveToThread(self.prepare_thread)

        self.prepare_thread.started.connect(self.prepare_worker.run)
        self.prepare_worker.progress.connect(self.on_prepare_progress)
        self.prepare_worker.finished.connect(self.on_prepare_success)
        self.prepare_worker.invalid_audio.connect(self.on_prepare_invalid_audio)
        self.prepare_worker.failed.connect(self.on_prepare_failed)

        self.prepare_worker.finished.connect(self.prepare_thread.quit)
        self.prepare_worker.invalid_audio.connect(self.prepare_thread.quit)
        self.prepare_worker.failed.connect(self.prepare_thread.quit)
        self.prepare_thread.finished.connect(self.cleanup_prepare_worker)

        self.prepare_thread.start()

    def on_prepare_progress(self, value: int, message: str):
        self.set_generate_progress(value, message)
        self.log(self.txt(f"[进度] {value}% {message}", f"[Progress] {value}% {message}"))

    def cleanup_prepare_worker(self):
        if self.prepare_worker is not None:
            self.prepare_worker.deleteLater()
            self.prepare_worker = None
        if self.prepare_thread is not None:
            self.prepare_thread.deleteLater()
            self.prepare_thread = None
        self.set_busy(False)

    def on_prepare_success(self, result):
        self.set_generate_progress(100, self.txt("生成完成", "Generation complete"))
        self.log(self.txt("[OK] 已生成 XML 和 fmod_ready_wav", "[OK] Generated XML and fmod_ready_wav"))
        self.log(self.txt(f"  输出目录: {result.output_dir}", f"  Output folder: {result.output_dir}"))
        self.log(self.txt(f"  备份目录: {result.backup_dir}", f"  Backup folder: {result.backup_dir}"))
        if result.backup_snapshot_dir:
            self.log(self.txt(f"  本次备份: {result.backup_snapshot_dir}", f"  Backup snapshot: {result.backup_snapshot_dir}"))
        self.log(self.txt(f"  目标电台: {result.station_name}", f"  Target station: {result.station_name}"))
        self.log(self.txt(f"  使用音频: {result.used_count}", f"  Used audio: {result.used_count}"))
        self.log(self.txt(f"  原地使用合规音频: {result.original_count}", f"  Used as-is: {result.original_count}"))
        self.log(self.txt(f"  FFmpeg规范化音频: {result.normalized_count}", f"  Normalized audio: {result.normalized_count}"))
        self.log(self.txt(f"  丢弃音频: {result.discarded_count}", f"  Discarded audio: {result.discarded_count}"))
        self.log(self.txt(f"  输出 XML: {result.output_xml}", f"  Output XML: {result.output_xml}"))
        self.log(self.txt(
            f"  已替换并匹配音量的音乐文件夹: {project_output_dir() / FMOD_READY_WAV_DIR_NAME}",
            f"  Ready WAV folder: {project_output_dir() / FMOD_READY_WAV_DIR_NAME}",
        ))
        self.log(self.txt("  bank 重构：请使用 Fmod Bank Tools 完成。", "  Bank rebuild: use Fmod Bank Tools."))
        self.update_guide("generated")
        self.refresh_calibration_audio_list()
        QMessageBox.information(
            self,
            self.txt("生成完成", "Generation complete"),
            self.txt(
                f"已生成 XML 和 fmod_ready_wav。\n\n输出 XML：{result.output_xml}\n\n下一步：在 Fmod Bank Tools 中使用 output/fmod_ready_wav 重构 bank。",
                f"XML and fmod_ready_wav generated.\n\nOutput XML: {result.output_xml}\n\nNext: use output/fmod_ready_wav in Fmod Bank Tools to rebuild the bank.",
            )
        )

    def on_prepare_invalid_audio(self, exc: InvalidAudioError):
        self.set_generate_progress(0, self.txt("音频处理失败", "Audio processing failed"))
        lines = [self.txt("存在无法规范化或不符合要求的音频：", "Some audio files cannot be normalized or are invalid:")]
        for file, errors in exc.invalid_files.items():
            lines.append(f"- {file}")
            for e in errors:
                lines.append(f"  * {e}")
        msg = "\n".join(lines)
        self.log("[ERROR] " + msg)
        QMessageBox.critical(self, self.txt("音频处理失败", "Audio processing failed"), msg)

    def on_prepare_failed(self, exc: Exception):
        self.set_generate_progress(0, self.txt("生成失败", "Generation failed"))
        self.show_error(self.txt("生成失败", "Generation failed"), exc)

    def save_segments_and_write_xml(self):
        """Save marker settings and rewrite XML only.

        This button used to call the full Generate pipeline, which re-ran
        normalization, mapping, fmod_ready_wav creation and loudness matching.
        That was correct but extremely slow.  It is now an XML-only operation.
        """
        if self.is_generating:
            QMessageBox.information(
                self,
                self.txt("正在生成", "Generating"),
                self.txt("当前正在生成，请等待完成。", "Generation is already running. Please wait."),
            )
            return

        xml, music, station = self.xml_path(), self.music_dir(), self.station_combo.currentData()
        if not xml:
            QMessageBox.warning(self, self.txt("缺少 XML", "Missing XML"), self.txt("请先选择 RadioInfo_CN.xml", "Please select RadioInfo_CN.xml first"))
            return
        if not music:
            QMessageBox.warning(self, self.txt("缺少音乐文件夹", "Missing music folder"), self.txt("请先选择音乐文件夹", "Please select a music folder first"))
            return
        if not station:
            QMessageBox.warning(self, self.txt("缺少电台", "Missing station"), self.txt("请先选择目标电台", "Please select a target station first"))
            return

        self.save_current_segments()

        try:
            self.log(self.txt("[INFO] 正在保存段落设置并更新 XML。", "[INFO] Saving markers and updating XML."))
            result = regenerate_xml_only(xml, music, station)
            self.log(self.txt(f"[OK] 已重写 XML: {result.output_xml}", f"[OK] XML saved: {result.output_xml}"))
            self.log(self.txt(f"  更新槽位: {result.changed_slots}", f"  Updated slots: {result.changed_slots}"))
            QMessageBox.information(
                self,
                self.txt("XML 已保存", "XML saved"),
                self.txt(
                    f"段落设置已保存，XML 已更新。\n\n输出 XML：{result.output_xml}",
                    f"Markers saved and XML updated.\n\nOutput XML: {result.output_xml}",
                )
            )
        except Exception as exc:
            self.show_error(self.txt("重写 XML 失败", "Failed to rewrite XML"), exc)

    def refresh_calibration_audio_list(self):
        folder = self.music_dir()
        self.calib_audio_combo.blockSignals(True)
        self.calib_audio_combo.clear()
        self.current_audio_info = None
        if folder and folder.exists():
            wavs = sorted(folder.glob("*.wav"), key=natural_key)
            for wav in wavs:
                try:
                    info = read_wav_info(wav)
                except Exception:
                    continue
                self.calib_audio_combo.addItem(f"{wav.name} | {info.samplerate}Hz | {info.duration_sec:.2f}s", str(wav))
        self.calib_audio_combo.blockSignals(False)
        self.on_calibration_audio_changed()

    def on_calibration_audio_changed(self):
        path_text = self.calib_audio_combo.currentData()
        if not path_text:
            self.current_audio_info = None
            self.sample_rate_label.setText("sample rate: -")
            self.duration_label.setText("duration: -")
            return
        path = Path(path_text)
        try:
            info = read_wav_info(path)
        except Exception as exc:
            self.show_error("读取试听音频失败", exc)
            return
        self.current_audio_info = info
        self.player.set_source(path.resolve())
        max_sample = max(0, info.sample_length - 1)
        for spin in list(self.marker_spins.values()) + [self.sample_spin]:
            spin.setMaximum(min(2_147_483_647, max_sample))

        # v3.6.2 修复：
        # QMediaPlayer.durationChanged 在部分 WAV/后端上可能返回偏短时长，
        # 导致进度条拖到最右边时音乐实际还没结束，进而写入错误 End。
        # 因此进度条不再使用播放器返回的 duration_ms，
        # 而是直接使用 WAV header 的真实 sample/frame 数。
        self.position_slider.setRange(0, max_sample)

        self.sample_rate_label.setText(f"sample rate: {info.samplerate} Hz")
        self.duration_label.setText(f"duration: {info.duration_sec:.3f}s / {info.sample_length} samples")
        self.load_markers_for_current_audio()
        self.on_player_position_changed(0)

    def load_markers_for_current_audio(self):
        info = self.current_audio_info
        if info is None:
            return
        positions = {"TrackStart": 0, "End": max(0, info.sample_length - 1)}
        seg_path = self.segments_path()
        if seg_path and seg_path.exists():
            try:
                data = load_segments(seg_path)
                item = data.get("items", {}).get(info.filename)
                if item and isinstance(item.get("markers"), dict):
                    positions.update(markers_from_json(item["markers"], info).positions)
            except Exception as exc:
                self.log(f"[WARN] 读取段落设置失败，使用默认 Marker: {exc}")
        for name, spin in self.marker_spins.items():
            spin.setValue(positions.get(name, -1))

    def play_audio(self):
        if self.current_audio_info is None:
            QMessageBox.warning(self, self.txt("未选择音频", "No audio selected"), self.txt("请先刷新并选择一个 WAV 文件", "Please refresh and select a WAV file first"))
            return
        self.player.play()

    def _update_position_label_from_sample(self, sample: int) -> None:
        if self.current_audio_info is None:
            self.position_label.setText("0.000s / sample 0")
            return
        sample = max(0, min(int(sample), max(0, self.current_audio_info.sample_length - 1)))
        seconds = sample_to_seconds(sample, self.current_audio_info.samplerate)
        self.position_label.setText(f"{seconds:.3f}s / sample {sample}")

    def seek_player(self, value: int):
        # QSlider 的 value 就是真实 WAV sample position。
        sample = int(value)
        self.player.seek_sample(sample)
        self._update_position_label_from_sample(sample)

    def on_player_position_changed(self, sample: int):
        # WavPreviewPlayer 直接回报 sample，不经过毫秒换算。
        sample = int(sample)
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(sample)
        self.position_slider.blockSignals(False)
        self._update_position_label_from_sample(sample)

    def current_sample_from_player(self) -> int:
        if self.current_audio_info is None:
            return 0
        return max(0, min(int(self.position_slider.value()), max(0, self.current_audio_info.sample_length - 1)))

    def set_marker_from_current_position(self):
        sample = self.current_sample_from_player()
        target = self.marker_target_combo.currentText()
        spin = self.marker_spins.get(target)
        if spin:
            spin.setValue(sample)
        self.log(f"[OK] {target} = {sample}")

    def current_markers_from_ui(self) -> SegmentMarkers:
        positions: dict[str, int] = {}
        for name, spin in self.marker_spins.items():
            value = spin.value()
            if value >= 0:
                positions[name] = value
        return SegmentMarkers(positions)

    def save_current_segments(self):
        info, seg_path = self.current_audio_info, self.segments_path()
        if info is None:
            QMessageBox.warning(self, self.txt("未选择音频", "No audio selected"), self.txt("请先选择一个 WAV 文件", "Please select a WAV file first"))
            return
        if seg_path is None:
            QMessageBox.warning(self, self.txt("缺少音乐文件夹", "Missing music folder"), self.txt("请先选择音乐文件夹", "Please select a music folder first"))
            return
        markers = self.current_markers_from_ui()
        positions = markers.positions
        if "End" in positions and "TrackStart" in positions and positions["End"] < positions["TrackStart"]:
            QMessageBox.warning(self, self.txt("Marker 不合法", "Invalid marker"), self.txt("End 不能小于 TrackStart", "End cannot be earlier than TrackStart"))
            return
        for start_name, end_name in [("TrackLoopStart", "TrackLoopEnd"), ("PostRaceLoopStart", "PostRaceLoopEnd")]:
            if start_name in positions and end_name in positions and positions[end_name] < positions[start_name]:
                QMessageBox.warning(self, self.txt("Marker 不合法", "Invalid marker"), self.txt(f"{end_name} 不能小于 {start_name}", f"{end_name} cannot be earlier than {start_name}"))
                return
        save_audio_markers(seg_path, info, markers)
        self.log(f"[OK] 已保存段落设置: {info.filename} -> {markers_to_json(markers)}")
        self.log(f"  segments: {seg_path}")

    def convert_seconds_to_sample(self):
        sr = self.current_audio_info.samplerate if self.current_audio_info else 48000
        sample = seconds_to_sample(self.seconds_spin.value(), sr)
        self.sample_spin.setValue(min(sample, self.sample_spin.maximum()))
        self.log(f"[换算] {self.seconds_spin.value():.6f}s @ {sr}Hz = {sample} samples")

    def convert_sample_to_seconds(self):
        sr = self.current_audio_info.samplerate if self.current_audio_info else 48000
        seconds = sample_to_seconds(self.sample_spin.value(), sr)
        self.seconds_spin.setValue(seconds)
        self.log(f"[换算] {self.sample_spin.value()} samples @ {sr}Hz = {seconds:.6f}s")

    def show_error(self, title: str, exc: Exception):
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.log(f"[ERROR] {title}: {exc}")
        self.log(detail)
        QMessageBox.critical(self, title, str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
