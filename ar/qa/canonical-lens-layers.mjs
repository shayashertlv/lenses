/** Analytic layered-light oracle for actual TryOnRenderer fixtures.
 * Expected geometry/UV/normals are rebuilt from manifest recipes, never sampled
 * from loaded attributes. All eligibility rules depend on geometry before color.
 */
import * as THREE from 'three';
import {evaluateLensAppearance} from '../src/eyewear/lens-appearance.ts';

const tolerance=.008;
const poses=[{yaw:0,roll:0},{yaw:-40,roll:0},{yaw:40,roll:0},{yaw:60,roll:0},{yaw:25,roll:30}];
const expectedIds=['two_layers','reversed_two_layers','three_layers','four_layers','crossing_layers','opaque_between_layers','opaque_before_layers','opaque_behind_layers',
 'total_mirror_front','total_mirror_behind','distinct_left_right','curved_layer_normals','nested_layers_with_opaque'];

function makeSurface(spec,mesh){
 const positions=[],normals=[],uvs=[],indices=[],[nx,ny]=spec.segments,[tx,ty]=spec.tilt??[0,0],[kx,ky]=spec.curvature??[0,0];
 for(let iy=0;iy<=ny;iy++)for(let ix=0;ix<=nx;ix++){
  const u=ix/nx,v=iy/ny,dx=spec.width*(u-.5),dy=spec.height*(v-.5);
  positions.push(new THREE.Vector3(...[spec.center[0]+dx,spec.center[1]+dy,spec.z+tx*dx+ty*dy+kx*dx*dx+ky*dy*dy].map(Math.fround)));
  const authoredNormal=new THREE.Vector3(-tx-2*kx*dx,-ty-2*ky*dy,1).normalize();
  const magnitude=spec.unequal_normal_magnitudes?[.25,2,.5,3][(positions.length)%4]:1;
  normals.push(new THREE.Vector3(...authoredNormal.multiplyScalar(magnitude).toArray().map(Math.fround)));uvs.push(new THREE.Vector2(Math.fround(u),Math.fround(v)));
 }
 for(let iy=0;iy<ny;iy++)for(let ix=0;ix<nx;ix++){const a=iy*(nx+1)+ix;indices.push(a,a+1,a+nx+1,a+1,a+nx+2,a+nx+1);}
 const boundary=[];for(let ix=0;ix<=nx;ix++)boundary.push(ix);
 for(let iy=1;iy<=ny;iy++)boundary.push(iy*(nx+1)+nx);
 for(let ix=nx-1;ix>=0;ix--)boundary.push(ny*(nx+1)+ix);
 for(let iy=ny-1;iy>0;iy--)boundary.push(iy*(nx+1));
 // Separate validation catches corruption before expected-pixel construction.
 const p=mesh.geometry.getAttribute('position'),n=mesh.geometry.getAttribute('normal'),uv=mesh.geometry.getAttribute('uv'),index=mesh.geometry.index;
 if(p.count!==positions.length||n.count!==positions.length||uv.count!==positions.length||index?.count!==indices.length)throw Error(`Layer geometry shape changed: ${spec.id}`);
 let maximum=0;
 for(let i=0;i<positions.length;i++){
  maximum=Math.max(maximum,new THREE.Vector3().fromBufferAttribute(p,i).distanceTo(positions[i]),
   new THREE.Vector3().fromBufferAttribute(n,i).distanceTo(normals[i]),
   Math.abs(uv.getX(i)-uvs[i].x),Math.abs(uv.getY(i)-uvs[i].y));
 }
 for(let i=0;i<indices.length;i++)if(index.getX(i)!==indices[i])throw Error(`Layer triangle indices changed: ${spec.id}`);
 if(maximum>2e-6)throw Error(`Layer authored attributes changed: ${spec.id}: ${maximum}`);
 return {spec,mesh,positions,normals,uvs,indices,boundary,surfaceMaximumError:maximum};
}

function projection(surface,camera,width,height){
 return surface.positions.map(p=>{const q=p.clone().applyMatrix4(surface.mesh.matrixWorld).project(camera);return new THREE.Vector2((q.x+1)*width/2,(1-q.y)*height/2);});
}
function segmentDistance(point,a,b){const d=b.clone().sub(a),t=THREE.MathUtils.clamp(point.clone().sub(a).dot(d)/d.lengthSq(),0,1);return point.distanceTo(a.clone().addScaledVector(d,t));}
function nearBoundary(surface,projected,x,y){const point=new THREE.Vector2(x+.5,y+.5),ids=surface.boundary;return ids.some((id,i)=>segmentDistance(point,projected[id],projected[ids[(i+1)%ids.length]])<2);}

