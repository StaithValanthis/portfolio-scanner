import { html, render, useEffect, useRef, useState } from 'https://unpkg.com/htm/preact/standalone.module.js';

/* ------------ helpers ------------ */
const fmt = {
  n2: x => (x ?? 0).toFixed(2),
  pct: x => `${((x ?? 0) * 100).toFixed(2)}%`,
  dt: s => (s ? new Date(s).toLocaleString() : ''),
};
const GET  = (url) => fetch(url).then(r => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); const ct=r.headers.get('content-type')||''; return ct.includes('json')?r.json():r.text(); });
const POST = (url, body) => fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) }).then(r=>r.json());

/* ------------ small UI atoms ------------ */
const Chip = ({children}) => html`<span class="px-2 py-0.5 rounded-full text-xs bg-neutral-800">${children}</span>`;
const Badge = ({children}) => {
  const color = children==='BUY' ? 'bg-green-600' : children==='SELL' ? 'bg-red-600' : children==='HOLD' ? 'bg-yellow-600' : 'bg-neutral-700';
  return html`<span class=${`px-2 py-1 rounded text-xs ${color}`}>${children}</span>`;
};
const Stat = ({label, value, sub}) => html`
  <div class="bg-neutral-900 rounded-2xl p-4">
    <div class="text-sm text-neutral-400">${label}</div>
    <div class="text-2xl font-semibold">${value}</div>
    ${sub && html`<div class="text-xs text-neutral-400 mt-1">${sub}</div>`}
  </div>`;

/* ------------ charts ------------ */
function EquityChart({ data }) {
  const ref = useRef();
  useEffect(() => {
    if (!data || !data.dates || data.dates.length===0) return;
    const ctx = ref.current.getContext('2d');
    if (ref.current._chart) ref.current._chart.destroy();
    ref.current._chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.dates,
        datasets: [
          { label: 'Equity', data: data.equity, borderWidth: 1, fill: false, tension: 0.1 },
          { label: `Benchmark ${data.bench?.label||''}`, data: data.bench?.equity||[], borderWidth: 1, fill: false, tension: 0.1 },
          { label: 'Drawdown', data: data.drawdown, borderWidth: 1, fill: false, tension: 0.1, yAxisID: 'y1' },
        ],
      },
      options: {
        animation: false,
        scales: { y: { beginAtZero: false }, y1: { position: 'right', beginAtZero: true, suggestedMin: -1, suggestedMax: 0 } },
        plugins: { legend: { display: true } },
      },
    });
  }, [data]);
  return html`<canvas class="w-full h-64" ref=${ref}></canvas>`;
}

function PieChart({ data, onSliceClick }) {
  const ref = useRef();
  useEffect(() => {
    if (!data || !data.items || data.items.length===0) return;
    const labels = data.items.map(i=>i.label);
    const weights = data.items.map(i=>+(i.weight*100).toFixed(2));
    const ctx = ref.current.getContext('2d');
    if (ref.current._chart) ref.current._chart.destroy();
    ref.current._chart = new Chart(ctx, {
      type: 'doughnut',
      data: { labels, datasets: [{ data: weights }] },
      options: {
        onClick: (evt, els) => { if (!els || els.length===0) return; const idx = els[0].index; onSliceClick && onSliceClick(labels[idx]); },
        plugins: { tooltip: { callbacks: { label: c => `${c.label}: ${c.parsed}%` } }, legend: { position: 'right' } },
      },
    });
  }, [data]);
  return html`<canvas class="w-full h-64" ref=${ref}></canvas>`;
}

/* ------------ modal for news ------------ */
function NewsModal({ ticker, onClose }) {
  const [items, setItems] = useState(null);
  useEffect(() => { (async () => {
    try { const res = await GET(`/api/news?ticker=${encodeURIComponent(ticker)}&days=7&limit=20`); setItems(res.items || []); }
    catch { setItems([]); }
  })(); }, [ticker]);
  return html`
  <div class="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
    <div class="bg-neutral-900 rounded-2xl p-4 w-full max-w-3xl max-h-[80vh] overflow-auto">
      <div class="flex items-center justify-between mb-2">
        <div class="font-semibold">News — ${ticker}</div>
        <button class="px-2 py-1 bg-neutral-800 rounded text-sm" onClick=${onClose}>Close</button>
      </div>
      ${!items && html`<div class="text-sm text-neutral-400">Loading…</div>`}
      ${items && items.length===0 && html`<div class="text-sm text-neutral-400">No recent articles.</div>`}
      ${items && items.length>0 && html`<ul class="space-y-2">
        ${items.map(it => html`<li class="bg-neutral-800 rounded p-2">
          <a class="underline" href=${it.link} target="_blank" rel="noreferrer">${it.title}</a>
          <div class="text-xs text-neutral-400 mt-1 flex items-center gap-2">
            <span>${fmt.dt(it.published)}</span>
            ${it.sentiment != null && html`<span class="${it.sentiment>0.1?'text-green-400':(it.sentiment<-0.1?'text-red-400':'text-neutral-400')}">sent ${(it.sentiment>=0?'+':'')}${it.sentiment.toFixed(2)}</span>`}
          </div>
        </li>`)}
      </ul>`}
    </div>
  </div>`;
}

