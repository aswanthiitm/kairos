const pptxgen = require('pptxgenjs');
const p = new pptxgen();
p.layout = 'LAYOUT_WIDE';                 // 13.3 x 7.5
p.author = 'KAIROS'; p.company = 'Accenture Innovation Challenge 2026';
p.title = 'KAIROS - Business Proposal';

const BONE='E9E7E2', SURF='F2F0EC', SUNK='E3E0DA', INK='191B1A', INK2='4B514E', INK3='7C837F',
      LINE='C9C5BC', ACC='8A4A32', GOOD='2F5D42', WARN='8A6A1F', BAD='8A3A32', SLATE='465361';
const H='Arial', B='Calibri', M='Consolas';
const W=13.3, MG=0.72;

// the product mark, repeated as the deck's motif
function mark(s, x, y, sz, col){
  s.addShape(p.ShapeType.rect, {x, y, w:sz, h:sz, fill:{type:'none'},
    line:{color:col, width:1.6}});
  s.addShape(p.ShapeType.rect, {x:x+sz*0.34, y:y+sz*0.34, w:sz*0.32, h:sz*0.32, fill:{color:col}});
}
function slide(bg){ const s=p.addSlide(); s.background={color:bg||BONE}; return s; }
function eyebrow(s, t, col){ s.addText(t, {x:MG, y:0.42, w:W-2*MG, h:0.22, isTextBox:true, margin:0,
  fontFace:M, fontSize:9, color:col||INK3, charSpacing:2.2}); }
function title(s, t, col){ s.addText(t, {x:MG, y:0.68, w:W-2*MG, h:0.62, isTextBox:true, margin:0,
  fontFace:H, fontSize:31, bold:true, color:col||INK, lineSpacing:32}); }
function standfirst(s, t){ s.addText(t, {x:MG, y:1.28, w:W-2*MG-2.2, h:0.3, isTextBox:true,
  margin:0, fontFace:B, fontSize:13.5, color:INK3}); }
function foot(s, n){ s.addText('KAIRÓS', {x:MG, y:6.92, w:2, h:0.24, isTextBox:true, margin:0,
    fontFace:M, fontSize:8, color:INK3, charSpacing:1.6});
  s.addText(String(n), {x:W-MG-0.6, y:6.92, w:0.6, h:0.24, isTextBox:true, margin:0,
    fontFace:M, fontSize:8, color:INK3, align:'right'}); }

/* 1 — title, dark */
{ const s=slide(INK);
  mark(s, MG, 1.55, 0.42, ACC);
  s.addText('KAIRÓS', {x:MG, y:2.15, w:9, h:1.25, isTextBox:true, margin:0,
    fontFace:H, fontSize:66, bold:true, color:SURF, charSpacing:5});
  s.addText('A KPI intelligence-to-action engine', {x:MG, y:3.42, w:9.4, h:0.5, isTextBox:true,
    margin:0, fontFace:B, fontSize:21, color:'B0A99B'});
  s.addText('It detects a material metric movement, decomposes it exactly, grades every explanation against a standard of proof, and recommends an action with a named owner — or refuses, and prices the experiment that would settle it.',
    {x:MG, y:4.05, w:8.6, h:1.1, isTextBox:true, margin:0, fontFace:B, fontSize:13.5,
     color:'8E948F', lineSpacing:20});
  s.addText([{text:'Kairós', options:{italic:true}}, {text:' — the opportune moment', options:{}}],
    {x:MG, y:5.25, w:6, h:0.3, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:ACC});
  s.addText('Accenture Innovation Challenge 2026  ·  Round 2  ·  BusinessIntelligence.ai',
    {x:MG, y:6.55, w:10, h:0.3, isTextBox:true, margin:0, fontFace:M, fontSize:10, color:'6E7573'});
}

