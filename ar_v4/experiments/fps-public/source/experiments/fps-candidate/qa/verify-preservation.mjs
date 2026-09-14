import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../../',import.meta.url));
export async function verifyPreservation(){
  const manifest=JSON.parse(await fs.readFile(new URL('./g-preservation.json',import.meta.url),'utf8'));
  assert.equal(manifest.schema,'fps-candidate-g-preservation-v1');
  const seen=new Set();
  for(const item of manifest.files){
    const filename=path.resolve(root,item.path),relative=path.relative(root,filename);
    assert.ok(relative&&!relative.startsWith('..')&&!path.isAbsolute(relative));assert.ok(!seen.has(filename));seen.add(filename);
    const bytes=await fs.readFile(filename);
    assert.equal(bytes.length,item.bytes,`Original length changed: ${item.path}`);
    assert.equal(createHash('sha256').update(bytes).digest('hex'),item.sha256,`Original changed: ${item.path}`);
  }
  return {schema:'fps-candidate-preservation-result-v1',filesVerified:seen.size,checkedAt:new Date().toISOString()};
}
if(process.argv.includes('--verify'))console.log(JSON.stringify(await verifyPreservation()));
