import { html } from 'https://unpkg.com/htm/preact/standalone.module.js';

function Chip({ children }) { return html`<span class="px-2 py-0.5 rounded-full text-[10px] bg-neutral-800">${children}</span>`; }
function Badge({ children }) { const color = children === 'BUY' ? 'bg-green-600' : children === 'HOLD' ? 'bg-yellow-600' : children === 'SELL' ? 'bg-red-600' : 'bg-neutral-700'; return html`<span class="px-2 py-1 rounded text-xs ${color}">${children}</span>`; }
function Stat({ label, value, sub }) { return html`<div class="bg-neutral-900 rounded-2xl p-4"><div class="text-sm text-neutral-400">${label}</div><div class="text-2xl font-semibold">${value}</div>${sub && html`<div class="text-xs text-neutral-400 mt-1">${sub}</div>`}${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}</div>`; }

function EquityChart({ data }) {
  const ref = React.useRef();
  React.useEffect(()=>{
    if (!data || !data.dates || data.dates.length===0) return;
    const ctx = ref.current.getContext('2d');
    if (ref.current._chart) ref.current._chart.destroy();
    ref.current._chart = new Chart(ctx, {
      type: 'line',
      data: { 
        labels: data.dates, 
        datasets: [
          { label: 'Equity (1.0=Start)', data: data.equity, borderWidth: 1, fill: false, tension: 0.1 },
          { label: `Benchmark ${data.bench?.label||''}`, data: data.bench?.equity||[], borderWidth: 1, fill: false, tension: 0.1 },
          { label: 'Drawdown', data: data.drawdown, borderWidth: 1, fill: false, tension: 0.1, yAxisID: 'y1' }
        ] 
      },
      options: {
        animation: false,
        scales: { y: { beginAtZero: false }, y1: { position: 'right', beginAtZero: true, suggestedMin: -1, suggestedMax: 0 } },
        plugins: { legend: { display: true } }
      }
    });
  }, [data]);
  return html`<canvas class="w-full h-60" ref=${ref}></canvas>`;
}

function PieChart({ data, onSliceClick }) {
  const ref = React.useRef();
  React.useEffect(()=>{
    if (!data || !data.items || data.items.length===0) return;
    const labels = data.items.map(i=>i.label);
    const weights = data.items.map(i=>+(i.weight*100).toFixed(2));
    const ctx = ref.current.getContext('2d');
    if (ref.current._chart) ref.current._chart.destroy();
    ref.current._chart = new Chart(ctx, {
      type: 'doughnut',
      data: { labels, datasets: [{ data: weights }] },
      options: {
        onClick: (evt, elements) => { if (!elements || elements.length===0) return; const idx = elements[0].index; onSliceClick && onSliceClick(labels[idx]); },
        plugins: { tooltip: { callbacks: { label: ctx => `${ctx.label}: ${ctx.parsed}%` } }, legend: { position: 'right' } }
      }
    });
  }, [data]);
  return html`<canvas class="w-full h-64" ref=${ref}></canvas>`;
}

function RebalanceTable(){
  const [res, setRes] = React.useState(null);
  React.useEffect(()=>{ const handler = ()=>{ setRes(window.rebalanceResult||null); }; window.addEventListener('rebalance-update', handler); handler(); return ()=> window.removeEventListener('rebalance-update', handler); }, []);
  if (!res) return html`<div class="text-neutral-400 text-sm">No suggestions yet.${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}</div>`;
  return html`<div>
    <div class="text-neutral-300 mb-2">Base ${res.base_currency}; NAV incl. cash: ${res.nav_with_cash.toFixed(2)}</div>
    <div class="overflow-auto"><table class="min-w-full text-sm">
      <thead class="text-neutral-400"><tr><th class="p-2 text-left">Ticker</th><th class="p-2 text-left">Side</th><th class="p-2 text-right">Qty Δ</th><th class="p-2 text-right">Notional Δ</th><th class="p-2 text-right">Px</th></tr></thead>
      <tbody>${res.suggestions.map(s => html`<tr class="border-t border-neutral-800"><td class="p-2 font-mono">${s.ticker}</td><td class="p-2">${s.side}</td><td class="p-2 text-right">${s.qty_delta.toFixed(4)}</td><td class="p-2 text-right">${s.notional_delta.toFixed(2)}</td><td class="p-2 text-right">${s.price.toFixed(4)}</td></tr>`)}</tbody>
    </table></div>
  ${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}</div>`;
}

