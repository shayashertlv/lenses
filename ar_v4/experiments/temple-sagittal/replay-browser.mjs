import * as T from 'three';
import {TryOnRenderer as Perfecto} from '../../src/render/renderer.ts';
import {TryOnRenderer as Candidate} from './renderer.ts';
import {rearDropForPose} from './rear-drop.ts';
const check=(v,m)=>{if(!v)throw Error(m);};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const inside=(r,x,y)=>x>=r.x0&&x<r.x1&&y>=r.y0&&y<r.y1;
const bytes=c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data;
const copy=c=>{const d=document.createElement('canvas');d.width=c.width;d.height=c.height;d.getContext('2d').drawImage(c,0,0);return d;};
const diff=(a,b,w,h,rois=null)=>{let changedPixels=0,maxDelta=0;const bounds=[w,h,-1,-1];for(let y=0;y<h;y++)for(let x=0;x<w;x++){if(rois&&!rois.some(r=>x>=r[0]&&y>=r[1]&&x<r[2]&&y<r[3]))continue;let d=0;for(let k=0;k<4;k++)d=Math.max(d,Math.abs(a[(y*w+x)*4+k]-b[(y*w+x)*4+k]));if(d){changedPixels++;maxDelta=Math.max(maxDelta,d);bounds[0]=Math.min(bounds[0],x);bounds[1]=Math.min(bounds[1],y);bounds[2]=Math.max(bounds[2],x);bounds[3]=Math.max(bounds[3],y);}}return{changedPixels,maxDelta,bounds:changedPixels?bounds:null};};

