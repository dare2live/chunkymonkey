/* Shared chrome + real-page navigation. DESIGN.md owner. Prototype hash routing is gone. */
(function () {
  const APP = "/app";
  const TABS = {
    foundation: [
      { id: "matrix", label: "全量矩阵", en: "MATRIX" },
      { id: "ops",    label: "日更",     en: "OPS" },
    ],
    lab: [
      { id: "overview",    label: "实验总览", en: "OVERVIEW" },
      { id: "packages",    label: "策略包",   en: "PACKAGES" },
      { id: "experiments", label: "实验明细", en: "EXPERIMENTS" },
      { id: "release",     label: "发布门",   en: "RELEASE" },
      { id: "snapshots",   label: "快照封存", en: "SNAPSHOTS" },
    ],
    insight: [
      { id: "market",   label: "市场快照", en: "MARKET" },
      { id: "flows",    label: "资金流向", en: "FLOWS" },
      { id: "warnings", label: "退潮预警", en: "WARNINGS" },
      { id: "briefing", label: "盘中简报", en: "BRIEFING" },
      { id: "screener", label: "形态选股", en: "SCREENER" },
      { id: "dossier",  label: "个股档案", en: "DOSSIER" },
      { id: "inst",     label: "机构席位", en: "INSTITUTIONS" },
      { id: "paper",    label: "观察账本", en: "LEDGER" },
    ],
  };
  const SUB_LABEL = {
    "foundation/domain": "域详情",
    "lab/expdetail": "消融详情",
    "insight/sector": "板块下钻",
  };
  const SPACE_NAME = {
    foundation: "FOUNDATION · 底座",
    lab: "LAB · 实验室",
    insight: "INSIGHT · 洞察",
  };
  const SPACE_HOME = {
    foundation: "foundation/matrix",
    lab: "lab/overview",
    insight: "insight/market",
  };

  function pagePath(space, tab) {
    return `${APP}/${space}/${tab}.html`;
  }

  function goto(nav, extra) {
    extra = extra || {};
    const parts = String(nav || "").split("/");
    const space = parts[0];
    const tab = parts[1];
    if (!space || !tab) return;
    const url = new URL(pagePath(space, tab), location.origin);
    if (extra.code) url.searchParams.set("code", extra.code);
    if (extra.holder) url.searchParams.set("holder", extra.holder);
    if (extra.chain) url.searchParams.set("chain", extra.chain);
    if (extra.domain) url.searchParams.set("domain", extra.domain);
    if (extra.family) url.searchParams.set("family", extra.family);
    if (extra.block) url.searchParams.set("block", extra.block);
    const next = url.pathname + url.search;
    if (location.pathname + location.search === next) {
      if (typeof window.CM_boot === "function") window.CM_boot();
      window.scrollTo(0, 0);
      return;
    }
    location.href = next;
  }

  function paintChrome() {
    const space = document.body.dataset.space || "foundation";
    const tab = document.body.dataset.tab || "matrix";
    const header = document.getElementById("site-header");
    if (header) {
      header.innerHTML =
        `<div class="brand">ChunkyMonkey</div>
         <nav class="spaces">
           <button type="button" data-space="foundation" data-nav="foundation/matrix">FOUNDATION · 底座</button>
           <button type="button" data-space="lab" data-nav="lab/overview">LAB · 实验室</button>
           <button type="button" data-space="insight" data-nav="insight/market">INSIGHT · 洞察</button>
         </nav>`;
      header.querySelectorAll("[data-space]").forEach((b) => {
        b.classList.toggle("on", b.dataset.space === space);
      });
    }
    const bar = document.getElementById("tabbar");
    if (bar && TABS[space]) {
      let html = `<span class="tb-space">${SPACE_NAME[space]}</span>`;
      TABS[space].forEach((t) => {
        html += `<button type="button" class="${t.id === tab ? "on" : ""}" data-nav="${space}/${t.id}">${t.label}<span class="cnt">${t.en}</span></button>`;
      });
      const sub = `${space}/${tab}`;
      if (SUB_LABEL[sub]) {
        html += `<button type="button" class="on" data-nav="${sub}">→ ${SUB_LABEL[sub]}</button>`;
      }
      bar.innerHTML = html;
    }
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("a.xq")) return;
    const t = e.target.closest("[data-nav]");
    if (!t) return;
    e.preventDefault();
    const nav = t.dataset.nav;
    const extra = {};
    if (t.dataset.code) {
      extra.code = nav === "insight/dossier"
        ? String(t.dataset.code).replace(/\D/g, "").slice(0, 6)
        : String(t.dataset.code);
    }
    if (t.dataset.holder) extra.holder = t.dataset.holder;
    if (t.dataset.chain) extra.chain = t.dataset.chain;
    if (t.dataset.domain) extra.domain = t.dataset.domain;
    if (t.dataset.family) extra.family = t.dataset.family;
    if (t.dataset.block) extra.block = t.dataset.block;
    if (nav === "foundation/domain" && !extra.domain) {
      const nm = t.querySelector(".name");
      if (nm && nm.firstChild) extra.domain = nm.firstChild.textContent.trim();
    }
    if (nav === "insight/sector" && window.DRILL) {
      extra.chain = extra.chain || window.DRILL.chain;
      extra.code = extra.code || window.DRILL.code;
    }
    goto(nav, extra);
  });

  paintChrome();

  window.CM = {
    APP, TABS, SUB_LABEL, SPACE_NAME, SPACE_HOME, pagePath, goto, paintChrome,
  };
})();
