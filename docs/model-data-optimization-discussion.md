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
