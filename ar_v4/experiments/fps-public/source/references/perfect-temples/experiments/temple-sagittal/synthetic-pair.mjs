import {Matrix4,PerspectiveCamera,Vector3} from 'three';
import {VIRTUAL_CAMERA} from '../../src/render/projection.ts';
import {correctedBridgePose} from '../../src/render/bridge-pose.ts';

/** Generated geometry only. Never an altered wearer image or detector result. */
export function syntheticPair(canonical,downDegrees,yawDegrees=0){
 const w=1280,h=720,aspect=w/h,camera=new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees,aspect,1,10000);
 const matrix=new Matrix4().makeTranslation(0,0,-30).multiply(new Matrix4().makeRotationY(yawDegrees*Math.PI/180)).multiply(new Matrix4().makeRotationX(downDegrees*Math.PI/180));
 const points=Array.from({length:468},(_,i)=>new Vector3(...canonical.positions.slice(i*3,i*3+3)).applyMatrix4(matrix));
 const meanZ=points.reduce((s,p)=>s+p.z,0)/points.length,scale=-1/meanZ,frustumWidth=2*Math.tan(VIRTUAL_CAMERA.verticalFovDegrees*Math.PI/360)*aspect;
 const landmarks=points.map(p=>{const n=p.clone().project(camera);return{x:(n.x+1)/2,y:(1-n.y)/2,z:(-scale*p.z-1)/frustumWidth};});
 for(let i=0;i<10;i++)landmarks.push({...landmarks[i<5?33:263]});
 const detection={landmarks,matrix:matrix.toArray(),inferenceMs:0};
 const corrected=correctedBridgePose(detection.matrix,landmarks,canonical.positions,aspect).matrix;
 const source=document.createElement('canvas');source.width=w;source.height=h;const c=source.getContext('2d');
 c.fillStyle='#17212a';c.fillRect(0,0,w,h);c.strokeStyle='#2b3a46';c.lineWidth=1;
 for(let x=0;x<w;x+=40){c.beginPath();c.moveTo(x,0);c.lineTo(x,h);c.stroke();}for(let y=0;y<h;y+=40){c.beginPath();c.moveTo(0,y);c.lineTo(w,y);c.stroke();}
 c.strokeStyle='#79969e';c.fillStyle='#415967';c.lineWidth=.6;
 const tris=[];for(let i=0;i<canonical.indices.length;i+=3){const ids=canonical.indices.slice(i,i+3);tris.push({ids,z:ids.reduce((s,id)=>s+points[id].z,0)/3});}
 tris.sort((a,b)=>a.z-b.z);for(const {ids}of tris){c.beginPath();ids.forEach((id,i)=>{const p=landmarks[id];i?c.lineTo(p.x*w,p.y*h):c.moveTo(p.x*w,p.y*h);});c.closePath();c.fill();c.stroke();}
 c.fillStyle='#f1f6f7';c.font='bold 23px sans-serif';c.fillText('SYNTHETIC CANONICAL GEOMETRY — NOT A WEARER RECORDING',30,38);
 c.font='18px sans-serif';c.fillText(`Down ${downDegrees}° · yaw ${yawDegrees}° · assumed FOV 63° · distance 30 cm`,30,68);
 const roi=ids=>{const ps=ids.map(i=>landmarks[i]);return[Math.floor(Math.min(...ps.map(p=>p.x*w))-5),Math.floor(Math.min(...ps.map(p=>p.y*h))-5),Math.ceil(Math.max(...ps.map(p=>p.x*w))+5),Math.ceil(Math.max(...ps.map(p=>p.y*h))+5)];};
 return{original:{width:w,height:h,detection,sourceUrl:source.toDataURL()},meta:{key:`synthetic-down${downDegrees}-yaw${yawDegrees}`,pitchDegrees:-downDegrees,yawDegrees,
  recordedRawMatrix:detection.matrix,recordedCorrectedMatrix:corrected,recordedEyewearMatrix:corrected,
  regions:{noseProtectionRois:[{kind:'synthetic-nasal-core',roi:roi([1,2,4,6,98,133,168,197,327,362])}],wholeHeadReviewRoi:[360,140,920,620]}}};
}
