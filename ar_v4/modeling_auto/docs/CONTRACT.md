# Shared implementation contract

Everything is new and self-contained. Python 3.12+ FastAPI/httpx/Pillow backend,
native Blender 5.2 subprocess, Vite/strict TypeScript/Three.js frontend on 8060.
All paths, dependencies, caches and test outputs stay inside modeling_auto.
Agents own disjoint files; coordinate interface changes with root.

## Domain and orchestration (root)

Ordered stages: generate, lenses, connections, texture, finish, review, complete.
User-confirmed: Start run authorizes the complete five-call sequence automatically,
with no intermediate approval gates. Pause at final preview. Another edit authorizes
only one new finish call using five original references, then preview again.
No auto retry; cancellation and restart preserve reserved calls, response
bytes and last good revision. A saved task/result is resumed only by an explicit
local recovery action. Revision adoption occurs only after a complete worker
result and immutable artifact hashes. A final accept binds an exact .blend hash.
Review repeat is finish only. The connections image set is five model renders and
one real lens/frame close-up, confirmed by owner.

Separate user-requested test mode adds finish_refine after finish. Its immutable
per-job pipeline='test' authorizes generate,lenses,connections,texture,finish,
finish_refine automatically, then review. Default/legacy pipeline='current'
retains the original plan. Both test finish stages use original5 + current5 +
current lens/rim close-up (11 images). finish_refine executes with native stage
finish and its complete geometry/UV/normal/transform lock. Test review/edit
requires nonempty notes (at most6000 characters) and repeats finish_refine only;
preserve original notes, store the latest edit_instructions separately. Download
and finish accepts the exact packed master before downloading. Each Astra
operation snapshots context and exact current model/image hashes before dispatch.

Job public JSON: id,name,version,status,stage,message,error,notes,dimensions,
references:[{angle,url}],current:null|{id,blend_url,model_url,proofs:[{angle,url}],
closeup_url,inspection},revisions:[{id,stage,blend_url,model_url}],proposal:null|
{id,stage,provider,title,description,image_count,images:[{label,url}],settings,
input_sha256},calls:{astra,meshy},allowed_actions:[string],accepted:null|{sha256,url}.
Status: draft,running,waiting,failed,cancelled,interrupted,complete.
Public `current.stage` identifies the completed step that produced the displayed
revision; `stage` identifies the active/stopped step. `recovery_kind` is
`astra_script` when explicit recovery will execute a saved Astra script locally
and no completed native result is already available; otherwise null. It does not
authorize an automatic retry or alter the remaining-stage sequence.
Public pipeline defaults to current for legacy jobs; pipeline_stages lists only
the selected remote stages, and edit_instructions exposes latest test feedback.

HTTP: GET /api/health; GET /api/jobs; GET /api/jobs/{id};
POST /api/jobs multipart name,notes,dimensions(JSON),front/back/left/right/angled;
optional pipeline field accepts current/test at creation only, with no conversion
endpoint. Duplicate fields and unknown modes are rejected before creating a job.
POST /api/jobs/{id}/start {version};
POST /api/jobs/{id}/edit {version,notes?};
POST /api/jobs/{id}/recover {version};
POST /api/jobs/{id}/retry_auth {version}: owner-triggered new Astra request only
after a verified saved 401/invalid_api_key rejection, then remaining stages;
retains failed operation/counters and reuses completed revisions. Never automatic.
POST /api/jobs/{id}/cancel {version}; POST /api/jobs/{id}/accept {version};
GET /api/jobs/{id}/files/{artifact_id}. No arbitrary filesystem paths in URLs.
Conflict409 for stale/repeated state mutation. UI polls GET only, never automaticPOST.
Uploaded references normalized into new self-contained JPEG copies; originals kept.

## Providers and validation (provider agent)

Own app/providers.py, app/prompts.py, app/script_validation.py and their tests.
Exception ProviderError(message). Modules must not import controller or worker.

MeshyClient(key, client=None):
 async submit(stage:str, references:dict[str,Path], model_path:Path|None,
              receipt_dir:Path, cancel:asyncio.Event) -> str task_id
 async poll(stage,task_id,receipt_dir,cancel) -> dict task
 async download(task,receipt_dir,cancel) -> Path GLB (raw retained)
 async close(). No retrying POST; polling GET bounded/restart cancellable.
 function meshy_settings(stage)->dict pure request options without credentials/images.
 Pin documented Meshy7 Ultra/no remesh/blank for generate; Meshy7/8K/PBR for texture.
 Only four references Meshy supports; front primary then back/left/right.
 New full textured asset is imported natively, not map-transferred onto old mesh.