function NewsModal({ ticker, onClose }){
  const [items, setItems] = React.useState(null);
  React.useEffect(()=>{ (async()=>{ const res = await fetch(`/api/news?ticker=${ticker}&days=7&limit=20`).then(r=>r.json()); setItems(res.items||[]); })(); }, [ticker]);
  return html`<div class="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
    <div class="bg-neutral-900 rounded-2xl p-4 w-full max-w-2xl max-h-[80vh] overflow-auto">
      <div class="flex items-center justify-between mb-2">
        <div class="font-semibold">News — ${ticker}</div>
        <button class="px-2 py-1 bg-neutral-800 rounded text-sm" onClick=${onClose}>Close</button>
      </div>
      ${!items && html`<div class="text-sm text-neutral-400">Loading…</div>`}
      ${items && items.length===0 && html`<div class="text-sm text-neutral-400">No recent articles.</div>`}
      ${items && items.length>0 && html`<ul class="space-y-2">
        ${items.map(it => html`<li class="bg-neutral-950 rounded p-2">
          <a class="underline" href=${it.link} target="_blank" rel="noreferrer">${it.title}</a>
          <div class="text-xs text-neutral-400 mt-1 flex items-center gap-2">
            <span>${it.published ? new Date(it.published).toLocaleString() : ''}</span>
            <span class="${(it.sentiment||0) > 0.1 ? 'text-green-400' : (it.sentiment||0) < -0.1 ? 'text-red-400' : 'text-neutral-400'}">sentiment ${(it.sentiment>=0?'+':'')}${(it.sentiment||0).toFixed(2)}</span>
          </div>
        </li>`)}
      </ul>`}
    </div>
  ${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}</div>`;
}


function RebalanceBucketTable(){
  const [res, setRes] = React.useState(null);
  React.useEffect(()=>{ const handler = ()=>{ setRes(window.rebalanceBucket||null); }; window.addEventListener('rebalance-bucket-update', handler); handler(); return ()=> window.removeEventListener('rebalance-bucket-update', handler); }, []);
  if (!res) return html`<div class="text-neutral-400 text-sm">No suggestions yet.${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}</div>`;
  return html`<div>
    <div class="text-neutral-300 mb-2">Mode: ${res.mode.toUpperCase()} · Base ${res.base_currency}; NAV incl. cash: ${res.nav_with_cash.toFixed(2)}</div>
    <div class="overflow-auto"><table class="min-w-full text-sm">
      <thead class="text-neutral-400"><tr><th class="p-2 text-left">Ticker</th><th class="p-2 text-left">${res.mode==='sector'?'Sector':'Region'}</th><th class="p-2 text-left">Side</th><th class="p-2 text-right">Qty Δ</th><th class="p-2 text-right">Notional Δ</th><th class="p-2 text-right">Px</th></tr></thead>
      <tbody>${res.suggestions.map(s => html`<tr class="border-t border-neutral-800"><td class="p-2 font-mono">${s.ticker}</td><td class="p-2">${s.bucket}</td><td class="p-2">${s.side}</td><td class="p-2 text-right">${s.qty_delta.toFixed(4)}</td><td class="p-2 text-right">${s.notional_delta.toFixed(2)}</td><td class="p-2 text-right">${s.price.toFixed(4)}</td></tr>`)}</tbody>
    </table></div>
  ${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}</div>`;
}

