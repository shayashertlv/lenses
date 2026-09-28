/** QA-only implementation. Original closed geometry is never remeshed.
 * Production front_sheet_v1 code is neither installed nor modified here.
 */
import * as T from 'three';
import {createLensAppearanceUniforms,evaluateLensAppearance,LENS_RESPONSE_GLSL} from '../src/eyewear/lens-appearance.ts';
import {OpticalOverflowChecker} from '../src/render/layer-overflow.ts';

const CAPACITY=4,WIDTH=480,HEIGHT=320,COLOR_TOLERANCE=5e-5,DEPTH_TOLERANCE=2e-6;
const BACKGROUND=[.12,.23,.37],OPAQUE=[.65,.1,.28];
const assert=(value,message)=>{if(!value)throw Error(message);};
const maxError=(a,b)=>Math.max(...a.map((v,i)=>Math.abs(v-b[i])));
const vector=a=>new T.Vector3(...a);
const descriptor=(r=[.15,.25,.35],bottom=[.12,.2,.3],top=[.9,.55,.2])=>({schema_version:1,color_space:'scene_linear_srgb_D65',
 density_interpolation:'piecewise_smoothstep_optical_density',vertical_coordinate:'lens_local_bottom_0_top_1',
 normal_reflectance_rgb:r,refractive_index:1.5,roughness:0,
 optical_density_keyframes:[{v:0,optical_density_rgb:bottom},{v:.4,optical_density_rgb:bottom.map((x,i)=>(x+top[i])*.5)},{v:1,optical_density_rgb:top}],
 angular_reflectance_keyframes:null});
const A=descriptor(),B=descriptor([.65,.35,.1],[.25,.1,.35],[.5,.8,.3]),MIRROR=descriptor([1,1,1],[0,0,0],[0,0,0]);
const box=(center=[0,0,0],size=[1.6,1.2,.18],extra={})=>({center,size,...extra});
const group=(id,appearance=A,boxes=[box()])=>({id,appearance,boxes});
const poses=[{yaw:0,roll:0},{yaw:37,roll:21},{yaw:180,roll:0}];
const fixtures=[
 {id:'closed_lens',groups:[group('left')]},
 {id:'reversed_winding',groups:[group('left',A,[box([0,0,0],[1.6,1.2,.18],{reversed:true})])]},
 {id:'disconnected_one_group',groups:[group('one_element',A,[box([0,-.44,0],[1.6,.42,.18]),box([0,.44,.08],[1.6,.42,.18])])]},
 {id:'two_intersections_same_group',groups:[group('one_element',A,[box([0,0,.22],[1.6,1.2,.12]),box([0,0,-.22],[1.45,1.1,.12])])]},
 {id:'overlapping_distinct_groups',groups:[group('front',A,[box([-.12,0,.25])]),group('rear',B,[box([.12,0,-.25])])]},
 {id:'shared_descriptor_distinct_groups',groups:[group('left',A,[box([-.15,0,.23])]),group('right',A,[box([.15,0,-.23])])]},
 {id:'three_groups',groups:[group('a',A,[box([-.15,0,.4])]),group('b',B),group('c',A,[box([.15,0,-.4])])]},
 {id:'four_groups',groups:[group('a',A,[box([-.12,0,.6])]),group('b',B,[box([0,0,.2])]),group('c',A,[box([.08,0,-.2])]),group('d',B,[box([.16,0,-.6])])]},
 {id:'opaque_inside_closed_group',groups:[group('volume',A,[box([0,0,0],[1.6,1.2,.65])])],opaque:[box([0,0,0],[.52,.9,.08])]},
 {id:'opaque_between_groups',groups:[group('a',A,[box([0,0,.4])]),group('b',B,[box([0,0,-.4])])],opaque:[box([0,0,0],[.52,.9,.08])]},
 {id:'opaque_before_groups',groups:[group('a')],opaque:[box([0,0,.55],[.52,.9,.08])]},
 {id:'total_mirror_front',groups:[group('mirror',MIRROR,[box([0,0,.3])]),group('tint',A,[box([0,0,-.3])])]},
 {id:'total_mirror_behind',groups:[group('tint',A,[box([0,0,.3])]),group('mirror',MIRROR,[box([0,0,-.3])])]},
 {id:'identical_duplicate',groups:[group('one',A,[box(),box()])]},
];
function target(w,h){const rt=new T.WebGLRenderTarget(w,h,{type:T.FloatType,minFilter:T.NearestFilter,magFilter:T.NearestFilter,depthBuffer:true,stencilBuffer:false});
 rt.texture.colorSpace=T.LinearSRGBColorSpace;rt.texture.generateMipmaps=false;rt.depthTexture=new T.DepthTexture(w,h,T.FloatType);return rt;}
function makeCamera(kind,w,h){const camera=kind==='perspective'?new T.PerspectiveCamera(35,w/h,.1,20):new T.OrthographicCamera(-1.3*w/h,1.3*w/h,1.3,-1.3,.1,20);
 camera.position.set(0,0,5);camera.updateMatrixWorld();return camera;}
