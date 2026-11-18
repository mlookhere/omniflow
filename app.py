"""Sword x Staff — resilient MuMu automator with profiles and workflow recorder."""
from __future__ import annotations

import sys
import cv2
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from swordx.adb import ADB, ADBError
from swordx.config import ProfileStore, Settings
from swordx.detection import Observation, ScreenDetector
from swordx.engine import KNOWN_STALL_SECONDS, MAX_STALL_SECONDS, Runner
from swordx.recorder import RecorderDialog
from swordx.manager import DataManagerDialog
from swordx.workflows import BUILTIN, list_workflows
from swordx.stats import SessionStats, format_duration
from swordx.watchdog import restart_enabled, set_restart_enabled
from swordx.preview import (AndroidVideoStream, STREAM_SIZES, TARGET_FPS,
                            should_seed_preview, video_is_stale, fit_preview)

ROOT = Path(__file__).resolve().parent


class ClickablePreview(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class EmulatorPreviewThread(QThread):
    """Device-scoped, independent Android H.264 preview (no desktop capture)."""
    notice = Signal(str)

    def __init__(self, serial: str, enabled: threading.Event, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.enabled = enabled
        self.stopped = threading.Event()
        self.active = threading.Event()
        self.lock = threading.Lock()
        self.latest: QImage | None = None
        self.stream: AndroidVideoStream | None = None
        self.backend = 'Android H.264'
        self.error_message = ''
        self.last_frame_at = float('-inf')

    def interrupt(self):
        # Close the blocked stream read immediately when preview is turned off.
        with self.lock:
            stream = self.stream
        if stream:
            stream.interrupt()
        self.active.clear()

    def stop(self):
        self.stopped.set()
        self.interrupt()

    def take_latest(self) -> QImage | None:
        with self.lock:
            image, self.latest = self.latest, None
        return image

    def run(self):
        try:
            import av  # noqa: F401
        except ImportError:
            self.error_message = 'PyAV missing: run setup.bat to enable emulator video'
            self.notice.emit(self.error_message)
            return
        self.notice.emit(f'Android video assigned to {self.serial} (no desktop capture).')
        attempts = 0
        while not self.stopped.is_set():
            if not self.enabled.is_set():
                self.active.clear()
                self.stopped.wait(.1)
                continue
            size = STREAM_SIZES[min(attempts, len(STREAM_SIZES) - 1)]
            frames = 0
            last_present = 0.0
            failure = ''
            try:
                with AndroidVideoStream(self.serial, size) as stream:
                    with self.lock:
                        self.stream = stream
                    try:
                        for rgb in stream.frames():
                            if self.stopped.is_set() or not self.enabled.is_set():
                                break
                            now = time.monotonic()
                            if now - last_present < 1 / TARGET_FPS:
                                continue
                            last_present = now
                            rgb = fit_preview(rgb)
                            image = QImage(rgb.data, 360, 640, rgb.strides[0],
                                           QImage.Format_RGB888).copy()
                            with self.lock:
                                self.latest = image  # Discard stale frames.
                                self.last_frame_at = now
                            self.active.set()
                            frames += 1
                        if self.enabled.is_set() and not self.stopped.is_set():
                            failure = stream.error()
                    finally:
                        with self.lock:
                            self.stream = None
            except Exception as exc:
                failure = f'{type(exc).__name__}: {exc}'
            finally:
                self.active.clear()
                with self.lock:
                    self.latest = None
            if self.stopped.is_set():
                break
            if not self.enabled.is_set():
                continue
            # Android screenrecord normally ends after 180 seconds. Restart it
            # without declaring an emulator crash, and try smaller encodings on
            # startup failures.
            if frames >= 30:
                attempts = 0
                self.error_message = ''
            else:
                attempts += 1
                self.error_message = failure or 'No frames received from emulator video'
                self.notice.emit(f'Video for {self.serial}: {self.error_message}; retrying.')
            self.stopped.wait(.25 if frames >= 30 else 3)


class StateTimeoutDialog(QDialog):
    """Edit watchdog durations by state ID, not by list position or label."""

    def __init__(self, folder: Path, saved: dict[str, int], parent=None):
        super().__init__(parent)
        import json
        self.setWindowTitle('Screen-state timeouts')
        self.resize(460, 510)
        self.fields: dict[str, QSpinBox] = {}
        outer = QVBoxLayout(self)
        explanation = QLabel('Time a recognized state may remain unchanged before the game is restarted. '
                             'For matchmaking queues, choose Never. These settings do not change '
                             'your taps, delays, or workflow recordings.')
        explanation.setWordWrap(True)
        outer.addWidget(explanation)
        area = QScrollArea()
        area.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        for state in data['states']:
            name = data.get('actions', {}).get(state, {}).get('label', state.replace('_', ' ').title())
            field = QSpinBox()
            field.setRange(0, MAX_STALL_SECONDS // 60)
            field.setSuffix(' min')
            field.setSpecialValueText('Never')
            field.setValue(max(0, saved.get(state, KNOWN_STALL_SECONDS)) // 60)
            field.setToolTip('0 = Never restart for a recognized, unchanged screen')
            form.addRow(f'{name}:', field)
            self.fields[state] = field
        area.setWidget(content)
        outer.addWidget(area)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def values(self) -> dict[str, int]:
        # Store only differences from the legacy default; a configured zero is meaningful.
        return {state: field.value() * 60 for state, field in self.fields.items()
                if field.value() * 60 != KNOWN_STALL_SECONDS}


class AutomationThread(QThread):
    logged = Signal(str)
    state_seen = Signal(str, float)
    wait_status = Signal(str)
    counts = Signal(int)
    errored = Signal(str)
    initial_preview = Signal(QImage)

    def __init__(self, profile: str, settings: Settings, store: ProfileStore, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.settings = settings
        self.store = store
        self.stopped = threading.Event()
        self.paused = threading.Event()
        self.device = ADB(serial=settings.device)
        self.preview_enabled = threading.Event()
        self.preview_enabled.set()
        self.paused_at: float | None = None
        self.runner: Runner | None = None
        self.video_active = threading.Event()
        self.last_preview_seed = float('-inf')

    def run(self) -> None:
        try:
            folder = list_workflows().get(self.settings.workflow)
            if folder is None:
                raise ValueError(f'Workflow no longer exists: {self.settings.workflow}')
            runner = Runner(self.device, ScreenDetector(folder), self.settings,
                            self.logged.emit, self._report, self._completed)
            self.runner = runner
            failures = 0
            while not self.stopped.is_set():
                if self.paused.is_set():
                    self.stopped.wait(.2)
                    continue
                try:
                    runner.step()
                    remaining = runner.wait_remaining
                    if remaining is None:
                        self.wait_status.emit('No action delay active')
                    else:
                        total = max(1, int(remaining + .999))
                        minutes, seconds = divmod(total, 60)
                        self.wait_status.emit(f'Next action in {minutes}:{seconds:02d}')
                    failures = 0
                    self.stopped.wait(max(.35, self.settings.poll_seconds))
                except (ADBError, OSError, ValueError) as exc:
                    failures += 1
                    if failures == 1 or failures % 6 == 0:
                        self.logged.emit(f'Emulator connection issue: {exc}')
                    if failures >= 2:
                        try:
                            runner.reconnect()
                        except ADBError:
                            pass
                        try:
                            runner.recover_emulator()
                        except OSError as launch_error:
                            self.logged.emit(f'Could not launch MuMu: {launch_error}')
                        runner.device_size = None
                    self.stopped.wait(min(10, 1.5 * failures))
            self.logged.emit('Stopped')
        except Exception as exc:
            self.errored.emit(f'{type(exc).__name__}: {exc}')

    def _completed(self, session: int):
        # Session-only dashboard: keep historical profile totals untouched.
        self.counts.emit(session)

    def _report(self, found: Observation, frame):
        runner = self.runner
        if runner is None:
            return
        self.state_seen.emit(runner.labels.get(found.state, found.state), found.confidence)
        # Seed the display from the screenshot already captured for OCR while
        # the independent Android video encoder/decoder initializes. No extra
        # capture loop, Windows pixels, or interaction with match state.
        now = time.monotonic()
        if should_seed_preview(self.preview_enabled.is_set(), self.video_active.is_set(),
                               now - self.last_preview_seed):
            self.last_preview_seed = now
            rgb = fit_preview(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self.initial_preview.emit(QImage(rgb.data, 360, 640, rgb.strides[0],
                                             QImage.Format_RGB888).copy())

    def stop(self):
        self.stopped.set()

    def pause(self, is_paused: bool):
        if is_paused:
            if not self.paused.is_set():
                self.paused_at = time.monotonic()
            self.paused.set()
        else:
            # Paused minutes do not consume an armed pre-action delay.
            if self.runner:
                now = time.monotonic()
                self.runner.last_progress = now
                self.runner.static_since = now
                if self.paused_at is not None:
                    self.runner.skip_paused_time(now - self.paused_at)
            self.paused_at = None
            self.paused.clear()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.store = ProfileStore()
        self.profile_name = self.store.active()
        self.settings = self.store.get(self.profile_name)
        self.worker: AutomationThread | None = None
        self.preview_image: QPixmap | None = None
        self.preview_enabled = True
        self.desktop_preview: EmulatorPreviewThread | None = None
        self.preview_frames = 0
        self.preview_seeded = False
        self.preview_start = time.monotonic()
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(33)
        self.preview_timer.timeout.connect(self._poll_preview)
        self.session_stats: SessionStats | None = None
        self.metrics_timer = QTimer(self)
        self.metrics_timer.setInterval(500)
        self.metrics_timer.timeout.connect(self._update_metrics)
        self.setWindowTitle('Sword x Staff · Automator v1.5.3')
        self.resize(1260, 870)
        self.setMinimumSize(1040, 750)
        self._build()
        self._refresh_profiles()
        self._refresh_workflows()
        self._load_settings()
        self._refresh_devices()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        brand.setSpacing(2)
        brand.addWidget(self._label('SWORD × STAFF', 'title'))
        header.addLayout(brand)
        header.addStretch()
        self.status = self._label('●  IDLE', 'status')
        self._set_status('idle')
        header.addWidget(self.status)
        layout.addLayout(header)

        selector = self._card()
        selector_row = QHBoxLayout()
        selector.layout().addLayout(selector_row)
        selector_row.addWidget(self._label('PROFILE', 'overline'))
        self.profiles = QComboBox()
        self.profiles.setMinimumWidth(140)
        self.profiles.currentTextChanged.connect(self._change_profile)
        selector_row.addWidget(self.profiles, 2)
        self.new_profile = QPushButton('+ Profile')
        self.new_profile.clicked.connect(self._new_profile)
        selector_row.addWidget(self.new_profile)
        self.manage_btn = QPushButton('Manage data')
        self.manage_btn.clicked.connect(self._manage_data)
        self.manage_btn.setToolTip('Export, import, delete and locate saved profiles/workflows')
        selector_row.addWidget(self.manage_btn)
        selector_row.addSpacing(14)
        selector_row.addWidget(self._label('WORKFLOW', 'overline'))
        self.workflows = QComboBox()
        self.workflows.setMinimumWidth(170)
        selector_row.addWidget(self.workflows, 3)
        self.record_btn = QPushButton('Edit / Record')
        self.record_btn.setObjectName('gold')
        self.record_btn.clicked.connect(self._recorder)
        selector_row.addWidget(self.record_btn)
        layout.addWidget(selector)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        layout.addLayout(columns, 1)
        controls = self._card()
        controls.setFixedWidth(303)
        columns.addWidget(controls)
        inputs = controls.layout()
        inputs.setSpacing(9)
        inputs.addWidget(self._label('EMULATOR CONNECTION', 'section'))
        inputs.addWidget(self._label('MuMu instance', 'field'))
        device_row = QHBoxLayout()
        self.devices = QComboBox()
        device_row.addWidget(self.devices, 1)
        refresh = QPushButton('↻')
        refresh.setFixedWidth(38)
        refresh.setToolTip('Refresh connected MuMu devices')
        refresh.clicked.connect(self._refresh_devices)
        device_row.addWidget(refresh)
        inputs.addLayout(device_row)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText('ADB address (optional)')
        inputs.addWidget(self.endpoint)
        connect = QPushButton('Connect address')
        connect.setObjectName('subtle')
        connect.clicked.connect(self._connect)
        inputs.addWidget(connect)
        inputs.addSpacing(9)
        inputs.addWidget(self._label('GAME CONNECTION', 'section'))
        inputs.addWidget(self._label('Android package', 'field'))
        pkg_row = QHBoxLayout()
        self.package = QLineEdit()
        self.package.setPlaceholderText('Open game to detect')
        pkg_row.addWidget(self.package, 1)
        detect = QPushButton('Detect')
        detect.setObjectName('success')
        detect.clicked.connect(self._detect_package)
        pkg_row.addWidget(detect)
        inputs.addLayout(pkg_row)
        self.check_game_btn = QPushButton('Check game status (ADB)')
        self.check_game_btn.setObjectName('subtle')
        self.check_game_btn.setToolTip('Check the selected emulator, game package, foreground activity, and running process')
        self.check_game_btn.clicked.connect(self._check_game_status)
        inputs.addWidget(self.check_game_btn)
        inputs.addSpacing(12)
        self.advanced_toggle = QPushButton('Advanced / Recovery  ▾')
        self.advanced_toggle.setObjectName('subtle')
        self.advanced_toggle.setCheckable(True)
        inputs.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 5, 0, 0)
        advanced_layout.setSpacing(8)
        advanced_layout.addWidget(self._label('MuMu launcher (for emulator recovery)', 'field'))
        mu_row = QHBoxLayout()
        self.emulator = QLineEdit()
        self.emulator.setPlaceholderText('Select MuMu .exe')
        mu_row.addWidget(self.emulator, 1)
        choose = QPushButton('…')
        choose.setFixedWidth(34)
        choose.clicked.connect(self._choose_exe)
        mu_row.addWidget(choose)
        advanced_layout.addLayout(mu_row)
        self.diagnostics = QCheckBox('Save unfamiliar screenshots')
        advanced_layout.addWidget(self.diagnostics)
        self.game_recovery = QCheckBox('Automatically relaunch the game if it closes')
        self.game_recovery.setToolTip('While automation runs, check whether Sword x Staff is still running. '
                                      'If its process stops but MuMu is still connected, relaunch only the game. '
                                      'Does not restart the emulator or the desktop GUI.')
        advanced_layout.addWidget(self.game_recovery)
        self.timeout_btn = QPushButton('Screen-state watchdogs')
        self.timeout_btn.setToolTip('Optional game restart watchdog. Does not control missed-click retries.')
        self.timeout_btn.setObjectName('subtle')
        self.timeout_btn.clicked.connect(self._edit_timeouts)
        advanced_layout.addWidget(self.timeout_btn)
        self.app_recovery = QCheckBox('Restart desktop app after an unexpected exit')
        self.app_recovery.setToolTip('Requires start.bat or start.vbs. Never restarts after closing the GUI normally. '
                                    'Stops after repeated crashes; does not automatically resume automation.')
        self.app_recovery.setChecked(restart_enabled())
        self.app_recovery.toggled.connect(self._set_app_recovery)
        advanced_layout.addWidget(self.app_recovery)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.advanced_toggle.toggled.connect(
            lambda show: self.advanced_toggle.setText('Advanced / Recovery  ▴' if show
                                                      else 'Advanced / Recovery  ▾'))
        inputs.addWidget(self.advanced)
        inputs.addStretch(1)
        inputs.addWidget(self._label('AUTOMATION CONTROLS', 'section'))
        self.start_btn = QPushButton('▶  Start automation')
        self.start_btn.setObjectName('success')
        self.start_btn.clicked.connect(self._start_stop)
        inputs.addWidget(self.start_btn)
        self.pause_btn = QPushButton('Pause')
        self.pause_btn.setObjectName('gold')
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._pause)
        inputs.addWidget(self.pause_btn)

        right = QVBoxLayout()
        right.setSpacing(10)
        columns.addLayout(right, 1)
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(9)
        self.run_count = self._metric(metrics_row, 'SESSION RUNS', '0', 'metricPrimary')
        self.session_elapsed = self._metric(metrics_row, 'SESSION TIME', '00:00')
        self.average_run = self._metric(metrics_row, 'AVG / RUN', '—')
        self.runs_per_hour = self._metric(metrics_row, 'RUNS / HOUR', '0.0')
        right.addLayout(metrics_row)

        state_card = self._card()
        state_row = QHBoxLayout()
        state_card.layout().addLayout(state_row)
        state_details = QVBoxLayout()
        state_details.addWidget(self._label('CURRENT DETECTION', 'overline'))
        self.screen_name = self._label('Waiting to start', 'heading')
        state_details.addWidget(self.screen_name)
        self.confidence = self._label('Ready to recognize saved screen states', 'hint')
        state_details.addWidget(self.confidence)
        self.action_wait = self._label('No action delay active', 'hint')
        state_details.addWidget(self.action_wait)
        state_row.addLayout(state_details, 1)
        right_state = QVBoxLayout()
        self.state_badge = self._label('●  IDLE', 'detectBadge')
        self._set_detection('idle')
        right_state.addWidget(self.state_badge)
        self.fastest_run = self._label('Fastest: —', 'hint')
        self.last_run = self._label('Last: —', 'hint')
        right_state.addWidget(self.fastest_run)
        right_state.addWidget(self.last_run)
        state_row.addLayout(right_state)
        right.addWidget(state_card)

        detail = QHBoxLayout()
        detail.setSpacing(10)
        right.addLayout(detail, 1)
        screen_card = self._card()
        screen_card.setFixedWidth(307)
        screen_col = screen_card.layout()
        screen_col.addWidget(self._label('LIVE EMULATOR  /  CLICK TO TOGGLE', 'overline'))
        self.preview = ClickablePreview('Live emulator preview\n\nClick to toggle')
        self.preview.setObjectName('preview')
        self.preview.setCursor(Qt.PointingHandCursor)
        self.preview.clicked.connect(self._toggle_preview)
        self.preview.setToolTip('Click to toggle live video; automation is unaffected')
        self.preview.setMinimumSize(248, 410)
        self.preview.setAlignment(Qt.AlignCenter)
        screen_col.addWidget(self.preview, 1)
        self.preview_status = self._label('Android H.264 · 30 FPS target', 'hint')
        screen_col.addWidget(self.preview_status)
        detail.addWidget(screen_card)
        log_card = self._card()
        log_col = log_card.layout()
        log_col.addWidget(self._label('ACTIVITY LOG  /  RECOVERY', 'overline'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        log_col.addWidget(self.log, 1)
        detail.addWidget(log_card, 1)

        # All controls changing the live device or recording are locked during a run.
        self._settings_controls = (self.profiles, self.new_profile, self.manage_btn,
                                   self.workflows, self.record_btn, self.timeout_btn,
                                   self.devices, refresh, self.endpoint, connect,
                                   self.package, detect, self.check_game_btn, self.emulator, choose,
                                   self.diagnostics, self.game_recovery, self.advanced_toggle,
                                   self.app_recovery)

    def _set_app_recovery(self, enabled: bool) -> None:
        try:
            set_restart_enabled(enabled)
        except OSError as exc:
            self.app_recovery.blockSignals(True)
            self.app_recovery.setChecked(not enabled)
            self.app_recovery.blockSignals(False)
            QMessageBox.warning(self, 'Desktop recovery', f'Could not save preference: {exc}')

    def _metric(self, row: QHBoxLayout, title: str, value: str, kind: str = 'metric') -> QLabel:
        card = self._card()
        card.setObjectName('metricCard')
        card.layout().setContentsMargins(13, 11, 13, 11)
        card.layout().addWidget(self._label(title, 'overline'))
        number = self._label(value, kind)
        card.layout().addWidget(number)
        row.addWidget(card, 1)
        return number

    @staticmethod
    def _restyle(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_status(self, state: str):
        self.status.setProperty('level', state)
        self._restyle(self.status)

    def _set_detection(self, state: str):
        self.state_badge.setProperty('level', state)
        self._restyle(self.state_badge)

    def _update_metrics(self):
        if not self.session_stats:
            return
        snapshot = self.session_stats.snapshot()
        self.run_count.setText(str(snapshot.runs))
        self.session_elapsed.setText(format_duration(snapshot.elapsed))
        self.average_run.setText(format_duration(snapshot.average))
        self.fastest_run.setText('Fastest: ' + format_duration(snapshot.fastest))
        self.last_run.setText('Last: ' + format_duration(snapshot.latest))
        self.runs_per_hour.setText(f'{snapshot.per_hour:.1f}')

    @staticmethod
    def _card():
        widget = QFrame()
        widget.setObjectName('card')
        column = QVBoxLayout(widget)
        column.setContentsMargins(16, 15, 16, 15)
        column.setSpacing(8)
        return widget

    @staticmethod
    def _label(text, kind):
        label = QLabel(text)
        label.setObjectName(kind)
        label.setWordWrap(True)
        return label

    def _record(self, message: str):
        self.log.appendPlainText(f'{time.strftime("%H:%M:%S")}  {message}')

    def _refresh_profiles(self):
        self.profiles.blockSignals(True)
        self.profiles.clear()
        for name in self.store.names():
            self.profiles.addItem(name)
        self.profiles.setCurrentText(self.profile_name)
        self.profiles.blockSignals(False)

    def _refresh_workflows(self):
        choice = self.workflows.currentText() or self.settings.workflow
        self.workflows.clear()
        for name in list_workflows():
            self.workflows.addItem(name)
        index = self.workflows.findText(choice)
        self.workflows.setCurrentIndex(max(0, index))

    def _capture_settings(self):
        return Settings(device=self.devices.currentData() or self.settings.device,
                        endpoint=self.endpoint.text().strip(), package=self.package.text().strip(),
                        emulator_exe=self.emulator.text().strip(),
                        poll_seconds=self.settings.poll_seconds,
                        diagnostics=self.diagnostics.isChecked(),
                        auto_relaunch_game=self.game_recovery.isChecked(),
                        workflow=self.workflows.currentText() or BUILTIN,
                        state_timeouts=self.settings.state_timeouts)

    def _load_settings(self):
        self.settings = self.store.get(self.profile_name)
        self.endpoint.setText(self.settings.endpoint)
        self.package.setText(self.settings.package)
        self.emulator.setText(self.settings.emulator_exe)
        self.diagnostics.setChecked(self.settings.diagnostics)
        self.game_recovery.setChecked(self.settings.auto_relaunch_game)
        match = self.workflows.findText(self.settings.workflow)
        self.workflows.setCurrentIndex(match if match >= 0 else 0)
        idx = self.devices.findData(self.settings.device)
        self.devices.setCurrentIndex(idx if idx >= 0 else 0)
        if not self.worker or not self.worker.isRunning():
            self.session_stats = None
            self.run_count.setText('0')
            self.session_elapsed.setText('00:00')
            self.average_run.setText('—')
            self.fastest_run.setText('Fastest: —')
            self.last_run.setText('Last: —')
            self.runs_per_hour.setText('0.0')

    def _change_profile(self, name):
        if not name or name == self.profile_name:
            return
        self.store.save(self.profile_name, self._capture_settings())
        self.profile_name = name
        self.store.select(name)
        self._load_settings()
        self._record(f'Profile selected: {name}')

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, 'New profile', 'Profile name:')
        if not ok:
            return
        try:
            template = self._capture_settings()
            self.store.save(self.profile_name, template)
            self.store.create(name, template)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Profile', str(exc))
            return
        self.profile_name = name.strip()
        self.settings = self.store.get(self.profile_name)
        self._refresh_profiles()
        self._load_settings()

    def _refresh_devices(self):
        existing = self.devices.currentData() or self.settings.device
        self.devices.clear()
        self.devices.addItem('Choose device', '')
        try:
            found = ADB().discover(preferred=existing, endpoint=self.endpoint.text().strip())
            for serial in found:
                self.devices.addItem(serial, serial)
            idx = self.devices.findData(existing)
            if idx >= 0:
                self.devices.setCurrentIndex(idx)
            elif len(found) == 1:
                self.devices.setCurrentIndex(1)
            else:
                mumu = [serial for serial in found if ADB.is_mumu_serial(serial)]
                if len(mumu) == 1:
                    self.devices.setCurrentIndex(self.devices.findData(mumu[0]))
                else:
                    self._record('Start MuMu or enter its ADB connection address'
                                 if not found else 'Multiple devices detected; choose a MuMu device')
        except ADBError as exc:
            self._record(f'ADB: {exc}')

    def _connect(self):
        endpoint = self.endpoint.text().strip()
        if not endpoint:
            self._record('Enter a MuMu ADB address first.')
            return
        try:
            self._record(ADB().connect(endpoint))
        except ADBError as exc:
            QMessageBox.warning(self, 'Connection failed', str(exc))
        self._refresh_devices()

    def _detect_package(self):
        serial = self.devices.currentData()
        if not serial:
            self._record('Select a MuMu device first.')
            return
        try:
            package = ADB(serial=serial).foreground()
            if not package or package.startswith(('com.android.', 'com.google.android.')):
                raise ADBError('Open Sword x Staff in MuMu, then click Detect.')
            self.package.setText(package)
            self._record(f'Game identified: {package}')
        except ADBError as exc:
            QMessageBox.warning(self, 'Game detection', str(exc))

    def _check_game_status(self) -> None:
        serial = self.devices.currentData()
        package = self.package.text().strip()
        if not serial or not package:
            QMessageBox.information(self, 'ADB game status',
                                    'Select a MuMu instance and enter or detect the game package first.')
            return
        device = ADB(serial=serial)
        try:
            installed = device.installed(package)
            foreground = device.foreground()
            running = device.running(package)
        except ADBError as exc:
            self._record(f'ADB game status failed ({serial}): {exc}')
            QMessageBox.warning(self, 'ADB game status', str(exc))
            return
        state = ('RUNNING' if running is True else
                 'STOPPED' if running is False else 'UNKNOWN (process query unsupported)')
        message = (f'Device: {serial}\nPackage: {package}\n'
                   f'Installed: {"Yes" if installed else "NO"}\n'
                   f'Foreground app: {foreground or "Unknown"}\n'
                   f'Game process: {state}')
        self._record('ADB status: ' + message.replace('\n', ' | '))
        QMessageBox.information(self, 'ADB game status', message +
                                ('\n\nThe package is not installed on this device. '
                                 'Recheck the game package ID.' if not installed else ''))

    def _choose_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, 'MuMu launcher executable', '', 'Programs (*.exe)')
        if path:
            self.emulator.setText(path)

    def _edit_timeouts(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, 'State timeouts', 'Stop automation before changing timeouts.')
            return
        settings = self._capture_settings()
        folder = list_workflows().get(settings.workflow)
        if folder is None:
            QMessageBox.warning(self, 'State timeouts', 'Select a valid workflow first.')
            return
        saved = settings.state_timeouts.get(settings.workflow, {})
        dialog = StateTimeoutDialog(folder, saved, self)
        if dialog.exec() != QDialog.Accepted:
            return
        # Copy on write; do not mutate old profile/recording data while editing.
        overrides = {name: dict(values) for name, values in settings.state_timeouts.items()}
        values = dialog.values()
        if values:
            overrides[settings.workflow] = values
        else:
            overrides.pop(settings.workflow, None)
        settings.state_timeouts = overrides
        self.settings = settings
        self.store.save(self.profile_name, settings)
        self._record(f'Saved {settings.workflow} screen-state timeout settings')

    def _manage_data(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, 'Manage data', 'Stop automation before managing data.')
            return
        # Preserve unsaved control edits before exporting, deleting or importing.
        self.store.save(self.profile_name, self._capture_settings())
        dialog = DataManagerDialog(self.store, ROOT, self)
        dialog.exec()
        # A deleted active profile changes the active selection in the store.
        self.profile_name = self.store.active()
        self._refresh_profiles()
        self._refresh_workflows()
        self._load_settings()
        if dialog.changed:
            self._record('Profiles and workflow data updated.')

    def _recorder(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, 'Recorder', 'Stop automation before recording new screens.')
            return
        dialog = RecorderDialog(self.devices.currentData(), self.workflows.currentText(), self)
        if dialog.exec():
            self._refresh_workflows()
            if dialog.workflows.currentData():
                self.workflows.setCurrentText(dialog.workflows.currentText())
            self.store.save(self.profile_name, self._capture_settings())
            self._record('Workflow recording saved.')

    def _start_stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.status.setText('●  STOPPING')
            self._set_status('idle')
            self.start_btn.setEnabled(False)
            return
        settings = self._capture_settings()
        if not settings.device:
            QMessageBox.information(self, 'Device required', 'Select a MuMu device first.')
            return
        if not settings.package:
            self._detect_package()
            settings = self._capture_settings()
            if not settings.package:
                return
        self.store.save(self.profile_name, settings)
        self.settings = settings
        self.session_stats = SessionStats()
        self._update_metrics()
        self.metrics_timer.start()
        self.worker = AutomationThread(self.profile_name, settings, self.store, self)
        self.worker.initial_preview.connect(self._seed_preview)
        self.worker.logged.connect(self._record)
        self.worker.state_seen.connect(self._state_seen)
        self.worker.wait_status.connect(self.action_wait.setText)
        if not self.preview_enabled:
            self.worker.preview_enabled.clear()
        self.desktop_preview = EmulatorPreviewThread(settings.device, self.worker.preview_enabled, self)
        self.worker.video_active = self.desktop_preview.active
        self.desktop_preview.notice.connect(self._record)
        self.desktop_preview.start()
        self.preview_start = time.monotonic()
        self.preview_frames = 0
        self.preview_seeded = False
        self.preview_timer.start()
        self.worker.counts.connect(self._counts)
        self.worker.errored.connect(self._fatal)
        self.worker.finished.connect(self._finished)
        self.worker.start()
        self.start_btn.setText('■  Stop automation')
        self.start_btn.setObjectName('danger')
        self._restyle(self.start_btn)
        self.status.setText('●  RUNNING')
        self._set_status('running')
        self.pause_btn.setEnabled(True)
        for widget in self._settings_controls:
            widget.setEnabled(False)
        self._record(f'Started: {self.profile_name} · {settings.workflow}')

    def _pause(self):
        if not self.worker:
            return
        paused = not self.worker.paused.is_set()
        self.worker.pause(paused)
        self.pause_btn.setText('Resume' if paused else 'Pause')
        if self.session_stats:
            (self.session_stats.pause if paused else self.session_stats.resume)()
        self.status.setText('●  PAUSED' if paused else '●  RUNNING')
        self._set_status('paused' if paused else 'running')
        self._record('Paused' if paused else 'Resumed')

    def _state_seen(self, name, certainty):
        self.screen_name.setText(name)
        recognized = name != 'Unrecognized / battle in progress' and certainty >= .8
        self.state_badge.setText('●  DETECTED' if recognized else '●  UNKNOWN')
        self._set_detection('ok' if recognized else 'unknown')
        self.confidence.setText(f'Detector confidence: {certainty:.0%}' if recognized
                                else 'No matched action · monitoring')

    def _counts(self, session):
        if self.session_stats and session > len(self.session_stats.completed):
            duration = self.session_stats.complete()
            self._record(f'Run {session} completed · {format_duration(duration)}')
            self._update_metrics()

    def _toggle_preview(self):
        self.preview_enabled = not self.preview_enabled
        if self.worker:
            if self.preview_enabled:
                self.worker.preview_enabled.set()
            else:
                self.worker.preview_enabled.clear()
                if self.desktop_preview:
                    self.desktop_preview.interrupt()
        self.preview_image = None
        self.preview_seeded = False
        self.preview.clear()
        if self.preview_enabled:
            self.preview.setText('Waiting for live game…')
            self.preview_status.setText('Emulator stream starting · 30 FPS target')
            self.preview_start = time.monotonic()
            self.preview_frames = 0
        else:
            self.preview.setText('Preview off\n\nClick to enable')
            self.preview_status.setText('Capture suspended · automation still running')

    def _seed_preview(self, frame: QImage):
        if self.preview_enabled and self.desktop_preview and not self.desktop_preview.active.is_set():
            self.preview_seeded = True
            self._display_preview(frame, video=False)

    def _poll_preview(self):
        if not self.preview_enabled:
            return
        native = self.desktop_preview
        # An encoder can remain connected yet stop emitting video on a static
        # Android screen. Reuse the already-captured OCR image until it wakes.
        if native and native.active.is_set() and video_is_stale(time.monotonic(), native.last_frame_at):
            native.active.clear()
        if native and native.active.is_set():
            image = native.take_latest()
            if image is not None:
                self._display_preview(image)
                self.preview_seeded = False
        elapsed = time.monotonic() - self.preview_start
        if elapsed >= 1:
            source = (f'{native.serial} · Android H.264' if native and native.active.is_set()
                      else f'{native.serial} · {native.error_message or "connecting video"}'
                      if native else 'No emulator selected')
            self.preview_status.setText(f'{source} · {self.preview_frames / elapsed:.0f} FPS'
                                        if self.preview_frames else
                                        'Android stream starting · current emulator screen shown'
                                        if self.preview_seeded else f'{source} · waiting for video')
            self.preview_start = time.monotonic()
            self.preview_frames = 0

    def _display_preview(self, frame: QImage, video: bool = True):
        self.preview_image = QPixmap.fromImage(frame)
        if video:
            self.preview_frames += 1
        self._render_preview()

    def _render_preview(self):
        if self.preview_enabled and self.preview_image:
            self.preview.setPixmap(self.preview_image.scaled(self.preview.size(),
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'preview'):
            self._render_preview()

    def _fatal(self, message):
        self._record('Stopped: ' + message)
        QMessageBox.warning(self, 'Automation error', message)

    def _finished(self):
        self.preview_timer.stop()
        self.metrics_timer.stop()
        if self.session_stats:
            self.session_stats.finish()
            self._update_metrics()
        if self.desktop_preview:
            self.desktop_preview.stop()
            self.desktop_preview.wait(2000)
            self.desktop_preview = None
        self.action_wait.setText('No action delay active')
        self.state_badge.setText('●  IDLE')
        self._set_detection('idle')
        self.status.setText('●  IDLE')
        self._set_status('idle')
        self.start_btn.setText('▶  Start automation')
        self.start_btn.setObjectName('success')
        self._restyle(self.start_btn)
        self.start_btn.setEnabled(True)
        self.pause_btn.setText('Pause')
        self.pause_btn.setEnabled(False)
        for widget in self._settings_controls:
            widget.setEnabled(True)

    def closeEvent(self, event):
        if self.desktop_preview:
            self.desktop_preview.stop()
            self.desktop_preview.wait(2000)
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(17000):
                event.ignore()
                self._record('Waiting for an active ADB call to finish before closing…')
                return
        self.store.save(self.profile_name, self._capture_settings())
        event.accept()


STYLE = '''
/* SwordXAuto · charcoal surfaces, warm gold hierarchy, functional status accents */
QMainWindow, QDialog, QWidget { background: #161719; color: #e9e3d6; font-family: "Segoe UI", Arial; font-size: 12px; }
QFrame#card, QFrame#metricCard { background: #222325; border: 1px solid #39362f; border-radius: 11px; }
QFrame#metricCard { background: #292822; border-color: #4d442c; }
QLabel { background: transparent; }
QLabel#overline { color: #bba66c; font-size: 10px; font-weight: 800; letter-spacing: 1px; }
QLabel#title { color: #e8ce80; font-size: 26px; font-weight: 800; }
QLabel#heading { color: #faf4e6; font-size: 19px; font-weight: 750; }
QLabel#section { color: #dfc27b; font-size: 12px; font-weight: 800; }
QLabel#metric { color: #f5edda; font-size: 24px; font-weight: 800; }
QLabel#metricPrimary { color: #efcc68; font-size: 31px; font-weight: 900; }
QLabel#counter { color: #efcc68; font-size: 30px; font-weight: 800; }
QLabel#field { color: #d1c7b4; font-weight: 650; }
QLabel#hint { color: #aea698; font-size: 11px; }
QLabel#status, QLabel#detectBadge { font-size: 12px; font-weight: 800; padding: 7px 12px; border-radius: 7px; }
QLabel#status[level="running"], QLabel#detectBadge[level="ok"] { color: #7cdea3; background: #1b3526; border: 1px solid #417a50; }
QLabel#status[level="paused"], QLabel#status[level="idle"], QLabel#detectBadge[level="idle"] { color: #edca74; background: #3b3220; border: 1px solid #7c652d; }
QLabel#detectBadge[level="unknown"] { color: #f29c94; background: #3c2423; border: 1px solid #7e4240; }
QLabel#preview { border: 1px solid #514936; border-radius: 8px; color: #aaa084; background: #111213; }
QLineEdit, QComboBox, QListWidget, QPlainTextEdit, QSpinBox { border: 1px solid #48443a; border-radius: 7px; background: #1a1b1d; color: #f2e8d3; padding: 7px; selection-background-color: #8f7333; }
QLineEdit:focus, QComboBox:focus, QListWidget:focus, QSpinBox:focus { border: 1px solid #c8a451; }
QSpinBox { padding-right: 28px; }
QSpinBox::up-button, QSpinBox::down-button {
  subcontrol-origin: border; width: 24px; background: #37332a;
  border-left: 1px solid #645235;
}
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; border-bottom: 1px solid #645235; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #59492b; }
QSpinBox::up-button:disabled, QSpinBox::down-button:disabled { background: #292b2c; border-color: #383a3c; }
QSpinBox::up-arrow { image: url("@@UP_ARROW@@"); width: 12px; height: 8px; }
QSpinBox::down-arrow { image: url("@@DOWN_ARROW@@"); width: 12px; height: 8px; }
QSpinBox::up-arrow:disabled { image: url("@@UP_DISABLED@@"); }
QSpinBox::down-arrow:disabled { image: url("@@DOWN_DISABLED@@"); }
QListWidget::item:selected { background: #615029; color: #fff5d9; }
QScrollArea, QScrollArea > QWidget > QWidget { background: #222325; border: none; }
QPlainTextEdit { font-family: Consolas, "Cascadia Code", monospace; font-size: 11px; color: #d8cdb5; }
QPushButton { border: 1px solid #504737; border-radius: 7px; padding: 8px 10px; background: #35332d; color: #f0e5ce; font-weight: 700; }
QPushButton:hover { background: #4b4230; border-color: #b18a39; }
QPushButton:disabled { color: #807d73; background: #28292b; border-color: #363638; }
QPushButton#primary, QPushButton#gold { background: #d6ac56; color: #201b13; border-color: #e7c775; font-weight: 900; }
QPushButton#primary:hover, QPushButton#gold:hover { background: #efd17b; }
QPushButton#success { background: #276d49; border-color: #4ea873; color: #eefff1; }
QPushButton#success:hover { background: #35865c; }
QPushButton#danger { background: #713433; border-color: #ae5652; color: #fff1ec; }
QPushButton#danger:hover { background: #974543; }
QPushButton#subtle { background: #2b2a27; border-color: #484237; }
QPushButton#regionTool { color: #a1e5b5; border-color: #3a6d4b; }
QPushButton#gestureTool { color: #edcb77; border-color: #796333; }
QPushButton#regionTool[selected="true"] { background: #276d49; color: #ffffff; }
QPushButton#gestureTool[selected="true"] { background: #ae8337; color: #14120f; }
/* Explicit disabled states override gold/green/red and their hover rules. */
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#gold:disabled,
QPushButton#success:disabled, QPushButton#danger:disabled, QPushButton#subtle:disabled,
QPushButton#regionTool:disabled, QPushButton#gestureTool:disabled,
QPushButton#regionTool[selected="true"]:disabled,
QPushButton#gestureTool[selected="true"]:disabled {
  background: #292b2c; color: #777a7c; border: 1px solid #383a3c;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QListWidget:disabled {
  background: #242628; color: #7b7d7e; border-color: #36383a;
}
QCheckBox:disabled { color: #787b7b; }
QTabBar::tab:disabled { color: #7d7b76; background: #252627; }

QTabWidget::pane { border: 1px solid #504737; border-radius: 7px; background: #222325; padding: 5px; }
QTabBar::tab { background: #302e28; color: #bfa975; padding: 9px 12px; border: 1px solid #504737; border-bottom: none; }
QTabBar::tab:selected { background: #806630; color: #fff4d2; font-weight: 800; }
QCheckBox { color: #d7cfbb; spacing: 8px; }
QCheckBox#passive { color: #df968e; font-weight: 750; }
QLabel#stateBanner { padding: 9px 10px; border: 1px solid #60543b; border-radius: 7px; font-weight: 800; }
QLabel#stateBanner[kind="empty"], QLabel#stateBanner[kind="new"] { background: #30302b; color: #e7cc83; }
QLabel#stateBanner[kind="action"] { background: #1c3529; border-color: #407a58; color: #92e5b1; }
QLabel#stateBanner[kind="wait"] { background: #402525; border-color: #a25852; color: #f4a099; }
QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:checked { background: #d1a653; border: 1px solid #eed389; border-radius: 3px; }
QScrollBar:vertical { background: #242424; width: 11px; }
QScrollBar::handle:vertical { background: #62573b; border-radius: 5px; min-height: 24px; }
QToolTip { background: #302c22; color: #f5e9d0; border: 1px solid #b59044; }
'''

# QSS resolves resources from the process working directory otherwise; use
# absolute bundled icon paths so both start.bat and start.vbs work anywhere.
STYLE = (STYLE.replace('@@UP_ARROW@@', (ROOT / 'assets' / 'icons' / 'spin-up.png').as_posix())
              .replace('@@DOWN_ARROW@@', (ROOT / 'assets' / 'icons' / 'spin-down.png').as_posix())
              .replace('@@UP_DISABLED@@', (ROOT / 'assets' / 'icons' / 'spin-up-disabled.png').as_posix())
              .replace('@@DOWN_DISABLED@@', (ROOT / 'assets' / 'icons' / 'spin-down-disabled.png').as_posix()))


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setFont(QFont('Segoe UI', 10))
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
