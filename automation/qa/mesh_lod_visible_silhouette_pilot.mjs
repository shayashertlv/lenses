/** Bounded frame-only index simplification. Optical geometry is never touched. */
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import {MeshoptSimplifier} from '../../ar/node_modules/meshoptimizer/meshopt_simplifier.js';
const [input,output,reportPath,targetText,locksPath]=process.argv.slice(2);
const lockReceipt=JSON.parse(await fs.readFile(locksPath));
if(!input||!output||!reportPath)throw Error('Usage: mesh_lod.mjs input.glb output.glb report.json targetTriangles');
const target=Number(targetText??150000),raw=await fs.readFile(input);
if(crypto.createHash('sha256').update(raw).digest('hex')!==lockReceipt.source_sha256)throw Error('Contour lock source mismatch');
if(raw.toString('ascii',0,4)!=='glTF'||raw.readUInt32LE(8)!==raw.length)throw Error('Invalid GLB');
let doc,bin;for(let offset=12;offset<raw.length;){const n=raw.readUInt32LE(offset),type=raw.readUInt32LE(offset+4),chunk=raw.subarray(offset+8,offset+8+n);if(type===0x4e4f534a)doc=JSON.parse(chunk.toString());else if(type===0x004e4942)bin=chunk;offset+=8+n;}
if(doc.skins?.length||doc.animations?.length)throw Error('LOD requires static geometry');
const widths={SCALAR:1,VEC2:2,VEC3:3,VEC4:4},types={5120:[1,'getInt8'],5121:[1,'getUint8'],5122:[2,'getInt16'],5123:[2,'getUint16'],5125:[4,'getUint32'],5126:[4,'getFloat32']};
function rows(index){const a=doc.accessors[index],v=doc.bufferViews[a.bufferView],t=types[a.componentType],width=widths[a.type];if(a.sparse||a.extensions||!t||!width)throw Error('Unsupported LOD accessor');const values=new Float32Array(a.count*width),view=new DataView(bin.buffer,bin.byteOffset,bin.byteLength),stride=v.byteStride??width*t[0],start=(v.byteOffset??0)+(a.byteOffset??0);for(let i=0;i<a.count;i++)for(let j=0;j<width;j++){let value=view[t[1]](start+i*stride+j*t[0],true);if(a.normalized&&a.componentType!==5126)value=a.componentType===5121?value/255:a.componentType===5123?value/65535:Math.max(-1,value/(a.componentType===5120?127:32767));values[i*width+j]=value;}return {values,width,count:a.count};}
const activeMeshes=new Set();function visit(i){const n=doc.nodes[i];if(n.mesh!==undefined)activeMeshes.add(n.mesh);for(const child of n.children??[])visit(child);}for(const root of doc.scenes[doc.scene??0].nodes)visit(root);
const primitives=doc.meshes.flatMap((mesh,mi)=>activeMeshes.has(mi)?mesh.primitives.map((p,pi)=>({p,mi,pi})):[]),optical=row=>!!doc.materials?.[row.p.material]?.extensions?.LENSES_lens_appearance;
const counts=primitives.map(row=>row.p.indices===undefined?doc.accessors[row.p.attributes.POSITION].count/3:doc.accessors[row.p.indices].count/3);
const opticalCount=counts.reduce((s,n,i)=>s+(optical(primitives[i])?n:0),0),frameCount=counts.reduce((s,n)=>s+n,0)-opticalCount;
const ratio=Math.min(1,Math.max(0,target-opticalCount)/Math.max(1,frameCount));
await MeshoptSimplifier.ready;
const chunks=[bin],receipts=[];let length=bin.length;
for(const [ordinal,row]of primitives.entries()){
 const {p,mi,pi}=row;if(optical(row)||counts[ordinal]<128)continue;
 if(p.mode!==undefined&&p.mode!==4||p.targets||p.extensions)throw Error('LOD requires ordinary triangles');
 const position=rows(p.attributes.POSITION),original=p.indices===undefined?Uint32Array.from({length:position.count},(_,i)=>i):Uint32Array.from(rows(p.indices).values);
 const extras=Object.entries(p.attributes).filter(([name])=>name!=='POSITION').map(([name,index])=>({name,...rows(index)}));
 const costNormals=false; // QA: source shading is baked separately.
 const weights=extras.flatMap(a=>Array(a.width).fill(a.name.startsWith('TEXCOORD')?8:a.name.startsWith('COLOR')?2:.5));
 if(costNormals)weights.push(2,2,2);
 // Welding compares every authored attribute. UV islands, color boundaries and
 // hard normals remain seams even when their positions coincide.
 const unique=[],remap=new Uint32Array(position.count),seen=new Map();
 for(let i=0;i<position.count;i++){const key=[...position.values.subarray(i*3,i*3+3),...extras.flatMap(a=>Array.from(a.values.subarray(i*a.width,(i+1)*a.width)))].join(',');let at=seen.get(key);if(at===undefined){at=unique.length;seen.set(key,at);unique.push(i);}remap[i]=at;}
 const positions=new Float32Array(unique.length*3),attributes=new Float32Array(unique.length*weights.length);
 for(let i=0;i<unique.length;i++){const from=unique[i];positions.set(position.values.subarray(from*3,from*3+3),i*3);let offset=i*weights.length;for(const a of extras){attributes.set(a.values.subarray(from*a.width,(from+1)*a.width),offset);offset+=a.width;}}
 const welded=Uint32Array.from(original,i=>remap[i]),targetCount=Math.max(12,Math.floor(original.length*ratio/3)*3);
 if(costNormals){
  // Cost-only geometric normals protect curvature on provider meshes whose
  // absent NORMAL attribute makes AR use flat shading. Never author new shading.
  const normals=new Float64Array(positions.length);
  for(let k=0;k<welded.length;k+=3){const [a,b,c]=[welded[k]*3,welded[k+1]*3,welded[k+2]*3],u=[0,1,2].map(j=>positions[b+j]-positions[a+j]),v=[0,1,2].map(j=>positions[c+j]-positions[a+j]),n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];for(const i of[a,b,c])for(let j=0;j<3;j++)normals[i+j]+=n[j];}
  for(let i=0;i<unique.length;i++){const n=normals.subarray(i*3,i*3+3),d=Math.hypot(...n);for(let j=0;j<3;j++)attributes[i*weights.length+weights.length-3+j]=d?n[j]/d:0;}
 }
 const lockRow=lockReceipt.primitives.find(r=>r.mesh_index===mi&&r.primitive_index===pi);
 if(!lockRow)throw Error('Missing visible contour source primitive');
 const lockedPositions=new Set(lockRow.source_vertex_ids.map(i=>Array.from(position.values.subarray(i*3,i*3+3)).join(',')));
 const vertexLock=Uint8Array.from(unique,i=>lockedPositions.has(Array.from(position.values.subarray(i*3,i*3+3)).join(','))?1:0);
 const [simplified,error]=MeshoptSimplifier.simplifyWithAttributes(welded,positions,3,attributes,weights.length,weights,vertexLock,targetCount,.01,['LockBorder']);
 const indices=Uint32Array.from(simplified,i=>unique[i]);
 receipts.push({mesh_index:mi,primitive_index:pi,source_triangles:original.length/3,lod_triangles:indices.length/3,error_fraction:error,locked_borders:true,attribute_weights:weights,cost_only_geometric_normals:costNormals,silhouette_locked_vertices:vertexLock.reduce((a,b)=>a+b,0),welded_vertices:unique.length});
 if(indices.length>=original.length)continue;
 const bytes=Buffer.from(indices.buffer),padding=Buffer.alloc((-length)&3);chunks.push(padding,bytes);length+=padding.length;
 const vi=doc.bufferViews.length;doc.bufferViews.push({buffer:0,byteOffset:length,byteLength:bytes.length,target:34963});length+=bytes.length;
 p.indices=doc.accessors.length;doc.accessors.push({bufferView:vi,componentType:5125,count:indices.length,type:'SCALAR'});
}
doc.buffers[0].byteLength=length;const binary=Buffer.concat(chunks),json=Buffer.from(JSON.stringify(doc)),jp=Buffer.alloc((-json.length)&3,32),bp=Buffer.alloc((-binary.length)&3),head=Buffer.alloc(12),jh=Buffer.alloc(8),bh=Buffer.alloc(8);
head.write('glTF');head.writeUInt32LE(2,4);head.writeUInt32LE(12+8+json.length+jp.length+8+binary.length+bp.length,8);jh.writeUInt32LE(json.length+jp.length);jh.writeUInt32LE(0x4e4f534a,4);bh.writeUInt32LE(binary.length+bp.length);bh.writeUInt32LE(0x004e4942,4);
const result=Buffer.concat([head,jh,json,jp,bh,binary,bp]);await fs.writeFile(output,result,{flag:'wx'});
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
await fs.writeFile(reportPath,JSON.stringify({method:'visible_contour_locked_frame_index_lod_qa_v1',locks_receipt_sha256:sha(await fs.readFile(locksPath)),source_sha256:sha(raw),output_sha256:sha(result),source_triangles:frameCount+opticalCount,optical_triangles:opticalCount,target_triangles:target,source_vertex_attributes_unchanged:true,optical_geometry_unchanged:true,primitives:receipts,accepted:false},null,2)+'\n',{flag:'wx'});
