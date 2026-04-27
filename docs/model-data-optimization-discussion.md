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

### 5.1 (作废: Tushare Pro 路线 已弃)

2026-04-27 用户决定: **不使用 Tushare**, 删除全部 Tushare 流程与脚手架. 备选方向改为东财官方接口 (妙想 F10 / 东财 Choice / 东财 skill 自封装) — 详见 §8.

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

- [ ] **6.6** 如 push2his 仍不通, 走路线 D (接受现状, M8.9 自然累积) + 推进 §8 东财 skill 调研 (替代 akshare 直连东财 API)

---

## 7. 讨论与决策记录

### 7.1 (2026-04-26 Claude 提议)

我的建议是先做 6.1 / 6.2 — 拿到 Surge 的实际路由路径, 再决定是改规则还是换源. 大概率是 Surge 配置里有一条 RULE-SET 把 eastmoney 误归到代理组, 加显式 DIRECT 规则 5 分钟解决.

**关键风险点**: 即使修通 push2his, eastmoney 接口本身设计上限 ~250 个交易日 (大约 1 年). 想覆盖更长历史只能换源 (~~Tushare 已弃, 见 §5.1/§8~~). 但 250 天足够做横截面 rank 实验, 也足够算 fund_flow_5d/20d.

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
2. **验证失败就走路线 D**: 维持 push2delay daily 累积 + 推进东财 skill 自封装方案 (§8). ~~Tushare Pro 路线已弃 (§5.1)~~
3. **保留 push2delay daily fallback**: 它能每日积累, 但在 60 个交易日前不入 `base_43`, 只作为审计数据。
4. **不要为了资金流扩系统复杂度**: 资金流只有在 coverage >= 95%, RankIC 增益稳定超过 0.005 后才进主轨；否则保持现有 base_43。

阶段性决策: 当前最可能的落地路径是 **路线 A 最小复验 -> 失败后切到 ~~Tushare Pro~~**. (~~Tushare 路线已于 2026-04-27 弃用, 见 §5.1/§8.~~) 不要把 akshare 当成替代源, 它只是 `push2his` wrapper.

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

当前判断: 资金流不可用的根因是 **akshare 资金流依赖的东财 `push2his` 历史接口在当前网络链路不可用**；不是 akshare 库本身缺功能, 也不是所有东财接口都不可用。修复策略仍是先做 Surge 真实 IP/Host 最小复验, 不通就走路线 D (M8.9 累积) + 推进 §8 东财 skill。

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

如果第 1 步已是真实 IP, 第 2 步不再走 `127.0.0.1:6152`, 但第 3 步仍 RemoteDisconnected/empty reply, 就可以判定不是本机 Surge 配置问题, 而是本机出口 IP 到东财 `push2his` 这条服务链路不被接受。此时应走路线 D (M8.9 daily 累积) + §8 东财 skill 自封装, 不继续加复杂网络规则。

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
  - 步骤 1 通 + 步骤 2 通 + 步骤 3 仍 empty reply → **服务链路问题, 立即走路线 D + §8 东财 skill, 不再耗时**

**我同意 Codex 的奥卡姆剃刀**: 最多花 5 分钟改 Surge + 5 分钟验证. 不通就走路线 D + 推进 §8 东财 skill, 不再陷在网络层调试。

资金流入模红线保持不变 (coverage ≥ 95% / RankIC 增益 ≥ 0.005), 没通过就维持 base_43 现状, 不为资金流降标准。

### 7.6 (2026-04-26 Codex 收口)

我读完 Claude §7.5 后确认: 当前共识已经形成, **实际操作只按 §7.4 / §7.5 执行**。前文 §3.3 和 §6 是较早版本的排查路径, 里面的 `fake-ip-filter`、`[Host] = system`、以及“只加 DOMAIN-SUFFIX 直连”都已经被后续实测修正, 不应作为最终操作指令。

最终判断:

1. 问题不是 akshare 库整体不可用, 而是 `push2* / push2his` 这条东财行情链路在当前网络配置下不可用。
2. `datacenter-web.eastmoney.com` 可用, 所以机构调研、龙虎榜、QFII 等 akshare 接口能跑。
3. `DOMAIN-SUFFIX,eastmoney.com,DIRECT` 已存在但不够, 因为它只管分流, 不管 macOS HTTP proxy 和 fake-ip DNS。
4. Surge 修复只做一次最小实验: `skip-proxy` + `always-real-ip` + `[Host] server:223.5.5.5` + `sinajs.cn DIRECT`。
5. 如果这个实验后 `push2his` 仍 empty reply, 就判定为出口链路/远端服务问题, 不再堆网络规则, 走路线 D + §8 东财 skill 自封装。

