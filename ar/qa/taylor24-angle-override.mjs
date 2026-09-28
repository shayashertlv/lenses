/** Fixed QA-only math from acos-precision-v1; no tunable order/coefficients. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const sha=source=>crypto.createHash('sha256').update(source).digest('hex');
const gcd=(a,b)=>{while(b)[a,b]=[b,a%b];return a;};
export const TAYLOR24_COEFFICIENTS=[];
let central=1n;
for(let n=0;n<24;n++){if(n)central=central*BigInt(4*n-2)/BigInt(n);let numerator=central,denominator=4n**BigInt(n)*BigInt(2*n+1);const d=gcd(numerator,denominator);numerator/=d;denominator/=d;
 TAYLOR24_COEFFICIENTS.push({n,numerator:String(numerator),denominator:String(denominator),shaderLiteral:(Number(numerator)/Number(denominator)).toPrecision(17)});}
const horner=[`highp float p=${TAYLOR24_COEFFICIENTS.at(-1).shaderLiteral};`,...TAYLOR24_COEFFICIENTS.slice(0,-1).reverse().map(c=>`p=p*y+${c.shaderLiteral};`)].join('\n');
export const TAYLOR24_GLSL=`highp float qaTaylor24Acos(highp float c){highp float y=(1.0-c)*0.5;${horner}return 2.0*sqrt(y)*p;}`;
const EXPRESSION='float angle=degrees(acos(clamp(dot(N,V),0.0,1.0)));';
const REPLACEMENT='float angle=degrees(qaTaylor24Acos(clamp(dot(N,V),0.0,1.0)));';
const MARKER='fragmentShader:`${LENS_RESPONSE_GLSL}\n';
export function overrideAngleMath(source,mode){assert(['builtin','taylor24'].includes(mode),'angle-math must be builtin or taylor24');
 if(mode==='builtin')return {code:source,receipt:{mode,applied:false,sourceSha256:sha(source),transformedSourceSha256:sha(source)}};
 assert.equal(source.split(EXPRESSION).length-1,1,'Expected exactly one unchanged optical acos expression');
 assert.equal(source.split(MARKER).length-1,1,'Expected exactly one unchanged optical fragment shader marker');
 assert.equal(source.split('function opticalMaterial(appearance)').length-1,1,'Expected one optical material function');
 const start=source.indexOf('function opticalMaterial(appearance)'),end=source.indexOf('class Transport',start),expression=source.indexOf(EXPRESSION),marker=source.indexOf(MARKER);
 assert(start>=0&&marker>start&&expression>marker&&expression<end,'Override is outside the bounded optical fragment');
 assert(!source.includes('qaTaylor24Acos'),'Refuse duplicate/preexisting replacement');
 const code=source.replace(MARKER,MARKER+TAYLOR24_GLSL+'\n').replace(EXPRESSION,REPLACEMENT);
 return {code,receipt:{mode,applied:true,sourceSha256:sha(source),transformedSourceSha256:sha(code),expectedExpression:EXPRESSION,
  replacementExpression:REPLACEMENT,expectedExpressionCount:1,injectionMarker:MARKER,injectionCount:1,helper:TAYLOR24_GLSL,
  helperSha256:sha(TAYLOR24_GLSL),coefficients:TAYLOR24_COEFFICIENTS,
  priorProbeReportSha256:'f8130bfa6beb0c3267240ae68f93932efe1c86b5a720d047e4c5132d2924f20a',
  scope:'Test-only in-memory optical fragment override; CPU oracle, original files, coefficients and thresholds unchanged.'}};}
