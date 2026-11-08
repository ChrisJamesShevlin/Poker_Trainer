/* Analytics page: builds the whole tab from /api/analytics. All text goes in via textContent. */
(function () {
  const C = { c1: '#3987e5', c2: '#d95926', pos: '#3987e5', neg: '#e66767', good: '#0ca30c', crit: '#d03b3b',
              ink: '#e8eef4', ink2: '#b4c1ce', muted: '#8da0b3', hair: 'rgba(255,255,255,.07)',
              base: '#3a4756', surface: '#171f27', tipbg: '#0e1318' };
  const FONT = 'system-ui,-apple-system,"Segoe UI",Roboto,sans-serif';
  Chart.defaults.font.family = FONT;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = C.muted;

  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const dp = v => (Math.abs(v) >= 100 ? 0 : 2);          // cents on small (dollar-sized) amounts
  const num = v => (+v).toLocaleString(undefined, { maximumFractionDigits: dp(v) });
  const signed = v => (v >= 0 ? '+' : '−') + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: dp(v) });
  const pct = (v, d = 0) => v == null ? 'n/a' : (v * 100).toFixed(d) + '%';
  const compact = v => Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(v);
  const get = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } };
  const put = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };

  let charts = [], view = get('anView', 'charts'), last = null;
  // money is your real bankroll; big blinds make results comparable across stakes. stake = a big blind, or null for all stakes
  let unit = get('anUnit', 'money'), stake = get('anStake', null);
  const BB = () => unit === 'bb';
  const uword = () => (BB() ? ' bb' : ' chips');
  const sv = v => signed(v) + (BB() ? ' bb' : '');            // a signed value with its unit
  const r1 = v => Math.round(v * 10) / 10;
  let coach = { state: 'idle', text: '', at: null };  // idle | loading | done | error

  /* ---------- chart plugins ---------- */
  const crosshair = {
    id: 'crosshair',
    afterDatasetsDraw(chart) {
      const a = chart.tooltip && chart.tooltip._active;
      if (!chart.config.options.crosshair || !a || !a.length) return;
      const x = a[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
      ctx.save(); ctx.strokeStyle = C.base; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
    }
  };
  const endDots = {  // end-point dot with a 2px surface ring, and the value beside it (line charts)
    id: 'endDots',
    afterDatasetsDraw(chart) {
      chart.data.datasets.forEach((ds, di) => {
        if (!ds.endDot) return;
        const meta = chart.getDatasetMeta(di);
        let i = ds.data.length - 1; while (i >= 0 && ds.data[i] == null) i--;
        if (i < 0 || !meta.data[i]) return;
        const { x, y } = meta.data[i], ctx = chart.ctx;
        ctx.save();
        ctx.fillStyle = C.surface; ctx.beginPath(); ctx.arc(x, y, 6, 0, 7); ctx.fill();
        ctx.fillStyle = ds.borderColor; ctx.beginPath(); ctx.arc(x, y, 4, 0, 7); ctx.fill();
        if (ds.endLabel) {
          ctx.fillStyle = C.ink; ctx.font = '600 12px ' + FONT; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
          ctx.fillText(ds.endLabel(ds.data[i]), x - 4, y - 9);
        }
        ctx.restore();
      });
    }
  };
  const tipValues = {  // value at the tip of each horizontal bar
    id: 'tipValues',
    afterDatasetsDraw(chart) {
      const fmt = chart.config.options.tipFormat; if (!fmt) return;
      const ctx = chart.ctx; ctx.save();
      ctx.fillStyle = C.ink; ctx.font = '600 12px ' + FONT; ctx.textBaseline = 'middle';
      chart.getDatasetMeta(0).data.forEach((bar, i) => {
        const v = chart.data.datasets[0].data[i];
        ctx.textAlign = v >= 0 ? 'left' : 'right';
        ctx.fillText(fmt(v), bar.x + (v >= 0 ? 7 : -7), bar.y);
      });
      ctx.restore();
    }
  };

  /* ---------- shared options ---------- */
  function scales(o = {}) {
    const x = Object.assign({
      grid: { display: false }, border: { color: C.base },
      ticks: { color: C.muted, maxTicksLimit: 7, maxRotation: 0 }
    }, o.x || {});
    const y = Object.assign({
      grid: { color: c => c.tick && c.tick.value === 0 ? C.base : C.hair, lineWidth: 1 },
      border: { display: false },
      ticks: { color: C.muted, maxTicksLimit: 6, callback: v => compact(v) + (o.suffix || '') }
    }, o.y || {});
    return { x, y };
  }
  function tooltip(extra = {}) {
    return Object.assign({
      backgroundColor: C.tipbg, borderColor: 'rgba(255,255,255,.12)', borderWidth: 1, padding: 10, cornerRadius: 8,
      titleColor: C.muted, titleFont: { size: 11, weight: '400' }, bodyColor: C.ink, bodyFont: { size: 13, weight: '600' },
      footerColor: C.muted, footerFont: { size: 11, weight: '400' },
      boxWidth: 14, boxHeight: 3, boxPadding: 4, usePointStyle: false
    }, extra);
  }
  function base(extra = {}) {
    return Object.assign({
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      layout: { padding: { top: 16, right: 14, bottom: 0, left: 0 } },
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: tooltip() }
    }, extra);
  }
  const line = (label, data, color, o = {}) => Object.assign({
    label, data, borderColor: color, borderWidth: 2, tension: .25, pointRadius: 0, pointHoverRadius: 5,
    pointHoverBackgroundColor: color, pointHoverBorderColor: C.surface, pointHoverBorderWidth: 2,
    borderJoinStyle: 'round', borderCapStyle: 'round', spanGaps: false, endDot: true
  }, o);
  const wash = (hex, a = .10) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };
  const colRadius = ctx => (ctx.raw >= 0 ? { topLeft: 4, topRight: 4 } : { bottomLeft: 4, bottomRight: 4 });
  const barRadius = ctx => (ctx.raw >= 0 ? { topRight: 4, bottomRight: 4 } : { topLeft: 4, bottomLeft: 4 });

  /* ---------- card helper ---------- */
  function card(parent, title, desc, opts = {}) {
    const c = el('div', 'an-card' + (opts.wide ? ' wide' : ''));
    c.appendChild(el('h3', null, title));
    c.appendChild(el('p', 'desc', desc));
    if (opts.legend) {
      const lg = el('div', 'legend');
      opts.legend.forEach(([label, color, kind]) => {
        const s = el('span'); const i = el('i', kind === 'sq' ? 'sq' : ''); i.style.background = color;
        s.appendChild(i); s.appendChild(document.createTextNode(label)); lg.appendChild(s);
      });
      c.appendChild(lg);
    }
    const w = el('div', 'cwrap' + (opts.tall ? ' tall' : ''));
    const cv = el('canvas'); cv.setAttribute('role', 'img'); cv.setAttribute('aria-label', title + '. ' + desc);
    w.appendChild(cv);
    if (opts.empty) w.appendChild(el('div', 'empty', opts.empty));
    c.appendChild(w); parent.appendChild(c);
    return cv;
  }
  const EMPTY = 'Not enough data yet - save a few hands and this fills in.';

  /* ---------- page ---------- */
  function build(s) {
    const root = $('anRoot'); root.textContent = '';
    charts.forEach(c => c.destroy()); charts = [];

    // header
    const head = el('div', 'an-head');
    const t = el('div'); t.appendChild(el('h2', null, 'Analytics'));
    t.appendChild(el('p', null, 'Everything you save is stored and counted here, across all sessions.'));
    head.appendChild(t);
    const acts = el('div', 'an-actions');
    const seg = el('div', 'an-seg');
    [['charts', 'Charts'], ['tables', 'Tables']].forEach(([k, label]) => {
      const b = el('button', view === k ? 'on' : '', label); b.onclick = () => { view = k; put('anView', k); build(last); };
      seg.appendChild(b);
    });
    acts.appendChild(seg);
    const useg = el('div', 'an-seg'); useg.title = 'Money adds up your real winnings; big blinds compare results fairly across stakes';
    [['money', 'Money'], ['bb', 'Big blinds']].forEach(([k, label]) => {
      const b = el('button', unit === k ? 'on' : '', label); b.onclick = () => { unit = k; put('anUnit', k); build(last); }; useg.appendChild(b);
    });
    acts.appendChild(useg);
    if ((s.stakes && s.stakes.length > 1) || stake != null) {
      const sel = el('select', 'an-sel'); sel.setAttribute('aria-label', 'Stake');
      [['', 'All stakes']].concat(s.stakes.map(x => [String(x.bb), `Big blind ${num(x.bb)} (${x.hands} hands)`])).forEach(([v, label]) => {
        const o = el('option', null, label); o.value = v; if ((stake == null && v === '') || (stake != null && String(stake) === v)) o.selected = true; sel.appendChild(o);
      });
      sel.onchange = () => { stake = sel.value === '' ? null : parseFloat(sel.value); put('anStake', stake); load(); };
      acts.appendChild(sel);
    }
    const clr = el('button', 'an-btn', 'Clear history');
    clr.onclick = async () => {
      if (!confirm('Clear ALL saved history?\n\nThis permanently deletes every saved session and hand, and the analytics start from zero. It cannot be undone.')) return;
      await fetch('/api/history/clear', { method: 'POST' });
      if (window.loadSession) await window.loadSession();
      load();
    };
    const cb = el('button', 'an-btn primary', coach.state === 'loading' ? 'Coach is thinking\u2026' : 'Coach analysis');
    cb.disabled = coach.state === 'loading'; cb.onclick = runCoach;
    acts.appendChild(cb);
    acts.appendChild(clr);
    head.appendChild(acts);
    root.appendChild(head);
    if (coach.state !== 'idle') root.appendChild(coachPanel());
    if (!BB() && stake == null && s.stakes && s.stakes.length > 1)
      root.appendChild(el('div', 'an-note', `You have results at ${s.stakes.length} different stakes. Money adds them together as they are, so a result at a bigger stake counts for more. ` +
        'Switch to Big blinds to compare them fairly, or pick one stake above.'));

    (s.mismatches || []).forEach(m => root.appendChild(el('div', 'an-note',
      `The session on ${m.label} doesn't add up: your balances say ${signed(r1(m.session_bb))} bb, but the hands you logged add up to ${signed(r1(m.hands_bb))} bb ` +
      `(balance ${m.balance_before} at the start, ${m.balance_after} at the end, buy-in ${m.buy_in}). Check the starting and ending balances: both should be your total ` +
      'chips the same way, so the net result and the totals above can be trusted.')));

    // hero + tiles
    const top = el('div', 'an-top');
    const hero = el('div', 'an-card hero');
    hero.appendChild(el('div', 'hlbl', BB() ? 'Net result from play (big blinds)' : 'Net result from play'));
    const hasSess = s.session_pl.length > 0;
    hero.appendChild(el('div', 'val', hasSess ? (BB() ? signed(r1(s.winnings_bb[s.winnings_bb.length - 1])) + ' bb' : signed(s.total_pl)) : '–'));
    const sub = hasSess
      ? `${s.session_pl.length} finished session${s.session_pl.length === 1 ? '' : 's'}, ${s.hands} hand${s.hands === 1 ? '' : 's'} saved.`
      : 'Finish a session (click the session button, then enter your balance) to see your result.';
    hero.appendChild(el('div', 'sub', sub));
    const wr = s.win_rate_roll.filter(v => v != null);
    if (wr.length) {
      const now = wr[wr.length - 1], all = s.win_rate * 100, d = now - all;
      const tr = el('div', 'trend', (d >= 0 ? '▲ ' : '▼ ') + `Last ${Math.min(20, s.cum_results.length)} hands: ${now.toFixed(0)}% won vs ${all.toFixed(0)}% overall`);
      hero.appendChild(tr);
    }
    top.appendChild(hero);
    const tiles = el('div', 'tiles');
    [
      ['Hands won', pct(s.win_rate), 'of hands with a result'],
      ['bb / 100', s.bb_per_100 == null ? 'n/a' : (s.bb_per_100 >= 0 ? '+' : '−') + Math.abs(s.bb_per_100).toFixed(1), 'big blinds per 100 hands'],
      ['Good calls', pct(s.good_call_rate), `${s.call_margins.length - s.bad_calls} of ${s.call_margins.length} calls`],
      ['Followed advice', pct(s.follow_rate), 'did what the tool suggested'],
      ['Preflop VPIP', pct(s.vpip), 'hands you chose to play']
    ].forEach(([k, v, h]) => {
      const tl = el('div', 'tile'); tl.appendChild(el('div', 'v', v)); tl.appendChild(el('div', 'k', k)); tl.appendChild(el('div', 'h', h)); tiles.appendChild(tl);
    });
    top.appendChild(tiles); root.appendChild(top);

    // insights
    const ins = insights(s);
    const ic = el('div', 'an-card insights'); ic.appendChild(el('h3', null, 'What stands out'));
    const ul = el('ul');
    ins.forEach(([kind, text]) => {
      const li = el('li'); li.appendChild(el('span', 'ic ' + kind, kind === 'good' ? '✓' : kind === 'bad' ? '!' : 'i'));
      li.appendChild(el('span', null, text)); ul.appendChild(li);
    });
    ic.appendChild(ul); root.appendChild(ic);

    if (view === 'tables') { tables(root, s); return; }
    charts_(root, s);
  }

  /* ---------- coach analysis (LLM) ---------- */
  function inline(parent, text) {  // **bold** only; everything else stays plain text
    text.split(/(\*\*[^*]+\*\*)/).forEach(part => {
      if (/^\*\*[^*]+\*\*$/.test(part)) parent.appendChild(el('strong', null, part.slice(2, -2)));
      else if (part) parent.appendChild(document.createTextNode(part));
    });
  }
  function renderMarkdown(box, md) {
    let ul = null;
    md.split('\n').forEach(raw => {
      const line = raw.trimEnd();
      let m;
      if (!line.trim()) { ul = null; return; }
      if ((m = line.match(/^#{1,4}\s+(.*)/))) { ul = null; box.appendChild(el('h4', null, m[1])); return; }
      if ((m = line.match(/^\s*(?:[-*\u2022]|\d+[.)])\s+(.*)/))) {
        if (!ul) { ul = el('ul'); box.appendChild(ul); }
        const li = el('li'); inline(li, m[1]); ul.appendChild(li); return;
      }
      ul = null; const p = el('p'); inline(p, line); box.appendChild(p);
    });
  }
  function coachPanel() {
    const c = el('div', 'an-card coach');
    const h = el('div', 'coach-head');
    h.appendChild(el('h3', null, 'Coach analysis'));
    const x = el('button', 'coach-x', '\u2715'); x.setAttribute('aria-label', 'Close'); x.onclick = () => { coach.state = 'idle'; build(last); };
    h.appendChild(x); c.appendChild(h);
    const body = el('div', 'coach-body');
    if (coach.state === 'loading') {
      body.appendChild(el('div', 'spin'));
      body.appendChild(el('p', 'dim', 'Reading your stats and recent hands. This can take up to a minute.'));
    } else if (coach.state === 'error') {
      body.appendChild(el('p', 'err', coach.text));
    } else {
      renderMarkdown(body, coach.text);
    }
    c.appendChild(body);
    if (coach.state === 'done') c.appendChild(el('p', 'coach-note',
      `AI-generated from your saved stats and last 30 hands (${coach.at}). Treat it as a study prompt, not a verdict - small samples are noisy and the equity numbers rest on assumed opponent ranges.`));
    return c;
  }
  async function runCoach() {
    coach = { state: 'loading', text: '', at: null }; build(last);
    try {
      const r = await fetch('/api/coach', { method: 'POST' });
      let d;
      try { d = await r.json(); } catch (e) {
        d = { ok: false, text: 'The server sent an unexpected reply (it may be running an older version). Stop it with Ctrl+C, run python3 server.py again, and reload this page.' };
      }
      coach = d.ok ? { state: 'done', text: d.text, at: new Date().toLocaleString() } : { state: 'error', text: d.text || 'The coach could not answer.', at: null };
    } catch (e) { coach = { state: 'error', text: 'Could not reach the server - check that python3 server.py is still running in your terminal.', at: null }; }
    build(last);
  }

  function insights(s) {
    const out = [];
    if (!s.hands) return [['info', 'No hands saved yet. Play a few with the tool and press "Save & next" - the charts below build up from there.']];
    if (s.hands < 30) out.push(['info', `Only ${s.hands} hands so far - treat every number here as a rough hint until you have a few hundred.`]);
    if (s.call_margins.length) {
      if (s.bad_calls) out.push(['bad', `${s.bad_calls} of ${s.call_margins.length} calls were below break-even` +
        ((BB() ? s.ev_bad_calls_bb : s.ev_bad_calls) < 0 ? ` - about ${num(Math.abs(BB() ? s.ev_bad_calls_bb : s.ev_bad_calls))} ${BB() ? 'big blinds' : 'chips'} of expected value given away.` : '.')]);
      else out.push(['good', `All ${s.call_margins.length} of your calls had the odds to be profitable.`]);
    }
    const eq = s.call_equity_roll.filter(v => v != null), rq = s.call_required_roll.filter(v => v != null);
    if (eq.length > 3) {
      const gap = eq[eq.length - 1] - rq[rq.length - 1], first = eq[0] - rq[0];
      out.push([gap >= 0 ? 'good' : 'bad', `Recently your calls have ${gap >= 0 ? 'beaten' : 'missed'} break-even by ${Math.abs(gap).toFixed(1)} points` +
        (eq.length > 8 ? ` (it was ${first >= 0 ? '+' : '−'}${Math.abs(first).toFixed(1)} at the start).` : '.')]);
    }
    const nk = BB() ? 'net_bb' : 'net';
    const pos = s.by_position.filter(p => (BB() ? p.hands_bb : p.hands) >= 3).sort((a, b) => a[nk] - b[nk]);
    if (pos.length >= 2) {
      const w = pos[0], b = pos[pos.length - 1];
      out.push(['info', `Best position: ${b.name} (${sv(b[nk])} over ${b.hands} hands). Worst: ${w.name} (${sv(w[nk])} over ${w.hands}).`]);
    }
    if (s.follow_rate != null && s.hands >= 10) out.push(['info', `You followed the tool's suggestion ${pct(s.follow_rate)} of the time.`]);
    return out.length ? out : [['info', 'Keep saving hands to unlock more insights.']];
  }

  function charts_(root, s) {
    const mk = (cv, cfg) => charts.push(new Chart(cv, cfg));

    root.appendChild(el('div', 'an-sec', 'Progress over time'));
    const g1 = el('div', 'an-grid'); root.appendChild(g1);

    // 1 total winnings: running total of each finished session's result, independent of balance or platform
    const win1 = BB() ? s.winnings_bb : s.winnings_chips;
    let cv = card(g1, 'Total winnings', BB() ? 'Big blinds won or lost, added up after each finished session, so different stakes are comparable.' : 'Money won or lost, added up after each finished session.',
      { wide: true, tall: true, empty: s.winnings_chips.length > 1 ? null : 'Finish a session to start the winnings line.' });
    mk(cv, { type: 'line', data: { labels: s.winnings_labels, datasets: [line('Winnings', win1, C.c1, { fill: 'origin', backgroundColor: wash(C.c1), endLabel: v => sv(r1(v)) })] },
      crosshair: true, plugins: [crosshair, endDots],
      options: base({ crosshair: true, scales: scales({ y: { ticks: { callback: v => compact(v), maxTicksLimit: 6 }, grace: '8%' } }),
        plugins: { legend: { display: false }, tooltip: tooltip({ callbacks: {
          label: c => ` Total: ${sv(r1(c.parsed.y))}`,
          footer: items => BB() ? `${signed(s.winnings_chips[items[0].dataIndex])} in money` : `${signed(r1(s.winnings_bb[items[0].dataIndex]))} big blinds` } }) } }) });

    // 2 cumulative result
    const cum = BB() ? s.cum_results_bb : s.cum_results;
    cv = card(g1, 'Running result', BB() ? 'Total big blinds won or lost across your saved hands, hand by hand.' : 'Total chips won or lost across your saved hands, hand by hand.',
      { empty: cum.length > 1 ? null : EMPTY });
    mk(cv, { type: 'line', data: { labels: cum.map((_, i) => i + 1), datasets: [line('Result', cum, C.c1, { fill: 'origin', backgroundColor: wash(C.c1), endLabel: v => sv(r1(v)) })] },
      plugins: [crosshair, endDots],
      options: base({ crosshair: true, scales: scales({ x: { title: { display: false } } }),
        plugins: { legend: { display: false }, tooltip: tooltip({ callbacks: { title: i => 'Hand ' + i[0].label, label: c => ' ' + signed(r1(c.parsed.y)) + uword() } }) } }) });

    // 3 rolling win rate
    const wrn = s.win_rate_roll.filter(v => v != null).length;
    cv = card(g1, 'Win rate, recent hands', `Share of your last 20 hands that you won. Overall: ${pct(s.win_rate)}.`,
      { empty: wrn > 1 ? null : 'Needs at least 5 hands with a result.' });
    mk(cv, { type: 'line', data: { labels: s.win_rate_roll.map((_, i) => i + 1), datasets: [line('Win rate', s.win_rate_roll, C.c1, { endLabel: v => v.toFixed(0) + '%' })] },
      plugins: [crosshair, endDots],
      options: base({ crosshair: true, scales: scales({ suffix: '%', y: { min: 0, max: 100, ticks: { callback: v => v + '%', stepSize: 25 } } }),
        plugins: { legend: { display: false }, tooltip: tooltip({ callbacks: { title: i => 'Hand ' + i[0].label, label: c => ` ${c.parsed.y.toFixed(0)}% of the last 20 won` } }) } }) });

    // 4 equity vs break-even
    const cen = s.call_equity_roll.filter(v => v != null).length;
    cv = card(g1, 'Call quality over time', 'Average equity when you called, against the equity you needed (last 10 calls). Equity above the needed line means profitable calls.',
      { wide: true, legend: [['Your equity when calling', C.c1], ['Equity needed to break even', C.c2]], empty: cen > 1 ? null : 'Needs at least 3 calls.' });
    mk(cv, { type: 'line', data: { labels: s.call_equity_roll.map((_, i) => i + 1), datasets: [
      line('Your equity', s.call_equity_roll, C.c1), line('Needed', s.call_required_roll, C.c2)] },
      plugins: [crosshair, endDots],
      options: base({ crosshair: true, scales: scales({ suffix: '%', y: { min: 0, ticks: { callback: v => v + '%' } } }),
        plugins: { legend: { display: false }, tooltip: tooltip({ callbacks: { title: i => 'Call ' + i[0].label,
          label: c => ` ${c.dataset.label}: ${c.parsed.y.toFixed(1)}%` } }) } }) });

    // 5 session results
    const sp = BB() ? s.session_pl_bb : s.session_pl;
    cv = card(g1, 'Result per session', BB() ? 'Big blinds won or lost in each finished session.' : 'Chips won or lost in each finished session.',
      { legend: [['Up session', C.pos, 'sq'], ['Down session', C.neg, 'sq']], empty: sp.length ? null : 'Finish a session to see it here.' });
    mk(cv, { type: 'bar', data: { labels: s.session_labels, datasets: [{ data: sp, maxBarThickness: 24, borderRadius: colRadius, borderSkipped: false,
      backgroundColor: sp.map(v => v >= 0 ? C.pos : C.neg), hoverBackgroundColor: sp.map(v => v >= 0 ? '#6da7ec' : '#ee8a8a') }] },
      options: base({ interaction: { mode: 'index', intersect: false }, scales: scales(),
        plugins: { legend: { display: false }, tooltip: tooltip({ displayColors: false, callbacks: { label: c => c.parsed.y == null ? '' : ' ' + signed(r1(c.parsed.y)) + uword(),
          afterLabel: c => { const p = s.per_session[c.dataIndex]; return p ? `${p.hands} hands saved` : ''; } } }) } }) });

    // 6 bb/100 per session
    const bbs = s.per_session.map(p => p.bb100);
    cv = card(g1, 'bb / 100 per session', 'Big blinds won per 100 hands in each session - comparable across stakes.',
      { legend: [['Up session', C.pos, 'sq'], ['Down session', C.neg, 'sq']], empty: bbs.some(v => v != null) ? null : 'Needs hands saved with a result and a big blind.' });
    mk(cv, { type: 'bar', data: { labels: s.per_session.map(p => p.label), datasets: [{ data: bbs, maxBarThickness: 24, borderRadius: colRadius, borderSkipped: false,
      backgroundColor: bbs.map(v => v >= 0 ? C.pos : C.neg) }] },
      options: base({ scales: scales(), plugins: { legend: { display: false }, tooltip: tooltip({ displayColors: false, callbacks: { label: c => c.parsed.y == null ? '' : ' ' + c.parsed.y.toFixed(1) + ' bb/100' } }) } }) });

    root.appendChild(el('div', 'an-sec', 'Decisions'));
    const g2 = el('div', 'an-grid'); root.appendChild(g2);

    // 7 call margin
    const cm = s.call_margins.map(m => m * 100);
    cv = card(g2, 'Every call you made', 'Equity minus the equity you needed. Above zero is a profitable call.',
      { wide: true, legend: [['✓ Profitable', C.good, 'sq'], ['✕ Below break-even', C.crit, 'sq']], empty: cm.length ? null : 'No calls saved yet.' });
    mk(cv, { type: 'bar', data: { labels: cm.map((_, i) => i + 1), datasets: [{ data: cm, maxBarThickness: 24, borderRadius: colRadius, borderSkipped: false,
      backgroundColor: s.call_margins.map(m => m < -0.02 ? C.crit : C.good) }] },
      options: base({ scales: scales({ suffix: ' pts', y: { ticks: { callback: v => v + '' } } }),
        plugins: { legend: { display: false }, tooltip: tooltip({ displayColors: false, callbacks: {
          title: i => s.call_labels[i[0].dataIndex],
          label: c => ` ${c.parsed.y >= 0 ? '+' : '−'}${Math.abs(c.parsed.y).toFixed(1)} points`,
          afterLabel: c => `Equity ${(s.call_equity[c.dataIndex] * 100).toFixed(0)}% vs ${(s.call_required[c.dataIndex] * 100).toFixed(0)}% needed` } }) } }) });

    // 8 actions
    const ac = Object.entries(s.action_counts).sort((a, b) => b[1] - a[1]);
    cv = card(g2, 'What you do', 'How often you chose each action.', { empty: ac.length ? null : 'No hands saved yet.' });
    mk(cv, { type: 'bar', data: { labels: ac.map(a => a[0]), datasets: [{ data: ac.map(a => a[1]), maxBarThickness: 24, borderRadius: barRadius, borderSkipped: false, backgroundColor: C.c1 }] },
      plugins: [tipValues], tipFormat: v => String(v),
      options: base({ indexAxis: 'y', tipFormat: v => String(v), layout: { padding: { top: 6, right: 44, bottom: 0, left: 0 } }, interaction: { mode: 'nearest', intersect: true, axis: 'y' },
        scales: { x: { grid: { color: C.hair }, border: { display: false }, ticks: { color: C.muted, precision: 0, maxTicksLimit: 5 }, beginAtZero: true },
                  y: { grid: { display: false }, border: { color: C.base }, ticks: { color: C.ink2 } } },
        plugins: { legend: { display: false }, tooltip: tooltip({ displayColors: false, callbacks: { label: c => ` ${c.parsed.x} hands` } }) } }) });

    // 9 advice vs result
    const hb0 = rows0 => {
      const rows = rows0.filter(r => !BB() || r.avg_bb != null).map(r => Object.assign({}, r, { v: BB() ? r.avg_bb : r.avg }));
      const cv2 = card(g2, 'Following the advice', (BB() ? 'Average big blinds' : 'Average chips') + ' per hand when you did what the tool suggested, against when you did something else.',
        { legend: [['Won', C.pos, 'sq'], ['Lost', C.neg, 'sq']], empty: rows.length ? null : 'Needs hands saved with a result.' });
      mk(cv2, { type: 'bar', data: { labels: rows.map(r => r.name), datasets: [{ data: rows.map(r => r.v), maxBarThickness: 24, borderRadius: barRadius, borderSkipped: false,
        backgroundColor: rows.map(r => r.v >= 0 ? C.pos : C.neg) }] },
        plugins: [tipValues],
        options: base({ indexAxis: 'y', tipFormat: v => signed(r1(v)), layout: { padding: { top: 6, right: 64, bottom: 0, left: 8 } }, interaction: { mode: 'nearest', intersect: true, axis: 'y' },
          scales: { x: { grid: { color: c => c.tick && c.tick.value === 0 ? C.base : C.hair }, border: { display: false }, ticks: { color: C.muted, maxTicksLimit: 5, callback: v => compact(v) } },
                    y: { grid: { display: false }, border: { color: C.base }, ticks: { color: C.ink2 } } },
          plugins: { legend: { display: false }, tooltip: tooltip({ displayColors: false, callbacks: { label: c => ' ' + signed(r1(c.parsed.x)) + uword() + ' per hand', afterLabel: c => `${rows[c.dataIndex].hands} hands` } }) } }) });
    };
    hb0(s.advice_split);

    root.appendChild(el('div', 'an-sec', 'Where you win and lose'));
    const g3 = el('div', 'an-grid'); root.appendChild(g3);
    const hb = (parent, title, desc, rows) => {
      const nk2 = BB() ? 'net_bb' : 'net';
      const cv2 = card(parent, title, desc, { legend: [['Won', C.pos, 'sq'], ['Lost', C.neg, 'sq']], empty: rows.length ? null : 'Needs hands saved with a result and position.' });
      mk(cv2, { type: 'bar', data: { labels: rows.map(r => r.name), datasets: [{ data: rows.map(r => r[nk2]), maxBarThickness: 24, borderRadius: barRadius, borderSkipped: false,
        backgroundColor: rows.map(r => r[nk2] >= 0 ? C.pos : C.neg) }] },
        plugins: [tipValues],
        options: base({ indexAxis: 'y', tipFormat: v => signed(r1(v)), layout: { padding: { top: 6, right: 64, bottom: 0, left: 8 } }, interaction: { mode: 'nearest', intersect: true, axis: 'y' },
          scales: { x: { grid: { color: c => c.tick && c.tick.value === 0 ? C.base : C.hair }, border: { display: false }, ticks: { color: C.muted, maxTicksLimit: 5, callback: v => compact(v) } },
                    y: { grid: { display: false }, border: { color: C.base }, ticks: { color: C.ink2 } } },
          plugins: { legend: { display: false }, tooltip: tooltip({ displayColors: false, callbacks: { label: c => ' ' + signed(r1(c.parsed.x)) + uword(), afterLabel: c => `${rows[c.dataIndex].hands} hands` } }) } }) });
    };
    const nm = BB() ? 'big blinds' : 'chips';
    hb(g3, 'By position', `Net ${nm} from each seat. Small samples swing a lot.`, s.by_position);
    hb(g3, 'By street', `Net ${nm} by the street the hand ended on.`, s.by_street);
  }

  /* ---------- table view (every chart's numbers, no hover needed) ---------- */
  function tables(root, s) {
    const wrap = el('div', 'an-tables'); root.appendChild(wrap);
    const tbl = (title, headers, rows, scroll) => {
      const c = el('div', 'an-card'); c.appendChild(el('h3', null, title)); c.appendChild(el('p', 'desc', ''));
      const sc = el('div', scroll ? 'scroll' : ''); const t = el('table'); const hr = el('tr');
      headers.forEach(h => hr.appendChild(el('th', null, h))); const th = el('thead'); th.appendChild(hr); t.appendChild(th);
      const tb = el('tbody');
      if (!rows.length) { const r = el('tr'); const d = el('td', null, 'No data yet'); d.colSpan = headers.length; r.appendChild(d); tb.appendChild(r); }
      rows.forEach(r => { const tr = el('tr'); r.forEach(v => tr.appendChild(el('td', null, v == null ? '' : String(v)))); tb.appendChild(tr); });
      t.appendChild(tb); sc.appendChild(t); c.appendChild(sc); wrap.appendChild(c);
    };
    tbl('Total winnings', ['Point', 'Chips', 'Big blinds'],
      s.winnings_chips.map((v, i) => [s.winnings_labels[i], signed(v), signed(Math.round(s.winnings_bb[i] * 10) / 10)]));
    tbl('Sessions', ['Ended', 'Result', 'Result (bb)', 'Hands', 'bb/100', 'Win rate'],
      s.per_session.map((p, i) => [p.label, signed(p.pl), s.session_pl_bb[i] == null ? '' : signed(r1(s.session_pl_bb[i])), p.hands, p.bb100 == null ? '' : p.bb100.toFixed(1), pct(p.win_rate)]));
    tbl('Running result and win rate', ['Hand', 'Running result', 'Running result (bb)', 'Win rate (last 20)'],
      s.cum_results.map((v, i) => [i + 1, signed(v), signed(r1(s.cum_results_bb[i])), s.win_rate_roll[i] == null ? '' : s.win_rate_roll[i].toFixed(0) + '%']), true);
    tbl('Calls', ['#', 'Hand', 'Equity', 'Needed', 'Margin'],
      s.call_margins.map((m, i) => [i + 1, s.call_labels[i], pct(s.call_equity[i], 1), pct(s.call_required[i], 1), (m >= 0 ? '+' : '−') + Math.abs(m * 100).toFixed(1)]), true);
    tbl('Actions', ['Action', 'Times'], Object.entries(s.action_counts));
    tbl('Following the advice', ['', 'Hands', 'Net chips', 'Avg per hand', 'Net bb', 'Avg bb per hand'],
      s.advice_split.map(p => [p.name, p.hands, signed(p.net), signed(p.avg), signed(r1(p.net_bb)), p.avg_bb == null ? '' : signed(r1(p.avg_bb))]));
    tbl('By position', ['Position', 'Hands', 'Net chips', 'Net bb'], s.by_position.map(p => [p.name, p.hands, signed(p.net), signed(r1(p.net_bb))]));
    tbl('By street', ['Street', 'Hands', 'Net chips', 'Net bb'], s.by_street.map(p => [p.name, p.hands, signed(p.net), signed(r1(p.net_bb))]));
  }

  async function load() {
    const root = $('anRoot'); root.classList.add('loading');
    try {
      last = await (await fetch('/api/analytics' + (stake != null ? '?stake=' + encodeURIComponent(stake) : ''))).json();
      build(last);
    } finally { root.classList.remove('loading'); }
  }
  window.loadAnalytics = load;
})();
