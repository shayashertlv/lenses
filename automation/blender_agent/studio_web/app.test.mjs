/** Offline browser contract test. All job/model/provider endpoints are fixtures. */
import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {chromium} from '../../../ar/node_modules/@playwright/test/index.mjs';

const directory=fileURLToPath(new URL('.',import.meta.url));
const output=path.resolve(directory,'../../data/blender_agent/studio-ui-qa');
await mkdir(output,{recursive:true});
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lYQAAAAASUVORK5CYII=','base64');
const mutations=[],errors=[];let jobs=[],base='',materialDelay=0,specificsFailures=0;
const makeDoc=(revision='original')=>({revision,model_sha256:revision==='original'?'a'.repeat(64):'b'.repeat(64),viewer:{lens_reflection:1},source_blend_matches_revision:revision==='original',
  materials:[{id:'0',name:'Polished black frame',roles:['frame','temple'],editable:true,editable_keys:['base_color','metallic','roughness'],properties:{base_color:'#27342b',metallic:.12,roughness:.029999999329}},
             {id:'1',name:'Warm optical tint',roles:['lens'],editable:true,editable_keys:['base_color','roughness','transmission','ior','clearcoat','clearcoat_roughness','iridescence','iridescence_ior','iridescence_thickness','attenuation_color','attenuation_distance'],properties:{base_color:'#b79c7a',roughness:.08,transmission:.85,ior:1.5,clearcoat:.4,clearcoat_roughness:.1,iridescence:.8,iridescence_ior:1.3,iridescence_thickness:400,attenuation_color:'#b7a090',attenuation_distance:.15}}],
  controls:[{key:'base_color',label:'Colour / tint',type:'color',help:'Tints the surface and multiplies any existing colour texture.'}, {key:'metallic',label:'Mirror / metallic strength',type:'range',min:0,max:1,step:.01,help:'Lower behaves as a nonmetal; higher gives a metallic response.'}, {key:'roughness',label:'Surface roughness',type:'range',min:0,max:1,step:.01,help:'Lower makes sharper reflections; higher softens them.'},{key:'transmission',label:'See-through strength',type:'range',min:.001,max:1,step:.001,help:'Higher transmits more background light. This is not alpha opacity.'},
    ...[['ior','Glass refraction',1,2.5,.01],['clearcoat','Clearcoat gloss',0,1,.01],['clearcoat_roughness','Clearcoat roughness',0,1,.01],['iridescence','Colour-shift strength',0,1,.01],['iridescence_ior','Coating refraction',1,3,.01],['iridescence_thickness','Coating thickness',0,1500,1],['attenuation_distance','Absorption distance',0,10,.001]].map(([key,label,min,max,step])=>({key,label,type:'range',min,max,step,help:'Fixture explanation for '+label+'.'})),{key:'attenuation_color',label:'Absorption tint',type:'color',help:'Colour of light transmitted through the material volume.'}]});
