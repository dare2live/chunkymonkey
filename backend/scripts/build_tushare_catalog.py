#!/usr/bin/env python3
"""TuShare 接口目录生成器 — 官网文档镜像 -> 结构化 catalog (模块化数据源管理).

定位 (2026-06-11, 用户决策 tushare 长期主源):
  backend/config/tushare_api_catalog.json = sync_registry 的上游字典 —
  接口名/中文名/积分/限频/字段/数据起始/更新时间 的机器可读真相源。
  sync_registry 新增条目前先查 catalog; probe 实测结果以 `probed_*` 字段增量回写。

源: ~/Documents/M/stock/tushare/tushare.pro/document/ (SiteSucker 镜像, 文件名含全角问号)
重跑: PYTHONPATH=backend python backend/scripts/build_tushare_catalog.py
      (镜像更新后重跑; probed_* 字段从旧 catalog 继承不丢)
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

MIRROR = Path.home() / "Documents" / "M" / "stock" / "tushare" / "tushare.pro" / "document"
OUT = Path(__file__).resolve().parents[1] / "config" / "tushare_api_catalog.json"


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("tr", "p", "br", "h1", "h2", "h3", "li"):
            self.parts.append("\n")
        if tag == "td":
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _text_of(path: Path) -> str:
    p = _Text()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    t = "".join(p.parts)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", t))


def parse_doc(path: Path) -> dict | None:
    t = _text_of(path)
    m = re.search(r"接口[:：]\s*([a-z][a-z0-9_]+)", t)
    if not m:
        return None
    api = m.group(1)
    doc_id = re.search(r"doc_id=(\d+)", path.name)
    cn = re.search(r"\n([^\n|]{2,30}?)\s*\n+\s*接口[:：]", t)
    desc = re.search(r"描述[:：]\s*([^\n]{5,200})", t)
    points = re.search(r"(?:权限|积分要求)[:：]\s*([^\n]{2,160})", t)
    points_num = re.search(r"([0-9][0-9,]{2,6})\s*积分", t)
    limit = re.search(r"限量[:：]\s*([^\n]{2,160})", t)
    update = re.search(r"更新[时间频率][:：]?\s*([^\n]{2,80})|数据更新[:：]?\s*([^\n]{2,80})", t)
    start = re.search(r"数据(?:历史|开始)[于:：from]*\s*([0-9]{4}[年0-9.-]{0,8})|历史[:：]\s*([0-9]{4}年[^\n]{0,20})", t)

    # 输入/输出参数表 (粗提取: 表格行 "| name | type | ... | 描述")
    fields_out: list[list[str]] = []
    out_sec = t.split("输出参数")[-1] if "输出参数" in t else ""
    for row in re.finditer(r"\n\s*\|\s*([a-z][a-z0-9_]+)\s*\|[^|\n]*\|?[^|\n]*\|\s*([^|\n]{1,60})", out_sec):
        fields_out.append([row.group(1), row.group(2).strip()])
        if len(fields_out) >= 60:
            break

    def _g(m_, *idx):
        if not m_:
            return None
        for i in idx:
            if m_.group(i):
                return m_.group(i).strip()
        return None

    return {
        "api": api,
        "doc_id": doc_id.group(1) if doc_id else None,
        "cn_name": _g(cn, 1),
        "desc": _g(desc, 1),
        "points": _g(points, 1),
        "points_num": int(points_num.group(1).replace(",", "")) if points_num else None,
        "rate_limit": _g(limit, 1),
        "update_time": _g(update, 1, 2),
        "data_start": _g(start, 1, 2),
        "output_fields": fields_out,
    }


def main() -> int:
    if not MIRROR.exists():
        print(f"镜像目录不存在: {MIRROR}", file=sys.stderr)
        return 1
    old: dict[str, dict] = {}
    if OUT.exists():
        old = {e["api"]: e for e in json.loads(OUT.read_text())["apis"]}

    catalog: dict[str, dict] = {}
    for f in sorted(MIRROR.glob("*.html")):
        try:
            e = parse_doc(f)
        except Exception:  # noqa: BLE001 — 单页解析失败不挡全局
            continue
        if not e:
            continue
        # 同接口多页取字段更全的
        if e["api"] not in catalog or len(e["output_fields"]) > len(catalog[e["api"]]["output_fields"]):
            catalog[e["api"]] = e

    # 继承 probe 实测字段 (probed_status/probed_date/probed_rows 等增量回写不丢)
    for api, e in catalog.items():
        for k, v in (old.get(api) or {}).items():
            if k.startswith("probed_"):
                e[k] = v

    OUT.write_text(json.dumps(
        {"generated_from": str(MIRROR), "api_count": len(catalog),
         "apis": sorted(catalog.values(), key=lambda x: x["api"])},
        ensure_ascii=False, indent=1))
    print(f"catalog: {len(catalog)} 接口 -> {OUT}")
    # 人读目录摘要
    by_points: dict[str, int] = {}
    for e in catalog.values():
        key = (e.get("points") or "unknown")[:18]
        by_points[key] = by_points.get(key, 0) + 1
    for k, v in sorted(by_points.items(), key=lambda x: -x[1])[:10]:
        print(f"  {v:>4}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
