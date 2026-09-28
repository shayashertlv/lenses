/** Independent fixed-function viewport qualification. Inputs are exact float32
 * clip attributes: no matrix arithmetic, renderer geometry or captured depth is
 * used to construct either predeclared reference. No per-pixel oracle choice. */
const f = Math.fround;
const even = value => {const low=Math.floor(value),r=value-low;return r<.5?low:r>.5?low+1:low%2===0?low:low+1;};
function adjacent(value, count) {
  const bytes=new ArrayBuffer(4),floats=new Float32Array(bytes),bits=new Uint32Array(bytes);
  floats[0]=value;
  bits[0]+=Math.sign(value)*count;
  return floats[0];
}
function reference(clip, sample, direct) {
  const points=clip.map(p=>p.slice(0,2).map(x=>{
    const window=direct?(x/p[3]+1)*256:f(f(f(x/p[3])+1)*256);
    return even(window*256)/256;
  }));
  const [a,b,c]=points,[x,y]=sample,den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);
  const p=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/den;
  const q=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/den;
  const bary=[p,q,1-p-q],depth=bary.reduce((sum,w,i)=>sum+w*f(f(clip[i][2]+clip[i][3])*.5)/clip[i][3],0);
  return {points,rgba:[...bary,depth]};
}
export function probeViewportSubpixels() {
  const canvas=document.createElement('canvas'),gl=canvas.getContext('webgl2');
  if(!gl||!gl.getExtension('EXT_color_buffer_float'))throw Error('Viewport probe needs float WebGL2 targets');
  const debug=gl.getExtension('WEBGL_debug_renderer_info');
  const backend=debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);
  const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);
    if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));return shader;};
  const vertex=compile(gl.VERTEX_SHADER,`#version 300 es
    precision highp float;in vec4 clipPosition;in vec3 corner;out vec3 bary;
    void main(){gl_Position=clipPosition;bary=corner;}`);
  const fragment=compile(gl.FRAGMENT_SHADER,`#version 300 es
    precision highp float;in vec3 bary;out vec4 outputColor;
    void main(){outputColor=vec4(bary,gl_FragCoord.z);}`);
  const program=gl.createProgram();gl.attachShader(program,vertex);gl.attachShader(program,fragment);gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
  const vao=gl.createVertexArray(),position=gl.createBuffer(),bary=gl.createBuffer(),target=gl.createTexture(),framebuffer=gl.createFramebuffer();
  gl.bindVertexArray(vao);gl.useProgram(program);
  const attrib=(name,buffer,size,values)=>{gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,values,gl.DYNAMIC_DRAW);
    const at=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(at);gl.vertexAttribPointer(at,size,gl.FLOAT,false,0,0);};
  attrib('clipPosition',position,4,new Float32Array(12));attrib('corner',bary,3,new Float32Array([1,0,0,0,1,0,0,0,1]));
  gl.bindTexture(gl.TEXTURE_2D,target);gl.texStorage2D(gl.TEXTURE_2D,1,gl.RGBA32F,512,512);
  gl.bindFramebuffer(gl.FRAMEBUFFER,framebuffer);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,target,0);
  if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw Error('Viewport probe target incomplete');
  gl.viewport(0,0,512,512);gl.disable(gl.DITHER);gl.disable(gl.BLEND);gl.disable(gl.DEPTH_TEST);
  const rows=[],sample=[255.5,280.5],raw=new Float32Array(4);
  // Fixed coverage chosen before measurement. Float attributes immediately
  // around n.8 half ties exercise both signs, axes and exact-power-of-two W.
  try {
    for(const w of [.5,1,2])for(const axis of [0,1])for(const cell of [160,224,320,352,416])for(const offset of [-2,-1,0,1,2]) {
      const points=[[300,65],[63,435],[445,415]],depth=[.2,.8,.4];
      const midpoint=cell+193.5/256,coordinate=adjacent(f((midpoint/256-1)*w),offset);
      const clip=points.map((p,i)=>[f((p[0]/256-1)*w),f((p[1]/256-1)*w),f((depth[i]*2-1)*w),w]);
      clip[0][axis]=coordinate;
      // A y-boundary near the center can move the fixed sample outside. Use
      // the recipe centroid (fixed from inputs, never GPU output) in all cases.
      const xy=clip.map(p=>[(p[0]/w+1)*256,(p[1]/w+1)*256]);
      const pixel=xy[0].map((_,c)=>Math.floor(xy.reduce((sum,p)=>sum+p[c],0)/3));
      sample[0]=pixel[0]+.5;sample[1]=pixel[1]+.5;
      gl.bindBuffer(gl.ARRAY_BUFFER,position);gl.bufferSubData(gl.ARRAY_BUFFER,0,new Float32Array(clip.flat()));
      gl.clearBufferfv(gl.COLOR,0,new Float32Array([-1,-1,-1,-1]));gl.drawArrays(gl.TRIANGLES,0,3);
      gl.readPixels(...pixel,1,1,gl.RGBA,gl.FLOAT,raw);
      const direct=reference(clip,sample,true),rounded=reference(clip,sample,false),actual=Array.from(raw);
      const error=expected=>Math.max(...actual.map((value,c)=>Math.abs(value-expected.rgba[c])));
      rows.push({w,axis,cell,offset,pixel,clip,actual,direct,rounded,directError:error(direct),roundedError:error(rounded)});
    }
    const maximumDirectError=Math.max(...rows.map(r=>r.directError)),maximumRoundedError=Math.max(...rows.map(r=>r.roundedError));
    const tolerance=3e-7,roundingDifferenceCases=rows.filter(r=>JSON.stringify(r.direct.points)!==JSON.stringify(r.rounded.points)).length;
    const status=maximumDirectError<=tolerance&&maximumRoundedError>2e-6&&roundingDifferenceCases>=10&&gl.getError()===gl.NO_ERROR?'passed':'failed';
    return {method:'fixed_clip_subpixel_tie_raster_probe_v1',status,backend,tolerance,caseCount:rows.length,
      maximumDirectError,maximumRoundedError,roundingDifferenceCases,rows,
      interpretation:'Qualifies direct viewport-to-n.8 snapping on this recorded backend. Explicit intermediate float32 rounding is a rejected reference, not a runtime error.',
      scope:'Independent exact clip inputs, no production matrix transforms or per-pixel reference selection. Other devices require their own qualification.'};
  } finally {
    gl.deleteFramebuffer(framebuffer);gl.deleteTexture(target);gl.deleteBuffer(position);gl.deleteBuffer(bary);gl.deleteVertexArray(vao);
    gl.deleteProgram(program);gl.deleteShader(vertex);gl.deleteShader(fragment);gl.getExtension('WEBGL_lose_context')?.loseContext();
  }
}
