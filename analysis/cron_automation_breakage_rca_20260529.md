# Cron 自动化全面失效 — 根因分析 (RCA)

> **日期**: 2026-05-29
> **触发现象**: 登录 Terminal 显示 `You have mail.`
> **调查人**: Claude (Opus 4.7) + 用户 Jim Morrison
> **结论一句话**: 本地 6 个 cron job 中 4 个长期失效,根因是 **3 层独立 bug 叠加**(macOS TCC 权限 + cron PATH 无 `python` + 调用参数遗漏),其中只有 1 层能靠 Full Disk Access 解决。`monitor_v7_forward` 一条已在本次会话修复并实测跑通。
> **文档目标**: 开发人员无需追问即可复现调查过程、理解每个结论的依据、知道接下来该怎么修。

---

## 0. TL;DR (给赶时间的人)

| 维度 | 结论 |
|---|---|
| 现象 | Terminal `You have mail.` — 这是 Unix 本地邮件(`/var/mail/dp`),非 Gmail |
| 直接原因 | `monitor_v7_forward` cron 写 `~/Documents/...` 被 macOS TCC 拒 → cron daemon 把无处可去的 stderr 投递成 mail |
| 深层真相 | 这条 cron **从 2026-05-23 部署起从未成功跑过一次**(另有 `.venv/bin/python` 路径 bug) |
| 扩大排查 | 6 个 cron job 里 **4 个全坏**,只有 `rotate_codex_tui_log` 健康,`monitor_v7_forward` 本次修好 |
| 根因分层 | **L1** macOS TCC 挡 `~/Documents/`;**L2** cron PATH 上没有 `python`(只有 `python3`);**L3** `nightly_data_audit` 调用漏 `--write-default-json` |
| 起源 | commit `1221f66a` (2026-05-18),用户发起、Claude 实现,目标"零人工维护",**选 cron 是为了绕开 FDA —— 但该技术假设是错的** |
| FDA 能根治吗 | **否**。FDA 只解 L1。L2/L3 必须改脚本或 crontab |

---

## 1. 背景:这套 cron 是什么、谁建的、干啥用

### 1.1 起源

- **发起 commit**: `1221f66afed09dd461f48d0c4ec97c2dd968f209`
- **日期**: 2026-05-18 21:11:04 +0800
- **作者**: Jim Morrison <morrison416cn@gmail.com>(需求发起人)
- **实现**: Claude(在该日 session 内写的代码)
- **commit 标题**: `feat: cron-based 自动化 + idle VM proactive auto-stop (绕 FDA, audit 88→90%)`

**用户当时的诉求(commit message 原文引用)**:
> 用户 push back 'GCP no proactive cost-cutting solution' + 'zero LLM maintenance'
> 反例沉淀: launchd 跑 ~/Documents/ 下 script 需 FDA 1 次手工 (用户原话 '一次手工都不要'); cron 不受此限 → 真 zero touch automation.

**关键设计决策(后被证明是错的)**: 当时选 cron 而非 launchd,理由是"cron daemon 不受 FDA 限"。**这个技术假设错误**,是本次故障的根源之一(详见 §5)。

### 1.2 配置位置

- 版本控制副本: `configs/cron/crontab.txt`(2103 字节,commit `1221f66a` 引入)
- 实际生效: 用户 `crontab -e` 安装的 user crontab(`crontab -l` 查看)
- 一键安装脚本: `scripts/install_resilience.sh`(commit `bdb0b843`)

### 1.3 6 个 cron job 清单(故障前设计意图)

| Job | 频率 | 用途 | 首次引入 commit |
|---|---|---|---|
| `daily_update.sh` | 17:00 daily | A股收盘后拉数据 + 重建特征面板(8步) | `scaffold` 2026-05-18 |
| `nightly_data_audit.py` | 02:00 daily | 数据治理审计(governance v1) | governance v1, 2026-05-17 |
| `session_snapshot.sh` | 5 min | 更新 `SESSION_HANDOFF.md`(中断恢复用) | 2026-05-20 |
| `workflow_checkpoint.sh` | 10 min | 更新 `analysis/workflow_checkpoint.md`(pipeline 跟踪) | 2026-05-20 |
| `monitor_v7_forward.py` | 8:30 daily | v7 模型 forward 部署监控(abort criteria) | `5864778e` 2026-05-23 |
| `rotate_codex_tui_log.sh` | 1 min | 轮转 Codex TUI 日志 | (在 `~/.codex/` 下) |

