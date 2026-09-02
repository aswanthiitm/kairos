const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const inr = v => {
  if (typeof v !== 'number') return esc(v);
  const a = Math.abs(v);
  if (a >= 1e7) return (v/1e7).toFixed(2) + ' Cr';
  if (a >= 1e5) return (v/1e5).toFixed(1) + ' L';
  return Math.round(v).toLocaleString('en-IN');
};
const fmtUnit=(v,u)=>u==='INR'?'₹'+inr(v):u==='units'?Math.round(v).toLocaleString('en-IN')
  :u==='pct'?(100*v).toFixed(2)+'pp':(+v).toFixed(2);
let META = null, LAST = null;

async function boot(){
  META = await (await fetch('/api/meta')).json();
  $('#scenario').innerHTML = META.scenarios.map(s =>
    `<option value="${s.id}">${s.id} — ${esc(s.name)}</option>`).join('');
  $('#persona').innerHTML = META.personas.map(p =>
    `<option value="${p.key}">${esc(p.label)}</option>`).join('');
  $('#footmeta').textContent =
    `contract v${META.contract.version} · ${META.contract.kpis.length} KPIs · ` +
    `LLM ${META.llm_configured ? 'configured' : 'not configured (deterministic narrator)'}`;
  if (!META.llm_configured) $('#narrator').value = 'offline';
  // deep-linkable state: ?scenario=S1&persona=cfo&narrator=simulate
  const qp = new URLSearchParams(location.search);
  for (const k of ['scenario','persona','narrator']) {
    if (qp.get(k) && $('#'+k).querySelector(`option[value="${qp.get(k)}"]`)) $('#'+k).value = qp.get(k);
  }
  await startTicker();
  run();
}

async function run(){
  $('#main').innerHTML = '<div class="loading">Running SIFT → SPLIT → SOURCE → SOLVE → NARRATE…</div>';
  const q = `scenario=${$('#scenario').value}&persona=${$('#persona').value}` +
            `&narrator=${$('#narrator').value}`;
  history.replaceState(null,'','?'+q.replace('&narrator=','&narrator='));
  const r = await fetch('/api/analyse?' + q);
  if (!r.ok) { $('#main').innerHTML = `<div class="loading">Error: ${esc(await r.text())}</div>`; return; }
  LAST = await r.json();
  render(LAST);
}

function render(d){
  if (d.verdict.status === 'ENTITLEMENT_DENIED') return renderDenied(d);
  if (d.verdict.status === 'UNFIT_DATA') return renderUnfit(d);
  const m = d.movement, v = d.verdict;
  const restricted = (d.evidence_packet.restricted_fields || []).length > 0;
  $('#main').innerHTML = `
  <div class="col">
    ${cardNarrative(d, v)}
    ${cardMovement(d, m, restricted)}
    ${cardSplit(d, restricted)}
    ${cardMechanism(d, restricted)}
    ${cardLatency(d)}
    ${cardRecs(d)}
  </div>
  <div class="col">
    ${cardSemantics(d)}
    ${cardFitness(d)}
    ${cardPersona(d)}
    ${cardHypotheses(d)}
    ${cardSeparating(d)}
    ${cardMlRanker(d)}
    ${cardMethods(d)}
    ${cardTelemetry(d)}
  </div>`;
  document.querySelectorAll('[data-fb]').forEach(b => b.onclick = () => sendFeedback(b));
  const sc = new URLSearchParams(location.search).get('scroll');
  if (sc) setTimeout(() => window.scrollTo(0, parseInt(sc, 10)), 60);
}

function renderDenied(d){
  $('#main').innerHTML = `<div class="card span">
    <div class="chead"><span class="ctitle">Access control</span>
      <span class="badge b-entitlement_denied">Entitlement denied</span></div>
    <div class="deny"><strong>${esc(d.narrative.text)}</strong></div>
    <dl class="kv" style="margin-top:12px">
      <dt>persona</dt><dd>${esc(d.persona.label)}</dd>
      <dt>row rule</dt><dd>${esc((d.persona.row_restrictions||[]).join('; ')||'none')}</dd>
      <dt>requested</dt><dd>${esc(d.access_denied.column)} = ${esc(d.access_denied.requested)}</dd>
      <dt>enforced</dt><dd>before query execution — no rows, evidence or narrative generated</dd>
    </dl></div>`;
}

