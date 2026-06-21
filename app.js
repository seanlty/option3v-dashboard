const POINT_VALUE = 50;
const STORAGE_KEY = "txo-dashboard-positions-v1";
const FINMIND_URL = "https://api.finmindtrade.com/api/v4/data";
const T_QUOTE_STRIKE_RANGE = 5000;
const SAMPLE_MONTHLY_STRIKE_STEP = 100;
const QUOTE_POLL_MS = 5000;
const FUTURE_SPECS = {
  TXF: { label: "大台 TXF", multiplier: 200, aliases: ["TXF"] },
  MXF: { label: "小台 MXF", multiplier: 50, aliases: ["MXF", "MTX"] },
  TMF: { label: "微台 TMF", multiplier: 10, aliases: ["TMF"] },
};
const FUTURE_ALIAS_TO_PRODUCT = Object.fromEntries(
  Object.entries(FUTURE_SPECS).flatMap(([product, spec]) => spec.aliases.map((alias) => [alias, product])),
);
const KNOWN_TWSE_CLOSED_DATES = new Set(["2026-05-01"]);
const CHART_PRICE_SCALE_WIDTH = 72;
const FALLBACK_SETTLEMENT_DATES = [
  "2022-01-19", "2022-02-16", "2022-03-16", "2022-04-20", "2022-05-18", "2022-06-15", "2022-07-20", "2022-08-17", "2022-09-21", "2022-10-19", "2022-11-16", "2022-12-21",
  "2023-01-30", "2023-02-15", "2023-03-15", "2023-04-19", "2023-05-17", "2023-06-21", "2023-07-19", "2023-08-16", "2023-09-20", "2023-10-18", "2023-11-15", "2023-12-20",
  "2024-01-17", "2024-02-21", "2024-03-20", "2024-04-17", "2024-05-15", "2024-06-19", "2024-07-17", "2024-08-21", "2024-09-18", "2024-10-16", "2024-11-20", "2024-12-18",
  "2025-01-15", "2025-02-19", "2025-03-19", "2025-04-16", "2025-05-21", "2025-06-18", "2025-07-16", "2025-08-20", "2025-09-17", "2025-10-15", "2025-11-19", "2025-12-17",
  "2026-01-21", "2026-02-23", "2026-03-18", "2026-04-15", "2026-05-20", "2026-06-17", "2026-07-15", "2026-08-19", "2026-09-16", "2026-10-21", "2026-11-18", "2026-12-16",
];

const state = {
  positions: [],
  filter: "all",
  spot: 22600,
  rate: 0.015,
  indexCandles: [],
  optionChain: [],
  automationPositions: [],
  futuresQuote: null,
  futuresQuotes: [],
  fugleTQuote: null,
  fugleTQuoteEventSource: null,
  tVixResizeFrame: null,
  activeMarketTab: "index",
  activeRegimeTab: "decision",
  activeAutomationTab: "current",
  indexQuote: null,
  indexChart: null,
  scoreChart: null,
  scoreDeltaChart: null,
  candleSeries: null,
  scoreSeries: null,
  scoreDeltaSeries: null,
  indexVisibleRangeKey: "",
  chartsSynced: false,
  crosshairsSynced: false,
  isSyncingChartRange: false,
  isSyncingCrosshair: false,
  candleByTime: new Map(),
  scoreByTime: new Map(),
  scoreDeltaByTime: new Map(),
  settlementDates: [...FALLBACK_SETTLEMENT_DATES],
  isFetchingIndex: false,
  isFetchingQuotes: false,
  chartResizeFrame: null,
  chartResizeObserver: null,
  strategyFilters: {
    view: "all",
    volatility: "all",
    timeValue: "all",
  },
};

const els = {};

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  setDefaultDates();
  loadPositions();
  seedMarketData();
  bindEvents();
  renderAll();
  loadSettlementDates().then(renderAll);
  fetchIndexCandles({ auto: true });
  fetchRealtimeQuotes({ auto: true });
  connectFugleTQuote();
  window.setInterval(() => fetchRealtimeQuotes({ auto: true, refresh: true }), QUOTE_POLL_MS);
  window.addEventListener("resize", () => {
    drawPayoff();
    scheduleIndexChartResize();
    scheduleTQuoteVixChartResize();
  });
});

function bindElements() {
  Object.assign(els, {
    positionForm: document.querySelector("#positionForm"),
    addPositionBtn: document.querySelector("#addPositionBtn"),
    entryPriceLabel: document.querySelector("#entryPriceLabel"),
    expiryLabel: document.querySelector("#expiryLabel"),
    positionsBody: document.querySelector("#positionsBody"),
    spotInput: document.querySelector("#spotInput"),
    spotReadout: document.querySelector("#spotReadout"),
    payoffSpotReadout: document.querySelector("#payoffSpotReadout"),
    deltaReadout: document.querySelector("#deltaReadout"),
    pnlReadout: document.querySelector("#pnlReadout"),
    expiryReadout: document.querySelector("#expiryReadout"),
    payoffCanvas: document.querySelector("#payoffCanvas"),
    payoffStats: document.querySelector("#payoffStats"),
    startDateInput: document.querySelector("#startDateInput"),
    endDateInput: document.querySelector("#endDateInput"),
    fetchIndexBtn: document.querySelector("#fetchIndexBtn"),
    indexChart: document.querySelector("#indexChart"),
    priceChart: document.querySelector("#priceChart"),
    eventLane: document.querySelector("#eventLane"),
    scoreChart: document.querySelector("#scoreChart"),
    scoreDeltaChart: document.querySelector("#scoreDeltaChart"),
    marketStatus: document.querySelector("#marketStatus"),
    optionDateInput: document.querySelector("#optionDateInput"),
    contractInput: document.querySelector("#contractInput"),
    rateInput: document.querySelector("#rateInput"),
    fetchOptionsBtn: document.querySelector("#fetchOptionsBtn"),
    optionChainBody: document.querySelector("#optionChainBody"),
    optionStatus: document.querySelector("#optionStatus"),
    automationCurrentPane: document.querySelector("#automationCurrentPane"),
    automationHistoryPane: document.querySelector("#automationHistoryPane"),
    automationHistoryContractSelect: document.querySelector("#automationHistoryContractSelect"),
    automationHistoryBody: document.querySelector("#automationHistoryBody"),
    automationHistoryStatus: document.querySelector("#automationHistoryStatus"),
    settlementCalendar: document.querySelector("#settlementCalendar"),
    calendarList: document.querySelector("#calendarList"),
    regimeFramework: document.querySelector("#regimeFramework"),
    regimeAdvice: document.querySelector("#regimeAdvice"),
    regimeDecisionPane: document.querySelector("#regimeDecisionPane"),
    regimeStrategyPane: document.querySelector("#regimeStrategyPane"),
    strategyBody: document.querySelector("#strategyBody"),
    strategyStatus: document.querySelector("#strategyStatus"),
    strategyViewFilter: document.querySelector("#strategyViewFilter"),
    strategyVolFilter: document.querySelector("#strategyVolFilter"),
    strategyTimeFilter: document.querySelector("#strategyTimeFilter"),
    tQuoteBody: document.querySelector("#tQuoteBody"),
    tQuoteStatus: document.querySelector("#tQuoteStatus"),
    tVixValue: document.querySelector("#tVixValue"),
    tVixMeta: document.querySelector("#tVixMeta"),
    tVixChart: document.querySelector("#tVixChart"),
    marketIndexPane: document.querySelector("#marketIndexPane"),
    marketFuturesPane: document.querySelector("#marketFuturesPane"),
  });
}

function bindEvents() {
  els.positionForm.addEventListener("submit", handleAddPosition);
  els.addPositionBtn.addEventListener("click", handleAddPosition);
  els.positionForm.elements.instrument.addEventListener("change", updatePositionFormMode);
  updatePositionFormMode();
  els.spotInput?.addEventListener("input", () => {
    state.spot = number(els.spotInput.value, state.spot);
    renderAll();
  });
  els.rateInput?.addEventListener("input", () => {
    state.rate = number(els.rateInput.value, state.rate);
    renderAll();
  });
  document.querySelectorAll(".segmented button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".segmented button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.filter = button.dataset.filter;
      renderPositions();
    });
  });
  els.fetchIndexBtn.addEventListener("click", fetchIndexCandles);
  els.fetchOptionsBtn?.addEventListener("click", fetchRealtimeQuotes);
  els.positionsBody.addEventListener("click", handlePositionAction);
  els.optionChainBody?.addEventListener("click", handleChainAction);
  els.tQuoteBody.addEventListener("click", handleChainAction);
  els.automationHistoryContractSelect?.addEventListener("change", renderAutomationHistoryTable);
  els.strategyViewFilter.addEventListener("change", handleStrategyFilterChange);
  els.strategyVolFilter.addEventListener("change", handleStrategyFilterChange);
  els.strategyTimeFilter.addEventListener("change", handleStrategyFilterChange);
  document.querySelectorAll("[data-market-tab]").forEach((button) => {
    button.addEventListener("click", () => switchMarketTab(button.dataset.marketTab));
  });
  document.querySelectorAll("[data-regime-tab]").forEach((button) => {
    button.addEventListener("click", () => switchRegimeTab(button.dataset.regimeTab));
  });
  document.querySelectorAll("[data-automation-tab]").forEach((button) => {
    button.addEventListener("click", () => switchAutomationTab(button.dataset.automationTab));
  });
  populateStrategyFilters();
  observeIndexChartSize();
}

function switchMarketTab(tab) {
  state.activeMarketTab = tab || "index";
  document.querySelectorAll("[data-market-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.marketTab === state.activeMarketTab);
  });
  const showIndex = state.activeMarketTab === "index";
  if (els.marketIndexPane) els.marketIndexPane.hidden = !showIndex;
  if (els.marketFuturesPane) els.marketFuturesPane.hidden = showIndex;
  if (showIndex) scheduleIndexChartResize();
}

function switchRegimeTab(tab) {
  state.activeRegimeTab = tab || "decision";
  document.querySelectorAll("[data-regime-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.regimeTab === state.activeRegimeTab);
  });
  const showDecision = state.activeRegimeTab === "decision";
  if (els.regimeDecisionPane) els.regimeDecisionPane.hidden = !showDecision;
  if (els.regimeStrategyPane) els.regimeStrategyPane.hidden = showDecision;
}

function switchAutomationTab(tab) {
  state.activeAutomationTab = tab || "current";
  document.querySelectorAll("[data-automation-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.automationTab === state.activeAutomationTab);
  });
  const showCurrent = state.activeAutomationTab === "current";
  if (els.automationCurrentPane) els.automationCurrentPane.hidden = !showCurrent;
  if (els.automationHistoryPane) els.automationHistoryPane.hidden = showCurrent;
}

function setDefaultDates() {
  const today = new Date();
  const end = toDateInput(today);
  const start = defaultIndexStartDate(today);
  const nextExpiry = thirdWednesday(today.getFullYear(), today.getMonth());
  const expiry = nextExpiry < stripTime(today)
    ? thirdWednesday(today.getFullYear(), today.getMonth() + 1)
    : nextExpiry;

  els.startDateInput.value = start;
  els.endDateInput.value = end;
  if (els.optionDateInput) els.optionDateInput.value = end;
  els.positionForm.elements.expiry.value = toDateInput(expiry);
}

function defaultIndexStartDate(today) {
  return lastSettlementDateOfYear(2025) || toDateInput(addDays(today, -120));
}

function lastSettlementDateOfYear(year) {
  const prefix = `${year}-`;
  return settlementDates()
    .filter((date) => date.startsWith(prefix))
    .sort()
    .at(-1);
}

async function loadSettlementDates() {
  try {
    const response = await fetch("data/settlement_dates.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const dates = Object.values(data)
      .flat()
      .map(normalizeDateValue)
      .filter(Boolean)
      .sort();
    if (dates.length) {
      state.settlementDates = unique(dates);
    }
  } catch (error) {
    state.settlementDates = [...FALLBACK_SETTLEMENT_DATES];
  }
}

function settlementDates() {
  return state.settlementDates.length ? state.settlementDates : FALLBACK_SETTLEMENT_DATES;
}

function loadPositions() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    state.positions = raw ? JSON.parse(raw) : defaultPositions();
  } catch (error) {
    state.positions = defaultPositions();
  }
}

function savePositions() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.positions));
  } catch (error) {
    // Some embedded/local browser contexts block storage; the in-memory state still works.
  }
}

function defaultPositions() {
  const expiry = toDateInput(frontMonthExpiry(new Date()));
  return [
    {
      id: makeId(),
      kind: "live",
      type: "call",
      side: "short",
      strike: 23000,
      qty: 2,
      premium: 155,
      expiry,
    },
    {
      id: makeId(),
      kind: "live",
      type: "put",
      side: "short",
      strike: 22000,
      qty: 2,
      premium: 118,
      expiry,
    },
    {
      id: makeId(),
      kind: "sim",
      type: "put",
      side: "long",
      strike: 21600,
      qty: 1,
      premium: 72,
      expiry,
    },
  ];
}

function seedMarketData() {
  state.indexCandles = sampleIndexCandles();
  state.optionChain = sampleOptionChain();
}

function renderAll() {
  if (els.spotInput) els.spotInput.value = Math.round(state.spot);
  els.spotReadout.textContent = formatNumber(state.spot, 0);
  if (els.payoffSpotReadout) els.payoffSpotReadout.textContent = formatNumber(state.spot, 0);
  renderPositions();
  drawPayoff();
  renderIndexChart();
  renderOptionChain();
  populateAutomationHistoryContracts();
  renderAutomationHistoryTable();
  renderStrategyTable();
  renderTQuoteTable();
  drawTQuoteVixChart();
  renderCalendar();
  renderRiskAndAdvice();
}

function updatePositionFormMode() {
  const instrument = els.positionForm.elements.instrument.value || "option";
  const isFuture = instrument === "future";
  document.querySelectorAll(".option-only").forEach((element) => {
    element.hidden = isFuture;
    element.querySelectorAll("input, select").forEach((control) => { control.disabled = isFuture; });
  });
  document.querySelectorAll(".future-only").forEach((element) => {
    element.hidden = !isFuture;
    element.querySelectorAll("input, select").forEach((control) => { control.disabled = !isFuture; });
  });
  if (els.entryPriceLabel) els.entryPriceLabel.textContent = isFuture ? "建倉價" : "權利金";
  if (els.expiryLabel) els.expiryLabel.textContent = isFuture ? "到期月" : "到期日";
}

