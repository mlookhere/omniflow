"""Small, explicit data-management dialog. No implicit overwrite or deletion."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout)

from .config import ProfileStore, data_dir
from .storage import delete_workflow, export_backup, import_backup
from .workflows import BUILTIN, list_workflows, workflow_root


class DataManagerDialog(QDialog):
    def __init__(self, store: ProfileStore, project_root: Path, parent=None):
        super().__init__(parent)
        self.store = store
        self.project_root = project_root
        self.changed = False
        self.setWindowTitle('Profiles, workflows & backups')
        self.setMinimumWidth(600)
        self._build()
        self._refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        intro = QLabel('Profiles and recordings are stored separately from the app. '
                       'Import never replaces existing names; duplicates get an “(imported)” suffix.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        folder_row = QHBoxLayout()
        self.location = QLineEdit(str(data_dir()))
        self.location.setReadOnly(True)
        folder_row.addWidget(self.location, 1)
        folder_row.addWidget(self._button('Open data folder', lambda: self._open(data_dir())))
        layout.addLayout(folder_row)
        layout.addWidget(self._button('Open recorded workflows folder', lambda: self._open(workflow_root())))

        profile_group = QGroupBox('Profiles · includes settings and completed-run totals')
        profile_layout = QVBoxLayout(profile_group)
        self.profiles = QComboBox()
        profile_layout.addWidget(self.profiles)
        profile_buttons = QHBoxLayout()
        profile_buttons.addWidget(self._button('Export profile', lambda: self._export('profile')))
        profile_buttons.addWidget(self._button('Import profile', lambda: self._import('profile')))
        profile_buttons.addWidget(self._button('Delete profile', self._delete_profile))
        profile_layout.addLayout(profile_buttons)
        layout.addWidget(profile_group)

        workflow_group = QGroupBox('Recorded workflows · includes screenshots and templates')
        workflow_layout = QVBoxLayout(workflow_group)
        self.workflows = QComboBox()
        workflow_layout.addWidget(self.workflows)
        workflow_layout.addWidget(self._button('Open selected workflow files', self._open_selected_workflow))
        workflow_buttons = QHBoxLayout()
        workflow_buttons.addWidget(self._button('Export workflow', lambda: self._export('workflow')))
        workflow_buttons.addWidget(self._button('Import workflow', lambda: self._import('workflow')))
        workflow_buttons.addWidget(self._button('Delete workflow', self._delete_workflow))
        workflow_layout.addLayout(workflow_buttons)
        layout.addWidget(workflow_group)

        backup_group = QGroupBox('Full backup / restore')
        backup_layout = QVBoxLayout(backup_group)
        note = QLabel('Export every profile and custom workflow together. Restore merges them '
                      'without overwriting your current files.')
        note.setWordWrap(True)
        backup_layout.addWidget(note)
        actions = QHBoxLayout()
        actions.addWidget(self._button('Export everything', lambda: self._export('all')))
        actions.addWidget(self._button('Import backup', lambda: self._import('all')))
        backup_layout.addLayout(actions)
        layout.addWidget(backup_group)
        close = self._button('Done', self.accept)
        layout.addWidget(close)

    @staticmethod
    def _button(label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        return button

    def _refresh(self):
        old_profile = self.profiles.currentText() or self.store.active()
        old_workflow = self.workflows.currentText()
        self.profiles.clear()
        self.profiles.addItems(self.store.names())
        self.profiles.setCurrentText(old_profile if old_profile in self.store.names() else self.store.active())
        self.workflows.clear()
        for name in list_workflows():
            if name != BUILTIN:
                self.workflows.addItem(name)
        if old_workflow:
            self.workflows.setCurrentText(old_workflow)

    def _open(self, folder: Path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, 'File Explorer', f'Could not open folder:\n{folder}')

    def _open_selected_workflow(self):
        name = self.workflows.currentText()
        if not name:
            QMessageBox.information(self, 'Workflow files', 'No recorded workflow selected.')
            return
        self._open(list_workflows()[name])

    def _export(self, kind: str):
        name = self.profiles.currentText() if kind == 'profile' else self.workflows.currentText()
        if not name and kind != 'all':
            QMessageBox.information(self, 'Export', 'There is nothing to export.')
            return
        suffix = {'profile': f'profile-{name}', 'workflow': f'workflow-{name}', 'all': 'all-data'}[kind]
        suggested = self.project_root / 'backups' / f'SwordXAuto-{suffix}.zip'
        try:
            suggested.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            suggested = data_dir() / suggested.name
        output, _ = QFileDialog.getSaveFileName(self, 'Export SwordXAuto data', str(suggested),
                                                 'ZIP backup (*.zip)',
                                                 options=QFileDialog.Option.DontConfirmOverwrite)
        if not output:
            return
        try:
            target = export_backup(Path(output), self.store,
                                   profiles=[name] if kind == 'profile' else [] if kind == 'workflow' else None,
                                   workflows=[name] if kind == 'workflow' else [] if kind == 'profile' else None)
        except (ValueError, OSError, FileExistsError) as exc:
            QMessageBox.warning(self, 'Export failed', f'{exc}\n\nExisting backup files are never replaced.')
            return
        QMessageBox.information(self, 'Export complete', f'Saved:\n{target}')

    def _import(self, kind: str):
        source, _ = QFileDialog.getOpenFileName(self, 'Import SwordXAuto backup',
                                                 str(self.project_root / 'backups'), 'ZIP backup (*.zip)')
        if not source:
            return
        try:
            result = import_backup(Path(source), self.store, items=kind)
        except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
            QMessageBox.warning(self, 'Import failed', str(exc))
            return
        self.changed = bool(result.profiles or result.workflows)
        self._refresh()
        detail = '\n'.join((('Profiles: ' + ', '.join(result.profiles)) if result.profiles else 'No profiles added',
                            ('Workflows: ' + ', '.join(result.workflows)) if result.workflows else 'No workflows added'))
        QMessageBox.information(self, 'Import complete', detail + '\n\nExisting files were not changed.')

    def _delete_profile(self):
        name = self.profiles.currentText()
        if not name:
            return
        if QMessageBox.question(self, 'Delete profile',
                                f'Permanently delete profile “{name}” and its run counter?\n\n'
                                'The referenced workflow will remain saved. Export first if needed.',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.store.delete(name)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Delete profile', str(exc))
            return
        self.changed = True
        self._refresh()

    def _delete_workflow(self):
        name = self.workflows.currentText()
        if not name:
            return
        using = [profile for profile in self.store.names() if self.store.get(profile).workflow == name]
        impact = (f'\n\nUsed by profiles: {", ".join(using)}. They will switch to the built-in workflow.'
                  if using else '')
        if QMessageBox.question(self, 'Delete workflow',
                                f'Permanently delete “{name}” and its recorded screenshots/regions?'
                                f'{impact}\n\nExport a backup first if needed.',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            delete_workflow(name, self.store)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Delete workflow', str(exc))
            return
        self.changed = True
        self._refresh()
