"""Non-destructive migration of SwordXAuto user workflows to OmniFlow."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from swordx.workflows import BUILTIN, list_workflows, create_workflow


def root_dir() -> Path:
    base = os.environ.get('LOCALAPPDATA') or str(Path.home()/'.local'/'share')
    root = Path(base)/'OmniFlow'
    root.mkdir(parents=True, exist_ok=True)
    return root


def local_workflows(root: Path | None = None) -> dict[str, Path]:
    return {name:folder for name,folder in list_workflows(root or root_dir()).items()
            if name != BUILTIN}


def migrate_legacy(root: Path | None = None, old: Path | None = None) -> list[str]:
    """Copy (never move/overwrite) valid legacy workflow directories once."""
    root = root or root_dir()
    old = old or root.parent/'SwordXAuto'
    marker = root/'.legacy-imported'
    if marker.exists() or not old.exists():
        return []
    from swordx.workflows import workflow_root
    new_dir = workflow_root(root)
    imported = []
    names = set(local_workflows(root))
    for source in sorted((old/'workflows').iterdir()) if (old/'workflows').is_dir() else []:
        if not source.is_dir() or source.is_symlink() or not (source/'manifest.json').is_file():
            continue
        try:
            info=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
            name=info['name']
            if not isinstance(name,str) or not name or any(ch in name for ch in '/\\:*?"<>|'):
                continue
            if name.casefold() in {x.casefold() for x in names}:
                continue
            # Prevent source tree symlink escape and accidental binary imports.
            if any(p.is_symlink() for p in source.rglob('*')):
                continue
            destination=new_dir/uuid4().hex
            shutil.copytree(source,destination)
            imported.append(name)
            names.add(name)
        except (OSError,ValueError,KeyError,TypeError):
            continue
    # Old named presets remain available in OmniFlow without modifying source.
    source_profiles=old/'profiles.json'
    target_profiles=root/'profiles.json'
    if source_profiles.is_file() and not target_profiles.exists():
        try:
            payload=json.loads(source_profiles.read_text(encoding='utf-8'))
            if isinstance(payload.get('profiles'),dict):
                shutil.copy2(source_profiles,target_profiles)
        except (OSError,ValueError,TypeError,AttributeError):
            pass
    marker.write_text('Source workflows and presets copied; originals were not modified.\n',encoding='utf-8')
    return imported


def preferences(root: Path | None = None) -> dict:
    path=(root or root_dir())/'preferences.json'
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError,TypeError):
        return {}


def save_preferences(data: dict, root: Path | None = None) -> None:
    path=(root or root_dir())/'preferences.json'
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,indent=2),encoding='utf-8')
    os.replace(tmp,path)
