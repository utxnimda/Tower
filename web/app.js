'use strict';

const $ = id => document.getElementById(id);
const names = ['金', '木', '水', '火', '土'];
const colors = ['#987020', '#3d803f', '#2c75aa', '#b95135', '#805d9f'];
const jobTypes = ['伐木', '采矿', '种植', '锻造', '工艺', '炼丹', '研究', '建造', '操练'];
const state = {
  snapshot: null, result: null, layout: 'baseline', heat: 'none', page: 'planner',
  excluded: new Set(), selection: null, hover: null, assets: {}, images: new Map(),
  cam: { zoom: 1, x: 0, y: 0, cell: 34 }, pointer: null, jobId: null,
  polling: false, solving: false, cssWidth: 0, cssHeight: 0, visual: null, placementTab: 'pedestals',
  workerJobs: new Map(), workerCompare: 'assigned', workerSort: 'original', selectedWorkerId: null,
};
const canvas = $('mapCanvas');
const ctx = canvas.getContext('2d');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const fmt = (value, digits = 1) => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: digits });
const same = (a, b) => String(a) === String(b);
const coordKey = (x, y) => `${x},${y}`;
const round1 = value => Math.round(value * 10) / 10;
function dateText(value) {
  if (value == null || value === '') return '已读取';
  const raw = typeof value === 'number' || /^\d{10,13}$/.test(String(value)) ? Number(value) : value;
  const date = new Date(typeof raw === 'number' && raw < 1e11 ? raw * 1000 : raw);
  if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ').slice(0, 19);
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}

async function api(path, body, method) {
  const response = await fetch(path, { method: method || (body === undefined ? 'GET' : 'POST'), headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  let data;
  try { data = await response.json(); } catch { throw new Error(`本地服务返回了非 JSON 响应 (${response.status})`); }
  if (!response.ok || data.error && !data.state) throw new Error(typeof data.error === 'string' ? data.error : data.error?.message || `请求失败 (${response.status})`);
  return data;
}
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4200); }
function fail(message) { $('formError').textContent = message; $('formError').hidden = false; }
function clearError() { $('formError').hidden = true; }
function showPage(page) {
  state.page = page;
  for (const key of ['planner', 'inventory', 'rules']) $(`${key}Page`).hidden = key !== page;
  document.querySelectorAll('[data-page]').forEach(button => button.classList.toggle('active', button.dataset.page === page));
  if (page === 'planner') requestAnimationFrame(resizeCanvas);
  if (page === 'inventory') renderInventory();
}
function itemById(id) { return state.snapshot?.items?.find(item => same(item.id, id)); }
function buildingById(id) { return state.snapshot?.buildings?.find(item => same(item.id, id)); }
function workerById(id) { return state.snapshot?.workers?.find(worker => same(worker.id, id)); }
function workerPortrait(worker, extraClass = '') {
  const asset = state.assets[worker.asset];
  return asset ? `<img class="worker-portrait ${extraClass}" src="/assets/${esc(asset.file || asset)}" alt="${esc(worker.name)}人物图">` : `<span class="worker-portrait portrait-missing ${extraClass}" title="此人物图片暂不可用" aria-label="此人物图片暂不可用">人</span>`;
}
function workerRadius(worker) { return worker.rangePixels != null && Number.isFinite(Number(worker.rangePixels)) ? Number(worker.rangePixels) / 100 : worker.rangeTiles != null && Number.isFinite(Number(worker.rangeTiles)) ? Number(worker.rangeTiles) : null; }
function workerCovers(worker, facility) { const radius = workerRadius(worker); return radius != null && !!worker.jobType && worker.jobType === facility.jobType && (worker.x - facility.x) ** 2 + (worker.y - facility.y) ** 2 <= radius ** 2; }
function visibleWorkers(layout) {
  const originals = (state.snapshot?.workers || []).filter(worker => worker.inMainCity && worker.x != null && worker.y != null && Number.isFinite(Number(worker.x)) && Number.isFinite(Number(worker.y)));
  const planned = Array.isArray(layout.workers) ? layout.workers : [];
  const result = planned.map(worker => ({ ...workerById(worker.workerId || worker.id), ...worker, workerId: worker.workerId || worker.id }));
  for (const worker of originals) if (!result.some(row => same(row.workerId, worker.id))) result.push({ ...worker, workerId: worker.id, jobType: jobTypes.includes(worker.currentTaskType) ? worker.currentTaskType : null, fixed: true });
  return result.filter(worker => worker.x != null && worker.y != null && Number.isFinite(Number(worker.x)) && Number.isFinite(Number(worker.y)));
}
function isTribulation(building) { return num(building.template) === 66 || String(building.name).includes('渡劫'); }
function isPedestal(building) { return num(building.template) === 67 || /置物台|灵台/.test(building.name || ''); }
function placed(item) { return item.placedBuildingId != null && item.placedBuildingId !== 0 || ['placed', 'pedestal', '19', '已摆放', '灵台'].includes(String(item.storage)); }
function itemValues(item, tier = 0) {
  if (!item) return { qi: 0, elements: [0, 0, 0, 0, 0] };
  // Match Construct's expression order; rounding boundaries depend on multiplying by 10 first.
  const effective = raw => Math.round((10 * num(raw)) * (1 + num(tier) * 0.15)) / 10;
  return { qi: effective(item.qi), elements: names.map((_, i) => effective(item.elements?.[i])) };
}
function baseLayout() {
  const snapshot = state.snapshot;
  if (!snapshot) return null;
  const trib = snapshot.buildings.find(isTribulation);
  const baseline = snapshot.baseline || {};
  return {
    ...baseline,
    tribulation: baseline.tribulation || (trib ? { x: trib.x, y: trib.y, buildingId: trib.id } : null),
    pedestals: baseline.pedestals || snapshot.buildings.filter(isPedestal).map(b => ({ x: b.x, y: b.y, tier: b.tier, buildingId: b.id, itemId: b.itemId ?? snapshot.items.find(item => same(item.placedBuildingId, b.id))?.id })),
    facilities: baseline.facilities || snapshot.facilities,
    status: baseline.status || 'current',
  };
}
function selectedLayout() {
  if (!state.snapshot) return null;
  if (state.layout === 'baseline') return { ...baseLayout(), ...(state.result?.baseline || {}) };
  const value = state.result?.[state.layout];
  return value?.layout ? { ...value, ...value.layout } : value || baseLayout();
}
function footprint(building) {
  const width = Math.max(1, num(building.width, 1)), height = Math.max(1, num(building.height, width));
  return { xmin: num(building.x) - Math.floor((width - 1) / 2), ymin: num(building.y) - Math.floor((height - 1) / 2), width, height };
}
function getRect() { return { xmin: num($('rectX').value), ymin: num($('rectY').value), width: num($('rectW').value), height: num($('rectH').value) }; }
function pointInRect(x, y, rect) { return x >= rect.xmin && x < rect.xmin + rect.width && y >= rect.ymin && y < rect.ymin + rect.height; }
function contains(building, x, y) { return pointInRect(x, y, footprint(building)); }

