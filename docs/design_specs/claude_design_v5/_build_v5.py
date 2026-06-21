# 设计卡构建脚本 — 共享样式一处定义, 8 张 v5 卡批量生成
BASE = """:root{--brand:#3657D8;--brandb:#dbe3fb;--bg:#FAFAFA;--sf:#fff;--ink:#111827;--mut:#6b7280;--ln:#e5e7eb;
--ok:#0B9D6A;--okb:#dcf4e7;--wn:#d97706;--wnb:#fef3c7;--bd:#dc2626;--bdb:#fee2e2;--uk:#9ca3af;--ukb:#f3f4f6}
*{box-sizing:border-box}body{font-family:-apple-system,'PingFang SC',sans-serif;background:var(--bg);color:var(--ink);margin:0;font-size:13px}
table{font-variant-numeric:tabular-nums;width:100%;border-collapse:collapse;font-size:12px}
td,th{text-align:left;padding:6px 8px;border-top:1px solid var(--ln)}th{color:var(--mut);font-weight:500;border:0}
.top{display:flex;gap:14px;align-items:center;padding:10px 20px;background:var(--sf);border-bottom:1px solid var(--ln)}
.top b{font-size:15px}.nav{display:flex;gap:2px}.nav a{padding:6px 12px;border-radius:8px;color:var(--mut);text-decoration:none;font-size:12px}
.nav a.on{background:var(--brandb);color:var(--brand);font-weight:600}
.role{margin-left:auto;display:flex;gap:4px;background:var(--ukb);border-radius:8px;padding:3px}
.role span{padding:4px 10px;border-radius:6px;font-size:11px;color:var(--mut)}.role span.on{background:var(--sf);color:var(--ink);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.wrap{padding:16px 20px;display:grid;gap:14px}
.card{background:var(--sf);border:1px solid var(--ln);border-radius:12px;padding:14px}.card h3{margin:0 0 8px;font-size:13px}
.card .sub{font-size:11px;color:var(--mut);margin-bottom:10px}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
.tag.ok{background:var(--okb);color:var(--ok)}.tag.bd{background:var(--bdb);color:var(--bd)}
.tag.wn{background:var(--wnb);color:var(--wn)}.tag.uk{background:var(--ukb);color:var(--uk)}.tag.br{background:var(--brandb);color:var(--brand)}
.ent{color:var(--brand);font-weight:600;border-bottom:1px dashed var(--brand);cursor:pointer}
h1{font-size:17px;margin:0 0 4px}p.lead{color:var(--mut);font-size:12px;max-width:900px;margin:0 0 14px}
"""
def page(fname, group, body, extra_css=""):
    html = f"""<!-- @dsCard group="{group}" -->
<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><style>{BASE}{extra_css}</style></head><body>{body}</body></html>"""
    open(fname, 'w').write(html)
    print("built", fname)

NAV = lambda on: ('<div class="top"><b>ChunkyMonkey</b><div class="nav">'
  + ''.join(f'<a class="{"on" if t==on else ""}">{t}</a>' for t in ['今日决策','策略组合','档案','实验工厂','数据底座','治理'])
  + '<span style="margin-left:8px;color:var(--ln)">|</span></div>{role}</div>')

def role_pills(active):
    return ('<div class="role">' + ''.join(
        f'<span class="{"on" if r==active else ""}">{r}</span>' for r in ['投资人','研究员','管理员']) + '</div>')

