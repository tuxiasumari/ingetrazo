# Development guide

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

Requires Python 3.11+.

## Running tests

```bash
python -m pytest tests/
```

(No tests yet — contributions welcome.)

## Style

- PEP 8, 100-character soft limit.
- All code, comments and commit messages in English.
- UI strings localized via `i18n/`. Never hardcode user-facing text.

## Submitting changes

See [../CONTRIBUTING.md](../CONTRIBUTING.md).
