# 资金流接入专题讨论

**起始**: 2026-04-26
**作者**: Claude / Codex / 用户
**目标**: 把主力资金流历史数据接入 `raw_fund_flow_daily`, 至少 250 个交易日, 让 base_43 主轨能加 `fund_flow_5d/20d` 横截面 rank 维度

---

## 1. 当前事实底稿

### 1.1 库内现状 (实测 2026-04-26 15:50)

```sql
SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date)
FROM raw_fund_flow_daily;
-- rows=5496 / days=1 / range 2026-04-24 ~ 2026-04-24
-- source=eastmoney_push2delay_latest
```

只有 1 天 (2026-04-24) × 5496 票, 全部来自 push2delay fallback. 完全不够算 5d/20d rank.

### 1.2 网络解析 (用户机器, 2026-04-26 15:50)

```
push2his.eastmoney.com    → 198.18.1.51    HTTPS RemoteDisconnected
push2.eastmoney.com       → 198.18.2.234   未测
push2delay.eastmoney.com  → 198.18.4.78    HTTPS 200 OK (但接口只返回 1 行)
hq.sinajs.cn              → 198.18.4.76    未测
```

198.18.x.x 是 Surge 的 fake-ip 网段 (RFC 6815 测试段). 不是真实 IP, 由 Surge 接管 OS 路由后内部 NAT 到真实节点.

### 1.3 用户代理软件

**Surge** (不是 ClashX). Surge 4+ 版本支持 fake-ip + 规则集路由.

---

## 2. 用户的核心疑问: "akshare 咋就拿不到资金流呢?"

### 2.1 关键事实 — akshare 不是数据源

akshare 是 **Python HTTP wrapper**, 不是独立数据源. `ak.stock_individual_fund_flow(stock='600519', market='sh')` 内部就是 `requests.get('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get', params={...})`.

所以:

```
"akshare 拿不到资金流"
   ↕ 等价
"push2his.eastmoney.com 这个域名在你机器上连不通"
   ↕ 等价
"Surge 把 push2his.eastmoney.com 路由到了一个不可用的策略/节点"
```

把 akshare 当作"独立数据源"是常见误解 — 它只是把多个网页接口封装成 Python 函数, 数据本质还是 eastmoney/sina/tushare 的 HTTP 端点.

### 2.2 实测验证 (用 trust_env=False 绕系统代理 env, 但走系统路由)

| 域名 | 状态 | 行数 | 说明 |
|---|---|---|---|
| `push2delay.eastmoney.com` | 200 OK | 1 行 | 接口设计上只给最新交易日 |
| `push2his.eastmoney.com` | RemoteDisconnected | — | Surge 把它路由到了挂掉的代理或 Reject |

`push2delay` 通 = Surge fake-ip → 真实 IP 转换机制正常.
`push2his` 不通 = 这个具体域名匹配到了不同的策略, 走了挂掉的节点.

### 2.3 为什么 trust_env=False 不能解决?

`requests.Session(trust_env=False)` 只让 requests 不读 `HTTP_PROXY/HTTPS_PROXY` 环境变量, **不影响 OS 层路由**. Surge 在系统层接管所有 TCP 流量, fake-ip 是 OS-level NAT, 跟 Python 进程是否走代理 env 无关.

唯一解决路径: **改 Surge 的规则集**, 让 `push2his.eastmoney.com` 走真实 DNS + DIRECT.

---

## 3. Surge 诊断与修复方案

### 3.1 Step 1: 看 Surge 实时活动确定路由路径

```
Surge 控制台 → 实时活动 → 搜索 "push2his"
或
Surge 仪表板 → 网络活动 → 过滤 eastmoney
```

期望看到的关键字段:
- **规则**: 命中了哪条 (DOMAIN-SUFFIX / GEOIP / FINAL / RULE-SET)
- **策略**: 走的策略组 (DIRECT / Proxy_HK / Reject)
- **节点**: 实际代理节点
- **状态**: 连接是否成功, 失败原因

如果 `push2his` 被路由到一个挂掉的代理节点 → 改成 DIRECT
如果被 REJECT/REJECT-DROP 拦截 → 移除拦截
如果命中 GEOIP CN/Final → 加显式直连规则

### 3.2 Step 2: 查 Surge 配置文件

Surge 配置通常在:

```
~/Library/Application Support/Surge/Profiles/<profile>.conf
```