# ---------- 10 IA v5 ----------
page("10_ia_v5.html", "IA", """
<div style="margin:24px">
<h1>统筹 IA v5 — 角色版面 × 档案体系 × 机器 verdict 流</h1>
<p class="lead">三个设计传统的合流: 旧 v3 的<b>档案体系</b> (股票/公式/机构视图 + 全局抽屉, 任何实体名可点开档案) +
v4 的<b>每日动线与 verdict 流</b> (状态色只来自机器判决) + 新增<b>角色版面</b> (同一档案底座, 三种工作台)。</p>
<table style="max-width:980px">
<tr><th style="width:110px">角色</th><th>默认首屏</th><th>可见模块</th><th>设计语气</th></tr>
<tr><td><b>投资人</b> (用户)</td><td>今日决策: KPI 四格 + 候选 (带可解释分解) + 持仓与退出信号</td><td>今日决策 / 策略组合 / 档案 (股票·策略)</td><td>结果优先, 零运维噪音; 每个数字可点穿到证据</td></tr>
<tr><td><b>研究员</b></td><td>实验工厂: 预注册→gate→判决流水线 + 12 周路线</td><td>实验工厂 / 档案 (公式·实验·数据域) / 可视化工作台</td><td>判据与证据优先; 阴性结果同等可见</td></tr>
<tr><td><b>管理员</b> (总指挥)</td><td>指挥台: 接手对账 verdict 横幅 + 手动任务 + 告警</td><td>指挥台 / 数据底座 / 治理 / 全部档案</td><td>能不能动 → 哪里坏了 → 一键修</td></tr>
</table>
<h1 style="margin-top:26px">档案体系 (6 类) — 全局可达: 任何界面点击实体名 = 打开档案抽屉, 深查另开整页</h1>
<table style="max-width:980px">
<tr><th style="width:110px">档案</th><th>核心内容</th><th>真相源</th></tr>
<tr><td><b>股票档案</b></td><td>价格+信号叠加图 · 推荐/退出原因 (公式贡献分解) · 机构持仓 · 概念归属 · 事件时间线</td><td>K线/mart 推荐/fact_concept_event</td></tr>
<tr><td><b>公式档案</b></td><td>人话+机器话定义 · 参数溯源 (yaml/optuna) · OOS 成绩单 · 贡献 waterfall · 审计状态</td><td>formula_engine/optuna study/oos_* 列</td></tr>
<tr><td><b>策略档案</b></td><td>法的可视化: 创世层/判断法典/死亡条款 · paper_sim 净值 · ablation 记录</td><td>prereg 文档/paper_sim</td></tr>
<tr><td><b>机构档案</b></td><td>跟随价值评分卡 · 第二次行为标准 · 持仓变动</td><td>institution mart (旧主线收编)</td></tr>
<tr><td><b>数据域档案</b></td><td>registry 条目可视化: pit_anchor/SLA/watermark/失败单/样本契约</td><td>sync_registry/data-status</td></tr>
<tr><td><b>实验档案</b></td><td>预注册判据 (冻结) → gate → 三判官 → 处置</td><td>prereg 文档/sherpa gates</td></tr>
</table></div>""")

# ---------- 11 投资人首屏 ----------
page("11_role_investor.html", "Screens·角色版面", NAV('今日决策').format(role=role_pills('投资人')) + """
<div class="wrap">
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
<div class="card"><div class="sub">年化收益 (含成本 OOS)</div><div style="font-size:26px;font-weight:700;color:var(--uk)">unknown</div><div class="sub">第一张可信表 W8 · 点击看为什么</div></div>
<div class="card"><div class="sub">最大回撤</div><div style="font-size:26px;font-weight:700;color:var(--uk)">unknown</div><div class="sub">目标 ≥ -20%</div></div>
<div class="card"><div class="sub">超额 vs HS300</div><div style="font-size:26px;font-weight:700;color:var(--uk)">unknown</div><div class="sub">基准转正中</div></div>
<div class="card"><div class="sub">月胜率</div><div style="font-size:26px;font-weight:700;color:var(--uk)">unknown</div><div class="sub">目标 ≥ 55%</div></div></div>
<div style="display:grid;grid-template-columns:1.3fr .7fr;gap:14px">
<div class="card"><h3>今日候选 (paper 态 · 每分可解释)</h3>
<table><tr><th>股票</th><th>综合分</th><th>为什么 (点击展开贡献分解)</th><th>退出挂钩</th></tr>
<tr><td><span class="ent">五粮液 000858</span></td><td><b>0.71</b></td><td><span class="tag br">reversal_1m_deep +0.42</span> <span class="tag br">stage=1 +0.18</span> <span class="tag uk">L1 筹码 off</span></td><td>wr_tp · elg_outflow</td></tr>
<tr><td><span class="ent">宁德时代 300750</span></td><td><b>0.66</b></td><td><span class="tag br">reversal_1m_deep +0.39</span> <span class="tag br">板块顺风 +0.11</span></td><td>同上</td></tr></table>
<div class="sub" style="margin-top:8px">每个贡献 tag 可点 → 公式档案 (定义/参数来源/OOS 成绩单) — 公式可解释的入口在决策现场, 不在文档里</div></div>
<div class="card"><h3>持仓与退出信号</h3>
<table><tr><th>持仓</th><th>状态</th></tr>
<tr><td><span class="ent">招商银行 600036</span></td><td><span class="tag ok">持有 · 无退出信号</span></td></tr>
<tr><td><span class="ent">隆基绿能 601012</span></td><td><span class="tag wn">LHB 上榜 (实验观察, 未启用)</span></td></tr></table>
<div class="sub" style="margin-top:8px">验证期 W1-W12: 0 真金白银 · 全部 paper_sim 候选态 (验证期横幅常显)</div></div>
</div></div>""")