function handleAddPosition(event) {
  event.preventDefault();
  const form = new FormData(els.positionForm);
  const instrument = form.get("instrument") || "option";
  const expiry = form.get("expiry");
  const base = {
    id: makeId(),
    kind: form.get("kind"),
    instrument,
    side: form.get("side"),
    qty: Math.max(1, number(form.get("qty"), 1)),
    premium: Math.max(0, number(form.get("premium"), 0)),
    contract: expiryToContract(expiry),
    expiry,
  };
  const position = instrument === "future"
    ? {
        ...base,
        product: normalizeFutureProduct(form.get("futureProduct")),
      }
    : {
        ...base,
        type: form.get("type"),
        strike: number(form.get("strike"), 0),
      };
  state.positions.push(position);
  const synced = syncPositionMarketPrices();
  savePositions();
  renderAll();
  if (!synced) fetchRealtimeQuotes({ refresh: true });
}

function handlePositionAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  if (button.dataset.action === "delete") {
    state.positions = state.positions.filter((position) => position.id !== id);
    savePositions();
    renderAll();
  }
  if (button.dataset.action === "copy") {
    const source = state.positions.find((position) => position.id === id);
    if (!source) return;
    state.positions.push({ ...source, id: makeId(), kind: "sim" });
    savePositions();
    renderAll();
  }
}

function handleChainAction(event) {
  const button = event.target.closest("button[data-action='add-chain']");
  if (!button) return;
  const strike = number(button.dataset.strike, state.spot);
  const type = button.dataset.type;
  const price = number(button.dataset.price, 0);
  const expiry = toDateInput(contractToExpiry(button.dataset.contract) || parseDate(els.positionForm.elements.expiry.value) || frontMonthExpiry(new Date()));
  state.positions.push({
    id: makeId(),
    kind: "sim",
    type,
    side: "long",
    strike,
    qty: 1,
    premium: price,
    contract: button.dataset.contract,
    market: price,
    expiry,
  });
  syncPositionMarketPrices();
  savePositions();
  renderAll();
}

function renderPositions() {
  const positions = filteredPositions();
  els.positionsBody.innerHTML = positions.map((position) => {
    const metrics = positionMetrics(position);
    const pnlClass = metrics.pnl >= 0 ? "positive" : "negative";
    const marketTitle = marketSourceLabel(metrics.marketSource);
    return `
      <tr>
        <td><span class="tag ${position.kind}">${position.kind === "live" ? "實際" : "模擬"}</span></td>
        <td>${contractLabel(position)}</td>
        <td>${position.side === "long" ? "買進" : "賣出"}</td>
        <td>${position.qty}</td>
        <td>${formatNumber(position.premium, 1)}</td>
        <td title="${marketTitle}">${formatQuote(metrics.market)}</td>
        <td class="${pnlClass}">${formatMoney(metrics.pnl)}</td>
        <td>${formatNumber(metrics.delta, 2)}</td>
        <td>${formatNullableNumber(metrics.gamma, 3)}</td>
        <td>${formatNullableMoney(metrics.theta)}</td>
        <td>${formatNullableMoney(metrics.vega)}</td>
        <td>${formatNullablePercent(metrics.iv)}</td>
        <td>
          <button type="button" class="ghost-action" data-action="copy" data-id="${position.id}" title="複製為模擬">複製</button>
          <button type="button" class="danger-action" data-action="delete" data-id="${position.id}" title="刪除">刪除</button>
        </td>
      </tr>
    `;
  }).join("");

  if (!positions.length) {
    els.positionsBody.innerHTML = `<tr><td colspan="13">目前沒有符合篩選的部位。</td></tr>`;
  }

  const totals = aggregateRisk(state.positions);
  els.deltaReadout.textContent = formatNumber(totals.delta, 2);
  els.pnlReadout.textContent = formatMoney(totals.pnl);
  els.pnlReadout.className = totals.pnl >= 0 ? "positive" : "negative";
  els.expiryReadout.textContent = nearestExpiryLabel();
}

function drawPayoff() {
  const canvas = els.payoffCanvas;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const width = rect.width;
  const height = rect.height;
  const pad = { left: 64, right: 24, top: 24, bottom: 44 };
  const bounds = payoffPriceBounds();
  const { xMin, xMax } = bounds;
  const strikeLevels = positionStrikeLevels();
  const points = [];
  for (let price = xMin; price <= xMax; price += 50) {
    points.push({
      price,
      expiry: portfolioPayoff(price),
      theory: portfolioTheoryPnl(price),
    });
  }
  const allY = points.flatMap((point) => [point.expiry, point.theory, 0]);
  const yMin = Math.min(...allY);
  const yMax = Math.max(...allY);
  const yPad = Math.max(1000, (yMax - yMin) * 0.12);
  const scaleX = (price) => pad.left + ((price - xMin) / (xMax - xMin)) * (width - pad.left - pad.right);
  const scaleY = (value) => pad.top + ((yMax + yPad - value) / (yMax - yMin + yPad * 2)) * (height - pad.top - pad.bottom);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  drawPnlZones(ctx, points, scaleX, pad.top, height - pad.bottom);
  drawGrid(ctx, width, height, pad, xMin, xMax, yMin - yPad, yMax + yPad, scaleX, scaleY);
  drawZeroPnlLine(ctx, scaleY(0), pad.left, width - pad.right);
  drawStrikeMarkers(ctx, strikeLevels, scaleX, pad.top, height - pad.bottom);
  drawLine(ctx, points, scaleX, scaleY, "expiry", "#2563eb", 2.6);
  drawLine(ctx, points, scaleX, scaleY, "theory", "#0f8f66", 2);
  drawVerticalMarker(ctx, scaleX(state.spot), pad.top, height - pad.bottom, "#d64545", `現貨 ${formatNumber(state.spot, 0)}`);
  drawLegend(ctx, width, pad);
  renderPayoffStats(points);
}

function drawGrid(ctx, width, height, pad, xMin, xMax, yMin, yMax, scaleX, scaleY) {
  ctx.strokeStyle = "#e6ebf0";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#627181";
  ctx.font = "12px system-ui";
  const zeroY = scaleY(0);
  ctx.beginPath();
  ctx.moveTo(pad.left, zeroY);
  ctx.lineTo(width - pad.right, zeroY);
  ctx.stroke();
  for (let i = 0; i <= 5; i += 1) {
    const price = xMin + ((xMax - xMin) * i) / 5;
    const x = scaleX(price);
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, height - pad.bottom);
    ctx.stroke();
    ctx.fillText(formatNumber(price, 0), x - 24, height - 18);
  }
  for (let i = 0; i <= 4; i += 1) {
    const value = yMin + ((yMax - yMin) * i) / 4;
    const y = scaleY(value);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(formatCompact(value), 8, y + 4);
  }
}

function drawPnlZones(ctx, points, scaleX, yTop, yBottom) {
  ctx.save();
  for (let index = 1; index < points.length; index += 1) {
    const prev = points[index - 1];
    const next = points[index];
    const x1 = scaleX(prev.price);
    const x2 = scaleX(next.price);
    const midpointPnl = (prev.expiry + next.expiry) / 2;
    ctx.fillStyle = midpointPnl >= 0 ? "rgba(15, 143, 102, 0.06)" : "rgba(214, 69, 69, 0.06)";
    ctx.fillRect(x1, yTop, Math.max(1, x2 - x1), yBottom - yTop);
  }
  ctx.restore();
}

function drawZeroPnlLine(ctx, y, xLeft, xRight) {
  ctx.save();
  ctx.strokeStyle = "#111827";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(xLeft, y);
  ctx.lineTo(xRight, y);
  ctx.stroke();
  ctx.fillStyle = "#111827";
  ctx.font = "12px system-ui";
  ctx.fillText("損益 0", xLeft + 8, y - 6);
  ctx.restore();
}

function drawStrikeMarkers(ctx, strikes, scaleX, yTop, yBottom) {
  if (!strikes.length) return;
  ctx.save();
  ctx.strokeStyle = "#94a3b8";
  ctx.fillStyle = "#627181";
  ctx.font = "11px system-ui";
  strikes.forEach((strike, index) => {
    const x = scaleX(strike);
    ctx.setLineDash([3, 5]);
    ctx.beginPath();
    ctx.moveTo(x, yTop);
    ctx.lineTo(x, yBottom);
    ctx.stroke();
    ctx.setLineDash([]);
    const label = `K ${formatNumber(strike, 0)}`;
    const labelY = yTop + 14 + (index % 2) * 14;
    ctx.fillText(label, index === strikes.length - 1 ? x - 44 : x + 4, labelY);
  });
  ctx.restore();
}

function drawLine(ctx, points, scaleX, scaleY, key, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = scaleX(point.price);
    const y = scaleY(point[key]);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawVerticalMarker(ctx, x, yTop, yBottom, color, label) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.setLineDash([5, 5]);
  ctx.beginPath();
  ctx.moveTo(x, yTop);
  ctx.lineTo(x, yBottom);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = color;
  ctx.font = "12px system-ui";
  ctx.fillText(label, x + 8, yTop + 14);
  ctx.restore();
}

function drawLegend(ctx, width, pad) {
  const items = [
    ["到期損益", "#2563eb"],
    ["理論損益", "#0f8f66"],
  ];
  let x = width - pad.right - 180;
  items.forEach(([label, color]) => {
    ctx.fillStyle = color;
    ctx.fillRect(x, pad.top, 18, 4);
    ctx.fillStyle = "#16202a";
    ctx.font = "12px system-ui";
    ctx.fillText(label, x + 24, pad.top + 6);
    x += 88;
  });
}

function renderPayoffStats(points) {
  const extremes = expiryPayoffExtremes();
  const breakevens = findBreakevens(points);
  const atSpot = portfolioPayoff(state.spot);
  const priceLevels = positionPriceLevels();
  const strikeRange = priceLevels.length
    ? `${formatNumber(priceLevels[0], 0)} - ${formatNumber(priceLevels.at(-1), 0)} / ${priceLevels.length} 檔`
    : "無部位";
  const maxProfitText = extremes.maxProfit === Infinity ? "無上限" : formatMoney(extremes.maxProfit);
  const maxLossText = extremes.maxLoss === -Infinity ? "無下限" : formatMoney(extremes.maxLoss);
  els.payoffStats.innerHTML = [
    ["現價到期損益", formatMoney(atSpot), atSpot >= 0 ? "positive" : "negative"],
    ["最大利潤", maxProfitText, "positive"],
    ["最大損失", maxLossText, "negative"],
    ["兩平點", breakevens.length ? breakevens.map((item) => formatNumber(item, 0)).join(" / ") : "無", ""],
    ["履約/建倉價範圍", strikeRange, ""],
    ["到期損益區間", `${maxLossText} ~ ${maxProfitText}`, ""],
  ].map(([label, value, className]) => `
    <div class="stat-card">
      <span>${label}</span>
      <strong class="${className}">${value}</strong>
    </div>
  `).join("");
}

function renderIndexChart() {
  if (!window.LightweightCharts) {
    renderFallbackChart();
    return;
  }

  if (!state.indexChart) {
    state.indexChart = LightweightCharts.createChart(els.priceChart, {
      layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#edf2f7" }, horzLines: { color: "#edf2f7" } },
      rightPriceScale: { borderColor: "#d7dee6", minimumWidth: CHART_PRICE_SCALE_WIDTH },
      timeScale: { borderColor: "#d7dee6", visible: false },
      localization: { locale: "zh-TW" },
    });
    state.candleSeries = state.indexChart.addSeries
      ? state.indexChart.addSeries(LightweightCharts.CandlestickSeries, {
          upColor: "#0f8f66",
          downColor: "#d64545",
          borderVisible: false,
          wickUpColor: "#0f8f66",
          wickDownColor: "#d64545",
        })
      : state.indexChart.addCandlestickSeries();
  }

  if (!state.scoreChart) {
    state.scoreChart = LightweightCharts.createChart(els.scoreChart, {
      layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#edf2f7" }, horzLines: { color: "#edf2f7" } },
      rightPriceScale: { borderColor: "#d7dee6", minimumWidth: CHART_PRICE_SCALE_WIDTH },
      timeScale: { borderColor: "#d7dee6" },
      localization: { locale: "zh-TW" },
    });
    state.scoreSeries = state.scoreChart.addSeries && LightweightCharts.HistogramSeries
      ? state.scoreChart.addSeries(LightweightCharts.HistogramSeries, {
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
          base: 0,
        })
      : state.scoreChart.addHistogramSeries({ priceFormat: { type: "price", precision: 2, minMove: 0.01 }, base: 0 });
    state.scoreSeries.applyOptions({
      autoscaleInfoProvider: () => ({
        priceRange: {
          minValue: -10,
          maxValue: 10,
        },
      }),
    });
  }

  if (!state.scoreDeltaChart) {
    state.scoreDeltaChart = LightweightCharts.createChart(els.scoreDeltaChart, {
      layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#edf2f7" }, horzLines: { color: "#edf2f7" } },
      rightPriceScale: { borderColor: "#d7dee6", minimumWidth: CHART_PRICE_SCALE_WIDTH },
      timeScale: { borderColor: "#d7dee6" },
      localization: { locale: "zh-TW" },
    });
    state.scoreDeltaSeries = state.scoreDeltaChart.addSeries && LightweightCharts.HistogramSeries
      ? state.scoreDeltaChart.addSeries(LightweightCharts.HistogramSeries, {
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
          base: 0,
        })
      : state.scoreDeltaChart.addHistogramSeries({ priceFormat: { type: "price", precision: 2, minMove: 0.01 }, base: 0 });
    state.scoreDeltaSeries.applyOptions({
      autoscaleInfoProvider: () => ({
        priceRange: {
          minValue: -20,
          maxValue: 20,
        },
      }),
    });
  }

  const opScores = calculateOpScores(state.indexCandles);
  const opScoreDeltas = calculateOpScoreDeltas(opScores);
  state.candleByTime = new Map(state.indexCandles.map((candle) => [candle.time, candle]));
  state.scoreByTime = new Map(opScores.map((score) => [score.time, score]));
  state.scoreDeltaByTime = new Map(opScoreDeltas.map((score) => [score.time, score]));
  state.candleSeries.setData(candlesWithSettlementWhitespace(state.indexCandles));
  state.scoreSeries.setData(scoresWithSettlementWhitespace(opScores, state.indexCandles));
  state.scoreDeltaSeries.setData(scoresWithSettlementWhitespace(opScoreDeltas, state.indexCandles));
  resizeIndexChart();
  syncIndexChartRanges();
  syncIndexCrosshairs();
  applySettlementTimeScalePadding();
  fitIndexChartToDataIfNeeded();
  requestAnimationFrame(() => requestAnimationFrame(renderEventLane));
}