function poseMatrix(pose){return pose.matrix?.clone()??new T.Matrix4().makeRotationFromEuler(new T.Euler(0,pose.yaw*Math.PI/180,pose.roll*Math.PI/180,'YXZ'));}
function groupBounds(g){return [Math.min(...g.boxes.map(b=>b.center[1]-b.size[1]/2)),Math.max(...g.boxes.map(b=>b.center[1]+b.size[1]/2))];}
function geometry(b,bounds,segments=1){const geometry=new T.BoxGeometry(...b.size,segments,segments,segments);geometry.translate(...b.center);
 const p=geometry.getAttribute('position'),uv=geometry.getAttribute('uv'),n=geometry.getAttribute('normal');
 for(let i=0;i<p.count;i++){uv.setXY(i,0,(p.getY(i)-bounds[0])/(bounds[1]-bounds[0]));if(b.normalOverride)n.setXYZ(i,...b.normalOverride);}
 if(b.reversed){const index=geometry.index;for(let i=0;i<index.count;i+=3){const a=index.getX(i);index.setX(i,index.getX(i+1));index.setX(i+1,a);}}
 return geometry;
}
function opticalMaterial(appearance){return new T.ShaderMaterial({side:T.DoubleSide,forceSinglePass:true,transparent:false,blending:T.NoBlending,
 depthTest:true,depthWrite:true,toneMapped:false,uniforms:{...createLensAppearanceUniforms(appearance),mode:{value:0},nearest:{value:null},opaqueDepth:{value:null},
 previous:{value:null},size:{value:new T.Vector2()},first:{value:true},perspective:{value:false}},
 vertexShader:`varying float intrinsicV; varying vec3 viewNormal,viewPosition;
 void main(){intrinsicV=uv.y;viewNormal=normalize(normalMatrix*normal);vec4 p=modelViewMatrix*vec4(position,1.0);viewPosition=-p.xyz;gl_Position=projectionMatrix*p;}`,
 fragmentShader:`${LENS_RESPONSE_GLSL}
 uniform int mode;uniform sampler2D nearest,opaqueDepth,previous;uniform vec2 size;uniform bool first,perspective;
 varying float intrinsicV;varying vec3 viewNormal,viewPosition;
 void main(){if(mode==0){gl_FragColor=vec4(0.0,0.0,0.0,gl_FragCoord.z);return;}
 vec2 uv=gl_FragCoord.xy/size;
 if(gl_FragCoord.z!=texture2D(nearest,uv).a)discard;
 if(gl_FragCoord.z>=texture2D(opaqueDepth,uv).r)discard;
 float prior=texture2D(previous,uv).a;
 if(!first&&((mode==1&&gl_FragCoord.z>=prior)||(mode==2&&gl_FragCoord.z<=prior)))discard;
 vec3 V=perspective?normalize(viewPosition):vec3(0.0,0.0,1.0),N=normalize(viewNormal);if(dot(N,V)<0.0)N=-N;
 float angle=degrees(acos(clamp(dot(N,V),0.0,1.0)));
 LensResponse response=evaluateLensResponse(clamp(intrinsicV,0.0,1.0),angle);
 if(mode==3){gl_FragColor=vec4(intrinsicV,angle,dot(N,V),gl_FragCoord.z);return;}
 if(mode==4){gl_FragColor=vec4(response.reflectance,gl_FragCoord.z);return;}
 if(mode==5){gl_FragColor=vec4(response.transmission,gl_FragCoord.z);return;}
 if(mode==6){gl_FragColor=vec4(N,gl_FragCoord.z);return;}
 if(mode==7){gl_FragColor=vec4(V,gl_FragCoord.z);return;}
 if(mode==2){gl_FragColor=vec4(response.transmission,gl_FragCoord.z);return;}
 vec3 environment=vec3(.55,.42,.28)+.16*reflect(-V,N);
 gl_FragColor=vec4(response.transmission*texture2D(previous,uv).rgb+response.reflectance*environment,gl_FragCoord.z);
 }`});}