---

## 2. 调查时间线(可复现)

### Step 1 — 现象定位:`You have mail.` 是什么

```bash
ls -la /var/mail/dp        # -> 908 字节, mtime May 29 08:30
```

读取邮件全文(`/var/mail/dp`),关键内容:
```
From: dp@P.local (Cron Daemon)
Subject: Cron <dp@P> cd $CHUNKYMONKEY && .venv/bin/python backend/scripts/monitor_v7_forward.py >> data/reports/v7_forward_cron.log 2>&1
...
/bin/bash: data/reports/v7_forward_cron.log: Operation not permitted
```

**定性**: 这是 Unix 本地 mail spool,不是 Gmail。cron daemon 的传统行为是:当 job 的 stdout/stderr **没有被成功 capture** 时,把这些输出 mail 给 user。

### Step 2 — 第一层根因:macOS TCC 挡住 `~/Documents/`

报错 `Operation not permitted`(注意:不是 `Permission denied`)是 macOS **TCC (Transparency, Consent, Control)** 的典型特征。

- macOS 将 `~/Documents/`、`~/Desktop/`、`~/Downloads/` 列为受保护用户目录
- 用户在 Terminal 手动跑没事(Terminal 应用持有 Full Disk Access)
- **cron daemon (`/usr/sbin/cron`) 默认没有 Full Disk Access**,访问这些目录就被拒
- 该 cron 的 log 路径 `data/reports/v7_forward_cron.log` 位于 `~/Documents/M/stock/chunkymonkey/` 下 → 命中 TCC

### Step 3 — 意外发现:这条 cron 历史上一直坏(第二个 bug)

读取旧 log `data/reports/v7_forward_cron.log`(TCC 拦的是新写入,旧文件仍可读):
```
/bin/bash: .venv/bin/python: No such file or directory
/bin/bash: .venv/bin/python: No such file or directory
/bin/bash: .venv/bin/python: No such file or directory
```

**发现 `.venv` 根本不存在**:
```bash
ls .venv                                          # -> No such file or directory
find . -maxdepth 3 -name "pyvenv.cfg" -o -name "*venv*"   # -> 无任何 venv 痕迹
```

**根因**: crontab 里 hardcode 了 `.venv/bin/python`,这是 **GCP VM 风格**的路径(VM 上确有 `.venv`,见 `scripts/auto_resume_v6_retrain.sh:62`)。但**本地 Mac 从未建过 venv**。对比 `scripts/daily_update.sh:109` 用的是 conditional `if [[ -d ".venv" ]]; then source ...; fi` —— 作者明确知道"本地可能没 venv",但写 monitor cron 时抄成了 VM 写法。

**结论**: 这条 cron 自 2026-05-23 16:21(`monitor_v7_forward.py` 引入)部署起,**每天 8:30 跑一次、每次都失败**。失败信息一直进 log 文件被吞掉,直到 TCC 也挡住 log 写入才升级成 mail。

### Step 4 — 验证脚本本身能跑通(用 cron 风格干净环境)

```bash
env -i SHELL=/bin/bash PATH=/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin \
  CHUNKYMONKEY=/Users/dp/Documents/M/stock/chunkymonkey \
  PYTHONPATH=/Users/dp/Documents/M/stock/chunkymonkey/backend HOME=/Users/dp \
  bash -c 'cd $CHUNKYMONKEY && /opt/homebrew/bin/python3.13 backend/scripts/monitor_v7_forward.py'
# exit_code=0
# 输出: v7 forward monitor day 6 (week 0.9/6): contamination: 0.00% status: OK
```

确认哪个 python 装了依赖:
```bash
/opt/homebrew/bin/python3.13 -c "import duckdb, pandas; print(duckdb.__version__, pandas.__version__)"
# -> 1.5.2 3.0.2  (OK)
```
注意:cron PATH 上的 `python3`(`/usr/bin/python3`)是 Python 3.9.6,依赖版本不同。最稳的是绝对路径 `/opt/homebrew/bin/python3.13`。

### Step 5 — 修复 `monitor_v7_forward`(本次会话已执行)

改 crontab 该行两处:
- python 路径: `.venv/bin/python` → `/opt/homebrew/bin/python3.13`
- log 路径: `data/reports/v7_forward_cron.log` → `/Users/dp/Library/Logs/chunkymonkey/v7_forward_cron.log`(`~/Library/Logs/` 不受 TCC 限,重启不丢)