function resizeIndexChart() {
  if (!state.indexChart || !state.scoreChart || !state.scoreDeltaChart) return;
  const priceSize = chartElementSize(els.priceChart);
  const scoreSize = chartElementSize(els.scoreChart);
  const deltaSize = chartElementSize(els.scoreDeltaChart);
  if (!priceSize.width || !scoreSize.width || !deltaSize.width) return;
  state.indexChart.applyOptions(priceSize);
  state.scoreChart.applyOptions(scoreSize);
  state.scoreDeltaChart.applyOptions(deltaSize);
  renderEventLane();
}

function scheduleIndexChartResize() {
  if (state.chartResizeFrame) cancelAnimationFrame(state.chartResizeFrame);
  state.chartResizeFrame = requestAnimationFrame(() => {
    state.chartResizeFrame = null;
    resizeIndexChart();
  });
}

function observeIndexChartSize() {
  if (!window.ResizeObserver || state.chartResizeObserver) return;
  state.chartResizeObserver = new ResizeObserver(() => scheduleIndexChartResize());
  [els.priceChart, els.scoreChart, els.scoreDeltaChart].forEach((element) => {
    if (element) state.chartResizeObserver.observe(element);
  });
}

function chartElementSize(element) {
  const rect = element.getBoundingClientRect();
  return {
    width: Math.max(1, Math.floor(element.clientWidth || rect.width)),
    height: Math.max(1, Math.floor(element.clientHeight || rect.height)),
  };
}

function syncIndexChartRanges() {
  if (state.chartsSynced || !state.indexChart || !state.scoreChart || !state.scoreDeltaChart) return;
  state.chartsSynced = true;
  const syncRangeToOtherCharts = (sourceChart, range) => {
    if (state.isSyncingChartRange || !range) return;
    state.isSyncingChartRange = true;
    [state.indexChart, state.scoreChart, state.scoreDeltaChart]
      .filter((chart) => chart !== sourceChart)
      .forEach((chart) => chart.timeScale().setVisibleLogicalRange(range));
    state.isSyncingChartRange = false;
    renderEventLane();
  };
  state.indexChart.timeScale().subscribeVisibleLogicalRangeChange((range) => syncRangeToOtherCharts(state.indexChart, range));
  state.scoreChart.timeScale().subscribeVisibleLogicalRangeChange((range) => syncRangeToOtherCharts(state.scoreChart, range));
  state.scoreDeltaChart.timeScale().subscribeVisibleLogicalRangeChange((range) => syncRangeToOtherCharts(state.scoreDeltaChart, range));
}

function syncIndexCrosshairs() {
  if (state.crosshairsSynced || !state.indexChart || !state.scoreChart || !state.scoreDeltaChart) return;
  state.crosshairsSynced = true;

  const clearOtherCrosshairs = (sourceChart) => {
    [state.indexChart, state.scoreChart, state.scoreDeltaChart]
      .filter((chart) => chart !== sourceChart)
      .forEach((chart) => chart.clearCrosshairPosition());
  };

  const syncCrosshairAtTime = (sourceChart, time) => {
    const candle = state.candleByTime.get(time);
    const score = state.scoreByTime.get(time);
    const delta = state.scoreDeltaByTime.get(time);
    if (sourceChart !== state.indexChart) {
      candle ? state.indexChart.setCrosshairPosition(candle.close, time, state.candleSeries) : state.indexChart.clearCrosshairPosition();
    }
    if (sourceChart !== state.scoreChart) {
      score ? state.scoreChart.setCrosshairPosition(score.value, time, state.scoreSeries) : state.scoreChart.clearCrosshairPosition();
    }
    if (sourceChart !== state.scoreDeltaChart) {
      delta ? state.scoreDeltaChart.setCrosshairPosition(delta.value, time, state.scoreDeltaSeries) : state.scoreDeltaChart.clearCrosshairPosition();
    }
  };

  state.indexChart.subscribeCrosshairMove((param) => {
    if (state.isSyncingCrosshair) return;
    state.isSyncingCrosshair = true;
    if (!param.point || !param.time) {
      clearOtherCrosshairs(state.indexChart);
    } else {
      syncCrosshairAtTime(state.indexChart, param.time);
    }
    state.isSyncingCrosshair = false;
  });

  state.scoreChart.subscribeCrosshairMove((param) => {
    if (state.isSyncingCrosshair) return;
    state.isSyncingCrosshair = true;
    if (!param.point || !param.time) {
      clearOtherCrosshairs(state.scoreChart);
    } else {
      syncCrosshairAtTime(state.scoreChart, param.time);
    }
    state.isSyncingCrosshair = false;
  });

  state.scoreDeltaChart.subscribeCrosshairMove((param) => {
    if (state.isSyncingCrosshair) return;
    state.isSyncingCrosshair = true;
    if (!param.point || !param.time) {
      clearOtherCrosshairs(state.scoreDeltaChart);
    } else {
      syncCrosshairAtTime(state.scoreDeltaChart, param.time);
    }
    state.isSyncingCrosshair = false;
  });
}

function applySettlementTimeScalePadding() {
  const options = {
    rightOffset: 0,
    barSpacing: 8,
  };
  state.indexChart.timeScale().applyOptions(options);
  state.scoreChart.timeScale().applyOptions(options);
  state.scoreDeltaChart.timeScale().applyOptions(options);
}

function fitIndexChartToDataIfNeeded() {
  const first = state.indexCandles[0]?.time || "";
  const last = state.indexCandles.at(-1)?.time || "";
  const key = `${first}:${last}:${state.indexCandles.length}`;
  if (!first || state.indexVisibleRangeKey === key) return;
  state.indexVisibleRangeKey = key;
  requestAnimationFrame(() => {
    [state.indexChart, state.scoreChart, state.scoreDeltaChart].forEach((chart) => chart?.timeScale().fitContent());
    requestAnimationFrame(renderEventLane);
  });
}

function candlesWithSettlementWhitespace(candles) {
  const markerDates = settlementMarkerDates();
  const existing = new Set(candles.map((candle) => candle.time));
  const whitespace = markerDates
    .filter((date) => !existing.has(date))
    .map((time) => ({ time }));
  return [...candles, ...whitespace].sort((a, b) => String(a.time).localeCompare(String(b.time)));
}

function scoresWithSettlementWhitespace(scores, candles) {
  const chartTimes = candlesWithSettlementWhitespace(candles).map((item) => item.time);
  const scoreMap = new Map(scores.map((score) => [score.time, score]));
  return chartTimes.map((time) => scoreMap.get(time) || { time });
}

function settlementMarkerDates() {
  const startDate = els.startDateInput?.value || "";
  const endDate = els.endDateInput?.value || "";
  return settlementDates()
    .filter((date) => date >= startDate)
    .filter((date) => !endDate || date <= endDate)
    .filter((date, index, dates) => dates.indexOf(date) === index);
}

function chartEvents() {
  return settlementMarkerDates().map((date) => ({
    date,
    type: "settlement",
    label: "S",
    title: `月選擇權結算日 ${date}`,
  }));
}

function renderEventLane() {
  if (!els.eventLane || !state.indexChart) return;
  const width = els.eventLane.clientWidth;
  const events = chartEvents();
  els.eventLane.innerHTML = "";
  events.forEach((event) => {
    const x = state.indexChart.timeScale().timeToCoordinate(event.date);
    if (!Number.isFinite(x) || x < -20 || x > width + 20) return;
    const marker = document.createElement("button");
    marker.type = "button";
    marker.className = "event-marker";
    marker.dataset.eventType = event.type;
    marker.style.left = `${x}px`;
    marker.title = event.title;
    marker.textContent = event.label;
    els.eventLane.appendChild(marker);
  });
}

function renderFallbackChart() {
  els.priceChart.innerHTML = `<canvas id="fallbackIndexCanvas" width="800" height="360"></canvas>`;
  const canvas = document.querySelector("#fallbackIndexCanvas");
  const ctx = canvas.getContext("2d");
  const rect = els.priceChart.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;
  const prices = state.indexCandles.flatMap((item) => [item.high, item.low]);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const xStep = rect.width / Math.max(1, state.indexCandles.length - 1);
  const y = (price) => 20 + ((max - price) / (max - min)) * (rect.height - 44);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 2;
  ctx.beginPath();
  state.indexCandles.forEach((item, index) => {
    const x = index * xStep;
    if (index === 0) ctx.moveTo(x, y(item.close));
    else ctx.lineTo(x, y(item.close));
  });
  ctx.stroke();
}

function calculateOpScores(candles) {
  const params = {
    lengthL: 20,
    lengthS: 7,
    pctThreshold: 1.1,
    barSizeThreshold: 0.7,
    gapThreshold: 1.1,
  };

  return candles.map((candle, index) => {
    const ksvL = ksvValue(candles, index, params.lengthL);
    const ksvS = ksvValue(candles, index, params.lengthS);
    const ksvAvg = average([ksvL, ksvS].filter(Number.isFinite));
    const ksvScore = Number.isFinite(ksvAvg) ? round2(10 * ksvAvg - 5) : 0;

    const dayRange = candle.high - candle.low;
    const volS = rollingAverage(candles, index, 5, (item) => item.high - item.low);
    const volL = rollingAverage(candles, index, 10, (item) => item.high - item.low);
    const prevVolL = rollingAverage(candles, index - 1, 10, (item) => item.high - item.low);
    let volScore = 0;
    if (Number.isFinite(volS) && Number.isFinite(volL) && volS >= volL) volScore += 1;
    if (Number.isFinite(volL) && Number.isFinite(prevVolL) && volL >= prevVolL) volScore += 1;

    const prevClose = candles[index - 1]?.close;
    const size = candle.high > candle.low ? dayRange : 0;
    const pct = prevClose ? (size / prevClose) * 100 : 0;
    const bar = size > 0 ? Math.abs(candle.close - candle.open) / size : 0;
    let sizeScore = 0;
    if (pct >= params.pctThreshold) sizeScore += 1;
    if (bar >= params.barSizeThreshold) sizeScore += 1;
    sizeScore *= Math.sign(candle.close - candle.open);

    const gapPct = prevClose ? ((candle.open - prevClose) / prevClose) * 100 : 0;
    const gapScore = Math.abs(gapPct) >= params.gapThreshold ? Math.sign(gapPct) : 0;
    const totalScore = round2(ksvScore + Math.sign(ksvScore) * volScore + sizeScore + gapScore);

    return {
      time: candle.time,
      value: totalScore,
      color: scoreColor(totalScore),
    };
  });
}

function calculateOpScoreDeltas(opScores) {
  return opScores.map((score, index) => {
    const previous = opScores[index - 1]?.value;
    const value = Number.isFinite(previous) ? round2(score.value - previous) : 0;
    return {
      time: score.time,
      value,
      color: deltaColor(value),
    };
  });
}

function ksvValue(candles, index, length) {
  if (index < length - 1) return NaN;
  const window = candles.slice(index - length + 1, index + 1);
  const lowestLow = Math.min(...window.map((item) => item.low));
  const highestHigh = Math.max(...window.map((item) => item.high));
  const range = highestHigh - lowestLow;
  if (range <= 0) return NaN;
  return (candles[index].close - lowestLow) / range;
}

function rollingAverage(items, index, length, mapper) {
  if (index < length - 1) return NaN;
  const values = items.slice(index - length + 1, index + 1).map(mapper).filter(Number.isFinite);
  if (values.length !== length) return NaN;
  return average(values);
}

function scoreColor(value) {
  if (value > 0) return "#0f8f66";
  if (value < 0) return "#d64545";
  return "#94a3b8";
}

function deltaColor(value) {
  if (value > 0) return "#0f8f66";
  if (value < 0) return "#d64545";
  return "#94a3b8";
}

function renderOptionChain() {
  if (!els.optionChainBody) return;
  const rows = state.automationPositions;
  els.optionChainBody.innerHTML = rows.map((row) => {
    const dailyClass = row.dailyPnl >= 0 ? "positive" : "negative";
    const totalClass = row.totalPnl >= 0 ? "positive" : "negative";
    return `
      <tr>
        <td>${row.date}</td>
        <td>${row.contract}</td>
        <td>${row.side}</td>
        <td>${row.direction}</td>
        <td>${row.qty}</td>
        <td>${formatNumber(row.entryPrice, 1)}</td>
        <td>${formatNumber(row.marketPrice, 1)}</td>
        <td class="${dailyClass}">${formatMoney(row.dailyPnl)}</td>
        <td class="${totalClass}">${formatMoney(row.totalPnl)}</td>
      </tr>
    `;
  }).join("");

  if (!rows.length) {
    els.optionChainBody.innerHTML = `<tr><td colspan="9">本合約周期尚未建立自動化交易部位。</td></tr>`;
  }
}

function populateAutomationHistoryContracts() {
  const select = els.automationHistoryContractSelect;
  if (!select) return;
  const previous = select.value;
  const options = historicalAutomationContracts();
  select.innerHTML = options.map((contract) => `<option value="${contract}">${contract}</option>`).join("");
  if (previous && options.includes(previous)) {
    select.value = previous;
  }
}

function historicalAutomationContracts() {
  const todayText = toDateInput(new Date());
  return settlementDates()
    .filter((date) => date < todayText)
    .map((date) => date.slice(0, 7).replace("-", ""))
    .filter((contract, index, contracts) => contracts.indexOf(contract) === index)
    .sort((a, b) => b.localeCompare(a));
}

function renderAutomationHistoryTable() {
  if (!els.automationHistoryBody) return;
  const contract = els.automationHistoryContractSelect?.value || historicalAutomationContracts()[0] || "";
  els.automationHistoryBody.innerHTML = `<tr><td colspan="9">歷史合約 ${contract || "--"} 的自動化部位回朔載入邏輯後續補上。</td></tr>`;
  if (els.automationHistoryStatus) {
    els.automationHistoryStatus.textContent = contract
      ? `${contract} 歷史回朔資料載入邏輯後續補上。`
      : "尚無可選擇的歷史合約月份。";
  }
}