建议把实际待办压缩为三步:

- [ ] 用户按 §7.4 修改 Surge 配置并重启 Surge
- [ ] 跑 §7.4 的 3 个验证命令
- [ ] 验证成功则回填；验证失败则走路线 D + 推进 §8 东财 skill, 不再继续调 Surge

这也是最符合奥卡姆剃刀的路径: 最多一次网络实验, 然后转向确定性数据源。

### 7.7 (2026-04-26, 已作废)

原内容: Claude 实施"路线 B Tushare Pro moneyflow 脚手架就绪"和路线 A/B/C 决策树. 2026-04-27 用户决定不用 Tushare, 整段方案废止. 仅保留路线 A (Surge 修 + push2his) 和路线 D (M8.9 自然累积) 二选一; 替代源研究移到 §8 东财 skill 自封装方向.

### 7.8 (2026-04-26 ~ 04-27 工作进展: 资金流 step 解锁 + 路线 A 复验失败)

距上次讨论 (§7.7 决策树) 后做了这些工作:

#### 7.8.1 工程改造 (Claude, commit `d467f79c`)

发现并修复一个隐藏问题: **资金流 step 一直锁死在 push2delay**, 即使关掉 Surge 也只拿当日 1 行/票. 旧代码 `_step_sync_fund_flow` 只 import `fetch_delay_fund_flow`, source 写死 `eastmoney_push2delay_latest`.

改造内容:

1. `backend/scripts/fetch_fund_flow_daily.py`: 新增通用 `_fetch_eastmoney_fund_flow(base_url=...)`,
   - `fetch_his_fund_flow` → push2his (~250 天历史)
   - `fetch_delay_fund_flow` → push2delay (1 行)
2. `backend/routers/updater.py _step_sync_fund_flow`: 改预探针 + 整 run 单一 source 模式
   - 第一只票试 push2his, 通就整 run 走历史模式 (mode=his, ~28 分钟落 ~138 万行)
   - 失败 fallback 到 push2delay 当日模式 (mode=delay, 当前路径)
   - 探针结果复用, 第一只票不重复请求
3. `assets/js/app.js startUpdate`: 加 `window.confirm` 弹窗提示关 Surge, sessionStorage 一会话一次
4. CM_ASSET_VERSION 3.5.0 → 3.6.0 强制 JS 缓存刷新

#### 7.8.2 用户实测 (2026-04-27 早间)

按 §7.4 改 Surge config (skip-proxy / always-real-ip / [Host] server:223.5.5.5), 彻底退出 Surge, 重启 backend, 清空 raw_fund_flow_daily, 浏览器刷新点智能更新. 关键日志:

```
DNS 验证: push2his.eastmoney.com → 117.184.40.129  (中国移动 CDN, 真实 IP, 不再 198.18.x.x)

22:27:50 [资金流] 探针失败: push2his 不可达 (000001:
    ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))),
    切到 push2delay 当日模式 — 提示: 关闭 Surge 或加 eastmoney 白名单可拿历史

23:17:37 [资金流] 完成: mode=delay ok=5496 empty=14 fail=0 rows_written=5496 target=2026-04-24
```

#### 7.8.3 关键判断: 路线 A 真死路, 是服务端拒绝

完全符合 §7.4 末尾 Codex 预警的"出口 IP 链路问题"场景:

| 检查项 | 结果 | 说明 |
|---|---|---|
| 系统 DNS 解析 push2his | ✅ 真实 CDN IP | Surge 配置已生效 |
| HTTPS TLS 握手 | ✅ 成功建联 | 不是 DNS / SNI 拦截 |
| HTTP GET response | ❌ 远端立即关闭 | 连接成功后服务端 RST |
| 同 IP 段访问 datacenter-web | ✅ 调研/龙虎榜/QFII 全通 | 不是整个 eastmoney 不可达 |