function intersections(surface,worldRay){
 const inverse=surface.mesh.matrixWorld.clone().invert(),ray=worldRay.clone().applyMatrix4(inverse),normalMatrix=new THREE.Matrix3().getNormalMatrix(surface.mesh.matrixWorld),hits=[];
 for(let offset=0;offset<surface.indices.length;offset+=3){
  const [ia,ib,ic]=surface.indices.slice(offset,offset+3),a=surface.positions[ia],b=surface.positions[ib],c=surface.positions[ic];
  const local=ray.intersectTriangle(a,b,c,true,new THREE.Vector3());if(!local)continue;
  const world=local.clone().applyMatrix4(surface.mesh.matrixWorld),distance=world.distanceTo(worldRay.origin);
  if(hits.some(hit=>Math.abs(hit.distance-distance)<1e-7))continue; // Shared triangle boundary, same interface.
  const weights=THREE.Triangle.getBarycoord(local,a,b,c,new THREE.Vector3()),w=weights.toArray(),ids=[ia,ib,ic];
  const uv=new THREE.Vector2(),normal=new THREE.Vector3();
  for(let i=0;i<3;i++){
   uv.addScaledVector(surface.uvs[ids[i]],w[i]);
   // Visible normal_vertex normalizes each transformed vertex before interpolation.
   normal.addScaledVector(surface.normals[ids[i]].clone().applyNormalMatrix(normalMatrix).normalize(),w[i]);
  }
  normal.normalize();const cosine=normal.dot(worldRay.direction.clone().negate());if(cosine<=.02)continue;
  hits.push({id:surface.spec.id,distance,world,u:uv.x,v:uv.y,angle:Math.acos(THREE.MathUtils.clamp(cosine,0,1))*180/Math.PI,
   optical:!!surface.spec.appearance,spec:surface.spec});
 }
 return hits;
}

export function rayOracle(surfaces,camera,x,y,width,height,background,environment){
 const raycaster=new THREE.Raycaster();raycaster.setFromCamera(new THREE.Vector2(2*(x+.5)/width-1,1-2*(y+.5)/height),camera);
 const hits=surfaces.flatMap(surface=>intersections(surface,raycaster.ray)).sort((a,b)=>a.distance-b.distance);
 if(!hits.some(hit=>hit.optical))return null;
 if(hits.some(hit=>hit.u<.06||hit.u>.94||hit.v<.06||hit.v>.94))return null;
 // Resolve crossing surfaces only away from their intersection/float-depth tie.
 const depthMargin=.00025*Math.max(...surfaces.map(surface=>surface.mesh.matrixWorld.getMaxScaleOnAxis()));
 if(hits.some((hit,i)=>i>0&&Math.abs(hit.distance-hits[i-1].distance)<depthMargin))return null;
 const stop=hits.find(hit=>!hit.optical),visible=hits.filter(hit=>hit.optical&&(!stop||hit.distance<stop.distance));
 let color=stop?[...stop.spec.linear_rgb]:[...background];
 for(const hit of [...visible].reverse()){
  const response=evaluateLensAppearance(hit.spec.appearance,hit.v,hit.angle);
  color=response.transmission_rgb.map((transmission,c)=>transmission*color[c]+response.reflectance_rgb[c]*environment[c]);
 }
 return {expected:color,order:visible.map(hit=>hit.id),allOpticalOrder:hits.filter(hit=>hit.optical).map(hit=>hit.id),
  opaqueStop:stop?.id??null,hits:visible.map(({id,u,v,angle,distance,world})=>({id,u,v,angle,distance,depth:world.clone().project(camera).z*.5+.5}))};
}

function rays(surfaces,camera,width,height,background,environment,step=3){
 const projected=surfaces.map(surface=>projection(surface,camera,width,height));
 const points=projected.flat(),minX=Math.max(3,Math.ceil(Math.min(...points.map(p=>p.x))/step)*step),maxX=Math.min(width-3,Math.max(...points.map(p=>p.x)));
 const minY=Math.max(3,Math.ceil(Math.min(...points.map(p=>p.y))/step)*step),maxY=Math.min(height-3,Math.max(...points.map(p=>p.y)));
 const result=[];
 for(let y=minY;y<=maxY;y+=step)for(let x=minX;x<=maxX;x+=step){
  if(surfaces.some((surface,i)=>nearBoundary(surface,projected[i],x,y)))continue;
  const oracle=rayOracle(surfaces,camera,x,y,width,height,background,environment);if(oracle)result.push({x,y,...oracle});
 }
 return result;
}

function samples(instance,pixels,surfaces,background,environment,linear){
 instance.scene.updateMatrixWorld(true);
 return rays(surfaces,instance.camera,pixels.width,pixels.height,background,environment).map(oracle=>{
  const {x,y}=oracle;
  const offset=(y*pixels.width+x)*4,actual=[0,1,2].map(c=>linear(pixels.data[offset+c]));
  return {...oracle,actual,maximumError:Math.max(...actual.map((value,c)=>Math.abs(value-oracle.expected[c])))};
 });
}

