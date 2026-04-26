# 资金流全量历史接入调研 (2026-04-26)

## 背景

- `raw_fund_flow_daily` 现状: **rows=5496 / days=1 / 范围 2026-04-24 ~ 2026-04-24** (单日快照)
- 来源: `push2delay.eastmoney.com`, 接口结构上只给最新一天
- 阻塞原因: ClashX/Surge fake-ip 模式把 `*.eastmoney.com` 解析到 `198.18.x.x`, `push2his` 历史接口连接被远端立即关闭
- 决策依据: §4.21 Codex "M9 不入模, 等积累 20-60 个交易日"; M8.9 daily 自动化已落地 (commit `c7182be9`), 但单纯 daily 累积要 3 个月才有 20 天数据, 6 个月才有 60 天

## 实测网络状态 (2026-04-26 15:50)

```
push2his.eastmoney.com    → 198.18.1.51   (ConnectionError: RemoteDisconnected)
push2.eastmoney.com       → 198.18.2.234  (未测, 推断同上)
push2delay.eastmoney.com  → 198.18.4.78   (200 OK, 但只返回 1 行 = 当日)
hq.sinajs.cn              → 198.18.4.76   (sina 接口同样在 fake-ip 范围)
```

`session.trust_env=False` 不能绕开问题, 因为 198.18.x.x 是 ClashX 的 fake-ip 网段 — 只要 ClashX 接管 OS 路由, 走系统 DNS 就拿到 fake-ip; 走代理才能拿到真实路由。

## 路线评估

### 路线 A: 修 ClashX/Surge 白名单 (推荐首选)

**做法**:
- ClashX/Surge 配置加规则:
  ```yaml
  - DOMAIN-SUFFIX,eastmoney.com,DIRECT
  - DOMAIN-SUFFIX,sinajs.cn,DIRECT
  ```
- `fake-ip-filter` 增加这两个后缀, 避免 fake-ip 接管
- 验收: `python3 -c "import socket; print(socket.gethostbyname('push2his.eastmoney.com'))"` 不应再返回 `198.18.x.x`

**收益**:
- `push2his` 历史接口复活, 单股可取 ~120-250 个交易日历史 (≈ 半年到 1 年)
- 无代码改动, 现有 `fetch_fund_flow_daily.py --source auto` 能直接落库
- 5507 票全量历史回填估计 ~50 分钟 (按 0.3s/票 + retry)

**成本**:
- 用户操作 5-10 分钟 (改代理 config + 重启 ClashX/Surge)

**风险**:
- 历史深度受限于 eastmoney 设计 (~250 个交易日上限), 不能回到 2023 年
- 如果用户机器 ClashX 有自动更新规则集, 白名单可能被覆盖

**Codex §4.21 / §4.36 的原始建议**: 已明确推荐此路线作为代理修复方案, 不引入新依赖。

### 路线 B: Tushare Pro `moneyflow` 接口

**做法**:
- 注册 https://tushare.pro/, 需要积分门槛 (~¥200/年付费或贡献数据换积分)
- 写新 client `services/tushare_fund_flow_client.py`, 调用 `pro.moneyflow(ts_code='600519.SH', start_date='20200101', end_date='20260101')`
- 字段对齐到 `raw_fund_flow_daily`, 标 `source='tushare_pro'`

**收益**:
- 历史完整 (10+ 年), 完全覆盖训练期
- 接口稳定, 不依赖代理
- 多线程下载 5507 票 ~30-60 分钟

**成本**:
- ¥200/年 token 费 (用户决定)
- 1-2 天工程 (client + 测试 + 增量同步)

**风险**:
- Tushare 字段语义可能与 eastmoney 不完全对齐 (主力定义、净占比口径), 需做 spot check
- 长期依赖第三方 API, 涨价或限频风险

### 路线 C: 新浪 `vMS_MFTrend` 网页爬虫

**做法**:
- URL: `https://vip.stock.finance.sina.com.cn/q/go.php/vMS_MFTrend/...?stock=sh600519`
- 反爬: 单股请求限速 (~1-3s 间隔, 否则 IP 封禁), 可能需要登录 cookie
- 5507 票全量爬 ~3-5 小时

**收益**:
- 免费, 历史完整
- 数据源用户已有访问权 (网页能打开)

**成本**:
- 2-3 天工程 (爬虫 + 限速 + 错误处理 + IP 池)
- 长期维护负担 (反爬规则迭代)

**风险**:
- 不稳定, 高于 Tushare
- 与 eastmoney 字段口径差异更大

### 路线 D: 接受现状 + 替代信号 (保底)

**做法**:
- 资金流维持现状 (1 天 + daily 累积)
- 用 `inst_holdings` 季度变动 + `raw_lhb_*` (龙虎榜) + `raw_margin_*` (两融) + `raw_institution_survey_*` (调研) 模拟"主力意图"
- 这些信号已在 `base_43` 主轨, 实测 mean excess **+22.07pp/fold** (M8.0.4 修复后)

**收益**:
- 0 工程, 0 成本
- 主轨已能跑, 资金流不阻塞 alpha

**风险**:
- 缺横截面分钟级资金流信号, alpha 上限受限
- 但季度持仓 + 龙虎榜 + 两融已是 base_43 top 重要性

### 路线 E: 自己用日 K + 龙虎榜 + 五档拼"伪资金流" (不推荐)

**做法**: 主力资金流 ≈ `(大单买入 - 大单卖出) / 全成交额`, 大单定义阈值

**问题**:
- 需要分钟级或 tick 数据, 当前只有日 K (open/high/low/close/volume)
- 通达信 mootdx 可下分钟 K, 但单股下载时间长且无法区分大小单 (没逐笔成交)
- **结论**: 工程量极大且数据本质缺失, 不可行

## 建议

按 ROI 排序:

1. **路线 A 修代理** — 用户机器侧操作 5-10 分钟, 拿 ~250 天历史. 失败成本最低, 收益最高
2. **路线 D 接受现状** — 主轨已跑, 资金流不阻塞. 与 A 不冲突, 可并行 (改完代理还可以 toggle 开)
3. **路线 B Tushare Pro** — 如果路线 A 改完代理仍卡 (push2his 限制 250 天且未来失效), 考虑 ¥200/年买 Tushare token

**不建议**:
- 路线 C (爬虫维护负担太重)
- 路线 E (数据本质缺失)

## 落地方案 (一旦用户选定)

### 选 A: 用户改代理后
```bash
cd backend
# 全量回填 (~50 分钟)
python3 -m scripts.fetch_fund_flow_daily --source auto --rate-limit 0.3
# 验证
python3 -c "
import duckdb
conn = duckdb.connect('../data/smartmoney.duckdb', read_only=True)
print(conn.execute('SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) FROM raw_fund_flow_daily').fetchone())
"
# 期望: ~250 天, 范围 2025-05~2026-04
```

### 选 B: Tushare Pro
- 用户给 token (`.env` 或 `~/.tushare_token`)
- 我新写 `backend/services/tushare_fund_flow_client.py` (复用现有 schema)
- 加 `--source tushare` 选项到 `fetch_fund_flow_daily.py`

### 选 D: 不动
- M8.9 daily 自动累积每天 +1 行/票
- 60 个交易日 ≈ 3 个月后 (2026-07 左右) 重新评估是否做 `fund_flow_5d/20d` 实验

---

**Claude 推荐**: 路线 **A** + **D** 并行. A 是低成本撬动 250 天历史的关键; D 是 alpha 主轨已有的安全垫. B 留作 A 失败后的付费选项.
