import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Euler, Matrix4, PerspectiveCamera, Quaternion, Vector3} from 'three';
import {correctedBridgePose} from '../src/render/bridge-pose.ts';
import {DEFAULT_STEADY, PoseStabilizer} from '../src/render/pose-stabilizer.ts';
import type {SteadyOptions} from '../src/render/pose-stabilizer.ts';
import type {Landmark} from '../src/face/protocol.ts';

// Historical reference stays in this regression only; the application has one accepted default.
const previousSteady: SteadyOptions = {rotationMinCutoffHz: 1, rotationBeta: .1, depthMinCutoffHz: 1,
  depthBeta: .2, derivativeCutoffHz: 1, resetGapMs: 500};

// Capture-relative screen error, not raw-angle smoothness: a smooth but trailing frame can still float off the face.
const canonical: number[] = JSON.parse(readFileSync(new URL('../public/models/canonical-face.json', import.meta.url), 'utf8')).positions;
const width=1280, height=720, aspect=width/height;
const camera=new PerspectiveCamera(63,aspect,1,10000); camera.updateMatrixWorld();
const points=[[0,3.271027,6.531958919387042],[-7.25,4.5,6.53],[7.25,4.5,6.53],[-6,0,6.53],[6,0,6.53],[-7.3,3.6,2.5],[7.3,3.6,2.5]];
interface Pose {depth:number; yaw?:number; pitch?:number; roll?:number; x?:number; y?:number}
const matrix=(p:Pose):number[]=>new Matrix4().compose(new Vector3(p.x??0,p.y??0,-p.depth),new Quaternion().setFromEuler(new Euler((p.pitch??0)*Math.PI/180,(p.yaw??0)*Math.PI/180,(p.roll??0)*Math.PI/180,'YXZ')),new Vector3(1,1,1)).toArray();
const project=(p:readonly number[],m:readonly number[]):[number,number]=>{
  const v=new Vector3().fromArray(p).applyMatrix4(new Matrix4().fromArray(m)).project(camera);
  return [(v.x+1)*width/2,(1-v.y)*height/2];
};
const landmarks=(m:readonly number[]):Landmark[]=>Array.from({length:478},(_,i)=>{
  const [x,y]=project(i<468?canonical.slice(i*3,i*3+3):[0,0,0],m);return{x:x/width,y:y/height,z:0};
});
const rms=(a:number[])=>Math.sqrt(a.reduce((s,x)=>s+x*x,0)/a.length);
const random=(seed:number)=>()=>{seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};
const gauss=(next:()=>number)=>Math.sqrt(-2*Math.log(1-next()))*Math.cos(2*Math.PI*next());

function measure(options:SteadyOptions|null,fps:number,path:(t:number)=>Pose,noisy=false){
  const filter=options?new PoseStabilizer(options):null,next=random(17);
  const series:{t:number; max:number; errors:[number,number][]}[]=[];
  for(let i=0;i<=8*fps;i++){
    const t=i/fps,truth=path(t),truthMatrix=matrix(truth);
    const raw=matrix(noisy?{...truth,yaw:(truth.yaw??0)+.4*gauss(next),pitch:(truth.pitch??0)+.4*gauss(next),roll:(truth.roll??0)+.4*gauss(next),depth:truth.depth+.3*gauss(next)}:truth);
    const attached=correctedBridgePose(filter?filter.apply(raw,t*1000).matrix:raw,landmarks(truthMatrix),canonical,aspect).matrix;
    const errors=points.map(p=>{const a=project(p,attached),b=project(p,truthMatrix);return[a[0]-b[0],a[1]-b[1]] as [number,number];});
    series.push({t,max:Math.max(...errors.map(e=>Math.hypot(...e))),errors});
  }
  const selected=series.filter(f=>f.t>=2&&f.t<=6),shake:number[]=[];
  for(let i=2;i<series.length;i++)if(series[i]!.t>=2)for(let k=0;k<points.length;k++){
    const a=series[i]!.errors[k]!,b=series[i-1]!.errors[k]!,c=series[i-2]!.errors[k]!;
    shake.push(Math.hypot(a[0]-2*b[0]+c[0],a[1]-2*b[1]+c[1]));
  }
  return{series,error:rms(selected.flatMap(f=>f.errors.map(e=>Math.hypot(...e)))),max:Math.max(...selected.map(f=>f.max)),shake:rms(shake)};
}

test('default steadiness more than halves projected frame lag on turns, nods, and distance changes at both camera rates',()=>{
  for(const fps of[15,30])for(const depth of[20,45,70])for(const movement of['turn','nod','distance']){
    const path=(t:number):Pose=>{const swing=t>=2&&t<=6?Math.sin(Math.PI*(t-2)):0;return{depth:depth-(movement==='distance'?.15*depth*swing:0),yaw:movement==='turn'?20*swing:0,pitch:movement==='nod'?20*swing:0};};
    const baseline=measure(previousSteady,fps,path),responsive=measure(DEFAULT_STEADY,fps,path);
    assert.ok(responsive.error<baseline.error*.5,`${movement}, ${depth}cm, ${fps}fps: ${responsive.error}px vs ${baseline.error}px baseline`);
  }
});

test('default steadiness still suppresses rest noise, including the magnification at 20 cm',()=>{
  for(const fps of[15,30])for(const depth of[20,45,70]){
    const path=():Pose=>({depth,yaw:5,pitch:-8,roll:2});
    const raw=measure(null,fps,path,true),baseline=measure(previousSteady,fps,path,true),responsive=measure(DEFAULT_STEADY,fps,path,true);
    assert.ok(responsive.shake<raw.shake*.65,`${depth}cm, ${fps}fps: responsive shake ${responsive.shake}px must stay below raw ${raw.shake}px`);
    assert.ok(responsive.error<raw.error*.8,`${depth}cm, ${fps}fps: rest error ${responsive.error}px vs raw ${raw.error}px`);
    assert.ok(responsive.error<baseline.error*2,`${depth}cm, ${fps}fps: rest error increase must stay below 2x; ${responsive.error}px vs ${baseline.error}px`);
  }
});

test('default steadiness settles after a stop without overshooting and leaves lateral bridge tracking immediate',()=>{
  const ramp=(t:number)=>t<2?0:t<3?t-2:t<4?1:t<5?5-t:0;
  for(const fps of[15,30])for(const depth of[20,45,70]){
    const path=(t:number):Pose=>({depth:depth-.2*depth*ramp(t),yaw:25*ramp(t)});
    const result=measure(DEFAULT_STEADY,fps,path);
    for(const stop of[3,5])assert.ok(result.series.filter(f=>f.t>=stop+.2&&f.t<stop+.9).every(f=>f.max<3),`${depth}cm, ${fps}fps: settling below 3px must take at most 200ms`);
    // The first-order rotation/depth filter cannot move beyond either endpoint of this monotone section.
    const filter=new PoseStabilizer(DEFAULT_STEADY);
    for(let i=0;i<=fps*3;i++){
      const raw=matrix(path(i/fps)),out=filter.apply(raw,1000*i/fps).matrix;
      assert.ok(out[14]!>=-depth-1e-9&&out[14]!<=-.8*depth+1e-9);
    }
    const lateral=measure(DEFAULT_STEADY,fps,t=>({depth,x:4*ramp(t),y:-2*ramp(t)}));
    assert.ok(lateral.max<1e-8,`${depth}cm, ${fps}fps: lateral motion must not acquire a smoothing tail`);
  }
});