或 GUI 里"配置文件 → 编辑当前配置". grep `eastmoney` 看现有规则:

```bash
grep -i "eastmoney\|sinajs" ~/Library/Application\ Support/Surge/Profiles/*.conf
```

大概率没有显式规则, 而是被一条泛规则 (Final / GEOIP CN / 某个 RULE-SET) 路由错了.

### 3.3 Step 3: 加显式直连规则

在 Surge config 的 `[Rule]` 段顶部加 (顶部优先级最高):

```
# 资金流数据源, 强制直连
DOMAIN-SUFFIX,eastmoney.com,DIRECT
DOMAIN-SUFFIX,sinajs.cn,DIRECT
```

如果使用 fake-ip 模式, `[General]` 段加白名单防止 fake-ip 接管:

```
[General]
fake-ip-filter = *.eastmoney.com, *.sinajs.cn, ...其他原有...
```

如果想让这两个域名直接走系统 DNS (不参与 Surge DNS 解析), `[Host]` 段:

```
*.eastmoney.com = system
*.sinajs.cn = system
```

任选一种或组合使用. 最稳妥是三个都加.

### 3.4 Step 4: 重启 Surge 后验证

```bash
# 1. DNS 应解析到真实 IP, 不是 198.18.x.x
python3 -c "import socket; print(socket.gethostbyname('push2his.eastmoney.com'))"
# 期望: 真实公网 IP (eastmoney 的 CDN 一般在 60.205.x.x / 39.106.x.x / 122.51.x.x 等)

# 2. push2his 应返回历史数据
python3 <<'PY'
import requests
s = requests.Session(); s.trust_env=False
r = s.get('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get',
         params={'secid':'1.600519','klt':'101','fqt':'1','lmt':'10',
                 'fields1':'f1,f2,f3,f7',
                 'fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65'},
         headers={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/zjlx/detail.html'},
         timeout=15)
print('status:', r.status_code)
print('行数:', len(r.json().get('data', {}).get('klines', [])))
print('首行:', r.json().get('data', {}).get('klines', [''])[0][:80])
PY
# 期望: status=200, 行数=10, 首行包含日期+净流入字段

# 3. akshare 端到端测试
python3 -c "
import akshare as ak
df = ak.stock_individual_fund_flow(stock='600519', market='sh')
print(df.shape)
print(df.head(3))
print('日期范围:', df['日期'].min(), '~', df['日期'].max())
"
# 期望: shape ~ (200-250, 14), 日期范围覆盖 ~250 个交易日
```

---

## 4. 修复成功后的落地

### 4.1 全量回填

```bash
cd /Users/dp/Documents/M/stock/backend
# auto 模式: 优先 push2his 历史接口, 失败 fallback 到 push2delay 当日
python3 -m scripts.fetch_fund_flow_daily --source auto --rate-limit 0.3
```

预估: 5507 票 × 0.3s sleep + retry ≈ **30-50 分钟**, 落库 ~138 万行.

### 4.2 数据验证 SQL

```sql
-- 覆盖天数
SELECT COUNT(DISTINCT trade_date) AS days,
       MIN(trade_date) AS start, MAX(trade_date) AS end
FROM raw_fund_flow_daily
WHERE source LIKE '%push2his%' OR source LIKE 'akshare%';
-- 期望: days >= 200, start <= 2025-06

-- 票覆盖
SELECT COUNT(DISTINCT stock_code), COUNT(*) AS rows
FROM raw_fund_flow_daily;
-- 期望: stocks >= 5400, rows >= 100 万

-- 字段完整性 spot check
SELECT
    SUM(CASE WHEN main_net_amount IS NULL THEN 1 ELSE 0 END) AS null_main,
    SUM(CASE WHEN super_large_net_pct IS NULL THEN 1 ELSE 0 END) AS null_super,
    SUM(CASE WHEN small_net_pct IS NULL THEN 1 ELSE 0 END) AS null_small
FROM raw_fund_flow_daily;
-- 期望: 全部 < rows 的 1%
```

### 4.3 入模时机 (Codex §4.21 决策保留)

拿到 250 天数据后**仍不立即入模**, 先做:

