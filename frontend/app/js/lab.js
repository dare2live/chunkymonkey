/* Lab observation surface. DESIGN.md owner. Reads /api/v3/lab only. Never paints claimable. */
(function () {
  const wrap = () => document.getElementById("lab-body");

  async function labget(p) {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), 30000);
    try {
      const r = await fetch(p, { signal: c.signal });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } finally {
      clearTimeout(t);
    }
  }

  function emptyHtml(title, text) {
    return `<div class="typed-empty" style="margin-top:40px"><span class="lamp unk"><i></i><b>${esc(title)}</b></span>
      <p>${text}<span class="mono">lab observation · typed empty · 不回落过期判决数字</span></p></div>`;
  }

  function vchip(verdict) {
    const v = String(verdict || "unknown").toLowerCase();
    const cls = v === "reject" ? "reject" : v === "inconclusive" ? "inconclusive" : v === "accept" ? "accept" : "";
    return `<span class="vchip ${cls}">${esc(v)}</span>`;
  }

  function pct(v) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    const x = n * 100;
    return (x >= 0 ? "+" : "") + x.toFixed(Math.abs(x) >= 10 ? 2 : 3) + "%";
  }
  function pctAbs(v) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return (Math.abs(n) * 100).toFixed(2) + "%";
  }

  function shortHash(h) {
    const s = String(h || "");
    return s.length > 16 ? s.slice(0, 16) + "…" : (s || "—");
  }

  function gateSym(state) {
    if (state === "pass") return "✓";
    if (state === "partial") return "◐";
    if (state === "unknown") return "?";
    if (state === "blocked") return "—";
    return "✗";
  }

  function rung(name, note, tag, kind) {
    return `<div class="rung ${kind}">
      <span class="dot"></span>
      <span class="r-name">${esc(name)}</span>
      <div class="r-note">${note}</div>
      <span class="r-tag">${esc(tag)}</span>
    </div>`;
  }

  function familyCard(family, rows, extra) {
    const verdicts = rows.map((r) => String(r.verdict || "unknown"));
    const familyVerdict = verdicts.includes("accept") ? "ACCEPT"
      : verdicts.includes("reject") ? "REJECT"
      : verdicts.includes("inconclusive") ? "INCONCLUSIVE"
      : "UNKNOWN";
    const chips = rows.map((r) => `${vchip(r.verdict)}`).join("");
    const blocks = rows.map((r) => `<span class="done">${esc(r.block_label || r.block)}</span>`).join("");
    const snap = (rows[0] && rows[0].snapshot_id) || "—";
    return `<div class="scard" data-nav="lab/experiments">
      <span class="idx">${esc(family)}</span>
      <h3>${esc(extra.title)}</h3>
      <div class="en">${esc(extra.en)}</div>
      <div class="blocks">${blocks}</div>
      <div class="blocks" style="margin-top:8px">${chips}</div>
      <div class="verdict-zone">
        <div class="verdict-label">Ablation family · 不是 StrategySpec</div>
        <div class="verdict">${esc(familyVerdict)}</div>
        <div class="meta">快照 <b>${esc(snap)}</b><br>claimable=<b>false</b> · ${esc(extra.note)}</div>
      </div>
    </div>`;
  }

  async function paintOverview() {
    const el = wrap(); if (!el) return;
    let d;
    try { d = await labget("/api/v3/lab/overview"); }
    catch (e) {
      el.innerHTML = emptyHtml("UNAVAILABLE", `实验室后端不可达：${esc(e.message)}。<br>`);
      return;
    }
    const fw = d.framework || {};
    const live = fw.live_inputs || {};
    const rx = fw.formal_rx_compute || {};
    const pkgs = (d.packages && d.packages.packages) || [];
    const exps = d.experiments || [];
    const rel = d.release || {};
    const byFam = {};
    exps.forEach((row) => {
      (byFam[row.family] || (byFam[row.family] = [])).push(row);
    });
    const ladder = [
      rung("LOCAL SMOKE", `本地冒烟 · ${fw.framework_ready ? "<b>framework_ready</b>" : "未就绪"}<br>小样本快检，不产 Release`, fw.execution_mode || "manual_only", fw.framework_ready ? "on" : "lock"),
      rung("RX BATCH", `批量实验 · ${fw.formal_rx_authorized ? "<b>已授权</b>" : "未授权"}<br>allowed=${String(!!rx.allowed)} · claimable=${String(!!rx.claimable)}`, fw.formal_rx_authorized ? "RX locked to goal.md" : "未授权", fw.formal_rx_authorized ? "sched" : "lock"),
      rung("OPTUNA", `超参搜索 · <b>未开 runner</b><br>yaml 第二钥保持空`, "未授权", "lock"),
      rung("REMOTE", `远程算力 · <b>未授权</b><br>协议与预算先行`, "未授权", "lock"),
    ].join("");
    const specCards = pkgs.map((p) => {
      const spec = (p.specs && p.specs[0]) || {};
      return `<div class="scard" data-nav="lab/packages">
        <span class="idx">${esc(p.package_id)}</span>
        <h3>${esc(p.package_id)}</h3>
        <div class="en">paper ${esc(spec.paper_status || "—")} · pnl ${esc(spec.pnl_source || "—")}</div>
        <div class="blocks">${(p.layers || []).map((ly) => `<span>${esc(ly.label)}</span>`).join("")}</div>
        <div class="verdict-zone">
          <div class="verdict-label">StrategySpec · 非 Release</div>
          <div class="verdict" style="font-size:28px">LOADED</div>
          <div class="meta">claimable=<b>false</b><br>加载 ≠ 可申报</div>
        </div>
      </div>`;
    }).join("");
    const ablation = [
      familyCard("institution_follow_v1", byFam.institution_follow_v1 || [], {
        title: "机构跟随 · 消融",
        en: "PHASE E · ablation_only",
        note: "不是跟随 spec 纸面",
      }),
      familyCard("main_rally_v1", byFam.main_rally_v1 || [], {
        title: "主升浪 · setup 消融",
        en: "PHASE F · ablation_only",
        note: "不是 full-episode 猎手",
      }),
    ].join("");
    const gates = (rel.gates || []).map((g) =>
      `<span class="rgate ${g.state === "pass" ? "done" : g.state === "partial" ? "part" : ""}"><i></i>${esc(g.label)} · ${esc(g.state)}</span>`
    ).join("");
    el.innerHTML = `
      <div class="stale-banner soft" style="margin-top:32px">
        <span class="lamp ${fw.framework_ready ? "ok" : "hard"}"><i></i><b>${fw.framework_ready ? "FRAMEWORK READY" : "NOT READY"}</b></span>
        <span style="font-size:12.5px;color:var(--ink-2);line-height:1.7">surface <span class="mono">${esc(d.surface_status || "")}</span>
          · holdout ${esc(live.holdout_start || "—")} · train_end ${esc(live.train_end || "—")}
          · claimable=<b>false</b> · StrategyRelease 未发布。画像 / spec 纸面 / 消融三层不许混称。</span>
      </div>
      <div class="epigraph">
        “不存在任何已发布生产策略。E/F 是诚实 reject，不是 Release。”
        <span class="src">—— strategy_validation_contract.md §9 / §10 · 现查 /api/v3/lab</span>
      </div>
      <div class="sec-title" style="margin-top:64px">算力阶梯 · COMPUTE LADDER</div>
      <div class="compute-ladder">${ladder}</div>
      <div class="sec-title" style="margin-top:72px">策略包 · PACKAGES —— typed spec，不是证书</div>
      <div class="strategies">${specCards}</div>
      <div class="sec-title" style="margin-top:72px">消融族 · ABLATION —— 同快照同折同成本下的判决</div>
      <div class="strategies">${ablation}</div>
      <div class="release-strip" style="margin-top:56px">
        <span class="rs-title">Release Gates §9</span>
        ${gates}
        <span class="rs-end" data-nav="lab/release" style="cursor:pointer">九门详表 →</span>
      </div>`;
  }

  async function paintPackages() {
    const el = wrap(); if (!el) return;
    let d;
    try { d = await labget("/api/v3/lab/packages"); }
    catch (e) { el.innerHTML = emptyHtml("UNAVAILABLE", `${esc(e.message)}<br>`); return; }
    const pkgs = d.packages || [];
    el.innerHTML = `<div class="stale-banner soft" style="margin-top:32px">
        <span class="lamp ${d.loaded ? "ok" : "hard"}"><i></i><b>${d.loaded ? "LOADED" : "BLOCKED"}</b></span>
        <span style="font-size:12.5px;color:var(--ink-2);line-height:1.7">三包 StrategySpec 可加载。claimable=<b>false</b>。跟随纸面 ≠ 画像 α ≠ E 消融。</span>
      </div>` + pkgs.map((p) => {
        const spec = (p.specs && p.specs[0]) || {};
        const layers = (p.layers || []).map((ly) =>
          `<div class="gaterow"><span class="g-sym">${ly.role === "not_implemented" ? "—" : "·"}</span>
            <span class="g-name">${esc(ly.label)}</span>
            <span class="g-detail">${esc(ly.role)} · 不是 ${esc(ly.not)}</span></div>`
        ).join("");
        const unimplemented = (p.layers || []).filter((ly) => ly.role === "not_implemented");
        const emptyNote = unimplemented.length
          ? `<div class="typed-empty" style="margin-top:16px"><span class="lamp unk"><i></i><b>CAPABILITY EMPTY</b></span>
              <p>${unimplemented.map((ly) => esc(ly.label)).join(" · ")} 未实现。这是能力空态，不是 0。<span class="mono">${esc(p.package_id)}</span></p></div>`
          : "";
        const extra = p.package_id === "formulas"
          ? `<p class="mono" style="font-size:11px;color:var(--ink-3);margin-top:12px">formula_ids · ${(p.specs || []).map((s) => esc(s.spec_id)).join(" · ")}</p>`
          : "";
        return `<div class="sec-title" style="margin-top:48px">${esc(p.package_id)}</div>
          <div class="qtable">${layers}</div>
          <div class="evidence" style="margin-top:16px">
            <h4>SPEC FIELDS · 现查 YAML</h4>
            <div class="ev-line"><span>candidate</span> · ${esc(spec.candidate_generation || "—")}</div>
            <div class="ev-line"><span>entry</span> ····· ${esc(spec.entry_kind || "—")} after ${esc(spec.entry_after || "—")}</div>
            <div class="ev-line"><span>exit</span> ······ ${esc(spec.exit_kind || "—")} / ${esc(spec.exit_event || "—")}</div>
            <div class="ev-line"><span>pnl_source</span> · ${esc(spec.pnl_source || "—")}</div>
            <div class="ev-line"><span>paper_status</span> ${esc(spec.paper_status || "—")}</div>
            <div class="ev-line"><span>config_hash</span> · ${esc(shortHash(spec.config_hash))}</div>
          </div>${extra}${emptyNote}`;
      }).join("");
  }

  function expNote(row) {
    const g = (row.edge_gates && row.edge_gates.checks) || {};
    const parts = [
      `eval <b>${pct(g.eval_total_return)}</b>`,
      `holdout ${pct(g.holdout_net_return)}`,
      `maxDD ${pctAbs(g.max_drawdown)}`,
      `n=${g.n_trades_completed ?? "—"}`,
    ];
    return parts.join(" · ") + (row.reason ? ` —— ${esc(row.reason)}` : "");
  }

  async function paintExperiments() {
    const el = wrap(); if (!el) return;
    let d;
    try { d = await labget("/api/v3/lab/experiments"); }
    catch (e) { el.innerHTML = emptyHtml("UNAVAILABLE", `${esc(e.message)}<br>`); return; }
    const rows = d.experiments || [];
    el.innerHTML = `<div class="dtable" style="border-top-color:var(--ink); margin-top:32px">
      <div class="exprow" style="cursor:default; border-bottom-color:var(--ink)">
        <span class="xp-fam">EXPERIMENT</span>
        <span class="xp-fam">BLOCK</span>
        <span class="xp-fam">VERDICT</span>
        <span class="xp-fam">MEASURED</span>
        <span class="xp-fam">ROLE</span>
      </div>
      ${rows.map((r) => `<div class="exprow" data-nav="lab/expdetail" data-family="${esc(r.family)}" data-block="${esc(r.block)}">
        <span class="xp-id">${esc(r.family)}:${esc((r.block || "").toUpperCase())}</span>
        <span class="xp-block">${esc(r.block_label || r.block)}</span>
        <span>${vchip(r.verdict)}</span>
        <span class="xp-note">${expNote(r)}</span>
        <span class="xp-fam">${esc(r.role || "ablation_only")}</span>
      </div>`).join("")}
    </div>
    <p class="mono" style="margin-top:16px;font-size:10.5px;color:var(--ink-3)">n=${rows.length} · claimable=false · 消融 JSON 不是跟随 spec / 不是 rally hunter</p>`;
  }

  async function paintExpdetail() {
    const el = wrap(); if (!el) return;
    const qs = new URLSearchParams(location.search);
    const family = qs.get("family");
    const block = qs.get("block");
    if (!family || !block) {
      el.innerHTML = emptyHtml("NEED QUERY", `缺少 <span class="mono">family</span> / <span class="mono">block</span>。从实验明细点一行进入。<br>`);
      return;
    }
    let d;
    try { d = await labget(`/api/v3/lab/experiments/${encodeURIComponent(family)}/${encodeURIComponent(block)}`); }
    catch (e) { el.innerHTML = emptyHtml("UNAVAILABLE", `${esc(e.message)}<br>`); return; }
    const r = d.experiment || {};
    const g = (r.edge_gates && r.edge_gates.checks) || {};
    const evalRet = Number(g.eval_total_return);
    const width = Number.isFinite(evalRet) ? Math.min(50, Math.abs(evalRet) * 50) : 6;
    const barCls = evalRet < 0 ? "neg" : "pos";
    const checks = Object.entries(g).map(([k, v]) => {
      const ok = v === true;
      const bad = v === false;
      const sym = ok ? "✓" : bad ? "✗" : "·";
      const color = ok ? "var(--ok)" : bad ? "var(--hard)" : "var(--ink-3)";
      const shown = typeof v === "number"
        ? (k.indexOf("drawdown") >= 0 ? pctAbs(v) : k.indexOf("return") >= 0 ? pct(v) : String(v))
        : String(v);
      return `<div class="gaterow"><span class="g-sym" style="color:${color}">${sym}</span><span class="g-name">${esc(k)}</span><span class="g-detail">${esc(shown)}</span></div>`;
    }).join("");
    el.innerHTML = `
      <div class="hero compact" style="margin-top:20px">
        <div class="kicker">Lab / Experiment Detail · ${esc(r.family)} · ${esc(r.block_label)} · ${esc(r.role)}</div>
        <h1 class="sm">${esc((r.block || "").toUpperCase())} ${vchip(r.verdict)}</h1>
        <p class="sub">${esc(r.reason || "")} · claimable=<b>false</b> · 不是 StrategyRelease</p>
      </div>
      <div class="ablation">
        <div class="abrow">
          <span class="ab-name">${esc((r.block || "").toUpperCase())}<span class="cn">${esc(r.block_label || "")}</span></span>
          <div class="ab-track">
            <span class="ab-zero"></span>
            <span class="ab-bar ${barCls}" style="width:${Math.max(6, width)}%">${pct(g.eval_total_return)}</span>
          </div>
          <div class="ab-meta">holdout ${pct(g.holdout_net_return)} · maxDD ${pctAbs(g.max_drawdown)}<br>n_trades ${g.n_trades_completed ?? "—"} · ${esc(r.edge_gates && r.edge_gates.reason || "")}</div>
        </div>
      </div>
      <div class="sec-title" style="margin-top:64px">判决清单 · EDGE GATES</div>
      <div class="qtable">${checks}</div>
      <div class="evidence">
        <h4>EVIDENCE · 压缩投影（分区清单不下发）</h4>
        <div class="ev-line"><span>experiment_id</span> · ${esc(r.experiment_id || "—")}</div>
        <div class="ev-line"><span>snapshot</span> ········ ${esc(r.snapshot_id || "—")}</div>
        <div class="ev-line"><span>snapshot_hash</span> ·· ${esc(shortHash(r.snapshot_hash))}</div>
        <div class="ev-line"><span>coverage</span> ······· ${esc((r.coverage && r.coverage.window_start) || "—")} → ${esc((r.coverage && r.coverage.window_end) || "—")} · n=${(r.coverage && r.coverage.accepted_nominal_day_count) ?? "—"} · partitions omitted</div>
        <div class="ev-line"><span>frozen_at</span> ······· ${esc(r.frozen_at || "—")}</div>
      </div>`;
  }

  async function paintRelease() {
    const el = wrap(); if (!el) return;
    let d;
    try { d = await labget("/api/v3/lab/release"); }
    catch (e) { el.innerHTML = emptyHtml("UNAVAILABLE", `${esc(e.message)}<br>`); return; }
    const rows = (d.gates || []).map((g) => {
      const color = g.state === "pass" ? "var(--ok)" : g.state === "partial" ? "var(--soft)" : g.state === "fail" ? "var(--hard)" : "var(--ink-3)";
      return `<div class="gaterow"${g.id === "accept" ? ' style="background:var(--paper-2)"' : ""}>
        <span class="g-sym" style="color:${color}">${gateSym(g.state)}</span>
        <span class="g-name">${esc(g.label)}</span>
        <span class="g-detail">${esc(g.detail)}</span></div>`;
    }).join("");
    el.innerHTML = `<div class="qtable" style="margin-top:40px">${rows}</div>
      <div style="margin-top:72px; border-top:1px solid var(--ink); padding-top:28px; display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap:16px">
        <span style="font-size:clamp(28px,4vw,44px); font-weight:680; letter-spacing:-0.02em">STRATEGY RELEASE — 未发布</span>
        <span class="mono" style="font-size:11px; color:var(--ink-3)">accept ${d.n_accept}/${d.n_experiments} · claimable=false</span>
      </div>`;
  }

  async function paintSnapshots() {
    const el = wrap(); if (!el) return;
    let d;
    try { d = await labget("/api/v3/lab/snapshots"); }
    catch (e) { el.innerHTML = emptyHtml("UNAVAILABLE", `${esc(e.message)}<br>`); return; }
    const cards = (d.cards || []).map((c) => {
      if (c.kind === "holdout_seal") {
        return `<div class="snap">
          <span class="sn-kind">Holdout Seal</span>
          <h4>sealed ≥ ${esc(c.holdout_start || "—")}</h4>
          <div class="sn-meta">opaque=${String(!!c.opaque)} · partitions omitted<br>hash <b>${esc(shortHash(c.seal_hash))}</b><br>单触原则 · 本页不打开分区</div>
        </div>`;
      }
      return `<div class="snap">
        <span class="sn-kind">${esc(c.kind)}</span>
        <h4>${esc(c.snapshot_id || "—")}</h4>
        <div class="sn-meta">frozen <b>${esc(c.frozen_at || "—")}</b><br>
          ${c.shadow_overall_status ? "shadow " + esc(c.shadow_overall_status) + "<br>" : ""}
          ${esc(c.relpath || "")}</div>
      </div>`;
    }).join("");
    el.innerHTML = `<div class="snap-grid">${cards}
      <div class="snap" style="background:var(--paper-2)">
        <span class="sn-kind">Pre-registration</span>
        <h4 style="font-family:inherit; font-size:13.5px; line-height:1.8; font-weight:500">换快照、换 universe、换折、换成本，任一变更都开启一份新研究，旧判决不失效也不迁移。</h4>
        <div class="sn-meta">strategy_validation_contract.md · 冻结条款</div>
      </div>
    </div>`;
  }

  window.liveLabBoot = function () {
    const tab = document.body.dataset.tab;
    if (tab === "overview") paintOverview();
    else if (tab === "packages") paintPackages();
    else if (tab === "experiments") paintExperiments();
    else if (tab === "expdetail") paintExpdetail();
    else if (tab === "release") paintRelease();
    else if (tab === "snapshots") paintSnapshots();
  };
  if (document.body && document.body.dataset.space === "lab") window.liveLabBoot();
})();
