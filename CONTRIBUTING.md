# Contributing to Wasia

Thank you for your interest in Wasia! Contributions of any kind are welcome — code, documentation, translations, bug reports, design feedback.

## Development setup

```bash
git clone https://github.com/<your-user>/wasia.git
cd wasia
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

Requires Python 3.11+.

## Code style

- **Language**: all code, comments, commit messages and pull request descriptions in **English**, so anyone in the world can contribute.
- **PEP 8** with a soft 100-character line limit.
- **Type hints** encouraged for public functions, not enforced.
- **Docstrings** for public APIs (short and clear).
- **Spanish UI strings** live in `i18n/es.json`; English in `i18n/en.json`. Never hardcode user-facing text.

## Folder layout

See [docs/architecture.md](docs/architecture.md) for a tour. Briefly:

- `core/` — configuration, scene graph, camera, geometry primitives, layers.
- `views/` — Qt widgets (main window, viewport, side panels).
- `tools/` — built-in modeling tools (line, rectangle, push/pull, select, ...).
- `plugins/` — third-party tools, loaded at runtime.
- `georef/` — real-world location: DEM, satellite tiles, projections.
- `styles/` — visual style presets (shader modes).
- `materials/` — material library and editor.
- `analysis/` — 3D-printing-oriented checks (manifold, wall thickness, overhangs).
- `formats/` — import / export (OBJ, COLLADA, glTF, STL, 3MF, IFC, native).
- `i18n/` — UI translations.
- `resources/` — shaders, icons, fonts, stylesheets.
- `docs/` — architecture and contributor documentation.
- `tests/` — automated tests.

## Workflow

1. **Fork** the repository.
2. Create a feature branch: `git checkout -b feature/short-description`.
3. Commit small, focused changes with descriptive messages.
4. Push to your fork and open a **Pull Request**.
5. A maintainer will review. We try to respond within a week.

## Issue triage

- **Bug report** — describe the bug, steps to reproduce, expected vs. actual behavior, screenshots when relevant.
- **Feature request** — what you want, why it matters, alternative tools that do it today.
- **Good first issue** — these are tagged for newcomers. Comment on the issue to claim one.

## Communication

- Open **issues** for bugs and proposals.
- Use **GitHub Discussions** for open-ended questions, architecture debates, and showcasing your projects built with Wasia.

## Licensing of contributions

By submitting a contribution you agree it will be licensed under **GPL-3.0-or-later** (the project license).