class Transport{
 constructor(renderer,fixture,pose,camera,w,h,light=false,segments=1){Object.assign(this,{renderer,fixture,pose,camera,w,h,light});this.maps=[];this.scenes=[];this.materials=[];this.geometry=[];
  this.opaque=target(w,h);this.opaqueScene=new T.Scene();this.opaqueMaterials=[];const matrix=poseMatrix(pose);
  for(const b of fixture.opaque??[]){const geom=geometry(b,[-1,1]);this.geometry.push(geom);const mat=new T.MeshBasicMaterial({color:new T.Color(...OPAQUE),toneMapped:false,side:T.DoubleSide});
   this.opaqueMaterials.push(mat);const mesh=new T.Mesh(geom,mat);mesh.matrixAutoUpdate=false;mesh.matrix.copy(matrix);this.opaqueScene.add(mesh);}
  this.all=new T.Scene();this.records=[];
  for(const g of fixture.groups){const map=target(w,h),scene=new T.Scene(),material=opticalMaterial(g.appearance);material.uniforms.size.value.set(w,h);material.uniforms.perspective.value=!!camera.isPerspectiveCamera;
   material.uniforms.nearest.value=map.texture;material.uniforms.opaqueDepth.value=this.opaque.depthTexture;
   this.maps.push(map);this.scenes.push(scene);this.materials.push(material);
   for(const b of g.boxes){const geom=geometry(b,groupBounds(g),segments);this.geometry.push(geom);for(const destination of [scene,this.all]){const mesh=new T.Mesh(geom,material);mesh.matrixAutoUpdate=false;mesh.matrix.copy(matrix);mesh.frustumCulled=false;destination.add(mesh);}}
  }
  this.peels=Array.from({length:light?5:3},()=>target(w,h));this.checker=new OpticalOverflowChecker();
  this.quadCamera=new T.OrthographicCamera(-1,1,1,-1,0,1);this.quadGeometry=new T.PlaneGeometry(2,2);
  this.copyMaterial=new T.ShaderMaterial({depthTest:false,depthWrite:false,blending:T.NoBlending,toneMapped:false,uniforms:{image:{value:null}},
   vertexShader:'varying vec2 uvOut;void main(){uvOut=uv;gl_Position=vec4(position.xy,0.0,1.0);}',fragmentShader:'uniform sampler2D image;varying vec2 uvOut;void main(){gl_FragColor=vec4(texture2D(image,uvOut).rgb,0.0);}'});
  this.copyScene=new T.Scene();this.copyScene.add(new T.Mesh(this.quadGeometry,this.copyMaterial));
 }
 render(){const r=this.renderer;r.autoClear=false;r.setScissorTest(false);r.setRenderTarget(this.opaque);r.setViewport(0,0,this.w,this.h);r.state.buffers.depth.setClear(1);
  r.setClearColor(new T.Color(...(this.light?[1,1,1]:BACKGROUND)),1);r.clear(true,true,false);r.render(this.opaqueScene,this.camera);
  for(let i=0;i<this.maps.length;i++){const m=this.materials[i];m.uniforms.mode.value=0;m.uniforms.nearest.value=null;m.depthFunc=T.LessDepth;r.setRenderTarget(this.maps[i]);r.setClearColor(0,1);r.clear(true,true,false);r.render(this.scenes[i],this.camera);m.uniforms.nearest.value=this.maps[i].texture;}
  let previous=this.opaque.texture;
  for(let layer=0;layer<=CAPACITY;layer++){const out=this.light?this.peels[layer]:this.peels[layer===CAPACITY?2:layer%2];r.setRenderTarget(out);r.state.buffers.depth.setClear(this.light?1:0);
   r.setClearColor(this.light?0xffffff:0,this.light?1:0);r.clear(true,true,false);
   if(!this.light){this.copyMaterial.uniforms.image.value=previous;r.render(this.copyScene,this.quadCamera);}
   for(const m of this.materials){m.uniforms.mode.value=this.light?2:1;m.uniforms.first.value=layer===0;m.uniforms.previous.value=previous;m.depthFunc=this.light?T.LessDepth:T.GreaterDepth;}
   r.render(this.all,this.camera);
   if(layer===CAPACITY)this.checker.assertNoOverflow(r,out.texture,this.w,this.h,this.light?'Prototype light groups':'Prototype camera groups',this.light?'one_minus_alpha':'alpha');
   else previous=out.texture;
   if(layer===CAPACITY-1)this.output=out;
  }
  r.state.buffers.depth.setClear(1);r.setRenderTarget(null);return this.output;
 }
 read(rt=this.output){const data=new Float32Array(this.w*this.h*4);this.renderer.readRenderTargetPixels(rt,0,0,this.w,this.h,data);return data;}
 diagnostic(groupIndex,x,y){const result={},out=target(this.w,this.h),r=this.renderer,m=this.materials[groupIndex];m.depthFunc=T.LessDepth;
  for(let mode=3;mode<=7;mode++){m.uniforms.mode.value=mode;r.setRenderTarget(out);r.state.buffers.depth.setClear(1);r.setClearColor(0,1);r.clear(true,true,false);r.render(this.scenes[groupIndex],this.camera);const data=this.read(out),i=(y*this.w+x)*4;result[mode]=Array.from(data.slice(i,i+4));}
  out.dispose();r.setRenderTarget(null);return result;
 }
 receiver(depth){const uniforms={opaque:{value:this.opaque.depthTexture},receiverDepth:{value:depth}};for(let i=0;i<CAPACITY;i++)uniforms[`layer${i}`]={value:this.peels[i].texture};
  const material=new T.ShaderMaterial({depthTest:false,depthWrite:false,blending:T.NoBlending,toneMapped:false,uniforms,
   vertexShader:'varying vec2 uvOut;void main(){uvOut=uv;gl_Position=vec4(position.xy,0.0,1.0);}',
   fragmentShader:`varying vec2 uvOut;uniform sampler2D opaque;uniform float receiverDepth;${Array.from({length:4},(_,i)=>`uniform sampler2D layer${i};`).join('')}
   void main(){vec3 T=vec3(1.0);if(texture2D(opaque,uvOut).r<receiverDepth)T=vec3(0.0);
   ${Array.from({length:4},(_,i)=>`vec4 p${i}=texture2D(layer${i},uvOut);if(p${i}.a<receiverDepth)T*=p${i}.rgb;`).join('')}
   gl_FragColor=vec4(T,1.0);}`});
  const scene=new T.Scene();scene.add(new T.Mesh(this.quadGeometry,material));const out=target(this.w,this.h);this.renderer.setRenderTarget(out);this.renderer.render(scene,this.quadCamera);
  const data=this.read(out);this.renderer.setRenderTarget(null);out.dispose();material.dispose();return data;
 }
 dispose(){for(const target of [this.opaque,...this.maps,...this.peels])target.dispose();for(const g of this.geometry)g.dispose();for(const m of [...this.materials,...this.opaqueMaterials,this.copyMaterial])m.dispose();this.quadGeometry.dispose();this.checker.dispose();}
}

