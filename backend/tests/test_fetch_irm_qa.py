"""fetch_irm_qa 离线单测 — SH HTML 解析 / 相对时间还原 / 月度 parquet 幂等 merge.

HTML fixture 来自 2026-06-11 实测捕获结构 (sns.sseinfo.com /ajax/userfeeds.do 与
/ajax/feeds.do 两种变体), 不发任何网络请求.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from bs4 import BeautifulSoup

import scripts.fetch_irm_qa as F

REF = datetime(2026, 6, 11, 22, 30, 0)

# 2026-06-11 实测 userfeeds.do 返回结构还原: 问块 ask_ico + 答块 answer_ico,
# 答块时间 m_feed_from 挂在 detail 外的兄弟 .m_feed_func (实测布局)
ITEM_USERFEEDS = """
<div class="m_feed_item" id="item-1745384">
  <div class="m_feed_detail" style="border: none;">
    <div class="m_feed_face"><a rel="face" uid="120315" href="user.do?uid=120315"></a><p>投资者_1523083768000</p></div>
    <div class="m_feed_cnt">
      <div class="m_feed_info"><div class="index_ico ask_ico"></div></div>
      <div class="m_feed_txt"><a href='user.do?uid=24998' >:会稽山(601579)</a>董秘您好，请问618期间销量如何？</div>
      <div class="m_feed_func">
        <div class="m_feed_from"><span>13小时前</span><em>来自</em><a href="javascript:;">微信</a></div>
      </div>
    </div>
  </div>
  <div class="m_feed_detail m_qa">
    <div class="m_feed_face"><a rel="tag" class="ansface" uid="24998" href="user.do?uid=24998"></a><p>会稽山</p></div>
    <div class="m_feed_cnt">
      <div class="m_feed_info"><div class="index_ico answer_ico"></div></div>
      <div class="m_feed_txt" id="m_feed_txt-1745384">尊敬的投资者，您好！目前爽酒销售情况正常。</div>
    </div>
  </div>
  <div class="m_feed_func top10">
    <div class="m_feed_from"><span>12小时前</span><em>来自</em><a href="javascript:;">网站</a></div>
  </div>
</div>
"""


@pytest.mark.parametrize("text,want", [
    ("45分钟前", REF - timedelta(minutes=45)),
    ("13小时前", REF - timedelta(hours=13)),
    ("刚刚", REF),
    ("3天前", REF - timedelta(days=3)),
    ("今天 09:15", datetime(2026, 6, 11, 9, 15)),
    ("昨天 21:00", datetime(2026, 6, 10, 21, 0)),
    ("06月10日 09:30", datetime(2026, 6, 10, 9, 30)),
    ("06-10 09:30", datetime(2026, 6, 10, 9, 30)),
    ("12月30日 10:00", datetime(2025, 12, 30, 10, 0)),  # 跨年回退
    ("2026-06-01 08:00", datetime(2026, 6, 1, 8, 0)),
    ("2026年06月01日 08:00", datetime(2026, 6, 1, 8, 0)),
    ("乱七八糟", None),
])
def test_parse_sh_time(text, want):
    assert F.parse_sh_time(text, REF) == want


def test_parse_sh_time_truncates_relative_to_minute():
    ref = datetime(2026, 6, 11, 22, 30, 45, 123456)
    got = F.parse_sh_time("13小时前", ref)
    assert got == datetime(2026, 6, 11, 9, 30, 0)  # 不携带 ref 的秒/微秒伪精度


def _item(html: str):
    return BeautifulSoup(html, "lxml").select_one("div.m_feed_item")


def test_parse_sh_item_userfeeds_layout():
    row = F._parse_sh_item(_item(ITEM_USERFEEDS), REF)
    assert row is not None
    assert row["code"] == "601579"
    assert row["question"] == "董秘您好，请问618期间销量如何？"
    assert row["answer"] == "尊敬的投资者，您好！目前爽酒销售情况正常。"
    assert row["q_time"] == REF - timedelta(hours=13)
    # 答块时间挂在 detail 外 -> item 级兜底取第二个 m_feed_from
    assert row["a_time"] == REF - timedelta(hours=12)
    assert row["source"] == "sh"


def test_parse_sh_item_feeds_layout_variant():
    # feeds.do 变体: 问 detail 带 m_qa_detail class, 问 m_feed_txt 带 id (实测差异)
    html = ITEM_USERFEEDS.replace(
        '<div class="m_feed_detail" style="border: none;">',
        '<div class="m_feed_detail m_qa_detail">',
    ).replace(
        '<div class="m_feed_txt"><a href',
        '<div class="m_feed_txt" id="m_feed_txt-1745384"><a href',
    )
    row = F._parse_sh_item(_item(html), REF)
    assert row is not None and row["code"] == "601579"


def test_parse_sh_item_unanswered():
    html = ITEM_USERFEEDS.split('<div class="m_feed_detail m_qa">')[0] + "</div>"
    row = F._parse_sh_item(_item(html), REF)
    assert row is not None
    assert row["answer"] is None and row["a_time"] is None


def _sz_row(fetched: str, answer: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "code": "000001", "question": "Q1", "answer": answer,
        "q_time": pd.Timestamp("2026-06-09 10:45:19"),
        "a_time": pd.Timestamp("2026-06-11 21:43:33"),
        "source": "sz", "fetched_at": pd.Timestamp(fetched)}])


def test_write_month_files_idempotent_keeps_latest(tmp_path):
    F.write_month_files(_sz_row("2026-06-11 22:00", "old"), tmp_path)
    F.write_month_files(_sz_row("2026-06-11 23:00", "new"), tmp_path)
    df = pd.read_parquet(tmp_path / "irm_qa_202606.parquet")
    assert len(df) == 1
    assert df.iloc[0]["answer"] == "new"
    assert list(df.columns) == [
        "code", "question", "answer", "q_time", "a_time", "source", "fetched_at"]


def test_write_month_files_sh_qtime_jitter_dedup(tmp_path):
    def sh(qt: str, fetched: str) -> pd.DataFrame:
        return pd.DataFrame([{
            "code": "601579", "question": "Q-sh", "answer": "A-sh",
            "q_time": pd.Timestamp(qt), "a_time": pd.NaT,
            "source": "sh", "fetched_at": pd.Timestamp(fetched)}])

    F.write_month_files(sh("2026-06-11 09:30", "2026-06-11 22:00"), tmp_path)
    # 相对时间还原的分钟级抖动不应产生重复行 (sh 的 q_time 不入 dedup key)
    F.write_month_files(sh("2026-06-11 09:31", "2026-06-11 23:00"), tmp_path)
    df = pd.read_parquet(tmp_path / "irm_qa_202606.parquet")
    assert len(df) == 1


def test_write_month_files_partitions_by_qtime_month(tmp_path):
    may = _sz_row("2026-06-11 22:00", "a")
    may["q_time"] = pd.Timestamp("2026-05-20 10:00")  # 5 月提问, 6 月回答 -> 落 202605
    written = F.write_month_files(may, tmp_path)
    assert "irm_qa_202605.parquet" in written
    assert (tmp_path / "irm_qa_202605.parquet").exists()