function connectFugleTQuote() {
  fetchFugleTQuoteSnapshot();
  if (!window.EventSource) {
    els.tQuoteStatus.textContent = "瀏覽器不支援 EventSource，改用手動刷新。";
    return;
  }
  if (state.fugleTQuoteEventSource) {
    state.fugleTQuoteEventSource.close();
  }
  const source = new EventSource("/api/live/tquote-events");
  state.fugleTQuoteEventSource = source;
  source.onmessage = (event) => {
    try {
      applyFugleTQuotePayload(JSON.parse(event.data));
    } catch (error) {
      els.tQuoteStatus.textContent = `Fugle live payload 解析失敗：${error.message}`;
    }
  };
  source.onerror = () => {
    els.tQuoteStatus.textContent = "Fugle live 連線中斷，等待重新連線...";
  };
}

async function fetchFugleTQuoteSnapshot() {
  try {
    const response = await fetch("/api/live/tquote", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    applyFugleTQuotePayload(await response.json());
  } catch (error) {
    els.tQuoteStatus.textContent = `Fugle T 型報價尚未可用：${error.message}`;
  }
}

function applyFugleTQuotePayload(payload) {
  state.fugleTQuote = normalizeTQuotePayload(payload);
  renderTQuoteTable();
  drawTQuoteVixChart();
}

function normalizeTQuotePayload(payload) {
  if (!payload) return null;
  if (payload.schema === "quote_snapshot") return payload;
  if (payload.quote_snapshot?.schema === "quote_snapshot") return payload.quote_snapshot;
  return {
    schema: "legacy_tquote",
    contract_month: payload.contract,
    settlement_date: payload.settlement_date,
    session: payload.after_hours ? "night" : "day",
    snapshot_at: payload.last_aggregate_at || payload.last_book_at || payload.last_event_at || "",
    status: payload.status,
    stale: Boolean(payload.stale),
    error: payload.error || "",
    source: {
      type: payload.source?.type || "fugle_legacy",
      event_counts: payload.event_counts || {},
      last_book_at: payload.last_book_at,
      last_aggregate_at: payload.last_aggregate_at,
    },
    underlying: {
      symbol: payload.future_symbol,
      price: payload.future_price,
    },
    rows: payload.rows || [],
    vix: payload.vix ? {
      value_percent: firstNumber(payload.vix.value_percent, payload.vix.value),
      sample_count: payload.vix.sample_count,
      call_count: payload.vix.call_count,
      put_count: payload.vix.put_count,
      method: payload.vix.method,
    } : null,
    vix_series: (payload.vix_series || []).map((point) => ({
      ...point,
      value_percent: firstNumber(point.value_percent, point.value),
    })),
  };
}

function tQuoteSourceText(payload) {
  const sourceType = payload.source?.type || "";
  const status = payload.status || "loading";
  if (payload.stale || sourceType === "fugle_cache") {
    const age = formatAgeSeconds(payload.source?.cache_age_seconds);
    return `最後有效截面${age ? ` ${age}` : ""}`;
  }
  if (sourceType === "fugle_rest_probe") return `Fugle REST ${status}`;
  if (sourceType === "fugle_live") return `Fugle live ${status}`;
  return status;
}

function formatAgeSeconds(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "";
  if (value < 90) return `${Math.round(value)}秒前`;
  const minutes = value / 60;
  if (minutes < 90) return `${Math.round(minutes)}分鐘前`;
  return `${Math.round(minutes / 60)}小時前`;
}

function tQuoteVixPercent(vix) {
  return firstNumber(vix?.value_percent, vix?.value);
}

function renderTQuoteTable() {
  const payload = state.fugleTQuote;
  if (!payload) {
    els.tQuoteStatus.textContent = "正在連線 Fugle live T 型報價...";
    els.tQuoteBody.innerHTML = `<tr><td colspan="23">正在連線 Fugle live T 型報價...</td></tr>`;
    renderTQuoteVixSummary();
    return;
  }
  if (payload.error) {
    els.tQuoteStatus.textContent = `Fugle live：${payload.status || "error"} / ${payload.error}`;
  }
  const rows = payload.rows || [];
  const contract = payload.contract_month || payload.contract || "TXO";
  const futureSymbol = payload.underlying?.symbol || payload.future_symbol;
  const futurePrice = firstNumber(payload.underlying?.price, payload.future_price);
  const futureText = futureSymbol && futurePrice
    ? `${futureSymbol} ${formatNumber(futurePrice, 0)}`
    : "中心期貨讀取中";
  const sessionText = payload.session === "night" || payload.after_hours ? "夜盤" : "日盤";
  const eventCounts = payload.source?.event_counts || payload.event_counts;
  const eventText = eventCounts
    ? Object.entries(eventCounts).map(([key, value]) => `${key}:${value}`).join(" ")
    : "";
  const sourceText = tQuoteSourceText(payload);
  els.tQuoteStatus.textContent = `${contract} ${sessionText} / ${futureText} / ${sourceText}${eventText ? ` / ${eventText}` : ""}`;
  renderTQuoteVixSummary();
  if (!rows.length) {
    els.tQuoteBody.innerHTML = `<tr><td colspan="23">尚無 Fugle T 型報價資料。</td></tr>`;
    return;
  }
  const centerPrice = futurePrice || tQuoteCenterPrice();
  const atm = rows.reduce((best, row) => Math.abs(row.strike - centerPrice) < Math.abs(best.strike - centerPrice) ? row : best, rows[0]);
  els.tQuoteBody.innerHTML = rows.map((row) => {
    const atmClass = atm && row.strike === atm.strike ? "atm-row" : "";
    const call = row.call || null;
    const put = row.put || null;
    return `
      <tr class="${atmClass}">
        ${renderFugleCallCells(call, row.strike, contract)}
        <td class="strike-cell">${formatNumber(row.strike, 0)}</td>
        ${renderFuglePutCells(put, row.strike, contract)}
      </tr>
    `;
  }).join("");
}

function renderFugleCallCells(leg, strike, contract) {
  if (!leg) return `<td colspan="11">-</td>`;
  return `
    <td>${formatNullableNumber(leg.volume, 0)}</td>
    <td class="quote-call">${renderFugleQuoteButton(leg, "call", strike, contract)}</td>
    <td>${formatNullableNumber(leg.bid_size, 0)}</td>
    <td class="quote-call">${formatTQuoteNumber(leg.bid)}</td>
    <td class="quote-call">${formatTQuoteNumber(leg.ask)}</td>
    <td>${formatNullableNumber(leg.ask_size, 0)}</td>
    <td>${formatNullablePercent(leg.mid_iv)}</td>
    <td>${formatNullableNumber(leg.delta, 4)}</td>
    <td>${formatNullableNumber(leg.gamma, 6)}</td>
    <td class="${pnlClass(leg.theta)}">${formatNullableNumber(leg.theta, 1)}</td>
    <td>${formatNullableNumber(leg.vega, 1)}</td>
  `;
}

function renderFuglePutCells(leg, strike, contract) {
  if (!leg) return `<td colspan="11">-</td>`;
  return `
    <td>${formatNullableNumber(leg.vega, 1)}</td>
    <td class="${pnlClass(leg.theta)}">${formatNullableNumber(leg.theta, 1)}</td>
    <td>${formatNullableNumber(leg.gamma, 6)}</td>
    <td>${formatNullableNumber(leg.delta, 4)}</td>
    <td>${formatNullablePercent(leg.mid_iv)}</td>
    <td>${formatNullableNumber(leg.bid_size, 0)}</td>
    <td class="quote-put">${formatTQuoteNumber(leg.bid)}</td>
    <td class="quote-put">${formatTQuoteNumber(leg.ask)}</td>
    <td>${formatNullableNumber(leg.ask_size, 0)}</td>
    <td class="quote-put">${renderFugleQuoteButton(leg, "put", strike, contract)}</td>
    <td>${formatNullableNumber(leg.volume, 0)}</td>
  `;
}

function renderFugleQuoteButton(leg, type, strike, contract) {
  const price = fugleLegMarkPrice(leg);
  const text = formatTQuoteNumber(firstNumber(leg?.last, leg?.mid, price));
  if (!price || price <= 0) return text;
  return `<button type="button" class="quote-price" data-action="add-chain" data-type="${type}" data-strike="${strike}" data-price="${price}" data-contract="${contract}" title="加入 ${type === "call" ? "Call" : "Put"} 模擬部位">${text}</button>`;
}

function fugleLegMarkPrice(leg) {
  if (leg?.mid > 0) return leg.mid;
  if (leg?.bid > 0 && leg?.ask > 0) return (leg.bid + leg.ask) / 2;
  if (leg?.last > 0) return leg.last;
  return leg?.bid || leg?.ask || 0;
}

function renderTQuoteVixSummary() {
  const payload = state.fugleTQuote;
  const vix = payload?.vix;
  const valuePercent = tQuoteVixPercent(vix);
  if (els.tVixValue) {
    els.tVixValue.textContent = valuePercent ? `${formatNumber(valuePercent, 2)}%` : "--";
  }
  if (els.tVixMeta) {
    els.tVixMeta.textContent = vix
      ? `4C+4P ATM 加權 / 樣本 ${vix.call_count}C + ${vix.put_count}P / ${payload.snapshot_at || ""}${payload.stale ? " / 最後有效截面" : ""}`
      : "等待 Fugle live IV 樣本...";
  }
}

function drawTQuoteVixChart() {
  const canvas = els.tVixChart;
  if (!canvas) return;
  const series = state.fugleTQuote?.vix_series || [];
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  const values = series.map((point) => Number(firstNumber(point.value_percent, point.value))).filter(Number.isFinite);
  if (values.length < 2) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "13px Segoe UI, sans-serif";
    ctx.fillText("等待 VIX 速算序列...", 16, height / 2);
    return;
  }
  const pad = { left: 46, right: 18, top: 12, bottom: 24 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(0.5, max - min);
  const yMin = min - span * 0.18;
  const yMax = max + span * 0.18;
  const scaleX = (index) => pad.left + (index / Math.max(1, values.length - 1)) * (width - pad.left - pad.right);
  const scaleY = (value) => pad.top + ((yMax - value) / (yMax - yMin)) * (height - pad.top - pad.bottom);
  ctx.strokeStyle = "#e2e8f0";
  ctx.lineWidth = 1;
  for (let index = 0; index < 4; index += 1) {
    const y = pad.top + (index / 3) * (height - pad.top - pad.bottom);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
  }
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = scaleX(index);
    const y = scaleY(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  const last = values.at(-1);
  ctx.fillStyle = "#2563eb";
  ctx.beginPath();
  ctx.arc(scaleX(values.length - 1), scaleY(last), 3.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#475569";
  ctx.font = "12px Segoe UI, sans-serif";
  ctx.fillText(`${yMax.toFixed(1)}%`, 6, pad.top + 4);
  ctx.fillText(`${yMin.toFixed(1)}%`, 6, height - pad.bottom + 4);
  ctx.textAlign = "right";
  ctx.fillText(`${last.toFixed(2)}%`, width - pad.right, Math.max(pad.top + 12, scaleY(last) - 8));
  ctx.textAlign = "left";
}

function scheduleTQuoteVixChartResize() {
  if (state.tVixResizeFrame) cancelAnimationFrame(state.tVixResizeFrame);
  state.tVixResizeFrame = requestAnimationFrame(() => {
    state.tVixResizeFrame = null;
    drawTQuoteVixChart();
  });
}

function formatTQuoteNumber(value) {
  return Number.isFinite(Number(value)) && Number(value) > 0 ? formatNumber(Number(value), 1) : "-";
}

function pnlClass(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "";
  if (parsed > 0) return "positive";
  if (parsed < 0) return "negative";
  return "";
}

function renderStrategyTable() {
  const allRows = buildStrategyRows();
  const rows = filteredStrategyRows(allRows);
  els.strategyStatus.textContent = `符合條件 ${rows.length} 筆 / 全部 ${allRows.length} 筆策略`;
  els.strategyBody.innerHTML = rows.map((row) => `
    <tr class="${row.expiry === "P1" && row.id !== 1 ? "divider" : ""}">
      <td>${row.id}</td>
      <td class="${row.marketClass}">${row.market}</td>
      <td class="vol-cell">${row.volTrend}</td>
      <td class="expiry-cell">${row.expiry}</td>
      <td>${row.direction}</td>
      <td>${row.volLeg}</td>
      <td>${row.timeLeg}</td>
      <td>${row.trade}</td>
    </tr>
  `).join("");

  if (!rows.length) {
    els.strategyBody.innerHTML = `<tr><td colspan="8">沒有符合目前 3V 篩選條件的策略。</td></tr>`;
  }
}

function populateStrategyFilters() {
  const rows = buildStrategyRows();
  setSelectOptions(els.strategyViewFilter, unique(rows.map((row) => row.market)), "全部方向");
  setSelectOptions(els.strategyVolFilter, unique(rows.map((row) => row.volTrend)), "全部波動率");
  setSelectOptions(els.strategyTimeFilter, unique(rows.map((row) => row.expiry)), "全部時間價值");
}

function handleStrategyFilterChange() {
  state.strategyFilters = {
    view: els.strategyViewFilter.value,
    volatility: els.strategyVolFilter.value,
    timeValue: els.strategyTimeFilter.value,
  };
  renderStrategyTable();
}

function filteredStrategyRows(rows) {
  return rows.filter((row) => {
    const viewOk = state.strategyFilters.view === "all" || row.market === state.strategyFilters.view;
    const volOk = state.strategyFilters.volatility === "all" || row.volTrend === state.strategyFilters.volatility;
    const timeOk = state.strategyFilters.timeValue === "all" || row.expiry === state.strategyFilters.timeValue;
    return viewOk && volOk && timeOk;
  });
}

function renderCalendar() {
  const today = stripTime(new Date());
  renderSettlementCalendar(today);
  const items = settlementReminderDates(today);
  els.calendarList.innerHTML = items.slice(0, 5).map((date) => {
    const dte = businessDaysBetween(today, date);
    const urgent = dte <= 5 ? "到期警戒" : "正常追蹤";
    return `
      <div class="calendar-item">
        <time>${toDateInput(date)}</time>
        <span>月選擇權推估結算日，剩餘 ${dte} 個交易日</span>
        <strong>${urgent}</strong>
      </div>
    `;
  }).join("");
}

function renderSettlementCalendar(today) {
  const todayText = toDateInput(today);
  const dates = settlementDates();
  const previousSettlement = [...dates].reverse().find((date) => date <= todayText)
    || dates[0];
  const nextSettlement = dates.find((date) => date > todayText)
    || dates[dates.length - 1];
  const startDate = parseDate(previousSettlement);
  const endDate = parseDate(nextSettlement);
  const months = monthsBetween(startDate, endDate);
  const contractMonth = settlementContractMonth(nextSettlement);

  els.settlementCalendar.innerHTML = `
    <div class="settlement-calendar-header">
      <div>
        <strong>${contractMonth}</strong>
        <span>${previousSettlement} ~ ${nextSettlement}</span>
      </div>
      <span class="settlement-countdown">剩餘 ${businessDaysBetween(today, endDate)} 個交易日</span>
    </div>
    <div class="settlement-months">
      ${months.map((month) => renderSettlementMonth(month, startDate, endDate, today)).join("")}
    </div>
  `;
}

function renderSettlementMonth(monthDate, startDate, endDate, today) {
  const monthStart = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1);
  const monthEnd = new Date(monthDate.getFullYear(), monthDate.getMonth() + 1, 0);
  const leadingBlanks = Array.from({ length: monthStart.getDay() }, () => null);
  const monthDays = Array.from({ length: monthEnd.getDate() }, (_, index) => new Date(monthDate.getFullYear(), monthDate.getMonth(), index + 1));
  const days = [...leadingBlanks, ...monthDays];
  const weekdayLabels = ["日", "一", "二", "三", "四", "五", "六"];

  return `
    <div class="settlement-month">
      <div class="settlement-month-title">${monthDate.getFullYear()}年${monthDate.getMonth() + 1}月</div>
      <div class="settlement-weekdays">
        ${weekdayLabels.map((label) => `<span>${label}</span>`).join("")}
      </div>
      <div class="settlement-days">
        ${days.map((date) => date ? renderSettlementDay(date, startDate, endDate, today) : `<div class="settlement-day blank"></div>`).join("")}
      </div>
    </div>
  `;
}

function renderSettlementDay(date, startDate, endDate, today) {
  const isStart = sameDate(date, startDate);
  const isEnd = sameDate(date, endDate);
  const isToday = sameDate(date, today);
  const isWeekend = date.getDay() === 0 || date.getDay() === 6;
  const inCycle = date >= startDate && date <= endDate;
  const timeValue = inCycle && !isWeekend ? timeValueClass(date, endDate) : "";
  const classes = [
    "settlement-day",
    inCycle ? "in-cycle" : "",
    isWeekend ? "weekend" : "",
    timeValue,
    isStart ? "cycle-start" : "",
    isEnd ? "cycle-end" : "",
    isToday ? "today" : "",
  ].filter(Boolean).join(" ");

  return `
    <div class="${classes}">
      <strong>${date.getDate()}</strong>
      <span>${isEnd ? "結算" : timeValue.replace("time-", "").toUpperCase()}</span>
    </div>
  `;
}

function timeValueClass(date, settlementDate) {
  const daysToSettlement = calendarDaysBetween(date, settlementDate);
  if (daysToSettlement >= 0 && daysToSettlement <= 2) return "time-p3";
  if (daysToSettlement >= 3 && daysToSettlement <= 12) return "time-p2";
  if (daysToSettlement >= 13) return "time-p1";
  return "";
}

function timeValueStateForCurrentTradingDate() {
  const lastCandleDate = state.indexCandles.at(-1)?.time;
  const date = parseDate(lastCandleDate) || stripTime(new Date());
  const dateText = toDateInput(date);
  const nextSettlementText = settlementDates().find((item) => item >= dateText) || settlementDates().at(-1) || "";
  const nextSettlement = parseDate(nextSettlementText);
  const phase = nextSettlement ? timeValueClass(date, nextSettlement).replace("time-", "").toUpperCase() : "";
  const labels = {
    P1: "P1（買方天堂）",
    P2: "P2（賣方天堂）",
    P3: "P3（收割期）",
  };
  return {
    date: dateText,
    settlement: nextSettlementText,
    days: nextSettlement ? calendarDaysBetween(date, nextSettlement) : null,
    phase: phase || "N/A",
    label: labels[phase] || "等待行事曆資料",
  };
}

function settlementReminderDates(today) {
  const todayText = toDateInput(today);
  return settlementDates()
    .filter((date) => date >= todayText)
    .map(parseDate)
    .filter(Boolean)
    .slice(0, 2);
}

function settlementContractMonth(settlementDate) {
  return `${settlementDate.slice(0, 4)}-${settlementDate.slice(5, 7)}月選`;
}

function renderRiskAndAdvice() {
  const totals = aggregateRisk(state.positions);
  const chainStats = optionChainStats();
  const trend = indexTrend();
  const timeValue = timeValueStateForCurrentTradingDate();

  els.regimeFramework.innerHTML = `
    <section class="regime-category">
      <div class="regime-category-title">
        <span>View</span>
        <strong>方向判斷 placeholder</strong>
      </div>
      <div class="view-stack">
        <div class="view-row">
          <div class="view-factor">
            <span>H</span>
            <strong>過熱K</strong>
            <p>待補判斷邏輯</p>
          </div>
          <div class="view-factor">
            <span>2</span>
            <strong>2Q</strong>
            <p>待補判斷邏輯</p>
          </div>
        </div>
        <div class="view-row">
          <div class="view-factor">
            <span>P</span>
            <strong>樞紐點</strong>
            <p>待補判斷邏輯</p>
          </div>
          <div class="view-factor">
            <span>K</span>
            <strong>關鍵K</strong>
            <p>待補判斷邏輯</p>
          </div>
        </div>
        <div class="view-row">
          <div class="view-factor">
            <span>R</span>
            <strong>修正比例</strong>
            <p>待補判斷邏輯</p>
          </div>
          <div class="view-factor">
            <span>D</span>
            <strong>道式防線</strong>
            <p>待補判斷邏輯</p>
          </div>
        </div>
      </div>
    </section>
    <section class="regime-category">
      <div class="regime-category-title">
        <span>Volatility</span>
        <strong>波動判斷 placeholder</strong>
      </div>
      <div class="volatility-grid">
        <div><span>波動上升</span><strong>待補</strong></div>
        <div><span>波動持平</span><strong>待補</strong></div>
        <div><span>波動下降</span><strong>待補</strong></div>
      </div>
    </section>
    <section class="regime-category">
      <div class="regime-category-title">
        <span>Time Value</span>
        <strong>${timeValue.label}</strong>
      </div>
      <p>依最後交易日 ${timeValue.date} 對應 ${timeValue.settlement || "--"} 結算行事曆，剩餘 ${timeValue.days ?? "--"} 天。</p>
    </section>
  `;

  const bias = trend > 0.015 ? "偏多" : trend < -0.015 ? "偏空" : "震盪";
  const volTone = chainStats.atmIv && chainStats.atmIv > 0.28 ? "波動偏高" : "波動中性";
  const deltaTone = totals.delta > 35 ? "部位偏多" : totals.delta < -35 ? "部位偏空" : "方向曝險中性";
  const gammaTone = totals.gamma < -0.25 ? "負 Gamma 偏大，急漲急跌時調整壓力會上升" : "Gamma 風險目前可控";
  const thetaTone = totals.theta > 0 ? "時間價值對部位有利" : "時間價值正在消耗部位";

  els.regimeAdvice.innerHTML = `
    <strong>${bias} / ${volTone} / ${deltaTone}</strong>
    <ul>
      <li>${gammaTone}。</li>
      <li>${thetaTone}，需搭配剩餘 DTE 檢查。</li>
      <li>目前建議優先觀察 ${supportResistanceText()}，此區間會影響賣方保證金與避險節奏。</li>
      <li>此區塊是風險輔助判斷，不是自動交易指令。</li>
    </ul>
  `;
}

async function fetchIndexCandles(options = {}) {
  if (state.isFetchingIndex) return;
  const startDate = els.startDateInput.value;
  const endDate = els.endDateInput.value;
  if (!startDate || !endDate) return;
  state.isFetchingIndex = true;
  els.fetchIndexBtn.disabled = true;
  try {
    els.marketStatus.textContent = "正在透過本機 FinMind token 讀取加權指數日線...";
    let sourceLabel = "本機 FinMind token";
    let latestRows = [];
    let latestFetchError = "";
    let tradingDates = [];
    let historicalCandles = [];
    try {
      const localPayload = await localIndexCandleData(startDate, endDate);
      tradingDates = normalizeTradingDateRows(localPayload.trading_dates || [], startDate, endDate);
      historicalCandles = normalizeDailyCandles(localPayload.daily_candles || []);
      latestRows = Array.isArray(localPayload.latest_rows) ? localPayload.latest_rows : [];
      latestFetchError = localPayload.latest_error || "";
    } catch (localError) {
      sourceLabel = "FinMind";
      latestFetchError = localError.message || String(localError);
      els.marketStatus.textContent = "本機日線 API 無法使用，改用 FinMind 直接端點讀取交易日曆...";
      tradingDates = await fetchTradingDates(startDate, endDate);
      els.marketStatus.textContent = "正在使用加權指數日資料端點抓取歷史 OHLC...";
      historicalCandles = await fetchTaiexDailyCandles(startDate, endDate);
    }

    if (!tradingDates.length && historicalCandles.length) {
      tradingDates = unique(historicalCandles.map((candle) => candle.time))
        .filter((date) => date >= startDate && date <= endDate)
        .sort();
    }
    if (!tradingDates.length) throw new Error("指定區間沒有交易日。");

    const tradingDateSet = new Set(tradingDates);
    const candlesByDate = new Map(
      historicalCandles
        .filter((candle) => tradingDateSet.has(candle.time))
        .map((candle) => [candle.time, candle]),
    );

    const latestTradingDate = tradingDates[tradingDates.length - 1];
    if (!latestRows.length) {
      els.marketStatus.textContent = `正在用五秒資料 resample 最新交易日 ${latestTradingDate}...`;
      try {
        latestRows = await finmindData("TaiwanVariousIndicators5Seconds", { start_date: latestTradingDate });
        latestFetchError = "";
      } catch (error) {
        latestFetchError = error.message || String(error);
      }
    }
    const latestCandle = aggregateTaiexRows(latestRows, latestTradingDate);
    if (latestCandle && tradingDateSet.has(latestCandle.time)) {
      candlesByDate.set(latestCandle.time, latestCandle);
    }

    const mergedCandles = tradingDates
      .map((date) => candlesByDate.get(date))
      .filter(Boolean);
    const droppedCount = tradingDates.length - mergedCandles.length;
    const candles = mergedCandles.slice(-120);

    if (!candles.length) throw new Error("查無 TAIEX 資料，可能是假日、權限或請求額度限制。");
    state.indexCandles = candles;
    const lastCandle = candles[candles.length - 1];
    const spotSource = latestCandle && lastCandle.time === latestCandle.time
      ? `${sourceLabel} 加權指數五秒重建 ${latestCandle.time}`
      : `${sourceLabel} 加權指數收盤價 ${lastCandle.time}`;
    setSpot(lastCandle.close, spotSource);
    const droppedText = droppedCount > 0 ? `，已略過 ${droppedCount} 個無日資料交易日` : "";
    const latestText = latestCandle
      ? `，最新交易日 ${latestTradingDate} 已用五秒資料重建`
      : `，最新交易日 ${latestTradingDate} 沒有可用五秒資料，保留日資料`;
    const latestWarning = !latestCandle && latestFetchError ? `；五秒資料警告：${latestFetchError}` : "";
    els.marketStatus.textContent = `已更新 ${candles.length} 根加權指數日線（${sourceLabel}）${droppedText}${latestText}${latestWarning}。`;
    renderAll();
  } catch (error) {
    const fallbackText = options.auto ? "，目前保留示範日線" : "";
    els.marketStatus.textContent = `FinMind 抓取失敗：${error.message}${fallbackText}`;
  } finally {
    els.fetchIndexBtn.disabled = false;
    state.isFetchingIndex = false;
  }
}

async function localIndexCandleData(startDate, endDate) {
  const urls = [
    new URL("/api/index-candles", window.location.origin),
    new URL("http://127.0.0.1:8766/api/index-candles"),
  ];
  urls.forEach((url) => {
    url.searchParams.set("start_date", startDate);
    url.searchParams.set("end_date", endDate);
  });

  let lastError = null;
  for (const url of urls) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response?.ok) {
        lastError = new Error(`HTTP ${response?.status || "unknown"}`);
        continue;
      }
      const payload = await response.json();
      if (payload.ok) return payload;
      lastError = new Error(payload.error || "本機 index API 回傳失敗。");
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("本機 index API 未啟動。");
}

async function fetchTradingDates(startDate, endDate) {
  const rows = await finmindData("TaiwanStockTradingDate", { start_date: startDate, end_date: endDate });
  return normalizeTradingDateRows(rows, startDate, endDate);
}

function normalizeTradingDateRows(rows, startDate, endDate) {
  const dates = rows
    .map((row) => normalizeDateValue(row.date || row.trading_date || row.stock_date))
    .filter((date) => date && date >= startDate && date <= endDate)
    .filter((date) => !KNOWN_TWSE_CLOSED_DATES.has(date))
    .sort();
  return unique(dates);
}

async function fetchTaiexDailyCandles(startDate, endDate) {
  const attempts = [
    { dataset: "TaiwanStockPrice", params: { data_id: "TAIEX", start_date: startDate, end_date: endDate } },
    { dataset: "TaiwanStockPrice", params: { stock_id: "TAIEX", start_date: startDate, end_date: endDate } },
  ];

  for (const attempt of attempts) {
    try {
      const rows = await finmindData(attempt.dataset, attempt.params);
      const candles = normalizeDailyCandles(rows);
      if (candles.length) return candles;
    } catch (error) {
      // Try the next compatible daily endpoint shape.
    }
  }

  throw new Error("加權指數日資料端點查無資料。");
}

function normalizeDailyCandles(rows) {
  return rows
    .map(normalizeDailyCandle)
    .filter(Boolean)
    .sort((a, b) => a.time.localeCompare(b.time));
}

async function fetchRealtimeQuotes(options = {}) {
  if (state.isFetchingQuotes) return;
  state.isFetchingQuotes = true;
  const shouldAnnounce = !options.refresh;
  if (shouldAnnounce) {
    if (els.fetchOptionsBtn) els.fetchOptionsBtn.disabled = true;
    if (els.optionStatus) els.optionStatus.textContent = "正在讀取自動化部位即時報價資料...";
  }
  try {
    const payload = await localQuoteCache({ force: !options.auto && !options.refresh });
    const futuresRows = (payload.futures || []).map(normalizeFuturesSnapshotRow).filter(Boolean);
    const futuresQuote = selectFrontMonthFuture(futuresRows, "TXF");
    if (!futuresQuote) throw new Error("查無近月台指期貨 snapshot。");
    const indexQuote = selectIndexQuote((payload.index || []).map(normalizeIndexSnapshotRow).filter(Boolean));
    const filtered = (payload.options || [])
      .filter((row) => String(row.options_id || "").toUpperCase().startsWith("TXO"))
      .map(normalizeOptionSnapshotRow)
      .filter((row) => row && row.strike > 0)
      .sort((a, b) => a.strike - b.strike);
    if (!filtered.length) throw new Error("查無 TXO 月選擇權 snapshot，請確認 sponsor 權限或 token。");
    state.futuresQuote = futuresQuote;
    state.futuresQuotes = futuresRows;
    if (indexQuote) {
      state.indexQuote = indexQuote;
      setSpot(indexQuote.close, `FinMind 加權指數 snapshot${indexQuote.date ? ` ${indexQuote.date}` : ""}`);
    }
    state.optionChain = filtered;
    syncPositionMarketPrices();
    const latestTime = latestQuoteTime([futuresQuote, ...filtered]);
    const cacheTime = payload.updated_at ? `，更新 ${formatCacheTime(payload.updated_at)}` : "";
    const warningText = payload.error ? `；上次更新警告：${payload.error}` : "";
    const indexText = indexQuote ? `；加權指數 ${formatNumber(indexQuote.close, 0)}` : "";
    if (els.optionStatus) {
      els.optionStatus.textContent = `已讀取 ${filtered.length} 筆 TXO snapshot；近月台指期 ${futuresQuote.futures_id} ${formatNumber(futuresQuote.close, 0)}${indexText}${latestTime ? `，行情 ${latestTime}` : ""}${cacheTime}${warningText}。`;
    }
    renderAll();
  } catch (error) {
    const fallbackText = options.auto ? "，目前保留示範選擇權鏈" : "";
    if (shouldAnnounce) {
      if (els.optionStatus) els.optionStatus.textContent = `自動化部位即時報價讀取失敗：${error.message}${fallbackText}`;
    }
  } finally {
    if (shouldAnnounce && els.fetchOptionsBtn) els.fetchOptionsBtn.disabled = false;
    state.isFetchingQuotes = false;
  }
}

async function localQuoteCache(options = {}) {
  const urls = [
    new URL("/api/latest-quotes", window.location.origin),
    new URL("http://127.0.0.1:8766/api/latest-quotes"),
  ];
  if (options.force) {
    urls.forEach((url) => url.searchParams.set("force", "1"));
  }
  let response = null;
  for (const url of urls) {
    response = await fetch(url, { cache: "no-store" }).catch(() => null);
    if (response?.ok) break;
  }
  if (!response?.ok) throw new Error(response ? `HTTP ${response.status}` : "即時報價服務未啟動");
  const payload = await response.json();
  if (!payload.ok && !(payload.futures || []).length && !(payload.options || []).length) {
    throw new Error(payload.error || "即時報價尚未產生。");
  }
  return payload;
}

async function fetchOptionDaily() {
  if (els.fetchOptionsBtn) els.fetchOptionsBtn.disabled = true;
  if (els.optionStatus) els.optionStatus.textContent = "正在向 FinMind 抓取 TaiwanOptionDaily...";
  try {
    if (!els.optionDateInput) throw new Error("日資料查詢介面未啟用。");
    const params = { start_date: els.optionDateInput.value };
    const rows = await finmindData("TaiwanOptionDaily", params);
    const contract = els.contractInput?.value.trim() || "";
    const filtered = rows
      .filter((row) => normalizeOptionId(row.option_id) === "TXO")
      .filter((row) => !contract || String(row.contract_date).includes(contract))
      .map(normalizeOptionRow)
      .filter((row) => row.close > 0)
      .sort((a, b) => a.strike - b.strike);
    if (!filtered.length) throw new Error("查無 TXO 選擇權資料，請確認日期、契約月份或權限。");
    state.optionChain = filtered;
    if (els.optionStatus) els.optionStatus.textContent = `已更新 ${filtered.length} 筆 TXO 選擇權日資料。`;
    renderAll();
  } catch (error) {
    if (els.optionStatus) els.optionStatus.textContent = `FinMind 抓取失敗：${error.message}`;
  } finally {
    if (els.fetchOptionsBtn) els.fetchOptionsBtn.disabled = false;
  }
}

async function finmindData(dataset, params) {
  const url = new URL(FINMIND_URL);
  url.searchParams.set("dataset", dataset);
  Object.entries(params).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value);
  });
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = await response.json();
  if (payload.status && payload.status !== 200) {
    throw new Error(payload.msg || `FinMind status ${payload.status}`);
  }
  return payload.data || [];
}