# ---------- 12 管理员首屏 ----------
page("12_role_admin.html", "Screens·角色版面", NAV('指挥台').format(role=role_pills('管理员')).replace('今日决策','指挥台') + """
<div class="wrap">
<div style="display:flex;align-items:center;gap:14px;background:var(--okb);border:1px solid var(--ok);border-radius:12px;padding:12px 16px">
<span style="background:var(--ok);color:#fff;font-weight:700;border-radius:8px;padding:6px 14px">可以动</span>
<div><b>接手对账 OK</b> — flag 0 · 写锁空闲 · 弹仓 8/8 · 脏树 0 <span class="sub">sherpa takeover @ 09:02 · FAIL 时锁住全部手动按钮</span></div></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
<div class="card"><h3>手动任务 (注册表驱动)</h3>
<table><tr><td><b>daily_update</b><br><span class="sub">上次 20:54 OK · 1 降级</span></td><td style="text-align:right"><span class="tag br">跑</span></td></tr>
<tr><td><b>tdx_pool_refresh</b><br><span class="sub">上次 47 活/70 死</span></td><td style="text-align:right"><span class="tag br">跑</span></td></tr>
<tr><td><b>chain9 历史回填</b><br><span class="sub">在跑 · step 2c · 3.1k/9.7k 调用</span></td><td style="text-align:right"><span class="tag wn">38%</span></td></tr></table></div>
<div class="card"><h3>verdict 流 (机器生成)</h3><div style="line-height:2.1">
<span class="tag ok">OK</span> K 线 06-12 落 5,200 行<br>
<span class="tag wn">WARN</span> kline watermark 滞后 1 天 → <span class="ent">数据域档案: kline_daily</span><br>
<span class="tag bd">FAIL</span> dc_member 截断签名 21/30 → <span class="ent">数据域档案: dc_member</span><br>
<span class="tag wn">WARN</span> failure_queue 6 open</div></div></div></div>""")

# ---------- 13 研究员首屏 ----------
page("13_role_researcher.html", "Screens·角色版面", NAV('实验工厂').format(role=role_pills('研究员')) + """
<div class="wrap">
<div class="card"><h3>12 周路线 · W1 末</h3><div style="display:flex;gap:3px">""" + ''.join(
    f'<span style="flex:1;text-align:center;font-size:10px;padding:6px 0;border-radius:6px;background:{bg};color:{c}{w}">{t}</span>'
    for t,bg,c,w in [("W1","var(--okb)","var(--ok)",""),("W2","var(--brandb)","var(--brand)",";font-weight:700"),
    ("W3 判决","var(--ukb)","var(--mut)",""),("W4 生死关","var(--ukb)","var(--mut)",""),("W5","var(--ukb)","var(--mut)",""),
    ("W6 ablation","var(--ukb)","var(--mut)",""),("W7","var(--ukb)","var(--mut)",""),("W8 第一张表","var(--ukb)","var(--mut)",""),
    ("W9 复审","var(--ukb)","var(--mut)",""),("W10","var(--ukb)","var(--mut)",""),("W11","var(--ukb)","var(--mut)",""),("W12 GO/NO-GO","var(--ukb)","var(--mut)","")]) + """
</div></div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
<div class="card"><h3><span class="ent">LHB 上榜即退出</span> <span class="tag wn">gate 等数据</span></h3>
<div class="sub">预注册已冻结 · J1 +1.0pp / J2 5/7 年 / J3 1.5x</div>
<table><tr><td>G2 top_list 728 日</td><td><span class="tag bd">回填中</span></td></tr></table></div>
<div class="card"><h3><span class="ent">LF V0 龙头-跟随</span> <span class="tag bd">NO-GO</span></h3>
<div class="sub">预注册已冻结 · J1 +0.55pp / 三期同号 / PIT 留存 50%</div>
<table><tr><td>G2 dc_member 零截断</td><td><span class="tag bd">21/30 日</span></td></tr></table></div>
<div class="card"><h3><span class="ent">C0 筹码口径</span> <span class="tag bd">FAIL 已裁决</span></h3>
<div class="sub">处置: 5 combo 冻结 · 复活钥匙 cyq_chips 对照</div>
<table><tr><td>判负归档 = 合格产出</td><td><span class="tag ok">已入档</span></td></tr></table></div></div>
<div class="card"><h3>可视化工作台 (研究员专属)</h3><div class="sub">任选 mart/fact 表 → 标准图组 (分布/分年/截面) · 全部 read_only · 输出可存 analysis/ 证据</div></div>
</div>""")