/* 2 — the problem */
{ const s=slide(); eyebrow(s,'THE PROBLEM'); title(s,'Detection is solved. Explanation is not.');
  standfirst(s,'Three structural failures keep the translation manual — and they cost days, not minutes.');
  s.addText('A dashboard can say North revenue fell 18.8%. It cannot say the fall is three enterprise accounts that stopped reordering after a warehouse missed its dispatch SLA ten days earlier — because that sentence lives in service tickets, call transcripts and a dispatch log no BI tool reads.',
    {x:MG, y:1.95, w:7.4, h:1.1, isTextBox:true, margin:0, fontFace:B, fontSize:14, color:INK2, lineSpacing:21});
  const rows=[['Noise blindness','Anomaly tools alert on deviation, not consequence. Flag everything and leaders mute you.'],
              ['Correlation theatre','“Insight” features surface what moved alongside a metric. A leader cannot act on that.'],
              ['Organisational amnesia','The company solved this pattern fourteen months ago. That memory sits in a dead slide.']];
  let y=3.05;
  rows.forEach((r,i)=>{ mark(s, MG, y+0.06, 0.2, ACC);
    s.addText(r[0], {x:MG+0.42, y, w:2.9, h:0.3, isTextBox:true, margin:0, fontFace:H, fontSize:14, bold:true, color:INK});
    s.addText(r[1], {x:MG+3.45, y:y-0.02, w:5.4, h:0.62, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:INK2, lineSpacing:18});
    y+=0.92; });
  s.addShape(p.ShapeType.rect,{x:9.55,y:1.95,w:3.05,h:3.9,fill:{color:SUNK}});
  s.addText('3–5', {x:9.85, y:2.32, w:2.5, h:1.18, isTextBox:true, margin:0, fontFace:H, fontSize:60, bold:true, color:BAD});
  s.addText('days for an analyst to translate a movement into a cause', {x:9.85, y:3.42, w:2.45, h:0.8, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:INK2, lineSpacing:18});
  s.addText('The consequence is decision latency, not data latency. Pipelines refresh hourly; the explanation refreshes weekly.',
    {x:9.85, y:4.42, w:2.45, h:1.2, isTextBox:true, margin:0, fontFace:B, fontSize:11.5, color:INK3, lineSpacing:17, italic:true});
  foot(s,2);
}

/* 3 — why not solved */
{ const s=slide(); eyebrow(s,'WHY IT IS STILL OPEN'); title(s,'$1.1B automated the search');
  standfirst(s,'Nobody built the standard of proof, the evidence outside the warehouse, or the memory.');
  s.addText('Four adjacent categories — BI copilots, warehouse-native analysts, driver attribution, causal decision intelligence — each solved a different third. None ships the standard of proof, the evidence outside the warehouse, or the memory of what worked.',
    {x:MG, y:1.95, w:6.1, h:1.1, isTextBox:true, margin:0, fontFace:B, fontSize:13.5, color:INK2, lineSpacing:20});
  s.addText('And the binding obstacle is elsewhere again', {x:MG, y:3.25, w:6.1, h:0.32, isTextBox:true, margin:0, fontFace:H, fontSize:15, bold:true, color:INK});
  s.addText('Olszak & Bartuś (Procedia Computer Science 270, 2025) interviewed twenty organisations across three sectors. Data quality was the only barrier every single respondent named.',
    {x:MG, y:3.65, w:6.1, h:0.9, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:INK2, lineSpacing:19});
  s.addTable([
    [{text:'BARRIER',options:{fontFace:M,fontSize:8.5,color:INK3,bold:false}},{text:'OF 20',options:{fontFace:M,fontSize:8.5,color:INK3,align:'right'}}],
    [{text:'Data availability and quality',options:{bold:true}},{text:'20',options:{fontFace:M,bold:true,color:BAD,align:'right'}}],
    ['Integrating diverse sources',{text:'18',options:{fontFace:M,align:'right'}}],
    ['High implementation cost',{text:'15',options:{fontFace:M,align:'right'}}],
    ['Shortage of specialists',{text:'12',options:{fontFace:M,align:'right'}}],
    ['Integration with existing BI',{text:'11',options:{fontFace:M,align:'right'}}],
    ['Security and regulation',{text:'10',options:{fontFace:M,align:'right'}}]],
    {x:7.35, y:1.95, w:5.25, colW:[3.95,1.3], fontFace:B, fontSize:12, color:INK2,
     border:{type:'solid',color:LINE,pt:0.5}, fill:{color:SURF}, rowH:0.34, valign:'middle'});
  s.addText('KAIRÓS treats that ranking as an instruction: a data fitness gate runs first and may halt the run.',
    {x:7.35, y:4.85, w:5.25, h:0.6, isTextBox:true, margin:0, fontFace:B, fontSize:12, color:ACC, lineSpacing:18, italic:true});
  foot(s,3);
}