function App() {
  const [cfg, setCfg] = React.useState(null);
  const [rows, setRows] = React.useState([]);
  const [holdings, setHoldings] = React.useState([]);
  const [snap, setSnap] = React.useState(null);
  const [ann, setAnn] = React.useState({});
  const [bt, setBt] = React.useState(null);
  const [selTicker, setSelTicker] = React.useState('');
  const [curve, setCurve] = React.useState(null);
  const [cacheInfo, setCacheInfo] = React.useState(null);
  const [upcoming, setUpcoming] = React.useState([]);
  const [pie, setPie] = React.useState(null);
  const [breakdownMode, setBreakdownMode] = React.useState('ticker');
  const [filterTicker, setFilterTicker] = React.useState('');
  const [newsTk, setNewsTk] = React.useState('');

  async function load() {
    const [c, h, s, p, a, ci, up, br] = await Promise.all([
      fetch('/api/config').then(r=>r.json()),
      fetch('/api/holdings').then(r=>r.json()),
      fetch('/api/scan').then(r=>r.json()),
      fetch('/api/portfolio').then(r=>r.json()).catch(()=>null),
      fetch('/api/announcements').then(r=>r.json()).catch(()=>({})),
      fetch('/api/cache').then(r=>r.json()).catch(()=>null),
      fetch('/api/upcoming?days=7').then(r=>r.json()).catch(()=>[]),
      fetch(`/api/portfolio_breakdown?by=${encodeURIComponent(breakdownMode)}`).then(r=>r.json()).catch(()=>null)
    ]);
    setCfg(c); setHoldings(h); setRows(s); setSnap(p); setAnn(a); setCacheInfo(ci); setUpcoming(up); setPie(br);
  }
  React.useEffect(()=>{ load(); }, []);
  React.useEffect(()=>{ (async()=>{ const br = await fetch(`/api/portfolio_breakdown?by=${encodeURIComponent(breakdownMode)}`).then(r=>r.json()).catch(()=>null); if(br) setPie(br); })(); }, [breakdownMode]);

  async function addHolding(e) {
    e.preventDefault();
    const f = new FormData(e.target);
    await fetch('/api/holdings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ticker: f.get('ticker'), qty: parseFloat(f.get('qty')), avg_price: parseFloat(f.get('avg_price')) })});
    e.target.reset(); load();
  }

  return html`
  <div class="space-y-6">
    <header class="flex items-center justify-between">
      <h1 class="text-2xl font-semibold">Portfolio Manager & Scanner (AU + US)</h1>
      <button class="px-3 py-2 bg-blue-600 rounded" onClick=${load}>Refresh</button>
    </header>

    <section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <h2 class="font-medium mb-2">Next 7 days</h2>
      ${(!upcoming || upcoming.length===0) && html`<div class="text-sm text-neutral-400">No upcoming earnings or ex-dividend dates in the next week.</div>`}
      ${upcoming && upcoming.length>0 && html`<div class="flex flex-wrap gap-2">
        ${upcoming.map(u => html`<div class="px-3 py-2 rounded-xl bg-neutral-800 text-xs"><span class="font-mono mr-2">${u.ticker}</span><span class="mr-2">${u.type}</span><span class="opacity-80">${new Date(u.date).toLocaleString()}</span></div>`)}
      </div>`}
    </section>

    ${snap && html`<section class="grid md:grid-cols-4 gap-4">
      <${Stat} label="NAV" value={`${snap.nav.toFixed(2)} ${cfg.base_currency}`} sub={`As of ${new Date(snap.asof).toLocaleString()}`} />
      <${Stat} label="Total PnL" value={`${(snap.pnl_pct*100).toFixed(2)}%`} sub={`${snap.pnl_total.toFixed(2)} ${cfg.base_currency}`} />
      <${Stat} label="Top Position" value={snap.top_positions?.[0]?.ticker || '-'} sub={`${(snap.top_positions?.[0]?.weight*100).toFixed(1)||0}% of NAV`} />
      <${Stat} label="Risk Flags" value={snap.risk_flags.length} sub={snap.risk_flags.join(', ')} />
    </section>`}

    ${pie && pie.items && pie.items.length>0 && html`<section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <div class="flex items-center justify-between mb-2">
        <h2 class="font-medium">Portfolio Allocation</h2>
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
          <div class="mb-2 text-neutral-300">Click a slice to filter by ticker (ticker mode). Toggle Sector/Region for composition.</div>
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

    <section class="grid md:grid-cols-2 gap-6">
      <div class="bg-neutral-900 rounded-2xl p-4 shadow">
        <h2 class="font-medium mb-2">Holdings</h2>
        <form class="flex gap-2 mb-3" onSubmit=${addHolding}>
          <input name="ticker" placeholder="Ticker (e.g., CBA.AX, AAPL)" class="flex-1 bg-neutral-800 rounded px-2 py-1" required />
          <input name="qty" type="number" step="0.0001" placeholder="Qty" class="w-28 bg-neutral-800 rounded px-2 py-1" required />
          <input name="avg_price" type="number" step="0.0001" placeholder="Avg Px" class="w-28 bg-neutral-800 rounded px-2 py-1" required />
          <button class="px-3 py-1 bg-green-700 rounded">Add</button>
        </form>
        <form class="flex items-center gap-2" onSubmit=${async (e)=>{e.preventDefault(); const f=e.target.file.files[0]; if(!f) return; const fd=new FormData(); fd.append('file', f); await fetch('/api/holdings/import',{method:'POST', body: fd}); e.target.reset(); load();}}>
          <input name="file" type="file" accept=".csv" class="text-sm" />
          <button class="px-3 py-1 bg-blue-700 rounded">Upload CSV</button>
        </form>
        <div class="divide-y divide-neutral-800 mt-3">
          ${holdings.map(h => html`<div class="py-2 text-sm grid grid-cols-4 gap-2 items-center">
            <span class="font-mono">${h.ticker}</span><span>${h.qty}</span><span>@ ${h.avg_price}</span><span class="text-xs opacity-70 justify-self-end">Edit</span>
          </div>`)}
        </div>
      </div>
      <div class="bg-neutral-900 rounded-2xl p-4 shadow">
        <h2 class="font-medium mb-2">Config (signals)</h2>
        ${cfg && html`<pre class="text-xs bg-neutral-950 p-3 rounded overflow-auto max-h-72">${JSON.stringify(cfg.signals, null, 2)}</pre>`}
      </div>
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <h2 class="font-medium mb-3">Opportunities & Actions</h2>
      <div class="overflow-auto">
        <table class="min-w-full text-sm">
          <thead class="text-neutral-400"><tr><th class="text-left p-2">Ticker</th><th class="text-left p-2">Side</th><th class="text-right p-2">Score</th><th class="text-right p-2">Price (${cfg?.base_currency})</th><th class="text-left p-2">Reasons</th><th class="text-left p-2">Data / Events</th></tr></thead>
          <tbody>
            ${rows.filter(r => !filterTicker || r.ticker===filterTicker).map(r => html`<tr class="border-t border-neutral-800">
              <td class="p-2 font-mono">${r.ticker}</td><td class="p-2"><${Badge}>${r.side}</${Badge}></td>
              <td class="p-2 text-right">${r.score.toFixed(2)}</td><td class="p-2 text-right">${r.px.toFixed(2)}</td>
              <td class="p-2">${r.reasons.join('; ')}</td>
              <td class="p-2 text-xs text-neutral-300">
                <div class="flex gap-1 items-center">
                  <${Chip}>${(r.extras.facts_provider || 'yf').toUpperCase()}</${Chip}>
                  <${Chip}>facts ${(r.extras.facts_completeness*100||0).toFixed(0)}%</${Chip}>
                  ${r.extras.news_sentiment_avg != null && html`<${Chip}>news ${(r.extras.news_sentiment_avg>=0?'+':'')}${r.extras.news_sentiment_avg.toFixed(2)}</${Chip}>`}
                  ${r.extras.events && r.extras.events.earnings_date && html`<${Chip}>ER: ${new Date(r.extras.events.earnings_date).toLocaleDateString()}</${Chip}>`}
                  ${r.extras.events && r.extras.events.ex_div_date && html`<${Chip}>ExDiv: ${new Date(r.extras.events.ex_div_date).toLocaleDateString()}</${Chip}>`}
                </div>
                <button data-news class="mt-1 px-2 py-1 bg-neutral-800 rounded text-xs" onClick=${()=>setNewsTk(r.ticker)}>News</button>
              </td>
            </tr>`)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <h2 class="font-medium mb-3">Backtest (simple SMA200 + momentum)</h2>
      <form class="flex gap-2 items-center" onSubmit=${async (e)=>{e.preventDefault(); const f=new FormData(e.target); const t=f.get('tickers'); const y=f.get('years')||5; const res=await fetch(`/api/backtest?tickers=${encodeURIComponent(t)}&years=${y}`).then(r=>r.json()); setBt(res); setSelTicker(''); setCurve(null);}}>
        <input name="tickers" placeholder="Tickers comma-separated (e.g., AAPL,MSFT,CBA.AX)" class="flex-1 bg-neutral-800 rounded px-2 py-1" required />
        <input name="years" type="number" min="1" max="10" value="5" class="w-24 bg-neutral-800 rounded px-2 py-1" />
        <button class="px-3 py-1 bg-purple-700 rounded">Run</button>
      </form>
      ${bt && html`
        <div class="mt-4">
          ${bt.summary && html`<div class="text-sm text-neutral-300 mb-2">
            Avg CAGR: ${(bt.summary.avg_cagr*100).toFixed(2)}% · Avg MaxDD: ${(bt.summary.avg_max_dd*100).toFixed(2)}% · Avg Sharpe: ${bt.summary.avg_sharpe.toFixed(2)} · Tickers: ${bt.summary.tickers}
          </div>`}
          <div class="overflow-auto">
            <table class="min-w-full text-sm">
              <thead class="text-neutral-400"><tr><th class="p-2 text-left">Ticker</th><th class="p-2 text-right">CAGR</th><th class="p-2 text-right">Max DD</th><th class="p-2 text-right">Sharpe</th><th class="p-2 text-right">Trades</th></tr></thead>
              <tbody>${bt.results && bt.results.map(r => html`<tr class="border-t border-neutral-800"><td class="p-2 font-mono">${r.ticker}</td><td class="p-2 text-right">${(r.cagr*100).toFixed(2)}%</td><td class="p-2 text-right">${(r.max_dd*100).toFixed(2)}%</td><td class="p-2 text-right">${r.sharpe.toFixed(2)}</td><td class="p-2 text-right">${r.trades}</td></tr>`)}</tbody>
            </table>
          </div>
          <a class="inline-block mt-3 text-xs underline opacity-80 hover:opacity-100" href=${`/api/backtest.csv?tickers=${encodeURIComponent(bt.results.map(x=>x.ticker).join(","))}&years=5`}>Download CSV</a>
          <div class="mt-4 flex items-center gap-2">
            <select class="bg-neutral-800 rounded px-2 py-1" onChange=${async (e)=>{const tk=e.target.value; setSelTicker(tk); if(!tk) return; const res=await fetch(`/api/backtest_equity?ticker=${tk}&years=5`).then(r=>r.json()); setCurve(res);}}>
              <option value="">— Select ticker for chart —</option>
              ${bt.results && bt.results.map(r => html`<option value=${r.ticker}>${r.ticker}</option>`) }
            </select>
          </div>
          ${curve && html`<div class="mt-3"><${EquityChart} data=${curve} /></div>`}
        </div>`}
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <h2 class="font-medium mb-3">Cache</h2>
      ${!cacheInfo && html`<div class="text-sm text-neutral-400">No cache info yet.</div>`}
      ${cacheInfo && html`<div class="text-sm text-neutral-300 mb-2">Dir: ${cacheInfo.dir} · Files: ${cacheInfo.count}</div>`}
      ${cacheInfo && html`<div class="overflow-auto"><table class="min-w-full text-sm">
        <thead class="text-neutral-400"><tr><th class="p-2 text-left">File</th><th class="p-2 text-right">Size</th><th class="p-2 text-right">Age (min)</th></tr></thead>
        <tbody>${cacheInfo.items.map(it => html`<tr class="border-t border-neutral-800"><td class="p-2">${it.file}</td><td class="p-2 text-right">${it.size}</td><td class="p-2 text-right">${it.age_min}</td></tr>`)}</tbody>
      </table></div>`}
      <button class="mt-3 px-3 py-1 bg-red-700 rounded" onClick=${async ()=>{await fetch('/api/cache/clear',{method:'POST'}); const ci = await fetch('/api/cache').then(r=>r.json()); setCacheInfo(ci);}}>Clear Cache</button>
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <h2 class="font-medium mb-3">ASX Announcements (recent)</h2>
      ${Object.keys(ann||{}).length === 0 && html`<div class="text-sm text-neutral-400">No ASX holdings or no recent announcements.</div>`}
      ${Object.entries(ann||{}).map(([tk, items]) => html`
        <div class="mb-3">
          <div class="font-semibold mb-1">${tk}</div>
          <ul class="list-disc pl-5 space-y-1">
            ${items.map(it => html`<li><a class="underline" href=${it.link} target="_blank">${it.title}</a> <span class="text-xs text-neutral-400">${it.published?new Date(it.published).toLocaleString():''}</span></li>`)}
          </ul>
        </div>
      `)}
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <h2 class="font-medium mb-3">Rebalance to Target Weights</h2>
      <form class="space-y-2" onSubmit=${async (e)=>{
        e.preventDefault();
        const body = e.target.targets.value.trim(); if (!body) return;
        const targets = body.split(/\n+/).map(l=>l.trim()).filter(Boolean).map(l=>{ const [tk, w] = l.split('='); const num = parseFloat(String(w).replace('%','').trim()); return { ticker: tk.trim().toUpperCase(), target_weight: (num||0)/100.0 }; });
        const cash = parseFloat(e.target.cash.value||'0')||0;
        const min_order_value = parseFloat(e.target.min_order.value||'0')||0;
        const lot_size = parseInt(e.target.lot_size.value||'1')||1;
        const seed_source = String(e.target.seed_source.value||'watchlist');
        const res = await fetch('/api/rebalance_suggest', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({targets, cash, min_order_value, lot_size, seed_source})}).then(r=>r.json());
        window.rebalanceResult = res; window.dispatchEvent(new Event('rebalance-update'));
      }}>
        <textarea name="targets" rows="5" class="w-full bg-neutral-800 rounded p-2 text-sm" placeholder="AAPL=15\nMSFT=10\nCBA.AX=12"></textarea>
        <div class="flex items-center gap-2">
          <input name="cash" type="number" step="0.01" class="bg-neutral-800 rounded px-2 py-1 text-sm" placeholder="Extra cash (optional)" />
          <input name="min_order" type="number" step="0.01" class="bg-neutral-800 rounded px-2 py-1 text-sm" placeholder="Min order (base)" />
          <input name="lot_size" type="number" step="1" min="1" class="bg-neutral-800 rounded px-2 py-1 text-sm" placeholder="Lot size (e.g., 1)" />
          <select name="seed_source" class="bg-neutral-800 rounded px-2 py-1 text-sm">
            <option value="watchlist">Seed: Watchlist</option>
            <option value="signals">Seed: Signals (BUYs)</option>
            <option value="none">Seed: None</option>
          </select>
          <button class="px-3 py-1 bg-green-700 rounded text-sm">Suggest Trades</button>
        </div>
      </form>
      <div id="rb-table" class="mt-3 text-sm"><${RebalanceTable} /></div>
    </section>

    <section class="bg-neutral-900 rounded-2xl p-4 shadow">
      <h2 class="font-medium mb-3">Rebalance by Sector/Region</h2>
      <form class="space-y-2" onSubmit=${async (e)=>{
        e.preventDefault();
        const mode = e.target.mode.value;
        const body = e.target.targets.value.trim(); if (!body) return;
        const targets = body.split(/\n+/).map(l=>l.trim()).filter(Boolean).map(l=>{ const [bucket, w] = l.split('='); const num = parseFloat(String(w).replace('%','').trim()); return { bucket: bucket.trim(), target_weight: (num||0)/100.0 }; });
        const cash = parseFloat(e.target.cash.value||'0')||0;
        const min_order_value = parseFloat(e.target.min_order_b.value||'0')||0;
        const lot_size = parseInt(e.target.lot_size_b.value||'1')||1;
        const seed_source = String(e.target.seed_source_b.value||'watchlist');
        const res = await fetch('/api/rebalance_by_bucket', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode, targets, cash, min_order_value, lot_size, seed_source})}).then(r=>r.json());
        window.rebalanceBucket = res; window.dispatchEvent(new Event('rebalance-bucket-update'));
      }}>
        <div class="flex items-center gap-2">
          <label class="text-sm">Mode</label>
          <select name="mode" class="bg-neutral-800 rounded px-2 py-1 text-sm">
            <option value="sector">SECTOR</option>
            <option value="region">REGION</option>
          </select>
          <input name="cash" type="number" step="0.01" class="bg-neutral-800 rounded px-2 py-1 text-sm" placeholder="Extra cash (optional)" />
          <input name="min_order_b" type="number" step="0.01" class="bg-neutral-800 rounded px-2 py-1 text-sm" placeholder="Min order (base)" />
          <input name="lot_size_b" type="number" step="1" min="1" class="bg-neutral-800 rounded px-2 py-1 text-sm" placeholder="Lot size (e.g., 1)" />
          <select name="seed_source_b" class="bg-neutral-800 rounded px-2 py-1 text-sm">
            <option value="watchlist">Seed: Watchlist</option>
            <option value="signals">Seed: Signals (BUYs)</option>
            <option value="none">Seed: None</option>
          </select>
          <button class="px-3 py-1 bg-green-700 rounded text-sm">Suggest</button>
        </div>
        <textarea name="targets" rows="5" class="w-full bg-neutral-800 rounded p-2 text-sm" placeholder="Technology=25\nFinancial Services=15\nAustralia=40"></textarea>
      </form>
      <div class="mt-3 text-sm"><${RebalanceBucketTable} /></div>
    </section>
  ${newsTk && html`<${NewsModal} ticker=${newsTk} onClose=${()=>setNewsTk('')} />`}</div>`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