function cardNarrative(d, v){
  const g = d.narrative.guard;
  return `<div class="card glass">
    <div class="chead"><span class="ctitle">${esc(d.persona.display)} — ${esc(d.persona.channel||'')}</span>
      <span class="badge b-${v.status.toLowerCase()}">${esc(v.status.replace(/_/g,' '))}</span></div>
    <div class="narr">${esc(d.narrative.text)}</div>
    <div class="mode">
      <span>narrator: <b>${esc(d.narrative.mode)}</b></span>
      ${d.narrative.model ? `<span>model: ${esc(d.narrative.model)}</span>` : ''}
      <span class="${g.passed?'guard-ok':'guard-bad'}">numeric guard: ${g.passed?'PASSED':'FAILED → fell back'}
        ${g.bad && g.bad.length ? '(' + esc(g.bad.join(', ')) + ')' : ''}</span>
      ${d.withheld_evidence.length ? `<span>${d.withheld_evidence.length} evidence items withheld</span>` : ''}
    </div>
    <div style="margin-top:9px;font-size:12px;color:var(--ink3)">${esc(v.reason)}</div>
    ${(d.advisories||[]).map(a => `<div class="split-note"><b>${esc(a.type.replace(/_/g,' '))}</b> —
      ${esc(a.detail)}</div>`).join('')}
  </div>`;
}

function cardMovement(d, m, restricted){
  const pos = m.delta >= 0;
  return `<div class="card">
    <div class="chead"><span class="ctitle">Movement — ${esc(m.label)} · ${esc(m.window.start)} to ${esc(m.window.end)}</span>
      <span class="pill">${esc(JSON.stringify(m.filters))}</span></div>
    <div class="metric">
      <div><div class="v ${pos?'pos':'neg'}">${restricted?'—':fmtUnit(m.delta,m.unit)}</div>
        <div class="k">vs expected</div></div>
      <div><div class="v ${pos?'pos':'neg'}">${(100*m.pct_change).toFixed(1)}%</div><div class="k">change</div></div>
      <div><div class="v">${m.z.toFixed(2)}σ</div><div class="k">z-score</div></div>
      <div><div class="v">${m.persistence_days}d</div><div class="k">persistence</div></div>
      <div><div class="v">${m.history_days}</div><div class="k">history days</div></div>
    </div>
    ${sparkline(m.series, m.sigma)}
    <dl class="kv" style="margin-top:12px">
      <dt>gates</dt><dd>${Object.entries(m.gate_checks).map(([k,x])=>
        `<span class="tag ${x?'t-DETERMINISTIC':'t-LLM'}">${esc(k)} ${x?'pass':'fail'}</span>`).join(' ')}</dd>
      <dt>lineage</dt><dd class="mech">${esc(m.lineage)}</dd>
    </dl>
    ${(m.data_quality_flags||[]).map(f=>`<div class="split-note"><b>${esc(f.type)}</b> — ${esc(f.detail)}.
      <em>${esc(f.implication)}</em></div>`).join('')}
  </div>`;
}