// Independent analytic slab intersections. No GPU buffers, triangles, Three
// raycaster, peel code or rendered depth participate in the oracle.
function boxHit(origin,direction,b){let enter=-Infinity,exit=Infinity,enterAxis=-1,exitAxis=-1,enterSign=0,exitSign=0;
 for(let a=0;a<3;a++){const low=b.center[a]-b.size[a]/2,high=b.center[a]+b.size[a]/2;
  if(Math.abs(direction[a])<1e-14){if(origin[a]<low||origin[a]>high)return null;continue;}
  let t0=(low-origin[a])/direction[a],t1=(high-origin[a])/direction[a],s0=-1,s1=1;
  if(t0>t1){[t0,t1]=[t1,t0];[s0,s1]=[s1,s0];}
  if(t0>enter){enter=t0;enterAxis=a;enterSign=s0;}if(t1<exit){exit=t1;exitAxis=a;exitSign=s1;}if(enter>exit)return null;
 }
 if(exit<=0)return null;const t=enter>0?enter:exit,axis=enter>0?enterAxis:exitAxis,normal=[0,0,0];normal[axis]=enter>0?enterSign:exitSign;
 const point=origin.map((v,i)=>v+t*direction[i]);const edgeMargin=Math.min(...[0,1,2].filter(a=>a!==axis).map(a=>b.size[a]/2-Math.abs(point[a]-b.center[a])));
 return {t,point,normal:b.normalOverride??normal,edgeMargin};
}
function ray(camera,x,y,w,h,pose){const uv=new T.Vector3(2*(x+.5)/w-1,2*(y+.5)/h-1,0),world=uv.unproject(camera);
 const origin=camera.isPerspectiveCamera?camera.position.clone():new T.Vector3(world.x,world.y,camera.position.z);
 const direction=camera.isPerspectiveCamera?world.sub(origin).normalize():new T.Vector3(0,0,-1);
 const inverse=poseMatrix(pose).invert();return {origin:origin.applyMatrix4(inverse).toArray(),direction:direction.transformDirection(inverse).toArray()};}
function analytic(fixture,pose,camera,x,y,w,h){const r=ray(camera,x,y,w,h,pose),matrix=poseMatrix(pose),normalMatrix=new T.Matrix3().getNormalMatrix(camera.matrixWorldInverse.clone().multiply(matrix));
 const worldNormal=normal=>vector(normal).applyMatrix3(normalMatrix).normalize();
 const depth=point=>(vector(point).applyMatrix4(matrix).project(camera).z+1)/2;
 let margin=Infinity,opaque=null;for(const b of fixture.opaque??[]){const hit=boxHit(r.origin,r.direction,b);if(hit&&(!opaque||hit.t<opaque.t))opaque=hit;}
 if(opaque)margin=Math.min(margin,opaque.edgeMargin);
 const hits=[],ambiguities=[];
 for(const g of fixture.groups){const candidates=g.boxes.map(b=>boxHit(r.origin,r.direction,b)).filter(Boolean).sort((a,b)=>a.t-b.t);if(!candidates.length)continue;
  const hit=candidates[0];margin=Math.min(margin,hit.edgeMargin);for(const other of candidates.slice(1))if(Math.abs(other.t-hit.t)<1e-9&&maxError(other.normal,hit.normal)>1e-7)ambiguities.push('same_group_conflicting_attributes');
  const bounds=groupBounds(g),rawV=(hit.point[1]-bounds[0])/(bounds[1]-bounds[0]);
  assert(rawV>=-2e-12&&rawV<=1+2e-12,'Analytic intersection escaped group height');const v=Math.max(0,Math.min(1,rawV));
  const N=worldNormal(hit.normal),pointView=vector(hit.point).applyMatrix4(matrix).applyMatrix4(camera.matrixWorldInverse),V=camera.isPerspectiveCamera?pointView.negate().normalize():new T.Vector3(0,0,1);
  if(N.dot(V)<0)N.negate();const cosine=Math.max(0,Math.min(1,N.dot(V))),angle=Math.acos(cosine)*180/Math.PI;
  const response=evaluateLensAppearance(g.appearance,v,angle),reflected=V.clone().negate().addScaledVector(N,2*cosine);
  hits.push({...hit,group:g.id,depth:depth(hit.point),v,angle,R:response.reflectance_rgb,T:response.transmission_rgb,E:[.55,.42,.28].map((a,i)=>a+.16*reflected.getComponent(i))});
 }
 hits.sort((a,b)=>a.t-b.t);for(let i=1;i<hits.length;i++)if(Math.abs(hits[i].t-hits[i-1].t)<1e-8)ambiguities.push('coincident_distinct_groups');
 const relevant=hits.filter(hit=>!opaque||hit.t<opaque.t);let color=opaque?[...OPAQUE]:[...BACKGROUND];
 for(const hit of [...relevant].reverse())color=color.map((v,i)=>hit.T[i]*v+hit.R[i]*hit.E[i]);
 return {hits,relevant,opaqueDepth:opaque?depth(opaque.point):1,color,margin,ambiguities};
}

