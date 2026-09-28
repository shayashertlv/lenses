/** Diagnostic only: fixed GLSL expression graphs and independent float32 CPU
 * graphs. Never chooses a per-pixel oracle from the smallest GPU residual. */
import {probeViewportSubpixels} from './viewport-subpixel-probe.mjs';
const f=Math.fround;
function matVec(matrix,vector,fused=false){const out=[];for(let row=0;row<4;row++){let sum=0;for(let c=0;c<4;c++)sum=f(sum+(fused?matrix[c*4+row]*vector[c]:f(matrix[c*4+row]*vector[c])));out.push(sum);}return out;}
function matMat(a,b){const out=[];for(let col=0;col<4;col++)out.push(...matVec(a,b.slice(col*4,col*4+4)));return out;}
const even=value=>{const lo=Math.floor(value),r=value-lo;return r<.5?lo:r>.5?lo+1:lo%2===0?lo:lo+1;};
const screen=(clip,w,h)=>[f(f(f(clip[0]/clip[3])+1)*w/2),f(f(f(clip[1]/clip[3])+1)*h/2)];
const snap=(clip,w,h)=>screen(clip,w,h).map(value=>even(value*256)/256);
const delta=(a,b)=>Math.max(...a.map((v,i)=>Math.abs(v-b[i])));
function vertices(box){const result=[];for(const [u,v,a,us,vs,as]of [[2,1,0,-1,-1,1],[2,1,0,1,-1,-1],[0,2,1,1,1,1],[0,2,1,1,-1,-1],[0,1,2,1,-1,1],[0,1,2,-1,-1,-1]])
 for(let y=0;y<2;y++)for(let x=0;x<2;x++){const local=[0,0,0];local[u]=(x-.5)*box.size[u]*us;local[v]=(y-.5)*box.size[v]*vs;local[a]=box.size[a]/2*as;result.push(local.map((p,i)=>f(f(p)+box.center[i])));}return result;}
function capture(gl,positions,P,M,expression){const compile=(type,code)=>{const s=gl.createShader(type);gl.shaderSource(s,code);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
 const vertex=compile(gl.VERTEX_SHADER,`#version 300 es\nprecision highp float;in vec3 position;uniform mat4 P,M;out vec4 probeClip;void main(){${expression}gl_Position=probeClip;}`);
 const fragment=compile(gl.FRAGMENT_SHADER,'#version 300 es\nprecision highp float;out vec4 color;void main(){color=vec4(1.0);}');
 const program=gl.createProgram();gl.attachShader(program,vertex);gl.attachShader(program,fragment);gl.transformFeedbackVaryings(program,['probeClip'],gl.INTERLEAVED_ATTRIBS);gl.linkProgram(program);
 if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
 const vao=gl.createVertexArray(),input=gl.createBuffer(),output=gl.createBuffer(),tf=gl.createTransformFeedback();gl.bindVertexArray(vao);gl.bindBuffer(gl.ARRAY_BUFFER,input);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(positions.flat()),gl.STATIC_DRAW);
 const attribute=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(attribute);gl.vertexAttribPointer(attribute,3,gl.FLOAT,false,0,0);gl.useProgram(program);gl.uniformMatrix4fv(gl.getUniformLocation(program,'P'),false,new Float32Array(P));gl.uniformMatrix4fv(gl.getUniformLocation(program,'M'),false,new Float32Array(M));
 gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK,tf);gl.bindBuffer(gl.TRANSFORM_FEEDBACK_BUFFER,output);gl.bufferData(gl.TRANSFORM_FEEDBACK_BUFFER,positions.length*16,gl.STREAM_READ);gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER,0,output);
 gl.enable(gl.RASTERIZER_DISCARD);gl.beginTransformFeedback(gl.POINTS);gl.drawArrays(gl.POINTS,0,positions.length);gl.endTransformFeedback();gl.disable(gl.RASTERIZER_DISCARD);
 const raw=new Float32Array(positions.length*4);gl.getBufferSubData(gl.TRANSFORM_FEEDBACK_BUFFER,0,raw);
 const extension=gl.getExtension('WEBGL_debug_shaders'),translated=extension?.getTranslatedShaderSource(vertex)??null;
 gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK,null);gl.bindVertexArray(null);gl.deleteTransformFeedback(tf);gl.deleteBuffer(input);gl.deleteBuffer(output);gl.deleteVertexArray(vao);gl.deleteProgram(program);gl.deleteShader(vertex);gl.deleteShader(fragment);
 return {clip:Array.from({length:positions.length},(_,i)=>Array.from(raw.slice(i*4,i*4+4))),translated};}
