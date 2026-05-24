const POINT_VALUE = 50;
const STORAGE_KEY = "txo-dashboard-positions-v1";
const FINMIND_URL = "https://api.finmindtrade.com/api/v4/data";
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
  indexChart: null,
  scoreChart: null,
  scoreDeltaChart: null,
  candleSeries: null,
  scoreSeries: null,
  scoreDeltaSeries: null,
  chartsSynced: false,
  crosshairsSynced: false,
  isSyncingChartRange: false,
  isSyncingCrosshair: false,
  candleByTime: new Map(),
  scoreByTime: new Map(),
  scoreDeltaByTime: new Map(),
  settlementDates: [...FALLBACK_SETTLEMENT_DATES],
  finmindToken: "",
  isFetchingIndex: false,
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
  loadEnvToken().then(() => fetchIndexCandles({ auto: true }));
  window.addEventListener("resize", () => {
    drawPayoff();
    resizeIndexChart();
  });
});

function bindElements() {
  Object.assign(els, {
    positionForm: document.querySelector("#positionForm"),
    addPositionBtn: document.querySelector("#addPositionBtn"),
    positionsBody: document.querySelector("#positionsBody"),
    spotInput: document.querySelector("#spotInput"),
    spotReadout: document.querySelector("#spotReadout"),
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
    settlementCalendar: document.querySelector("#settlementCalendar"),
    calendarList: document.querySelector("#calendarList"),
    riskSummary: document.querySelector("#riskSummary"),
    regimeAdvice: document.querySelector("#regimeAdvice"),
    strategyBody: document.querySelector("#strategyBody"),
    strategyStatus: document.querySelector("#strategyStatus"),
    strategyViewFilter: document.querySelector("#strategyViewFilter"),
    strategyVolFilter: document.querySelector("#strategyVolFilter"),
    strategyTimeFilter: document.querySelector("#strategyTimeFilter"),
  });
}

