#!/usr/bin/env python3
"""互动易/e互动 问答直抓 — 产业链 L2 关系边数据源 (TuShare irm_qa_sz/sh 无权限的替代).

接口形态 (2026-06-11 实测, 共 10 个探测请求 @>=2.2s 间隔, 全部记录于本 docstring):

[深交所互动易] POST http://irm.cninfo.com.cn/newircs/index/search
  form: pageNo / pageSize / searchTypes="11" (问答类型)
  返回 JSON: {pageNo, pageSize, totalRecord, totalPage, results, count}
  results 行字段 (实测样例 001205 盛航股份):
    stockCode / companyShortName / mainContent(提问) / attachedContent(回答, 可缺)
    pubDate(提问时间, epoch-ms 字符串) / attachedPubDate(回答时间, epoch-ms 字符串)
    updateDate(epoch-ms, 流排序键) / qaStatus(2=已答) / esId(唯一 id) / secid / trade(行业)
  排序: updateDate 降序 (实测 page1 相邻行 1781185443000 > 1781185383000, 顶部行
  updateDate=2026-06-11 21:44 与 packageDate "45分钟前" 吻合)。
  per-code 服务端过滤实测全部失败 (totalRecord=0):
    stockCode=000001 / keyWord=000001 / stockCode="001205" (已知有 71328 总记录中含其问答)
    / stockCode="000001,gssz0000001" (secid 经 POST /newircs/index/queryKeyboardInfo
    keyWord=000001 实测解析成功)。
  => TODO(per-code filter): 服务端按代码过滤参数未明; 本脚本采用全局流翻页 + 本地按
  代码过滤 — 对多代码列表反而请求数更少 (一次翻页覆盖所有代码), 不是规避。

[上证e互动] POST http://sns.sseinfo.com/ajax/feeds.do
  form: type=11 / pageSize / page / lastid=-1 / show=1
  返回 HTML 片段: div.m_feed_item 列表, 每 item 内:
    含 .ask_ico 的 .m_feed_detail = 提问块, 其 .m_feed_txt 以锚文本 ":公司名(6位代码)"
    开头, 其后为提问正文; 含 .answer_ico 的块 = 回答块 (可缺 = 未答)。
    .m_feed_from > span = 时间, 相对文本 ("45分钟前"/"13小时前"), 旧条目为绝对日期。
  代码从提问前缀锚文本提取 — 同样全局流 + 本地过滤, 无需解析公司 uid
  (per-uid 端点 POST /ajax/userfeeds.do typeCode=company&uid=N 已实测可用, 备查;
  但 code->uid 解析端点未在 10 请求预算内验证, 故不依赖)。

落盘: data/concept_snapshots/irm_qa/irm_qa_<YYYYMM>.parquet (按提问时间月份分文件)
字段: code/question/answer/q_time/a_time/source[sz|sh]/fetched_at (北京时间, tz-naive)
幂等: 同月重抓 = 读旧文件 merge + 按 dedup key 去重 (保留最新 fetched_at) + 原子覆盖。

失败纪律 (宪法 v2 第 6 条): 0 行 / 结构漂移 / 翻页不前进 / 时间全解析失败 => 显式
raise, 绝不静默落空; 仅 httpx 传输层错误做有限退避重试。

用法:
  .venv/bin/python backend/scripts/fetch_irm_qa.py --codes 000001,600519 --days 3
  .venv/bin/python backend/scripts/fetch_irm_qa.py --days 1 --source sz   # 全市场
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO / "data" / "concept_snapshots" / "irm_qa"

# ---------------- 限频/端点常量 (来源见行内注释) ----------------
# 礼貌限频: 2026-06-11 调研实测 10 请求 @2.2s 间隔双端点均无封禁/限流响应;
# 沿用同量级, 不加速 (交易所公开接口, 非授权 API)。
REQUEST_INTERVAL_SEC = 2.0
# 传输层错误 (连接/超时) 才重试; HTTP 非 200 / 解析失败一律 raise 不重试 (调研纪律: 失败不重试轰炸)。
TRANSPORT_RETRIES = 2
RETRY_BACKOFF_SEC = 5.0
TIMEOUT_SEC = 15.0          # 实测两端点响应均 < 3s, 15s 足够余量
SZ_PAGE_SIZE = 50           # 实测仅验证 5/10; 若服务端 cap, 翻页以响应 echo 的 totalPage 为准不失步
SH_PAGE_SIZE = 20           # 实测验证 3/5; e互动日均量远低于互动易, 20 足够
MAX_PAGES_PER_SOURCE = 400  # 防 runaway: 400 页 x 2s ~= 13min 上限; 触顶 raise 提示调小 --days
SZ_SEARCH_URL = "http://irm.cninfo.com.cn/newircs/index/search"
SH_FEEDS_URL = "http://sns.sseinfo.com/ajax/feeds.do"
# UA 必带 (任务约束); 普通桌面 Chrome UA, 2026-06-11 实测两端点接受。
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_BJ_TZ = timezone(timedelta(hours=8))  # 数学常数: 北京时区固定 UTC+8, 无夏令时


def now_bj() -> datetime:
    return datetime.now(_BJ_TZ).replace(tzinfo=None)


_last_request_ts = 0.0


def _polite_request(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    """全局共享限频 + 仅传输层错误退避重试; 非 200 raise."""
    global _last_request_ts
    last_exc: Exception | None = None
    for attempt in range(TRANSPORT_RETRIES + 1):
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()
        try:
            resp = client.request(method, url, **kw)
        except httpx.TransportError as exc:
            last_exc = exc
            print(f"WARN transport error try{attempt + 1}/{TRANSPORT_RETRIES + 1} "
                  f"{url}: {str(exc)[:120]}", flush=True)
            time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise RuntimeError(
                f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
        return resp
    raise RuntimeError(f"transport failed after {TRANSPORT_RETRIES + 1} tries: {url}") from last_exc


# ---------------- 深交所互动易 ----------------

def _ms_to_dt(v) -> datetime | None:
    if v in (None, "", 0):
        return None
    return datetime.fromtimestamp(int(v) / 1000.0, _BJ_TZ).replace(tzinfo=None)


def harvest_sz(client: httpx.Client, cutoff: datetime, fetched_at: datetime) -> list[dict]:
    """全局流按 updateDate 降序翻页, 收割 updateDate >= cutoff 的窗口."""
    cutoff_ms = int(cutoff.replace(tzinfo=_BJ_TZ).timestamp() * 1000)
    rows: list[dict] = []
    seen_ids: set[str] = set()
    malformed = 0
    page = 1
    while True:
        if page > MAX_PAGES_PER_SOURCE:
            raise RuntimeError(
                f"sz: 翻到 {MAX_PAGES_PER_SOURCE} 页仍未触及窗口下界 {cutoff} — "
                "调小 --days 或调大 MAX_PAGES_PER_SOURCE, 不静默截断")
        resp = _polite_request(client, "POST", SZ_SEARCH_URL, data={
            "pageNo": page, "pageSize": SZ_PAGE_SIZE, "searchTypes": "11"})
        payload = resp.json()  # 解析失败即 raise, 不吞
        results = payload.get("results")
        total_page = int(payload.get("totalPage") or 0)
        if page == 1 and (not results or int(payload.get("totalRecord") or 0) == 0):
            raise RuntimeError(f"sz: page1 空结果, 接口语义疑似变更: {str(payload)[:300]}")
        if not results:
            break  # 翻完
        page_update_ms: list[int] = []
        for r in results:
            es_id = r.get("esId")
            if es_id in seen_ids:
                continue  # 翻页期间流前移导致的重复
            seen_ids.add(es_id)
            code = r.get("stockCode")
            q = r.get("mainContent")
            if not code or not re.fullmatch(r"\d{6}", str(code)) or not q:
                malformed += 1
                continue
            upd = int(r.get("updateDate") or 0)
            page_update_ms.append(upd)
            q_time = _ms_to_dt(r.get("pubDate"))
            a_time = _ms_to_dt(r.get("attachedPubDate"))
            anchor = max(t for t in (q_time, a_time) if t is not None) if (q_time or a_time) else None
            if anchor is None or anchor < cutoff:
                continue  # 窗口外 (回答被后期编辑 updateDate 前移的旧行也在此过滤)
            rows.append({
                "code": str(code), "question": str(q).strip(),
                "answer": (str(r["attachedContent"]).strip()
                           if r.get("attachedContent") else None),
                "q_time": q_time, "a_time": a_time,
                "source": "sz", "fetched_at": fetched_at,
            })
        if malformed > 0 and malformed > 0.1 * len(seen_ids):
            raise RuntimeError(f"sz: 畸形行 {malformed}/{len(seen_ids)} > 10%, 字段结构疑似漂移")
        # 流为 updateDate 降序 (docstring 实测): 本页最新都早于 cutoff => 后页更旧, 停
        if page_update_ms and max(page_update_ms) < cutoff_ms:
            break
        if total_page and page >= total_page:
            break
        page += 1
    print(f"sz: pages={page} kept={len(rows)} malformed={malformed}", flush=True)
    return rows


# ---------------- 上证e互动 ----------------

_SH_CODE_RE = re.compile(r"[:：]?\s*(.+?)\((\d{6})\)\s*$")
_SH_TIME_PATTERNS = [  # e互动相对/绝对时间文本 (实测见 "45分钟前"/"13小时前"; 其余为常见展示格式兜底)
    (re.compile(r"^刚刚$|^(\d+)\s*秒前$"), "sec"),
    (re.compile(r"^(\d+)\s*分钟前$"), "min"),
    (re.compile(r"^(\d+)\s*小时前$"), "hour"),
    (re.compile(r"^(\d+)\s*天前$"), "day"),
    (re.compile(r"^今天\s*(\d{1,2}):(\d{2})$"), "today"),
    (re.compile(r"^昨天\s*(\d{1,2}):(\d{2})$"), "yesterday"),
    (re.compile(r"^(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})$"), "md"),
    (re.compile(r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$"), "md"),
    (re.compile(r"^(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?(?:\s+(\d{1,2}):(\d{2}))?$"), "full"),
]


def parse_sh_time(text: str, ref: datetime) -> datetime | None:
    s = (text or "").strip()
    for pat, kind in _SH_TIME_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        g = m.groups()
        # 相对时间本身只有分钟级精度, 截断到分钟避免把 fetched_at 的秒/微秒伪装成精度
        ref_min = ref.replace(second=0, microsecond=0)
        if kind == "sec":
            return ref_min
        if kind == "min":
            return ref_min - timedelta(minutes=int(g[0]))
        if kind == "hour":
            return ref_min - timedelta(hours=int(g[0]))
        if kind == "day":
            return ref_min - timedelta(days=int(g[0]))
        if kind == "today":
            return ref.replace(hour=int(g[0]), minute=int(g[1]), second=0, microsecond=0)
        if kind == "yesterday":
            return (ref - timedelta(days=1)).replace(
                hour=int(g[0]), minute=int(g[1]), second=0, microsecond=0)
        if kind == "md":
            cand = datetime(ref.year, int(g[0]), int(g[1]), int(g[2]), int(g[3]))
            return cand.replace(year=ref.year - 1) if cand > ref + timedelta(days=1) else cand
        if kind == "full":
            return datetime(int(g[0]), int(g[1]), int(g[2]),
                            int(g[3] or 0), int(g[4] or 0))
    return None


def _parse_sh_item(item, ref: datetime) -> dict | None:
    """单个 div.m_feed_item -> row dict; 结构不符返回 None (调用方统计)."""
    q_detail = a_detail = None
    for det in item.select("div.m_feed_detail"):
        if det.select_one(".ask_ico") is not None and q_detail is None:
            q_detail = det
        elif det.select_one(".answer_ico") is not None and a_detail is None:
            a_detail = det
    if q_detail is None:
        return None
    q_txt_div = q_detail.select_one(".m_feed_txt")
    if q_txt_div is None:
        return None
    anchor_a = q_txt_div.find("a")
    if anchor_a is None:
        return None
    m = _SH_CODE_RE.search(anchor_a.get_text(strip=True))
    if not m:
        return None
    code = m.group(2)
    anchor_text = anchor_a.get_text()
    full_text = q_txt_div.get_text()
    question = full_text.replace(anchor_text, "", 1).strip()
    if not question:
        return None

    def _time_of(detail) -> datetime | None:
        # 时间块 .m_feed_from 可能在 detail 内或紧随其后的 .m_feed_func 内, 取 item 级兜底
        frm = detail.select_one(".m_feed_from span") if detail else None
        return parse_sh_time(frm.get_text(strip=True), ref) if frm else None

    q_time = _time_of(q_detail)
    answer = a_time = None
    if a_detail is not None:
        a_txt_div = a_detail.select_one(".m_feed_txt")
        if a_txt_div is not None:
            answer = a_txt_div.get_text(strip=True) or None
        a_time = _time_of(a_detail)
    if q_time is None or (a_detail is not None and a_time is None):
        # item 级兜底: 答块的 .m_feed_from 实测可能挂在 detail 外的兄弟 .m_feed_func 里
        spans = [s.get_text(strip=True) for s in item.select(".m_feed_from span")]
        times = [t for t in (parse_sh_time(s, ref) for s in spans) if t is not None]
        if q_time is None and times:
            q_time = min(times)
        if a_detail is not None and a_time is None and len(times) >= 2:
            a_time = max(times)
    return {"code": code, "question": question, "answer": answer,
            "q_time": q_time, "a_time": a_time, "source": "sh", "fetched_at": ref}


def harvest_sh(client: httpx.Client, cutoff: datetime, fetched_at: datetime) -> list[dict]:
    rows: list[dict] = []
    seen_ids: set[str] = set()
    unparsed_items = 0
    page = 1
    while True:
        if page > MAX_PAGES_PER_SOURCE:
            raise RuntimeError(
                f"sh: 翻到 {MAX_PAGES_PER_SOURCE} 页仍未触及窗口下界 {cutoff} — "
                "调小 --days 或调大 MAX_PAGES_PER_SOURCE, 不静默截断")
        resp = _polite_request(
            client, "POST", SH_FEEDS_URL,
            data={"type": "11", "pageSize": str(SH_PAGE_SIZE), "page": str(page),
                  "lastid": "-1", "show": "1"},
            headers={"Referer": "http://sns.sseinfo.com/qa.do",
                     "X-Requested-With": "XMLHttpRequest"})
        soup = BeautifulSoup(resp.text, "lxml")
        items = [it for it in soup.select("div.m_feed_item") if it.get("id")]  # 滤掉 currentPage 占位块
        if page == 1 and not items:
            raise RuntimeError(f"sh: page1 无 feed item, 接口语义疑似变更: {resp.text[:300]}")
        if not items:
            break
        new_ids = {it.get("id") for it in items} - seen_ids
        if not new_ids:
            raise RuntimeError(f"sh: page={page} 翻页未前进 (无新 item id), 分页语义疑似变更")
        page_times: list[datetime] = []
        for it in items:
            iid = it.get("id")
            if iid in seen_ids:
                continue
            seen_ids.add(iid)
            row = _parse_sh_item(it, fetched_at)
            if row is None:
                unparsed_items += 1
                continue
            anchor = max(t for t in (row["q_time"], row["a_time"]) if t is not None) \
                if (row["q_time"] or row["a_time"]) else None
            if anchor is not None:
                page_times.append(anchor)
                if anchor < cutoff:
                    continue
            rows.append(row)  # 时间解析不出的 item 保守保留, 由 unparsed 计数监控
        if unparsed_items > 0 and unparsed_items > 0.2 * len(seen_ids):
            raise RuntimeError(f"sh: 解析失败 item {unparsed_items}/{len(seen_ids)} > 20%, HTML 结构疑似漂移")
        if page_times and not any(t >= cutoff for t in page_times):
            break  # 整页都早于窗口, 流降序 => 停
        if not page_times and len(seen_ids) > SH_PAGE_SIZE:
            raise RuntimeError("sh: 连续整页时间解析失败, 窗口停止条件失效 — 检查时间格式新形态")
        page += 1
    print(f"sh: pages={page} kept={len(rows)} unparsed={unparsed_items}", flush=True)
    return rows


# ---------------- 落盘 (按月 parquet, 幂等 merge) ----------------

def _month_key(row: pd.Series) -> str:
    for col in ("q_time", "a_time", "fetched_at"):
        v = row[col]
        if pd.notna(v):
            return f"{v.year:04d}{v.month:02d}"
    raise RuntimeError("row 无任何时间字段, 不应发生")  # fetched_at 恒非空, 防御


def write_month_files(df: pd.DataFrame, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    df = df.copy()
    df["_month"] = df.apply(_month_key, axis=1)
    # dedup key: sz 的 q_time 是稳定 epoch, 入 key; sh 的 q_time 由相对文本还原有分钟级
    # 抖动, 不入 key (同月同公司同问题文本视为同条, 保留最新 fetched_at 的 answer)
    def _key(r: pd.Series) -> str:
        t = r["q_time"].isoformat() if (r["source"] == "sz" and pd.notna(r["q_time"])) else ""
        return f'{r["source"]}|{r["code"]}|{t}|{r["question"]}'

    for month, part in df.groupby("_month"):
        path = out_dir / f"irm_qa_{month}.parquet"
        merged = part.drop(columns=["_month"])
        if path.exists():
            old = pd.read_parquet(path)
            merged = pd.concat([old, merged], ignore_index=True)
        merged["_k"] = merged.apply(_key, axis=1)
        merged = (merged.sort_values("fetched_at")
                  .drop_duplicates("_k", keep="last")
                  .drop(columns="_k")
                  .sort_values(["q_time", "code"], na_position="last")
                  .reset_index(drop=True))
        tmp = path.with_name(path.name + ".tmp")
        merged.to_parquet(tmp, index=False)
        os.replace(tmp, path)  # 原子覆盖 = 同月重抓幂等
        written[path.name] = len(merged)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--codes", default="",
                    help="逗号分隔 6 位股票代码列表; 留空 = 不过滤 (全市场窗口收割)")
    ap.add_argument("--days", type=int, default=3,
                    help="回看窗口天数 (按问/答时间较晚者落窗), 默认 3")
    ap.add_argument("--source", choices=["sz", "sh", "both"], default="both")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args()

    codes = {c.strip() for c in args.codes.split(",") if c.strip()}
    for c in codes:
        if not re.fullmatch(r"\d{6}", c):
            raise SystemExit(f"非法代码 {c!r}: 需 6 位数字")
    if args.days < 1:
        raise SystemExit("--days 必须 >= 1")

    fetched_at = now_bj()
    cutoff = fetched_at - timedelta(days=args.days)
    out_dir = Path(args.out_dir)
    print(f"window=[{cutoff} .. {fetched_at}] codes={sorted(codes) or 'ALL'} "
          f"source={args.source}", flush=True)

    all_written: dict[str, int] = {}
    with httpx.Client(timeout=TIMEOUT_SEC, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        for src, fn in (("sz", harvest_sz), ("sh", harvest_sh)):
            if args.source not in (src, "both"):
                continue
            rows = fn(client, cutoff, fetched_at)  # 源失败 raise, 不吞 — 已完成源的数据已落盘
            if codes:
                rows = [r for r in rows if r["code"] in codes]
                print(f"{src}: after code filter kept={len(rows)}", flush=True)
            if not rows:
                print(f"{src}: 窗口内 0 行 (代码过滤后), 不落盘", flush=True)
                continue
            df = pd.DataFrame(rows, columns=[
                "code", "question", "answer", "q_time", "a_time", "source", "fetched_at"])
            for col in ("q_time", "a_time", "fetched_at"):
                df[col] = pd.to_datetime(df[col])
            all_written.update(write_month_files(df, out_dir))

    if all_written:
        for name, n in sorted(all_written.items()):
            print(f"WROTE {out_dir / name}: {n} rows", flush=True)
    else:
        print("RESULT: 窗口内无匹配行, 未写文件 (源端点本身已验证非空, 属代码过滤/窗口选择结果)",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
