"""``backend/config/nominal_ohlcv_acquire.yaml`` loader + row validator — offline.

Every fail-closed test injects its own minimal-valid YAML doc via ``tmp_path``
(project rule: feedback-test-must-carry-its-own-fixture) and isolates exactly
one gating condition (project rule: 每个门控条件一个隔离用例) — everything
else in the fixture stays valid so a mutation that disables the *wrong* check
cannot pass this test by accident. One test (``test_real_config_loads_and_is_
internally_consistent``) reads the real checked-in config with no injected
path and no monkeypatching, per 业主 09-16 明令 "至少一条测试读真实配置文件"。

The row-level validator built on top of the loaded rules
(``build_pre_close_origin_validator``) is unit-tested here in isolation
(construction + both directions of the ``kind==unknown⇔pre_close IS NULL``
check); the end-to-end land→accept path that exercises it through
``security_day_partition._candidate_rows`` is covered separately in
``test_nominal_ohlcv_acceptance.py`` (assertions 2-5 of the cut-1 spec).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.data_sources.nominal_ohlcv_acquire_rules import (
    NominalOhlcvAcquireRules,
    build_pre_close_origin_validator,
    load_nominal_ohlcv_acquire_rules,
)
from services.data_sources.security_day_partition import SecurityDayValidationError

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REAL_CONFIG_PATH = _BACKEND_DIR / "config" / "nominal_ohlcv_acquire.yaml"


def _minimal_valid_rules() -> dict:
    """A self-contained, loader-valid doc every fail-closed test mutates one
    field of."""
    return {
        "version": 1,
        "pre_close_origin": {
            "provider_x": {"kind": "provider", "note": "test provider"},
            "derived_y": {"kind": "derived", "note": "test derived"},
            "unknown_z": {"kind": "unknown", "note": "test unknown"},
        },
        "reference": {
            "source": "baostock",
            # 返修 (blocking 发现修复) 新增字段, 校验必须落在
            # sources/baostock.py::API_FUNCTION_NAMES 白名单里 —— 隔离用例见下方
            # test_reference_api_not_in_whitelist_rejected。
            "api": "query_history_k_data_plus",
            "close_tolerance": 0.005,
            "sh_sz_max_unknown_rows": 5,
            # 2026-09-16 刀2 新增字段 (fuyao dump + baostock 适配器用) — 隔离用例
            # 见 test_fuyao_daily_k_adapter.py 那五条各自 mutate 一个字段的用例;
            # 这里只需要一份内部自洽的最小合法值。
            "prefetch_window_days": 15,
            "baostock_fields": ["date", "code", "close", "preclose", "volume", "tradestatus"],
            "exchange_suffix_to_baostock_prefix": {"SH": "sh.", "SZ": "sz."},
            "change_pct_round_digits": 4,
        },
        "dump": {
            # 返修 (blocking 发现修复) 新增字段, 仓库相对路径 —— 隔离用例见下方
            # test_dump_cache_dir_absolute_path_rejected /
            # test_dump_cache_dir_dotdot_rejected。
            "cache_dir": "data/scratch/fuyao_dumps",
            "incremental_floor": "20260901",
            "column_map": {
                "thscode": "ts_code",
                "date_ms": "trade_date",
                "open_price": "open",
                "high_price": "high",
                "low_price": "low",
                "close_price": "close",
                "volume": "vol",
                "turnover": "amount",
            },
            "vol_divisor": 100,
            "amount_divisor": 1000,
            "adjusted_filter_value": "none",
            "timezone": "Asia/Shanghai",
        },
        "backfill_origin_by_source": {
            "vendor_a": "provider_x",
            "vendor_b": "derived_y",
        },
    }


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "nominal_ohlcv_acquire.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# control case — the fixture itself must be valid, and the real config too
# ---------------------------------------------------------------------------


def test_minimal_valid_fixture_loads_successfully(tmp_path):
    path = _write(tmp_path, _minimal_valid_rules())
    rules = load_nominal_ohlcv_acquire_rules(path)
    assert rules.allowed_pre_close_origins == {"provider_x", "derived_y", "unknown_z"}
    assert rules.reference_source == "baostock"
    assert rules.reference_api == "query_history_k_data_plus"
    assert rules.close_tolerance == 0.005
    assert rules.sh_sz_max_unknown_rows == 5
    assert rules.dump_cache_dir == "data/scratch/fuyao_dumps"
    assert rules.dump_incremental_floor == "20260901"
    assert rules.backfill_origin_by_source == {"vendor_a": "provider_x", "vendor_b": "derived_y"}


def test_real_config_loads_and_is_internally_consistent():
    """Reads the real checked-in ``nominal_ohlcv_acquire.yaml`` — no injected
    path, no monkeypatch (业主 09-16 明令: 至少一条测试读真实配置文件)."""

    rules = load_nominal_ohlcv_acquire_rules()
    assert isinstance(rules, NominalOhlcvAcquireRules)
    expected_origins = {
        "provider_tushare",
        "derived_tdxhub_xdxr",
        "provider_baostock",
        "unknown_no_reference_bj",
        "unknown_reference_unavailable",
        "unknown_reference_mismatch",
    }
    assert rules.allowed_pre_close_origins == expected_origins
    assert rules.pre_close_origin["provider_tushare"].kind == "provider"
    assert rules.pre_close_origin["derived_tdxhub_xdxr"].kind == "derived"
    assert rules.pre_close_origin["unknown_no_reference_bj"].kind == "unknown"
    assert rules.reference_source == "baostock"
    assert rules.reference_api == "query_history_k_data_plus"
    assert rules.sh_sz_max_unknown_rows == 5
    assert rules.dump_cache_dir == "data/scratch/fuyao_dumps"
    assert rules.dump_incremental_floor == "20260901"
    # 修订5: backfill_origin_by_source 的每个值必须属于 pre_close_origin 取值集
    # (loader 已经在 load 时校验过, 这里对真实配置再断言一次作为回归钉子)。
    assert set(rules.backfill_origin_by_source.values()) <= rules.allowed_pre_close_origins
    assert rules.backfill_origin_by_source == {
        "tushare": "provider_tushare",
        "tdxhub": "derived_tdxhub_xdxr",
    }


# ---------------------------------------------------------------------------
# top-level key set (assertion 1a)
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["extra_top_key"] = True
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: 顶层键"):
        load_nominal_ohlcv_acquire_rules(path)


def test_missing_top_level_key_rejected(tmp_path):
    data = _minimal_valid_rules()
    del data["dump"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: 顶层键"):
        load_nominal_ohlcv_acquire_rules(path)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_non_positive_version_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["version"] = 0
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: version"):
        load_nominal_ohlcv_acquire_rules(path)


def test_bool_version_rejected(tmp_path):
    """bool is an int subclass in Python — must not sneak past the positive-int check."""
    data = _minimal_valid_rules()
    data["version"] = True
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: version"):
        load_nominal_ohlcv_acquire_rules(path)


# ---------------------------------------------------------------------------
# pre_close_origin — empty set / unknown kind (assertion 1b) / entry shape
# ---------------------------------------------------------------------------


def test_empty_pre_close_origin_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["pre_close_origin"] = {}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: pre_close_origin 取值集不能为空"):
        load_nominal_ohlcv_acquire_rules(path)


def test_unknown_kind_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["pre_close_origin"]["provider_x"]["kind"] = "provider2"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: pre_close_origin\.provider_x\.kind"):
        load_nominal_ohlcv_acquire_rules(path)


def test_pre_close_origin_entry_unknown_key_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["pre_close_origin"]["provider_x"]["extra"] = "nope"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: pre_close_origin\.provider_x 键必须"):
        load_nominal_ohlcv_acquire_rules(path)


def test_pre_close_origin_empty_note_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["pre_close_origin"]["provider_x"]["note"] = "  "
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: pre_close_origin\.provider_x\.note"):
        load_nominal_ohlcv_acquire_rules(path)


# ---------------------------------------------------------------------------
# reference — source / close_tolerance / sh_sz_max_unknown_rows
# ---------------------------------------------------------------------------


def test_reference_unknown_key_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["reference"]["extra"] = 1
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: reference 键必须"):
        load_nominal_ohlcv_acquire_rules(path)


def test_reference_empty_source_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["reference"]["source"] = ""
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: reference\.source"):
        load_nominal_ohlcv_acquire_rules(path)


def test_close_tolerance_zero_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["reference"]["close_tolerance"] = 0
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: reference\.close_tolerance"):
        load_nominal_ohlcv_acquire_rules(path)


def test_close_tolerance_negative_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["reference"]["close_tolerance"] = -0.01
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: reference\.close_tolerance"):
        load_nominal_ohlcv_acquire_rules(path)


def test_sh_sz_max_unknown_rows_zero_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["reference"]["sh_sz_max_unknown_rows"] = 0
    path = _write(tmp_path, data)
    with pytest.raises(
        ValueError, match=r"nominal_ohlcv_acquire: reference\.sh_sz_max_unknown_rows"
    ):
        load_nominal_ohlcv_acquire_rules(path)


def test_sh_sz_max_unknown_rows_non_int_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["reference"]["sh_sz_max_unknown_rows"] = 5.5
    path = _write(tmp_path, data)
    with pytest.raises(
        ValueError, match=r"nominal_ohlcv_acquire: reference\.sh_sz_max_unknown_rows"
    ):
        load_nominal_ohlcv_acquire_rules(path)


def test_reference_api_not_in_whitelist_rejected(tmp_path):
    """隔离用例: 只违反 reference.api 这一条, 其它字段全部合法。"""
    data = _minimal_valid_rules()
    data["reference"]["api"] = "query_daily_history_k_totally_made_up"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: reference\.api"):
        load_nominal_ohlcv_acquire_rules(path)


def test_reference_api_accepts_other_whitelisted_endpoint(tmp_path):
    """白名单不是只认死一个值 —— sources/baostock.py::API_FUNCTION_NAMES 里的另一个
    合法端点也必须放行, 证明校验真的在查白名单而不是在比字符串相等。"""
    data = _minimal_valid_rules()
    data["reference"]["api"] = "query_daily_history_k_AStock"
    path = _write(tmp_path, data)
    rules = load_nominal_ohlcv_acquire_rules(path)
    assert rules.reference_api == "query_daily_history_k_AStock"


# ---------------------------------------------------------------------------
# dump.incremental_floor / dump.cache_dir
# ---------------------------------------------------------------------------


def test_incremental_floor_unknown_key_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["dump"]["extra"] = "x"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: dump 键必须"):
        load_nominal_ohlcv_acquire_rules(path)


def test_incremental_floor_bad_format_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["dump"]["incremental_floor"] = "2026-09-01"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: dump\.incremental_floor"):
        load_nominal_ohlcv_acquire_rules(path)


def test_dump_cache_dir_empty_rejected(tmp_path):
    """隔离用例: 只违反 dump.cache_dir 这一条 (空字符串)。"""
    data = _minimal_valid_rules()
    data["dump"]["cache_dir"] = "  "
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: dump\.cache_dir"):
        load_nominal_ohlcv_acquire_rules(path)


def test_dump_cache_dir_absolute_path_rejected(tmp_path):
    """隔离用例: 只违反 dump.cache_dir 这一条 (绝对路径)。"""
    data = _minimal_valid_rules()
    data["dump"]["cache_dir"] = "/etc/fuyao_dumps"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: dump\.cache_dir"):
        load_nominal_ohlcv_acquire_rules(path)


def test_dump_cache_dir_dotdot_rejected(tmp_path):
    """隔离用例: 只违反 dump.cache_dir 这一条 (路径穿越)。"""
    data = _minimal_valid_rules()
    data["dump"]["cache_dir"] = "data/scratch/../../etc"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: dump\.cache_dir"):
        load_nominal_ohlcv_acquire_rules(path)


# ---------------------------------------------------------------------------
# backfill_origin_by_source (assertion 5 / 修订5)
# ---------------------------------------------------------------------------


def test_backfill_origin_not_in_allowed_set_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["backfill_origin_by_source"]["vendor_a"] = "not_a_registered_origin"
    path = _write(tmp_path, data)
    with pytest.raises(
        ValueError, match=r"nominal_ohlcv_acquire: backfill_origin_by_source\.vendor_a"
    ):
        load_nominal_ohlcv_acquire_rules(path)


def test_backfill_origin_by_source_empty_rejected(tmp_path):
    data = _minimal_valid_rules()
    data["backfill_origin_by_source"] = {}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"nominal_ohlcv_acquire: backfill_origin_by_source 不能为空"):
        load_nominal_ohlcv_acquire_rules(path)


# ---------------------------------------------------------------------------
# build_pre_close_origin_validator — unit tests on the returned callable
# ---------------------------------------------------------------------------


def _rules(tmp_path) -> NominalOhlcvAcquireRules:
    return load_nominal_ohlcv_acquire_rules(_write(tmp_path, _minimal_valid_rules()))


def test_validator_accepts_provider_row_with_value(tmp_path):
    validate = build_pre_close_origin_validator(_rules(tmp_path))
    validate({"pre_close": 13.16, "pre_close_origin": "provider_x"})  # no raise


def test_validator_accepts_unknown_row_with_null(tmp_path):
    validate = build_pre_close_origin_validator(_rules(tmp_path))
    validate({"pre_close": None, "pre_close_origin": "unknown_z"})  # no raise


def test_validator_rejects_origin_not_in_set(tmp_path):
    validate = build_pre_close_origin_validator(_rules(tmp_path))
    with pytest.raises(SecurityDayValidationError) as caught:
        validate({"pre_close": 13.16, "pre_close_origin": "foo"})
    assert caught.value.code == "INVALID_ENRICHMENT"


def test_validator_rejects_unknown_kind_with_non_null_value(tmp_path):
    validate = build_pre_close_origin_validator(_rules(tmp_path))
    with pytest.raises(SecurityDayValidationError) as caught:
        validate({"pre_close": 39.30, "pre_close_origin": "unknown_z"})
    assert caught.value.code == "INVALID_ENRICHMENT"
    assert "unknown⇔NULL" in caught.value.detail


def test_validator_rejects_provider_kind_with_null_value(tmp_path):
    validate = build_pre_close_origin_validator(_rules(tmp_path))
    with pytest.raises(SecurityDayValidationError) as caught:
        validate({"pre_close": None, "pre_close_origin": "provider_x"})
    assert caught.value.code == "INVALID_ENRICHMENT"
    assert "unknown⇔NULL" in caught.value.detail
