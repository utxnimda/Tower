'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ui=require('./web/city.js');

function fixture(){
  return {id:'snapshot',mapBounds:{xmin:1,xmax:50,ymin:1,ymax:50},bounds:{xmin:1,xmax:20,ymin:1,ymax:20},
    pedestalLimit:{maximum:27},counts:{pedestals:1},
    buildings:[{id:1,template:66,name:'渡劫台',x:10,y:10,width:3,height:3,tier:0,facilityCapacity:2},
      {id:2,template:67,name:'置物台',x:10,y:7,width:1,height:1,tier:1,itemId:'19:2'},
      {id:3,template:1,name:'伐木场',x:5,y:5,width:3,height:3,facilityCapacity:8},
      {id:4,template:2,name:'研究所',x:15,y:5,width:3,height:3,facilityCapacity:4}],
    facilityGroups:[{template:1,ownerId:3,name:'林地',jobType:'伐木',element:1,count:7,ownerCapacity:8},
      {template:2,ownerId:4,name:'研究',jobType:'研究',element:2,count:2,ownerCapacity:4}],
    facilities:[{id:'f1',template:1,ownerId:3,name:'林地',jobType:'伐木',element:1,x:6,y:5},
      {id:'f2',template:2,ownerId:4,name:'研究',jobType:'研究',element:2,x:14,y:7}],
    workers:[{id:'w1',name:'甲',inMainCity:true,x:5,y:7,rangePixels:150,rangeTiles:1.5,stats:{rows:[{jobType:'伐木',value:30}]}},
      {id:'w2',name:'乙',inMainCity:true,x:6,y:7,rangePixels:150,rangeTiles:1.5,stats:{rows:[{jobType:'伐木',value:60}]}},
      {id:'w3',name:'丙',inMainCity:true,x:14,y:8,rangePixels:150,rangeTiles:1.5,stats:{rows:[{jobType:'伐木',value:100},{jobType:'研究',value:40}]}},
      {id:'w4',name:'丁',inMainCity:true,x:14,y:9,rangePixels:150,rangeTiles:1.5,stats:{rows:[]}},
      {id:'w5',name:'场外',inMainCity:false,rangePixels:150,stats:{rows:[{jobType:'伐木',value:9999}]}},
      {id:'w6',name:'范围未知',inMainCity:true,x:14,y:10,rangePixels:null,stats:{rows:[{jobType:'伐木',value:9998}]}}],
    items:[{id:'19:2',name:'测试灵物',qi:10.25,elements:[1,2,3,4,5],storage:19,slot:2,quality:3,tier:1,level:4,placedBuildingId:2}],resources:[],
    planningDefaults:{priorityStrategy:'focus',allowMainBuildingMoves:true,seconds:60,
      facilityRequests:[{template:1,ownerId:3,count:7,minWorkers:1},{template:2,ownerId:4,count:2,minWorkers:1}],
      jobRequirements:[{jobType:'伐木',workerCount:2,minFacilitiesPerWorker:3,allocation:'exclusive'},
        {jobType:'研究',workerCount:2,allocation:'station'}],
      workerAssignments:[{workerId:'w1',jobType:'伐木'},{workerId:'w2',jobType:'伐木'},
        {workerId:'w3',jobType:'研究'},{workerId:'w4',jobType:'研究'}]}};
}

test('save counts, zero counts, focus preference and movable buildings are preserved',()=>{
  const snapshot=fixture(),options=ui.normalizeDefaults(snapshot);
  assert.deepEqual(options.facilityRequests.map(r=>r.count),[7,2]);
  assert.equal(options.priorityStrategy,'focus');assert.equal(options.allowMainBuildingMoves,true);assert.equal(options.maxPedestals,27);
  assert.equal(options.seconds,60);assert.deepEqual(options.lockedFacilityIds,[]);
  snapshot.planningDefaults.facilityRequests[1].count=0;
  assert.equal(ui.normalizeDefaults(snapshot).facilityRequests[1].count,0);
});

