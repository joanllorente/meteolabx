"""Regresiones de arranque: Ranking no debe cargar el resto de pestañas."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _imported_tabs(statement: str) -> set[str]:
    script = (
        "import sys\n"
        f"{statement}\n"
        "print('\\n'.join(sorted(name for name in sys.modules "
        "if name == 'tabs' or name.startswith('tabs.'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip().startswith("tabs")}


def test_tabs_package_does_not_eagerly_import_tab_modules():
    assert _imported_tabs("import tabs") == {"tabs"}


def test_importing_ranking_does_not_import_other_tabs():
    imported = _imported_tabs("import tabs.ranking")
    assert "tabs.ranking" in imported
    assert imported.isdisjoint(
        {"tabs.observation", "tabs.trends", "tabs.historical", "tabs.map"}
    )


def test_main_tab_loader_requires_the_requested_tab():
    source = (ROOT / "meteolabx.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    loader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_get_tab_module"
    )
    assert [arg.arg for arg in loader.args.args] == ["tab_id"]
    assert '"ranking": "tabs.ranking"' in source
    assert '"map": "tabs.map"' in source


def test_main_waits_for_storage_before_resolving_active_tab():
    source = (ROOT / "meteolabx.py").read_text(encoding="utf-8")
    sidebar_pos = source.index("theme_mode, dark = render_sidebar()")
    storage_gate_pos = source.index("if not local_storage_snapshot_ready():", sidebar_pos)
    active_tab_pos = source.index("active_tab = _sync_active_tab_state()", sidebar_pos)
    assert sidebar_pos < storage_gate_pos < active_tab_pos
    assert "st.stop()" in source[storage_gate_pos:active_tab_pos]


def test_cold_boot_without_autoconnect_forces_ranking_before_tab_render():
    source = (ROOT / "meteolabx.py").read_text(encoding="utf-8")
    storage_gate_pos = source.index("if not local_storage_snapshot_ready():")
    active_tab_sync_pos = source.index("active_tab = _sync_active_tab_state()", storage_gate_pos)
    tab_render_pos = source.index('_boot_mark(f"before tab render', active_tab_sync_pos)

    assert 'st.session_state["_boot_default_tab_pending"] = True' in source[
        storage_gate_pos:active_tab_sync_pos
    ]
    sync_source = source[source.index("def _sync_active_tab_state"):storage_gate_pos]
    assert 'st.session_state["active_tab"] = "ranking"' in sync_source
    assert 'not _query_param_value("e")' in sync_source
    assert active_tab_sync_pos < tab_render_pos


def test_plotly_is_not_registered_for_ranking_or_map():
    source = (ROOT / "meteolabx.py").read_text(encoding="utf-8")
    assert 'if active_tab in {"observation", "trends", "historical"}:' in source


def test_main_keeps_browser_context_separate_from_sensitive_storage():
    source = (ROOT / "meteolabx.py").read_text(encoding="utf-8")
    assert "hydrate_browser_context_live(get_browser_context)" in source
    assert "from components.browser_context import get_browser_context" in source
    assert "_remove_boot_splash()" in source
