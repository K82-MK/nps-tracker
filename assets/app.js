/* 국민연금 매수매도 추적기 — 정적 SPA (해시 라우팅)
 *   #/kr               국내 최신 영업일
 *   #/kr/2026-10-27    국내 특정일(없으면 직전 영업일)
 *   #/stock/005930     종목 상세(최근 60영업일)
 *   #/us               해외 13F 최신 분기
 *   #/us/2026Q2        해외 특정 분기
 */
(() => {
  "use strict";
  const $app = document.getElementById("app");
  const WEEK = "일월화수목금토";
  const PENSION_TIP = "‘연기금’은 KRX 투자자 분류로, 국민연금 외에 공무원·사학·군인연금과 각종 공제회 등이 함께 포함됩니다. 국민연금만의 매매가 아닙니다.";
  const DISCLAIMER = "정보 제공용이며 투자 권유가 아닙니다.";
  const NOTE_13F = "13F는 분기 말 기준 미국 상장 종목만 포함하며 최대 45일 지연 공개됨";
  const charts = [];
  const cache = new Map();
  let bust = "";

  if (window.ChartZoom) Chart.register(window.ChartZoom);

  // ---------------------------------------------------------------- utils
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nf = (v, d = 0) => (v == null || Number.isNaN(v) ? "-" : Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const signed = (v, d = 0) => (v == null ? "-" : (v > 0 ? "+" : "") + nf(v, d));
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "dn" : "flat");
  function won(v) { // 원 → 조/억
    if (v == null) return "-";
    const a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 1 }) + "조";
    if (a >= 1e8) return nf(Math.round(v / 1e8)) + "억";
    return nf(v) + "원";
  }
  function usd(v) {
    if (v == null) return "-";
    const a = Math.abs(v);
    if (a >= 1e9) return "$" + (v / 1e9).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "B";
    if (a >= 1e6) return "$" + (v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "M";
    return "$" + nf(v);
  }
  const wd = (d) => WEEK[new Date(d + "T00:00:00+09:00").getDay()];
  const mmdd = (d) => d.slice(5);

  async function getJSON(path) {
    if (cache.has(path)) return cache.get(path);
    const p = fetch(`${path}${bust ? `?v=${bust}` : ""}`, { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new Error(`${path} ${r.status}`);
      return r.json();
    });
    cache.set(path, p);
    p.catch(() => cache.delete(path));
    return p;
  }

  function destroyCharts() { while (charts.length) charts.pop().destroy(); }

  const icon = {
    first: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 7l-5 5 5 5M18 7l-5 5 5 5"/></svg>',
    prev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>',
    next: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
    last: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 7l5 5-5 5M6 7l5 5-5 5"/></svg>',
    cal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="16" rx="2"/><path d="M16 3v4M8 3v4M4 11h16"/></svg>',
    doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2zM9 13h6M9 17h6"/></svg>',
  };
  const infoTip = (text = PENSION_TIP) =>
    `<span class="info"><button type="button" aria-label="연기금 설명" title="${esc(text)}">i</button><span class="tip" role="tooltip">${esc(text)}</span></span>`;

  document.addEventListener("click", (e) => {
    const b = e.target.closest(".info button");
    document.querySelectorAll(".info.open").forEach((x) => { if (!b || x !== b.parentElement) x.classList.remove("open"); });
    if (b) { b.parentElement.classList.toggle("open"); e.stopPropagation(); }
  });

  function footer(meta, extra = "", pension = true) {
    return `<footer class="foot">
      <p><b>출처</b> ${esc(meta?.source || "KRX, DART")}</p>
      <p><b>기준일</b> ${esc(meta?.as_of || "-")}${meta?.generated ? ` · 갱신 ${esc(meta.generated.replace("T", " ").slice(0, 16))}` : ""}</p>
      ${extra}
      ${pension ? `<p>${esc(meta?.pension_note || PENSION_TIP)}</p>` : ""}
      <p>${esc(DISCLAIMER)}</p></footer>`;
  }

  function setTab(t) {
    document.querySelectorAll(".tabs a").forEach((a) => (a.dataset.tab === t ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  }

  // ---------------------------------------------------------------- router
  let lastView = "";
  async function route() {
    destroyCharts();
    const viewKey = (location.hash || "#/kr").split("/")[1];
    if (viewKey !== "kr" || lastView !== "kr") window.scrollTo(0, 0);
    lastView = viewKey;
    const [, view = "kr", arg] = (location.hash || "#/kr").split("/");
    try {
      const idx = await getJSON("data/index.json").catch(() => ({ dates: [] }));
      bust = (idx.updated || "").replace(/\D/g, "");
      if (view === "us") { setTab("us"); await renderUS(arg); }
      else if (view === "stock") { setTab("kr"); await renderStock(arg, idx); }
      else { setTab("kr"); await renderKR(arg, idx); }
    } catch (err) {
      console.error(err);
      $app.innerHTML = `<div class="empty"><h2>데이터를 불러오지 못했어요</h2><p>${esc(err.message)}<br>잠시 후 새로고침하세요.</p></div>`;
    }
  }
  window.addEventListener("hashchange", route);

  // ---------------------------------------------------------------- 국내
  const krState = { side: "buy", sortKey: null, sortDir: 1 };

  function resolveDate(arg, dates) {
    if (!arg) return { date: dates[dates.length - 1], note: "" };
    if (dates.includes(arg)) return { date: arg, note: "" };
    const prev = [...dates].reverse().find((d) => d < arg);
    if (prev) return { date: prev, note: `${arg}(${wd(arg)})는 휴장일이거나 데이터가 없어 직전 영업일 ${prev}(${wd(prev)}) 데이터를 표시합니다.` };
    return { date: dates[0], note: `${arg} 이전 데이터가 없어 가장 오래된 ${dates[0]} 데이터를 표시합니다.` };
  }

  async function renderKR(arg, idx) {
    const dates = idx.dates || [];
    if (!dates.length) {
      $app.innerHTML = `<div class="empty"><h2>아직 수집된 데이터가 없어요</h2>
        <p>GitHub 저장소 Actions 탭에서 ‘국내 연기금 수집’을 백필(120일)로 한 번 실행하면 여기에 표시됩니다.</p></div>`;
      return;
    }
    const { date, note } = resolveDate(arg, dates);
    const [day, market, dart] = await Promise.all([
      getJSON(`data/daily/${date}.json`),
      getJSON("data/market.json"),
      getJSON("data/dart/nps_5pct.json").catch(() => ({ by_date: [] })),
    ]);
    const i = dates.indexOf(date);
    const go = (d) => (location.hash = `#/kr/${d}`);
    const back5 = dates[Math.max(0, i - 5)];
    const pfK = day.flows.KOSPI.find((x) => x.investor === "연기금")?.net;
    const pfQ = day.flows.KOSDAQ.find((x) => x.investor === "연기금")?.net;

    $app.innerHTML = `
      <section class="card" aria-label="날짜와 지수">
        <div class="datenav">
          <button data-go="${back5}" ${i === 0 ? "disabled" : ""} aria-label="5영업일 전" title="5영업일 전">${icon.first}</button>
          <button data-go="${dates[i - 1] || ""}" ${i === 0 ? "disabled" : ""} aria-label="이전 영업일" title="이전 영업일">${icon.prev}</button>
          <label class="datebox" title="달력에서 날짜 선택">
            <span class="d">${date}</span><span class="w">(${day.weekday})</span>${icon.cal}
            <input type="date" id="pick" value="${date}" min="${dates[0]}" max="${dates[dates.length - 1]}" aria-label="날짜 선택">
          </label>
          <button data-go="${dates[i + 1] || ""}" ${i === dates.length - 1 ? "disabled" : ""} aria-label="다음 영업일" title="다음 영업일">${icon.next}</button>
          <button data-go="${dates[dates.length - 1]}" ${i === dates.length - 1 ? "disabled" : ""} aria-label="최신 영업일" title="최신 영업일">${icon.last}</button>
        </div>
        ${note ? `<p class="notice">${esc(note)}</p>` : ""}
        <div class="strip">
          ${["KOSPI", "KOSDAQ"].map((m) => {
            const x = day.index[m]; const pf = m === "KOSPI" ? pfK : pfQ;
            return `<div><div class="k">${m}</div>
              <div class="v num">${nf(x.close, 2)} <span class="c ${cls(x.chg)}">${signed(x.chg, 2)} (${signed(x.chg_pct, 2)}%)</span></div>
              <div class="pf">연기금 ${infoTip()} <b class="${cls(pf)} num">${signed(pf)}억</b></div></div>`;
          }).join("")}
        </div>
      </section>

      <section class="card" aria-label="지수와 연기금 순매수 추이">
        <div class="chartwrap"><canvas id="mainChart" aria-label="KOSPI·KOSDAQ 지수와 일별 연기금 순매수 막대 차트" role="img"></canvas></div>
        <div class="legend">
          <span><i class="ln" style="background:var(--kospi)"></i>KOSPI(우)</span>
          <span><i class="ln" style="background:var(--kosdaq)"></i>KOSDAQ(좌)</span>
          <span><i style="background:var(--buy-bar)"></i>순매수 KOSPI</span>
          <span><i style="background:var(--buy-bar-2)"></i>순매수 KOSDAQ</span>
          <span><i style="background:var(--sell-bar)"></i>순매도 KOSPI</span>
          <span><i style="background:var(--sell-bar-2)"></i>순매도 KOSDAQ</span>
        </div>
        <div class="chart-tools"><span>확대·이동 가능, 막대를 누르면 그날로</span>
          <span class="zoom"><button data-z="in" aria-label="확대">＋</button><button data-z="out" aria-label="축소">－</button><button data-z="reset">전체</button></span></div>
      </section>

      ${dartBlock(date, dart)}

      <section class="card" aria-label="연기금 종목별 매매">
        <div class="toggle" role="group" aria-label="순매수/순매도 선택">
          <span class="when">${mmdd(date)} (${day.weekday})</span>
          <button data-side="buy" aria-pressed="${krState.side === "buy"}">순매수 <span class="n num">(${nf(day.totals.buy, 0)}억)</span></button>
          <button data-side="sell" aria-pressed="${krState.side === "sell"}">순매도 <span class="n num">(${nf(day.totals.sell, 0)}억)</span></button>
        </div>
        <p class="hint">종목을 누르면 최근 60영업일 상세로 이동합니다. 금액 단위 억원, 연속 5일 이상 강조. 합계는 해당 방향 전 종목 기준.</p>
        <div class="tbl-scroll"><table class="t" id="kt"></table></div>
      </section>

      <details class="card more">
        <summary>투자자별 순매수 (억원)</summary>
        <div class="flows num">
          <div class="h">투자자</div><div class="h r">KOSPI</div><div class="h r">KOSDAQ</div>
          ${day.flows.KOSPI.map((f) => {
            const q = day.flows.KOSDAQ.find((x) => x.investor === f.investor)?.net;
            const pf = f.investor === "연기금" ? " pf" : "";
            return `<div class="${pf}">${esc(f.investor)}${f.investor === "연기금" ? " " + infoTip() : ""}</div><div class="r ${cls(f.net)}${pf}">${signed(f.net)}</div><div class="r ${cls(q)}${pf}">${signed(q)}</div>`;
          }).join("")}
        </div>
      </details>
      ${footer(day.meta)}`;

    $app.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => b.dataset.go && go(b.dataset.go)));
    const pick = $app.querySelector("#pick");
    pick.addEventListener("change", () => pick.value && go(pick.value));
    $app.querySelector(".datebox").addEventListener("click", () => { try { pick.showPicker(); } catch (_) { /* 미지원 브라우저는 input 클릭으로 열림 */ } });
    $app.querySelectorAll("[data-side]").forEach((b) => b.addEventListener("click", () => {
      krState.side = b.dataset.side; krState.sortKey = null;
      $app.querySelectorAll("[data-side]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      drawKrTable(day);
    }));
    drawKrTable(day);
    drawMainChart(market, date, go);
  }

  function dartBlock(date, dart) {
    const groups = dart.by_date || [];
    const today = groups.find((g) => g.date === date);
    const recent = groups.slice(0, 6);
    if (!groups.length) return "";
    const lead = today
      ? `<a class="lead" href="${esc(today.items[0].url)}" target="_blank" rel="noopener">${icon.doc}${+date.slice(5, 7)}월 ${+date.slice(8)}일 접수 국민연금 대량보유 공시 ${today.items.length}건 보기</a>`
      : `<a class="lead" href="${esc(recent[0].items[0].url)}" target="_blank" rel="noopener">${icon.doc}${+recent[0].date.slice(5, 7)}월 ${+recent[0].date.slice(8)}일 접수 국민연금 대량보유 공시 보기</a>`;
    return `<details class="card dart more"><summary>국민연금 대량보유(5%) 공시</summary>${lead}
      <ul>${recent.flatMap((g) => g.items.map((x) =>
        `<li><span class="dt num">${mmdd(g.date)}</span><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.corp)} · ${esc(x.title)}</a></li>`)).join("")}</ul></details>`;
  }

  const KR_COLS = [
    { k: "rank", label: "순위", cls: "c-rank stk", sort: true },
    { k: "name", label: "종목명", cls: "c-name stk", sort: false },
    { k: "net", label: null, sort: true },
    { k: "close", label: "종가", sort: true },
    { k: "streak", label: null, sort: true },
    { k: "revenue", label: "작년매출", sort: true },
    { k: "mcap", label: "시가총액", sort: true },
  ];

  function drawKrTable(day) {
    const side = krState.side;
    const rows = [...(day[side] || [])];
    const { sortKey, sortDir } = krState;
    if (sortKey) rows.sort((a, b) => {
      const va = sortKey === "net" ? Math.abs(a.net) : a[sortKey], vb = sortKey === "net" ? Math.abs(b.net) : b[sortKey];
      return ((va ?? -Infinity) > (vb ?? -Infinity) ? 1 : (va ?? -Infinity) < (vb ?? -Infinity) ? -1 : 0) * sortDir;
    });
    const label = (c) => c.label ?? (c.k === "net" ? (side === "buy" ? "매수액(억)" : "매도액(억)") : side === "buy" ? "연속매수" : "연속매도");
    const ry = rows.find((r) => r.revenue_year)?.revenue_year;
    const t = document.getElementById("kt");
    t.innerHTML = `<thead><tr>${KR_COLS.map((c) => `<th scope="col" class="${c.cls || ""} ${c.sort ? "sortable" : ""}" data-k="${c.k}"
        ${sortKey === c.k ? `aria-sort="${sortDir > 0 ? "ascending" : "descending"}"` : ""}
        ${c.k === "revenue" && ry ? `title="${ry}년 사업보고서 기준"` : ""}>${label(c)}</th>`).join("")}</tr></thead>
      <tbody>${rows.length ? rows.map((r) => `<tr data-code="${r.code}" tabindex="0">
        <td class="c-rank stk num">${r.rank}</td>
        <td class="c-name stk"><div class="nm">${esc(r.name)}</div><div class="cd num">${r.code}<span class="mk ${r.market}">${r.market}</span></div></td>
        <td class="amt num ${side === "buy" ? "up" : "dn"}">${nf(r.net, 2)}</td>
        <td class="num">${nf(r.close)}</td>
        <td>${r.streak >= 2 ? `<span class="streak ${r.streak >= 5 ? "hot" : ""} ${side} num">${r.streak}일${r.streak_capped ? "+" : ""}</span>` : ""}</td>
        <td class="num">${won(r.revenue)}</td>
        <td class="num">${won(r.mcap)}</td></tr>`).join("")
        : `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">해당 종목이 없습니다</td></tr>`}</tbody>`;
    t.querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.k;
      if (krState.sortKey === k) krState.sortDir *= -1; else { krState.sortKey = k; krState.sortDir = k === "rank" ? 1 : -1; }
      drawKrTable(day);
    }));
    t.querySelectorAll("tbody tr[data-code]").forEach((tr) => {
      const open = () => (location.hash = `#/stock/${tr.dataset.code}`);
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => e.key === "Enter" && open());
    });
  }

  const selectedLine = {
    id: "selectedLine",
    afterDatasetsDraw(chart, _a, opts) {
      if (opts.index == null || opts.index < 0) return;
      const x = chart.scales.x.getPixelForValue(opts.index);
      if (x < chart.chartArea.left || x > chart.chartArea.right) return;
      const { ctx, chartArea: { top, bottom } } = chart;
      ctx.save(); ctx.strokeStyle = opts.color; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
    },
  };

  function drawMainChart(m, date, go) {
    const n = m.dates.length;
    const pos = (a) => a.map((v) => (v > 0 ? v : 0)), neg = (a) => a.map((v) => (v < 0 ? v : 0));
    const maxAbs = Math.max(1, ...m.pf_kospi.map((v, i) => Math.abs(v || 0) + Math.abs(m.pf_kosdaq[i] || 0)));
    const sel = m.dates.indexOf(date);
    const grid = css("--line"), tick = css("--muted");
    const ctx = document.getElementById("mainChart");
    const bar = (label, data, color) => ({ type: "bar", label, data, backgroundColor: color, yAxisID: "yb", stack: "pf", order: 3, barPercentage: 0.9, categoryPercentage: 0.8 });
    const line = (label, data, color, axis) => ({ type: "line", label, data, borderColor: color, backgroundColor: color, yAxisID: axis, borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.2, order: 1 });
    const chart = new Chart(ctx, {
      data: {
        labels: m.dates.map((d) => d.slice(2).replace(/-/g, ".")),
        datasets: [
          line("KOSPI", m.kospi, css("--kospi"), "y1"),
          line("KOSDAQ", m.kosdaq, css("--kosdaq"), "y"),
          bar("순매수 KOSPI", pos(m.pf_kospi), css("--buy-bar")),
          bar("순매수 KOSDAQ", pos(m.pf_kosdaq), css("--buy-bar-2")),
          bar("순매도 KOSPI", neg(m.pf_kospi), css("--sell-bar")),
          bar("순매도 KOSDAQ", neg(m.pf_kosdaq), css("--sell-bar-2")),
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { color: tick, maxRotation: 0, autoSkipPadding: 18, font: { size: 11 } } },
          y: { position: "left", grid: { color: grid }, ticks: { color: css("--kosdaq"), font: { size: 11 }, maxTicksLimit: 6 } },
          y1: { position: "right", grid: { display: false }, ticks: { color: tick, font: { size: 11 }, maxTicksLimit: 6 } },
          yb: { stacked: true, display: false, min: -maxAbs * 1.05, max: maxAbs * 2.6 },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            filter: (it) => it.raw !== 0,
            callbacks: {
              title: (it) => { const d = m.dates[it[0].dataIndex]; return `${d} (${wd(d)})`; },
              label: (it) => it.dataset.type === "line" ? ` ${it.dataset.label} ${nf(it.raw, 2)}` : ` 연기금 ${it.dataset.label} ${signed(it.raw)}억`,
            },
          },
          zoom: {
            limits: { x: { minRange: 10 } },
            pan: { enabled: true, mode: "x" },
            zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" },
          },
          selectedLine: { index: sel, color: css("--ink") },
        },
        onClick: (_e, els) => { if (els.length) go(m.dates[els[0].index]); },
      },
      plugins: [selectedLine],
    });
    charts.push(chart);
    // 처음엔 선택일을 포함한 최근 60영업일
    const span = 60;
    if (n > span) {
      const end = Math.min(n - 1, Math.max(sel + 10, span - 1));
      chart.zoomScale("x", { min: Math.max(0, end - span + 1), max: end }, "none");
    }
    $app.querySelectorAll("[data-z]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.z === "in") chart.zoom(1.4); else if (b.dataset.z === "out") chart.zoom(0.7); else chart.resetZoom();
    }));
  }

  // ---------------------------------------------------------------- 종목 상세
  async function renderStock(code, idx) {
    const s = await getJSON(`data/stocks/${code}.json`).catch(() => null);
    if (!s) {
      $app.innerHTML = `<a class="back" href="#/kr">${icon.prev}국내로</a><div class="empty"><h2>${esc(code)} 상세 데이터가 없어요</h2><p>최근 60영업일 동안 연기금 순매수·순매도 상위에 오른 종목만 상세를 제공합니다.</p></div>`;
      return;
    }
    const h = s.history;
    const last = [...h].reverse().find((x) => x.close != null) || {};
    const streak = s.streak > 0 ? `<span class="up">${s.streak}일 연속 순매수</span>` : s.streak < 0 ? `<span class="dn">${-s.streak}일 연속 순매도</span>` : "없음";
    $app.innerHTML = `
      <a class="back" href="javascript:history.length>1?history.back():location.hash='#/kr'">${icon.prev}뒤로</a>
      <section class="card">
        <div class="st-head"><h1>${esc(s.name)}</h1><div class="cd num">${s.code}<span class="mk ${s.market}">${s.market}</span></div></div>
        <div class="kv num">
          <div><div class="k">종가 (${esc(last.date || "-")})</div><div class="v">${nf(last.close)}원</div></div>
          <div><div class="k">연기금 연속 ${infoTip()}</div><div class="v">${streak}${s.streak_capped ? "+" : ""}</div></div>
          <div><div class="k">${h.length}영업일 누적 순매수</div><div class="v ${cls(s.sum_net)}">${signed(s.sum_net, 1)}억</div></div>
          <div><div class="k">작년매출${s.revenue_year ? ` (${s.revenue_year})` : ""}</div><div class="v">${won(s.revenue)}</div></div>
          <div><div class="k">시가총액</div><div class="v">${won(last.mcap)}</div></div>
          <div><div class="k">기간</div><div class="v" style="font-size:13.5px">${h[0].date} ~ ${h[h.length - 1].date}</div></div>
        </div>
      </section>
      <section class="card">
        <h2 class="sec-h">최근 ${h.length}영업일 연기금 매매와 주가</h2>
        <div class="chartwrap"><canvas id="stChart" role="img" aria-label="연기금 일별 순매수 막대와 종가 선 차트"></canvas></div>
        <div class="legend"><span><i style="background:var(--buy-bar)"></i>순매수</span><span><i style="background:var(--sell-bar)"></i>순매도</span><span><i class="ln" style="background:var(--kospi)"></i>종가(우)</span></div>
      </section>
      <section class="card">
        <h2 class="sec-h">일별 내역</h2>
        <div class="tbl-scroll"><table class="t" style="min-width:420px"><thead><tr>
          <th class="c-rank stk" style="width:84px;min-width:84px">날짜</th><th>순매수(억)</th><th>매수(억)</th><th>매도(억)</th><th>종가</th></tr></thead>
          <tbody id="hist">${[...h].reverse().map((x, i) => `<tr style="cursor:default${i >= 20 ? ";display:none" : ""}"><td class="c-rank stk num" style="width:84px">${mmdd(x.date)} ${wd(x.date)}</td>
            <td class="num amt ${cls(x.net)}">${signed(x.net, 2)}</td><td class="num">${nf(x.buy, 2)}</td><td class="num">${nf(x.sell, 2)}</td><td class="num">${nf(x.close)}</td></tr>`).join("")}</tbody></table></div>
        ${h.length > 20 ? `<button class="rowmore" type="button">전체 ${h.length}일 보기</button>` : ""}
      </section>
      ${footer(s.meta)}`;
    $app.querySelector(".rowmore")?.addEventListener("click", (e) => {
      $app.querySelectorAll("#hist tr").forEach((tr) => (tr.style.display = ""));
      e.target.remove();
    });
    const c = new Chart(document.getElementById("stChart"), {
      data: {
        labels: h.map((x) => x.date.slice(5).replace("-", ".")),
        datasets: [
          { type: "line", label: "종가", data: h.map((x) => x.close), yAxisID: "yp", borderColor: css("--kospi"), borderWidth: 2, pointRadius: 0, tension: 0.2, order: 1, spanGaps: true },
          { type: "bar", label: "연기금 순매수", data: h.map((x) => x.net), yAxisID: "yn", order: 2,
            backgroundColor: h.map((x) => (x.net >= 0 ? css("--buy-bar") : css("--sell-bar"))) },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { grid: { display: false }, ticks: { color: css("--muted"), maxRotation: 0, autoSkipPadding: 16, font: { size: 11 } } },
          yn: { position: "left", grid: { color: css("--line") }, ticks: { color: css("--muted"), font: { size: 11 }, callback: (v) => nf(v) }, title: { display: true, text: "억원", color: css("--muted"), font: { size: 11 } } },
          yp: { position: "right", grid: { display: false }, ticks: { color: css("--muted"), font: { size: 11 }, callback: (v) => nf(v) } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (it) => it.dataset.type === "line" ? ` 종가 ${nf(it.raw)}원` : ` 연기금 ${signed(it.raw, 2)}억` } },
          zoom: { pan: { enabled: true, mode: "x" }, zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" } },
        },
      },
    });
    charts.push(c);
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- 해외 13F
  const usState = { view: "top", all: false };
  const SECTOR_COLORS = ["#123B33", "#1D5BD6", "#D7263D", "#E09F1F", "#0E8A6E", "#7A5AF8", "#5B6B82", "#C2410C", "#0891B2", "#A21CAF", "#94A3B8"];

  async function renderUS(arg) {
    const idx = await getJSON("data/13f/index.json").catch(() => null);
    if (!idx || !idx.quarters?.length) {
      $app.innerHTML = `<div class="empty"><h2>아직 13F 데이터가 없어요</h2><p>GitHub Actions에서 ‘해외 13F 수집’을 한 번 실행하면 표시됩니다.</p><p class="banner">${NOTE_13F}</p></div>`;
      return;
    }
    const qsel = idx.quarters.find((q) => q.quarter === arg) ? arg : idx.quarters[0].quarter;
    const q = await getJSON(`data/13f/${qsel}.json`);
    const s = q.summary;
    $app.innerHTML = `
      <section class="card">
        <div class="q-bar">
          <label class="sr" for="qpick">분기 선택</label>
          <select id="qpick">${idx.quarters.map((x) => `<option value="${x.quarter}" ${x.quarter === qsel ? "selected" : ""}>${x.quarter} (${x.report_date})</option>`).join("")}</select>
          <span class="fd">보고서 제출일 <b class="num">${q.filing_date}</b>${q.url ? ` · <a href="${esc(q.url)}" target="_blank" rel="noopener">원문</a>` : ""}</span>
        </div>
        <p class="banner">${NOTE_13F}</p>
        <div class="kpis num">
          <div><div class="k">총 보유액</div><div class="v">${usd(s.total_usd)}</div><div class="s">${s.total_krw ? `약 ${won(s.total_krw)} (환율 ${nf(s.usd_krw, 1)}원, ${esc(s.fx_date)})` : "원화 환산 없음"}</div></div>
          <div><div class="k">보유 종목 수</div><div class="v">${nf(s.count)}</div><div class="s">전분기 ${nf(s.prev_count)} (${signed(s.count - s.prev_count)})</div></div>
          <div><div class="k">신규매수</div><div class="v up">${nf(s.new)}</div><div class="s">비중확대 ${nf(s.increased)}</div></div>
          <div><div class="k">전량매도</div><div class="v dn">${nf(s.exited)}</div><div class="s">비중축소 ${nf(s.decreased)} · 유지 ${nf(s.kept)}</div></div>
        </div>
      </section>
      <section class="card">
        <h2 class="sec-h">섹터별 비중</h2>
        <div class="donut-wrap"><canvas id="donut" width="150" height="150" role="img" aria-label="섹터별 비중 도넛 차트"></canvas>
          <ul class="sec-list num">${q.sectors.map((x, i) => `<li><span><i style="background:${SECTOR_COLORS[i % SECTOR_COLORS.length]}"></i>${esc(x.sector)}</span><b>${nf(x.weight, 1)}%</b></li>`).join("")}</ul></div>
        <p class="hint">섹터는 SEC 업종(SIC) 코드를 묶은 근사 분류입니다.</p>
      </section>
      <section class="card">
        <div class="seg" role="group" aria-label="표 선택">
          <button data-v="top" aria-pressed="${usState.view === "top"}">상위 보유</button>
          <button data-v="new" aria-pressed="${usState.view === "new"}">신규매수 TOP 10</button>
          <button data-v="exit" aria-pressed="${usState.view === "exit"}">전량매도 TOP 10</button>
          <button data-v="up" aria-pressed="${usState.view === "up"}">비중확대</button>
          <button data-v="down" aria-pressed="${usState.view === "down"}">비중축소</button>
        </div>
        <div id="ustbl"></div>
      </section>
      ${footer(q.meta, `<p><b>안내</b> ${NOTE_13F}</p>`, false)}`;

    $app.querySelector("#qpick").addEventListener("change", (e) => (location.hash = `#/us/${e.target.value}`));
    $app.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => {
      usState.view = b.dataset.v; usState.all = false;
      $app.querySelectorAll("[data-v]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      drawUsTable(q);
    }));
    drawUsTable(q);
    charts.push(new Chart(document.getElementById("donut"), {
      type: "doughnut",
      data: { labels: q.sectors.map((x) => x.sector), datasets: [{ data: q.sectors.map((x) => x.weight), backgroundColor: q.sectors.map((_, i) => SECTOR_COLORS[i % SECTOR_COLORS.length]), borderColor: css("--card"), borderWidth: 2 }] },
      options: { responsive: false, cutout: "62%", animation: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => ` ${it.label} ${nf(it.raw, 1)}%` } } } },
    }));
  }

  function drawUsTable(q) {
    const v = usState.view;
    let rows, exited = false;
    if (v === "new") rows = q.holdings.filter((h) => h.status === "신규매수").slice(0, 10);
    else if (v === "exit") { rows = q.exited.slice(0, 10); exited = true; }
    else if (v === "up") rows = q.holdings.filter((h) => h.status === "비중확대");
    else if (v === "down") rows = q.holdings.filter((h) => h.status === "비중축소");
    else rows = q.holdings;
    const limit = usState.all || v === "new" || v === "exit" ? rows.length : 30;
    const shown = rows.slice(0, limit);
    const el = document.getElementById("ustbl");
    el.innerHTML = `<div class="tbl-scroll"><table class="t us"><thead><tr>
        <th class="c-rank stk">#</th><th class="c-name stk">티커 · 종목명</th>
        <th>${exited ? "직전 보유주식" : "보유주식수"}</th><th>${exited ? "직전 평가액" : "평가액"}</th><th>${exited ? "직전 비중" : "비중"}</th>
        <th>증감(주)</th><th>증감(%)</th><th>구분</th></tr></thead>
      <tbody>${shown.map((h, i) => `<tr style="cursor:default">
        <td class="c-rank stk num">${i + 1}</td>
        <td class="c-name stk"><div class="nm"><span class="tk">${esc(h.ticker || "—")}</span></div><div class="cd">${esc(h.name)}</div></td>
        <td class="num">${nf(exited ? h.prev_shares : h.shares)}</td>
        <td class="num">${usd(exited ? h.prev_value : h.value)}</td>
        <td class="num">${nf(exited ? h.prev_weight : h.weight, 2)}%</td>
        <td class="num ${cls(h.chg_shares)}">${signed(h.chg_shares)}</td>
        <td class="num ${cls(h.chg_shares)}">${h.chg_pct == null ? (h.status === "신규매수" ? "신규" : "-") : signed(h.chg_pct, 1) + "%"}</td>
        <td><span class="pill ${h.status}">${h.status}</span></td></tr>`).join("") || `<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">해당 종목이 없습니다</td></tr>`}</tbody></table></div>
      ${rows.length > limit ? `<button class="showall" type="button">전체 ${nf(rows.length)}종목 보기</button>` : ""}`;
    el.querySelector(".showall")?.addEventListener("click", () => { usState.all = true; drawUsTable(q); });
  }

  route();
})();
