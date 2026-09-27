/** QA-only per-primitive index LOD; never changes source attribute/image bytes. */
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import {MeshoptSimplifier} from '../../ar/node_modules/meshoptimizer/meshopt_simplifier.js';
const [input, output, reportPath, configPath] = process.argv.slice(2);
const config = JSON.parse(await fs.readFile(configPath));
const raw = await fs.readFile(input), sha = b => crypto.createHash('sha256').update(b).digest('hex');
if (sha(raw) !== config.source_sha256) throw Error('Source changed');
if (raw.toString('ascii',0,4)!=='glTF' || raw.readUInt32LE(8)!==raw.length) throw Error('Invalid GLB');
let doc, bin;
for(let at=12;at<raw.length;){const n=raw.readUInt32LE(at),kind=raw.readUInt32LE(at+4),chunk=raw.subarray(at+8,at+8+n);if(kind===0x4e4f534a)doc=JSON.parse(chunk.toString());else if(kind===0x004e4942)bin=chunk;at+=8+n;}
if(doc.skins?.length || doc.animations?.length)throw Error('Static assets only');
const widths={SCALAR:1,VEC2:2,VEC3:3,VEC4:4},types={5121:[1,'getUint8'],5123:[2,'getUint16'],5125:[4,'getUint32'],5126:[4,'getFloat32']};
function rows(index){const a=doc.accessors[index],v=doc.bufferViews[a.bufferView],t=types[a.componentType],width=widths[a.type];if(a.sparse||a.extensions||!t||!width||a.normalized)throw Error('Unsupported accessor');const values=new Float32Array(a.count*width),view=new DataView(bin.buffer,bin.byteOffset,bin.byteLength),stride=v.byteStride??width*t[0],start=(v.byteOffset??0)+(a.byteOffset??0);for(let i=0;i<a.count;i++)for(let j=0;j<width;j++)values[i*width+j]=view[t[1]](start+i*stride+j*t[0],true);return {values,width,count:a.count};}
const chunks=[bin],receipts=[];let length=bin.length;
await MeshoptSimplifier.ready;
const seenBindings=new Set();
for(const spec of config.primitives){
 const binding=`${spec.mesh_index}/${spec.primitive_index}`;
 if(seenBindings.has(binding))throw Error('Instanced mesh needs separate processing');seenBindings.add(binding);
 const p=doc.meshes[spec.mesh_index].primitives[spec.primitive_index];
 if((p.mode??4)!==4||p.targets||p.extensions)throw Error('Ordinary triangles required');
 const position=rows(p.attributes.POSITION),original=p.indices===undefined?Uint32Array.from({length:position.count},(_,i)=>i):Uint32Array.from(rows(p.indices).values);
 const allExtras=Object.entries(p.attributes).filter(([name])=>name!=='POSITION').map(([name,index])=>({name,...rows(index)}));
 // Lens UVs will be replaced by fresh optical preparation. They are neither
 // changed nor baked here, but need not constrain optical geometry reduction.
 const extras=spec.optical?allExtras.filter(a=>a.name==='NORMAL'):allExtras;
 const weights=extras.flatMap(a=>Array(a.width).fill(a.name.startsWith('TEXCOORD')?8:a.name==='NORMAL'?.1:a.name.startsWith('COLOR')?2:.5));
 const unique=[],remap=new Uint32Array(position.count),seen=new Map();
 for(let i=0;i<position.count;i++){const key=[...position.values.subarray(i*3,i*3+3),...extras.flatMap(a=>Array.from(a.values.subarray(i*a.width,(i+1)*a.width)))].join(',');let at=seen.get(key);if(at===undefined){at=unique.length;seen.set(key,at);unique.push(i);}remap[i]=at;}
 const positions=new Float32Array(unique.length*3),attributes=new Float32Array(unique.length*weights.length);
 for(let i=0;i<unique.length;i++){const from=unique[i];positions.set(position.values.subarray(from*3,from*3+3),i*3);let offset=i*weights.length;for(const a of extras){attributes.set(a.values.subarray(from*a.width,(from+1)*a.width),offset);offset+=a.width;}}
 const welded=Uint32Array.from(original,i=>remap[i]),lock=new Uint8Array(unique.length),referenced=new Set(welded);
 for(let axis=0;axis<3;axis++){let lo=Infinity,hi=-Infinity;for(const i of referenced){lo=Math.min(lo,positions[i*3+axis]);hi=Math.max(hi,positions[i*3+axis]);}for(const i of referenced)if(positions[i*3+axis]===lo||positions[i*3+axis]===hi)lock[i]=1;}
 const target=Math.max(12,Math.min(original.length,Math.floor(spec.target_triangles)*3));
 const [simplified,error]=MeshoptSimplifier.simplifyWithAttributes(welded,positions,3,attributes,weights.length,weights,lock,target,config.error_limit,['LockBorder']);
 const indices=Uint32Array.from(simplified,i=>unique[i]);
 const bytes=Buffer.from(indices.buffer),padding=Buffer.alloc((-length)&3);chunks.push(padding,bytes);length+=padding.length;
 const vi=doc.bufferViews.length;doc.bufferViews.push({buffer:0,byteOffset:length,byteLength:bytes.length,target:34963});length+=bytes.length;
 p.indices=doc.accessors.length;doc.accessors.push({bufferView:vi,componentType:5125,count:indices.length,type:'SCALAR'});
 receipts.push({...spec,source_triangles:original.length/3,lod_triangles:indices.length/3,error_fraction:error,locked_borders:true,locked_extrema:lock.reduce((a,b)=>a+b,0),attribute_costs:extras.map(a=>a.name),source_vertex_attributes_unchanged:true});
 console.log(JSON.stringify({part:spec.part_index,triangles:indices.length/3,error}));
}
doc.buffers[0].byteLength=length;
const binary=Buffer.concat(chunks),json=Buffer.from(JSON.stringify(doc)),jp=Buffer.alloc((-json.length)&3,32),bp=Buffer.alloc((-binary.length)&3),head=Buffer.alloc(12),jh=Buffer.alloc(8),bh=Buffer.alloc(8);
head.write('glTF');head.writeUInt32LE(2,4);head.writeUInt32LE(12+8+json.length+jp.length+8+binary.length+bp.length,8);jh.writeUInt32LE(json.length+jp.length);jh.writeUInt32LE(0x4e4f534a,4);bh.writeUInt32LE(binary.length+bp.length);bh.writeUInt32LE(0x004e4942,4);
const result=Buffer.concat([head,jh,json,jp,bh,binary,bp]);await fs.writeFile(output,result,{flag:'wx'});
await fs.writeFile(reportPath,JSON.stringify({method:'explicit_part_index_lod_v1',source_sha256:sha(raw),output_sha256:sha(result),source_binary_prefix_sha256:sha(bin),source_vertex_attributes_unchanged:true,source_images_unchanged:true,primitive_order_preserved:true,optical_binding_requires_fresh_preparation:true,source_triangles:receipts.reduce((s,p)=>s+p.source_triangles,0),lod_triangles:receipts.reduce((s,p)=>s+p.lod_triangles,0),primitives:receipts,accepted:false},null,2)+'\n',{flag:'wx'});
