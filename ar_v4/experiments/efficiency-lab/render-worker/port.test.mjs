import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {portedSources} from './port-renderer.mjs';
test('worker renderer port is exactly reproducible from G with only reviewed canvas/type/export changes',async()=>{
  const sources=await portedSources();assert.equal(sources.length,9);
  for(const {to,value} of sources)assert.equal(await readFile(to,'utf8'),value,to);
});