async function scanSaves(autoLoad = false) {
  $('refreshSaves').disabled = true;
  try {
    const data = await api('/api/saves');
    const saves = Array.isArray(data) ? data : data.saves || [];
    const oldKey = $('saveSelect').value;
    $('saveSelect').innerHTML = saves.length ? saves.map((save, i) => `<option value="${esc(save.key ?? save.id ?? save.path ?? i)}">${esc(save.label || save.name || save.path || `存档 ${i + 1}`)}</option>`).join('') : '<option value="">未发现可用存档</option>';
    if ([...$('saveSelect').options].some(option => option.value === oldKey)) $('saveSelect').value = oldKey;
    $('loadSave').disabled = !saves.length || state.solving;
    if (!saves.length) $('saveMeta').textContent = '尚未发现存档。请在游戏保存后重新扫描。';
    if (autoLoad && saves.length) await loadSave();
  } catch (error) { fail(error.message); }
  finally { $('refreshSaves').disabled = false; }
}
async function loadSave() {
  if (state.solving) return;
  clearError();
  $('loadSave').disabled = true;
  $('saveMeta').textContent = '正在读取存档副本与游戏配置…';
  try {
    const data = await api('/api/load', { key: $('saveSelect').value });
    const snapshot = data.snapshot || data;
    if (!snapshot.bounds || !Array.isArray(snapshot.items)) throw new Error('存档响应缺少地图边界或灵物数据。');
    snapshot.buildings ||= []; snapshot.facilities ||= []; snapshot.resources ||= []; snapshot.facilityGroups ||= []; snapshot.workers ||= [];
    state.snapshot = snapshot; state.result = null; state.layout = 'baseline'; state.excluded.clear(); state.selection = null;
    state.workerJobs.clear(); state.workerCompare = 'assigned'; state.workerSort = 'original'; state.selectedWorkerId = null;
    $('saveMeta').textContent = `${snapshot.label || '当前存档'} · ${dateText(snapshot.savedAt || snapshot.timestamp)}\n${snapshot.items.length} 件灵物 · ${snapshot.facilities.length} 座设施`;
    $('saveMeta').title = snapshot.path || '';
    $('workspaceTitle').textContent = '主城布局';
    $('snapshotSummary').textContent = snapshot.label || '当前存档';
    $('inventoryBadge').textContent = snapshot.items.length;
    $('maxPedestals').value = snapshot.buildings.filter(isPedestal).length || Math.min(snapshot.items.length, 16);
    setFullMap(); renderFacilityRequests(); renderWorkerAssignments(); renderInventory();
    $('emptyState').hidden = true; $('solveButton').disabled = false; $('exportLayout').disabled = false; $('exportInventory').disabled = false;
    $('comparisonPanel').hidden = true; $('resultNotice').hidden = true;
    updateLayoutTabs(); rebuildVisual(); fitMap();
    toast(`已读取 ${snapshot.items.length} 件灵物，存档和游戏均保持原状。`);
  } catch (error) { fail(error.message); $('saveMeta').textContent = '读取失败，请查看提示后重新尝试。'; }
  finally { $('loadSave').disabled = false; }
}
function setFullMap() {
  if (!state.snapshot) return;
  const b = state.snapshot.bounds;
  $('rectX').value = b.xmin; $('rectY').value = b.ymin; $('rectW').value = b.xmax - b.xmin + 1; $('rectH').value = b.ymax - b.ymin + 1;
  $('boundsHint').textContent = `已解锁 X ${b.xmin}–${b.xmax}，Y ${b.ymin}–${b.ymax}。蓝色虚线内为本次规划区。`;
  draw();
}
function ownerCapacity(group) {
  const value = group.ownerCapacity ?? buildingById(group.ownerId)?.facilityCapacity;
  return value != null && Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : null;
}
function renderFacilityRequests() {
  const groups = state.snapshot?.facilityGroups || [];
  $('facilityRequests').innerHTML = groups.length ? groups.map((group, i) => {
    const capacity = ownerCapacity(group);
    return `<label class="facility-row"><span class="facility-label"><b style="color:${colors[num(group.element)] || '#bbc8ad'}">${esc(group.name)} <span class="muted">· ${names[num(group.element)] || '—'}</span></b><small>归属 ${esc(group.ownerName || buildingById(group.ownerId)?.name || group.ownerId)} #${esc(group.ownerId)}${capacity == null ? '' : ` · 总上限 ${capacity}`}</small></span><input type="number" min="0" max="${capacity ?? 200}" step="1" value="${num(group.count)}" data-group-index="${i}" aria-label="${esc(group.name)} 数量"></label>`;
  }).join('') : '<p class="muted small">此存档暂无单格设施。可先优化渡劫台与灵台。</p>';
  $('facilityRequests').querySelectorAll('input').forEach(input => input.addEventListener('input', renderElementTotals));
  renderElementTotals();
}
function renderElementTotals() {
  const totals = [0, 0, 0, 0, 0];
  document.querySelectorAll('[data-group-index]').forEach(input => {
    const group = state.snapshot.facilityGroups[num(input.dataset.groupIndex)];
    if (group && group.element >= 0 && group.element < 5) totals[group.element] += num(input.value);
  });
  $('elementTotals').innerHTML = names.map((name, i) => `<div class="element-total" style="color:${colors[i]}">${name}<strong>${totals[i]}</strong></div>`).join('');
}
function statNumber(value) { return value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value); }
function workerStats(worker) { return worker?.stats && typeof worker.stats === 'object' ? worker.stats : {}; }
function workerStatRows(worker) { return Array.isArray(workerStats(worker).rows) ? workerStats(worker).rows : []; }
function workerWorkRow(worker, job) { return workerStatRows(worker).find(row => row.group === 'work' && row.jobType === job) || { label: job ? `${job}工作力` : '未选择工种', jobType: job, bare: null, equipped: null }; }
function assignedWorkerJob(id) { return state.workerJobs.get(String(id)) || ''; }
function statDifference(row) {
  const bare = statNumber(row?.bare), equipped = statNumber(row?.equipped);
  if (bare == null || equipped == null) return { value: null, percent: null, tone: 'unknown' };
  const raw = equipped - bare, value = Math.abs(raw) < 1e-9 ? 0 : raw;
  const improvement = row.lowerIsBetter ? -value : value;
  return { value, percent: bare > 0 ? value / bare * 100 : null, tone: improvement > 0 ? 'better' : improvement < 0 ? 'worse' : 'neutral' };
}
function statValueHTML(value, unit = '') {
  const number = statNumber(value);
  return number == null ? '<span class="stat-unknown">未确认</span>' : `<span class="stat-value">${fmt(number, 2)}${unit ? `<small class="stat-unit">${esc(unit)}</small>` : ''}</span>`;
}
function statChangeHTML(row, relative = true, compact = false) {
  const delta = statDifference(row);
  if (delta.value == null) return '<span class="stat-unknown">未确认</span>';
  if (delta.value === 0) return '<span class="stat-change neutral">0</span>';
  const unit = row.unit === '%' || Number(row.id) === 51 ? '百分点' : row.unit || '';
  const sign = delta.value > 0 ? '+' : '';
  const percentValue = `${delta.percent > 0 ? '+' : ''}${fmt(delta.percent, 1)}%`;
  const percent = relative && delta.value !== 0 && delta.percent != null ? `<small class="stat-relative" title="相对无普通装备">${compact ? `(${percentValue})` : `相对 ${percentValue}`}</small>` : '';
  return `<span class="stat-change ${delta.tone}"><span class="stat-delta-value">${sign}${fmt(delta.value, 2)}</span>${unit ? `<small class="stat-unit">${esc(unit)}</small>` : ''}${percent}</span>`;
}
function workerLevelText(worker) {
  const stats = workerStats(worker), level = statNumber(stats.level ?? worker.level), professionLevel = statNumber(stats.professionLevel ?? worker.professionLevel);
  return `${esc(worker.profession || '未知职业')}${professionLevel == null ? '' : ` Lv ${fmt(professionLevel, 0)}`}${level == null ? '' : ` · 人物 Lv ${fmt(level, 0)}`}`;
}
function sortedWorkers(workers) {
  if (state.workerCompare === 'assigned' || state.workerSort === 'original') return [...workers];
  return workers.map((worker, index) => ({ worker, index })).sort((a, b) => {
    const value = worker => { const row = workerWorkRow(worker, state.workerCompare); return state.workerSort === 'gain' ? statDifference(row).value : statNumber(row[state.workerSort === 'bare' ? 'bare' : 'equipped']); };
    const x = value(a.worker), y = value(b.worker);
    return x == null && y == null ? a.index - b.index : x == null ? 1 : y == null ? -1 : y - x || a.index - b.index;
  }).map(row => row.worker);
}
function updateWorkerSortControl() {
  $('workerSort').disabled = state.solving || state.workerCompare === 'assigned';
  $('workerSort').title = state.workerCompare === 'assigned' ? '先选择同一个比较工种，再按能力或装备增益排序。' : '只比较当前所选工种；未确认的数值排在最后。';
  $('workerCompareHint').textContent = state.workerCompare === 'assigned' ? '随岗位查看；选择同一工种后可按能力排序。' : `统一比较${state.workerCompare}，不改变任何人的规划工种。`;
}
function syncWorkerCardSelection() {
  $('workerAssignments').querySelectorAll('[data-worker-row]').forEach(row => row.classList.toggle('selected', same(row.dataset.workerRow, state.selectedWorkerId)));
}
function selectWorkerDetails(id, locate = false) {
  const worker = state.visual?.objects.find(object => object.kind === 'worker' && same(object.workerId, id));
  if (!worker) { toast('当前布局没有此人物的有效站位。'); return; }
  state.selectedWorkerId = String(id); state.selection = { x: worker.x, y: worker.y };
  if (locate) { const point = projection(worker.x, worker.y); state.cam.x += state.cssWidth / 2 - point.x; state.cam.y += state.cssHeight / 2 - point.y; }
  syncWorkerCardSelection(); renderDetails(); draw();
  (locate ? $('canvasWrap') : $('workerDetailPanel')).scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function renderWorkerAssignments() {
  const originals = (state.snapshot?.workers || []).filter(worker => worker.inMainCity);
  originals.forEach(worker => {
    if (state.workerJobs.has(String(worker.id))) return;
    const suggestion = state.snapshot.defaultWorkerAssignments?.find(row => same(row.workerId, worker.id))?.jobType || worker.suggestedJobType;
    state.workerJobs.set(String(worker.id), worker.selectable !== false && jobTypes.includes(suggestion) ? suggestion : '');
  });
  const focusedId = document.activeElement?.dataset?.workerId;
  const scrollLeft = $('workerAssignments').scrollLeft;
  $('workerCount').textContent = `${originals.length} 人在主城`;
  $('workerCompare').value = state.workerCompare; $('workerSort').value = state.workerSort;
  updateWorkerSortControl();
  const body = sortedWorkers(originals).map(worker => {
    const selected = assignedWorkerJob(worker.id), compareJob = state.workerCompare === 'assigned' ? selected : state.workerCompare;
    const row = workerWorkRow(worker, compareJob);
    return `<tr class="worker-table-row ${same(worker.id, state.selectedWorkerId) ? 'selected' : ''}" data-worker-row="${esc(worker.id)}"><th scope="row" class="worker-cell-person"><button type="button" class="worker-row-identity" data-worker-detail="${esc(worker.id)}" title="${esc(worker.name)} · #${esc(worker.id)}">${workerPortrait(worker, 'portrait-row')}<strong>${esc(worker.name)}</strong></button></th><td class="worker-cell-profession" title="${workerLevelText(worker)}">${workerLevelText(worker)}</td><td class="worker-cell-job"><select id="worker-${esc(worker.id)}" data-worker-id="${esc(worker.id)}" aria-label="${esc(worker.name)} 工种" ${worker.selectable === false || state.solving ? 'disabled' : ''} ${worker.selectable === false ? 'data-unavailable="true"' : ''}><option value="">保持原位</option>${jobTypes.map(job => `<option value="${job}" ${job === selected ? 'selected' : ''}>${job}</option>`).join('')}</select></td><td class="worker-cell-bare" data-label="无装">${statValueHTML(row.bare, row.unit)}</td><td class="worker-cell-equipped" data-label="当前">${statValueHTML(row.equipped, row.unit)}</td><td class="worker-cell-gain" data-label="增益">${statChangeHTML(row, true, true)}</td><td class="worker-cell-range" title="地图实际工作半径">${fmt(workerRadius(worker), 2)}<small class="stat-unit">格</small></td><td class="worker-cell-actions"><button type="button" data-worker-detail="${esc(worker.id)}" aria-label="${esc(worker.name)} 详情">详情</button><button type="button" data-worker-locate="${esc(worker.id)}" aria-label="${esc(worker.name)} 定位">定位</button></td></tr>`;
  }).join('');
  $('workerAssignments').innerHTML = body ? `<table class="worker-roster"><colgroup><col class="roster-col-person"><col class="roster-col-profession"><col class="roster-col-job"><col class="roster-col-value"><col class="roster-col-value"><col class="roster-col-gain"><col class="roster-col-range"><col class="roster-col-actions"></colgroup><thead><tr><th scope="col">人物</th><th scope="col">职业 / 等级</th><th scope="col">规划工种</th><th scope="col" title="保留内丹，移除普通装备">无普通装备</th><th scope="col">当前穿戴</th><th scope="col">装备增益</th><th scope="col">范围</th><th scope="col">操作</th></tr></thead><tbody>${body}</tbody></table>` : '<p class="muted small">当前存档没有可显示的主城人员。</p>';
  $('workerAssignments').querySelectorAll('select[data-worker-id]').forEach(select => select.addEventListener('change', () => {
    state.workerJobs.set(String(select.dataset.workerId), select.value);
    renderWorkerAssignments(); renderDetails();
  }));
  $('workerAssignments').querySelectorAll('[data-worker-detail]').forEach(button => button.addEventListener('click', () => selectWorkerDetails(button.dataset.workerDetail)));
  $('workerAssignments').querySelectorAll('[data-worker-locate]').forEach(button => button.addEventListener('click', () => selectWorkerDetails(button.dataset.workerLocate, true)));
  $('workerAssignments').scrollLeft = scrollLeft;
  if (focusedId) $(`worker-${focusedId}`)?.focus({ preventScroll: true });
  renderWorkerTotals();
}
function renderWorkerTotals() {
  const totals = Object.fromEntries(jobTypes.map(job => [job, 0]));
  document.querySelectorAll('[data-worker-id]').forEach(select => { if (totals[select.value] != null) totals[select.value]++; });
  const selected = Object.values(totals).reduce((sum, count) => sum + count, 0);
  $('workerTotals').innerHTML = `<span>参与 ${selected} 人</span>` + jobTypes.filter(job => totals[job]).map(job => `<span>${job} ${totals[job]}</span>`).join('');
}
function getOptions() {
  const rect = getRect(), b = state.snapshot.bounds;
  const integers = [rect.xmin, rect.ymin, rect.width, rect.height, num($('maxPedestals').value), num($('newPedestalTier').value)];
  if (integers.some(value => !Number.isInteger(value))) throw new Error('坐标、宽高、灵台数和等阶必须是整数。');
  if (rect.width < 3 || rect.height < 3) throw new Error('规划区宽度和高度至少为 3 格，才能容纳渡劫台。');
  if (rect.xmin < b.xmin || rect.ymin < b.ymin || rect.xmin + rect.width - 1 > b.xmax || rect.ymin + rect.height - 1 > b.ymax) throw new Error('规划矩形超出当前主城已解锁区域，请调整起点或宽高。');
  const facilityRequests = [...document.querySelectorAll('[data-group-index]')].map(input => {
    const group = state.snapshot.facilityGroups[num(input.dataset.groupIndex)], count = Number(input.value);
    if (!Number.isInteger(count) || count < 0) throw new Error('设施数量必须是非负整数。');
    return { template: group.template, ownerId: group.ownerId, count };
  });
  const ownerTotals = new Map();
  for (const request of facilityRequests) {
    const key = String(request.ownerId);
    ownerTotals.set(key, (ownerTotals.get(key) || 0) + request.count);
  }
  for (const [ownerId, total] of ownerTotals) {
    const group = state.snapshot.facilityGroups.find(group => same(group.ownerId, ownerId));
    const capacity = group && ownerCapacity(group);
    if (capacity != null && total > capacity) {
      const ownerName = group.ownerName || buildingById(ownerId)?.name || '主建筑';
      throw new Error(`${ownerName} #${ownerId} 的设施合计为 ${total} 座，超过总上限 ${capacity} 座。该上限由同一主建筑的全部设施共同使用，请减少数量。`);
    }
  }
  const workerAssignments = [...document.querySelectorAll('[data-worker-id]')].filter(select => select.value && !select.dataset.unavailable).map(select => ({ workerId: select.dataset.workerId, jobType: select.value }));
  const options = { rect, maxPedestals: Number($('maxPedestals').value), newPedestalTier: Number($('newPedestalTier').value), delta: Number($('delta').value), weight: Number($('weight').value), seconds: Number($('seconds').value), facilityRequests, workerAssignments, excludeItemIds: [...state.excluded] };
  if (options.maxPedestals < 0 || options.newPedestalTier < 0 || options.delta < 0 || options.weight < 0 || options.seconds < 5 || !Object.values(options).filter(value => typeof value === 'number').every(Number.isFinite)) throw new Error('请填写有效的非负参数，搜索时间至少为 5 秒。');
  return options;
}
function setSolving(value) {
  state.solving = value;
  $('solveButton').disabled = value || !state.snapshot; $('loadSave').disabled = value;
  $('cancelButton').hidden = !value; $('jobProgress').hidden = !value;
  $('solveButton').querySelector('span').textContent = value ? '正在推演…' : '开始推演';
  document.querySelectorAll('.settings input,.settings select,#personnelPanel select').forEach(input => { input.disabled = value || !!input.dataset.unavailable; });
  updateWorkerSortControl();
}
async function solve() {
  if (!state.snapshot || state.solving) return;
  clearError();
  try {
    const options = getOptions();
    setSolving(true); state.selection = null;
    $('progressText').textContent = '正在准备可行区域与灵物组合…'; $('progressBar').style.width = '3%';
    const data = await api('/api/solve', { snapshotId: state.snapshot.id, options });
    if (data.result) { acceptResult(data.result); setSolving(false); return; }
    state.jobId = data.jobId || data.id;
    if (!state.jobId) throw new Error('求解器未返回任务编号。');
    pollJob();
  } catch (error) { setSolving(false); fail(error.message); }
}
async function pollJob() {
  if (!state.jobId || state.polling) return;
  state.polling = true;
  try {
    const data = await api(`/api/jobs/${encodeURIComponent(state.jobId)}`);
    const progress = data.progress;
    const pct = typeof progress === 'number' ? (progress <= 1 ? progress * 100 : progress) : num(progress?.percent, progress?.fraction != null ? num(progress.fraction) * 100 : num(progress?.seconds) > 0 ? num(progress?.elapsed) / num(progress.seconds) * 100 : num(progress?.elapsed) / Math.max(5, num($('seconds').value)) * 100);
    $('progressBar').style.width = `${Math.max(4, Math.min(98, pct || 15))}%`;
    $('progressText').textContent = typeof progress === 'string' ? progress : progress?.message || data.message || '正在搜索渡劫与设施的摆放组合…';
    if (data.result) acceptResult(data.result);
    if (['done', 'completed', 'complete', 'success', 'cancelled', 'canceled', 'failed', 'error'].includes(String(data.state).toLowerCase())) {
      setSolving(false); state.jobId = null;
      if (data.error) fail(typeof data.error === 'string' ? data.error : data.error.message || JSON.stringify(data.error));
      else if (!data.result && !state.result) toast('任务已结束，未产生可展示的布局。');
      return;
    }
  } catch (error) { setSolving(false); state.jobId = null; fail(`读取搜索状态失败：${error.message}`); return; }
  finally { state.polling = false; }
  if (state.jobId) setTimeout(pollJob, 900);
}
async function cancelSolve() {
  if (!state.jobId) return;
  $('cancelButton').disabled = true;
  try { await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, {}); $('progressText').textContent = '已请求停止，等待求解器返回当前结果…'; }
  catch (error) { toast(error.message); }
  finally { $('cancelButton').disabled = false; }
}
function acceptResult(result) {
  state.result = result;
  if (state.layout === 'baseline') state.layout = result.balanced ? 'balanced' : result.strict ? 'strict' : 'baseline';
  updateLayoutTabs(); rebuildVisual(); renderComparison();
  const diagnostics = result.diagnostics;
  const messages = Array.isArray(diagnostics) ? diagnostics : [...(Array.isArray(diagnostics?.messages) ? diagnostics.messages : diagnostics?.messages ? [diagnostics.messages] : []), ...(Array.isArray(diagnostics?.warnings) ? diagnostics.warnings : diagnostics?.warnings ? [diagnostics.warnings] : [])];
  const readable = (Array.isArray(messages) ? messages : [messages]).map(v => typeof v === 'string' ? v : v.message || '').filter(Boolean);
  const noLayout = !result.strict && !result.balanced;
  $('resultNotice').hidden = false;
  $('resultNotice').classList.toggle('warning', noLayout);
  $('resultNotice').textContent = readable.length ? readable.join(' · ') : noLayout ? '没有找到满足当前约束的方案。请查看参数、设施归属范围和可用空格。' : '已生成布局建议。点击灵台查看三步覆盖；切换渡劫优先与折中方案比较收益。';
  if (typeof diagnostics?.scope === 'string') $('resultNotice').textContent += ` ${diagnostics.scope}`;
}
function updateLayoutTabs() {
  document.querySelectorAll('[data-layout]').forEach(button => { button.disabled = button.dataset.layout !== 'baseline' && !state.result?.[button.dataset.layout]; button.classList.toggle('active', button.dataset.layout === state.layout); });
}
function proofText(layout) {
  if (!layout) return '暂无结果';
  if (['current', 'baseline'].includes(layout.status)) return '存档现状';
  if (typeof layout.proof === 'string') return layout.proof;
  if (layout.proof?.message) return layout.proof.message;
  if (layout.proof?.status) return String(layout.proof.status);
  const labels = { optimal: '已证明最优', proven_optimal: '已证明最优', feasible: '可行解 · 未证明最优', heuristic: '搜索所得 · 未证明最优', time_limit: '限时可行解', infeasible: '不可行', cancelled: '已停止 · 当前可行解', current: '存档现状', approximate: '近似解' };
  return labels[layout.status] || (layout.provenOptimal ? '已证明最优' : layout.status || '可行解 · 未证明最优');
}
function isProven(layout) { return layout?.objectiveProvenOptimal === true || layout?.provenOptimal === true || ['optimal', 'proven_optimal'].includes(layout?.status) || layout?.proof?.optimal === true; }

