from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

from services import holder_research_class as hrc
from services.holder_research_class import (
    HolderResearchClassError,
    classify_holder_name,
    holder_in_preset,
    load_holder_research_class,
)


GOLDEN_POSITIVE = (
    ("中央汇金投资有限责任公司", "huijin", "national_team_stabilizer"),
    ("中央汇金资产管理有限责任公司", "huijin", "national_team_stabilizer"),
    ("中央汇金资产管理有限公司", "huijin", "national_team_stabilizer"),
    ("中国证券金融股份有限公司", "csfc", "national_team_stabilizer"),
    (
        "易方达基金-农业银行-易方达中证金融资产管理计划",
        "csfc_plan",
        "national_team_stabilizer",
    ),
    ("嘉实中证金融资产管理计划", "csfc_plan", "national_team_stabilizer"),
    ("全国社保基金四一三组合", "nssf", "national_team_wind"),
    ("全国社会保障基金理事会", "nssf", "national_team_wind"),
    ("基本养老保险基金一一零一组合", "pension", "national_team_wind"),
    ("梧桐树投资平台有限责任公司", "safe_platform", "national_team_stabilizer"),
)

FALSE_POSITIVES = (
    "徐国新",
    "海南致衍私募基金管理合伙企业(有限合伙)-致衍梧桐树2号私募证券投资基金",
    "中国工商银行股份有限公司-富国新兴产业股票型证券投资基金",
    "中国国际金融股份有限公司",
    "中国平安人寿保险股份有限公司",
    "香港中央结算有限公司",
    "中航基金-徐国新-中航基金丹寅1号单一资产管理计划",
    "上海迎水投资管理有限公司-迎水汇金15号私募证券投资基金",
    "九泰基金-中证金融-九泰基金-泰增战略3号资产管理计划",
    "中国建设银行股份有限公司-华宝中证金融科技主题交易型开放式指数证券投资基金",
    "博时基金-国新投资有限公司-博时基金-国新2号单一资产管理计划",
)


def _write_policy(path: Path, payload: dict) -> Path:
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _live_mapping() -> dict:
    return yaml.safe_load(hrc.CONFIG_PATH.read_text(encoding="utf-8"))


def test_live_policy_loads_and_keeps_nssf_out_of_default() -> None:
    policy = load_holder_research_class()
    assert policy.namespace == "holder_research_class"
    assert policy.include_nssf_in_default is False
    assert policy.default_preset == "national_team_stabilizer"
    assert policy.tag_ids() == {
        "huijin",
        "csfc",
        "csfc_plan",
        "safe_platform",
        "nssf",
        "pension",
        "soe_operator",
    }
    default = policy.preset_by_id("national_team_stabilizer")
    assert default.tags == {"huijin", "csfc", "csfc_plan", "safe_platform"}
    wind = policy.preset_by_id("national_team_wind")
    assert wind.tags == default.tags | {"nssf", "pension"}
    assert len(policy.config_hash) == 64


@pytest.mark.parametrize("name, tag, preset", GOLDEN_POSITIVE)
def test_golden_names_hit_expected_tag_and_preset(name: str, tag: str, preset: str) -> None:
    hit = classify_holder_name(name)
    assert tag in hit.tags
    assert preset in hit.presets
    assert holder_in_preset(name, preset) is True
    if tag in {"nssf", "pension"}:
        assert "national_team_stabilizer" not in hit.presets
        assert holder_in_preset(name, "national_team_stabilizer") is False
    else:
        assert "national_team_stabilizer" in hit.presets
        assert "national_team_wind" in hit.presets


@pytest.mark.parametrize("name", FALSE_POSITIVES)
def test_false_positives_stay_untagged(name: str) -> None:
    hit = classify_holder_name(name)
    assert hit.tags == ()
    assert hit.presets == ()


def test_soe_operator_is_tagged_but_not_in_national_team_presets() -> None:
    hit = classify_holder_name("国新投资有限公司")
    assert hit.tags == ("soe_operator",)
    assert hit.presets == ()
    chengtong = classify_holder_name("中国诚通控股集团有限公司")
    assert chengtong.tags == ("soe_operator",)
    assert chengtong.presets == ()


def test_pension_numeric_account_alias() -> None:
    hit = classify_holder_name("基本养老保险基金000801")
    assert hit.tags == ("pension",)
    assert hit.presets == ("national_team_wind",)


def test_matcher_source_has_no_cjk() -> None:
    text = Path(hrc.__file__).read_text(encoding="utf-8")
    assert re.search(r"[\u4e00-\u9fff]", text) is None


def test_classify_does_not_consult_holder_type() -> None:
    policy = load_holder_research_class()
    assert not hasattr(classify_holder_name, "holder_type")
    hit = classify_holder_name("中国证券金融股份有限公司", policy=policy)
    other = classify_holder_name("中国国际金融股份有限公司", policy=policy)
    assert "csfc" in hit.tags
    assert other.tags == ()


def test_unknown_root_key_is_rejected(tmp_path: Path) -> None:
    payload = _live_mapping()
    payload["extra"] = True
    path = _write_policy(tmp_path / "policy.yaml", payload)
    with pytest.raises(HolderResearchClassError, match="unknown keys"):
        load_holder_research_class(path)


def test_unknown_entity_key_is_rejected(tmp_path: Path) -> None:
    payload = _live_mapping()
    payload["entities"][0]["holder_type"] = "证券公司"
    path = _write_policy(tmp_path / "policy.yaml", payload)
    with pytest.raises(HolderResearchClassError, match="unknown keys"):
        load_holder_research_class(path)


def test_nssf_cannot_enter_default_while_flag_is_false(tmp_path: Path) -> None:
    payload = _live_mapping()
    payload["presets"][0]["tags"].append("nssf")
    path = _write_policy(tmp_path / "policy.yaml", payload)
    with pytest.raises(HolderResearchClassError, match="omit nssf and pension"):
        load_holder_research_class(path)


def test_flag_true_requires_nssf_in_default(tmp_path: Path) -> None:
    payload = _live_mapping()
    payload["include_nssf_in_default"] = True
    path = _write_policy(tmp_path / "policy.yaml", payload)
    with pytest.raises(HolderResearchClassError, match="omits nssf"):
        load_holder_research_class(path)


def test_unknown_preset_id_fails_closed() -> None:
    with pytest.raises(HolderResearchClassError, match="unknown preset"):
        holder_in_preset("中央汇金投资有限责任公司", "national_team_unspecified")
