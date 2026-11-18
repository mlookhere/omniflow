"""Compact, no-coordinates editor for prioritized workflow chains."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout,
)

from .chains import delete_chain, read_chains, save_chain


class ChainLibraryDialog(QDialog):
    """Edit a chain separately from the existing individual workflow Studio."""

    def __init__(self, root: Path, workflows: dict[str, Path], parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.workflows = dict(workflows)
        self._library = read_chains(root)
        self._loading = False
        self._dirty = False
        self._last_workflow_name = None
        self.saved = False
        self.setWindowTitle('Workflow Chains · Priority and completion')
        self.resize(780, 730)
        layout = QVBoxLayout(self)

        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel('Saved chain'))
        self.saved_chains = QComboBox()
        saved_row.addWidget(self.saved_chains, 1)
        self.load_button = QPushButton('Load')
        self.load_button.clicked.connect(self._load_selected)
        saved_row.addWidget(self.load_button)
        new_button = QPushButton('New')
        new_button.clicked.connect(self._new)
        saved_row.addWidget(new_button)
        delete_button = QPushButton('Delete')
        delete_button.clicked.connect(self._delete)
        saved_row.addWidget(delete_button)
        layout.addLayout(saved_row)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText('Daily routine')
        self.name.textChanged.connect(self._mark_dirty)
        form.addRow('Chain name', self.name)
        self.repeat = QCheckBox('Repeat the full chain after all configured stages complete')
        self.repeat.setChecked(True)
        self.repeat.toggled.connect(self._mark_dirty)
        form.addRow('Repeat chain', self.repeat)
        layout.addLayout(form)

        explanation = QLabel(
            'Top row is highest priority. After a Dynamic workflow finishes, '
            'the next ready row is preferred; if no other trigger is present, '
            'the previous Dynamic workflow can run again. Timed and Run-count '
            'stages repeat until their limit, then advance. An active run is '
            'never interrupted at the time limit.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        layout.addWidget(QLabel('Priority order (top to bottom)'))
        self.rows = QListWidget()
        self.rows.currentRowChanged.connect(self._select_row)
        layout.addWidget(self.rows, 1)
        move = QHBoxLayout()
        for title, callback in (
                ('Move up', lambda: self._move(-1)),
                ('Move down', lambda: self._move(1)),
                ('Remove row', self._remove)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            move.addWidget(button)
        layout.addLayout(move)

        editor = QFormLayout()
        self.workflow = QComboBox()
        self.workflow.addItems(list(workflows))
        self.workflow.currentIndexChanged.connect(self._refresh_states)
        editor.addRow('Workflow', self.workflow)
        self.trigger = QComboBox()
        self.complete = QComboBox()
        editor.addRow('Start trigger image/state', self.trigger)
        editor.addRow('Completed image/state', self.complete)
        self.mode = QComboBox()
        for title, value in [
            ('Dynamic · one completed run, then re-evaluate priorities', 'dynamic'),
            ('Timed · repeat until minutes expire; finish current run', 'timed'),
            ('Run count · repeat until N full completions', 'count'),
        ]:
            self.mode.addItem(title, value)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        editor.addRow('Execution mode', self.mode)
        self.limit = QSpinBox()
        editor.addRow('Time or run limit', self.limit)
        layout.addLayout(editor)

        buttons = QHBoxLayout()
        add = QPushButton('Add workflow')
        add.clicked.connect(self._add)
        buttons.addWidget(add)
        update = QPushButton('Update selected row')
        update.clicked.connect(self._update)
        buttons.addWidget(update)
        buttons.addStretch()
        save = QPushButton('Save chain')
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        done = QPushButton('Close')
        done.clicked.connect(self.close)
        buttons.addWidget(done)
        layout.addLayout(buttons)
        self._refresh_states()
        self._mode_changed()
        self._refresh_library()
        self._new(ask=False)

    def _mark_dirty(self, *_):
        if not self._loading:
            self._dirty = True

    def _may_discard(self):
        if not self._dirty:
            return True
        return QMessageBox.question(
            self, 'Discard unsaved chain edits?',
            'Unsaved priority and mode changes will be lost. Continue?',
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    def _refresh_library(self, selected=''):
        self._library = read_chains(self.root)
        self.saved_chains.blockSignals(True)
        self.saved_chains.clear()
        self.saved_chains.addItems(list(self._library))
        if selected:
            self.saved_chains.setCurrentText(selected)
        self.saved_chains.blockSignals(False)

    def _refresh_states(self, *_):
        name = self.workflow.currentText()
        # A different workflow has different state IDs. Select its first
        # trigger and last completion by default instead of carrying stale IDs.
        same_workflow = name == self._last_workflow_name
        current_trigger = self.trigger.currentData() if same_workflow else None
        current_complete = self.complete.currentData() if same_workflow else None
        self._last_workflow_name = name
        self.trigger.clear()
        self.complete.clear()
        path = self.workflows.get(name)
        if path is not None:
            try:
                data = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
                for key in data['states']:
                    label = data['actions'].get(key, {}).get('label', key)
                    self.trigger.addItem(label, key)
                    self.complete.addItem(label, key)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        for combo, key in ((self.trigger, current_trigger),
                           (self.complete, current_complete)):
            found = combo.findData(key)
            if found >= 0:
                combo.setCurrentIndex(found)
        if current_complete is None and self.complete.count() > 1:
            self.complete.setCurrentIndex(self.complete.count() - 1)

    def _mode_changed(self, *_):
        kind = self.mode.currentData()
        self.limit.setEnabled(kind != 'dynamic')
        self.limit.setRange(1, 10080 if kind == 'timed' else 100000)
        self.limit.setSuffix(' minutes' if kind == 'timed' else ' runs' if kind == 'count' else '')
        if kind != 'dynamic' and self.limit.value() < 1:
            self.limit.setValue(1)

    def _draft_row(self):
        kind = self.mode.currentData()
        return {'workflow': self.workflow.currentText(),
                'trigger': self.trigger.currentData(),
                'complete': self.complete.currentData(),
                'mode': kind, 'limit': self.limit.value() if kind != 'dynamic' else 0}

    def _append(self, row):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, dict(row))
        self.rows.addItem(item)
        self._refresh_labels()

    def _refresh_labels(self):
        for index in range(self.rows.count()):
            item = self.rows.item(index)
            row = item.data(Qt.UserRole)
            mode = row['mode'].title()
            if row.get('mode') == 'timed':
                mode += f' · {row["limit"]} min'
            elif row.get('mode') == 'count':
                mode += f' · {row["limit"]} runs'
            item.setText(
                f'{index + 1}. {row["workflow"]} — {mode} '
                f'(trigger: {row["trigger"]}, completed: {row["complete"]})')

    def _add(self):
        row = self._draft_row()
        self._append(row)
        self.rows.setCurrentRow(self.rows.count() - 1)
        self._mark_dirty()

    def _update(self):
        selected = self.rows.currentItem()
        if selected is None:
            return self._add()
        selected.setData(Qt.UserRole, self._draft_row())
        self._refresh_labels()
        self._mark_dirty()

    def _select_row(self, index):
        if index < 0 or self._loading:
            return
        item = self.rows.item(index)
        if item is None:
            return
        row = item.data(Qt.UserRole)
        self._loading = True
        try:
            self.workflow.setCurrentText(row['workflow'])
            self._refresh_states()
            for control, value in ((self.trigger, row['trigger']),
                                   (self.complete, row['complete'])):
                position = control.findData(value)
                if position >= 0:
                    control.setCurrentIndex(position)
            self.mode.setCurrentIndex(max(0, self.mode.findData(row['mode'])))
            self.limit.setValue(max(1, row.get('limit', 1)))
        finally:
            self._loading = False

    def _remove(self):
        row = self.rows.currentRow()
        if row < 0:
            return
        self.rows.takeItem(row)
        self._refresh_labels()
        self._mark_dirty()

    def _move(self, direction):
        index = self.rows.currentRow()
        target = index + direction
        if index < 0 or target < 0 or target >= self.rows.count():
            return
        item = self.rows.takeItem(index)
        self.rows.insertItem(target, item)
        self.rows.setCurrentRow(target)
        self._refresh_labels()
        self._mark_dirty()

    def _new(self, *, ask=True):
        if ask and not self._may_discard():
            return
        self._loading = True
        try:
            self.name.clear()
            self.repeat.setChecked(True)
            self.rows.clear()
            self._dirty = False
        finally:
            self._loading = False

    def _load_selected(self):
        name = self.saved_chains.currentText()
        if not name or not self._may_discard():
            return
        entry = self._library[name]
        self._loading = True
        try:
            self.name.setText(name)
            self.repeat.setChecked(entry.get('repeat', True))
            self.rows.clear()
            for row in entry['steps']:
                self._append(row)
            self._dirty = False
        finally:
            self._loading = False
        if self.rows.count():
            self.rows.setCurrentRow(0)

    def _save(self):
        source = {
            'name': self.name.text().strip(),
            'repeat': self.repeat.isChecked(),
            'steps': [self.rows.item(i).data(Qt.UserRole)
                      for i in range(self.rows.count())],
        }
        try:
            plan = save_chain(self.root, source, self.workflows)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, 'Cannot save workflow chain', str(exc))
            return
        self.saved = True
        self._dirty = False
        self._refresh_library(plan.name)
        QMessageBox.information(self, 'Workflow chain saved',
                                f'{plan.name} saved with {len(plan.steps)} prioritized workflows.')

    def _delete(self):
        name = self.saved_chains.currentText()
        if not name:
            return
        if QMessageBox.question(
                self, 'Delete chain?', f'Delete saved chain "{name}"? '
                'Individual workflows will not be deleted.',
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            delete_chain(self.root, name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Cannot delete chain', str(exc))
            return
        self.saved = True
        self._dirty = False
        self._refresh_library()
        self._new(ask=False)

    def closeEvent(self, event):
        if self._may_discard():
            event.accept()
        else:
            event.ignore()