test('independent resource quotas reject 5 for two times 3, permit 7',()=>{
  const snapshot=fixture(),options=ui.normalizeDefaults(snapshot);
  assert.deepEqual(ui.validateOptions(snapshot,options),[]);
  options.facilityRequests[0].count=5;
  assert.match(ui.validateOptions(snapshot,options).join(' '),/无法满足 2 人各至少 3 块/);
  options.facilityRequests[0].count=7;
  options.facilityRequests[1].count=0;options.jobRequirements[1].workerCount=0;
  options.workerAssignments=options.workerAssignments.filter(w=>w.jobType!=='研究');
  assert.deepEqual(ui.validateOptions(snapshot,options),[]);
});

test('per-facility simultaneous staffing cannot count the same person twice',()=>{
  const snapshot=fixture(),options=ui.normalizeDefaults(snapshot);
  options.facilityRequests[1].minWorkers=2;
  assert.match(ui.validateOptions(snapshot,options).join(' '),/各座合计至少需要 4 个独立站位人员/);
  options.workerAssignments.push({workerId:'w1',jobType:'研究'});
  assert.match(ui.validateOptions(snapshot,options).join(' '),/同一人物不能同时承担两个主要工种/);
});

test('capacity, finite integers and roster count errors are explained before solve',()=>{
  const snapshot=fixture(),options=ui.normalizeDefaults(snapshot);
  options.facilityRequests[0].count=9;options.maxPedestals=28;
  assert.match(ui.validateOptions(snapshot,options).join(' '),/超过容量 8/);
  assert.match(ui.validateOptions(snapshot,options).join(' '),/可建上限 27/);
  options.jobRequirements[0].workerCount=NaN;
  assert.match(ui.validateOptions(snapshot,options).join(' '),/伐木人数须为/);
});

test('auto fill uses available skills and never steals existing duties or unknown-range people',()=>{
  const snapshot=fixture(),options=ui.normalizeDefaults(snapshot);
  options.workerAssignments=[{workerId:'w3',jobType:'研究'},{workerId:'w4',jobType:'研究'}];
  const selected=ui.assignMissingWorkers(snapshot,options);
  assert.equal(selected.assignments.find(w=>w.workerId==='w3').jobType,'研究');
  assert.deepEqual(selected.assignments.filter(w=>w.jobType==='伐木').map(w=>w.workerId),['w2','w1']);
  assert.deepEqual(selected.missing,[]);
  options.jobRequirements[0].workerCount=3;
  assert.match(ui.assignMissingWorkers(snapshot,options).missing.join(' '),/仍缺 1 人/);
  assert.equal(options.workerAssignments.length,2);
});

test('center-cell Manhattan range and game tier rounding are used by map details',()=>{
  const snapshot=fixture(),scene=ui.makeScene(snapshot);
  assert.equal(ui.auraAt(snapshot,scene,10,10).qi,11.8);
  assert.equal(ui.auraAt(snapshot,scene,11,10).qi,0);
  // The game evaluates 50 * 1.15 below the binary midpoint, so this is 5.7.
  assert.deepEqual(ui.auraAt(snapshot,scene,10,10).elements,[1.2,2.3,3.5,4.6,5.7]);
  assert.equal(scene.workers.some(w=>w.id==='w5'),false);
});

test('result scene changes only proposed entities and keeps all saved information for item lookup',()=>{
  const snapshot=fixture(),result={layout:{buildings:[{buildingId:1,x:12,y:12}],pedestals:[{id:'p2',buildingId:2,tier:1,x:12,y:9,itemId:'19:2'}],
    facilities:[{id:'f1',template:1,ownerId:3,x:7,y:5,primaryWorkerIds:['w1'],assistingWorkerIds:['w2']}],
    workers:[{workerId:'w1',jobType:'伐木',x:7,y:6,facilityIds:['f1']}]}};
  const scene=ui.makeScene(snapshot,result);
  assert.equal(scene.buildings.find(b=>b.id===1).x,12);
  assert.equal(scene.buildings.find(b=>b.id===3).x,5);
  assert.equal(scene.pedestals[0].key,'p:2');assert.equal(scene.workers.find(w=>w.id==='w1').name,'甲');
  assert.deepEqual(scene.facilities[0].assistingWorkerIds,['w2']);
  assert.equal(snapshot.buildings[0].x,10);
  const moves=ui.createMoves(snapshot,result);
  assert.ok(moves.some(m=>m.action==='移除'&&m.key==='f:f2'));
  assert.ok(moves.some(m=>m.action==='移动'&&m.key==='p:2'));
  assert.ok(moves.some(m=>m.action==='调整职责'&&m.key==='w:w1'));
});