// Independent D3D11 raster reference built from analytic box face corners.
// The actual renderer mesh, framebuffer and depth textures are never consulted.
// D3D11 requires n.8 nearest-even XY snapping and interpolation from snapped
// positions (§3.4.1 and15.16). Continuous slab-ray diagnostics remain reported.
const nearestEven=value=>{const base=Math.floor(value),part=value-base;return part<.5?base:part>.5?base+1:base%2===0?base:base+1;};
function rasterFixture(fixture,pose,camera,w,h){const mv=camera.matrixWorldInverse.clone().multiply(poseMatrix(pose)),normal=new T.Matrix3().getNormalMatrix(mv),projection=camera.projectionMatrix.clone();
 for(const m of [mv,normal,projection])m.elements=m.elements.map(Math.fround);
 function boxTriangles(b,bounds){const triangles=[],faces=[[2,1,0,-1,-1,1],[2,1,0,1,-1,-1],[0,2,1,1,1,1],[0,2,1,1,-1,-1],[0,1,2,1,-1,1],[0,1,2,-1,-1,-1]];
  for(const [u,v,a,us,vs,as]of faces){const vertices=[];
   for(let y=0;y<2;y++)for(let x=0;x<2;x++){const local=[0,0,0];local[u]=(x-.5)*b.size[u]*us;local[v]=(y-.5)*b.size[v]*vs;local[a]=b.size[a]/2*as;
    for(let k=0;k<3;k++)local[k]=Math.fround(Math.fround(local[k])+b.center[k]);
    const n=[0,0,0];n[a]=as;const N=vector((b.normalOverride??n).map(Math.fround)).applyMatrix3(normal).normalize();
    const view=new T.Vector4(...local,1).applyMatrix4(mv),clip=view.clone().applyMatrix4(projection),point=new T.Vector3((clip.x/clip.w+1)*w/2,(clip.y/clip.w+1)*h/2,(clip.z/clip.w+1)/2);
    vertices.push({point:new T.Vector3(nearestEven(point.x*256)/256,nearestEven(point.y*256)/256,point.z),inverseW:1/clip.w,
     v:Math.fround((local[1]-bounds[0])/(bounds[1]-bounds[0])),normal:N,view:new T.Vector3(-view.x,-view.y,-view.z)});
   }
   for(const indices of [[0,2,1],[2,3,1]])triangles.push(indices.map(i=>vertices[i]));
  }return triangles;
 }
 return {groups:fixture.groups.map(g=>({...g,triangles:g.boxes.flatMap(b=>boxTriangles(b,groupBounds(g)))})),opaque:(fixture.opaque??[]).flatMap(b=>boxTriangles(b,[-1,1])),perspective:!!camera.isPerspectiveCamera};
}
function rasterHit(triangles,x,y){let nearest=null;for(const t of triangles){const [a,b,c]=t.map(v=>v.point),den=(b.y-c.y)*(a.x-c.x)+(c.x-b.x)*(a.y-c.y);if(Math.abs(den)<1e-12)continue;
 const p=((b.y-c.y)*(x+.5-c.x)+(c.x-b.x)*(y+.5-c.y))/den,q=((c.y-a.y)*(x+.5-c.x)+(a.x-c.x)*(y+.5-c.y))/den,weights=[p,q,1-p-q];if(weights.some(v=>v<0))continue;
 const depth=weights.reduce((sum,v,i)=>sum+v*t[i].point.z,0);if(depth<0||depth>=1||nearest&&depth>=nearest.depth)continue;
 const iw=weights.map((v,i)=>v*t[i].inverseW),total=iw.reduce((a,b)=>a+b,0),perspective=iw.map(v=>v/total),normal=new T.Vector3(),view=new T.Vector3();let v=0;
 for(let i=0;i<3;i++){normal.addScaledVector(t[i].normal,perspective[i]);view.addScaledVector(t[i].view,perspective[i]);v+=perspective[i]*t[i].v;}
 nearest={depth,v:Math.max(0,Math.min(1,v)),normal:normal.normalize(),view:view.normalize()};
 }return nearest;}
