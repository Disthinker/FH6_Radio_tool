from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QSlider,
    QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, QWidget, QSizePolicy
)

from .core import InvalidAudioError
from .models import SegmentMarkers
from .project_tools import prepare_project_outputs, project_backup_dir, project_output_dir, project_work_dir
from .segment_tools import (
    MARKER_DESCRIPTIONS, MARKER_ORDER, SEGMENTS_FILE_NAME, load_segments,
    markers_from_json, markers_to_json, sample_to_seconds, save_audio_markers,
    seconds_to_sample,
)
from .simple_tools import infer_station_name
from .metadata_tools import METADATA_FILE_NAME, ensure_metadata_file_for_music_dir_to_path
from .order_tools import FMOD_EXTRACT_TEMPLATE_DIR_NAME, FMOD_REBUILD_WORKSPACE_DIR_NAME, FMOD_READY_WAV_DIR_NAME, TRACK_ORDER_FILE_NAME, import_fmod_extract_folder
from .wav_tools import list_audio_candidates, natural_key, read_wav_info, validate_wav
from .xml_tools import list_station_infos, parse_xml


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FH6 Radio Tool v3.4 - 发布精简版")
        self.resize(1280, 940)

        self.station_infos = []
        self.current_audio_info = None
        self.marker_spins: dict[str, QSpinBox] = {}

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)

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
        self.player.durationChanged.connect(self.on_player_duration_changed)

    def _build_layout(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)

        sidebar = QGroupBox("操作向导")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.addWidget(self.guide_text)
        sidebar.setMaximumWidth(420)
        sidebar.setMinimumWidth(360)
        root_layout.addWidget(sidebar, stretch=0)

        main_panel = QWidget()
        layout = QVBoxLayout(main_panel)
        root_layout.addWidget(main_panel, stretch=1)

        path_box = QGroupBox("1. 选择文件")
        grid = QGridLayout(path_box)
        btn_xml = QPushButton("选择 XML")
        btn_xml.clicked.connect(self.choose_xml)
        grid.addWidget(QLabel("RadioInfo_CN.xml:"), 0, 0)
        grid.addWidget(self.xml_edit, 0, 1)
        grid.addWidget(btn_xml, 0, 2)

        btn_music = QPushButton("选择音乐文件夹")
        btn_music.clicked.connect(self.choose_music_dir)
        grid.addWidget(QLabel("音乐文件夹:"), 1, 0)
        grid.addWidget(self.music_dir_edit, 1, 1)
        grid.addWidget(btn_music, 1, 2)
        grid.addWidget(self.output_label, 2, 0, 1, 3)
        layout.addWidget(path_box)

        self.update_guide("start")

        station_box = QGroupBox("2. 准备")
        sgrid = QGridLayout(station_box)
        self.station_combo.currentIndexChanged.connect(self.on_station_changed)
        sgrid.addWidget(QLabel("目标电台:"), 0, 0)
        sgrid.addWidget(self.station_combo, 0, 1, 1, 4)
        sgrid.addWidget(self.station_summary_label, 1, 0, 1, 5)

        btn_auto = QPushButton("自动匹配电台")
        btn_auto.clicked.connect(self.auto_select_station)
        btn_auto.setToolTip("根据音乐文件夹名尝试选择对应电台；选错时可手动改下拉框。")

        btn_validate = QPushButton("校验音频")
        btn_validate.clicked.connect(self.validate_music_folder)
        btn_validate.setToolTip("检查采样率、声道、位深；不合规音频最终会自动规范化为 *_fh6_norm.wav。")

        btn_metadata = QPushButton("① 歌名表")
        btn_metadata.clicked.connect(self.ensure_metadata_table)
        btn_metadata.setToolTip("生成 output/track_metadata.csv。")

        sgrid.addWidget(btn_auto, 2, 0, 1, 2)
        sgrid.addWidget(btn_validate, 2, 2)
        sgrid.addWidget(btn_metadata, 2, 3, 1, 2)
        layout.addWidget(station_box)

        mismatch_box = QGroupBox("3. 校准与生成")
        mgrid = QGridLayout(mismatch_box)

        mismatch_tip = QLabel(
            "导入 Extract 后，工具会自动完成映射、替换和音量匹配。"
        )
        mismatch_tip.setWordWrap(True)
        mgrid.addWidget(mismatch_tip, 0, 0, 1, 4)

        btn_import_extract = QPushButton("② 导入 Extract")
        btn_import_extract.clicked.connect(self.import_fmod_extract_assets)
        btn_import_extract.setToolTip("选择 Fmod Bank Tools 的 Wav Output Directory，生成已替换且音量匹配的 fmod_ready_wav。")

        btn_order = QPushButton("查看映射")
        btn_order.clicked.connect(self.show_track_order_info)
        btn_order.setToolTip("查看 work/track_order.csv。通常只用于确认；自动校准失败时才手动修正。")

        btn_write = QPushButton("③ 最终生成")
        btn_write.clicked.connect(self.run_prepare)
        btn_write.setToolTip("生成 XML、fmod_ready_wav 和说明。")

        mgrid.addWidget(btn_import_extract, 1, 0, 1, 2)
        mgrid.addWidget(btn_order, 1, 2)
        mgrid.addWidget(btn_write, 1, 3)
        layout.addWidget(mismatch_box)

        layout.addWidget(QLabel("4. 音频校验结果"))
        layout.addWidget(self.audio_table, stretch=1)

        calib_box = QGroupBox("5. 试听与段落设置")
        cgrid = QGridLayout(calib_box)
        btn_refresh = QPushButton("刷新可试听 WAV")
        btn_refresh.clicked.connect(self.refresh_calibration_audio_list)
        self.calib_audio_combo.currentIndexChanged.connect(self.on_calibration_audio_changed)
        cgrid.addWidget(QLabel("试听音频:"), 0, 0)
        cgrid.addWidget(self.calib_audio_combo, 0, 1, 1, 4)
        cgrid.addWidget(btn_refresh, 0, 5)

        btn_play = QPushButton("播放")
        btn_play.clicked.connect(self.play_audio)
        btn_pause = QPushButton("暂停")
        btn_pause.clicked.connect(self.player.pause)
        btn_stop = QPushButton("停止")
        btn_stop.clicked.connect(self.player.stop)
        cgrid.addWidget(btn_play, 1, 0)
        cgrid.addWidget(btn_pause, 1, 1)
        cgrid.addWidget(btn_stop, 1, 2)
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
            spin.setToolTip(MARKER_DESCRIPTIONS.get(name, ""))
            self.marker_spins[name] = spin
            desc = QLabel(MARKER_DESCRIPTIONS.get(name, ""))
            desc.setWordWrap(True)
            col = 0 if idx % 2 == 0 else 3
            if idx % 2 == 0 and idx != 0:
                row += 1
            cgrid.addWidget(label, row, col)
            cgrid.addWidget(spin, row, col + 1)
            cgrid.addWidget(desc, row, col + 2)

        row += 1
        cgrid.addWidget(QLabel("当前位置写入:"), row, 0)
        cgrid.addWidget(self.marker_target_combo, row, 1)
        btn_set = QPushButton("写入 Marker")
        btn_set.clicked.connect(self.set_marker_from_current_position)
        btn_save = QPushButton("保存段落设置")
        btn_save.clicked.connect(self.save_current_segments)
        btn_save_write = QPushButton("保存并重新生成 XML")
        btn_save_write.clicked.connect(self.save_segments_and_write_xml)
        cgrid.addWidget(btn_set, row, 2)
        cgrid.addWidget(btn_save, row, 3)
        cgrid.addWidget(btn_save_write, row, 4, 1, 2)

        row += 1
        cgrid.addWidget(QLabel("秒数:"), row, 0)
        cgrid.addWidget(self.seconds_spin, row, 1)
        btn_sec_to_sample = QPushButton("秒 → 采样点")
        btn_sec_to_sample.clicked.connect(self.convert_seconds_to_sample)
        cgrid.addWidget(btn_sec_to_sample, row, 2)
        cgrid.addWidget(QLabel("采样点:"), row, 3)
        cgrid.addWidget(self.sample_spin, row, 4)
        btn_sample_to_sec = QPushButton("采样点 → 秒")
        btn_sample_to_sec.clicked.connect(self.convert_sample_to_seconds)
        cgrid.addWidget(btn_sample_to_sec, row, 5)
        layout.addWidget(calib_box)

        layout.addWidget(QLabel("5. 日志"))
        layout.addWidget(self.log_box, stretch=1)
        self.setCentralWidget(root)

    def update_guide(self, stage: str = ""):
        xml_ok = bool(self.xml_edit.text().strip())
        music_ok = bool(self.music_dir_edit.text().strip())
        station_ok = self.station_combo.currentIndex() >= 0

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
            "Bank/Build/Cache 按 READ_ME 设置",
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
        path, _ = QFileDialog.getOpenFileName(self, "选择 RadioInfo_CN.xml", "", "XML Files (*.xml);;All Files (*)")
        if path:
            self.xml_edit.setText(path)
            self.load_xml(Path(path))
            self.update_guide("xml")

    def choose_music_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择音乐文件夹")
        if path:
            self.music_dir_edit.setText(path)
            self.output_label.setText(f"输出目录：{project_output_dir()}    备份目录：{project_backup_dir()}")
            self.validate_music_folder()
            self.refresh_calibration_audio_list()
            self.auto_select_station(silent=True)
            self.update_guide("music")

    def load_xml(self, path: Path):
        try:
            tree = parse_xml(path)
            self.station_infos = list_station_infos(tree)
            self.station_combo.blockSignals(True)
            self.station_combo.clear()
            for info in self.station_infos:
                rates = ",".join(str(x) for x in info.sample_rates) or "unknown"
                self.station_combo.addItem(f"{info.name} | 槽位 {info.track_slot_count} | SR {rates}", info.name)
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
            self.station_summary_label.setText("目标电台：请先选择 XML")
            return
        banks = ", ".join(info.banks)
        rates = ", ".join(str(x) for x in info.sample_rates) or "unknown"
        self.station_summary_label.setText(f"电台：{info.name} | Track槽位={info.track_slot_count} | SampleRate={rates} | Banks=[{banks}]")

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
                status, action = "OK", "原地使用"
                ok_count += 1
            else:
                status, action = "需要规范化", "FFmpeg -> 同目录 *_fh6_norm.wav"
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
            QMessageBox.warning(self, "缺少音乐文件夹", "请先选择音乐文件夹")
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
                "歌名表已生成",
                f"已生成/刷新：\n{path}\n\n"
                "请编辑 display_name 与 artist 列，然后点击“③ 最终生成 txt 与 XML”。"
            )
        except Exception as exc:
            self.show_error("生成歌名表失败", exc)

    def import_fmod_extract_assets(self):
        folder = self.music_dir()
        if folder is None:
            QMessageBox.warning(self, "缺少音乐文件夹", "请先选择音乐文件夹")
            return

        path = QFileDialog.getExistingDirectory(
            self,
            "选择 Fmod Bank Tools Extract 后的 Wav Output Directory"
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
                "导入完成",
                f"已导入 Extract 模板。\n\n"
                "请点击“③ 最终生成 txt 与 XML”。\n"
                "工具会生成 output/fmod_ready_wav，后续在 Fmod Bank Tools 中把它作为 Wav Output Directory。"
            )
        except Exception as exc:
            self.show_error("导入 Fmod Extract 模板失败", exc)

    def show_track_order_info(self):
        folder = self.music_dir()
        if folder is None:
            QMessageBox.warning(self, "缺少音乐文件夹", "请先选择音乐文件夹")
            return
        path = project_work_dir() / TRACK_ORDER_FILE_NAME
        self.log(f"[INFO] 槽位映射表: {path}")
        QMessageBox.information(
            self,
            "槽位映射表",
            f"槽位映射表位置：\n{path}\n\n"
            "请先点击“生成 txt 与 XML”生成它。\n"
            "如果出现歌名和实际播放不对应，请编辑此 CSV：\n"
            "- slot_index 表示 XML 槽位\n"
            "- sound_name 是原游戏资源名，请勿改\n"
            "- audio_filename 是实际打包的 wav 文件名\n"
            "- display_name / artist 是游戏中显示的名称\n\n"
            "调整后重新点击“③ 最终生成 txt 与 XML”。"
        )

    def run_prepare(self):
        xml, music, station = self.xml_path(), self.music_dir(), self.station_combo.currentData()
        if not xml:
            QMessageBox.warning(self, "缺少 XML", "请先选择 RadioInfo_CN.xml")
            return
        if not music:
            QMessageBox.warning(self, "缺少音乐文件夹", "请先选择音乐文件夹")
            return
        if not station:
            QMessageBox.warning(self, "缺少电台", "请先选择目标电台")
            return

        try:
            result = prepare_project_outputs(xml, music, station)
            assets_text = ", ".join(str(p) for p in result.output_assets_txts)
            self.log("[OK] 已生成 txt 与 XML")
            self.log(f"  输出目录: {result.output_dir}")
            self.log(f"  备份目录: {result.backup_dir}")
            if result.backup_snapshot_dir:
                self.log(f"  本次备份: {result.backup_snapshot_dir}")
            self.log(f"  目标电台: {result.station_name}")
            self.log(f"  使用音频: {result.used_count}")
            self.log(f"  原地使用合规音频: {result.original_count}")
            self.log(f"  FFmpeg规范化音频: {result.normalized_count}")
            self.log(f"  丢弃音频: {result.discarded_count}")
            self.log(f"  输出 XML: {result.output_xml}")
            self.log(f"  已替换并匹配音量的音乐文件夹: {project_output_dir() / FMOD_READY_WAV_DIR_NAME}")
            self.log(f"  已替换并匹配音量的音乐文件夹: {project_output_dir() / FMOD_READY_WAV_DIR_NAME}")
            self.log("  bank 重构：请用户自行使用 Fmod Bank Tools 完成。")
            self.update_guide("generated")
            self.refresh_calibration_audio_list()
            self.validate_music_folder()
            QMessageBox.information(self, "生成完成", "已生成 txt 与 XML。\\n\\n"
                                    f"输出 XML：{result.output_xml}\\n"
                                    "请使用 output/fmod_ready_wav 在 Fmod Bank Tools 中重构 bank。")
        except InvalidAudioError as exc:
            lines = ["存在无法规范化或不符合要求的音频："]
            for file, errors in exc.invalid_files.items():
                lines.append(f"- {file}")
                for e in errors:
                    lines.append(f"  * {e}")
            msg = "\\n".join(lines)
            self.log("[ERROR] " + msg)
            QMessageBox.critical(self, "音频处理失败", msg)
        except Exception as exc:
            self.show_error("生成 txt/XML 失败", exc)

    def save_segments_and_write_xml(self):
        self.save_current_segments()
        self.run_prepare()

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
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        max_sample = max(0, info.sample_length - 1)
        for spin in list(self.marker_spins.values()) + [self.sample_spin]:
            spin.setMaximum(min(2_147_483_647, max_sample))
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
            QMessageBox.warning(self, "未选择音频", "请先刷新并选择一个 WAV 文件")
            return
        self.player.play()

    def seek_player(self, value: int):
        self.player.setPosition(value)

    def on_player_duration_changed(self, duration_ms: int):
        self.position_slider.setRange(0, max(0, duration_ms))

    def on_player_position_changed(self, position_ms: int):
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(position_ms)
        self.position_slider.blockSignals(False)
        sample = 0
        seconds = position_ms / 1000.0
        if self.current_audio_info is not None:
            sample = seconds_to_sample(seconds, self.current_audio_info.samplerate)
            sample = min(sample, max(0, self.current_audio_info.sample_length - 1))
        self.position_label.setText(f"{seconds:.3f}s / sample {sample}")

    def current_sample_from_player(self) -> int:
        if self.current_audio_info is None:
            return 0
        return min(seconds_to_sample(self.player.position() / 1000.0, self.current_audio_info.samplerate),
                   max(0, self.current_audio_info.sample_length - 1))

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
            QMessageBox.warning(self, "未选择音频", "请先选择一个 WAV 文件")
            return
        if seg_path is None:
            QMessageBox.warning(self, "缺少音乐文件夹", "请先选择音乐文件夹")
            return
        markers = self.current_markers_from_ui()
        positions = markers.positions
        if "End" in positions and "TrackStart" in positions and positions["End"] < positions["TrackStart"]:
            QMessageBox.warning(self, "Marker 不合法", "End 不能小于 TrackStart")
            return
        for start_name, end_name in [("TrackLoopStart", "TrackLoopEnd"), ("PostRaceLoopStart", "PostRaceLoopEnd")]:
            if start_name in positions and end_name in positions and positions[end_name] < positions[start_name]:
                QMessageBox.warning(self, "Marker 不合法", f"{end_name} 不能小于 {start_name}")
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