AstraClient(key,client=None): async edit(stage, images:list[Path],
 context:dict, receipt_dir:Path, cancel:asyncio.Event)->str script; async close().
 context contains scene inspection, user dimensions/notes and model hash.
 Exactly5 original refs for lenses/finish;6 current views+closeup forconnections.
 Test mode keeps lenses5/connections6; both finish and finish_refine use11 images
 in original-reference then current-render order. Current finish stays5.
 One forced run_blender_python custom tool, no narrative or second tool response.
 gpt-6-astra, reasoning high, standalone request, no previous_response_id/history.
 Durable redacted receipts/raw response and script before validation/execution.
 validate_script(script)->None raises ValueError; shared exact allowlist and
 safe_builtins()/guarded_import usable by worker; guard BMP IO/ops filesystem/network,
 private attributes, exec/eval/open and dangerous Blender operations. Support
 bpy,bmesh,math,mathutils and specific bvhtree/kdtree/geometry queries consistently.
 Instructions describe one real editing session; no fabricated hidden context.
 Scope lenses: preserve existing shape, gentle smoothing and curved closed lenses;
 connections: only lens/rim seating corrections; finish: materials only.
 Direct zero-argument datablock.as_pointer() is supported for current-session
 identity/deduplication. Method capture/rebinding, guarded reflective access,
 from_address/from_pointer and arbitrary native imports remain unavailable.
 Existing geometry locks still decide whether the script output is adoptable.
 Worker capabilities documented jointly with native agent before final prompts.

## Blender adapter and worker (native agent)

Own app/blender_runner.py, blender/*, scripts/native_smoke.py and native tests.
 BlenderRunner(executable=None,timeout=600,resolution=768,samples=24).
 async run(action:str,input_path:Path,output_dir:Path,*,script:str|None=None,
            stage:str|None=None,dimensions:dict|None=None,cancel=None)->dict
 action='import' (blank or full textured GLB), 'edit' (.blend+script), 'inspect'.
 Return {blend_path,model_path,proofs:{front:path,back:path,left:path,right:path,
 angled:path},closeup_path,inspection:{objects,materials,bounds,geometry_sha256,
 warnings,...},hashes:{relative_or_key:sha256}}. Absolute paths inside output_dir.
 Native process --background --factory-startup --disable-autoexec, own HOME/temp,
 cancellation kills owned process tree. Input never saved over; packed .blend and
 GLB +5 views+oblique lens/frame closeup. Save/reopen/check self-contained assets.
 Native import of complete textured Meshy GLB; missing tangents not a fatal
 scalar-map transfer condition because there is no old bridge.
 Stage finish exact geometry lock, including evaluated meshes/transforms/visibility;
 connections keep non-lens geometry unchanged; first lens edit must not rebuild,
 resize/symmetrize/remesh the frame. Keep original revision always, report limits.
 Do not port the old optical estimator or block calls on guessed seam inference.
 Editor uses bpy to inspect current geometry dynamically. Provide documented
 helpers only if actually implemented/tested. New lenses tagged auto_role lens_left,
 lens_right; show closeup of real lens/rim regions, not a guessed arbitrary view.
 Static mesh+scene validity, bounded script/native runtime, finite coordinates,
 sensible limits for unremeshed Ultra output (not arbitrary300k rejection).

## Frontend (UI agent)

Own package.json/package-lock,tsconfig,vite.config,index.html,web/* and browser tests.
 Create clean functional standalone UI: labeled uploads/dimensions, explicit
 run disclosure/settings/images, cancel/status/history, GLB preview with
 OrbitControls, five view proofs and lens closeup, final accept/download .blend
or another material edit. Start run has a concise disclosure of its five remote
calls (2 Meshy,3 Astra). No per-step approvals. Model/schema above. Proposal may
remain null; settings/disclosure shown at draft. Expose local setup password fields
for new API keys (never read model_studio/.env). Root supplies POST /api/settings
{openai_key?,meshy_key?}; GET health indicates key presence only, never key values.
 Native browser-download link on accepted exact artifact; do not open arbitrarypaths.
 npmcache under data/cache/npm. Native viewer local bundled assets only, no CDN.
 Current/Test navigation selects the creation mode; /?pipeline=test opens test
 creation, while a selected saved job always determines its own mode. Test run
 disclosure is 2 Meshy/4 Astra, with two labeled finish passes. Final test actions
 are Download & finish run or Send another edit with required specific feedback.

## Testing

All provider calls faked, no real credentials or old models. Build a NEW synthetic
frame/rims and curved-lens fixture for native full flow (including actual edits).
Test wrongimagecounts, invalidscript/imports, malformedresponses, HTTPfailures,
timeouts/cancel, duplicate/stale approvals, restart, accepteddownloadintegrity,
missing tangents, texture identity, closeup meaningfulness and materialgeometrylock.
Tests and scripts clearly label fixtures; they are not quality acceptance.