// Independent CPU raster reference: authoring recipes -> exact exported float32
// attributes -> float32 uniform matrices -> raster-grid projected vertices. No
// values from the measured GPU attachments enter this calculation.
const nearestEven=value=>{const lower=Math.floor(value),fraction=value-lower;return fraction<.5?lower:fraction>.5?lower+1:lower%2===0?lower:lower+1;};
function shadowRasterGeometry(surface,camera,width,height,subpixelBits){
 const mv=new THREE.Matrix4().multiplyMatrices(camera.matrixWorldInverse,surface.mesh.matrixWorld),nm=new THREE.Matrix3().getNormalMatrix(mv);
 mv.elements=mv.elements.map(Math.fround);nm.elements=nm.elements.map(Math.fround);
 const projection=camera.projectionMatrix.clone();projection.elements=projection.elements.map(Math.fround);
 const scale=2**subpixelBits;
 const vertices=surface.positions.map((position,i)=>{
  const clip=new THREE.Vector4(position.x,position.y,position.z,1).applyMatrix4(mv).applyMatrix4(projection);
  const point=new THREE.Vector3((clip.x/clip.w+1)*width/2,(1-clip.y/clip.w)*height/2,clip.z/clip.w*.5+.5);
  const snapped=point.clone();snapped.x=nearestEven(point.x*scale)/scale;snapped.y=nearestEven(point.y*scale)/scale;
  return {point,snapped,inverseW:1/clip.w,normal:surface.normals[i].clone().applyNormalMatrix(nm),uv:surface.uvs[i]};
 });
 return {surface,vertices};
}
function shadowRasterHit(raster,x,y,snap){
 const {surface,vertices}=raster,point=new THREE.Vector3(x+.5,y+.5,0);
 for(let offset=0;offset<surface.indices.length;offset+=3){
  const triangle=surface.indices.slice(offset,offset+3).map(i=>vertices[i]),p=triangle.map(v=>(snap?v.snapped:v.point).clone().setZ(0));
  const weights=THREE.Triangle.getBarycoord(point,...p,new THREE.Vector3());if(!weights||Math.min(...weights.toArray())<0)continue;
  const w=weights.toArray(),sum=w.reduce((sum,value,i)=>sum+value*triangle[i].inverseW,0),uv=new THREE.Vector2(),normal=new THREE.Vector3();
  for(let i=0;i<3;i++){const weight=w[i]*triangle[i].inverseW/sum;uv.addScaledVector(triangle[i].uv,weight);normal.addScaledVector(triangle[i].normal,weight);}
  const cosine=normal.normalize().z,angle=Math.acos(THREE.MathUtils.clamp(cosine,0,1))*180/Math.PI;
  return {u:uv.x,v:uv.y,angle,depth:w.reduce((sum,value,i)=>sum+value*triangle[i].point.z,0)};
 }
 return null;
}