# ---------- 14 股票档案 ----------
page("14_dossier_stock.html", "Dossiers·档案", NAV('档案').format(role=role_pills('投资人')) + """
<div class="wrap">
<div class="card"><div style="display:flex;align-items:baseline;gap:12px"><h1 style="margin:0">五粮液 <span style="font-family:monospace;font-size:14px;color:var(--mut)">000858.SZ</span></h1>
<span class="tag ok">候选中 · 0.71</span><span class="tag uk">paper 态</span><span class="sub">白酒 · 申万L2 食品饮料 · 概念: <span class="ent">白酒</span> <span class="ent">消费</span></span></div></div>
<div style="display:grid;grid-template-columns:1.4fr .6fr;gap:14px">
<div class="card"><h3>价格 + 信号叠加 (240 日)</h3>
<svg viewBox="0 0 600 160" style="width:100%;height:160px">
<polyline fill="none" stroke="#3657D8" stroke-width="2" points="0,120 60,110 120,125 180,95 240,100 300,70 330,85 360,60 420,75 480,55 540,62 600,48"/>
<circle cx="300" cy="70" r="5" fill="#0B9D6A"/><text x="308" y="64" font-size="10" fill="#0B9D6A">reversal 信号 t</text>
<circle cx="360" cy="60" r="5" fill="#d97706"/><text x="368" y="54" font-size="10" fill="#d97706">t+1 open 入场</text>
<line x1="480" y1="20" x2="480" y2="150" stroke="#dc2626" stroke-dasharray="4"/><text x="486" y="30" font-size="10" fill="#dc2626">wr_tp 触发位</text></svg>
<div class="sub">叠加层可勾选: 信号 / 进出点 / 涨停日 / LHB 上榜日 / 概念事件 — 每个标记可点开解释</div></div>
<div class="card"><h3>为什么在候选里 (贡献分解)</h3>
<table><tr><th>因子</th><th>贡献</th><th></th></tr>
<tr><td><span class="ent">reversal_1m_deep</span></td><td>+0.42</td><td><span class="tag ok">oos 0.0186</span></td></tr>
<tr><td><span class="ent">stage=1</span></td><td>+0.18</td><td><span class="tag ok">PIT 净</span></td></tr>
<tr><td>L1 筹码确认</td><td>—</td><td><span class="tag uk">冻结 (C0)</span></td></tr>
<tr><td>板块顺风 L3</td><td>+0.11</td><td><span class="tag wn">W5 口径</span></td></tr></table>
<div class="sub">每行可点 → 公式档案; 分数 = Σ贡献, 不存在不可溯源的黑箱项</div></div></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
<div class="card"><h3>事件时间线</h3><table>
<tr><td>06-12</td><td>入候选 (0.71) · <span class="sub">evidence: run #2381</span></td></tr>
<tr><td>06-10</td><td>概念事件: <span class="ent">白酒</span> member 无变动</td></tr>
<tr><td>06-05</td><td>北向净买入 2.1 亿 <span class="tag uk">研究层</span></td></tr></table></div>
<div class="card"><h3>机构持仓 (跟随价值)</h3><table>
<tr><td><span class="ent">XX 资管</span></td><td>评分 8.2 · <span class="sub">第二次行为: 加仓兑现</span></td></tr></table></div>
</div></div>""")