function aggregateTaiexRows(rows, date) {
  const values = rows
    .map((row) => {
      const timestamp = normalizeTaiexIndicatorTimestamp(row);
      return {
        timestamp,
        date: normalizeDateValue(timestamp),
        value: firstNumber(row.TAIEX, row.taiex),
      };
    })
    .filter((row) => row.date === date && Number.isFinite(row.value) && row.value > 0)
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  if (!values.length) return null;
  const candle = {
    time: date,
    open: values[0].value,
    high: Math.max(...values.map((row) => row.value)),
    low: Math.min(...values.map((row) => row.value)),
    close: values[values.length - 1].value,
  };
  return isValidOhlc(candle.open, candle.high, candle.low, candle.close) ? candle : null;
}

function normalizeTaiexIndicatorTimestamp(row) {
  const rawDate = String(row.date || row.datetime || row.Date || "").trim().replace("T", " ");
  const normalizedDate = normalizeDateValue(rawDate);
  const time = String(row.time || row.Time || "").trim();
  if (rawDate.includes(":")) return rawDate;
  if (normalizedDate && time) return `${normalizedDate} ${time}`;
  return rawDate || normalizedDate;
}

function normalizeDailyCandle(row) {
  const time = normalizeDateValue(row.date || row.stock_date || row.Date);
  const open = firstNumber(row.open, row.Open, row.open_price);
  const high = firstNumber(row.max, row.high, row.High, row.high_price);
  const low = firstNumber(row.min, row.low, row.Low, row.low_price);
  const close = firstNumber(row.close, row.Close, row.close_price);
  if (!time || !isValidOhlc(open, high, low, close)) return null;
  return { time, open, high, low, close };
}