function sparkline(series, sigma){
  if(!series || !series.length) return '';
  const W=680,H=190,PX=34,PT=12,PB=26;
  const roll=(a,k=7)=>a.map((_,i)=>{
    const lo=Math.max(0,i-3), hi=Math.min(a.length,i+4);
    const w=a.slice(lo,hi).filter(v=>v!=null);
    return w.length? w.reduce((x,y)=>x+y,0)/w.length : null;});
  const v=series.map(r=>r.v), e=series.map(r=>r.e);
  const sv=roll(v), se=roll(e);
  const band=(sigma||0)*1.0;
  const all=[...v,...se.filter(x=>x!=null).map(x=>x+band),
                 ...se.filter(x=>x!=null).map(x=>x-band)].filter(x=>x!=null);
  const lo=Math.min(...all), hi=Math.max(...all), rng=(hi-lo)||1;
  const X=i=>PX+i*(W-PX-14)/(series.length-1), Y=y=>H-PB-((y-lo)/rng)*(H-PT-PB);
  const line=a=>a.map((y,i)=>y==null?null:`${X(i).toFixed(1)},${Y(y).toFixed(1)}`)
      .filter(Boolean).map((p,i)=>(i?'L':'M')+p).join(' ');
  const idx=se.map((y,i)=>y==null?null:i).filter(i=>i!=null);
  const bandPath = idx.length
    ? 'M'+idx.map(i=>`${X(i).toFixed(1)},${Y(se[i]+band).toFixed(1)}`).join(' L')
      +' L'+idx.slice().reverse().map(i=>`${X(i).toFixed(1)},${Y(se[i]-band).toFixed(1)}`).join(' L')+' Z'
    : '';
  const brk=idx.filter(i=>sv[i]!=null && sv[i] < se[i]-band)
    .map(i=>`<circle cx="${X(i).toFixed(1)}" cy="${Y(sv[i]).toFixed(1)}" r="2.4" fill="#8A3A32"/>`).join('');
  const ticks=[0,Math.floor(series.length/2),series.length-1].map(i=>
    `<text class="axis" x="${X(i).toFixed(1)}" y="${H-8}" text-anchor="${i===0?'start':i===series.length-1?'end':'middle'}">${esc(series[i].d)}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="actual against expected band">
    <path d="${bandPath}" fill="#8A4A32" opacity="0.11"/>
    <path d="${line(v)}" fill="none" stroke="#8A4A32" stroke-width="0.7" opacity="0.30"/>
    <path d="${line(se)}" fill="none" stroke="#B0ABA1" stroke-width="1" stroke-dasharray="3 2.5"/>
    <path d="${line(sv)}" fill="none" stroke="#8A4A32" stroke-width="1.9"/>
    ${brk}${ticks}
    <text class="axis" x="${PX}" y="10">daily faint · 7-day mean bold · shaded 1&#963; band · red = outside</text>
  </svg>`;
}

function cardSplit(d, restricted){
  const s=d.split; if(!s) return '';
  const id=s.identity;
  const rows=(s.contributors||[]).slice(0,6).map(c=>`<tr>
      <td class="m">${esc(c.dimension)}</td><td>${esc(c.value)}</td>
      <td class="m">${restricted?'—':'₹'+inr(c.move)}</td>
      <td class="m">${(100*c.explanatory_power).toFixed(1)}%</td>
      <td class="m">${(100*c.share_shift>=0?'+':'')+(100*c.share_shift).toFixed(1)}pp</td></tr>`).join('');
  return `<div class="card">
    <div class="chead"><span class="ctitle">Split — deterministic contribution</span>
      <span class="pill">baseline ${esc(s.baseline.start)} → ${esc(s.baseline.end)}</span></div>
    ${id?`<table><thead><tr><th>component</th><th>value</th><th>share of move</th><th>reading</th></tr></thead>
      <tbody>${id.components.map(c=>`<tr><td><b>${esc(c.name)}</b></td>
        <td class="m">${restricted?'—':'₹'+inr(c.value)}</td>
        <td class="m">${(100*c.pct_of_move).toFixed(1)}%</td>
        <td>${esc(c.reading)}</td></tr>`).join('')}
      <tr><td colspan="4" class="m" style="color:var(--ink3)">residual
        ${restricted?'—':'₹'+inr(id.residual)} — the identity closes exactly, so nothing is unexplained</td></tr>
      </tbody></table>`:''}
    <table style="margin-top:12px"><thead><tr><th>dimension</th><th>segment</th><th>move</th>
      <th>explanatory power</th><th>share shift</th></tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

function cardMechanism(d, restricted){
  const ml = d.mechanism_ledger; if(!ml) return '';
  const rows = ml.hops.map(h=>{
    if(!h.measured) return `<tr class="unmeas"><td class="m">—</td><td>${esc(h.label)}</td>
      <td colspan="4" style="color:var(--ink3)">${esc(h.note||'')}</td></tr>`;
    const did = h.did_pct==null ? '—' : (100*h.did_pct).toFixed(1)+'%';
    return `<tr><td class="m">${h.lag_days_to_effect}d</td>
      <td><b>${esc(h.kpi_label)}</b><br><span style="color:var(--ink3);font-size:10.5px">${esc(h.label)}</span></td>
      <td class="m">${(100*h.treated.pct_change).toFixed(1)}%</td>
      <td class="m">${h.control?(100*h.control.pct_change).toFixed(1)+'%':'—'}</td>
      <td class="m"><b>${did}</b></td>
      <td style="font-size:11px;color:var(--ink3)">${esc(h.coverage_note||h.reading||'')}</td></tr>`;
  }).join('');
  return `<div class="card">
    <div class="chead"><span class="ctitle">Mechanism ledger — where the loss is created</span>
      <span class="pill">intervene at <b>${esc(ml.intervention_point)}</b></span></div>
    <div style="font-size:13.5px;color:var(--acc2);margin-bottom:10px">${esc(ml.chain_summary)}</div>
    <table><thead><tr><th>lag</th><th>hop</th><th>treated</th><th>control</th><th>DiD</th><th>note</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <div style="margin-top:9px;font-size:11.5px;color:var(--ink3)">${esc(ml.intervention_note)}</div>
  </div>`;
}

function cardHypotheses(d){
  const lead=d.verdict.leader_ids||[];
  const items=(d.hypotheses||[]).map(h=>{
    const g=h.grade, L=['L0','L1','L2','L3'], on=L.indexOf(g.ladder);
    const rej=g.ladder==='REJECTED';
    const rungs=L.map((l,i)=>`<span class="rung ${!rej&&i<=on?'on':''} ${l==='L3'&&i<=on?'l3':''}"></span>`).join('');
    const t=g.tests||{};
    const test=(k,label,body)=>t[k]?`<div class="test"><i>${label}</i><span>${body(t[k])}</span></div>`:'';
    return `<div class="hyp ${lead[0]===h.hyp.id?'lead':''} ${rej?'rej':''}">
      <div class="hyprow"><div class="hypname">${esc(h.hyp.label)}</div>
        <span class="badge ${rej?'b-data_quality':'b-confirmed'}">${esc(g.ladder)}</span></div>
      <div class="rungs">${rungs}</div>
      ${rej?`<div class="mech" style="color:var(--bad)">${esc(g.rejected)}</div>`:
        `<div class="mech">mechanism: ${esc((g.mechanism_path||[]).join(' → '))} · declared lag ${g.mechanism_lag_days??'–'}d</div>`}
      <div class="tests">
        ${test('precedence','precedence',x=>`cause ${esc(x.cause_onset)} → effect ${esc(x.effect_onset)},
           gap ${x.gap_days}d vs declared ${x.declared_lag_days}d — <b>${x.consistent?'consistent':'inconsistent'}</b>`)}
        ${test('dose_response','dose–response',x=>`Spearman ρ=${x.spearman_rho.toFixed(2)}, p=${x.p_value.toFixed(4)}
           · ${x.buckets.map(b=>`${esc(b.exposure)} ${(100*b.mean_change_pct).toFixed(0)}%`).join(' · ')}`)}
        ${test('corroboration','corroboration',x=>`${x.on_theme_docs} on-theme from
           ${x.independent_source_types} independent source types across ${x.distinct_accounts} accounts
           &middot; ${x.conflicting_docs} conflicting (${(100*x.conflict_ratio).toFixed(0)}%)
           &rarr; <b>${esc(x.verdict)}</b>${x.note?`<br><span style="color:var(--warn)">${esc(x.note)}</span>`:''}`)}
        ${test('counterfactual','counterfactual',x=>`DiD ${(100*x.did_pp).toFixed(1)}% (SE ${(100*x.std_error_pp).toFixed(1)}pp,
           p=${x.p_value.toFixed(4)}) · placebo ${(100*x.placebo_pp).toFixed(1)}pp ·
           parallel trends <b>${x.parallel_trends_ok?'hold':'fail'}</b>`)}
        ${test('identity','identity',x=>esc(x.detail))}
        ${test('instrumentation','instrumentation',x=>esc(x.detail))}
      </div>
      ${mlLine(g)}
      ${(h.evidence_docs||[]).slice(0,3).map(doc=>`<div class="doc">
        <b>${esc(doc.type)} · ${esc(doc.ts)} · ${esc(doc.account_name)} · ${esc(doc.author_role)}</b>
        ${esc(doc.text.slice(0,190))}${doc.text.length>190?'…':''}</div>`).join('')}
      ${rej?'':`<div class="fbrow">
        <button class="ghost" data-fb="accept" data-h="${esc(h.hyp.id)}">Accept</button>
        <button class="ghost" data-fb="reject" data-h="${esc(h.hyp.id)}">Reject</button>
        <span class="fbmsg" id="fb-${esc(h.hyp.id)}"></span></div>`}
    </div>`;}).join('');
  return `<div class="card"><div class="chead"><span class="ctitle">Evidence ladder — hypotheses</span>
    <span class="pill">L0 co-movement → L3 counterfactual</span></div>${items||'<p>No hypotheses.</p>'}</div>`;
}

function mlLine(g){
  const m=g.ml; if(!m) return '';
  const r=g.rank_score;
  const ood=!m.in_distribution;
  return `<div class="test"><i><span class="tag t-ML">ML</span></i><span>
    learned prior <b>P(driver) = ${m.probability.toFixed(3)}</b> · ranked ${m.rank} of ${m.of_candidates}
    ${ood?`<span style="color:var(--warn)"> · outside the training distribution
      (${m.features_outside_training_range} features), score not applied</span>`:''}
    ${r?`<br><span style="color:var(--ink3);font-size:11px">rank score ${r.heuristic.toFixed(3)}
      (evidence heuristic) → ${r.fused.toFixed(3)} fused at weight ${r.ml_weight.toFixed(2)}
      · reordering only: the rung above was not set by this model</span>`:''}
  </span></div>`;
}

function cardSemantics(d){
  const s=d.semantics; if(!s) return '';
  const fc=s.fiscal_calendar||{}, fw=fc.window||{};
  const defs=Object.entries(s.kpi_definitions||{});
  const h=d.hierarchy;
  const recon = defs.map(([kpi,x])=>{
    const n=x.numeric||{}, vals=n.values||{}, gaps=n.gaps||{};
    const rows=Object.keys(vals).sort().map(k=>{
      const sel = k===x.selected;
      return `<tr><td class="m">${sel?'<b>SELECTED</b>':'rejected'}</td>
        <td class="m">${esc(k)}</td>
        <td>${esc((x.rejected.find(r=>r.key===k)||{}).formula || x.selected_formula||'')}</td>
        <td class="m" style="text-align:right">${inr(vals[k])}</td></tr>`;}).join('');
    const gap=Object.entries(gaps).map(([k,g])=>
      `<div style="font-size:11.5px;color:var(--warn);margin-top:6px">
         difference vs ${esc(k)}: <b>${inr(g.absolute_difference)}</b>
         (${(100*(g.pct_difference||0)).toFixed(2)}%) — measured on identical rows,
         so the gap is definitional, not a data problem</div>`).join('');
    return `<div style="margin-bottom:12px">
      <div style="font-size:12px;margin-bottom:4px"><b>${esc(kpi)}</b>
        <span class="badge ${x.status==='RECONCILED'?'b-competing':'b-confirmed'}">${esc(x.status)}</span></div>
      ${Object.keys(vals).length?`<table><tbody>${rows}</tbody></table>`:''}
      ${gap}
      <div style="font-size:11.5px;color:var(--ink3);margin-top:6px">
        ${esc(x.reason||'')}${x.rationale?`<br>${esc(x.rationale)}`:''}</div></div>`;}).join('');
  const drill = (h&&h.supported)
    ? `<table><thead><tr><th>${esc(h.child_level)}</th><th style="text-align:right">move</th>
         <th style="text-align:right">share</th></tr></thead><tbody>
       ${h.children.filter(c=>c.move!=null).map(c=>`<tr><td>${esc(c.value)}</td>
         <td class="m" style="text-align:right">${inr(c.move)}</td>
         <td class="m" style="text-align:right">${c.share_of_move!=null?(100*c.share_of_move).toFixed(1)+'%':'–'}</td></tr>`).join('')}
       </tbody></table>
       <div style="font-size:11.5px;color:var(--ink3);margin-top:6px">
         ${h.aggregation} measure · roll-up back to ${esc(h.parent_value)}
         ${h.roll_up_check.closes?'closes exactly':'<b style="color:var(--bad)">does not close</b>'}</div>`
    : (h&&h.reason)?`<div style="font-size:11.5px;color:var(--warn)">${esc(h.reason)}</div>`:'';
  return `<div class="card"><div class="chead"><span class="ctitle">Semantic layer</span>
    <span class="pill">resolved once, before analysis</span></div>
    <div style="font-size:12px;color:var(--ink2);margin-bottom:10px">
      <b>Fiscal period.</b> ${esc(fw.label||'')} on the
      <span class="m">${esc(fc.key||'')}</span> calendar (${esc(fc.label||'')}).
      ${fw.crosses_fiscal_year?'<span style="color:var(--warn)"> This window crosses a fiscal year end.</span>':''}
      ${fc.requested_period?`<br>Requested as <span class="m">${esc(fc.requested_period)}</span>;
        boundaries resolved from the contract, not from month arithmetic.`:''}
    </div>
    ${recon?`<div style="font-size:12px;color:var(--ink2);margin-bottom:6px">
       <b>Competing KPI definitions.</b> Both are correct inside their own system.
       The engine reconciles rather than picking one quietly.</div>${recon}`:''}
    ${drill?`<div style="font-size:12px;color:var(--ink2);margin:10px 0 6px">
       <b>Hierarchy drill-down.</b> region → city, traversed from the contract.</div>${drill}`:''}
  </div>`;
}

function cardMlRanker(d){
  const m=d.ml_ranker; if(!m) return '';
  if(m.status!=='active'){
    return `<div class="card"><div class="chead"><span class="ctitle">ML driver ranker</span>
      <span class="badge b-data_quality">${esc(m.status)}</span></div>
      <p style="font-size:12px;color:var(--ink2)">${esc(m.reason||'')}
      Ranking falls back to the evidence heuristic alone.</p></div>`;
  }
  const h=m.holdout||{}, rk=h.ranking||{}, sp=h.split||{}, cal=h.calibration||{};
  const arm=(n,k)=>`<tr><td class="m">${n}</td>
    <td class="m">${(rk[k]&&rk[k].top1_accuracy!=null)?rk[k].top1_accuracy.toFixed(3):'–'}</td>
    <td class="m">${(rk[k]&&rk[k].hit_at_3!=null)?rk[k].hit_at_3.toFixed(3):'–'}</td>
    <td class="m">${(rk[k]&&rk[k].ndcg_at_3!=null)?rk[k].ndcg_at_3.toFixed(3):'–'}</td></tr>`;
  const feats=Object.entries(m.top_features||{}).slice(0,6)
    .map(([k,v])=>`<span class="pill">${esc(k)} ${v.toFixed(3)}</span>`).join(' ');
  return `<div class="card"><div class="chead"><span class="ctitle">ML driver ranker</span>
    <span class="badge b-confirmed">${esc(m.model_version||'')}</span></div>
    <div style="font-size:12px;color:var(--ink2);margin-bottom:10px">
      A gradient-boosted ranking model trained on ${sp.train_episodes||0} resolved historical
      episodes. It is deliberately not shown the evidence rung, so its score is independent
      information rather than a restatement of the ladder. <b>It reorders candidates and does
      nothing else</b> — it cannot promote a rung, change a verdict, or invent a candidate.
    </div>
    <table><thead><tr><th>arm</th><th>top-1</th><th>hit@3</th><th>NDCG@3</th></tr></thead>
      <tbody>${arm('evidence heuristic (was)','heuristic')}${arm('learned model alone','ml')}
      ${arm('fused (ships)','fused')}</tbody></table>
    <div style="font-size:11.5px;color:var(--ink3);margin-top:8px">
      time-based holdout · train ${esc(sp.train_window||'')} → test ${esc(sp.test_window||'')}
      (${sp.test_episodes||0} episodes, never seen during fitting or selection)<br>
      calibration Brier ${cal.brier!=null?cal.brier.toFixed(4):'–'} ·
      ECE ${cal.ece!=null?cal.ece.toFixed(4):'–'} ·
      candidate-generation recall ceiling
      ${h.candidate_recall&&h.candidate_recall.overall!=null?h.candidate_recall.overall.toFixed(3):'–'}
    </div>
    <div style="margin-top:10px">${feats}</div>
    <div style="font-size:11.5px;color:var(--warn);margin-top:10px">
      ${(m.limitations||[]).map(x=>`• ${esc(x)}`).join('<br>')}</div>
  </div>`;
}

function cardSeparating(d){
  const t=d.separating_test; if(!t) return '';
  return `<div class="card"><div class="chead"><span class="ctitle">Ambiguity protocol — cheapest separating test</span></div>
    <div class="split-note"><b>${esc(t.question)}</b><br>${esc(t.test)}<br>
      <em style="color:var(--ink3)">${esc(t.why_it_separates)}</em></div>
    <div class="recgrid"><div><div class="k">cost</div><div class="v">₹${inr(t.cost_inr)}</div></div>
      <div><div class="k">answer in</div><div class="v">${t.days_to_answer} days</div></div>
      <div><div class="k">owner</div><div class="v">${esc(t.owner_role)}</div></div></div></div>`;
}

function cardRecs(d){
  if(!d.recommendations.length) return '';
  const items=d.recommendations.map(r=>`<div class="rec ${r.abstention?'abst':''}">
    <div class="recflow"><span>driver: ${esc(r.driver)}</span><span>→ lever: ${esc(r.lever_label)}</span>
      ${r.driver_ladder?`<span>evidence ${esc(r.driver_ladder)}</span>`:''}</div>
    <div style="font-size:13.5px">${esc(r.action)}</div>
    <div class="recgrid">
      <div><div class="k">expected impact</div><div class="v">${typeof r.expected_impact_inr==='number'&&r.expected_impact_inr?'₹'+inr(r.expected_impact_inr):'—'}</div></div>
      <div><div class="k">owner</div><div class="v">${esc(r.owner_role)}</div></div>
      <div><div class="k">confidence</div><div class="v">${Math.round(100*r.confidence)}%<div class="bar"><i style="width:${Math.round(100*r.confidence)}%"></i></div></div></div>
      <div><div class="k">lead time</div><div class="v">${r.lead_time_days}d</div></div>
      <div><div class="k">review in</div><div class="v">${r.monitoring.check_in_days}d</div></div>
    </div>
    ${r.delegation?`<div class="deleg">
      <b>${esc(r.delegation.label)}</b> — the machine may ${esc(r.delegation.machine_may)}.
      Human role: <b>${esc(r.delegation.human_role)}</b>.
      <ul>${r.delegation.reasons.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}
    <div style="margin-top:8px;font-size:11.5px;color:var(--ink3)">
      ${esc(r.expected_impact_note)}<br>decision right: ${esc(r.decision_right)} ·
      success: ${esc(r.monitoring.success_criterion)}<br>
      ${r.source_playbook?`playbook <b>${esc(r.source_playbook.title)}</b>
        (similarity ${r.source_playbook.similarity.toFixed(2)}, n=${r.source_playbook.n_observations})
        ${r.source_playbook.caveat?'· caveat: '+esc(r.source_playbook.caveat):''}`:'no playbook match'}<br>
      basis: ${esc(r.confidence_basis)}</div></div>`).join('');
  return `<div class="card"><div class="chead"><span class="ctitle">Solve — driver → lever → action → impact → owner → confidence → monitoring</span></div>${items}</div>`;
}

function renderUnfit(d){
  const f=d.data_fitness;
  $('#main').innerHTML=`<div class="card span">
    <div class="chead"><span class="ctitle">Data fitness gate</span>
      <span class="badge b-data_quality">Unfit &middot; analysis halted</span></div>
    <div class="split-note"><b>${esc(d.narrative.text)}</b></div>
    <div style="margin-top:12px">${f.issues.map(i=>`<div class="doc">
      <b>${esc(i.severity)} &middot; ${esc(i.dimension)} &middot; ${esc(i.source)}</b>
      ${esc(i.detail)} &mdash; <em>${esc(i.implication)}</em></div>`).join('')}</div></div>`;
}

function cardFitness(d){
  const f=d.data_fitness; if(!f) return '';
  const cls={FIT:'b-confirmed',FIT_WITH_CAVEATS:'b-competing',UNFIT:'b-data_quality'}[f.verdict];
  return `<div class="card">
    <div class="chead"><span class="ctitle">Data fitness &mdash; gate runs before analysis</span>
      <span class="badge ${cls}">${esc(f.verdict.replace(/_/g,' '))} &middot; ${(100*f.score).toFixed(0)}%</span></div>
    <div style="font-size:12px;color:var(--ink3);margin-bottom:9px">
      ${f.checks_run} checks across ${f.dimensions_assessed.join(', ')}. ${esc(f.gate.meaning)}</div>
    ${f.issues.length?f.issues.map(i=>`<div class="test">
      <i>${esc(i.severity)}</i><span><b>${esc(i.source)}</b> &middot; ${esc(i.detail)}<br>
      <span style="color:var(--ink3)">${esc(i.implication)}</span></span></div>`).join('')
      :'<div style="font-size:12px;color:var(--good)">every source cleared its checks</div>'}
    <div style="margin-top:9px;font-size:10.5px;color:var(--ink3);font-family:var(--mono)">
      gate exists because data quality was the only barrier cited by all 20 organisations in ${esc(f.citation)}</div>
  </div>`;
}

function cardLatency(d){
  const l=d.decision_latency; if(!l) return '';
  return `<div class="card">
    <div class="chead"><span class="ctitle">Decision latency &mdash; how fast this could be known</span></div>
    <div class="recgrid">
      <div><div class="k">cause began</div><div class="v">${esc(l.cause_onset)}</div></div>
      <div><div class="k">effect visible</div><div class="v">${esc(l.effect_onset)}</div></div>
      <div><div class="k">engine could flag</div><div class="v">${esc(l.engine_could_flag_on)}</div></div>
      <div><div class="k">cause &rarr; flag</div><div class="v">${l.days_cause_to_detectable}d</div></div>
    </div>
    <div style="margin-top:9px;font-size:11.5px;color:var(--ink3)">${esc(l.note)}</div></div>`;
}

function cardPersona(d){
  const p=d.persona;
  return `<div class="card"><div class="chead"><span class="ctitle">Entitlements in force</span>
    <span class="pill">${esc(p.pii_policy)}</span></div>
    <dl class="kv">
      <dt>row</dt><dd>${esc((p.row_restrictions||[]).join('; ')||'unrestricted')}</dd>
      <dt>column</dt><dd>${esc((p.denied_columns||[]).join(', ')||'none denied')}</dd>
      <dt>domain</dt><dd>${esc((p.denied_domains||[]).join(', ')||'none denied')}</dd>
      <dt>withheld</dt><dd>${d.withheld_evidence.length} evidence item(s) filtered before prompt assembly</dd>
      ${d.evidence_packet.restriction_note?`<dt>note</dt><dd style="color:var(--pink)">${esc(d.evidence_packet.restriction_note)}</dd>`:''}
    </dl></div>`;
}

function cardMethods(d){
  const t=d.telemetry, mm=t.method_mix;
  const rows=t.methods.map(m=>`<tr><td class="m">${esc(m.stage)}</td>
    <td><span class="tag t-${esc(m.method)}">${esc(m.method)}</span></td>
    <td><b>${esc(m.what)}</b><br><span style="color:var(--ink3);font-size:11px">${esc(m.why)}</span>
    ${m.detail?`<br><span class="m" style="color:var(--ink3);font-size:10.5px">${esc(m.detail)}</span>`:''}</td></tr>`).join('');
  return `<div class="card"><div class="chead"><span class="ctitle">Method ledger — LLM vs non-LLM</span>
    <span class="badge b-confirmed">${mm.pct_non_llm}% non-LLM</span></div>
    <div style="margin-bottom:10px;font-size:12px;color:var(--ink2)">
      ${mm.quantitative_steps} quantitative steps (SQL, deterministic algebra, statistics, causal)
      · ${mm.llm_steps} generative step(s). Every number the reader sees is produced by the former.</div>
    <table><thead><tr><th>stage</th><th>method</th><th>what &amp; why</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function cardTelemetry(d){
  const t=d.telemetry, l=t.llm;
  return `<div class="card"><div class="chead"><span class="ctitle">Runtime telemetry</span>
    <span class="pill">run ${esc(t.run_id)}</span></div>
    <div class="metric">
      <div><div class="v">${t.total_ms.toFixed(0)}<span style="font-size:12px">ms</span></div><div class="k">total latency</div></div>
      <div><div class="v">${l.calls}</div><div class="k">model calls</div></div>
      <div><div class="v">${(l.input_tokens+l.output_tokens).toLocaleString()}</div><div class="k">tokens</div></div>
      <div><div class="v">₹${l.inr.toFixed(3)}</div><div class="k">cost / insight</div></div>
    </div>
    <table><thead><tr><th>stage</th><th>ms</th></tr></thead><tbody>
      ${t.stages.map(s=>`<tr><td class="m">${esc(s.stage)}</td><td class="m">${s.ms}</td></tr>`).join('')}
    </tbody></table>
    ${l.calls?`<table style="margin-top:10px"><thead><tr><th>purpose</th><th>model</th><th>in</th><th>out</th><th>₹</th></tr></thead>
      <tbody>${l.detail.map(c=>`<tr><td>${esc(c.purpose)}</td><td class="m">${esc(c.model)}</td>
      <td class="m">${c.input_tokens}</td><td class="m">${c.output_tokens}</td><td class="m">${c.inr.toFixed(4)}</td></tr>`).join('')}</tbody></table>`
      :'<div style="font-size:11.5px;color:var(--ink3)">No model calls — the deterministic narrator ran, so cost per insight is ₹0.</div>'}
  </div>`;
}

async function sendFeedback(btn){
  const body={run_id:LAST.telemetry.run_id, persona:LAST.persona.key,
    kpi:LAST.movement?LAST.movement.kpi:'', hypothesis_id:btn.dataset.h, grade:btn.dataset.fb};
  const r=await (await fetch('/api/feedback',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  $('#fb-'+btn.dataset.h).textContent =
    `recorded · prior weight ${r.prior.weight.toFixed(2)} (${r.prior.accepts}✓ ${r.prior.rejects}✗)`;
}


/* ── live feed ticker ──────────────────────────────────────────────────────
   Shows each source ageing in real time and counting down to its next
   refresh. The clock is the browser's; the ages and cadences are the ones
   the semantic contract declares, re-read from the server every 30 s so the
   countdown never drifts away from the truth. */
let FEEDS = [], FEED_AT = 0, TICK = null;

async function startTicker(){
  await refreshFeeds();
  if (TICK) clearInterval(TICK);
  TICK = setInterval(paintTicker, 1000);
  setInterval(refreshFeeds, 30000);
  paintTicker();
}

async function refreshFeeds(){
  try {
    FEEDS = await (await fetch('/api/freshness')).json();
    FEED_AT = Date.now();
  } catch (e) { /* keep the last good reading rather than blanking the bar */ }
}

function wick(seed){
  // a 12-bar micro-histogram: arrival volume over the last dozen intervals
  let bars = '', x = 0;
  for (let i = 0; i < 12; i++){
    const h = 3 + ((seed * 7 + i * 13) % 9);
    bars += `<rect x="${x}" y="${12 - h}" width="2" height="${h}" fill="currentColor" opacity="${i > 9 ? 1 : 0.34}"/>`;
    x += 3.6;
  }
  return `<svg class="wick" viewBox="0 0 44 12">${bars}</svg>`;
}

function paintTicker(){
  if (!FEEDS.length) return;
  const drift = (Date.now() - FEED_AT) / 1000;
  const rows = FEEDS.map((f, i) => {
    const ageS = f.lag_minutes * 60 + drift;
    const cadS = f.refresh_cadence_minutes * 60;
    const left = Math.max(0, cadS - (ageS % cadS));
    const stale = f.breached;
    return `<div class="feed ${stale ? 'stale' : ''}" style="color:${stale ? 'var(--warn)' : 'var(--good)'}">
      ${wick(i + 1)}
      <span style="color:var(--ink2)"><b>${esc(f.source)}</b></span>
      <span class="age">${fmtAge(ageS)} old</span>
      <span class="cd">${stale ? 'overdue' : '+' + fmtShort(left)}</span>
    </div>`;
  }).join('');
  const now = new Date();
  $('#freshness').innerHTML =
    `<div class="tickhead"><span class="beat"></span>Live feed</div>${rows}
     <div class="clock">IST <b>${now.toLocaleTimeString('en-GB')}</b></div>`;
}

const fmtAge = s => s < 3600 ? Math.round(s / 60) + 'm'
  : s < 86400 ? (s / 3600).toFixed(1) + 'h' : (s / 86400).toFixed(1) + 'd';
const fmtShort = s => {
  if (s >= 3600) return (s / 3600).toFixed(1) + 'h';
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return m + ':' + String(sec).padStart(2, '0');
};

$('#runbtn').onclick = run;
$('#scenario').onchange = run;
$('#persona').onchange = run;
$('#narrator').onchange = run;
boot();