/* 4 — the engine */
{ const s=slide(); eyebrow(s,'THE ENGINE'); title(s,'Ten stages');
  standfirst(s,'Each one is defined as much by what it refuses to do as by what it computes.');
  const st=[['SEMANTIC','Whose definition?','Compute a KPI two systems define differently'],
            ['FITNESS','Fit to reason over?','Analyse an estate that fails the gate'],
            ['DRIFT','Has the ground moved?','Trust a ranker outside its training support'],
            ['SIFT','Real, and material?','Wake anyone for an immaterial move'],
            ['SPLIT','Where exactly?','Leave a residual — the identity closes to under ₹1'],
            ['SOURCE','Why, and how sure?','Award L3 when the placebo test fails'],
            ['PROPAGATE','Where is the loss created?','Measure a cause in its effect’s window'],
            ['FORECAST','What if nobody acts?','Voice an extrapolation as a counterfactual'],
            ['SOLVE','What should we do?','Emit an action whose lever isn’t in the contract'],
            ['NARRATE','How do we say it?','Publish a number absent from the evidence packet']];
  let y=1.92;
  st.forEach((r,i)=>{
    const col = i===9 ? BAD : ACC;
    s.addText(r[0], {x:MG, y, w:1.55, h:0.28, isTextBox:true, margin:0, fontFace:M, fontSize:10.5, bold:true, color:col});
    s.addText(r[1], {x:MG+1.65, y, w:2.9, h:0.28, isTextBox:true, margin:0, fontFace:H, fontSize:11.5, bold:true, color:INK});
    s.addText('refuses to  ' + r[2], {x:MG+4.65, y, w:7.2, h:0.28, isTextBox:true, margin:0, fontFace:B, fontSize:11.5, color:INK2});
    if(i<9) s.addShape(p.ShapeType.line,{x:MG,y:y+0.34,w:11.86,h:0,line:{color:LINE,width:0.5}});
    y+=0.5; });
  foot(s,4);
}