1. **Coverage profile**: 每个交易日票覆盖率, 期望 > 95%
2. **Quality profile**: main_net_amount 分布、异常值检测、与日 K 涨跌幅相关性
3. **横截面特征实验**: `fund_flow_5d_rank` / `fund_flow_20d_rank` 加进 base_43 训练, 看 RankIC 是否提升 > 0.005
4. **不通过红线就不入模**, 维持现状

---

## 5. 备选方案 (路线 A 失败时启动)

### 5.1 路线 B: Tushare Pro moneyflow

- 历史完整 (10+ 年), ¥200/年 token
- 工程量: 1-2 天写 `services/tushare_fund_flow_client.py`
- 优势: 完全脱离代理网络, 接口稳定
- 劣势: 字段口径需对齐 (Tushare 主力定义 vs Eastmoney 可能差异)
- 启动条件: Surge 改完仍卡 push2his (例如东财服务端封 IP), 或者用户希望覆盖 2023 年以前历史

### 5.2 路线 D: 接受现状

- 主轨 `base_43` +22pp/fold 不依赖资金流
- M8.9 daily 自动累积每天 +1 行/票
- 60 个交易日后 (~2026-07) 重新评估
- 优势: 0 工程, 0 成本
- 劣势: 缺横截面分钟级资金流信号, alpha 上限受限

---

## 6. 待用户操作清单

- [ ] **6.1** 打开 Surge 控制台 → 实时活动, 触发一次 push2his 请求 (跑下面命令), 把命中的规则 + 策略 + 节点 + 状态截图或粘贴到这里
  ```bash
  curl -v --max-time 5 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid=1.600519&klt=101&fqt=1&lmt=10' 2>&1 | head -30
  ```

- [ ] **6.2** 检查 Surge config, 看是否已有 eastmoney 相关规则
  ```bash
  grep -in "eastmoney\|sinajs\|GEOIP\|fake-ip" ~/Library/Application\ Support/Surge/Profiles/*.conf 2>/dev/null
  ```

- [ ] **6.3** 加规则 (3.3 节), 重启 Surge

- [ ] **6.4** 跑 3.4 节验证命令, 把输出贴回来

- [ ] **6.5** 如验证通过, 我跑 4.1 全量回填 + 4.2 数据验证

- [ ] **6.6** 如 push2his 仍不通, 决定路线 B (Tushare Pro 付费 ¥200) 还是路线 D (接受现状)

---

## 7. 讨论与决策记录

### 7.1 (2026-04-26 Claude 提议)

我的建议是先做 6.1 / 6.2 — 拿到 Surge 的实际路由路径, 再决定是改规则还是换源. 大概率是 Surge 配置里有一条 RULE-SET 把 eastmoney 误归到代理组, 加显式 DIRECT 规则 5 分钟解决.

**关键风险点**: 即使修通 push2his, eastmoney 接口本身设计上限 ~250 个交易日 (大约 1 年). 想覆盖更长历史只能走路线 B (Tushare Pro). 但 250 天足够做横截面 rank 实验, 也足够算 fund_flow_5d/20d.

**我不建议**:
- 路线 C (新浪爬虫): 反爬不稳定, 维护负担重
- 路线 E (自拼伪资金流): 缺 tick / 分钟数据, 数据本质缺失, 不可行

等用户操作 6.1 / 6.2 后继续.

### 7.2 (2026-04-26 Codex 复核)

我按最新文档重新实测后, 需要修正一个关键判断: **当前阻塞不只是缺少 eastmoney DIRECT 规则**。本机 Surge 配置里已经有:

```text
DOMAIN-SUFFIX,eastmoney.com,DIRECT,extended-matching
```

但没有把 `*.eastmoney.com` 加进 `always-real-ip`, 也没有 `[Host]` 强制系统 DNS. 系统解析仍然给 `push2his.eastmoney.com -> 198.18.1.51`, 公网 DNS 可解析到真实 IP (`61.129.129.199` / `101.226.30.221`)。

实测结果:

| 项目 | 结果 | 结论 |
|---|---|---|
| 库内 `raw_fund_flow_daily` | 5496 行 / 1 天 / 2026-04-24 | 文档底稿正确, 不能算 5d/20d |
| `push2delay` | 200 OK, 只返回 1 行 | fallback 正常, 但只能日增 |
| `push2his` 普通 DNS | TLS 握手成功后 empty reply | 不是简单 DNS 解析失败 |
| `push2his --resolve` 真实 IP | 仍然 empty reply | 仅加真实 IP 不一定解决 |
| akshare 1.18.57 | 仍调用 `push2his` 普通 requests | akshare 本身无独立数据源 |
| `curl_cffi` Chrome/Safari 指纹 | 仍连接被关闭 | 不像是普通 requests 指纹问题 |

