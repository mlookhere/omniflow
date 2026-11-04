# OmniFlow

Universal MuMu Android visual automation (Python/PySide6). Work in progress.

## Development state

The repository is being initialized from the previously packaged offline OmniFlow v0.1.0 source. The full application and its legacy assets are **not yet committed**; do not treat this README as an installable release.

See [control issue #1](https://github.com/mlookhere/omniflow/issues/1) for the visual-first workflow editor, universal state engine, 10-instance scheduling, and required test gates.

## Required verification

- Python automation, workflow migration, action and scheduler tests
- Windows Qt smoke tests and actual MuMu ADB/preview/recovery testing
- Reliable scheduled concurrency across up to ten devices

**CI=Passed requires observed successful Actions checks.**