function isValidOhlc(open, high, low, close) {
  return [open, high, low, close].every((value) => Number.isFinite(value) && value > 0)
    && high >= low
    && high >= open
    && high >= close
    && low <= open
    && low <= close;
}

function normalizeDateValue(value) {
  if (!value) return "";
  const text = String(value).slice(0, 10).replace(/\//g, "-");
  const match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (!match) return "";
  return `${match[1]}-${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}`;
}

function firstNumber(...values) {
  for (const value of values) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return NaN;
}

function normalizeOptionRow(row) {
  return {
    option_id: normalizeOptionId(row.option_id),
    contract: String(row.contract_date || ""),
    strike: number(row.strike_price, 0),
    type: normalizeCallPut(row.call_put),
    open: number(row.open, 0),
    high: number(row.max, 0),
    low: number(row.min, 0),
    close: number(row.close || row.settlement_price, 0),
    volume: number(row.volume, 0),
    open_interest: number(row.open_interest, 0),
  };
}

function normalizeFuturesSnapshotRow(row) {
  const futuresId = String(row.futures_id || "").toUpperCase().trim();
  const parsed = parseFuturesId(futuresId);
  if (!parsed) return null;
  const bid = firstNumber(row.buy_price, 0);
  const ask = firstNumber(row.sell_price, 0);
  const close = firstNumber(row.close, bid && ask ? (bid + ask) / 2 : bid || ask);
  if (!Number.isFinite(close) || close <= 0) return null;
  return {
    futures_id: futuresId,
    product: parsed.product,
    contract: parsed.contract,
    close,
    bid,
    ask,
    volume: firstNumber(row.total_volume, row.volume, 0),
    date: String(row.date || ""),
    expiry: parsed.expiry,
  };
}

function normalizeIndexSnapshotRow(row) {
  const stockId = String(row.stock_id || row.data_id || "").toUpperCase().trim();
  const bid = firstNumber(row.buy_price, 0);
  const ask = firstNumber(row.sell_price, 0);
  const close = firstNumber(row.close, row.TAIEX, bid && ask ? (bid + ask) / 2 : bid || ask);
  if (!Number.isFinite(close) || close <= 0) return null;
  return {
    stock_id: stockId,
    close,
    bid,
    ask,
    date: String(row.date || ""),
  };
}

function normalizeOptionSnapshotRow(row) {
  const optionsId = String(row.options_id || "").toUpperCase().trim();
  const parsed = parseTxoOptionsId(optionsId);
  if (!parsed) return null;
  const bid = firstNumber(row.buy_price, 0);
  const ask = firstNumber(row.sell_price, 0);
  const close = firstNumber(row.close, bid && ask ? (bid + ask) / 2 : bid || ask);
  return {
    option_id: "TXO",
    options_id: optionsId,
    contract: parsed.contract,
    strike: parsed.strike,
    type: parsed.type,
    open: firstNumber(row.open, 0),
    high: firstNumber(row.high, row.max, 0),
    low: firstNumber(row.low, row.min, 0),
    close: Number.isFinite(close) ? Math.max(0, close) : 0,
    bid,
    bid_volume: firstNumber(row.buy_volume, 0),
    ask,
    ask_volume: firstNumber(row.sell_volume, 0),
    volume: firstNumber(row.total_volume, row.volume, 0),
    open_interest: 0,
    date: String(row.date || ""),
  };
}

function parseTxoOptionsId(optionsId) {
  const match = String(optionsId || "").toUpperCase().match(/^TXO(\d+)([A-X])(\d)$/);
  if (!match) return null;
  const strike = Number(match[1]);
  const code = match[2];
  const yearDigit = Number(match[3]);
  const callMonth = "ABCDEFGHIJKL".indexOf(code);
  const putMonth = "MNOPQRSTUVWX".indexOf(code);
  const monthIndex = callMonth >= 0 ? callMonth : putMonth;
  if (!Number.isFinite(strike) || monthIndex < 0) return null;
  const year = yearFromDerivativeDigit(yearDigit, monthIndex);
  return {
    strike,
    type: callMonth >= 0 ? "call" : "put",
    contract: `${year}${String(monthIndex + 1).padStart(2, "0")}`,
  };
}

function futureContractExpiry(futuresId) {
  return parseFuturesId(futuresId)?.expiry || null;
}

function parseFuturesId(futuresId) {
  const match = String(futuresId || "").toUpperCase().match(/^([A-Z]+)([A-L])(\d)$/);
  if (!match) return null;
  const product = normalizeFutureProduct(match[1]);
  const monthIndex = "ABCDEFGHIJKL".indexOf(match[2]);
  if (monthIndex < 0) return null;
  const expiry = thirdWednesday(yearFromDerivativeDigit(Number(match[3]), monthIndex), monthIndex);
  return {
    product,
    contract: `${expiry.getFullYear()}${String(expiry.getMonth() + 1).padStart(2, "0")}`,
    expiry,
  };
}

function yearFromDerivativeDigit(yearDigit, monthIndex) {
  const today = stripTime(new Date());
  const decadeYear = Math.floor(today.getFullYear() / 10) * 10 + yearDigit;
  return [decadeYear - 10, decadeYear, decadeYear + 10]
    .map((year) => ({ year, distance: Math.abs(thirdWednesday(year, monthIndex) - today) }))
    .sort((a, b) => a.distance - b.distance)[0].year;
}

function normalizeOptionId(value) {
  return String(value || "").toUpperCase().trim();
}

function normalizeFutureProduct(value) {
  const text = String(value || "").toUpperCase().trim();
  return FUTURE_ALIAS_TO_PRODUCT[text] || (FUTURE_SPECS[text] ? text : "TXF");
}

function futureMultiplier(product) {
  return FUTURE_SPECS[normalizeFutureProduct(product)]?.multiplier || FUTURE_SPECS.TXF.multiplier;
}

function normalizeCallPut(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("put") || text.includes("p") || text.includes("賣")) return "put";
  return "call";
}

function selectFrontMonthFuture(rows, product = "TXF") {
  const today = stripTime(new Date());
  const targetProduct = normalizeFutureProduct(product);
  const activeRows = rows.filter((row) => row && row.close > 0 && row.product === targetProduct);
  const datedRows = activeRows
    .filter((row) => row.expiry)
    .sort((a, b) => a.expiry - b.expiry);
  const frontMonth = datedRows.find((row) => row.expiry >= today);
  if (frontMonth) return frontMonth;
  if (datedRows.length) return datedRows[datedRows.length - 1];
  return activeRows.sort((a, b) => b.volume - a.volume)[0] || null;
}

function selectIndexQuote(rows) {
  return rows
    .filter((row) => row && row.close > 0)
    .sort((a, b) => {
      const aIsTaiex = a.stock_id === "001" || a.stock_id === "TAIEX";
      const bIsTaiex = b.stock_id === "001" || b.stock_id === "TAIEX";
      if (aIsTaiex !== bIsTaiex) return aIsTaiex ? -1 : 1;
      return String(b.date).localeCompare(String(a.date));
    })[0] || null;
}

function setSpot(value) {
  const parsed = number(value, NaN);
  if (!Number.isFinite(parsed) || parsed <= 0) return;
  state.spot = parsed;
}

function latestQuoteTime(rows) {
  return rows
    .map((row) => row.date)
    .filter(Boolean)
    .sort()
    .at(-1) || "";
}

function optionMarkPrice(option) {
  if (!option) return 0;
  if (option.close > 0) return option.close;
  if (option.bid > 0 && option.ask > 0) return (option.bid + option.ask) / 2;
  return option.bid || option.ask || 0;
}

function tQuoteCenterPrice() {
  return state.futuresQuote?.close || state.spot;
}

function formatQuote(value) {
  return value > 0 ? formatNumber(value, 1) : "-";
}

function formatCacheTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("zh-TW", { hour12: false });
}

function filteredPositions() {
  if (state.filter === "all") return state.positions;
  return state.positions.filter((position) => position.kind === state.filter);
}