因此路线 A 仍可做一个最小验证, 但期望值要调低: 把 `*.eastmoney.com` / `*.sinajs.cn` 加进 `always-real-ip` 和 `[Host]`, 重启 Surge 后再跑 3.4 验证。如果 `push2his` 仍 empty reply, 就不要继续在代理规则上耗时间。

我的建议:

1. **先做 5 分钟网络验证**: 加 `always-real-ip` / `[Host]`, 重启 Surge, 再测 `push2his`。这是最低成本。
2. **验证失败就切路线 B**: Tushare Pro moneyflow 是最短确定路径, 比继续找新浪/网页爬虫更符合奥卡姆剃刀。
3. **保留 push2delay daily fallback**: 它能每日积累, 但在 60 个交易日前不入 `base_43`, 只作为审计数据。
4. **不要为了资金流扩系统复杂度**: 资金流只有在 coverage >= 95%, RankIC 增益稳定超过 0.005 后才进主轨；否则保持现有 base_43。

阶段性决策: 当前最可能的落地路径是 **路线 A 最小复验 -> 失败后 Tushare Pro**。不要把 akshare 当成替代源, 它只是 `push2his` wrapper。

### 7.3 (2026-04-26 Codex 追加: 为什么 akshare 其他数据可用, 资金流不可用)

用户指出“akshare 获取其他数据就行, 资金流就不行”。复核后结论是: **不是 akshare 整体可用而资金流单独坏, 而是 akshare 背后的数据源分成两类**。

第一类是 `datacenter-web.eastmoney.com/api/data/v1/get`, 当前机器可用。项目里已经成功的机构调研、龙虎榜、QFII/股东分析等 akshare 接口都走这条线:

| akshare 函数 | 底层域名 | 实测 |
|---|---|---|
| `stock_jgdy_tj_em` | `datacenter-web.eastmoney.com` | OK, 8782 行 |
| `stock_lhb_detail_em` | `datacenter-web.eastmoney.com` | OK, 85 行 |
| `stock_gdfx_holding_detail_em` | `datacenter-web.eastmoney.com` | OK, 1147 行 |

第二类是行情/历史行情线 `push2*.eastmoney.com` / `push2his.eastmoney.com`, 当前机器上 GET API 会在 HTTP 层被关闭。资金流属于这条线:

| akshare 函数 | 底层域名 | 实测 |
|---|---|---|
| `stock_zh_a_spot_em` | `82.push2.eastmoney.com/api/qt/clist/get` | RemoteDisconnected |
| `stock_board_industry_name_em` | `17.push2.eastmoney.com/api/qt/clist/get` | RemoteDisconnected |
| `stock_zh_a_hist` | `push2his.eastmoney.com/api/qt/stock/kline/get` | RemoteDisconnected |
| `stock_individual_fund_flow` | `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` | RemoteDisconnected |

所以“其他数据可用”的真实原因是: 那些成功接口多数并没有走 `push2his`。项目日 K 之所以还能跑, 也不是因为 `ak.stock_zh_a_hist` 当前可用, 而是系统优先走 mootdx/通达信, 再用 sina/tx fallback；行业分类也已经改成通达信 `tdxhy.cfg`。

补充观察:

- `requests` 默认会读取 macOS 系统代理配置, `urllib.getproxies()` 当前能看到 `127.0.0.1:6152`; 项目自写客户端里有 `trust_env=False` 或清理代理环境, 但原生 akshare 函数没有这个控制。
- 即使绕过环境代理并直接用真实 IP 测 `push2his`, GET API 仍 empty reply；因此代理环境变量不是唯一解释。
- `push2delay.eastmoney.com` 可用, 但接口结构只给最新 1 天, 所以只能做 daily fallback, 不能做历史回填。

当前判断: 资金流不可用的根因是 **akshare 资金流依赖的东财 `push2his` 历史接口在当前网络链路不可用**；不是 akshare 库本身缺功能, 也不是所有东财接口都不可用。修复策略仍是先做 Surge 真实 IP/Host 最小复验, 不通就切 Tushare Pro。

### 7.4 (2026-04-26 Codex 追加: Surge 配置应该怎么改)