function bindEvents() {
  els.positionForm.addEventListener("submit", handleAddPosition);
  els.addPositionBtn.addEventListener("click", handleAddPosition);
  els.spotInput.addEventListener("input", () => {
    state.spot = number(els.spotInput.value, state.spot);
    renderAll();
  });
  els.rateInput.addEventListener("input", () => {
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
  els.fetchOptionsBtn.addEventListener("click", fetchOptionDaily);
  els.positionsBody.addEventListener("click", handlePositionAction);
  els.optionChainBody.addEventListener("click", handleChainAction);
  els.strategyViewFilter.addEventListener("change", handleStrategyFilterChange);
  els.strategyVolFilter.addEventListener("change", handleStrategyFilterChange);
  els.strategyTimeFilter.addEventListener("change", handleStrategyFilterChange);
  populateStrategyFilters();
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
  els.optionDateInput.value = end;
  els.positionForm.elements.expiry.value = toDateInput(expiry);
}

function defaultIndexStartDate(today) {
  const sixMonthsAgo = new Date(today.getFullYear(), today.getMonth() - 6, today.getDate());
  const threshold = toDateInput(sixMonthsAgo);
  return settlementDates().find((date) => date >= threshold)
    || settlementDates()[0]
    || threshold;
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
  const expiry = toDateInput(thirdWednesday(new Date().getFullYear(), new Date().getMonth() + 1));
  return [
    {
      id: makeId(),
      kind: "live",
      type: "call",
      side: "short",
      strike: 23000,
      qty: 2,
      premium: 155,
      market: 132,
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
      market: 126,
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
      market: 78,
      expiry,
    },
  ];
}

function seedMarketData() {
  state.indexCandles = sampleIndexCandles();
  state.optionChain = sampleOptionChain();
}

function renderAll() {
  els.spotInput.value = Math.round(state.spot);
  els.spotReadout.textContent = formatNumber(state.spot, 0);
  renderPositions();
  drawPayoff();
  renderIndexChart();
  renderOptionChain();
  renderStrategyTable();
  renderCalendar();
  renderRiskAndAdvice();
}

function handleAddPosition(event) {
  event.preventDefault();
  const form = new FormData(els.positionForm);
  const position = {
    id: makeId(),
    kind: form.get("kind"),
    type: form.get("type"),
    side: form.get("side"),
    strike: number(form.get("strike"), 0),
    qty: Math.max(1, number(form.get("qty"), 1)),
    premium: Math.max(0, number(form.get("premium"), 0)),
    market: Math.max(0, number(form.get("market"), 0)),
    expiry: form.get("expiry"),
  };
  state.positions.push(position);
  savePositions();
  renderAll();
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
  const expiry = contractToExpiry(button.dataset.contract) || els.positionForm.elements.expiry.value;
  state.positions.push({
    id: makeId(),
    kind: "sim",
    type,
    side: "long",
    strike,
    qty: 1,
    premium: price,
    market: price,
    expiry,
  });
  savePositions();
  renderAll();
}

function renderPositions() {
  const positions = filteredPositions();
  els.positionsBody.innerHTML = positions.map((position) => {
    const metrics = positionMetrics(position);
    const pnlClass = metrics.pnl >= 0 ? "positive" : "negative";
    return `
      <tr>
        <td><span class="tag ${position.kind}">${position.kind === "live" ? "實際" : "模擬"}</span></td>
        <td>${contractLabel(position)}</td>
        <td>${position.side === "long" ? "買進" : "賣出"}</td>
        <td>${position.qty}</td>
        <td>${formatNumber(position.premium, 1)}</td>
        <td>${formatNumber(position.market, 1)}</td>
        <td class="${pnlClass}">${formatMoney(metrics.pnl)}</td>
        <td>${formatNumber(metrics.delta, 2)}</td>
        <td>${formatNumber(metrics.gamma, 3)}</td>
        <td>${formatMoney(metrics.theta)}</td>
        <td>${formatMoney(metrics.vega)}</td>
        <td>${formatPercent(metrics.iv)}</td>
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
  const strikes = state.positions.map((item) => item.strike);
  const minStrike = Math.min(state.spot, ...strikes);
  const maxStrike = Math.max(state.spot, ...strikes);
  const xMin = Math.floor((minStrike - 1400) / 100) * 100;
  const xMax = Math.ceil((maxStrike + 1400) / 100) * 100;
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

  drawGrid(ctx, width, height, pad, xMin, xMax, yMin - yPad, yMax + yPad, scaleX, scaleY);
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
  const expiryValues = points.map((point) => point.expiry);
  const maxProfit = Math.max(...expiryValues);
  const maxLoss = Math.min(...expiryValues);
  const breakevens = findBreakevens(points);
  const atSpot = portfolioPayoff(state.spot);
  els.payoffStats.innerHTML = [
    ["現價到期損益", formatMoney(atSpot), atSpot >= 0 ? "positive" : "negative"],
    ["最大利潤", maxProfit > 9000000 ? "無上限" : formatMoney(maxProfit), "positive"],
    ["最大損失", maxLoss < -9000000 ? "無下限" : formatMoney(maxLoss), "negative"],
    ["兩平點", breakevens.length ? breakevens.map((item) => formatNumber(item, 0)).join(" / ") : "無", ""],
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
  state.indexChart.timeScale().fitContent();
  state.scoreChart.timeScale().fitContent();
  state.scoreDeltaChart.timeScale().fitContent();
  applySettlementTimeScalePadding();
  requestAnimationFrame(() => requestAnimationFrame(renderEventLane));
}

function resizeIndexChart() {
  if (!state.indexChart || !state.scoreChart || !state.scoreDeltaChart) return;
  const priceRect = els.priceChart.getBoundingClientRect();
  const scoreRect = els.scoreChart.getBoundingClientRect();
  const deltaRect = els.scoreDeltaChart.getBoundingClientRect();
  state.indexChart.applyOptions({ width: Math.floor(priceRect.width), height: Math.floor(priceRect.height) });
  state.scoreChart.applyOptions({ width: Math.floor(scoreRect.width), height: Math.floor(scoreRect.height) });
  state.scoreDeltaChart.applyOptions({ width: Math.floor(deltaRect.width), height: Math.floor(deltaRect.height) });
  renderEventLane();
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
  const hasFutureSettlement = settlementMarkerDates().some((date) => date > (els.endDateInput?.value || ""));
  const options = {
    rightOffset: hasFutureSettlement ? 6 : 2,
    barSpacing: 11,
  };
  state.indexChart.timeScale().applyOptions(options);
  state.scoreChart.timeScale().applyOptions(options);
  state.scoreDeltaChart.timeScale().applyOptions(options);
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
  const today = toDateInput(new Date());
  const startDate = els.startDateInput?.value || "";
  const endDate = els.endDateInput?.value || "";
  const dates = settlementDates();
  const occurredInRange = dates
    .filter((date) => date >= startDate)
    .filter((date) => !endDate || date <= endDate)
    .filter((date) => date <= today);
  const upcoming = dates
    .filter((date) => date >= startDate)
    .find((date) => date > today);
  return unique(upcoming ? [...occurredInRange, upcoming] : occurredInRange);
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
  const grouped = groupOptionChain(state.optionChain);
  els.optionChainBody.innerHTML = grouped.map((row) => {
    const callIv = row.call ? impliedVolatility(state.spot, row.strike, dteFromContract(row.contract), state.rate, row.call.close, "call") : null;
    const putIv = row.put ? impliedVolatility(state.spot, row.strike, dteFromContract(row.contract), state.rate, row.put.close, "put") : null;
    const skew = callIv && putIv ? putIv - callIv : null;
    const volume = (row.call?.volume || 0) + (row.put?.volume || 0);
    const oi = (row.call?.open_interest || 0) + (row.put?.open_interest || 0);
    return `
      <tr>
        <td>${formatNumber(row.strike, 0)}</td>
        <td>${row.call ? formatNumber(row.call.close, 1) : "-"}</td>
        <td>${callIv ? formatPercent(callIv) : "-"}</td>
        <td>${row.put ? formatNumber(row.put.close, 1) : "-"}</td>
        <td>${putIv ? formatPercent(putIv) : "-"}</td>
        <td class="${skew && skew > 0 ? "negative" : ""}">${skew === null ? "-" : formatPercent(skew)}</td>
        <td>${formatNumber(volume, 0)}</td>
        <td>${formatNumber(oi, 0)}</td>
        <td>
          ${row.call ? `<button type="button" class="ghost-action" data-action="add-chain" data-type="call" data-strike="${row.strike}" data-price="${row.call.close}" data-contract="${row.contract}">+C</button>` : ""}
          ${row.put ? `<button type="button" class="ghost-action" data-action="add-chain" data-type="put" data-strike="${row.strike}" data-price="${row.put.close}" data-contract="${row.contract}">+P</button>` : ""}
        </td>
      </tr>
    `;
  }).join("");
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
      <td>${row.spread}</td>
    </tr>
  `).join("");

  if (!rows.length) {
    els.strategyBody.innerHTML = `<tr><td colspan="9">沒有符合目前 3V 篩選條件的策略。</td></tr>`;
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
  els.riskSummary.innerHTML = [
    ["淨 Delta", formatNumber(totals.delta, 2)],
    ["Gamma", formatNumber(totals.gamma, 3)],
    ["Theta / 日", formatMoney(totals.theta)],
    ["Vega / 1% IV", formatMoney(totals.vega)],
    ["ATM IV", chainStats.atmIv ? formatPercent(chainStats.atmIv) : "-"],
    ["Put Skew", chainStats.skew ? formatPercent(chainStats.skew) : "-"],
  ].map(([label, value]) => `
    <div class="risk-chip">
      <span>${label}</span>
      <strong>${value}</strong>
    </div>
  `).join("");

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
    els.marketStatus.textContent = "正在讀取台股交易日曆...";
    const tradingDates = await fetchTradingDates(startDate, endDate);
    if (!tradingDates.length) throw new Error("指定區間沒有交易日。");

    els.marketStatus.textContent = "正在使用加權指數日資料端點抓取歷史 OHLC...";
    const historicalCandles = await fetchTaiexDailyCandles(startDate, endDate);
    const tradingDateSet = new Set(tradingDates);
    const candlesByDate = new Map(
      historicalCandles
        .filter((candle) => tradingDateSet.has(candle.time))
        .map((candle) => [candle.time, candle]),
    );

    const latestTradingDate = tradingDates[tradingDates.length - 1];
    els.marketStatus.textContent = `正在用五秒資料 resample 最新交易日 ${latestTradingDate}...`;
    const latestRows = await finmindData("TaiwanVariousIndicators5Seconds", { start_date: latestTradingDate });
    const latestCandle = aggregateTaiexRows(latestRows, latestTradingDate);
    if (latestCandle && tradingDateSet.has(latestCandle.time)) {
      candlesByDate.set(latestCandle.time, latestCandle);
    }

    const candles = tradingDates
      .map((date) => candlesByDate.get(date))
      .filter(Boolean)
      .slice(-90);
    const droppedCount = tradingDates.length - candles.length;

    if (!candles.length) throw new Error("查無 TAIEX 資料，可能是假日、權限或請求額度限制。");
    state.indexCandles = candles;
    state.spot = candles[candles.length - 1].close;
    const droppedText = droppedCount > 0 ? `，已略過 ${droppedCount} 個無日資料交易日` : "";
    const latestText = latestCandle ? `，最新交易日 ${latestTradingDate} 已用五秒資料重建` : `，最新交易日 ${latestTradingDate} 沒有五秒資料，保留日資料`;
    els.marketStatus.textContent = `已更新 ${candles.length} 根加權指數日線${droppedText}${latestText}。`;
    renderAll();
  } catch (error) {
    const fallbackText = options.auto ? "，目前保留示範日線" : "";
    els.marketStatus.textContent = `FinMind 抓取失敗：${error.message}${fallbackText}`;
  } finally {
    els.fetchIndexBtn.disabled = false;
    state.isFetchingIndex = false;
  }
}

async function fetchTradingDates(startDate, endDate) {
  const rows = await finmindData("TaiwanStockTradingDate", { start_date: startDate, end_date: endDate });
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
    { dataset: "TaiwanStockTotalReturnIndex", params: { data_id: "TAIEX", start_date: startDate, end_date: endDate } },
  ];

  for (const attempt of attempts) {
    try {
      const rows = await finmindData(attempt.dataset, attempt.params);
      const candles = rows
        .map(normalizeDailyCandle)
        .filter(Boolean)
        .sort((a, b) => a.time.localeCompare(b.time));
      if (candles.length) return candles;
    } catch (error) {
      // Try the next compatible daily endpoint shape.
    }
  }

  throw new Error("加權指數日資料端點查無資料。");
}

async function fetchOptionDaily() {
  els.fetchOptionsBtn.disabled = true;
  els.optionStatus.textContent = "正在向 FinMind 抓取 TaiwanOptionDaily...";
  try {
    const params = { start_date: els.optionDateInput.value };
    const rows = await finmindData("TaiwanOptionDaily", params);
    const contract = els.contractInput.value.trim();
    const filtered = rows
      .filter((row) => normalizeOptionId(row.option_id) === "TXO")
      .filter((row) => !contract || String(row.contract_date).includes(contract))
      .map(normalizeOptionRow)
      .filter((row) => row.close > 0)
      .sort((a, b) => a.strike - b.strike);
    if (!filtered.length) throw new Error("查無 TXO 選擇權資料，請確認日期、契約月份或權限。");
    state.optionChain = filtered;
    els.optionStatus.textContent = `已更新 ${filtered.length} 筆 TXO 選擇權日資料。`;
    renderAll();
  } catch (error) {
    els.optionStatus.textContent = `FinMind 抓取失敗：${error.message}`;
  } finally {
    els.fetchOptionsBtn.disabled = false;
  }
}

async function finmindData(dataset, params) {
  const url = new URL(FINMIND_URL);
  url.searchParams.set("dataset", dataset);
  Object.entries(params).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value);
  });
  if (state.finmindToken) url.searchParams.set("token", state.finmindToken);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = await response.json();
  if (payload.status && payload.status !== 200) {
    throw new Error(payload.msg || `FinMind status ${payload.status}`);
  }
  return payload.data || [];
}