function shadowPeelSamples(instance,surfaces){
 const shadows=instance.shadows,targets=shadows.canonicalTargets;
 if(targets.length!==5)throw Error('Expected four canonical shadow peels and an overflow target');
 const camera=shadows.lightCamera,width=targets[0].width,height=targets[0].height;
 const buffers=targets.slice(0,4).map(target=>{const raw=new Float32Array(width*height*4);instance.renderer.readRenderTargetPixels(target,0,0,width,height,raw);return raw;});
 const appearance=new Map(surfaces.filter(surface=>surface.spec.appearance).map(surface=>[surface.spec.id,surface.spec.appearance]));
 const gl=instance.renderer.getContext(),subpixelBits=gl.getParameter(gl.SUBPIXEL_BITS),rasters=new Map(surfaces.map(surface=>[surface.spec.id,shadowRasterGeometry(surface,camera,width,height,subpixelBits)]));
 const debugRenderer=gl.getExtension('WEBGL_debug_renderer_info'),backend=debugRenderer?gl.getParameter(debugRenderer.UNMASKED_RENDERER_WEBGL):null;
 // This harness launches ANGLE D3D11. The backend's official raster contract
 // mandates exactly 8 fractional XY bits, nearest-even rounding and varying/Z
 // interpolation on snapped vertices (§3.4.1,15.16), even when WebGL reports a
 // conservative SUBPIXEL_BITS minimum of4. Other backends are not validated.
 // https://microsoft.github.io/DirectX-Specs/d3d/archive/D3D11_3_FunctionalSpec.htm#CoordinateSnapping
 if(typeof backend!=='string'||!/Direct3D11|D3D11/i.test(backend))throw Error(`Shadow raster oracle requires identified D3D11 backend, got ${backend}`);
 const rasters8=new Map(surfaces.map(surface=>[surface.spec.id,shadowRasterGeometry(surface,camera,width,height,8)]));
 const rows=rays(surfaces,camera,width,height,[1,1,1],[0,0,0],4).map(sample=>{
  const offset=((height-1-sample.y)*width+sample.x)*4,peels=[];
  for(let i=0;i<4;i++){
   const hit=sample.hits[i],expected=hit?evaluateLensAppearance(appearance.get(hit.id),hit.v,hit.angle).transmission_rgb:[1,1,1];
   const actual=[0,1,2].map(c=>buffers[i][offset+c]),actualDepth=buffers[i][offset+3],expectedDepth=hit?.depth??1;
   const raster=hit?shadowRasterHit(rasters.get(hit.id),sample.x,sample.y,true):null;
   const floatRaster=hit?shadowRasterHit(rasters.get(hit.id),sample.x,sample.y,false):null;
   const raster8=hit?shadowRasterHit(rasters8.get(hit.id),sample.x,sample.y,true):null;
   const rasterExpected=raster?evaluateLensAppearance(appearance.get(hit.id),raster.v,raster.angle).transmission_rgb:[1,1,1];
   const floatExpected=floatRaster?evaluateLensAppearance(appearance.get(hit.id),floatRaster.v,floatRaster.angle).transmission_rgb:[1,1,1];
   const expected8=raster8?evaluateLensAppearance(appearance.get(hit.id),raster8.v,raster8.angle).transmission_rgb:[1,1,1];
   peels.push({index:i,layer:hit?.id??null,expected,actual,expectedDepth,actualDepth,hit,raster,floatRaster,rasterExpected,floatExpected,
    rasterCoefficientError:Math.max(...actual.map((value,c)=>Math.abs(value-rasterExpected[c]))),rasterDepthError:Math.abs(actualDepth-(raster?.depth??1)),
    floatCoefficientError:Math.max(...actual.map((value,c)=>Math.abs(value-floatExpected[c]))),floatDepthError:Math.abs(actualDepth-(floatRaster?.depth??1)),
    raster8,expected8,raster8CoefficientError:Math.max(...actual.map((value,c)=>Math.abs(value-expected8[c]))),raster8DepthError:Math.abs(actualDepth-(raster8?.depth??1)),
    coefficientError:Math.max(...actual.map((value,c)=>Math.abs(value-expected[c]))),depthError:Math.abs(actualDepth-expectedDepth)});
  }
  return {x:sample.x,y:sample.y,order:sample.order,opaqueStop:sample.opaqueStop,peels};
 });
 const maximumCoefficientError=Math.max(...rows.flatMap(row=>row.peels.map(peel=>peel.coefficientError))),maximumDepthError=Math.max(...rows.flatMap(row=>row.peels.map(peel=>peel.depthError)));
 const maximumRasterCoefficientError=Math.max(...rows.flatMap(row=>row.peels.map(peel=>peel.raster8CoefficientError))),maximumRasterDepthError=Math.max(...rows.flatMap(row=>row.peels.map(peel=>peel.raster8DepthError)));
 const diagnostics=shadows.layerDiagnostics;
 return {samples:rows,maximumCoefficientError,maximumDepthError,maximumRasterCoefficientError,maximumRasterDepthError,diagnostics,subpixelBits,
  rasterReference:{backend,fractionalBits:8,rounding:'nearest_even',attributes:'independent authoring recipe cast to GLB float32',uniforms:'float32 matrices; CPU double multiplication',
   specification:'https://microsoft.github.io/DirectX-Specs/d3d/archive/D3D11_3_FunctionalSpec.htm#CoordinateSnapping',coefficientTolerance:.00005,normalizedDepthTolerance:.000002},
  passed:rows.length>=12&&maximumRasterCoefficientError<=.00005&&maximumRasterDepthError<=.000002&&diagnostics.enabled&&diagnostics.overflow===false&&diagnostics.overflowCheck!=='not_run',
  scope:'All four actual FloatRGBA shadow peels, independent D3D11 raster-grid light-angle/UV/depth oracle; opaque stop and empty layers checked. Ideal continuous-ray and conservative WebGL4-bit predictions are retained as diagnostics.'};
}