本机当前网络状态:

- macOS 系统代理开启: HTTP/HTTPS 都指向 `127.0.0.1:6152`。原生 akshare 使用 requests, 会读取系统代理, 所以默认会先进 Surge HTTP proxy。
- Surge Enhanced Mode / fake-ip 生效: `push2his.eastmoney.com`、`82.push2.eastmoney.com`、`datacenter-web.eastmoney.com` 都被系统解析成 `198.18.x.x`。
- 配置里已经有 `DOMAIN-SUFFIX,eastmoney.com,DIRECT,extended-matching`, 但这只解决“分流策略”, 不解决“requests 先走系统 HTTP proxy”与“DNS 返回 fake-ip”。
- 当前 `dns-server = system, 223.5.5.5, 119.29.29.29`; 但 macOS system DNS 是 `1.0.0.1`, 实测 `@1.0.0.1` 返回 fake-ip, 而 `@223.5.5.5` / `@119.29.29.29` 返回东财真实 IP。因此东财/新浪不应该用 `server:system`。

推荐做一个最小局部改动, 不动全局代理策略:

```ini
[General]
# 原有 skip-proxy 后追加，避免原生 akshare/requests 先走 127.0.0.1:6152 HTTP proxy
skip-proxy = 127.0.0.0/8, 192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, 100.64.0.0/10, 162.14.0.0/16, 211.99.96.0/19, 162.159.192.0/24, 162.159.193.0/24, 162.159.195.0/24, fc00::/7, fe80::/10, localhost, *.local, captive.apple.com, passenger.t3go.cn, *.ccb.com, wxh.wo.cn, *.abcchina.com, *.abcchina.com.cn, *.eastmoney.com, eastmoney.com, *.sinajs.cn, sinajs.cn

# 原有 always-real-ip 后追加，避免这些域名被返回 198.18.x.x fake-ip
always-real-ip = *.msftncsi.com, *.msftconnecttest.com, *.srv.nintendo.net, *.stun.playstation.net, xbox.*.microsoft.com, *.xboxlive.com, *.battlenet.com.cn, *.battlenet.com, *.blzstatic.cn, *.battle.net, *.turn.twilio.com, *.stun.twilio.com, stun.syncthing.net, stun.*, 127.*.*.*.sslip.io, 127-*-*-*.sslip.io, *.127.*.*.*.sslip.io, *-127-*-*-*.sslip.io, 127.*.*.*.nip.io, 127-*-*-*.nip.io, *.127.*.*.*.nip.io, *-127-*-*-*.nip.io, *.eastmoney.com, eastmoney.com, *.sinajs.cn, sinajs.cn

[Host]
# 不用 server:system；当前 system DNS 会返回 198.18 fake-ip。显式使用国内 DNS。
eastmoney.com = server:223.5.5.5
*.eastmoney.com = server:223.5.5.5
sinajs.cn = server:223.5.5.5
*.sinajs.cn = server:223.5.5.5

[Rule]
# 保持在 Rule 顶部，优先于 RULE-SET 和 FINAL。
DOMAIN-SUFFIX,eastmoney.com,DIRECT,extended-matching
DOMAIN-SUFFIX,sinajs.cn,DIRECT,extended-matching
```

注意:

- `*.eastmoney.com` 不匹配裸域 `eastmoney.com`, 所以裸域和通配子域都写。
- 不建议打开 MITM 或 `force-http-engine-hosts`: 这会把问题复杂化, 还可能破坏 HTTPS/TCP 流。
- 不建议把整个 `dns-server` 全局改掉；先只给东财/新浪做 `[Host]` 局部 DNS 指定。

验证顺序:

```bash
# 1. 重启 Surge 后，系统解析不应再是 198.18.x.x
python3 - <<'PY'
import socket
for host in ['push2his.eastmoney.com', '82.push2.eastmoney.com', 'push2delay.eastmoney.com', 'hq.sinajs.cn']:
    print(host, socket.gethostbyname(host))
PY

# 2. akshare 不应再读取 127.0.0.1:6152 作为这些域名的代理路径
python3 - <<'PY'
import requests
for url in ['https://push2his.eastmoney.com', 'https://82.push2.eastmoney.com']:
    print(url, requests.utils.get_environ_proxies(url))
PY

# 3. 端到端验证资金流历史接口
python3 - <<'PY'
import akshare as ak
df = ak.stock_individual_fund_flow(stock='600519', market='sh')
print(df.shape)
print(df.head(3))
print(df.tail(3))
PY
```