function rasterExpected(raster,x,y){const opaque=rasterHit(raster.opaque,x,y),hits=[];for(const g of raster.groups){const hit=rasterHit(g.triangles,x,y);if(!hit)continue;
 const V=raster.perspective?hit.view:new T.Vector3(0,0,1),N=hit.normal;if(N.dot(V)<0)N.negate();const cosine=Math.max(0,Math.min(1,N.dot(V))),angle=Math.acos(cosine)*180/Math.PI;
 const response=evaluateLensAppearance(g.appearance,hit.v,angle),reflection=V.clone().negate().addScaledVector(N,2*cosine);
 hits.push({group:g.id,depth:hit.depth,v:hit.v,angle,normal:N.toArray(),view:V.toArray(),T:response.transmission_rgb,R:response.reflectance_rgb,E:[.55,.42,.28].map((v,i)=>v+.16*reflection.getComponent(i))});}
 hits.sort((a,b)=>a.depth-b.depth);const relevant=hits.filter(hit=>!opaque||hit.depth<opaque.depth);let color=opaque?[...OPAQUE]:[...BACKGROUND];for(const hit of [...relevant].reverse())color=color.map((v,i)=>hit.T[i]*v+hit.R[i]*hit.E[i]);
 return {hits,relevant,color,opaqueDepth:opaque?.depth??1};
}
function summary(rows,key){const values=rows.map(r=>r[key]);return {maximum:Math.max(0,...values),mean:values.reduce((a,b)=>a+b,0)/Math.max(1,values.length),count:values.length};}
function samples(w,h){const points=[];for(let y=11;y<h-10;y+=17)for(let x=13;x<w-10;x+=19)points.push([x,y]);return points;}
function screenshot(data,w,h,label){const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;const image=canvas.getContext('2d').createImageData(w,h);
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){const from=(y*w+x)*4,to=((h-1-y)*w+x)*4;for(let c=0;c<3;c++){const linear=Math.max(0,Math.min(1,data[from+c]));image.data[to+c]=Math.round(255*(linear<=.0031308?12.92*linear:1.055*linear**(1/2.4)-.055));}image.data[to+3]=255;}
 canvas.getContext('2d').putImageData(image,0,0);const card=document.createElement('div');card.className='card';const text=document.createElement('div');text.textContent=label;const img=new Image();img.src=canvas.toDataURL();card.append(text,img);document.querySelector('#cards').append(card);
}

async function checkCase(renderer,fixture,pose,kind){const camera=makeCamera(kind,WIDTH,HEIGHT),visible=new Transport(renderer,fixture,pose,camera,WIDTH,HEIGHT);
 visible.render();const pixels=visible.read(),maps=visible.maps.map(rt=>visible.read(rt)),rows=[],raster=rasterFixture(fixture,pose,camera,WIDTH,HEIGHT);let excluded=0,optical=0,maxHits=0;
 for(const [x,y] of samples(WIDTH,HEIGHT)){const continuous=analytic(fixture,pose,camera,x,y,WIDTH,HEIGHT);if(continuous.margin<.035||continuous.ambiguities.length){excluded++;continue;}const expected=rasterExpected(raster,x,y);
  const offset=(y*WIDTH+x)*4,actual=Array.from(pixels.slice(offset,offset+3));if(expected.relevant.length)optical++;maxHits=Math.max(maxHits,expected.relevant.length);
  const depthErrors=fixture.groups.map((g,i)=>{const hit=expected.hits.find(h=>h.group===g.id);return Math.abs(maps[i][offset+3]-(hit?.depth??1));});
  const nominalDepthErrors=fixture.groups.map((g,i)=>Math.abs(maps[i][offset+3]-(continuous.hits.find(h=>h.group===g.id)?.depth??1)));
  rows.push({x,y,expected:expected.color,actual,error:maxError(actual,expected.color),depthError:Math.max(...depthErrors),nominalError:maxError(actual,continuous.color),nominalDepthError:Math.max(...nominalDepthErrors),groups:expected.relevant.map(h=>h.group)});
 }
 if(kind==='perspective'&&pose.yaw===0)screenshot(pixels,WIDTH,HEIGHT,fixture.id);
 const result={fixture:fixture.id,pose,projection:kind,samples:rows.length,opticalSamples:optical,excludedByGeometryMargin:excluded,maximumGroups:maxHits,
  color:summary(rows,'error'),depth:summary(rows,'depthError'),continuousRayColor:summary(rows,'nominalError'),continuousRayDepth:summary(rows,'nominalDepthError'),worst:rows.reduce((a,b)=>!a||b.error>a.error?b:a,null),depthWorst:rows.reduce((a,b)=>!a||b.depthError>a.depthError?b:a,null)};
 if(result.color.maximum>COLOR_TOLERANCE){const {x,y}=result.worst;result.failureDiagnostics={expected:rasterExpected(raster,x,y),groups:fixture.groups.map((g,i)=>({id:g.id,actual:visible.diagnostic(i,x,y)}))};}
 visible.dispose();return result;
}
async function checkLight(renderer,fixture,pose){const camera=makeCamera('orthographic',WIDTH,HEIGHT);camera.position.set(0,0,5);camera.quaternion.setFromEuler(new T.Euler(-.28,.34,0));camera.position.copy(new T.Vector3(0,0,5).applyQuaternion(camera.quaternion));camera.updateMatrixWorld();
 // Analytic ray helper assumes the canonical camera frame; compose the inverse
 // light orientation into the object pose for an equivalent independent setup.
 const lightRotation=new T.Matrix4().makeRotationFromQuaternion(camera.quaternion).invert().multiply(poseMatrix(pose));
 camera.position.set(0,0,5);camera.quaternion.identity();camera.updateMatrixWorld();
 const lightPose={matrix:lightRotation};const originalPoseFunction=pose;
 // Pass an explicit matrix to both geometry and analytic transforms. The ray
 // intersections remain analytic slabs, not mesh/renderer-derived triangles.
 const light=new Transport(renderer,fixture,lightPose,camera,WIDTH,HEIGHT,true);light.render();const peels=light.peels.slice(0,4).map(rt=>light.read(rt)),rows=[],raster=rasterFixture(fixture,lightPose,camera,WIDTH,HEIGHT);
 const receivers=[.7,0,-.7].map(z=>({z,depth:(new T.Vector3(0,0,z).project(camera).z+1)/2}));
 const receiverPixels=receivers.map(receiver=>light.receiver(receiver.depth));const receiverRows=[];
 for(const [x,y]of samples(WIDTH,HEIGHT)){const continuous=analytic(fixture,lightPose,camera,x,y,WIDTH,HEIGHT);if(continuous.margin<.035||continuous.ambiguities.length)continue;const expected=rasterExpected(raster,x,y),offset=(y*WIDTH+x)*4;
  for(let i=0;i<4;i++){const hit=expected.relevant[i],nominal=continuous.relevant[i],actual=Array.from(peels[i].slice(offset,offset+3));rows.push({x,y,layer:i,error:maxError(actual,hit?.T??[1,1,1]),depthError:Math.abs(peels[i][offset+3]-(hit?.depth??1)),nominalError:maxError(actual,nominal?.T??[1,1,1]),nominalDepthError:Math.abs(peels[i][offset+3]-(nominal?.depth??1))});}
  for(let i=0;i<receivers.length;i++){const receiver=receivers[i];if(expected.hits.some(hit=>Math.abs(hit.depth-receiver.depth)<1e-5)||Math.abs(expected.opaqueDepth-receiver.depth)<1e-5)continue;
   let transmission=[1,1,1];if(expected.opaqueDepth<receiver.depth)transmission=[0,0,0];else for(const hit of expected.relevant)if(hit.depth<receiver.depth)transmission=transmission.map((v,c)=>v*hit.T[c]);
   const actual=Array.from(receiverPixels[i].slice(offset,offset+3));receiverRows.push({x,y,receiverZ:receiver.z,error:maxError(actual,transmission),expected:transmission,actual});}
 }
 const result={fixture:fixture.id,pose:originalPoseFunction,peels:summary(rows,'error'),depth:summary(rows,'depthError'),continuousRayColor:summary(rows,'nominalError'),continuousRayDepth:summary(rows,'nominalDepthError'),receiver:summary(receiverRows,'error'),
  worst:rows.reduce((a,b)=>!a||b.error>a.error?b:a,null),depthWorst:rows.reduce((a,b)=>!a||b.depthError>a.depthError?b:a,null),receiverWorst:receiverRows.reduce((a,b)=>!a||b.error>a.error?b:a,null)};light.dispose();return result;
}