const smooth=(lo,hi,value)=>{const t=THREE.MathUtils.clamp((value-lo)/(hi-lo),0,1);return t*t*(3-2*t);};
function receiverTexel(surfaces,light,u,v,receiverDepth,depthSpan,contactEnd){
 if(u<0||u>1||v<0||v>1)return {transmission:[1,1,1],activeLayers:0,opaqueBeforeReceiver:false};
 const ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2(2*u-1,2*v-1),light);
 const hits=surfaces.flatMap(surface=>intersections(surface,ray.ray)).sort((a,b)=>a.distance-b.distance),stop=hits.find(hit=>!hit.optical);
 const depth=hit=>hit.world.clone().project(light).z*.5+.5;
 const weight=hit=>{const separation=(receiverDepth-depth(hit))*depthSpan;return smooth(.025,contactEnd,separation)*(1-smooth(4,10,separation));};
 let transmission=[1,1,1],activeLayers=0;
 for(const hit of hits.filter(hit=>hit.optical&&depth(hit)<receiverDepth&&(!stop||hit.distance<stop.distance))){
  const strength=weight(hit),response=evaluateLensAppearance(hit.spec.appearance,hit.v,hit.angle).transmission_rgb;
  transmission=transmission.map((value,c)=>value*(1-strength+strength*response[c]));if(strength>0)activeLayers++;
 }
 const opaqueBeforeReceiver=!!stop&&depth(stop)<receiverDepth;
 if(stop)transmission=transmission.map(value=>value*(1-weight(stop)));
 return {transmission,activeLayers,opaqueBeforeReceiver};
}
function filteredReceiver(surfaces,light,lp,mapSize,depthSpan,contactEnd){
 const u=lp.x*.5+.5,v=lp.y*.5+.5,receiverDepth=lp.z*.5+.5,taps=[],expected=[0,0,0];
 // Five bilinear comparisons at the fixed minimum one-texel footprint. Each
 // tap is independently ray traced against the authoring recipe at a texel
 // center, while receiver depth stays equal to this screen sample's depth.
 for(const [dx,dy]of [[0,0],[1,0],[-1,0],[0,1],[0,-1]]){
  const gx=u*mapSize+dx-.5,gy=v*mapSize+dy-.5,bx=Math.floor(gx),by=Math.floor(gy),fx=gx-bx,fy=gy-by;
  for(const [ox,oy,w]of [[0,0,(1-fx)*(1-fy)],[1,0,fx*(1-fy)],[0,1,(1-fx)*fy],[1,1,fx*fy]]){
   const tu=(bx+ox+.5)/mapSize,tv=(by+oy+.5)/mapSize,result=receiverTexel(surfaces,light,tu,tv,receiverDepth,depthSpan,contactEnd);
   for(let c=0;c<3;c++)expected[c]+=w/5*result.transmission[c];
   taps.push({u:tu,v:tv,weight:w/5,...result});
  }
 }
 return {expected,taps,...receiverTexel(surfaces,light,u,v,receiverDepth,depthSpan,contactEnd)};
}
function shadowReceiverSamples(instance,surfaces,api,render){
 const shadow=instance.shadows,light=shadow.lightCamera,receiver=shadow.receiverScene.children.find(object=>object.isMesh),original=receiver.geometry;
 const lead=surfaces.find(surface=>surface.spec.appearance),guide=new THREE.Vector3(...lead.spec.center,lead.spec.z).applyMatrix4(lead.mesh.matrixWorld),projected=guide.clone().project(light);
 const lightRay=new THREE.Raycaster();lightRay.setFromCamera(new THREE.Vector2(projected.x,projected.y),light);
 const centerHits=surfaces.flatMap(surface=>intersections(surface,lightRay.ray)).sort((a,b)=>a.distance-b.distance),optical=centerHits.filter(hit=>hit.optical);
 if(optical.length!==2)throw Error('Receiver depth fixture requires two central optical intersections');
 const locations=[['before',optical[0].distance-.7],['between',.15*optical[0].distance+.85*optical[1].distance],['behind',optical[1].distance+.7]],rows=[];
 const uniform=shadow.receiverMaterial.uniforms,mapSpan=uniform.mapSpanCm.value,contactEnd=Math.max(.14,2*mapSpan/512);
 api.setBackground([255,255,255]);
 try{
  for(const [location,distance]of locations){
   const center=lightRay.ray.at(distance,new THREE.Vector3()),geometry=new THREE.PlaneGeometry(2,2);
   const normal=new THREE.Vector3(0,0,1).applyNormalMatrix(new THREE.Matrix3().getNormalMatrix(lead.mesh.matrixWorld)).normalize();
   geometry.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,0,1),normal));geometry.translate(center.x,center.y,center.z);
   geometry.setAttribute('shadowPosition',geometry.getAttribute('position').clone());receiver.geometry=geometry;
   try{
    render();const target=shadow.compositeTarget,raw=new Uint8Array(target.width*target.height*4);instance.renderer.readRenderTargetPixels(target,0,0,target.width,target.height,raw);
    const p=center.clone().project(instance.camera),px=Math.floor((p.x+1)*target.width/2),py=Math.floor((1-p.y)*target.height/2);
    const plane=new THREE.Plane().setFromNormalAndCoplanarPoint(normal,center),samples=[];
    for(const dy of [-1,0,1])for(const dx of [-1,0,1]){
     const x=px+dx,y=py+dy,viewRay=new THREE.Raycaster();viewRay.setFromCamera(new THREE.Vector2(2*(x+.5)/target.width-1,1-2*(y+.5)/target.height),instance.camera);
     const world=viewRay.ray.intersectPlane(plane,new THREE.Vector3());if(!world)throw Error('Missing controlled receiver-plane intersection');
     const lp=world.clone().project(light),oracle=filteredReceiver(surfaces,light,lp,shadow.canonicalTargets[0].width,uniform.depthSpanCm.value,contactEnd),{expected}=oracle;
     const offset=((target.height-1-y)*target.width+x)*4,actual=[0,1,2].map(c=>api.linear(raw[offset+c]));
     samples.push({x,y,activeLayers:oracle.activeLayers,opaqueBeforeReceiver:oracle.opaqueBeforeReceiver,expected,actual,taps:oracle.taps,unfilteredExpected:oracle.transmission,maximumError:Math.max(...actual.map((value,c)=>Math.abs(value-expected[c])))});
    }
    rows.push({location,samples,maximumError:Math.max(...samples.map(sample=>sample.maximumError))});
   }finally{receiver.geometry=original;geometry.dispose();}
  }
 }finally{receiver.geometry=original;api.setBackground();}
 const maximumError=Math.max(...rows.map(row=>row.maximumError));
 return {probes:rows,maximumError,passed:maximumError<=tolerance,
  scope:'Actual shadow receiver shader with temporary planes before/between/behind optical sheets. Source white, strengths one, softness zero; expected 20 ray-traced shadow texel comparisons implement five bilinear filters with fixed receiver depth, including opaque edges. No GPU map values enter the oracle.'};
}