function positionMetrics(position) {
  if (isFuturePosition(position)) return futurePositionMetrics(position);
  const sign = position.side === "long" ? 1 : -1;
  const t = dte(position.expiry);
  const marketQuote = positionMarketQuote(position);
  const market = marketQuote.price;
  const quoteLeg = marketQuote.leg || tQuoteOptionLeg(position);
  const quoteGreeksReady = quoteLeg && ["delta", "gamma", "theta", "vega"].every((key) => Number.isFinite(Number(quoteLeg[key])));
  if (quoteGreeksReady) {
    const multiplier = sign * position.qty * POINT_VALUE;
    return {
      market,
      marketSource: marketQuote.source,
      pnl: (market - position.premium) * multiplier,
      delta: Number(quoteLeg.delta) * multiplier,
      gamma: Number(quoteLeg.gamma) * multiplier,
      theta: Number(quoteLeg.theta) * multiplier,
      vega: Number(quoteLeg.vega) * multiplier,
      iv: firstNumber(quoteLeg.mid_iv, quoteLeg.iv),
    };
  }
  const iv = impliedVolatility(state.spot, position.strike, t, state.rate, market || position.premium, position.type) || 0.22;
  const greeks = blackScholesGreeks(state.spot, position.strike, t, state.rate, iv, position.type);
  const multiplier = sign * position.qty * POINT_VALUE;
  return {
    market,
    marketSource: marketQuote.source,
    pnl: (market - position.premium) * multiplier,
    delta: greeks.delta * multiplier,
    gamma: greeks.gamma * multiplier,
    theta: greeks.theta * multiplier,
    vega: (greeks.vega / 100) * multiplier,
    iv,
  };
}

function futurePositionMetrics(position) {
  const sign = position.side === "long" ? 1 : -1;
  const marketQuote = positionMarketQuote(position);
  const market = marketQuote.price;
  const multiplier = futureMultiplier(position.product);
  const qty = number(position.qty, 0);
  return {
    market,
    marketSource: marketQuote.source,
    pnl: (market - position.premium) * sign * qty * multiplier,
    delta: sign * qty * multiplier,
    gamma: null,
    theta: null,
    vega: null,
    iv: null,
  };
}

function positionMarketPrice(position) {
  return positionMarketQuote(position).price;
}

function positionMarketQuote(position) {
  const snapshot = isFuturePosition(position)
    ? snapshotFutureMarketQuote(position)
    : snapshotOptionMarketQuote(position);
  if (snapshot.price > 0) return snapshot;
  if (position.market > 0) {
    return { price: position.market, source: "legacy" };
  }
  return { price: position.premium, source: "entry" };
}

function snapshotOptionMarketQuote(position) {
  const fugleQuote = snapshotOptionMarketQuoteFromTQuote(position);
  if (fugleQuote.price > 0) return fugleQuote;

  const contract = positionContract(position) || currentMonthContract();
  const sameSeries = state.optionChain
    .filter((row) => row.type === position.type)
    .filter((row) => Math.abs(row.strike - position.strike) < 0.001);
  const exact = sameSeries.find((row) => contract && row.contract === contract);
  const fallback = contract ? null : sameSeries[0];
  const quote = exact || fallback;
  const price = optionMarkPrice(quote);
  if (price <= 0) return { price: 0, source: "missing", contract };
  return {
    price,
    source: exact ? "snapshot" : "snapshot-fallback",
    contract: quote.contract,
  };
}

function snapshotOptionMarketQuoteFromTQuote(position) {
  const contract = positionContract(position);
  const snapshot = state.fugleTQuote;
  const snapshotContract = snapshot?.contract_month || snapshot?.contract;
  if (!snapshot || (contract && snapshotContract && contract !== snapshotContract)) {
    return { price: 0, source: "missing", contract };
  }
  const leg = tQuoteOptionLeg(position);
  const price = fugleLegMarkPrice(leg);
  if (price <= 0) return { price: 0, source: "missing", contract: snapshotContract || contract };
  const usesMid = firstNumber(leg?.mid) > 0 || (firstNumber(leg?.bid) > 0 && firstNumber(leg?.ask) > 0);
  const source = snapshot.stale
    ? (usesMid ? "fugle-cache-mid" : "fugle-cache-last")
    : (usesMid ? "fugle-live-mid" : "fugle-live-last");
  return {
    price,
    source,
    contract: snapshotContract || contract,
    leg,
  };
}

function tQuoteOptionLeg(position) {
  const snapshot = state.fugleTQuote;
  if (!snapshot?.rows?.length || isFuturePosition(position)) return null;
  const strike = number(position.strike, NaN);
  if (!Number.isFinite(strike)) return null;
  const row = snapshot.rows.find((item) => Math.abs(number(item.strike, NaN) - strike) < 0.001);
  if (!row) return null;
  return row[position.type] || null;
}

function snapshotFutureMarketQuote(position) {
  const product = normalizeFutureProduct(position.product);
  const contract = positionContract(position) || state.futuresQuote?.contract || "";
  const sameProduct = state.futuresQuotes.filter((row) => row.product === product);
  const exact = sameProduct.find((row) => contract && row.contract === contract);
  const fallback = selectFrontMonthFuture(sameProduct, product);
  const proxy = product === "MXF"
    ? state.futuresQuotes.find((row) => row.product === "TXF" && contract && row.contract === contract) || state.futuresQuote
    : null;
  const quote = exact || fallback || proxy;
  const price = quote?.close || 0;
  if (price <= 0) return { price: 0, source: "missing", contract };
  if (!exact && !fallback && proxy) {
    return {
      price,
      source: "txf-proxy",
      contract: quote.contract,
    };
  }
  return {
    price,
    source: exact ? "snapshot" : "snapshot-fallback",
    contract: quote.contract,
  };
}

function syncPositionMarketPrices() {
  let changed = false;
  state.positions = state.positions.map((position) => {
    const quote = isFuturePosition(position)
      ? snapshotFutureMarketQuote(position)
      : snapshotOptionMarketQuote(position);
    if (quote.price <= 0) return position;
    const nextContract = position.contract || quote.contract || positionContract(position);
    if (Math.abs(number(position.market, 0) - quote.price) < 0.001 && position.contract === nextContract) {
      return position;
    }
    changed = true;
    return {
      ...position,
      contract: nextContract,
      market: quote.price,
    };
  });
  if (changed) savePositions();
  return changed;
}

function isFuturePosition(position) {
  return position.instrument === "future";
}

function positionContract(position) {
  if (position.contract) return String(position.contract);
  return expiryToContract(position.expiry);
}

