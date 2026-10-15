/* The city planner only reads snapshots and renders proposed changes. */
(function (root) {
  'use strict';
  const ELEMENTS = ['金', '木', '水', '火', '土'];
  const RESOURCE_JOBS = new Set(['种植', '伐木', '采矿']);
  const QUALITY = ['', '凡物', '优秀', '精致', '绝世', '传说', '神话'];
  const QUALITY_COLORS = ['', '#666b65', '#42844a', '#3877aa', '#a049a9', '#a27d17', '#c04539'];
  const CELL = 48;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const fmt = value => finite(value) ? Number(value.toFixed(2)).toLocaleString('zh-CN') : '—';
  const coord = value => value && finite(value.x) && finite(value.y) ? `(${value.x}, ${value.y})` : '无';
  const groupKey = row => `${row.template}:${row.ownerId}`;
  const roundAura = (value, tier) => Math.round((10 * Number(value || 0)) * (1 + Number(tier || 0) * .15)) / 10;

  function normalizeDefaults(snapshot) {
    const defaults = snapshot.planningDefaults || {};
    const requests = new Map((defaults.facilityRequests || []).map(row => [groupKey(row), row]));
    const facilityRequests = (snapshot.facilityGroups || []).map(group => {
      const configured = requests.get(groupKey(group));
      return {template:group.template, ownerId:group.ownerId, count:configured?.count ?? group.count,
        minWorkers:RESOURCE_JOBS.has(group.jobType) ? 1 : (configured?.minWorkers ?? 1)};
    });
    const workerAssignments = (defaults.workerAssignments || []).map(row => ({workerId:row.workerId, jobType:row.jobType}));
    const jobs = [...new Set([...(snapshot.facilityGroups || []).map(g => g.jobType),
      ...(defaults.jobRequirements || []).map(r => r.jobType)])];
    const jobRequirements = jobs.map(jobType => {
      const request = (defaults.jobRequirements || []).find(row => row.jobType === jobType);
      const count = workerAssignments.filter(w => w.jobType === jobType).length;
      return {jobType, workerCount:request?.workerCount ?? count,
        ...(RESOURCE_JOBS.has(jobType) ? {minFacilitiesPerWorker:request?.minFacilitiesPerWorker ?? 1, allocation:'exclusive'} : {})};
    });
    return {mode:defaults.mode || 'migration', maxPedestals:defaults.maxPedestals ?? snapshot.pedestalLimit?.maximum ?? snapshot.counts?.pedestals ?? 0,
      seconds:defaults.seconds ?? 60, allowMainBuildingMoves:defaults.allowMainBuildingMoves ?? true, lockedBuildingIds:defaults.lockedBuildingIds || [],
      lockedFacilityIds:defaults.lockedFacilityIds || [], lockedWorkerIds:defaults.lockedWorkerIds || [],
      priorityStrategy:defaults.priorityStrategy || 'focus', facilityRequests, jobRequirements, workerAssignments};
  }

  function validateOptions(snapshot, options) {
    const errors = [];
    const integer = (value, label, min=0) => {
      if (!Number.isInteger(value) || value < min) errors.push(`${label}须为不小于 ${min} 的整数。`);
    };
    integer(options.maxPedestals, '灵台数');
    integer(options.seconds, '搜索时间', 5);
    if (options.seconds > 600) errors.push('搜索时间最多 600 秒。');
    const maximum = snapshot.pedestalLimit?.maximum;
    if (finite(maximum) && options.maxPedestals > maximum) errors.push(`灵台数超过存档可建上限 ${maximum}。`);
    const byJob = new Map();
    const byOwner = new Map();
    for (const request of options.facilityRequests) {
      const group = snapshot.facilityGroups.find(g => groupKey(g) === groupKey(request));
      if (!group) { errors.push('存在不属于当前存档的设施分组，请重新读取。'); continue; }
      integer(request.count, `${group.name}数量`); integer(request.minWorkers, `${group.name}每座人数`);
      const totals = byJob.get(group.jobType) || {count:0, minimum:0};
      totals.count += request.count; totals.minimum += request.count * request.minWorkers;
      byJob.set(group.jobType, totals);
      byOwner.set(group.ownerId, (byOwner.get(group.ownerId) || 0) + request.count);
    }
    for (const [ownerId, count] of byOwner) {
      const owner = snapshot.buildings.find(b => b.id === ownerId);
      if (owner && finite(owner.facilityCapacity) && count > owner.facilityCapacity)
        errors.push(`${owner.name} #${ownerId} 的设施总数 ${count} 超过容量 ${owner.facilityCapacity}。`);
    }
    const seen = new Set();
    for (const row of options.workerAssignments) {
      if (seen.has(row.workerId)) errors.push('同一人物不能同时承担两个主要工种。');
      seen.add(row.workerId);
      if (!(snapshot.workers || []).some(w => w.id === row.workerId && w.inMainCity)) errors.push(`人物 ${row.workerId} 不在当前主城。`);
    }
    for (const request of options.jobRequirements) {
      integer(request.workerCount, `${request.jobType}人数`);
      const total = byJob.get(request.jobType) || {count:0, minimum:0};
      const selected = options.workerAssignments.filter(w => w.jobType === request.jobType).length;
      if (selected !== request.workerCount) errors.push(`${request.jobType}需要 ${request.workerCount} 人，已选 ${selected} 人；请到“人员”页签调整。`);
      if (total.count === 0 && request.workerCount > 0) errors.push(`${request.jobType}设施为 0，请将对应人数和人物职责一起调整。`);
      if (RESOURCE_JOBS.has(request.jobType)) {
        integer(request.minFacilitiesPerWorker, `${request.jobType}每人至少负责数`);
        if (total.count > 0 && request.workerCount < 1) errors.push(`${request.jobType}有 ${total.count} 块设施，至少需要 1 人负责。`);
        if (request.workerCount * request.minFacilitiesPerWorker > total.count)
          errors.push(`${request.jobType}共 ${total.count} 块，无法满足 ${request.workerCount} 人各至少 ${request.minFacilitiesPerWorker} 块的独立主分工。`);
      } else if (request.workerCount < total.minimum) {
        errors.push(`${request.jobType}各座合计至少需要 ${total.minimum} 个独立站位人员，目前设置 ${request.workerCount} 人。`);
      }
    }
    return [...new Set(errors)];
  }

  function assignMissingWorkers(snapshot, options) {
    const assignments = options.workerAssignments.map(row => ({...row}));
    const busy = new Set(assignments.map(row => row.workerId));
    const missing = [];
    for (const requirement of options.jobRequirements) {
      let remaining = requirement.workerCount - assignments.filter(row => row.jobType === requirement.jobType).length;
      if (remaining <= 0) continue;
      const candidates = snapshot.workers.filter(w => w.inMainCity && finite(w.rangePixels) && !busy.has(w.id)).sort((a,b) => {
        const ability = worker => worker.stats?.rows?.find(r => r.jobType === requirement.jobType)?.value ?? -1;
        return ability(b) - ability(a) || String(a.id).localeCompare(String(b.id));
      });
      for (const worker of candidates.slice(0, remaining)) {
        assignments.push({workerId:worker.id, jobType:requirement.jobType}); busy.add(worker.id); remaining--;
      }
      if (remaining > 0) missing.push(`${requirement.jobType}仍缺 ${remaining} 人`);
    }
    return {assignments, missing};
  }

  function makeScene(snapshot, result=null) {
    const layout = result?.layout;
    const savedBuildings = new Map((snapshot.buildings || []).map(b => [String(b.id), b]));
    const movedBuildings = new Map((layout?.buildings || []).map(b => [String(b.buildingId ?? b.id), b]));
    const buildings = (snapshot.buildings || []).filter(b => b.template !== 67).map(b => ({...b, ...(movedBuildings.get(String(b.id)) || {}), kind:'building', key:`b:${b.id}`}));
    const pedestals = (layout ? layout.pedestals || [] : snapshot.buildings.filter(b => b.template === 67)).map(p => {
      const buildingId = p.buildingId ?? p.id;
      const identity = buildingId ?? `item-${p.itemId}`;
      return {...(savedBuildings.get(String(buildingId)) || {}), ...p, id:identity, buildingId,
        width:1,height:1,template:67,name:'置物台',kind:'pedestal',key:`p:${identity}`};
    });
    const savedFacilities = new Map((snapshot.facilities || []).map(f => [String(f.id), f]));
    const facilities = (layout ? layout.facilities || [] : snapshot.facilities || []).map(f => {
      const group = snapshot.facilityGroups?.find(g => g.template === f.template && g.ownerId === f.ownerId);
      return {...(group || {}), ...(savedFacilities.get(String(f.id)) || {}), ...f, width:1,height:1,kind:'facility',key:`f:${f.id}`};
    });
    const plannedWorkers = new Map((layout?.workers || []).map(w => [String(w.workerId ?? w.id), w]));
    const workers = (snapshot.workers || []).filter(w => w.inMainCity).map(w => {
      const planned = plannedWorkers.get(String(w.id));
      return {...w, ...(planned || {}), id:w.id, workerId:w.id, jobType:planned?.jobType || w.currentTaskType,
        kind:'worker',width:1,height:1,key:`w:${w.id}`};
    });
    const resources = (snapshot.resources || []).map((r,i) => ({...r,kind:'resource',width:1,height:1,key:`r:${i}`}));
    return {buildings,pedestals,facilities,workers,resources, objects:[...resources,...buildings,...pedestals,...facilities,...workers]};
  }

  function auraAt(snapshot, scene, x, y) {
    const items = new Map(snapshot.items.map(i => [i.id, i]));
    const aura = {qi:0,elements:[0,0,0,0,0],contributors:[]};
    for (const pedestal of scene.pedestals) {
      if (Math.abs(pedestal.x - x) + Math.abs(pedestal.y - y) > 3) continue;
      const item = items.get(pedestal.itemId);
      if (!item) continue;
      aura.qi += roundAura(item.qi, pedestal.tier);
      aura.elements = aura.elements.map((v,i) => v + roundAura(item.elements[i], pedestal.tier));
      aura.contributors.push({pedestal,item});
    }
    return aura;
  }

  function createMoves(snapshot, result) {
    if (!result?.layout) return [];
    const before = makeScene(snapshot), after = makeScene(snapshot,result);
    const original = new Map(before.objects.filter(o => o.kind !== 'resource').map(o => [o.key,o]));
    const next = new Map(after.objects.filter(o => o.kind !== 'resource').map(o => [o.key,o]));
    const moves = [];
    for (const [key, object] of next) {
      const old = original.get(key);
      if (!old || old.x !== object.x || old.y !== object.y) moves.push({key, kind:object.kind, name:object.name || object.id,
        action:old?'移动':'新增', from:old?{x:old.x,y:old.y}:null,to:{x:object.x,y:object.y},object});
      if (old && object.kind === 'pedestal' && old.itemId !== object.itemId) moves.push({key,kind:'item',name:object.name,
        action:'更换灵物',from:{x:object.x,y:object.y},to:{x:object.x,y:object.y},oldItemId:old.itemId,itemId:object.itemId,object});
      if (old && object.kind === 'worker' && (old.jobType || '') !== (object.jobType || '')) moves.push({key,kind:'job',name:object.name,
        action:'调整职责',from:{x:old.x,y:old.y},to:{x:object.x,y:object.y},oldJob:old.jobType,jobType:object.jobType,object});
    }
    for (const [key, object] of original) if (!next.has(key)) moves.push({key,kind:object.kind,name:object.name || object.id,action:'移除',from:{x:object.x,y:object.y},to:null,object});
    return moves;
  }

  function comparisonMarkers(snapshot, result) {
    if(!result?.layout)return {};
    const before=new Map(makeScene(snapshot).objects.filter(o=>o.kind!=='resource').map(o=>[o.key,o]));
    const after=new Map(makeScene(snapshot,result).objects.filter(o=>o.kind!=='resource').map(o=>[o.key,o]));
    const counters={building:0,pedestal:0,facility:0,worker:0};
    const prefixes={building:'建',pedestal:'台',facility:'设',worker:'人'};
    const markers={};
    const keys=[...new Set([...before.keys(),...after.keys()])].sort((a,b)=>a.localeCompare(b,'en',{numeric:true}));
    for(const key of keys){
      const old=before.get(key),next=after.get(key),kind=(next||old).kind;
      const label=prefixes[kind]+String(++counters[kind]).padStart(2,'0');
      if(old&&next&&old.x===next.x&&old.y===next.y)continue;
      markers[key]={label,kind,status:!old?'added':!next?'removed':'moved',
        before:old?{x:old.x,y:old.y}:null,after:next?{x:next.x,y:next.y}:null};
    }
    return markers;
  }

  function objectInBounds(object,bounds) {
    if(!finite(object.x)||!finite(object.y))return false;
    const width=object.width||1,height=object.height||1;
    const left=object.x-Math.floor((width-1)/2),top=object.y-Math.floor((height-1)/2);
    return left<=bounds.xmax&&left+width-1>=bounds.xmin&&top<=bounds.ymax&&top+height-1>=bounds.ymin;
  }

  function toCsv(rows) {
    return '\ufeff' + rows.map(row => row.map(value => {
      let str = String(value ?? '');
      if (/^[=+\-@\t\r]/.test(str)) str = "'" + str;
      return '"' + str.replace(/"/g,'""') + '"';
    }).join(',')).join('\r\n');
  }
  function diagnosticText(result) {
    const info=result?.diagnostics;
    if(typeof info==='string')return info;
    if(Array.isArray(info))return info.map(x=>typeof x==='string'?x:x.message||'').filter(Boolean).join('\n');
    return [...(info?.errors || []),...(info?.warnings || [])].join('\n');
  }
  function lockDescriptor(snapshot, object) {
    if(object.kind==='building'||object.kind==='pedestal'){
      const original=snapshot.buildings.find(b=>String(b.id)===String(object.buildingId ?? object.id));
      return original?{field:'lockedBuildingIds',id:original.id,original,forced:original.canMove===false}:null;
    }
    if(object.kind==='facility'){
      const original=snapshot.facilities.find(f=>String(f.id)===String(object.id));
      return original?{field:'lockedFacilityIds',id:original.id,original}:null;
    }
    if(object.kind==='worker'){
      const original=snapshot.workers.find(w=>w.id===object.id);
      return original?{field:'lockedWorkerIds',id:original.id,original}:null;
    }
    return null;
  }
  function acceptedPlan(previous, pendingOptions, job) {
    const terminal=['completed','done','complete','succeeded','success','cancelled','canceled'];
    if(terminal.includes(job.state)&&job.result?.layout)return {result:job.result,resultOptions:pendingOptions};
    return {result:previous.result,resultOptions:previous.resultOptions};
  }
  const api = {normalizeDefaults, validateOptions, assignMissingWorkers, makeScene, auraAt, createMoves, comparisonMarkers, objectInBounds, toCsv, roundAura, esc, diagnosticText, lockDescriptor, acceptedPlan};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  const state = {snapshot:null,options:null,assets:{},result:null,resultOptions:null,pendingOptions:null,scene:null,scenes:{},view:'before',selected:null,selectedView:'before',comparison:false,
    moves:[],markers:{},jobId:null,poll:null,dirty:false,scale:1,centerX:0,centerY:0,baseScale:1,drag:null,loadToken:0};
  const svgNS = 'http://www.w3.org/2000/svg';
  function svg(name,attributes={},text) {
    const element = document.createElementNS(svgNS,name);
    for (const [key,value] of Object.entries(attributes)) if (value != null) element.setAttribute(key,String(value));
    if (text != null) element.textContent = text;
    return element;
  }
  function assetUrl(key) { const file = state.assets?.[key]?.file; return file ? `/assets/${encodeURIComponent(file)}` : ''; }
  function assetKey(object) { return object.kind === 'worker' ? object.asset : `${object.kind === 'facility' ? 'ss' : 'jz'}${object.template}`; }
  function imageTag(key,cls='',alt='') { const url=assetUrl(key); return url?`<img class="${esc(cls)}" src="${esc(url)}" alt="${esc(alt)}" loading="lazy">`:''; }
  function message(text,error=false) { $('formMessage').textContent=text; $('formMessage').classList.toggle('error',error); }
  function markDirty() { state.dirty=true; $('dirtyBadge').hidden=false; message(state.result?'设置已改变。当前地图结果仍对应上一次计算；请重新规划。':'配置已更新，可以开始规划。'); refreshJobHints(); }
  async function request(url,body) {
    const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    let data; try {data=await response.json();} catch {throw new Error(`服务返回了无法读取的内容（${response.status}）。`);}
    if(!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
    return data;
  }
  function changeTab(name) {
    for (const button of document.querySelectorAll('[data-tab]')) {const active=button.dataset.tab===name;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;$(`panel-${button.dataset.tab}`).hidden=!active;}
  }
  function readOptions() {
    const options=JSON.parse(JSON.stringify(state.options));
    options.mode=document.querySelector('input[name=mode]:checked').value;
    const number=id=>$(id).value.trim()===''?NaN:Number($(id).value);
    options.maxPedestals=number('maxPedestals');options.seconds=number('seconds');
    options.priorityStrategy=$('priorityStrategy').value;options.allowMainBuildingMoves=$('allowMainBuildingMoves').checked;
    const listed=new Set([...document.querySelectorAll('[data-lock]')].map(input=>Number(input.dataset.lock)));
    options.lockedBuildingIds=[...(options.lockedBuildingIds || []).filter(id=>!listed.has(id)),...[...document.querySelectorAll('[data-lock]:checked')].map(input=>Number(input.dataset.lock))];
    options.facilityRequests=options.facilityRequests.map((row,i)=>({...row,count:number(`facility-count-${i}`),minWorkers:RESOURCE_JOBS.has(state.snapshot.facilityGroups[i].jobType)?1:number(`facility-workers-${i}`)}));
    options.jobRequirements=options.jobRequirements.map((row,i)=>({...row,workerCount:number(`job-count-${i}`),...(RESOURCE_JOBS.has(row.jobType)?{minFacilitiesPerWorker:number(`job-minimum-${i}`),allocation:'exclusive'}:{})}));
    options.workerAssignments=[...document.querySelectorAll('[data-worker]')].filter(select=>select.value).map(select=>({workerId:select.dataset.worker,jobType:select.value}));
    return options;
  }
  function renderSettings() {
    const {snapshot,options}=state;
    $('maxPedestals').value=options.maxPedestals;$('seconds').value=options.seconds;
    $('priorityStrategy').value=options.priorityStrategy;$('allowMainBuildingMoves').checked=options.allowMainBuildingMoves;
    document.querySelector(`input[name=mode][value=${options.mode}]`).checked=true;
    $('pedestalHint').textContent=`存档已摆 ${snapshot.counts?.pedestals ?? 0} 座 · 可建上限 ${snapshot.pedestalLimit?.maximum ?? '未确认'} · 灵物 ${snapshot.items.length} 件`;
    $('buildingLocks').innerHTML=snapshot.buildings.filter(b=>b.template!==67).map(b=>`<label><input type="checkbox" data-lock="${b.id}" ${options.lockedBuildingIds.includes(b.id)||b.canMove===false?'checked':''} ${b.canMove===false?'disabled data-game-fixed':''}>${esc(b.name)} #${b.id}<small title="${esc(b.moveRestriction || '')}">${b.canMove===false?'游戏状态暂不可搬':coord(b)}</small></label>`).join('');
    $('facilityRequests').innerHTML=snapshot.facilityGroups.map((g,i)=>{
      const req=options.facilityRequests[i];
      return `<div class="facility-row"><div><div class="entity-name">${esc(g.name)} <small>${ELEMENTS[g.element] || ''}</small></div><div class="entity-sub">${esc(g.ownerName)} #${g.ownerId}<br>主建筑共享容量 ${g.ownerCapacity}</div></div><label>数量<input aria-label="${esc(g.name)} #${g.ownerId} 数量" id="facility-count-${i}" type="number" min="0" step="1" value="${req.count}"></label>${RESOURCE_JOBS.has(g.jobType)?'<span class="resource-note">下方配置<br>人数与份额</span>':`<label>每座至少人数<input aria-label="${esc(g.name)} #${g.ownerId} 每座至少人数" id="facility-workers-${i}" type="number" min="0" step="1" value="${req.minWorkers}"></label>`}</div>`;
    }).join('');
    $('jobRequirements').innerHTML=options.jobRequirements.map((r,i)=>`<div class="job-row"><h3>${esc(r.jobType)}<span id="job-selected-${i}"></span></h3><div class="field-grid"><label>工作人员数<input id="job-count-${i}" aria-label="${esc(r.jobType)} 工作人员数" type="number" min="0" step="1" value="${r.workerCount}"></label>${RESOURCE_JOBS.has(r.jobType)?`<label>每人至少负责几块<input id="job-minimum-${i}" aria-label="${esc(r.jobType)} 每人至少负责几块" type="number" min="0" step="1" value="${r.minFacilitiesPerWorker}"></label>`:'<p class="small-note">每个人只计入一座设施的主要工作人数。</p>'}</div><p id="job-summary-${i}" class="small-note"></p></div>`).join('');
    const assignment=new Map(options.workerAssignments.map(r=>[r.workerId,r.jobType]));
    $('workerRoster').innerHTML=snapshot.workers.filter(w=>w.inMainCity).map(w=>{
      const suggestion=snapshot.planningDefaults?.suggestions?.find(s=>s.workerId===w.id);
      return `<div class="worker-row">${imageTag(w.asset,'worker-avatar',w.name)||'<span class="worker-avatar placeholder">人</span>'}<div class="worker-info"><button data-detail-worker="${esc(w.id)}" title="查看人物详细属性">${esc(w.name)}</button><small>${esc(w.profession)} · Lv.${esc(w.stats?.level ?? '—')}</small><small title="${esc(suggestion?.reason || '')}">${suggestion?esc(suggestion.source==='saved'?'存档岗位':`建议：${suggestion.jobType || '待安排'}`):'手动安排'}</small></div><select data-worker="${esc(w.id)}" aria-label="${esc(w.name)} 主要职责"><option value="">不安排</option>${options.jobRequirements.map(j=>`<option value="${esc(j.jobType)}" ${assignment.get(w.id)===j.jobType?'selected':''}>${esc(j.jobType)}</option>`).join('')}</select></div>`;
    }).join('');
    $('readWarnings').textContent=(snapshot.warnings||[]).join('；');
    renderPriorityHint();refreshJobHints();refreshLockSummary();
  }
  function renderPriorityHint() {
    const text={focus:'先让渡劫灵气达到最高，再优先提高研究与操练各一座；其余设施随后提高。',balanced:'先保证渡劫灵气，再抬高全部研究、操练设施中最弱的一座，然后提高合计。',total:'先保证渡劫灵气，再提高研究与操练设施的总收益；单座之间可能不均衡。'};
    $('priorityDescription').textContent=text[$('priorityStrategy').value];
  }
  function refreshJobHints() {
    if(!state.snapshot)return;
    const options=readOptions();
    options.jobRequirements.forEach((r,i)=>{
      const selected=options.workerAssignments.filter(w=>w.jobType===r.jobType).length;
      const total=options.facilityRequests.reduce((sum,f,k)=>sum+(state.snapshot.facilityGroups[k].jobType===r.jobType?f.count:0),0);
      const node=$(`job-selected-${i}`);node.textContent=`已选 ${selected} / ${fmt(r.workerCount)} 人`;node.className=selected===r.workerCount?'job-active-count':'job-short-count';
      $(`job-summary-${i}`).textContent=RESOURCE_JOBS.has(r.jobType)?`共 ${fmt(total)} 块 · 至少分配 ${fmt(r.workerCount*r.minFacilitiesPerWorker)} 块独立份额，剩余也会安排负责人。`:`共 ${fmt(total)} 座 · 人员会在相应工作范围内安排站位。`;
    });
    $('workerCountHint').textContent=`主城 ${state.snapshot.workers.filter(w=>w.inMainCity).length} 人 · 当前安排 ${options.workerAssignments.length} 人。点击名字查看装备和当前属性。`;
  }
  function refreshLockSummary() {
    if(!state.snapshot)return;
    const options=readOptions();
    $('lockSummary').textContent=`已锁定建筑／灵台 ${options.lockedBuildingIds.length} 个、设施 ${options.lockedFacilityIds.length} 个、人物 ${options.lockedWorkerIds.length} 人。点击地图对象可锁定原位。`;
  }

  function worldRect(bounds) { return {x:(bounds.xmin-1)*CELL,y:(bounds.ymin-1)*CELL,width:(bounds.xmax-bounds.xmin+1)*CELL,height:(bounds.ymax-bounds.ymin+1)*CELL}; }
  function mapBounds(){return $('unlockedOnly').checked?state.snapshot.bounds:state.snapshot.mapBounds;}
  function mapPanes(){return [{viewport:$('mapViewport'),canvas:$('cityMap'),view:state.comparison?'before':state.view},...(state.comparison?[{viewport:$('comparisonViewport'),canvas:$('comparisonMap'),view:'after'}]:[])];}
  function updateViewBox() {
    for(const {viewport,canvas} of mapPanes()){
      const width=viewport.clientWidth/state.scale,height=viewport.clientHeight/state.scale;
      if(width>0&&height>0)canvas.setAttribute('viewBox',`${state.centerX-width/2} ${state.centerY-height/2} ${width} ${height}`);
      if(state.snapshot){
        const bounds=worldRect(mapBounds());
        for(const badge of canvas.querySelectorAll('[data-badge-width]')){
          const badgeWidth=Number(badge.dataset.badgeWidth),badgeHeight=Number(badge.dataset.badgeHeight);
          const x=Math.max(bounds.x+badgeWidth/state.scale,Math.min(bounds.x+bounds.width,Number(badge.dataset.anchorX)));
          const y=Math.max(bounds.y,Math.min(bounds.y+bounds.height-badgeHeight/state.scale,Number(badge.dataset.anchorY)));
          badge.setAttribute('transform',`translate(${x} ${y}) scale(${1/state.scale}) translate(${-badgeWidth} 0)`);
        }
      }
    }
    $('zoomValue').textContent=`${Math.round(state.scale/state.baseScale*100)}%`;
  }
  function fitMap() {
    if(!state.snapshot)return;
    const rect=worldRect(mapBounds()),panes=mapPanes(),viewport=panes[0].viewport;
    const width=Math.min(...panes.map(p=>p.viewport.clientWidth)),height=Math.max(100,Math.min(...panes.map(p=>p.viewport.clientHeight))-50);
    const left=state.comparison||$('settingsCard').hidden||viewport.clientWidth<760?20:$('settingsCard').offsetWidth+40;
    const right=state.comparison||$('detailCard').hidden||viewport.clientWidth<900?20:$('detailCard').offsetWidth+40;
    const usable=Math.max(180,width-left-right);
    state.scale=Math.min(usable/(rect.width+CELL),height/(rect.height+CELL));state.baseScale=state.scale;
    state.centerX=rect.x+rect.width/2+(width/2-left-usable/2)/state.scale;
    state.centerY=rect.y+rect.height/2;
    updateViewBox();
  }
  function zoom(factor,clientX,clientY,viewport=$('mapViewport')) {
    if(!state.snapshot)return;
    const rect=viewport.getBoundingClientRect();
    const x=clientX==null?rect.width/2:clientX-rect.left,y=clientY==null?rect.height/2:clientY-rect.top;
    const offsetX=x-rect.width/2,offsetY=y-rect.height/2;
    const wx=state.centerX+offsetX/state.scale,wy=state.centerY+offsetY/state.scale;
    state.scale=Math.max(state.baseScale*.35,Math.min(state.baseScale*12,state.scale*factor));
    state.centerX=wx-offsetX/state.scale;state.centerY=wy-offsetY/state.scale;updateViewBox();
  }
  function objectBounds(object) {
    const width=object.width||1,height=object.height||1;
    return {x:(object.x-Math.floor((width-1)/2)-1)*CELL,y:(object.y-Math.floor((height-1)/2)-1)*CELL,width:width*CELL,height:height*CELL};
  }
  function renderPane(canvas,scene,view) {
    const fragment=document.createDocumentFragment(),bounds=worldRect(mapBounds()),unlocked=worldRect(state.snapshot.bounds);
    const defs=svg('defs');
    const clipId=`mapClip-${canvas.id}`;
    const clip=svg('clipPath',{id:clipId,clipPathUnits:'userSpaceOnUse'});clip.append(svg('rect',bounds));defs.append(clip);fragment.append(defs);
    const group=svg('g',{'clip-path':`url(#${clipId})`});
    group.append(svg('rect',{...bounds,fill:'#dfe5df'}));group.append(svg('rect',{...unlocked,fill:'#d2dfbf',stroke:'#a7bd98','stroke-width':2}));
    let path='';for(let x=bounds.x;x<=bounds.x+bounds.width;x+=CELL)path+=`M${x} ${bounds.y}v${bounds.height}`;
    for(let y=bounds.y;y<=bounds.y+bounds.height;y+=CELL)path+=`M${bounds.x} ${y}h${bounds.width}`;
    group.append(svg('path',{d:path,stroke:'#98ae8e','stroke-opacity':.4,'stroke-width':.7,fill:'none'}));
    const selected=scene.objects.find(o=>o.key===state.selected&&objectInBounds(o,mapBounds()));
    if(selected?.kind==='pedestal'){
      for(let dx=-3;dx<=3;dx++)for(let dy=-3;dy<=3;dy++)if(Math.abs(dx)+Math.abs(dy)<=3)
        group.append(svg('rect',{x:(selected.x+dx-1)*CELL,y:(selected.y+dy-1)*CELL,width:CELL,height:CELL,fill:'#a58db725','pointer-events':'none'}));
    }
    if(selected?.kind==='worker'&&finite(selected.rangeTiles))group.append(svg('circle',{cx:(selected.x-.5)*CELL,cy:(selected.y-.5)*CELL,r:selected.rangeTiles*CELL,class:'selected-range'}));
    const sorted=scene.objects.filter(o=>objectInBounds(o,mapBounds())).sort((a,b)=>(a.kind==='resource'?-1:b.kind==='resource'?1:0)||a.y-b.y||(a.kind==='worker'?1:-1));
    const badges=svg('g',{class:'comparison-markers'});
    for(const object of sorted){
      const rect=objectBounds(object),marker=state.markers[object.key];
      const sideName=view==='after'?'规划':'当前';
      const node=svg('g',{'data-object':object.key,'data-view':view,class:`map-object map-${object.kind}${state.selected===object.key?' selected':''}`,tabindex:0,role:'button','aria-label':`${sideName} ${marker?.label || ''} ${object.name || '设施'} ${coord(object)}`});
      node.append(svg('title',{},`${object.name || '设施'} ${coord(object)} · 点击查看`));
      let fill=object.kind==='pedestal'?'#d9cce0':object.kind==='facility'?(['研究','操练'].includes(object.jobType)?'#c9dae9':'#dce5ca'):'#d7d5bc';
      if(object.kind==='resource')fill='#bdcead';
      const footprint=object.kind==='worker'?svg('circle',{cx:(object.x-.5)*CELL,cy:(object.y-.5)*CELL,r:15,class:'object-footprint'}):svg('rect',{x:rect.x+2,y:rect.y+2,width:rect.width-4,height:rect.height-4,rx:object.kind==='pedestal'?7:3,fill,stroke:'#93a58c','stroke-width':.8,class:'object-footprint'});
      node.append(footprint);
      const url=assetUrl(assetKey(object));
      if(url)node.append(svg('image',{href:url,x:rect.x+3,y:rect.y+2,width:rect.width-6,height:rect.height-8,preserveAspectRatio:'xMidYMid meet',class:'object-image',opacity:object.kind==='resource'?.6:1}));
      if(object.kind==='pedestal'){
        const item=state.snapshot.items.find(i=>i.id===object.itemId);
        const itemUrl=item&&assetUrl(`lw${item.template}`);
        if(itemUrl)node.append(svg('image',{href:itemUrl,x:rect.x+9,y:rect.y+1,width:30,height:30,preserveAspectRatio:'xMidYMid meet',class:'object-image'}));
        if(item)node.append(svg('circle',{cx:rect.x+39,cy:rect.y+8,r:4,fill:QUALITY_COLORS[item.quality]||'#8a6b9e',stroke:'#fff','stroke-width':1}));
      }
      if(object.kind!=='resource')node.append(svg('text',{x:rect.x+rect.width/2,y:rect.y+rect.height-3,'text-anchor':'middle',class:'object-label'},object.kind==='pedestal'?`台 ${object.id}`:object.name || object.jobType || object.id));
      group.append(node);
      if(marker){
        const badgeWidth=Math.max(30,marker.label.length*7+7);
        const badge=svg('g',{'data-object':object.key,'data-view':view,'data-badge-width':badgeWidth,'data-badge-height':marker.status==='moved'?18:33,'data-anchor-x':rect.x+rect.width-2,'data-anchor-y':rect.y+2,class:`comparison-badge badge-${marker.kind}${state.selected===object.key?' selected':''}`});
        badge.append(svg('rect',{x:0,y:0,width:badgeWidth,height:18,rx:4}));
        badge.append(svg('text',{x:badgeWidth/2,y:12.5,'text-anchor':'middle'},marker.label));
        if(marker.status!=='moved'){
          badge.append(svg('rect',{x:0,y:20,width:badgeWidth,height:13,rx:3,class:'comparison-status-background'}));
          badge.append(svg('text',{x:badgeWidth/2,y:29.5,'text-anchor':'middle',class:'comparison-status'},marker.status==='added'?'新增':'移除'));
        }
        badges.append(badge);
      }
    }
    const selectedBadge=badges.querySelector('.selected');if(selectedBadge)badges.append(selectedBadge);
    group.append(badges);
    fragment.append(group);
    const step=$('unlockedOnly').checked?2:5;
    for(let x=mapBounds().xmin;x<=mapBounds().xmax;x+=step)fragment.append(svg('text',{x:(x-.5)*CELL,y:bounds.y-9,'text-anchor':'middle',fill:'#6c8172','font-size':10},x));
    for(let y=mapBounds().ymin;y<=mapBounds().ymax;y+=step)fragment.append(svg('text',{x:bounds.x-9,y:(y-.5)*CELL+3,'text-anchor':'end',fill:'#6c8172','font-size':10},y));
    canvas.replaceChildren(fragment);
  }
  function renderMap() {
    if(!state.snapshot)return;
    state.scenes={before:makeScene(state.snapshot),after:state.result?makeScene(state.snapshot,state.result):null};
    state.scene=state.scenes[state.view];
    for(const pane of mapPanes()){pane.viewport.dataset.view=pane.view;renderPane(pane.canvas,state.scenes[pane.view],pane.view);}
    $('primaryMapLabel').innerHTML=state.comparison||state.view==='before'?'<strong>当前主城</strong><span>存档原位置</span>':'<strong>规划结果</strong><span>建议摆放位置</span>';
    $('primaryPane').setAttribute('aria-label',state.comparison||state.view==='before'?'当前主城地图':'规划结果地图');
    $('mapEmpty').hidden=true;updateViewBox();
    renderMetrics();
  }

  function facilityWorkers(facility,scene,view) {
    if(view!=='after')return {primary:[],assist:[]};
    const primary=scene.workers.filter(w=>facility.primaryWorkerIds?facility.primaryWorkerIds.includes(w.id):(w.facilityIds||[]).map(String).includes(String(facility.id)));
    const assist=scene.workers.filter(w=>facility.assistingWorkerIds?facility.assistingWorkerIds.includes(w.id):!w.fixed&&w.jobType===facility.jobType&&!primary.some(p=>p.id===w.id)&&finite(w.rangeTiles)&&Math.hypot(w.x-facility.x,w.y-facility.y)<=w.rangeTiles+1e-9);
    return {primary,assist};
  }
  const facts=rows=>`<div class="detail-facts">${rows.map(([label,value])=>`<div><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join('')}</div>`;
  const elementGrid=values=>`<div class="aura-grid">${ELEMENTS.map((name,i)=>`<div><span>${name}</span>${fmt(values[i])}</div>`).join('')}</div>`;
  function workerChips(workers){return workers.length?`<div class="detail-chips">${workers.map(w=>`<button data-select-object="w:${esc(w.id)}">${esc(w.name)}</button>`).join('')}</div>`:'<p class="muted">无</p>';}
  function renderDetail(object,view=state.selectedView) {
    const snapshot=state.snapshot,scene=state.scenes[view],marker=state.markers[object.key];
    let html=`<div class="detail-title">${imageTag(assetKey(object),'',object.name)}<div><h2>${marker?`<span class="comparison-tag tag-${marker.kind}">${esc(marker.label)}</span> `:''}${esc(object.name || object.jobType || '设施')}</h2><p>${view==='after'?'规划位置':'当前存档位置'} ${coord(object)} · ${esc(object.kind==='worker'?object.id:object.id??object.template)}</p></div></div>`;
    if(state.result){
      const before=state.scenes.before.objects.find(o=>o.key===object.key),after=state.scenes.after.objects.find(o=>o.key===object.key);
      html+=`<div class="detail-comparison"><button data-detail-view="before" ${before?'':'disabled'} class="${view==='before'?'active':''}">当前 ${coord(before)}</button><button data-detail-view="after" ${after?'':'disabled'} class="${view==='after'?'active':''}">规划 ${coord(after)}</button>${state.comparison?'<button data-focus-pair>定位两边</button>':''}</div>`;
    }
    const lock=lockDescriptor(snapshot,object);
    if(lock){const options=readOptions();html+=`<label class="check muted"><input type="checkbox" data-object-lock ${options[lock.field].includes(lock.id)||lock.forced?'checked':''} ${state.jobId||lock.forced?'disabled':''} ${lock.forced?'data-game-fixed':''}>锁定存档原位 ${coord(lock.original)}</label>`;if(lock.forced)html+=`<p class="muted">${esc(lock.original.moveRestriction || '游戏状态暂不可搬')}。本次规划保持原位。</p>`;}
    else if(object.kind!=='resource')html+='<p class="muted">新增对象，没有可锁定的存档原位。</p>';
    if(object.kind==='pedestal'){
      const item=snapshot.items.find(i=>i.id===object.itemId);
      html+=facts([['灵台阶级',`${object.tier ?? 0} 阶`],['作用范围','上下左右 3 步']]);
      if(item){
        const old=snapshot.buildings.find(b=>b.id===item.placedBuildingId);
        html+=`<h3>这里放哪件灵物</h3><div class="detail-title">${imageTag(`lw${item.template}`,'',item.name)}<div><h2 style="color:${QUALITY_COLORS[item.quality]||'#615070'}">${esc(item.name)}</h2><p>${esc(QUALITY[item.quality] || `品质 ${item.quality}`)} · ${item.tier} 阶 · Lv.${item.level}</p></div></div>`;
        html+=`<div class="locate-box">${item.storage===18?'在游戏灵物库存中寻找':`当前放在 ${esc(old?.name || '置物台')} #${item.placedBuildingId} ${coord(old)}`}<br>用“名称、品质、阶级、等级、下方数值”核对同名物品。<br><span class="muted">存档槽位 ${item.storage}:${item.slot}（不是游戏内显示编号）</span></div>`;
        html+=facts([['灵物原始灵气',fmt(item.qi)],['此灵台发出灵气',fmt(roundAura(item.qi,object.tier))]]);
        html+='<h3>灵物原始五行</h3>'+elementGrid(item.elements);
        html+='<h3>经过灵台倍率后的五行</h3>'+elementGrid(item.elements.map(v=>roundAura(v,object.tier)));
      }else html+='<p class="detail-description">这座灵台未放置灵物。</p>';
    }else if(object.kind==='worker'){
      const suggestion=snapshot.planningDefaults?.suggestions?.find(s=>s.workerId===object.id);
      html+=facts([['主要职责',object.jobType || '未安排'],['实际工作范围',`${fmt(object.rangeTiles)} 格`],['职业',object.profession],['人物等级',object.stats?.level ?? '—']]);
      if(suggestion)html+=`<p class="detail-description">职责建议：${esc(suggestion.jobType || '待安排')}。${esc(suggestion.reason || '')}</p>`;
      const facilityIds=object.facilityIds||object.primaryFacilityIds||[];
      if(view==='after'){
        html+='<h3>主要负责设施</h3>'+(facilityIds.length?`<div class="detail-chips">${facilityIds.map(id=>{const f=scene.facilities.find(f=>String(f.id)===String(id));return `<button data-select-object="f:${esc(id)}">${esc(f?.name || id)} ${coord(f)}</button>`;}).join('')}</div>`:'<p class="muted">没有主要负责设施。</p>');
        const assisting=scene.facilities.filter(f=>f.assistingWorkerIds?f.assistingWorkerIds.includes(object.id):!object.fixed&&RESOURCE_JOBS.has(f.jobType)&&f.jobType===object.jobType&&!facilityIds.map(String).includes(String(f.id))&&finite(object.rangeTiles)&&Math.hypot(object.x-f.x,object.y-f.y)<=object.rangeTiles+1e-9);
        if(assisting.length)html+=`<h3>范围内可协助</h3><div class="detail-chips">${assisting.map(f=>`<button data-select-object="${esc(f.key)}">${esc(f.name)} ${coord(f)}</button>`).join('')}</div>`;
      }
      html+='<h3>当前穿戴的工作能力</h3><table class="detail-table">'+(object.stats?.rows||[]).filter(r=>r.group==='work'||r.group==='basic').map(r=>`<tr><td>${esc(r.label)}</td><td>${fmt(r.value)} ${esc(r.unit || '')}</td></tr>`).join('')+'</table>';
      html+='<h3>当前装备</h3><table class="detail-table">'+(object.stats?.equipment||[]).map(e=>`<tr><td>${esc(e.slotName)}</td><td>${esc(e.name)} · ${e.tier} 阶 Lv.${e.level}</td></tr>`).join('')+'</table>';
      html+=`<p class="muted">${esc(object.stats?.note || '当前常驻属性，不含临时战斗效果。')}</p>`;
    }else if(object.kind==='facility'){
      const aura=auraAt(snapshot,scene,object.x,object.y),value=aura.elements[object.element];
      const owner=scene.buildings.find(b=>b.id===object.ownerId);
      html+=facts([['工种',object.jobType],['所属主建筑',`${owner?.name || object.ownerId} #${object.ownerId}`],['所需五行',ELEMENTS[object.element]],['对应五行值',fmt(value)],['环境加成',`${fmt(Math.round(value/2*100)/100)}%`],['环境乘数',`×${fmt(1+value/200)}`]]);
      html+='<p class="muted">环境加成用于工作速度计算；最终产出还受人物、科技、暴击和实际目标切换影响。</p>';
      if(view==='after'){
        const workers=facilityWorkers(object,scene,view);html+='<h3>主要工作人员</h3>'+workerChips(workers.primary);
        if(RESOURCE_JOBS.has(object.jobType))html+='<h3>范围内可协助</h3>'+workerChips(workers.assist);
      }
      html+=`<h3>供给灵台 · ${aura.contributors.length} 座</h3><div class="detail-chips">${aura.contributors.map(c=>`<button data-select-object="${esc(c.pedestal.key)}">${esc(c.item.name)} ${coord(c.pedestal)}</button>`).join('')}</div>`;
    }else{
      html+=facts([['占地',`${object.width} × ${object.height}`],['建筑阶级',object.tier ?? '—']]);
      if(object.template===66){const aura=auraAt(snapshot,scene,object.x,object.y);html+='<h3>渡劫环境</h3>'+facts([['中心格灵气',fmt(aura.qi)],['供给灵台',`${aura.contributors.length} 座`]])+'<p class="muted">按游戏中心格读取。周边灵台没有距离衰减。</p>';}
      if(object.kind==='building'){
        const facilities=scene.facilities.filter(f=>f.ownerId===object.id);
        html+=facts([['设施数量',facilities.length],['设施容量',object.facilityCapacity ?? '—']]);
        html+=`<h3>关联设施</h3><div class="detail-chips">${facilities.map(f=>`<button data-select-object="${esc(f.key)}">${esc(f.name)} ${coord(f)}</button>`).join('')}</div>`;
      }
    }
    $('detailContent').innerHTML=html;$('detailCard').hidden=false;
  }
  function focusSelection(key,force=false) {
    const scenes=state.comparison?[state.scenes.before,state.scenes.after]:[state.scenes[state.view]];
    const objects=scenes.map(scene=>scene?.objects.find(o=>o.key===key)).filter(Boolean);
    if(!objects.length)return;
    if(force&&state.comparison)showSettings(false);
    const panes=mapPanes(),width=Math.min(...panes.map(p=>p.viewport.clientWidth)),height=Math.min(...panes.map(p=>p.viewport.clientHeight));
    const detailRect=$('detailCard').hidden?null:$('detailCard').getBoundingClientRect();
    const coveredBottom=state.comparison&&detailRect?Math.max(0,...panes.map(p=>{
      const rect=p.viewport.getBoundingClientRect();
      return detailRect.right>rect.left&&detailRect.left<rect.right&&detailRect.bottom>rect.top&&detailRect.top<rect.bottom?rect.bottom-Math.max(rect.top,detailRect.top):0;
    })):0;
    const availableWidth=Math.max(120,width-48),availableHeight=Math.max(80,height-48-coveredBottom);
    const safeCenterY=24+availableHeight/2;
    const visible=objects.every(o=>{
      const screenX=((o.x-.5)*CELL-state.centerX)*state.scale+width/2,screenY=((o.y-.5)*CELL-state.centerY)*state.scale+height/2;
      return screenX>24&&screenX<width-24&&screenY>24&&screenY<24+availableHeight;
    });
    if(!force&&visible)return;
    const boxes=objects.map(objectBounds),left=Math.min(...boxes.map(b=>b.x))-CELL,right=Math.max(...boxes.map(b=>b.x+b.width))+CELL;
    const top=Math.min(...boxes.map(b=>b.y))-CELL,bottom=Math.max(...boxes.map(b=>b.y+b.height))+CELL;
    const fit=Math.min(availableWidth/(right-left),availableHeight/(bottom-top));
    state.scale=Math.min(force?Math.max(state.scale,1):state.scale,fit);
    state.centerX=(left+right)/2;state.centerY=(top+bottom)/2+(height/2-safeCenterY)/state.scale;updateViewBox();
  }
  function selectObject(key,center=false,view=state.selectedView) {
    if(!state.scenes[view])return;
    const object=state.scenes[view].objects.find(o=>o.key===key);
    if(!object)return;
    state.selected=key;state.selectedView=view;
    if(!state.comparison&&state.view!==view){state.view=view;updateViewControls();}
    renderDetail(object,view);renderMap();
    if(center||state.comparison)focusSelection(key,center);
  }
  function renderMetrics() {
    const view=state.comparison?'after':state.view,scene=state.scenes[view];
    if(!scene)return;
    const tribulation=scene.buildings.find(b=>b.template===66);
    const d=tribulation?auraAt(state.snapshot,scene,tribulation.x,tribulation.y).qi:null;
    const values=scene.facilities.map(f=>({jobType:f.jobType,value:auraAt(state.snapshot,scene,f.x,f.y).elements[f.element]}));
    const best=job=>{const all=values.filter(f=>f.jobType===job);return all.length?Math.max(...all.map(f=>f.value)):null;};
    $('metricD').textContent=fmt(d);$('metricPriority').textContent=`${fmt(best('研究'))} / ${fmt(best('操练'))}`;$('metricS').textContent=fmt(values.reduce((s,f)=>s+f.value,0));
    if(view==='before')$('resultStatus').textContent='显示存档原位置与当前灵物。研究／操练显示各自最高单座五行。';
    else {
      const proof=state.result?.proof||{};
      const proven=proof.DProven===true;
      $('resultStatus').textContent=`${state.comparison?'下方数值为规划结果。':''}${proven?'渡劫灵气已达到上界。':`当前可行方案；渡劫理论上界 ${fmt(proof.DUpperBound)}，尚未证最优。`}${state.result?.validation?.passed?' 布局已独立校验。':''}\n${state.dirty?'设置已修改，结果仍对应上次配置。':'研究／操练显示各自最高单座五行；其余收益与迁移数仍为限时搜索结果。'}`;
    }
  }
  function renderMoves() {
    const items=new Map(state.snapshot.items.map(i=>[i.id,i]));
    state.moves=createMoves(state.snapshot,state.result);
    state.markers=comparisonMarkers(state.snapshot,state.result);
    const count=kind=>state.moves.filter(m=>m.kind===kind&&m.action==='移动').length;
    $('moveSummary').textContent=`建筑 ${count('building')} 次 · 灵台 ${count('pedestal')} 次 · 设施 ${count('facility')} 次 · 人物 ${count('worker')} 次迁移。同编号对应同一个对象；点击条目在双图中定位。物品更换和职责变更另列。`;
    $('moveList').innerHTML=state.moves.length?state.moves.map((m,i)=>`<button class="move-row" data-move="${i}">${state.markers[m.key]?`<span class="comparison-tag tag-${state.markers[m.key].kind}">${esc(state.markers[m.key].label)}</span> `:''}<span class="move-kind">${esc(m.action)}</span>${esc(m.name)}<small>${m.kind==='item'?`${esc(items.get(m.oldItemId)?.name || '空')} → ${esc(items.get(m.itemId)?.name || '空')} · ${coord(m.to)}`:m.kind==='job'?`${esc(m.oldJob || '自动')} → ${esc(m.jobType || '未安排')}`:`${coord(m.from)} → ${coord(m.to)}`}</small></button>`).join(''):'<p class="muted">此方案不需要改变现有位置。</p>';
    $('exportJson').disabled=false;$('exportCsv').disabled=false;
  }
  function download(filename,type,content) {const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function updateViewControls() {
    $('singleViewControls').hidden=state.comparison;
    for(const [id,value] of [['viewBefore','before'],['viewAfter','after']]){$(id).classList.toggle('active',value===state.view);$(id).setAttribute('aria-pressed',String(value===state.view));}
  }
  function showSettings(visible){
    $('settingsCard').hidden=!visible;$('toggleSettings').textContent=visible?'收起设置':'展开设置';$('toggleSettings').setAttribute('aria-expanded',String(visible));
  }
  function setComparison(enabled,fit=true){
    state.comparison=Boolean(enabled&&state.result);
    $('compareMaps').checked=state.comparison;$('comparisonPane').hidden=!state.comparison;
    $('mapStage').classList.toggle('comparing',state.comparison);$('workspace').classList.toggle('comparison-mode',state.comparison);
    if(state.comparison)showSettings(false);
    else if(state.selected&&state.scenes[state.selectedView])state.view=state.selectedView;
    updateViewControls();renderMap();
    const selected=state.scenes[state.selectedView]?.objects.find(o=>o.key===state.selected);if(selected)renderDetail(selected,state.selectedView);
    if(fit)fitMap();
  }
  function changeView(view) {
    if(view==='after'&&!state.result)return;
    state.view=view;state.selectedView=view;updateViewControls();
    renderMap();if(state.selected){const object=state.scenes[view]?.objects.find(o=>o.key===state.selected);if(object)renderDetail(object,view);else $('detailCard').hidden=true;}
  }
  function setRunning(running) {
    $('solveButton').disabled=running||!state.snapshot;$('loadButton').disabled=running;$('cancelButton').hidden=!running;
    $('solveButton').textContent=running?'正在规划…':'开始规划';
    for(const input of document.querySelectorAll('.settings-body input,.settings-body select,#autoAssignButton,#detailContent [data-object-lock]'))input.disabled=running||input.hasAttribute('data-game-fixed');
  }
  async function loadSnapshot() {
    const key=$('saveSelect').value;if(!key)return;
    const token=++state.loadToken;setRunning(true);message('正在读取稳定存档快照…');
    try{
      const response=await request('/api/load',{key});if(token!==state.loadToken)return;
      state.snapshot=response.snapshot?{...response.snapshot,planningDefaults:response.planningDefaults || response.snapshot.planningDefaults}:response;
      state.options=normalizeDefaults(state.snapshot);state.result=null;state.resultOptions=null;state.moves=[];state.markers={};state.selected=null;state.selectedView='before';state.dirty=false;
      $('dirtyBadge').hidden=true;$('detailCard').hidden=true;$('viewAfter').disabled=true;$('exportJson').disabled=true;$('exportCsv').disabled=true;
      state.comparison=false;$('compareMaps').checked=false;$('compareMaps').disabled=true;$('comparisonPane').hidden=true;$('comparisonMap').replaceChildren();$('mapStage').classList.remove('comparing');$('workspace').classList.remove('comparison-mode');
      $('moveList').innerHTML='';$('moveSummary').textContent='计算后，直接查看当前位置与目标位置的差异。';
      $('snapshotDate').textContent=`存档快照 ${state.snapshot.savedAt || state.snapshot.gameDate || state.snapshot.label}`;
      renderSettings();changeView('before');fitMap();message(`已读取 ${state.snapshot.counts?.buildings ?? state.snapshot.buildings.length} 座建筑、${state.snapshot.facilities.length} 座设施。请按需要调整。`);
      request('/api/assets').then(data=>{if(token===state.loadToken){
        state.assets=data.manifest||data;renderMap();
        for(const worker of state.snapshot.workers){const select=document.querySelector(`[data-worker="${CSS.escape(worker.id)}"]`),url=assetUrl(worker.asset);if(select&&url){const img=document.createElement('img');img.className='worker-avatar';img.src=url;img.alt=worker.name;select.closest('.worker-row').firstElementChild.replaceWith(img);}}
        const object=state.scenes[state.selectedView]?.objects.find(o=>o.key===state.selected);if(object)renderDetail(object,state.selectedView);
      }}).catch(error=>message(`存档已读取；图片暂不可用：${error.message}`));
    }catch(error){message(error.message,true);}finally{setRunning(false);}
  }
  async function solve() {
    if(!state.snapshot)return;
    const options=readOptions(),errors=validateOptions(state.snapshot,options);
    if(errors.length){message(errors.join('\n'),true);return;}
    state.pendingOptions=JSON.parse(JSON.stringify(options));setRunning(true);message('正在创建规划任务…');
    try {const job=await request('/api/solve',{snapshotId:state.snapshot.id,options});state.jobId=job.jobId;pollJob();}
    catch(error){state.jobId=null;setRunning(false);message(error.message,true);}
  }
  async function pollJob() {
    const jobId=state.jobId;if(!jobId)return;
    try{
      const job=await request(`/api/jobs/${encodeURIComponent(jobId)}`);if(state.jobId!==jobId)return;
      const progress=job.progress;
      const progressText=typeof progress==='string'?progress:progress?.message || progress?.stage || progress?.phase || '';
      if(progressText)message(progressText);
      if(['completed','done','complete','succeeded','success'].includes(job.state)||(['cancelled','canceled'].includes(job.state)&&job.result?.layout)){
        state.jobId=null;setRunning(false);
        if(job.result?.layout){Object.assign(state,acceptedPlan(state,state.pendingOptions,job));state.pendingOptions=null;state.options=JSON.parse(JSON.stringify(state.resultOptions));state.dirty=false;$('dirtyBadge').hidden=true;$('viewAfter').disabled=false;$('compareMaps').disabled=false;renderMoves();changeView('after');
          const diagnostic=diagnosticText(job.result);
          message(`${['cancelled','canceled'].includes(job.state)?'搜索已停止，保留本次已找到并校验的可行方案。':'规划完成。'}点击地图建筑查看灵物、人员与位置。${diagnostic?'\n'+diagnostic:''}`);
        }else message(diagnosticText(job.result) || job.result?.message || job.result?.error || '本次搜索未找到满足全部条件的方案，不等于已证明无解。可调整配置或搜索时间。',true);
        return;
      }
      if(['failed','error','cancelled','canceled','infeasible'].includes(job.state)){
        state.jobId=null;setRunning(false);message(job.error?.message || job.error || (job.state==='cancelled'||job.state==='canceled'?'搜索已停止，保留上一份结果。':'未找到可行方案。请检查配置或增加搜索时间。'),!['cancelled','canceled'].includes(job.state));return;
      }
      state.poll=setTimeout(pollJob,700);
    }catch(error){state.jobId=null;setRunning(false);message(`读取任务进度失败：${error.message}`,true);}
  }
  async function cancel() {if(!state.jobId)return;try{await request(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`,{});message('正在停止搜索…');}catch(error){message(error.message,true);}}

  function attachEvents() {
    $('loadButton').addEventListener('click',loadSnapshot);$('solveButton').addEventListener('click',solve);$('cancelButton').addEventListener('click',cancel);
    document.querySelector('.tabs').addEventListener('click',event=>{const button=event.target.closest('[data-tab]');if(button)changeTab(button.dataset.tab);});
    document.querySelector('.tabs').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const tabs=[...document.querySelectorAll('[data-tab]')],index=tabs.indexOf(document.activeElement);let next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;changeTab(tabs[next].dataset.tab);tabs[next].focus();event.preventDefault();});
    document.querySelector('.settings-body').addEventListener('change',event=>{if(event.target.matches('input,select')){markDirty();if(event.target.id==='priorityStrategy')renderPriorityHint();if(event.target.hasAttribute('data-lock'))refreshLockSummary();}});
    $('workerRoster').addEventListener('click',event=>{const button=event.target.closest('[data-detail-worker]');if(button)selectObject(`w:${button.dataset.detailWorker}`,true,state.comparison?'after':state.view);});
    $('autoAssignButton').addEventListener('click',()=>{const options=readOptions(),selected=assignMissingWorkers(state.snapshot,options);for(const select of document.querySelectorAll('[data-worker]'))select.value=selected.assignments.find(w=>w.workerId===select.dataset.worker)?.jobType || '';markDirty();message(selected.missing.length?`${selected.missing.join('；')}。已分配到其他工种的人员保持原职责，请手动调整。`:'已按当前能力补齐空缺人选。已有职责保持不变。',selected.missing.length>0);});
    $('toggleSettings').addEventListener('click',()=>showSettings($('settingsCard').hidden));
    $('closeDetail').addEventListener('click',()=>{$('detailCard').hidden=true;state.selected=null;renderMap();});
    $('detailContent').addEventListener('click',event=>{const target=event.target.closest('[data-select-object]');if(target)selectObject(target.dataset.selectObject,true,state.selectedView);const side=event.target.closest('[data-detail-view]');if(side)selectObject(state.selected,false,side.dataset.detailView);if(event.target.closest('[data-focus-pair]'))focusSelection(state.selected,true);});
    $('detailContent').addEventListener('change',event=>{if(!event.target.matches('[data-object-lock]'))return;const object=state.scenes[state.selectedView]?.objects.find(o=>o.key===state.selected),lock=object&&lockDescriptor(state.snapshot,object);if(!lock||lock.forced)return;const options=readOptions(),ids=new Set(options[lock.field]);if(event.target.checked)ids.add(lock.id);else ids.delete(lock.id);state.options[lock.field]=[...ids];if(lock.field==='lockedBuildingIds'){const checkbox=document.querySelector(`[data-lock="${lock.id}"]`);if(checkbox)checkbox.checked=event.target.checked;}markDirty();refreshLockSummary();});
    $('viewBefore').addEventListener('click',()=>changeView('before'));$('viewAfter').addEventListener('click',()=>changeView('after'));
    $('unlockedOnly').addEventListener('change',()=>{renderMap();fitMap();});$('compareMaps').addEventListener('change',()=>setComparison($('compareMaps').checked));$('fitMap').addEventListener('click',fitMap);
    $('zoomIn').addEventListener('click',()=>zoom(1.3));$('zoomOut').addEventListener('click',()=>zoom(1/1.3));
    $('fullscreenButton').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('workspace').requestFullscreen();}catch{message('当前浏览器未允许全屏，可以收起设置并放大地图。');}});
    document.addEventListener('fullscreenchange',()=>{$('fullscreenButton').textContent=document.fullscreenElement?'退出全屏':'全屏';setTimeout(fitMap,30);});
    for(const viewport of [$('mapViewport'),$('comparisonViewport')]){
      viewport.addEventListener('wheel',event=>{event.preventDefault();zoom(Math.exp(-event.deltaY*.0015),event.clientX,event.clientY,viewport);},{passive:false});
      viewport.addEventListener('pointerdown',event=>{if(event.button!==0)return;const object=event.target.closest('[data-object]');state.drag={id:event.pointerId,x:event.clientX,y:event.clientY,centerX:state.centerX,centerY:state.centerY,moved:false,target:object?.dataset.object,view:object?.dataset.view||viewport.dataset.view};viewport.setPointerCapture(event.pointerId);});
      viewport.addEventListener('pointermove',event=>{if(!state.drag||event.pointerId!==state.drag.id)return;const dx=event.clientX-state.drag.x,dy=event.clientY-state.drag.y;if(Math.abs(dx)+Math.abs(dy)>4)state.drag.moved=true;if(state.drag.moved){viewport.classList.add('dragging');state.centerX=state.drag.centerX-dx/state.scale;state.centerY=state.drag.centerY-dy/state.scale;updateViewBox();}});
      viewport.addEventListener('pointerup',event=>{if(!state.drag||event.pointerId!==state.drag.id)return;const drag=state.drag;state.drag=null;viewport.classList.remove('dragging');if(viewport.hasPointerCapture(event.pointerId))viewport.releasePointerCapture(event.pointerId);if(!drag.moved&&drag.target)selectObject(drag.target,false,drag.view);});
      viewport.addEventListener('pointercancel',()=>{state.drag=null;viewport.classList.remove('dragging');});
      viewport.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){const target=event.target.closest('[data-object]');if(target){event.preventDefault();selectObject(target.dataset.object,false,target.dataset.view||viewport.dataset.view);}}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();state.centerX+=event.key==='ArrowLeft'?-80/state.scale:event.key==='ArrowRight'?80/state.scale:0;state.centerY+=event.key==='ArrowUp'?-80/state.scale:event.key==='ArrowDown'?80/state.scale:0;updateViewBox();}});
      new ResizeObserver(()=>updateViewBox()).observe(viewport);
    }
    $('moveList').addEventListener('click',event=>{const node=event.target.closest('[data-move]');if(!node)return;const move=state.moves[Number(node.dataset.move)];if(!state.comparison)setComparison(true,false);selectObject(move.key,true,move.action==='移除'?'before':'after');});
    $('exportJson').addEventListener('click',()=>download('主城排布方案.json','application/json;charset=utf-8',JSON.stringify({schemaVersion:3,snapshotId:state.snapshot.id,saveKey:state.snapshot.key,savedAt:state.snapshot.savedAt,options:state.resultOptions,result:state.result},null,2)));
    $('exportCsv').addEventListener('click',()=>download('主城迁移清单.csv','text/csv;charset=utf-8',toCsv([['操作','对象','原坐标','目标坐标','灵物或职责'],...state.moves.map(m=>[m.action,m.name,coord(m.from),coord(m.to),m.kind==='item'?state.snapshot.items.find(i=>i.id===m.itemId)?.name:m.jobType||''])])));
  }
  async function init() {
    attachEvents();renderPriorityHint();
    try{
      const data=await request('/api/saves'),saves=data.saves||[];
      $('saveSelect').innerHTML=saves.length?saves.map(s=>`<option value="${esc(s.key)}">${esc(s.label || s.key)}${s.savedAt?' · '+esc(s.savedAt):''}</option>`).join(''):'<option value="">未找到可读存档</option>';
      if(saves.length)await loadSnapshot();else message('未找到可读存档，请确认本地服务指向正确游戏目录。',true);
    }catch(error){message(`无法连接读取服务：${error.message}`,true);$('saveSelect').innerHTML='<option value="">连接失败</option>';}
  }
  init();
})(typeof window==='undefined'?globalThis:window);