function rebuildVisual() {
  const layout = selectedLayout();
  if (!layout) return;
  const snapshot = state.snapshot;
  const tribBuilding = snapshot.buildings.find(isTribulation);
  const objects = [];
  snapshot.resources.forEach(resource => objects.push({ ...resource, kind: 'resource', width: 1, height: 1, asset: `jz${resource.template}` }));
  snapshot.buildings.filter(b => !isTribulation(b) && !isPedestal(b)).forEach(b => objects.push({ ...b, kind: 'building', asset: `jz${b.template}` }));
  if (layout.tribulation) objects.push({ ...tribBuilding, ...layout.tribulation, name: '渡劫台', width: 3, height: 3, kind: 'tribulation', asset: 'jz66' });
  (layout.pedestals || []).forEach((pedestal, index) => {
    const building = buildingById(pedestal.buildingId), item = itemById(pedestal.itemId);
    objects.push({ ...building, ...pedestal, item, index: index + 1, name: `灵台 ${index + 1}`, width: 1, height: 1, kind: 'pedestal', asset: 'jz67' });
  });
  const facilityList = layout.facilities || snapshot.facilities;
  facilityList.forEach(f => { const group = snapshot.facilityGroups.find(g => same(g.template, f.template)); objects.push({ ...f, jobType: f.jobType || group?.jobType, name: f.name || group?.name || '设施', width: 1, height: 1, kind: 'facility', asset: `ss${f.template}` }); });
  visibleWorkers(layout).forEach((worker, index) => objects.push({ ...worker, name: worker.name || `人员 ${worker.workerId}`, kind: 'worker', index: index + 1, width: 1, height: 1 }));
  const grid = new Map(), b = snapshot.bounds;
  for (let y = b.ymin; y <= b.ymax; y++) for (let x = b.xmin; x <= b.xmax; x++) grid.set(coordKey(x, y), { x, y, qi: 0, elements: [0, 0, 0, 0, 0], sources: [], objects: [] });
  for (const object of objects) {
    const f = footprint(object);
    for (let y = f.ymin; y < f.ymin + f.height; y++) for (let x = f.xmin; x < f.xmin + f.width; x++) grid.get(coordKey(x, y))?.objects.push(object);
  }
  for (const pedestal of objects.filter(o => o.kind === 'pedestal' && o.item)) {
    const values = itemValues(pedestal.item, pedestal.tier);
    for (let dy = -3; dy <= 3; dy++) for (let dx = -3; dx <= 3; dx++) {
      if (Math.abs(dx) + Math.abs(dy) > 3) continue;
      const tile = grid.get(coordKey(num(pedestal.x) + dx, num(pedestal.y) + dy));
      if (!tile) continue;
      tile.qi = round1(tile.qi + values.qi); tile.elements = tile.elements.map((v, i) => round1(v + values.elements[i]));
      tile.sources.push({ pedestal, values });
    }
  }
  const tribTile = layout.tribulation ? grid.get(coordKey(layout.tribulation.x, layout.tribulation.y)) : null;
  const D = layout.D ?? layout.metrics?.D ?? tribTile?.qi ?? 0;
  const elements = [0, 0, 0, 0, 0];
  facilityList.forEach(f => { const tile = grid.get(coordKey(f.x, f.y)); if (tile && f.element >= 0 && f.element < 5) elements[f.element] += tile.elements[f.element]; });
  const S = layout.S ?? layout.metrics?.S ?? elements.reduce((a, v) => a + v, 0);
  state.visual = { layout, objects, grid, D, S, average: layout.average ?? layout.metrics?.average ?? (facilityList.length ? S / facilityList.length : 0), byElement: layout.byElement || elements, facilityCount: facilityList.length };
  updateMetrics(); renderWorkerCoverage(); renderDetails(); renderPlacement(); draw();
}
function renderWorkerCoverage() {
  const v = state.visual;
  if (!v) return;
  const coverage = v.layout.workerCoverage;
  const notice = $('workerCoverageNotice');
  notice.hidden = false;
  notice.classList.toggle('warning', !coverage?.validated);
  if (coverage?.validated) {
    notice.textContent = `工作人员已校验 · ${coverage.workerCount ?? v.objects.filter(o => o.kind === 'worker' && o.jobType).length} 人参与 · ${coverage.coveredFacilities} / ${coverage.totalFacilities} 座设施在对应工种工作范围内。未参与人员保持原位。`;
  } else if (state.layout === 'baseline') {
    notice.textContent = '当前人员位置来自存档。自动模式的实际工作岗位未保存，图上“自动”人员不计作已确认工作覆盖；请在左侧核对工种建议后再规划。';
  } else {
    notice.textContent = '未校验工作人员：此结果缺少完整人员覆盖验证，不应据此确认可投入生产。请按当前人员设置重新搜索。';
  }
  if (coverage?.warnings?.length) notice.textContent += ` ${coverage.warnings.join(' · ')}`;
}
function updateMetrics() {
  const v = state.visual;
  if (!v) return;
  $('metricD').textContent = fmt(v.D); $('metricS').textContent = fmt(v.S); $('metricAverage').textContent = fmt(v.average, 2);
  $('metricFacilityCount').textContent = `${v.facilityCount} 座单格设施`;
  $('metricItems').textContent = `${v.layout.pedestals?.length || 0} / ${state.snapshot.items.length - state.excluded.size}`;
  $('metricProof').textContent = proofText(v.layout);
  $('metricProof').title = `${proofText(v.layout)}${v.layout.DUpperBound != null ? `；渡劫上界 ${fmt(v.layout.DUpperBound)}` : ''}${v.layout.DMaximumProven ? '；渡劫最大值已证明' : ''}${v.layout.DProvenOptimal ? '；本方案达到渡劫最大值' : ''}`;
  const base = state.result?.baseline || state.snapshot.baseline;
  const baseD = base?.D ?? base?.metrics?.D, baseS = base?.S ?? base?.metrics?.S;
  $('metricDNote').textContent = state.layout !== 'baseline' && baseD != null ? `较当前 ${v.D - baseD >= 0 ? '+' : ''}${fmt(v.D - baseD)}` : '读取中心格总灵气';
  $('metricSNote').textContent = state.layout !== 'baseline' && baseS != null ? `较当前 ${v.S - baseS >= 0 ? '+' : ''}${fmt(v.S - baseS)}` : '按各设施对应五行累加';
  const bounds = state.snapshot.bounds;
  $('mapSummary').textContent = `${bounds.xmax - bounds.xmin + 1} × ${bounds.ymax - bounds.ymin + 1} 已解锁 · ${v.objects.filter(o => o.kind === 'building').length} 座固定主建筑 · ${Math.round(state.cam.zoom * 100)}%`;
}
function renderComparison() {
  if (!state.result) return;
  $('comparisonPanel').hidden = false;
  $('comparisonBody').innerHTML = [['baseline', '当前布局'], ['strict', '渡劫优先'], ['balanced', '折中方案']].map(([key, label]) => {
    const raw = key === 'baseline' ? { ...baseLayout(), ...state.result.baseline } : state.result[key];
    if (!raw) return `<tr><td>${label}</td><td colspan="5" class="muted">未找到可用方案</td></tr>`;
    const layout = raw.layout ? { ...raw, ...raw.layout } : raw;
    const metrics = layout.metrics || layout;
    return `<tr class="${key === state.layout ? 'selected' : ''}" data-compare-layout="${key}"><td>${label}</td><td>${fmt(metrics.D)}</td><td>${fmt(metrics.S)}</td><td>${fmt(metrics.average, 2)}</td><td>${layout.pedestals?.length ?? '—'}</td><td><span class="proof-chip ${isProven(layout) || key === 'baseline' ? '' : 'uncertain'}">${esc(proofText(layout))}</span></td></tr>`;
  }).join('');
  $('comparisonBody').querySelectorAll('[data-compare-layout]').forEach(row => row.addEventListener('click', () => chooseLayout(row.dataset.compareLayout)));
}
function chooseLayout(layout) { state.layout = layout; state.selection = null; updateLayoutTabs(); rebuildVisual(); renderComparison(); }

