"""OmniFlow Studio: screenshot-first, game-agnostic workflow authoring.

Users select detection zones, colors, pixels and gestures on a saved or live
screen. Coordinates are implementation details, never mandatory form fields.
Existing workflow manifests and legacy recorder captures stay compatible.
"""
from __future__ import annotations

import json
from copy import deepcopy

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QHeaderView,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from swordx.recorder import CaptureCanvas, RecorderDialog
from .devices import android_text_argument
from .visual import (action_summary, condition_summary, require_frame,
                     sample_average, sample_color, validate_point, validate_zone)


class ZoneCaptureDialog(QDialog):
    """One visual zone/touch picker for OCR, colors, taps and swipes."""

    def __init__(self, frame: np.ndarray, mode: str, *, initial=None, parent=None):
        super().__init__(parent)
        if mode not in ('region', 'point', 'swipe'):
            raise ValueError('Unsupported visual selection mode')
        self.setWindowTitle({'region': 'Select recognition area',
                             'point': 'Select a location',
                             'swipe': 'Record swipe path'}[mode])
        self.mode = mode
        self.resize(570, 780)
        layout = QVBoxLayout(self)
        label = QLabel({'region': 'Drag a box around only the distinctive UI or text to recognize.',
                        'point': 'Click the button, pixel, or exact target on the screenshot.',
                        'swipe': 'Hold the mouse button, drag along the desired path, and release.'}[mode])
        label.setWordWrap(True)
        layout.addWidget(label)
        self.canvas = CaptureCanvas()
        self.canvas.load(require_frame(frame))
        if initial is not None:
            if mode == 'region':
                self.canvas.boxes[0] = validate_zone(initial)
            elif mode == 'point':
                self.canvas.gestures[0] = {'kind': 'tap', 'point': list(validate_point(initial))}
            else:
                self.canvas.gestures[0] = {'kind': 'drag', 'start': list(validate_point(initial['start'])),
                                           'end': list(validate_point(initial['end'])), 'duration_ms': 700}
        self.canvas.mode = {'region': 0, 'point': 1, 'swipe': 2}[mode]
        layout.addWidget(self.canvas, 1)
        self.status = QLabel('Right-click your selection to clear it. The emulator is never touched here.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def result_selection(self):
        if self.mode == 'region':
            if self.canvas.boxes[0] is None:
                raise ValueError('Draw a rectangle on the screenshot first')
            return list(validate_zone(self.canvas.boxes[0]))
        gesture = self.canvas.gestures[0]
        if not gesture:
            raise ValueError('Select a location or draw a swipe on the screenshot first')
        if self.mode == 'point':
            return list(validate_point(gesture['point']))
        return {'start': list(validate_point(gesture['start'])),
                'end': list(validate_point(gesture['end']))}

    def accept(self):
        try:
            self.result_selection()
        except (ValueError, KeyError) as exc:
            self.status.setText(str(exc))
            return
        super().accept()


class ActionEditor(QDialog):
    """Touch actions are recorded visually; only timing and key text are typed."""
    TYPES = [('Tap', 'tap'), ('Double tap', 'double_tap'), ('Swipe / drag', 'swipe'),
             ('Press & hold', 'hold'), ('Wait', 'wait'), ('Back', 'back'),
             ('Android key', 'key'), ('Type text into focused field', 'text'),
             ('Tap field and type text', 'field_text'),
             ('Launch app', 'launch'), ('Count run', 'count_run')]

    def __init__(self, initial=None, parent=None, frame=None):
        super().__init__(parent)
        self.setWindowTitle('Add an action')
        self.setMinimumWidth(410)
        self.frame = require_frame(frame) if frame is not None else None
        self.initial = dict(initial or {})
        self.selection = None
        kind = 'swipe' if self.initial.get('kind') == 'drag' else self.initial.get('kind', 'tap')
        if kind in ('tap','double_tap','hold','field_text') and 'point' in self.initial:
            self.selection = list(validate_point(self.initial['point']))
        if kind in ('swipe','drag') and 'start' in self.initial and 'end' in self.initial:
            self.selection = {'start': list(validate_point(self.initial['start'])),
                              'end': list(validate_point(self.initial['end']))}
        self._point_selection = (list(self.selection)
                                 if isinstance(self.selection, list) else None)
        self._swipe_selection = (dict(self.selection)
                                 if isinstance(self.selection, dict) else None)
        self._selection_mode = ('point' if kind in ('tap', 'double_tap', 'hold', 'field_text')
                                else 'swipe' if kind in ('swipe', 'drag') else None)
        layout = QVBoxLayout(self)
        guide = QLabel('1. Choose what OmniFlow should do.  2. Pick its target on the screenshot.  3. Save the action.')
        guide.setWordWrap(True)
        layout.addWidget(guide)
        form = QFormLayout()
        layout.addLayout(form)
        self.kind = QComboBox()
        for name, code in self.TYPES:
            self.kind.addItem(name, code)
        form.addRow('Action', self.kind)
        self.pick = QPushButton('Select on screenshot…')
        self.pick.clicked.connect(self._pick)
        form.addRow('Target', self.pick)
        self.summary = QLabel('No target selected')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.duration = QSpinBox()
        self.duration.setRange(50, 10000)
        self.duration.setValue(int(self.initial.get('duration_ms', 650)))
        self.duration.setSuffix(' ms')
        form.addRow('Hold/swipe duration', self.duration)
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 86400)
        self.seconds.setValue(int(self.initial.get('seconds', 1)))
        self.seconds.setSuffix(' sec')
        form.addRow('Wait duration', self.seconds)
        self.keycode = QSpinBox()
        self.keycode.setRange(0, 300)
        self.keycode.setValue(int(self.initial.get('code', 4)))
        form.addRow('Android key', self.keycode)
        self.text = QLineEdit(str(self.initial.get('text', '')))
        self.text.setMaxLength(250)
        self.text.setPlaceholderText('ASCII text; for Unicode use an IME (planned)')
        form.addRow('Text to type', self.text)
        self.form = form
        self.kind.currentIndexChanged.connect(self._visibility)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self._visibility()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _visibility(self, *_):
        kind = self.kind.currentData()
        needs_point = kind in ('tap', 'double_tap', 'hold', 'field_text')
        needs_swipe = kind in ('swipe', 'drag')
        for widget, visible in ((self.pick, needs_point or needs_swipe),
                                (self.duration, kind in ('hold','swipe','drag')),
                                (self.seconds, kind == 'wait'),
                                (self.keycode, kind == 'key'),
                                (self.text, kind in ('text', 'field_text'))):
            self.form.setRowVisible(widget, visible)
        # Keep each visual target when temporarily viewing a different action
        # kind. Only the target for the active kind may authorize Save.
        if self._selection_mode == 'point' and isinstance(self.selection, list):
            self._point_selection = list(self.selection)
        elif self._selection_mode == 'swipe' and isinstance(self.selection, dict):
            self._swipe_selection = dict(self.selection)
        if needs_point:
            self.selection = (list(self._point_selection)
                              if self._point_selection is not None else None)
            self._selection_mode = 'point'
        elif needs_swipe:
            self.selection = (dict(self._swipe_selection)
                              if self._swipe_selection is not None else None)
            self._selection_mode = 'swipe'
        else:
            self.selection = None
            self._selection_mode = None
        self._refresh_summary()

    def _refresh_summary(self):
        kind = self.kind.currentData()
        if kind in ('tap', 'double_tap', 'hold','swipe','drag','field_text'):
            self.summary.setText('Target recorded on the screenshot' if self.selection is not None
                                 else 'Select the target visually; no coordinates to type')
            self.pick.setEnabled(self.frame is not None)
            if self.frame is None:
                self.pick.setToolTip('Capture or load a screen in Studio first')
        else:
            self.summary.setText('No screen location needed')

    def _pick(self):
        if self.frame is None:
            QMessageBox.information(self, 'Screenshot required', 'Capture or open a saved screen in Studio first.')
            return
        kind = self.kind.currentData()
        mode = 'swipe' if kind in ('swipe', 'drag') else 'point'
        initial = self.selection if ((mode == 'swipe') == isinstance(self.selection, dict)) else None
        dialog = ZoneCaptureDialog(self.frame, mode, initial=initial, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.selection = dialog.result_selection()
            self._refresh_summary()

    def result_action(self):
        kind = self.kind.currentData()
        if kind == 'back':
            return {'kind': 'key', 'code': 4}
        if kind in ('tap','double_tap','hold'):
            if not isinstance(self.selection, list):
                raise ValueError('Select the tap/hold location on a screenshot before saving')
            result = {'kind': kind, 'point': list(validate_point(self.selection))}
            if kind == 'hold':
                result['duration_ms'] = self.duration.value()
            return result
        if kind in ('swipe','drag'):
            if not isinstance(self.selection, dict):
                raise ValueError('Draw the swipe path on the screenshot before saving')
            return {'kind': 'swipe', 'start': list(validate_point(self.selection['start'])),
                    'end': list(validate_point(self.selection['end'])),
                    'duration_ms': self.duration.value()}
        if kind == 'wait':
            return {'kind': 'wait','seconds': self.seconds.value()}
        if kind == 'key':
            return {'kind': 'key', 'code': self.keycode.value()}
        if kind in ('text', 'field_text'):
            android_text_argument(self.text.text())
            if kind == 'field_text':
                if not isinstance(self.selection, list):
                    raise ValueError('Select the input field on a screenshot first')
                return {'kind': 'field_text',
                        'point': list(validate_point(self.selection)),
                        'text': self.text.text()}
            return {'kind': 'text', 'text': self.text.text()}
        return {'kind':kind}

    def accept(self):
        try:
            self.result_action()
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, 'Finish action', str(exc))
            return
        super().accept()


