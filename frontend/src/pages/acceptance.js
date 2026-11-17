import {acceptance,datasets} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,fmt,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
import {AcceptanceResult,PhaseStatus,SampleOutcome} from '../constants/enums.js';

const VIEWS=[['all','全部'],['collect','收集中'],['supplement','待补报'],['open','待继续验收'],['passed','达标'],['failed','未达标']];

function phaseRows(c){
 return c.phases.map(p=>'<tr class="'+(p.status==='passed'?'ok':p.status==='failed'?'bad':'')+'"><td>第 '+p.number+' 期</td><td>'+p.size+'</td><td>'+esc(fmt(p.alpha))+'</td><td>'+esc(p.acceptance_number??'—')+'</td><td>'+p.survived+' / '+p.failed+' / '+p.pending+'</td><td>'+p.enrolled+' / '+p.size+'</td><td>'+(p.risk==null?'—':esc(p.risk.toExponential(2)))+'</td><td>'+esc(PhaseStatus[p.status]||p.status)+'</td></tr>').join('');
}

function caseCard(c){
 return '<article class="case-card" data-case="'+c.id+'"><div class="case-head"><div><strong>'+esc(c.name)+'</strong><p>'+esc(c.model)+' · 合格里程 '+esc(fmt(c.qualified_km))+' 公里 · 最低存活率 '+esc((c.min_survival*100).toFixed(0))+'%</p></div><span class="status '+(c.result||c.status)+'">'+esc(c.status==='open'?'验收中':AcceptanceResult[c.result]||'已封案')+'</span></div><div class="case-metrics"><span>当前第 '+c.current_phase+' 期</span><span>存活 '+c.totals.survived+' · 失效 '+c.totals.failed+' · 待补 '+c.totals.pending+'</span><span>额度已用 '+esc(fmt(c.totals.allowance_used))+' / '+esc(fmt(c.total_alpha))+'</span><span>'+(c.waiting_supplement?'等待补报':c.waiting_new?'继续收样':'已封案')+'</span></div>'+(c.close_reason?'<p class="case-close">'+esc(c.close_reason)+'</p>':'')+'<div class="case-actions"><a class="button small" href="#/acceptance/'+c.id+'">逐期依据 ↗</a>'+(c.approvable&&session.user.role==='admin'?'<button class="button small primary" data-approve="'+c.id+'">批准放行</button>':'')+(c.distributable?'<a class="text-button" href="#/reports">可建立分发计划 ↗</a>':'')+'</div></article>';
}

function openForm(config){
 const phaseLine=(n)=>'<div class="phase-line"><span>第 '+n+' 期</span><input type="number" min="1" max="'+config.max_phase_size+'" name="size-'+n+'" value="30" required><input type="number" min="0.001" max="'+config.max_total_alpha+'" step="0.001" name="alpha-'+n+'" value="0.05" required></div>';
 return '<form id="case-form"><label>验收案名称<input name="name" required minlength="2" placeholder="例如：AC-8 新型号放行验收 · 30,000 公里"></label><div class="parameter-grid"><label>部件型号<input name="model" required value="AC-8"></label><label>合格里程（公里）<input type="number" min="1" max="10000000" step="any" name="qualified_km" value="30000" required></label><label>最低存活率<input type="number" min="0.5" max="0.999" step="0.001" name="min_survival" value="0.85" required></label><label>关联数据批次（可选）<select name="dataset_id"><option value="">不关联</option>'+workspace.datasets.map(d=>'<option value="'+d.id+'">'+esc(d.name)+'</option>').join('')+'</select></label></div><p class="hint">逐期人数与误判额度在开验时锁定，额度合计不得超过 '+esc(fmt(config.max_total_alpha))+'；最多 '+config.max_phases+' 期。</p><div class="phase-table"><div class="phase-line head"><span>期号</span><span>计划人数</span><span>误判额度</span></div>'+phaseLine(1)+phaseLine(2)+phaseLine(3)+'</div><button type="submit" class="primary" '+(session.user.role==='viewer'?'disabled':'')+'>开验并锁定规则</button></form>';
}

export async function acceptancePage(id,view='all'){
 workspace.datasets=await datasets.list();
 const config=await acceptance.config();
 workspace.acceptanceConfig=config;
 if(id){
  const c=await acceptance.get(Number(id));
  return heading('04 / ACCEPTANCE','验收案逐期依据 · '+c.name,c.status==='open'?'当前第 '+c.current_phase+' 期：按额度检验存活率，收满且全部判定后才下结论。':'已封案，结论与逐期依据只读留档。','<a class="button" href="#/acceptance">返回验收列表 ↗</a>')+detailPane(c,config);
 }
 const cases=await acceptance.list(view);
 return heading('04 / ACCEPTANCE','新型号分期放行验收','先锁定合格里程、最低存活率、逐期人数与误判额度，再按额度逐期检验存活率。反复查看曲线不算达标，只有收满期数并按判定数达标才可批准分发。')+'<div class="acceptance-grid"><section class="panel"><div class="section-title"><h2>开验</h2><span>规则开验即锁定</span></div>'+openForm(config)+'</section><section><div class="section-title"><h2>验收案</h2><span>'+cases.length+' 条</span></div><div class="filter-row">'+VIEWS.map(([key,label])=>'<a class="filter '+(view===key?'active':'')+'" href="#/acceptance?view='+key+'">'+esc(label)+'</a>').join('')+'</div><div class="case-list">'+(cases.length?cases.map(caseCard).join(''):'<p class="empty">该筛选下暂无验收案。</p>')+'</div></section></div>';
}