let materialDoc=makeDoc();
const viewer=`<!doctype html><html><body style="margin:0;background:#eeeee7;display:grid;place-items:center;height:100vh;color:#77836d;font:13px sans-serif"><div>Controlled preview fixture</div><script>
const q=new URLSearchParams(location.search),channel=q.get('studioChannel'),origin=q.get('studioOrigin');window.received=[];
window.addEventListener('message',event=>{if(event.origin!==origin||event.source!==parent||event.data.channel!==channel)return;window.received.push(event.data);parent.postMessage({type:'lenses-studio:applied',channel,model_sha256:event.data.model_sha256,pending:false},origin);});
parent.postMessage({type:'lenses-studio:ready',channel,loaded:true,model_sha256:q.get('sha')},origin);
</script></body></html>`;
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,base||'http://127.0.0.1');let body={};
    if(['POST','PATCH'].includes(req.method)){for await(const chunk of req)body._raw=(body._raw||'')+chunk;body=JSON.parse(body._raw||'{}');assert.equal(req.headers['x-csrf-token'],'fixture-token');mutations.push({path:url.pathname,method:req.method,body});}
    const json=value=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
    if(url.pathname==='/api/config')return json({csrf_token:'fixture-token',defaults:{budget_usd:20,effort:'max'},capabilities:{openai_key:true,gemini_key:true,blender:true,ar:true},ar_origin:base});
    if(url.pathname==='/api/jobs'&&req.method==='GET')return json({jobs});
    if(req.method==='GET'&&/^\/api\/jobs\/[^/]+$/.test(url.pathname))return json(jobs.find(job=>job.id===url.pathname.split('/').at(-1)));
    const normalize=images=>images.map((image,index)=>({...image,name:'reference-'+index+'.png',original_name:image.name,url:base+'/reference.png',data_url:undefined}));
    if(url.pathname==='/api/jobs'&&req.method==='POST'){const job={...body,id:'fixture-1',status:'draft',created_at:new Date().toISOString(),images:normalize(body.images)};jobs.push(job);return json(job);}
    const job=jobs[0];
    if(url.pathname==='/api/jobs/fixture-1'&&req.method==='PATCH'){Object.assign(job,body,{images:normalize(body.images||job.images)});return json(job);}
    if(url.pathname==='/api/jobs/fixture-1')return json(job);
    if(url.pathname.endsWith('/specifics')){
      if(specificsFailures){specificsFailures--;job.error='Gemini search failed. Retry the research request.';res.statusCode=502;return json({error:job.error});}
      job.error=null;const source={url:'https://manufacturer.example/model-code',title:'Manufacturer model specification',quote:'Tinted sunglasses'};
      job.specifics={schema_version:1,identity:{brand:'Fixture',model:'The everyday frame',variant:'Black',matched:true},specs:{lens_type:'Tinted sunglasses'},evidence:{lens_type:[source]},sources:[source],uncertainties:['Overall frame width is not established by the available sources.'],search_suggestions_html:'<a href="https://www.google.com/search?q=fixture">Search this exact model</a>',model:'Gemini Pro fixture'};
      job.specs={...job.specs,lens_type:'Tinted sunglasses'};job.spec_provenance={frame_finish:{kind:'user'},lens_type:{kind:'web',evidence:[source]}};
      job.specifics_json=JSON.stringify({identity:job.specifics.identity,specs:job.specifics.specs},null,2);return json(job.specifics);
    }
    if(url.pathname.endsWith('/start')){Object.assign(job,{status:'completed',started_once:true,progress:{message:'Offline fixture completed.',spent_usd:2.31,reserved_usd:.4},result:{model_url:'/api/jobs/fixture-1/model.glb',model_sha256:materialDoc.model_sha256,scene_url:'/api/jobs/fixture-1/scene.blend',scene_matches_revision:true,viewer_url:base+'/fixture-viewer?mode=3d&sha='+materialDoc.model_sha256,ar_url:base+'/fixture-viewer?mode=ar&sha='+materialDoc.model_sha256}});return json(job);}
    if(url.pathname.endsWith('/materials')){
      if(req.method==='GET')return json(materialDoc);
      if(materialDelay)await new Promise(resolve=>setTimeout(resolve,materialDelay));assert.equal(body.base_revision,materialDoc.revision);materialDoc=makeDoc('revision-1');materialDoc.viewer=body.viewer;
      for(const [id,edits] of Object.entries(body.edits))Object.assign(materialDoc.materials.find(material=>material.id===id).properties,edits);
      Object.assign(job.result,{model_sha256:materialDoc.model_sha256,scene_matches_revision:false,revision:'revision-1',model_url:'/api/jobs/fixture-1/model.glb?revision=revision-1',viewer_url:base+'/fixture-viewer?mode=3d&sha='+materialDoc.model_sha256,ar_url:base+'/fixture-viewer?mode=ar&sha='+materialDoc.model_sha256});return json({job,materials:materialDoc,receipt:{geometry_unchanged:true}});
    }
    if(url.pathname==='/reference.png'){res.setHeader('Content-Type','image/png');return res.end(png);}
    if(url.pathname==='/fixture-viewer'){res.setHeader('Content-Type','text/html');return res.end(viewer);}
    const file=url.pathname==='/'?'index.html':url.pathname.slice(1);
    if(!['index.html','app.js','style.css'].includes(file)){res.writeHead(404);return res.end();}
    res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(await readFile(path.join(directory,file)));
  }catch(error){errors.push(String(error));res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:String(error)}));}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch({headless:true});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1060}});const pageErrors=[];page.on('pageerror',error=>pageErrors.push(String(error)));
  await page.goto(base);await page.locator('#save-draft').waitFor();await page.waitForFunction(()=>document.querySelector('#project-list').textContent.includes('saved models'));
  assert.equal(mutations.length,0,'Loading cannot create jobs or call a provider.');
  assert.equal(await page.locator('#budget').inputValue(),'20','A new draft retains the default budget.');
  await page.screenshot({path:path.join(output,'brief-empty.png'),fullPage:true});
  await page.screenshot({path:path.join(output,'brief-overview.png')});
  assert.equal(await page.locator('#description').count(),0,'The free-text description section is removed.');
  await page.locator('#generate-specifics').click();assert.match(await page.locator('#notice').textContent(),/brand, full model code/);assert.equal(mutations.length,0,'A missing product identifier cannot spend on a search.');
  await page.locator('#image-input').setInputFiles({name:'front.png',mimeType:'image/png',buffer:png});
  await page.locator('#model-name').fill('The everyday frame');await page.locator('#spec-notes').fill('Keep the temples substantial and the silhouette softly squared.');await page.locator('#spec-frame_finish').selectOption('Matte');
  await page.locator('.reference-caption select').nth(0).selectOption('front');await page.locator('.reference-caption select').nth(1).selectOption('photo');
  await page.locator('#save-draft').click();await page.waitForFunction(()=>document.querySelector('#draft-state').textContent==='Draft saved');
  assert.equal(mutations.filter(row=>row.path==='/api/jobs').length,1);assert.equal(await page.locator('#drop-zone').isVisible(),true,'Draft photos remain editable after saving.');
  await page.locator('#image-input').setInputFiles({name:'side.png',mimeType:'image/png',buffer:png});await page.locator('#save-draft').click();await page.waitForFunction(()=>document.querySelector('#image-count').textContent==='2 images'&&document.querySelector('#draft-state').textContent==='Draft saved');
  const patch=mutations.filter(row=>row.method==='PATCH').at(-1);assert.equal(patch.body.images.length,2);assert.equal('data_url' in patch.body.images[0],false);assert.match(patch.body.images[1].data_url,/^data:image\/png/);
  await page.locator('#generate-specifics').click();await page.waitForFunction(()=>document.querySelector('#spec-lens_type').value==='Tinted sunglasses');
  assert.equal(await page.locator('#spec-frame_finish').inputValue(),'Matte','Gemini cannot erase a supplied fact.');assert.equal(mutations.filter(row=>row.path.endsWith('/specifics')).length,1);assert.equal(mutations.filter(row=>row.path.endsWith('/start')).length,0);
  assert.equal(mutations.some(row=>'description' in row.body),false,'New drafts send structured fields, not a description.');
  assert.match(await page.locator('#provenance-frame_finish').textContent(),/Your supplied value/);assert.match(await page.locator('#provenance-lens_type').textContent(),/Manufacturer model specification/);assert.equal(await page.locator('#provenance-lens_type a').getAttribute('href'),'https://manufacturer.example/model-code');assert.match(await page.locator('#provenance-frame_width_mm').textContent(),/Unknown/);
  assert.equal(await page.locator('.search-suggestions iframe').getAttribute('sandbox'),'allow-popups allow-popups-to-escape-sandbox','Grounding suggestions are isolated from the application.');
  await page.locator('#specifics-json-details summary').click();assert.match(await page.locator('#specifics-json').textContent(),/Tinted sunglasses/);
  await page.screenshot({path:path.join(output,'brief-filled.png'),fullPage:true});
  await page.reload();await page.waitForFunction(()=>document.querySelector('#specifics-notes').textContent.includes('Overall frame width is not established'));
  specificsFailures=1;await page.locator('#generate-specifics').click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('Gemini search failed'));assert.equal(await page.locator('#generate-specifics').isEnabled(),true,'A failed provider request can be sent again to Gemini.');assert.equal(await page.locator('#spec-frame_finish').inputValue(),'Matte');await page.locator('#generate-specifics').click();await page.waitForFunction(()=>document.querySelector('#draft-state').textContent==='Draft saved'&&!document.querySelector('#generate-specifics').disabled);assert.equal(mutations.filter(row=>row.path.endsWith('/specifics')).length,3);
  jobs[0].status='researching';await page.reload();await page.waitForFunction(()=>document.querySelector('#job-status').textContent==='Researching specifics');Object.assign(jobs[0],{status:'draft',specs:{...jobs[0].specs,lens_height_mm:55}});await page.waitForFunction(()=>document.querySelector('#spec-lens_height_mm').value==='55');await page.locator('[data-section=brief]').click();assert.equal(await page.locator('#generate-specifics').isEnabled(),true,'Reloading during research eventually refreshes the actual fields.');
  await page.locator('#start-run').click();await page.locator('#open-result').waitFor({state:'visible'});assert.equal(mutations.filter(row=>row.path.endsWith('/start')).length,1);
  await page.locator('#open-result').click();await page.waitForFunction(()=>document.querySelector('#preview-state').textContent==='Saved appearance');
  assert.equal(await page.locator('#control-transmission').count(),0,'Unsupported frame transmission must not be shown.');
  assert.equal(await page.getByLabel('Surface roughness value').inputValue(),'0.03','Float32 tails are hidden at control precision.');
  await page.getByLabel('Colour / tint hex colour').fill('#997755');assert.equal(await page.locator('#control-base_color').inputValue(),'#997755');
  await page.waitForFunction(()=>document.querySelector('#material-dirty').hidden===false);await page.waitForTimeout(160);
  let frame=page.frames().find(frame=>frame.url().includes('/fixture-viewer'));assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['0'].base_color),'#997755');assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['0'].roughness),.029999999329,'Display rounding never mutates an unedited property.');assert.equal(mutations.filter(row=>row.path.endsWith('/materials')).length,0,'Sliders only preview.');
  await page.locator('#mode-ar').click();await page.waitForFunction(()=>document.querySelector('#preview-state').textContent==='Unsaved preview');frame=page.frames().find(frame=>frame.url().includes('mode=ar'));assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['0'].base_color),'#997755');
  await page.locator('#material-select').selectOption('1');await page.locator('#control-transmission').evaluate(input=>{input.value='.52';input.dispatchEvent(new Event('input',{bubbles:true}));});await page.waitForTimeout(160);assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['1'].transmission),.52);
  for(const input of await page.locator('#material-controls input').all()){const help=await input.getAttribute('aria-describedby');assert.ok(help,'Every material input is linked to an effect explanation.');assert.ok((await page.locator('#'+help).textContent()).length>20);}
  assert.match(await page.locator('#control-transmission-help').textContent(),/not alpha opacity/);assert.match(await page.locator('#reflection-help').textContent(),/not embedded in the GLB/);assert.equal(await page.locator('#lens-reflection').getAttribute('aria-describedby'),'reflection-help');
  await page.locator('[data-section=progress]').click();assert.equal(await page.locator('#model-frame').getAttribute('src'),'about:blank','Leaving Model stops the embedded camera/viewer.');await page.locator('[data-section=model]').click();await page.waitForFunction(()=>document.querySelector('#preview-state').textContent==='Unsaved preview');frame=page.frames().find(frame=>frame.url().includes('mode=ar'));assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['1'].transmission),.52,'Local edits survive leaving and remounting the model.');
  await page.locator('#mode-3d').click();await page.waitForFunction(()=>document.querySelector('#preview-state').textContent==='Unsaved preview');frame=page.frames().find(frame=>frame.url().includes('mode=3d'));assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['1'].transmission),.52);
  await page.locator('#reset-materials').click();await page.waitForFunction(()=>document.querySelector('#preview-state').textContent==='Saved appearance');assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['0'].base_color),'#27342b');assert.equal(await frame.evaluate(()=>window.received.at(-1).edits['1'].transmission),.85);
  await page.locator('#control-roughness').evaluate(input=>{input.value='.2';input.dispatchEvent(new Event('input',{bubbles:true}));});await page.locator('#new-project').click();assert.equal(await page.locator('#discard-dialog').isVisible(),true);await page.locator('#discard-cancel').click();
  materialDelay=350;await page.locator('#save-materials').click();await page.locator('#new-project').click();assert.equal(await page.locator('#discard-dialog').isVisible(),false);assert.equal(await page.locator('#project-title').textContent(),'The everyday frame','Switching projects is blocked during a save.');await page.waitForFunction(()=>document.querySelector('#preview-state').textContent==='Saved appearance'&&document.querySelector('#download-model').href.includes('revision-1')&&document.querySelector('#draft-state').textContent==='Brief saved').catch(async error=>{console.error(await page.evaluate(()=>({preview:document.querySelector('#preview-state').textContent,draft:document.querySelector('#draft-state').textContent,notice:document.querySelector('#notice').textContent,status:document.querySelector('#job-status').textContent,url:document.querySelector('#download-model').href})));throw error;});
  assert.equal(mutations.filter(row=>row.path.endsWith('/materials')).length,1);assert.equal(await page.locator('#material-dirty').isVisible(),false);assert.match(await page.locator('#material-message').textContent(),/original Blender source remains unchanged/);
  const stickyChecks=[];
  for(const viewport of [{width:1440,height:1060},{width:1440,height:700},{width:844,height:390},{width:720,height:530},{width:390,height:844},{width:320,height:568}]){
    await page.setViewportSize(viewport);
    for(const mode of ['3d','ar']){
      await page.locator('#mode-'+mode).click();await page.waitForFunction(()=>document.querySelector('#preview-state').textContent==='Saved appearance');await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
      const layout=await page.evaluate(()=>{const preview=document.querySelector('.viewer-card').getBoundingClientRect(),stage=document.querySelector('.viewer-stage').getBoundingClientRect(),last=document.querySelector('#download-model').getBoundingClientRect();return {top:preview.top,bottom:preview.bottom,stage:stage.height,controlTop:last.top,controlBottom:last.bottom,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth+1,scrollY};});
      assert.ok(layout.scrollY>0,'Fixture actually scrolls the long material panel.');assert.ok(layout.top>=-1&&layout.bottom<=layout.height+1,'Entire preview stays visible at '+JSON.stringify(viewport)+' '+JSON.stringify(layout));assert.ok(layout.stage>=70,'Model retains a usable preview height.');assert.equal(layout.overflow,false,'No horizontal overflow.');
      if(viewport.width<=680)assert.ok(layout.controlTop>=layout.bottom&&layout.controlBottom<=layout.height+1,'Last controls remain reachable below the pinned preview.');
      stickyChecks.push({mode,viewport,...layout});
      if(viewport.width===1440&&viewport.height===1060)await page.screenshot({path:path.join(output,'model-desktop-sticky-'+mode+'.png')});
      if(viewport.width===390)await page.screenshot({path:path.join(output,'model-mobile-sticky-'+mode+'.png')});
    }
  }
  await page.setViewportSize({width:1440,height:1060});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,'model-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,'model-mobile.png'),fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'No horizontal overflow on mobile.');
  await page.locator('[data-section=brief]').click();await page.screenshot({path:path.join(output,'brief-mobile.png'),fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
  Object.assign(jobs[0],{status:'interrupted',progress:{spent_usd:null,reserved_usd:null},final_output:'The rear pad shape is uncertain.\nReview the side reference before accepting.'});Object.assign(jobs[0].result,{candidate_status:'interrupted_run_candidate',evidence_scope:'Synthetic structural check only.',validation:{compatible:true,product_accuracy_verified:false}});
  jobs.push({...structuredClone(jobs[0]),id:'fixture-2',name:'Second draft',status:'draft',started_once:false,result:null});
  await page.reload();await page.waitForFunction(()=>document.querySelector('#job-status').textContent==='Interrupted');await page.locator('[data-section=progress]').click();assert.equal(await page.locator('#spent').textContent(),'—');assert.equal(await page.locator('#reserved').textContent(),'—');assert.equal(await page.locator('#budget-track').isVisible(),false);assert.equal(await page.locator('#resume-run').isDisabled(),true);await page.locator('#artist-notes>summary').click();assert.match(await page.locator('#artist-final-output').textContent(),/rear pad shape is uncertain/);assert.match(await page.locator('#candidate-scope').textContent(),/structural check only/);
  await page.getByRole('button',{name:'Second draft Draft'}).click();await page.waitForFunction(()=>document.querySelector('#project-title').textContent==='Second draft');assert.equal(await page.locator('#drop-zone').isVisible(),true,'Opening a draft after a frozen run restores reference editing.');assert.equal(await page.locator('.reference-caption select').first().isDisabled(),false);
  jobs[0].result.scene_matches_revision=null;materialDoc.source_blend_matches_revision=true;jobs[0].budget_usd=null;jobs[0].progress={spent_usd:2.31,reserved_usd:.4};
  await page.getByRole('button',{name:'The everyday frame Interrupted'}).click();await page.waitForFunction(()=>document.querySelector('#project-title').textContent==='The everyday frame');await page.waitForFunction(()=>document.querySelector('#material-message').textContent==='The saved Blender source has not been verified against this export.');assert.match(await page.locator('#download-scene').textContent(),/Original Blender source/);
  await page.locator('[data-section=progress]').click();assert.equal(await page.locator('#cap').textContent(),'—','An unavailable imported cap is never replaced with the draft default.');assert.equal(await page.locator('#spent').textContent(),'$2.31');assert.equal(await page.locator('#reserved').textContent(),'$0.40');assert.equal(await page.locator('#budget-track').isVisible(),false);assert.equal(await page.locator('#resume-run').isDisabled(),true,'Known spending cannot enable Resume when the total cap is unknown.');
  await page.locator('[data-section=brief]').click();assert.equal(await page.locator('#drop-zone').isVisible(),false);assert.equal(await page.locator('.reference-caption select').first().isDisabled(),true,'Opening a frozen run after a draft locks references.');
  assert.deepEqual(pageErrors,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,scope:'Offline mocked API/iframe contracts; no Gemini, Astra, Blender or real camera calls.',stickyChecks,mutations:mutations.map(({method,path})=>({method,path})),screenshots:output}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