class RuleEditor(QDialog):
    """Visual OCR, color and pixel rules; all positions sampled from the screen."""

    def __init__(self, region=None, initial=None, parent=None, frame=None):
        super().__init__(parent)
        self.setWindowTitle('Add visual condition')
        self.setMinimumWidth(440)
        self.frame = require_frame(frame) if frame is not None else None
        initial = initial or {}
        self.bounds = list(initial.get('rect', region or [])) or None
        self.point = list(initial['point']) if initial.get('point') else None
        self.bgr = list(initial.get('bgr', [])) or None
        self._selected_kind = initial.get('type', 'ocr')
        layout = QVBoxLayout(self)
        instruction = QLabel('Choose a detector, draw its target on the captured screen, then set the expected text or color.')
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        form = QFormLayout()
        layout.addLayout(form)
        self.kind = QComboBox()
        for label, code in [('Text visible (OCR)', 'ocr'), ('Pixel matches color', 'pixel'),
                            ('Zone matches color', 'color')]:
            self.kind.addItem(label, code)
        form.addRow('Recognize', self.kind)
        self.pick = QPushButton('Select zone on screenshot…')
        self.pick.clicked.connect(self._pick)
        form.addRow('Where', self.pick)
        self.zone_status = QLabel('No zone chosen')
        self.zone_status.setWordWrap(True)
        layout.addWidget(self.zone_status)
        self.text = QLineEdit(str(initial.get('text','')))
        self.text.setPlaceholderText('Text you want to recognize')
        form.addRow('Expected text', self.text)
        self.read_text = QPushButton('Read text from selected zone (OCR)')
        self.read_text.clicked.connect(self._read_ocr)
        form.addRow('Read from screenshot', self.read_text)
        self.regex = QCheckBox('Match a regular expression')
        self.regex.setChecked(bool(initial.get('regex',False)))
        form.addRow('', self.regex)
        self.negate = QCheckBox('This must NOT be visible')
        self.negate.setChecked(bool(initial.get('negate',False)))
        form.addRow('', self.negate)
        self.color_info = QLabel('Color is sampled automatically from the selected area')
        self.color_info.setWordWrap(True)
        form.addRow('Sample', self.color_info)
        self.tolerance = QSpinBox()
        self.tolerance.setRange(0, 255)
        self.tolerance.setValue(int(initial.get('tolerance', 25)))
        form.addRow('Color tolerance', self.tolerance)
        self.form = form
        self.kind.currentIndexChanged.connect(self._visibility)
        self.kind.setCurrentIndex(max(0, self.kind.findData(initial.get('type','ocr'))))
        self._visibility()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _visibility(self, *_):
        kind = self.kind.currentData()
        if kind != self._selected_kind:
            # Pixel points and rectangular zones are different selections.
            # A converted rule must never inherit color from the old target.
            if 'pixel' in (kind, self._selected_kind):
                self.bounds = None
                self.point = None
            self.bgr = (sample_average(self.frame, self.bounds)
                        if kind == 'color' and self.frame is not None
                        and self.bounds is not None else None)
            self._selected_kind = kind
        ocr = kind == 'ocr'
        for widget in (self.text, self.read_text, self.regex):
            self.form.setRowVisible(widget, ocr)
        self.form.setRowVisible(self.tolerance, not ocr)
        self.form.setRowVisible(self.color_info, not ocr)
        self.pick.setEnabled(self.frame is not None)
        self.pick.setText('Select pixel on screenshot…' if kind == 'pixel'
                          else 'Draw recognition zone on screenshot…')
        selected = self.point is not None if kind == 'pixel' else self.bounds is not None
        self.zone_status.setText(
            ('Pixel selected' if kind == 'pixel' else 'Zone selected') if selected else
            ('Select a pixel on the screenshot before saving' if kind == 'pixel'
             else 'Capture a screenshot and draw a zone before saving'))

    def _pick(self):
        if self.frame is None:
            QMessageBox.information(self, 'Screenshot required', 'Capture or open a saved game screen first.')
            return
        kind = self.kind.currentData()
        mode = 'point' if kind == 'pixel' else 'region'
        initial = self.point if mode == 'point' else self.bounds
        dialog = ZoneCaptureDialog(self.frame, mode, initial=initial, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        value = dialog.result_selection()
        if kind == 'pixel':
            self.point = value
            self.bounds = [max(0,value[0]-3), max(0,value[1]-3),
                           min(540,value[0]+4), min(960,value[1]+4)]
            self.bgr = sample_color(self.frame, self.point)
        else:
            self.bounds = value
            if kind == 'color':
                self.bgr = sample_average(self.frame, self.bounds)
        self._visibility()

    def _read_ocr(self):
        if self.frame is None or self.bounds is None:
            QMessageBox.information(self, 'Choose a zone', 'Draw a text region on the screenshot first.')
            return
        from .engine import OCREngine, WorkflowError
        x1,y1,x2,y2 = validate_zone(self.bounds)
        try:
            recognized = OCREngine.read(self.frame[y1:y2,x1:x2])
        except WorkflowError as exc:
            QMessageBox.warning(self, 'OCR unavailable', str(exc))
            return
        if recognized.strip():
            self.text.setText(recognized.strip())
        else:
            QMessageBox.information(self, 'No text detected', 'Try a tighter zone around larger text.')

    def result_rule(self):
        if self.bounds is None:
            raise ValueError('Draw the detection zone on a screenshot first')
        bounds = list(validate_zone(self.bounds))
        kind = self.kind.currentData()
        out = {'type': kind, 'rect': bounds, 'negate': self.negate.isChecked()}
        if kind == 'ocr':
            from .engine import validate_ocr_expression
            value = self.text.text().strip()
            if not value:
                raise ValueError('Enter text to recognize or use OCR autofill')
            validate_ocr_expression(value, self.regex.isChecked())
            out.update(text=value, regex=self.regex.isChecked())
        else:
            if kind == 'pixel':
                if self.point is None:
                    raise ValueError('Choose a pixel on the screenshot')
                out['point'] = list(validate_point(self.point))
            if self.bgr is None:
                if self.frame is None:
                    raise ValueError('Capture a screenshot to sample its color')
                self.bgr = sample_color(self.frame, self.point) if kind == 'pixel' else sample_average(self.frame, bounds)
            out.update(bgr=self.bgr, tolerance=self.tolerance.value())
        return out

    def accept(self):
        try:
            self.result_rule()
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, 'Finish recognition rule', str(exc))
            return
        super().accept()