/* 5 — evidence ladder */
{ const s=slide(); eyebrow(s,'THE STANDARD OF PROOF');
  title(s,'The Evidence Ladder');
  standfirst(s,'Nothing below L2 is ever phrased as a cause.');
  s.addText('Bradford Hill’s viewpoints (1965) operationalised on Pearl’s causal hierarchy (CACM 2019).',
    {x:MG, y:1.72, w:8, h:0.3, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:INK3});
  const L=[['L0','Co-movement','two series moved together','“moved alongside”','C9C5BC'],
           ['L1','Precedence + gradient','cause preceded effect within the graph’s declared lag','“associated with”',ACC],
           ['L2','Independent corroboration','sources not sharing a pipeline agree, conflict under 40%','“likely cause”',ACC],
           ['L3','Counterfactual','difference-in-differences against a real untreated cohort, placebo must validate','“quantified at −20.6%, p<0.0001”',GOOD]];
  let y=2.18;
  L.forEach(r=>{
    s.addShape(p.ShapeType.rect,{x:MG,y,w:0.62,h:0.78,fill:{color:r[4]}});
    s.addText(r[0],{x:MG,y:y+0.2,w:0.62,h:0.36,isTextBox:true,margin:0,fontFace:M,fontSize:15,bold:true,color:r[4]===LINE?INK:'F2F0EC',align:'center'});
    s.addText(r[1],{x:MG+0.85,y:y+0.06,w:3.1,h:0.3,isTextBox:true,margin:0,fontFace:H,fontSize:13,bold:true,color:INK});
    s.addText(r[2],{x:MG+0.85,y:y+0.36,w:5.0,h:0.46,isTextBox:true,margin:0,fontFace:B,fontSize:11.5,color:INK2});
    s.addText(r[3],{x:MG+6.1,y:y+0.2,w:5.7,h:0.36,isTextBox:true,margin:0,fontFace:B,fontSize:12,color:r[4]===LINE?INK3:r[4],italic:true});
    y+=0.92; });
  s.addText('Conflict is measured, not assumed', {x:MG, y:5.9, w:5, h:0.3, isTextBox:true, margin:0, fontFace:H, fontSize:13, bold:true, color:INK});
  s.addTable([
    [{text:'CASE',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'ON-THEME',options:{fontFace:M,fontSize:8.5,color:INK3,align:'right'}},{text:'CONFLICTING',options:{fontFace:M,fontSize:8.5,color:INK3,align:'right'}},{text:'RATIO',options:{fontFace:M,fontSize:8.5,color:INK3,align:'right'}},{text:'VERDICT',options:{fontFace:M,fontSize:8.5,color:INK3}}],
    ['Dispatch SLA',{text:'12',options:{fontFace:M,align:'right'}},{text:'0',options:{fontFace:M,align:'right'}},{text:'0.00',options:{fontFace:M,align:'right'}},{text:'corroborated → L3',options:{color:GOOD,bold:true}}],
    ['Price increase',{text:'2',options:{fontFace:M,align:'right'}},{text:'10',options:{fontFace:M,align:'right'}},{text:'0.83',options:{fontFace:M,align:'right',bold:true,color:BAD}},{text:'contested — rung suppressed',options:{color:BAD,bold:true}}]],
    {x:MG, y:6.25, w:11.86, colW:[2.3,1.5,1.7,1.2,5.16], fontFace:B, fontSize:11.5, color:INK2,
     border:{type:'solid',color:LINE,pt:0.5}, fill:{color:SURF}, rowH:0.3, valign:'middle'});
  foot(s,5);
}

/* 6 — division of labour */
{ const s=slide(); eyebrow(s,'THE ARCHITECTURAL COMMITMENT');
  title(s,'The model proposes. It never computes.');
  standfirst(s,'The split is a direct read of where language models actually fail.');
  s.addShape(p.ShapeType.rect,{x:MG,y:1.95,w:6.2,h:1.5,fill:{color:SUNK}});
  s.addText('Language models are strong at proposing causal explanations from world knowledge and near-random at inferring causation from correlation.',
    {x:MG+0.28, y:2.15, w:5.65, h:1.1, isTextBox:true, margin:0, fontFace:B, fontSize:14, color:INK, italic:true, lineSpacing:21});
  s.addText('Kıcıman et al. (TMLR 2024) found LLMs beat existing algorithms on knowledge-based causal tasks. Corr2Cause (ICLR 2024) tested seventeen models on inferring causation from correlation and found them near random. Both are true — the architecture is a read of where the split falls.',
    {x:MG, y:3.6, w:6.2, h:1.3, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:INK2, lineSpacing:19});
  s.addText('Enforced at runtime, not asserted', {x:MG, y:5.0, w:6.2, h:0.3, isTextBox:true, margin:0, fontFace:H, fontSize:13, bold:true, color:INK});
  s.addText([{text:'A closed evidence packet — no tools, no data access, so it cannot compute.\n',options:{breakLine:true}},
             {text:'A numeric guard — every figure parsed and matched against that packet. An unverifiable number fails the narrative.',options:{}}],
    {x:MG, y:5.37, w:6.2, h:0.9, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:INK2, lineSpacing:19, bullet:true});
  s.addShape(p.ShapeType.rect,{x:7.5,y:1.95,w:5.1,h:4.25,fill:{color:SURF},line:{color:LINE,width:0.7}});
  s.addText('91–100%', {x:7.8, y:2.2, w:4.5, h:0.95, isTextBox:true, margin:0, fontFace:H, fontSize:52, bold:true, color:GOOD});
  s.addText('of reasoning steps are non-LLM, measured on a live run', {x:7.8, y:3.15, w:4.5, h:0.55, isTextBox:true, margin:0, fontFace:B, fontSize:13, color:INK2, lineSpacing:19});
  const mm=[['SQL','28'],['RULES','10'],['STATISTICS','4'],['DETERMINISTIC','4'],['RETRIEVAL','3'],['CAUSAL','2'],['ML','2'],['LLM','1']];
  let y=3.86;
  mm.forEach(r=>{ const isLLM=r[0]==='LLM';
    s.addText(r[0],{x:7.8,y,w:2.4,h:0.24,isTextBox:true,margin:0,fontFace:M,fontSize:10.5,color:isLLM?BAD:INK2});
    s.addText(r[1],{x:10.3,y,w:0.6,h:0.24,isTextBox:true,margin:0,fontFace:M,fontSize:10.5,bold:true,color:isLLM?BAD:INK,align:'right'});
    s.addShape(p.ShapeType.rect,{x:11.05,y:y+0.055,w:Math.max(0.06,parseInt(r[1])/28*1.25),h:0.13,fill:{color:isLLM?BAD:ACC}});
    y+=0.29; });
  s.addText('Every step declares its method and why that method was chosen.',{x:7.8,y:6.02,w:4.5,h:0.3,isTextBox:true,margin:0,fontFace:B,fontSize:11,color:INK3,italic:true});
  foot(s,6);
}