export function probeShadowArithmetic(renderer,fixture,root,shadow){const canvas=document.createElement('canvas'),gl=canvas.getContext('webgl2');if(!gl)throw Error('WebGL2 transform feedback unavailable');
 const positions=fixture.groups.flatMap(g=>g.boxes.flatMap(vertices)),camera=shadow.lightCamera;
 const M=camera.matrixWorldInverse.clone().multiply(root.matrixWorld).elements.map(f),P=camera.projectionMatrix.elements.map(f),PM=matMat(P,M);
 const expressions={leftAssociated:'probeClip=P*M*vec4(position,1.0);',sequential:'vec4 view=M*vec4(position,1.0);probeClip=P*view;'};
 const gpu=Object.fromEntries(Object.entries(expressions).map(([key,expression])=>[key,capture(gl,positions,P,M,expression)])),rows=[];
 for(let i=0;i<positions.length;i++){const p=[...positions[i],1],chain=matVec(P,matVec(M,p)),matrix=matVec(PM,p),fused=matVec(P,matVec(M,p,true),true),left=gpu.leftAssociated.clip[i],sequential=gpu.sequential.clip[i];
  rows.push({i,position:positions[i],cpuChain:chain,cpuMatrixProduct:matrix,cpuFusedChain:fused,gpuLeft:left,gpuSequential:sequential,
   snaps:{cpuChain:snap(chain,512,512),cpuMatrixProduct:snap(matrix,512,512),cpuFusedChain:snap(fused,512,512),gpuLeft:snap(left,512,512),gpuSequential:snap(sequential,512,512)},
   leftVsCpuChain:delta(left,chain),leftVsCpuMatrixProduct:delta(left,matrix),sequentialVsCpuChain:delta(sequential,chain),leftVsSequential:delta(left,sequential)});}
 const actualGL=renderer.getContext(),extension=actualGL.getExtension('WEBGL_debug_shaders');let productionVertex=null,productionTranslated=null;
 for(const program of renderer.info.programs??[]){const source=actualGL.getShaderSource(program.vertexShader);if(source?.includes('vLensLightNormal')){productionVertex=source;
  // Three marks attached shaders for deletion after linking. ANGLE's debug
  // extension rejects those objects even while getShaderSource still works.
  productionTranslated=actualGL.getShaderParameter(program.vertexShader,actualGL.DELETE_STATUS)?null:extension?.getTranslatedShaderSource(program.vertexShader)??null;break;}}
 const differingSnaps=rows.filter(r=>JSON.stringify(r.snaps.cpuChain)!==JSON.stringify(r.snaps.gpuLeft)||JSON.stringify(r.snaps.cpuMatrixProduct)!==JSON.stringify(r.snaps.gpuLeft)||JSON.stringify(r.snaps.gpuSequential)!==JSON.stringify(r.snaps.gpuLeft));
 const result={viewportRaster:probeViewportSubpixels(),fixture:fixture.id,positionCount:positions.length,expressions,uploadedProjection:P,uploadedModelView:M,rows,differingSnaps,
  maximum:Object.fromEntries(['leftVsCpuChain','leftVsCpuMatrixProduct','sequentialVsCpuChain','leftVsSequential'].map(k=>[k,Math.max(...rows.map(r=>r[k]))])),
  productionVertex,productionTranslated,probeTranslated:{leftAssociated:gpu.leftAssociated.translated,sequential:gpu.sequential.translated},
  scope:'Fixed independent recipe vertices, actual declared uniform matrices. A separate WebGL2 transform-feedback program executes each predeclared expression. This diagnoses expression graphs; altered program shape can change compiler scheduling. No per-pixel minimum-residual reference is selected.'};
 gl.getExtension('WEBGL_lose_context')?.loseContext();return result;}