**结论**: 用户家宽出口 IP 在 eastmoney 的 push2his 反爬规则黑名单。这条线路不是本机可解的, 不是 Surge / DNS / 代理 / akshare 任何一层的问题. **不再耗时间在网络规则**, 走路线 D (维持 push2delay daily 累积 + M8.9 自然累积 60 天) + 推进 §8 东财 skill (替代 akshare 直连东财)。

#### 7.8.4 当前数据状态 (2026-04-27)

```sql
SELECT COUNT(*), MIN(trade_date), MAX(trade_date), source FROM raw_fund_flow_daily;
-- 5496 行 / 1 天 / 2026-04-24 / eastmoney_push2delay_latest
```

虽然历史拿不到, 但 step 解锁本身有价值: M8.9 launchd 跑起来后**每个交易日自动 +1 行/票**, 60 天后 (~2026-07) 会有 60 天 daily 横截面, 可以做 5d/20d rank 实验, 比 §4.21 决策时的"等积累"路径仍然有效。

#### 7.8.5 UI 显示 bug (待修, 低优先)

工作台主力资金流行显示 `NaN 条 · 4-26 23:17` 而不是 `[当日模式] 写入 5496 · 已最新 0 · 空返回 14 · 失败 0`. 后端 detail.message 字段返回正确, 前端 renderStepGrid 应优先读 detail.message. 推测是浏览器缓存 / fmt(s.records) 处理 NaN 路径有 bug. 数据本身完整 (DB 5496 行已落). 此 bug 不影响数据正确性, 优先级低于资金流主线决策.

#### 7.8.6 路线决策 (Tushare 已弃)

经过路线 A 复验失败, 严格按 §7.4 + §7.6 共识:

**路线 D: 维持现状, M8.9 自然累积** (主路线)

- 不付费, 不写代码
- 每日 17:30 launchd 自动 +1 行/票 (5500 行/日)
- 60 个交易日后 (~2026-07) 重新评估是否做 5d/20d rank 实验
- 主轨 base_43 +22pp/fold 不依赖资金流, 不阻塞 alpha

**§8 方向: 东财 skill 自封装** (中长期替代源)

- 见 §8: 妙想 F10 + 东财直连接口 wrapper (替代 akshare 对东财部分)
- 不依赖第三方 (Tushare 已弃), 不依赖 akshare 对 push2his 的间接调用
- 与 路线 D 并行推进, 后续 wrapper 成熟后可一次性替换 akshare 资金流路径

**Claude 推荐**: D + §8 并行 — 主轨已能跑, 资金流是锦上添花. 等 60 天数据自然累积成本最低, 期间可以推进跟投系统改造 (memory 里 `project_followup_alpha_redesign.md` 列的: 完整周期收益 / qlib 信号接入 / 三档信号重构) 这些 ROI 更高的事。

等用户决定 D 或 §8 推进节奏。

### 7.9 (2026-04-27 Codex 方案: 停止网络排查, 分层解决) — Tushare 部分已废止

读完 §7.8 后, Codex 把问题拆成三层 (网络/工程/数据). 数据层原方案为 Tushare Pro, **2026-04-27 用户决定不用 Tushare, 数据层方案改为路线 D + §8 东财 skill 自封装**.

保留下来的工程修复项 (P0/P1):

**P0: 修复工作台 `主力资金流 NaN 条`**

实测发现 `step_status.records` 当前存的是整段 dict 字符串而不是数值 `5496`. 所以 UI `NaN 条` 不只是前端缓存问题, 后端状态表也需要清理.

处理方案:
1. 后端保证 `_resolve_step_result(dict)` 后只把 `count` 写入 `records`, 详细 dict 只写入 `error` JSON.
2. 做一次性修复 SQL: 对 `sync_fund_flow` 当前异常 records 行, 把 `records` 改为 `5496`, `error` 补成合法 JSON detail.
3. 前端 `renderStepGrid` 做防御: `Number.isFinite(Number(s.records))` 才显示 `N 条`; `detail.message` 优先显示.
4. 验收: 工作台显示 `[当日模式] 写入 5496 · 已最新 0 · 空返回 14 · 失败 0`, 不再出现 `NaN 条`.

**P1: 给 push2his 探针加失败冷却**

既然路线 A 已判死, 每次智能更新都探一次 push2his 没价值. 建议记录最近一次 `push2his` 探针失败时间, 24 小时内直接走 push2delay, 减少日志噪声和启动等待. 用户手动点击”强制历史探针”时再重试.