/* ------------ main app ------------ */
function App() {
  const [cfg, setCfg] = useState(null);
  const [scanRows, setScanRows] = useState([]);
  const [portfolio, setPortfolio] = useState(null);
  const [pie, setPie] = useState(null);
  const [breakdownMode, setBreakdownMode] = useState('ticker');
  const [filterTicker, setFilterTicker] = useState('');
  const [bt, setBt] = useState(null);
  const [curve, setCurve] = useState(null);
  const [ann, setAnn] = useState({});
  const [upcoming, setUpcoming] = useState([]);
  const [newsTk, setNewsTk] = useState('');
  const [holdings, setHoldings] = useState([]);
  const [scope, setScope] = useState('mylist'); // NEW

  const refreshAll = async () => {
    try {
      const [c, s, p, h, br, up] = await Promise.all([
        GET('/api/config'),
        GET(`/api/scan?scope=${encodeURIComponent(scope)}`).catch(()=>[]),
        GET('/api/portfolio').catch(()=>null),
        GET('/api/holdings').catch(()=>[]),
        GET(`/api/portfolio_breakdown?by=${encodeURIComponent(breakdownMode)}`).catch(()=>null),
        GET('/api/upcoming?days=7').catch(()=>[]),
      ]);
      setCfg(c); setScanRows(Array.isArray(s)?s:[]); setPortfolio(p); setHoldings(h);
      setPie(br); setUpcoming(up);
      if (h?.length) { try { setAnn(await GET('/api/announcements')); } catch {} } else { setAnn({}); }
    } catch (e) {
      console.error('refreshAll failed:', e);
    }
  };

  useEffect(() => { refreshAll(); }, [scope]);
  useEffect(() => { (async()=>{ try{ setPie(await GET(`/api/portfolio_breakdown?by=${breakdownMode}`)); }catch{} })(); }, [breakdownMode]);

  // add holding
  const onAddHolding = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    await POST('/api/holdings', { ticker: String(f.get('ticker')||'').toUpperCase(), qty: +f.get('qty'), avg_price: +f.get('avg_price') });
    e.target.reset(); refreshAll();
  };

  // upload CSV
  const onUploadCSV = async (e) => {
    e.preventDefault();
    const f = e.target.file.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    const res = await fetch('/api/holdings/import', { method:'POST', body: fd });
    if (res.ok) refreshAll();
  };

  // backtest
  const runBacktest = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const tickers = fd.get('tickers'); const years = fd.get('years') || 5;
    const r = await GET(`/api/backtest?tickers=${encodeURIComponent(tickers)}&years=${years}`);
    setBt(r); setCurve(null);
  };

  return html`
  <div class="space-y-6">
    <header class="flex items-center justify-between gap-2 flex-wrap">
      <h1 class="text-2xl font-semibold">Portfolio Scanner</h1>
      <div class="flex items-center gap-2">
        <label class="text-sm text-neutral-300">Scope</label>
        <select class="bg-neutral-800 rounded px-2 py-1 text-sm" value=${scope} onChange=${e=>setScope(e.target.value)}>
          <option value="mylist">My List (Holdings ∪ Watchlist)</option>
          <option value="all">All (My List ∪ Universes)</option>
        </select>
        <button class="px-3 py-2 bg-blue-600 rounded" onClick=${refreshAll}>Refresh</button>
      </div>
    </header>

    <section class="grid md:grid-cols-3 gap-4">
      <${Stat} label="Base currency" value=${cfg?.base_currency || '—'} />
      <${Stat} label="NAV" value=${portfolio ? fmt.n2(portfolio.nav) : '0.00'} sub=${portfolio && `As of ${fmt.dt(portfolio.asof)}`} />
      <${Stat} label="Signals loaded" value=${scanRows.length} />
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4">
      <h2 class="font-medium mb-2">Holdings</h2>
      <form class="flex flex-wrap gap-2 mb-3" onSubmit=${onAddHolding}>
        <input name="ticker" placeholder="Ticker (e.g., CBA.AX, AAPL)" class="flex-1 bg-neutral-800 rounded px-2 py-1" required />
        <input name="qty" type="number" step="0.0001" placeholder="Qty" class="w-28 bg-neutral-800 rounded px-2 py-1" required />
        <input name="avg_price" type="number" step="0.0001" placeholder="Avg price" class="w-28 bg-neutral-800 rounded px-2 py-1" required />
        <button class="px-3 py-1 bg-green-600 rounded">Add</button>
      </form>
      <form class="flex items-center gap-2 mb-3" onSubmit=${onUploadCSV}>
        <input name="file" type="file" accept=".csv" class="text-sm" />
        <button class="px-3 py-1 bg-blue-600 rounded">Upload CSV</button>
      </form>
      <div class="divide-y divide-neutral-800">
        ${holdings.map(h => html`<div class="py-2 text-sm grid grid-cols-4 gap-2">
          <span class="font-mono">${h.ticker}</span>
          <span>${h.qty}</span>
          <span>@ ${h.avg_price}</span>
          <span class="text-xs opacity-70">ID ${h.id}</span>
        </div>`)}
        ${holdings.length===0 && html`<div class="text-sm text-neutral-400">No holdings yet — add a few above.</div>`}
      </div>
    </section>

    ${pie && pie.items && pie.items.length>0 && html`
    <section class="bg-neutral-900 rounded-2xl p-4">
      <div class="flex items-center justify-between mb-2">
        <h2 class="font-medium">Portfolio allocation</h2>
        <div class="text-xs text-neutral-400">Base: ${pie.base_currency}</div>
      </div>
      <div class="flex gap-2 mb-3">
        ${['ticker','sector','region'].map(m => html`
          <button class="px-2 py-1 rounded ${breakdownMode===m?'bg-blue-700':'bg-neutral-800'} text-xs" onClick=${()=>{ setBreakdownMode(m); setFilterTicker(''); }}>${m.toUpperCase()}</button>
        `)}
      </div>
      <div class="grid md:grid-cols-2 gap-4 items-center">
        <div><${PieChart} data=${pie} onSliceClick=${(label)=>{ if (breakdownMode==='ticker') setFilterTicker(label); }} /></div>
        <div class="text-sm">
          <div class="mb-2 text-neutral-400">Click a slice to filter the table by ticker (ticker mode).</div>
          <div class="space-y-1 max-h-56 overflow-auto">
            ${pie.items.map(i => html`<div class="flex justify-between">
              <span class="font-mono ${filterTicker===i.label?'text-white':'text-neutral-300'}">${i.label}</span>
              <span class="${filterTicker===i.label?'text-white':'text-neutral-300'}">${(i.weight*100).toFixed(2)}%</span>
            </div>`)}
          </div>
          ${filterTicker && html`<button class="mt-3 px-3 py-1 bg-neutral-800 rounded text-xs" onClick=${()=>setFilterTicker('')}>Clear filter</button>`}
        </div>
      </div>
    </section>`}

    <section class="bg-neutral-900 rounded-2xl p-4">
      <h2 class="font-medium mb-3">Opportunities & Actions</h2>
      <div class="overflow-auto">
        <table class="min-w-full text-sm">
          <thead class="text-neutral-400"><tr>
            <th class="text-left p-2">Ticker</th><th class="text-left p-2">Side</th>
            <th class="text-right p-2">Score</th><th class="text-right p-2">Price (${cfg?.base_currency||''})</th>
            <th class="text-left p-2">Reasons</th><th class="text-left p-2">Data / Events</th></tr></thead>
          <tbody>
            ${scanRows.filter(r => !filterTicker || r.ticker===filterTicker).map(r => html`<tr class="border-t border-neutral-800">
              <td class="p-2 font-mono">${r.ticker}</td>
              <td class="p-2"><${Badge}>${r.side}</${Badge}></td>
              <td class="p-2 text-right">${r.score.toFixed(2)}</td>
              <td class="p-2 text-right">${r.px.toFixed(2)}</td>
              <td class="p-2">${(r.reasons||[]).join('; ')}</td>
              <td class="p-2 text-xs text-neutral-300">
                <div class="flex gap-1 items-center flex-wrap">
                  <${Chip}>facts ${(r.extras?.facts_completeness*100||0).toFixed(0)}%</${Chip}>
                  ${r.extras?.news_sentiment_avg!=null && html`<${Chip}>news ${(r.extras.news_sentiment_avg>=0?'+':'')}${r.extras.news_sentiment_avg.toFixed(2)}</${Chip}>`}
                  ${r.extras?.events?.earnings_date && html`<${Chip}>ER: ${new Date(r.extras.events.earnings_date).toLocaleDateString()}</${Chip}>`}
                  ${r.extras?.events?.ex_div_date && html`<${Chip}>ExDiv: ${new Date(r.extras.events.ex_div_date).toLocaleDateString()}</${Chip}>`}
                  <button class="ml-2 px-2 py-1 bg-neutral-800 rounded" onClick=${()=>setNewsTk(r.ticker)}>News</button>
                </div>
              </td>
            </tr>`)}
            ${scanRows.length===0 && html`<tr><td class="p-3 text-neutral-400" colspan="6">No opportunities yet — add holdings/watchlist or switch scope to <b>All</b>.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4">
      <h2 class="font-medium mb-3">Backtest (simple)</h2>
      <form class="flex gap-2 items-center flex-wrap" onSubmit=${runBacktest}>
        <input name="tickers" placeholder="Tickers (AAPL,MSFT,CBA.AX)" class="flex-1 bg-neutral-800 rounded px-2 py-1" required />
        <input name="years" type="number" min="1" max="15" value="5" class="w-24 bg-neutral-800 rounded px-2 py-1" />
        <button class="px-3 py-1 bg-purple-700 rounded">Run</button>
        ${bt?.results?.length>0 && html`
          <a class="text-xs underline opacity-80 hover:opacity-100" href=${`/api/backtest.csv?tickers=${encodeURIComponent(bt.results.map(x=>x.ticker).join(','))}&years=5`}>Download CSV</a>`}
      </form>
      ${bt && html`
        <div class="mt-4">
          ${bt.summary && html`<div class="text-sm text-neutral-300 mb-2">
            Avg CAGR: ${fmt.pct(bt.summary.avg_cagr/100)} · Avg MaxDD: ${fmt.pct(bt.summary.avg_max_dd/100)} · Avg Sharpe: ${bt.summary.avg_sharpe?.toFixed(2)} · Tickers: ${bt.summary.tickers}
          </div>`}
          <div class="overflow-auto">
            <table class="min-w-full text-sm">
              <thead class="text-neutral-400"><tr><th class="p-2 text-left">Ticker</th><th class="p-2 text-right">CAGR</th><th class="p-2 text-right">Max DD</th><th class="p-2 text-right">Sharpe</th><th class="p-2 text-right">Trades</th></tr></thead>
              <tbody>${(bt.results||[]).map(r => html`
                <tr class="border-t border-neutral-800">
                  <td class="p-2 font-mono">${r.ticker}</td>
                  <td class="p-2 text-right">${fmt.pct(r.cagr/100)}</td>
                  <td class="p-2 text-right">${fmt.pct(r.max_dd/100)}</td>
                  <td class="p-2 text-right">${r.sharpe?.toFixed(2)}</td>
                  <td class="p-2 text-right">${r.trades}</td>
                </tr>`)}
              </tbody>
            </table>
          </div>
          <div class="mt-4 flex items-center gap-2">
            <select class="bg-neutral-800 rounded px-2 py-1" onChange=${async (e)=>{ const tk = e.target.value; if(!tk){ setCurve(null); return; } setCurve(await GET(`/api/backtest_equity?ticker=${tk}&years=5`)); }}>
              <option value="">— Select ticker for chart —</option>
              ${(bt.results||[]).map(r => html`<option value=${r.ticker}>${r.ticker}</option>`)}
            </select>
          </div>
          ${curve && html`<div class="mt-3"><${EquityChart} data=${curve} /></div>`}
        </div>`}
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4">
      <h2 class="font-medium mb-3">ASX Announcements (recent)</h2>
      ${Object.keys(ann||{}).length===0 && html`<div class="text-sm text-neutral-400">No AU holdings or no recent announcements.</div>`}
      ${Object.entries(ann||{}).map(([tk, items]) => html`
        <div class="mb-3">
          <div class="font-semibold mb-1">${tk}</div>
          <ul class="list-disc pl-5 space-y-1">
            ${items.map(it => html`<li><a class="underline" href=${it.link} target="_blank">${it.title}</a> <span class="text-xs text-neutral-400">${fmt.dt(it.published)}</span></li>`)}
          </ul>
        </div>
      `)}
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4">
      <h2 class="font-medium mb-3">Cache</h2>
      <div class="flex items-center gap-2">
        <button class="px-3 py-1 bg-neutral-800 rounded text-sm" onClick=${async()=>{ await fetch('/api/cache/clear',{method:'POST'}); await refreshAll(); }}>Clear Cache</button>
      </div>
    </section>

    ${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}
  </div>`;
}

render(html`<${App} />`, document.getElementById('root'));
