"""Lossless, collision-safe profile and workflow backups."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .config import ProfileStore
from .workflows import BUILTIN, _write_manifest, list_workflows, workflow_root

FORMAT = 'SwordXAuto-Backup'
VERSION = 1
MAX_ENTRY = 35 * 1024 * 1024
MAX_TOTAL = 300 * 1024 * 1024
MAX_FILES = 3000


@dataclass
class ImportResult:
    profiles: list[str]
    workflows: list[str]


def _unique_name(name: str, occupied: set[str]) -> str:
    if name.casefold() not in occupied:
        occupied.add(name.casefold())
        return name
    for num in range(1, 10000):
        suffix = ' (imported)' if num == 1 else f' (imported {num})'
        candidate = name[:48 - len(suffix)] + suffix
        if candidate.casefold() not in occupied:
            occupied.add(candidate.casefold())
            return candidate
    raise ValueError('Too many records with the same name')


def _valid_name(name: str) -> bool:
    return (isinstance(name, str) and 1 <= len(name) <= 48 and name == name.strip()
            and not any(char in name for char in '\\/:*?"<>|\r\n'))


def _valid_profile(payload: object) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get('settings'), dict):
        return False
    count = payload.get('runs', 0)
    return type(count) is int and 0 <= count <= 2**63 - 1


def _content_paths(manifest: dict) -> set[str]:
    """Only package images explicitly referenced by the workflow manifest."""
    if not isinstance(manifest.get('states'), dict) or not isinstance(manifest.get('actions'), dict):
        raise ValueError('Invalid workflow manifest')
    paths: set[str] = set()
    if set(manifest['states']) != set(manifest['actions']):
        raise ValueError('Workflow states and actions do not match')
    for key, probes in manifest['states'].items():
        if not isinstance(key, str) or not isinstance(probes, list) or not probes:
            raise ValueError('Invalid detection state')
        for probe in probes:
            if not isinstance(probe, dict):
                raise ValueError('Invalid detection region')
            paths.add(_safe_image('anchors', probe.get('file'), '.png'))
        action = manifest['actions'][key]
        if not isinstance(action, dict) or not isinstance(action.get('label'), str):
            raise ValueError('Invalid workflow action')
        if action.get('capture'):
            paths.add(_safe_image('captures', action['capture'], '.jpg'))
    return paths


def _safe_image(directory: str, filename: object, ext: str) -> str:
    if (not isinstance(filename, str) or not filename or filename in ('.', '..')
            or '/' in filename or '\\' in filename or not filename.lower().endswith(ext)):
        raise ValueError('Workflow contains an unsafe image filename')
    return f'{directory}/{filename}'


def export_backup(destination: Path, store: ProfileStore, *, profiles: list[str] | None = None,
                  workflows: list[str] | None = None, base: Path | None = None) -> Path:
    """Export everything by default, or selected entries, never overwriting an existing file."""
    destination = Path(destination)
    available = list_workflows(base)
    profile_names = profiles if profiles is not None else store.names()
    workflow_names = workflows if workflows is not None else [n for n in available if n != BUILTIN]
    with store.lock:
        selected_profiles = {name: json.loads(json.dumps(store._require(name))) for name in profile_names}
    if len(set(workflow_names)) != len(workflow_names):
        raise ValueError('Duplicate workflows requested')
    if BUILTIN in workflow_names:
        raise ValueError('The built-in workflow ships with the application and is not exported')
    entries = []
    for name in workflow_names:
        if name not in available:
            raise ValueError(f'Unknown workflow: {name}')
        folder = available[name]
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('name') != name or not _valid_name(name):
            raise ValueError(f'Invalid workflow: {name}')
        images = _content_paths(manifest)
        for relative in images:
            path = folder / relative
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ENTRY:
                raise ValueError(f'Missing or unsafe workflow image: {relative}')
        entries.append((name, folder, manifest, images))
    header = {'format': FORMAT, 'version': VERSION, 'profiles': selected_profiles,
              'workflows': [{'name': name, 'folder': str(i)} for i, (name, *_rest) in enumerate(entries)]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create guarantees an export never overwrites a pre-existing backup.
    with destination.open('xb') as output:
        try:
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as archive:
                archive.writestr('backup.json', json.dumps(header, ensure_ascii=False, indent=2))
                for i, (_, folder, manifest, images) in enumerate(entries):
                    prefix = f'workflows/{i}'
                    archive.writestr(f'{prefix}/manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                    for relative in sorted(images):
                        archive.write(folder / relative, arcname=f'{prefix}/{relative}')
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return destination


def _extract_validated(archive: zipfile.ZipFile, root: Path) -> dict:
    infos = archive.infolist()
    if len(infos) > MAX_FILES or sum(info.file_size for info in infos) > MAX_TOTAL:
        raise ValueError('Backup is too large')
    present: set[str] = set()
    for info in infos:
        path = PurePosixPath(info.filename)
        if (info.is_dir() or info.filename in present or '\\' in info.filename
                or path.is_absolute() or '..' in path.parts or not path.parts
                or info.file_size > MAX_ENTRY or info.flag_bits & 0x1
                or (info.external_attr >> 16) & 0o170000 == 0o120000):
            raise ValueError('Backup contains unsafe or duplicate files')
        present.add(info.filename)
        # Only the backup header and workflow assets are supported.
        if (info.filename != 'backup.json'
                and (len(path.parts) != 4 or path.parts[0] != 'workflows'
                     or not path.parts[1].isdigit() or path.parts[2] not in ('anchors', 'captures'))
                and (len(path.parts) != 3 or path.parts[0] != 'workflows'
                     or not path.parts[1].isdigit() or path.parts[2] != 'manifest.json')):
            raise ValueError('Backup contains unsupported files')
        target = root.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open('wb') as destination:
            shutil.copyfileobj(source, destination)
    header_file = root / 'backup.json'
    if not header_file.is_file():
        raise ValueError('Backup manifest is missing')
    header = json.loads(header_file.read_text(encoding='utf-8'))
    if not isinstance(header, dict) or header.get('format') != FORMAT or header.get('version') != VERSION:
        raise ValueError('Unsupported SwordXAuto backup format')
    if not isinstance(header.get('profiles'), dict) or not isinstance(header.get('workflows'), list):
        raise ValueError('Backup profile or workflow list is invalid')
    for name, profile in header['profiles'].items():
        if not _valid_name(name) or not _valid_profile(profile):
            raise ValueError('Backup contains an invalid profile')
    folders = set()
    names = set()
    for entry in header['workflows']:
        if not isinstance(entry, dict) or not _valid_name(entry.get('name')):
            raise ValueError('Backup contains an invalid workflow name')
        if entry['name'].casefold() in names or not str(entry.get('folder', '')).isdigit():
            raise ValueError('Backup contains duplicate or invalid workflow entries')
        names.add(entry['name'].casefold())
        key = entry['folder']
        if key in folders:
            raise ValueError('Duplicate workflow folder in backup')
        folders.add(key)
        folder = root / 'workflows' / key
        path = folder / 'manifest.json'
        if not path.is_file():
            raise ValueError('Workflow manifest is missing')
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('name') != entry['name']:
            raise ValueError('Workflow name does not match backup')
        referenced = _content_paths(manifest)
        actual = {file.relative_to(folder).as_posix() for file in folder.rglob('*') if file.is_file()}
        if actual != referenced | {'manifest.json'}:
            raise ValueError('Workflow image files are missing or unexpected')
    return header


def import_backup(source: Path, store: ProfileStore, *, base: Path | None = None,
                  items: str = 'all') -> ImportResult:
    """Stage and validate fully first. All conflicts are renamed; existing items never change."""
    if items not in ('profile', 'workflow', 'all'):
        raise ValueError('Choose profiles, workflows or all data')
    with tempfile.TemporaryDirectory(prefix='swordx-import-') as stage:
        staged = Path(stage)
        try:
            with zipfile.ZipFile(source) as archive:
                header = _extract_validated(archive, staged)
        except (zipfile.BadZipFile, RuntimeError, UnicodeError) as exc:
            raise ValueError('Invalid or unreadable SwordXAuto backup') from exc
        existing_workflows = list_workflows(base)
        occupied_workflows = {name.casefold() for name in existing_workflows}
        mapped: dict[str, str] = {}
        planned_workflows: list[tuple[str, Path]] = []
        for entry in (header['workflows'] if items != 'profile' else []):
            name = _unique_name(entry['name'], occupied_workflows)
            mapped[entry['name']] = name
            planned_workflows.append((name, staged / 'workflows' / entry['folder']))
        added: list[Path] = []
        try:
            for name, staged_folder in planned_workflows:
                target = workflow_root(base) / uuid4().hex
                shutil.copytree(staged_folder, target)
                added.append(target)
                manifest_file = target / 'manifest.json'
                manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
                manifest['name'] = name
                _write_manifest(manifest_file, manifest)
            imported_profiles: list[str] = []
            with store.lock:
                occupied_profiles = {name.casefold() for name in store.data['profiles']}
                planned: dict[str, dict] = {}
                for original, payload in (header['profiles'].items() if items != 'workflow' else []):
                    name = _unique_name(original, occupied_profiles)
                    copy = json.loads(json.dumps(payload))
                    selected = copy['settings'].get('workflow')
                    if selected in mapped:
                        copy['settings']['workflow'] = mapped[selected]
                    planned[name] = copy
                    imported_profiles.append(name)
                store.data['profiles'].update(planned)
                try:
                    store._write()
                except Exception:
                    for name in planned:
                        del store.data['profiles'][name]
                    raise
        except Exception:
            for target in added:
                shutil.rmtree(target, ignore_errors=True)
            raise
    return ImportResult(imported_profiles, [name for name, _ in planned_workflows])



def duplicate_workflow(source: str, new_name: str, *, base: Path | None = None) -> Path:
    """Create an independent, non-overwriting copy of a recorded workflow.

    Source data is read only; the copy receives its own manifest and image files.
    Publish the staged directory only after every referenced image has been copied.
    """
    new_name = new_name.strip()
    if not _valid_name(new_name):
        raise ValueError('Enter a workflow name up to 48 characters')
    available = list_workflows(base)
    if source == BUILTIN or source not in available:
        raise ValueError('Select a saved recorded workflow to duplicate')
    if any(name.casefold() == new_name.casefold() for name in available):
        raise ValueError('A workflow with that name already exists')

    root = workflow_root(base)
    original = available[source]
    if original.is_symlink() or original.parent.resolve() != root.resolve():
        raise ValueError('Source must be a saved recorded workflow')
    manifest = json.loads((original / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('name') != source:
        raise ValueError('Workflow manifest name does not match')
    images = _content_paths(manifest)

    # Stage outside the discoverable workflows directory to prevent partially
    # copied workflows showing up in the editor after an interrupted copy.
    with tempfile.TemporaryDirectory(prefix='.swordx-duplicate-', dir=root.parent) as stage:
        staged = Path(stage) / 'workflow'
        staged.mkdir()
        for relative in images:
            source_file = original / relative
            if source_file.is_symlink() or not source_file.is_file():
                raise ValueError(f'Missing or unsafe workflow image: {relative}')
            target_file = staged / relative
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target_file)
        (staged / 'anchors').mkdir(exist_ok=True)
        (staged / 'captures').mkdir(exist_ok=True)
        manifest['name'] = new_name
        _write_manifest(staged / 'manifest.json', manifest)
        # A fresh UUID folder prevents replacing any existing workflow assets.
        target = root / uuid4().hex
        while target.exists():
            target = root / uuid4().hex
        staged.rename(target)
    return target


def delete_workflow(name: str, store: ProfileStore, *, base: Path | None = None) -> None:
    """Delete a user workflow and replace any profile references with the built-in one."""
    available = list_workflows(base)
    if name == BUILTIN or name not in available:
        raise ValueError('Only recorded workflows can be deleted')
    folder = available[name]
    if folder.parent.resolve() != workflow_root(base).resolve() or folder.is_symlink():
        raise ValueError('Workflow folder is not a user recording')
    # Move first so any failure to save profile references can restore the workflow.
    temporary = workflow_root(base).parent / ('.removing-' + uuid4().hex)
    folder.rename(temporary)
    try:
        with store.lock:
            affected = [(entry, profile['settings'].get('workflow'))
                        for entry, profile in store.data['profiles'].items()
                        if profile['settings'].get('workflow') == name]
            for entry, _ in affected:
                store.data['profiles'][entry]['settings']['workflow'] = BUILTIN
            try:
                if affected:
                    store._write()
            except Exception:
                for entry, old in affected:
                    store.data['profiles'][entry]['settings']['workflow'] = old
                raise
    except Exception:
        temporary.rename(folder)
        raise
    shutil.rmtree(temporary)