test('object locks use original IDs and do not create locks for hypothetical new objects',()=>{
  const snapshot=fixture();
  assert.equal(ui.lockDescriptor(snapshot,{kind:'pedestal',buildingId:2,id:'p2'}).field,'lockedBuildingIds');
  assert.equal(ui.lockDescriptor(snapshot,{kind:'facility',id:'f1'}).field,'lockedFacilityIds');
  assert.equal(ui.lockDescriptor(snapshot,{kind:'worker',id:'w1'}).field,'lockedWorkerIds');
  assert.equal(ui.lockDescriptor(snapshot,{kind:'facility',id:'new1'}),null);
  assert.equal(ui.lockDescriptor(snapshot,{kind:'resource',template:1}),null);
  snapshot.buildings[1].canMove=false;
  assert.equal(ui.lockDescriptor(snapshot,{kind:'pedestal',buildingId:2,id:'p2'}).forced,true);
  assert.equal(ui.lockDescriptor(snapshot,{kind:'building',id:1}).forced,false);
});

test('CSV and displayed strings are escaped, diagnostics retain unknown versus infeasible distinction',()=>{
  assert.match(ui.toCsv([['=1+2','a"b','(2, 3)']]),/"'=1\+2","a""b","\(2, 3\)"/);
  assert.equal(ui.esc('<img onerror="x">'),'&lt;img onerror=&quot;x&quot;&gt;');
  assert.equal(ui.diagnosticText({diagnostics:{errors:['限时未找到，不等于证明无解'],warnings:['工作量并非最终产速']}}),'限时未找到，不等于证明无解\n工作量并非最终产速');
});

test('all fixed UI hooks resolve exactly once and no old plan auto-import remains',()=>{
  const html=fs.readFileSync('./web/index.html','utf8'),source=fs.readFileSync('./web/city.js','utf8');
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(ids.length,new Set(ids).size);
  for(const match of source.matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.includes(match[1]),`missing DOM hook ${match[1]}`);
  assert.equal(source.includes('URLSearchParams'),false);
  assert.ok(html.includes('role="tablist"'));assert.ok(html.includes('只看解锁区'));
});

test('cancelling retains a new feasible result, failure keeps prior result paired with its original options',()=>{
  const previous={result:{layout:{old:true}},resultOptions:{count:2}},pending={count:7};
  const result={layout:{current:true}};
  assert.deepEqual(ui.acceptedPlan(previous,pending,{state:'cancelled',result}),{result,resultOptions:pending});
  assert.deepEqual(ui.acceptedPlan(previous,pending,{state:'done',result}),{result,resultOptions:pending});
  assert.deepEqual(ui.acceptedPlan(previous,pending,{state:'cancelled',result:null}),previous);
  assert.deepEqual(ui.acceptedPlan(previous,pending,{state:'done',result:{layout:null,status:'unknown'}}),previous);
  assert.deepEqual(ui.acceptedPlan(previous,pending,{state:'error',error:'bad'}),previous);
});

