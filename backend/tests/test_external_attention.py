from datetime import date, datetime, timedelta

from services import external_attention


def test_snapshot_helpers_accept_records():
    comment_map = external_attention._normalize_comment_snapshot([
        {
            "代码": "1",
            "名称": "平安银行",
            "交易日": "20260501",
            "最新价": "12.34",
            "涨跌幅": "1.2",
            "换手率": "0.8",
            "市盈率": "7.5",
            "主力成本": "11.9",
            "机构参与度": 0.35,
            "综合得分": "72",
            "上升": "3",
            "目前排名": "18",
            "关注指数": "66.5",
        }
    ])

    assert comment_map["000001"]["stock_name"] == "平安银行"
    assert comment_map["000001"]["comment_trade_date"] == "2026-05-01"
    assert comment_map["000001"]["institution_participation"] == 35.0
    assert comment_map["000001"]["comment_available"] == 1

    today = date.today()
    survey_map = external_attention._aggregate_survey_snapshot([
        {
            "代码": "000001",
            "接待日期": today.isoformat(),
            "公告日期": (today - timedelta(days=1)).isoformat(),
            "接待机构数量": "4",
            "接待方式": "现场调研",
        }
    ])

    assert survey_map["000001"]["survey_count_30d"] == 1
    assert survey_map["000001"]["survey_org_total_90d"] == 4
    assert survey_map["000001"]["last_survey_reception"] == "现场调研"


def test_fetch_stock_attention_detail_uses_record_payloads(monkeypatch):
    today = date.today()
    now = datetime.now()
    payloads = {
        "stock_individual_info_em": [
            {"item": "股票简称", "value": "平安银行"},
            {"item": "行业", "value": "银行"},
        ],
        "stock_comment_detail_zlkp_jgcyd_em": [
            {"交易日": (today - timedelta(days=idx)).isoformat(), "机构参与度": 50 + idx}
            for idx in range(3)
        ],
        "stock_comment_detail_zhpj_lspf_em": [
            {"交易日": today.isoformat(), "评分": 72}
        ],
        "stock_comment_detail_scrd_focus_em": [
            {"交易日": today.isoformat(), "用户关注指数": 80}
        ],
        "stock_comment_detail_scrd_desire_em": [
            {"交易日": today.isoformat(), "市场参与意愿": 65}
        ],
        "stock_research_report_em": [
            {
                "日期": today.isoformat(),
                "机构": "示例证券",
                "东财评级": "买入",
                "报告名称": "业绩稳健增长",
                "目标价": "15.5",
            }
        ],
        "stock_news_em": [
            {
                "发布时间": now.strftime("%Y-%m-%d %H:%M:%S"),
                "文章来源": "示例新闻",
                "标题": "关注度提升",
                "新闻内容": "调研活动增加",
            }
        ],
    }

    def fake_call(func_name, *args, **kwargs):
        assert kwargs.get("symbol") in (None, "000001")
        return payloads.get(func_name, [])

    monkeypatch.setattr(external_attention, "_call_akshare_records", fake_call)

    detail = external_attention.fetch_stock_attention_detail("1")

    assert detail["stock_code"] == "000001"
    assert detail["stock_name"] == "平安银行"
    assert detail["basic_info"]["行业"] == "银行"
    assert detail["series"]["focus_index"]["current"] == 80.0
    assert detail["research"]["count_total"] == 1
    assert detail["news"]["count_total"] == 1
    assert detail["diagnostics"]["research_report"]["ok"] is True
    assert len(detail["timeline_events"]) == 2


def test_akshare_cache_returns_copies():
    key = ("func", (), ())
    external_attention._akshare_cache_put(key, [{"代码": "000001", "值": 1}])

    cached = external_attention._akshare_cache_get(key)
    cached[0]["值"] = 99

    assert external_attention._akshare_cache_get(key)[0]["值"] == 1