function renderPlacement() {
  if (!state.visual) return;
  $('placementPanel').hidden = false;
  $('placementMode').textContent = { baseline: '当前布局', strict: '渡劫优先', balanced: '折中方案' }[state.layout];
  const trib = state.visual.layout.tribulation;
  $('tribulationCoordinate').textContent = trib ? `渡劫台中心 (${trib.x}, ${trib.y}) · 占地 3×3 · 中心格灵气 ${fmt(state.visual.D)}` : '此布局没有渡劫台';
  document.querySelectorAll('[data-placement-tab]').forEach(button => button.classList.toggle('active', button.dataset.placementTab === state.placementTab));
  if (state.placementTab === 'pedestals') {
    $('placementHead').innerHTML = '<tr><th>台号</th><th>坐标</th><th>灵物 / 库存编号</th><th>台等阶</th><th>贡献灵气</th>' + names.map((name, i) => `<th style="color:${colors[i]}">${name}</th>`).join('') + '</tr>';
    const objects = state.visual.objects.filter(o => o.kind === 'pedestal');
    $('placementBody').innerHTML = objects.map(o => { const values = itemValues(o.item, o.tier); return `<tr data-locate-x="${o.x}" data-locate-y="${o.y}"><td>${o.index}</td><td>(${o.x}, ${o.y})</td><td>${esc(o.item?.name || '空灵台')}<small class="muted"> #${esc(o.itemId ?? '—')}</small></td><td>${num(o.tier)}</td><td>${fmt(values.qi)}</td>${values.elements.map((v, i) => `<td style="color:${colors[i]}">${fmt(v)}</td>`).join('')}</tr>`; }).join('') || '<tr><td colspan="10" class="empty-table">本方案没有摆放灵台。</td></tr>';
  } else if (state.placementTab === 'workers') {
    $('placementHead').innerHTML = '<tr><th>人员 / 编号</th><th>坐标</th><th>职业</th><th>工种</th><th>半径 · 格</th><th>安排</th><th>可服务设施</th></tr>';
    $('placementBody').innerHTML = state.visual.objects.filter(o => o.kind === 'worker').map(o => {
      const facilities = state.visual.objects.filter(f => f.kind === 'facility' && workerCovers(o, f));
      return `<tr data-locate-x="${o.x}" data-locate-y="${o.y}"><td><span class="worker-identity">${workerPortrait(o, 'portrait-small')}<span>${o.index} · ${esc(o.name)} <small class="muted">#${esc(o.workerId)}</small></span></span></td><td>(${o.x}, ${o.y})</td><td>${esc(o.profession || '—')}</td><td>${esc(o.jobType || (o.settingState === '自动' ? '自动 · 实际岗位未知' : '未分配'))}</td><td>${fmt(workerRadius(o), 2)}</td><td>${o.fixed ? '保持原位' : '规划站位'}</td><td>${facilities.map(f => `${esc(f.name)} (${f.x}, ${f.y})`).join('、') || '—'}</td></tr>`;
    }).join('') || '<tr><td colspan="7" class="empty-table">本布局没有主城人员数据。</td></tr>';
  } else {
    $('placementHead').innerHTML = '<tr><th>设施 / 编号</th><th>坐标</th><th>所属主建筑</th><th>生效五行</th><th>属性值</th><th>范围</th><th>工种 / 覆盖人员</th></tr>';
    $('placementBody').innerHTML = state.visual.objects.filter(o => o.kind === 'facility').map(o => {
      const owner = buildingById(o.ownerId), f = owner ? footprint(owner) : null;
      const valid = f ? pointInRect(o.x, o.y, { xmin: f.xmin - 3, ymin: f.ymin - 3, width: f.width + 6, height: f.height + 6 }) : null;
      const value = state.visual.grid.get(coordKey(o.x, o.y))?.elements?.[o.element];
      const workers = state.visual.objects.filter(w => w.kind === 'worker' && workerCovers(w, o));
      return `<tr data-locate-x="${o.x}" data-locate-y="${o.y}"><td>${esc(o.name)} <small class="muted">#${esc(o.id ?? o.slot ?? '新增')}</small></td><td>(${o.x}, ${o.y})</td><td>${esc(owner?.name || '未知')} #${esc(o.ownerId)}</td><td style="color:${colors[num(o.element)]}">${names[o.element] || '—'}</td><td>${fmt(value)}</td><td>${valid == null ? '待核对' : valid ? '有效' : '范围外'}</td><td>${esc(o.jobType || '—')} · ${workers.map(w => esc(w.name)).join('、') || '未确认覆盖'}</td></tr>`;
    }).join('') || '<tr><td colspan="7" class="empty-table">本方案没有单格设施。</td></tr>';
  }
  $('placementBody').querySelectorAll('[data-locate-x]').forEach(row => row.addEventListener('click', () => { state.selection = { x: num(row.dataset.locateX), y: num(row.dataset.locateY) }; renderDetails(); draw(); $('canvasWrap').scrollIntoView({ behavior: 'smooth', block: 'center' }); }));
}

