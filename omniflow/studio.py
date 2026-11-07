"""OmniFlow Studio: screenshot-first, game-agnostic workflow authoring.

Users select detection zones, colors, pixels and gestures on a saved or live
screen. Coordinates are implementation details, never mandatory form fields.
Existing workflow manifests and legacy recorder captures stay compatible.
"""
from __future__ import annotations

import json

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

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


class OmniStudio(RecorderDialog):
    def __init__(self, serial='', selected_workflow='', parent=None, base=None):
        super().__init__(serial, selected_workflow, parent, base=base)
        self.setWindowTitle('OmniFlow · Visual Workflow Studio')
        self.extra_chain: list[dict] | None = None
        self.extra_conditions: list[dict] = []
        rules = QWidget(); rule_col = QVBoxLayout(rules)
        instruction = QLabel('WHEN this appears: draw a zone to recognize text, an image, or a color. Combine conditions, including NOT-visible checks.')
        instruction.setWordWrap(True)
        rule_col.addWidget(instruction)
        self.rules_list=QListWidget();rule_col.addWidget(self.rules_list)
        rule_tools=QHBoxLayout();rule_col.addLayout(rule_tools)
        for title,fn in [('+ Condition',self._add_rule),('Edit',self._edit_rule),('Remove',self._remove_rule)]:
            btn=QPushButton(title);btn.clicked.connect(fn);rule_tools.addWidget(btn)
        self.rules_only=QCheckBox('Use conditions only (ignore image template)')
        self.rules_only.toggled.connect(self._field_changed)
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
        for title,fn in [('+ Action',self._add_action),('Edit',self._edit_action),('↑',lambda:self._move_action(-1)),
                         ('↓',lambda:self._move_action(1)),('Remove',self._remove_action)]:
            btn=QPushButton(title);btn.clicked.connect(fn);controls.addWidget(btn)
        reset=QPushButton('Use original recorded tap/drag only')
        reset.clicked.connect(self._reset_chain)
        chain_col.addWidget(reset)
        self.tabs.addTab(chain,'THEN · Actions')
        self._load_extra()

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
        self._refresh_extra()

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

    def _refresh_extra(self):
        self.rules_list.clear();self.actions_list.clear()
        if hasattr(self, 'rule_test_status'):
            self.rule_test_status.setText('Unsaved rules can be tested before saving.')
        self.canvas.extra_zones = [dict(item) for item in self.extra_conditions]
        self.canvas.update()
        for rule in self.extra_conditions:
            op='NOT ' if rule.get('negate') else ''
            self.rules_list.addItem(condition_summary(rule))
        for i,action in enumerate(self.extra_chain or []):
            self.actions_list.addItem(f'{i+1}. {action_summary(action)}')

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
            try:self.extra_conditions.append(dialog.result_rule())
            except ValueError as exc:QMessageBox.warning(self,'Rule',str(exc));return
            self._refresh_extra();self._field_changed()

    def _edit_rule(self):
        row=self.rules_list.currentRow()
        if row<0:return
        image = self._editor_frame()
        dialog=RuleEditor(initial=self.extra_conditions[row],parent=self,frame=image)
        if dialog.exec()==QDialog.Accepted:
            try:self.extra_conditions[row]=dialog.result_rule()
            except ValueError as exc:QMessageBox.warning(self,'Rule',str(exc));return
            self._refresh_extra();self._field_changed()

    def _remove_rule(self):
        row=self.rules_list.currentRow()
        if row>=0:self.extra_conditions.pop(row);self._refresh_extra();self._field_changed()

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
            self._ensure_chain();self.extra_chain.append(dialog.result_action())
            self._refresh_extra();self._field_changed()

    def _edit_action(self):
        row=self.actions_list.currentRow()
        if row<0:return
        image = self._editor_frame()
        dialog=ActionEditor(self.extra_chain[row],parent=self,frame=image)
        if dialog.exec()==QDialog.Accepted:
            self.extra_chain[row]=dialog.result_action();self._refresh_extra();self._field_changed()

    def _move_action(self,shift):
        row=self.actions_list.currentRow();other=row+shift
        if self.extra_chain is not None and 0<=row<len(self.extra_chain) and 0<=other<len(self.extra_chain):
            self.extra_chain[row],self.extra_chain[other]=self.extra_chain[other],self.extra_chain[row]
            self._refresh_extra();self.actions_list.setCurrentRow(other);self._field_changed()

    def _remove_action(self):
        row=self.actions_list.currentRow()
        if self.extra_chain is not None and row>=0:
            self.extra_chain.pop(row);self._refresh_extra();self._field_changed()

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
        from swordx.adb import ADBError
        folder=self._folder()
        if folder is None:
            QMessageBox.warning(self,'Test detection','Save a workflow first');return
        try:
            if self.serial:
                screenshot=Device(serial=self.serial).screenshot()
            elif self.canvas.frame is not None:
                screenshot=self.canvas.frame
            else:
                QMessageBox.warning(self,'Test detection','Connect an emulator or load a recorded screen')
                return
            detector = OmniDetector(folder)
            result, states = detector.inspect(screenshot)
            kind = 'WAIT · no action' if result.passive else 'ACTION' if result.key else 'UNKNOWN'
            lines = []
            for state in states:
                image = (f"Image {state['template_confidence']:.0%} "
                         f"(minimum {state['threshold']:.0%})"
                         if state['has_template'] else 'Condition-only')
                lines.append(f"{state['label']}: {state['status']} — {image}")
                rules = detector.actions[state['key']].get('conditions', [])
                outcomes = state['condition_results']
                if outcomes is None and rules and state['status'] == 'template below threshold':
                    lines.append('  Conditions not evaluated: image threshold failed')
                elif outcomes is not None:
                    for passed, rule in zip(outcomes, rules):
                        lines.append(
                            f"  {'PASS' if passed else 'FAIL'} — {condition_summary(rule)}")
            message = QMessageBox(self)
            message.setWindowTitle('Recognition test')
            message.setIcon(QMessageBox.Information)
            message.setText(f'{kind}\nState: {result.label}\nConfidence: {result.confidence:.1%}')
            message.setInformativeText(
                'Saved workflow inspected on one screenshot. No actions were executed.')
            message.setDetailedText('\n'.join(lines))
            message.exec()
        except (ADBError,WorkflowError,OSError,ValueError) as exc:
            QMessageBox.warning(self,'Recognition test',str(exc))

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
        matched = sum(item['status'] == 'matched' for item in results)
        tested = sum(item['status'] in ('matched', 'different', 'unknown')
                     for item in results)
        unavailable = sum(item['status'] == 'unavailable' for item in results)
        errors = sum(item['status'] == 'error' for item in results)
        detail = []
        for item in results:
            actual = f" → {item['actual']}" if item['actual_key'] else ''
            detail.append(
                f"{item['status'].upper()} · {item['expected']}{actual} "
                f"({item['reason']})")
        box = QMessageBox(self)
        box.setWindowTitle('Recorded screenshot replay')
        box.setIcon(QMessageBox.Information)
        box.setText(
            f'{matched}/{tested} readable screenshots selected their recorded state'
            if tested else 'No saved screenshots could be replayed')
        box.setInformativeText(
            f'{unavailable} unavailable; {errors} recognition errors. '
            'One-frame replay only: no actions, timing, or emulator input.')
        box.setDetailedText('\n'.join(detail))
        box.exec()

    def _reset_chain(self):
        self.extra_chain=None;self._refresh_extra();self._field_changed()

    def _save(self):
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