async function timing(renderer){const result=[];for(const [w,h]of[[480,320],[1280,720]]){const fixture={groups:[group('a',A,[box([-.2,0,.15])]),group('b',B,[box([.2,0,-.15])])]},pose=poses[1];
 const camera=new Transport(renderer,fixture,pose,makeCamera('perspective',w,h),w,h,false,32),light=new Transport(renderer,fixture,pose,makeCamera('orthographic',512,512),512,512,true,32);
 for(let i=0;i<3;i++){camera.render();light.render();renderer.getContext().finish();}
 const measurements=[];for(let i=0;i<10;i++){const start=performance.now();camera.render();light.render();const submitted=performance.now();renderer.getContext().finish();measurements.push({submissionMs:submitted-start,synchronizedMs:performance.now()-start});}
 result.push({width:w,height:h,lightMap:512,groups:2,sourceTriangles:2*6*2*32*32,includesTwoOverflowReadbacks:true,
  timings:{submission:summary(measurements,'submissionMs'),synchronized:summary(measurements,'synchronizedMs')},
  targetBytesEstimate:{camera:(4+2)*w*h*20,light:(6+2)*512*512*20,groupMapsOnly:(2*w*h+2*512*512)*20},
  memoryScope:'RGBA32F color plus Float32 depth; excludes driver padding, reduction targets, geometry and browser framebuffer'});
 camera.dispose();light.dispose();await new Promise(resolve=>setTimeout(resolve,0));}return result;}

function tieControls(renderer){const camera=makeCamera('orthographic',WIDTH,HEIGHT),pose=poses[0],result=[];
 for(const conflict of [false,true]){const first=box(),second=box([0,0,0],[1.6,1.2,.18],conflict?{normalOverride:[.8,0,.6]}:{}),images=[];
  const fixture={groups:[group('one',A,[first,second])]},oracle=analytic(fixture,pose,camera,WIDTH/2,HEIGHT/2,WIDTH,HEIGHT);
  for(const boxes of [[first,second],[second,first]]){const transport=new Transport(renderer,{groups:[group('one',A,boxes)]},pose,camera,WIDTH,HEIGHT);transport.render();images.push(transport.read());transport.dispose();}
  let maximum=0;for(let i=0;i<images[0].length;i++)if(i%4!==3)maximum=Math.max(maximum,Math.abs(images[0][i]-images[1][i]));
  result.push({control:conflict?'same_group_conflicting_normals':'same_group_identical_attributes',analyticAmbiguities:oracle.ambiguities,drawOrderMaximumDifference:maximum,
   supported:!conflict,runtimeRejects:false,contract:conflict?'Unsupported: a future importer must reject conflicting coincident attributes; depth alone has no unique response.':'Duplicate nearest hits with identical optical attributes must be order-invariant.'});
 }
 const coincident={groups:[group('a',A),group('b',B)]};const oracle=analytic(coincident,pose,camera,WIDTH/2,HEIGHT/2,WIDTH,HEIGHT);
 result.push({control:'coincident_distinct_groups',analyticAmbiguities:oracle.ambiguities,supported:false,runtimeRejects:false,
  contract:'Unsupported: distinct optical events at equal depth need rejection or an independently authored ordering contract.'});return result;
}