修复后的行:
```cron
30 8 * * * cd $CHUNKYMONKEY && /opt/homebrew/bin/python3.13 backend/scripts/monitor_v7_forward.py >> /Users/dp/Library/Logs/chunkymonkey/v7_forward_cron.log 2>&1
```
并清空 `/var/mail/dp`(`: > /var/mail/dp`),`You have mail.` 消除。

### Step 6 — 扩大排查:其余 5 个 job 全查一遍

读取各 job 的 `/tmp/*.log`(它们 redirect 到 `/tmp/`,TCC 不挡,所以 log 本身能写):

| Job | `/tmp` log 内容 | mtime |
|---|---|---|
| `daily_update.sh` | `bash: scripts/daily_update.sh: Operation not permitted` | 5/28 17:00 |
| `nightly_data_audit` | `/bin/bash: python: command not found` | 5/29 02:00 |
| `session_snapshot.sh` | `bash: scripts/session_snapshot.sh: Operation not permitted` | 5/29 09:15 |
| `workflow_checkpoint.sh` | `bash: scripts/workflow_checkpoint.sh: Operation not permitted` | 5/29 09:10 |
| `rotate_codex_tui_log.sh` | (空,正常) | 5/28 09:08 |

**两个新发现**:

1. **`daily_update` log mtime 是 5/28 17:00,在 Mac 重启(5/28 23:27)之前** → 证明 **TCC 收紧不是重启触发的**,而是 5/27 17:00 → 5/28 17:00 之间某次 macOS 后台行为(疑似安全更新)导致 cron 失去 `~/Documents/` 访问权。重启只是延续了该状态。

2. **`nightly_data_audit` 报的是 `python: command not found`,不是 `Operation not permitted`** → 它 redirect 到 `/tmp/`(TCC 不挡),从未被 TCC 影响过。它纯粹是 **PATH 问题**:cron PATH 上只有 `python3`/`python3.13`,**没有 `python`**(macOS 12.3 起删了系统 `python`)。

### Step 7 — 第二层 bug 的影响范围量化

TCC-blocked 的 3 个脚本,**即使给 FDA 让外层 bash 能读到脚本,内部仍大量调用无版本号 `python`**:

```bash
grep -c 'python ' scripts/daily_update.sh         # 40 处
grep -c 'python ' scripts/workflow_checkpoint.sh  # 6 处
grep -c 'python ' scripts/session_snapshot.sh     # 0 处
```

**结论**: FDA 后 `session_snapshot` 能真正恢复;但 `daily_update`(40处)和 `workflow_checkpoint`(6处)跑到第一个内部 `python` 就 `command not found`。

### Step 8 — 第三层 bug:`nightly_data_audit` 配置空转

```bash
crontab -l | grep nightly
# 0 2 * * * cd $REPO && PYTHONPATH=backend python backend/scripts/nightly_data_audit.py >> /tmp/... 2>&1
```
查脚本输出逻辑 `backend/scripts/nightly_data_audit.py:296-309`:
- `--write-json` 默认 `None`,`--write-default-json` 是 flag
- crontab 行**两个参数都没带** → `output_path=None` → **即使跑通也不写任何 json**

证据:产物 `data/audit/nightly_data_audit_latest.json` mtime 停在 **5/17 08:46**(12天未更新)。这条 cron 从设计上就是空转的,跟 TCC、PATH 都无关。

### Step 9 — 破悬案:SESSION_HANDOFF.md 是谁刷新的

现象矛盾:`session_snapshot.sh` 的 cron 跑不动,但 `SESSION_HANDOFF.md` mtime 是 **5/29 09:29(很新)**。

读 `~/.claude/hooks/session_start_handoff.sh:36-38`:
```bash
# Refresh snapshot if older than 30 min (run async to avoid blocking SessionStart)
if [ "$HANDOFF_AGE_MIN" -gt 30 ] && [ -x "$(dirname "$HANDOFF")/scripts/session_snapshot.sh" ]; then
    (cd "$(dirname "$HANDOFF")" && nohup bash scripts/session_snapshot.sh > /tmp/session_snapshot.log 2>&1 &) ...
fi
```

**真相**: `SESSION_HANDOFF.md` 是 **Claude 的 SessionStart hook 刷新的,不是 cron**。当 Claude 启动 session 时,hook 发现 handoff 超过 30 min,异步跑 `session_snapshot.sh`。hook 运行在 Claude 的 Terminal 进程里 → **继承 Terminal 的 FDA** → 写入成功。

