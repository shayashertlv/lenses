// One-time isolated source scaffold. Existing candidate files are never overwritten.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
const root = process.cwd(), target = path.join(root, 'experiments/fps-candidate/runtime');
const entry = 'experiments/speed-lab/live-main.ts';
const seen = new Set(), texts = new Map();
const local = /(['"])(\.[^'"\r\n]*\.(?:ts|css))\1/g;
const clone = name => name.startsWith('experiments/') && !name.startsWith('experiments/temple-sagittal/');
async function visit(name) {
  if (seen.has(name)) return; seen.add(name);
  const text = await fs.readFile(path.join(root,name),'utf8');
  if (!clone(name)) return;
  texts.set(name,text);
  for(const match of text.matchAll(local)) {
    const child=path.relative(root,path.resolve(root,path.dirname(name),match[2])).replaceAll('\\','/');
    if(!child.startsWith('../')) await visit(child);
  }
}
await visit(entry);
for(const [name,text] of texts) {
  const output=path.join(target,name);
  const adjusted=text.replace(local,(whole,quote,specifier)=>{
    const child=path.relative(root,path.resolve(root,path.dirname(name),specifier)).replaceAll('\\','/');
    const destination=texts.has(child)?path.join(target,child):path.join(root,child);
    let relative=path.relative(path.dirname(output),destination).replaceAll('\\','/');
    if(!relative.startsWith('.'))relative='./'+relative;
    return quote+relative+quote;
  });
  await fs.mkdir(path.dirname(output),{recursive:true});
  try{await fs.writeFile(output,adjusted,{flag:'wx'});}catch(error){if(error.code!=='EEXIST')throw error;}
}
const files=[];
// Pin all known G dependencies and current imported sources, including uncommitted inputs.
const prior=JSON.parse(await fs.readFile('experiments/efficiency-lab/qa/g-base-manifest.json','utf8'));
for(const name of new Set([...seen,...prior.files.map(file=>file.path)])) {
  const bytes=await fs.readFile(path.join(root,name));
  files.push({path:name,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});
}
await fs.writeFile('experiments/fps-candidate/qa/g-preservation.json',JSON.stringify({schema:'fps-candidate-g-preservation-v1',scope:'Current original G and accepted dependencies before this experiment; current dirty checkout bytes, not a promoted commit.',files},null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({copied:texts.size,preserved:files.length}));
