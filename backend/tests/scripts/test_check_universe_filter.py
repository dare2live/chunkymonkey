import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_universe_filter.py"
SPEC = importlib.util.spec_from_file_location("check_universe_filter", SCRIPT_PATH)
check_universe_filter = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(check_universe_filter)


def _write_py(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_check_file_flags_production_dim_active_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(check_universe_filter, "REPO_ROOT", tmp_path)
    table_name = "dim_active_" + "a_stock"
    path = _write_py(
        tmp_path / "backend" / "services" / "bad_universe.py",
        f'SQL = "SELECT * FROM {table_name}"\n',
    )

    findings = check_universe_filter.check_file(path)

    assert len(findings) == 1
    assert findings[0]["file"] == "backend/services/bad_universe.py"


def test_check_file_skips_test_fixtures_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(check_universe_filter, "REPO_ROOT", tmp_path)
    table_name = "dim_active_" + "a_stock"
    path = _write_py(
        tmp_path / "backend" / "tests" / "test_fixture.py",
        f'SQL = "CREATE TABLE {table_name} (stock_code TEXT)"\n',
    )

    assert check_universe_filter.check_file(path) == []
    assert len(check_universe_filter.check_file(path, include_tests=True)) == 1


def test_filter_files_excludes_tests_unless_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(check_universe_filter, "REPO_ROOT", tmp_path)
    prod = _write_py(tmp_path / "backend" / "services" / "prod.py", "")
    test = _write_py(tmp_path / "backend" / "tests" / "test_prod.py", "")

    assert check_universe_filter._filter_files([prod, test], include_tests=False) == [prod]
    assert check_universe_filter._filter_files([prod, test], include_tests=True) == [prod, test]