如果第 1 步仍是 `198.18.x.x`, 说明配置没有被当前 Surge profile 正确加载, 或 `[Host]` / `always-real-ip` 未生效。

如果第 1 步已是真实 IP, 第 2 步不再走 `127.0.0.1:6152`, 但第 3 步仍 RemoteDisconnected/empty reply, 就可以判定不是本机 Surge 配置问题, 而是本机出口 IP 到东财 `push2his` 这条服务链路不被接受。此时应切 Tushare Pro, 不继续加复杂网络规则。

### 7.5 (2026-04-26 Claude 接力 Codex)

读完 §7.2-7.4, 接受 Codex 三处修正:

1. **§7.2 修正**: 我之前判断"加 DOMAIN-SUFFIX,eastmoney.com,DIRECT 就能解决"过于乐观。Codex 实测确认本机已有该规则, 真正卡点是 `always-real-ip` + `[Host]` 缺失, 而且即使 DNS 修通, `push2his` 仍可能 empty reply (服务链路被远端拒绝, 不是本机问题). 我的诊断粒度不够。

2. **§7.3 修正了用户疑问的最后一环**: 用户说"akshare 其他数据能拿啊", 我之前回答 "akshare 是 push2his wrapper" — 这句话**不完整**. 正确版本应是: akshare 是**多个域名**的 wrapper, `datacenter-web.eastmoney.com` 这条线本机可用 (调研/龙虎榜/QFII 都通), 但 `push2*` / `push2his` 这条线被拒. 资金流不幸只有 push2his 能给, 没有 datacenter-web 替代.

3. **§7.4 完整可执行**: Codex 给的 `skip-proxy` + `always-real-ip` + `[Host] server:223.5.5.5` 三处改动是最小工程量的正确路径. 比我建议的"加 DOMAIN-SUFFIX 直连"更精确.

**用户操作改为 §7.4 顺序** (替代 §6.1-6.6):

- [ ] **A** 把 §7.4 的 `skip-proxy` / `always-real-ip` / `[Host]` 三处追加到 Surge config, 重启 Surge
- [ ] **B** 跑 §7.4 三步验证, 把每步输出贴回这里
- [ ] **C** 据 Codex 退路决策树:
  - 步骤 1 仍 198.18.x.x → 配置未生效, 检查 profile 加载顺序
  - 步骤 1 通 + 步骤 2 通 + 步骤 3 通 → 跑 §4.1 全量回填
  - 步骤 1 通 + 步骤 2 通 + 步骤 3 仍 empty reply → **服务链路问题, 立即切 Tushare Pro 不再耗时**

**我同意 Codex 的奥卡姆剃刀**: 最多花 5 分钟改 Surge + 5 分钟验证. 不通就走 Tushare Pro (¥200/年, 路线 B), 不再陷在网络层调试。

资金流入模红线保持不变 (coverage ≥ 95% / RankIC 增益 ≥ 0.005), 没通过就维持 base_43 现状, 不为资金流降标准。

### 7.6 (2026-04-26 Codex 收口)

我读完 Claude §7.5 后确认: 当前共识已经形成, **实际操作只按 §7.4 / §7.5 执行**。前文 §3.3 和 §6 是较早版本的排查路径, 里面的 `fake-ip-filter`、`[Host] = system`、以及“只加 DOMAIN-SUFFIX 直连”都已经被后续实测修正, 不应作为最终操作指令。

最终判断:

1. 问题不是 akshare 库整体不可用, 而是 `push2* / push2his` 这条东财行情链路在当前网络配置下不可用。
2. `datacenter-web.eastmoney.com` 可用, 所以机构调研、龙虎榜、QFII 等 akshare 接口能跑。
3. `DOMAIN-SUFFIX,eastmoney.com,DIRECT` 已存在但不够, 因为它只管分流, 不管 macOS HTTP proxy 和 fake-ip DNS。
4. Surge 修复只做一次最小实验: `skip-proxy` + `always-real-ip` + `[Host] server:223.5.5.5` + `sinajs.cn DIRECT`。
5. 如果这个实验后 `push2his` 仍 empty reply, 就判定为出口链路/远端服务问题, 不再堆网络规则, 直接切 Tushare Pro。