function expiryToContract(expiry) {
  const date = parseDate(expiry);
  if (!date) return "";
  return `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function marketSourceLabel(source) {
  if (source === "fugle-live-mid") return "Fugle live bid/ask mid";
  if (source === "fugle-live-last") return "Fugle live last；bid/ask mid 不可用";
  if (source === "fugle-cache-mid") return "Fugle 最後有效截面 bid/ask mid";
  if (source === "fugle-cache-last") return "Fugle 最後有效截面 last；bid/ask mid 不可用";
  if (source === "snapshot") return "FinMind 即時 snapshot";
  if (source === "snapshot-fallback") return "FinMind snapshot：未找到相同到期月，改用近月報價";
  if (source === "txf-proxy") return "FinMind 未回傳小台 snapshot，暫用同月份大台 TXF 即時價估算";
  if (source === "legacy") return "沿用舊部位儲存的市價，等待 FinMind snapshot";
  return "尚無 snapshot，暫以建倉價估算";
}

function aggregateRisk(positions) {
  return positions.reduce((acc, position) => {
    const metrics = positionMetrics(position);
    acc.pnl += metrics.pnl;
    acc.delta += metrics.delta;
    acc.gamma += metrics.gamma;
    acc.theta += metrics.theta;
    acc.vega += metrics.vega;
    return acc;
  }, { pnl: 0, delta: 0, gamma: 0, theta: 0, vega: 0 });
}

function positionStrikeLevels() {
  return unique(state.positions.filter((position) => !isFuturePosition(position)).map((position) => number(position.strike, NaN)).filter(Number.isFinite))
    .sort((a, b) => a - b);
}

function positionPriceLevels() {
  return unique(state.positions.map((position) => {
    return isFuturePosition(position) ? number(position.premium, NaN) : number(position.strike, NaN);
  }).filter(Number.isFinite))
    .sort((a, b) => a - b);
}

function payoffPriceBounds() {
  const levels = positionPriceLevels();
  if (!levels.length) {
    return {
      xMin: Math.max(0, Math.floor((state.spot - 1500) / 100) * 100),
      xMax: Math.ceil((state.spot + 1500) / 100) * 100,
    };
  }

  const minStrike = Math.min(state.spot, levels[0]);
  const maxStrike = Math.max(state.spot, levels.at(-1));
  const strikeSpan = Math.max(500, maxStrike - minStrike);
  const premiumSpan = state.positions.reduce((sum, position) => {
    if (isFuturePosition(position)) return sum;
    return sum + number(position.premium, 0) * number(position.qty, 1);
  }, 0);
  const padding = Math.max(1200, strikeSpan * 0.65, premiumSpan + 500);
  return {
    xMin: Math.max(0, Math.floor((minStrike - padding) / 100) * 100),
    xMax: Math.ceil((maxStrike + padding) / 100) * 100,
  };
}

function portfolioPayoff(underlying) {
  return state.positions.reduce((sum, position) => {
    if (isFuturePosition(position)) {
      const sign = position.side === "long" ? 1 : -1;
      return sum + (underlying - position.premium) * sign * position.qty * futureMultiplier(position.product);
    }
    const intrinsic = position.type === "call"
      ? Math.max(0, underlying - position.strike)
      : Math.max(0, position.strike - underlying);
    const sign = position.side === "long" ? 1 : -1;
    return sum + (intrinsic - position.premium) * sign * position.qty * POINT_VALUE;
  }, 0);
}

function expiryPayoffExtremes() {
  const criticalPrices = unique([0, ...positionPriceLevels()]).sort((a, b) => a - b);
  const values = criticalPrices.length ? criticalPrices.map(portfolioPayoff) : [0];
  const upperSlope = state.positions.reduce((sum, position) => {
    if (isFuturePosition(position)) {
      const sign = position.side === "long" ? 1 : -1;
      return sum + sign * position.qty * futureMultiplier(position.product);
    }
    if (position.type !== "call") return sum;
    const sign = position.side === "long" ? 1 : -1;
    return sum + sign * position.qty * POINT_VALUE;
  }, 0);

  return {
    maxProfit: upperSlope > 0 ? Infinity : Math.max(...values),
    maxLoss: upperSlope < 0 ? -Infinity : Math.min(...values),
  };
}

function portfolioTheoryPnl(underlying) {
  return state.positions.reduce((sum, position) => {
    if (isFuturePosition(position)) {
      const sign = position.side === "long" ? 1 : -1;
      return sum + (underlying - position.premium) * sign * position.qty * futureMultiplier(position.product);
    }
    const t = dte(position.expiry);
    const market = positionMarketPrice(position);
    const iv = impliedVolatility(state.spot, position.strike, t, state.rate, market || position.premium, position.type) || 0.22;
    const price = blackScholesPrice(underlying, position.strike, t, state.rate, iv, position.type);
    const sign = position.side === "long" ? 1 : -1;
    return sum + (price - position.premium) * sign * position.qty * POINT_VALUE;
  }, 0);
}

function blackScholesPrice(s, k, tDays, r, vol, type) {
  const t = Math.max(tDays / 365, 1 / 365);
  const sigma = Math.max(vol, 0.0001);
  const d1 = (Math.log(s / k) + (r + (sigma * sigma) / 2) * t) / (sigma * Math.sqrt(t));
  const d2 = d1 - sigma * Math.sqrt(t);
  if (type === "call") {
    return s * normCdf(d1) - k * Math.exp(-r * t) * normCdf(d2);
  }
  return k * Math.exp(-r * t) * normCdf(-d2) - s * normCdf(-d1);
}

function blackScholesGreeks(s, k, tDays, r, vol, type) {
  const t = Math.max(tDays / 365, 1 / 365);
  const sigma = Math.max(vol, 0.0001);
  const sqrtT = Math.sqrt(t);
  const d1 = (Math.log(s / k) + (r + (sigma * sigma) / 2) * t) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const pdf = normPdf(d1);
  const delta = type === "call" ? normCdf(d1) : normCdf(d1) - 1;
  const gamma = pdf / (s * sigma * sqrtT);
  const vega = s * pdf * sqrtT;
  const callTheta = (-(s * pdf * sigma) / (2 * sqrtT) - r * k * Math.exp(-r * t) * normCdf(d2)) / 365;
  const putTheta = (-(s * pdf * sigma) / (2 * sqrtT) + r * k * Math.exp(-r * t) * normCdf(-d2)) / 365;
  return { delta, gamma, theta: type === "call" ? callTheta : putTheta, vega };
}

function impliedVolatility(s, k, tDays, r, marketPrice, type) {
  if (!marketPrice || marketPrice <= 0) return null;
  let low = 0.01;
  let high = 2.5;
  for (let i = 0; i < 80; i += 1) {
    const mid = (low + high) / 2;
    const price = blackScholesPrice(s, k, tDays, r, mid, type);
    if (Math.abs(price - marketPrice) < 0.01) return mid;
    if (price > marketPrice) high = mid;
    else low = mid;
  }
  return (low + high) / 2;
}

function normCdf(x) {
  return 0.5 * (1 + erf(x / Math.sqrt(2)));
}

function normPdf(x) {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

function erf(x) {
  const sign = x >= 0 ? 1 : -1;
  const abs = Math.abs(x);
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const t = 1 / (1 + p * abs);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-abs * abs);
  return sign * y;
}

function groupOptionChain(rows, options = {}) {
  const { contract = "", range = 1200, center = state.spot } = options;
  const byStrike = new Map();
  rows.filter((row) => !contract || row.contract === contract).forEach((row) => {
    if (!byStrike.has(row.strike)) byStrike.set(row.strike, { strike: row.strike, contract: row.contract });
    const target = byStrike.get(row.strike);
    target.contract = target.contract || row.contract;
    target[row.type] = row;
  });
  return Array.from(byStrike.values())
    .filter((row) => Math.abs(row.strike - center) <= range)
    .sort((a, b) => a.strike - b.strike);
}

function tQuoteStrikeStatus(rows) {
  const strikes = rows.map((row) => row.strike).filter(Number.isFinite);
  if (strikes.length < 2) return "履約價間距待確認";
  const gaps = [];
  for (let index = 1; index < strikes.length; index += 1) {
    gaps.push(strikes[index] - strikes[index - 1]);
  }
  const minGap = Math.min(...gaps);
  const maxGap = Math.max(...gaps);
  const distinctGaps = unique(gaps).sort((a, b) => a - b);
  const gapText = distinctGaps.length <= 3
    ? distinctGaps.map((gap) => `${gap}點`).join("/")
    : `${minGap}-${maxGap}點`;
  return `履約價 ${formatNumber(strikes[0], 0)}-${formatNumber(strikes.at(-1), 0)} / 實際間距 ${gapText}`;
}

function currentMonthContract() {
  const contracts = unique(state.optionChain.map((row) => row.contract).filter(Boolean));
  if (!contracts.length) return "";
  const monthlyContracts = contracts.filter((contract) => /^\d{6}$/.test(contract));
  const candidates = monthlyContracts.length ? monthlyContracts : contracts;
  if (state.futuresQuote?.contract && candidates.includes(state.futuresQuote.contract)) {
    return state.futuresQuote.contract;
  }

  const today = stripTime(new Date());
  const withExpiry = candidates
    .map((contract) => ({ contract, expiry: contractToExpiry(contract) }))
    .filter((item) => item.expiry)
    .sort((a, b) => a.expiry - b.expiry);
  const frontMonth = withExpiry.find((item) => item.expiry >= today);
  if (frontMonth) return frontMonth.contract;
  if (withExpiry.length) return withExpiry[withExpiry.length - 1].contract;

  return candidates.sort()[0];
}

function optionChainStats() {
  const grouped = groupOptionChain(state.optionChain);
  if (!grouped.length) return {};
  const atm = grouped.reduce((best, row) => {
    return Math.abs(row.strike - state.spot) < Math.abs(best.strike - state.spot) ? row : best;
  }, grouped[0]);
  const t = dteFromContract(atm.contract);
  const callIv = atm.call ? impliedVolatility(state.spot, atm.strike, t, state.rate, optionMarkPrice(atm.call), "call") : null;
  const putIv = atm.put ? impliedVolatility(state.spot, atm.strike, t, state.rate, optionMarkPrice(atm.put), "put") : null;
  return {
    atmIv: average([callIv, putIv].filter(Boolean)),
    skew: callIv && putIv ? putIv - callIv : null,
  };
}

function buildStrategyRows() {
  const markets = [
    { name: "急漲", className: "market-fast", direction: "BC" },
    { name: "緩漲", className: "market-slow", direction: "SP" },
    { name: "盤整", className: "market-range", direction: "BC + SP" },
    { name: "緩跌", className: "market-down", direction: "SC" },
    { name: "急跌", className: "market-down", direction: "BP" },
  ];
  const volTrends = ["波動上升", "波動盤整", "波動下降"];
  const expiries = ["P1", "P2", "P3", "P4"];
  const overrides = strategyOverrides();
  let id = 1;
  const rows = [];

  markets.forEach((market) => {
    volTrends.forEach((volTrend) => {
      expiries.forEach((expiry) => {
        const key = `${market.name}|${volTrend}|${expiry}`;
        const row = {
          id,
          market: market.name,
          marketClass: market.className,
          volTrend,
          expiry,
          direction: market.direction,
          volLeg: volLegFor(volTrend),
          timeLeg: timeLegFor(expiry),
          trade: tradeFor(market.name, volTrend, expiry),
          spread: spreadFor(market.name, volTrend, expiry),
          ...overrides[key],
        };
        rows.push(row);
        id += 1;
      });
    });
  });

  return rows;
}

function strategyOverrides() {
  return {
    "急漲|波動上升|P1": { trade: "3BC + 2BP" },
    "急漲|波動上升|P2": { trade: "2BC + BP" },
    "急漲|波動上升|P3": { trade: "BC", spread: "BCSC,BPSP,BC" },
    "急漲|波動上升|P4": { trade: "2BC + BP" },
    "急漲|波動盤整|P1": { trade: "2BC + BP" },
    "急漲|波動盤整|P2": { trade: "BC" },
    "急漲|波動盤整|P3": { trade: "SP", spread: "BCSC,SP" },
    "急漲|波動盤整|P4": { trade: "BC" },
    "急漲|波動下降|P1": { trade: "BC", spread: "BCSC,BPSP,BC" },
    "急漲|波動下降|P2": { trade: "SP" },
    "急漲|波動下降|P3": { trade: "2SP + SC" },
    "急漲|波動下降|P4": { trade: "SP" },
    "緩漲|波動上升|P1": { trade: "2BC + BP" },
    "緩漲|波動上升|P2": { trade: "BC" },
    "緩漲|波動上升|P3": { trade: "SP" },
    "緩漲|波動上升|P4": { trade: "BC" },
    "緩漲|波動盤整|P1": { trade: "BC" },
    "緩漲|波動盤整|P2": { trade: "SP" },
    "緩漲|波動盤整|P3": { trade: "SC + 2SP" },
    "緩漲|波動盤整|P4": { trade: "SP" },
  };
}

function volLegFor(volTrend) {
  if (volTrend === "波動上升") return "BC + BP";
  if (volTrend === "波動下降") return "SC + SP";
  return "-";
}

function timeLegFor(expiry) {
  if (expiry === "P1") return "BC + BP";
  if (expiry === "P3") return "SC + SP";
  return "-";
}

function tradeFor(market, volTrend, expiry) {
  const directionBias = {
    急漲: "BC",
    緩漲: "SP",
    盤整: "IC",
    緩跌: "SC",
    急跌: "BP",
  }[market];
  const highVolBias = market.includes("漲") ? "BC + BP" : market.includes("跌") ? "SC + SP" : "BPSP";
  const lowVolBias = market.includes("漲") ? "SP" : market.includes("跌") ? "SC" : "BCSC";

  if (volTrend === "波動上升" && expiry === "P1") return highVolBias;
  if (volTrend === "波動下降" && expiry === "P3") return lowVolBias;
  if (expiry === "P2" || expiry === "P4") return directionBias;
  return directionBias;
}

function spreadFor(market, volTrend, expiry) {
  if (market === "盤整" && volTrend === "波動下降") return "BCSC,BPSP";
  if (market.includes("漲") && expiry === "P3") return "BCSC,SP";
  if (market.includes("跌") && expiry === "P3") return "BPSP,SC";
  if (volTrend === "波動下降" && expiry === "P1") return "BCSC,BPSP";
  return "";
}

function indexTrend() {
  if (state.indexCandles.length < 6) return 0;
  const last = state.indexCandles[state.indexCandles.length - 1].close;
  const prev = state.indexCandles[state.indexCandles.length - 6].close;
  return (last - prev) / prev;
}

function supportResistanceText() {
  const grouped = groupOptionChain(state.optionChain);
  if (!grouped.length) return "ATM 附近履約價";
  const topOi = [...grouped].sort((a, b) => ((b.call?.open_interest || 0) + (b.put?.open_interest || 0)) - ((a.call?.open_interest || 0) + (a.put?.open_interest || 0)))[0];
  const nearest = grouped.reduce((best, row) => Math.abs(row.strike - state.spot) < Math.abs(best.strike - state.spot) ? row : best, grouped[0]);
  return `${formatNumber(nearest.strike, 0)} ATM 與 ${formatNumber(topOi.strike, 0)} 高 OI`;
}

function findBreakevens(points) {
  const result = [];
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const next = points[i];
    if ((prev.expiry <= 0 && next.expiry >= 0) || (prev.expiry >= 0 && next.expiry <= 0)) {
      const ratio = Math.abs(prev.expiry) / (Math.abs(prev.expiry) + Math.abs(next.expiry) || 1);
      result.push(prev.price + (next.price - prev.price) * ratio);
    }
  }
  return result;
}

function contractLabel(position) {
  if (isFuturePosition(position)) {
    const product = normalizeFutureProduct(position.product);
    const spec = FUTURE_SPECS[product] || FUTURE_SPECS.TXF;
    const contract = positionContract(position) || state.futuresQuote?.contract || "";
    return `${spec.label} ${contract}`;
  }
  const ym = positionContract(position) || "TXO";
  return `TXO ${ym} ${position.strike}${position.type === "call" ? "C" : "P"}`;
}

function nearestExpiryLabel() {
  const today = stripTime(new Date());
  const dates = state.positions
    .map((item) => parseDate(item.expiry))
    .filter((date) => date && date >= today)
    .sort((a, b) => a - b);
  if (!dates.length) return "--";
  return `${toDateInput(dates[0])} / ${businessDaysBetween(today, dates[0])}D`;
}

function dte(expiry) {
  const date = parseDate(expiry);
  if (!date) return 1;
  return Math.max(1, calendarDaysBetween(stripTime(new Date()), date));
}

function dteFromContract(contract) {
  const expiry = contractToExpiry(contract);
  return expiry ? Math.max(1, calendarDaysBetween(stripTime(new Date()), expiry)) : 30;
}

function contractToExpiry(contract) {
  const digits = String(contract || "").replace(/\D/g, "");
  if (digits.length < 6) return null;
  const year = Number(digits.slice(0, 4));
  const month = Number(digits.slice(4, 6)) - 1;
  if (!year || month < 0) return null;
  return thirdWednesday(year, month);
}

function frontMonthExpiry(date) {
  const today = stripTime(date);
  const currentMonthExpiry = thirdWednesday(today.getFullYear(), today.getMonth());
  return currentMonthExpiry >= today
    ? currentMonthExpiry
    : thirdWednesday(today.getFullYear(), today.getMonth() + 1);
}

function thirdWednesday(year, monthIndex) {
  const date = new Date(year, monthIndex, 1);
  const firstWednesdayOffset = (3 - date.getDay() + 7) % 7;
  return stripTime(new Date(year, monthIndex, 1 + firstWednesdayOffset + 14));
}

function businessDaysBetween(start, end) {
  let count = 0;
  const cursor = new Date(start);
  while (cursor <= end) {
    const day = cursor.getDay();
    if (day !== 0 && day !== 6) count += 1;
    cursor.setDate(cursor.getDate() + 1);
  }
  return count;
}

function calendarDaysBetween(start, end) {
  return Math.ceil((end - start) / 86400000);
}

function weekdayDatesBetween(startText, endText) {
  const start = parseDate(startText);
  const end = parseDate(endText);
  if (!start || !end || start > end) return [];
  const dates = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    const day = cursor.getDay();
    if (day !== 0 && day !== 6) dates.push(toDateInput(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
}

function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function monthsBetween(start, end) {
  const months = [];
  const cursor = new Date(start.getFullYear(), start.getMonth(), 1);
  const last = new Date(end.getFullYear(), end.getMonth(), 1);
  while (cursor <= last) {
    months.push(new Date(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }
  return months;
}

function sameDate(a, b) {
  return a && b && toDateInput(a) === toDateInput(b);
}

function parseDate(value) {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : stripTime(value);
  const text = String(value);
  const normalized = normalizeDateValue(text);
  const date = normalized ? new Date(`${normalized}T00:00:00`) : new Date(text);
  return Number.isNaN(date.getTime()) ? null : stripTime(date);
}

function stripTime(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function toDateInput(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function sampleIndexCandles() {
  const start = addDays(new Date(), -32);
  const candles = [];
  let close = 22140;
  for (let i = 0; i < 24; i += 1) {
    const date = addDays(start, i);
    if (date.getDay() === 0 || date.getDay() === 6) continue;
    const drift = Math.sin(i / 2.6) * 95 + (i - 10) * 18;
    const open = close + Math.sin(i) * 70;
    close = 22140 + drift + i * 17;
    const high = Math.max(open, close) + 90 + (i % 3) * 22;
    const low = Math.min(open, close) - 80 - (i % 4) * 18;
    candles.push({ time: toDateInput(date), open, high, low, close });
  }
  return candles;
}

function sampleOptionChain() {
  const expiry = frontMonthExpiry(new Date());
  const contract = `${expiry.getFullYear()}${String(expiry.getMonth() + 1).padStart(2, "0")}`;
  const rows = [];
  for (let strike = 19600; strike <= 25600; strike += SAMPLE_MONTHLY_STRIKE_STEP) {
    const distance = Math.abs(strike - state.spot);
    const base = Math.max(28, 240 - distance * 0.14);
    rows.push({
      option_id: "TXO",
      contract,
      strike,
      type: "call",
      bid: Math.max(0, base + (state.spot - strike) * 0.22 - 1),
      ask: Math.max(0, base + (state.spot - strike) * 0.22 + 1),
      close: Math.max(8, base + (state.spot - strike) * 0.22),
      volume: Math.max(0, Math.round(3500 - distance * 1.2 + Math.random() * 240)),
      open_interest: Math.max(0, Math.round(6200 - distance * 1.5 + Math.random() * 700)),
    });
    rows.push({
      option_id: "TXO",
      contract,
      strike,
      type: "put",
      bid: Math.max(0, base + (strike - state.spot) * 0.24 - 1),
      ask: Math.max(0, base + (strike - state.spot) * 0.24 + 1),
      close: Math.max(8, base + (strike - state.spot) * 0.24),
      volume: Math.max(0, Math.round(3600 - distance * 1.1 + Math.random() * 260)),
      open_interest: Math.max(0, Math.round(6600 - distance * 1.3 + Math.random() * 760)),
    });
  }
  return rows;
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function number(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

function unique(values) {
  return Array.from(new Set(values));
}

function setSelectOptions(select, values, allLabel) {
  select.innerHTML = [
    `<option value="all">${allLabel}</option>`,
    ...values.map((value) => `<option value="${value}">${value}</option>`),
  ].join("");
}

function makeId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatNumber(value, digits = 0) {
  return Number(value).toLocaleString("zh-TW", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatMoney(value) {
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(Math.round(value)).toLocaleString("zh-TW")}`;
}

function formatNullableNumber(value, digits = 0) {
  return Number.isFinite(value) ? formatNumber(value, digits) : "-";
}

function formatNullableMoney(value) {
  return Number.isFinite(value) ? formatMoney(value) : "-";
}

function formatNullablePercent(value) {
  return Number.isFinite(value) ? formatPercent(value) : "-";
}

function formatPercent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function formatCompact(value) {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1000000) return `${sign}${(abs / 1000000).toFixed(1)}M`;
  if (abs >= 1000) return `${sign}${(abs / 1000).toFixed(0)}K`;
  return `${sign}${abs.toFixed(0)}`;
}