# ---------- 15 公式档案 ----------
page("15_dossier_formula.html", "Dossiers·档案", NAV('档案').format(role=role_pills('研究员')) + """
<div class="wrap">
<div class="card"><div style="display:flex;align-items:baseline;gap:12px"><h1 style="margin:0">公式档案: reversal_1m_deep</h1>
<span class="tag ok">现役 · B 主书地基</span><span class="sub">公式工厂 6+54 之一 · 回调十字星标准</span></div></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
<div class="card"><h3>定义 (两种话 — 判断法典纪律)</h3>
<div style="background:var(--ukb);border-radius:8px;padding:10px;margin-bottom:8px"><b>人话</b><br>一个月内深度回调后, 在低位收出企稳形态的股票, 次日开盘买入。</div>
<div style="background:var(--ukb);border-radius:8px;padding:10px;font-family:monospace;font-size:11px;line-height:1.7">
<b style="font-family:inherit">机器话</b><br>ret_21d &lt;= q20(cross_section)<br>AND close/max(close,21) &lt;= 0.85<br>AND candle in ('doji','hammer')<br>signal_t 收盘生成 → t+1 open 执行</div></div>
<div class="card"><h3>参数溯源 (零拍脑袋审计)</h3>
<table><tr><th>参数</th><th>值</th><th>来源 (可点)</th></tr>
<tr><td>回调分位 q</td><td>0.20</td><td><span class="tag br">optuna study #f2-32</span></td></tr>
<tr><td>深度阈值</td><td>0.85</td><td><span class="tag br">formula_bank.yaml §reversal</span></td></tr>
<tr><td>窗口</td><td>21d</td><td><span class="tag br">yaml (日历月)</span></td></tr>
<tr><td>止损/止盈</td><td>walk-forward</td><td><span class="tag br">mart..oos 列</span></td></tr></table>
<div class="sub">没有来源标签的参数不允许出现在档案里 — 渲染层强制 (rule-compliance 的 UI 化)</div></div></div>
<div style="display:grid;grid-template-columns:1.2fr .8fr;gap:14px">
<div class="card"><h3>OOS 成绩单 (诚实口径)</h3>
<table><tr><th>口径</th><th>值</th><th>窗口</th></tr>
<tr><td>RankIC (5d)</td><td>0.0186</td><td>2023-01→2026-05 walk-forward</td></tr>
<tr><td>地基组合 (与 stage=1)</td><td>+0.392 / 58.1%</td><td>§10 表口径 · 58% 叙事唯一合法支撑</td></tr>
<tr><td>分年同号</td><td>3/3 年正</td><td>2023/2024/2025</td></tr></table>
<div class="sub">红线常显: RankIC&gt;0.3 或胜率&gt;95% = leakage 警报而非喜讯</div></div>
<div class="card"><h3>审计状态</h3><table>
<tr><td>PIT 审计</td><td><span class="tag ok">PASS 06-04</span></td></tr>
<tr><td>反例关联</td><td><span class="tag ok">无未结</span></td></tr>
<tr><td>搭载实验</td><td><span class="ent">B-V1 正交性生死关 (W4)</span></td></tr></table></div></div></div>""")

