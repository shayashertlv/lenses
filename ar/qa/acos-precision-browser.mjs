/** One bounded GPU arithmetic experiment, independent of lens renderers. */
const assert=(value,message)=>{if(!value)throw Error(message);};
const digest=async buffer=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),x=>x.toString(16).padStart(2,'0')).join('');
export async function run(){const config=await(await fetch('/probe-config.json')).json(),input=await(await fetch('/probe-input.bin')).arrayBuffer();
 assert(await digest(input)===config.inputSha256,'Input digest mismatch');
 const canvas=document.createElement('canvas');canvas.width=config.width;canvas.height=config.height;
 const gl=canvas.getContext('webgl2',{antialias:false,depth:false,stencil:false});assert(gl,'WebGL2 unavailable');assert(gl.getExtension('EXT_color_buffer_float'),'Float color targets unavailable');
 const debug=gl.getExtension('WEBGL_debug_renderer_info'),precision=gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER,gl.HIGH_FLOAT),resources=[];
 const result={status:'running',backend:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),
  fragmentHighp:{rangeMin:precision.rangeMin,rangeMax:precision.rangeMax,precision:precision.precision},inputSha256:config.inputSha256};
 const vertex=`#version 300 es\nprecision highp float;void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.0-1.0,0.0,1.0);}`;
 const horner=[`highp float p=${config.coefficients.at(-1).shaderLiteral};`,...config.coefficients.slice(0,-1).reverse().map(c=>`p=p*y+${c.shaderLiteral};`)].join('\n');
 const fragment=`#version 300 es
 precision highp float;precision highp int;uniform highp sampler2D source;layout(location=0) out highp vec4 result;
 highp float seriesAcos(highp float c){highp float y=(1.0-c)*0.5;${horner}return 2.0*sqrt(y)*p;}
 void main(){highp float c=texelFetch(source,ivec2(gl_FragCoord.xy),0).r;result=vec4(c,acos(c),seriesAcos(c),(1.0-c)*0.5);}`;
 result.vertexShader=vertex;result.fragmentShader=fragment;
 function shader(type,source){const shader=gl.createShader(type);resources.push(['shader',shader]);gl.shaderSource(shader,source);gl.compileShader(shader);assert(gl.getShaderParameter(shader,gl.COMPILE_STATUS),gl.getShaderInfoLog(shader));return shader;}
 try{const program=gl.createProgram();resources.push(['program',program]);gl.attachShader(program,shader(gl.VERTEX_SHADER,vertex));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);assert(gl.getProgramParameter(program,gl.LINK_STATUS),gl.getProgramInfoLog(program));gl.useProgram(program);
  const source=gl.createTexture();resources.push(['texture',source]);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,source);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.R32F,config.width,config.height,0,gl.RED,gl.FLOAT,new Float32Array(input));gl.uniform1i(gl.getUniformLocation(program,'source'),0);
  const output=gl.createTexture();resources.push(['texture',output]);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,output);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA32F,config.width,config.height,0,gl.RGBA,gl.FLOAT,null);
  const framebuffer=gl.createFramebuffer();resources.push(['framebuffer',framebuffer]);gl.bindFramebuffer(gl.FRAMEBUFFER,framebuffer);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,output,0);assert(gl.checkFramebufferStatus(gl.FRAMEBUFFER)===gl.FRAMEBUFFER_COMPLETE,'Incomplete framebuffer');
  gl.viewport(0,0,config.width,config.height);gl.disable(gl.DITHER);gl.disable(gl.BLEND);gl.disable(gl.DEPTH_TEST);gl.drawArrays(gl.TRIANGLES,0,3);gl.finish();assert(gl.getError()===gl.NO_ERROR,'WebGL error');
  const values=new Float32Array(config.width*config.height*4);gl.readPixels(0,0,config.width,config.height,gl.RGBA,gl.FLOAT,values);assert(gl.getError()===gl.NO_ERROR,'Float readback failed');
  result.gpuResultSha256=await digest(values.buffer);const saved=await fetch('/probe-results',{method:'POST',body:values.buffer});assert(saved.ok,'Result upload failed');
  result.status='measured';document.querySelector('#status').textContent=JSON.stringify({status:result.status,backend:result.backend,inputCount:config.sampleCount,fragmentHighp:result.fragmentHighp},null,2);return result;
 }finally{for(const [kind,resource]of resources.reverse()){if(kind==='shader')gl.deleteShader(resource);else if(kind==='program')gl.deleteProgram(resource);else if(kind==='texture')gl.deleteTexture(resource);else gl.deleteFramebuffer(resource);}gl.getExtension('WEBGL_lose_context')?.loseContext();}
}