function comparisonFixture(){
  const snapshot=fixture();
  snapshot.workers[0].name=snapshot.workers[1].name='同名人物';
  const result={layout:{
    buildings:snapshot.buildings.filter(b=>b.template!==67).map(b=>({buildingId:b.id,x:b.x+(b.id===1?2:0),y:b.y})),
    pedestals:[{id:'p1',buildingId:2,tier:1,x:12,y:7,itemId:'19:2'},
      {id:'new-pad',buildingId:null,tier:0,x:13,y:7,itemId:null}],
    facilities:[{...snapshot.facilities[0],x:7},
      {id:'new-field',template:1,ownerId:3,name:'林地',jobType:'伐木',element:1,x:8,y:5}],
    workers:[{workerId:'w1',x:7,y:6,jobType:'伐木'},
      {workerId:'w2',x:8,y:6,jobType:'伐木'},
      {workerId:'w3',x:14,y:8,jobType:'研究'}]}};
  return {snapshot,result};
}

test('comparison uses matching identity badges for duplicate names and keeps before/after positions',()=>{
  const {snapshot,result}=comparisonFixture();
  const markers=ui.comparisonMarkers(snapshot,result);
  assert.equal(markers['w:w1'].label,'人01');
  assert.equal(markers['w:w2'].label,'人02');
  assert.notEqual(markers['w:w1'].label,markers['w:w2'].label);
  assert.deepEqual(markers['w:w1'].before,{x:5,y:7});
  assert.deepEqual(markers['w:w1'].after,{x:7,y:6});
  assert.equal(markers['w:w1'].status,'moved');
  assert.equal(markers['b:1'].kind,'building');
  assert.match(markers['p:2'].label,/^台\d+$/);
  assert.equal(new Set(Object.values(markers).map(m=>m.label)).size,Object.keys(markers).length);
});

test('comparison labels do not depend on list ordering, names, or render side',()=>{
  const {snapshot,result}=comparisonFixture();
  const expected=ui.comparisonMarkers(snapshot,result);
  const other=structuredClone({snapshot,result});
  for(const key of ['buildings','facilities','workers'])other.snapshot[key].reverse();
  for(const key of ['buildings','pedestals','facilities','workers'])other.result.layout[key].reverse();
  other.snapshot.workers.forEach(w=>w.name='改名');
  assert.deepEqual(ui.comparisonMarkers(other.snapshot,other.result),expected);
  assert.deepEqual(ui.comparisonMarkers(snapshot,null),{});
});

test('added and removed objects appear on only their real side; stationary changes have no move badge',()=>{
  const {snapshot,result}=comparisonFixture();
  const markers=ui.comparisonMarkers(snapshot,result);
  assert.equal(markers['f:f2'].status,'removed');
  assert.deepEqual(markers['f:f2'].before,{x:14,y:7});
  assert.equal(markers['f:f2'].after,null);
  const added=Object.values(markers).filter(m=>m.status==='added');
  assert.equal(added.length,2);
  assert.ok(added.every(m=>m.before===null&&m.after!==null));
  assert.equal(markers['w:w3'],undefined,'same location with changed duty is not a spatial move');
  result.layout.pedestals[0].x=10;
  result.layout.pedestals[0].itemId=null;
  assert.equal(ui.comparisonMarkers(snapshot,result)['p:2'],undefined,'same pedestal only replacing its item is not a spatial move');
});

test('unlocked filtering removes outside resources and respects whole building footprints',()=>{
  const bounds={xmin:1,xmax:20,ymin:1,ymax:20};
  for(const point of [{x:1,y:1},{x:20,y:20}])assert.equal(ui.objectInBounds({...point,width:1,height:1,kind:'resource'},bounds),true);
  for(const point of [{x:0,y:8},{x:21,y:8},{x:8,y:0},{x:8,y:21}])assert.equal(ui.objectInBounds({...point,width:1,height:1,kind:'resource'},bounds),false);
  assert.equal(ui.objectInBounds({x:21,y:10,width:3,height:3},bounds),true,'edge footprint has an inside portion for clipping');
  assert.equal(ui.objectInBounds({x:22,y:10,width:3,height:3},bounds),false,'whole footprint outside is omitted');
  assert.equal(ui.objectInBounds({x:0,y:10,width:2,height:2},bounds),true,'even-sized buildings use the upper-left anchor');
});
