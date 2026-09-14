import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import {after,before,test} from 'node:test';
import {chromium} from 'playwright';

// Real IndexedDB, no camera/GPU or server. Production module with only TypeScript syntax removed.
const source=stripTypeScriptTypes(await readFile(new URL('./stability-store.ts',import.meta.url),'utf8'),{mode:'strip'});
const script=source.replace(/^export /gm,'')+'\nglobalThis.Stability={openStabilityStore};';
let browser;
before(async()=>{browser=await chromium.launch({headless:true,args:['--disable-gpu']});});
after(async()=>{await browser?.close();});
async function fixture(t){
  const context=await browser.newContext();t.after(()=>context.close());
  await context.route('https://stability-store.test/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Scalar storage fixture</title>'}));
  await context.addInitScript({content:script});
  await context.addInitScript({content:`
    globalThis.identity={buildId:'build-one',eyewearId:'amber-horizon',hairModelId:'hair-only',variant:'hair',sourceWidth:720,sourceHeight:1280,power:'unknown'};
    globalThis.reportFor=manifest=>{
      const chunk=manifest.plan[manifest.nextChunkIndex],active=manifest.active;
      return {schema:'ar-continuous-comparison-v1',sessionId:'session-'+active.documentId,
        workload:manifest.identity,completed:true,partial:false,
        metadata:{build:{id:manifest.identity.buildId},performanceTimeOriginMs:performance.timeOrigin,
          stability:{suiteId:manifest.id,chunkId:chunk.id,documentId:active.documentId,buildId:manifest.identity.buildId,timeOrigin:performance.timeOrigin}},
        protocol:{studyOptions:chunk.condition==='continuous'?'g-continuous':chunk.condition==='restarted'?'g-restart':'g-page',measureMs:chunk.measureMs,order:Array(chunk.windowCount).fill('g')},
        windows:Array.from({length:chunk.windowCount},()=>({pipeline:'g',completed:true})),
        rows:[{fields:{sessionId:'session-'+active.documentId,pipeline:'g',capturedAtMs:11,publishedAtMs:21},native:{workerMs:5}}],hairDeliveryDrain:{state:'drained'}};
    };
    globalThis.saveOwned=async(store,manifest,complete=true)=>store.complete({suiteId:manifest.id,token:manifest.active.token,documentId:manifest.active.documentId,
      report:reportFor(manifest),summary:{completedArFps:18,frameAgeMs:{p95:155}},complete});
  `});
  const page=await context.newPage();await page.goto('https://stability-store.test/');return {context,page};
}
test('real IndexedDB claims atomically across tabs, then preserves a raw report across a genuine reload',{timeout:30_000},async t=>{
  const {context,page}=await fixture(t),other=await context.newPage();await other.goto('https://stability-store.test/');
  const created=await page.evaluate(async()=>{globalThis.store=await Stability.openStabilityStore();return store.create({identity,selection:'all',direction:'forward'});});
  const claim=async(target,documentId)=>target.evaluate(async({created,documentId})=>{
    const store=await Stability.openStabilityStore();try{return{okay:true,manifest:await store.claim({suiteId:created.id,token:created.handoffToken,identity,documentId})};}
    catch(error){return{okay:false,code:error.code};}finally{store.close();}
  },{created,documentId});
  const claims=await Promise.all([claim(page,'page-one'),claim(other,'page-two')]);
  assert.equal(claims.filter(result=>result.okay).length,1);assert.equal(claims.find(result=>!result.okay).code,'stale');
  const winner=claims[0].okay?page:other;
  const saved=await winner.evaluate(async()=>{const store=await Stability.openStabilityStore();const active=await store.read();const next=await saveOwned(store,active);store.close();return next;});
  assert.equal(saved.status,'ready');assert.equal(saved.results.length,1);
  await winner.reload();
  const result=await winner.evaluate(async()=>{
    const store=await Stability.openStabilityStore();let readBlob=false;const original=IDBObjectStore.prototype.getAll;
    IDBObjectStore.prototype.getAll=function(...args){if(this.name==='reports')readBlob=true;return original.apply(this,args);};
    const manifest=await store.read();IDBObjectStore.prototype.getAll=original;
    const parts=await store.exportParts(),raw=JSON.parse(await parts.reports[0].blob.text());store.close();
    return {manifest,readBlob,raw,partBytes:parts.reports[0].blob.size};
  });
  assert.equal(result.readBlob,false);assert.equal(result.manifest.handoffToken,saved.handoffToken);
  assert.equal(result.raw.rows[0].fields.capturedAtMs,11);assert.ok(result.partBytes>500);
  assert.equal(result.raw.metadata.stability.documentId,saved.results[0].documentId);
});
test('quota failure aborts both raw Blob and handoff, retains active owner and allows explicit retry',{timeout:30_000},async t=>{
  const {page}=await fixture(t);
  const result=await page.evaluate(async()=>{
    const store=await Stability.openStabilityStore();const created=await store.create({identity,selection:'fresh-page',direction:'forward'});
    const active=await store.claim({suiteId:created.id,token:created.handoffToken,identity,documentId:'one'});
    globalThis.currentRaw=reportFor(active);const original=IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put=function(value,...args){if(this.name==='manifest'&&value.status==='ready')throw new DOMException('Synthetic quota exhausted','QuotaExceededError');return original.call(this,value,...args);};
    let error;try{await saveOwned(store,active);}catch(failure){error={code:failure.code,message:failure.message};}finally{IDBObjectStore.prototype.put=original;}
    const preserved=await store.read(),parts=await store.exportParts(),retainedInMemory=currentRaw.rows.length;
    const retry=await saveOwned(store,preserved),exported=await store.exportReports();store.close();
    return{error,preserved,countBeforeRetry:parts.reports.length,retainedInMemory,retry,reportCount:exported.reports.length};
  });
  assert.equal(result.error.code,'storage');assert.match(result.error.message,/quota exhausted/);
  assert.equal(result.preserved.status,'running');assert.equal(result.preserved.handoffToken,null);
  assert.equal(result.countBeforeRetry,0);assert.equal(result.retainedInMemory,1);
  assert.equal(result.retry.status,'ready');assert.equal(result.reportCount,1);
});
test('existing suite and partial raw reports are retained until an explicit matching-id delete',{timeout:30_000},async t=>{
  const {page}=await fixture(t);
  const result=await page.evaluate(async()=>{
    const store=await Stability.openStabilityStore();const created=await store.create({identity,selection:'continuous',direction:'reverse'});
    const active=await store.claim({suiteId:created.id,token:created.handoffToken,identity,documentId:'one'});
    const saved=await saveOwned(store,active,false),errors=[];
    for(const operation of [()=>store.create({identity,selection:'continuous',direction:'forward'}),()=>store.delete('different-suite'),
      ()=>store.claim({suiteId:saved.id,token:created.handoffToken,documentId:'two',identity})]){
      try{await operation();}catch(error){errors.push(error.code);}
    }
    const exported=await store.exportReports();await store.delete(saved.id);const empty=await store.read();
    const next=await store.create({identity,selection:'restarted',direction:'forward'});store.close();
    return{saved,errors,exported,empty,next};
  });
  assert.deepEqual(result.errors,['existing','stale','stale']);assert.equal(result.saved.status,'partial');
  assert.equal(result.exported.reports.length,1);assert.equal(result.exported.reports[0].completed,false);
  assert.equal(result.empty,null);assert.equal(result.next.status,'ready');assert.notEqual(result.next.id,result.saved.id);
});