/* 7 — what it demonstrates */
{ const s=slide(); eyebrow(s,'THE PROTOTYPE');
  title(s,'Five cases, ground truth planted');
  standfirst(s,'So the engine is scored against what was buried, not admired for what it says.');
  s.addTable([
    [{text:'',options:{}},{text:'CASE',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'WHAT WAS PLANTED',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'VERDICT',options:{fontFace:M,fontSize:8.5,color:INK3}}],
    [{text:'S1',options:{fontFace:M,bold:true,color:ACC}},'North revenue −18.8%','WH-3 dispatch SLA collapse → three named accounts cut reorder cadence at a 10-day lag, plus a tier-mix shift',{text:'CONFIRMED · L3',options:{color:GOOD,bold:true,fontFace:M,fontSize:10.5}}],
    [{text:'S2',options:{fontFace:M,bold:true,color:ACC}},'West volume −8.9%','A competitor promotion and our own price rise — same day, same channel, no clean control',{text:'COMPETING',options:{color:WARN,bold:true,fontFace:M,fontSize:10.5}}],
    [{text:'S3',options:{fontFace:M,bold:true,color:ACC}},'New category, 19 days','Too short a series to fit seasonality',{text:'INSUFFICIENT HISTORY',options:{color:SLATE,bold:true,fontFace:M,fontSize:10.5}}],
    [{text:'S4',options:{fontFace:M,bold:true,color:ACC}},'WH-4 delivery “improves”','Late-shipment rows silently stopped loading',{text:'DATA QUALITY',options:{color:BAD,bold:true,fontFace:M,fontSize:10.5}}],
    [{text:'S6',options:{fontFace:M,bold:true,color:ACC}},'South modern trade −18.9%','Competitor promotion hitting one channel only — the others are an untreated control',{text:'CONFIRMED · L3',options:{color:GOOD,bold:true,fontFace:M,fontSize:10.5}}]],
    {x:MG, y:1.95, w:11.86, colW:[0.55,2.5,6.0,2.81], fontFace:B, fontSize:12, color:INK2,
     border:{type:'solid',color:LINE,pt:0.5}, fill:{color:SURF}, rowH:0.62, valign:'middle'});
  s.addText('S2 and S6 are deliberate mirrors: the same class of cause, one confounded by design and one clean. The pair is what makes the abstention in S2 read as judgement rather than weakness.',
    {x:MG, y:5.35, w:8.4, h:0.6, isTextBox:true, margin:0, fontFace:B, fontSize:13, color:INK2, lineSpacing:20, italic:true});
  s.addText('125', {x:9.6, y:5.22, w:1.4, h:0.76, isTextBox:true, margin:0, fontFace:H, fontSize:38, bold:true, color:ACC});
  s.addText('automated tests assert these against the ground-truth file', {x:10.75, y:5.3, w:1.9, h:0.7, isTextBox:true, margin:0, fontFace:B, fontSize:11, color:INK2, lineSpacing:16});
  foot(s,7);
}