function isolate(instance,hideHead=true){const hidden=[],materials=[];instance.eyewearAsset.traverse(object=>{
 if(object.isMesh&&!object.userData.runtimeConformanceId){hidden.push([object,object.visible]);object.visible=false;}
});if(hideHead)instance.scene.traverse(object=>{if(object.isMesh)for(const material of Array.isArray(object.material)?object.material:[object.material])if(!material.colorWrite&&!materials.some(pair=>pair[0]===material)){materials.push([material,material.visible]);material.visible=false;}});
return()=>{for(const [object,visible]of hidden)object.visible=visible;for(const [material,visible]of materials)material.visible=visible;};}

export async function debugLayerTransport(api){
 const {manifest,canonical,create,flatEnvironment,setEnvironment,source,detection}=api,entry=manifest.layer_cases.find(entry=>entry.id==='two_layers');
 const instance=await create(entry,canonical),black=flatEnvironment(instance.renderer,[0,0,0]),white=flatEnvironment(instance.renderer,[1,1,1]);
 const restore=isolate(instance,false),rows=[];let time=6000;
 try{
  for(const hiddenHead of [false,true])for(const [environment,target]of [['black',black],['white',white]]){
   instance.pose(source,detection(canonical),time+=100);setEnvironment(instance,target);
   const materials=[];if(hiddenHead)instance.scene.traverse(object=>{if(object.isMesh)for(const material of Array.isArray(object.material)?object.material:[object.material])if(!material.colorWrite&&!materials.some(pair=>pair[0]===material)){materials.push([material,material.visible]);material.visible=false;}});
   const renderer=instance.renderer,original=renderer.render,peels=[];
   renderer.render=function(scene,camera){const result=original.call(this,scene,camera);if(scene===instance.canonicalLayers.scene){const buffer=this.getRenderTarget(),raw=new Float32Array(4);this.readRenderTargetPixels(buffer,240,320-1-150,1,1,raw);peels.push([...raw]);}return result;};
   try{instance.render(null,{hair:false,shadows:false});}finally{renderer.render=original;for(const [material,visible]of materials)material.visible=visible;}
   const opaque=new Float32Array(4);renderer.readRenderTargetPixels(instance.canonicalLayers.opaque,240,320-1-150,1,1,opaque);
   const depthTarget=new THREE.WebGLRenderTarget(1,1,{type:THREE.FloatType,depthBuffer:false}),scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,0,1),geometry=new THREE.PlaneGeometry(2,2);
   const material=new THREE.ShaderMaterial({depthTest:false,depthWrite:false,toneMapped:false,uniforms:{depthMap:{value:instance.canonicalLayers.opaque.depthTexture}},vertexShader:'void main(){gl_Position=vec4(position.xy,0.,1.);}',fragmentShader:'uniform sampler2D depthMap;void main(){float d=texture2D(depthMap,vec2(240.5/480.,169.5/320.)).r;gl_FragColor=vec4(d,d,d,1.);}'});
   scene.add(new THREE.Mesh(geometry,material));const previous=renderer.getRenderTarget(),viewport=renderer.getViewport(new THREE.Vector4());renderer.setRenderTarget(depthTarget);renderer.render(scene,camera);const depth=new Float32Array(4);renderer.readRenderTargetPixels(depthTarget,0,0,1,1,depth);renderer.setRenderTarget(previous);renderer.setViewport(viewport);
   depthTarget.dispose();material.dispose();geometry.dispose();rows.push({hiddenHead,environment,opaque:[...opaque],opaqueDepth:depth[0],peels});
  }
  return {status:'diagnostic',scope:'Per-pass capture atx240,y150 with actual vs explicitly hidden head-depth material; no conformance verdict',rows};
 }finally{restore();black.dispose();white.dispose();instance.dispose();}
}