# ---------- 16 策略档案 ----------
page("16_dossier_strategy.html", "Dossiers·档案", NAV('档案').format(role=role_pills('投资人')) + """
<div class="wrap">
<div class="card"><div style="display:flex;align-items:baseline;gap:12px"><h1 style="margin:0">策略档案: B 主书 — Reversal-Plus v2</h1>
<span class="tag wn">W6 ablation 前 · paper 态</span></div>
<div class="sub">策略档案 = 法的可视化: 创世层 / 判断法典 / 死亡条款 三块骨头常显, 不是营销页</div></div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
<div class="card" style="border-top:3px solid var(--ink)"><h3>创世层 (不可变)</h3>
<div style="line-height:1.9;font-size:12px"><b>为什么存在</b>: 把"深回调后的企稳"这一个被实测验证的行为模式, 做成可审计的入场流。<br>
<b>死线</b>: 信号 t 收盘生成 t+1 open 执行 · 全 KPI 含成本 OOS 口径 · 不引用 in-sample 数字</div></div>
<div class="card" style="border-top:3px solid var(--brand)"><h3>判断法典 (演化中 · 留痕)</h3>
<table><tr><td>三层确认 gate</td><td><span class="tag uk">待验 (W3/W4)</span></td></tr>
<tr><td>期望值账单三列同表</td><td><span class="tag ok">纪律件 6</span></td></tr>
<tr><td>4 基准缺一不可</td><td><span class="tag ok">含 random-entry</span></td></tr></table></div>
<div class="card" style="border-top:3px solid var(--bd)"><h3>死亡条款</h3>
<table><tr><td>感知死: 预测不回填</td><td><span class="tag ok">0 超期</span></td></tr>
<tr><td>判断死: A0 锚点偏离带</td><td><span class="tag uk">W2 复跑</span></td></tr>
<tr><td>谄媚死: 判据放宽讨论</td><td><span class="tag ok">0 触发</span></td></tr></table></div></div>
<div class="card"><h3>paper_sim 净值 (含成本 OOS)</h3>
<div style="height:120px;display:flex;align-items:center;justify-content:center;background:var(--ukb);border-radius:8px;color:var(--uk);font-weight:700">unknown — 第一张可信表 W8 (B-V3)</div></div></div>""")

# ---------- 17 可视化原则 ----------
page("17_viz_principles.html", "Foundations", """
<div style="margin:24px;max-width:960px">
<h1>数据可视化原则 — 真金白银项目的图表纪律</h1>
<table>
<tr><th style="width:200px">原则</th><th>规则</th></tr>
<tr><td><b>1. verdict 即颜色</b></td><td>图表状态色只绑定机器判决字段 (OK/WARN/FAIL/UNKNOWN 四态), 前端不做阈值判断; unknown 灰永不被 0 或绿色冒充</td></tr>
<tr><td><b>2. 数字必可溯源</b></td><td>每个图表角标 evidence 来源 (run id / yaml 路径 / optuna study); 点击穿透到档案或 artifact — "看着合理"的数字不许上屏</td></tr>
<tr><td><b>3. 分布优先于均值</b></td><td>月胜率画分布直方图不画单均值; RankIC 画分年小提琴; 单点数字必带窗口与口径角标</td></tr>
<tr><td><b>4. 等宽数字</b></td><td>全站 tabular-nums (旧 v3 既有纪律), 表格右对齐数字列</td></tr>
<tr><td><b>5. 红线常显</b></td><td>异常好看 = 警报: RankIC&gt;0.3 / 胜率&gt;95% / 年化&gt;100% 的图表自动加红色 leakage 警示边框</td></tr>
<tr><td><b>6. 时间轴标口径</b></td><td>所有时间序列标注 PIT 口径 (signal t / 执行 t+1 / JOIN t-1); UTC 时间戳一律 +8h 标注北京时</td></tr>
</table>
<h1 style="margin-top:24px">标准图组 (按档案类型)</h1>
<table>
<tr><th style="width:200px">档案</th><th>标准图</th></tr>
<tr><td>股票档案</td><td>价格+信号叠加 (svg) · 事件时间线 · 贡献分解条</td></tr>
<tr><td>公式档案</td><td>OOS RankIC 分年箱线 · 信号量月度柱 · 参数来源桑基 (yaml/optuna/measured 三源)</td></tr>
<tr><td>策略档案</td><td>paper_sim 净值+回撤双轴 · 月胜率分布直方 · 换手/成本瀑布</td></tr>
<tr><td>数据域档案</td><td>watermark 新鲜度日历热图 · 行数年度对账条 (min-date vs data_start)</td></tr>
<tr><td>实验档案</td><td>判官仪表 (阈值线+实测点+CI) · 分期同号矩阵</td></tr>
<tr><td>regime 温度计</td><td>炸板率/连板高度双轨带状图, 当前位置高亮</td></tr>
</table></div>""")
