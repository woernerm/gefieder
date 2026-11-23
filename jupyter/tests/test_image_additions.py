"""What this image adds to Lab and to quak by hand, checked where both are installed.

Neither addition fails loudly: an extension Lab does not recognise is skipped with a line in
the browser's console, and a quak bundle the patch no longer matches renders the same broken
histogram it always did. So both are asserted against the packages this image installed.
"""
import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

VENV = Path(os.environ.get("NOTEBOOK_VENV", "/opt/notebook"))
CLONE = VENV / "share" / "jupyter" / "labextensions" / "models-clone"


def test_lab_loads_the_clone_plugin():
    listed = subprocess.run(
        [VENV / "bin" / "jupyter", "labextension", "list"],
        capture_output=True, text=True, check=True,
    )
    # Lab writes the list to stderr.
    line = next(l for l in listed.stderr.splitlines() if "models-clone" in l)
    assert "enabled" in line and "OK" in line


def test_the_clone_dialog_is_offered_in_the_file_menu_and_the_toolbar():
    schema = json.loads((CLONE / "schemas" / "models-clone" / "plugin.json").read_text())
    command = "models-clone:open"

    menus = schema["jupyter.lab.menus"]["main"]
    file_menu = next(menu for menu in menus if menu["id"] == "jp-mainmenu-file")
    assert command in [item["command"] for item in file_menu["items"]]
    assert command in [item["command"] for item in schema["jupyter.lab.toolbars"]["FileBrowser"]]
    assert f'"{command}"' in (CLONE / "static" / "remoteEntry.js").read_text()


def themed_widget():
    """startup.py's _themed_widget, without running the rest of the file.

    The file loads SQLMesh into a kernel when it runs, so only its definitions are taken.
    """
    source = (VENV / "etc" / "sqlmesh_startup.py").read_text()
    module = ast.parse(source)
    module.body = [
        node for node in module.body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef))
    ]
    namespace = {}
    exec(compile(module, "sqlmesh_startup.py", "exec"), namespace)
    return namespace["_themed_widget"]


def test_a_column_holding_one_value_is_binned_around_it():
    quak = pytest.importorskip("quak")

    widget = themed_widget()(quak)

    assert widget is not quak.Widget, "quak's export no longer matches; the table is unthemed"
    patched = re.search(r"function [\w$]+\(([\w$]+),([\w$]+),[\w$]+\)\{\1===\2&&", widget._esm)
    assert patched, "quak's bin function no longer matches; constant columns break again"