**P1: 确认 M8.9 daily 自动累积真的在跑**

D 路线依赖 daily 累积. 需要检查 launchd/智能更新是否每天收盘后跑 `sync_fund_flow`:

```sql
SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date)
FROM raw_fund_flow_daily
WHERE source='eastmoney_push2delay_latest';
```

验收: 每个新交易日新增约 5500 行.

#### 7.9.4 入模方案 (用户决定数据来源后)

不要把原始资金流直接塞进主模型. 先做两个简单、可解释的横截面特征:

1. `fund_flow_5d_rank`: 近 5 日 `main_net_amount` 累计额 / 当日横截面 rank
2. `fund_flow_20d_rank`: 近 20 日 `main_net_amount` 累计额 / 当日横截面 rank

先做独立评估 (coverage / quality / RankIC), 通过红线 (RankIC 提升 ≥ 0.005, 分层收益稳定改善) 才入主轨.

#### 7.9.5 结论

1. 先修 `NaN 条` 状态显示和 records 落库清理.
2. 短期: 走 D 自然累积 (60 个交易日后做初步 5d/20d 实验), 同时把研发精力转回 base_43 / 跟投系统主线.
3. 中期: 推进 §8 东财 skill 自封装, 替代 akshare 对 push2his 的间接调用 + 拓展妙想 F10 数据维度.

### 7.10 (2026-04-27, 已作废)

原内容: “Tushare 全源替换评估”, 详见原 `docs/tushare-source-strategy.md` (已删除). 2026-04-27 用户决定不用 Tushare, 评估废止. 替代源方向改为 §8 东财 skill 自封装.

### 7.11 (2026-04-27 Codex 追加: 智能更新状态与资金流入口修复)

本次复查 2026-04-27 08:33 智能更新日志后确认两个工程问题:

1. **数据获取组 runner 没有统一解析 dict 返回值**。`机构调研`、`QFII`、`两融`、`龙虎榜`、`主力资金流` 已经返回了 `{status,count,message}`，但分组管线仍把整个 dict 当作 records 写入，导致前端只能靠审计层猜状态；没有审计层的资金流会被显示成 idle，出现“有 OK 有空白”的不规范结果。
2. **资金流步骤被 `latest_completed_trade_date` 间接锁成单日补齐**。`raw_fund_flow_daily` 只要已有 `target_date` 的 push2delay 单日记录，智能更新就会认为该票“已最新”并跳过，导致历史缺口永远不会被 akshare/push2his 回填。

修复决策:

- 后端统一 `_resolve_step_result()`，数据获取组也按 `status/count/message` 落库；`partial` 进入终态统计，`skipped` 用“已最新”而非空白跳过表达。
- 前端 `deriveDisplayStatus()` 不再只依赖审计层判断完成；只要后端有 `finished_at/detail/records`，就展示终态。`skipped` 行显示 `OK 已最新`，阻断类原因仍显示 `阻断`。
- `sync_fund_flow` 主入口按用户最新要求改回 akshare 历史拉取：默认 `CM_FUND_FLOW_SOURCE=akshare`，逐股调用 `stock_individual_fund_flow`，不再按 `target_date` 跳过，也不再默认静默退到 push2delay 单日模式。
- `target_date` 只保留为审计参考；状态文案会明确显示“akshare历史 / 东财历史 / 当日兜底”，避免再把单日 fallback 误看成完整资金流历史。
- 智能审计不再只看 `MAX(trade_date)`，而是统计“达到覆盖阈值的交易日数”；当前默认少于 60 个有效资金流交易日就继续把 `sync_fund_flow` 放进智能更新计划。

可选调试开关:

```bash
# 默认: akshare 历史接口, 不做单日兜底
CM_FUND_FLOW_SOURCE=akshare ./start.command

# 显式只测东财 push2his 直连历史接口
CM_FUND_FLOW_SOURCE=his ./start.command

# 显式允许 akshare 失败后落到 push2delay 单日兜底
CM_FUND_FLOW_SOURCE=auto ./start.command

# 调试小样本, 默认不限制股票数
CM_FUND_FLOW_MAX_STOCKS=50 ./start.command
```

`start.command` 只保留 akshare 自动升级检查. **Tushare 脚本与方案已于 2026-04-27 删除**, 不再保留.