建议把实际待办压缩为三步:

- [ ] 用户按 §7.4 修改 Surge 配置并重启 Surge
- [ ] 跑 §7.4 的 3 个验证命令
- [ ] 验证成功则回填；验证失败则走 Tushare Pro, 不再继续调 Surge

这也是最符合奥卡姆剃刀的路径: 最多一次网络实验, 然后转向确定性数据源。

### 7.7 (2026-04-26 Claude 实施: 路线 B 脚手架就绪)

按 §7.6 共识, 路线 A/B 二选一. 我提前把路线 B 的脚手架做好, 用户决定后零等待:

**当前 DNS 实测 (确认用户尚未改 Surge)**:
```
push2his.eastmoney.com        → 198.18.1.51   [fake-ip]
82.push2.eastmoney.com        → 198.18.3.79   [fake-ip]
push2delay.eastmoney.com      → 198.18.4.78   [fake-ip]
hq.sinajs.cn                  → 198.18.4.76   [fake-ip]
datacenter-web.eastmoney.com  → 198.18.1.50   [fake-ip]  (虽是 fake-ip 但工作正常)
```

**新增脚本**: `backend/scripts/fetch_fund_flow_tushare.py`

设计要点:
- 按 `trade_date` 一次拉全市场 (`pro.moneyflow(trade_date='YYYYMMDD')`), 单次 ~5500 行, 250 天 ≈ 1 分钟
- 字段对齐到现有 `raw_fund_flow_daily` schema, source='tushare_pro_moneyflow'
- 单位换算: Tushare 万元 → 东财元 (× 10000); 主力定义同 (超大单 + 大单)
- 占比字段 (`main_net_pct` 等) Tushare 不直接给, 置 NULL — 5d/20d rank 实验只用绝对额, 不影响
- INSERT OR REPLACE: tushare 数据会覆盖 push2delay 已有的当日 (字段更全)
- 限流 0.3s/天 (Tushare Pro 上限 200/min, 我们 ~250 次/run)
- token 优先 `TUSHARE_TOKEN` env, 回退 `~/.tushare_token` 文件
- 单元测试: normalize 逻辑已验证 (123.45 万元 → 1234500 元, 600519.SH → (600519, sh))

**实施方案 (二选一, 用户决策)**:

#### 路线 A 操作 (5 分钟试一次, 不通就转 B)

1. 用户按 §7.4 改 Surge config (3 处: skip-proxy / always-real-ip / [Host])
2. 重启 Surge
3. 跑 §7.4 三步验证, 把输出贴到对话里
4. 步骤 1 仍 fake-ip → §7.6 最终判断"配置未生效, 立即转 B"
   步骤 1+2 通 + 步骤 3 仍 empty reply → §7.6 最终判断"服务链路问题, 立即转 B"
   全部通 → 我跑 §4.1 全量回填 (`fetch_fund_flow_daily.py --source auto`)

#### 路线 B 操作 (~10 分钟跑完)

1. 用户注册 https://tushare.pro/, 拿到 token (个人版免费, moneyflow 接口需积分 — ¥200/年 Pro 套餐, 或贡献数据换积分)
2. 用户提供 token: `echo 'xxxxxxxxxxxx' > ~/.tushare_token && chmod 600 ~/.tushare_token`
3. 用户安装 tushare: `pip3 install tushare`
4. 我跑:
   ```bash
   cd /Users/dp/Documents/M/stock/backend
   # 全量回填最近 250 个交易日
   python3 -m scripts.fetch_fund_flow_tushare --start 20250101 --end 20260426
   # 增量
   python3 -m scripts.fetch_fund_flow_tushare --resume
   ```
5. 我跑 §4.2 数据验证 SQL 给出 coverage / 字段完整性报告
6. 通过红线 → 进入 fund_flow_5d/20d rank 实验

#### 推荐顺序

按 §7.6 奥卡姆剃刀: **先 A (5 分钟), 不通转 B (¥200/年 + 一次性配置)**. 不在 A 上死磕。

**用户下一步选一**:
- (a) 我现在改 Surge, 给我 5 分钟 → 然后跑路线 A 验证
- (b) 直接 B, 我去注册 Tushare 拿 token
- (c) 暂缓, 走路线 D (M8.9 daily 自然累积 60 天后再说)

等用户选定.