/* 8 — security + decision rights */
{ const s=slide(); eyebrow(s,'GOVERNANCE');
  title(s,'Entitlements bind the data');
  standfirst(s,'And the human–AI mode on each recommendation is derived, not configured.');
  s.addText('Enforced before anything reaches a prompt', {x:MG, y:1.92, w:5.9, h:0.3, isTextBox:true, margin:0, fontFace:H, fontSize:14, bold:true, color:INK});
  s.addTable([
    [{text:'PERSONA',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'SEES',options:{fontFace:M,fontSize:8.5,color:INK3}}],
    [{text:'CFO',options:{bold:true}},'All regions; no CRM verbatims. Reaches L3 on the counterfactual; 23 items withheld'],
    [{text:'RSM North',options:{bold:true}},{text:'region = North. A West query is refused before any SQL runs',options:{color:BAD}}],
    [{text:'Supply Chain',options:{bold:true}},'No rupee column at all — the packet holds no currency figure anywhere'],
    [{text:'Analyst',options:{bold:true}},'Full method detail and lineage']],
    {x:MG, y:2.28, w:5.9, colW:[1.7,4.2], fontFace:B, fontSize:11.5, color:INK2,
     border:{type:'solid',color:LINE,pt:0.5}, fill:{color:SURF}, rowH:0.52, valign:'middle'});
  s.addText('Human–AI mode is derived, not configured', {x:7.05, y:1.92, w:5.55, h:0.3, isTextBox:true, margin:0, fontFace:H, fontSize:14, bold:true, color:INK});
  s.addTable([
    [{text:'RECOMMENDATION',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'MODE',options:{fontFace:M,fontSize:8.5,color:INK3}}],
    ['Counter-promotion, ₹0.7 L',{text:'AI-led, human approves',options:{color:GOOD}}],
    [{text:'Service credit, ₹44.6 L',options:{}},{text:'Human-led — exceeds the RSM’s ₹25 L limit',options:{color:WARN}}],
    ['Price correction',{text:'Human-led — hard to reverse',options:{color:WARN}}],
    ['Under abstention',{text:'Human only, AI abstains',options:{color:SLATE}}],
    [{text:'—',options:{color:INK3}},{text:'AI-delegated: reserved, never assigned',options:{color:BAD,bold:true}}]],
    {x:7.05, y:2.28, w:5.55, colW:[2.55,3.0], fontFace:B, fontSize:11.5, color:INK2,
     border:{type:'solid',color:LINE,pt:0.5}, fill:{color:SURF}, rowH:0.42, valign:'middle'});
  s.addShape(p.ShapeType.rect,{x:MG,y:5.35,w:11.86,h:0.95,fill:{color:SUNK}});
  s.addText('Every lever is externally visible to a customer, moves money, or both. Full machine delegation is left empty on purpose — which makes EU AI Act Art. 14 human oversight an inspectable property of each recommendation rather than a policy sentence.',
    {x:MG+0.28, y:5.52, w:11.3, h:0.62, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:INK2, lineSpacing:19});
  foot(s,8);
}

