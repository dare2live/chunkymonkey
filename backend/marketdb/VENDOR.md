# marketdb — vendored subset

来源: https://github.com/HiThink-Tech/Financial-API.git — `python/marketdb/`
并入日期: 2026-09-08
并入时上游 commit: `2ec779c94e798ea941befb2d1c951c9420fdadd0`

## 为什么在仓内而不是 sibling 目录

原来它是 `/Users/dp/Documents/M/stock/fuyao` 这个浅克隆，靠
`sibling_repos.ensure_import_path()` 插进 `sys.path` 才能 import。那个布局有三个实测问题:

1. **没钉版本**: `required_marker` 只查文件是否存在, `git pull` 会静默换掉行为。
   该克隆是 `--depth 1`(1 个 commit, 2026-08-27), 连 fetch 都没做过。
2. **不受门管**: 22 道提交门的 object 作用域是 `backend/**`, sibling 在作用域外。
3. **不在 CI 可达范围**: `.github/workflows/ci.yml` 不 clone sibling,
   `backend/tests` 里 0 个文件 import marketdb —— 这段代码从未被 CI 执行过。

## 并入范围 = 真实导入闭包, 不是整仓

实测(真跑一次 import, 取 `sys.modules` 差集)是 **6 个模块 / 521 行**:

    marketdb/__init__.py              18
    marketdb/_version.py               2
    marketdb/credentials.py           94
    marketdb/providers/__init__.py     3
    marketdb/providers/dump.py       249
    marketdb/providers/rest.py       155

只按 chunkymonkey 的 3 行 import 静态推会得到"2 文件 343 行"——漏掉 `__init__.py` 链
(`providers/__init__` 拉 rest, `marketdb/__init__` 拉 _version)。静态闭包不等于导入闭包,
这里用的是后者。

上游其余部分(importers / checks / calculations / updaters / sdk 等)**没有并入**,
chunkymonkey 一行都没用到。要用新功能就回上游取, 别在这里凭空加。

## 许可

上游根目录 `LICENSE` 是 MIT (Copyright (c) 2026 HiThink-Tech), 已随文件一起拷入本目录。
**但上游 `python/pyproject.toml` 写的是 `license = { text = "Proprietary" }`** ——
同一仓库两处声明互相矛盾, 而我们并入的正是 `python/` 下的代码。
这个矛盾已于并入当日报给业主, 由业主处置; 本文件只如实记录, 不做法律判断。

## 消费方

`backend/services/data_sources/sources/fuyao.py` (3 处 import)。

## 一条从原 sibling 登记里继承下来的约束

原 `sibling_repos.yaml` 的 fuyao 条目带着一句 note: **Do not vendor their marketdb
DuckDB into chunkymonkey**。它说的是上游那个 **DuckDB 数据库文件**, 不是这些 Python 代码 ——
并入代码不解除这条约束: 数据仍然只能经 land -> accept 进本仓的库, 不许把别人的库文件
直接搬进来当真相源(红线 4 依赖只向下)。删登记条目时把这句一起搬到这里, 免得它随条目消失。

## 并入后的自查（重新同步上游时也跑一遍）

```
grep -rnE '(^|[^/A-Za-z])(docs|tests|scripts|stress)/[A-Za-z0-9_./-]+\.(py|md|json)' \
  backend/marketdb | grep -v 'backend/' | grep -v 上游
```

输出必须为空：每条路径引用要么带 `backend/` 前缀且文件真存在，要么在同一行明标
`上游@<SHA>`。并入只带 package 不带 `tests/` `docs/` `scripts/`，所以上游指向那些目录的
docstring 到了本仓就是悬空的 —— 2026-09-08 三包并入时实测中了 4 处（其中 2 处在上游本来
就悬空：它们指的 `docs/eastmoney-aif10-spec.md` 从未存在过）。

这是手跑的规则不是门 —— 门分不清「历史提及」与「路径声明」，硬做会假阳性（红线 13）。