export async function createReplay(model){
 const source=document.createElement('canvas'),perfectoCanvas=document.createElement('canvas'),display=document.createElement('canvas');
 const reference=await Perfecto.create(perfectoCanvas,new AbortController().signal,model),candidate=await Candidate.create(display,new AbortController().signal,model);
 let frozen=null,det=null,meta=null,before=null,after=null,baselineBytes=null,candidateBytes=null,refSnapshot=null;
 const snapshot=s=>({...s,surfacePositions:Array.from(s.surfacePositions)});
 return{
  async frame(original,caseMeta){
   meta=caseMeta;det=structuredClone(original.detection);const input=JSON.stringify(det);
   source.width=original.width;source.height=original.height;const image=new Image();image.src=original.sourceUrl;await image.decode();source.getContext('2d').drawImage(image,0,0);
   check(reference.present(source,det),'Original perfecto presentation failed');
   // Copy native canvas synchronously before its default framebuffer can clear.
   before=copy(perfectoCanvas);baselineBytes=bytes(before);refSnapshot=reference.captureSnapshot;frozen=Array.from(refSnapshot.surfacePositions);
   for(const k of ['rawMatrix','correctedMatrix','eyewearMatrix'])check(same(refSnapshot[k],meta['recorded'+k[0].toUpperCase()+k.slice(1)]),'Original exact pose differs '+k);
   const drop={method:'temple-rear-drop-v1',dropM:rearDropForPose(det.matrix)};
   check(candidate.present(source,det),'Candidate presentation failed');
   candidate.selectVariant('perfecto');check(diff(baselineBytes,bytes(display),display.width,display.height).changedPixels===0,'Compositor baseline differs from independent untouched perfecto');
   candidate.selectVariant('candidate');after=copy(display);candidateBytes=bytes(after);const current=candidate.captureSnapshot,protection=current.protection;
   check(same(Array.from(current.surfacePositions),frozen),'Candidate changed frozen original nose surface');
   check(same(current.rawMatrix,refSnapshot.rawMatrix)&&same(current.correctedMatrix,refSnapshot.correctedMatrix)&&same(current.eyewearMatrix,refSnapshot.eyewearMatrix),'Candidate changed original pose');
   check(same(current.templeClip,refSnapshot.templeClip)&&same(current.templeVisibility,refSnapshot.templeVisibility),'Candidate changed original clipping or visibility');
   check(JSON.stringify(det)===input,'Candidate mutated source detection');
   const w=display.width,h=display.height;let protectedPixels=0,protectedChanged=0,outsideChanged=0;
   for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    const p=protection?.protectedRects.some(r=>inside(r,x,y)),e=protection?.editableRects.some(r=>inside(r,x,y));let changed=false;
    for(let k=0;k<4;k++)changed ||= baselineBytes[(y*w+x)*4+k]!==candidateBytes[(y*w+x)*4+k];
    protectedPixels+=Number(p);protectedChanged+=Number(p&&changed);outsideChanged+=Number(!e&&changed);
   }
   check(protectedChanged===0&&outsideChanged===0,'Protected/outside-corridor byte preservation failed');
   const whole=diff(baselineBytes,candidateBytes,w,h),nose=diff(baselineBytes,candidateBytes,w,h,meta.regions.noseProtectionRois.map(r=>r.roi));
   if(drop.dropM===0)check(whole.changedPixels===0,'Inactive control differs from perfecto');
   // Independent projection of every original front/lens/pad vertex. Includes
   // hidden vertices and an AA neighborhood, without depth or role visibility.
   let opticalVertices=0,uncoveredOpticalVertices=0;reference.scene.updateMatrixWorld(true);
   let lensRear=Infinity;reference.eyewearPose.traverse(m=>{if(m.isMesh&&!m.userData.templeVisibilityOverlay&&(m.material.transmission??0)>0){const a=m.geometry.getAttribute('position');for(let i=0;i<a.count;i++)lensRear=Math.min(lensRear,a.getZ(i));}});
   reference.eyewearPose.traverse(m=>{if(!m.isMesh||m.userData.templeVisibilityOverlay)return;const a=m.geometry.getAttribute('position'),lens=(m.material.transmission??0)>0;
    for(let i=0;i<a.count;i++){if(!lens&&Math.abs(a.getX(i))>.045&&a.getZ(i)<lensRear-.015)continue;
     const p=new T.Vector3().fromBufferAttribute(a,i).applyMatrix4(m.matrixWorld).project(reference.camera),x=(p.x+1)*w/2,y=(1-p.y)*h/2;
     if(x<0||x>=w||y<0||y>=h)continue;opticalVertices++;
     const r=protection?.protectedRects[0];if(drop.dropM>0&&(!r||x-3<r.x0||x+3>=r.x1||y-3<r.y0||y+3>=r.y1))uncoveredOpticalVertices++;
    }
   });check(uncoveredOpticalVertices===0,'Original optical vertices extend outside protected footprint');
   const mask=document.createElement('canvas');mask.width=w;mask.height=h;const mc=mask.getContext('2d');mc.fillStyle='#000';mc.fillRect(0,0,w,h);if(protection){mc.fillStyle='#00aa00';for(const r of protection.editableRects)mc.fillRect(r.x0,r.y0,r.x1-r.x0,r.y1-r.y0);mc.fillStyle='#0044ff';for(const r of protection.protectedRects)mc.fillRect(r.x0,r.y0,r.x1-r.x0,r.y1-r.y0);}
   return{before:before.toDataURL(),after:after.toDataURL(),source:source.toDataURL(),mask:mask.toDataURL(),width:w,height:h,drop,protection,whole,nose,protectedPixels,protectedChanged,outsideChanged,opticalVertices,uncoveredOpticalVertices,nativeSamples:candidate.nativeSamples,baselineSnapshot:snapshot(refSnapshot),candidateSnapshot:snapshot(current),inputUnchanged:true,independentPerfectoByteExact:true};
  },
  verifyReplay(){
   const expected=after.toDataURL(),s=candidate.captureSnapshot;
   check(candidate.present(source,det,frozen,s.templeClip,s.templeVisibility,s.rearDrop,s.protection),'Saved candidate replay');check(display.toDataURL()===expected,'Saved candidate replay changed pixels');
   check(candidate.present(source,det),'Live selector reentry');
   candidate.selectVariant('perfecto');check(display.toDataURL()===before.toDataURL(),'Perfecto selector differs');candidate.selectVariant('candidate');check(display.toDataURL()===expected,'Held candidate selector differs');
   const originalSource=copy(source);source.getContext('2d').fillStyle='#ff00ff';source.getContext('2d').fillRect(0,0,source.width,source.height);
   candidate.selectVariant('perfecto');check(display.toDataURL()===before.toDataURL(),'Selector used caller-mutated source');candidate.selectVariant('candidate');check(display.toDataURL()===expected,'Candidate selector used caller-mutated source');source.getContext('2d').drawImage(originalSource,0,0);
   if(s.rearDrop.dropM>0){const weakened=structuredClone(s.protection);weakened.protectedRects[0].x1--;
    check(candidate.present(source,det,frozen,s.templeClip,s.templeVisibility,s.rearDrop,weakened),'Invalid protection baseline fallback');check(display.toDataURL()===before.toDataURL()&&candidate.diagnostics.fallback!==null&&candidate.captureSnapshot.rearDrop.dropM===0,'Invalid protection failed to preserve perfecto');}
   check(candidate.present(source,det,frozen,s.templeClip,s.templeVisibility,null,null),'Legacy candidate-neutral replay');check(display.toDataURL()===before.toDataURL(),'Legacy no-drop replay differs');
   check(candidate.present(source,det),'Live reentry');check(display.toDataURL()===expected,'Live reentry not identical');
   check(!candidate.present(source,{landmarks:[],matrix:null,inferenceMs:0}),'No-face presentation must be false');check(candidate.captureSnapshot===null,'No-face stale metadata');
   check(candidate.present(source,det),'No-face reentry');check(display.toDataURL()===expected,'No-face reentry differs');
   const originalBranchPresent=candidate.branch.present;
   try{candidate.branch.present=()=>{throw Error('Injected isolated branch failure');};
    check(candidate.present(source,det),'Branch-failure baseline presentation');check(display.toDataURL()===before.toDataURL(),'Branch failure did not copy perfecto');
    const noFace={landmarks:[],matrix:null,inferenceMs:0};reference.present(source,noFace);const noFaceImage=copy(perfectoCanvas).toDataURL();check(!candidate.present(source,noFace),'No-face branch failure return');check(display.toDataURL()===noFaceImage&&candidate.captureSnapshot===null,'No-face branch failure lost current source');
   }finally{candidate.branch.present=originalBranchPresent;}
   check(candidate.present(source,det)&&display.toDataURL()===expected,'Injected failure reentry');
   return{savedCandidateByteExact:true,heldSelectorsByteExact:true,callerMutationIsolated:true,invalidProtectionFailsClosed:true,legacyNoDropByteExact:true,liveReentryByteExact:true,noFaceCleared:true,noFaceReentryByteExact:true,branchFailureCopiesPerfecto:true,noFaceBranchFailureCopiesCurrentSource:true};
  },
  diagnostic(){return candidate.exportDiagnostic();},
  dispose(){candidate.dispose();reference.dispose();}
 };
}