export async function debugShadowRaster(api){
 const {manifest,canonical,create,source,detection}=api,entry=manifest.layer_cases.find(entry=>entry.id==='curved_layer_normals'),instance=await create(entry,canonical);
 const restore=isolate(instance);
 try{
  const authored=new Map();instance.eyewearAsset.traverse(object=>{if(object.isMesh&&object.userData.runtimeConformanceId)authored.set(object.userData.runtimeConformanceId,object);});
  const surfaces=[...entry.sheets,...entry.opaque].map(spec=>makeSurface(spec,authored.get(spec.id)));
  instance.pose(source,detection(canonical),6100);instance.setShadows({enabled:true,frameStrength:1,lensStrength:1,softness:0});instance.render(null,{hair:false,shadows:true});
  return {status:'diagnostic',scope:'Independent float32 uniform/4-bit and8-bit raster hypotheses, not conformance verdict',shadow:shadowPeelSamples(instance,surfaces)};
 }finally{restore();instance.dispose();}
}

export async function runLayerConformance(api){
 const {manifest,canonical,create,flatEnvironment,setEnvironment,source,detection,backgroundLinear,linear,png}=api;
 const report={schema_version:1,status:'running',maximumError:0,cases:[],negativeControls:[],
  tolerance,scope:'Actual renderer camera-space front-sheet layering under controlled uniform environments; no ray displacement or interreflection',
  exclusions:['Two-pixel projected boundary margin, intrinsic UV margin and 0.25mm asset-space crossing/depth-tie margin are decided before residuals.',
   'Original shipped opaque parts and actual face/head depth-only materials are hidden in advance for exact numeric probes; authored unlit opaque strip remains visible. Real head-depth stopping has a separate saved per-pass diagnostic.']};
 api.parentReport.layers=report;
 if(JSON.stringify((manifest.layer_cases??[]).map(entry=>entry.id).sort())!==JSON.stringify([...expectedIds].sort()))throw Error('Layer fixture set is incomplete');
 for(const entry of manifest.layer_cases){
  let instance,black,white;const row={id:entry.id,probes:[],passed:false};report.cases.push(row);
  try{
   instance=await create(entry,canonical);row.renderedAssetIntegrity=instance.renderedAssetIntegrity;
   const authored=new Map();instance.eyewearAsset.traverse(object=>{if(object.isMesh&&object.userData.runtimeConformanceId)authored.set(object.userData.runtimeConformanceId,object);});
   const specs=[...entry.sheets,...entry.opaque];if(authored.size!==specs.length)throw Error(`Missing or repeated authored layer identity: ${entry.id}`);
   const surfaces=specs.map(spec=>makeSurface(spec,authored.get(spec.id)));row.surfaceMaximumError=Math.max(...surfaces.map(surface=>surface.surfaceMaximumError));
   const restore=isolate(instance);black=flatEnvironment(instance.renderer,[0,0,0]);white=flatEnvironment(instance.renderer,[1,1,1]);let timestamp=6000;
   for(const [name,environment,target] of [['black',[0,0,0],black],['white',[1,1,1],white]]){
    setEnvironment(instance,target);
    for(const pose of poses){
     instance.pose(source,detection(canonical,pose.yaw,pose.roll),timestamp+=100);const renderStart=performance.now();instance.render(null,{hair:false,shadows:false});
     const renderEnd=performance.now(),pixels=instance.readback(),readbackEnd=performance.now(),rows=samples(instance,pixels,surfaces,backgroundLinear,environment,linear);
     const overlap=rows.filter(sample=>sample.allOpticalOrder.length>=entry.minimum_overlap).length;
     const probe={environment:name,...pose,samples:rows,overlapSamples:overlap,maximumError:Math.max(...rows.map(sample=>sample.maximumError)),
      timingMs:{renderSubmissionAndOverflowSync:renderEnd-renderStart,diagnosticReadback:readbackEnd-renderEnd}};row.probes.push(probe);
     if(rows.length<12||(pose.yaw===0&&overlap<6))throw Error(`Insufficient analytic layer coverage: ${entry.id}/${name}/${pose.yaw}: ${rows.length}/${overlap}`);
     if(name==='white'&&pose.yaw===0){const card=document.createElement('div');card.className='case';card.innerHTML=`<strong>Layers · ${entry.label}</strong><img src="${png(pixels)}">`;document.getElementById('cases').append(card);}
    }
   }
   const all=row.probes.flatMap(probe=>probe.samples),orders=new Set(all.filter(sample=>sample.allOpticalOrder.length>=2).map(sample=>sample.allOpticalOrder.join('>')));
   row.coverage={samples:all.length,orders:[...orders],opaqueStoppedSamples:all.filter(sample=>sample.opaqueStop).length,
    cameraBackgroundSamples:all.filter(sample=>!sample.opaqueStop).length,
    measuredSheetIds:[...new Set(all.flatMap(sample=>sample.hits.map(hit=>hit.id)))]};
   if(entry.requires_multiple_orders){
    const perPixel=new Map();for(const probe of row.probes.filter(p=>p.environment==='white'))for(const sample of probe.samples){
     if(sample.allOpticalOrder.length<2)continue;const key=`${sample.x},${sample.y}`,seen=perPixel.get(key)??new Set();seen.add(sample.allOpticalOrder.join('>'));perPixel.set(key,seen);
    }
    row.coverage.poseOrderSwapPixels=[...perPixel.values()].filter(seen=>seen.size>1).length;
    if(orders.size<2||row.coverage.poseOrderSwapPixels<2)throw Error('Crossing fixture did not exercise order changes across pixels and poses');
   }
   if(entry.opaque.length&&(row.coverage.opaqueStoppedSamples<8||row.coverage.cameraBackgroundSamples<8))throw Error('Opaque-strip fixture lacks both depth-stop and background coverage');
   if(!entry.sheets.every(sheet=>row.coverage.measuredSheetIds.includes(sheet.id)))throw Error('A declared layer has no measured optical incidence');
   instance.pose(source,detection(canonical),timestamp+=100);instance.setShadows({enabled:true,frameStrength:1,lensStrength:1,softness:0});instance.render(null,{hair:false,shadows:true});
   row.shadows=shadowPeelSamples(instance,surfaces);
   if(entry.id==='two_layers'||entry.id.startsWith('opaque_')){
    row.receiver=shadowReceiverSamples(instance,surfaces,api,()=>{instance.pose(source,detection(canonical),timestamp+=100);instance.render(null,{hair:false,shadows:true});});
    if(entry.id==='two_layers'){
     row.receiver.depthCoverage=row.receiver.probes.every((probe,index)=>probe.samples.every(sample=>sample.activeLayers===index));
     row.receiver.passed&&=row.receiver.depthCoverage;
    }
   }
   row.maximumError=Math.max(...row.probes.map(probe=>probe.maximumError));row.passed=row.maximumError<=tolerance&&row.shadows.passed&&(!row.receiver||row.receiver.passed);
   restore();
  }finally{black?.dispose();white?.dispose();instance?.dispose();}
 }
 for(const entry of manifest.layer_negative_controls??[]){
  let instance,error=null;try{instance=await create(entry,canonical);instance.render(null,{hair:false,shadows:false});}catch(e){error=String(e.message??e);}finally{instance?.dispose();}
  const expectedRejection=!!error&&(entry.expected_error==='overflow'?/overflow|capacity|(?:four|4).*layer|layer.*(?:four|4)/i.test(error):/coincident|coplanar|duplicate|overlap/i.test(error));
  const control={id:entry.id,rejected:!!error,error,expectedRejection};
  if(entry.expected_error==='overflow'){
   let shadowInstance,shadowError=null;try{shadowInstance=await create(entry,canonical);shadowInstance.setShadows({enabled:true,frameStrength:1,lensStrength:1,softness:0});shadowInstance.render(null,{hair:false,shadows:true});}catch(e){shadowError=String(e.message??e);}finally{shadowInstance?.dispose();}
   control.shadowError=shadowError;control.shadowOverflowRejected=!!shadowError&&/Canonical shadow:.*capacity exceeded/i.test(shadowError);control.expectedRejection&&=control.shadowOverflowRejected;
  }
  report.negativeControls.push(control);
 }
 report.maximumError=Math.max(...report.cases.map(row=>row.maximumError));
 const timing=report.cases.flatMap(row=>row.probes.map(probe=>probe.timingMs.renderSubmissionAndOverflowSync)).sort((a,b)=>a-b);
 report.timing={scope:'Desktop synthetic fixtures at480x320, includes CPU submission and synchronous overflow flag read; shader compilation/warmup may occur. Excludes diagnostic image readback and oracle computation.',
  samples:timing.length,medianMs:timing[Math.floor(timing.length/2)],p95Ms:timing[Math.floor(timing.length*.95)],maximumMs:Math.max(...timing)};
 report.complete=report.cases.length===expectedIds.length&&report.cases.every(row=>row.probes.length===poses.length*2)&&report.negativeControls.length===3;
 report.status=report.complete&&report.cases.every(row=>row.passed)&&report.negativeControls.every(row=>row.rejected&&row.expectedRejection)?'passed':'failed';
 return report;
}