/* 9 — business case */
{ const s=slide(); eyebrow(s,'BUSINESS CASE');
  title(s,'Collapsing the distance to action');
  standfirst(s,'Measured on the prototype, not projected.');
  const stats=[['≈450 ms','end to end, offline'],['≈80 ms','repeat run, cache hit — zero tokens'],['₹0.30','per insight, one model call'],['1.9–3.2 ms','core queries at 4.5M rows']];
  let x=MG;
  stats.forEach(r=>{ s.addShape(p.ShapeType.rect,{x,y:1.92,w:2.86,h:1.24,fill:{color:SURF},line:{color:LINE,width:0.7}});
    s.addText(r[0],{x:x+0.22,y:2.06,w:2.5,h:0.5,isTextBox:true,margin:0,fontFace:H,fontSize:24,bold:true,color:ACC});
    s.addText(r[1],{x:x+0.22,y:2.60,w:2.5,h:0.45,isTextBox:true,margin:0,fontFace:B,fontSize:11,color:INK2,lineSpacing:15});
    x+=3.0; });
  s.addTable([
    [{text:'LEVER',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'MECHANISM',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'BASIS IN THE PROTOTYPE',options:{fontFace:M,fontSize:8.5,color:INK3}}],
    [{text:'Recovered leakage',options:{bold:true}},'Acting in week 1 rather than week 2 on service-driven churn','S1: ₹2.15 Cr movement, 87.6% attributable, playbook recovery 71% measured'],
    [{text:'Analyst capacity',options:{bold:true}},'“Why did X move” fire-drills automated','Each incident is 2–4 analyst days today'],
    [{text:'Avoided wrong actions',options:{bold:true}},'Abstention prevents intervening on the wrong cause','S2: two rivals both at L1 — a confident engine would have picked one'],
    [{text:'Compounding memory',options:{bold:true}},'Effect sizes re-estimated from realised outcomes','Playbook accuracy improves with each closed loop']],
    {x:MG, y:3.38, w:11.86, colW:[2.5,4.3,5.06], fontFace:B, fontSize:12, color:INK2,
     border:{type:'solid',color:LINE,pt:0.5}, fill:{color:SURF}, rowH:0.6, valign:'middle'});
  s.addText('Success is measured on diagnosis precision, decision latency and recommendation calibration — deliberately not on insight volume, which is the metric that manufactures alert fatigue.',
    {x:MG, y:5.85, w:11.86, h:0.55, isTextBox:true, margin:0, fontFace:B, fontSize:12.5, color:ACC, italic:true, lineSpacing:19});
  foot(s,9);
}

/* 10 — roadmap */
{ const s=slide(); eyebrow(s,'ROADMAP'); title(s,'Precision gates the rollout');
  standfirst(s,'Scope expands only after analyst-graded precision clears the bar.');
  const ph=[['1','Prove precision','0–3 mo','One P&L metric, one function. SEMANTIC + FITNESS + SIFT + SPLIT on the existing semantic layer. Narratives reviewed, never auto-published.','Analyst-graded precision clears an agreed bar'],
            ['2','Add evidence and memory','3–6 mo','Connect unstructured sources with entitlements and redaction. Seed playbooks from past incidents. Enable the ladder and abstention.','≥60% of material movements reach L2+'],
            ['3','Close the loop','6–12 mo','Multi-KPI, multi-persona, outcome logging, the ranker trained on the client’s resolved episodes. Recommendations remain proposed.','Recommendation calibration measurable'],
            ['4','Productise','12 mo+','Industry-tuned graph and playbook packs as a delivery accelerator','Reuse across engagements']];
  let y=1.95;
  ph.forEach((r,i)=>{
    s.addShape(p.ShapeType.rect,{x:MG,y,w:0.52,h:0.52,fill:{color:i===0?ACC:SUNK}});
    s.addText(r[0],{x:MG,y:y+0.1,w:0.52,h:0.32,isTextBox:true,margin:0,fontFace:H,fontSize:17,bold:true,color:i===0?'F2F0EC':INK2,align:'center'});
    s.addText(r[1],{x:MG+0.75,y:y-0.02,w:3.0,h:0.3,isTextBox:true,margin:0,fontFace:H,fontSize:14,bold:true,color:INK});
    s.addText(r[2],{x:MG+0.75,y:y+0.28,w:3.0,h:0.26,isTextBox:true,margin:0,fontFace:M,fontSize:10,color:INK3});
    s.addText(r[3],{x:MG+4.0,y:y-0.02,w:5.0,h:0.85,isTextBox:true,margin:0,fontFace:B,fontSize:11.5,color:INK2,lineSpacing:17});
    s.addText('EXIT  ' + r[4],{x:MG+9.2,y:y+0.06,w:2.66,h:0.7,isTextBox:true,margin:0,fontFace:B,fontSize:11,color:ACC,lineSpacing:16});
    if(i<3) s.addShape(p.ShapeType.line,{x:MG,y:y+1.08,w:11.86,h:0,line:{color:LINE,width:0.5}});
    y+=1.24; });
  foot(s,10);
}

