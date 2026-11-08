"""Editable click-to-record UI for one- or two-region visual states."""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QFrame,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .adb import ADB, ADBError
from .detection import SIZE, normalize
from .storage import duplicate_workflow
from .workflows import (
    BUILTIN, create_workflow, list_workflows, load_step, record_step,
    remove_step, reorder_steps, step_order, update_step_settings,
)


class ReorderList(QListWidget):
    """Persist presentation order after an internal drag; no runtime ordering."""
    reordered = Signal(list)

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setDragEnabled(True)

    def dropEvent(self, event):
        before = self.keys()
        # Dragging is presentation-only; do not interpret Qt's temporary
        # current-item changes as a request to discard the editor draft.
        blocked = self.blockSignals(True)
        try:
            super().dropEvent(event)
        finally:
            self.blockSignals(blocked)
        after = self.keys()
        if before != after:
            self.reordered.emit(after)

    def keys(self) -> list[str]:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]


class CaptureCanvas(QWidget):
    """Edit up to two region/gesture pairs on an aspect-correct game capture."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(310, 550)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.frame: np.ndarray | None = None
        self.picture: QPixmap | None = None
        # Modes 0–2: region/tap/drag 1; modes 3–5: region/tap/drag 2.
        self.mode = 0
        self.boxes: list[tuple[int, int, int, int] | None] = [None, None]
        self.gestures: list[dict | None] = [None, None]
        self.taps: list[tuple[int, int] | None] = [None, None]  # Legacy GUI compatibility.
        self.drag_durations = [700, 700]
        self.hide_gestures = False
        self.extra_zones: list[dict] = []  # Universal OCR/color/negative overlays.
        self.down: QPoint | None = None
        self.hover: QPoint | None = None

    def _set_gesture(self, index: int, gesture: dict | None):
        self.gestures[index] = gesture
        self.taps[index] = (tuple(gesture['point']) if gesture and gesture['kind'] == 'tap'
                            else None)
        self.changed.emit()
        self.update()

    def load(self, frame: np.ndarray, *,
             boxes: list[tuple[int, int, int, int] | None] | None = None,
             taps: list[tuple[int, int] | None] | None = None,
             gestures: list[dict] | None = None) -> None:
        self.frame = frame.copy()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.picture = QPixmap.fromImage(QImage(
            rgb.data, SIZE[0], SIZE[1], rgb.strides[0], QImage.Format_RGB888).copy())
        self.boxes = ([*boxes, None, None][:2] if boxes is not None else [None, None])
        if gestures is None:
            gestures = [{'kind': 'tap', 'point': list(tap)} if tap is not None else None
                        for tap in ([*taps, None, None][:2] if taps is not None else [None, None])]
        self.gestures = ([*(dict(item) if item else None for item in gestures), None, None][:2])
        self.taps = [tuple(item['point']) if item and item['kind'] == 'tap' else None
                     for item in self.gestures]
        self.drag_durations = [item['duration_ms'] if item and item['kind'] == 'drag' else 700
                               for item in self.gestures]
        self.down = self.hover = None
        self.mode = 0
        self.update()
        self.changed.emit()

    def replace_frame(self, frame: np.ndarray) -> None:
        """Recapture without changing the regions and gestures."""
        durations = self.drag_durations.copy()
        self.load(frame, boxes=self.boxes.copy(), gestures=self.gestures.copy())
        self.drag_durations = durations

    def clear_marks(self) -> None:
        self.boxes = [None, None]
        self.gestures = [None, None]
        self.taps = [None, None]
        self.drag_durations = [700, 700]
        self.mode = 0
        self.down = self.hover = None
        self.update()
        self.changed.emit()

    def set_drag_duration(self, index: int, duration_ms: int) -> None:
        self.drag_durations[index] = duration_ms
        item = self.gestures[index]
        if item and item['kind'] == 'drag':
            self._set_gesture(index, {**item, 'duration_ms': duration_ms})

    def _rect(self) -> QRect:
        width = min(self.width(), round(self.height() * SIZE[0] / SIZE[1]))
        height = round(width * SIZE[1] / SIZE[0])
        if height > self.height():
            height = self.height()
            width = round(height * SIZE[0] / SIZE[1])
        return QRect((self.width()-width)//2, (self.height()-height)//2, width, height)

    def _point(self, event) -> QPoint | None:
        rect = self._rect()
        pos = event.position().toPoint()
        if not rect.contains(pos):
            return None
        return QPoint(min(SIZE[0]-1, max(0, round((pos.x()-rect.x()) * SIZE[0] / rect.width()))),
                      min(SIZE[1]-1, max(0, round((pos.y()-rect.y()) * SIZE[1] / rect.height()))))

    def _screen_point(self, x: int, y: int) -> QPoint:
        rect = self._rect()
        return QPoint(rect.x() + round(x * rect.width() / SIZE[0]),
                      rect.y() + round(y * rect.height() / SIZE[1]))

    def _screen_rect(self, box: tuple[int, int, int, int]) -> QRect:
        x1, y1, x2, y2 = box
        return QRect(self._screen_point(x1, y1), self._screen_point(x2, y2))

    @staticmethod
    def _draw_marker(painter: QPainter, pos: QPoint, label: str, color: QColor):
        painter.setPen(QPen(color, 3))
        painter.setBrush(QColor(15, 20, 28, 190))
        painter.drawEllipse(pos, 12, 12)
        painter.setPen(QColor('#ffffff'))
        painter.drawText(QRect(pos.x()-9, pos.y()-9, 18, 18), Qt.AlignCenter, label)

    @staticmethod
    def _draw_arrow(painter: QPainter, a: QPoint, b: QPoint, color: QColor):
        painter.setPen(QPen(color, 3))
        painter.drawLine(a, b)
        angle = math.atan2(b.y()-a.y(), b.x()-a.x())
        for offset in (-0.5, 0.5):
            painter.drawLine(b, QPoint(round(b.x()-18*math.cos(angle+offset)),
                                       round(b.y()-18*math.sin(angle+offset))))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#0b131c'))
        screen = self._rect()
        if self.picture is None:
            painter.setPen(QColor('#a0b2c2'))
            painter.drawText(screen, Qt.AlignCenter, 'Capture a screen or select a saved state')
            return
        painter.drawPixmap(screen, self.picture)
        for i, box in enumerate(self.boxes):
            if box:
                color = (QColor('#df7771') if self.hide_gestures else
                         QColor('#79d49c') if i == 0 else QColor('#b0dfa7'))
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self._screen_rect(box))
                pos = self._screen_point(box[0], box[1])
                painter.fillRect(QRect(pos.x(), pos.y()-18, 73, 17), QColor('#17232b'))
                painter.setPen(color)
                painter.drawText(pos.x()+4, pos.y()-5, f'Region {i+1}')
        if self.down and self.hover:
            if self.mode % 3 == 0:
                a, b = self.down, self.hover
                painter.setPen(QPen(QColor('#79d49c'), 2, Qt.DashLine))
                painter.drawRect(self._screen_rect((min(a.x(), b.x()), min(a.y(), b.y()),
                                                    max(a.x(), b.x()), max(a.y(), b.y()))))
            elif self.mode % 3 == 2:
                self._draw_arrow(painter, self._screen_point(self.down.x(), self.down.y()),
                                 self._screen_point(self.hover.x(), self.hover.y()), QColor('#ffe08a'))
        for i, gesture in enumerate(self.gestures):
            if gesture is None or self.hide_gestures:
                continue
            color = QColor('#f0cb70') if i == 0 else QColor('#e5aa57')
            if gesture['kind'] == 'tap':
                self._draw_marker(painter, self._screen_point(*gesture['point']), str(i+1), color)
            else:
                start = self._screen_point(*gesture['start'])
                end = self._screen_point(*gesture['end'])
                self._draw_arrow(painter, start, end, color)
                self._draw_marker(painter, start, f'{i+1}S', color)
                if start != end:
                    self._draw_marker(painter, end, f'{i+1}E', color)
        # Show composable recognition rules without occupying the 2 legacy
        # gesture slots. These are visual guides only: detector state is stored
        # by OmniStudio in the manifest's condition list.
        for i, rule in enumerate(self.extra_zones):
            bounds = rule.get('rect')
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
                continue
            x1, y1, x2, y2 = bounds
            if not (0 <= x1 < x2 <= SIZE[0] and 0 <= y1 < y2 <= SIZE[1]):
                continue
            color = QColor('#e7817b' if rule.get('negate') else
                           '#7bd6e6' if rule.get('type') == 'ocr' else '#eac674')
            painter.setPen(QPen(color, 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._screen_rect((x1,y1,x2,y2)))
            tag = ('NOT ' if rule.get('negate') else '') + str(rule.get('type','rule')).upper()
            where = self._screen_point(x1, y1)
            painter.fillRect(QRect(where.x(), where.y()-17, max(72, 10*len(tag)), 17), QColor('#18222a'))
            painter.setPen(color)
            painter.drawText(where.x()+3, where.y()-4, tag)
        painter.end()

    @staticmethod
    def _distance_sq_to_line(p: QPoint, a: QPoint, b: QPoint) -> float:
        vx, vy = b.x()-a.x(), b.y()-a.y()
        if vx == vy == 0:
            return (p.x()-a.x())**2 + (p.y()-a.y())**2
        t = max(0., min(1., ((p.x()-a.x())*vx+(p.y()-a.y())*vy)/(vx*vx+vy*vy)))
        return (p.x()-a.x()-t*vx)**2 + (p.y()-a.y()-t*vy)**2

    def remove_at(self, pos: QPoint) -> bool:
        """Right-click gestures first (including drag lines), then regions."""
        distances = []
        for i, gesture in enumerate(self.gestures):
            if gesture is None:
                continue
            if gesture['kind'] == 'tap':
                a = b = self._screen_point(*gesture['point'])
            else:
                a = self._screen_point(*gesture['start'])
                b = self._screen_point(*gesture['end'])
            distances.append((self._distance_sq_to_line(pos, a, b), i))
        if distances:
            distance, index = min(distances)
            if distance <= 18**2:
                self._set_gesture(index, None)
                return True
        for i in (1, 0):
            if self.boxes[i] is not None and self._screen_rect(self.boxes[i]).contains(pos):
                self.boxes[i] = None
                self._set_gesture(i, None)
                # Region 1 remains mandatory; promote pair 2 with its gesture.
                if i == 0 and self.boxes[1] is not None:
                    self.boxes[0], self.boxes[1] = self.boxes[1], None
                    self.gestures[0], self.gestures[1] = self.gestures[1], None
                    self.taps[0], self.taps[1] = self.taps[1], None
                    self.drag_durations[0], self.drag_durations[1] = self.drag_durations[1], 700
                    self.changed.emit()
                    self.update()
                return True
        return False

    def mousePressEvent(self, event):
        point = self._point(event)
        if point is None or self.frame is None:
            return
        if event.button() == Qt.RightButton:
            self.remove_at(event.position().toPoint())
            return
        if event.button() != Qt.LeftButton:
            return
        index, tool = divmod(self.mode, 3)
        if tool == 1:
            self._set_gesture(index, {'kind': 'tap', 'point': [point.x(), point.y()]})
        else:
            self.down = self.hover = point

    def mouseMoveEvent(self, event):
        if self.down is not None:
            self.hover = self._point(event)
            self.update()

    def mouseReleaseEvent(self, event):
        if self.down is None:
            return
        point = self._point(event)
        if point is not None:
            start = self.down
            index, tool = divmod(self.mode, 3)
            if tool == 0:
                box = (min(start.x(), point.x()), min(start.y(), point.y()),
                       max(start.x(), point.x()), max(start.y(), point.y()))
                if box[2]-box[0] >= 18 and box[3]-box[1] >= 15:
                    self.boxes[index] = box
                    self.changed.emit()
            elif tool == 2:
                self._set_gesture(index, {'kind': 'drag', 'start': [start.x(), start.y()],
                                          'end': [point.x(), point.y()],
                                          'duration_ms': self.drag_durations[index]})
        self.down = self.hover = None
        self.update()


class RecorderDialog(QDialog):
    def __init__(self, serial: str, selected_workflow: str = '', parent=None, base=None):
        super().__init__(parent)
        self.base = base
        self.serial = serial
        self.selected_workflow = selected_workflow
        self.editing_key: str | None = None
        self.needs_recapture = False
        self._loading = False
        self._visual_dirty = False
        self._dirty = False
        self._workflow_index = 0
        self._persisted_step_order: list[str] = []
        self.setWindowTitle('Workflow Studio · Sword x Staff')
        self.resize(1080, 900)
        self.setMinimumSize(850, 760)
        self._build()
        self._refresh_workflows()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        canvas_column = QVBoxLayout()
        canvas_column.setSpacing(9)
        canvas_title = QLabel('SCREEN PREVIEW  /  DETECTION MAP')
        canvas_title.setObjectName('overline')
        canvas_column.addWidget(canvas_title)
        self.canvas = CaptureCanvas()
        canvas_column.addWidget(self.canvas, 1)
        canvas_legend = QLabel('GREEN  Detection regions     GOLD  Taps / swipes     RED  Wait states\n'
                               'Left-click to draw or place  ·  Right-click to remove')
        canvas_legend.setObjectName('hint')
        canvas_legend.setWordWrap(True)
        canvas_column.addWidget(canvas_legend)
        layout.addLayout(canvas_column, 3)

        panel = QWidget()
        side = QVBoxLayout(panel)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        layout.addWidget(panel, 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)
        side.addWidget(scroll, 1)
        sidebar = QWidget()
        right = QVBoxLayout(sidebar)
        right.setContentsMargins(4, 2, 8, 4)
        right.setSpacing(9)
        scroll.setWidget(sidebar)

        title = QLabel('WORKFLOW STUDIO')
        title.setObjectName('title')
        right.addWidget(title)
        tip = QLabel('Select a screen state to inspect its capture, detection regions and action.')
        tip.setObjectName('hint')
        tip.setWordWrap(True)
        right.addWidget(tip)

        right.addWidget(QLabel('WORKFLOW'))
        choices = QHBoxLayout()
        self.workflows = QComboBox()
        self.workflows.currentIndexChanged.connect(self._load_steps)
        choices.addWidget(self.workflows, 1)
        create = QPushButton('+ New')
        create.clicked.connect(self._new_workflow)
        choices.addWidget(create)
        self.duplicate = QPushButton('Duplicate')
        self.duplicate.setToolTip('Make a separate editable copy of the selected workflow')
        self.duplicate.clicked.connect(self._duplicate_workflow)
        choices.addWidget(self.duplicate)
        right.addLayout(choices)

        right.addWidget(self._section_label('RECORDED SCREEN STATES  ·  DRAG TO REORDER'))
        self.steps = ReorderList()
        self.steps.setMinimumHeight(195)
        self.steps.currentItemChanged.connect(self._select_step)
        self.steps.reordered.connect(self._reordered)
        right.addWidget(self.steps)
        row = QHBoxLayout()
        fresh = QPushButton('+ New state')
        self.new_state_button = fresh
        fresh.clicked.connect(self._new_state)
        row.addWidget(fresh, 1)
        delete = QPushButton('Delete selected')
        self.delete_button = delete
        delete.setObjectName('danger')
        delete.clicked.connect(self._delete)
        row.addWidget(delete, 1)
        right.addLayout(row)

        self.selected_state_banner = QLabel('NO STATE SELECTED')
        self.selected_state_banner.setObjectName('stateBanner')
        self.selected_state_banner.setProperty('kind', 'empty')
        right.addWidget(self.selected_state_banner)
        right.addWidget(self._section_label('STATE NAME  /  SOURCE IMAGE'))
        self.step_name = QLineEdit()
        self.step_name.setPlaceholderText('State name, e.g. Start match')
        self.step_name.textChanged.connect(self._field_changed)
        right.addWidget(self.step_name)
        capture = QPushButton('Capture / replace MuMu screen')
        self.capture_button = capture
        capture.setObjectName('subtle')
        capture.clicked.connect(self._capture)
        right.addWidget(capture)
        tabs = QTabWidget()
        self.tabs = tabs
        tabs.setObjectName('editorTabs')
        detection_tab = QWidget()
        visual_layout = QVBoxLayout(detection_tab)
        visual_layout.setSpacing(9)
        behavior_tab = QWidget()
        timing_layout = QVBoxLayout(behavior_tab)
        timing_layout.setSpacing(9)
        tabs.addTab(detection_tab, 'Detection & gestures')
        tabs.addTab(behavior_tab, 'Timing & retry')
        right.addWidget(tabs)
        self.passive = QCheckBox('WAIT STATE  ·  Detect but do not tap')
        self.passive.setObjectName('passive')
        self.passive.setToolTip('For Cancel, waiting, or other screens where no tap is safe. '
                                'Only a detection region is needed.')
        self.passive.toggled.connect(self._passive_changed)
        visual_layout.addWidget(self.passive)
        visual_layout.addWidget(self._section_label('DETECTION REGIONS  →  ACTIONS'))
        actions = QGridLayout()
        self.buttons: list[QPushButton] = []
        for i, label in enumerate(('Region 1', 'Tap 1', 'Drag 1',
                                   'Region 2', 'Tap 2', 'Drag 2')):
            button = QPushButton(label)
            button.setObjectName('regionTool' if i % 3 == 0 else 'gestureTool')
            button.clicked.connect(lambda _, mode=i: self._set_mode(mode))
            self.buttons.append(button)
            actions.addWidget(button, i // 3, i % 3)
        visual_layout.addLayout(actions)
        self.progress = QLabel('Capture a screen to begin.')
        self.progress.setWordWrap(True)
        visual_layout.addWidget(self.progress)
        visual_layout.addWidget(QLabel('DRAG / LONG PRESS DURATION'))
        durations = QHBoxLayout()
        self.duration_inputs: list[QSpinBox] = []
        for index in range(2):
            durations.addWidget(QLabel(f'{index+1}:'))
            field = QSpinBox()
            field.setRange(100, 10000)
            field.setSingleStep(100)
            field.setSuffix(' ms')
            field.setValue(700)
            field.valueChanged.connect(lambda duration, i=index: self.canvas.set_drag_duration(i, duration))
            self.duration_inputs.append(field)
            durations.addWidget(field)
        visual_layout.addLayout(durations)
        self.canvas.changed.connect(self._canvas_changed)

        timing_layout.addWidget(QLabel('STATE BEHAVIOR'))
        self.terminal = QCheckBox('End of run (count after leaving this state)')
        self.terminal.toggled.connect(self._field_changed)
        timing_layout.addWidget(self.terminal)
        wait_row = QHBoxLayout()
        wait_row.addWidget(QLabel('Wait before action:'))
        self.wait_minutes = QSpinBox()
        self.wait_minutes.setRange(0, 1440)
        self.wait_minutes.setSuffix(' min')
        self.wait_minutes.valueChanged.connect(self._field_changed)
        wait_row.addWidget(self.wait_minutes)
        self.wait_seconds = QSpinBox()
        self.wait_seconds.setRange(0, 59)
        self.wait_seconds.setSuffix(' sec')
        self.wait_seconds.valueChanged.connect(self._field_changed)
        wait_row.addWidget(self.wait_seconds)
        timing_layout.addLayout(wait_row)

        self.retry = QCheckBox('Retry if action was missed')
        self.retry.setToolTip('Only retries if a matching action state is still present. A Wait state blocks actions.')
        self.retry.toggled.connect(self._field_changed)
        timing_layout.addWidget(self.retry)
        retry_row = QHBoxLayout()
        self.retry_mode = QComboBox()
        self.retry_mode.addItem('After interval (same screen)', 'interval')
        self.retry_mode.addItem('Only after leaving and returning', 'on_return')
        self.retry_mode.setToolTip('For Match/Cancel buttons, use only after leaving and returning.')
        self.retry_mode.currentIndexChanged.connect(self._field_changed)
        retry_row.addWidget(self.retry_mode, 1)
        self.retry_interval = QSpinBox()
        self.retry_interval.setRange(1, 86400)
        self.retry_interval.setValue(25)
        self.retry_interval.setSuffix(' sec')
        self.retry_interval.setToolTip('Minimum interval between actions; not an automatic queue timeout.')
        self.retry_interval.valueChanged.connect(self._field_changed)
        retry_row.addWidget(self.retry_interval)
        timing_layout.addLayout(retry_row)
        retry_limit = QHBoxLayout()
        retry_limit.addWidget(QLabel('Maximum extra taps:'))
        self.max_extra_taps = QSpinBox()
        self.max_extra_taps.setRange(0, 20)
        self.max_extra_taps.setSpecialValueText('Unlimited · legacy')
        self.max_extra_taps.setToolTip('Only for timed retries. 2 = initial tap + at most two extra taps until state changes. 0 retains older unlimited behavior.')
        self.max_extra_taps.valueChanged.connect(self._field_changed)
        retry_limit.addWidget(self.max_extra_taps, 1)
        timing_layout.addLayout(retry_limit)
        self.retry.toggled.connect(self._retry_limit_visibility)
        self.retry_mode.currentIndexChanged.connect(self._retry_limit_visibility)
        self.max_extra_taps.setEnabled(False)
        self.retry.toggled.connect(self.retry_mode.setEnabled)
        self.retry.toggled.connect(self.retry_interval.setEnabled)
        self.retry_mode.setEnabled(False)
        self.retry_interval.setEnabled(False)

        hint = QLabel('For a Wait state, draw a distinctive region around Cancel and leave taps empty. '
                      'Wait states take precedence over actions on the same screen. '
                      'Right-click markers or regions to remove them.')
        hint.setWordWrap(True)
        visual_layout.addWidget(hint)
        visual_layout.addStretch(1)
        timing_layout.addStretch(1)

        # One save action persists every field. It stays visible while scrolling.
        self.save = QPushButton('Save State')
        self.save.setObjectName('primary')
        self.save.clicked.connect(self._save)
        side.addWidget(self.save)
        self.save_status = QLabel('Select a state, or capture to create one.')
        self.save_status.setWordWrap(True)
        side.addWidget(self.save_status)
        done = QPushButton('Done · Use this workflow')
        self.done_button = done
        done.clicked.connect(self.accept)
        side.addWidget(done)
        self._set_mode(0)
        self._update_controls()

    @staticmethod
    def _section_label(title: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName('overline')
        return label

    def _update_controls(self):
        """Make unavailable actions visibly disabled, without losing their settings."""
        folder = self._folder()
        has_workflow = folder is not None
        capture = has_workflow and bool(self.serial)
        editing = has_workflow and self.editing_key is not None
        self.new_state_button.setEnabled(has_workflow)
        self.delete_button.setEnabled(editing)
        self.capture_button.setEnabled(capture)
        self.steps.setEnabled(has_workflow)
        self.step_name.setEnabled(has_workflow)
        self.tabs.setEnabled(has_workflow)
        self.save.setEnabled(has_workflow and self._dirty)
        self.done_button.setEnabled(has_workflow)
        self.duplicate.setEnabled(has_workflow)
        passive = self.passive.isChecked()
        has_frame = self.canvas.frame is not None
        for index in range(2):
            has_region = self.canvas.boxes[index] is not None
            can_region = has_workflow and has_frame and (index == 0 or self.canvas.boxes[0] is not None)
            self.buttons[index * 3].setEnabled(can_region)
            for offset in (1, 2):
                self.buttons[index * 3 + offset].setEnabled(can_region and has_region and not passive)
            self.duration_inputs[index].setEnabled(
                has_workflow and not passive and bool(self.canvas.gestures[index])
                and self.canvas.gestures[index]['kind'] == 'drag')
        if not has_workflow:
            heading, kind = 'SELECT OR CREATE A WORKFLOW', 'empty'
        elif not editing:
            heading, kind = 'NEW SCREEN STATE  ·  UNSAVED', 'new'
        elif passive:
            heading, kind = f'WAIT STATE  ·  {self.step_name.text()}', 'wait'
        else:
            heading, kind = f'ACTION STATE  ·  {self.step_name.text()}', 'action'
        self.selected_state_banner.setText(heading)
        if self.selected_state_banner.property('kind') != kind:
            self.selected_state_banner.setProperty('kind', kind)
            self.selected_state_banner.style().unpolish(self.selected_state_banner)
            self.selected_state_banner.style().polish(self.selected_state_banner)

    def _passive_changed(self, passive: bool):
        for button in (self.buttons[1], self.buttons[2], self.buttons[4], self.buttons[5]):
            button.setEnabled(not passive)
        self.retry.setEnabled(not passive)
        for control in (self.retry_mode, self.retry_interval):
            control.setEnabled(not passive and self.retry.isChecked())
        self._retry_limit_visibility()
        for control in (self.wait_minutes, self.wait_seconds):
            control.setEnabled(not passive)
        self.canvas.hide_gestures = passive
        self.canvas.update()
        if passive:
            self._set_mode(0)
        self._field_changed()
        self._progress()

    def _retry_limit_visibility(self, *_):
        self.max_extra_taps.setEnabled(not self.passive.isChecked() and self.retry.isChecked()
                                       and self.retry_mode.currentData() == 'interval')

    def _field_changed(self, *_):
        if not self._loading:
            self._dirty = True
            self.save_status.setText('Unsaved changes')
        self._update_controls()

    def _canvas_changed(self):
        if not self._loading:
            self._visual_dirty = True
            self._field_changed()
        self._progress()

    @staticmethod
    def _step_label(action: dict, region_count: int) -> str:
        gestures = action.get('gestures') or []
        types = ('WAIT / NO TAP' if action.get('passive') else
                 ', '.join(gesture.get('kind', 'tap') for gesture in gestures) or 'tap')
        wait_seconds = action.get('pre_tap_delay_seconds', 0)
        suffix = f' · wait {wait_seconds // 60}m {wait_seconds % 60}s' if wait_seconds else ''
        if action.get('retry', False):
            suffix += (' · retry on return' if action.get('retry_mode') == 'on_return'
                       else f' · retry every {action.get("retry_interval_seconds", 25)}s')
        return (('● ' if action.get('end') else '  ') + action['label'] +
                f' · {region_count} region(s) · {types}' + suffix)

    def _confirm_discard(self) -> bool:
        return (not self._dirty or QMessageBox.question(
            self, 'Unsaved state', 'Discard the unsaved state changes?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes)

    def accept(self):
        if self._confirm_discard():
            super().accept()

    def reject(self):
        if self._confirm_discard():
            super().reject()

    def _refresh_workflows(self):
        keep = self.selected_workflow or self.workflows.currentText()
        self.workflows.blockSignals(True)
        self.workflows.clear()
        self.workflows.addItem('Select or create workflow…', '')
        for name, folder in list_workflows(self.base).items():
            if name != BUILTIN:
                self.workflows.addItem(name, str(folder))
        self.workflows.blockSignals(False)
        index = self.workflows.findText(keep)
        self.workflows.setCurrentIndex(max(0, index))
        self._load_steps()
        self.duplicate.setEnabled(self._folder() is not None)

    def _folder(self) -> Path | None:
        path = self.workflows.currentData()
        return Path(path) if path else None

    def _new_workflow(self):
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, 'New workflow', 'Workflow name:')
        if not ok:
            return
        try:
            create_workflow(name, self.base)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Workflow', str(exc))
            return
        self._dirty = False
        self.selected_workflow = name.strip()
        self._refresh_workflows()

    def _duplicate_workflow(self):
        source = self.workflows.currentText() if self._folder() else ''
        if not source:
            QMessageBox.information(self, 'Duplicate workflow', 'Select a recorded workflow first.')
            return
        if not self._confirm_discard():
            return
        occupied = {name.casefold() for name in list_workflows(self.base)}
        suggested = ''
        for index in range(1, 10000):
            suffix = ' (copy)' if index == 1 else f' (copy {index})'
            candidate = source[:48 - len(suffix)] + suffix
            if candidate.casefold() not in occupied:
                suggested = candidate
                break
        name, ok = QInputDialog.getText(self, 'Duplicate workflow',
                                        'Name for the new, independent workflow:',
                                        text=suggested)
        if not ok:
            return
        try:
            duplicate_workflow(source, name, base=self.base)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, 'Could not duplicate workflow', str(exc))
            return
        self._dirty = False
        self.selected_workflow = name.strip()
        self._refresh_workflows()
        self.save_status.setText('Workflow copied. Select any state to edit the copy.')

    def _load_steps(self, *_):
        if not self._confirm_discard():
            self.workflows.blockSignals(True)
            self.workflows.setCurrentIndex(self._workflow_index)
            self.workflows.blockSignals(False)
            return
        self._workflow_index = self.workflows.currentIndex()
        self.selected_workflow = self.workflows.currentText() if self._folder() else ''
        self.duplicate.setEnabled(self._folder() is not None)
        self._dirty = False
        self.steps.blockSignals(True)
        self.steps.clear()
        self.steps.blockSignals(False)
        self._persisted_step_order = []
        # A workflow switch must not leave another workflow's screenshot visible.
        self.canvas.frame = None
        self.canvas.picture = None
        self.canvas.update()
        self._new_state(confirm=False)
        folder = self._folder()
        if folder:
            data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            self._persisted_step_order = step_order(data)
            self.steps.blockSignals(True)
            for key in self._persisted_step_order:
                action = data['actions'][key]
                row = QListWidgetItem(self._step_label(action, len(data['states'][key])))
                row.setData(Qt.UserRole, key)
                tint = ('#ec9891' if action.get('passive') else
                        '#edc56d' if action.get('end') else '#9bdeb0')
                row.setForeground(QBrush(QColor(tint)))
                row.setToolTip('Click to edit; drag to reorder (execution is state-based)')
                self.steps.addItem(row)
            self.steps.blockSignals(False)

    def _new_state(self, _checked=False, *, confirm=True):
        if confirm and not self._confirm_discard():
            return
        self._loading = True
        try:
            self.steps.blockSignals(True)
            self.steps.clearSelection()
            self.steps.setCurrentRow(-1)
            self.steps.blockSignals(False)
            self.editing_key = None
            self.needs_recapture = False
            self.step_name.clear()
            self.retry.setChecked(False)
            self.passive.setChecked(False)
            self.retry_mode.setCurrentIndex(0)
            self.retry_interval.setValue(25)
            self.max_extra_taps.setValue(2)
            self.terminal.setChecked(False)
            self.wait_minutes.setValue(0)
            self.wait_seconds.setValue(0)
            self.canvas.clear_marks()
            self.canvas.frame = None
            self.canvas.picture = None
            self.canvas.update()
            self._sync_durations()
            self._set_mode(0)
        finally:
            self._loading = False
        self._visual_dirty = False
        self._dirty = False
        self.save_status.setText('New state — capture a screen to begin.')
        self._progress()

    def _restore_step_selection(self, previous):
        """Keep highlighted state aligned with the editor after a failed switch."""
        self.steps.blockSignals(True)
        try:
            if previous is not None:
                self.steps.setCurrentItem(previous)
            else:
                self.steps.setCurrentRow(-1)
        finally:
            self.steps.blockSignals(False)

    def _select_step(self, current, _previous):
        if current is None or not self._folder():
            return
        key = current.data(Qt.UserRole)
        try:
            # Validate and decode the destination *before* offering to
            # discard the current draft. Corrupt imported screenshots,
            # gestures or settings must never cost unsaved work.
            action, boxes, taps, frame = load_step(self._folder(), key)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._restore_step_selection(_previous)
            QMessageBox.warning(self, 'Could not load state', str(exc))
            return
        if not self._confirm_discard():
            self._restore_step_selection(_previous)
            return
        self._loading = True
        try:
            self.editing_key = key
            self.needs_recapture = bool(action.get('_needs_recapture'))
            self.canvas.load(frame, boxes=boxes, taps=taps, gestures=action.get('gestures'))
            self._sync_durations()
            self.step_name.setText(action['label'])
            self.retry.setChecked(action.get('retry', False))
            self.passive.setChecked(action.get('passive', False))
            self.retry_mode.setCurrentIndex(1 if action.get('retry_mode') == 'on_return' else 0)
            self.retry_interval.setValue(action.get('retry_interval_seconds', 25))
            self.max_extra_taps.setValue(action.get('max_extra_taps', 0))
            self.terminal.setChecked(action.get('end', False))
            seconds = action.get('pre_tap_delay_seconds', 0)
            self.wait_minutes.setValue(seconds // 60)
            self.wait_seconds.setValue(seconds % 60)
            self._set_mode(0)
        finally:
            self._loading = False
        self._visual_dirty = False
        self._dirty = False
        self.save_status.setText('Select any setting and click Save State to apply.')
        self._progress()

    def _capture(self):
        if not self.serial:
            QMessageBox.information(self, 'Device', 'Select a MuMu device in the main window first.')
            return
        try:
            frame = normalize(ADB(serial=self.serial).screenshot())
            if self.editing_key:
                self.canvas.replace_frame(frame)
            else:
                self.canvas.load(frame)
            self.needs_recapture = False
            self._visual_dirty = True
            self._field_changed()
            self._set_mode(0)
            self._progress()
        except ADBError as exc:
            QMessageBox.warning(self, 'Capture failed', str(exc))

    def _sync_durations(self):
        for field, value in zip(self.duration_inputs, self.canvas.drag_durations):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)

    def _set_mode(self, index: int):
        self.canvas.mode = index
        for i, button in enumerate(self.buttons):
            button.setProperty('selected', i == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _progress(self):
        pairs = [(gesture['kind'] if box is not None and gesture is not None else
                  'needs tap/drag' if box is not None else 'not set')
                 for box, gesture in zip(self.canvas.boxes, self.canvas.gestures)]
        message = (f'Wait state · detection only · Region 1: {"set" if self.canvas.boxes[0] else "not set"}'
                   if self.passive.isChecked() else
                   f'Region 1: {pairs[0]}   ·   Region 2: {pairs[1]}')
        if self.needs_recapture:
            message += '\nOlder recording: settings can be saved without recapture. Recapture to change regions/actions.'
        self.progress.setText(message)
        if not self.passive.isChecked() and self.canvas.mode in (0, 3):
            index = self.canvas.mode // 3
            if self.canvas.boxes[index] is not None and self.canvas.gestures[index] is None:
                self._set_mode(index*3+1)

    def _delay_seconds(self) -> int:
        return self.wait_minutes.value() * 60 + self.wait_seconds.value()

    def _refresh_selected_label(self):
        item = self.steps.currentItem()
        if item is None or self._folder() is None:
            return
        content = json.loads((self._folder() / 'manifest.json').read_text(encoding='utf-8'))
        key = item.data(Qt.UserRole)
        action = content['actions'][key]
        item.setText(self._step_label(action, len(content['states'][key])))
        tint = ('#ec9891' if action.get('passive') else
                '#edc56d' if action.get('end') else '#9bdeb0')
        item.setForeground(QBrush(QColor(tint)))

    def _save(self):
        folder = self._folder()
        if folder is None:
            QMessageBox.information(self, 'Workflow', 'Create or select a workflow first.')
            return

        # Settings-only edits preserve all saved visual templates, including
        # recordings imported from older versions with no full screenshot.
        visual_edit = self.editing_key is None or self._visual_dirty
        condition_only = bool(getattr(self, 'rules_only', None) and self.rules_only.isChecked())
        have_conditions = bool(getattr(self, 'extra_conditions', []))
        condition_match = (self.condition_logic.currentData()
                           if hasattr(self, 'condition_logic') else 'all')
        image_threshold = (self._image_threshold_override()
                           if hasattr(self, '_image_threshold_override') else None)
        priority = (self._priority_value() if hasattr(self, '_priority_value')
                    else None)
        if visual_edit:
            if self.canvas.frame is None or self.needs_recapture:
                QMessageBox.information(self, 'Screen capture',
                                        'Capture the current game screen before saving visual changes.')
                return
            if (not condition_only and self.canvas.boxes[0] is None) or (not self.passive.isChecked() and
                                               self.canvas.gestures[0] is None and
                                               not getattr(self, 'extra_chain', [])):
                QMessageBox.information(self, 'Region 1', 'Draw Region 1 and add a tap/drag for actions.')
                return
            if not self.passive.isChecked() and not getattr(self, 'extra_chain', []) and (
                    (self.canvas.boxes[1] is None) != (self.canvas.gestures[1] is None)):
                QMessageBox.information(self, 'Region 2',
                                        'Region 2 needs Tap 2 or Drag 2, or remove both.')
                return
            boxes = ([self.canvas.boxes[0]] if self.canvas.boxes[0] is not None and not condition_only else [])
            # Explicit action chains replace legacy region/touch pairs.
            # Passing an empty legacy slot as [None] would make record_step
            # reject an otherwise valid visually authored action sequence.
            use_legacy_gestures = (
                not self.passive.isChecked() and not condition_only and
                getattr(self, 'extra_chain', None) is None)
            gestures = [self.canvas.gestures[0]] if use_legacy_gestures else []
            if self.canvas.boxes[1] is not None and not condition_only:
                boxes.append(self.canvas.boxes[1])
                if use_legacy_gestures and self.canvas.gestures[1] is not None:
                    gestures.append(self.canvas.gestures[1])
        try:
            if visual_edit:
                key = record_step(folder, self.step_name.text(), self.canvas.frame,
                                  boxes, end=self.terminal.isChecked(), retry=self.retry.isChecked(),
                                  gestures=gestures, key=self.editing_key,
                                  pre_tap_delay_seconds=self._delay_seconds(),
                                  retry_mode=self.retry_mode.currentData(),
                                  retry_interval_seconds=self.retry_interval.value(),
                                  passive=self.passive.isChecked(),
                                  max_extra_taps=self.max_extra_taps.value(),
                                  action_chain=getattr(self, 'extra_chain', None),
                                  conditions=getattr(self, 'extra_conditions', []),
                                  condition_only=condition_only,
                                  condition_match=condition_match,
                                  image_threshold=image_threshold,
                                  priority=priority)
            else:
                key = self.editing_key
                # Studio-only fields participate in the same atomic settings
                # write; the legacy editor preserves them when it has none.
                studio = ({
                    'action_chain': self.extra_chain,
                    'conditions': self.extra_conditions,
                    'skip_template': condition_only,
                    'condition_match': condition_match,
                    'threshold': image_threshold,
                    'priority': priority,
                } if hasattr(self, 'extra_conditions') else None)
                update_step_settings(folder, key, self.step_name.text(),
                                     end=self.terminal.isChecked(), retry=self.retry.isChecked(),
                                     retry_mode=self.retry_mode.currentData(),
                                     retry_interval_seconds=self.retry_interval.value(),
                                     pre_tap_delay_seconds=self._delay_seconds(),
                                     passive=self.passive.isChecked(),
                                     max_extra_taps=self.max_extra_taps.value(),
                                     studio=studio)
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, 'Could not save state', str(exc))
            return

        self._dirty = False
        if visual_edit:
            self._load_steps()
            for row in range(self.steps.count()):
                if self.steps.item(row).data(Qt.UserRole) == key:
                    self.steps.setCurrentRow(row)
                    break
        else:
            self._refresh_selected_label()
        self._visual_dirty = False
        self._dirty = False
        self.save_status.setText('State saved. Restart automation to apply changes.')
        self._update_controls()

    def _align_state_selection(self) -> None:
        """Keep the highlighted row on the state actually being edited."""
        keys = self.steps.keys()
        selected = keys.index(self.editing_key) if self.editing_key in keys else -1
        blocked = self.steps.blockSignals(True)
        try:
            self.steps.setCurrentRow(selected)
        finally:
            self.steps.blockSignals(blocked)

    def _restore_state_order(self) -> bool:
        """Undo a failed drag without touching unsaved editor fields/history."""
        keys = self._persisted_step_order
        current = self.steps.keys()
        if len(keys) != len(current) or set(keys) != set(current):
            return False
        blocked = self.steps.blockSignals(True)
        try:
            items = {}
            while self.steps.count():
                item = self.steps.takeItem(0)
                items[item.data(Qt.UserRole)] = item
            for key in keys:
                self.steps.addItem(items[key])
            self._align_state_selection()
        finally:
            self.steps.blockSignals(blocked)
        return True

    def _reordered(self, keys: list[str]):
        folder = self._folder()
        if folder is None:
            return
        try:
            reorder_steps(folder, keys)
        except (ValueError, OSError) as exc:
            restored = self._restore_state_order()
            message = str(exc)
            if not restored:
                message += '\nThe state list changed; save your draft before reloading.'
            QMessageBox.warning(self, 'Could not reorder states', message)
            return
        self._persisted_step_order = list(keys)
        self._align_state_selection()

    def _delete(self):
        item = self.steps.currentItem()
        folder = self._folder()
        if not item or not folder:
            return
        if (QMessageBox.question(self, 'Delete state',
                                 f'Delete "{item.text().strip()}"? This cannot be undone.',
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                != QMessageBox.Yes):
            return
        # Deletion clears the editor after the manifest is committed. Ask
        # separately before discarding any unsaved WHEN/THEN draft edits.
        if not self._confirm_discard():
            return
        try:
            remove_step(folder, item.data(Qt.UserRole))
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, 'Could not delete state', str(exc))
            return
        self._dirty = False
        self._load_steps()