export async function run(){const report=window.groupReport={schemaVersion:1,profile:'effective_optical_group_v1',implementation:'QA-only prototype',status:'running',
 accepted:false,qualityVerdict:'unmeasured',tolerances:{linearCoefficient:COLOR_TOLERANCE,normalizedDepth:DEPTH_TOLERANCE},capacityLimit:CAPACITY,cases:[],lightCases:[],
 assumptions:['One complete effective R/T response at nearest intersection per semantic group.',
 'Symmetric front/back response with face-forward interpolated authored normals; no refraction, thickness absorption or internal reflections.',
 'A receiver or opaque surface inside a closed group receives the complete effective entry response.',
 'Analytic geometry margin excludes edges before reading residuals; synthetic scene-linear float targets, roughness zero.',
 'Receiver test is an unfiltered effective product, without production contact/falloff, penumbra, camera display mapping or PMREM.',
 'Group identification, derived optical coordinates and original source geometry quality are not established by this proof.',
 'Conflicting same-depth attributes require separate importer rejection; exact depth alone cannot choose a unique response.'],fixtures};
 const renderer=new T.WebGLRenderer({antialias:false});renderer.setSize(WIDTH,HEIGHT);renderer.outputColorSpace=T.LinearSRGBColorSpace;renderer.toneMapping=T.NoToneMapping;
 const gl=renderer.getContext(),debug=gl.getExtension('WEBGL_debug_renderer_info');report.backend={renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),subpixelBits:gl.getParameter(gl.SUBPIXEL_BITS),maxTextureUnits:gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS)};
 assert(/Direct3D11|D3D11/i.test(report.backend.renderer),'Discrete raster oracle is validated only for D3D11');
 report.rasterReference={subpixelBits:8,rounding:'nearest_even',geometry:'Analytic box face corners, declared tessellation, float32 authored positions/UV and uploaded matrices; no GPU geometry/depth used.',
  specification:'https://microsoft.github.io/DirectX-Specs/d3d/archive/D3D11_3_FunctionalSpec.htm#CoordinateSnapping',continuousSlabRayDiagnosticsRetained:true};
 assert(renderer.extensions.has('EXT_color_buffer_float'),'Float color targets unavailable');
 try{
  for(const fixture of fixtures){for(const pose of poses)for(const kind of ['perspective','orthographic'])report.cases.push(await checkCase(renderer,fixture,pose,kind));
   for(const pose of [poses[0],poses[2]])report.lightCases.push(await checkLight(renderer,fixture,pose));
   document.querySelector('#status').textContent=`Measured ${fixture.id}; ${report.cases.length} camera cases, ${report.lightCases.length} light cases`;await new Promise(resolve=>setTimeout(resolve,0));}
  const fifth={id:'fifth_group',groups:Array.from({length:5},(_,i)=>group(`group_${i}`,A,[box([0,0,.8-i*.4],[1.6,1.2,.12])]))};report.capacity=[];
  for(const light of [false,true]){const transport=new Transport(renderer,fifth,poses[0],makeCamera('orthographic',WIDTH,HEIGHT),WIDTH,HEIGHT,light);let rejected=false,message;
   try{transport.render();}catch(error){message=String(error);rejected=/capacity exceeded/.test(message);}finally{transport.dispose();}report.capacity.push({path:light?'light':'camera',rejected,message});}
  report.tieControls=tieControls(renderer);
  report.maximumColorError=Math.max(...report.cases.map(c=>c.color.maximum),...report.lightCases.map(c=>c.peels.maximum));
  report.maximumDepthError=Math.max(...report.cases.map(c=>c.depth.maximum),...report.lightCases.map(c=>c.depth.maximum));
  report.receiverMaximumError=Math.max(...report.lightCases.map(c=>c.receiver.maximum));
  report.timing=await timing(renderer);
  const ties=report.tieControls;
  const controlsPass=ties[0].drawOrderMaximumDifference===0&&ties[1].drawOrderMaximumDifference>1e-4&&ties[1].analyticAmbiguities.includes('same_group_conflicting_attributes')&&ties[2].analyticAmbiguities.includes('coincident_distinct_groups');
  const coveragePass=report.cases.length===fixtures.length*poses.length*2&&report.lightCases.length===fixtures.length*2&&report.cases.every(c=>c.opticalSamples>=8)
   &&report.cases.some(c=>c.fixture==='four_groups'&&c.maximumGroups===4);
  report.coverageComplete=coveragePass;report.negativeControlsDemonstrated=controlsPass;
  report.status=report.maximumColorError<=COLOR_TOLERANCE&&report.maximumDepthError<=DEPTH_TOLERANCE&&report.receiverMaximumError<=COLOR_TOLERANCE&&report.capacity.every(c=>c.rejected)&&controlsPass&&coveragePass?'passed':'failed';
  document.querySelector('#status').textContent=JSON.stringify({status:report.status,cameraCases:report.cases.length,lightCases:report.lightCases.length,
   maximumColorError:report.maximumColorError,maximumDepthError:report.maximumDepthError,receiverMaximumError:report.receiverMaximumError,capacity:report.capacity},null,2);return report;
 }finally{renderer.dispose();}
}
