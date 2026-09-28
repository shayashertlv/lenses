/** Dense deterministic comparison of builtin acos and one fixed Taylor formula. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=(name,fallback)=>process.argv.find(x=>x.startsWith(`--${name}=`))?.slice(name.length+3)??fallback;
const output=path.resolve(root,option('output','qa/output/acos-precision-v1'));
await fs.mkdir(output,{recursive:true});assert.equal((await fs.readdir(output)).length,0,'Use a new/empty probe directory');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex'),fileSha=async file=>sha(await fs.readFile(file));
const gcd=(a,b)=>{while(b){[a,b]=[b,a%b];}return a;};
const terms=24,coefficients=[];let central=1n,tail;
for(let n=0;n<=terms;n++){if(n)central=central*BigInt(4*n-2)/BigInt(n);let numerator=central,denominator=4n**BigInt(n)*BigInt(2*n+1);const d=gcd(numerator,denominator);numerator/=d;denominator/=d;
 const value=Number(numerator)/Number(denominator),coefficient={n,numerator:String(numerator),denominator:String(denominator),double:value,float32:Math.fround(value),shaderLiteral:value.toPrecision(17)};
 if(n<terms)coefficients.push(coefficient);else tail={numerator,denominator};}
const boundNumerator=283n*tail.numerator,boundDenominator=100n*tail.denominator*2n**BigInt(terms),bound=Number(boundNumerator)/Number(boundDenominator);
const uniformSteps=2**20,extra=[];const bits=new Uint32Array(1),asFloat=new Float32Array(bits.buffer);
for(let i=0;i<=4096;i++){bits[0]=0x3f800000-i;extra.push(asFloat[0]);}
for(let i=0;i<=32;i++){bits[0]=i;extra.push(asFloat[0]);}
for(let k=1;k<=149;k++)extra.push(Math.fround(2**-k));
for(let i=0;i<=4096;i++)extra.push(Math.fround(Math.cos(Math.PI*.5*i/4096)));
const sampleCount=uniformSteps+1+extra.length,width=1024,height=Math.ceil(sampleCount/width),input=new Float32Array(width*height);input.fill(1);
for(let i=0;i<=uniformSteps;i++)input[i]=i/uniformSteps;input.set(extra,uniformSteps+1);
const inputBytes=Buffer.from(input.buffer),config={schemaVersion:1,terms,coefficients,width,height,sampleCount,inputSha256:sha(inputBytes),
 sweep:{uniformSteps,uniformEndpointsIncluded:true,nearOneBitNeighbors:4097,subnormalBitNeighbors:33,powersOfTwo:149,uniformAngleCosines:4097,paddedTexels:input.length-sampleCount}};
await fs.writeFile(path.join(output,'cosines.f32.bin'),inputBytes);await fs.writeFile(path.join(output,'config.json'),JSON.stringify(config,null,2)+'\n');
const files=['qa/acos-precision.mjs','qa/acos-precision-browser.mjs','qa/acos-precision.html','package-lock.json'];
const codePins=async()=>Object.fromEntries(await Promise.all(files.map(async file=>[file,await fileSha(path.join(root,file))])));const before=await codePins();
const errors=[],warnings=[];let uploaded=false;
const server=await createServer({configFile:false,root,plugins:[{name:'isolated-acos-data',configureServer(server){server.middlewares.use(async(req,res,next)=>{
 try{if(req.url==='/probe-config.json'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(config));return;}
  if(req.url==='/probe-input.bin'){res.setHeader('Content-Type','application/octet-stream');res.end(inputBytes);return;}
  if(req.url==='/probe-results'&&req.method==='POST'){assert(!uploaded,'Only one result upload is allowed');const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;assert(size<=input.byteLength*4,'Result capacity exceeded');chunks.push(chunk);}assert.equal(size,input.byteLength*4,'Result length mismatch');await fs.writeFile(path.join(output,'gpu-results.rgba32f.bin'),Buffer.concat(chunks));uploaded=true;res.end('saved');return;}
  next();}catch(error){errors.push(String(error));res.statusCode=500;res.end(String(error));}});}}],server:{host:'127.0.0.1',port:0,hmr:false}});
let browser,report;
try{await server.listen();browser=await chromium.launch({headless:true,channel:'chromium',args:['--enable-gpu','--use-angle=d3d11','--ignore-gpu-blocklist']});
 const page=await browser.newPage();page.setDefaultTimeout(180000);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());if(m.type()==='warning')warnings.push(m.text());});
 await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/acos-precision.html`);await page.waitForFunction(()=>!!window.acosProbe);report=await page.evaluate(()=>window.acosProbe.run());
 assert(uploaded,'Missing GPU result');const raw=await fs.readFile(path.join(output,'gpu-results.rgba32f.bin'));assert.equal(sha(raw),report.gpuResultSha256,'GPU result digest mismatch');const gpu=new Float32Array(raw.buffer,raw.byteOffset,raw.byteLength/4);
 const stats=()=>({count:0,sum:0,sumSquares:0,maximumRadians:0,worst:null});const builtin=stats(),series=stats(),float32Model=stats(),bins=Array.from({length:10},(_,i)=>({cosineRange:[i/10,(i+1)/10],builtin:stats(),series:stats()}));
 let echoDifferences=0,maximumEchoError=0,invalidOutputs=0,maxDoublePolynomialError=0;const endpoints=[];
 const update=(s,error,row)=>{s.count++;s.sum+=error;s.sumSquares+=error*error;if(error>s.maximumRadians){s.maximumRadians=error;s.worst=row;}};
 function cpuSeries(c,round=false){const f=round?Math.fround:x=>x,y=f(f(1-c)*.5);let p=round?coefficients.at(-1).float32:coefficients.at(-1).double;
  for(let i=coefficients.length-2;i>=0;i--)p=f(f(p*y)+(round?coefficients[i].float32:coefficients[i].double));return f(f(2*f(Math.sqrt(y)))*p);}
 for(let i=0;i<sampleCount;i++){const c=input[i],reference=Math.acos(c),actualBuiltin=gpu[i*4+1],actualSeries=gpu[i*4+2],model=cpuSeries(c,true);
  const row={index:i,cosine:c,referenceRadians:reference,builtinRadians:actualBuiltin,seriesRadians:actualSeries,float32ModelRadians:model,gpuEcho:gpu[i*4],gpuY:gpu[i*4+3]};
  if(!Number.isFinite(actualBuiltin)||!Number.isFinite(actualSeries)){invalidOutputs++;continue;}
  if(gpu[i*4]!==c)echoDifferences++;maximumEchoError=Math.max(maximumEchoError,Math.abs(gpu[i*4]-c));maxDoublePolynomialError=Math.max(maxDoublePolynomialError,Math.abs(cpuSeries(c)-reference));
  const a=Math.abs(actualBuiltin-reference),b=Math.abs(actualSeries-reference);update(builtin,a,row);update(series,b,row);update(float32Model,Math.abs(model-reference),row);
  const bin=bins[Math.min(9,Math.floor(c*10))];update(bin.builtin,a,row);update(bin.series,b,row);
  if(i===0||i===uniformSteps)endpoints.push(row);}
 const finish=s=>({count:s.count,maximumRadians:s.maximumRadians,maximumDegrees:s.maximumRadians*180/Math.PI,meanRadians:s.sum/s.count,rmsRadians:Math.sqrt(s.sumSquares/s.count),worst:s.worst});
 const u=2**-24,roundingOperations=2*(terms-1)+3,gamma=roundingOperations*u/(1-roundingOperations*u),idealBound=(Math.PI/2)*gamma+u+bound;
 Object.assign(report,{createdAt:new Date().toISOString(),browserVersion:browser.version(),sampleCount,sweep:config.sweep,coefficients,
  input:{path:'cosines.f32.bin',sha256:config.inputSha256},gpuResult:{path:'gpu-results.rgba32f.bin',sha256:report.gpuResultSha256,channels:['echo_cosine','builtin_acos_radians','series_acos_radians','series_y']},
  builtin:finish(builtin),series:finish(series),float32Model:finish(float32Model),bins:bins.map(b=>({...b,builtin:finish(b.builtin),series:finish(b.series)})),endpoints,
  invalidOutputs,echoDifferences,maximumEchoError,maxDoublePolynomialError,
  exactArithmeticTruncation:{terms,formula:'acos(c)=2*sqrt(y)*sum(n=0..23,a_n*y^n), y=(1-c)/2, a_n=binom(2n,n)/(4^n*(2n+1))',
   coefficientMonotonicity:'a_(n+1)/a_n=(2n+1)^2/(2*(n+1)*(2n+3))<1',
   proof:'0<=y<=1/2; omitted positive tail <=2*sqrt(y)*a_24*y^24/(1-y)<=2*sqrt(2)*a_24*2^-24. Replace 2*sqrt(2) by strict rational upper bound 283/100.',
   rationalUpperNumerator:String(boundNumerator),rationalUpperDenominator:String(boundDenominator),upperRadians:bound,upperDegrees:bound*180/Math.PI},
  conditionalFloat32Rounding:{unitRoundoff:u,operationAllowance:roundingOperations,positiveHornerGamma:gamma,upperRadians:idealBound,upperDegrees:idealBound*180/Math.PI,
   assumptions:'IEEE round-to-nearest correctly rounded basic arithmetic and sqrt, float32 coefficient rounding, no overflow/underflow in positive polynomial; uploaded c in [0,1].',
   derivation:'Positive Horner arithmetic plus coefficient/sqrt/product roundings bounded conservatively by gamma_(2*(N-1)+3) times pi/2. For c>=1/2 subtraction 1-c is exact by Sterbenz; below 1/2 its propagated angle error is <u radians. Add exact truncation bound.',
   scope:'Conditional arithmetic-model estimate, not a WebGL/GLSL guarantee or a bound proved for arbitrary GPU compilers/backends.'},
  observedWorstErrorReduction:builtin.maximumRadians/series.maximumRadians,
  productionChanged:false,productionOrPhotoAccuracyAccepted:false,
  limitations:['One fixed polynomial was tested; no coefficient/degree search or per-product branches.',
   'Input reference is CPU double Math.acos of each uploaded float32 cosine, not the unquantized angle that generated it.',
   'Measured maximum is limited to this finite sweep and backend; no quantified all-GPU bound is claimed.',
   'This probe does not substitute the formulation into lens shaders or establish final RGB conformance.',
   'Concurrent CPU work makes timing unsuitable for performance conclusions.']});
 report.implementation=before;report.implementationAfter=await codePins();report.sourceSnapshotStable=JSON.stringify(before)===JSON.stringify(report.implementationAfter);report.errors=errors;report.warnings=warnings;
 if(errors.length||invalidOutputs||!report.sourceSnapshotStable)report.status='failed';
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');await fs.writeFile(path.join(output,'shader.frag'),report.fragmentShader);await page.screenshot({path:path.join(output,'probe.png')});
 console.log(JSON.stringify({status:report.status,sampleCount,builtin:report.builtin,series:report.series,truncation:report.exactArithmeticTruncation.upperRadians,
  conditionalFloat32Bound:idealBound,echoDifferences,maximumEchoError,maxDoublePolynomialError,sourceSnapshotStable:report.sourceSnapshotStable,errors,warnings},null,2));assert.equal(report.status,'measured');
}finally{await browser?.close();await server.close();}