function renderInventory() {
  if (!state.snapshot) return;
  const search = $('itemSearch').value.trim().toLowerCase(), source = $('itemSource').value, sort = $('itemSort').value;
  const items = state.snapshot.items.filter(item => {
    if (search && !`${item.name} ${item.id} ${item.slot} ${item.template}`.toLowerCase().includes(search)) return false;
    if (source === 'placed' && !placed(item) || source === 'bag' && placed(item) || source === 'excluded' && !state.excluded.has(item.id)) return false;
    return true;
  }).sort((a, b) => sort === 'name' ? String(a.name).localeCompare(String(b.name), 'zh-CN') : (sort === 'qi' ? num(b.qi) - num(a.qi) : num(b.elements?.[num(sort)]) - num(a.elements?.[num(sort)])) || String(a.id).localeCompare(String(b.id)));
  const placedCount = state.snapshot.items.filter(placed).length;
  $('inventorySummary').textContent = `共 ${state.snapshot.items.length} 件 · 背包 ${state.snapshot.items.length - placedCount} · 已摆放 ${placedCount} · 排除 ${state.excluded.size} · 当前显示 ${items.length} 件`;
  $('inventoryBody').innerHTML = items.length ? items.map(item => `<tr class="${state.excluded.has(item.id) ? 'excluded' : ''}"><td><input type="checkbox" data-item-id="${esc(item.id)}" ${state.excluded.has(item.id) ? '' : 'checked'} ${state.solving ? 'disabled' : ''} aria-label="让 ${esc(item.name)} 参与求解"></td><td><span class="item-name">${esc(item.name)}</span><small>#${esc(item.id)} · 模板 ${esc(item.template)}</small></td><td>${placed(item) ? `<span class="proof-chip">已摆放</span><small>灵台 #${esc(item.placedBuildingId ?? '—')}</small>` : '<span class="muted">背包</span>'}</td><td>${fmt(item.level, 0)}</td><td>${esc(item.qualityLabel || item.quality || '—')}</td><td><strong>${fmt(item.qi)}</strong></td>${names.map((_, i) => `<td style="color:${colors[i]}">${fmt(item.elements?.[i])}</td>`).join('')}</tr>`).join('') : '<tr><td colspan="11" class="empty-table">没有匹配的灵物。</td></tr>';
  $('inventoryBody').querySelectorAll('[data-item-id]').forEach(input => input.addEventListener('change', () => {
    const item = state.snapshot.items.find(v => same(v.id, input.dataset.itemId));
    if (!item) return;
    input.checked ? state.excluded.delete(item.id) : state.excluded.add(item.id);
    renderInventory(); updateMetrics();
  }));
}

function projection(x, y) {
  const b = state.snapshot?.bounds || { xmin: 0, xmax: 18, ymin: 0, ymax: 18 };
  const dx = x - (b.xmin + b.xmax) / 2, dy = y - (b.ymin + b.ymax) / 2;
  const c = state.cam.cell * state.cam.zoom;
  return { x: state.cssWidth / 2 + state.cam.x + dx * c, y: state.cssHeight / 2 + state.cam.y + dy * c };
}
function unproject(x, y) {
  const b = state.snapshot?.bounds;
  if (!b) return null;
  const c = state.cam.cell * state.cam.zoom;
  const dx = (x - state.cssWidth / 2 - state.cam.x) / c, dy = (y - state.cssHeight / 2 - state.cam.y) / c;
  return { x: Math.round(dx + (b.xmin + b.xmax) / 2), y: Math.round(dy + (b.ymin + b.ymax) / 2) };
}
function tilePath(x, y, inset = 0) {
  const delta = .5 - inset;
  const corners = [[x - delta, y - delta], [x + delta, y - delta], [x + delta, y + delta], [x - delta, y + delta]].map(([a, b]) => projection(a, b));
  ctx.beginPath(); ctx.moveTo(corners[0].x, corners[0].y); corners.slice(1).forEach(p => ctx.lineTo(p.x, p.y)); ctx.closePath();
}
function areaPath(rect, inset = 0) {
  const corners = [[rect.xmin - .5 + inset, rect.ymin - .5 + inset], [rect.xmin + rect.width - .5 - inset, rect.ymin - .5 + inset], [rect.xmin + rect.width - .5 - inset, rect.ymin + rect.height - .5 - inset], [rect.xmin - .5 + inset, rect.ymin + rect.height - .5 - inset]].map(([x, y]) => projection(x, y));
  ctx.beginPath(); ctx.moveTo(corners[0].x, corners[0].y); corners.slice(1).forEach(p => ctx.lineTo(p.x, p.y)); ctx.closePath();
}
function resizeCanvas() {
  if (state.page !== 'planner') return;
  const rect = $('canvasWrap').getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  const dpr = window.devicePixelRatio || 1;
  state.cssWidth = rect.width; state.cssHeight = rect.height;
  canvas.width = Math.round(rect.width * dpr); canvas.height = Math.round(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); draw();
}
function fitMap() {
  resizeCanvas();
  if (!state.snapshot) return;
  const b = state.snapshot.bounds, w = b.xmax - b.xmin + 1, h = b.ymax - b.ymin + 1;
  const width = w, height = h;
  state.cam.zoom = Math.max(.3, Math.min(2, Math.min((state.cssWidth - 70) / (width * state.cam.cell), (state.cssHeight - 65) / (height * state.cam.cell))));
  state.cam.x = 0; state.cam.y = 6; updateMetrics(); draw();
}
function zoom(factor, point) {
  const old = state.cam.zoom, next = Math.max(.22, Math.min(3.8, old * factor));
  if (point) { state.cam.x = point.x - state.cssWidth / 2 - (point.x - state.cssWidth / 2 - state.cam.x) * next / old; state.cam.y = point.y - state.cssHeight / 2 - (point.y - state.cssHeight / 2 - state.cam.y) * next / old; }
  state.cam.zoom = next; updateMetrics(); draw();
}
function imageFor(key) {
  if (!state.assets[key]) return null;
  if (!state.images.has(key)) {
    const img = new Image(); img.onload = draw; img.src = `/assets/${state.assets[key].file || state.assets[key]}`; state.images.set(key, img);
  }
  const image = state.images.get(key); return image.complete && image.naturalWidth ? image : null;
}
function draw() {
  if (!state.cssWidth || state.page !== 'planner') return;
  ctx.clearRect(0, 0, state.cssWidth, state.cssHeight);
  if (!state.visual) return;
  const { objects, grid } = state.visual, b = state.snapshot.bounds;
  const c = state.cam.cell * state.cam.zoom;
  const heatIndex = state.heat === 'qi' ? -1 : Number(state.heat);
  const heatValue = tile => state.heat === 'qi' ? tile.qi : tile.elements[heatIndex] || 0;
  const maxHeat = state.heat === 'none' ? 0 : Math.max(1, ...[...grid.values()].map(heatValue));
  const selectedTile = state.selection ? grid.get(coordKey(state.selection.x, state.selection.y)) : null;
  const selectedObject = selectedTile?.objects.find(o => o.kind === 'worker') || selectedTile?.objects.find(o => o.kind === 'pedestal') || selectedTile?.objects.find(o => o.kind === 'facility');
  const rect = getRect();
  // Clip every map layer, including sprites and range outlines, to unlocked land.
  ctx.save();
  areaPath({ xmin: b.xmin, ymin: b.ymin, width: b.xmax - b.xmin + 1, height: b.ymax - b.ymin + 1 });
  ctx.clip();
  for (const tile of grid.values()) {
    const alternate = (tile.x + tile.y) % 2;
    tilePath(tile.x, tile.y);
    ctx.fillStyle = alternate ? '#e2eacb' : '#e6eed4'; ctx.fill();
    ctx.strokeStyle = '#a7b591'; ctx.lineWidth = .8; ctx.stroke();
    if (!pointInRect(tile.x, tile.y, rect)) { ctx.fillStyle = '#e9edf0aa'; ctx.fill(); }
    if (state.heat !== 'none' && heatValue(tile) > 0) {
      ctx.fillStyle = hexAlpha(heatIndex >= 0 ? colors[heatIndex] : '#e5b43a', .10 + .48 * heatValue(tile) / maxHeat); ctx.fill();
      if (c > 33 && !tile.objects.length) { const p = projection(tile.x, tile.y); ctx.fillStyle = '#283329'; ctx.font = `${Math.max(8, Math.min(11, c / 3.5))}px "Segoe UI"`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(fmt(heatValue(tile)), p.x, p.y); }
    }
    if (selectedObject?.kind === 'pedestal' && Math.abs(tile.x - selectedObject.x) + Math.abs(tile.y - selectedObject.y) <= 3) { ctx.fillStyle = '#4b94d638'; ctx.fill(); ctx.strokeStyle = '#438cceaa'; ctx.lineWidth = 1; ctx.stroke(); }
    if (selectedObject?.kind === 'worker' && workerRadius(selectedObject) != null && (tile.x - selectedObject.x) ** 2 + (tile.y - selectedObject.y) ** 2 <= workerRadius(selectedObject) ** 2) { ctx.fillStyle = '#5596e438'; ctx.fill(); ctx.strokeStyle = '#387ac3aa'; ctx.lineWidth = 1; ctx.stroke(); }
  }
  // Fill occupancy before painting sprites so each building keeps its own footprint.
  for (const object of objects) {
    areaPath(footprint(object), .035);
    ctx.fillStyle = object.kind === 'worker' ? '#d4e5fc' : object.kind === 'tribulation' ? '#e8e3d3' : object.kind === 'pedestal' ? '#c9e5eb' : object.kind === 'facility' ? hexAlpha(colors[num(object.element)] || '#76836a', .18) : object.kind === 'resource' ? '#c8d4b3' : '#d9dfd0';
    ctx.fill(); ctx.strokeStyle = object.kind === 'tribulation' ? '#8d7948' : object.kind === 'pedestal' ? '#45878d' : object.kind === 'facility' ? colors[num(object.element)] || '#7e9763' : '#7f9075'; ctx.lineWidth = object.kind === 'tribulation' ? 2 : 1; ctx.stroke();
  }
  if (selectedObject?.kind === 'facility') {
    const owner = buildingById(selectedObject.ownerId);
    if (owner) { const f = footprint(owner); areaPath({ xmin: f.xmin - 3, ymin: f.ymin - 3, width: f.width + 6, height: f.height + 6 }); ctx.strokeStyle = '#277a48'; ctx.lineWidth = 2; ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]); }
  }
  const sorted = [...objects].sort((a, z) => a.y - z.y);
  for (const object of sorted) drawObject(object, c);
  if (selectedObject?.kind === 'worker' && workerRadius(selectedObject) != null) {
    const p = projection(selectedObject.x, selectedObject.y);
    ctx.beginPath(); ctx.arc(p.x, p.y, workerRadius(selectedObject) * c, 0, Math.PI * 2); ctx.strokeStyle = '#2569b8'; ctx.lineWidth = 1.7; ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]);
  }
  areaPath(rect, .02); ctx.strokeStyle = '#286cc0'; ctx.lineWidth = 1.7; ctx.setLineDash([7, 5]); ctx.stroke(); ctx.setLineDash([]);
  if (state.visual.layout.tribulation) {
    const t = state.visual.layout.tribulation; tilePath(t.x, t.y, .08); ctx.strokeStyle = '#886b2c'; ctx.lineWidth = 1.5; ctx.stroke();
    const p = projection(t.x, t.y); ctx.fillStyle = '#286cc0'; ctx.beginPath(); ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2); ctx.fill();
  }
  if (state.selection) { tilePath(state.selection.x, state.selection.y, .015); ctx.strokeStyle = '#176bca'; ctx.lineWidth = 2.5; ctx.stroke(); }
  else if (state.hover && grid.has(coordKey(state.hover.x, state.hover.y))) { tilePath(state.hover.x, state.hover.y, .02); ctx.strokeStyle = '#3f7eb5'; ctx.lineWidth = 1.5; ctx.stroke(); }
  ctx.restore();
  drawCoordinates(b, c);
  $('heatLegend').hidden = state.heat === 'none'; $('heatMax').textContent = fmt(maxHeat); $('heatLegend').querySelector('i').style.background = `linear-gradient(90deg,#f3f5ed,${heatIndex >= 0 ? colors[heatIndex] : '#e5b43a'})`;
}
function hexAlpha(hex, alpha) { const value = hex.replace('#', ''); return `rgba(${parseInt(value.slice(0, 2), 16)},${parseInt(value.slice(2, 4), 16)},${parseInt(value.slice(4, 6), 16)},${alpha})`; }
function drawObject(object, c) {
  const f = footprint(object), centerX = f.xmin + (f.width - 1) / 2, centerY = f.ymin + (f.height - 1) / 2;
  const p = projection(centerX, centerY), image = $('showSprites').checked ? imageFor(object.asset) : null;
  const big = f.width > 1;
  if (object.kind === 'worker') {
    if (image) {
      const scale = Math.min(c * .84 / image.naturalWidth, c * .83 / image.naturalHeight);
      const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
      ctx.drawImage(image, p.x - w / 2, p.y - c * .09 - h / 2, w, h);
    }
    const badgeX = p.x + (image ? c * .30 : 0), badgeY = p.y - c * (image ? .30 : .07);
    ctx.beginPath(); ctx.arc(badgeX, badgeY, Math.max(5, c * (image ? .14 : .24)), 0, Math.PI * 2); ctx.fillStyle = object.jobType ? '#3478c1' : '#71839a'; ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = .8; ctx.stroke();
    ctx.fillStyle = '#fff'; ctx.font = `${Math.max(7, Math.min(11, c * .25))}px "Segoe UI"`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(String(object.index), badgeX, badgeY);
    if ($('showLabels').checked && c >= 24) {
      ctx.font = `${Math.max(8, Math.min(10, c * .25))}px "Microsoft YaHei"`;
      const label = object.name || object.jobType || '人员', width = Math.min(c * .96, ctx.measureText(label).width + 4);
      ctx.fillStyle = '#fffffff0'; ctx.fillRect(p.x - width / 2, p.y + c * .34 - 6, width, 12);
      ctx.fillStyle = '#294765'; ctx.fillText(label, p.x, p.y + c * .34, c * .92);
    }
    return;
  }
  if (image) {
    const maxW = c * (big ? f.width * .82 : .72);
    const maxH = c * (big ? f.height * .73 : .65);
    const scale = Math.min(maxW / image.naturalWidth, maxH / image.naturalHeight), w = image.naturalWidth * scale, h = image.naturalHeight * scale;
    const top = p.y - h / 2 - (big ? 0 : c * .05);
    ctx.save(); ctx.globalAlpha = state.heat === 'none' ? 1 : .82; ctx.drawImage(image, p.x - w / 2, top, w, h); ctx.restore();
  } else {
    ctx.fillStyle = object.kind === 'pedestal' ? '#266271' : object.kind === 'facility' ? colors[num(object.element)] || '#505f45' : object.kind === 'tribulation' ? '#78602d' : '#4d5d49';
    ctx.font = `${Math.max(9, Math.min(big ? 23 : 14, c * (big ? .58 : .4)))}px "Microsoft YaHei"`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(object.kind === 'pedestal' ? '灵' : object.kind === 'facility' ? names[num(object.element)] || '设' : object.kind === 'tribulation' ? '渡劫台' : (object.name || '').slice(0, 3), p.x, p.y - (big ? 5 : 2));
  }
  if (object.kind === 'pedestal') {
    const x = p.x + c * .29, y = p.y - c * .30;
    ctx.fillStyle = '#216bb5'; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = .7; ctx.beginPath(); ctx.arc(x, y, Math.max(6.5, c * .17), 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.font = `${Math.max(8, Math.min(11, c * .3))}px "Segoe UI"`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#ffffff'; ctx.fillText(String(object.index), x, y + .3);
  }
  if ($('showLabels').checked && object.kind !== 'pedestal' && (c > 24 || big) && (object.kind !== 'facility' || c >= 32)) {
    let label = object.name || '';
    const limit = object.kind === 'facility' && c < 42 ? 2 : 4;
    if (!big && label.length > limit) label = label.slice(0, limit);
    const y = p.y + c * (big ? f.height * .36 : .36);
    ctx.font = `${Math.max(8, Math.min(big ? 12 : 10, c * (big ? .3 : .26)))}px "Microsoft YaHei"`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const w = ctx.measureText(label).width + 6;
    ctx.fillStyle = '#fffffff0'; ctx.fillRect(p.x - w / 2, y - 6, w, 12); ctx.fillStyle = '#303b30'; ctx.fillText(label, p.x, y);
  }
}
function drawCoordinates(bounds, c) {
  ctx.fillStyle = '#56616b'; ctx.font = `${Math.max(9, Math.min(11, c * .3))}px "Segoe UI"`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  const step = c < 18 ? 2 : 1;
  for (let x = bounds.xmin; x <= bounds.xmax; x += step) { const p = projection(x, bounds.ymin - .86); ctx.fillText(String(x), p.x, p.y); }
  for (let y = bounds.ymin; y <= bounds.ymax; y += step) { const p = projection(bounds.xmin - .86, y); ctx.fillText(String(y), p.x, p.y); }
  const p = projection(bounds.xmin - .9, bounds.ymin - .9); ctx.fillStyle = '#56616b'; ctx.font = '9px "Segoe UI"'; ctx.fillText('Y / X', p.x, p.y);
}
function workerStatTable(rows, selectedJob = '') {
  return `<table class="worker-stat-table"><colgroup><col class="worker-stat-name"><col><col><col></colgroup><thead><tr><th scope="col">属性 / 等级</th><th scope="col">无普通装备</th><th scope="col">当前穿戴</th><th scope="col">变化</th></tr></thead><tbody>${rows.map(row => {
    const level = statNumber(row.skillLevel), cap = statNumber(row.skillCap);
    return `<tr class="${row.jobType && row.jobType === selectedJob ? 'assigned-work-row' : ''}"><th scope="row">${esc(row.label || row.jobType || '未命名属性')}${level == null ? '' : `<small>Lv ${fmt(level, 0)}${cap == null ? '' : ` / ${fmt(cap, 0)}`}</small>`}${row.lowerIsBetter ? '<small>越低越好</small>' : ''}${row.note ? `<small class="row-note">${esc(row.note)}</small>` : ''}</th><td>${statValueHTML(row.bare, row.unit)}</td><td>${statValueHTML(row.equipped, row.unit)}</td><td>${statChangeHTML(row)}</td></tr>`;
  }).join('')}</tbody></table>`;
}
function equipmentDetailsHTML(worker) {
  const stats = workerStats(worker), equipment = stats.equipment;
  const pills = Array.isArray(stats.sources?.innerPills) ? stats.sources.innerPills : [];
  const retained = pills.length ? `<div class="worker-inner-pills"><h5>已炼化内丹 · 对照中保留</h5><p>${pills.map(item => esc(item.name || (item.template != null ? `内丹 #${item.template}` : item.itemId || '内丹名称未确认'))).join('、')}</p></div>` : '';
  if (!Array.isArray(equipment)) return '<p class="muted small">当前快照未提供装备明细。</p>' + retained;
  if (!equipment.length) return '<p class="muted small">当前没有穿戴装备。</p>' + retained;
  return `<ul class="worker-equipment-list">${equipment.map(item => {
    const level = statNumber(item.level), tier = statNumber(item.tier), attributes = Array.isArray(item.attributes) ? item.attributes : [];
    return `<li><div class="equipment-heading"><span>${esc(item.slotName || item.slot || '装备位')}</span><b>${esc(item.name || '名称未确认')}</b></div><p class="equipment-meta">${level == null ? '' : `Lv ${fmt(level, 0)}`}${tier == null ? '' : ` · 等阶 ${fmt(tier, 0)}`}${item.itemId == null ? '' : ` · #${esc(item.itemId)}`}</p>${item.retainedInBare ? '<span class="equipment-retained">无普通装备对照中仍保留</span>' : ''}${attributes.length ? `<ul class="equipment-attributes">${attributes.map(attribute => `<li><span>${esc(attribute.label || '属性')}</span>${statValueHTML(attribute.value, attribute.unit)}</li>`).join('')}</ul>` : ''}</li>`;
  }).join('')}</ul>` + retained;
}
function workerFold(title, rows) { return `<details class="worker-detail-fold"><summary>${esc(title)}${rows.length ? ` · ${rows.length}` : ''}</summary>${rows.length ? workerStatTable(rows) : '<p class="muted small">当前快照暂无已确认数据。</p>'}</details>`; }
function renderWorkerDetails(worker, tile) {
  const stats = workerStats(worker), rows = workerStatRows(worker), plannedJob = assignedWorkerJob(worker.workerId);
  const workRows = jobTypes.map(job => workerWorkRow(worker, job));
  const basicDefaults = [{ id: 49, label: '工作间隔', unit: '秒/次', lowerIsBetter: true }, { id: 50, label: '工作范围', unit: '格' }, { id: 51, label: '工作暴击率', unit: '%' }, { id: 52, label: '工作暴击倍率', unit: '×' }];
  const basicRows = basicDefaults.map(fallback => ({ bare: null, equipped: null, ...fallback, ...rows.find(row => Number(row.id) === fallback.id) }));
  const served = (state.visual?.objects || []).filter(object => object.kind === 'facility' && workerCovers(worker, object));
  const warnings = Array.isArray(stats.warnings) ? stats.warnings : [];
  const status = stats.status === 'exact' ? '常驻属性已解析' : stats.status === 'partial' ? '部分数值待确认' : '等待能力数据';
  $('workerDetailTitle').textContent = `${worker.name || '人物'} · 工作能力`;
  $('workerDetailPanel').hidden = false;
  $('workerDetailContent').innerHTML = `<div class="worker-detail-overview"><div class="worker-detail-heading">${workerPortrait(worker, 'portrait-detail')}<div><p>${workerLevelText(worker)}</p><p class="detail-coordinate">#${esc(worker.workerId)} · 站位 (${worker.x}, ${worker.y})</p><p class="worker-stats-status ${stats.status === 'exact' ? '' : 'partial'}">${status}</p></div></div><div class="worker-detail-context"><div><span>规划设置</span><strong>${esc(plannedJob || '保持原位')}</strong></div><div><span>当前布局岗位</span><strong>${esc(worker.jobType || '实际岗位未知')}</strong></div><div><span>地图工作范围</span><strong>${fmt(workerRadius(worker), 2)} 格</strong></div><div><span>当前布局站位</span><strong>${worker.fixed ? '保持原位' : '规划站位'}</strong></div></div></div><div class="worker-detail-tables"><section><h4 class="detail-subheading">九工种工作力</h4>${workerStatTable(workRows, plannedJob)}</section><section><h4 class="detail-subheading">基础工作指标</h4>${workerStatTable(basicRows)}<p class="worker-stat-footnote">暴击率差值以百分点表示，倍率直接显示 ×。百分比以无普通装备为基数；未确认值不计为 0。</p></section></div><div class="worker-detail-folds">${workerFold('附加指标', rows.filter(row => row.group === 'bonus'))}${workerFold('三维属性', rows.filter(row => row.group === 'primary'))}<details class="worker-detail-fold"><summary>装备来源${Array.isArray(stats.equipment) ? ` · ${stats.equipment.length}` : ''}</summary>${equipmentDetailsHTML(worker)}</details><details class="worker-detail-fold"><summary>工作覆盖 · ${served.length} 座</summary><ul class="source-list">${served.map(facility => `<li><span>${esc(facility.name)} (${facility.x}, ${facility.y})</span><b>#${esc(facility.ownerId)}</b></li>`).join('') || `<li>${worker.jobType ? '没有对应工种设施在此范围内。' : '未确认实际岗位，不推定工作覆盖。'}</li>`}</ul><p class="muted small">同一人一次选择一个目标；距离覆盖不表示同时满速生产。</p></details><details class="worker-detail-fold"><summary>站位灵气 · ${fmt(tile.qi)}</summary>${elementBars(tile.elements)}</details><details class="worker-detail-fold"><summary>数据说明${warnings.length ? ` · ${warnings.length} 条提示` : ''}</summary><p class="muted small">${esc(stats.note || '存档常驻能力，不含临时 BUFF；无普通装备保留内丹。')}</p>${warnings.length ? `<ul class="worker-stat-warnings">${warnings.map(warning => `<li>${esc(typeof warning === 'string' ? warning : warning.message || '有属性尚未确认')}</li>`).join('')}</ul>` : ''}${worker.rangeEvidence?.status === 'unknown' ? `<p class="small worker-unknown">地图工作半径未确认：${esc((worker.rangeEvidence.reasons || []).join('；'))}</p>` : ''}</details></div>`;
}
function renderDetails() {
  const detailsPanel = $('mapDetails');
  detailsPanel.hidden = true;
  $('detailHeading').textContent = '地块详情';
  if (!state.selection || !state.visual) { state.selectedWorkerId = null; syncWorkerCardSelection(); $('workerDetailPanel').hidden = true; $('detailContent').innerHTML = ''; return; }
  const tile = state.visual.grid.get(coordKey(state.selection.x, state.selection.y));
  if (!tile) { state.selectedWorkerId = null; syncWorkerCardSelection(); $('workerDetailPanel').hidden = true; return; }
  const object = tile.objects.find(o => o.kind === 'worker') || tile.objects.find(o => o.kind === 'pedestal') || tile.objects.find(o => o.kind === 'facility') || tile.objects.find(o => o.kind === 'tribulation') || tile.objects[0];
  state.selectedWorkerId = object?.kind === 'worker' ? String(object.workerId) : null;
  syncWorkerCardSelection();
  if (object?.kind === 'worker') { renderWorkerDetails(object, tile); return; }
  $('workerDetailPanel').hidden = true;
  detailsPanel.hidden = false;
  const labels = { worker: '人员 · 工作距离已高亮', pedestal: '灵台 · 范围已高亮', facility: '设施 · 归属范围已描边', tribulation: '渡劫台 · 中心格生效', building: '固定主建筑', resource: '原生资源 · 占用格' };
  const targetTile = object?.kind === 'tribulation' ? state.visual.grid.get(coordKey(object.x, object.y)) || tile : tile;
  const asset = object && state.assets[object.asset];
  let html = `<h3 class="detail-title">${esc(object?.name || '空地')}</h3><p class="detail-coordinate">地块 (${tile.x}, ${tile.y})${object && (object.x !== tile.x || object.y !== tile.y) ? ` · 中心 (${object.x}, ${object.y})` : ''}</p><span class="detail-tag">${labels[object?.kind] || '可用地块'}</span>`;
  if (asset) html += `<div class="detail-icon-wrap"><img src="/assets/${esc(asset.file || asset)}" alt="${esc(object.name)}"></div>`;
  if (object?.kind === 'pedestal') {
    html += `<div class="detail-stat"><span>摆放灵物</span><strong>${esc(object.item?.name || '未放置')}</strong></div><div class="detail-stat"><span>灵物编号</span><strong>${esc(object.itemId ?? '—')}</strong></div><div class="detail-stat"><span>灵台等阶 / 倍率</span><strong>${num(object.tier)} / ${fmt(1 + num(object.tier) * .15, 2)}×</strong></div><h4 class="detail-subheading">此灵台贡献</h4>${elementBars(itemValues(object.item, object.tier).elements)}<div class="detail-stat"><span>贡献总灵气</span><strong>${fmt(itemValues(object.item, object.tier).qi)}</strong></div>`;
  } else if (object?.kind === 'facility') {
    const owner = buildingById(object.ownerId);
    html += `<div class="detail-stat"><span>所属主建筑</span><strong>${esc(owner?.name || '未知')} #${esc(object.ownerId)}</strong></div><div class="detail-stat"><span>生效属性</span><strong style="color:${colors[num(object.element)]}">${names[num(object.element)] || '—'} ${fmt(tile.elements[num(object.element)])}</strong></div>`;
    if (owner) { const f = footprint(owner); const valid = pointInRect(tile.x, tile.y, { xmin: f.xmin - 3, ymin: f.ymin - 3, width: f.width + 6, height: f.height + 6 }); html += `<div class="detail-stat"><span>归属作用范围</span><strong>${valid ? '范围内' : '范围外'}</strong></div>`; }
    const workers = state.visual.objects.filter(o => o.kind === 'worker' && workerCovers(o, object));
    html += `<div class="detail-stat"><span>所需工种</span><strong>${esc(object.jobType || '未知')}</strong></div><h4 class="detail-subheading">对应工种覆盖人员 · ${workers.length}</h4><ul class="source-list">${workers.map(w => `<li><span>${esc(w.name)}</span><b>(${w.x}, ${w.y})</b></li>`).join('') || '<li>没有已确认对应工种的覆盖人员。</li>'}</ul>`;
  } else if (object?.kind === 'building' || object?.kind === 'tribulation') html += `<div class="detail-stat"><span>占地 / 建筑编号</span><strong>${object.width || 1}×${object.height || 1} / #${esc(object.id || object.buildingId || '—')}</strong></div>`;
  html += `<h4 class="detail-subheading">${object?.kind === 'tribulation' ? '渡劫台中心格' : '当前地块'}灵气</h4><div class="detail-stat"><span>总灵气</span><strong>${fmt(targetTile.qi)}</strong></div>${elementBars(targetTile.elements)}<h4 class="detail-subheading">覆盖来源 · ${targetTile.sources.length} 座灵台</h4><ul class="source-list">${targetTile.sources.map(source => `<li><span>${source.pedestal.index} · ${esc(source.pedestal.item?.name || '灵物')}</span><b>+${fmt(source.values.qi)}</b></li>`).join('') || '<li>当前没有灵物覆盖此格。</li>'}</ul>`;
  $('detailContent').innerHTML = html;
}
function elementBars(values) {
  const max = Math.max(1, ...values.map(Number));
  return `<div class="element-bars">${names.map((name, i) => `<div class="element-bar-row"><span style="color:${colors[i]}">${name}</span><div class="element-bar"><i style="width:${Math.min(100, num(values[i]) / max * 100)}%;background:${colors[i]}"></i></div><strong>${fmt(values[i])}</strong></div>`).join('')}</div>`;
}
function pointerPosition(event) { const rect = canvas.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top }; }
canvas.addEventListener('pointerdown', event => { if (!state.snapshot || event.button !== 0) return; const p = pointerPosition(event); state.pointer = { id: event.pointerId, startX: p.x, startY: p.y, x: p.x, y: p.y, moved: false }; canvas.setPointerCapture(event.pointerId); });
canvas.addEventListener('pointermove', event => {
  const p = pointerPosition(event);
  if (state.pointer) {
    const pointer = state.pointer;
    if (Math.hypot(p.x - pointer.startX, p.y - pointer.startY) > 4) pointer.moved = true;
    if (pointer.moved) { state.cam.x += p.x - pointer.x; state.cam.y += p.y - pointer.y; canvas.classList.add('dragging'); $('hoverTip').hidden = true; }
    pointer.x = p.x; pointer.y = p.y; draw(); return;
  }
  state.hover = unproject(p.x, p.y);
  const tile = state.hover && state.visual?.grid.get(coordKey(state.hover.x, state.hover.y));
  $('hoverTip').hidden = !tile;
  if (tile) { $('hoverTip').textContent = `(${tile.x}, ${tile.y}) ${tile.objects.map(o => o.name).join(' / ') || '空地'} · 灵气 ${fmt(tile.qi)}`; $('hoverTip').style.left = `${Math.max(6, Math.min(p.x + 14, state.cssWidth - 225))}px`; $('hoverTip').style.top = `${Math.max(6, Math.min(p.y + 18, state.cssHeight - 40))}px`; }
  draw();
});
canvas.addEventListener('pointerup', event => { if (!state.pointer) return; if (!state.pointer.moved) { const p = pointerPosition(event), tile = unproject(p.x, p.y); state.selection = tile && state.visual?.grid.has(coordKey(tile.x, tile.y)) ? tile : null; renderDetails(); } state.pointer = null; canvas.classList.remove('dragging'); canvas.releasePointerCapture(event.pointerId); draw(); });
canvas.addEventListener('pointercancel', () => { state.pointer = null; canvas.classList.remove('dragging'); });
canvas.addEventListener('pointerleave', () => { if (!state.pointer) { state.hover = null; $('hoverTip').hidden = true; draw(); } });
canvas.addEventListener('wheel', event => { if (!state.snapshot) return; event.preventDefault(); zoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, pointerPosition(event)); }, { passive: false });
canvas.addEventListener('keydown', event => { if (event.key === 'Escape') { state.selection = null; renderDetails(); draw(); } else if (event.key === '+' || event.key === '=') zoom(1.15); else if (event.key === '-') zoom(1 / 1.15); else if (event.key === '0') fitMap(); });

