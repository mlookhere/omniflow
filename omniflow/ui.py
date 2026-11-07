"""OmniFlow desktop hub: ten-device dashboard, visual studio and scheduling."""
from __future__ import annotations

import threading
from collections import deque
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
from PySide6.QtCore import Qt, QThread, QTimer, Signal, QTime
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateTimeEdit,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget)

from swordx.adb import ADB, ADBError
from swordx.stats import format_duration
from swordx.preview import (AndroidVideoStream, StreamRetry, TARGET_FPS,
                            VIDEO_STALE_SECONDS, VIDEO_STARTUP_SECONDS, fit_preview,
                            snapshot_during_retry,
                            screen_is_unchanged)
from swordx.storage import export_backup, import_backup
from swordx.config import ProfileStore
from . import __version__
from .devices import Device, canonical_mumu_serial, discover_mumu
from .manager import InstanceManager
from .schedule import Job, ScheduleStore
from .storage import root_dir, migrate_legacy, local_workflows, preferences, save_preferences
from .studio import OmniStudio


class DeviceScan(QThread):
    devices = Signal(list)
    problem = Signal(str)

    def run(self):
        try:
            self.devices.emit(discover_mumu())
        except (ADBError, OSError) as exc:
            self.problem.emit(str(exc))


def preview_image(rgb):
    """Render a full portrait or landscape frame with no stride-dependent crop."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Preview expects an RGB frame')
    width, height = (480, 270) if rgb.shape[1] > rgb.shape[0] else (270, 480)
    scaled = fit_preview(rgb, width, height)
    return (QImage(scaled.data, width, height, scaled.strides[0],
                   QImage.Format_RGB888).copy(), scaled)


class VideoThread(QThread):
    """One selected-device video stream with a latest-frame-only buffer.

    Decode every complete H.264 frame and overwrite the old image. The Qt UI
    decides when to display it (up to TARGET_FPS), avoiding bursty input
    throttling, backpressure, and old detector frames interleaved with video.
    """
    error = Signal(str)

    def __init__(self, serial: str, parent=None, *, snapshot_source=None):
        super().__init__(parent)
        self.serial = serial
        # Optional read-only cache from the same selected automation session.
        self.snapshot_source = snapshot_source
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.latest_kind = 'video'
        self.landscape = False
        self.last_rgb = None
        self.stream = None
        self.stream_started_at = 0.0
        self.last_frame_at = 0.0
        self.interrupted = False
        self.frame_times = deque(maxlen=90)
        self._last_snapshot_error = ''
        self._last_snapshot_error_at = 0.0

    def stop(self):
        self.stopped.set()
        with self.lock:
            stream = self.stream
        if stream:
            stream.interrupt()

    def take(self):
        with self.lock:
            image, self.latest = self.latest, None
            kind = self.latest_kind
        return image, kind

    def _publish(self, rgb, kind: str):
        self.landscape = rgb.shape[1] > rgb.shape[0]
        image, rgb = preview_image(rgb)
        with self.lock:
            self.latest = image
            self.latest_kind = kind
            if kind == 'video':
                self.last_rgb = rgb
                now = time.monotonic()
                self.last_frame_at = now
                self.frame_times.append(now)

    def _read_selected_snapshot(self):
        """Reuse an existing fresh detector frame before asking ADB for another."""
        frame = self.snapshot_source() if self.snapshot_source else None
        return frame if frame is not None else ADB(serial=self.serial).screenshot(timeout=4)

    def _fallback_screenshot(self):
        """Show a bounded, selected-device snapshot if MediaCodec is unavailable.

        Never starts an extra encoder. Called only between failed H.264 starts;
        its short ADB timeout keeps Stop responsive, and it is independent of
        ongoing workflow screenshots captured by the automation worker.
        """
        if self.stopped.is_set():
            return
        try:
            # The selected worker may already have a fresh frame. ADB remains
            # the fallback if the selected session is absent or its image aged.
            frame = self._read_selected_snapshot()
            if self.stopped.is_set():
                return
            self._publish(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), 'snapshot')
        except (ADBError, OSError, ValueError) as exc:
            # Keep retrying video, but do not flood the log every 1.5 seconds
            # when a device is disconnected or screenshots are unsupported.
            if not self.stopped.is_set():
                message = f'{self.serial}: screenshot fallback unavailable: {exc}'
                now = time.monotonic()
                if message != self._last_snapshot_error or now - self._last_snapshot_error_at >= 15:
                    self._last_snapshot_error = message
                    self._last_snapshot_error_at = now
                    self.error.emit(message)

    def _watchdog(self):
        """Recover blocked H.264 reads without mistaking static screens for failure."""
        while not self.stopped.wait(0.5):
            now = time.monotonic()
            with self.lock:
                stream = self.stream
                at = self.last_frame_at
                source = self.last_rgb
                started = self.stream_started_at
                interrupted = self.interrupted
            if not stream or interrupted or started <= 0:
                continue
            limit = VIDEO_STALE_SECONDS if at > 0 else VIDEO_STARTUP_SECONDS
            if now - (at if at > 0 else started) < limit:
                continue
            if at > 0 and source is not None:
                try:
                    # One selected worker already taking screenshots can answer
                    # the static-screen probe without another ADB request.
                    snapshot = self._read_selected_snapshot()
                except (ADBError, OSError, ValueError):
                    snapshot = None
                with self.lock:
                    if (self.stopped.is_set() or self.stream is not stream or
                            self.interrupted or self.last_frame_at != at):
                        continue  # The decoder already resumed during the probe.
                    if screen_is_unchanged(source, snapshot):
                        self.last_frame_at = time.monotonic()
                        continue  # Static screen: no need to reset MediaCodec.
            if self.interrupt_if_stale(time.monotonic()):
                self.error.emit(f'{self.serial}: Android video stalled; restarting encoder')

    def fps(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        with self.lock:
            return sum(at >= now - 1.0 for at in self.frame_times)

    def is_fresh(self, now: float) -> bool:
        with self.lock:
            return self.last_frame_at > 0 and now - self.last_frame_at < VIDEO_STALE_SECONDS

    def interrupt_if_stale(self, now: float) -> bool:
        """Unstick a blocked emulator encoder, without terminating its worker."""
        with self.lock:
            stream = self.stream
            age = (now - self.last_frame_at if self.last_frame_at > 0 else
                   now - self.stream_started_at)
            limit = VIDEO_STALE_SECONDS if self.last_frame_at > 0 else VIDEO_STARTUP_SECONDS
            if not stream or self.interrupted or self.stream_started_at <= 0 or age < limit:
                return False
            self.interrupted = True
        stream.interrupt()  # Kill the specific device's screenrecord subprocess.
        return True

    def run(self):
        try:
            import av  # noqa: F401
        except ImportError:
            self.decoder_unavailable = True
            self.error.emit('PyAV not installed; using low-rate ADB screenshots for the selected emulator.')
            while not self.stopped.is_set():
                snapshot_during_retry(self.stopped, 1.5, self._fallback_screenshot)
            return
        retry = StreamRetry()
        try:
            width, height = ADB(serial=self.serial).size()
            self.landscape = width > height
        except (ADBError, OSError, ValueError):
            pass  # Fallback screenshots will establish the actual orientation.
        # One small watchdog thread for the one selected device. It stops as
        # soon as the preview stops, even if an H.264 read is blocked.
        watchdog = threading.Thread(target=self._watchdog, name='OmniFlow-video-watchdog',
                                    daemon=True)
        watchdog.start()
        try:
            while not self.stopped.is_set():
                count = 0
                started = time.monotonic()
                reason = ''
                size, bitrate = retry.profile(self.landscape)
                stream = None
                try:
                    with AndroidVideoStream(self.serial, size, bitrate=bitrate) as stream:
                        with self.lock:
                            self.stream = stream
                            self.stream_started_at = time.monotonic()
                            self.last_frame_at = 0.0
                            self.last_rgb = None
                            self.frame_times.clear()
                            self.interrupted = False
                        # Stop may arrive during AndroidVideoStream.__enter__()
                        # before stop() can see the new subprocess. Do not
                        # enter its blocking decoder after cancellation.
                        if self.stopped.is_set():
                            stream.interrupt()
                            break
                        last_converted = 0.0
                        for rgb in stream.frames():
                            if self.stopped.is_set():
                                break
                            count += 1
                            now = time.monotonic()
                            # Decode every frame to keep the H.264 parser in sync,
                            # but only resize/copy the newest available image.
                            if now - last_converted < 1 / TARGET_FPS:
                                with self.lock:
                                    self.last_frame_at = now
                                continue
                            self._publish(rgb, 'video')
                            last_converted = now
                        if not self.stopped.is_set():
                            reason = stream.error()
                except Exception as exc:
                    reason = AndroidVideoStream.explain_failure(
                        exc, stream.error() if stream is not None else '')
                finally:
                    with self.lock:
                        self.stream = None
                        self.stream_started_at = 0.0
                        self.interrupted = False
                if self.stopped.is_set():
                    break
                delay = retry.complete(count, time.monotonic() - started)
                if retry.failures == 0:
                    # Android's built-in screenrecord normally ends after about
                    # three minutes. A healthy end is a seamless new session.
                    pass
                elif retry.should_log():
                    self.error.emit(f'{self.serial}: H.264 retry {retry.failures} at '
                                    f'{size[0]}x{size[1]}, {bitrate // 1000} kbps; '
                                    f'{reason or "no decoded frames"}. '
                                    f'Next attempt in {delay:g}s')
                # Continue taking screenshots during long encoder backoffs,
                # rather than freezing at the first image for up to 8 seconds.
                # Only the selected device incurs these ADB snapshot calls.
                if retry.failures:
                    snapshot_during_retry(self.stopped, delay, self._fallback_screenshot)
                else:
                    self.stopped.wait(delay)
        finally:
            self.stopped.set()
            watchdog.join(timeout=1)


class ScheduleDialog(QDialog):
    def __init__(self, devices: list[str], workflows: list[str], current: Job | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Schedule automation')
        self.setMinimumWidth(510)
        outer=QVBoxLayout(self)
        form=QFormLayout();outer.addLayout(form)
        self.name=QLineEdit(current.name if current else '')
        self.name.setPlaceholderText('Daily rewards / overnight farm')
        form.addRow('Name',self.name)
        self.device=QComboBox();self.device.setEditable(True)
        self.device.addItems(devices)
        if current:self.device.setCurrentText(current.serial)
        form.addRow('Emulator ADB serial',self.device)
        self.package=QLineEdit(current.package if current else '')
        self.package.setPlaceholderText('com.example.game')
        form.addRow('Android package',self.package)
        self.workflow=QComboBox();self.workflow.addItems(workflows)
        if current:self.workflow.setCurrentText(current.workflow)
        form.addRow('Workflow',self.workflow)
        self.kind=QComboBox()
        for label,kind in [('One time','once'),('Every day','daily'),('Selected weekdays','weekly'),
                           ('Every N minutes','interval')]:self.kind.addItem(label,kind)
        if current:self.kind.setCurrentIndex(max(0,self.kind.findData(current.kind)))
        form.addRow('Repeat',self.kind)
        self.timezone=QComboBox();self.timezone.setEditable(True)
        self.timezone.addItems(['America/Los_Angeles','UTC','America/New_York','America/Chicago',
                                'America/Denver','Europe/London','Europe/Berlin','Asia/Tokyo'])
        self.timezone.setCurrentText(current.timezone if current else 'America/Los_Angeles')
        form.addRow('Time zone (IANA)',self.timezone)
        self.once=QDateTimeEdit();self.once.setCalendarPopup(True)
        self.once.setDateTime(datetime.now()+timedelta(minutes=5))
        if current and current.kind=='once':
            from datetime import datetime as dt
            d=dt.fromisoformat(current.spec['at'])
            self.once.setDateTime(d.replace(tzinfo=None))
        form.addRow('Date and time',self.once)
        self.time=QTimeEdit();self.time.setDisplayFormat('HH:mm')
        self.time.setTime(self.once.time())
        if current and current.kind in ('daily','weekly'):
            hour,minute=map(int,current.spec['time'].split(':'))
            self.time.setTime(QTime(hour,minute))
        form.addRow('Local clock time',self.time)
        self.weekdays=[]
        day_row=QHBoxLayout()
        for day in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']:
            flag=QCheckBox(day);flag.setChecked(True);day_row.addWidget(flag);self.weekdays.append(flag)
        if current and current.kind=='weekly':
            days=set(current.spec.get('weekdays',[]))
            for i,flag in enumerate(self.weekdays):flag.setChecked(i in days)
        self.days_panel=QWidget()
        self.days_panel.setLayout(day_row)
        form.addRow('Run on weekdays',self.days_panel)
        self.interval=QSpinBox();self.interval.setRange(1,10080)
        self.interval.setValue(int(current.spec.get('minutes',60)) if current else 60)
        self.interval.setSuffix(' min')
        form.addRow('Interval',self.interval)
        self.limit=QSpinBox();self.limit.setRange(0,10080)
        self.limit.setSpecialValueText('Unlimited')
        self.limit.setValue(current.max_minutes if current else 60)
        self.limit.setSuffix(' min')
        form.addRow('Max session duration',self.limit)
        self.run_limit=QSpinBox();self.run_limit.setRange(0,100000)
        self.run_limit.setSpecialValueText('Unlimited')
        self.run_limit.setValue(current.max_runs if current else 0)
        form.addRow('Stop after completed runs',self.run_limit)
        self.grace=QSpinBox();self.grace.setRange(0,1440);self.grace.setSuffix(' min')
        self.grace.setValue(current.grace_seconds//60 if current else 5)
        form.addRow('Start if late by ≤',self.grace)
        self.kind.currentIndexChanged.connect(self._visibility)
        self._visibility()
        help_text=QLabel('Schedules run while OmniFlow or its optional schedule agent is running. '
                         'Busy devices are skipped, never controlled twice. Late jobs outside the grace period are skipped.')
        help_text.setWordWrap(True);help_text.setObjectName('muted');outer.addWidget(help_text)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _visibility(self):
        kind=self.kind.currentData();f=self.layout().itemAt(0).layout()
        f.setRowVisible(self.once,kind=='once')
        f.setRowVisible(self.time,kind in ('daily','weekly'))
        f.setRowVisible(self.interval,kind=='interval')
        f.setRowVisible(self.days_panel,kind=='weekly')

    def values(self):
        kind=self.kind.currentData()
        if kind=='once':spec={'at':self.once.dateTime().toPython().isoformat()}
        elif kind in ('daily','weekly'):
            spec={'time':self.time.time().toString('HH:mm')}
            if kind=='weekly':spec['weekdays']=[i for i,check in enumerate(self.weekdays) if check.isChecked()]
        else:spec={'minutes':self.interval.value(),'anchor':time.time()+self.interval.value()*60}
        return dict(name=self.name.text(),serial=self.device.currentText().strip(),
                    package=self.package.text().strip(),workflow=self.workflow.currentText(),
                    kind=kind,spec=spec,timezone_name=self.timezone.currentText().strip(),
                    max_minutes=self.limit.value(),max_runs=self.run_limit.value(),
                    grace_seconds=self.grace.value()*60)


def _button(label, fn, kind=''):
    button=QPushButton(label)
    if kind:button.setObjectName(kind)
    button.clicked.connect(fn)
    return button


class OmniWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.root=root_dir()
        self.imported=migrate_legacy(self.root)
        self.config=preferences(self.root)
        self.db=ScheduleStore(self.root/'schedules.sqlite3')
        self.profiles=ProfileStore(self.root)
        self.manager=InstanceManager(self.db,poll_seconds=self.config.get('poll_seconds',0.85),
                                     auto_relaunch=self.config.get('auto_relaunch',True),
                                     dry_run=self.config.get('dry_run',False))
        self.devices=[]
        self.scan=None
        self.video=None
        self.video_serial=''
        self.requested_video_serial=''
        self.last_video_serial=''
        self.last_detector_display_at=0.0
        self.last_log_text=''
        self.last_job_refresh=0.0
        self.setWindowTitle(f'OmniFlow {__version__} · MuMu Automation')
        self.resize(1390,870)
        self.setMinimumSize(1060,700)
        self._build()
        self._refresh_workflows()
        self._refresh_presets()
        self._refresh_jobs()
        self.timer=QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(900)
        self.frame_timer=QTimer(self)
        self.frame_timer.setTimerType(Qt.PreciseTimer)
        self.frame_timer.timeout.connect(self._frame)
        self.frame_timer.start(round(1000 / TARGET_FPS))
        self.refresh_devices()
        if self.imported:self._log(f'Imported {len(self.imported)} legacy workflow(s) without changing originals')

    def _panel(self):
        frame=QFrame();frame.setObjectName('panel')
        return frame

    def _build(self):
        root=QWidget();self.setCentralWidget(root)
        layout=QVBoxLayout(root);layout.setContentsMargins(18,14,18,14);layout.setSpacing(12)
        banner=QHBoxLayout();layout.addLayout(banner)
        title=QLabel('OMNIFLOW');title.setObjectName('brand');banner.addWidget(title)
        tagline=QLabel('LOCAL-FIRST · STATE-DRIVEN AUTOMATION');tagline.setObjectName('muted');banner.addWidget(tagline)
        banner.addStretch()
        self.summary=QLabel('0 / 10 SESSIONS');self.summary.setObjectName('summary');banner.addWidget(self.summary)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self._dashboard()
        self._schedules()
        self._library()
        self._settings()
        self.statusBar().showMessage('OmniFlow ready · Schedules active while this app is running')

    def _dashboard(self):
        view=QWidget();self.tabs.addTab(view,'Dashboard')
        column=QVBoxLayout(view)
        controls=self._panel();row=QHBoxLayout(controls);column.addWidget(controls)
        row.addWidget(QLabel('Emulator'))
        self.serial=QComboBox();self.serial.setEditable(True);self.serial.setMinimumWidth(165);row.addWidget(self.serial,2)
        row.addWidget(_button('Refresh',self.refresh_devices))
        row.addWidget(QLabel('App package'))
        self.package=QLineEdit(self.config.get('package',''));self.package.setPlaceholderText('com.example.game')
        row.addWidget(self.package,3)
        row.addWidget(_button('Detect',self._detect_package))
        row.addWidget(_button('Apps',self._select_installed_app))
        row.addWidget(QLabel('Workflow'))
        self.workflow=QComboBox();row.addWidget(self.workflow,2)
        row.addWidget(_button('Start',self._start,'success'))
        row.addWidget(_button('Pause / Resume',self._pause,'gold'))
        row.addWidget(_button('Stop',self._stop,'danger'))
        presets=QHBoxLayout();column.addLayout(presets)
        presets.addWidget(QLabel('CONFIGURATION PRESET'))
        self.preset=QComboBox();self.preset.setMinimumWidth(165)
        self.preset.currentTextChanged.connect(self._apply_preset)
        presets.addWidget(self.preset,2)
        presets.addWidget(_button('Save preset',self._save_preset))
        presets.addWidget(_button('+ Preset',self._new_preset))
        presets.addWidget(_button('Delete preset',self._delete_preset))
        presets.addStretch()
        presets.addWidget(QLabel('Each session is independent. Changes apply to new runs.'))
        split=QSplitter(Qt.Horizontal);column.addWidget(split,1)
        left=self._panel();l=QVBoxLayout(left);split.addWidget(left)
        l.addWidget(QLabel('ACTIVE EMULATORS  ·  MAXIMUM 10'))
        self.session_table=QTableWidget(0,6)
        self.session_table.setHorizontalHeaderLabels(['Device','Game','Workflow','Status','Runs','Elapsed'])
        self.session_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.session_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.session_table.itemSelectionChanged.connect(self._selected)
        l.addWidget(self.session_table)
        self.session_info=QLabel('Select a running instance to inspect its state and metrics.')
        self.session_info.setWordWrap(True);self.session_info.setObjectName('muted');l.addWidget(self.session_info)
        right=self._panel();r=QVBoxLayout(right);split.addWidget(right)
        preview_top=QHBoxLayout();r.addLayout(preview_top)
        preview_top.addWidget(QLabel('SELECTED INSTANCE  /  LIVE PREVIEW'))
        self.video_switch=QCheckBox('Live video · selected instance only')
        self.video_switch.setChecked(True)  # Exactly one encoder, regardless of session count.
        self.video_switch.toggled.connect(self._toggle_video)
        preview_top.addWidget(self.video_switch)
        self.video_status=QLabel('Detector preview')
        self.video_status.setObjectName('muted')
        preview_top.addWidget(self.video_status)
        self.preview=QLabel('Start an emulator session to view its screen')
        self.preview.setMinimumSize(270,480);self.preview.setMaximumHeight(560)
        self.preview.setAlignment(Qt.AlignCenter);self.preview.setObjectName('preview')
        r.addWidget(self.preview,1)
        r.addWidget(QLabel('ACTIVITY LOG'))
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.logs.setMaximumBlockCount(250)
        r.addWidget(self.logs,1)
        split.setSizes([850,450])

    def _schedules(self):
        view=QWidget();self.tabs.addTab(view,'Scheduling')
        col=QVBoxLayout(view);toolbar=QHBoxLayout();col.addLayout(toolbar)
        toolbar.addWidget(QLabel('AUTOMATION SCHEDULES  ·  PERSISTENT'))
        toolbar.addStretch()
        for label,func in [('+ New schedule',self._add_schedule),('Edit',self._edit_schedule),
                           ('Enable / Disable',self._toggle_job),('Delete',self._delete_job),
                           ('Refresh',self._refresh_jobs)]:
            toolbar.addWidget(_button(label,func,'gold' if label.startswith('+') else ''))
        self.job_table=QTableWidget(0,7)
        self.job_table.setHorizontalHeaderLabels(['Name','Device','Workflow','Recurrence','Next local','Last status','On'])
        self.job_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.job_table.setEditTriggers(QTableWidget.NoEditTriggers)
        col.addWidget(self.job_table,1)
        col.addWidget(QLabel('RECENT SCHEDULE OCCURRENCES  ·  AUDIT HISTORY'))
        self.history_table=QTableWidget(0,3)
        self.history_table.setHorizontalHeaderLabels(['Job','Scheduled time','Outcome'])
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setMaximumHeight(165)
        col.addWidget(self.history_table)
        foot=QLabel('Scheduled starts are claimed atomically. A busy emulator is skipped, not double-tapped. '
                    'For schedules while the UI is closed, install the optional local schedule agent.')
        foot.setObjectName('muted');foot.setWordWrap(True);col.addWidget(foot)

    def _library(self):
        view=QWidget();self.tabs.addTab(view,'Workflow Library & Studio')
        col=QVBoxLayout(view);bar=QHBoxLayout();col.addLayout(bar)
        bar.addWidget(QLabel('VISUAL WORKFLOWS'))
        bar.addStretch()
        for title,fun in [('+ New',self._new_workflow),('Duplicate',self._duplicate_workflow),
                          ('Open Studio',self._studio),('Export all',self._export),
                          ('Import backup',self._import),('Delete',self._delete_workflow),
                          ('Refresh',self._refresh_workflows)]:
            bar.addWidget(_button(title,fun,'gold' if title=='Open Studio' else ''))
        self.workflow_table=QTableWidget(0,3)
        self.workflow_table.setHorizontalHeaderLabels(['Workflow','States','Stored folder'])
        self.workflow_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.workflow_table.setEditTriggers(QTableWidget.NoEditTriggers)
        col.addWidget(self.workflow_table,1)
        note=QLabel('Existing SwordXAuto recordings are copied once and remain editable. '
                    'Create visual regions, OCR/color/negative conditions, and action sequences in Studio.')
        note.setWordWrap(True);note.setObjectName('muted');col.addWidget(note)

    def _settings(self):
        view=QWidget();self.tabs.addTab(view,'Performance & Recovery')
        col=QVBoxLayout(view)
        title=QLabel('10-INSTANCE PERFORMANCE PROFILE')
        col.addWidget(title)
        panel=self._panel();form=QFormLayout(panel);col.addWidget(panel)
        self.poll=QDoubleSpinBox();self.poll.setRange(0.35,5.0)
        self.poll.setSingleStep(0.1);self.poll.setDecimals(2);self.poll.setSuffix(' sec / device')
        self.poll.setValue(self.manager.poll_seconds)
        form.addRow('OCR/image poll interval',self.poll)
        self.auto_relaunch=QCheckBox('Restart the Android game when its process is confirmed closed')
        self.auto_relaunch.setChecked(self.manager.auto_relaunch)
        form.addRow('Crash recovery',self.auto_relaunch)
        self.dry_run=QCheckBox('Dry run: recognize states without touching the emulator')
        self.dry_run.setChecked(self.manager.dry_run)
        form.addRow('Safe testing',self.dry_run)
        help=QLabel('Recommended: 0.7–1.2s per device, shared OpenCV with one CPU thread, '
                    'one optional H.264 video preview at a time. '
                    'The poll interval does not constrain the selected 30 FPS preview.')
        help.setObjectName('muted');help.setWordWrap(True);col.addWidget(help)
        col.addWidget(_button('Save for new sessions',self._save_performance,'gold'))
        col.addStretch()

    def _save_performance(self):
        self.config['poll_seconds']=self.poll.value()
        self.config['auto_relaunch']=self.auto_relaunch.isChecked()
        self.config['dry_run']=self.dry_run.isChecked()
        save_preferences(self.config,self.root)
        self.manager.poll_seconds=self.poll.value()
        self.manager.auto_relaunch=self.auto_relaunch.isChecked()
        self.manager.dry_run=self.dry_run.isChecked()
        self._log('Performance and recovery settings saved for new sessions')

    def _refresh_presets(self):
        self.preset.blockSignals(True)
        self.preset.clear();self.preset.addItems(self.profiles.names())
        self.preset.setCurrentText(self.profiles.active())
        self.preset.blockSignals(False)

    def _apply_preset(self,name):
        if not name:return
        try:
            config=self.profiles.get(name)
            self.profiles.select(name)
        except ValueError:return
        if config.device:self.serial.setCurrentText(config.device)
        if config.package:self.package.setText(config.package)
        if self.workflow.findText(config.workflow)>=0:self.workflow.setCurrentText(config.workflow)

    def _save_preset(self):
        name=self.preset.currentText()
        if not name:return
        config=self.profiles.get(name)
        config.device=self.serial.currentText().strip()
        config.package=self.package.text().strip()
        config.workflow=self.workflow.currentText()
        self.profiles.save(name,config)
        self._log(f'Updated configuration preset: {name}')

    def _delete_preset(self):
        name=self.preset.currentText()
        if not name:return
        if QMessageBox.question(self,'Delete preset',f'Delete saved preset "{name}"?')!=QMessageBox.Yes:return
        try:self.profiles.delete(name)
        except ValueError as exc:self._warning(str(exc));return
        self._refresh_presets()

    def _new_preset(self):
        name,ok=QInputDialog.getText(self,'New preset','Preset name:')
        if not ok:return
        from swordx.config import Settings
        config=Settings(device=self.serial.currentText().strip(),
                        package=self.package.text().strip(),workflow=self.workflow.currentText())
        try:self.profiles.create(name,config)
        except ValueError as exc:self._warning(str(exc));return
        self._refresh_presets()

    def _log(self,text):
        self.statusBar().showMessage(text,10000)
        self.logs.appendPlainText(f'{time.strftime("%H:%M:%S")}  {text}')

    def refresh_devices(self):
        if self.scan and self.scan.isRunning():return
        self.scan=DeviceScan(self)
        self.scan.devices.connect(self._devices_found)
        self.scan.problem.connect(self._log)
        self.scan.start()

    def _devices_found(self,devices):
        self.devices=devices
        current=self.serial.currentText() or self.config.get('device','')
        self.serial.blockSignals(True);self.serial.clear();self.serial.addItems(devices)
        if current:self.serial.setCurrentText(current)
        self.serial.blockSignals(False)
        self._log(f'{len(devices)} connected Android device(s) detected')

    def _detect_package(self):
        serial=self.serial.currentText().strip()
        if not serial:self._warning('Select an emulator first');return
        try:
            package=Device(serial=serial).foreground()
            if not package or package.startswith(('com.android.','com.google.android.')):
                raise ValueError('Open the game in MuMu, then click Detect')
            self.package.setText(package)
        except (ADBError,ValueError) as exc:self._warning(str(exc))

    def _select_installed_app(self):
        serial=self.serial.currentText().strip()
        if not serial:self._warning('Select an emulator first');return
        try:
            apps=Device(serial=serial).apps()
        except ADBError as exc:
            self._warning(str(exc));return
        if not apps:self._warning('No third-party Android apps detected on this emulator');return
        selected,ok=QInputDialog.getItem(self,'Installed Android apps',
                                         'Select application package:',apps,editable=False)
        if ok:self.package.setText(selected)

    def _warning(self,message):
        QMessageBox.warning(self,'OmniFlow',message)

    def _start(self):
        serial=self.serial.currentText().strip()
        pkg=self.package.text().strip()
        workflow=self.workflow.currentText()
        selected=local_workflows(self.root)
        folder=selected.get(workflow)
        if folder is None:self._warning('Create or select a recorded workflow first');return
        ok,msg=self.manager.start(serial,pkg,workflow,folder)
        self._log(msg)
        if not ok:self._warning(msg)
        self.config.update(device=serial,package=pkg,workflow=workflow)
        save_preferences(self.config,self.root)

    def _selected_serial(self):
        row=self.session_table.currentRow()
        return self.session_table.item(row,0).text() if row>=0 and self.session_table.item(row,0) else ''

    def _stop(self):
        serial=self._selected_serial() or self.serial.currentText()
        self.manager.stop(serial)

    def _pause(self):
        serial=self._selected_serial() or self.serial.currentText()
        session=next((s for s in self.manager.snapshot() if s.serial==serial),None)
        if session:self.manager.pause(serial,not session.pause_event.is_set())

    def _selected(self):
        self.last_log_text = ''
        serial = self._selected_serial()
        if (self.video_switch.isChecked() and
                canonical_mumu_serial(serial) != canonical_mumu_serial(self.video_serial)):
            self._toggle_video(True)

    def _toggle_video(self, enabled):
        self.requested_video_serial = self._selected_serial() if enabled else ''
        current = self.video
        if current is not None:
            if current.isRunning():
                if (canonical_mumu_serial(current.serial) ==
                        canonical_mumu_serial(self.requested_video_serial) and
                        not current.stopped.is_set()):
                    return
                current.stop()
                self.video_status.setText('Switching video…')
                return  # finished() will start the next encoder. Never overlap.
            self.video = None
            current.deleteLater()
        self._start_requested_video()

    def _cached_session_frame(self, serial: str):
        # The ADB transport can be listed as localhost:<port> or
        # 127.0.0.1:<port>. Only reuse frames from the same actual device.
        device = canonical_mumu_serial(serial)
        session = next((s for s in self.manager.snapshot()
                        if canonical_mumu_serial(s.serial) == device), None)
        return session.recent_image() if session is not None else None

    def _start_requested_video(self):
        self.video_serial = ''
        self.last_video_serial = ''
        serial = self.requested_video_serial
        if not serial:
            self.video_status.setText('Detector preview')
            return
        self.video_serial = serial
        thread = VideoThread(
            serial, self, snapshot_source=lambda: self._cached_session_frame(serial))
        self.video = thread
        thread.error.connect(self._video_error)
        thread.finished.connect(self._video_finished)
        self.video_status.setText('Connecting H.264…')
        thread.start()

    def _video_finished(self):
        finished = self.sender()
        if finished is not self.video:
            if finished is not None:
                finished.deleteLater()
            return
        self.video = None
        self.video_serial = ''
        finished.deleteLater()
        if getattr(finished, 'decoder_unavailable', False):
            self.requested_video_serial = ''
            self.video_switch.blockSignals(True)
            self.video_switch.setChecked(False)
            self.video_switch.blockSignals(False)
            self.video_status.setText('PyAV missing · detector preview')
        elif self.requested_video_serial and self.video_switch.isChecked():
            self._start_requested_video()
        else:
            self.video_status.setText('Detector preview')

    def _video_error(self, message: str):
        self._log(message)
        if self.video_switch.isChecked():
            self.video_status.setText('ADB snapshot fallback · PyAV missing' if
                                      getattr(self.video, 'decoder_unavailable', False) else
                                      'Reconnecting H.264 · ADB snapshot fallback')

    def _frame(self):
        serial = self._selected_serial()
        if not serial:
            return
        now = time.monotonic()
        if (self.video and
                canonical_mumu_serial(self.video_serial) == canonical_mumu_serial(serial)):
            image, kind = self.video.take()
            if image is not None:
                self.preview.setPixmap(QPixmap.fromImage(image))
                self.last_video_serial = serial
                if kind == 'snapshot':
                    self.video_status.setText('Snapshot fallback · reconnecting H.264')
                elif int(now) != getattr(self, '_last_fps_second', -1):
                    self._last_fps_second = int(now)
                    self.video_status.setText(f'Live H.264 · {self.video.fps(now)} FPS')
                return
            if (self.video.is_fresh(now) and
                    canonical_mumu_serial(self.last_video_serial) == canonical_mumu_serial(serial)):
                # No new video frame yet: keep the last one displayed. Never
                # flash a slower OCR screenshot between decoded video frames.
                if int(now) != getattr(self, '_last_fps_second', -1):
                    self._last_fps_second = int(now)
                    fps = self.video.fps(now)
                    self.video_status.setText(f'Live H.264 · {fps} FPS' if fps else
                                              'Live H.264 · screen unchanged')
                return
            # The worker's own watchdog handles stalled encoders, even if the
            # Qt thread is busy. This timer only updates the image and status.
        # The existing screenshot is already cached by the workflow worker.
        # Refresh fallback only a few times per second to avoid unnecessary
        # pixmap creation at 30Hz when screenshots arrive at ~1Hz.
        if now - self.last_detector_display_at < 0.25:
            return
        self.last_detector_display_at = now
        session = next((s for s in self.manager.snapshot()
                        if canonical_mumu_serial(s.serial) == canonical_mumu_serial(serial)), None)
        if session:
            frame = session.latest()
            if frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image, _ = preview_image(rgb)
                self.preview.setPixmap(QPixmap.fromImage(image))

    def _tick(self):
        try:
            statuses=self.manager.tick_schedules(local_workflows(self.root))
            for status in statuses:self._log(status)
            for event in self.manager.drain_events():self._log(event)
            now=time.monotonic()
            if statuses or now-self.last_job_refresh>=10:
                self._refresh_jobs()
                self.last_job_refresh=now
        except Exception as exc:
            self._log(f'Scheduler: {exc}')
        selected=self._selected_serial()
        sessions=self.manager.snapshot()
        self.summary.setText(f'{len(sessions)} / 10 SESSIONS')
        self.session_table.blockSignals(True)
        self.session_table.setRowCount(len(sessions))
        for index,sess in enumerate(sessions):
            stat=sess.stats.snapshot()
            for j,value in enumerate([sess.serial,sess.package,sess.workflow,sess.status,
                                      str(stat.runs),format_duration(stat.elapsed)]):
                self.session_table.setItem(index,j,QTableWidgetItem(value))
        if selected:
            for index,sess in enumerate(sessions):
                if sess.serial==selected:self.session_table.selectRow(index);break
        elif sessions:
            self.session_table.selectRow(0)
        self.session_table.blockSignals(False)
        if not sessions:
            self.preview.clear()
            self.preview.setText('No active emulator selected')
            if self.manager.completed_sessions:
                last=self.manager.completed_sessions[-1]
                stats=last.stats.snapshot()
                self.session_info.setText(f'Last: {last.serial} · {stats.runs} runs · '
                                           f'Avg {format_duration(stats.average)} · '
                                           f'Total {format_duration(stats.elapsed)}')
            else:
                self.session_info.setText('No sessions have run yet.')
        selected=self._selected_serial()
        if (self.video_switch.isChecked() and
                canonical_mumu_serial(selected) != canonical_mumu_serial(self.video_serial)):
            self._toggle_video(bool(selected))
        elif not selected and self.video:
            self._toggle_video(False)
        sess=next((s for s in sessions if s.serial==selected),None)
        if sess:
            stat=sess.stats.snapshot()
            self.session_info.setText(f'{sess.current} · {sess.confidence:.0%} confidence   |   '
                    f'{stat.runs} runs   ·   Avg {format_duration(stat.average)}   ·   '
                    f'Fastest {format_duration(stat.fastest)}   ·   {stat.per_hour:.1f}/hr')
            log='\n'.join(sess.logs)
            if log!=self.last_log_text:
                self.logs.setPlainText(log)
                self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())
                self.last_log_text=log

    def _refresh_workflows(self):
        workflows=local_workflows(self.root)
        previous=self.workflow.currentText() if hasattr(self,'workflow') else self.config.get('workflow','')
        self.workflow.blockSignals(True);self.workflow.clear();self.workflow.addItems(list(workflows))
        if previous in workflows:self.workflow.setCurrentText(previous)
        self.workflow.blockSignals(False)
        self.workflow_table.setRowCount(len(workflows))
        for i,(name,path) in enumerate(workflows.items()):
            import json
            try:count=len(json.loads((path/'manifest.json').read_text())['states'])
            except (OSError,KeyError,ValueError):count=0
            for j,value in enumerate((name,str(count),str(path))):
                self.workflow_table.setItem(i,j,QTableWidgetItem(value))
        self.workflow_table.resizeColumnsToContents()

    def _selected_workflow(self):
        row=self.workflow_table.currentRow()
        return self.workflow_table.item(row,0).text() if row>=0 and self.workflow_table.item(row,0) else self.workflow.currentText()

    def _new_workflow(self):
        from swordx.workflows import create_workflow
        name,ok=QInputDialog.getText(self,'New workflow','Workflow name:')
        if not ok:return
        try:create_workflow(name,self.root)
        except (OSError,ValueError) as exc:self._warning(str(exc));return
        self._refresh_workflows();self.workflow.setCurrentText(name.strip())

    def _duplicate_workflow(self):
        from swordx.storage import duplicate_workflow
        name=self._selected_workflow()
        suggestion=name[:41]+' (copy)'
        copy,ok=QInputDialog.getText(self,'Duplicate workflow','New workflow name:',text=suggestion)
        if not ok:return
        try:duplicate_workflow(name,copy,base=self.root)
        except (OSError,ValueError) as exc:self._warning(str(exc));return
        self._refresh_workflows();self.workflow.setCurrentText(copy.strip())

    def _studio(self):
        selected=self._selected_workflow()
        if any(sess.workflow==selected for sess in self.manager.snapshot()):
            self._warning('Stop sessions using this workflow before editing it');return
        dialog=OmniStudio(self.serial.currentText().strip(),selected,self,base=self.root)
        dialog.exec()
        self._refresh_workflows()

    def _delete_workflow(self):
        from swordx.storage import delete_workflow
        selected=self._selected_workflow()
        if selected not in local_workflows(self.root):return
        if any(s.workflow==selected for s in self.manager.snapshot()):
            self._warning('Stop active sessions using this workflow before deleting it');return
        if any(job.workflow==selected for job in self.db.list_jobs()):
            self._warning('Delete or edit schedules using this workflow before deleting it');return
        if any(self.profiles.get(name).workflow==selected for name in self.profiles.names()):
            self._warning('Change presets referencing this workflow before deleting it');return
        if QMessageBox.question(self,'Delete workflow',
                 f'Permanently delete {selected} and its recorded images?')!=QMessageBox.Yes:return
        try:delete_workflow(selected,self.profiles,base=self.root)
        except (OSError,ValueError) as exc:self._warning(str(exc));return
        self._refresh_workflows()

    def _export(self):
        output,_=QFileDialog.getSaveFileName(self,'Export workflows and profiles','OmniFlow-backup.zip','ZIP files (*.zip)')
        if not output:return
        # Existing backup format is compatible with previous SwordXAuto builds.
        try:export_backup(Path(output),ProfileStore(self.root),base=self.root)
        except (OSError,ValueError) as exc:self._warning(str(exc))

    def _import(self):
        source,_=QFileDialog.getOpenFileName(self,'Import workflow backup','','ZIP files (*.zip)')
        if not source:return
        try:import_backup(Path(source),ProfileStore(self.root),base=self.root)
        except (OSError,ValueError) as exc:self._warning(str(exc));return
        self._refresh_workflows()

    def _refresh_jobs(self):
        selection=self._job() if self.job_table.currentRow()>=0 else None
        selected_id=selection.id if selection else None
        jobs=self.db.list_jobs()
        self.job_table.setRowCount(len(jobs))
        for i,job in enumerate(jobs):
            next_when=(datetime.fromtimestamp(job.next_at,ZoneInfo(job.timezone)).strftime('%Y-%m-%d %H:%M')
                       if job.next_at is not None else '—')
            for j,value in enumerate([job.name,job.serial,job.workflow,job.kind,
                                      next_when,job.last_status,'ON' if job.enabled else 'OFF']):
                cell=QTableWidgetItem(str(value))
                cell.setData(Qt.UserRole,job.id)
                self.job_table.setItem(i,j,cell)
        self.job_table.resizeColumnsToContents()
        if selected_id:
            for i, job in enumerate(jobs):
                if job.id==selected_id:self.job_table.selectRow(i);break
        lookup={job.id:job for job in jobs}
        history=self.db.history(50)
        self.history_table.setRowCount(len(history))
        for i,(ident,intended,claimed,outcome) in enumerate(history):
            job=lookup.get(ident)
            zone=ZoneInfo(job.timezone) if job else ZoneInfo('UTC')
            stamp=datetime.fromtimestamp(intended,zone).strftime('%Y-%m-%d %H:%M')
            for j,value in enumerate((job.name if job else ident[:10],stamp,outcome)):
                self.history_table.setItem(i,j,QTableWidgetItem(str(value)))
        self.history_table.resizeColumnsToContents()

    def _job(self):
        row=self.job_table.currentRow()
        if row<0 or not self.job_table.item(row,0):return None
        ident=self.job_table.item(row,0).data(Qt.UserRole)
        return next((x for x in self.db.list_jobs() if x.id==ident),None)

    def _save_job(self,job=None):
        dlg=ScheduleDialog(self.devices,list(local_workflows(self.root)),job,self)
        if job is None and self.serial.currentText():dlg.device.setCurrentText(self.serial.currentText())
        if job is None and self.package.text():dlg.package.setText(self.package.text())
        if job is None and self.workflow.currentText():dlg.workflow.setCurrentText(self.workflow.currentText())
        if dlg.exec()!=QDialog.Accepted:return
        try:
            self.db.save(**dlg.values(),job_id=job.id if job else None)
        except (ValueError,KeyError,OverflowError) as exc:self._warning(str(exc));return
        self._refresh_jobs()

    def _add_schedule(self):self._save_job()
    def _edit_schedule(self):
        job=self._job()
        if job:self._save_job(job)
    def _toggle_job(self):
        job=self._job()
        if job:
            try:self.db.set_enabled(job.id,not job.enabled)
            except ValueError as exc:self._warning(str(exc));return
            self._refresh_jobs()
    def _delete_job(self):
        job=self._job()
        if job and QMessageBox.question(self,'Delete schedule',f'Delete "{job.name}"?')==QMessageBox.Yes:
            self.db.delete(job.id);self._refresh_jobs()

    def closeEvent(self,event):
        self.timer.stop();self.frame_timer.stop()
        self.requested_video_serial = ''
        if self.video:
            self.video.stop()
            # Encoder is already killed; waiting here only ensures Qt does not
            # destroy a running QThread during application shutdown.
            self.video.wait(5000)
        self.manager.stop_all()
        # Wait for owned leases to be released before quitting, without
        # freezing the UI for a long-running Android command.
        for session in self.manager.snapshot():
            if session.worker:session.worker.join(timeout=1)
        super().closeEvent(event)


STYLE='''
QWidget { background:#17181a; color:#ece6d8; font: 12px "Segoe UI"; }
QMainWindow,QDialog { background:#151618; }
QLabel { background:transparent; }
QLabel#brand { font-size:29px; font-weight:900; color:#e8c873; letter-spacing:3px; }
QLabel#muted { color:#aaa395; font-size:11px; }
QLabel#summary { color:#9fe5ad; font-weight:800; background:#213729; border-radius:8px; padding:8px 13px; }
QFrame#panel { background:#242425; border:1px solid #4e432b; border-radius:12px; }
QTabWidget::pane { border:1px solid #4b4331; background:#222326; border-radius:7px; }
QTabBar::tab { background:#2a2a2c; padding:10px 18px; color:#b8ab8a; font-weight:650; }
QTabBar::tab:selected { background:#69532a; color:#fbe8b6; }
QPushButton { background:#373229; border:1px solid #6c5a37; color:#eddec0; border-radius:7px; padding:8px; font-weight:750; }
QPushButton:hover { background:#5b4a2f; }
QPushButton#gold { background:#c29b4c; color:#17130d; border-color:#e6c77a; }
QPushButton#success { background:#276c48; color:#eefff3; border-color:#418e60; }
QPushButton#danger { background:#783c38; color:#fff0eb; border-color:#aa5b52; }
QPushButton:disabled { background:#2b2c2d; color:#787a7c; border-color:#3d3e40; }
QComboBox,QLineEdit,QSpinBox,QDateTimeEdit,QTimeEdit,QPlainTextEdit,QListWidget,QTableWidget {
 background:#1a1b1d; color:#f4e8d0; border:1px solid #62523a; border-radius:6px; padding:6px;
 selection-background-color:#745d2c;
}
QTableWidget { gridline-color:#333538; alternate-background-color:#272827; }
QHeaderView::section { background:#343128; color:#dbbf7e; padding:8px; border:1px solid #514730; }
QLabel#preview { border:1px solid #524733; border-radius:8px; background:#121314; color:#9c957f; }
QCheckBox { spacing:7px; }
QCheckBox::indicator { width:16px; height:16px; }
QCheckBox::indicator:checked { background:#c39a43; border:1px solid #e8cb7c; border-radius:3px; }
QLabel#title { color:#e8c873; font-size:22px; font-weight:900; }
QLabel#overline { color:#d4b46e; font-weight:750; font-size:11px; }
QLabel#hint { color:#bbb1a0; font-size:11px; }
QLabel#stateBanner { padding:9px; border-radius:6px; font-weight:800; }
QLabel#stateBanner[kind="action"] { background:#24432d; color:#9ae6b0; }
QLabel#stateBanner[kind="wait"] { background:#432927; color:#edaba1; }
QLabel#stateBanner[kind="new"] { background:#403826; color:#f0d182; }
QPushButton#regionTool { border-color:#3b9467; color:#a1e6b8; }
QPushButton#gestureTool { border-color:#9b7d3d; color:#ecd18f; }
QPushButton#regionTool[selected="true"] { background:#286b48; color:#fff; }
QPushButton#gestureTool[selected="true"] { background:#ae8744; color:#17130c; }
QPushButton#primary { background:#cba85b; color:#18140d; }
QScrollBar:vertical { background:#242526; width:11px; }
QScrollBar::handle:vertical { background:#665332; border-radius:5px; }
QStatusBar { color:#b9b0a0; }
'''


def main():
    import sys
    # Do not spin up dozens of OpenCV worker threads for ten independent instances.
    cv2.setNumThreads(1)
    app=QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    window=OmniWindow();window.show()
    return app.exec()