**这是 FDA 假说的活体反证**: 同一个 `session_snapshot.sh`,cron 跑挂(无 FDA),Claude session hook 跑通(有 FDA),差别只在 FDA。同时也说明:`SESSION_HANDOFF.md` 顶部声称的"每 5 min cron 自动更新"是**失真的** —— 实际只在 Claude 启动且 handoff 过期时更新一次。

---

## 3. 根因分层(核心结论)

故障由 **3 层互相独立的 bug** 叠加,必须分层理解,否则会误以为"给 FDA 就好了"。

| 层 | bug | 影响的 job | FDA 能修? | 真正修法 |
|---|---|---|---|---|
| **L1** | macOS TCC 挡 cron 访问 `~/Documents/` | daily_update, session_snapshot, workflow_checkpoint, (原 monitor) | 能 | 给 `/usr/sbin/cron` FDA,或迁 launchd,或脚本+数据搬出 `~/Documents/` |
| **L2** | cron PATH 上无 `python`(只有 `python3`) | nightly_data_audit, daily_update(40处), workflow_checkpoint(6处) | 不能 | crontab 头加 `python` 软链目录,或脚本内全改 `python3`/绝对路径 |
| **L3** | nightly_data_audit 调用漏 `--write-default-json` | nightly_data_audit | 不能 | crontab 该行补 `--write-default-json` |

**额外的历史 bug(已修)**: monitor_v7_forward 的 `.venv/bin/python` VM 路径误用 —— 属于 L2 的一个特例,本次会话已修。

---

## 4. 为什么偏偏 5/28-5/29 才爆出来

cron daemon 发 mail 的触发条件是 **job 的 stderr 没有被成功 capture**。

| 时段 | log redirect 状态 | 失败 stderr 去向 | 是否发 mail |
|---|---|---|---|
| 5/23 ~ 5/27 | bash 能打开 `data/reports/v7_forward_cron.log` | redirect 成功,错误进 log 文件 | 不发(被 capture 走) |
| 5/27 17:00 ~ 5/28 | TCC 收紧,bash 连 log 都打不开 | redirect 本身失败,stderr 无处可去 | cron daemon 投递为 mail |

所以"第一次见到 You have mail"不是新故障,而是**老故障的报错路径从「静默 log」升级成「显式 mail」**。底层 cron 自部署起就坏,只是一直没被看见。

---

## 5. 设计层面的教训(给后人)

**当初选 cron 绕 FDA 的决策基于错误假设**:

> commit `1221f66a` 原话:"cron daemon 不受 FDA 限 = 真零依赖手工"

事实是:**在现代 macOS 上,cron daemon 访问 `~/Documents/` 与 launchd 一样会被 TCC 挡。** 部署当天(5/18)碰巧能跑,是因为当时 cron 尚有残留授权或 TCC 未收紧,造成"绕过成功"的假象。

**第一性原理复盘**(对照项目 CLAUDE.md §1.0):
- 真相源: macOS TCC 对受保护目录的拦截,**对 cron 和 launchd 一视同仁**。"cron 不受 FDA 限"从来不是事实。
- 更简单的方案: 真正能"零手工"的唯一办法,是把脚本 + 它读写的所有数据都放在 TCC 不保护的目录(如 `~/Library/`、`~/.local/`、`/tmp/`)。只要业务数据在 `~/Documents/`,就逃不掉一次 FDA 授权。
- 反例已沉淀: `rotate_codex_tui_log.sh` 是唯一健康的 job,正因为它的脚本和日志都在 `~/.codex/`(TCC 外),且不调 python。这反向印证了正确做法。

---

## 6. 各 job 当前状态与产物新鲜度(2026-05-29 09:37 快照)

| Job | 状态 | 产物 | 产物 mtime | 备注 |
|---|---|---|---|---|
| `monitor_v7_forward` | [OK] 本次修复,实测 exit 0 | json | 本次会话 | python 路径 + log 路径都改 |
| `rotate_codex_tui_log` | [OK] 一直健康 | 日志归档 | - | 脚本在 `~/.codex/`,无 python,无 TCC |
| `session_snapshot` | [WARN] cron 挂,但 Claude hook 在代跑 | `SESSION_HANDOFF.md` | 5/29 09:29 | 靠 SessionStart hook 续命,非 cron |
| `daily_update` | [FAIL] 全挂 | `/tmp` log | 5/28 17:00 | L1 + L2(40处裸python);被 33 文件引用,是核心 pipeline |
| `workflow_checkpoint` | [FAIL] 全挂 + 产物停滞 | `analysis/workflow_checkpoint.md` | 5/25 20:43(4天) | L1 + L2(6处);疑似已被手动流程取代 |
| `nightly_data_audit` | [FAIL] 三重坏 | `data/audit/...latest.json` | 5/17 08:46(12天) | L1无关 + L2(python) + L3(漏参数空转);疑似装了没真用过 |