class ReplayResultsDialog(QDialog):
    """Readable saved-screen audit, including states that also matched."""

    def __init__(self, results: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle('Saved screenshot recognition replay')
        self.resize(1050, 680)
        self._results = results
        layout = QVBoxLayout(self)
        matched = sum(item['status'] == 'matched' for item in results)
        tested = sum(item['status'] in ('matched', 'different', 'unknown')
                     for item in results)
        unavailable = sum(item['status'] == 'unavailable' for item in results)
        errors = sum(item['status'] == 'error' for item in results)
        overlaps = sum(bool(item['overlaps']) for item in results)
        self.summary = QLabel(
            f'{matched}/{tested} readable screenshots selected their recorded state'
            if tested else 'No saved screenshots could be replayed')
        layout.addWidget(self.summary)
        self.counts = QLabel(
            f'{overlaps} with overlapping matches · {unavailable} unavailable · '
            f'{errors} recognition errors')
        layout.addWidget(self.counts)
        self.table = QTableWidget(len(results), 6)
        self.table.setHorizontalHeaderLabels(
            ['Recorded state', 'Result', 'Selected state', 'Other matches',
             'Explanation', 'Would execute (preview)'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        for row, item in enumerate(results):
            competing = ', '.join(
                f"{match['label']} (WAIT)" if match['passive'] else match['label']
                for match in item['overlaps'])
            plan = ' → '.join(item.get('action_plan', [])) or '—'
            for column, value in enumerate((
                    item['expected'], item['status'].upper(), item['actual'] or '—',
                    competing or '—', item['reason'], plan)):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        header = self.table.horizontalHeader()
        for column in (0, 1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel('Recognition evidence for selected screenshot'))
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMinimumHeight(145)
        self.table.currentCellChanged.connect(
            lambda row, _column, _old_row, _old_column: self._show_evidence(row))
        layout.addWidget(self.evidence, 1)
        if results:
            self.table.setCurrentCell(0, 0)
        note = QLabel(
            'Offline, one-frame recognition and planned-action preview only. '
            'Real input requires two matching frames, fresh capture and session permission. '
            'No actions or emulator input. Unsaved Studio changes are not included.')
        note.setWordWrap(True)
        layout.addWidget(note)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _show_evidence(self, row: int):
        if not 0 <= row < len(self._results):
            self.evidence.clear()
            return
        item = self._results[row]
        lines = item.get('evidence') or [item.get('reason') or 'No recognition evidence available']
        self.evidence.setPlainText('\n'.join(lines))


class OmniStudio(RecorderDialog):
    def __init__(self, serial='', selected_workflow='', parent=None, base=None):
        super().__init__(serial, selected_workflow, parent, base=base)
        self.setWindowTitle('OmniFlow · Visual Workflow Studio')
        self.extra_chain: list[dict] | None = None
        self.extra_conditions: list[dict] = []
        self._undo_edits: list[dict] = []
        self._redo_edits: list[dict] = []
        self._history_busy = False
        self._undo_buttons: list[QPushButton] = []
        self._redo_buttons: list[QPushButton] = []
        self._selection_buttons: dict[str, dict[str, QPushButton]] = {
            'rule': {}, 'action': {}}
        rules = QWidget(); rule_col = QVBoxLayout(rules)
        instruction = QLabel('WHEN this appears: draw a zone to recognize text, an image, or a color. Combine conditions, including NOT-visible checks.')
        instruction.setWordWrap(True)
        rule_col.addWidget(instruction)
        self.rules_list=QListWidget();rule_col.addWidget(self.rules_list)
        rule_tools=QHBoxLayout();rule_col.addLayout(rule_tools)
        for name, title, callback in (
                ('add', '+ Condition', self._add_rule),
                ('edit', 'Edit', self._edit_rule),
                ('duplicate', 'Copy', self._duplicate_rule),
                ('up', '↑', lambda: self._move_rule(-1)),
                ('down', '↓', lambda: self._move_rule(1)),
                ('remove', 'Remove', self._remove_rule)):
            btn = QPushButton(title)
            btn.clicked.connect(callback)
            rule_tools.addWidget(btn)
            self._selection_buttons['rule'][name] = btn
        self.rules_list.currentRowChanged.connect(
            lambda _: self._update_selection_buttons('rule'))
        self._add_history_controls(rule_col)
        self.rules_only=QCheckBox('Use conditions only (ignore image template)')
        self.rules_only.toggled.connect(self._rule_mode_changed)
        rule_col.addWidget(self.rules_only)
        draft_tester = QPushButton('Test current conditions on screenshot (no taps)')
        draft_tester.clicked.connect(self._test_draft_conditions)
        rule_col.addWidget(draft_tester)
        self.rule_test_status = QLabel('Unsaved rules can be tested before saving.')
        self.rule_test_status.setWordWrap(True)
        rule_col.addWidget(self.rule_test_status)
        tester=QPushButton('Test saved workflow on current screen (no taps)')
        tester.clicked.connect(self._test_detection)
        rule_col.addWidget(tester)
        replay = QPushButton('Replay recorded screenshots (no taps)')
        replay.clicked.connect(self._replay_saved_screenshots)
        rule_col.addWidget(replay)
        self.tabs.addTab(rules,'WHEN · Recognition')
        chain=QWidget();chain_col=QVBoxLayout(chain)
        instruction = QLabel('THEN do these actions in order. Choose every touch or swipe from a screenshot; no coordinate entry.')
        instruction.setWordWrap(True)
        chain_col.addWidget(instruction)
        self.actions_list=QListWidget();chain_col.addWidget(self.actions_list)
        controls=QHBoxLayout();chain_col.addLayout(controls)
        for name, title, callback in (
                ('add', '+ Action', self._add_action),
                ('edit', 'Edit', self._edit_action),
                ('duplicate', 'Copy', self._duplicate_action),
                ('up', '↑', lambda: self._move_action(-1)),
                ('down', '↓', lambda: self._move_action(1)),
                ('remove', 'Remove', self._remove_action)):
            btn = QPushButton(title)
            btn.clicked.connect(callback)
            controls.addWidget(btn)
            self._selection_buttons['action'][name] = btn
        self.actions_list.currentRowChanged.connect(
            lambda _: self._update_selection_buttons('action'))
        reset=QPushButton('Use original recorded tap/drag only')
        reset.clicked.connect(self._reset_chain)
        chain_col.addWidget(reset)
        self._add_history_controls(chain_col)
        draft_actions = QPushButton('Preview current draft actions (no taps)')
        draft_actions.clicked.connect(self._preview_draft_actions)
        chain_col.addWidget(draft_actions)
        self.action_preview_status = QLabel(
            'Unsaved actions can be checked before saving or running.')
        self.action_preview_status.setWordWrap(True)
        chain_col.addWidget(self.action_preview_status)
        self.tabs.addTab(chain,'THEN · Actions')
        self._load_extra()

    def _update_selection_buttons(self, kind: str):
        widget = self.rules_list if kind == 'rule' else self.actions_list
        row, count = widget.currentRow(), widget.count()
        for name, button in self._selection_buttons[kind].items():
            if name == 'add':
                continue
            button.setEnabled(
                0 <= row < count and
                (name != 'up' or row > 0) and
                (name != 'down' or row < count - 1))

    def _add_history_controls(self, layout):
        controls = QHBoxLayout()
        for label, callback, collection in (
                ('Undo draft edit', self._undo_edit, self._undo_buttons),
                ('Redo draft edit', self._redo_edit, self._redo_buttons)):
            button = QPushButton(label)
            button.setEnabled(False)
            button.setToolTip('Undo/redo WHEN rules and THEN actions until Save or state switch')
            button.clicked.connect(callback)
            collection.append(button)
            controls.addWidget(button)
        layout.addLayout(controls)

    def _edit_snapshot(self) -> dict:
        return {
            'conditions': deepcopy(self.extra_conditions),
            'chain': deepcopy(self.extra_chain),
            'rules_only': self.rules_only.isChecked(),
            'rule_row': self.rules_list.currentRow(),
            'action_row': self.actions_list.currentRow(),
        }

    def _update_history_buttons(self):
        for button in self._undo_buttons:
            button.setEnabled(bool(self._undo_edits))
        for button in self._redo_buttons:
            button.setEnabled(bool(self._redo_edits))

    def _remember_edit(self, snapshot=None):
        if self._history_busy:
            return
        self._undo_edits.append(snapshot if snapshot is not None else self._edit_snapshot())
        del self._undo_edits[:-30]
        self._redo_edits.clear()
        self._update_history_buttons()

    def _clear_edit_history(self):
        self._undo_edits.clear()
        self._redo_edits.clear()
        self._update_history_buttons()

    def _apply_edit_snapshot(self, snapshot: dict):
        self._history_busy = True
        try:
            self.extra_conditions = deepcopy(snapshot['conditions'])
            self.extra_chain = deepcopy(snapshot['chain'])
            self.rules_only.blockSignals(True)
            try:
                self.rules_only.setChecked(snapshot['rules_only'])
            finally:
                self.rules_only.blockSignals(False)
            self._refresh_extra(rule_row=snapshot['rule_row'],
                                action_row=snapshot['action_row'])
        finally:
            self._history_busy = False
        # Conservatively retain unsaved status: other Recorder fields may also
        # have been edited, and no disk writes should occur during undo/redo.
        self._field_changed()

    def _undo_edit(self):
        if not self._undo_edits:
            return
        self._redo_edits.append(self._edit_snapshot())
        self._apply_edit_snapshot(self._undo_edits.pop())
        self._update_history_buttons()

    def _redo_edit(self):
        if not self._redo_edits:
            return
        self._undo_edits.append(self._edit_snapshot())
        self._apply_edit_snapshot(self._redo_edits.pop())
        self._update_history_buttons()

    def _rule_mode_changed(self, checked: bool):
        if self._history_busy:
            return
        previous = self._edit_snapshot()
        previous['rules_only'] = not checked
        self._remember_edit(previous)
        self._field_changed()

    def _load_extra(self):
        if not hasattr(self,'rules_list'):return
        self.extra_chain=None;self.extra_conditions=[]
        folder=self._folder()
        if folder and self.editing_key:
            action=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['actions'].get(self.editing_key,{})
            self.extra_chain=action.get('action_chain')
            self.extra_conditions=action.get('conditions',[])
            self.rules_only.blockSignals(True)
            self.rules_only.setChecked(bool(action.get('skip_template',False)))
            self.rules_only.blockSignals(False)
        else:
            self.rules_only.blockSignals(True)
            self.rules_only.setChecked(False)
            self.rules_only.blockSignals(False)
        self._refresh_extra(rule_row=-1, action_row=-1)
        self._clear_edit_history()

    def _select_step(self, current, previous):
        super()._select_step(current,previous)
        if current is not None and current.data(Qt.UserRole)==self.editing_key:
            self._load_extra()

    def _new_state(self,*args,**kwargs):
        super()._new_state(*args,**kwargs)
        # The base editor keeps _dirty when the user declines to discard.
        # Do not reload saved rules over unsaved Studio edits in that case.
        if not self._dirty:
            self._load_extra()

    def _refresh_extra(self, *, rule_row=None, action_row=None):
        rule_row = self.rules_list.currentRow() if rule_row is None else rule_row
        action_row = self.actions_list.currentRow() if action_row is None else action_row
        self.rules_list.clear()
        self.actions_list.clear()
        if hasattr(self, 'rule_test_status'):
            self.rule_test_status.setText('Unsaved rules can be tested before saving.')
        if hasattr(self, 'action_preview_status'):
            self.action_preview_status.setText(
                'Unsaved actions can be checked before saving or running.')
        self.canvas.extra_zones = [dict(item) for item in self.extra_conditions]
        self.canvas.update()
        for rule in self.extra_conditions:
            self.rules_list.addItem(condition_summary(rule))
        for i, action in enumerate(self.extra_chain or []):
            self.actions_list.addItem(f'{i+1}. {action_summary(action)}')
        if rule_row >= 0 and self.rules_list.count():
            self.rules_list.setCurrentRow(min(rule_row, self.rules_list.count()-1))
        if action_row >= 0 and self.actions_list.count():
            self.actions_list.setCurrentRow(min(action_row, self.actions_list.count()-1))
        self._update_selection_buttons('rule')
        self._update_selection_buttons('action')

    def _editor_frame(self):
        """Prefer the loaded capture; use the selected emulator if necessary."""
        if self.canvas.frame is not None and not self.needs_recapture:
            return self.canvas.frame
        if self.serial:
            from .devices import Device
            from swordx.detection import normalize
            try:
                image = normalize(Device(serial=self.serial).screenshot())
            except Exception as exc:
                QMessageBox.warning(self, 'Capture game screen', f'Could not capture MuMu: {exc}')
                return None
            # Legacy states can have template/gesture overlays but no saved
            # full-screen capture. Keep those edits while refreshing the image.
            if self.editing_key:
                self.canvas.replace_frame(image)
            else:
                self.canvas.load(image)
            self.needs_recapture = False
            self._visual_dirty = True
            self._field_changed()
            return image
        QMessageBox.information(self, 'Capture game screen',
                                'Capture an actual MuMu screenshot first. Reconstructed legacy previews cannot be used to record new touches.')
        return None

    def _add_rule(self):
        image = self._editor_frame()
        if image is None:
            return
        dialog=RuleEditor(self.canvas.boxes[0],parent=self,frame=image)
        if dialog.exec()==QDialog.Accepted:
            try:
                result = dialog.result_rule()
            except ValueError as exc:
                QMessageBox.warning(self, 'Rule', str(exc))
                return
            self._remember_edit()
            self.extra_conditions.append(result)
            self._refresh_extra(rule_row=len(self.extra_conditions)-1)
            self._field_changed()

    def _edit_rule(self):
        row=self.rules_list.currentRow()
        if row<0:return
        image = self._editor_frame()
        dialog=RuleEditor(initial=self.extra_conditions[row],parent=self,frame=image)
        if dialog.exec()==QDialog.Accepted:
            try:
                result = dialog.result_rule()
            except ValueError as exc:
                QMessageBox.warning(self, 'Rule', str(exc))
                return
            if result != self.extra_conditions[row]:
                self._remember_edit()
                self.extra_conditions[row] = result
                self._refresh_extra(rule_row=row)
                self._field_changed()

    def _duplicate_rule(self):
        row = self.rules_list.currentRow()
        if not 0 <= row < len(self.extra_conditions):
            return
        self._remember_edit()
        self.extra_conditions.insert(row + 1, deepcopy(self.extra_conditions[row]))
        self._refresh_extra(rule_row=row + 1)
        self._field_changed()

    def _move_rule(self, shift: int):
        row = self.rules_list.currentRow()
        other = row + shift
        if not (0 <= row < len(self.extra_conditions) and
                0 <= other < len(self.extra_conditions)):
            return
        self._remember_edit()
        self.extra_conditions[row], self.extra_conditions[other] = (
            self.extra_conditions[other], self.extra_conditions[row])
        self._refresh_extra(rule_row=other)
        self._field_changed()

    def _remove_rule(self):
        row=self.rules_list.currentRow()
        if 0 <= row < len(self.extra_conditions):
            self._remember_edit()
            self.extra_conditions.pop(row)
            self._refresh_extra(rule_row=row)
            self._field_changed()

    def _ensure_chain(self):
        if self.extra_chain is None:
            self.extra_chain=[dict(g) for g in self.canvas.gestures if g]

    def _add_action(self):
        default=None
        gesture=next((g for g in self.canvas.gestures if g),None)
        if gesture:default=gesture
        image = self._editor_frame()
        dialog=ActionEditor(default,parent=self,frame=image)
        if dialog.exec()==QDialog.Accepted:
            result = dialog.result_action()
            self._remember_edit()
            self._ensure_chain()
            self.extra_chain.append(result)
            self._refresh_extra(action_row=len(self.extra_chain)-1)
            self._field_changed()

    def _edit_action(self):
        row=self.actions_list.currentRow()
        if row<0:return
        image = self._editor_frame()
        dialog=ActionEditor(self.extra_chain[row],parent=self,frame=image)
        if dialog.exec()==QDialog.Accepted:
            result = dialog.result_action()
            if result != self.extra_chain[row]:
                self._remember_edit()
                self.extra_chain[row] = result
                self._refresh_extra(action_row=row)
                self._field_changed()

    def _duplicate_action(self):
        row = self.actions_list.currentRow()
        if self.extra_chain is None or not 0 <= row < len(self.extra_chain):
            return
        self._remember_edit()
        self.extra_chain.insert(row + 1, deepcopy(self.extra_chain[row]))
        self._refresh_extra(action_row=row + 1)
        self._field_changed()

    def _move_action(self,shift):
        row=self.actions_list.currentRow();other=row+shift
        if self.extra_chain is not None and 0<=row<len(self.extra_chain) and 0<=other<len(self.extra_chain):
            self._remember_edit()
            self.extra_chain[row], self.extra_chain[other] = self.extra_chain[other], self.extra_chain[row]
            self._refresh_extra(action_row=other)
            self._field_changed()

    def _remove_action(self):
        row=self.actions_list.currentRow()
        if self.extra_chain is not None and 0 <= row < len(self.extra_chain):
            self._remember_edit()
            self.extra_chain.pop(row)
            self._refresh_extra(action_row=row)
            self._field_changed()

    def _preview_draft_actions(self):
        """Check unsaved THEN steps without any screenshot, save or Android I/O."""
        from .engine import WorkflowError, resolve_action_chain
        from .replay import describe_action_plan

        action = {
            'passive': self.passive.isChecked(),
            'pre_tap_delay_seconds': self._delay_seconds(),
        }
        if self.extra_chain is not None:
            action['action_chain'] = self.extra_chain
        else:
            action['gestures'] = [dict(step) for step in self.canvas.gestures
                                  if step]
        regions = len(self.canvas.boxes)
        chain = resolve_action_chain(action, regions)
        try:
            steps = describe_action_plan(action, chain)
        except (WorkflowError, ValueError, TypeError, KeyError) as exc:
            self.action_preview_status.setText(
                f'Draft action invalid: {exc}. Nothing saved or tapped.')
            return
        note = (' Legacy paired regions: only the first alternative is shown; '
                'the selected region decides the live action.'
                if self.extra_chain is None and regions > 1 and
                len(action['gestures']) > 1 and not action['passive']
                else '')
        self.action_preview_status.setText(
            'Draft-only action preview (no taps or saves):\n' +
            '\n'.join(f'{index}. {step}' for index, step in enumerate(steps, 1)) +
            note + '\nLive execution additionally requires confirmed fresh '
            'screenshots, permission and timing.')

    def _test_draft_conditions(self):
        """Inspect current WHEN rules without saving or sending game input."""
        if not self.extra_conditions:
            self.rule_test_status.setText('Add a recognition condition to test.')
            return
        image = self._editor_frame()
        if image is None:
            return
        from .engine import WorkflowError, preview_conditions
        try:
            outcomes = preview_conditions(image, self.extra_conditions)
        except (WorkflowError, ValueError, KeyError, cv2.error) as exc:
            self.rule_test_status.setText(f'Condition test failed: {exc}')
            return
        details = [
            f'{"PASS" if ok else "FAIL"} — {condition_summary(rule)}'
            for ok, rule in zip(outcomes, self.extra_conditions)
        ]
        self.rule_test_status.setText(
            f'{sum(outcomes)}/{len(outcomes)} conditions match this screenshot. '
            'Draft only: image templates and other states were not tested; '
            'nothing was saved or tapped.\n' + '\n'.join(details))

    def _test_detection(self):
        from .devices import Device
        from .engine import OmniDetector, WorkflowError
        from .replay import describe_recognition_evidence
        from swordx.adb import ADBError
        folder=self._folder()
        if folder is None:
            QMessageBox.warning(self,'Test detection','Save a workflow first');return
        try:
            # Refuse unsafe saved rules before attempting even a read-only ADB
            # screenshot. Live inspection must not probe a device if startup
            # validation would already reject the workflow.
            detector = OmniDetector(folder)
            if self.serial:
                screenshot=Device(serial=self.serial).screenshot()
            elif self.canvas.frame is not None:
                screenshot=self.canvas.frame
            else:
                QMessageBox.warning(self,'Test detection','Connect an emulator or load a recorded screen')
                return
            result, states = detector.inspect(screenshot)
            kind = 'WAIT · no action' if result.passive else 'ACTION' if result.key else 'UNKNOWN'
            lines = describe_recognition_evidence(detector, states)
            message = QMessageBox(self)
            message.setWindowTitle('Recognition test')
            message.setIcon(QMessageBox.Information)
            message.setText(f'{kind}\nState: {result.label}\nConfidence: {result.confidence:.1%}')
            message.setInformativeText(
                'Saved workflow inspected on one screenshot. No actions were executed.')
            message.setDetailedText('\n'.join(lines))
            message.exec()
        except (ADBError, WorkflowError, OSError, ValueError,
                KeyError, TypeError, cv2.error) as exc:
            QMessageBox.warning(self, 'Recognition test', str(exc))

    def _replay_saved_screenshots(self):
        """Audit saved captures offline; never read from or control the emulator."""
        from .engine import WorkflowError
        from .replay import replay_saved_captures

        folder = self._folder()
        if folder is None:
            QMessageBox.warning(self, 'Screenshot replay', 'Select a saved workflow first.')
            return
        try:
            results = replay_saved_captures(folder)
        except (WorkflowError, OSError, ValueError, KeyError, cv2.error) as exc:
            QMessageBox.warning(self, 'Screenshot replay', str(exc))
            return
        ReplayResultsDialog(results, parent=self).exec()

    def _reset_chain(self):
        if self.extra_chain is not None:
            self._remember_edit()
            self.extra_chain = None
            self._refresh_extra(action_row=-1)
            self._field_changed()

    def _save(self):
        # Copied/imported rules may bypass the RuleEditor dialog.
        # Fail before any manifest or screenshot writes, including for states
        # that still require an image template.
        from .engine import WorkflowError, validate_condition
        try:
            for index, rule in enumerate(self.extra_conditions, 1):
                try:
                    validate_condition(rule)
                except WorkflowError as exc:
                    raise WorkflowError(f'Condition {index}: {exc}') from exc
        except WorkflowError as exc:
            QMessageBox.warning(self, 'Rules', str(exc))
            return
        # Imported or copied chains can bypass the ActionEditor dialog.
        # Reject malformed commands before the recorder writes any manifest.
        if self.extra_chain is not None:
            from .engine import WorkflowError, validate_action
            try:
                for step in self.extra_chain:
                    validate_action(step)
            except WorkflowError as exc:
                QMessageBox.warning(self, 'Actions', str(exc))
                return
        if self.rules_only.isChecked() and not self.extra_conditions:
            QMessageBox.warning(self,'Rules','Condition-only states require at least one condition')
            return
        if self.extra_chain == [] and not self.passive.isChecked():
            QMessageBox.warning(self,'Actions','Add an action or use the original tap/drag')
            return
        if self.extra_chain is None and not self.passive.isChecked() and not any(self.canvas.gestures):
            QMessageBox.warning(self,'Actions','This state needs an action sequence or a recorded tap/drag')
            return
        # Turning a condition-only state back into a template state needs a
        # real recorded region. Otherwise the toggle would silently do nothing.
        folder = self._folder()
        if (folder and self.editing_key and not self.rules_only.isChecked() and
                not self._visual_dirty):
            data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            if not data['states'][self.editing_key]:
                QMessageBox.warning(
                    self, 'Image template required',
                    'Draw a detection region and recapture the screen before using image matching.')
                return
        super()._save()
        if self._dirty or not self.editing_key:
            return
        self._load_extra()
        self.save_status.setText('Saved state, conditions and action sequence')