function detailPane(c,config){
 const locked='<section class="panel"><div class="section-title"><h2>开验锁定的规则</h2><span>不可修改</span></div><div class="case-metrics"><span>型号 '+esc(c.model)+'</span><span>合格里程 '+esc(fmt(c.qualified_km))+' 公里</span><span>最低存活率 '+esc((c.min_survival*100).toFixed(1))+'%</span><span>额度上限使用 '+esc(fmt(c.total_alpha))+'</span></div></section>';
 const phaseTable='<section class="panel"><div class="section-title"><h2>逐期依据</h2><span>达到里程算存活，提前失效算失败，未到里程的在役件等补报</span></div><div class="table-wrap"><table><thead><tr><th>期号</th><th>计划人数</th><th>误判额度</th><th>判定数</th><th>存活/失效/待补</th><th>收样</th><th>尾部概率</th><th>判定</th></tr></thead><tbody>'+phaseRows(c)+'</tbody></table></div>'+(c.close_reason?'<p class="case-close">'+esc(c.close_reason)+'</p>':'')+'</section>';
 const pending='<section class="panel"><div class="section-title"><h2>待收齐 · 在役待补</h2><span>'+(c.pending_samples||[]).length+' 件未到合格里程，不剔除</span></div>'+table([{key:'asset',label:'资产号'},{key:'km',label:'最近里程'},{key:'phase',label:'归属期'},{key:'outcome',label:'状态'}],(c.pending_samples||[]).map(s=>({...s,outcome:SampleOutcome[s.outcome]})),'当前没有待补报的在役件。')+'<p class="hint">这些在役件达到合格里程或提前失效后，需在当期补报；补报不计为新样本。</p></section>';
 const reportForm=c.status==='open'?'<section class="panel"><div class="section-title"><h2>补报当期样本</h2><span>第 '+c.current_phase+' 期 · 还可新增 '+Math.max(0,(c.current_phase_state?.size||0)-(c.current_phase_state?.enrolled||0))+' 件</span></div><form id="report-form"><label>上传 CSV 或 JSON<input type="file" id="report-file" accept=".json,.csv"></label><label>本期记录 JSON<textarea name="records" rows="6" placeholder=\'[{"asset":"AC-8-001","km":32000,"failed":0}]\' required></textarea></label><label>请求号（重试沿用同一请求号，不重复计样）<input name="request_id" required minlength="8" value="'+esc('req-'+c.id+'-'+c.current_phase+'-'+Date.now().toString(36))+'"></label><button type="submit" class="primary" '+(session.user.role==='viewer'?'disabled':'')+'>提交本期报期</button><p class="hint">同号异值拒收；已判定资产的观测值不可更改；后期必须等待前期下结论。</p></form></section>':'';
 const log='<section class="panel"><div class="section-title"><h2>报期与结论记录</h2><span>请求号可追溯</span></div>'+table([{key:'phase',label:'期号'},{key:'accepted',label:'新增'},{key:'supplemented',label:'补报'},{key:'conclusion',label:'结论'},{key:'message',label:'说明'},{key:'actor',label:'提交人'}],c.reports||[])+'</section>';
 const samples='<section class="panel"><div class="section-title"><h2>参评样本（最多 300 件）</h2><span>跨期不重复参评</span></div>'+table([{key:'asset',label:'资产号'},{key:'km',label:'里程'},{key:'failed',label:'失效标记'},{key:'outcome',label:'判定'},{key:'phase',label:'归属期'}],(c.samples||[]).map(s=>({...s,outcome:SampleOutcome[s.outcome]})))+'</section>';
 const approve=c.approvable&&session.user.role==='admin'?'<p><button class="primary" id="approve-case" data-case="'+c.id+'">批准放行此达标验收案</button></p>':'';
 const dist=c.distributable?'<p class="hint">已批准，可前往“分发档案”建立以该验收案为依据的简报计划。</p>':'';
 return locked+phaseTable+(c.status==='open'?pending:'')+reportForm+approve+dist+log+samples;
}

export function bindAcceptance(refresh,id){
 document.querySelector('#case-form')?.addEventListener('submit',async e=>{
  e.preventDefault();
  const form=e.target,data=new FormData(form);
  const phases=[];
  for(let n=1;n<=3;n++){const size=data.get('size-'+n),alpha=data.get('alpha-'+n);if(!size||!alpha)continue;phases.push({size:Number(size),alpha:Number(alpha)});}
  const body={name:data.get('name'),model:data.get('model'),qualified_km:Number(data.get('qualified_km')),min_survival:Number(data.get('min_survival')),phases};
  if(data.get('dataset_id'))body.dataset_id=Number(data.get('dataset_id'));
  const b=form.querySelector('[type=submit]');b.disabled=true;
  try{const c=await acceptance.create(body);notify('验收案已开验，规则已锁定');location.hash='#/acceptance/'+c.id;}catch(error){notify(error.message,true);b.disabled=false;}
 });
 document.querySelectorAll('[data-approve]').forEach(button=>button.onclick=async()=>{try{await acceptance.approve(button.dataset.approve);notify('已批准，可建立分发计划');await refresh();}catch(error){notify(error.message,true);}});
 document.querySelector('#approve-case')?.addEventListener('click',async e=>{try{await acceptance.approve(e.target.dataset.case);notify('已批准放行');await refresh();}catch(error){notify(error.message,true);}});
 const file=document.querySelector('#report-file');
 file?.addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;const text=await f.text();const records=f.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);document.querySelector('[name=records]').value=JSON.stringify(records,null,2);}catch(error){notify(error.message,true);}});
 const reportForm=document.querySelector('#report-form');
 if(reportForm)submit(reportForm,async f=>{
  const records=JSON.parse(f.get('records'));
  const c=await acceptance.report(Number(id),{request_id:f.get('request_id'),records});
  notify(c.close_reason||(c.status==='open'?'报期已受理，继续验收':'报期已受理'));
  await refresh();
 });
}