/* 11 — risks */
{ const s=slide(); eyebrow(s,'RISK'); title(s,'Risk, and what absorbs it');
  standfirst(s,'Each mitigation is built in, not bolted on.');
  s.addTable([
    [{text:'RISK',options:{fontFace:M,fontSize:8.5,color:INK3}},{text:'MITIGATION',options:{fontFace:M,fontSize:8.5,color:INK3}}],
    [{text:'Confident but wrong',options:{bold:true}},'L2 evidence floor; explicit Unknown state; mandatory citation; the numeric guard rejects unverifiable figures'],
    [{text:'Spurious causes',options:{bold:true}},'Curated causal graph blocks inadmissible mechanisms; the placebo must validate before L3 is awarded'],
    [{text:'Poor data quality',options:{bold:true}},'A fitness gate runs first across five dimensions and may halt the run; class-level completeness catches a load that drops one kind of row while totals hold'],
    [{text:'The ground moves',options:{bold:true}},'Drift monitored separately for data and model; a ranker scoring outside its training support has its authority withdrawn for that run'],
    [{text:'Over-trust',options:{bold:true}},'Rival hypotheses shown by default; actions stay proposed; full machine delegation reserved and never assigned'],
    [{text:'Sensitive evidence',options:{bold:true}},'Row, column and domain entitlements applied to data before prompt assembly; PII redacted pre-retrieval; withheld items surfaced as a count'],
    [{text:'Regulatory',options:{bold:true}},'Art. 12 post-hoc reconstruction satisfied by full lineage; Art. 14 oversight by never auto-executing; India’s DPDP makes purpose limitation a design constraint'],
    [{text:'Platform commoditisation',options:{bold:true}},'We deliberately do not rebuild text-to-SQL or the semantic layer — now free platform features. The moat is proof, evidence and memory']],
    {x:MG, y:1.95, w:11.86, colW:[2.9,8.96], fontFace:B, fontSize:11.5, color:INK2,
     border:{type:'solid',color:LINE,pt:0.5}, fill:{color:SURF}, rowH:0.56, valign:'middle'});
  foot(s,11);
}

/* 12 — close */
{ const s=slide(INK);
  mark(s, MG, 1.5, 0.34, ACC);
  s.addText('What the prototype proves', {x:MG, y:2.0, w:9, h:0.7, isTextBox:true, margin:0,
    fontFace:H, fontSize:34, bold:true, color:SURF});
  const pts=['recovered the buried service failure at L3 — DiD −20.6%, p<0.0001, placebo clean — and rediscovered the 10-day mechanism lag independently',
             'abstained where two explanations were confounded by design, and priced a ₹1.8 L, 14-day separating test instead of guessing',
             'blamed the pipeline, not the business, when late-shipment rows stopped loading',
             'reconciled two competing definitions of net revenue and reported the gap',
             'withdrew its own learned ranker’s authority when inputs fell outside its training support',
             'caught its own hallucination — an injected ₹4.2 Cr figure failed the guard and the deterministic narrative was published instead'];
  let y=3.0;
  pts.forEach(t=>{ s.addShape(p.ShapeType.rect,{x:MG+0.02,y:y+0.1,w:0.1,h:0.1,fill:{color:ACC}});
    s.addText(t,{x:MG+0.36,y,w:8.5,h:0.52,isTextBox:true,margin:0,fontFace:B,fontSize:12.5,color:'B0A99B',lineSpacing:18});
    y+=0.56; });
  s.addText('github.com/aswanthiitm/kairos', {x:9.6, y:5.85, w:3.1, h:0.3, isTextBox:true, margin:0,
    fontFace:M, fontSize:11, color:ACC, align:'right'});
  s.addText('125 tests · 4 sources · 5 KPIs · 10 stages', {x:9.0, y:6.2, w:3.7, h:0.3, isTextBox:true,
    margin:0, fontFace:M, fontSize:9.5, color:'6E7573', align:'right'});
}

p.writeFile({fileName:'submission/KAIROS-Business-Proposal.pptx'})
 .then(f=>console.log('wrote', f));