function download(name, text, type = 'text/plain;charset=utf-8') { const url = URL.createObjectURL(new Blob([text], { type })), anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 2000); }
function csv(rows) { return '\uFEFF' + rows.map(row => row.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n'); }
function exportLayout() {
  if (!state.snapshot || !state.visual) return;
  const layout = selectedLayout(), workers = visibleWorkers(layout);
  const coverageLinks = workers.map(worker => ({ workerId: worker.workerId, jobType: worker.jobType, facilities: state.visual.objects.filter(f => f.kind === 'facility' && workerCovers(worker, f)).map(f => ({ id: f.id, name: f.name, jobType: f.jobType, x: f.x, y: f.y })) }));
  download(`灵气布局_${state.layout}.json`, JSON.stringify({ source: state.snapshot.label, sourceTimestamp: state.snapshot.timestamp, coordinates: '主城归一格坐标', mode: state.layout, layout: { ...layout, workers }, workerCoverageLinks: coverageLinks, workerCoverageNote: layout.workerCoverage?.validated ? '工作人员覆盖已独立校验；距离覆盖不代表同时满速生产。' : '未校验工作人员，自动岗位未知，关系仅列已知工种的几何覆盖。', metrics: { D: state.visual.D, S: state.visual.S, average: state.visual.average }, items: state.snapshot.items.filter(item => layout?.pedestals?.some(p => same(p.itemId, item.id))) }, null, 2), 'application/json;charset=utf-8');
}
function exportCoordinates() {
  if (!state.visual) return;
  const rows = [['类型', '地图编号', '名称', 'X', 'Y', '宽', '高', '灵物编号', '灵物名称', '所属主建筑编号', '所属主建筑名称', '生效属性', '等阶', '贡献灵气', ...names, '设施生效属性值', '人员编号', '工种', '工作半径(格)', '工作半径(像素)', '服务设施或覆盖人员', '工作人员验证']];
  state.visual.objects.filter(o => ['tribulation', 'pedestal', 'facility', 'worker'].includes(o.kind)).forEach(o => {
    const values = o.kind === 'pedestal' ? itemValues(o.item, o.tier) : null;
    const links = o.kind === 'worker' ? state.visual.objects.filter(f => f.kind === 'facility' && workerCovers(o, f)).map(f => `${f.id ?? ''} ${f.name} (${f.x},${f.y})`) : o.kind === 'facility' ? state.visual.objects.filter(w => w.kind === 'worker' && workerCovers(w, o)).map(w => `${w.workerId} ${w.name} (${w.x},${w.y})`) : [];
    rows.push([{ tribulation: '渡劫台', pedestal: '灵台', facility: '设施', worker: '工作人员' }[o.kind], o.index, o.name, o.x, o.y, o.width, o.height, o.itemId, o.item?.name, o.ownerId, buildingById(o.ownerId)?.name, names[o.element] || '', o.tier, values?.qi, ...(values?.elements || ['', '', '', '', '']), o.kind === 'facility' ? state.visual.grid.get(coordKey(o.x, o.y))?.elements?.[o.element] : '', o.workerId, o.jobType || (o.kind === 'worker' ? '未确认实际岗位' : ''), o.kind === 'worker' ? workerRadius(o) : '', o.kind === 'worker' ? o.rangePixels : '', links.join('；'), state.visual.layout.workerCoverage?.validated ? '已校验' : '未校验']);
  });
  download(`摆放坐标_${state.layout}.csv`, csv(rows), 'text/csv;charset=utf-8');
}
function exportItems() {
  if (!state.snapshot) return;
  const rows = [['编号', '名称', '模板', '来源', '等级', '等阶', '品质', '灵气', ...names, '已放灵台编号', '参与优化']];
  state.snapshot.items.forEach(item => rows.push([item.id, item.name, item.template, placed(item) ? '已摆放' : '背包', item.level, item.tier, item.quality, item.qi, ...(item.elements || []), item.placedBuildingId, state.excluded.has(item.id) ? '否' : '是']));
  download('全部灵物.csv', csv(rows), 'text/csv;charset=utf-8');
}

document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
document.querySelectorAll('[data-layout]').forEach(button => button.addEventListener('click', () => chooseLayout(button.dataset.layout)));
document.querySelectorAll('[data-heat]').forEach(button => button.addEventListener('click', () => { state.heat = button.dataset.heat; document.querySelectorAll('[data-heat]').forEach(b => b.classList.toggle('active', b === button)); draw(); }));
$('refreshSaves').addEventListener('click', () => scanSaves()); $('loadSave').addEventListener('click', loadSave); $('useFullMap').addEventListener('click', setFullMap); $('solveButton').addEventListener('click', solve); $('cancelButton').addEventListener('click', cancelSolve);
$('fitMap').addEventListener('click', fitMap); $('zoomIn').addEventListener('click', () => zoom(1.2)); $('zoomOut').addEventListener('click', () => zoom(1 / 1.2));
$('showLabels').addEventListener('change', draw); $('showSprites').addEventListener('change', draw);
$('workerCompare').addEventListener('change', () => { state.workerCompare = $('workerCompare').value; if (state.workerCompare === 'assigned') state.workerSort = 'original'; renderWorkerAssignments(); });
$('workerSort').addEventListener('change', () => { state.workerSort = $('workerSort').value; renderWorkerAssignments(); });
$('openWorkers').addEventListener('click', () => $('personnelPanel').scrollIntoView({ behavior: 'smooth', block: 'start' }));
$('closeWorkerDetail').addEventListener('click', () => { state.selection = null; renderDetails(); draw(); });
$('closeMapDetails').addEventListener('click', () => { state.selection = null; renderDetails(); draw(); });
['rectX', 'rectY', 'rectW', 'rectH'].forEach(id => $(id).addEventListener('input', draw));
['itemSearch', 'itemSource', 'itemSort'].forEach(id => $(id).addEventListener(id === 'itemSearch' ? 'input' : 'change', renderInventory));
$('includeAll').addEventListener('click', () => { if (state.solving) return; state.excluded.clear(); renderInventory(); updateMetrics(); });
$('exportLayout').addEventListener('click', exportLayout); $('exportCSV').addEventListener('click', exportCoordinates); $('exportInventory').addEventListener('click', exportItems);
$('exportPlacementCSV').addEventListener('click', exportCoordinates);
$('togglePlacement').addEventListener('click', () => { $('placementContent').hidden = !$('placementContent').hidden; $('togglePlacement').textContent = $('placementContent').hidden ? '⌄' : '⌃'; });
document.querySelectorAll('[data-placement-tab]').forEach(button => button.addEventListener('click', () => { state.placementTab = button.dataset.placementTab; renderPlacement(); }));
new ResizeObserver(resizeCanvas).observe($('canvasWrap'));
window.addEventListener('beforeunload', event => { if (state.solving) { event.preventDefault(); event.returnValue = ''; } });
async function start() {
  try { await api('/api/health'); $('connectionDot').className = 'status-dot ok'; $('connectionText').textContent = '本地离线服务 · 已连接'; }
  catch { $('connectionDot').className = 'status-dot error'; $('connectionText').textContent = '本地服务未连接'; }
  try { const response = await fetch('/assets/manifest.json'); if (response.ok) state.assets = await response.json(); } catch { /* Labels keep the map usable without game art. */ }
  await scanSaves(true);
}
start();
