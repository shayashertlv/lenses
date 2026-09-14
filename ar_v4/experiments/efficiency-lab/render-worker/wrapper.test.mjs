import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';
const wrapper=new URL('../comparison-renderer.ts',import.meta.url);
const lower=new Set([new URL('../g-readback-diagnostic/renderer.ts',import.meta.url).href,new URL('../renderer.ts',import.meta.url).href,new URL('../../speed-lab/renderer.ts',import.meta.url).href]);
const hooks=registerHooks({load(url,context,next){
  if(lower.has(url))return {format:'module',shortCircuit:true,source:'export class LiveHairRenderer {}'};
  if(url===wrapper.href)return {format:'module',shortCircuit:true,source:transformSync(fileURLToPath(url),readFileSync(new URL(url),'utf8')).code};
  return next(url,context);
}});
const {ComparisonRenderer}=await import(wrapper.href);hooks.deregister();
const gate=()=>{let release;const promise=new Promise(resolve=>{release=resolve;});return {promise,release};};
test('wrapper queues pending worker variants and retains current G until synchronous finish',async()=>{
  const initialization=gate(),native=gate();let display='new G',privateFrame=false,selected='accepted',prepareVariant,lastWorker='old worker';
  const publications=[],worker={
    selectVariant(value){selected=value;if(!privateFrame){display=lastWorker+' '+value;publications.push(display);}},
    async prepare(_frame,_detection,_pair,_model,_hair,_source,variant){privateFrame=true;selected=variant;prepareVariant=variant;await native.promise;return true;},
    async complete(){assert.equal(privateFrame,true);},
    finish(){assert.equal(privateFrame,true);privateFrame=false;lastWorker='new worker';display=lastWorker+' '+selected;publications.push(display);return true;},
  };
  const renderer=new ComparisonRenderer({}, {}, {},new AbortController().signal,'amber-horizon');
  renderer.worker=worker;renderer.initializePipeline=()=>initialization.promise;renderer.selectPipeline('render-worker');
  const pending=renderer.prepare({}, {}, {}, {},true);
  renderer.selectVariant('hair');assert.equal(display,'new G');assert.deepEqual(publications,[]);
  initialization.release();await Promise.resolve();assert.equal(prepareVariant,'hair');
  renderer.selectVariant('accepted');assert.equal(display,'new G');
  native.release();await pending;await renderer.complete(null);assert.equal(display,'new G');assert.deepEqual(publications,[]);
  renderer.selectVariant('hair');assert.equal(display,'new G');
  renderer.finish(null);assert.equal(display,'new worker hair');assert.deepEqual(publications,['new worker hair']);
  renderer.selectVariant('accepted');assert.equal(display,'new worker accepted');assert.equal(publications.length,2);
});
