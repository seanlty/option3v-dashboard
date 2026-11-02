/* The preview shares the live dashboard, with a separate portfolio and quote view. */
window.dashboardPreview = (() => {
  let catalog = null;
  let live = null;
  let initialPosition = true;
  let selectedStrike = null;
  let focusSource = null;
  let focusKey = "";
  let focusRows = [];
  let focusTimer = 0;
  let retryAfter = 0;
  let nextFocusCheck = 0;
  const requestedStrike = Number(new URLSearchParams(location.search).get("strike")) || null;

  function hasPrice(leg) {
    return leg && [leg.bid, leg.ask, leg.last].some(value => Number(value) > 0);
  }

  function merge(snapshot) {
    live = snapshot;
    const matching = catalog && (!snapshot || catalog.contract_month === snapshot.contract_month) ? catalog : null;
    const center = snapshot?.underlying?.price || matching?.center;
    if (!center) return snapshot;
    const rows = new Map();
    for (const row of matching?.rows || []) {
      if (Math.abs(row.strike - center) <= T_QUOTE_STRIKE_RANGE) rows.set(row.strike, {...row});
    }
    for (const row of snapshot?.rows || []) {
      if (Math.abs(row.strike - center) > T_QUOTE_STRIKE_RANGE) continue;
      const merged = rows.get(row.strike) || {strike: row.strike};
      for (const side of ["call", "put"]) {
        const leg = row[side];
        if (!hasPrice(leg)) continue;
        const streamed = !snapshot.stale && snapshot.source?.type === "fugle_live";
        merged[side] = {...leg, feed: streamed ? "stream" : "fugleSnapshot",
          quote_at: leg.quote_at || leg.aggregate_at || snapshot.snapshot_at};
      }
      rows.set(row.strike, merged);
    }
    for (const row of focusRows) {
      const merged = rows.get(row.strike);
      if (!merged) continue;
      for (const side of ["call", "put"]) {
        const leg = row[side];
        if (hasPrice(leg)) merged[side] = {...leg, feed:"stream", quote_at:leg.time || leg.last_updated};
      }
    }
    const result = snapshot || {contract_month: matching.contract_month, settlement_date: matching.settlement_date,
      underlying: {symbol: matching.future_symbol, price: center}, status: "快照", source: {type: "finmind"}};
    return {...result, underlying: {...result.underlying, price: center, symbol: result.underlying?.symbol || matching?.future_symbol},
      rows: [...rows.values()].sort((a, b) => a.strike - b.strike)};
  }

  function closeFocus() {
    focusSource?.close();
    focusSource = null;
    focusRows = [];
    focusKey = "";
  }

  function checkFocus() {
    focusTimer = 0;
    if (!window.EventSource || !live || document.hidden) { closeFocus(); return; }
    const wrap = document.querySelector(".t-quote-wrap");
    const box = wrap.getBoundingClientRect();
    const top = Math.max(0, box.top) + wrap.querySelector("thead").offsetHeight;
    const bottom = Math.min(window.innerHeight, box.bottom);
    if (top >= bottom) { closeFocus(); return; }
    const base = new Set(live.metadata?.selected_strikes || live.rows.map(row => row.strike));
    const midpoint = (top + bottom) / 2;
    const visible = [...document.querySelectorAll("#tQuoteBody tr[data-strike]")].map(row => {
      const rect = row.getBoundingClientRect();
      return {strike:Number(row.dataset.strike), rect, distance:Math.abs((rect.top + rect.bottom) / 2 - midpoint)};
    }).filter(row => row.rect.bottom > top && row.rect.top < bottom && !base.has(row.strike));
    const strikes = visible.sort((a,b) => a.distance - b.distance).slice(0,8).map(row => row.strike).sort((a,b) => a-b);
    const key = strikes.length ? [live.contract_month, live.session, ...strikes].join(",") : "";
    if (key === focusKey) return;
    closeFocus();
    if (!key || Date.now() < retryAfter || Date.now() < nextFocusCheck) return;
    focusKey = key;
    nextFocusCheck = Date.now() + 2500;
    const params = new URLSearchParams({contract:live.contract_month, strikes:strikes.join(",")});
    const source = focusSource = new EventSource("/api/preview/focus-events?" + params);
    const fallback = () => {
      if (source !== focusSource) return;
      closeFocus();
      retryAfter = Date.now() + 15000;
      applyFugleTQuotePayload(live);
    };
    source.onmessage = event => {
      if (source !== focusSource) return;
      const data = JSON.parse(event.data);
      if (data.status !== "stream" || data.contract_month !== live?.contract_month) { fallback(); return; }
      focusRows = data.rows;
      applyFugleTQuotePayload(live);
    };
    source.onerror = fallback;
  }

  function scheduleFocus() {
    if (!focusTimer) focusTimer = window.setTimeout(checkFocus, 350);
  }

  function timeLabel(value) {
    if (!value) return "";
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2} /.test(value)) {
      return value.slice(0, 19).match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)?.[0].slice(5) || "";
    }
    let stamp = value;
    if (/^\d{10,16}$/.test(String(value))) {
      const digits = String(value).length;
      stamp = Number(value) * (digits <= 10 ? 1000 : digits > 13 ? 0.001 : 1);
    }
    const date = new Date(stamp);
    if (!Number.isFinite(date.getTime())) return "";
    return date.toLocaleString("sv-SE", {timeZone:"Asia/Taipei"}).slice(5, 19);
  }

  function sources(row) {
    const names = {stream:"串流", finmind:"快照", fugleSnapshot:"快照", unquoted:"無報價"};
    return ["call", "put"].map((side, i) => {
      const leg = row[side], stamp = timeLabel(leg?.quote_at);
      const label = leg ? (names[leg.feed] || "無報價") : (catalog?.has_listing ? "未掛牌" : "無資料");
      const source = leg?.feed === "finmind" ? "FinMind" : leg?.feed === "stream" || leg?.feed === "fugleSnapshot" ? "Fugle" : "";
      return `<small class="feed-note" data-feed="${leg?.feed || "none"}" title="${source} ${label} ${stamp}">${i ? "P" : "C"} ${label} ${stamp ? stamp.slice(0, 11) : "—"}</small>`;
    }).join("");
  }

  function positionRow(strike, explicit = false) {
    const row = document.querySelector(`#tQuoteBody tr[data-strike="${Number(strike)}"]`);
    if (!row) return false;
    const wrap = row.closest(".t-quote-wrap");
    const rect = wrap.getBoundingClientRect();
    wrap.scrollTop += row.getBoundingClientRect().top - rect.top - (wrap.clientHeight - row.offsetHeight) / 2;
    const cell = row.querySelector(".strike-cell");
    wrap.scrollLeft += cell.getBoundingClientRect().left - rect.left - (wrap.clientWidth - cell.offsetWidth) / 2;
    if (explicit) {
      document.querySelector("#tQuoteBody .selected-strike")?.classList.remove("selected-strike");
      selectedStrike = strike;
      document.querySelector("#strikeMessage").textContent = `已定位 ${formatNumber(strike, 0)}`;
    }
    row.classList.toggle("selected-strike", explicit || strike === selectedStrike);
    return true;
  }

  function afterRender(rows, atm) {
    const range = document.getElementById("previewRange");
    if (!range) return;
    range.textContent = `${formatNumber(rows[0].strike, 0)} – ${formatNumber(rows.at(-1).strike, 0)} · ${rows.length} 個履約價`
      + (catalog?.has_listing ? "" : " · 等待掛牌清單");
    [...document.querySelectorAll("#tQuoteBody tr")].forEach((el,i) => {
      el.dataset.strike = rows[i].strike;
      el.classList.toggle("selected-strike", rows[i].strike === selectedStrike);
    });
    if (initialPosition && catalog && rows.length) {
      initialPosition = false;
      if (!requestedStrike || !positionRow(requestedStrike, true)) {
        positionRow(atm);
        if (requestedStrike) document.querySelector("#strikeMessage").textContent = "此履約價不在目前區間內";
      }
    }
    scheduleFocus();
  }

  async function refreshCatalog() {
    try {
      const response = await fetch("/api/preview/chain", {cache:"no-store"});
      if (!response.ok) throw new Error("snapshot");
      catalog = await response.json();
      applyFugleTQuotePayload(live);
      document.getElementById("catalogMessage").textContent = "";
    } catch {
      document.getElementById("catalogMessage").textContent = "快照暫時無法更新";
    } finally {
      window.setTimeout(refreshCatalog, 30000);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.title = "滿版測試 · OP3V 月選擇權部位監控";
    const grid = document.querySelector(".dashboard-grid");
    for (const panel of ["market", "t-quote", "positions", "payoff", "option-chain", "regime", "calendar"]) {
      grid.append(grid.querySelector(`.${panel}-panel`));
    }
    document.querySelector(".eyebrow").textContent = "OP3V  /  版面測試";
    const heading = document.querySelector("h1");
    heading.textContent = "月選擇權部位監控";
    const nav = document.createElement("nav");
    nav.className = "preview-nav";
    nav.innerHTML = '<a href="#t-quote">T 字報價 ↓</a><a href="/">正式版 ↗</a>';
    document.querySelector(".topbar").append(nav);
    // The base URL keeps static assets shared. Fragment links must keep the preview path.
    nav.firstElementChild.href = location.pathname + location.search + "#t-quote";
    const controls = document.querySelector("#marketIndexPane .data-controls");
    const credit = controls.lastElementChild;
    document.getElementById("marketStatus").after(credit);
    document.querySelector(".market-panel .panel-header").append(controls);
    const tape = document.querySelector(".t-quote-panel");
    tape.id = "t-quote";
    const wrap = tape.querySelector(".t-quote-wrap");
    wrap.addEventListener("scroll", scheduleFocus, {passive:true});
    window.addEventListener("scroll", scheduleFocus, {passive:true});
    document.addEventListener("visibilitychange", scheduleFocus);
    window.addEventListener("pagehide", closeFocus);
    wrap.after(tape.querySelector(".t-vix-panel"));
    wrap.insertAdjacentHTML("beforebegin", `<div class="strike-toolbar">
      <div><strong id="previewRange">等待掛牌資料</strong>
      <small>Fugle 串流與行情快照 · 各側標示報價時間</small></div>
      <form id="strikeJump"><label for="strikeInput">定位履約價</label>
      <input id="strikeInput" inputmode="numeric" placeholder="例 47000" autocomplete="off">
      <button type="submit" class="secondary-action">定位</button>
      <button type="button" id="jumpAtm" class="ghost-action">回價平</button></form>
      <span id="strikeMessage" role="status"></span><span id="catalogMessage" role="status"></span></div>`);
    document.getElementById("strikeJump").addEventListener("submit", event => {
      event.preventDefault();
      initialPosition = false;
      const strike = Number(document.getElementById("strikeInput").value.replaceAll(",", "").trim());
      if (!positionRow(strike, true)) document.getElementById("strikeMessage").textContent = "此履約價不在目前清單內";
    });
    document.getElementById("jumpAtm").addEventListener("click", () => {
      const atm = document.querySelector("#tQuoteBody .atm-row");
      if (atm) positionRow(Number(atm.dataset.strike), true);
    });
    if (requestedStrike) document.getElementById("strikeInput").value = requestedStrike;
    if (location.hash === "#t-quote") tape.scrollIntoView();
    refreshCatalog();
  });
  return {merge, sources, afterRender};
})();
