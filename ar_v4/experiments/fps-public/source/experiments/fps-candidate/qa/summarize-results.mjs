import fs from 'node:fs/promises';
const files=process.argv.slice(2);
const group=runs=>{
  const rows=runs.flatMap(run=>run.rows??[]),intervals=runs.reduce((n,run)=>n+Math.max(0,(run.rows?.length??0)-1),0);
  const span=runs.reduce((n,run)=>n+(run.rows?.length>1?run.rows.at(-1).publishedAtMs-run.rows[0].publishedAtMs:0),0);
  const ages=rows.map(row=>row.totalMs).sort((a,b)=>a-b),tracked=rows.filter(row=>row.hasFace);
  return {frames:rows.length,fps:span?intervals*1000/span:null,runFps:runs.map(run=>run.summary.processedFps),
    ageP95:ages[Math.ceil(ages.length*.95)-1]??null,tracked:tracked.length,masked:tracked.filter(row=>row.hasMask).length,
    visibleHairEdits:rows.filter(row=>row.changedPixels>0).length,
    queryReductionFrames:rows.filter(row=>Object.entries(row.native??{}).some(([key,value])=>key.endsWith('.reducedGlQueriesUsed')&&value===true)).length,
    deferredCheckFrames:rows.filter(row=>Object.entries(row.native??{}).some(([key,value])=>key.endsWith('.deferredSubmissionChecksUsed')&&value===true)).length};
};
const summaries=[];
for(const filename of files){
  const report=JSON.parse(await fs.readFile(filename,'utf8'));
  summaries.push({file:filename,complete:report.complete,passed:report.passed,build:report.productionBuild?.fingerprint,
    groups:Object.fromEntries([...new Set(report.runs.map(run=>run.variant))].map(mode=>[mode,group(report.runs.filter(run=>run.variant===mode))])),
    order:report.runs.map(run=>({mode:run.variant,fps:run.summary?.processedFps,age95:run.summary?.ageMs?.p95}))});
}
console.log(JSON.stringify(summaries,null,2));