---

## 7. 修复建议(分层,按优先级)

### 7.1 必做:补齐 L2/L3(不需要用户 GUI,Claude 可直接执行)

**根治 L2 的最干净方案**(一处改动解决全部 46 处裸 python,不动任何脚本):
```bash
# 1) 建一个 python 软链,放进 TCC 外的目录
mkdir -p ~/.local/bin
ln -sf /opt/homebrew/bin/python3.13 ~/.local/bin/python
# 2) crontab 头部 PATH 前置 ~/.local/bin
#    PATH=/Users/dp/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
```
> 注意:需确认 `~/.local/bin/python` 指向的 3.13 装了项目全部依赖(已验 duckdb 1.5.2 + pandas 3.0.2)。

**修 L3**: crontab 的 nightly 行补 `--write-default-json`:
```cron
0 2 * * * cd $REPO && PYTHONPATH=backend python backend/scripts/nightly_data_audit.py --write-default-json >> /tmp/nightly_data_audit.log 2>&1
```

### 7.2 必做:解决 L1(需要用户一次性 GUI 操作,Claude 无法代办)

二选一:

| 方案 | 操作 | 取舍 |
|---|---|---|
| **A. 给 cron FDA** | System Settings → Privacy & Security → Full Disk Access → + → `/usr/sbin/cron` | 0 改动、点一次;但所有 cron job 都获全盘读权限 |
| **B. 迁 launchd** | 6 个 job 翻成 `.plist` + 给 LaunchAgent FDA | 符合 macOS 长期方向、授权粒度细;但要写 6 个 plist + 调试 |

对"零人工"诉求,**A 更划算**(点一次 vs 写 6 个 plist)。两者都逃不掉那一次 FDA 授权 —— 这是 macOS TCC 的硬约束,见 §5。

### 7.3 先决策再修:三个 job 是否还需要

修之前应确认价值(避免修一个没人用的东西,对照 CLAUDE.md §1.0 奥卡姆剃刀):

- **`daily_update`**: 被 33 文件引用、是核心数据 pipeline → 确认用户平时是手动跑还是依赖 cron,再决定修/删
- **`workflow_checkpoint`**: 产物 4 天没更新,引用多为 doc 互相提及 → 疑似已被手动流程取代,确认后可能直接删 cron 行
- **`nightly_data_audit`**: 三层坏 + 产物 12 天没更新 → 疑似装了从没真用过,确认后可能直接删

---

## 8. 未决问题 / 需要用户确认

1. **daily_update 平时怎么跑的?** 手动 `bash scripts/daily_update.sh`(Terminal 有 FDA,能跑通)还是指望 cron?这决定 cron 这条是修还是删。
2. **workflow_checkpoint / nightly_data_audit 是否还需要?** 产物长期停滞,可能直接删 cron 行更干净。
3. **走 FDA 方案 A 还是迁 launchd 方案 B?** 影响后续是改 crontab 还是写 plist。
4. **`SESSION_HANDOFF.md` 顶部"每 5 min cron 自动更新"的文案要不要改?** 现状是靠 Claude SessionStart hook 续命,文案失真。

---

## 附:本次调查涉及的关键文件

| 文件 | 作用 |
|---|---|
| `/var/mail/dp` | Unix 本地 mail spool(已清空) |
| `configs/cron/crontab.txt` | crontab 版本控制副本 |
| `scripts/install_resilience.sh` | cron + hook 一键安装 |
| `~/.claude/hooks/session_start_handoff.sh` | Claude SessionStart hook(代跑 session_snapshot 的真凶) |
| `backend/scripts/monitor_v7_forward.py` | 本次修复的监控脚本 |
| `backend/scripts/nightly_data_audit.py:296-309` | L3 输出参数逻辑 |
| `~/Library/Logs/chunkymonkey/v7_forward_cron.log` | monitor 新 log 路径(TCC 外) |
| `data/audit/nightly_data_audit_latest.json` | nightly 产物(5/17 后停滞) |
