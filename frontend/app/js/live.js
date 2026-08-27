/* ============ dossier mini-tabs ============ */
document.addEventListener("click", e => {
  const b = e.target.closest("#dossier-tabs button");
  if (!b) return;
  const m = b.dataset.mini;
  document.querySelectorAll("#dossier-tabs button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll(".mini-panel").forEach(p => p.classList.toggle("on", p.dataset.mini === m));
});

/* ============ 迷你累计曲线 (sparkline) ============ */
/* 加工层原则: 后端 cum_values/stripe_cum/total_series.cum_values 在场则直用 (cumVals 参数),
   仅在旧后端缺字段时本地累计兜底; 缺日断线不补零 */
function sparkSVG(vals, w, h, stroke, cumVals) {
  if (!vals) return "";
  const hasCum = !!(cumVals && cumVals.length);
  let s = 0; const segs = []; let cur = [];
  vals.forEach((v, i) => {
    const c = hasCum ? cumVals[i]
      : (v === null || v === undefined ? null : (s += v));
    if (c === null || c === undefined) { if (cur.length > 1) segs.push(cur); cur = []; return; }
    cur.push([i, c]);
  });
  if (cur.length > 1) segs.push(cur);
  if (!segs.length) return "";
  const all = segs.flat(), ys = all.map(p => p[1]);
  const mn = Math.min(...ys, 0), mx = Math.max(...ys, 0);
  const X = i => (i / Math.max(1, vals.length - 1)) * (w - 6) + 3;
  const Y = v => (mx === mn ? h / 2 : h - 3 - ((v - mn) / (mx - mn)) * (h - 6));
  let inner = "";
  if (mn < 0 && mx > 0) inner += `<line x1="0" y1="${Y(0).toFixed(1)}" x2="${w}" y2="${Y(0).toFixed(1)}" stroke="currentColor" stroke-opacity="0.3" stroke-width="0.6" stroke-dasharray="2 2"/>`;
  for (const seg of segs)
    inner += `<polyline points="${seg.map(p => X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join(" ")}" fill="none" stroke="${stroke}" stroke-width="1.4"/>`;
  const last = all[all.length - 1];
  inner += `<circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="1.8" fill="${stroke}"/>`;
  const total = ys[ys.length - 1];
  inner += `<title>窗口累计 ${total >= 0 ? "+" : ""}${total.toFixed(1)} 亿 · 在场 ${all.length} 日 · 缺日断线不补零</title>`;
  return inner;
}

/* ============ 资金流趋势大图 (柱=当日 / 线=累计) ============ */
/* cumValues 在场 (后端加工层产出) 则直用, 缺日断柱/断线不补零; 缺省才本地累计兜底 */
function paintFlowCurve(svgId, dates, values, cumValues) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const W = 720, H = 180, mid = 92;
  const vals = values, n = vals.length, bw = W / n;
  const bMax = Math.max(...vals.map(v => Math.abs(v || 0)), 1e-9), bH = 62;
  let cum;
  if (cumValues && cumValues.length) cum = cumValues;
  else { cum = []; let s = 0; for (const v of vals) { s += v || 0; cum.push(s); } }
  const cVals = cum.filter(v => v != null);
  if (!cVals.length) return;
  const cMax = Math.max(...cVals.map(Math.abs), 1e-9), cH = 78;
  let bars = "";
  vals.forEach((v, i) => {
    if (v == null) return;                       // 缺日断柱不补零
    const x = i * bw + bw * 0.18, w = bw * 0.64;
    const h = Math.max(1, Math.abs(v) / bMax * bH);
    const y = v >= 0 ? mid - h : mid;
    bars += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="${v >= 0 ? "var(--in-3)" : "var(--out-3)"}"><title>${dates[i]} ${v >= 0 ? "+" : ""}${v.toFixed(1)}亿</title></rect>`;
  });
  const segs = []; let cur = [];
  cum.forEach((v, i) => {
    if (v == null) { if (cur.length) segs.push(cur); cur = []; return; }
    cur.push(`${(i * bw + bw / 2).toFixed(1)},${(mid - v / cMax * cH).toFixed(1)}`);
  });
  if (cur.length) segs.push(cur);
  const lines = segs.map(seg => `<polyline points="${seg.join(" ")}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>`).join("");
  const lastCum = cVals[cVals.length - 1];
  /* 文字不进 SVG: preserveAspectRatio="none" 会非均匀拉伸字形 —— 累计标签用 HTML overlay */
  svg.innerHTML =
    `<line x1="0" y1="${mid}" x2="${W}" y2="${mid}" stroke="var(--line-2)" stroke-width="1"/>` + bars + lines;
  const box = svg.closest(".curvebox");
  if (box) {
    let tag = box.querySelector(".cv-cum");
    if (!tag) { tag = document.createElement("span"); tag.className = "cv-cum"; box.appendChild(tag); }
    /* svg 在 curvebox 内的纵向起点 ≈ padding18 + cv-head12 + margin10 = 40px; CSS 定高 180px */
    const frac = Math.min(0.94, Math.max(0.03, (mid - lastCum / cMax * cH) / H));
    tag.style.top = `calc(40px + ${(frac * 180).toFixed(0)}px)`;
    tag.style.transform = "translateY(-50%)";
    tag.classList.toggle("up", lastCum >= 0); tag.classList.toggle("dn", lastCum < 0);
    tag.textContent = `窗口累计 ${lastCum >= 0 ? "+" : ""}${lastCum.toFixed(1)}亿`;
  }
}

/* ════════════════════════════════════════════════════════════
   LIVE LAYER —— 后端现查；失败 = typed empty，不回落打样
   ════════════════════════════════════════════════════════════ */
const OPS = { timer: null };
async function jget(p, ms) {
  const c = new AbortController(); const t = setTimeout(() => c.abort(), ms || 8000);
  try {
    const r = await fetch(p, { signal: c.signal });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } catch (e) {
    if (e && e.name === "AbortError") throw new Error("timeout");
    throw e;
  } finally { clearTimeout(t); }
}
async function jpost(p, body) {
  const r = await fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}
function esc(s) { return String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function typedEmpty(title, msg) {
  return `<div class="typed-empty" style="margin-top:40px"><span class="lamp unk"><i></i><b>${esc(title)}</b></span><p>${msg}</p></div>`;
}
function bucket(net) {
  const a = Math.abs(net || 0);
  const lvl = a >= 50 ? 4 : a >= 20 ? 3 : a >= 5 ? 2 : 1;
  return ((net || 0) >= 0 ? "i" : "o") + lvl;
}

async function liveMatrix() {
  const body = document.getElementById("matrix-body");
  const anchor = document.getElementById("matrix-anchor");
  if (!body) return;
  try {
    const m = await jget("/api/v3/ops/matrix", 15000);
    const groups = m.groups || [];
    const flags = Object.keys(m.alert_flags || {}).filter(k => m.alert_flags[k]);
    if (anchor) {
      anchor.innerHTML = `
        <span class="cell"><span class="lamp ${m.status === "ok" ? "ok" : "unk"}"><i></i></span>交易日锚 <b>${esc(m.today || "—")}</b></span>
        <span class="cell">源水位 <b>${m.n_domains ?? 0} 域</b></span>
        <span class="cell"><span class="lamp ${flags.length || m.n_alerts ? "hole" : "ok"}"><i></i></span>告警 <b>${m.n_alerts ?? flags.length}</b>${flags.length ? "&nbsp;<span style='color:var(--ink-3)'>" + esc(flags.join(" / ")) + "</span>" : ""}</span>
        <span class="spacer"></span>
        <button class="btn" data-ops-start="daily_update">一键日更 →</button>`;
    }
    if (!groups.length) {
      body.innerHTML = typedEmpty("EMPTY", `没有可读的水位快照。<span class="mono">${esc(m.source || "watermark SLA")}</span>`);
      return;
    }
    let html = `<div class="matrix"><div class="matrix-head"><span>域 / DOMAIN</span><span>前沿</span><span>滞后</span><span>SLA</span><span>STATUS</span><span>模式</span><span>备注</span></div>`;
    for (const g of groups) {
      html += `<div class="mgroup"><span>${esc(g.label)}</span><span class="n">${g.n}</span></div>`;
      for (const d of (g.domains || [])) {
        html += `<div class="mrow" data-nav="foundation/domain" data-domain="${esc(d.domain)}">
          <span class="name">${esc(d.domain)}<span class="cn">${esc(d.cn)}</span></span>
          <span class="frontier">${esc(d.watermark || "—")}</span>
          <span class="lag">${d.days_ago ?? "—"}</span>
          <span class="sla">${d.sla_days ?? "—"}</span>
          <span class="lamp ${esc(d.lamp || "unk")}"><i></i>${esc(d.status || "—")}</span>
          <span class="mode">${esc(d.mode || "—")}</span>
          <span class="note">${esc(d.note || "")}</span></div>`;
      }
    }
    html += `</div><p class="mono" style="margin-top:12px;font-size:10px;color:var(--ink-3)">● LIVE · ${esc(m.source || "")} · as-of ${esc(m.as_of || "—")}</p>`;
    body.innerHTML = html;
  } catch (e) {
    if (anchor) {
      anchor.innerHTML = `<span class="cell">后端未连接</span><span class="spacer"></span>
        <button class="btn" data-ops-start="daily_update">一键日更 →</button>`;
    }
    body.innerHTML = typedEmpty("UNAVAILABLE", `矩阵现查失败，不回落打样域。<span class="mono">${esc(e.message)}</span>`);
  }
}

async function liveDomain(rawKey) {
  const key = String(rawKey || "").split(" /")[0].trim();
  const $ = id => document.getElementById(id);
  if (!$("d-kpis")) return;
  $("d-crumbs").innerHTML = `<button class="crumb" data-nav="foundation/matrix">矩阵<span class="lv">ROOT</span></button><span class="crumb-sep">›</span><span class="crumb cur">${esc(key || "—")}</span>`;
  $("d-kicker").textContent = "Domain";
  $("d-title").textContent = key || "—";
  $("d-sub").textContent = "正在读取水位…";
  if (!key) {
    $("d-sub").textContent = "缺少 ?domain=";
    $("d-kpis").innerHTML = "";
    $("d-heat").innerHTML = typedEmpty("EMPTY", "未指定域。");
    $("d-legend").innerHTML = "";
    $("d-checks").innerHTML = "";
    $("d-contract").innerHTML = "";
    return;
  }
  try {
    const d = await jget("/api/v3/ops/matrix/" + encodeURIComponent(key), 15000);
    const item = d.item;
    if (!item) {
      $("d-kicker").textContent = "Domain / 水位未收录";
      $("d-sub").textContent = "SLA 快照里没有这一域。";
      $("d-kpis").innerHTML = "";
      $("d-heat").innerHTML = "";
      $("d-legend").innerHTML = "";
      $("d-heat-title").textContent = "分区日历 · 未接入";
      $("d-checks").innerHTML = typedEmpty("UNVERIFIED", `没有 ${esc(key)} 的文件水位。<span class="mono">${esc(d.source || "")}</span>`);
      $("d-contract").innerHTML = "";
      return;
    }
    $("d-crumbs").innerHTML =
      `<button class="crumb" data-nav="foundation/matrix">矩阵<span class="lv">ROOT</span></button><span class="crumb-sep">›</span>` +
      `<span class="crumb" style="cursor:default">${esc(item.group)}</span><span class="crumb-sep">›</span>` +
      `<span class="crumb cur">${esc(item.domain)}<span class="lv">${esc(item.mode)}</span></span>` +
      `<span style="margin-left:auto; font-family:var(--mono); font-size:10px; color:var(--ink-3)">as-of ${esc(d.as_of || "—")}</span>`;
    $("d-kicker").textContent = "Domain / " + (item.group || "");
    $("d-title").innerHTML = `${esc(item.domain)} <span style="font-size:0.42em; font-weight:400; color:var(--ink-3);">${esc(item.cn)}</span>`;
    $("d-sub").textContent = item.note || (item.status || "");
    $("d-kpis").innerHTML = `
      <div class="kpi"><div class="k-label">前沿 WATERMARK</div><div class="k-val">${esc(item.watermark || "—")}</div><div class="k-sub">SLA ${item.sla_days ?? "—"} 日</div></div>
      <div class="kpi"><div class="k-label">滞后 DAYS AGO</div><div class="k-val">${item.days_ago ?? "—"}</div><div class="k-sub">${esc(item.status || "")}</div></div>
      <div class="kpi"><div class="k-label">灯 LAMP</div><div class="k-val"><span class="lamp ${esc(item.lamp)}"><i></i>${esc(item.lamp)}</span></div><div class="k-sub">${item.alert ? "alert" : "—"}</div></div>`;
    $("d-heat-title").textContent = "分区日历 · 本投影不画 DuckDB 位图";
    $("d-heat").innerHTML = typedEmpty("UNVERIFIED", "分区位图需要读库；日更占写锁时本页只展示文件水位，不补画、不猜测。");
    $("d-legend").innerHTML = "";
    $("d-checks").innerHTML = `<div class="typed-empty" style="margin-top:8px"><span class="lamp ${esc(item.lamp)}"><i></i><b>${esc(item.status || "—")}</b></span>
      <p>status=${esc(item.status || "—")} · sla_axis=${esc(item.mode || "—")} · days_ago=${item.days_ago ?? "—"}
      <span class="mono">${esc(d.source || "")}</span></p></div>`;
    $("d-contract").innerHTML = `<h4>SOURCE</h4>
      <div class="ev-line"><span>data_domain</span> · ${esc(item.domain)}</div>
      <div class="ev-line"><span>watermark</span> · ${esc(item.watermark || "—")}</div>
      <div class="ev-line"><span>sla</span> · ${item.sla_days ?? "—"} · ${esc(item.mode || "—")}</div>`;
  } catch (e) {
    $("d-sub").textContent = "";
    $("d-kpis").innerHTML = "";
    $("d-heat").innerHTML = "";
    $("d-legend").innerHTML = "";
    $("d-checks").innerHTML = typedEmpty("UNAVAILABLE", `域详情现查失败，不回落打样日历。<span class="mono">${esc(e.message)}</span>`);
    $("d-contract").innerHTML = "";
  }
}
function renderDomain(rawKey) { liveDomain(rawKey); }

function fmtDur(s) {
  if (s == null) return "—";
  if (s < 60) return s.toFixed(1) + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m " + String(Math.round(s % 60)).padStart(2, "0") + "s";
  return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
}

/* ---------- OPS 工作台 ---------- */
const PHASES = ["preflight", "acquire", "clean", "process", "store"];
const PHASE_CN = { preflight: "① PREFLIGHT 预检", acquire: "② ACQUIRE 获取", clean: "③ CLEAN 清洗", process: "④ PROCESS 加工", store: "⑤ STORE 存储/治理" };
function phasePct(phase, active, outcome) {
  if (!active) {
    if (["success", "soft_waiting_clock", "integrity_observe"].includes(outcome)) return 100;
    if (outcome === "hard_fail") return 35;
    return 0;
  }
  const i = PHASES.indexOf(phase);
  return i < 0 ? 8 : Math.min(96, Math.round(((i + 0.45) / PHASES.length) * 100));
}
function clsLog(l) {
  if (/delta_manifest/i.test(l)) return "tl-delta";
  if (/PREFLIGHT BLOCK|AUTH BLOCK|TIER0 BLOCK|WRITER BLOCK|FAIL rc=[2-5]|HARD_FAIL|check_fail/i.test(l)) return "tl-fail";
  if (/soft_waiting_clock|SOFT_WAITING|pending_publish|DONE soft_waiting|integrity_observe/i.test(l)) return "tl-soft";
  if (/①\s*获取|ACQUIRE|②\s*清洗|CLEAN|③\s*加工|PROCESS|④\s*存储|Preflight|stage_status|DONE/i.test(l)) return "tl-stage";
  return "";
}
const LA_FORM_HTML = `
  <div class="la-form" id="la-form">
    <div><label>DOMAIN</label><select id="la-domain"><option value="daily">daily</option><option value="stock_st">stock_st</option></select></div>
    <div><label>MODE</label><select id="la-mode"><option value="land_then_accept">land_then_accept</option><option value="land_only">land_only</option><option value="accept_from_landing">accept_from_landing</option></select></div>
    <div><label>BATCH_ID（accept 模式必填）</label><input id="la-batch" placeholder="YYYYMMDD_…"></div>
    <div><label>START</label><input id="la-start" placeholder="YYYYMMDD"></div>
    <div><label>END</label><input id="la-end" placeholder="YYYYMMDD"></div>
    <div class="la-check"><input type="checkbox" id="la-local"><span>from_local_raw 用本地原料</span></div>
    <div class="la-result"><button class="nc-run" data-ops="land-accept">▶ 运行 S1/S2</button> <span id="la-msg"></span></div>
  </div>`;
function renderOps(s, nodesResp, healthResp) {
  const body = document.getElementById("ops-body"); if (!body) return;
  const active = !!(s.writer_busy || s.process_hint_running || s.running);
  const act = s.current_activity || null;
  const outcome = s.run_outcome || null;
  const ocCls = outcome === "hard_fail" ? "hard" : (outcome === "success" ? "ok" : "soft");
  const ocLamp = outcome === "hard_fail" ? "hard" : (outcome === "success" ? "ok" : "hole");
  /* 后端 progress_pct 在场直用 (加工层原则); 旧后端缺字段才本地推断兜底 */
  const pct = act && act.progress_pct != null ? act.progress_pct
    : phasePct(act && act.phase, active, outcome);
  const flags = Object.entries(s.alert_flags || {}).filter(([, v]) => v).map(([k]) => k);
  const summary = active && act ? act.summary
    : outcome ? `${s.run_outcome_label || outcome}${s.run_outcome_reason ? " — " + s.run_outcome_reason : ""}`
    : (act ? act.summary : "空闲 · 尚无日志");

  const steps = PHASES.map(ph => {
    const dur = s.stage_timing_s && s.stage_timing_s[ph] != null ? fmtDur(s.stage_timing_s[ph]) : (ph === "preflight" ? "内嵌" : "—");
    const b = s.budget_status && s.budget_status[ph];
    const chip = b ? `<span class="budget-chip ${b === "pass" ? "pass" : "fail"}">${esc(b.toUpperCase())}</span>` : `<span class="budget-chip pass">${ph === "preflight" ? "内嵌" : "—"}</span>`;
    const cur = active && act && act.phase === ph;
    return `<div class="step ${b && b !== "pass" ? "warn" : ""} ${cur ? "cur" : ""}"><span class="sdot"></span>
      <div class="s-name">${PHASE_CN[ph]}${cur ? " <span class='lamp soft'><i></i>当前</span>" : ""}</div>
      <div class="s-dur">${dur}</div><div class="s-note">${cur && act.progress_line ? esc(act.progress_line.slice(0, 80)) : ""}</div>${chip}</div>`;
  }).join("");

  const dueItems = (s.due_plan && s.due_plan.items) || [];
  const dueRows = dueItems.map(it => {
    const st = it.status || it.action || "—";
    const will = it.will_fetch ? "会" : "否" + (it.kind === "period_incremental" ? " · 披露钟" : "");
    const det = it.detail ? " · " + String(it.detail).slice(0, 48) : "";
    return `<div class="lvrow" style="grid-template-columns:150px 100px 70px 60px 1fr 90px; cursor:default">
      <span class="lv-name">${esc(it.domain)}</span><span class="lv-num">${esc(it.watermark || "—")}</span>
      <span class="lv-num ${it.sla_days != null && it.days_ago > it.sla_days ? "dn" : ""}">${it.days_ago ?? "—"}</span>
      <span class="lv-num">${it.sla_days ?? "—"}</span>
      <span class="lv-note" style="${st === "STALE_WATERMARK" ? "color:var(--hole)" : ""}">${esc(st)}${esc(det)}</span>
      <span class="lv-note">${will}</span></div>`;
  }).join("");

  const dm = s.delta_manifest || s.delta_manifest_live;
  let deltaHtml = "";
  if (dm) {
    const inc = (dm.acquire_summary && dm.acquire_summary.incremental) || dm.incremental || [];
    const adv = (dm.delta && dm.delta.advanced_partitions) || [];
    deltaHtml = `<div class="sec-title" style="margin-top:64px">增量清单 · DELTA MANIFEST —— ${esc(dm.run_date || s.report_date || "")} · 推进分区 ${adv.length} · 增量动作 ${inc.length}</div>
      <div class="dtable">${inc.slice(0, 12).map(r => `<div class="msg-row"><span class="cls" style="color:var(--ink-3)">${esc(r.domain || "—")}</span><span class="txt">${esc(r.action || "")} → ${esc(r.status || "")}${r.report_date ? " · " + esc(r.report_date) : ""}${r.written != null ? " · +" + r.written + " 行" : ""}</span><span class="ev">${esc(r.next_period ? "下期 " + r.next_period : (r.message || ""))}</span></div>`).join("") || '<div class="msg-row"><span class="txt">本轮无增量动作记录</span></div>'}</div>`;
  }

  const logLines = (s.log_tail || []).slice(-24);
  const logHtml = `<div class="term-head" style="margin-top:64px"><span>链日志尾部 · LOG TAIL</span><span>${esc(s.log_path || "")} · ${logLines.length} 行</span></div>
    <div class="term">${logLines.map(l => `<div class="${clsLog(l)}">${esc(l)}</div>`).join("")}</div>`;

  const nodes = (nodesResp && nodesResp.nodes) || [];
  const nodeCards = nodes.map(n => {
    const disabled = !n.runnable;
    const st = n.status || {};
    const nActive = !!st.process_hint_running;
    const lamp = disabled ? `<span class="lamp unk"><i></i>内嵌</span>`
      : nActive ? `<span class="lamp soft"><i></i>运行中</span>`
      : st.alert_summary ? `<span class="lamp hole"><i></i>告警</span>` : `<span class="lamp ok"><i></i>就绪</span>`;
    let foot = `<span class="ops-note">${n.job ? "job=" + esc(n.job) : "不可单独触发"}</span>`;
    if (n.job && !n.parameterized) foot += `<button class="nc-run" data-ops="run:${esc(n.job)}">▶ 运行</button>`;
    if (n.parameterized) foot += `<span class="ops-note">需参数 →</span>`;
    return `<div class="nodecard ${disabled ? "disabled" : ""}">
      <div class="nc-name">${esc(n.id)} ${lamp}</div>
      <div class="nc-desc">${esc(n.label || "")} · ${esc(n.description || "")}${n.disabled_reason ? " — " + esc(n.disabled_reason) : ""}</div>
      <div class="nc-foot">${foot}</div>${n.parameterized ? LA_FORM_HTML : ""}</div>`;
  }).join("");

  const classified = ((healthResp && healthResp.classified) || []).slice(0, 8).map(row => {
    const msg = typeof row === "string" ? row : (row.msg || row.reason || JSON.stringify(row));
    const cls = typeof row === "object" ? (row.cls || row.kind || "") : "";
    return `<div class="msg-row"><span class="cls">${esc(cls)}</span><span class="txt">${esc(msg)}</span></div>`;
  }).join("");
  const healthHtml = healthResp ? `<div class="sec-title" style="margin-top:64px">健康 · RUNTIME —— 告警旗与最近一次日更</div>
    <div class="trust-row">${(healthResp.checks || []).map(c =>
      `<span class="lamp ${esc(c.lamp || "unk")}"><i></i>${esc(c.label)} <b>${esc(c.state || "")}</b></span>`
    ).join("")}</div>
    ${classified ? `<div class="dtable" style="margin-top:16px">${classified}</div>` : ""}` : "";

  body.innerHTML = `
    <div class="outcome-banner ${ocCls}" style="margin-top:40px">
      <div>
        <div class="ob-state">${active ? "<span class='lamp soft' style='font-size:15px'><i></i></span>&nbsp;RUNNING" : `<span class="lamp ${ocLamp}" style="font-size:15px"><i></i></span>&nbsp;${esc(outcome || "idle")}`}</div>
        <div class="ob-sub">${esc(summary)}${flags.length ? `<br><b>${flags.length} 面 ALERT flag 在挂</b>` : ""}${act && act.stale_log ? `<br><b>日志 &gt;90s 无新行</b>` : ""}</div>
      </div>
      <div class="ob-meta">
        report_date = ${esc(s.report_date || "—")}<br>
        writer = ${active ? esc(s.owner || "?") + " · pid " + (s.owner_pid ?? "?") : "空闲"}<br>
        log = ${esc(s.log_path || "—")}
      </div>
    </div>
    <div class="ops-progress">
      <div class="ops-bar"><div class="ops-fill ${active ? "pulse" : ""}" style="width:${pct}%"></div></div>
      <span class="mono" style="font-size:10.5px; color:var(--ink-3)">${active ? `阶段 ${Math.max(1, PHASES.indexOf(act && act.phase) + 1)}/5 · ${pct}%` : (pct === 100 ? "已结束" : "非运行中")}</span>
    </div>
    <div class="stepper">${steps}</div>
    <div class="sec-title" style="margin-top:64px">跑前预览 · DUE PLAN —— ${esc((s.due_plan && s.due_plan.source) || "SLA 快照")} · as-of ${esc((s.due_plan && s.due_plan.as_of) || "—")}</div>
    <div class="dtable">
      <div class="lvrow head" style="grid-template-columns:150px 100px 70px 60px 1fr 90px"><span>域</span><span>水位</span><span>滞后天</span><span>SLA</span><span>判定</span><span>会抓吗</span></div>
      ${dueRows || '<div class="msg-row"><span class="txt">due_plan 为空</span></div>'}
    </div>
    ${deltaHtml}
    ${healthHtml}
    <div class="sec-title" style="margin-top:64px">分步流水线 · NODES —— 各段独立触发 · preflight 不可独立跑</div>
    <div class="nodes">${nodeCards}</div>
    ${logHtml}
`;
}
function setOpsNote(live, extra) {
  const n = document.getElementById("ops-note"); if (!n) return;
  n.classList.toggle("live", !!live);
  n.textContent = live ? ("● LIVE" + (extra || "")) : "○ 后端未连接";
}
async function opsRefresh() {
  try {
    const [s, ns, h] = await Promise.all([
      jget("/api/v3/ops/jobs/daily_update"),
      jget("/api/v3/ops/pipeline/nodes"),
      jget("/api/v3/ops/health").catch(() => null),
    ]);
    renderOps(s, ns, h);
    const active = !!(s.writer_busy || s.process_hint_running || s.running);
    setOpsNote(true, active ? " · 活动 · 2.5s 轮询" : " · 空闲");
    clearInterval(OPS.timer);
    if (active) OPS.timer = setInterval(opsRefresh, 2500);
  } catch (e) {
    setOpsNote(false);
    const body = document.getElementById("ops-body");
    if (body) body.innerHTML = typedEmpty("UNAVAILABLE", `日更状态现查失败，不回落打样运行。<span class="mono">${esc(e.message)}</span>`);
  }
}
document.addEventListener("click", async e => {
  const start = e.target.closest("[data-ops-start]");
  if (start) {
    e.preventDefault();
    start.disabled = true;
    try { await jpost("/api/v3/ops/jobs/" + start.dataset.opsStart + "/run"); }
    catch (err) { /* 409 写锁占用也进日更页看进度 */ }
    if (window.CM && window.CM.goto) window.CM.goto("foundation/ops");
    else location.href = "/app/foundation/ops.html";
    return;
  }
  const b = e.target.closest("[data-ops]"); if (!b) return;
  const cmd = b.dataset.ops;
  if (cmd === "refresh") { opsRefresh(); return; }
  if (cmd.startsWith("run:")) {
    b.disabled = true;
    try { await jpost("/api/v3/ops/jobs/" + cmd.slice(4) + "/run"); setTimeout(opsRefresh, 900); }
    catch (err) { alert("触发失败: " + err.message); }
    finally { b.disabled = false; }
    return;
  }
  if (cmd === "land-accept") {
    const g = id => document.getElementById(id);
    const msg = g("la-msg"); if (!msg) return;
    const body = { domain: g("la-domain").value, mode: g("la-mode").value };
    if (body.mode === "accept_from_landing") {
      const bid = g("la-batch").value.trim();
      if (!bid) { msg.textContent = "accept_from_landing 需 batch_id"; return; }
      body.batch_id = bid;
    } else {
      const st = g("la-start").value.trim(), en = g("la-end").value.trim();
      if (!/^\d{8}$/.test(st) || !/^\d{8}$/.test(en)) { msg.textContent = "start / end 需 YYYYMMDD"; return; }
      body.start = st; body.end = en;
    }
    if (g("la-local").checked) body.from_local_raw = true;
    msg.textContent = "提交中…";
    try {
      const r = await jpost("/api/v3/ops/pipeline/land-accept/run", body);
      msg.textContent = `accepted · pid=${r.pid} · argv=${(r.argv || []).join(" ")}`;
      setTimeout(opsRefresh, 900);
    } catch (err) { msg.textContent = "失败: " + err.message; }
  }
});

/* ---------- INSIGHT 实时层 ---------- */
const REGIME_CN = { surge_in: "脉冲流入", accum_in_silent: "横盘累积流入", accum_in_driving: "上行累积流入", surge_out: "脉冲流出", accum_out_silent: "横盘累积流出", accum_out_driving: "下行累积流出", neutral: "无显著形态" };
const CHAIN_CN = { sw_industry: "申万行业", dc_concept: "东财概念", dc_industry: "东财行业" };
const YI = v => v == null ? null : v / 1e8;
const FLOW = { chain: "dc_concept", win: 20 };
window.DRILL = window.DRILL || { chain: "sw_industry", code: null };
function regimeChip(r) {
  const cls = r && r !== "neutral" ? (r.indexOf("_in") >= 0 ? "in" : "out") : "";
  return `<span class="regime ${cls}">${REGIME_CN[r] || r || "—"}</span>`;
}
const fmtPct = v => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
const fmtYi = v => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1) + " 亿";
const clsSign = v => v == null ? "" : (v >= 0 ? "up" : "dn");

async function liveMarket() {
  try {
    const [sen, dr, hm] = await Promise.all([
      jget("/api/v3/pulse/sentiment?days=30"),
      jget("/api/v3/pulse/drill?chain=sw_industry"),
      jget("/api/v3/pulse/heatmap?chain=sw_industry&level=L1&days=20")]);
    const days = sen.days || sen.rows || [];
    const d = days[days.length - 1]; if (!d) throw new Error("no sentiment");
    const kpi = document.querySelector('[data-tab="market"] .kpi-strip');
    if (kpi) kpi.innerHTML = `
      <div class="kpi"><div class="k-label">涨停 LIMIT UP</div><div class="k-val">${d.limit_up_total ?? "—"}</div><div class="k-sub">跌停 ${d.limit_down_total ?? "—"} · 炸板率 ${d.zha_ban_rate == null ? "—" : (d.zha_ban_rate * 100).toFixed(1) + "%"}</div></div>
      <div class="kpi"><div class="k-label">涨跌比 ADV/DEC</div><div class="k-val">${d.adv_dec_ratio == null ? "—" : d.adv_dec_ratio.toFixed(2)}</div><div class="k-sub">${d.adv_dec_ratio == null ? "" : (d.adv_dec_ratio >= 1 ? "涨多跌少" : "跌多涨少 · 偏弱")}</div></div>
      <div class="kpi"><div class="k-label">连板高度 STREAK</div><div class="k-val">${d.max_limit_times ?? "—"}</div><div class="k-sub">晋级率 ${d.promotion_rate == null ? "—" : (d.promotion_rate * 100).toFixed(1) + "%"}</div></div>
      <div class="kpi"><div class="k-label">主力净流入 NET</div><div class="k-val ${clsSign(YI(d.mkt_net_amount))}">${YI(d.mkt_net_amount) == null ? "—" : (YI(d.mkt_net_amount) >= 0 ? "+" : "") + YI(d.mkt_net_amount).toFixed(1)}<span style="font-size:14px">亿</span></div><div class="k-sub">vendor imbalance 代理, 非守恒资金</div></div>
      <div class="kpi"><div class="k-label">两融余额 MARGIN</div><div class="k-val">${d.rzrqye == null ? "—" : (d.rzrqye / 1e12).toFixed(2)}<span style="font-size:14px;color:var(--ink-3)">万亿</span></div><div class="k-sub">日变动 <span class="${clsSign(YI(d.rzrqye_chg))}">${fmtYi(YI(d.rzrqye_chg))}</span></div></div>
      <div class="kpi"><div class="k-label">PE(TTM) / 换手</div><div class="k-val">${d.mkt_pe == null ? "—" : d.mkt_pe.toFixed(2)}</div><div class="k-sub">换手率 ${d.mkt_turnover == null ? "—" : d.mkt_turnover.toFixed(2) + "%"}</div></div>
      <div class="kpi"><div class="k-label">龙虎榜 DRAGON</div><div class="k-val">${d.lhb_count ?? "—"}<span style="font-size:14px;color:var(--ink-3)">家</span></div><div class="k-sub">机构净买 <span class="${clsSign(YI(d.lhb_inst_net))}">${fmtYi(YI(d.lhb_inst_net))}</span></div></div>`;
    const bn = document.getElementById("market-banner");
    if (bn) {
      const flowDate = (dr.rows && dr.rows[0] && dr.rows[0].trade_date) || "—";
      bn.innerHTML = `<span class="lamp soft"><i></i><b>STALE</b></span>
        <span style="font-size:12.5px; color:var(--ink-2); line-height:1.7">
          本页读 <b>两张 mart</b>，新鲜度各自标注、互不借用。<br>
          情绪快照停在 <span class="mono">${esc(d.trade_date)}</span>；板块资金停在 <span class="mono">${esc(flowDate)}</span>。
        </span>
        <span class="mono" style="margin-left:auto; white-space:nowrap">KPI as-of ${esc(d.trade_date)}<br>FLOW as-of ${esc(flowDate)}</span>`;
    }
    const series = {};
    (hm.sectors || []).forEach(s => {
      series[s.sector_code] = { vals: (s.values || []).map(YI), cum: (s.cum_values || []).map(YI) };
    });
    const rows = (dr.rows || []).slice().sort((a, b) => (b.net_amount ?? 0) - (a.net_amount ?? 0));
    const grid = document.getElementById("flowgrid");
    if (grid) grid.innerHTML = rows.map(r => {
      const net = YI(r.net_amount);
      const sr = series[r.sector_code] || null;
      const sub = `${fmtPct(r.pct_change)}${r.rs_rank_4w ? ` · rs#${r.rs_rank_4w}` : ""}${r.flow_streak ? ` · streak ${r.flow_streak > 0 ? "+" : ""}${r.flow_streak}` : ""}${r.flow_regime && r.flow_regime !== "neutral" ? " · " + (REGIME_CN[r.flow_regime] || "") : ""}`;
      return `<div class="fcell ${bucket(net)}" data-nav="insight/sector" data-chain="sw_industry" data-code="${esc(r.sector_code)}" title="点击下钻 ${esc(r.sector_name)}">
        <span class="f-name">${esc(r.sector_name)}</span>
        <svg class="f-spark" viewBox="0 0 150 20" preserveAspectRatio="none">${sr ? sparkSVG(sr.vals, 150, 20, "currentColor", sr.cum) : ""}</svg>
        <span><span class="f-net">${net == null ? "—" : (net >= 0 ? "+" : "") + net.toFixed(1) + "亿"}</span>
        <span class="f-sub">${sub} · 曲线=20日累计</span></span></div>`;
    }).join("");
  } catch (e) {
    const kpi = document.querySelector('[data-tab="market"] .kpi-strip');
    if (kpi) kpi.innerHTML = typedEmpty("UNAVAILABLE", `市场 KPI 现查失败，不回落打样数字。<span class="mono">${esc(e.message)}</span>`);
    const grid = document.getElementById("flowgrid");
    if (grid) grid.innerHTML = typedEmpty("UNAVAILABLE", "行业资金流现查失败。");
    const bn = document.getElementById("market-banner");
    if (bn) bn.innerHTML = `<span class="lamp unk"><i></i><b>UNAVAILABLE</b></span><span style="font-size:12.5px;color:var(--ink-2)">后端不可达，不回落打样 as-of。</span>`;
  }
  /* 连板梯队: 独立取数, 失败不影响上方 KPI/热力 */
  try {
    const st = await jget("/api/v3/pulse/strongest");
    const lad = document.getElementById("ladder");
    if (lad) {
      if (st && st.sectors && st.sectors.length) {
        const lv = r => r.rank === 1 ? 4 : r.rank <= 3 ? 3 : r.rank <= 8 ? 2 : 1;
        lad.innerHTML = st.sectors.map(r => {
          const boards = Math.min(8, +((String(r.up_stat || "").match(/(\d+)\s*板/) || [])[1] || 1));
          const neg = (r.pct_chg ?? 0) < 0;
          const col = `var(--${neg ? "out" : "in"}-${lv(r)})`;
          const blocks = Array.from({ length: 8 }, (_, i) => i < boards ? `<i style="background:${col}"></i>` : `<i class="off"></i>`).join("");
          return `<div class="lrow">
          <span class="rank">${r.rank}</span>
          <span class="l-name">${esc(r.name)}</span>
          <span class="l-stat">${esc(r.up_stat || "—")} · 连板 ${esc(r.cons_nums ?? "—")} 家 · 板块内涨停 ${r.up_nums ?? "—"} 家 · <span class="${clsSign(r.pct_chg)}">${fmtPct(r.pct_chg)}</span></span>
          <span class="lblocks">${blocks}</span></div>`;
        }).join("") + `<p style="margin-top:10px; font-family:var(--mono); font-size:10px; color:var(--ink-3)">● LIVE · pulse/strongest · trade_date ${esc(st.trade_date || "—")} · 方块数=连板高度(≤8) · 色阶=名次(越深越强) · 红=当日涨 / 绿=当日跌</p>`;
      } else {
        lad.innerHTML = typedEmpty("0 rows", "当前无连板梯队。");
      }
    }
  } catch (e) {
    const lad = document.getElementById("ladder");
    if (lad) lad.innerHTML = typedEmpty("UNAVAILABLE", "连板梯队现查失败，不回落打样梯队。");
  }
  try {
    const rot = await jget("/api/v3/pulse/rotation?chain=sw_industry&level=L1&lag=5");
    const rw = document.getElementById("rotationwrap");
    if (rw) {
      const secs = (rot.sectors || []).filter(s => s.rs_rank_4w != null);
      const movers = secs.slice().sort((a, b) => {
        const da = (a.prev_rs_rank_4w ?? a.rs_rank_4w) - a.rs_rank_4w;
        const db = (b.prev_rs_rank_4w ?? b.rs_rank_4w) - b.rs_rank_4w;
        return Math.abs(db) - Math.abs(da);
      }).slice(0, 12);
      if (!movers.length) {
        rw.innerHTML = `<div class="typed-empty"><span class="lamp unk"><i></i><b>0 rows</b></span>
          <p>当前无可用 RS 排名迁移。<span class="mono">pulse/rotation · sw L1 · lag=5</span></p></div>`;
      } else {
        rw.innerHTML = `<div class="dtable" style="margin-top:20px">
          <div class="lvrow head" style="grid-template-columns:72px 1fr 90px 90px 90px 110px"><span>现排名</span><span>板块</span><span>上周</span><span>迁移</span><span>RS 4w</span><span></span></div>
          ${movers.map(s => {
            const prev = s.prev_rs_rank_4w;
            const delta = (prev == null || s.rs_rank_4w == null) ? null : prev - s.rs_rank_4w;
            const arrow = delta == null ? "—" : (delta > 0 ? "↑" + delta : delta < 0 ? "↓" + Math.abs(delta) : "0");
            return `<div class="lvrow" style="grid-template-columns:72px 1fr 90px 90px 90px 110px;cursor:pointer" data-nav="insight/sector" data-chain="sw_industry" data-code="${esc(s.sector_code)}">
              <span class="lv-num">${s.rs_rank_4w ?? "—"}</span>
              <span class="lv-name">${esc(s.sector_name)}</span>
              <span class="lv-num">${prev ?? "—"}</span>
              <span class="lv-num ${delta > 0 ? "up" : delta < 0 ? "dn" : ""}">${arrow}</span>
              <span class="lv-num">${s.rs_4w == null ? "—" : (s.rs_4w >= 0 ? "+" : "") + Number(s.rs_4w).toFixed(1) + "%"}</span>
              <span class="lv-note">点行下钻</span></div>`;
          }).join("")}
        </div>
        <p style="margin-top:10px; font-family:var(--mono); font-size:10px; color:var(--ink-3)">● LIVE · pulse/rotation · ${esc(rot.latest_date || "—")} vs ${esc(rot.prev_date || "—")} · 排名数字越小越强 · 上升=名次变好 · 零买卖暗示</p>`;
      }
    }
  } catch (e) {
    const rw = document.getElementById("rotationwrap");
    if (rw) rw.innerHTML = typedEmpty("UNAVAILABLE", "轮动现查失败，不编造迁移。");
  }
  /* 潜伏象限: 两链 flow_board 的 accum_in_silent (价稳+连续净流入=悄悄流入), 自带 60 日 stripe */
  try {
    const [qi, qc] = await Promise.all([
      jget("/api/v3/pulse/flow_board?chain=dc_industry&limit=50&stripe_days=60"),
      jget("/api/v3/pulse/flow_board?chain=dc_concept&limit=50&stripe_days=60")]);
    const qw = document.getElementById("quietwrap");
    if (qw) {
      const sil = [...(qi.inflow || []), ...(qc.inflow || [])].filter(r => r.flow_regime === "accum_in_silent");
      const asof = qi.trade_date || qc.trade_date || "—";
      if (!sil.length) {
        qw.innerHTML = `<div class="typed-empty"><span class="lamp unk"><i></i><b>0 rows</b></span>
          <p>当前两链 (东财行业/东财概念) 均无 <span class="mono" style="display:inline">flow_regime = accum_in_silent</span> 的板块。<br>
          这是<b>条件型空态</b>：不是没数据，是没有板块满足「价稳 + 连续净流入」。空态本身即信息。
          <span class="mono">query: flow_board inflow · regime=accum_in_silent · as-of ${esc(asof)}</span></p></div>`;
      } else {
        const max = Math.max(...sil.map(r => Math.abs(YI(r.cum_net) ?? 0)), 1);
        qw.innerHTML = `<div class="flowgrid">` + sil.map(r => {
          const cum = YI(r.cum_net);
          const t = Math.abs(cum ?? 0) / max;
          const lvl = "i" + (t >= 0.75 ? 4 : t >= 0.45 ? 3 : t >= 0.12 ? 2 : 1);
          return `<div class="fcell ${lvl}" data-nav="insight/sector" data-chain="${esc(r.chain)}" data-code="${esc(r.sector_code)}" title="点击下钻 ${esc(r.sector_name)}">
            <span class="f-name">${esc(r.sector_name)}</span>
            <svg class="f-spark" viewBox="0 0 150 20" preserveAspectRatio="none">${sparkSVG((r.stripe || []).map(YI), 150, 20, "currentColor", (r.stripe_cum || []).map(YI))}</svg>
            <span><span class="f-net">${fmtYi(cum)}</span>
            <span class="f-sub">连续流入 ${r.flow_streak ?? "—"} 日 · 当日 ${fmtPct(r.pct_change)} · ${CHAIN_CN[r.chain] || esc(r.chain)} · 曲线=60日累计</span></span></div>`;
        }).join("") + `</div>
        <p style="margin-top:10px; font-family:var(--mono); font-size:10px; color:var(--ink-3)">● LIVE · accum_in_silent = 横盘累积流入：价格趴在横盘带 (|pct|&lt;1%) 而资金连续同向净流入 —— 行为标签「潜伏迹象」· 20 日累计净额排序 · as-of ${esc(asof)} · 点击卡片下钻</p>`;
      }
    }
  } catch (e) {
    const qw = document.getElementById("quietwrap");
    if (qw) qw.innerHTML = typedEmpty("UNAVAILABLE", "潜伏象限现查失败。");
  }
}

async function liveFlows() {
  try {
    const isSw = FLOW.chain === "sw_industry";
    const lvl = isSw ? "&level=L1" : "";
    const [hm, hw, fb] = await Promise.all([
      jget(`/api/v3/pulse/heatmap?chain=sw_industry&level=L1&days=${FLOW.win}`),
      jget(`/api/v3/pulse/heatmap?chain=${FLOW.chain}${lvl}&days=${FLOW.win}&top=12`),
      jget(`/api/v3/pulse/flow_board?chain=${FLOW.chain}&limit=12&stripe_days=60`)]);
    /* 趋势大图：截面合计由后端 total_series 产出 (划分截面才有; 不受 top 截断),
       旧后端缺字段时退化为 31 板块求和兜底 */
    if (hm.dates && hm.sectors) {
      const tsVals = hm.total_series && hm.total_series.values
        ? hm.total_series.values.map(v => (v == null ? null : YI(v)))
        : hm.dates.map((_, i) => hm.sectors.reduce((a, s) => a + (s.values && s.values[i] != null ? s.values[i] : 0), 0)).map(YI);
      const tsCum = hm.total_series && hm.total_series.cum_values
        ? hm.total_series.cum_values.map(v => (v == null ? null : YI(v))) : null;
      paintFlowCurve("mkttrend", hm.dates, tsVals, tsCum);
      const box = document.getElementById("mkttrend").closest(".curvebox");
      if (box) {
        const hd = box.querySelectorAll(".cv-head span");
        if (hd[1]) hd[1].innerHTML = "窗口: " + [20, 60, 120, 250].map(w => w === FLOW.win ? `<b style="color:var(--ink)">${w}</b>` : w).join(" / ") + " 日";
        const ax = box.querySelectorAll(".cv-axis span");
        if (ax[0]) ax[0].textContent = hm.dates[0];
        if (ax[2]) ax[2].textContent = hm.dates[hm.dates.length - 1];
      }
    }
    /* as-of */
    const asof = document.getElementById("flows-asof");
    if (asof && fb.trade_date) asof.textContent = `as-of ${fb.trade_date} · ${CHAIN_CN[FLOW.chain]} · 缺日=null 不补零`;
    /* 窗口累计瓦片 */
    const win = document.getElementById("flowwin");
    if (win && hw.sectors) {
      const arr = hw.sectors.map(s => ({ name: s.sector_name, code: s.sector_code, cum: YI(s.total_net_amount), vals: (s.values || []).map(YI), cumvals: (s.cum_values || []).map(YI) }))
        .sort((a, b) => (b.cum ?? 0) - (a.cum ?? 0)).slice(0, 12);
      const max = Math.max(...arr.map(r => Math.abs(r.cum ?? 0)), 1);
      win.innerHTML = arr.map(r => {
        const t = Math.abs(r.cum ?? 0) / max;
        const n = t >= 0.75 ? 4 : t >= 0.45 ? 3 : t >= 0.12 ? 2 : 1;
        const lvlCls = ((r.cum ?? 0) >= 0 ? "i" : "o") + n;
        const nDays = (r.vals || []).filter(v => v != null).length;
        return `<div class="fcell ${lvlCls}" data-nav="insight/sector" data-chain="${FLOW.chain}" data-code="${esc(r.code)}" title="点击下钻 ${esc(r.name)}">
          <span class="f-name">${esc(r.name)}</span>
          <svg class="f-spark" viewBox="0 0 150 20" preserveAspectRatio="none">${sparkSVG(r.vals, 150, 20, "currentColor", r.cumvals)}</svg>
          <span><span class="f-net">${(r.cum ?? 0) >= 0 ? "+" : ""}${(r.cum ?? 0).toFixed(1)}亿</span>
          <span class="f-sub">${FLOW.win} 日窗口 · ${nDays}/${FLOW.win} 日在场 · 曲线=累计路径</span></span></div>`;
      }).join("");
    }
    /* 形态榜：重建两表（保留表头行） */
    const fbRow = r => {
      const net = YI(r.net_amount);
      const side = (r.flow_regime || "").indexOf("_in") >= 0 ? "in" : "out";
      const stripe = (r.stripe || []).map(YI);
      const stripeCum = (r.stripe_cum || []).map(YI);
      return `<div class="fb-row" data-nav="insight/sector" data-chain="${esc(r.chain || FLOW.chain)}" data-code="${esc(r.sector_code)}">
        <span class="fb-name">${esc(r.sector_name)}</span><span>${regimeChip(r.flow_regime)}</span>
        <span class="fb-num ${clsSign(r.flow_streak)}">${r.flow_streak == null ? "—" : (r.flow_streak >= 0 ? "+" : "") + r.flow_streak}</span>
        <span class="fb-z">${r.flow_z == null ? "—" : r.flow_z.toFixed(2)}</span>
        <span class="fb-num ${clsSign(net)}">${fmtYi(net)}</span>
        <svg class="spark" viewBox="0 0 220 24" preserveAspectRatio="none">${sparkSVG(stripe, 220, 24, side === "in" ? "var(--in-tx)" : "var(--out-tx)", stripeCum)}</svg></div>`;
    };
    const tblIn = document.getElementById("flow-in");
    const tblOut = document.getElementById("flow-out");
    const headIn = tblIn && tblIn.querySelector(".fb-row");
    const headOut = tblOut && tblOut.querySelector(".fb-row");
    if (tblIn && headIn && fb.inflow) tblIn.innerHTML = headIn.outerHTML + fb.inflow.map(fbRow).join("");
    if (tblOut && headOut && fb.outflow) tblOut.innerHTML = headOut.outerHTML + fb.outflow.map(fbRow).join("");
  } catch (e) {
    const win = document.getElementById("flowwin");
    if (win) win.innerHTML = typedEmpty("UNAVAILABLE", `资金流向现查失败，不回落打样概念。<span class="mono">${esc(e.message)}</span>`);
  }
}

async function liveWarnings() {
  try {
    const w = await jget("/api/v3/pulse/warnings");
    const tbl0 = document.getElementById("warn-rank");
    const tbl1 = document.getElementById("warn-quiet");
    const rd = w.rank_dropouts || [], q = w.quiet_outflows || [];
    const th = w.thresholds || { rank_top: 3, quiet_outflow_days: 3 };
    if (tbl0) {
      const head = tbl0.querySelector(".lvrow.head").outerHTML;
      tbl0.innerHTML = head + (rd.map(r => `<div class="lvrow" style="grid-template-columns:1.2fr 100px 100px 110px 1fr; cursor:default">
        <span class="lv-name">${esc(r.sector_name)}<span class="cc">SW L1</span></span>
        <span class="lv-num">${r.prev_rank} <span style="color:var(--ink-3)">(${String(r.prev_date || "").slice(4)})</span></span>
        <span class="lv-num">${r.latest_rank} <span style="color:var(--ink-3)">(${String(r.latest_date || "").slice(4)})</span></span>
        <span class="lv-num">${r.rs_4w == null ? "—" : r.rs_4w.toFixed(2)}</span>
        <span class="lv-note">阈值 top_n_sectors=${th.rank_top} · 跌出即命中</span></div>`).join("")
        || '<div class="msg-row"><span class="txt">0 rows · 条件型空态：无板块跌出 top ' + th.rank_top + '</span></div>');
    }
    if (tbl1) {
      const head = tbl1.querySelector(".lvrow.head").outerHTML;
      const rows = q.slice(0, 8).map(r => {
        const spark = (r.stripe && r.stripe.length)
          ? sparkSVG(r.stripe.map(v => (v == null ? null : YI(v))), 200, 24, "var(--out-tx)",
                     (r.stripe_cum || []).map(v => (v == null ? null : YI(v))))
          : `<text x="4" y="16" font-size="9" fill="var(--ink-3)">—</text>`;
        return `<div class="lvrow" style="grid-template-columns:1.1fr 80px 100px 100px 200px 90px" data-nav="insight/sector" data-chain="${esc(r.chain)}" data-code="${esc(r.sector_code)}">
          <span class="lv-name">${esc(r.sector_name)}</span><span class="lv-num dn">${r.quiet_outflow_days} 日</span>
          <span class="lv-num ${clsSign(YI(r.net_amount))}">${fmtYi(YI(r.net_amount))}</span>
          <span class="lv-num ${clsSign(r.pct_change)}">${fmtPct(r.pct_change)}</span>
          <svg class="spark" viewBox="0 0 200 24" preserveAspectRatio="none">${spark}</svg>
          <span class="lv-note">${esc(r.chain)}</span></div>`;
      });
      tbl1.innerHTML = head + (rows.join("") || '<div class="msg-row"><span class="txt">0 rows · 条件型空态：无连续悄悄流出 ≥ ' + th.quiet_outflow_days + ' 日</span></div>');
    }
    const asof = (q[0] && q[0].trade_date) || (rd[0] && rd[0].latest_date);
    const ao = document.getElementById("warn-asof");
    if (ao && asof) ao.textContent = `${asof} · 预警为描述性观察，永不写成操作建议`;
  } catch (e) {
    const a = document.getElementById("warn-rank");
    const b = document.getElementById("warn-quiet");
    if (a) a.innerHTML = typedEmpty("UNAVAILABLE", "排名跌出现查失败，不回落打样板块。");
    if (b) b.innerHTML = typedEmpty("UNAVAILABLE", "静默流出现查失败。");
  }
}

/* ---------- 板块下钻（全动态） ---------- */
window.renderDrill = async function () {
  const sec = document.querySelector('[data-tab="sector"]'); if (!sec) return;
  const { chain, code } = window.DRILL || { chain: "sw_industry", code: null };
  try {
    const d = await jget(`/api/v3/pulse/drill?chain=${chain}${code ? `&code=${encodeURIComponent(code)}` : ""}`);
    const rows = d.rows || [], bc = d.breadcrumb || [];
    const asOf = (rows[0] && rows[0].trade_date) || "";
    let stripe = null;
    if (code) { try { stripe = await jget(`/api/v3/pulse/flow_stripe?chain=${chain}&code=${encodeURIComponent(code)}&days=60`); } catch (e) {} }
    while (sec.children.length > 2) sec.removeChild(sec.lastChild);
    const wrap = document.createElement("div");
    let crumbs = `<div class="crumbs"><button class="crumb" data-crumb="" data-chain="${esc(chain)}">${CHAIN_CN[chain] || esc(chain)}<span class="lv">ROOT</span></button>`;
    bc.forEach((b, i) => {
      crumbs += `<span class="crumb-sep">›</span><button class="crumb ${i === bc.length - 1 ? "cur" : ""}" data-crumb="${esc(b.code)}" data-chain="${esc(chain)}">${esc(b.name)}<span class="lv">${esc(b.level)}</span></button>`;
    });
    if (d.rows_level === "stock") crumbs += `<span class="crumb-sep">›</span><span class="crumb" style="cursor:default; border-style:dashed">成分股 ${rows.length}</span>`;
    crumbs += `<span style="margin-left:auto; font-family:var(--mono); font-size:10px; color:var(--ink-3)">● LIVE${asOf ? " · as-of " + esc(asOf) : ""} · 成分 PIT: in_date ≤ as_of &lt; out_date</span></div>`;
    wrap.innerHTML = crumbs;
    if (stripe && stripe.values) {
      wrap.insertAdjacentHTML("beforeend", `<div class="curvebox">
        <div class="cv-head"><span>${esc(bc.length ? bc[bc.length - 1].name : "")} ${esc(code)} · 资金流曲线 —— 柱=当日净流 / 线=窗口累计</span><span>60 日</span></div>
        <svg id="drillcurve" viewBox="0 0 720 180" preserveAspectRatio="none"></svg>
        <div class="cv-axis"><span>${stripe.dates[0]}</span><span>单位: 亿元 · 缺日断柱不补零</span><span>${stripe.dates[stripe.dates.length - 1]}</span></div></div>`);
    }
    if (d.rows_level === "stock") {
      wrap.insertAdjacentHTML("beforeend", `<div class="sec-title" style="margin-top:56px">成分股 · ${rows.length} 只 —— 按近 20 日累计净流降序 · LIVE</div>
        <div class="dtable" style="margin-top:0">
        <div class="lvrow head" style="grid-template-columns:1.2fr 100px 110px 110px 90px 60px"><span>股票</span><span style="text-align:right">当日净流</span><span style="text-align:right">20 日累计</span><span>形态 FORM</span><span></span><span></span></div>
        ${rows.map(r => {
          const net = YI(r.net_amount), cum = YI(r.cum_net);
          const note = `${r.flow_streak ? "streak " + r.flow_streak : ""}${r.limit_times ? " · " + r.limit_times + " 板" : ""}${r.is_breakout_event ? " · 突破事件" : ""}`;
          return `<div class="lvrow" style="grid-template-columns:1.2fr 100px 110px 110px 90px 60px" data-nav="insight/dossier" data-code="${esc(String(r.ts_code || "").slice(0, 6))}">
            <span class="lv-name">${esc(r.name)}<span class="cc">${esc(r.ts_code)}</span></span>
            <span class="lv-num ${clsSign(net)}">${fmtYi(net)}</span>
            <span class="lv-num ${clsSign(cum)}">${fmtYi(cum)}</span>
            <span>${r.form_name ? `<span class="regime ${clsSign(cum)}">${esc(r.form_name)}</span>` : "—"}</span>
            <span class="lv-note">${note}</span><span>${xqMark(r.ts_code, r.name)}</span></div>`;
        }).join("") || '<div class="msg-row"><span class="txt">空层 · 成分在册但本层无行</span></div>'}</div>`);
    } else {
      wrap.insertAdjacentHTML("beforeend", `<div class="sec-title" style="margin-top:56px">${esc(d.rows_level || "")} · ${bc.length ? "「" + esc(bc[bc.length - 1].name) + "」的子级" : CHAIN_CN[chain] + " 顶层"} —— 同层可比 · LIVE</div>
        <div class="dtable" style="margin-top:0">
        <div class="lvrow head"><span></span><span>板块</span><span style="text-align:right">涨幅</span><span style="text-align:right">净流</span><span style="text-align:right">RS 排名</span><span>形态</span><span></span></div>
        ${rows.map(r => {
          const isNull = r.net_amount == null && r.pct_change == null;
          if (isNull) return `<div class="lvrow nullrow"><span class="lv-step"></span><span class="lv-name">${esc(r.sector_name)}<span class="cc">${esc(r.sector_code)}</span></span><span class="lv-num">—</span><span class="lv-num">—</span><span class="lv-num">—</span><span>—</span><span class="lv-note">成分在册 · 无行情行, 指标 NULL 不隐藏</span></div>`;
          const net = YI(r.net_amount);
          return `<div class="lvrow" data-nav="insight/sector" data-chain="${esc(chain)}" data-code="${esc(r.sector_code)}">
            <span class="lv-step">›</span><span class="lv-name">${esc(r.sector_name)}<span class="cc">${esc(r.sector_code)}</span></span>
            <span class="lv-num ${clsSign(r.pct_change)}">${fmtPct(r.pct_change)}</span>
            <span class="lv-num ${clsSign(net)}">${fmtYi(net)}</span>
            <span class="lv-num">${r.rs_rank_4w ? "#" + r.rs_rank_4w : "—"}</span>
            <span>${regimeChip(r.flow_regime)}</span>
            <span class="lv-note">${r.flow_streak ? "streak " + r.flow_streak : ""}</span></div>`;
        }).join("") || '<div class="msg-row"><span class="txt">空层 · 未知码 200 空行不猜</span></div>'}</div>`);
    }
    wrap.insertAdjacentHTML("beforeend", ``);
    sec.appendChild(wrap);
    if (stripe && stripe.values) paintFlowCurve("drillcurve", stripe.dates,
      stripe.values.map(v => (v == null ? null : YI(v))),
      (stripe.cum_values || []).map(v => (v == null ? null : YI(v))));
  } catch (e) {
    while (sec.children.length > 2) sec.removeChild(sec.lastChild);
    const wrap = document.createElement("div");
    wrap.innerHTML = typedEmpty("UNAVAILABLE", `板块下钻现查失败，不回落打样 CRO。<span class="mono">${esc(e.message)}</span>`);
    sec.appendChild(wrap);
  }
};
document.addEventListener("click", e => {
  const c = e.target.closest("[data-crumb]"); if (!c) return;
  window.DRILL = { chain: c.dataset.chain || (window.DRILL || {}).chain || "sw_industry", code: c.dataset.crumb || null };
  window.renderDrill(); window.scrollTo(0, 0);
});
document.addEventListener("click", e => {
  const b = e.target.closest("[data-flowchain]");
  if (b) { document.querySelectorAll("[data-flowchain]").forEach(x => x.classList.toggle("on", x === b)); FLOW.chain = b.dataset.flowchain; liveFlows(); return; }
  const w = e.target.closest("[data-w]");
  if (w) { document.querySelectorAll("[data-w]").forEach(x => x.classList.toggle("on", x === w)); FLOW.win = +w.dataset.w; liveFlows(); }
});

/* ---------- 个股档案 (全动态 · 一投影五面) ---------- */
window.DOSSIER = window.DOSSIER || null;
function lineSVG(vals, w, h, stroke) {
  const pts = (vals || []).map((v, i) => (v == null ? null : [i, v]));
  const vs = pts.filter(Boolean); if (vs.length < 2) return "";
  const ys = vs.map(p => p[1]), mn = Math.min(...ys), mx = Math.max(...ys);
  const X = i => (i / Math.max(1, vals.length - 1)) * (w - 6) + 3;
  const Y = v => (mx === mn ? h / 2 : h - 3 - ((v - mn) / (mx - mn)) * (h - 6));
  let inner = `<polyline points="${vs.map(p => X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join(" ")}" fill="none" stroke="${stroke}" stroke-width="1.4"/>`;
  const last = vs[vs.length - 1];
  inner += `<circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="1.8" fill="${stroke}"/>`;
  return inner;
}
const SUFFIX = c => (c || "").startsWith("6") ? ".SH" : (c || "").startsWith("4") || (c || "").startsWith("8") ? ".BJ" : ".SZ";
/* 雪球个股外链: 6→SH, 0/3→SZ, 4/8→BJ — https://xueqiu.com/S/SH603228 */
const XQ = c => "https://xueqiu.com/S/" + ((c || "").startsWith("6") ? "SH" : (c || "").startsWith("4") || (c || "").startsWith("8") ? "BJ" : "SZ") + (c || "");
function xqMark(code, name) {
  const c = String(code || "").slice(0, 6);
  if (!/^\d{6}$/.test(c)) return "";
  return `<a class="xq xq-mark" href="${XQ(c)}" target="_blank" rel="noopener" title="雪球 ${esc(name || c)}">雪</a>`;
}
const DOT = s => s === "ok" ? ["var(--ok)", "●"] : (s === "stale" || s === "delegated") ? ["var(--soft)", "◐"] : ["var(--unk)", "▨"];
function facetChips(block) {
  if (!block) return "";
  const tags = block.tags || [], labels = block.tag_labels || [];
  const out = [];
  tags.forEach((t, i) => {
    if (t === "own_funds_account" && (tags.includes("foreign_own_funds") || tags.includes("domestic_insurer_own"))) return;
    out.push(labels[i] || t);
  });
  return out.map(l => `<span class="rtag">${esc(l)}</span>`).join("");
}
function researchChips(id) {
  if (!id) return "";
  return facetChips(id.holder_research_class) + facetChips(id.holder_capital_role);
}
function setMiniDot(mini, status, reason) {
  const b = document.querySelector(`#dossier-tabs button[data-mini="${mini}"] .st`);
  if (!b) return; const [c, g] = DOT(status); b.style.color = c; b.textContent = g; b.title = reason || status || "";
}
window.liveDossier = async function () {
  const list = document.getElementById("dossier-list");
  const det = document.getElementById("dossier-detail");
  const code = window.DOSSIER && window.DOSSIER.code;
  if (!code) {  /* 默认 = 股票列表 */
    if (det) det.style.display = "none";
    if (list) list.style.display = "";
    liveDossierList();
    return;
  }
  if (list) list.style.display = "none";
  if (det) det.style.display = "";
  let d;
  try { d = await jget(`/api/v3/stock/${code}/dossier`); }
  catch (e) {  /* 离线: 诚实空态, 不留烘焙旧股 */
    const head = document.getElementById("dossier-head");
    if (head) head.innerHTML = `<h1>${esc(code)}</h1>`;
    ["form", "fund", "holder", "inst", "cross", "lhb"].forEach(k => setMiniDot(k, "unknown", "backend offline"));
    ["dossier-form", "dossier-fund", "dossier-holder", "dossier-inst", "dossier-cross", "dossier-lhb"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>offline</b></span><p>后端未连接，本面无数可示。</p></div>`;
    });
    const kl = document.getElementById("dossier-kline");
    if (kl) kl.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>offline</b></span><p>K 线无数可示。</p></div>`;
    return;
  }
  const tabs = (d.usability && d.usability.tabs) || {};
  const head = document.getElementById("dossier-head");
  if (head) {
    const CN = { form: "形态", moneyflow: "资金", holders: "股东", holder_number: "户数", lhb_seats: "龙虎榜" };
    head.innerHTML = `<h1><a class="xq" href="${XQ(d.stock_code || code)}" target="_blank" rel="noopener" title="雪球 ${esc(d.stock_code || code)}">${esc((d.basic && d.basic.stock_name) || code)}</a></h1><span class="d-code"><a class="xq" href="${XQ(d.stock_code || code)}" target="_blank" rel="noopener" title="雪球 ${esc(d.stock_code || code)}">${esc(d.stock_code || code)}${SUFFIX(d.stock_code || code)}</a></span>` +
      Object.keys(CN).map(k => {
        const t = tabs[k] || {};
        const lamp = t.status === "ok" ? "ok" : (t.status === "stale" || t.status === "delegated") ? "soft" : "unk";
        return `<span class="lamp ${lamp}" title="${esc(t.reason || "")}"><i></i>${CN[k]} ${esc(t.status || "?")}</span>`;
      }).join("") +
      `<span class="mono" style="font-size:10px;color:var(--ink-3);margin-left:auto">${esc((d.basic && d.basic.industry && d.basic.industry.l1_name) || "")} · usability ${esc((d.usability && d.usability.status) || "—")} · cap ${esc((d.usability && d.usability.cap) || "—")}</span>`;
  }
  setMiniDot("form", (tabs.form || {}).status, (tabs.form || {}).reason);
  setMiniDot("holder", (tabs.holders || {}).status, (tabs.holders || {}).reason);
  setMiniDot("inst", (tabs.holders || {}).status, "机构面数据在 holders.institution_profile 内");
  setMiniDot("lhb", (tabs.lhb_seats || {}).status, (tabs.lhb_seats || {}).reason);
  renderDossierForm(d); renderDossierHolder(d); renderDossierInst(d); renderDossierLhb(d);
  liveDossierFund(code); liveDossierCross(code); liveDossierKline(code);
};

/* ---------- 个股档案 · 默认列表 (标签筛选 / 搜索 / 分页) ---------- */
const DOSLIST = { tag: null, q: null, offset: 0, limit: 200 };
async function liveDossierList() {
  const body = document.getElementById("dossier-list-body"); if (!body) return;
  const qs = new URLSearchParams();
  if (DOSLIST.tag) qs.set("tag", DOSLIST.tag);
  if (DOSLIST.q) qs.set("q", DOSLIST.q);
  qs.set("limit", String(DOSLIST.limit)); qs.set("offset", String(DOSLIST.offset));
  let d; try { d = await jget(`/api/v3/stock/list?${qs}`); }
  catch (e) {
    body.innerHTML = `<div class="typed-empty" style="margin-top:32px"><span class="lamp unk"><i></i><b>offline</b></span><p>后端未连接，列表无数可示。</p></div>`;
    return;
  }
  const rows = d.rows || [];
  const forms = (d.facets && d.facets.form_name) || [];
  const brk = (d.facets && d.facets.breakout) || 0;
  const chips = [`<span class="fchip ${DOSLIST.tag ? "" : "on"}" data-dtag="">全部</span>`]
    .concat(forms.slice(0, 10).map(f => `<span class="fchip ${DOSLIST.tag === f.value ? "on" : ""}" data-dtag="${esc(f.value)}">${esc(f.value)} ${f.count}</span>`))
    .concat([`<span class="fchip ${DOSLIST.tag === "突破事件" ? "on" : ""}" data-dtag="突破事件">突破事件 ${brk}</span>`]);
  body.innerHTML = `
    <div class="sec-title" style="margin-top:36px">股票列表 · ${d.total} 只 —— 身份 dim（非当日可交易宇宙）· 形态标签 as-of ${esc(d.as_of_form || "—")} · 点行进档案</div>
    <div class="filter-chips" style="margin-top:20px">${chips.join("")}</div>
    <p style="margin-top:10px; font-size:11.5px; color:var(--ink-3); line-height:1.6">chip 计数是该快照日全市场普查，不随搜索/筛选缩放。</p>
    <div class="dtable" style="margin-top:22px">
      <div class="lvrow head" style="grid-template-columns:120px 1fr 150px 170px 40px"><span>代码</span><span>名称</span><span>形态 FORM</span><span>位置 · 趋势</span><span></span></div>
      ${rows.map(r => `<div class="lvrow" style="grid-template-columns:120px 1fr 150px 170px 40px" data-nav="insight/dossier" data-code="${esc(r.stock_code)}" title="进入 ${esc(r.stock_name || r.stock_code)} 档案">
        <span class="lv-num">${esc(r.stock_code)}${SUFFIX(r.stock_code)}</span>
        <span class="lv-name">${esc(r.stock_name || "—")}</span>
        <span>${r.form_name ? `<span class="regime">${esc(r.form_name)}</span>` : '<span class="lv-note">—</span>'}</span>
        <span class="lv-note">${esc(r.axis_pos || "—")} · ${esc(r.axis_trend || "—")}${r.is_breakout_event ? ' · <b style="color:var(--in-tx)">突破</b>' : ""}</span>
        <span>${xqMark(r.stock_code, r.stock_name)}</span></div>`).join("") || '<div class="msg-row" style="display:block"><span class="txt">无匹配行</span></div>'}
    </div>
    <div style="margin-top:18px; display:flex; gap:10px; align-items:center">
      ${d.offset > 0 ? '<button class="btn ghost" id="doslist-prev">← 上一页</button>' : ""}
      ${d.offset + rows.length < d.total ? '<button class="btn ghost" id="doslist-next">下一页 →</button>' : ""}
      <span class="mono" style="font-size:10px; color:var(--ink-3)">${d.offset + (rows.length ? 1 : 0)}–${d.offset + rows.length} / ${d.total}</span>
    </div>`;
}
document.addEventListener("click", e => {
  const c = e.target.closest("#dossier-list-body .fchip");
  if (c) { DOSLIST.tag = c.dataset.dtag || null; DOSLIST.offset = 0; liveDossierList(); return; }
  if (e.target.closest("#doslist-prev")) { DOSLIST.offset = Math.max(0, DOSLIST.offset - DOSLIST.limit); liveDossierList(); return; }
  if (e.target.closest("#doslist-next")) { DOSLIST.offset += DOSLIST.limit; liveDossierList(); return; }
  const bk = e.target.closest("#dossier-back");
  if (bk) {
    window.DOSSIER = null;
    if (window.CM) window.CM.goto("insight/dossier");
    else window.liveDossier();
    window.scrollTo(0, 0);
  }
});

/* ---------- 形态页 · 量价走势图 (K 线 + 成交量, 后端 qfq 投影) ---------- */
async function liveDossierKline(code) {
  const el = document.getElementById("dossier-kline"); if (!el) return;
  let k; try { k = await jget(`/api/v3/stock/${code}/kline?days=180`); }
  catch (e) {
    el.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>offline</b></span><p>量价走势无数可示。</p></div>`;
    return;
  }
  const rows = k.rows || [];
  if (rows.length < 2) {
    el.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>${esc(k.status || "empty")}</b></span>
      <p>量价走势无数可示（需要至少两根 qfq 日线）。<span class="mono">${esc(k.reason || "no_qfq_kline")}</span></p></div>`;
    return;
  }
  el.innerHTML = `<div class="sec-title" style="margin-top:0">量价走势 · KLINE —— qfq 分析视图（非名义成交价）· 截至 ${esc(k.as_of || "—")} · ${k.days} 个交易日</div>
    <div class="curvebox" style="margin-top:20px">
      <svg id="dossiercandle" viewBox="0 0 720 300" preserveAspectRatio="none" style="height:300px; display:block"></svg>
      <div class="cv-axis"><span>${esc(rows[0].date)}</span><span>红涨绿跌 · 下柱 = 成交量 · qfq</span><span>${esc(rows[rows.length - 1].date)}</span></div></div>`;
  paintCandles("dossiercandle", rows);
}
function paintCandles(svgId, rows) {
  const svg = document.getElementById(svgId); if (!svg) return;
  const W = 720, H = 300, VOL_H = 54, GAP = 12, TOP = 10;
  const PH = H - VOL_H - GAP - TOP - 6;
  const n = rows.length;
  const step = (W - 12) / n;
  const bw = Math.max(1.2, Math.min(7, step * 0.62));
  let mn = Infinity, mx = -Infinity, vmx = 0;
  rows.forEach(r => {
    if (r.low != null) mn = Math.min(mn, r.low);
    if (r.high != null) mx = Math.max(mx, r.high);
    if (r.volume != null) vmx = Math.max(vmx, r.volume);
  });
  if (!(mx > mn)) return;
  const pad = (mx - mn) * 0.06; mn -= pad; mx += pad;
  const X = i => 6 + i * step + step / 2;
  const Y = v => TOP + (1 - (v - mn) / (mx - mn)) * PH;
  const VB = TOP + PH + GAP + VOL_H;  // 量柱底
  const parts = [];
  rows.forEach((r, i) => {
    if (r.open == null || r.close == null || r.high == null || r.low == null) return;
    const up = r.close >= r.open;
    const col = up ? "var(--in-4)" : "var(--out-4)";
    const x = X(i);
    parts.push(`<line x1="${x.toFixed(1)}" y1="${Y(r.high).toFixed(1)}" x2="${x.toFixed(1)}" y2="${Y(r.low).toFixed(1)}" stroke="${col}" stroke-width="1"/>`);
    const y1 = Y(Math.max(r.open, r.close)), y2 = Y(Math.min(r.open, r.close));
    parts.push(`<rect x="${(x - bw / 2).toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.8, y2 - y1).toFixed(1)}" fill="${col}"/>`);
    if (r.volume != null && vmx > 0) {
      const vh = (r.volume / vmx) * VOL_H;
      parts.push(`<rect x="${(x - bw / 2).toFixed(1)}" y="${(VB - vh).toFixed(1)}" width="${bw.toFixed(1)}" height="${vh.toFixed(1)}" fill="${col}" opacity="0.45"/>`);
    }
  });
  svg.innerHTML = parts.join("");
}
function renderDossierForm(d) {
  const el = document.getElementById("dossier-form"); if (!el) return;
  const fs = d.form_stage || {}, obs = d.observation || {};
  const zh = obs.axes_zh || [];
  const axes = [["Position 位置", fs.axis_pos], ["Trend 趋势", fs.axis_trend], ["Purity 纯度", fs.axis_purity], ["Volume 量能", fs.axis_vol], ["Vol Regime 波动", fs.axis_volregime]];
  const prs = fs.production_read_status || "—";
  el.innerHTML = `<div class="sec-title" style="margin-top:48px">形态轴 · AXES —— as-of ${esc(fs.trade_date || obs.as_of || "—")} · production_read <span class="lamp ${prs === "READY" ? "ok" : "soft"}"><i></i>${esc(prs)}</span></div>
    <div class="axis-grid" style="margin-top:24px">${axes.map(([l, v], i) =>
      `<div class="axis-cell"><div class="a-label">${l}</div><div class="a-val">${esc(v ?? "—")}</div><div class="a-sub">${esc(zh[i] || "")}</div></div>`).join("")}</div>
    <div class="qtable" style="margin-top:32px">
      <div class="qrow"><span class="q-name">form</span><span class="lamp ok"><i></i>${esc(fs.form_name || "—")}</span><span class="q-detail">${esc(fs.form_sub || "")} · 周 ${esc(fs.weekly_name || "—")} · 月 ${esc(fs.monthly_name || "—")}${fs.is_breakout_event ? " · 突破事件" : ""}</span></div>
      ${obs.text ? `<div class="qrow"><span class="q-name">observation</span><span class="lamp ${obs.status === "ok" ? "ok" : "unk"}"><i></i>${esc(obs.status || "—")}</span><span class="q-detail">${esc(obs.text)}</span></div>` : ""}
      ${(d.gaps || []).length ? `<div class="qrow"><span class="q-name">gaps</span><span class="lamp soft"><i></i>${d.gaps.length}</span><span class="q-detail">${d.gaps.map(esc).join(" · ")}</span></div>` : ""}
    </div>`;
}
async function liveDossierFund(code) {
  const el = document.getElementById("dossier-fund"); if (!el) return;
  let m; try { m = await jget(`/api/v3/decision/moneyflow/stock/${code}`); }
  catch (e) { setMiniDot("fund", "unknown", "decision api offline"); return; }
  setMiniDot("fund", m.status === "ok" ? "ok" : "stale", m.reason || m.status);
  const plane = (m.planes && m.planes.moneyflow_dc) || null;
  const staleNote = m.status !== "ok" ? `<div class="stale-banner soft" style="margin:0 0 22px"><span class="lamp soft"><i></i><b>${esc((m.status || "").toUpperCase())}</b></span><span style="font-size:12.5px;color:var(--ink-2)">${esc(m.reason || "")} —— 滞后照实标注，不装新鲜。</span></div>` : "";
  if (!plane || !(plane.horizons || []).length) {
    el.innerHTML = staleNote + `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>${esc((plane && plane.status) || "unknown")}</b></span><p>资金平面无可用行。<span class="mono">${esc((plane && plane.reason) || "no plane rows")}</span></p></div>`;
    return;
  }
  const hz = plane.horizons;
  el.innerHTML = staleNote + `<div class="sec-title" style="margin-top:0">资金流 · 窗口视角 —— ${esc(plane.label || "东财主力")} · as-of ${esc(plane.as_of || "—")}</div>
    <div class="dtable" style="margin-top:24px">
    <div class="lvrow head" style="grid-template-columns:70px 100px 90px 110px 140px 110px 1fr"><span>窗口</span><span>状态</span><span>覆盖</span><span style="text-align:right">累计净额</span><span style="text-align:right">净额/流通市值</span><span style="text-align:right">窗口涨跌</span><span></span></div>
    ${hz.map(h => `<div class="lvrow" style="grid-template-columns:70px 100px 90px 110px 140px 110px 1fr; cursor:default">
      <span class="lv-num">${h.horizon} 日</span>
      <span>${h.status === "known" ? '<span class="lamp ok"><i></i>known</span>' : `<span class="lamp unk"><i></i>${esc(h.status || "?")}</span>`}</span>
      <span class="lv-num">${h.coverage_days ?? "—"}/${h.required_days ?? "—"}</span>
      <span class="lv-num ${clsSign(YI(h.cum_net))}">${fmtYi(YI(h.cum_net))}</span>
      <span class="lv-num ${clsSign(h.relative_ratio_pct)}">${fmtPct(h.relative_ratio_pct)}</span>
      <span class="lv-num ${clsSign(h.window_return_pct)}">${fmtPct(h.window_return_pct)}</span>
      <span class="lv-note">${esc(h.ratio_source || "")}</span></div>`).join("")}</div>
    <p style="margin-top:16px; font-size:11.5px; color:var(--ink-3); line-height:1.7">流通市值 ${m.circ_mv && m.circ_mv.value != null ? (m.circ_mv.value / 1e8).toFixed(0) + " 亿 (as-of " + esc(m.circ_mv.as_of) + ")" : "unknown — 缺则不除"} · ${esc(m.disclaimer || "")}</p>`;
}
function renderDossierHolder(d) {
  const el = document.getElementById("dossier-holder"); if (!el) return;
  const h = d.holders || {}, rows = h.rows || [], hn = d.holder_number || {};
  const exited = h.exited || [], cc = h.change_counts || {};
  const t = ((d.usability || {}).tabs || {}).holders || {};
  if (!rows.length && !exited.length) {
    el.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>${esc(t.status || "unknown")}</b></span>
      <p>披露域本季无可用股东行。<b>显示 ${esc(t.status || "unknown")}，不显示旧值。</b><span class="mono">${esc(t.reason || "holders empty")}</span></p></div>`;
    return;
  }
  const ser = (hn.series || []).slice().reverse();
  const latest = hn.latest || {};
  const chgChip = r => {
    const cs = r.change_status || "";
    const num = r.hold_change_num;
    const shares = num == null ? "" : ` <span style="opacity:.75">${num >= 0 ? "+" : ""}${(Math.abs(num) >= 1e8 ? (num / 1e8).toFixed(2) + "亿" : Math.abs(num) >= 1e4 ? (num / 1e4).toFixed(1) + "万" : num) + "股"}</span>`;
    if (cs === "新进") return `<span class="chg new">新进${shares}</span>`;
    if (cs === "增持") return `<span class="chg add">增持${shares}</span>`;
    if (cs === "减持") return `<span class="chg trim">减持${shares}</span>`;
    if (cs === "不变") return `<span class="chg same">不变</span>`;
    return `<span class="chg same">${esc(cs || "—")}</span>`;
  };
  const chips = ["新进", "增持", "减持", "不变", "退出"].map(k => {
    const n = cc[k] || 0;
    const cls = { 新进: "new", 增持: "add", 减持: "trim", 不变: "same", 退出: "exit" }[k];
    return `<span class="chg ${cls}" style="${n ? "" : "opacity:.35"}">${k} ${n}</span>`;
  }).join(" ");
  el.innerHTML = `<div class="sec-title" style="margin-top:0">十大流通股东 · TOP 10 —— 报告期 ${esc(h.report_date || "—")}（上期 ${esc(h.prev_report_date || "—")}）· 披露轴非交易日轴</div>
    <div style="margin-top:18px; display:flex; gap:8px; flex-wrap:wrap; align-items:center">${chips}
      <span class="mono" style="font-size:10px; color:var(--ink-3); margin-left:auto">变动标签与退出对比 = 本期 vs 上期 period-diff · 持股收益仅闭合 episode 可测</span></div>
    <div class="dtable" style="margin-top:22px">
    <div class="lvrow head" style="grid-template-columns:36px 1.5fr 100px 90px 150px 90px 100px 100px 40px"><span>#</span><span>股东</span><span>类型</span><span style="text-align:right">占流通</span><span>本期变动</span><span style="text-align:right">连续在榜</span><span style="text-align:right">持仓日历天</span><span style="text-align:right">episode 超额</span><span></span></div>
    ${rows.map(r => `<div class="lvrow" style="grid-template-columns:36px 1.5fr 100px 90px 150px 90px 100px 100px 40px; ${r.has_institution_profile ? "" : "cursor:default"}"
      ${r.has_institution_profile ? `data-nav="insight/inst" data-holder="${esc(r.holder_name_norm || r.holder_name)}" title="该股东有机构画像 · 点击查看"` : 'title="episode_only / 无画像 · 不假链"'}>
      <span class="lv-num" style="color:var(--ink-3)">${r.holder_rank}</span>
      <span class="lv-name">${esc(r.holder_name)}${researchChips(r.research_identity)}</span>
      <span class="lv-note">${esc(r.holder_type || "—")}${r.institution_profile_low_sample ? " · low_sample" : ""}</span>
      <span class="lv-num" style="text-align:right">${r.hold_ratio_float == null ? "—" : r.hold_ratio_float.toFixed(2) + "%"}</span>
      <span>${chgChip(r)}</span>
      <span class="lv-num" style="text-align:right" title="连续出现在前十大的报告期数 (近 8 期窗口 heuristic)">${r.approx_periods_present == null ? "—" : r.approx_periods_present + " 期"}</span>
      <span class="lv-num" style="text-align:right">${r.holding_cycle_days == null ? "—" : r.holding_cycle_days + "d"}</span>
      <span class="lv-num ${clsSign(r.return_pct)}" style="text-align:right">${r.return_pct == null ? "—" : fmtPct(r.return_pct * 100)}</span>
      <span class="lv-note">${r.has_institution_profile ? "→" : ""}</span></div>`).join("")}</div>
    ${exited.length ? `<div class="sec-title" style="margin-top:40px">本期退出 · EXITED —— 上期在榜 / 本期跌出前十（period-diff 推导）</div>
    <div class="dtable" style="margin-top:18px">
    ${exited.map(e2 => `<div class="lvrow" style="grid-template-columns:1.6fr 120px 130px 130px 1fr; cursor:default">
      <span class="lv-name" style="color:var(--ink-2)">${esc(e2.holder_name)}${researchChips(e2.research_identity)}</span>
      <span class="lv-note">${esc(e2.holder_type || "—")}</span>
      <span><span class="chg exit">退出</span></span>
      <span class="lv-num">上期占比 ${e2.hold_ratio_float == null ? "—" : e2.hold_ratio_float.toFixed(2) + "%"}</span>
      <span class="lv-note">披露 ${esc(e2.notice_date || "—")}</span></div>`).join("")}</div>` : ""}
    <div class="sec-title" style="margin-top:48px">股东户数 · HOLDER NUMBER —— 公告轴 · 最新 ${latest.holder_num == null ? "—" : Number(latest.holder_num).toLocaleString()} 户（${esc(latest.end_date || "—")} 期末）</div>
    <div class="curvebox" style="margin-top:20px"><svg id="holderspark" viewBox="0 0 720 120" preserveAspectRatio="none" style="height:120px">${lineSVG(ser.map(s => s.holder_num), 720, 120, "var(--ink)")}</svg>
      <div class="cv-axis"><span>${esc(ser.length ? ser[0].end_date : "")}</span><span>单位: 户 · 公告轴稀疏点位 · 非交易日轴</span><span>${esc(ser.length ? ser[ser.length - 1].end_date : "")}</span></div></div>`;
}
function renderDossierInst(d) {
  const el = document.getElementById("dossier-inst"); if (!el) return;
  const h = d.holders || {}, rows = h.rows || [], ip = h.institution_profile || null;
  if (!ip || !rows.length) {
    el.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>unknown</b></span>
      <p>该股机构画像覆盖未知。<span class="mono">institution_profile absent · never invent alpha</span></p></div>`;
    return;
  }
  const inst = rows.filter(r => r.has_institution_profile);
  el.innerHTML = `<div class="kpi-strip" style="margin-top:8px">
      <div class="kpi"><div class="k-label">画像覆盖 COVERAGE</div><div class="k-val">${ip.coverage == null ? "—" : (ip.coverage * 100).toFixed(0) + "%"}</div><div class="k-sub">${ip.holders_with_profile}/${ip.holders_total} 户有机构画像</div></div>
      <div class="kpi"><div class="k-label">薄样本 LOW SAMPLE</div><div class="k-val">${ip.holders_profile_low_sample ?? "—"}</div><div class="k-sub">episode 数低于门槛 · 指标仅参考</div></div>
      <div class="kpi"><div class="k-label">EPISODE ONLY</div><div class="k-val">${ip.holders_episode_only ?? "—"}</div><div class="k-sub">本股有 episode 但 mart 无画像 · 不假链</div></div>
    </div>
    <div class="sec-title" style="margin-top:40px">机构股东 · 点击进机构席位页展开其全市场 episode</div>
    <div class="dtable" style="margin-top:20px">
    ${inst.map(r => `<div class="lvrow" style="grid-template-columns:1.8fr 120px 110px 120px 60px" data-nav="insight/inst" data-holder="${esc(r.holder_name_norm || r.holder_name)}" title="点击展开该机构 episode 时间线">
      <span class="lv-name">${esc(r.holder_name)}${researchChips(r.research_identity)}</span>
      <span class="lv-note">${esc(r.holder_type || "—")}</span>
      <span class="lv-num">${r.hold_ratio_float == null ? "—" : r.hold_ratio_float.toFixed(2) + "%"}</span>
      <span class="lv-note">${r.institution_profile_low_sample ? "low_sample" : (r.institution_metrics_status || "")}</span>
      <span class="lv-note">→</span></div>`).join("") || '<div class="msg-row"><span class="txt">本股前十大无机构画像行</span></div>'}</div>
    <div class="progress-law" style="margin-top:28px">${esc(ip.note || "")}</div>`;
}
function renderDossierLhb(d) {
  const el = document.getElementById("dossier-lhb"); if (!el) return;
  const lhb = d.lhb_seats || {};
  const t = ((d.usability || {}).tabs || {}).lhb_seats || {};
  const rows = lhb.rows || [];
  if (!rows.length) {
    el.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>${esc(t.status || "empty")}</b></span>
      <p>龙虎榜席位不是十大流通股东。无上榜不补零。<span class="mono">${esc((lhb.gaps || []).join(" / ") || t.reason || "lhb_seats_empty")}</span></p></div>`;
    return;
  }
  el.innerHTML = `<div class="sec-title" style="margin-top:0">龙虎榜席位 · LHB —— 上榜日 ${esc(lhb.trade_date || "—")} · 席位日不是股东披露期</div>
    <p style="margin-top:12px; font-size:11.5px; color:var(--ink-3); line-height:1.7">${esc(lhb.note || "")}</p>
    <div class="dtable" style="margin-top:22px">
    <div class="lvrow head" style="grid-template-columns:1.6fr 80px 120px 1fr"><span>席位</span><span>方向</span><span style="text-align:right">净买</span><span></span></div>
    ${rows.map(r => {
      const seat = r.seat_research_class || {};
      const folk = r.alias_kind === "folk" && r.display_name && r.display_name !== r.exalter;
      return `<div class="lvrow" style="grid-template-columns:1.6fr 80px 120px 1fr; cursor:default">
        <span class="lv-name">${esc(r.display_name || r.exalter)}${folk ? `<span class="tp">${esc(r.exalter)}</span>` : ""}${facetChips(seat)}</span>
        <span class="lv-note">${esc(r.side || "—")}</span>
        <span class="lv-num ${clsSign(r.net_buy)}" style="text-align:right">${r.net_buy == null ? "—" : r.net_buy}</span>
        <span></span></div>`;
    }).join("")}</div>`;
}
async function liveDossierCross(code) {
  const el = document.getElementById("dossier-cross"); if (!el) return;
  let x; try { x = await jget(`/api/v3/decision/intersection/stock/${code}`); }
  catch (e) { setMiniDot("cross", "unknown", "decision api offline"); return; }
  setMiniDot("cross", x.status === "ok" ? "ok" : "stale", x.reason || x.status);
  const ao = x.as_of || {};
  // 机器 reason → 人话
  const reasonCN = r => {
    if (!r) return "";
    const m = r.match(/as_of_lag_(\d+)_calendar_days_gt_sla_(\d+)\s+as_of=(\d{8})\s+expected=(\d{8})/);
    if (m) return `数据截至 ${m[3].slice(0,4)}-${m[3].slice(4,6)}-${m[3].slice(6,8)}，距应有日期已滞后 ${m[1]} 个自然日（SLA ${m[2]} 日）`;
    if (r === "no_stock_at_intersection_this_window") return "本窗口无个股在三链交集中";
    return r;
  };
  const fmtD = v => v && String(v).length === 8 ? `${String(v).slice(0,4)}-${String(v).slice(4,6)}-${String(v).slice(6,8)}` : (v || "—");
  const inX = !!x.in_intersection;
  const det = x.detail || null;
  const chainDefs = [
    ["东财行业强势链", "dc_industry", det ? det.industry_sectors : null],
    ["东财概念强势链", "dc_concept", det ? det.concept_sectors : null],
    ["申万行业强势链", "sw_industry", det ? det.sw_sectors : null],
  ];
  el.innerHTML = `
    ${x.status !== "ok" ? `<div class="stale-banner soft" style="margin:0 0 22px"><span class="lamp soft"><i></i><b>滞后</b></span><span style="font-size:12.5px;color:var(--ink-2)">${esc(reasonCN(x.reason))} —— 照实标注，不装新鲜。</span></div>` : ""}
    <div class="cross-verdict ${inX ? "in" : "out"}">
      <div class="cv-big">${inX ? "✓ 当前在三链交集内" : "当前不在三链交集内"}</div>
      <div class="cv-sub">东财行业 ∩ 东财概念 ∩ 申万行业 · ${esc(x.horizon ?? "—")} 日窗口强势链的会员交集 —— 三条链同时在列才算入交，任一链未知即未知，不做加权平均。</div>
    </div>
    ${inX && det ? `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); margin-top:22px; border-left:1px solid var(--line-2)">
      ${chainDefs.map(([cn, , sectors]) => `<div class="cross-chain">
        <div class="cc-name">${cn}</div>
        <div class="cc-sectors">${(sectors || []).map(s => `${esc(s.sector_name || s.sector_code)}${s.behavior_zh ? ` <span class="regime" style="margin-left:4px">${esc(s.behavior_zh)}</span>` : ""}`).join("、") || "—"}</div>
      </div>`).join("")}
    </div>
    ${det.why ? `<p style="margin-top:18px; font-size:12.5px; color:var(--ink-2); line-height:1.8">${esc(det.why)}</p>` : ""}`
    : `<p style="margin-top:18px; font-size:12.5px; color:var(--ink-3); line-height:1.8">该股未同时出现在三条强势链的会员名单里。这不是利空判断，只是“当前不满足交集条件”这一事实。</p>`}
    <div class="sec-title" style="margin-top:40px">三条链的数据日期 · 各自 as-of</div>
    <div class="dtable" style="margin-top:18px">
      ${chainDefs.map(([cn, key]) => `<div class="lvrow" style="grid-template-columns:1fr 140px; cursor:default">
        <span class="lv-name">${cn}</span>
        <span class="lv-num" style="text-align:right">${fmtD(ao[key])}</span></div>`).join("")}
    </div>
    <p style="margin-top:22px; font-size:11.5px; color:var(--ink-3); line-height:1.7">交集是观察证据，非买卖建议；未经 B0→B5 策略验证。</p>`;
}
document.addEventListener("click", e => {
  const b = e.target.closest("#dossier-go"); if (!b) return;
  const v = ((document.getElementById("dossier-input") || {}).value || "").trim();
  if (/^\d{6}$/.test(v)) {
    window.DOSSIER = { code: v };
    if (window.CM) window.CM.goto("insight/dossier", { code: v });
    else window.liveDossier();
    window.scrollTo(0, 0); return;
  }
  DOSLIST.q = v || null; DOSLIST.offset = 0;
  window.DOSSIER = null; window.liveDossier();
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target && e.target.id === "dossier-input") {
    const b = document.getElementById("dossier-go"); if (b) b.click();
  }
});

/* ---------- 机构席位 (全量 + 点击展开 episode) ---------- */
window.INST = window.INST || { open: null };
const INST_CACHE = {};
const shortHolder = s => (s || "").length > 24 ? s.slice(0, 24) + "…" : (s || "—");
window.liveInst = async function () {
  const wrap = document.getElementById("instwrap"); if (!wrap) return;
  let d, sig = { signals: [] };
  try { d = await jget("/api/v3/inst/profiles?limit=500", 30000); }
  catch (e) {
    wrap.innerHTML = `<div class="typed-empty" style="margin-top:40px"><span class="lamp unk"><i></i><b>UNAVAILABLE</b></span>
      <p>机构名单现查失败，不回落打样户。<span class="mono">${esc(e.message)}</span>
      日更占库或后端未就绪时会出现这种情况。</p></div>`;
    return;
  }
  try { sig = await jget("/api/v3/inst/signals?days=30&limit=40"); }
  catch (e) { sig = { signals: [] }; }
  const rows = d.profiles || [];
  const conf = (d.disclosure_conformity && d.disclosure_conformity.overall_status) || "—";
  const signals = sig.signals || [];
  const sigHtml = signals.length
    ? `<div class="dtable" style="margin-top:20px">${signals.map(s => `<div class="lvrow" style="grid-template-columns:1.4fr 88px 1fr 96px 110px 90px">
        <span class="lv-name" data-nav="insight/inst" data-holder="${esc(s.holder)}" style="cursor:pointer">${esc(shortHolder(s.holder))}<span class="tp">${esc(s.holder_type || "")}</span>${researchChips(s.research_identity)}</span>
        <span class="lv-num" data-nav="insight/dossier" data-code="${esc(s.stock)}" style="cursor:pointer">${esc(s.stock)}</span>
        <span class="lv-name" data-nav="insight/dossier" data-code="${esc(s.stock)}" style="cursor:pointer">${esc(s.sw_l1_at_open || "—")}</span>
        <span class="lv-num">${esc(s.open_notice || "—")}</span>
        <span class="lv-num ${clsSign(s.holder_median_alpha)}">${s.holder_median_alpha == null ? "—" : (s.holder_median_alpha >= 0 ? "+" : "") + (s.holder_median_alpha * 100).toFixed(2) + "%"}</span>
        <span class="lv-note">画像α</span></div>`).join("")}</div>
      <p class="mono" style="margin-top:8px;font-size:10px;color:var(--ink-3)">inst/signals · 近 30 日新开 episode · 按 notice_date 过滤 · 表内 α 是机构自身历史战绩，不是跟随收益 · 点机构名展开 / 点代码进档案</p>`
    : `<div class="typed-empty" style="margin-top:20px"><span class="lamp unk"><i></i><b>0 rows</b></span>
        <p>近 30 日无新开非被动 episode 展示流。这是条件型空态。<span class="mono">inst/signals · days=30</span></p></div>`;
  wrap.innerHTML = `<div class="stale-banner soft" style="margin-top:32px">
      <span class="lamp ${conf === "CONFORMING" ? "ok" : "hard"}"><i></i><b>${esc(conf)}</b></span>
      <span style="font-size:12.5px; color:var(--ink-2); line-height:1.7">本面 <span class="mono">${esc(d.surface_status || "")}</span> · cutover_allowed=${String(!!d.cutover_allowed)} —— 披露域研究证据，不产生信号/建议。点任意机构行展开其 episode 持仓时间线。</span>
      <span class="mono" style="margin-left:auto; white-space:nowrap">${rows.length} 户<br>order by median_alpha</span></div>
    <div class="sec-title" style="margin-top:36px">披露事件流 · SIGNALS —— 近窗新开 episode · 画像层</div>
    ${sigHtml}
    <div class="dtable" style="border-top-color:var(--ink); margin-top:36px" id="inst-table">
    <div class="irow head"><span>#</span><span>机构 / HOLDER</span><span>闭合</span><span>中位α</span><span>胜率α</span><span>均持仓</span><span>标记</span></div>
    ${rows.map((r, i) => `<div class="irow" data-inst="${esc(r.holder)}" style="cursor:pointer" title="点击展开 / 收起 episode 时间线">
      <span class="i-rank">${i + 1}</span>
      <span class="i-name">${esc(shortHolder(r.holder))}<span class="tp">${esc(r.holder_type || "")}</span>${researchChips(r.research_identity)}</span>
      <span class="i-n">${r.n_closed ?? "—"}</span>
      <span class="i-n ${clsSign(r.median_alpha)}">${r.median_alpha == null ? "—" : (r.median_alpha >= 0 ? "+" : "") + (r.median_alpha * 100).toFixed(2) + "%"}</span>
      <span class="i-n">${r.win_rate_alpha == null ? "—" : (r.win_rate_alpha * 100).toFixed(0) + "%"}</span>
      <span class="i-n">${r.avg_hold_days == null ? "—" : Math.round(r.avg_hold_days) + "d"}</span>
      <span class="i-sub">${r.low_sample ? "low_sample" : ""}${r.is_passive_holder ? " · 被动" : ""}${r.metrics_status && r.metrics_status !== "ranked" ? " · " + esc(r.metrics_status) : ""}</span></div>`).join("")}
    </div>
    <div class="outcome-banner soft" style="margin-top:40px">
      <div>
        <div class="ob-state" style="font-size:20px">机构赢 ≠ 你跟随也赢</div>
        <div class="ob-sub">上表是席位自身已闭合 episode 的历史统计；跟随 spec 在 <span data-nav="lab/packages" style="cursor:pointer;text-decoration:underline">LAB 策略包</span>，E 消融在实验明细。三层不许互借光环。</div>
      </div>
      <div class="ob-meta">surface ${esc(d.surface_status || "")}<br>conformity ${esc(conf)} · cutover ${String(!!d.cutover_allowed)}</div>
    </div>`;
  if (window.INST.open) {
    const h = window.INST.open;
    window.INST.open = null;
    await expandInst(h, null);
  }
};
async function expandInst(holder, rowEl) {
  const table = document.getElementById("inst-table"); if (!table) return;
  let row = rowEl;
  if (!row && holder) {
    try { row = table.querySelector(`[data-inst="${CSS.escape(holder)}"]`); }
    catch (e) { row = null; }
  }
  if (row && row.nextElementSibling && row.nextElementSibling.classList.contains("inst-detail")) {
    row.nextElementSibling.remove(); return;
  }
  table.querySelectorAll(".inst-detail").forEach(x => x.remove());
  const box = document.createElement("div");
  box.className = "inst-detail";
  box.style.cssText = "border:1px solid var(--line-2); border-top:none; padding:20px 24px; background:var(--paper-2)";
  box.innerHTML = `<span class="mono" style="font-size:10.5px; color:var(--ink-3)">加载 episode 时间线…</span>`;
  const unranked = !row;
  if (row) {
    row.after(box);
    row.scrollIntoView({ block: "nearest" });
  } else {
    const head = table.querySelector(".irow.head");
    (head || table).after(box);
  }
  let d;
  try { d = INST_CACHE[holder] || (INST_CACHE[holder] = await jget(`/api/v3/inst/profiles/${encodeURIComponent(holder)}`)); }
  catch (e) {
    box.innerHTML = `<div class="typed-empty" style="margin-top:0"><span class="lamp unk"><i></i><b>empty</b></span>
      <p>该机构无档案可展。<span class="mono">${esc(e.message)}</span></p></div>`;
    return;
  }
  const p = d.profile || {}, eps = p.episodes || [], dims = p.dims || [];
  const pctA = v => v == null ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%";
  const EG = "grid-template-columns:118px 132px 1fr 96px 96px 80px 104px 104px 84px";
  box.innerHTML = `
    ${unranked ? `<p class="mono" style="font-size:10.5px; color:var(--ink-3); margin:0 0 12px">不在本页前 500 排名表 · 仍按 holder 现查</p>` : ""}
    <div style="display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin-bottom:16px">
      <b style="font-size:15px; letter-spacing:0.01em">${esc(p.holder || holder)}</b>
      <span class="mono" style="font-size:10px; color:var(--ink-3)">${esc(p.holder_type || "")} · metrics ${esc(p.metrics_status || "—")}${p.is_passive_holder ? " · 被动产品" : ""} · episodes ${p.n_episodes ?? eps.length}</span>
    </div>
    <div class="kpi-strip" style="margin-top:0; margin-bottom:24px">
      <div class="kpi"><div class="k-label">闭合 EPISODES</div><div class="k-val" style="font-size:20px">${p.n_closed ?? "—"}</div><div class="k-sub">已平仓 · 可测收益</div></div>
      <div class="kpi"><div class="k-label">持有中 HOLDING</div><div class="k-val" style="font-size:20px">${p.n_holding ?? "—"}</div><div class="k-sub">未闭合不测收益</div></div>
      <div class="kpi"><div class="k-label">中位 α MEDIAN</div><div class="k-val ${clsSign(p.median_alpha)}" style="font-size:20px">${pctA(p.median_alpha)}</div><div class="k-sub">闭合·非被动·非 seeded</div></div>
      <div class="kpi"><div class="k-label">胜率 α WIN</div><div class="k-val" style="font-size:20px">${p.win_rate_alpha == null ? "—" : (p.win_rate_alpha * 100).toFixed(0) + "%"}</div><div class="k-sub">alpha &gt; 0 占比</div></div>
      <div class="kpi"><div class="k-label">均持仓天 AVG</div><div class="k-val" style="font-size:20px">${p.avg_hold_days == null ? "—" : Math.round(p.avg_hold_days) + "d"}</div><div class="k-sub">披露期界日历天</div></div>
      <div class="kpi"><div class="k-label">样本 SAMPLE</div><div class="k-val" style="font-size:20px; ${p.low_sample ? "color:var(--hole)" : ""}">${p.low_sample ? "LOW" : "OK"}</div><div class="k-sub">${p.low_sample ? "低于排名门槛 · 仅参考" : "达排名样本量"}</div></div>
    </div>
    ${dims.length ? `<div class="sec-title" style="margin:0 0 12px; font-size:11px">维度切片 · DIMS</div>
    <div class="dtable" style="margin:0 0 24px; background:var(--paper)">
      <div class="lvrow head" style="grid-template-columns:160px 1fr 90px 110px 100px"><span>维度</span><span>取值</span><span>闭合数</span><span style="text-align:right">中位α</span><span style="text-align:right">胜率α</span></div>
      ${dims.map(m => `<div class="lvrow" style="grid-template-columns:160px 1fr 90px 110px 100px">
        <span class="lv-note">${esc(m.dim_type)}</span>
        <span class="lv-name">${esc(m.dim_value)}</span>
        <span class="lv-num">${m.n_closed ?? "—"}</span>
        <span class="lv-num ${clsSign(m.median_alpha)}" style="text-align:right">${pctA(m.median_alpha)}</span>
        <span class="lv-num" style="text-align:right">${m.win_rate_alpha == null ? "—" : (m.win_rate_alpha * 100).toFixed(0) + "%"}</span></div>`).join("")}
    </div>` : ""}
    <div class="sec-title" style="margin:0 0 12px; font-size:11px">EPISODE 时间线 · 最近 ${eps.length} 条</div>
    <div class="dtable" style="margin-top:0; background:var(--paper)">
    <div class="lvrow head" style="${EG}"><span>代码</span><span>名称</span><span>行业@开仓</span><span>开仓</span><span>闭合</span><span>状态</span><span style="text-align:right">ret_c1</span><span style="text-align:right">alpha_c1</span><span style="text-align:right">加/减仓</span></div>
    ${eps.map(epi => `<div class="lvrow" style="${EG}" data-nav="insight/dossier" data-code="${esc(epi.stock)}" title="点击进 ${esc(epi.stock)} 个股档案">
      <span class="lv-num">${esc(epi.stock)}${SUFFIX(epi.stock)}</span>
      <span class="lv-name">${epi.name ? esc(epi.name) : '<span style="color:var(--ink-3)">—</span>'} ${xqMark(epi.stock, epi.name)}</span>
      <span class="lv-name">${esc(epi.sw_l1_at_open || "—")}</span>
      <span class="lv-num">${esc(epi.open_date || "—")}</span>
      <span class="lv-num">${esc(epi.close_date || "—")}</span>
      <span class="lv-note">${epi.status === "holding" ? "持有中" : esc(epi.status || "—")}</span>
      <span class="lv-num ${clsSign(epi.ret_c1)}" style="text-align:right">${epi.ret_c1 == null ? "—" : (epi.ret_c1 * 100).toFixed(2) + "%"}</span>
      <span class="lv-num ${clsSign(epi.alpha_c1)}" style="text-align:right">${epi.alpha_c1 == null ? "—" : (epi.alpha_c1 * 100).toFixed(2) + "%"}</span>
      <span class="lv-num" style="text-align:right">${epi.n_adds ?? 0} / ${epi.n_trims ?? 0}</span></div>`).join("") || '<div class="msg-row" style="display:block"><span class="txt">无 episode 行</span></div>'}
    </div>
    <p style="margin-top:12px; font-size:11px; color:var(--ink-3)">持有中 episode 的 ret/alpha 为 null —— 未闭合不测收益，永不虚构。点股票行进该股档案；行内「雪」在新标签页打开雪球。</p>`;
}
document.addEventListener("click", e => {
  const r = e.target.closest("#inst-table [data-inst]");
  if (r) { expandInst(r.dataset.inst, r); }
});

/* ---------- 盘中简报 (trust-gated) ---------- */
async function liveBriefing() {
  const wrap = document.getElementById("briefbody"); if (!wrap) return;
  let b; try { b = await jget("/api/v3/decision/briefing/daily"); }
  catch (e) {
    wrap.innerHTML = typedEmpty("UNAVAILABLE", `盘中简报现查失败，不回落打样事实。<span class="mono">${esc(e.message)}</span>`);
    return;
  }
  const inp = b.inputs || {};
  const CN = { moneyflow: "Cap B · 资金流", intersection: "Cap D · 三链交集", screener: "形态选股" };
  const lamp = t => t === "trusted" ? "ok" : "soft";
  const ao = v => v == null ? "—" : (typeof v === "object" ? Object.values(v).join(" / ") : v);
  const lamps = Object.entries(inp).map(([k, v]) =>
    `<span class="lamp ${lamp(v.trust)}" title="${esc(v.reason || "")}"><i></i>${CN[k] || esc(k)} <b>${esc(v.trust || "?")}</b> · as-of ${esc(ao(v.as_of))}</span>`).join("");
  let body;
  if (b.narrative) {
    body = `<div class="narrative-null" style="border-color:var(--ok)"><h4 style="color:var(--ok)">NARRATIVE READY</h4><p>${esc(b.narrative)}</p></div>` +
      (b.sections || []).map(s => `<div class="fact-line"><span class="mono">${esc(s.title || s.name || "SECTION")}</span>${esc(s.text || s.body || JSON.stringify(s))}</div>`).join("");
  } else {
    body = `<div class="narrative-null"><h4>NARRATIVE = NULL</h4>
      <p>trust gate 未通过：${esc((b.reason || "inputs untrusted").split(";").join("；"))}。<br>
      不是没话可说 —— 是不拿旧数据装新观点。<b>NULL 是系统输出，不是加载失败。</b></p></div>`;
  }
  wrap.innerHTML = `<div class="sec-title" style="margin-top:40px">信任门 · TRUST GATE —— 能力灯与各自 as-of</div>
    <div class="trust-row">${lamps}<span style="margin-left:auto; font-family:var(--mono); font-size:10.5px; color:var(--ink-3)">● LIVE · surface ${esc(b.surface || "")} · horizon ${esc(b.horizon ?? "—")}</span></div>
    ${body}
    <div class="progress-law" style="margin-top:40px">${esc(b.disclaimer || "")} tier0_write=${String(!!b.tier0_write)} —— 简报永不回写底座。</div>`;
}

/* ---------- 形态选股 (stale-honest) ---------- */
async function liveScreener() {
  const wrap = document.getElementById("screenbody"); if (!wrap) return;
  let opt, fs;
  try { [opt, fs] = await Promise.all([jget("/api/v3/screener/options"), jget("/api/v3/screener/form_stage")]); }
  catch (e) {
    wrap.innerHTML = typedEmpty("UNAVAILABLE", `形态分布现查失败，不回落打样计数。<span class="mono">${esc(e.message)}</span>`);
    return;
  }
  const stale = (opt && opt.status) || (fs && fs.status) || "—";
  const reason = (opt && opt.reason) || (fs && fs.reason) || "";
  const asof = (opt && opt.as_of) || (fs && fs.as_of) || "—";
  const facets = (opt && opt.facets) || {};
  const fNames = facets.form_name || facets.form_names || null;
  const rows = (fs && fs.rows) || [];
  let distHtml;
  if (fNames && Object.keys(fNames).length) {
    const ent = Object.entries(fNames).sort((a, b) => (b[1].count ?? b[1]) - (a[1].count ?? a[1]));
    const mx = Math.max(...ent.map(([, v]) => (v.count ?? v)), 1);
    distHtml = ent.map(([k, v]) => { const n = (v.count ?? v); return `<div class="distbar-row"><span class="d-name">${esc(k)}</span><div class="d-track"><div class="d-fill" style="width:${(n / mx * 100).toFixed(1)}%"></div></div><span class="d-n">${Number(n).toLocaleString()}</span></div>`; }).join("");
  } else {
    distHtml = `<div class="typed-empty" style="margin-top:20px"><span class="lamp unk"><i></i><b>facets = ∅</b></span>
      <p>形态分布 facets 为空 —— 滞后期间不计算分布，不拿旧分布充数。<span class="mono">screener/options · ${esc(stale)} · as-of ${esc(asof)}</span></p></div>`;
  }
  const rowsHtml = rows.length ? rows.slice(0, 60).map(r => `<div class="lvrow" style="grid-template-columns:110px 1fr 130px 110px 110px 60px" data-nav="insight/dossier" data-code="${esc(String(r.ts_code || r.stock_code || "").slice(0, 6))}">
      <span class="lv-num">${esc(r.ts_code || r.stock_code || "—")}</span><span class="lv-name">${esc(r.name || r.stock_name || "—")}</span>
      <span><span class="regime">${esc(r.form_name || "—")}</span></span>
      <span class="lv-note">${esc(r.axis_pos || "")} · ${esc(r.axis_trend || "")}</span>
      <span class="lv-note">${r.is_breakout_event ? "突破事件" : ""}</span><span>${xqMark(r.ts_code || r.stock_code, r.name || r.stock_name)}</span></div>`).join("")
    : "";
  wrap.innerHTML = `${stale !== "ok" ? `<div class="stale-banner soft" style="margin-top:32px"><span class="lamp soft"><i></i><b>${esc(stale.toUpperCase())}</b></span>
      <span style="font-size:12.5px;color:var(--ink-2);line-height:1.7">${esc(reason)} —— 本面滞后照实标注；形态是 Tier1 观察态标签，非预测。</span>
      <span class="mono" style="margin-left:auto; white-space:nowrap">as-of ${esc(asof)}</span></div>` : ""}
    <div class="sec-title" style="margin-top:40px">形态分布 · FORM DISTRIBUTION —— as-of ${esc(asof)} · 全市场 · LIVE</div>
    <div style="margin-top:24px">${distHtml}</div>
    <div class="sec-title" style="margin-top:56px">形态行 · ROWS —— 点击进个股档案</div>
    <div class="dtable" style="margin-top:20px">${rowsHtml || '<div class="msg-row" style="display:block"><span class="txt">0 rows · ' + esc(stale) + ' 期间不列个股，不装新鲜</span></div>'}</div>
    <div class="progress-law" style="margin-top:32px">${esc((fs && fs.disclaimer) || (opt && opt.disclaimer) || "")}</div>`;
}

/* ---------- 观察账本 (portfolio + nav 曲线) ---------- */
function paintNav(svgId, dates, navVals, benchVals) {
  const svg = document.getElementById(svgId); if (!svg) return;
  const W = 720, H = 180;
  const n0 = navVals[0] || 1, b0 = (benchVals || [])[0] || 1;
  const nav = navVals.map(v => v / n0), ben = (benchVals || []).map(v => v / b0);
  const all = nav.concat(ben).filter(v => v != null);
  if (all.length < 2) return;
  const mn = Math.min(...all), mx = Math.max(...all), pad = (mx - mn || 1) * 0.12;
  const X = i => (i / Math.max(1, navVals.length - 1)) * (W - 10) + 5;
  const Y = v => H - 14 - ((v - mn + pad) / (mx - mn + 2 * pad)) * (H - 28);
  const pl = (arr, stroke, dash) => `<polyline points="${arr.map((v, i) => X(i).toFixed(1) + "," + Y(v).toFixed(1)).join(" ")}" fill="none" stroke="${stroke}" stroke-width="1.8"${dash ? ' stroke-dasharray="5 4"' : ""}/>`;
  svg.innerHTML = `<line x1="0" y1="${Y(1).toFixed(1)}" x2="${W}" y2="${Y(1).toFixed(1)}" stroke="var(--line-2)" stroke-width="1" stroke-dasharray="2 3"/>`
    + (ben.length > 1 ? pl(ben, "var(--ink-3)", true) : "") + pl(nav, "var(--ink)", false)
    + `<circle cx="${X(nav.length - 1).toFixed(1)}" cy="${Y(nav[nav.length - 1]).toFixed(1)}" r="2.6" fill="var(--ink)"/>`;
}
async function livePaper() {
  const wrap = document.getElementById("paperbody"); if (!wrap) return;
  let pf, nv;
  try { [pf, nv] = await Promise.all([jget("/api/v3/paper/portfolio"), jget("/api/v3/paper/nav")]); }
  catch (e) {
    wrap.innerHTML = typedEmpty("UNAVAILABLE", `观察账本现查失败，不回落打样仓位。<span class="mono">${esc(e.message)}</span>`);
    return;
  }
  const k = pf.kpi || {};
  const pos = pf.positions || [];
  const navRows = (nv && nv.nav) || [];
  const pct = v => v == null ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%";
  wrap.innerHTML = `<div class="stale-banner soft" style="margin-top:32px">
      <span class="lamp ${pf.status === "ok" ? "ok" : "soft"}"><i></i><b>${esc((pf.status || "—").toUpperCase())}</b></span>
      <span style="font-size:12.5px;color:var(--ink-2);line-height:1.7">surface <span class="mono">${esc(pf.surface_status || "—")}</span> —— 账本为观察态留档，非策略判决；空仓即空仓，不用 filler。</span></div>
    <div class="kpi-strip" style="margin-top:36px">
      <div class="kpi"><div class="k-label">初始资金 INIT</div><div class="k-val">${k.init_cash == null ? "—" : (k.init_cash / 1e4).toFixed(0)}<span style="font-size:14px">万</span></div><div class="k-sub">paper ledger</div></div>
      <div class="kpi"><div class="k-label">最新净值 NAV</div><div class="k-val">${k.nav == null ? "—" : (k.nav / 1e4).toFixed(1)}<span style="font-size:14px">万</span></div><div class="k-sub">${navRows.length ? "as-of " + esc(navRows[navRows.length - 1].nav_date) : ""}</div></div>
      <div class="kpi"><div class="k-label">累计收益 RET</div><div class="k-val ${clsSign(k.ret_cum)}">${pct(k.ret_cum)}</div><div class="k-sub">基准 <span class="${clsSign(k.bench_ret_cum)}">${pct(k.bench_ret_cum)}</span></div></div>
      <div class="kpi"><div class="k-label">超额 EXCESS</div><div class="k-val ${clsSign(k.excess_cum)}">${pct(k.excess_cum)}</div><div class="k-sub">对基准累计</div></div>
      <div class="kpi"><div class="k-label">闭合 / 胜率</div><div class="k-val">${k.n_closed ?? 0}</div><div class="k-sub">win_rate ${k.win_rate == null ? "—" : (k.win_rate * 100).toFixed(0) + "%"}</div></div>
    </div>
    <div class="sec-title" style="margin-top:48px">净值曲线 · NAV vs 基准 —— 归一化起点 = 1</div>
    <div class="curvebox"><div class="cv-head"><span>实线=组合 NAV · 虚线=基准 · 空仓期 NAV 平直是真实状态不是缺图</span><span>${navRows.length ? esc(navRows[0].nav_date) + " → " + esc(navRows[navRows.length - 1].nav_date) : ""}</span></div>
      <svg id="papernav" viewBox="0 0 720 180" preserveAspectRatio="none"></svg>
      <div class="cv-axis"><span>${navRows.length ? esc(navRows[0].nav_date) : ""}</span><span>归一化净值 · 基准=paper 域自带 bench_close</span><span>${navRows.length ? esc(navRows[navRows.length - 1].nav_date) : ""}</span></div></div>
    <div class="sec-title" style="margin-top:48px">持仓 · POSITIONS</div>
    ${pos.length ? `<div class="dtable" style="margin-top:20px">${pos.map(p => `<div class="lvrow" style="cursor:default"><span class="lv-name">${esc(p.ts_code || p.name || "—")}</span><span class="lv-num">${esc(JSON.stringify(p))}</span></div>`).join("")}</div>`
      : `<div class="typed-empty" style="margin-top:20px"><span class="lamp unk"><i></i><b>0 positions</b></span>
        <p>当前无任何观察持仓。<b>条件型空态</b>：账本域健康，只是没有任何策略够格产生持仓 —— 见 LAB 发布门。
        <span class="mono">paper/portfolio · positions=[] · 与 LAB verdict 联动而非独立展示</span></p></div>`}`;
  if (navRows.length > 1) paintNav("papernav", navRows.map(r => r.nav_date), navRows.map(r => r.nav), navRows.map(r => r.bench_close));
}


let currentDomain = "";

function CM_boot() {
  const space = document.body.dataset.space;
  const tab = document.body.dataset.tab;
  const qs = new URLSearchParams(location.search);
  if (typeof OPS !== "undefined" && OPS.timer) clearInterval(OPS.timer);
  if (space === "foundation" && tab === "matrix" && typeof liveMatrix === "function") liveMatrix();
  if (space === "foundation" && tab === "domain") {
    currentDomain = qs.get("domain") || "";
    if (typeof liveDomain === "function") liveDomain(currentDomain);
  }
  if (space === "foundation" && tab === "ops" && typeof opsRefresh === "function") opsRefresh();
  if (space === "lab") {
    if (typeof window.liveLabBoot === "function") window.liveLabBoot();
    return;
  }
  if (space !== "insight") return;
  if (tab === "market" && typeof liveMarket === "function") liveMarket();
  else if (tab === "flows" && typeof liveFlows === "function") liveFlows();
  else if (tab === "warnings" && typeof liveWarnings === "function") liveWarnings();
  else if (tab === "sector" && window.renderDrill) {
    window.DRILL = {
      chain: qs.get("chain") || (window.DRILL && window.DRILL.chain) || "sw_industry",
      code: qs.get("code") || (window.DRILL && window.DRILL.code) || null,
    };
    window.renderDrill();
  }
  else if (tab === "briefing" && typeof liveBriefing === "function") liveBriefing();
  else if (tab === "screener" && typeof liveScreener === "function") liveScreener();
  else if (tab === "dossier" && window.liveDossier) {
    const code = qs.get("code");
    window.DOSSIER = (code && /^\d{6}$/.test(code)) ? { code } : null;
    window.liveDossier();
  }
  else if (tab === "inst" && window.liveInst) {
    window.INST = { open: qs.get("holder") || null };
    window.liveInst();
  }
  else if (tab === "paper" && typeof livePaper === "function") livePaper();
}
window.CM_boot = CM_boot;
CM_boot();

