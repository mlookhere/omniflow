"""Persistent named profiles and per-profile completion counters."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.local' / 'share')
    path = Path(base) / 'SwordXAuto'
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    device: str = ''
    endpoint: str = ''
    package: str = ''
    emulator_exe: str = ''
    poll_seconds: float = 0.9
    diagnostics: bool = True
    auto_relaunch_game: bool = True
    workflow: str = 'Built-in dungeon'
    # Per-profile, per-workflow state overrides. 0 disables the recognized-state watchdog.
    state_timeouts: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> 'Settings':
        path = data_dir() / 'settings.json'
        if not path.exists():
            return cls()
        try:
            content = json.loads(path.read_text(encoding='utf-8'))
            return cls(**{key: value for key, value in content.items() if key in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        (data_dir() / 'settings.json').write_text(json.dumps(asdict(self), indent=2), encoding='utf-8')


class ProfileStore:
    """Atomic saves, including writes triggered from the background worker."""

    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = self.root / 'profiles.json'
        self.lock = threading.RLock()
        try:
            self.data = json.loads(self.file.read_text(encoding='utf-8'))
            assert isinstance(self.data['profiles'], dict)
        except (OSError, ValueError, TypeError, KeyError, AssertionError):
            self.data = {'active': 'Default', 'profiles': {'Default': {
                'settings': asdict(Settings.load()), 'runs': 0}}}
            self._write()
        if not self.data['profiles']:
            self.data['profiles']['Default'] = {'settings': asdict(Settings()), 'runs': 0}
            self.data['active'] = 'Default'
            self._write()

    def _write(self) -> None:
        temp = self.file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.file)

    def names(self) -> list[str]:
        with self.lock:
            return list(self.data['profiles'])

    def active(self) -> str:
        with self.lock:
            name = self.data.get('active')
            return name if name in self.data['profiles'] else next(iter(self.data['profiles']))

    def select(self, name: str) -> None:
        with self.lock:
            self._require(name)
            self.data['active'] = name
            self._write()

    def _require(self, name: str) -> dict:
        if name not in self.data['profiles']:
            raise ValueError(f'Unknown profile: {name}')
        return self.data['profiles'][name]

    def get(self, name: str) -> Settings:
        with self.lock:
            raw = self._require(name)['settings']
            return Settings(**{k: v for k, v in raw.items() if k in Settings.__dataclass_fields__})

    def runs(self, name: str) -> int:
        with self.lock:
            return int(self._require(name).get('runs', 0))

    def save(self, name: str, settings: Settings) -> None:
        with self.lock:
            self._require(name)['settings'] = asdict(settings)
            self._write()

    def create(self, name: str, template: Settings | None = None) -> None:
        name = name.strip()
        if not (1 <= len(name) <= 48) or any(ch in name for ch in '\\/:*?"<>|\r\n'):
            raise ValueError('Profile name must be 1–48 characters without path characters')
        with self.lock:
            if any(n.casefold() == name.casefold() for n in self.data['profiles']):
                raise ValueError('A profile with that name already exists')
            self.data['profiles'][name] = {'settings': asdict(template or Settings()), 'runs': 0}
            self.data['active'] = name
            self._write()

    def delete(self, name: str) -> str:
        """Remove a profile. Never delete the last usable profile."""
        with self.lock:
            self._require(name)
            if len(self.data['profiles']) == 1:
                raise ValueError('Create or import another profile before deleting the last profile')
            removed = self.data['profiles'].pop(name)
            previous = self.data.get('active')
            if previous == name:
                self.data['active'] = next(iter(self.data['profiles']))
            try:
                self._write()
            except Exception:
                self.data['profiles'][name] = removed
                self.data['active'] = previous
                raise
            return self.active()

    def increment(self, name: str) -> int:
        with self.lock:
            profile = self._require(name)
            profile['runs'] = int(profile.get('runs', 0)) + 1
            self._write()
            return profile['runs']
