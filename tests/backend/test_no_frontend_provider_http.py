import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_PROVIDER_MODULES = (
    "services/meteocat.py",
    "services/euskalmet.py",
    "services/meteofrance.py",
    "services/frost.py",
    "services/weatherlink.py",
    "services/rain.py",
    "services/pressure.py",
    "services/_common.py",
    "services/wu_calibration.py",
)


def test_frontend_provider_wrappers_are_removed():
    for relative_path in FRONTEND_PROVIDER_MODULES:
        assert not (ROOT / relative_path).exists(), relative_path


def test_backend_and_domain_do_not_import_historical_services_package():
    for package in (ROOT / "server", ROOT / "domain"):
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not str(node.module or "").startswith("services"), path
                elif isinstance(node, ast.Import):
                    assert all(not alias.name.startswith("services") for alias in node.names), path


def test_provider_parsers_live_in_domain():
    expected = {
        "aemet_climo.py",
        "frost_climo.py",
        "meteocat_climo.py",
        "meteofrance_climo.py",
        "meteogalicia_climo.py",
        "poem.py",
        "weatherlink.py",
        "wu_climo.py",
    }
    assert expected <= {path.name for path in (ROOT / "domain" / "parsing").glob("*.py")}
    assert not list((ROOT / "services").glob("*_parsing.py"))