async function loadEnvToken() {
  try {
    const response = await fetch(".env", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const env = parseEnv(await response.text());
    state.finmindToken = env.FINMIND_TOKEN || "";
    if (!state.finmindToken) {
      els.marketStatus.textContent = ".env 找不到 FINMIND_TOKEN，FinMind 請求會以未登入模式嘗試。";
    }
  } catch (error) {
    els.marketStatus.textContent = `讀取 .env 失敗：${error.message}，FinMind 請求會以未登入模式嘗試。`;
  }
}

function parseEnv(text) {
  return text.split(/\r?\n/).reduce((acc, line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return acc;
    const index = trimmed.indexOf("=");
    if (index === -1) return acc;
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim().replace(/^['"]|['"]$/g, "");
    acc[key] = value;
    return acc;
  }, {});
}

function aggregateTaiexRows(rows, date) {
  const values = rows
    .map((row) => ({ date: row.date, value: number(row.TAIEX, NaN) }))
    .filter((row) => Number.isFinite(row.value))
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));
  if (!values.length) return null;
  return {
    time: date,
    open: values[0].value,
    high: Math.max(...values.map((row) => row.value)),
    low: Math.min(...values.map((row) => row.value)),
    close: values[values.length - 1].value,
  };
}

function normalizeDailyCandle(row) {
  const time = normalizeDateValue(row.date || row.stock_date || row.Date);
  const open = firstNumber(row.open, row.Open, row.open_price, row.TAIEX);
  const high = firstNumber(row.max, row.high, row.High, row.high_price, row.TAIEX);
  const low = firstNumber(row.min, row.low, row.Low, row.low_price, row.TAIEX);
  const close = firstNumber(row.close, row.Close, row.close_price, row.price, row.TAIEX);
  if (!time || ![open, high, low, close].every(Number.isFinite)) return null;
  return { time, open, high, low, close };
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

function normalizeOptionId(value) {
  return String(value || "").toUpperCase().trim();
}

function normalizeCallPut(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("put") || text.includes("p") || text.includes("賣")) return "put";
  return "call";
}

function filteredPositions() {
  if (state.filter === "all") return state.positions;
  return state.positions.filter((position) => position.kind === state.filter);
}

function positionMetrics(position) {
  const sign = position.side === "long" ? 1 : -1;
  const t = dte(position.expiry);
  const iv = impliedVolatility(state.spot, position.strike, t, state.rate, position.market || position.premium, position.type) || 0.22;
  const greeks = blackScholesGreeks(state.spot, position.strike, t, state.rate, iv, position.type);
  const multiplier = sign * position.qty * POINT_VALUE;
  return {
    pnl: (position.market - position.premium) * multiplier,
    delta: greeks.delta * multiplier,
    gamma: greeks.gamma * multiplier,
    theta: greeks.theta * multiplier,
    vega: (greeks.vega / 100) * multiplier,
    iv,
  };
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

function portfolioPayoff(underlying) {
  return state.positions.reduce((sum, position) => {
    const intrinsic = position.type === "call"
      ? Math.max(0, underlying - position.strike)
      : Math.max(0, position.strike - underlying);
    const sign = position.side === "long" ? 1 : -1;
    return sum + (intrinsic - position.premium) * sign * position.qty * POINT_VALUE;
  }, 0);
}

function portfolioTheoryPnl(underlying) {
  return state.positions.reduce((sum, position) => {
    const t = dte(position.expiry);
    const iv = impliedVolatility(state.spot, position.strike, t, state.rate, position.market || position.premium, position.type) || 0.22;
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

function groupOptionChain(rows) {
  const byStrike = new Map();
  rows.forEach((row) => {
    if (!byStrike.has(row.strike)) byStrike.set(row.strike, { strike: row.strike, contract: row.contract });
    const target = byStrike.get(row.strike);
    target.contract = target.contract || row.contract;
    target[row.type] = row;
  });
  return Array.from(byStrike.values())
    .filter((row) => Math.abs(row.strike - state.spot) <= 1200)
    .sort((a, b) => a.strike - b.strike);
}

function optionChainStats() {
  const grouped = groupOptionChain(state.optionChain);
  if (!grouped.length) return {};
  const atm = grouped.reduce((best, row) => {
    return Math.abs(row.strike - state.spot) < Math.abs(best.strike - state.spot) ? row : best;
  }, grouped[0]);
  const t = dteFromContract(atm.contract);
  const callIv = atm.call ? impliedVolatility(state.spot, atm.strike, t, state.rate, atm.call.close, "call") : null;
  const putIv = atm.put ? impliedVolatility(state.spot, atm.strike, t, state.rate, atm.put.close, "put") : null;
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
  const ym = position.expiry ? position.expiry.slice(0, 7).replace("-", "") : "TXO";
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
  const date = new Date(`${value}T00:00:00`);
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
  const expiry = thirdWednesday(new Date().getFullYear(), new Date().getMonth() + 1);
  const contract = `${expiry.getFullYear()}${String(expiry.getMonth() + 1).padStart(2, "0")}`;
  const rows = [];
  for (let strike = 21400; strike <= 23800; strike += 200) {
    const distance = Math.abs(strike - state.spot);
    const base = Math.max(28, 240 - distance * 0.14);
    rows.push({
      option_id: "TXO",
      contract,
      strike,
      type: "call",
      close: Math.max(8, base + (state.spot - strike) * 0.22),
      volume: Math.round(3500 - distance * 1.2 + Math.random() * 240),
      open_interest: Math.round(6200 - distance * 1.5 + Math.random() * 700),
    });
    rows.push({
      option_id: "TXO",
      contract,
      strike,
      type: "put",
      close: Math.max(8, base + (strike - state.spot) * 0.24),
      volume: Math.round(3600 - distance * 1.1 + Math.random() * 260),
      open_interest: Math.round(6600 - distance * 1.3 + Math.random() * 760),
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
