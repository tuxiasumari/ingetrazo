# Writing a Wasia plugin

Early draft. The plugin API is **not stable yet** — expect breaking changes during the 0.x series.

A plugin is a Python file (or package) placed under `plugins/` that registers one or more tools, importers, exporters or panels.

## Minimal example

```python
# plugins/hello_tool.py
from tools.base import Tool


class HelloTool(Tool):
    name = "Hello"
    shortcut = "H"

    def on_activate(self, viewport):
        viewport.show_message("Hello from a plugin!")

    def on_deactivate(self, viewport):
        pass
```

Wasia discovers `Tool` subclasses inside `plugins/` and adds them to the toolbar.

## Roadmap

- Tool registration (in progress).
- Importer / exporter registration.
- Side-panel registration.
- Plugin manifest (`plugin.toml`) for metadata and dependencies.
- Plugin manager UI (install, enable, disable, update).
