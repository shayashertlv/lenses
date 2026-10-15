# glasses3d progress

Started fresh on 2026-10-10 when WP6 was rebuilt. The previous log (to 62b25e4) and the WP6 v1 design are in
[archive/20261010](archive/20261010/README.md); older snapshots are in [archive/20261008](archive/20261008/README.md).
Archived files are evidence, never instructions. The WP6 v1 handoff, the two session prompts and the Astra temple lab
plan were deleted on 2026-10-13 (tag `review-20261013-pre-cleanup`).

**Full-codebase review 2026-10-13: [REVIEW_20261013.md](REVIEW_20261013.md)** (status per WP from the code, the
automation gaps, ranked defects, cleanup). Read it before starting WP2 D–F, WP7 or WP8.

**Active plan from 2026-10-13: [ASSEMBLY.md](ASSEMBLY.md)** (owner: Claude plans, Codex executes). Assemble the tools
into one automation in milestones M0 (blocking fixes) → M1 (job intake, registry v1, offline end-to-end run) → M2
(instructions, image policy) → M3 (delivery, owner review ledger) → M4 (seating, temples, print as stages) → M5 (paid
validation, owner OK per run). **M0-M4 are complete and proven offline**, including the owner-approved rimless
matte expansion and the final full regression suite. **M5-prep items 1-4 are complete at $0**: the $20 working
budget, unfinished draft delivery, isolated one-request smoke support, and the real Oakley job with a proven
intake and unapproved run-1 estimate. **M5-prep and M5 step 0 are complete**: the one approved real smoke passed
at $0.211823. **M5 Oakley run 1 was approved and attempted on the corrected job; it failed at the first render's
checkpoint verification after $0.828903. No delivery was published.** The $0 reproduction and accounting audit
are below. **The owner authorized the checkpoint/failed-draft repairs and exactly one fresh run on the same
corrected job**, conditional on the repairs, proofs and full suite passing and a new estimate under $20. Terms:
$20 settled stop, $23 reserve cap, max effort; not a resume. **Repairs committed as `123dcca`; the authorized
fresh run completed and delivered at $19.161346 on c0021, with specification and seating passing. Webcam review
was started.** Appearance limitations remain below; M5 owner visual acceptance is pending. **The separately approved
Oakley run 2 subsequently delivered a reserve-limit draft at $15.468835. M5-fix-2 is complete: Claude's
2026-10-14 review (`6570b88`) closed item 6 as delivered and withdrew the photo-pair agreement proof criterion;
calibration research has stopped. The historical disagreement evidence remains below. The owner then approved
exactly one fresh Oakley run 3. It completed normally at $6.478750, published a finished c0007 delivery, and
did not call `temple_stage`.** Specification validation passes; seating reports 70 failing stations and AR retains
temple-occlusion warnings. After M5-fix-3 and M5-fix-4, the separately approved fresh **Oakley run 4 completed
normally at $10.611259 on c0015**, with specification and AR checks passing without warnings, visible lens
transparency, and seating still reported as failing. **The owner visually accepted run 4 on 2026-10-15:
"as far as i can see the model is perfect".** This closes the known-product Oakley validation in M5 step 1;
Prada run 1 subsequently completed normally at **$11.903198 settled**, delivering **c0010** with specification,
seating and AR checks passing. Its temple stage applied the selected revision but reported unresolved top/rear
likeness. **Prada owner visual acceptance is pending**; FT1119 and the three unseen products have not been run.
No further paid run is authorized or started.
Claude's independent rerun of `tests.proofs.assembly_m1`
passed (90.0 s, 11 scripted requests, delivered, receipts clean, $0). The stray repo-root `data/assembly/
m1-proof-b8bb88d4` was moved to `glasses3d/data/assembly/m1-proof-b8bb88d4-from-root`. Every paid run still needs
an estimate and the owner's explicit approval.

## M5-step-2 Prada run 1: finished delivery and review (2026-10-15)

**Finished normally, not a money-stop draft. Spent $11.903198 settled; held $0; unknown $0.** Job
**`prada-beige-86248315`**, request **`20261015T083404-b50f0d`**, final checkpoint **c0010**. The model is
recognizably Prada, with close front colour and visibly transparent lenses. The outstanding visual issue is the
temple configuration across the top, rear and right-side photos. Technical finish is not owner acceptance;
no Prada verdict was entered and no other paid run was started.

Owner approval, recorded verbatim through `job approve`:

> I approve Prada run 1: job prada-beige-86248315, request 20261015T083404-b50f0d, fresh run, effort max, $20 settled stop and $23 maximum reserve. please start the run, then when the results are in please perform a review

**Execution and identity:** started from the clean primary checkout on `glasses3d` at **bee5e42**, using the
already-estimated request (expected $15.282279). Fresh, zero history, effort max, $20 settled stop / $23 reserve,
no turn cap. Detached keeper PID 18408 launched 08:45:01 UTC; model loop 08:45:04–09:37:03 UTC, **3,119.752 s**;
keeper exited **0** at 09:37:05 UTC. Forty-second monitoring, BLAS threads one and descendant EcoQoS opt-out
remained active; parallel camera fits used six workers. Native heartbeat maximum gap **2.14 s**, zero write
failures. No silent stall, unknown API outcome, teardown error or checkpoint failure. No code, job brief or
reference photo was changed by Codex during the run; only Astra authored the model.

**Accounting and approval audit:** **81/81 requests settled**, no rejected HTTP requests, dangling holds or
reconciliation entries. Parent **49 requests / $7.192743**; the one temple stage **32 / $4.710455**. Every receipt
was independently repriced and agrees with the job ledger and final budget. Largest temporary hold
**$2.628588**, request 81; peak settled plus in-flight hold **$14.347861** ($11.719273 already settled plus that
hold), below $23. The hold was released at settlement; it is not an additional charge. Peak counted input
**144,751 tokens**; aggregate cached-input share **95.4706%**. No money limit violation. Exact owner words,
approval/request hashes, max settings, six beige inputs and description verified; all **627 implementation
files** matched the saved receipt/source archive and clean bee5e42 before this log update. The six input hashes
remain unchanged, with no black photo sent to either parent or child. Evidence:
`data/assembly/m5-prada-prep/review/accounting-audit.json`; native
`data/jobs/prada-beige-86248315/runs/20261015T083404-b50f0d/{result.json,budget.json,transport/}` and
`data/jobs/prada-beige-86248315/budget/job-ledger.json`.

**What Astra did:** built a continuous full-rim acetate front/bridge, integral nasal bearing surfaces, curved
closed taupe lenses, broad faceted temples with full inward hooks, gold hinge parts, bilateral PRADA/MILANO
markings and simplified crests, inner-arm markings, and a single lens etch. It corrected the initial overly white
acetate using both photo-background and same-export worn comparisons. It discarded a front-decimation trial and
instead reduced lens tessellation, retaining the better front. Final export **98,946 triangles / 2,360,492 bytes**,
22 primitives, no external textures, baked transforms, metre units, +Y up/+Z forward, bridge underside at origin.
Overall width **145.64 mm is Astra's scale assumption**; no owner dimensions or model number were supplied.
Astra's final summary explicitly acknowledges unresolved top/rear tips, right-side outline and approximate crest.

**Temple integration proved in a real job:** `temple_stage` entered at parent c0002, ran for **19m 00.895s**, and
returned `finish: stuck` for unresolved visual agreement, with successful execution/hard checks. It deliberately
selected **revision 7 / original child c0009**, restored it as child c0014, and applied it as parent c0003.
All three fingerprints match; the fitted-spec SHA is revision 7, not latest applied trial 9 or built variant 11.
Independent GLB-array comparison shows only Temple_L/R geometry/normals changed; front, lenses, hinges, materials,
transforms and UVs were preserved. Stage solids closed, Euler 2, planar checks pass, minimum thickness **3.24 mm**.
The front border-ramp warning remains. Six crossing readings came only from photos 02/05, so zero fitted residual
with zero degrees of freedom is not independent multi-view agreement. Astra retained the measured facets after
trials could not reconcile top/rear and side views. Evidence: `review/stage-audit.json` under the prep folder;
run tool `0008-temple_stage`, `0040-facet_finish`, and the stage receipt.

**Final technical checks:** all **7 specification rows pass**, no specification error or live triangle-budget
failure. Both primary lenses are independently closed/outward with zero boundary/nonmanifold edges,
**2,247.036 mm³ volume each**, single-sided, transmission **1**, IOR **1.49**, roughness **0.045**. Opaque frame
material is unchanged by the exporter single-sided policy. Seating passes **768/768 acetate stations**, capture
**0.8204–0.8898 mm** against the 0.6 mm rule. An earlier helper repair was refused at c0004 for excessive face
deformation; Astra then added a smooth 0.72 mm buried lens margin and rechecked successfully. Seating remains
information under the owner's policy, not a finish gate. Decorative lens glyph component #8 is reported
zero-thickness/nonclosed: do not extend the closed-primary-lens result to every lettering component.

AR capture exits **0**, fixed scale, front portrait/checker and both close ±50° worn sides included. Eyes, skin
texture and checker squares are plainly visible through both lenses. Front lens background response **0.513** is
display-RGB information, **not physical VLT**. Four spec warnings remain: **6 front / 18 left-temple / 22
right-temple zero-area triangles**, plus temple roots only **1.62 mm** above lens centre. The AR report has those
four plus two broad-temple classification warnings and **LIVE SITE DIFFERS (arm spread)**: this review renderer
spreads from guard z −19.8 mm; live `ar/` pivots at thin-shaft boundary z −77 mm, potentially hiding proximal broad
arms in turned views. Astra's summary omitted these three renderer warnings; preserve them here. Front and
temples are separate model objects despite the heuristic warning's generic wording. No renderer or gate changed.
Full technical audit: `data/assembly/m5-prada-prep/review/technical_audit.json`.

**Visual review of all six beige photos and final AR:** warm cream replaces the initial chalk-white look; front
and angled grey/taupe lens colour is close, while the side-on crop looks more golden. Front lens median RGB is
**[186,181,173]** in both reference and drawing; frame **[232,226,201]** reference versus **[232,227,204]** drawing.
These support the eye judgment but are not appearance gates. Final basis-01 geometry comparisons on c0010:

| Reference / wearer view | Silhouette IoU | Mean contour distance |
|---|---:|---:|
| 04 front / both | 95.14% | 0.645 mm |
| 02 angled / left | 88.99% | 1.412 mm |
| 05 side / left | 84.04% | 1.962 mm |
| 03 top / both | 80.32% | 1.365 mm |
| 06 rear / both | 76.34% | 3.274 mm |
| 01 side / right | 68.96% | 5.801 mm |

The right-side matte includes visible floor reflection/shadow; its number is not a pure shape-error measurement.
Rear hooks visibly project too far inward and terminate higher than the reference; top tips and the right-side
far arm also differ. Existing evidence cannot separate camera, pose and geometry contributions, so this is not
proven to be simply short arms. Left-side projected reach is close. Facets are real, visible in normal passes and
worn sides, but the hinge-side triangle is faint and small arm-to-hook steps are less smooth than the photo.
Front bridge/corner bevels are sharper; crest/hardware are simplified and lens etch remains more conspicuous on
the face. A second fitted basis exists from c0006; finish uses its front camera, while the final six-view audit
uses basis-01. Do not claim improvement by comparing numbers across those bases. Detailed eye review with native
image paths: `data/assembly/m5-prada-prep/review/visual-review.md`.

**Infrastructure finding, not repaired during the run:** four font-loader refusals (calls 53–56) cost
**$0.411204 settled**, including **$0.288771** of retries after the first misleading suggestion. The sandbox says
to use `load_font(family)`, but the existing trusted worker loader has no general tool wrapper; neither bare
`load_font` nor `g3d.load_font` is callable. Font files exist. Astra recovered with geometric PRADA glyphs and
Blender's default MILANO font. Future repair should expose the existing allowlisted loader as a journaled host
tool, keeping arbitrary file reads blocked. No capability/gate was changed. Evidence: `review/font-loader-finding.json`.

**Delivery/review:** immutable delivery
`20261015T083404-b50f0d-99b86662482797375bfea35d0b91b33447d564942cf349d558f1af857415a94e`, GLB SHA
`7a0f508530daf5b3253479a3090c5e2d2450fbbbf1c21f18b66ab4ea0eb77d4a`. Native delivery report and manifest hashes
verified. `job review prada-beige-86248315 --port 0` started verified WMI-detached webcam server PID **33532**,
port **58592**, bound to these exact bytes and fixed lens environment 1; page queued in Codex. URL/binding in
`data/jobs/prada-beige-86248315/review/current.json`. Unedited short-path evidence copies, all hash-checked:

- `data/assembly/m5-prada-prep/review/front-photo-and-worn.png` (front photo first, final finish comparison);
- `data/assembly/m5-prada-prep/review/ar-contact-sheet.jpg` (portrait, checker, ±50° worn sides, standard poses);
- `data/assembly/m5-prada-prep/review/front-angle-side-and-worn.png` and `side-photo-and-worn.png`;
- `data/assembly/m5-prada-prep/review/six-view-geometry.png` (Blender/clay/overlay evidence; judge colour in AR);
- `data/assembly/m5-prada-prep/review/image-manifest.json` (original paths, hashes and delivery binding).

Only this progress log is committed with an explicit path. No runtime code changed, so no new test or full-suite
rerun was warranted: verification was the real end-to-end run, final spec/seating/AR checks and independent
read-only receipt, source, geometry and visual audits. The **197/197** offline intake tests from prep remain the
relevant code-test baseline. No photos, jobs, model assets or local evidence committed; never pushed. Await owner
visual judgment; no further paid run authorized.

## M5-step-2 Prada prep: ready for owner approval ($0, 2026-10-15)

Resumed clean on `glasses3d` at `6be1cbd`, after the owner supplied the missing description in this chat's reply
to question `call_11ac9dc3eaca415ca217378f3a742fb9`. **That latest owner reply is the description's source**, not
the older Studio research or a coding-agent paraphrase. The job stores the exact 306-character reply, including
the first line's trailing space and no final newline, byte-for-byte. Local verbatim copy:
`data/assembly/m5-prada-prep/owner-description.txt`; SHA
`38161f1e2bfd61fca102eb851838046cf1d166a56c072522113a0c6b0c3baaec`.

**Items 1-4 completed at $0; waiting for approval:**

1. Normal CLI `python -m glasses3d job create` created **`prada-beige-86248315`**, named **Prada beige**, from
   the six beige source photos listed in the source-photo audit below. Description is the owner's exact material,
   polished-beige finish and logo note. No separate owner measurements/specification file was supplied, so
   `specs_json` is exactly `{}`; no researched size, SKU or generated facts were added. Photos were copied without
   re-encoding. Job JSON SHA `b070a416e0a17fa3e2dbbe31509510621cbd83eadc2f88a365b046a6e59537ce`.
2. All **six copied photos** were checked against the original WP6 sources and visually rechecked. Labels remain:
   **01 side/right; 02 angled/left; 03 top/both; 04 front/both; 05 side/left; 06 rear/both** (wearer's sides).
   **6/6 hashes match; zero black photos.** The original source paths and full SHA-256 values are in the next
   section and `data/assembly/m5-prada-prep/photo-audit.json`. The front-first labelled sheet remains
   `data/assembly/m5-prada-prep/beige-photos-labelled.jpg`.
3. Printed and inspected the actual prepared brief, invocation, instructions and every tool description, including
   the nested temple registry. Saved all exact schemas, text, settings, photo IDs and hashes under
   **`data/assembly/m5-prada-prep/run-inputs/`**. Parent: **24 tools / 7,991 instruction characters** (under 8,000).
   Child: **8 tools**, with its separate **9,497-character** specialist instructions and matching approval contract.
   `temple_stage` fits the photographed broad, solid acetate bodies with faceted outer faces; its documented
   single-material/body-outline/hinge-capture limits still apply to Astra's future geometry. Metal hinges remain
   separate. Its catalog contains only `reference-01` through `reference-06`, all target photos. No historical
   black photo, WP6 scene, fitted temple, camera basis or conversation is imported. The child's future brief must
   be generated from the parent's current geometry and cameras at stage entry; it cannot exist before that model.
   Parent invocation explicitly says **$20 settled stop / $23 reserve**. Parent and child settings are max effort
   and share those enforced limits; no Astra-selected money argument exists. Retain the earlier audit's wording
   caveat: the child's first message calls remaining reserve an "allowance" and does not restate $20 there;
   subsequent shared footers do. This is a known presentation limitation, not another spending allowance.
4. `job estimate prada-beige-86248315 --cap 23 --settled-stop 20` created fresh request
   **`20261015T083404-b50f0d`**, request SHA
   **`e163cf30b49ff784aaf3f85933f14f9f8623e0af525fcc733caa822fdcca2721`**. Expected cost **$15.282279**;
   growth-only range **$13.525186-$20.843376** (CLI conservatively displays **$15.29, $13.53-$20.85**).
   Effort **max**, settled stop **$20**, maximum reserve **$23**, no turn cap, **fresh / not a resume**.
   Forecast assumes 60 replies and historical temple-run growth; it is not a confidence interval or a separately
   forecast parent-plus-specialist workload. The upper estimate exceeds $20; only the live money guard bounds
   requests, and completion before a money stop is not guaranteed. No approval has been written or consumed.

**Verification:** input preparation was recomputed with socket connections forbidden. Description bytes, all six
sent image identities, fresh zero-history input, settings, tool contracts and request SHA agree. Independent audit
reconstructed the same parent and nested schemas and verified all **12 preview-file hashes**. No worker was
started, no existing scene/checkpoint/history exists in this fresh job, and the empty template will be created by
the authorized runner. The run directory contains only its approval request. `git check-ignore` confirms the job,
photos and evidence stay local.

Relevant existing suites (`test_jobs`, `test_brief`, `test_instructions`, `test_session_inputs`, `test_approvals`,
`test_job_cli`, `test_temple_stage_budget`) passed **197/197 in 16.21 s**, using **8 workers, --dist loadfile**,
BLAS threads one, with **29.0 GiB free commit** checked before launch. Log and exit 0:
`data/assembly/m5-prada-prep/intake-tests.{log,exit}`. These are offline tests using synthetic temporary approvals;
the Prada request remains unapproved. No new test or runtime code was written, and no full model regression suite
was repeated for this job-data/progress-only prep. **Actual spend $0; no API request or paid run.**

Only PROGRESS.md is committed with an explicit path; no photos/job artifacts or code are committed, nothing pushed.
Next action is the owner's explicit approval of this job/request and estimate. Do not start a paid run until then.

## M5-step-2 Prada prep: initial missing-description audit, resolved above ($0, 2026-10-15)

Started in the primary `C:\Users\Shay\PycharmProjects\lenses` checkout, branch `glasses3d`, clean at
`cbc5cfc`; the requested ancestry check passed. Read ASSEMBLY's new M5-step-2 prep. Latest owner restriction,
verbatim: **"only use the beige model images! no black model images!"** No paid call was made.

**Item 1 is waiting for the owner's exact description.** No owner-authored earlier Prada note was found in the
original Studio evidence. All three checked job records have `description: ""`:

- `C:/Users/Shay/PycharmProjects/lenses/automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/job.json`
  (SHA `e94ad68c111c98a0532d7b4cc07ac657542c56ddb093a2da91397dff52540ed8`);
- `C:/Users/Shay/PycharmProjects/lenses/automation/data/blender_agent/studio/jobs/ff56ff2da2244c89b799f4a0605fb862/job.json`
  (SHA `19640da1d015fa1d2b92b03eabb8ddb3946848d0cbb192b8ebe76c5364b1224c`);
- `C:/Users/Shay/PycharmProjects/lenses-recovery/automation-cleanup-20261003T071535Z/moved/claude-review/evidence/prada-latest/job.json`.

Both Studio briefs, the primary prepared brief, both saved initial run prompts and both read-only conversation
databases were checked. Each database has one user-role row containing the software-assembled brief/invocation
and six images, with no separate human note. The eight specifications in each of the three records are explicitly
`spec_provenance.kind: "web"` (24 provenance entries checked), not owner-supplied specifications. The old WP6
brief is generated technical prose. None is copied into owner-verbatim fields. Asked the owner for the exact Prada
description, as ASSEMBLY item 1 explicitly requires. **No new job, request, estimate or approval exists yet.**

**Independent work while that answer is pending:** all six beige source photos were inspected by eye, with a second
independent visual audit agreeing on all six view/side labels. Side means the wearer's side facing the camera.
They are the exact six target photos from the accepted WP6 run-8 package; **6/6 source bytes and SHA-256 values
match** `glasses3d/data/wp6v2/lab/package_008/inputs/lab.json` and its copied images. That package's only other
photos are black B1/B2/B3; all three are excluded. No model, old scene or old camera basis is selected for the fresh job.

Every source path below is under the absolute source directory
`C:/Users/Shay/PycharmProjects/lenses/automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/images/`:

| Source file | View | Wearer's side | SHA-256 |
|---|---|---|---|
| `reference-01.webp` | side | right | `e1788c4c8869a11963f262514b0c8d9954c5e4345456bf29e87fd10d6fa1ba1a` |
| `reference-02.webp` | angled | left | `5141b9783a543e16fb3cf8cf9d29d20cb158e8ded182ef23e352e8fa2c73652f` |
| `reference-03.png` | top | both | `44b21912def3f898071ea281b74dcfc27b9e394b81bc2d00c3189cad2df27305` |
| `reference-04.png` | front | both | `010633ef45943b5d5c9a532f9b0c55216ef4690cbebcd4bbfede50845bd316b6` |
| `reference-05.png` | side | left | `f63752815dba9f40a39442faee48c704c847fd40a41b53da4a9fa559762ef1ea` |
| `reference-06.png` | rear | both | `1bfe0852faf6513a409fdc041bebe9403da3dfc419baae1aaa637e2582a95e8f` |

Local evidence: `glasses3d/data/assembly/m5-prada-prep/{photo-audit.json,views.json,beige-photos-labelled.jpg}`.
The audit includes full absolute paths, image sizes and WP6 manifest SHA. The front-first labelled sheet was
visually verified. Originals are unchanged. `git check-ignore -v` confirms these local artifacts are ignored.

**Preliminary tool audit, not completion of item 3:** `temple_stage` describes single-material slab temple bodies,
one top/bottom outline, a vertical inner face and a faceted outer surface. This fits the beige Prada arm bodies
shown; metal hinges and branding remain distinct. Capture still needs eligible geometry and fitted cameras from
the fresh run. The specialist receives only the new job's photos, so there is no automatic black-photo import.
The parent defaults and initial invocation state max effort, $20 settled stop and $23 reserve. The child uses the
same guard/stop, but `agent/temple_lab/stage.py:256-258` calls remaining reserve a stage "allowance" in its initial
message without restating the settled-stop value there; shared tool footers do state it. Record this wording caveat
for the full item-3 input review; no budget enforcement bypass was found and no code was changed.

**Next:** receive the owner's description, create the beige-only fresh job with exact owner text and only sourced
owner specs, dump/check the actual brief, instructions, all tool descriptions and stage contract, then prepare the
max-effort $20 settled / $23 reserve request and estimate. The forecast's 60 replies are an estimate, not a turn
cap or a separately forecast parent-plus-specialist workload. Stop for explicit approval after showing that estimate.
Items 1-4 are not claimed complete. No full regression suite was run for this interrupted data/document-only prep;
the executed checks are 6/6 image decodes/dimensions, 6/6 source/WP6 hashes, both visual label audits and ignored-file
verification. Only this progress record is committed, with an explicit path; nothing pushed.

## Oakley run 4: owner acceptance and final review ($0, 2026-10-15)

Owner requested: "please review run 4. as far as i can see the model is perfect". Recorded the exact verdict
**"as far as i can see the model is perfect"** through `job verdict ... accept`, explicitly binding run 4's
delivery id and GLB SHA **`981314580a6b174546ebac0e42509b03a2e83044e4ae888aa729539277d198fb`**. Ledger entry
**`verdict-6cb0c4050a3b47b8bb09c0a7c419ecaa`**, at **07:58:09 UTC**, sequence 1, record SHA
`e21c1408d1c04fd1262324dd6f08024095c309bbc46f403fa1a2951e2143ee4c`, is in the job's
`reviews/verdicts.jsonl`; `read_verdicts` verified the chain, head receipt and immutable delivery binding.
This is the owner's visual acceptance of the exact delivered product, not an inferred verdict from passing tools.

**Review conclusion:** preserve run 4 unchanged as the accepted end-to-end Oakley reference. The fresh review of
the saved front, six-photo sheet, checker, +/-50-degree worn sides, all six seating-repair pairs and tool journals
supports the intended AR result. Eyes are visible through the shield, violet/green-cyan coating is present in the
export, all four brow openings remain, and the nose carrier/pads, clamps, emblems, complete forked temples,
earsocks and small lens markings are present. Specification and AR checks are clean. No evidence in this review
warrants changing the model the owner accepted; no model or pipeline edits were made.

**What the run demonstrated:** Astra used the new photo-background and same-export worn comparison eleven times,
then finish supplied the front pair again. It revised the optical material using the actual exported AR appearance
and invoked the coating helper eight times. This exercised M5-fix-3's single-sided export and M5-fix-4's paired
appearance evidence in a complete paid run, ending in owner acceptance. The report-only seating policy allowed
local repairs while preserving photographed vents/notches. These observations support the workflow on this product;
one accepted Oakley run does not establish performance on other products or prove an isolated causal effect of
any one change. `temple_stage` and photo printing were not exercised, so their assembled paid validation remains open.

**Run efficiency/reliability:** the accepted result cost **$10.611259**, below its **$15.285399** forecast and
**$20** settled stop, and finished through `finish`, not a turn or budget cutoff. Four camera fits consumed
**1380.608 s / 23 min 0.608 s**, **41.0%** of the **3364.701 s** run; each used Astra's selected four workers.
All eleven dedicated colour/worn comparisons together took **227.496 s / 3 min 47.496 s**. Total tool time was
**1703.673 s**; provider waiting **1366.107 s**; pre-request counting/budget overhead **290.167 s**, plus about
4.75 s startup/bookkeeping. The saved trace identifies fitting as the largest future speed investigation target,
without justifying any change to checks, capabilities or this accepted model.

Caching covered **6,103,979 / 6,305,036 cumulative input tokens (96.811%)**; there were **193,497 cache-written**,
**7,560 plain input**, and **40,259 output tokens**. Only the initial request lacked cache hits. Peak context
**193,623** remained below the 272,000-token pricing step. Largest primary tool JSON was **11,527 characters**
and Astra read six report pages, so the 12k paging policy worked. Two safe tool refusals recovered, with no
paid request failure, transcript/teardown error, unknown charge or heartbeat write failure; maximum heartbeat gap
was **2.187 s**. No new automation blocker was found. All 60 responses/receipts reconcile to settled spend.

**Keep the numerical limitations:** colour saturation/distribution and fixed-room highlights differ from the studio
photos; top/right outline correspondence remains imperfect. The final top mean discrepancy is **3.945 mm**
(IoU **.588766**) and right **1.501 mm** (IoU **.840142**), against front **.753 mm** (IoU **.944103**).
These combine model shape, camera calibration and temple pose/registration; current evidence cannot assign all
the error to either the model or camera. The appearance tool renders authored geometry without articulating the
temples. The last six-photo sheet uses **basis-03/c0014**, with exactly the final exported GLB bytes; finish's
front and the final numerical comparison use **basis-04/c0015**. Seating's **148 FAIL / 26 TRANSITION** remain
honest informational results. No gate, tolerance or diagnostic was weakened, and these reports do not overturn
the owner's visual acceptance. Dimensions and optical parameters are appearance estimates, not measured product data.

The previous run-closeout section retains the full receipt audit, costs, model identifiers, final camera table,
and six before/after repair paths. This review made **$0** of API calls. The only new records are the owner verdict
and this progress log; no new run is authorized or started.

## Assembly M5 Oakley run 4: approved fresh run, finished delivery (2026-10-15)

Started clean in the primary `C:\Users\Shay\PycharmProjects\lenses` checkout on `glasses3d` at
`179ac43 Add photo-camera AR colour comparisons with worn evidence`. Owner authorization, verbatim:

> start a fresh Oakley run 4 on job oakley-oo9208-v2-3e057bab, effort max, $20 settled stop and $23 maximum reserve.

- **Request and approval:** new request `20261015T062433-662a82`, expected **$15.285399**, historical growth
  range **$13.528285-$20.846819**, reported before launch. Max effort, **$20 settled stop / $23 maximum reserve**;
  approval recorded with `job approve` and the exact owner words. Request SHA
  `fdf0fff7de25215364a705ada532b7de126c437cb711db81107ff4dfd055d787`. Only this paid run was started.
- **Fresh start:** preserved the previous generated session/checkpoints/cameras/work/worker/heartbeat under
  `data/assembly/m5oakley4-prior-active/`, verifying **395 archived files** and **10 stable input/approval files**.
  Job definition, seed, photos and brief were unchanged. The offline seed check showed only the bridge marker,
  verified **c0000**, zero product objects and no conversation. Receipt: `m5oakley4-fresh-start.json`.
  The real run records `resume:false`, zero history items and all six original photos. Snapshot is clean
  `179ac43` with all **24 tools**, including `compare_look_to_photos`. Credentials were loaded only by the existing
  runner loader; no credential contents were read or printed by the coding agent.
- **Execution:** detached keeper PID **34184**, runner launcher PID **10476**, actual Python PID **34552**;
  UTC **06:25:41-07:21:45** (Jerusalem **09:25:41-10:21:45**), **3364.701 s / 56 min 4.7 s**. Exit **0**;
  no run error, teardown error or transcript error. Read-only monitoring sampled every 40 seconds and live updates
  continued throughout: `m5oakley4-monitor.jsonl`, `m5oakley4_monitor.{log,receipt.json,status.json}`; monitor exit 0.
  BLAS threads were one, detached throttling opt-out enabled, with no competing heavy test pool.
- **Result:** **spent $10.611259 (settled)** across **60 requests / 60 tools**, no continuation. Finished normally
  on **c0015**, with **$0 held / $0 unknown**. It did not hit a money stop. `finish` accepted, and the immutable
  delivery is `deliveries/20261015T062433-662a82-73e35056be8298367b381cf79e641bbe4d6e6c5f60897bdd0874f0e7fca7ad66/`.
  GLB SHA **`981314580a6b174546ebac0e42509b03a2e83044e4ae888aa729539277d198fb`**, **607,988 bytes**,
  **40 objects / 25,848 triangles**. Checkpoint fingerprint
  `721236f39ab8a2ff1662e8bef8396c7e55cec7737b745ba46de74f4e98624b58`.
- **Checks and limitations:** specification validation passes with no errors/warnings; final AR capture passes
  with no errors/warnings. Print is not required for the plain finish. Seating is a report and remains false:
  **148 required FAIL, 256 PASS, 26 TRANSITION / 430 required stations**; another **470** stations are visible by
  design. The dry-run grow operation refused concave notches, without mutation. Astra retained the notch silhouette
  and vents instead of filling them to force a pass. Finish's front lens background-response number is **0.168**
  (display-RGB diagnostic, not physical VLT or a gate). All appearance acceptance remains with the owner.
- **Astra's summary:** it built the complete double-curved shield, vented brow, retention pods, silver emblems,
  lens lettering, nose pads, hinged forked temples and rubber earsocks. It used a uniform thin-film coating to
  produce moving green/cyan and violet hues, with no painted colour patch. Eyes and the checker remain visible.
  It reports less saturated lens colour than the studio photos, localized room reflections, approximate lettering
  and pod transitions, and uncertain top/right-side correspondence. Scale is inferred (about 145 mm fixed front,
  159 mm open temple spread), not measured. Top contour discrepancy is about **3.95 mm**, versus **0.75 mm** front.
- **Owner review:** `job review` started a verified detached WMI server, PID **27320**, port **61179**, bound to
  the immutable GLB above with fixed lens environment 1. `review/current.json` contains the complete webcam URL;
  the browser panel was requested in Codex. The immutable front comparison is `look/photo-000-comparison.png`,
  combined comparison `look/comparison.png`, and AR contact sheet **`contact-sheets/01.jpg`** under the delivery.
  The six-photo sheet is `work/arview/call_6iTfyMnPU3adkT9fDEh2QaPZ-look/comparison.png`: labeled c0014 but bound
  to the identical final GLB SHA above (the c0015 checkpoint edit changed metadata, not the exported asset).
  The front sheet visibly retains all four brow openings and shows the eyes through the lens. Colour still
  differs from the photo; no owner acceptance is inferred from the technical finish.

**Independent final accounting/integrity audit:** all **60 archived responses**, event hashes, transport/SDK
receipts and budget usage rows agree. `JobLedger.scan(write=False)` reprices cleanly with no warnings or blocking
issues. Run spend is exactly **10,611,259 microUSD**. Peak input **193,623 tokens**; largest single hold
**$3.239488** on request 60. Every request began below the $20 settled stop; the largest settled-plus-held amount
was **$13.635033**, below $23. All ten stable input/approval files and the regenerated brief retain their hashes;
all **627** frozen implementation files and source-archive members match clean `179ac43`. `load_delivery` verifies
the immutable delivery. The expected post-run change is this progress entry. No accounting discrepancy was found.

**Independent final model/tool audit:** **11/11** explicit `compare_look_to_photos` calls succeeded, each with
the matching export's worn portrait, plus the front comparison at finish. The later checks include all six photos.
`lens_coating` was actually called **8 times** (tools 0003, 0010, 0017, 0020, 0022, 0033, 0036, 0044).
Final GLB: uniform **435 nm** film, film IOR **1.8**, iridescence factor **1**, transmission **0.94**, roughness
**0.10**, tint **[0.44, 0.30, 0.50]**, single-sided. Bulk optical IOR **6** is explicitly an effective appearance
approximation selected by Astra, not a measured glass property. **Zero `temple_stage` calls**; temples were
authored directly. Two tool refusals recovered: private `__doc__` access at 0009 (no mutation), and the concave
`seat_lens` dry run at 0043 (no mutation). No paid request failed.

Six frozen-front seating-repair pairs were returned (0027, 0029, 0033, 0041, 0044, 0049). Independent visual review
finds central vents remain open in all six, as do the outer vents; 0044 narrows the outermost ends. The diagonal
lens shading streaks introduced at 0027 were repaired at 0029. The final front/face/checker images show violet
edges and a green/cyan centre with visible eyes/checker. On white the colour is still less saturated and distributed
differently from the reference, with conspicuous localized fixed-room highlights. These are observations, not
owner acceptance.

Final **basis-04** all-product photo comparison (`cameras/basis-04/compares.jsonl`); all cameras are usable under
the existing lens-optional criterion, which does not certify likeness or metrology:

| Photo | IoU | Mean contour discrepancy, mm | p90, mm |
|---|---:|---:|---:|
| 01 front | .944103 | .7531 | 2.1235 |
| 02 angled | .926641 | 1.0264 | 2.9934 |
| 03 right | .840142 | 1.5006 | 3.2982 |
| 04 back | .862717 | 1.3579 | 3.7259 |
| 05 left | .899073 | .9187 | 2.6202 |
| 06 top | .588766 | 3.9450 | 13.7631 |

Each repair's native frozen front images are under the job's `work/seating_reports/<id>/{before,after}/front-white.png`
(also `front_native.png`), with no report warnings:

| Tool | Checkpoint pair | Report id |
|---|---|---|
| 0027 | c0007 -> c0008 | `84751447e2231c4400096a81` |
| 0029 | c0008 -> c0009 | `fda71c62c35bdebc5fe54abd` |
| 0033 | c0009 -> c0010 | `0989fe6e2781ca3b7f9a4b14` |
| 0041 | c0011 -> c0012 | `4e1a4be0009e84adf4baf124` |
| 0044 | c0012 -> c0013 | `87df0ee80f6cb99556bfa144` |
| 0049 | c0013 -> c0014 | `5ff9999e6f953009ecbb23ff` |

No implementation, brief, job definition or photos were changed for this run. Only this progress record is
committed; no new regression suite is needed for a log-only change. The actual run's validation, capture and
immutable delivery are the verification. Nothing pushed.

## Assembly M5-fix-4: photo-camera AR colour comparisons ($0, 2026-10-15)

Started clean in the primary `C:\Users\Shay\PycharmProjects\lenses` checkout on `glasses3d` at
`44c4165 ASSEMBLY M5-fix-4: every colour check also shows the worn view (owner)`.
The owner's verdict and requirement, verbatim:

> the color arent close
>
> the lens is transparent after the fix which is good!
>
> im just making sure that when astra will edit the lenses that it will also look at the glasses on a person (ar view) in addition to the white background view

These close the pending M5-fix-3 colour question: transparency improved; colour was rejected. They do not authorize
a paid run or edits to the product. No material, colour, coating or geometry is changed during this tool work.

**Implementation and technical choices:**

- `compare_look_to_photos` is registered in the main session (24 tools). It exports the current head, reads the
  frozen basis without fitting it, defaults to every usable camera, accepts `refs`/`basis`, and orders front first.
  Each photo's outermost border pixels are counted once: median RGB is the AR background; the 95th-percentile
  channel distance and fraction within five levels describe border uniformity, with no rejection threshold.
- Camera conversion preserves yaw/pitch/roll, off-axis eye offsets, principal-point pixel centres and authored
  metres. Photos use the same uniform resampling grid as the camera, capped at 1280 pixels. Perspective at or below
  1e-9 is explicitly reported as a distant pinhole at 10,000 frame extents, with its approximation bound. No bridge
  offset or automatic face scale is added to explicit-camera geometry. Frozen temple openings are reported;
  this appearance tool draws the authored geometry without articulating it.
- One sheet pairs each photo and AR drawing at the same crop, with a paired lens close-up and a large worn-front
  portrait from the **same export**. All native per-photo sheets, renders, part IDs, cameras, the worn capture's
  report and its 35 native images remain hash-bound. Lens/frame medians and CIE L* use the same part-ID regions
  in both images, eroded three pixels. These are information, not appearance gates; registration limitations and
  fixed-room highlights are stated. A failed/missing worn capture yields unavailable evidence, never a photo-only
  success. The compact sheet reserves 300 pixels of height for the portrait even with many photo rows.
- The new instruction line requires both photo-background colour match and worn-face likeness/visible eyes after
  every lens/frame colour or material edit. Existing guidance and the owner's requirements remain; instructions
  total **7,991 characters**, below 8,000. `finish` adds the frozen front comparison and reuses its fresh worn AR
  capture. Missing comparison evidence remains informational. Delivery preserves separate AR receipts, comparison
  assets and the actual mixed image emitted to Astra. Regression tests reload delivery after source-image removal.
- A temporary white `silhouette` override accompanies explicit-camera beauty and part IDs. It supplies scalar
  coverage independent of the part palette, restores authored materials after drawing, and does not affect beauty
  or worn modes. The GPU regression checks identical occupied support to the existing part-ID pass.

**Evidence and verification:** both source GLBs are copied unchanged: accepted SHA
`2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688`; run-3 re-export SHA
`32e86e30e206e6c7243e8a5e6ebd85a60b5b40bbc450b53726340795c019e716`.
Six-photo and individual front sheets for both are under `data/assembly/m5fix4-d/{accepted,run3-reexport}/work/
arview/proof-look/` (`comparison.png`, `photo-000-comparison.png`). Measured photo backgrounds in reference order
are `#ffffff`, `#ffffff`, `#e4e3e3`, `#fefefe`, `#fdfdfd`, `#fefefe`. The sheets show the colour disagreement without
editing it: the photos have light violet/green lenses; both models remain greyer/darker, with visible eyes when worn.
Front lens median RGB / L*: accepted photo **[152,149,163] / 62.89**, drawing **[78,95,88] / 38.77**;
run-3 mask photo **[151,151,162] / 63.02**, drawing **[118,130,129] / 53.55**. Masks differ between models and
the statistics are contextual image evidence, not physical transmission or a colour acceptance rule.

`tests.proofs.assembly_m1` now actually invokes the new tool before finish: **12 scripted requests**, delivered,
**97.609 s**, **$0**, clean transport/tool/image/budget receipts, **35 verified native AR images**, front comparison
and same-export worn evidence in the delivery. Synthetic job: `synthetic-assembly-proof-75f3eaf3`. Focused SDK,
instruction and delivery checks passed 213, then the final composed evidence tests passed with **27** look tests
(overlapping counts). AR **498 unit + 106 QA** tests passed; TypeScript exited 0.

**Camera proof instrumentation:** initial attempts are retained in `m5fix4-b`/`m5fix4-c`. Thresholding coloured
part IDs at 24 against Blender alpha at 127 compared different edge coverage. A uniform white diagnostic fixed
that ambiguity, but native AR's four samples encode only 0/64/128/191/255: treating every 128 tie as inside
slightly dilates silhouettes and fills tiny notches. Its native IoUs were .998466/.998317/.997351/.997430/
.997998/.994520; reference-03's largest binary edge distance was 2.236 pixels. These were not likeness failures
or evidence to edit the cameras. Projected vertices agree algebraically to <7e-13 pixels; soft-coverage centroid
differences were <.012 pixels, and almost all extra pixels were exactly the half-coverage ties.

The corrected proof integrates the same native pixel areas on a fixed **4x4 subpixel grid in both renderers**.
AR tiles stay within the 1280 viewport limit; Blender uses the equivalent higher-resolution camera. The original
IoU >= .995 / maximum edge <=2 native-pixel limits remain unchanged. An independent synthetic diagonal-edge
witness encloses .405/.595 of a pixel but yields .5/.5 with four samples; the fixed integration distinguishes
.4375/.5625. Independent read-only review checked the projection and tile math. Saved evidence checks pass for
all **96 tiles**, with identical part-ID/white-mask occupied support and exactly matching high-resolution Blender
projection scaling. Eight-bit coverage quantization remains bounded by half a level; no seed/threshold search,
camera refit, photograph rejection or product edit was used. `m5fix4-d/coverage-integrity.json` records the witness.

**Final camera proof passes** (`m5fix4-d/proof.json`):

| Frozen reference | IoU | Largest edge offset, native px | Actual Blender projection error, px |
|---|---:|---:|---:|
| 01 front | .999888 | 1.000 | .000134 |
| 02 angled | .999851 | 1.000 | .000190 |
| 03 right | .999803 | 1.414 | .000065 |
| 04 back | .999834 | 1.000 | .000338 |
| 05 left | .999813 | 1.000 | .000091 |
| 06 top | .999511 | 1.000 | .000273 |

The matrix check uses Blender's actual camera projection, independently of the host algebra, at the existing
.01-pixel float32 allowance. All input GLB/basis hashes remain unchanged. Initial proof attempts and their failed
instrument measurements remain available. A final independent read-only review rehashed all 18 recorded inputs
with zero mismatches. The real Oakley cameras are perspective with zero eye offsets; 18 separate analytical cases
cover orthographic approximation, nonzero eye offsets, roll and oblique angles. This proves conversion, not the
frozen cameras' likeness to the photos. The final proof took **1345.375 s** because I overlapped its large GPU
renders with the full suite; the first two high-resolution renders suffered severe contention. Subsequent checks
are sequential. This was a scheduling mistake, not a reason to change a quality or timing threshold.

The initial full Python run (`-n 8 --dist loadfile`) finished in **1337.09 s**: **3357 passed, 27 failed, 2 skipped**.
One assertion still expected the old composite finish timeout: updated the contract to 1090 s (existing stages
plus the added capture and SDK margin), and pinned the new tool's 760 s. The other failures comprise the known
launcher/QoS inheritance interaction and GPU timeouts/cascades in dome, seeing, lighting and the real temple-lab
test. Sequential reruns of all affected modules with the launcher's descendant watcher disabled finished in
**285.90 s: 115 passed, 1 failed**. Every timeout/cascade recovered, including seeing, lighting and the real
temple-lab integration. The remaining failure is exactly the already-documented `test_photo_v1_pixels_are_unchanged`:
**199 reference-01/native pixels** versus its unchanged **114.24-pixel allowance**, matching the original
84b5bf5 baseline reproduction recorded under M5-fix-3. No golden, threshold, quality rule or existing render timing
assertion was changed. This pre-existing issue stays open; the full suite is not claimed completely green.

Photoprint ran separately with `-n 2 --dist loadfile` and **31,684,460 KB** of free commit before launch:
**314 passed in 209.80 s**, exit 0. Across the main suite, its targeted reruns and photoprint, the distinct final
results are **3697 passed, 1 pre-existing failure, 2 skipped** (overlapping rerun/focused counts are not added).
M5-fix-4 items 1-5 are complete at **$0**, with the existing pixel-baseline failure explicitly open. The front-first
comparison sheets and all six views are retained for owner review; no appearance acceptance is inferred.
Detached logs and PID/time/argv/exit receipts are named `data/assembly/m5fix4_*`; all launches use BLAS=1 and
EcoQoS opt-out. No paid API call or run request was made. Product assets remain unchanged; only the explicit
implementation, tests and this progress record are committed on `glasses3d`, without pushing.

## Assembly M5-fix-3: transparent solids and visible lens evidence ($0, 2026-10-15)

Started in the primary `C:\Users\Shay\PycharmProjects\lenses` checkout, clean on `glasses3d` at
`84b5bf5 ASSEMBLY M5-fix-3 item 1: apply the single-sided rule to every see-through closed solid`.
Read the amended M5-fix-3 and executed its items in order. No paid API call or run request was made.

**Implementation and technical choices:**

- The exporter examines whole evaluated meshes after transform baking, before glTF splits them into material,
  normal and UV primitives. Every face-connected shell must be a closed 2-manifold, consistently wound and have
  positive signed volume. Volume uses Blender's actual loop triangles in float64 around a local origin. Wire-only
  edges and loose vertices are ignored because they are not exported; a pinched nonmanifold vertex is not ignored.
  This is an inspection of a temporary mesh, with no geometry repair or authored-material edit.
- For **every exported transmission factor > 0**, including crystal frame parts, `doubleSided` becomes false only
  when every used mesh qualifies. Open, inward, inconsistently wound and zero-volume uses retain their sidedness
  with one plain note naming the material and reason. Opaque materials are untouched. Unused slots do not count;
  existing role-specific material copies are assessed independently. The export receipt's
  `single_sided_materials` records each change and its mesh evidence.
- `arview_capture`, also used by `finish` and draft finalization, keeps seven standard skin poses and both close
  +/-50-degree worn views. It now includes the existing pinned face-a portrait and one front checker. The new
  optional CLI `--lens-evidence` adds skin/dark pairs at all nine synthetic poses; its default invocation changes
  no pre-existing CLI capture mode. The normal tool capture has **35 native images**, all hash-bound in the raw
  report and copied into delivery. The single **1024x1012** sheet prioritizes condition-a eye detail, then checker,
  worn sides and standard poses. The normal image policy need not shrink it again.
- Authored scale, fixed lens environment **1**, posed/worn visible-head and hair-off behavior remain. The added
  portrait preserves the CLI's existing face fixture, hair-mask and lighting-condition contract; condition a is
  the runtime-lighting portrait shown on the sheet. No viewer material/shader, colour, coating or geometry changed.
- `lens_background_response` repeats the earlier review's method: decode each view's lens palette and tolerance,
  erode three pixels with a 7x7 square, then divide skin-minus-dark display RGB by **[160,129,107]**. All nine
  synthetic views are reported, with source filenames/hashes. Missing pairs/interior pixels are unavailable, never
  invented zero. Portraits have no controlled pair and are judged visually. Values are not clipped and have no
  passing threshold; they are not physical transmission or VLT. A short priority-visible summary retains the
  per-view numbers when the full tool result is paged. Tests verify zero response still permits finished delivery.

**Run-3 copy proof:** `data/assembly/m5fix3-copy-a/proof.json` (30.719 s, $0, zero model requests).
The copy job is `jobs/run-3-re-export-proof-copy-269cb90e` under that directory. Original c0007 SHA remains
`b506e111b40640c3c68746dcfb798fe28a450d55e422d6f8e97c64146cbdd383`; the original job/photos and delivered GLB
retain their hashes. Re-export SHA is **`32e86e30e206e6c7243e8a5e6ebd85a60b5b40bbc450b53726340795c019e716`**.

- Exact GLB comparison: **BIN byte-identical; JSON differs only at materials 6 and 7 `doubleSided`: true -> false**.
  These are `Emerald cyan violet interference shield` and `Polished smoke lens edge`, both on the same closed
  shield. The amended general rule includes the transmissive edge; keeping material 7 double-sided would be a
  special case. Every other optical/material/geometry field is identical. All nine before/after part-ID images
  have identical hashes. Specification passes; existing seating and temple warnings remain reports.
- Fresh before/after captures each produced 35 images and passed. Front background response is
  **0.0159085885 -> 0.1201689863 (7.55x)**, agreeing with the prior isolated single-sided diagnostic's own-mask
  **0.1201514668**. The earlier 0.120977 used an intersection with the accepted model's different mask. Eyes are
  visibly restored. Native crops are in the copy job's `work/arview/{before,after}/portrait__a__eyes.png`;
  labelled unchanged pixels are in `data/assembly/m5fix3-copy-a/before-after-face.png`.
- The accepted-Oakley preservation proof also passes: `data/assembly/m5fix3-worn-preserved/proof.json`.
  All **14 original standard panels are byte-identical** to the seven-pose CLI path, and both worn sides retain
  the visible head/hair-off contract. This proof was updated to assert the additive 35-image layout, retaining
  the old exact-pixel preservation assertions.

**Verification:** focused checks passed **95**, then **141** (overlapping counts). Synthetic regressions cover
closed lenses/crystal frames, open/inward/opaque controls, mixed material uses, disconnected shells, pinched
vertices, inconsistent winding, nonplanar faces, mirrored/nonuniform transforms, unused slots and loose wires.
`tests.proofs.assembly_m1` passed in **108.422 s**, **11 scripted requests**, clean receipts and **35 verified AR
images** in the finished delivery, actual **$0**. AR **498 unit + 106 QA** tests pass and TypeScript exits 0. An
initial AR unit assertion omitted the new additive `lensEvidence: false` default; corrected its expected object,
with the complete unit suite passing afterwards. Full Python main (`-n 8 --dist loadfile`) finished in **676.64 s**:
**3355 passed, 2 failed, 2 skipped**. Photoprint (`-n 2 --dist loadfile`) passed **314** in **209.83 s**.
The two main-suite failures were checked in isolation, with the launcher's descendant QoS watcher disabled:
**22 passed, 1 failed**. The process opt-out test then passed; the frozen `photo_v1` render still failed.

**Open, pre-existing full-suite failure:** `tests/test_render_dome.py::test_photo_v1_pixels_are_unchanged`.
Reference-01/native has **199 differing pixels** alone (195 in the loaded full run), above its unchanged
**114.24-pixel allowance**. To establish whether this change introduced it, extracted the Blender runtime at
the original **84b5bf5** into an ignored scratch directory using `git archive`, pointed only the diagnostic host's
worker path at it, and ran the exact same unchanged test. The original runtime also fails with **199 pixels**,
max difference **4/255**, mean **0.0003961/255**. Its source scene matches the original pinned SHA; no source,
golden, threshold or test was changed. This is an existing frozen-render discrepancy, not a green full suite.
Evidence: `data/assembly/m5fix3-render-baseline/proof.json` and `m5fix3_render_baseline.{log,status.json,receipt.json}`.
Across the full runs and isolated QoS recovery: **3670 distinct Python tests passed, 1 still failing, 2 skipped**.
The M5-fix-3 synthetic, copy-job, M1 and accepted-view preservation proofs all pass. Repairing the unrelated
frozen renderer/golden is left open; WP6 has not been reopened and no gate was weakened.
Long checks use detached logs and PID/argv/time/exit receipts under `data/assembly/m5fix3_*`, BLAS=1 and the
existing EcoQoS opt-out. No quality threshold was loosened. Independent read-only exporter/capture reviews found
no blocker; their topology and paging observations were covered before the full-suite run.

**Owner review:** served detached on **127.0.0.1:58103**, PID **30272** (WMI), with the exact label
**"run 3 re-exported with the lens fix, not a new delivery"**. Live verification confirms the served model hash,
authored scale and fixed lens environment 1. `data/assembly/m5fix3-copy-a/review.json` holds the verified webcam URL
and process receipt; opening it in the Codex browser panel was queued. It is the same Astra model and does not
replace the immutable delivery. The before/after front-face image is `m5fix3-copy-a/before-after-face.png`.
The owner's subsequent verdict is recorded verbatim under M5-fix-4 above: colour rejected, transparency improved.

## Assembly M5 Oakley run 3: approved fresh run, finished delivery (2026-10-14)

Started clean in the primary `C:\Users\Shay\PycharmProjects\lenses` checkout on `glasses3d` at
`6570b88 ASSEMBLY M5-fix-2: Claude's review; item 6 proof criterion withdrawn, tool delivered`.
Read ASSEMBLY's M5-fix-2 **Status 2026-10-14** and accepted the item 6 closeout. No further calibration research,
source-code change, job-definition change, brief edit or photo edit was made before or during this run.

- **Authorization and estimate:** correct job `oakley-oo9208-v2-3e057bab`; fresh request
  `20261014T142845-c99f84`, effort **max**, **$20 settled stop / $23 maximum reserve**. New estimate
  **$15.251285** (growth range **$13.494181-$20.808875**) was below the owner's $20 condition. The estimate uses
  the existing historical-growth model, not assumed savings from paged reports. Approved through `job approve`
  with the owner's latest message verbatim, retained in `data/assembly/m5oakley3-owner-approval.txt`.
  Approval SHA `c95d218fe7c9b2cb54e7ad0e651e9a35e2e2a86bcbd299c8115961afc9660a85`;
  request SHA `ee42f5840d8cfd273332c2f5e59b9d5fd4fe6b6f2e5bad55ba69e27111d6c260`.
- **Fresh start and monitoring:** preserved the previous generated session/checkpoints/cameras/work/worker state
  in `data/assembly/m5oakley3-prior-active/`, verifying **1,143 archived files**. The approved job inputs stayed
  unchanged; the new scene began with only the bridge marker, zero product objects and zero conversation items.
  Receipt: `data/assembly/m5oakley3-fresh-start.json`. Started detached without `--resume`; keeper PID 23576,
  runner PID 42312. Run UTC **14:31:10-15:10:19** (Jerusalem **17:31:10-18:10:19**), **2,348.965 s**;
  wrapper exit **0**. A separate read-only monitor sampled every 40 s, with live owner updates:
  `data/assembly/m5oakley3-monitor.jsonl`, `m5oakley3_monitor.{log,receipt.json,status.json}`. It exited 0.
- **Money and result:** **spent $6.478750 (settled)** across **43 requests**; final held **$0**, unknown **$0**.
  The run ended `completed` after `finish` accepted c0007, not at a budget stop. No continuation or second run.
  All 43 archived response usages reprice to exactly **6,478,750 microUSD**; receipt hashes/IDs/usages, budget
  rows, job-ledger budget hash, row hashes and closed-run anchors agree. Peak input **132,708 tokens** and
  largest single hold **$2.478050** occurred on request 43. No open requests or accounting discrepancies.
  Evidence under `data/jobs/oakley-oo9208-v2-3e057bab/runs/20261014T142845-c99f84/`:
  `budget.json`, `transport/`, `result.json`, `runner-status.json`, `events.jsonl`, `transcript.md`, `tools/`.
- **Immutable delivery:** status `delivered`, `finished: true`, checkpoint **c0007**, fingerprint
  `fa6c892876c25a6ed7ec7c921a35c7d1cc7ad8a8f31fc3b300f61ce3ead36daa`.
  Delivery folder under the job:
  `deliveries/20261014T142845-c99f84-f4ee4a39a933a46eaf4c88c596439c353a047f67848ebe5767b531b9188071d1/`.
  GLB SHA **`1f5bad10f75c3a03410158a0ff11b03d7133591da1a4cd32895e5dafa9f72603`**;
  **989,044 bytes, 49,568 triangles, 43 objects**. Specification validation passes with no errors or warnings.
  Final AR capture succeeds with no errors and eight warnings: five temple-cut warnings (including left/right
  close worn sides **9.4 / 6.3 mm**) and three part-visibility warnings. Seating is honestly **false** with
  **70 failing stations**; it remains a report under the owner's policy, not a finish requirement.
- **Lens colour:** Astra used `g3d.lens_coating` successfully in tools **0003, 0016, 0019, 0022 and 0032**.
  It inspected the initially pale/clear AR result, tried different film settings and substrate tint, and chose
  the stronger green/violet trial. Final GLB directly confirms uniform **515 nm** film, film IOR **1.50**,
  substrate IOR **1.52**, iridescence factor **1**, tint **[0.50, 0.52, 0.51]**, metallic **0.60**,
  transmission factor **0.90**, roughness **0.095**. The transmission factor is not measured light transmission.
  The final GLB has **no images or textures**: the colour comes from the optical material. It also increased
  vertical lens curvature. Failed inspection 0002 and rolled-back material call 0015 are not counted as changes.
  The final AR sheet shows green/cyan across the lens and violet/pink toward the edges/highlights; Astra says it
  remains darker and less pastel than the white-background photos under the fixed AR lighting.
- **Temple stage and limitations:** **zero `temple_stage` calls** among 43 tools. Astra modeled the branching,
  multi-material temples directly. It used `photo_read` twice and the existing off-axis fitting option once;
  its summary explicitly treats the cross-view disagreements as uncertainty, not certified dimensions.
  It reports approximate top/rear temple projections, estimated scale, preserved four brow vents, and remaining
  seating/AR warnings. `seat_lens` dry-run 0035 refused concave growth; Astra's support edit 0037 retained the
  diagnostic rather than filling all gaps. Source code and tool policy were unchanged.
- **Owner comparison:** new immutable sheet is the delivery's `contact-sheets/01.jpg`
  (SHA `49af21e15f9605e83eedbd294f4376e307ecc153e2a8f9bcc9d834160b02910b`). The accepted Oakley comparison is
  `data/assembly/m5fix2-worn-b/work/arview/worn-proof/contact-sheet.jpg`
  (SHA `077d68b85135e2aaaccaadc51b0704f0269c98fd388ffedbaa489a74f537b08f`), bound to accepted model SHA
  `2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688`.
  Both sheets use the current fixed-scale layout and close +/-50-degree views with visible head and hair off.
  No owner acceptance is inferred from technical finish. No webcam review server was requested for this run.
- **Final integrity audit:** all **25** immutable delivery files and the manifest match their hashes;
  `load_delivery` validates the snapshot and GLB/validation/AR bindings. All six photos, job definition, seed,
  approval/request and brief retain their pre-run hashes (10 preserved immutable file entries plus the brief).
  The job ledger changed only to record the authorized run. All **617** frozen implementation files match
  their receipt, and current files match apart from this expected post-run progress edit. No unexplained mismatch.

Only this progress log is committed for run 3. No implementation changed, so no new regression suite was run;
the actual run's validation, AR capture, finish and delivery evidence above are the verification. Nothing pushed.

## Oakley run 3: owner colour/transparency review ($0, 2026-10-14)

After the run, the owner requested webcam AR. `job review` bound the immutable run-3 delivery and verified its
served GLB SHA; detached server PID **29388**, port **64023**, created **16:01:52 UTC**. Binding is in the job's
`review/current.json`. The owner then reported: **"the color of the lenses isnt right (its close tho) and theyre
have no transparency"** and requested a run/model review. This is appearance feedback, not visual acceptance.
Review began clean on `glasses3d` at `3d043ed`. No paid call, implementation edit, delivered-model change,
job/brief/photo change, calibration research or replacement delivery was made.

**Verdict:** the owner's near-opacity observation is reproduced. The strongest isolated cause is the exported
closed shield's **double-sided rendering**, which causes repeated attenuation in Three's transmission input
on this GPU. The `transmissionFactor: 0.90` value was present and exported correctly; it did not establish
visible transparency. Technical finish and the earlier flat-background AR sheets did not verify eye visibility.

### Controlled evidence (diagnostic copy only)

Evidence: `data/assembly/m5oakley3-review/transparency/`. Three sequential hardware-GPU captures used the same
AR renderer, authored scale, lens environment **1**, seven poses, skin/dark/checker backgrounds and the same
pinned face-a portrait. **All three exited 0; 99 images, 22.234 s, $0.** All image hashes and original GLB hashes
were verified. Existing warnings remain in each report. The original accepted Oakley and original delivery
were unchanged. The sole experimental copy is explicitly named `DIAGNOSTIC_ONLY_run3_material6_single_sided.glb`;
it changes only `/materials/6/doubleSided` from true to false. All other parsed JSON and every binary chunk are
identical; original versus diagnostic part-ID images are pixel-identical in all seven poses.

Across **59,767 shared front-lens interior pixels**, mean display-RGB background-response gain was:

| Model | Background-response gain |
|---|---:|
| Owner-accepted Oakley | 0.133886 |
| Delivered run 3 | 0.016032 |
| Diagnostic copy, only single-sided shield | 0.120977 |

Run 3's response is about **88% weaker** than the accepted model; the one-field diagnostic increases it **7.55x**.
All seven poses show the same direction: run 3 **0.0130-0.0175**, diagnostic **0.1013-0.1314**. The response is
nonzero, so the technical description is **nearly opaque**, not zero transmission. These are rendered display-RGB
response measurements, **not physical light-transmission/VLT percentages or a new gate**. Analysis uses each
reported lens palette, a three-pixel erosion and all poses, with both shared-mask and own-mask results retained.
Portraits visibly reproduce hidden eyes in run 3 and their return in the diagnostic; they are fixed test-face
captures, not recordings of the owner's webcam.

Receipts and method: `experiment.json`, `capture.status.json`, `capture.log`, `analysis.json`, `analyse.py`,
`gpu-extension-probe.json`. Visuals: `portrait-eyes-comparison.png` and `front-checker-comparison.png`;
unaltered native crops remain at `{accepted,run3,diagnostic_single_sided}/portrait__a__eyes.png`.

### Cause and why the run missed it

- The actual reviewer uses **glasses3d/arview**. Its native material path preserves the GLB's PBR values
  (`arview/src/eyewear/optical-material.ts:277`, `arview/src/render/renderer.ts:938`). Both models contain one
  closed shield shell, not duplicate lenses. Run 3 has a separate rim material; the diagnostic leaves it untouched.
- Run 3's main lens is **doubleSided true**, has no volume extension, and Astra never enabled backface culling.
  The accepted lens is **doubleSided false**, with a **0.00155 m** volume thickness. Blender 5.2's installed glTF
  exporter (`blender/exp/material/materials.py:271-280`) forces single-sided export for volume materials; otherwise
  it follows `use_backface_culling`. This is authored/default export state, not dropped transmission or corruption.
- The installed Three 0.185.1 `WebGLRenderer.js:2050-2085` draws backs of double-sided transmissive materials into
  the transmission input when `WEBGL_multisampled_render_to_texture` is absent. A separate context using the same
  Chromium/ANGLE D3D11 configuration and Arc 140T GPU confirms that extension is absent. The controlled capture
  isolates the resulting behaviour. The main front surface then samples an image already attenuated by the back.
- The shader's per-surface camera coefficient also contains base colour, `(1 - metallic)` and Fresnel terms.
  Before Fresnel, run 3's **0.90 transmission / 0.60 metallic / [0.50,0.52,0.51] tint** gives
  **[0.1800,0.1872,0.1836]**, not 0.90. The accepted model's **1.00 / 0.72 / [0.35,0.33,0.35]** gives
  **[0.0980,0.0924,0.0980]** per surface; it is clearer despite the smaller single-surface coefficient because it
  avoids the extra pass. Alpha 1 / OPAQUE is not itself a defect: Three selects transmissive materials independently
  of alpha blending. These coefficients are not webcam or physical transmittance measurements.
- Astra used the coating helper successfully five times. It tried a paler c0005 recipe, then explicitly selected
  stronger green/violet in code call **0032:59-61**. It simultaneously changed lens curvature, temple geometry and
  material settings, so that comparison did not isolate which change improved colour. The final film is **515 nm /
  IOR 1.50** versus accepted **480 nm / 1.65**. Both material settings and curvature affect the colour. The fixed
  room lighting also differs from the photos; no colour-calibrated physical match is established. The one-field
  sidedness experiment already changes visible colour by restoring the transmitted background.
- The automation's AR wrapper hardcodes **skin background and `--no-portrait`**
  (`agent/tools/arview.py:119-121`). Its five standalone captures plus finish showed reflections over uniform
  backgrounds and close side views, without eye detail. The CLI already supports portraits and checker backgrounds,
  but the general Astra tool exposes no such choice. The prompt did explicitly require see-through lenses
  (`agent/instructions.py:61-62`); the missing visual evidence was not a missing owner instruction.
- Run 3 ended through accepted `finish` at **$6.478750**, with **$13.521250** still below the $20 settled stop.
  It was not forced to finish by budget or a runtime fault. The summary acknowledged darker lenses and approximate
  fit. Structural validity and completed AR capture allowed finish; neither certified photographic likeness.

### Remaining model/run findings

- The front is recognizable and much closer than the top. Latest frozen-basis photo overlap (c0006) was
  **0.9487 front, 0.8724 side, 0.5359 top**, versus **0.9523 / 0.8694 / 0.5139** before the revision. The side
  comparison shows boxier latch pods and less integrated shoulder/arm transitions; top arm curvature and projected
  interior remain approximate. These figures mix camera/model disagreement and are not pure geometry errors.
- Astra made an off-axis basis-02 fit but reused basis-01 for its subsequent photo measurements and final
  comparison. Its uncertainty remained unresolved; this review does not reopen calibration research.
- The final support repair retained the four openings; the frozen before/after report shows no repeated central
  vent loss. Seating improved from 240 to **70 failing stations**, retained as advisory under the owner's policy.
  Evidence: `work/seating_reports/10ecd3df6d9d379419bd790f/{before,after}/front-white.png` in the job.
- Final delivery still records five temple-cut warnings (up to **9.4 mm**, otherwise **6.3 mm**) and three
  part-visibility warnings, including the long central brow receiving temple treatment in some regions.
  No `temple_stage` call occurred. Accounting, checkpoint verification and immutable delivery integrity passed.

**Recommended next work, not implemented:** address the general closed-lens sidedness/transmission interaction
with independent $0 cases; give Astra detailed portrait/checker evidence so it can judge visibility, keeping the
owner's fixed reflection default and existing capabilities. Reassess the remaining hue/curvature mismatch only
after transmission renders correctly. Do not treat the diagnostic copy as a new product or tune a coating to
compensate for repeated attenuation. No new gate, budget change or paid run is authorized by this review.

## Assembly M5-fix-2: money, paged reports, measuring and worn views ($0, 2026-10-14)

**Closeout (`6570b88`, subsequent to the implementation notes below):** item 6 is delivered and closed; Claude
withdrew the pair-agreement criterion because it tests fitted-camera agreement, not the measuring tool's
correctness. Calibration research stops. The following failed-proof statements are historical evidence from
before that review, not an open task or an instruction to continue calibration.

Started in the primary `C:\Users\Shay\PycharmProjects\lenses` checkout, clean on `glasses3d` at
`a46387f ASSEMBLY M5-fix-2 item 7: close worn side views at +/-50 deg with a visible head`.
The owner authorized items 1-7 at **$0**, retaining every Astra capability and leaving money decisions to the
owner. No paid request, approval, job run or product-geometry edit was made. Items 1-5 and 7 are implemented;
item 6 is implemented but its real-photo agreement proof **fails and remains open**, as detailed below.

- **1 — Owner chooses money:** removed `budget_usd` from `temple_stage`. The stage receives the remaining
  owner-authorized reserve room and the same global settled stop; open or unknown holds still refuse entry.
  No default allowance, higher cap, tariff change or weaker reservation comparison was introduced.
- **2 — Bounded tool text, lossless reports:** main and facet results now have an aggregate **12,000-character**
  text limit, including their footer and error paths. Oversized records are saved with immutable content hashes;
  the inline summary gives counts and worst/failing rows first. General `read_report` supports JSON pointers,
  filtering and paging, including a single oversized row or string. All original data and images remain accessible;
  journals and finish evidence retain the complete records. Existing tools remain registered.
  Final offline replay: `data/assembly/m5fix2-reports-b/proof.json` and
  `m5fix2_reports_final.{log,receipt.json}` (exit 0). It covers **80 saved requests, three histories and 162
  hashed source files**, preserving images/order and observing zero prefix edits. Observed original peak input was
  **284,724 tokens**, largest hold **$8.346900**. The capped-history replay estimates **87,477 tokens** peak and
  **$1.912663** largest hold; conservative token-density sensitivity gives **133,108 / $2.483050**, still below
  the long-context step. Adding 12,000 header characters gives **92,517 / $1.975663**. These are offline estimates
  anchored to saved provider counts, not a new provider token count or a guarantee about future model choices.
  The replay isolates the cap policy, counts the reader schema and discloses cumulative adjustments; it does not
  simulate future paging requests. Nine oversized outputs are bounded; the 848-row seating report goes from
  **270,795 to 6,052 characters**, preserving all rows and its 38 FAIL rows in the full record. Maximum emitted
  text is **11,981 characters**. Original receipts reprice to **$15.468835**; this replay costs **$0**.
- **3 — Specialist scope is explicit:** temple, seating and print descriptions state supported constructions and
  actual refusal conditions. The temple description includes the existing 3.2 mm/4 mm/8 mm hinge heuristics,
  20,000-vertex and 15 mm limits, without telling Astra to distort geometry to fit them. Instructions tell Astra to
  compare each tool's scope with the photos. No tool or modeling route was removed.
- **4 — Separate spend and hold:** statuses, tool results, CLI and delivery reports say `spent $X (settled)` and
  `held for the request in flight $Y (released after it settles)`. Unknown charges are stated separately and remain
  reserved until reconciled. The owner-facing combined committed figure is removed; internal accounting is
  unchanged. Formatting only recognizes numeric money summaries, so arbitrary Blender/tool dictionaries with
  similarly named properties retain their content.
- **5 — Owner-authorized seating policy:** seating is still measured for every frame, returned in full/paged
  reports and preserved in immutable deliveries. A real `ok: false` report no longer blocks finish; missing or
  malformed reports still refuse, as do invalid specification/print evidence or mismatched export/AR identities.
  Astra is instructed to weigh the report against photographic features and preserve openings during repairs.
- **6 — General `photo_read`, unresolved real proof:** the main registry can read named corners or traced edge
  crossings in 2-4 photos using the frozen job basis, returning neutral-world XYZ, conditional covariance,
  residuals, pair disagreement, pick sheets and provenance. Explicit temple side applies that side's frozen pose;
  nothing is mirrored. It retains the facet method's 1 px camera floor, separate 0.3 degree sensitivity and
  condition >20 refusal. Camera/pose lineage is verified against the frozen source, including malformed or
  partially declared poses; no calibration is adjusted by this tool.
  Initial real proof: `data/assembly/m5fix2-photo-read-proof-c/proof.json` and
  `m5fix2_photo_read_final.{log,receipt.json}` (**exit 2, $0**). All four readings fail the required pair agreement.
  Maximum pair distances are **11.02 / 9.90 mm** for the two hinge pods and **16.95 / 11.31 mm** for the tips.
  The same positive-X hinge from front+side versus front+top differs by **9.34 mm**, while their conditional depth
  SDs are **0.60 / 0.56 mm**. Joint reprojection residuals reach 33-47 px; the tool reports `W_RESIDUAL` and
  `W_PAIR_DISAGREEMENT`, without inflating errors. Native-pixel picks were chosen visually before solving, not
  tuned against results. Rounded cap centres have correspondence ambiguity; hinge centres provide stronger
  evidence. Projection self-check agrees with `project_mm` to **4.55e-13 px**, and photo/model/basis hashes match.
  This establishes inconsistent evidence under the current frozen basis, **not a proven camera-calibration defect**;
  feature correspondence and per-photo pose remain possible causes. Diagnostic images and audit are in
  `data/assembly/m5fix2-photo-read-audit/`, including `positive-hinge-pair-disagreement.png`, shown to the owner.
  Under ASSEMBLY §1, asked whether to expand item 6 to repair calibration at $0 or commit with this proof
  unresolved. **Owner answered: "Expand item 6 to repair calibration".** The expansion is authorized at $0;
  diagnosis must distinguish calibration error from geometry/feature ambiguity, and independent calibration
  evidence must leave the four proof features held out. No error inflation or relaxed proof is authorized.
- **7 — Close worn views:** captures append yaw +50/-50 at fixed authored scale, hair off. The same face-surface
  and posterior-head occluder meshes are temporarily skin-shaded, with materials restored even after errors.
  Crops include the outer lens, drawn temple and 25 mm beyond its buried cap. The existing proxy has no anatomical
  ears; none were invented. Descriptions distinguish AR's spread/return/clip from authored product geometry,
  which Astra judges with photo compares and `photo_read`. A single 1024x1000 tool sheet gives the two close views
  substantial space, retaining all **20 native capture images** in the report and immutable delivery. Delivery
  links are rebuilt from hashed capture records and remain valid after the working files are deleted.
  Final proof: `data/assembly/m5fix2-worn-b/proof.json` and `m5fix2_worn_proof_final.{log,receipt.json}`
  (**exit 0, $0**). Both temples meet the visibly drawn hiding surface. All **14 original standard panels are
  byte-identical** to a separate capture without worn views. The actual emitted sheet decodes to the host sheet's
  pixels without a second shrink. Existing **6.1 mm TEMPLE CUT warnings** remain visible (also present at ±45);
  the proof does not alter the model or conceal them. Images:
  `data/assembly/m5fix2-worn-b/work/arview/worn-proof/yaw_p50__skin__beauty__worn.png` and
  `yaw_m50__skin__beauty__worn.png`; sheet `contact-sheet.jpg` in the same directory.

Both real proofs use accepted Oakley GLB SHA
`2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688` from the Studio delivery. The measuring proof
uses `data/assembly/m5fix1-camera-roll-a` basis `01`, SHA
`ae065be8bcb1241f5842e30f3243763bc04d7b9aefffcc914542b9b2213c67b1`.

**Verified implementation baseline, before the newly authorized calibration expansion:** **3,623 distinct Python
tests pass, two skip** after the prescribed isolated reruns. Full AR: **498 unit tests + 105 QA tests pass**;
TypeScript is clean. Details and receipts under `data/assembly/`:

- Main `m5fix2_full_main_b`, `-n 8 --dist loadfile --ignore=glasses3d/tests/photoprint`:
  **3,263 passed, 26 failed, 20 fixture errors, two skipped**, 921.86 s. Two failures were old wording assertions
  for `cap` / `settled`; updated to assert the new separate money wording. Twenty fixture errors came from one
  Blender startup timeout; 21 render failures followed one 300 s render timeout; the seeing worker exceeded its
  60 s check, the temple timing test measured 56.25 ms against 50 ms, and the QoS fixture conflicted with the
  launcher's descendant opt-out watcher. No timeout, speed or geometry threshold was changed.
- `m5fix2_wording_checks`: complete budget-guard and temple-lab modules, **207 passed**, 144.69 s.
- `m5fix2_isolated_blender`: temple timing plus the complete worker, render-dome and seeing modules,
  **73 passed**, 163.10 s; `m5fix2_isolated_qos`: **one passed**, 0.42 s, without the conflicting watcher.
- `m5fix2_full_print`, `-n 2 --dist loadfile`: **314 passed**, 247.75 s.
- `m5fix2_ar_unit_b`: **498 passed**, 20.74 s. The first run had 497 passes and one stale exact-object assertion
  omitting the additive `wornSides: false` field; updated that expected object and added the explicit-camera/worn
  incompatibility assertion. `m5fix2_ar_qa`: **105 passed**, 38.53 s; `m5fix2_ar_types`: exit 0.

Focused policy tests 215 passed/1 skipped; photo-tool checks 230 passed/1 skipped; final lineage/money checks
135 passed/1 skipped; delivery integration 52 passed; AR focused 29 passed and host checks 66 passed. These
overlap the full-suite count. Two symlink checks require Windows privileges unavailable to this process. An
initial broad focused command had 16 fixture isolation failures because explicit agent-leaf-before-parent test
collection omitted the parent offline fixture; parent-first collection passes and offline transports refused
before any inference. An initial main-suite run was stopped after 56 s for the discovered immutable-delivery path
fix, not counted as a test failure. Long checks have detached logs, argv/PID/time/exit receipts, BLAS=1 and EcoQoS
opt-out, with one pool-heavy job at a time. Free commit was checked before pools; main used eight workers and
photoprint two as prescribed. This verified implementation is committed separately while the authorized
calibration work continues; item 6 is not declared proven.

**Calibration expansion in progress:** two independent numeric audits found a general representation limitation:
the seven-parameter perspective camera fixes its optical axis through the shared normalization centre. A lateral
eye translation cannot be represented by shifting final image pixels. Existing `blender_camera.from_blender`
already compensates by changing the frame centre, which a shared multi-photo basis cannot do per camera.
Independent off-axis pinhole observations with held-out 3D points reproduce nonzero best-fit error; adding two
pre-roll lateral eye offsets recovers both training and held-out points to numerical precision. The on-axis
control remains exact. Reproducer: `data/assembly/m5fix2-photo-read-audit/pinhole-a/pinhole.json` (27 training,
12 held-out points; old held-out RMS **2.012 px**, corrected approximately **1e-13 px**). This establishes the
general representation limitation; the following ablations and correction measure its contribution to Oakley's
disagreement. Original proof points and errors remain held out.

**Authorized calibration repair, following baseline commit `6fb020e`:** optional `eye_x` / `eye_y` represent
lateral camera translation in the shared normalized frame. Legacy seven-field files keep zero offsets and serialize
unchanged. Projection, raster rays, Blender conversion, crops, triangulation, visibility and frozen-camera hashes
carry the offsets. The existing `from_blender` recentering behavior remains the default; `preserve_frame=True`
is explicit. Print viewing directions use the physical eye for the new cameras, retaining the old branch for
zero-offset frozen cameras. No product geometry or textures are edited.

The validated seven-parameter search remains the default. Astra can explicitly request `fit_cameras(off_axis=True)`
to add one nine-parameter continuation; the tool, host journal and result record the requested mode. Both camera
families from every view remain candidates on one common final ROI/grid; the selected camera's metrics and
pitch-twin evidence are recomputed. Existing angular, perspective, loss and usability policies remain unchanged.
The two added coordinates each have a predeclared domain of +/- the model's normalized enclosing-sphere radius,
with boundary hits reported. Optical pitch-sign checks remain required; physical eye positions must also respect
the declared view side. Pitch-twin reports include full cameras and physical positions because opposite optical
pitch need not mean opposite physical eye height after lateral translation. Independent review identified and
corrected candidate-retention, view-alias and print-direction gaps. After the real fit exited, the final axial-roll
canonicalization was restored after global selection (with an offset-preserving regression test), so all fitting
workers saw one source version. The real selected axial roll is already 84.93 degrees; that final normalization
does not change its camera or the held-out result.

Independent checks so far: representation/legacy tests **76 passed, one Windows skip**; actual Blender test
**one passed**, analytic maximum **0.000175 px**, rendered-centroid maximum **0.021651 px**, within the existing
0.01/0.1 px checks. A known off-axis synthetic silhouette fit reduces independent point RMS from **1.030151**
to **0.276921 px** without changing bounds, weights or seeds (**two tests passed**). Focused compatibility checks
after the review repairs: **185 passed, one Windows skip**, 40.00 s. These sets overlap and are not added to the
full-suite count. Evidence: `data/assembly/m5fix2-photo-read-audit/camera-eye-{blender,fit}.{log,status.json}`.

The separate real-photo lens-objective ablation did **not** fix agreement: the positive-X hinge's maximum pair
distance was **9.90 mm** originally, **9.20 mm** after another unchanged optional-lens refinement, and **9.13 mm**
with a full-lens objective. The optional-lens policy is therefore unchanged. Evidence:
`data/assembly/m5fix2-photo-read-audit/ablation-a/held-out.json`; all four readings still failed.

The fresh six-photo proof ran detached under keeper PID **34284**, started **2026-10-14 12:14:03 UTC**, using
**six workers** with **15.07 GiB** free commit, BLAS=1 and EcoQoS opt-out. From the repository root, use the venv's
Python with `-m glasses3d.tests.proofs.assembly_m5fix2_calibration --source
glasses3d/data/assembly/m5fix1-camera-roll-a --out glasses3d/data/assembly/m5fix2-photo-read-audit/NEW-OUTPUT
--workers 6` (the proof refuses an existing output). It copies immutable photos/model, preserves the
original candidates, refinement count and matte policy, records source-code/input hashes, freezes all cameras,
and only then imports the original four held-out points and invokes the real `photo_read` tool proof. Log/status:
`data/assembly/m5fix2-photo-read-audit/off-axis-a.{log,status.json}`. It finished **12:21:11 UTC**, **425.078 s**,
exit **2**, `accuracy_failed`. All six camera registrations are usable; all original/copy hashes match; there were
**zero network attempts and zero production-client constructions**, cost **$0**. No concurrent heavy pool ran.

The new cameras improve every held-out pair disagreement, but **all four readings still fail**:

| Held-out feature | Original maximum pair distance | Repaired maximum pair distance | New maximum residual | New pair/error ratio |
|---|---:|---:|---:|---:|
| Negative-X hinge logo centre | 11.02 mm | **9.25 mm** | 36.94 px | 7.81 |
| Positive-X hinge logo centre | 9.90 mm | **7.44 mm** | 25.57 px | 6.42 |
| Negative-X rounded terminal | 16.95 mm | **14.15 mm** | 40.75 px | 15.32 |
| Positive-X rounded terminal | 11.31 mm | **9.19 mm** | 32.76 px | 11.71 |

The ratio is the maximum pair difference divided by its reported axis uncertainty; the unchanged proof requires
at most one, plus a consistent joint solve. The top photo has optional-lens IoU **0.75406**, raw IoU **0.63650**;
its perspective is at the existing 0.6 bound. Angled photo 02 reaches the predeclared lateral-eye bound and is
flagged. Neither bound was widened. The common objective selects the unchanged front camera; photo 05's selected
continuation retains zero eye offsets. Records preserve both candidates, all losses and diagnostics. The unchanged
accepted mesh is a visual reference whose silhouette registration does not establish accurate metric calibration.
The representation defect is fixed; the remaining photo/model/correspondence uncertainty has not been resolved.

An independent photo-only review predeclared 12 additional rigid-front features without consulting projections or
residuals. These cannot identify all six free nine-parameter cameras: side photos 03/05 each have only four
identified points (eight scalar observations), and top 06 has none in that independent selection. This remains
underdetermined even with known feature XYZ. The shared frame supplies coordinates, not a physical calibration
measurement; anchoring to the
accepted mesh transfers its shape errors into the cameras. Front features also cannot determine temple opening.
No assumption that temple poses differ has been made. Exact observations and their limitations are preserved in
`data/assembly/m5fix2-photo-read-audit/independent-feature-audit.json`. These observations are diagnostic only;
no held-out point, error bar, quality threshold or fit parameter has been adjusted to force agreement. Closing the
real accuracy proof needs independent calibration/correspondence evidence, especially for top 06; the current
selected observations cannot identify it. Item 6 is **not declared complete**. Full regression results follow below.
All calibration work costs **$0**.

Focused camera regression: `m5fix2_camera_final_focused.{log,receipt.json}`, **17 passed**, 33.50 s,
including the global roll-normalization test; the frozen six-photo basis loads and verifies under this code.
The first expanded full suite, `m5fix2_calibration_full_main`, ran **12:23:09-12:36:12 UTC**, eight workers,
**16.22 GiB** free commit: **3,322 passed, five failed, two skipped**, 781.30 s. Three failures were the known
timing/QoS checks. One expected seven serialized fields. One was a real default-policy quality regression:
the extended fit selected optical pitch **9.65861 degrees** for known truth **8 degrees**, exceeding the
unchanged **1.5-degree** tolerance. Its silhouette loss and vertex projection were better, but that does not
certify optical orientation. The legacy fit's pitch was **7.98528 degrees**; independent known-vertex RMS was
**0.12981 px** legacy versus **0.08806 px** extended. Translation and principal-point freedom allow optical
orientation to drift while matching pixels. The original quality test and tolerances are untouched.

Technical decision, independently reviewed by both calibration and payload reviewers: keep the validated default
and expose extended fitting as an explicit capability, not an automatic replacement. No capability, historical
evidence or failed result was removed; no penalty, bound, seed or threshold was tuned to the failed fixture.
The six-photo proof already used explicit `off_axis=True`, so it remains the evidence for that method. Final
tool/default forwarding and journal checks: `m5fix2_camera_policy_focused`, **116 passed**, 17.16 s.
The final main suite, `m5fix2_calibration_final_main`, ran **12:45:23-12:56:49 UTC**, eight workers/loadfile with
**16.14 GiB** free commit: **3,326 passed, three failed, two skipped**, **683.66 s**. The original camera-angle
and exact seven-field serialization tests pass unchanged. The failures are the seeing worker's **74.255 s >60 s**,
temple Blender core's **91.4 ms >80 ms**, and the QoS baseline child's conflict with the descendant opt-out
watcher. As prescribed, complete affected Blender modules reran alone: `m5fix2_calibration_isolated_blender`,
**37 passed**, **109.25 s**. The QoS fixture passed without the conflicting descendant watcher:
`m5fix2_calibration_isolated_qos`, **one passed**, **0.30 s**. No timing, geometry or quality tolerance changed.

The last source review caught a reporting gap: extended camera candidates and pitch-twin diagnostics were frozen
on disk but omitted from the main tool's per-photo summary. The tool now returns the selected full camera,
off-axis candidate evidence and sign/twin evidence, so the existing limiter and `read_report` retain access.
One new regression follows the actual SDK fit tool through capped output and retrieves all three fields using
the registered reader. The final additive reporting fix changes no fitter math or default-camera results.
`m5fix2_calibration_report_checks`: **93 passed, one Windows skip**, **8.50 s**, including that new regression
and the complete tool/report modules. A second independent review verified the successful paged diagnostic path.

Print `m5fix2_calibration_full_print` ran **13:01:47-13:05:23 UTC**, two workers/loadfile, **16.16 GiB** free
commit: **310 passed, four failed**, **214.49 s**. All four failures were the colour-policy fixture's placeholder
`camera=object()`, which lacks the new shared direction helper's camera fields. Replaced only that placeholder
with a genuine zero-offset `Camera`; all colour, mask and sampling assertions remain unchanged. The complete
colour-policy module passed as `m5fix2_calibration_colour_checks`: **43 passed**, **18.99 s**. No runtime
compatibility fallback or filter change was added.

**Final regression coverage: 3,644 distinct Python tests pass, two Windows-privilege skips**, combining the
prescribed full suites, affected isolated reruns and the one late paging regression (not double-counting overlapping
modules). The unchanged AR implementation retains its completed **498 unit + 105 QA** passes and TypeScript check
from this milestone's baseline. All jobs exited with receipts; no heavy job remains running. Complete diff review
and `git diff --check` are clean. Source, tests and this record are committed with explicit paths on `glasses3d`;
nothing is pushed. **Item 6's real accuracy proof remains failed and open despite passing software regression
checks.** The owner-authorized work cost **$0**; no paid run was approved or started.

## Assembly M5 Oakley run 2: approved fresh run, draft delivered (2026-10-14)

Executed in the primary `C:\Users\Shay\PycharmProjects\lenses` checkout on `glasses3d`, initially clean at
`3b7001b Finish M5-fix-1 seating, camera and tool diagnostics`. The owner explicitly approved only job
`oakley-oo9208-v2-3e057bab`, request `20261014T084140-272538`, **fresh, not resume; $20 settled stop,
$23 maximum reserve, max effort**. The complete owner message was passed unchanged to `job approve` via
`data/assembly/m5oakley2-owner-approval.txt`; approval recorded at 08:43:53 UTC. The older job was not used.
No other paid run was started. No runtime source, quality check, gate, photos or finished product was edited by Codex.

- **Fresh-start handling and evidence preservation:** the first detached launch refused before inference or
  approval consumption because the job still contained the prior active session (`this job already has a session`).
  This refusal cost **$0**. Rather than resume against the owner's instruction, took the paid-run lock, archived
  the old `session`, `checkpoints`, `cameras`, `work`, `worker` and heartbeat into
  `data/assembly/m5oakley2-prior-active/`, and verified **435 file hashes**. Original inputs, ledger, historical runs,
  deliveries and reviews were preserved. A fresh offline scene contained only `EMPTY mdl_bridge_underside`,
  zero product objects and no conversation. Evidence: `data/assembly/m5oakley2-fresh-reset.json`; original refusal
  receipts: `m5oakley2-preflight-refusal-status.json` and `m5oakley2-preflight-refusal.log`. The same unconsumed
  approved request then started detached without `--resume`. Request/config/transport prove six approved photos,
  zero inherited history and verified c0000; request SHA `ab06913c7d3614ea035774c2b526e24fb13c9f726e7cf537dc7f727d4051a7dd`.
- **Execution:** keeper PID 9124, actual runner PID 38064, Blender PID 23684; run start
  **08:46:44.624 UTC**, run end **09:50:52.758 UTC**, **3,848.097 s**. Detached keeper finished 09:51:01 UTC,
  exit 1 for `budget_limit`. The persistent 40-second monitor and live checks recorded progress throughout;
  `data/assembly/m5oakley2-monitor.jsonl`, `m5oakley2_monitor.*`, and `m5oakley2_poll.py` retain evidence.
  Heartbeat: **1,909 beats, zero write failures, maximum gap 2.187 s**. Camera fitting used parallel workers,
  EcoQoS opt-out and BLAS=1, about 19 GiB total machine memory, without competing heavy jobs. No silent stall,
  request retry or manual interruption was needed.
- **Money and stop:** **80 settled requests, actual $15.468835**, zero held/unknown/rejected charges and no
  ledger violation. Correct-job lifetime ledger: **$35.459084**, 150 requests across its three runs.
  Final status **`budget_limit`**, accepted finish **false**, verified exit checkpoint **c0014**; no `finish`
  call or Astra final output. The next request was refused locally: **$8.389200** reservation required against
  **$7.531165** remaining reserve room, a **$0.858035** shortfall, while **$4.531165** still remained to the
  $20 settled stop. The unsent request incurred no charge. This is a reserve-limit draft, not a checkpoint failure.
- **Owner's reservation concern and exact cause:** the earlier status `$14.462361 settled / $22.809261 committed`
  included an **internal $8.346900 worst-case reservation**, not an additional payment. Request 80 counted
  **284,724 input tokens**, exceeding the **272,000** long-context threshold. The existing reserve policy priced
  all input at the long-context cache-write rate: **284,724 x $25/M = $7.118100**, plus maximum output
  **16,384 x $75/M = $1.228800**. Actual request-80 charge was **$1.006474** (270,502 cached tokens;
  14,096 cache-write tokens; 1,474 output tokens), releasing **$7.340426**. The final unsent input count
  **286,416** gives **$7.160400 + $1.228800 = $8.389200**. Root cause of the large context: call 63 asked
  for `stations_table=true`; its **848 station rows** came in **270,795 characters of tool text**, and request 64
  jumped **84,259 -> 184,858 tokens (+100,599)**. Request 79 was 270,628 tokens; the following AR capture/reply
  added 14,096 tokens, pushing request 80 across the tariff threshold. Four AR-capture increments total **56,730
  tokens**; together with the seating-table increment they account for **157,329 of 265,404 parent-context growth
  tokens (59.3%)**. These measured deltas include the preceding model reply; they are not provider-attributed
  per-field token counts. Histories remained append-only with **zero prefix edits**, so this is newly appended
  diagnostic bulk rather than the old history-rewrite cache bug. This exposed a cost-control limitation: large diagnostic
  outputs plus worst-case reservations can end a run below its settled stop. Recorded for a $0 follow-up;
  neither the approved cap nor the guard was changed to continue this run.
- **Astra's work and remaining summary:** the initial mesh code had a TypeError and rolled back cleanly; the
  corrected code built the shield, brow, clamps, nose parts, temples and hardware. The first temple preparation
  refusal accurately named multiple material slots, which Astra corrected. The first substantive temple stage
  spent **$2.330326** under a $4 allowance, stopped before its next reservation and returned no parent geometry.
  The second spent **$2.706939**, imported revision 3 as an accepted **partial / `stuck`** reconstruction, and
  explicitly reported broad root/front/rear contours, inconsistent cross-view readings and limitations expressing
  the vent/U-shaped border. Parent calls subsequently narrowed shoulders, restored real root openings, cleaned
  and closed temple boundaries, repaired lens support, added restrained Oakley/Prizm markings and tuned optics.
  Do not describe that partial stage as a full likeness pass. There is **no final Astra summary** (`final_output`
  and delivery `summary` are null). Its last saved `appearance_limit` says that fixed AR lighting gives smaller
  moving green/cyan highlights than the broad studio green region, no painted colour region is used, and
  top/side camera and root-facet residuals remain.
- **Coating helper used:** successful calls 4, 60, 69 and 74 invoke `g3d.lens_coating`; final optical adjustments
  in call 80 preserve it. The final GLB and AR runtime retain `KHR_materials_iridescence`, transmission and IOR.
  Direct final-GLB parsing gives factor **1**, film IOR **1.38**, stored minimum/maximum **360/390 nm**,
  effective uniform thickness **390 nm** (no thickness texture), substrate IOR **1.98**, transmission **0.94**,
  roughness **0.17**, metallic **0**. The packed 2048x1024 image has a uniform base and two small markings;
  the coloured interference is not a painted green region. Call 76 repaired a generated-image pixel loss after
  call 74 changed colour space; the final image is populated. The final AR sheet shows
  moving green/cyan/violet highlights over a mostly muted violet-grey lens, not the photos' broad coloured fields.
  Centre OAKLEY and lower-right PRIZM lettering are present but faint; angular hinge/temple shoulders remain.
- **Seating and opening preservation:** frame type was first inspected as semi-rimless (call 30), then persisted
  as `g3d_seat` before repair (call 31). The original lens failed cap recognition; call 32 rebuilt its triangulation
  and retained its openings. Subsequent seating/support work eventually passed without changing any rule.
  **Eight** mutations/undo/refusal reports provide before/after front panels at the same frozen camera, each with
  checkpoint and native-render hashes. Both central vents remain separate openings throughout. Native alpha<128
  pixel counts in identical central regions are listed below; these are raster opening measurements, not physical
  area, seating verdicts or proof of photographic likeness. Refused calls show the restored model, not discarded
  attempted geometry. The final openings are more rectangular than the reference.

  | Tool | Checkpoints | Central transparent pixels before -> after | Result |
  | --- | --- | ---: | --- |
  | 32, lens triangulation | c0003 -> c0004 | 931 -> 923 | both retained |
  | 62, seat lens | c0007 -> c0008 | 923 -> 900 | both retained, partial seating |
  | 64, undo seating | c0008 -> c0009 | 900 -> 923 | restored |
  | 65, supported edge/vent rebuild | c0009 -> c0010 | 923 -> 1,043 | both retained, squarer |
  | 67, seat lens | c0010 -> c0010 | 1,043 -> 1,043 | refused, restored |
  | 69, retaining jaws | c0010 -> c0011 | 1,043 -> 1,043 | both retained |
  | 70, seat lens | c0011 -> c0011 | 1,043 -> 1,043 | refused, restored |
  | 74, optics/cleanup/attachment plane | c0011 -> c0012 | 1,043 -> 1,036 | both retained |

  Review sheet: **`data/assembly/m5oakley2-seating-fronts.jpg`** (before left, after right, identical crop,
  no retouching); complete paths, native hashes, camera, crop and measurement regions:
  **`data/assembly/m5oakley2-seating-evidence.json`**. Source reports remain in the job's `work/seating_reports/`.
  Every report and actual comparison image was returned to Astra, including manual repairs and refusals.
- **Final delivered-byte checks:** specification **passes**, no errors or live-site failures; **29 objects,
  41,334 triangles, 910,140 bytes, 153.5 mm overall width**. Two retained mesh warnings: **82 zero-area
  triangles in left temple, 96 in right**. Final seating **passes**: semi-rimless/plain, **339 PASS + 2 TRANSITION
  of 341 required samples**, 581 visible by design; no failed or unmeasured required samples. The nested
  over-capture diagnostic remains: **94 points, maximum 16.2207 mm**, warning threshold 0.6 mm. Technical
  clarification of earlier log wording: `mid-depth 0.5` means the normalized halfway depth through lens thickness,
  **not a 0.5 mm pass threshold**; this lens's actual thin/plain rule is **0.30 mm pass / 0.45 mm target**.
  No rule changed. Print check is `not_required`. Final AR capture succeeds at fixed scale with no runtime errors
  or fallback, carrying the same two mesh warnings. Owner appearance acceptance remains pending.
- **Top-photo fit:** all six basis-02 fits are usable. Top `reference-06` is `topb`, **raw IoU 0.5978,
  lens-optional IoU 0.670970744, raw mean contour error 2.867 mm**, yaw **179.30**, pitch **73.18**,
  roll **89.24 degrees**. Correct orientation does not remove the visible inner-front and temple-tip mismatch.
  Basis-02 was fitted at c0013, not recaptured at c0014; all **79 non-image GLB bufferViews**, nodes, meshes
  and accessors are identical in the final export, because call 80 changed only optics/image pixels.
  Overlay: `data/jobs/oakley-oo9208-v2-3e057bab/cameras/basis-02/overlay_reference-06.png`.
- **Immutable draft and review:** delivery ID
  `20261014T084140-272538-8567b04b597b0877d6ca1533ab4a79672732cbfa476010dafc3d7ae0db31b2ba`;
  GLB SHA **`bb54e681fe782ba12356f2b7b66661b0cb1c80facc54f35c30893b8b48ec621a`**;
  manifest SHA `0c6f22e0a868fa08a77e966bccf7027c51fd00d554de0bfa7adf11eab6de5869`.
  Draft was generated automatically from verified c0014 and captured again after the final material change.
  Exact AR contact sheet is the above delivery's **`contact-sheets/01.jpg`**, SHA
  `56e32379835875197da1bde839e77207783edb07641d98b4579b9c1e59ade382`. `job review` was started with
  this explicit delivery ID and `--port 0` at **09:51:50 UTC**, ready/rendered on **127.0.0.1:54730**,
  detached PID **31404**, fixed scale/lens environment 1. `review/current.json` contains the exact webcam URL
  and matching model/manifest hashes; the URL was queued in Codex. Independent local HTTP GET returned 200,
  910,140 bytes and the exact delivered GLB SHA. No owner acceptance was recorded.
- **Verification and commit scope:** independent read-only audits covered financial receipts/approval, final
  geometry/materials/seating and all reference/AR/repair images. **160 request/response receipts** pair to the
  **80 settled rows**; all response archive hashes, IDs, usage, integer repricing, reserve calculations and ledger
  digests agree. **59 referenced blobs**, **19 delivery manifest files**, all six photos in every saved request,
  and all three job-ledger budget hashes verify. Usage: **6,833,552 input tokens**, of which **6,389,965 cached
  (93.51%)**, **433,507 cache-write**, and **61,488 output** (including 31,151 reasoning). Cost components:
  plain input **$0.102060**, cached reads **$6.660467**, cache writes **$5.595058**, output **$3.111250**.
  Parent 39 requests cost **$10.431570**; the two child stages' 18/23 requests account for the rest. Peak
  settled-plus-held exposure **$22.809261** stayed $0.190739 below the cap. Audit limit: 71 raw request-wire hashes
  cannot be independently reconstructed because their private encrypted/signature fields are deliberately masked;
  the nine unmasked requests reproduce their wire hash/size, and all 80 response/usage bindings verify.
  All **15 parent checkpoint blend hashes c0000-c0014** match their index; all **16 native seating-panel hashes**
  match. The runtime remains exactly the tested `3b7001b`
  implementation. This operation changes only this progress log; the prior full suite was not rerun for a
  documentation-only run record. No push and no further paid inference are authorized by this record.

## Assembly M5-fix-1 complete after the owner's seating decision (2026-10-14; $0)

Continued in the primary checkout on `glasses3d`, initially clean at `56b8082` (descendant of `779a177`).
The owner resolved the item-4 stop: **seating remains a required finish check**. The accepted Oakley passes
geometrically as semi-rimless (top mid-depth p5/median/p95 **0.6742/0.7153/0.8327 mm**, rule **0.5 mm**);
its default refusal was the missing declaration. Run 1's vent loss came from the support rebuild. No seating
threshold, geometric check, finish gate, product mesh, photo or historical run evidence was changed.

- **Owner's seating additions:** instructions require declaring the actual construction before deciding a repair
  is needed. Tool descriptions distinguish an inspection-only `frame_type` argument from persisted `g3d_seat`
  metadata and show how to declare it without rebuilding geometry. Instructions and mutation results require
  preserving every photo-visible opening and feature. Seating/hiding/undo tools now render front before/after
  at the same frozen camera and light frame; manual support/lens/eyewire edits use the documented
  `execute_blender_code(seating_repair=True)` path. A usable frozen front photo camera is preferred; otherwise
  a canonical front is frozen before the edit and explicitly labelled. Native panels, camera parameters,
  checkpoint/fingerprint and hashes are retained. These are reports, **not a new gate**. Nonfatal diagnostic
  failures preserve the completed mutation and return an incomplete report; fatal worker/integrity errors
  retain their normal handling. Composed SDK deadlines include both renders without changing mutation limits.
  The existing 8,000-character instruction limit is unchanged.
  Focused seating/tools/loop checks: **195 passed in 62.52 s**; final focused item-6/seating/CLI checks:
  **145 passed in 10.94 s** (overlapping selections). Independent review found two diagnostic error-path issues
  (image packaging could obscure a completed edit, and after-render failure omitted the report file); both were
  fixed with regressions. The real Blender proof `data/assembly/m5fix1-seating-report-c/proof.json` passed in
  **25.062 s**: a declared lens with a real gap is refused, both seating repairs produce frozen reports, manual
  support movement/restoration produces visibly different front panels, and one final `finish` captures AR and
  publishes the immutable delivery. The initial expanded fixture already passed after declaration; its incorrect
  test assumption was fixed by creating an actual synthetic gap, without changing a runtime check.
- **Item 5 complete:** top/bottom registration searches the full image-plane roll circle with uniform 45-degree
  seeds and overlapping +/-25-degree optimizer windows, including a window crossing 180 degrees. Original
  zero-roll seeds and other-view bounds remain intact. Bottom labels have negative-pitch semantics. Camera fits
  below **0.5 effective IoU** report `unusable`, retaining the camera, metrics, matte, candidates and overlays;
  optional-lens IoU is used where the existing transparent-lens objective supplies it. This labels registration
  evidence, not model likeness or finish eligibility. Synthetic +/-90 and +/-179 degree top fits and bottom fits,
  seam coverage and unchanged prototype reproduction checks passed. Seven obsolete status/label expectations
  were updated; the underlying fitting thresholds were not relaxed. Final related selection: **42 passed,
  1 deselected in 73.82 s**.
  Exact accepted-asset proof `data/assembly/m5fix1-camera-roll-a/proof.json`: **passed, 275.687 s, $0**.
  Accepted GLB SHA `2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688`, all six photos and old
  baseline evidence remain unchanged. Top raw IoU **0.348062 -> 0.652772**, optional IoU
  **0.404932 -> 0.750111**, raw mean contour error **6.073921 -> 2.348158 mm**. Winner: `topb`, yaw 176.3181,
  pitch 80.2032, roll 86.7082 degrees. The other five camera dictionaries and raw/optional IoUs/contour errors
  are identical. The inspected overlay follows the rotated frame and temples; lens/nose differences remain.
  Perspective 0.598894 is near the existing 0.6 bound, which was not changed. This is usable registration,
  not owner appearance acceptance. Overlay: `data/assembly/m5fix1-camera-roll-a/cameras/basis-01/overlay_reference-06.png`.
- **Item 6 complete:** private temple preparation returns the actual export refusal code/message before trying
  to use export bytes. Successful-export checkpoint/fingerprint/hash/drift checks remain. Missing-axis refusals
  and tool docs explain the existing recognition rule: a temple-role part centered on the relevant side,
  world-X and world-Z bbox extents each strictly below **3.2 mm**, center Z within **4 mm** of the temple-front
  hinge plane. The text identifies this as a tool heuristic and says not to shrink real hardware to satisfy it.
  `execute_blender_code` documents the accepted `partRole` values: **lens, frame, temple**. No recognition or
  geometry algorithm changed. Tests preserve the exact export cause and prove preparation opens no private lab
  on refusal. Independent read-only review found no remaining blocker.
- **Final $0 replay:** `data/assembly/m5fix1-history-final/proof.json` reruns all **65 saved requests**, keeps
  all six photos in every request and preserves all **180 source hashes**. History-caused cache invalidations
  (prefix rewrites) **3 -> 0**. Observed ledger **$19.161346**; modeled stable-prefix replay **$11.311824**.
  With 1,000 extra metadata characters per tool: **$12.494124**, peak estimated input **238,056** tokens
  (base **211,176**, below 272,000). Zero-cache sensitivity **$77.090520**. These are offline scenarios holding
  saved replies fixed, not measured provider cache hits or a promised future price. Actual spend **$0**.
- **Oakley run 2 forecast:** `data/assembly/m5fix1-estimate-final.json`, correct job
  `oakley-oo9208-v2-3e057bab`, max effort, **$20 settled stop / $23 maximum reserve**. Expected **$15.116178**,
  growth-only range **$13.359064-$20.291582**, assuming 60 replies and historical append-only calibration.
  No additional compact-output/one-call-finish savings are assumed. The high growth case can reach the money
  limits before completion. As part of estimating the new guidance, fixed an undercount: forecasts now include
  tool names and descriptions as well as parameter schemas (regression proves longer descriptions increase cost).
  No approval request was created, no model client constructed, and no paid run started. **Owner approval is
  required before Oakley run 2.**
- **Full regression verification:** main Python suite, `-n 8 --dist loadfile` excluding photoprint,
  **3,254 passed / 6 failed in 753.14 s**. Four failures were obsolete expected values: schema-only smoke token
  counting, the old top seed count, and two bottom-is-unknown assertions. Updated these to assert the complete
  tool payload, exact original zero-roll prefix plus full roll banks, valid bottom labels and continued rejection
  of an unknown label. Independent review confirmed no accuracy, tolerance, integrity or budget check weakened.
  The other two failures were the unchanged seeing timing limit (**94.376 s** under load vs **60 s**) and the
  EcoQoS inheritance test's known conflict with the descendant opt-out watcher. All four affected modules passed
  together in isolation: **71 passed in 88.29 s**; EcoQoS passed unchanged with that watcher disabled:
  **1 passed in 0.26 s**. Main suite reconciles to **3,260 distinct passing tests**. Photo-print, separately
  `-n 2 --dist loadfile`: **314 passed in 205.92 s**. Total **3,574 distinct Python tests passing**, no skips.
  Logs/argv/PID/start/end/exit receipts are under `data/assembly/m5fix1_final_{main,isolated,qos,photoprint}.*`.
  AR unit tests: **497 passed in 20.640 s**; AR QA: **103 passed in 38.176 s**; TypeScript `tsc --noEmit`
  passed. Receipts: `data/assembly/m5fix1_final_ar_{unit,qa,types}.*`. `git diff --check` passed.
  Implementation, regression tests, proof scripts and this log are committed together as
  **`Finish M5-fix-1 seating, camera and tool diagnostics`**, explicit paths on `glasses3d`, never pushed.
  **Open:** owner visual acceptance and approval of Oakley run 2. No paid work is authorized by this repair.

## Assembly M5-fix-1 stopped at item 4 (2026-10-14; $0; historical stop, resolved above)

Started on the primary checkout, branch `glasses3d`, clean at `779a177`; no worktree and no paid request.
The owner requires items 1–6 in order and a stop at item 4 if the accepted Oakley fails seating. Items 1–3 are
complete and regression-tested. The default accepted-asset seating check refused, so implementation stopped
at item 4. Items 5–6 and any seating-policy change remain pending the owner's answer. No paid run started.

- **Item 1 complete:** generic jobs and their nested temple stages now keep append-only request history. Old
  approvals requesting image-pruning epochs are refused. New image outputs are one preview at most 1024 px
  per side; native panels remain available with source hashes, sizes and labels. Repeated job images reference
  an already delivered call; unseen/cancelled images are sent again. Nested previews retain explicit pixel
  mappings and separate descriptions without changing standalone lab behavior.
  Saved-request proof `data/assembly/m5fix1-history-b/proof.json`: 65 requests, all six photos in every request,
  180 source hashes unchanged, prefix rewrites **3 → 0** (old requests 51/61/64), 21 panels → 12 compact images.
  Observed ledger **$19.161346** reconciled exactly; modeled stable-prefix replay **$11.311824**, peak estimated
  input 211,176 tokens. Adding 1,000 metadata characters per tool gives **$12.494124**, peak 238,056, below the
  272,000-token price step. Zero-cache sensitivity is $77.090520. These are offline scenarios holding the saved
  replies fixed, not measured provider cache hits or promised future spend. The 528-token calibration decrease
  at request 64 is disclosed and a monotone input envelope is used. Actual proof spend **$0**.
  Tests: combined image/CLI/instruction/replay/stage selection **184 passed**; final replay **18 passed**;
  final nested-stage selection **26 passed** (overlapping selections, not an aggregate count).
- **Item 2 complete:** `finish` exports/checks the current head and performs a fresh fixed-scale AR capture in
  one call. It preserves all failing spec/seating/print reports, adds AR failures, and provides a scene-render
  fallback when AR images are unavailable. Infrastructure/image failures retain completed reports; acceptance
  is persisted only after image output succeeds. Existing quality gates and delivery hash checks are intact.
  The composed deadline covers export, AR and diagnostic rendering (780 s hard / 790 s SDK). Guidance and the
  low-budget notice now describe the one-call path.
  Real Blender proof `data/assembly/m5fix1-finish-b/proof.json`: unseated refusal with images, then two seating
  calls and one accepted finish, immutable delivery verified; no separate export/AR tool calls; **13.03 s, $0**.
  Its unchanged image bytes legitimately reused the first displayed image while the final capture and delivery
  retained fresh source receipts. Tests: tools + loop **113 passed**, final finish regressions **28 passed**,
  instructions/runway **82 passed** (overlapping selections). The final combined agent selection passed
  **266 tests in 77.48 s**.
- **Item 3 complete:** the sandbox helper `g3d.lens_coating` builds the
  correct glTF control group, preserves authored optics, and retains the requested minimum/maximum thickness
  in the GLB. An untextured film renders at its maximum thickness. Optical-loss diagnostics inspect each
  exported material and warn on lost or unverified active optical effects without creating a quality gate.
  The first live-session proof exposed stale evaluated-material metadata; reading the original authored
  material fixes this, with a repeated factor-0 → factor-1 export regression. The real facade/GLB/fixed-AR proof
  `data/assembly/m5fix1-coating-b/proof.json` passed in **14.984 s, $0**, including min=140/max=460 nm, seven
  changed beauty poses, and verified checkpoint exit. The fixture proves exported capability, not Oakley likeness.
  Independent review also reproduced a pre-existing cache blind spot: renaming a glTF group interface socket
  removed iridescence without changing its fingerprint. Nonempty group interfaces now contribute their
  persisted names, types, defaults, order and structure; ordinary no-interface trees retain their exact old
  hash input. Used, zero-user and fake-user groups plus Blender save/restore are covered. Historical checkpoint
  files with nonempty interfaces have older fingerprints; files are untouched and strict restore is not bypassed.
  Coating/export-loss/sandbox/worker selection: **270 passed in 28.24 s**; interface/mixed-scene/coating selection:
  **21 passed in 15.13 s** (overlapping selections). No remaining coating blocker after independent review.
  Regression runs before item 4 so completed repairs are verified even if the owner stop is triggered.
  Main Python suite (`-n 8 --dist loadfile`, excluding photoprint): **3,210 passed / 3 failed in 877.80 s**.
  All three failures passed unchanged in isolation: temple-fit CPU-time threshold and the complete seeing
  module **23/23 in 90.06 s**, plus EcoQoS inheritance **1/1 in 0.26 s** with the interfering descendant watcher
  disabled for that test. The loaded render took 154.068 s against its unchanged 60 s limit; the loaded fit
  took 57.8125 ms against its unchanged 50 ms limit. Main suite reconciles to **3,213 distinct passing tests**.
  Photoprint (`-n 2 --dist loadfile`) passed **314/314 in 228.55 s**; total **3,527 distinct Python tests passed**
  after the unchanged isolated reruns. AR passed **497 unit + 103 QA**, and TypeScript checking.
  Process receipts/logs use `data/assembly/m5fix1_{full_main,isolated_qos,isolated_seeing,photoprint,ar_*}`.
  No paid call or seating change.
- **Provisional run-2 planning estimate through item 3:** local forecast
  `data/assembly/m5fix1-estimate-items1-3.json`: **$14.958464**, growth-only range **$13.201373–$20.118408**,
  60 replies, max effort, $20 settled stop/$23 reserve. The historical append-only calibration is retained;
  compact-image and one-call-finish savings are not credited. The separate saved-reply replay above is a
  scenario, not the future-run estimate. This forecast created no approval request and made no API call.
  The final estimate remains subject to the item-4 decision and items 5–6.
- **Item 4 baseline complete; stop rule triggered:** `data/assembly/m5fix1-baseline-a/proof.json`, **367.078 s,
  $0**, 10 memory-sized fit workers. Exact accepted Studio GLB SHA-256
  `2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688`; copied bytes, all six photos and source
  reports verified unchanged. No Blender import/re-export or product edit. The real fit/contour methods used an
  explicitly labelled immutable-GLB adapter; contour comparison omitted Blender native/clay passes, and the real
  fixed-scale AR capture supplied material renders. All six photos remained in the report.
  **Default seating `ok=false` solely because the old GLB lacks a frame-type declaration** (`g3d_seat` extras).
  Its inferred semi-rimless geometry has `graded_pass=true`. The independently specified semi-rimless diagnostic
  passed with no geometry or gate change: top stations 329 (323 LIPPED, 6 ENCLOSED), mid-depth p5/median/p95
  **0.6742 / 0.7153 / 0.8327 mm**, against the existing 0.5 mm rule. This is a declaration refusal, not evidence
  that the accepted model fails the geometric seating threshold. The diagnostic ran before the default verdict;
  the default check was the final instrument call. No seating code, accepted asset or requirement was changed.

  Contours from each asset's recorded camera basis (IoU / mean symmetric contour error in mm; these are
  independently fitted comparisons, not a common-camera shape ranking):

  | Photo/view | Accepted Studio GLB | Run 1 latest comparison |
  |---|---:|---:|
  | 01 front | 0.9681 / 0.426 | 0.9573 / 0.637 |
  | 02 angled left | 0.8857 / 1.384 | 0.9205 / 1.062 |
  | 03 right | 0.8537 / 1.537 | 0.8566 / 1.420 |
  | 04 rear | 0.8973 / 1.472 | 0.9084 / 1.014 |
  | 05 left | 0.8502 / 1.369 | 0.8774 / 1.198 |
  | 06 top | 0.3481 / 6.074 | 0.3560 / 6.082 |

  Run-1 rows 01–05 are c0021; top is c0008, because the final comparison omitted it. The separate final top
  refit was worse (0.3233 / 7.318 mm; prior run review). Current code still labels the poor accepted top fit
  `ok`; item 5 has deliberately not been applied while the owner stop is pending.
  Accepted AR capture succeeded at fixed scale: zero errors, 11 warnings (lens lettering excluded from brow
  readings, saturated junction readings, legacy mixed front/temple treatment, and 6.1 mm temple occlusion in
  the ±45° poses). Sheet: `data/assembly/m5fix1-baseline-a/work/arview/baseline-arview/contact-sheet.jpg`.
  Inspected that sheet and `data/assembly/m5fix1-baseline-a/vent-before-after.png`: the latter shows **run 1
  c0008 before repairs versus c0021 after repairs**, not an edited accepted model. Original image hashes and
  the two central transparent-component measurements reproduce **78.7% less width-normalized apparent opening
  area**. The different source resolutions/antialiasing are disclosed; this is neither physical area nor sealed vents.
  **Pending owner question:** should seating remain a required finish check, or become a report Astra weighs
  against the photos? Under the owner's explicit item-4 rule, no item 5/6 work or paid run may start before that answer.

No full-suite closeout or Oakley run-2 approval has occurred. No paid run is authorized by this repair request.

## Assembly M5: thorough fresh-run review (2026-10-13; $0)

Owner requested a thorough review using all run evidence. Full report:
**[REVIEW_OAKLEY_RUN_20261013.md](REVIEW_OAKLEY_RUN_20261013.md)**. Runtime implementation remains `123dcca`;
no source/model edits or paid requests during the review. Independent accounting, geometry and visual reviews
covered all 65 calls/provider records, all 21 tool-image outputs and six photos, both fits/comparison batches,
all three AR captures, and all 22 checkpoint file hashes. A fresh audit verified 130 transport records,
65 provider-to-tool argument bindings, 23 issued-code hashes and 65 journals; its 222 source files unchanged.

**Verdict:** reliable completed delivery, but photo likeness and the job's priority coating are not achieved.
New findings beyond the finish summary:

- A four-case $0 diagnostic-cube proof through the actual exporter establishes that **iridescence export is
  supported**. Astra used `Iridescence` instead of `Iridescence Factor` and omitted `Occlusion`, which causes
  Blender to inline away the glTF group. Correcting both exports `KHR_materials_iridescence`; either alone
  does not. The prior description of the capability as unavailable was too broad. Product appearance was not
  modified or retested; exporting the effect does not alone prove a photographic match.
- The two central vent openings lose about **79% of their rendered area** from c0008 to c0021 after resolution
  normalization. They remain tiny slits, not sealed. The change from separate lips to a continuous retention
  rail after seating refusals supports the mechanism. Seating PASS does not certify these photo details.
- The top fitter bounds roll to +/-25 degrees; the photo needs about 90 degrees. Retry remains misregistered
  (IoU .3233); no final registered top comparison. The saved top matte is correctly oriented.
- Actual 4.5 mm-wide temple knuckles fail the detector's <3.2 mm condition despite meeting its proximity rule.
  Four stage preflights refused; no nested paid stage ran. Call 25 separately hid an invalid-role export refusal
  behind a misleading checkpoint-mismatch message.
- **85.0% of $19.161346 was input processing**, including new content and replayed history. Context grew
  18,702 to 230,515 tokens. Three prompt-history image-pruning changes coincide with cache-prefix loss and
  cost $7.479712 (39.0%); request archives support
  the causal explanation, but this is not a measured counterfactual savings estimate. Final eight calls changed
  no product geometry/material and cost $7.374968 (38.5%). These checks were useful; their orchestration was costly.

Final spec 7/7 and seating passes remain true, with the transition/overcapture and AR warnings already recorded.
All checkpoint bytes match their original index; all requests settle exactly; budget stop/reserve respected.
The report separates automatic validity from photo likeness and actual wearer fit. Saved AR uses a featureless
skin background, not an owner webcam capture. Full evidence/reproduction under
`data/assembly/m5oakley/thorough-review/`; recommended general $0 repairs are listed, not implemented.
No gate loosened. Owner acceptance and any further paid run remain unapproved.

## Assembly M5: authorized fresh Oakley run (2026-10-13; completed, awaiting owner review)

All five requested $0 repair/verification steps passed before this run, committed as **`123dcca`** on branch
`glasses3d`, clean checkout `C:/Users/Shay/PycharmProjects/lenses`. No worktree, push, paid reproduction or
hand-authored product edit. Only the corrected job **`oakley-oo9208-v2-3e057bab`** was used.

**Fresh start, not resume:** preserved the complete failed-run job in `data/assembly/m5r1frozen/` and moved only
active session/checkpoints/job-scene state/heartbeat to `data/assembly/m5r1active/`. The old run records,
receipts, money ledger, input photos and seed stayed intact. Each preserved file was byte-verified (detach
specifications copied opaquely, never inspected). The reset helper initially passed `objects:None`, selecting
no objects in its verification query; corrected the helper to `objects:"all"` and independently verified the
already prepared state, without repeating the reset. Evidence `data/assembly/m5r1-fresh-reset.json` confirms
empty c0000, only the EMPTY origin marker, no conversation, no product geometry and unchanged inputs/ledger.

Fresh request **`20261013T173404-2accf4`**, request SHA
`8ffc6c1f5ef996a9263d78cbcd78c9c807c1898e8bba4c5bf88aa053cf0a25f5`.
Estimate **$14.957821**, range **$13.200719-$20.117690**, met the owner's expected-cost-under-$20 condition.
Recorded these exact owner words through `job approve --max-usd 23`:

> I approve a fresh Oakley run on the same job (oakley-oo9208-v2-3e057bab), not a resume, on the same terms: $20 settled stop, $23 maximum reserve, effort max. Start it only after steps 1-5 pass and its new estimate is under $20. When it ends, start job review and report as before. Do not start any other paid run.

Bound settings were **$20 settled stop / $23 maximum reserve / max effort**, `resume:false`, zero history,
all six photos newly supplied. Independent live-start audit checked approval/config/consumed-ledger bindings,
all seven input hashes, empty c0000 and the real provider request's model/effort. Started once, detached through
`job run`, using only the existing runner's `--env-file` loader. No credential file was read or printed by the
agent. BLAS 1, descendant EcoQoS opt-out; no competing heavy pool. Keeper PID 32504, runner PID 30884, Blender
PID 46044. Forty-second process/heartbeat/money monitoring: `data/assembly/m5oakley/fresh-monitor.jsonl`.

**Outcome: completed, accepted finish (not a draft)**. Started **17:34:27 UTC / 20:34:27 Jerusalem**, finished
**18:32:43 UTC / 21:32:43 Jerusalem**, **3,496.759 s (58 min 16.8 s)**; detached runner exited 0 at 18:32:44 UTC.
**65 API requests, 65 tool calls**, no continuation or second run. No teardown errors. Checkpoint **c0021**;
the exact old fingerprint failure did not recur. Model-code refusals/geometry failures were returned to Astra
and corrected by Astra through the tools. A topology-valid lens was achieved before seating, then the normal
seating gates passed. The specialist temple stage repeatedly refused; no specialist result is claimed.

**Money and receipts:** actual fresh-run settled spend **$19.161346**, final held **$0**, unknown **$0**. All
**65** charges were re-priced from provider usage; **130** request/response receipts verified. Largest individual
hold **$3.700638**; every request began below the settled stop and total committed spend stayed within $23.
The same job's aggregate, including the original failed run's **$0.828903**, is **$19.990249**. This aggregate
does not include the earlier smoke on its separate job. Seven audit groups passed: approval binding,
money/ledgers, transport receipts, referenced output blobs, six initial photos, immutable delivery and evidence
unchanged (**263** read files). **27** unique blobs verified across **195** receipt documents. Evidence:
`data/assembly/m5oakley/fresh-audit.json`; source ledgers and receipts at
`data/jobs/oakley-oo9208-v2-3e057bab/runs/20261013T173404-2accf4/{budget.json,transport/,result.json,transcript.md}`
and `data/jobs/oakley-oo9208-v2-3e057bab/budget/job-ledger.json`.

**Immutable delivery:**
`data/jobs/oakley-oo9208-v2-3e057bab/deliveries/20261013T173404-2accf4-cf35d209a42d0149c739654e5e046f58a46d405c93ea362e11ef629a7f675904/`.
GLB SHA **`a2814e098ce88aafc17c746e05af288d54f73eb28d82cb1d9f61044912279c23`**, **1,605,328 bytes**, **74,150
triangles**; reported width **155.65 mm** (estimated scale, no supplied physical measurements). `report.json`
contains Astra's complete finish summary and the bound checks. **Specification 7/7 rows pass**, no errors or
warnings. **Seating passes**, no reasons/refusals: 541 PASS, 1 TRANSITION, 366 VISIBLE_BY_DESIGN;
`share_ge_pass=0.9982`, `share_ge_target=0.9945`. The embedded seating report also records an over-capture
warning: 150 points, maximum 1.2977 mm against its 0.6 mm warning limit; this does not fail seating and the
lens was not shrunk to hide it. Print check passes as not required. **Fixed-scale AR: seven
poses, exit 0, captured_with_warnings, no errors**. Five warnings cover two lens-decal classifications, two
overlap-window saturation readings, and the AR arm heuristic treating the rearward-wrapped brow as arm geometry.
The earlier zero-area temple-triangle warnings are absent from the final capture.

**Astra's summary, condensed (not an owner likeness verdict):** complete curved shield, vented black brow,
retaining rail/locking pods, hinge details, silver oval emblems, faceted swept temples, sleeves, nasal carrier,
rubber pads/ribs and small OAKLEY/PRIZM markings. The exact photographic interference coating is **not fully
matched**: Blender thin-film values were not carried into `KHR_materials_iridescence`; the delivered coloured
specular coating gives moving green/cyan highlights over violet transmission, but the green is too highlight-like
and photo-camera renders remain too violet. All six photos informed geometry. Final basis-01 contour means
are approximately 0.64 mm front, 1.06 angled, 1.42 right, 1.01 rear and 1.20 left; these do not certify likeness.
Top fitting retained an approximately 90-degree image-plane orientation error after a targeted retry (IoU
0.3233); Astra inspected custom rolled top renders instead. Facets, far-shield side silhouette and tiny lettering
remain approximate. The temple specialist never accepted the authored knuckle axes. The rearward brow still
triggers the AR positional-arm warning. Scale is estimated. These limitations remain open for owner review.

**Webcam review started** through `job review ... --port 0` and verified ready/rendered at port **52798**,
detached server PID **24984**, bound to the immutable delivery SHA above, fixed lens environment intensity 1.
The app browser-open request was queued. Full launch URL and identity are in the job's `review` records.
AR contact sheet (immutable final delivery):
`data/jobs/oakley-oo9208-v2-3e057bab/deliveries/20261013T173404-2accf4-cf35d209a42d0149c739654e5e046f58a46d405c93ea362e11ef629a7f675904/contact-sheets/01.jpg`.

**Additional host diagnostic issue found, left open:** `temple_lab/package.py` replaces any unsuccessful
`export_head` with "parent export does not match its current checkpoint". In call 25, c0005 had unsupported
`partRole='hinge'` values and no cache entry; the source evidence supports export refusal, not a stale cache.
A later general fix should preserve the original export refusal and reserve the mismatch message for an
otherwise successful export with a genuinely wrong binding. No active-run patch or role-gate relaxation made.
No other paid run was started. M5 owner visual acceptance remains pending.

## Assembly M5: checkpoint and failed-draft repair (2026-10-13; $0; commit `123dcca`)

Owner request: identify the exact unstable property, repair the general checkpoint method, prove a mixed Blender
scene and the original tool journals round-trip, salvage failed runs from their last verified checkpoint, run
the full suite and commit before any new paid request. The owner also approved one **fresh** run on
`oakley-oo9208-v2-3e057bab` after those steps pass and its fresh estimate is below $20, with the same $20 settled
stop / $23 maximum reserve / max effort. The older `cd068969` job remains out of scope.

**Exact cause:** the field-by-field $0 comparison of every `_curve_digest`/`_rna` component found only these
automatic texture-space changes in the orphan curves left by text-to-mesh conversion:

| Datablock | Property | Before save/reload | After reload |
| --- | --- | --- | --- |
| Centre Oakley micro etch | `texspace_location.x` | 1.8925000429153442 | 1.8924999237060547 |
| Centre Oakley micro etch | `texspace_size.x` | 1.847499966621399 | 2.8474998474121094 |
| Centre Oakley micro etch | `texspace_size.y` | 0.3500000238418579 | 1.3499999046325684 |
| Prizm lower corner etch | `texspace_size.x` | 1.3245002031326294 | 2.32450008392334 |
| Prizm lower corner etch | `texspace_size.y` | 0.3410000205039978 | 1.3410000801086426 |

Both have `use_auto_texspace=true`. Every other raw curve component matched, and the original checkpoint SHA
stayed unchanged. Evidence: `data/assembly/m5curve-components/{before-components,after-components,diagnostic}.json`.
The converted leftovers expose `Curve` RNA; the separate live/orphan/fake text fixtures retain FONT content.

`g3d/checkpoint.py` now excludes texture-space bounds **only in automatic mode**, retaining manual bounds and
the mode flag. `protect()` retains zero-user members of all 23 collections already fingerprinted. Broader
fixtures exposed two more serialization differences: factory `ParticleSettings.trail_count` 0 becomes 1 on
reload (canonicalized to the RNA minimum), and volume file paths are remapped by save-as (canonicalized to the
absolute target, including Blender's empty factory-path behavior). Valid trail-count and real path changes
remain detectable. Value/RGB node output values and action custom properties are now fingerprinted too.
No blanket exclusion, fingerprint re-stamping, original checkpoint edit or quality-gate relaxation.

Real Blender mixed-scene regression: **1/1 passed in 8.84 s**, with **43 explicitly retained ID fixtures and 64
authored-content mutation controls**: live/orphan/fake text, font and formatting, converted leftovers, Bezier
and NURBS geometry, meshes/modifiers, node materials/groups, generated/packed pixels, and absolute/relative
volume paths across save directories. Evidence: `data/assembly/m5-roundtrip7.log` and
`data/assembly/m5-roundtrip-pytest7/test_mixed_scene_roundtrip_and0/report.json`.

**Second independently reproduced host defect:** the exact replay exposed a render-only change to the user
scene's `length_unit` (MILLIMETERS to METERS). Setting the temporary scene's unit system makes Blender's update
callback act on the current context scene; the minimal reproducer also showed GRAMS to KILOGRAMS. The renderer
now scopes those writes with `bpy.context.temp_override(scene=sc)`. Authored unit fields remain fingerprinted.
Real Blender regression **1/1 passed**, covering **8 scenarios** (metric/imperial, either authored scene active,
success/injected exception); every unit field, fingerprint, original context and cleanup matched.
Evidence: `data/assembly/render-unit-callback.json` and
`data/assembly/m5unitstest/test_temporary_render_units_ne0/report.json`.

**Exact original journal replay passed at $0** in 12.484 s: all **5 tool calls** (of which **3 contained code**),
with the original byte-bound arguments/snippets and original static refusal preserved. Save/restore matched
after every call, rendering created 12 images without scene drift, a fresh worker reopened c0001 exactly, and
all 91 original evidence files stayed unchanged. No model transport was constructed or external network used.
Evidence: `data/assembly/m5r1replay3/proof.json`, log/status/receipt `data/assembly/m5fix_replay3.*`.

**Failed-run draft delivery:** `failed` now selects the newest checkpoint whose original bytes and reopened
fingerprint verify, in a private worker, with every attempted checkpoint recorded. Only an integrity failure
can fall back to an older checkpoint. An export or quality failure never selects a more flattering older
model: normal export refusals remain refusals, and validation/seating/print/AR failures are reported in drafts.
The active worker must be closed; job head, best marker and journals do not move. A finalized failed result
raised through `SystemExit` is also salvaged before the original signal propagates. Focused tests **139/139**
plus the SystemExit follow-up **1/1** passed. Real Blender/AR failed-draft proof **15/15 passed in 17.938 s**,
including failed checks kept visible, immutable delivery, review and source preservation. Evidence:
`data/assembly/m5fd/proof.json`, `data/assembly/m5_failed_draft.*`. Independent code reviews found no blockers.

Full Python suite: **3,463 distinct tests passed** across the required main run and reconciled reruns. Main run
used `-n 8 --dist loadfile`: 3,143 passed, two failures (the known EcoQoS-watcher interference and the 60-second
render check under load), four historical-fixture setup errors, 718.65 s. Photoprint then used `-n 2 --dist
loadfile`: **314/314 passed in 213.90 s**. The QoS test passed alone with the descendant watcher disabled
(1/1, 0.30 s); complete `test_wp2_seeing.py` and `test_seeing_v3.py` passed sequentially alone (**54/54**, 113.11 s).
No time limit or assertion was changed. Logs/status/receipts: `data/assembly/m5fix_main.*`, `m5fix_print.*`,
`m5fix_isolated_qos.*`, `m5fix_seeing.*`.

The four setup errors were a test fixture importing a historical run-7 checkpoint index made by the older
fingerprint algorithm. This test evaluates seeing on revision-7 geometry, not historical-session compatibility.
It now verifies archived c0007 bytes against the original SHA (`3b4435b27313854367aec10c389087c9cad3b36d3d9311b03c1d3aaeed5924e1`)
and uses those exact bytes as an isolated test seed with a fresh checkpoint chain. New SHA/fingerprint restore
is explicitly checked; original index, original model and copied seed bytes are checked unchanged at teardown.
All visual assertions, photos, cameras, geometry and thresholds are unchanged. Independently reviewed.

AR: **497 unit tests + 103 QA tests passed** after isolated reruns, and `tsc --noEmit` passed. The concurrent
first pass had one hair-scheduling timing failure (496/497) and one Chromium connect-abort timing failure
(102/103); the entire affected modules then passed alone (**12/12** in 19.72 s and **5/5** in 36.55 s) with no
code/assertion change. Logs: `data/assembly/m5fix_ar_{unit,qa,check,hair,stall}.*`. `git diff --check` passed;
the complete runtime/test diff was independently reviewed. Repairs, proofs and full-suite verification are
complete at **$0**. The accompanying implementation commit precedes the fresh estimate/approval/run.

The first run's model still has unsupported part roles; it was not hand-corrected into a deliverable. The fresh
run will start from the empty job seed after preserving all failed-run evidence. M5 owner visual acceptance
remains open; no product quality result is inferred from these tool tests.

## Assembly M5: corrected Oakley run 1 (2026-10-13; failed, preserved for diagnosis)

Verified the requested checkout `C:/Users/Shay/PycharmProjects/lenses`, branch `glasses3d`, clean working tree,
and HEAD `f3a0e10` before approval. Used only Claude's corrected job **`oakley-oo9208-v2-3e057bab`** and existing
run request **`20261013T163235-772629`** (request SHA `53181395cde6d5c2cd83a70bff7fc4f349ed9df9bdc9d61dd4a63be04e96c5df`).
The older `cd068969` job was untouched. Numeric estimate: **$14.957821**, range **$13.200719-$20.117690**.

Recorded these exact owner words with `job approve --max-usd 23`:

> I approve Oakley run 1: job oakley-oo9208-v2-3e057bab, run request 20261013T163235-772629 (estimate $14.96, range $13.20-$20.12), with the $20 settled stop and $23 maximum reserve, effort max. Do not use the older job oakley-oo9208-v2-cd068969; Claude replaced it (see PROGRESS.md Decisions, commit f3a0e10).

Approval bound **max effort, $20 settled stop, $23 reserve cap**. Started once through detached `job run` with
the existing `--env-file glasses3d/.env` loader; no credential file was read or printed by the agent. Keeper PID
9708, runner PID 20392, Blender PID 38984; detached start 16:34:56 UTC / 19:34:56 Jerusalem. BLAS threads 1,
descendant EcoQoS opt-out, no competing heavy pool; approximately 17 GiB free commit at launch. The monitor
checked heartbeat, requests, tool progress and worker activity every 40 seconds and reported to the owner.

**Outcome: failed**, exit 1 at **16:39:22 UTC / 19:39:22 Jerusalem** (run duration 262.78 s). Five API requests,
five tool calls, no continuation. One capability-inspection snippet was statically refused and Astra corrected
it; then Astra built **28 mesh objects** in checkpoint **c0001**. Its first `render_views` wrote 12 PNGs (four
views, native/clay/normal), but the read-only operation's checkpoint restore failed:
`c27ae78f2a5b93963b2ddde432591d37dc5eb90c19e17a8f1643f45af50d7261 != bbc084bc33c76e029e0ce48ca829b6641a54684c4240673c55beffcd19c7182c`.
The original c0001 `.blend` SHA is `fce890aea9ff39eff87c068d823f6abca43dc24618de4a44037e4a608b1b71de`;
the run records `exit_checkpoint.verified:false`. No subsequent paid request was started.

**Actual settled cost $0.828903**, maximum individual request hold **$1.167888**, final held **$0**, unknown **$0**.
Six independent evidence checks passed: bound approval/settings, re-priced run ledger, ten transport receipts,
result/job-ledger agreement, all six original photo blobs, and explicit absent delivery. The audit also confirms
33 evidence files unchanged. This is an accounting/integrity pass, not a successful product run.
Evidence: `data/assembly/m5oakley/run1-audit.json` and `data/assembly/audit_oakley_run1.py`; the job's
`runs/20261013T163235-772629/{result.json,budget.json,transcript.md,runner-status.json,monitor.jsonl,transport/}`
and `budget/job-ledger.json` hold the original run records.

**Astra summary:** no final summary was produced. Its public pre-build message was:

> I’ll reconstruct the curved shield, vented brow and faceted temples from all six views. For the lens, I’ll test curvature and an angle-dependent coating so the green/cyan region shifts with the view rather than becoming a painted patch. Since no measurements are supplied, the AR scale will be an estimate.

**Validation and seating:** neither was reached; there is no GLB validation result, no seating result and no AR
contact sheet. `delivery.json` honestly says `not_finished`, `run_status:failed`; `failed` is outside the current
host-draft statuses. Attempted `job review ... --port 0`; it refused `delivery file is missing or unreadable:
current.json`, so no webcam review server was started. The unvalidated native three-quarter render remains at
`data/jobs/oakley-oo9208-v2-3e057bab/work/renders/call_AcKMPcxcQz5kIXp9QdOcf30t/three-quarter_native.png`.

**$0 diagnosis on a COPY:** replayed Astra's exact saved construction code from the copied seed and compared
full scene states before/after save/reopen, independently of rendering. Reproduced both exact fingerprints;
only the two orphan FONT-curve datablocks (`Centre Oakley micro etch`, `Prizm lower corner etch`) differed.
All object, mesh and material component hashes matched. Original checkpoint SHA unchanged, zero API calls.
Independent review confirmed the diagnosis. Evidence: `data/assembly/m5r1diag/{before,after,restore,diagnostic}.json`
and `data/assembly/diagnose_run1_checkpoint.py`. The exact unstable curve field still needs a minimal reproducer.
No integrity check was bypassed, fingerprint re-stamped, source gate loosened, or product geometry changed.

Independent source review also found 18 visible objects tagged `hardware`/`nosepad`; the exporter permits only
`lens/frame/temple`, so these are a separate expected export refusal requiring Astra's correction, not a host
hand-edit. Repairing save/reopen alone would not complete this early model. Expanding M5 into checkpoint repair
and failure-draft handling needs the owner's scope decision under their stated rule. No tracked runtime code
changed and no full regression suite was rerun for this evidence-only record. M5 product acceptance remains open.

## Assembly M5-prep (2026-10-13; complete, implementation commit `bab7cb4`)

Verified the requested checkout and branch `glasses3d` at `68e1ebf`; the owner's subsequent instruction adds
`faee122`'s first-message working-limit correction and `4b9d6d2`'s temple-stage allowance correction. The owner explicitly approved exactly one smoke in advance:
**"Yes, cap $1"**, only after M5-prep items 1-4 pass and a fresh estimate is under $1, with both settled stop and
reserve cap at $1. Oakley run 1 remains unapproved and must not start. Preparatory implementation and tests are $0.

Item 1: the guard now reports the settled stop separately from reserve capacity and computes remaining runway
after settled spend plus held and unknown requests, clamped at zero. First-message run text and tool footers use
the working settled stop. At 75% of settled spend the footer tells Astra the remaining runway and to finish its
best checkpoint soon. Generic tools gain a separate final text footer, preserving their initial JSON and image
indices; ordinary SDK errors carry it too, while fatal exceptions still propagate. Nested stage contexts read the
shared guard. Legacy runs without a settled stop retain their old cap footer. Instructions add the requested
finish guidance within the unchanged 8,000-character limit (7,962 characters), preserving the owner's exact table.
No reservation or settled-stop enforcement rule changed. Initial baseline: 253 passed, four stale output-shape
assertions updated to check the added footer while retaining their prior JSON/image/error assertions. Final
targeted set passed **291 unique tests** across the final runs: 285/286 in 57.29 seconds, followed by 132/132
in 8.56 seconds after correcting one new fixture's existing `canonical:front` label and adding five boundary/error
checks. The notice uses integer micro-USD, including the exact $0.075/$0.10 boundary. Generic SDK timeouts use
their existing message plus the footer. Evidence: `m5prep_runway_{base,final,edges}.log`. The added `4b9d6d2`
requirement is implemented: a temple-stage allowance cannot exceed the smaller of remaining reserve capacity and
remaining money to the settled stop (including held/unknown requests). Fifteen new cases cover boundaries,
exhaustion, holds, invalid arguments and legacy runs. Item 1 is complete. All checks use offline transports.

Item 2 in progress: draft review accepts an explicitly unfinished immutable delivery with a failed specification
check while preserving all server, hash, appearance and infrastructure checks. Accepted deliveries still require
a passing live specification check. Saved review records include the draft's run status and failed checks; edits
that promote it to a finish or hide a failure refuse. Review unit checks: **37 passed, one real-server test
deselected, 3.23 seconds** (`m5prep_draft_review_unit.log`); exact draft changes notes enter resume input once.
The publisher now exports a hash-verified private copy of best (else head), checks specification/seating/print,
captures fixed-scale AR, and publishes immutable `finished:false` evidence without moving the source scene or
claiming an accepted finish. Each supported end status retains a delivery attempt, including a clear reason when
no usable checkpoint exists. `job show` includes the draft and its failed checks. Focused draft/delivery/CLI/review/
verdict suite: **179 passed in 16.42 seconds**, including the real review server (`m5prep_draft_unit2.log`). One
additional changed-head-SHA regression was added after that collection and awaits the next run. Independent
publisher review fixed source-variable shadowing and strengthened draft-only seating SHA/fingerprint validation;
accepted-delivery checks remain unchanged. The stage/draft edge run passed **74/74 in 15.45 seconds**, including
the additional changed-head-SHA case (`m5prep_temple_draft_edges.log`). The real Blender/AR proof passed all
**15 checks in 12.91 seconds** (`data/assembly/m5d1/proof.json`, `m5prep_draft_real2.log`): marked best c0001
was exported while head c0002 and the original checkpoint/index/history bytes stayed unchanged; failed seating
remained explicit in the report, `job show` and live review; fixed-scale AR, immutable snapshots and exact
verdict-to-resume notes all passed. The first fixture used a 260-character Windows image path; shorter fixture
names fixed that test-only path issue without changing assertions. Its diagnostic is retained in
`data/assembly/m5prep-draft-real-20261013a/path-length-diagnostic.json`. No model request, production transport,
external network attempt or paid spend occurred. Item 2 is complete.

Item 3: `job estimate --smoke` and `job run --smoke` use a distinct approval-bound policy, one request with
4096 output tokens, identical working stop/reserve cap, the full real job instructions/brief/photos, and literal
`tool_choice=required`. After the unchanged canonical tool-contract check, the executable registry contains only
`scene_summary` and `budget_status`; a shared host counter allows one call before ending. Smoke history stays in
its own run directory, leaving ordinary modeling history fresh. There are no retries, continuation requests,
mutating tools or product deliveries in this mode. The one-request forecast counts all photos as fresh input;
the historical two-turn Studio forecast remains unchanged. The live count/reservation remains the money bound.
Independent review found no blocker. Existing regression tests: **184 passed in 58.09 seconds**; new smoke
composition and SDK transport tests: **43 passed in 19.77 seconds**. First-run failures were two new fixture
mistakes (photo filenames and JSON tuple/list comparison), corrected without weakening checks. Evidence:
`m5prep_smoke_base.log`, `m5prep_smoke_new2.log`. Item 3 is complete at $0.

Item 4: visually inspected all six originals and created `oakley-oo9208-v2-cd068969` through job intake.
Labels (wearer's side, as in cameras.py): 01 front/both; 02 angled/left; 03 side/right; 04 rear/both;
05 side/left; 06 top/both. All are authoritative, copied byte-for-byte and SHA-bound. `open_frame` matte preserves
the semi-rimless shield's openings. The old `job.json.description` is empty; the actual user description is its
`specs.notes` (provenance kind `user`), copied verbatim into the new description and specifications. Old host prose
that downgraded photos was not carried forward. The specifications separately record ASSEMBLY M5 step 1's exact
scope, "frame + lenses + seating only." Intake provenance and views live in `data/assembly/m5oakley/`.
`job estimate` prepared run `20261013T155843-7c80ac`: expected **$14.966873**, growth-only range
**$13.209792-$20.127653**, 14,880 initial context tokens, 60 replies as forecast only, modelled last-request hold
$3.875263. Execution remains max effort, $20 settled stop/$23 reserve and no reply cap. The forecast is not a
spend guarantee; its historical corpus predates image pruning and contains one product. **Run 1 is unapproved.**
The real-photo scripted proof uses a byte-identical intake copy, as required for $0 tests, so the actual job does
not inherit simulated ledger spending or fabricated assistant history. The proof passed **12/12 checks in
8.25 seconds**: eight successful read-only tools/requests (six `read_image`, `scene_summary`, `budget_status`),
twelve verified image blobs, complete brief/photo/approval binding, and a host turn limit before a ninth request.
The original job, including its unapproved estimate, stayed byte-for-byte unchanged. Its copy's ledger settled
$2.314415 in **simulated** usage; **actual spend was $0**, with zero production transports or external network
attempts. Evidence: `data/assembly/m5i1/proof.json`, `m5prep_intake.log`. Item 4 is complete.

Final regression checks are running: eight workers/loadfile for non-print tests, then two workers for print;
no competing heavy pools, BLAS 1 and EcoQoS opt-out. AR unit **497/497**, QA **103/103**, TypeScript clean.
Final review found that a post-teardown Ctrl-C/task cancellation could re-raise before draft publication; the
interruption cleanup path is now implemented and independently reviewed: only a fresh, finalized stopped receipt
for this exact run can trigger salvage, then the original interruption is re-raised. Smoke/stale/malformed results
are excluded, and existing SystemExit failure classification stays unchanged. Focused draft/job/smoke/interrupt
checks passed **111/111 in 54.19 seconds** (`m5prep_interruptions.log`); the added actual SDK cancellation case
also passed **1/1 in 8.80 seconds** (`m5prep_cancel_sdk.log`).
The environment lacks an API key, but the owner confirmed "both .env have open ai keys" after
requesting their links. Added explicit `job run --env-file` plumbing to the existing runner credential loader;
the approved smoke will use `glasses3d/.env`, without exposing its contents. The agent has not read or printed
credential files. Credential-path propagation plus existing job/CLI/smoke checks passed **68/68 in 12.53 seconds**
(`m5prep_credentials.log`), with fake nonexistent paths and read tripwires. The new smoke estimate
`20261013T161246-478a2a` is **$0.256900**, estimated reservation **$0.390800**, with $1 settled stop and $1 cap;
its real live token count still controls admission. No paid request has started.

Main suite: **3,075 passed, five failed in 690.48 seconds**. Two stale mocks in the new draft tests were already
corrected and passed in the 111-test follow-up. The isolated seeing/temple-UV modules then passed **24/24 in
129.84 seconds**, including both unchanged render/subprocess timeouts. EcoQoS passed **1/1 in 0.30 seconds**
without the external descendant watcher that alters its measured initial state. No thresholds, timeouts or quality
gates were changed. Current non-print coverage is **3,120 unique tests passed**, including 22 interruption and
18 credential-path cases added after the main collection. Print passed **314/314 in 207.56 seconds** with two
workers. Final coverage: **3,434 Python tests passed** after the documented targeted/isolated reruns, plus
**497 AR unit + 103 AR QA**, and TypeScript clean. All preparatory work cost $0. No check was loosened.
Evidence: `m5prep_{main,isolated,qos_qos,collect,print,ar_unit,ar_qa,tsc}.log` and adjacent process receipts.

### Approved smoke (M5 step 0; 2026-10-13, passed)

After items 1-4 and the full suite passed, committed the implementation as `bab7cb4` on `glasses3d`, with explicit
paths and no push. Recorded the owner's exact **"Yes, cap $1"** in the smoke approval and executed
`job run --smoke oakley-oo9208-v2-cd068969 --run-id 20261013T161246-478a2a --env-file glasses3d/.env` through the
existing credential loader. There was **exactly one real request** and one successful read-only `scene_summary`
call; it ended `completed` with `smoke.ok:true`, no retry/continuation or modeling work.

**Actual settled cost: $0.211823 (211,823 micro-USD)**, against the fresh $0.256900 estimate. The live count was
16,227 input tokens; the guard reserved **$0.407638**, below the approved **$1 settled stop/$1 reserve cap**.
Both request/response receipts are present, all six original photo blobs re-hash correctly, and the run ledger
re-prices successfully. The job ledger contains one settled request, with **$0 held and $0 unknown**. Tool journal
and normal-history checks confirm read-only operation and an isolated smoke conversation. No product finish or
delivery was claimed. Runner exited 0 at 16:21:19 UTC. Audit: `data/assembly/m5oakley/smoke-audit.json`.

Receipts and run ledger are under
`data/jobs/oakley-oo9208-v2-cd068969/runs/20261013T161246-478a2a/` (`transport/`, `tools/`, `input-receipt.json`,
`transcript.md`, `budget.json`, `result.json`); the cumulative ledger is `budget/job-ledger.json` in that job.
This is usage-priced settled cost from the pipeline ledger; no secrets or environment files were committed.

Refreshed the **unapproved** Oakley run-1 request after smoke startup created the empty seed, so its input binding
is current: **`20261013T162219-edaa19`**, replacing the earlier pre-seed estimate for approval purposes. It remains
**$14.966873 expected**, growth-only range **$13.209792-$20.127653**, $20 settled stop/$23 reserve, max effort,
60 replies for forecasting only. The actual modeling run has no reply cap. The six labels above are unchanged.
**Oakley run 1 has no approval or run ledger and was not started.** No open M5-prep defect; further paid product
validation remains the owner's decision.

## Assembly M0 (2026-10-13, $0; complete, implementation commit `27db2c0`)

Started in the requested repository checkout on branch `glasses3d`, at `02b0a16`; no worktree, push or paid API call.
Implemented all eight M0 items:
- one fixed paid-run lock under `glasses3d/data/` for lab packages and ordinary jobs;
- every head export carries `validate_spec`, declared-seat checking and the validation's GLB sha256; cached by
  actual bytes within a Seeing instance, with old export indexes validated again (never trusting old reports);
- facet finish refuses an invalid export as `E_EXPORT` and keeps the run open;
- a bounded Git diff pathspec preserves snapshot exclusions/deletions without Windows argv growth;
- three free token-count attempts with 0.5/1-second backoff; inference still never retries;
- `turns_used` comes from the interaction log in `finally`, including abnormal exits;
- print fixture pools capped at two, with the split/loadfile full-suite commands documented;
- approval-bound `RunSettings.settled_stop_usd`, separate from the reservation cap. The generic job path will set
  $20 settled / $23 reserve; legacy requests keep their existing settings when the new field is absent.

Technical choices: validation failures remain inspectable by seeing tools; finish refuses them. A malformed declared
seat cannot silently skip checking. Offline tests isolate the fixed lock constant inside each test, while separate
job folders within a test still contend. No gate or numerical threshold changed.

Initial checks: export validation 6/6; snapshot regressions plus existing dirty-source check 4/4; facet-finish checks
8/8. Budget/loop/ledger targeted run: 187 passed, one new assertion corrected, then 8 stop/cap/approval cases passed.
Full suite: **2,465 passed after isolated reruns, 11 skipped**. Main run: 2,224 passed, 11 skipped, 4 failed in
603.37 s (8 workers/loadfile). The head-comparison, fit-speed and 28-image render timing checks passed alone;
the seeing module plus the two timing cases passed 24/24 in 83.71 s. The fourth failure was the local test launcher's
descendant EcoQoS opt-out changing the initial state that `test_opt_out_self_reads_back_and_is_not_inherited`
measures; without that external watcher it passed 1/1 in 0.25 s. No test was weakened. Print suite: 237/237 in
197.37 s (2 workers/loadfile, nested bake pools capped at 2). Arview: 497/497 unit, 103/103 QA, TypeScript clean.

Evidence: `data/assembly/m0_{main,rerun,qos,print,ar_unit,ar_qa,tsc}.log`, with arguments, UTC timestamps, PIDs and
exit codes in adjacent receipt/status JSON files. BLAS threads = 1, EcoQoS opt-out, no overlapping heavy pools.
Open: the 11 pre-existing stale WP4 proof skips remain for M4a; WP5/WP6 owner closeouts and historical quality
limitations are unchanged. No open M0 defect. Next: M1 job intake, generic scene hooks and registry, offline proof.

## Assembly M1 (2026-10-13, $0; complete, implementation commit `92c0187`)

Added the generic job CLI (`create`, `show`, `estimate`, `approve`, `run`, `stop`), explicit-view photo intake,
verbatim owner facts/model requirements, an empty millimetre template, persistent scene hooks and fourteen async
tools. Runs require a single-use bound approval; defaults are max effort, $20 settled stop and $23 reserve cap.
Photos retain their original bytes and hashes; large transport copies are also bound. Intake rejects missing views,
duplicates and colliding camera stems. EXIF orientation now agrees across intake, fitting, comparison and image reads.

The scene records mutations and per-tool journal receipts, recovers interrupted mutations from the checkpoint index,
and verifies the exit checkpoint. Finish requires a validated current export and successful fixed-scale AR capture;
delivery checks the finished checkpoint again. Worker failures propagate instead of becoming ordinary compare results.
Reference rendering now supports arbitrary filenames, spaces, Unicode and canonical-name collisions, using safe
output aliases while retaining the actual photo identity. Canonical camera names and path guards are unchanged.
Camera pools use free commit memory (1.3 GiB/worker with 2 GiB spare), CPU count and a ceiling of 16; BLAS threads = 1.

Proof: `python -m glasses3d.tests.proofs.assembly_m1` built synthetic wrapped glasses, rendered three truth photos in
Blender, created a fresh job, and drove the real SDK through eight scripted calls: build, summary, fit, reference
render, compare, validated export, AR, finish. The final proof took **71.735 s** (`m1_proof3.log`), delivered job
`synthetic-assembly-proof-926ab6dc`, and verified every reference render used its fitted camera (including `front.png`
and `angle photo.png`). All original fit gates passed: IoU >0.97, contour <1.5 px, yaw/pitch errors <1.5 degrees.
Receipt audit: 16 transport files, 8 successful tool journals, 8 settled mock requests, 9 verified image blobs and
14 AR images; no unknown holds, raw image URLs or fake key in text receipts. Mock settlement was $2.542402;
**actual API spend was $0**, with non-loopback networking and production transports blocked.

The first proof used native antialiased photo fringes and failed side-view IoU (0.9521, angle error <0.1 degrees).
The known-truth fixture now colours Blender's hard silhouette, matching the existing synthetic fit fixture;
no fit gate changed. The three overlaps on the passing run before filename expansion were 0.9972, 0.9972, 0.9881.

Full Python suite: **2,588 unique tests passed after isolated/added-regression runs, 11 pre-existing WP4 skips**.
Main (8 workers/loadfile): 2,307 passed, 25 failed, 11 skipped in 730.22 s. Failures were one timing check, the
launcher's EcoQoS interference, a 300-second shared renderer timeout and its 20 follow-on failures, the lab's variant
image check under load, and one outdated assertion that `budget_status` had no timeout entry. The assertion now
checks the new tool deadlines and still refuses an unknown tool. Isolated rerun: 58 passed in 65.79 s; EcoQoS:
1 passed in 0.29 s. After the filename fix, the full Seeing module, new routing regressions and registry tests:
74 passed in 85.49 s. Print (2 workers/loadfile): 237 passed in 184.16 s. Arview: **497 unit + 103 QA passed**;
TypeScript clean. No timeout, numeric gate or quality threshold was increased.

Evidence: `data/assembly/m1_{main,rerun,qos,print,ar_unit,ar_qa,tsc,seeing,proof3}.log`, with UTC timestamps, arguments,
PIDs and exit codes in adjacent receipt/status files. No push or paid call. The first failed proof's temporary
fixture is local/untracked at repository-root `data/assembly/m1-proof-b8bb88d4`; automatic approval review blocked
its cleanup move with the stated reason "blocked by policy". It was excluded from the explicit-path commit.
Open: M2 instructions/image policy/estimate, M3 owner review, M4 specialist stages, and the existing WP4 skips.
A pre-existing library-only compare crop-name collision (`crop-01` photo plus generated `crop-01`) is not exposed by
the generic registry (which has no crop argument); keep it in view when assembling M4 packages. No open M1 tool defect.

## Assembly M2 (2026-10-13, $0; complete, implementation commit `0f2d282`)

Replaced the old WP7 proposal with object/evidence/stage teaching and an actual registry contract. The prompt quotes
the owner's seven-row model table exactly, preserves D1-D4, explains each current report, and names specialist tools
only when registered. Size: 6,836 characters now, 7,923 with all seven planned specialists, under the 8,000 cap.
Sixty-eight instruction checks passed, including retired-language witnesses and real report-field contracts.
The brief has one structured source for host prose and verbatim owner data: the retired-language check scans host
prose, while the complete owner description/specifications/names/photos remain approval-bound. This implements the
design's owner-text exclusion; it does not relax any geometry, export, budget or quality check.

Generic runs now apply their image policy before request 1 and every three requests thereafter, plus the existing
pressure triggers. Image occurrences are keyed by call, output position and digest; a new cached image cannot be
mistaken for an old one. Every image has an adjacent call/checkpoint-or-source-revision/view label. Photos and their
crops stay; superseded generated evidence becomes recoverable notes with paths, retaining the latest evidence per
view, current picks and the selected best checkpoint. `save_checkpoint(best=True)` makes that selection explicit and
durable. A new best selection can restore old images from the unchanged session on resume. Legacy facet settings
without a cadence retain their existing behavior. M4's nested facet stage must opt into the assembled image policy
and carry these labels when that stage is registered.

The scripted ten-request proof exercised epochs 1/4/7/10, identical counted/sent payloads, duplicate image bytes,
best-state restoration at resume, retained photos and intact session history. Image-policy/tools/session-input checks:
88 passed before the final additions; final affected set 71 passed in 24.26 s, plus the path assertion and the new
unspecified-view label passed in the logged late run (2/2). Fixture-only correction: the best-state test's FakeSession
needed the real checkpoint index. No production gate was changed.

Recalibrated job estimation on all eight complete paid glasses runs: 231 requests, $48.768638 recorded settlement.
The token-only fixture is reproduced from verified canonical ledgers; no prompts, photos, keys or provider IDs are
copied. The causal model uses an uncached first request, reuse of the preceding prefix, and token-exposure growth.
Honest leave-one-run-out median absolute error is **6.4634%**, within the unchanged <=10% check; independently fitting
the same method to all thirteen Studio runs gives **8.6787%**. The original Studio forecast and exact tests remain.
All 34 estimate checks passed. Individual glasses-run errors range -26.07% to +31.14%; the forecast states that the
corpus covers one product's temple stage, predates pruning and assumes no pruning savings. Live money guards govern.

End-to-end offline assembly proof passed in **71.875 s**, job `synthetic-assembly-proof-2bf7632f`: eight scripted real
SDK tool calls, actual Blender fitting/rendering, validated export and fixed-scale AR. All fit gates unchanged and
passing; 16 transport receipts, 8 tool journals/settlements, 9 verified image blobs, 14 AR images, clean receipts.
Mock settlement $2.718465; **actual API spend $0**, production transports and non-loopback networking blocked.

Full suite: **2,690 unique Python tests passed after isolated/late reruns, 11 existing WP4 skips**. Main run:
2,448 passed, 4 failed, 11 skipped in 682.30 s (8 workers/loadfile). The failures were the already-fixed fake-index
fixture, 50 ms fit timing under load, the launcher's EcoQoS watcher changing a measured child default, and the lab's
variant-image check under load. First three applicable cases passed alone 3/3 in 34.45 s; EcoQoS passed without the
external watcher 1/1 in 0.25 s. One new unspecified-view test was collected after the main run and passed separately.
Print: **237/237 in 184.55 s**, two workers/loadfile. Arview: **497 unit + 103 QA passed**, TypeScript clean.

Evidence: `data/assembly/m2_{main,rerun,qos,print,proof,root,late,ar_unit,ar_qa,ar_tsc}.log` and adjacent UTC/argv/PID/
exit receipts. `m2_image_policy.receipt.json` records the agent's captured targeted-test output. Free commit before
main/print was 22.47/23.75 GiB; BLAS 1, EcoQoS opt-out, no overlapping heavy pools. No push, paid call, loosened gate
or open M2 defect. The M1 root `data/` artifact remains untracked after its blocked cleanup. Next: M3 delivery and
owner review; the existing WP4 proof skips remain scheduled for M4a.

## Assembly M3 (2026-10-13, $0; complete, implementation commit `ebd5f47`)

Finished jobs now publish immutable, content-bound deliveries: GLB, validation/seating/cost/checkpoint report,
raw AR evidence, original contact sheets and the images actually shown to Astra. Every file's size and SHA-256
are verified; an existing delivery is never overwritten. The current pointer and flat `deliverable/` copies remain
convenient entry points, while verdicts identify immutable delivery IDs and GLB/manifest hashes. Publication requires
an accepted finish, verified exit checkpoint and successful fixed-scale AR evidence for those exact bytes.

`job review` starts the existing AR server through its Windows WMI detach path, verifies the live report and served
GLB independently, and saves which delivery was reviewed. It uses the same fixed `lensenv=1` appearance as capture
(D2), survives the launcher, refuses busy ports and never stops an unrelated server. `job verdict accept|changes`
defaults to that reviewed delivery even after another delivery becomes current. An explicit ID or expected GLB SHA
is also supported. The append-only, locked verdict chain detects edited/truncated/incomplete evidence. Notes remain
verbatim, are bound into the next approval and enter a resumed SDK conversation once according to its actual history.
Adding notes after approval invalidates the approval; no separate sent flag can lose a note on a failed run.

Migrated the actual legacy ledger without modifying its source: **7 preserved records, 3 whole-product acceptances,
4 scoped print/method decisions archived without promotion to product acceptance**. Repeating migration produced the
same result. Source SHA-256 `9b73e59170c27bd83e0d9e8c1e3744cf2e3b93f34361b6d198b6b72964a8a5a5`;
local outputs `data/ledger/verdicts.jsonl`, `verdicts.legacy.json`, `verdicts.migration.json`.

Proof: `tests.proofs.assembly_m3` passed in **82.094 s**, using fresh synthetic job
`synthetic-assembly-proof-3fcb965a`. It ran M1's actual Blender/fit/render/AR path, reviewed the immutable model in a
real detached server, recorded a synthetic changes verdict and resumed twice through the scripted real SDK.
Notes sent: **1 then 0**; all saved inputs contained the note exactly once. Both resumptions created new delivery IDs;
the first model/report/manifest/contact sheet stayed byte-identical and the live server continued serving that first
GLB. Receipt/image audits remained clean. **Actual API spend $0**; production transports and non-loopback networking
were blocked. Targeted checks: CLI 23/23, delivery 45/45, verdicts 26/26, review plus AR tool checks 59/59.

Full suite: **2,787 unique Python tests passed after isolated reruns, 11 existing WP4 skips**. Main run:
2,548 passed, 2 failed, 11 skipped in 616.70 s (8 workers/loadfile). The unchanged 28-image renderer timing check
took 120.24 s under load against its 60 s gate, then passed alone (whole test 17.08 s). The other failure was the
external launcher's EcoQoS watcher changing the initial child state measured by the opt-out test; without that
watcher it passed in 0.25 s. Print: **237/237 in 182.83 s**, two workers/loadfile with capped nested pools.
Arview: **497 unit + 103 QA passed**, TypeScript clean. No gate or threshold changed.

Evidence: `data/assembly/m3_{proof,cli,main,rerun,qos,print,ar_unit,ar_qa,ar_tsc}.log` and adjacent UTC/argv/PID/exit
receipts; targeted agent logs `m3_delivery.log`, `m3_verdicts.log`. Free commit before main/print was 23.33/23.29 GiB;
BLAS 1, EcoQoS opt-out, no overlapping heavy pools. Reviewed the complete M3 changes and the notes/verdict/proof
integration independently. No push or open M3 defect. The earlier root `data/` fixture remains untracked after the
automatic approval review blocked its cleanup move (reason: "blocked by policy"). Next: M4a, then M4b/c/d in order.

## Assembly M4 (2026-10-13, $0; complete, implementation commit `876c439`)

M4a adds five strict JSON seating tools through the journaled `g3d_call` allowlist; those helpers are no longer
accessible through snippets. Applied partial results keep their full domain report and a checkpoint, while refusals
restore the exact head. Read checks preserve failed quality results without mutation. Every export now carries the
all-frame seating check (including crystal and undeclared construction), and new finish/delivery requires it to pass.
Historic immutable deliveries retain their recorded evidence. Profile rollback removes generated cutters/Booleans
and cords before restoring the mesh, original metadata and backups, including exceptions after profile creation.

Focused checks: rollback/hash 10 passed; trusted dispatcher/facade 307 passed; real worker metal/browline integration
2 passed; export/delivery 107 passed; generic tools/instructions/CLI 135 passed before the read-envelope follow-up;
the final tool module passed 46 tests. Fresh WP4 proof: 27 cases, 262 records, no execution errors, 142 seconds with
14 workers (23.29 GiB free commit before launch). All four original blend hashes and all measured geometry/verdicts
match the prior proof; only crystal Boolean timing changed (0.353 seconds, below the unchanged 2-second limit).
`test_seat_real`: 24 passed, zero skipped, 129.71 seconds. Evidence: `data/assembly/m4a_*` logs/receipts and the
fresh `data/wp4/proof.json`; no paid API call.

The ten known WP4 quality failures remain failures: P2 71/71, P3 34/40 (+2 report-only), P4 109/112
(+2 pending readings), P5 31/32 (+1 N/A +2 report-only). Technical recommendations, without gate changes:
- Metal needs a general curvature degree of freedom: interior 0.4253/0.4262 is below 0.44 even though endpoints
  reach 0.45; a uniform 0.45 target overshoots to 0.4748, above the existing 0.47 upper limit.
- INVU needs joint node/interior constraints: interior minima 0.44885/0.4489 miss the strict 0.45 proof floor
  despite nodes at 0.450048. Do not borrow the runtime 0.005 tolerance for the stricter proof.
- FOLD remains partial/refused where curvature cannot support the move (4 direct and 21 knock-on failures;
  ungrown nasal minimum -0.305). Keep the refusal; broader curvature/propagation work needs its own scope.
- The narrow web's nominal 0.5 mm has measured capacity 0.3046–0.3067, above the 0.30 limit, so 96 moves and
  partial status are honest. Keep the zero-move proof failure and the original fixture.
- Keep `CORRECTION_AIM_MM=1e-4`: it compensates float32 error of about 1e-5, not a measurement tolerance.

An additional generic workflow replay exposed that the camera proof's lens discs were open. Its closed fan was
then too sparse to support the recognizer's local cap normals (9.0% cap area). The assembly fixture now uses the
existing default ten-ring curved plate (95.1% cap area), preserving the 48-point ellipse, wrap and 1 mm thickness.
The procedural snippet exactly matches that fixture's vertices/triangles, at 45,100 bytes. Actual Blender build,
both seats and exported checks passed in 8 seconds. The full 11-call workflow plus delivery/review/two resumes then
passed in **92.828 seconds** (`m4a_generic_proof3`, exit 0), with no unknown holds and $0 actual spend. No production
quality gate or camera threshold changed. M4a is complete; M4b is active. M4c–d and full M4 suite/commit are pending.

M4b in progress: the generic job now binds a temple-stage contract in its approval. The private stage shares the
parent's client, money guard, ledger, review log, global interaction count and stop signal; its SDK history, scene,
tool journals and image retention are separate. A stage allowance refusal is recoverable before reservation;
unknown requests, global budget/stop/turn failures remain fatal. Child receipt IDs are namespaced so identical
provider call IDs cannot overwrite parent receipts. Cancellation waits for actual child cleanup before closing
workers. The macro has no arbitrary whole-stage wall-clock limit; inference and individual tools retain their
existing deadlines. No second approval or paid-run lock is acquired.

Packages capture the current parent checkpoint/export and copy all exact photo IDs, frozen cameras and matte
bytes. Owner text stays verbatim in the bound brief, with separate neutral host prose for the instruction contract.
Explicit pose metadata is preserved; missing openings are marked as-authored, never described as fitted. The trusted
apply endpoint refuses a changed parent fingerprint. A real FT1119 capture-only check retained a subsequent
Saddle_bridge edit and all six camera/matte records in 6.031 seconds; its final Lab preparation was explicitly
stubbed, so this is not the required second-product stage proof.

M4b checks so far: 279 unique shared-loop/budget/receipt checks (276 in 92.00 seconds, then three additional
regressions; final stage module 20 passed in 20.11 seconds); generic CLI/tools/instructions/image-policy checks
142 passed in 10.51 seconds; CLI plus changed-specialist-approval refusal 24 passed in 8.87 seconds; package/Lab/CLI
171 passed, then package/CLI 51 passed after bound contract text and supplied-matte copying; trusted stale apply
1 passed. Fit/arrangement fixes passed 43 host tests. Evidence: `data/assembly/m4b_*`. Prada's real T5 rehearsal
passed **47/47 checks, 23 requests/turns, completed, $0**, in 112.17 seconds including 26.0 seconds preparation
and 83.3 seconds in the run (`m4b-t5-20261013-152145/rehearsal.json`, wrapper exit 0).

Independent review added exact child exit id/SHA, finish validity and export-byte checks before parent application;
19 stage orchestration/image/cancellation tests passed in 2.15 seconds. Owner notes now reach the child verbatim
through the approval-bound parent input, once per child history. Renamed photos keep explicit source identity while
private camera records use their copied local name; 71 package/CLI/orchestration tests passed in 7.51 seconds.
Product-specific light experiment claims and inferred/copied hinge-angle claims for missing pose data were removed
from generic host prose; calibrated records retain their exact facts. After that description-only change, CLI 42
passed and package/stage 33 passed. The second-product nested proof remains pending while UV transfer is finished.

The copied FT1119 temples have a UV seam through a rebuilt outer polygon. Per-corner transfer correctly refuses
that discontinuity rather than smearing it. The in-scope implementation choice is to add seam tessellation only
where required for UV transfer, preserving the core geometric surface and existing gates; interpolated inserted
normals need a separate error audit, since normalizing a new vertex can change interpolation inside its triangle.
The non-UV path remains unchanged. The real copied FT1119 UV build, manifold check, export validation and exact
undo passed. Later texture-generation and scaled-source regressions are still in progress; M4b is not complete.

The first real nested proof (`m4b_nested1`, copy `m4b-01c19f29`) correctly refused its original compare with
`E_NO_POSE`: the saved rigid WP3 camera fit has no explicit temple transform. Its scripted follow-up then failed
because child tools were unavailable in the parent (2 mock requests, actual $0). No gate was changed. Independent
reviews agree that a proven rigid fit used delta-zero rotation, which can be recorded explicitly without claiming
measured photo hinge angles. The bounded fix requires an explicit rigid-fit source marker and SHA lineage; unknown
or incomplete imported poses still refuse. Derived mesh axes have separate provenance, and the brief must label
these records as authored, uncalibrated transforms. Identity of posed vertices and projections is required. The
legacy FT camera report will be adapted only in its SHA-pinned proof fixture, never by product-specific runtime.
That fix now passes **29 checks** (22 new provenance/identity tests plus 7 existing camera tests) in 1.08 seconds,
and package/CLI integration **58 passed** in 7.30 seconds. Four independent camera projections and rasters preserve
the source vertices, depth, masks, face IDs, barycentric coordinates, points and normals exactly under the explicit
identity delta. Fourteen missing/tampered provenance cases refuse. Existing explicit poses remain untouched; the
runtime refuses missing geometric knuckles rather than inventing an axis. `E_NO_POSE` itself is unchanged.
Prada T5 was repeated after the UV-source helper: **47/47, 23 mock requests, completed, $0**, wrapper exit 0
(`m4b-t5-final/rehearsal.json`, 2026-10-13 12:42:43–12:45:01 UTC). No product appearance verdict has occurred.

The complete second-product proof now passes (`m4b_nested2`, copy `m4b-eecfca1e`, 87.031 seconds, wrapper
exit 0): nine mock requests share one approval/ledger; seven child requests compare four distinct heads and three
distinct alternatives, finish and apply through the trusted worker, then the parent resumes with a successful
inspection. All 23 non-temple primitives retain their geometry, all vertex attributes, materials and metadata;
the deliberate prior bridge edit is retained. Original scene, camera report, model and six source photos are
byte-identical. Actual spend is $0; this scripted control is not an appearance verdict. Final integration checks
(stage/package/rigid pose/CLI/tools/instructions): 195 passed in 10.80 seconds.

UV regression fixes reject shared target/backup mesh ownership before mutation and preserve the original partRole
on undo. The source generation helper protects live backup references. Seam intersections use the existing
EDGE_SLACK band to snap near-corner intersections onto the existing corner; directed-edge, closed-manifold and
Euler checks now reject malformed tessellation before Blender receives it. Independent review reproduced the
prior repeated corner/edge failure and confirmed the correction; no tolerance or geometric gate changed.
A real worker/journal regression passed (1 test, 4.68 seconds): after an actual complete second UV write, an
injected failed response restores exact prior UV bytes, fingerprint, checkpoint file/index and journal head.
Standalone direct-call failure restoration remains its existing pre-facet backup behavior; assembled calls use
the journal to restore the exact pre-call revision. The complete generation regression passed (1 pytest test,
19 receipt checks on both transformed/mirrored sides, 110.73 seconds): layer edits/additions/deletions and flags,
external geometry and scale/translation, independent known UV values at every output corner, exact A→B→A,
dry-run/variant nonmutation, failed-write cleanup, ownership refusals and exact original undo. Seam subdivision
legitimately changes the loop count, so this new test uses its independent authored UV function after subdivision;
unchanged-topology byte comparisons and the original 5e-6 UV tolerance remain. Final UV tests: 2 passed in
40.48 seconds (`m4b_uv_pass`), including real FT export validation. The independent concave audit's maximum surface
distance is 0.000019597 mm, corner normal difference 0.024898 degrees, interior difference 0.003921 degrees,
and authored-flat difference 0.002112 degrees. Its strict-interior lookup avoids selecting an adjacent shading fan
at a nearly collinear corner; the original 1e-4 mm and 0.5-degree limits remain. Final frozen-source Prada T5:
47/47 checks, 23 mock requests, completed, $0; prepare 35.0 seconds/run 95.8 seconds (`m4b-t5-frozen`).
M4b is complete. M4c is active; M4d and the full M4 suite/commit remain.

M4c's `photo_print` macro now uses explicit exact part names and a named frozen basis, fixed production thresholds,
memory-bounded workers and no coverage override. It preserves a fresh bake directory for every call, detaches the
requested parts' earlier print, replaces their print UVs, attaches only manifest-eligible parts, exports and checks
the manifest. Any later refusal or exception restores the pre-stage checkpoint, including its prior print. All
sub-steps share the macro's call id so checkpoint recovery produces one model-tool event. Cancellation checks
prevent a background bake from applying after cancellation. Optional calibration/registration files are confined
to the job; missing registration stays explicitly uncalibrated. Dispatcher, transaction, registry and instruction
checks: 211 passed in 7.18 seconds (`m4c_tool_second`, four workers/loadfile). The first collection exposed an
unordered set in the new dispatcher parametrization; sorting test cases fixed collection without changing checks.

The actual async SDK cancellation regression also passes: cancelling during a blocked bake sets the stop event,
the background thread retains the scene lock through cleanup, and no later mutation runs (24 transaction tests,
2.32 seconds). Print Blender checks: 13 synthetic plus 1 copied historical FT1119 passed; original bake metadata
unchanged. Five zero-area layout tests retain global face indices and finite collapsed charts; A1 still rejects
zero area, and the Blender tool returns `E_LAYOUT` without mutation. Safe detach refuses shared nonselected
material users, missing original swap materials or a tree that cannot exactly restore before changing anything.
Repeated attachment restores the authored materials. No gate changed. A final integration review found cached
exports retained an old checkpoint id after restoring identical geometry; cache hits now bind the current head
while preserving validated bytes/fingerprint/SHA. Export and transaction checks: 36 passed in 6.31 seconds.

Exact runtime object identities no longer strip `.001` or merge distinct Blender objects. The historical accepted
FT GLB uses 30 suffixed node names while its saved layout/recipe use unsuffixed names; reproduction therefore uses
an explicit test-only one-to-one migration on copies, retaining binary bytes and every other JSON field, with
rebound copied camera/registration hashes and a migration receipt. Runtime has no product/name alias fallback.
Target-role filtering validates all frozen camera/photo/matte identities before excluding non-target donors and
records those exclusions. Host integration: 60 passed in 42.95 seconds. Accepted-byte replay and full macro proof
remain pending the colour-policy source freeze.

The colour model is now geometry-bound across donor admission, bicubic footprints and the later highlight filter;
blue/green/grey candidates no longer inherit the unconditional brown B/R ceiling. Hardware/occlusion/clipping
margins and coverage/independent quality gates remain. Forty-three new tests passed in 19.40 seconds, including
exact masks and pigment-arm values on all six accepted FT photos; 71 existing focused tests passed in 71.63 seconds.
The warm compatibility branch is selected from at least 150 informative samples with at least 90% supporting warm
channel order. An initial new neutral-share veto wrongly classified glare/low-chroma warm pigment; removing that
unsound model condition (not changing a quality gate) restores exact parity and adds an independent glare control.
Warm-dominant mixed neutral pigment remains ambiguous and is explicitly reported by the policy; a broader palette
architecture was not added. Pure grey and non-warm patterns exercise the general model. Exact multi-material part
guards now aggregate forbidden lens/metal roles across every primitive, so a later acetate slot cannot erase them;
six permutation/role tests plus pin/name controls: 15 passed in 0.77 seconds.

Accepted FT1119 replay now passes: `data/assembly/m4c-accepted-ft-20261013-133134/result.json`, 252.20 seconds,
14 workers, $0. The PNG SHA is exactly `ac4280e9d0a2b85696c1d64f7c9508f56d81f2fc2857f7925af58fd844ddd5b4`;
the independent audit checks 729,621 photo texels with zero bad anchors and zero mixed-view faces. Layout,
class/source/tier arrays and gains match the accepted recipe. Registration differs only in its generic mode label
(the runtime no longer asserts a product-specific fact); numerical evidence and selections match. All input and
frozen implementation hashes stayed unchanged. The assembled macro proof is running separately on a copied job.

Final M4c integration review found that a later geometry edit could leave a stale print attached yet pass finish's
spec/seating checks. The bounded fix identifies active prints from their actual exported UV/texture binding and
checks the current export against its retained successful bake evidence, preserving restored older checkpoints and
intentional detach. This strengthens assembly integrity without changing any existing print quality threshold.

The first complete macro proof passed in 232.91 seconds: `m4c_macro1.log`, copied job under
`data/assembly/m4c-dc34956e/`. Real SDK tool invocation, production bake, trusted worker detach/UV/attach,
independent A6/UV/material checks, exact checkpoint restore and original-file hashes all passed; no paid call.
The later freshness safeguard is being verified separately and the final macro proof will include it. Freshness
reports are recomputed against successful retained application/manifests even when export bytes are cached; new
finish/delivery require a SHA-bound passing report, while historical immutable deliveries remain readable.
Complete old bake groups must be reprinted or detached together; the existing full-manifest checker is unchanged.

Independent review also found an output-parent junction escape and a cancellation/teardown race. The bake output
now resolves inside job work before creation, and cancellation waits for the print thread's actual cleanup before
the session can close (including repeated cancellation). The real Windows junction test and async cancellation
test pass: 29 transaction tests in 2.89 seconds. Initial finish/delivery/export/transaction integration: 141 passed,
1 symlink privilege skip in 9.20 seconds; the skipped case now uses a Windows junction and passes. No gate loosened.

The first attachment-marker design failed the unchanged B5 metadata-preservation check (12/14 Blender tests passed;
both failures were node extras). That design was removed. The final implementation uses existing print UV metadata
and actual image/sampler bindings, with no attach/detach metadata change: all 14 Blender tests pass in 33.50 seconds.
Nineteen host GLB tests pass in 1.57 seconds, including stale geometry/UV/image, missing/tampered evidence, restored
old and disjoint complete bakes, extra ineligible objects borrowing a valid print, stripped metadata and an
unsupported image source. The authored texture restored by detach remains valid. Combined finish/delivery/export/
transaction/freshness integration: 160 passed in 8.73 seconds (four workers/loadfile).

Final macro proof passes in **258.50 seconds**, `$0`, 14 workers: `m4c_macro_final.log`, copy under
`data/assembly/m4c-755a336f/`. The tool result independently confirms freshness status `verified` and one bound
attachment group, SHA-bound manifest, and the exact accepted `ac4280e9...` PNG. Detach reports `not_required`;
A6, UV invariance, B5, original-source hashes and pre-print checkpoint restoration all pass. Free commit before
launch was 21.25 GiB, BLAS 1, no overlapping heavy pools. Final bounded review repairs passed: malformed evidence
refuses rather than throwing (41 freshness tests, 2.41 seconds); basis-only imports exclude recorded alternate
colourways while explicit job roles remain authoritative (41 production-input tests, 45.66 seconds); legacy test
CLIs consistently use hash-rebound copies after the exact-name fixture migration (5 wrapper tests, 9.24 seconds).
These only affect malformed evidence or alternate input wrappers; the accepted replay and final macro inputs are
unchanged. M4c is complete.

M4d adds signed rear-three-quarter camera candidates, per-photo matte modes, and the existing lens-optional loss
for exactly classified rimless/semi-rimless lens faces. All optimizer levels and candidate comparisons receive the
same optional-face mask; raw IoU/contour metrics and existing thresholds remain unchanged, with optional metrics
reported separately. Each attempted photo keeps its identity, hash, failure and available evidence. Automatic
seeing operations use successful cameras and report unavailable refs; explicit failed-photo requests refuse with
the named diagnostic. An all-failed attempt remains inspectable and does not replace the previous usable default
basis. Temple stages still require a complete camera set; print requires at least two usable target cameras and
reports each excluded photo. Damaged frozen evidence refuses instead of silently selecting an older basis.

Camera fast checks: **31 passed in 1.36 seconds**. Dedicated/legacy optimizer checks: **23 passed in 178.75
seconds**, including full-prior signed rear recovery and unchanged front/top/elevated thresholds. Seeing/tool/
temple-package checks: **71 passed in 7.46 seconds**. Fit/intake/rigid checks: **100 passed in 2.25 seconds**;
partial-print/production: **47 passed in 42.30 seconds**, then 7 partial-input checks in 3.80 seconds including
direct loader matte-mode parity. Basis/legacy/temple integration: **70 passed in 11.21 seconds** (22 new partial
and integrity cases). Historical Prada tests use copied, hash-pinned camera metadata with the three actual `.001`
front names; runtime exact-name matching stays strict.

The mixed-photo proof **passed in 29.594 seconds**, `$0`, copy `data/assembly/m4d-da7887f6/`, log
`m4d_proof2.log`: front/rear cameras meet the unchanged synthetic truth gates, the glossy-floor matte matches
exactly, the blank photo keeps its empty-matte failure, automatic comparison uses the two usable cameras, and
explicit blank requests refuse. An all-failed refit preserves the previous default and all old basis/source bytes;
the live head is unchanged. Before launch: 23.15 GiB free commit, 14-worker upper limit, BLAS 1. Initial proof
intake correctly refused missing explicit `side: null`; only the synthetic view data was corrected. Review added
separate optional-lens metrics to frozen/tool reports and kept failed-reference diagnostics when an `all` render
can show only canonical views. Seven seeing-boundary tests pass in 2.69 seconds after correcting their fake bounds.

M4 regression underway: main Python suite uses eight workers/loadfile (23.13 GiB free commit); photoprint follows
at two workers. AR: **497/497**, QA: **103/103**, TypeScript check passes. Every run remains offline and `$0`.

Expanded M4 scope, explicitly approved by the owner: **"Expand M4 to fix rimless matte"**. An additional real
rimless stress case (actual closed lens-role meshes, hard-frame photo, existing untuned camera truth/priors)
failed its unchanged 98% optional-IoU check: fitted 0.857904; even the exact camera only 0.938868. Independent
matte-stage diagnosis: raw threshold matches the 5,802-pixel hard render exactly; opening removes 15 pixels,
closing adds 31, then default hole filling adds 336 background pixels. Lens-first-hit occlusion is not the main
error. Exact source model/photo/result are retained under `data/assembly/m4d-rimless-counterexample/`; diagnostic
receipts `m4d_rimless_diagnostic.json` and `m4d_rimless_matte_diagnostic.json`. No fixture, seed or threshold was
changed. Owner approval authorizes a general rimless-matte fix; it does not authorize loosening any gate.

Final UV review also found a bounded ownership bug: another object's retained mesh could be used as a live mesh
and then renamed, retargeting its string-based undo reference. A symmetric current/retained ownership preflight
now closes both directions. Its Blender regression passes in **8.82 seconds**, checks original and later UV-source
generations, refuses without changing scene/datablock state, then restores exact original mesh/UV bytes and cleans
retained generations (`m4_uv_ownership.log`).

Initial main suite completed: **2,901 passed, 16 failed in 692.89 seconds**. Nine failures came from historical
test doubles missing the new Basis usability methods (38 targeted tests pass, one unrelated real-Blender seed test
deselected because it passed in the full run). The instruction contract's literal-key checker now follows the
extracted fit-summary helper; a new fake-bounds test was corrected while the suite still had its earlier module
loaded. Combined root checks **125 passed in 7.35 seconds**. QoS regression **12 passed in 2.68 seconds** with no
external descendant watcher; timing suite **95 passed in 71.48 seconds**. Full seeing module **22 passed in
110.01 seconds** alone: two fixture failures were an adapter applied to the wrong asset (the original Blender
scene exports unsuffixed exact front names; the standalone historical GLB needs `.001` names). The original
camera metadata is retained for that live scene, with an added exact-name presence assertion. Its 60-second render
threshold is unchanged and passes without eight-worker load. No quality assertion was removed or reduced.

The authorized matte correction adds `open_frame`: the identical background/threshold/opening/closing/component
recipe, omitting unconditional hole filling. An absent photo mode selects it only with actual optional lens faces
from rimless/semi-rimless construction; explicit modes win and the resolved mode is frozen, including failed rows.
**115 focused checks pass in 21.39 seconds**, including 12 FT/Prada historical default masks byte-identical to the
independent WP3 recipe and frozen/raw-print mode parity. Exact-truth optional IoU is now **0.992969** with contour
0.063675 px. The unchanged real optimizer test still fails at **0.912881**; its evidence is preserved separately.
Independent review found missing optional-face handling inside existing camera refinement: full-lens bbox
alignment displaces the true-angle initial Y centre by 31.55 px (hard geometry: 0.19 px), and the effective
photo-dependent contour creates lens-ray correspondences without real hard geometry (86/833 samples). Bounded
alignment/ICP integration diagnosis is in progress; priors, thresholds and the failing test remain unchanged.

The final rimless correction passes the actual optimizer proof in **16.54 seconds**: optional IoU
**0.9907518411** (gate >=0.98), optional contour **0.0791593194 px** (gate <1 px), yaw **4.0320623 degrees**
versus truth 4, pitch **11.9279084 degrees** versus truth 12 (both within the unchanged 2-degree gate).
Raw IoU **0.1661735559** is retained separately; frozen matte, raw/optional metrics and source hashes are verified.
Evidence: `m4d_rimless_physical_support.log`, `data/assembly/m4d-rimless-verified/`.
Each existing angular prior now scores both the original full bounding-box alignment and an alignment to vertices
belonging to required faces; the unchanged loss chooses the better candidate, with full alignment winning ties.
Optional-lens interior points borrowed from photo-dependent contours cannot become artificial ICP ray constraints
in either correspondence direction. Actual rendered boundaries, including opaque lens edges, remain available.
There are no new angular priors, refinement iterations, loss weights or relaxed gates. Clear/opaque alignment,
both ICP directions, all-optional fallback and raw-metric checks pass **17/17 in 0.58 seconds**; the separate rear
recovery test runs in the full suite. Independent review found no blocker, and paths without optional faces retain
their original arithmetic. This completes the owner's approved rimless-matte expansion.

Initial full photoprint run: **313 passed, 1 failed in 217.39 seconds**. Its only failure expected the old `.001`
fixture name after the explicit exact-name migration. The assertion now uses the same pinned name mapping as its
copied asset; all decal-fill, see-through, 2% and 98% assertions remain unchanged. Five migration-wrapper checks
pass in 8.20 seconds and the saved bake satisfies that corrected identity assertion. Final source-frozen full
main suite started at **14:41:44 UTC**, eight workers/loadfile, **22.99 GiB free commit**, BLAS 1 and EcoQoS opt-out;
the final full photoprint rerun follows at two workers with no overlapping heavy pools.

Final source-frozen main run (`m4_final_main`): **2,948 passed, 2 failed, zero skipped in 648.55 seconds**.
Both remaining cases are the known environment-sensitive checks: the external descendant watcher changes the
initial Windows power state measured by `test_opt_out_self_reads_back_and_is_not_inherited`; the 28-image render
took 163.983 seconds under eight-worker load against its unchanged 60-second limit. Photoprint's final run started
at **14:53:50 UTC**, two workers/loadfile, **23.00 GiB free commit**; isolated checks follow before closing M4.
Final photoprint: **314/314 passed in 233.34 seconds**, exit 0 (`m4_final_print`). The unchanged Windows
power-state module passed **12/12 in 3.23 seconds** without the external descendant watcher (`m4_final_qos`).
The whole seeing module passed alone after the print pool exited: **22/22 in 111.73 seconds**, exit 0
(`m4_final_seeing`). Its unchanged 60-second render check passes; no overlapping heavy pools.

**Final M4 regression: 3,264 unique Python tests passed after the two isolated environment-sensitive reruns,
zero skipped** (2,950 main + 314 photoprint). Arview **497 unit + 103 QA tests passed**, TypeScript clean.
The final source was frozen before the main run; only this progress log changed during the final suites.
Every M4a-d proof, including Prada T5, the second-product nested temple stage, the exact accepted FT1119 print,
the mixed-photo camera proof and the approved rimless stress case, passes. All paid transports remained unused:
**actual API spend $0**. Independent integration reviews found no remaining M4 blocker, and `git diff --check`
is clean. The explicit commit list contains 120 source/test/documentation files; generated evidence, photos,
models and scenes are excluded. Work remains in the requested checkout on `glasses3d`, with no push or worktree.

Open limits remain explicit: ten historical WP4 quality failures (recommendations above), unbuilt junction captures
and photo-derived hide ranges outside M4, historical WP5 A4/A5/B3 failures, and the recorded warm-dominant mixed
neutral-pigment donor ambiguity. Offline integration proofs are not new product appearance acceptances; M5 and its
owner AR verdicts remain unrun. The original root `data/assembly/m1-proof-b8bb88d4` artifact remains untracked:
automatic approval review rejected its cleanup move with the stated reason "blocked by policy". No cleanup retry
or workaround was attempted, and the artifact is excluded from all commits.

M5 planning estimate, calculated locally from `estimate_g3d` and the frozen
`gpt-6-astra-standard-2026-09-27` tariff (no API/token-count call): 25,000 initial tokens, 60 replies and 16,384
maximum output tokens estimate **$15.699976 per run**, growth-only range **$13.942871–$21.678160**. This is not
a confidence interval; the calibration is eight historical temple runs on one product, and assumes no image-pruning
savings. Six such planning runs estimate **$94.20** total (growth-only range **$83.66–$130.07**); D5's unchanged
reserve ceilings bound six individual runs by **$138 plus the separately approved smoke cap**. The high-growth
scenario requires $29.886542 reserve and therefore cannot complete all 60 replies under the $23 guard. A minimal
one-turn smoke with 2,500 input tokens and 4,096 maximum output tokens estimates **$0.102150**, worst-case
reservation **$0.236050**; an actual smoke requires its own exact input estimate and owner approval. D5's $20
settled stop, $23 reserve ceiling, max effort and Oakley-first choice are final. Total M5 budget and smoke cap remain
unapproved; no M5 request has been sent.

## Status

| WP | State |
|---|---|
| WP0 / WP1 / WP3 / WP3b | Complete and tested |
| WP4 | Trusted seating tools integrated and proof refreshed in M4a; all 24 real-product tests pass without stale skips. Quality verdicts remain P2 71/71, P3 34/40 (+2 report), P4 109/112 (+2 pending), P5 31/32 (+1 N/A, +2 report): 10 known quality failures remain failures. Junction captures and photo-derived hide ranges remain unbuilt, outside M4 scope. D4 crystal Boolean choice is recorded in ASSEMBLY; no quality gate was loosened. |
| WP5 | Complete by owner visual acceptance 2026-10-08, FT1119 only: observed-slope join + Poisson is the default (`4304c1d`). Not an all-proofs pass: A4/A5/B3 failures stay recorded ([WP5_HANDOFF](WP5_HANDOFF.md)) |
| WP6 | **Complete by owner acceptance 2026-10-13** ("you know what. im satisfied with wp6, lets wrap this"), after paid run 8 `facet-astra-008` on the v3 tools ([WP6_V3.md](WP6_V3.md), section "WP6 v3" below). Result: run 8's best checkpoint c0015, revision 25 (`data/wp6v2/viewer/new_astra_run8.glb`). Left open at closing: the hinge-side face reads too faint (built 2.2° off the logo) and Astra's images pile up unlabelled ("Open at closing" below). (v2 stopped by the owner after run 7; v1 retired 2026-10-10, tag `wp6-v1-final`) |
| WP2 | A–F and all M4 specialist stages assembled and proven offline in M0–M4, including immutable delivery and owner review |
| WP7 | Implemented and proven in M2 ([WP7_DESIGN.md](WP7_DESIGN.md)); specialist tools and teaching registered and tested in M4 |
| WP8 | Not started (paid; owner OK per run) |

## WP6 v2 build order (WP6_FACETS §6)

1. **Archive + docs (§7)** — done 2026-10-10 (track F), committed in the archive commit after `62b25e4` (see git log).
   **Seeing (§3)** in parallel: render.py, seeing.py, compare.py, pose.py (new) and their tests.
2. Owner sheet #1 (after seeing). **Builder v2 (§2)** in parallel: g3d/temple/*, temple_bpy.py, core temple tests.
3. **Lab tools (§4)**: agent/temple_lab/*, worker method for variant renders, loop.py settings, tests, T5, T5b;
   remove the pending v1 modules below. Owner sheet #2.
4. Adversarial review of the whole diff, fixes, commit. Cost estimate. Owner OK. One paid run.

Phase 1–2 results (2026-10-11):
- Seeing is built: pose basis from fits.json, photo-matched compare, white balance and the photo_env light fit.
- **T2 rig check: no rig passes.** photo_env shows the gloss change 2.6 L8/mm against the 10 target. studio_dome_v1
  passes the size target but reverses the facet tone order. Details are in WP6_FACETS §3.2 (amended).
- The builder `g3d.temple/2` is built: 268 tests, v1 equivalence 3e-14 mm, template p95 0.30 mm.
- The v1 lab is broken until step 3 (it writes /1 specs).

Phase 3 and pre-flight results (2026-10-11):
- **Astra facet tools** (`agent/temple_lab`, rebuilt):
  - 7 tools, the strict edits union, judgements and finish preconditions;
  - the worker's non-mutating `temple_variants_render`;
  - loop settings bound in the approval: tool_choice required, finish-gated stop, context guard, timeouts and E_BUSY;
  - the six pending v1 relief modules are removed.
- **T5 rehearsal** (MockTransport, real Blender, 0 network): 22/22 checks. **T5b**: the preferred layout is reached from
  revision 0 in 23 edit ops.
- **Finish rule "distinct head"** (preconditions 1 and 4): two attempts differ only when the built temple surface
  (normal distance) or outline moves ≥ 0.2 mm. Five review rounds found ways to meet label-, layout- and
  crease-threshold rules with near-zero geometry. Crease comparison is now reported, never decisive.
- The light is the owner's choice C (`studio_dome_v1`). Sheet #2 is in `data/wp6v2/sheet2/`. The lighting sheet is in
  `data/wp6v2/light_choice/owner/`.
- **Estimate** (from T5 tool outputs): 40 replies $7.26 (cap $15); 60 replies $13.78, edit-heavy $16.56 (cap $20);
  90 replies $22.19 (cap $50). Package: `data/wp6v2/lab/package`. The request `runs/facet-astra-001` is UNAPPROVED.

**Paid run `facet-astra-002` (2026-10-11).**
- Approval, in the owner's words: "yuo can run it with 20$ max (dont use reserve spending, just cut it by 18$). dont
  have a replies limit."
- Settings: effort max, no reply cap, cap $20, no output squeezing.
- A host watcher (`data/wp6v2/lab/stop_at_18.py`) would have stopped the run at $18 settled. Until 10:29 it counted the
  guard's per-request hold as spend; it was corrected while running, and it never fired.
- Result: 10:03:29–10:30:09, **33 requests, $6.679752 settled**, Astra finished on its own via `facet_finish`
  "matched".
  - Best checkpoint c0013, revision 15. Export `head-10ab74e8a162dded.glb`; validate_spec ok (2 warnings).
  - Layout: logo triangle, crown, hinge flank and tail flank fading at 55–85 mm, roll 0.25 mm. Every crease was
    declared a ridge.
  - 3 variant sheets, 6+ builder refusals that Astra fixed itself.
- Owner sheet: `data/wp6v2/result_facet-astra-002/` (tests/proofs/wp6v2_result_sheet.py, deleted 2026-10-13). The
  owner's verdict is pending.

**Owner feedback on facet-astra-002, verbatim:**
- "yes it did miss the red line."
- "the top triangle facet is barely noticeable. every other triangle also faces the same issue which is not being
  pulled enough outwards"
- an unwanted line from the logo apex
- "the each triangle line is very soft wheres it needs to be sharp"

**Fixes:**
- `c7362c4` builder: flat cells before a fade, sharp creases, E_FADE_CLIFF.
- `b34df09` black photos B1–B3 for Astra, plus clearer instructions.
- `cc38cbe` pre-flight P1: the start-model "camera error" sentence had kept the facets shallow.
- A three-lens pre-flight review gave GO.

**Paid run `facet-astra-003` (2026-10-11).**
- Approval: "yes, make the fixes and run it again", with the same terms. 13:27:20–13:42:02, 15 requests, **$3.598669**.
- Astra finished on its own as "matched". Best checkpoint c0011, rev 12; export `head-bad259c0eb030eff.glb`; spec ok.
- Layout: logo, heel, crown, lower_aft and tail. The crease behind the logo is now present (valley, 12°). Creases run
  12–19° (run 2: 7–8°). Roll 0.15 mm.
- Crown and tail both fade 47→76 mm, so the upper-back triangle does not read as its own flat face.
- Sheet: `data/wp6v2/result_facet-astra-003/` (faces_run3.png). The owner's verdict is pending; first remark: "it still
  looks like the top triangle isnt being recognized right?"

**Owner feedback on facet-astra-003, verbatim:** "i meant 4 and the results look like it really didnt recognize it"
(face 4 = the thin band above the logo, the top of the temple's front block). Evidence: run 3's top photo (03)
outline, photo minus model, both sides: d30 +1.5, d40 +0.7, d50 −1.7, d60 −2.1 against a common −0.6. From above the
real front block is broad and widest near d 25–30; run 3 is slim there and widest at d 50–60. Astra's own summary
named "minor top-view shoulder/transition offsets (about 1–2 mm)" and still finished "matched".

**Fixes for run 4** (owner: "yes, implement changes, test them, and run a paid run with a budget of 15$ to force
stop"):
- Finish precondition 5 (`matched` only): the top photo's outline measured and within 1 mm of its common offset at
  every station; every compare that shows the top photo prints its outline line (WP6_FACETS §4.1).
- Instructions: judge top and bottom faces from the top photo (edge-on from the side); the outline line's stations
  are shape, not camera error; the top photo may be oblique (a station is one image row); a corner in the top
  outline needs separate facets meeting at a node; a thin band takes a crease + rise plane; finish before the cap.
- Builder `W_FADE_EARLY` (core wp6-core-2.2): a fade that leaves its facet flat for under 5 mm and under a quarter of
  its length (run 3's tail: 0.1 mm).
- Tool test ($0, a COPY of run 3's package, `data/wp6v2/tooltest_face4/`, never Astra input; photo-minus-model mm at
  the real top-photo compare):
  - run 3's best head: d30 +2.1, d40 +1.2, d50 −1.1, d60 −1.5 off;
  - its layout with node depths A, C, D and the crown's rise searched (45 tries): best d30 +1.3, d60 −1.2;
  - face 4 split from the upper-back face (A-B-C and B-F-C), depths and rises searched (~50 tries): best d30 +1.3
    alone; 35 tries with face 4 through its three nodes were refused E_ILL_CONDITIONED;
  - a fit on the mesh's widest point per d (ignoring the oblique top camera, pitch 60°) passed on the mesh but read
    d30 +1.8, d40 +3.4, d50 +2.8, d60 +2.2 at the camera.
  No tested head met the rule. Whole models at the same compare: the original and the owner's earlier "preferred"
  shape fail it (preferred d30 +2.2, d40 +2.9, d50 +1.8); only Astra's rejected c0003 passes, with d40 unmeasured
  (now refused as unmeasured). Told to the owner before the run: the rule pushes Astra to work on the top view;
  passing it does not make a shape good; Astra may finish "stuck".
- Pre-flight review (5 reviewers + 5 verifiers): fixed the T5 outline check, unmeasured stations passing, 0.1 mm
  deviations, the oblique-camera wording, W_FADE_EARLY's length rule and wording, this paragraph's claims, and the
  money plan: the owner named one number, so the cap is $15 (the budget guard), which spend can never pass; the
  guard keeps room for the reply in flight, so the run ends near $13 at the latest. No separate watcher.

**Paid run `facet-astra-004` (2026-10-11).**
- Approval in the owner's words: "yes, implement changes, test them, and run a paid run with a budget of 15$ to force
  stop". Cap $15 (the budget guard), effort max, no reply cap, K 4. Code `e138736`; T5 `t5_004` 27/27.
- 15:01:06–15:30:05, 32 requests, **$8.209531** settled. Astra finished on its own as **"stuck"** (the outline rule
  was never met; no finish refusals). Best checkpoint c0019 (revision 17); export `head-38c91e136fe6ab7b.glb`
  (sha 0857568818…); validate_spec ok.
- Layout: hinge, logo, sweep, tail and two upper faces. **Face 4 is its own facet** (`upper_front`: hinge top, the
  top-outline peak, the logo corner; crease + rise −0.59, steeper than run 3's −0.35). The upper back is a separate
  face (`upper_rear`), with a ridge between them. Fades start at 77 mm, so no face fades from its own corner.
  Seven creases declared; roll 0.12 mm.
- Top photo outline of the best head: only d30 +1.45 beyond 1 mm (run 3: d30 +2.1, d40 +1.2, d50 −1.1, d60 −1.5).
  Astra's summary: about 1.4 mm short at d30; its last three distinct changes moved the error elsewhere.
- Sheets: `data/wp6v2/result_facet-astra-004/` (faces_run4.png, top_run4.png, reference-0N.png, black-side.png); the
  3D viewer's default is run 4.

**Owner feedback on facet-astra-004, verbatim:** "the only issue that i still see is the top triangle. please
investigate. (its split into two)". Found: the real top triangle (face 4) is ONE long thin face (hinge top, the
top-outline peak, a point far back; its lower edge one straight line through the logo's back corner). Run 4 added a
crease from the peak down to the logo corner that no photo shows (`upper_front` | `upper_rear`). Contributing causes:
the run-4 instruction "A corner in the top photo's outline is a corner of the faces …" (now removed), the
coordinator's own split tool test, and layout decided from one viewing direction; run 2 reached the same split
through a builder refusal (a single face 4 refused because every crease at the logo corner was declared a ridge, and
"do not redeclare" left splitting as the way out). Evidence: `result_facet-astra-004/top_triangle.png`; run 4's creases drawn on the black angled
photo with a transported camera cross the top face where the photo shows no edge (review notes).

**Review of Astra's guidance (2026-10-11).** 26 of 106 instruction sentences were not general (20 added from this
product's failures); Prada assumptions also sit in code (packaging defaults, "acetate" in briefs, gloss-black for
every colourway, fixed section stations, the top-only outline rule). Why the layout rests on one image: the prompt sent
every position reading to the side photos ("never from oblique views") and asked for no check from another angle;
the "two photos agree" rule compared one direction with itself (B1 uses 05's camera, 01 is its mirror); the black
angled photo had no camera; in run 4 the layout was fixed before Astra first saw an angled view, and the top-photo
rule kept it chasing one photo's numbers (13 of 14 judgements of 03). Not money, resolution or context.

**Owner decisions after the review, verbatim:** "yes, build 1-3 and make the rule information only" (1: general
instructions with per-job facts in the brief and a confirm-from-another-angle habit; 2: cameras for the black angled
and front photos; 3: variant sheets that show black photos with each variant's crease lines). "please max utilize
local machine for faster work" (pytest-xdist pinned; builds and reviews run in parallel). The outline rule is
information only since `2ca5be9`.

**Built for run 5 (2026-10-11; build workflow, review, fix workflow, re-review; all $0):**
- **General instructions** (`instructions.txt`, 12.8k chars): no product or photo-set facts; lay out in the square-on
  views, then confirm (keep / remove / add creases) in a calibrated view that sees the face at a moderate angle; a
  crease only where two directions show it; never split a face no photo shows divided; offsets and the outline line
  are information; the corner rule (one face straight through a corner: its creases cannot all be ridges; keep the
  face whole; decide with variants at the moderate and glossy views when no silhouette shows it).
- **Per-job brief** (`cli.brief_text`): each photo's direction, angle band to the outer face and points_px error per
  mm of depth, direction groups, glossy photos, number lines, camera-fit text, render light, revision 0, roll unknown.
- **Cameras for B2/B3** (black angled / front) by similarity transport from 02/04 (`g3d_host/camera_similarity.py`
  restored from wp6-v1-final; `tests/proofs/wp6v2_basis.py`): B2 0.82 px / IoU 0.992 (model check 0.934, temple
  0.875); B3 1.09 px / 0.992 (0.977 / 0.886) after the per-photo matte fix (white-background matte for B2/B3, B1 keeps
  its dark matte). Existing basis records byte-identical (`data/wp6v2/basis-pre-b2b3`, `basis-pre-b3fix` kept).
- **facet_variants** takes any photo with a camera (1..4, at most 3 targets), renders other colours in gloss black,
  and draws each model's crease lines (the compare overlay's rule, host-side, matches it to 0-2 px).
- **Builder:** E_CREASE_SHAPE names a node a facet runs straight through ("one is a valley or two of those faces are
  one ...; keep X whole").
- **Estimate:** output tokens per reply from runs 2-4 (1852 expected, 2064 high); up to 4 variant sheets.
- T5 rehearsal 31/31; reviews: no P1 left. Owner, verbatim: "you may do a paid run, only when your ready, max 15$".

**Paid run `facet-astra-005` (2026-10-11).** Approval in the owner's words: "you may do a paid run, only when your
ready, max 15$". Cap $15 (guard), effort max, no reply cap, K 4; code `bf0b699`; T5 `t5_005` 31/31; estimate_005
(40 replies $10.63, up to $13.78).
- 19:3x–19:54, 30 requests, **$6.766442** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0017
  (revision 14); export `head-dd42b6a10e649032.glb` (sha 7895814159…); validate_spec ok.
- Behaviour: the first compare included the angled view; Astra opened a crop of the black angled photo (B2) before
  laying out; every one of its variant sheets included B2. **The top triangle stayed one face** (`crown` A-P-R-B, no
  crease from the peak to the logo corner); one valley declared at the logo's back corner (`lower_sweep|tail_face`);
  `crown|logo` declared a valley; two thin bands along the top edge (`top_front_band`, `top_rear_band`, under 0.5 mm
  tall from the side) with a ridge at the peak. Roll 0.2 mm.
- Still off, by Astra's summary and the images: from above, the front block is about 1.9-2.5 mm too slim at d30-40
  (run 4 was closer there); a small lip at the crown in the front view.
- Sheets: `data/wp6v2/result_facet-astra-005/` (top_triangle_run5.png, faces_run5.png, top_run5.png,
  angled_run5.png, reference-0N.png); the 3D viewer's default is run 5. The owner's verdict is pending.

**Owner feedback on facet-astra-005, verbatim:** "the inward edge is the one thats really bugging me since the layout
is perfect. what caused it?" Found ($0; `data/wp6v2/tooltest_crown_ridge/`, review notes): the crown|logo crease is a
ridge in the photos (~80%; depth read from the angled views gives a ridge with probability 0.96). Run 5 built an 8°
valley because the logo's back corner (B) had t solve, so the builder took it from the crown's extrapolated plane
(5.41 against ~7.0-7.5 in the photos); a bracket chasing the top photo's outline (var013: five changes at once and an
explicit valley declaration in every arm) then flipped it. Astra never saw solved values or the flip against the head
(applying the variant said "no change"), and had no way to measure t from the angled photos (points_px gives the
model's t). Owner, verbatim: "ok, fix it and run again, max 15$".

**Built for run 6 (2026-10-11, $0; build, two review rounds, fixes):**
- facet_inspect prints solved node values (`solve -> 5.41`) and, per crease, a host margin in the builder's own
  planes (each face's corner farthest across the crease line, its offset in X = x_in(d) + t from the other face's
  plane, + = ridge; solved setting corners named). Receipts list `solved` moves and `margins` (flips, solved or
  fragile creases); `crease_vs_head` / `diff_vs_head` state shape flips against the head, also when a variant is
  applied with no edits and on facet_restore.
- photo_compare `depths`: t measured from a photo at (d, y) and a pixel (exact inverse projection; px per mm; miss);
  front/rear views pick the temple nearest at the model's t. Run 5's logo corner reads 7.0-7.4 in B2, 02 and B3.
- E_CREASE_SHAPE names the crease's solved corners; E_RELIEF_STEP names the rise_per_mm that closes it.
- Instructions (13.9k chars, cap 14,000): decide ridge/valley from evidence about that crease (measured corner t,
  read the host margin, never extrapolate t by hand; views that look along it); measured t for the corners that set a
  decided crease; one bracket answers one question; never change a crease's shape for an outline number; on
  E_CREASE_SHAPE first measure its solved corners. T5 38/38 (`t5_wip6e`).

**Paid run `facet-astra-006` (2026-10-12).** Approval in the owner's words: "ok, fix it and run again, max 15$". Cap $15,
effort max, no reply cap, K 4; code `7631a31`; T5 `t5_006` 38/38; estimate_006 (40 replies $10.77, up to $13.80).
- 00:15-00:34, 26 requests, **$5.075421** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0013
  (revision 13); export `head-8e43a634deb82aa3.glb` (sha daa5beb96b…); validate_spec ok.
- Fixed: the top triangle stays one face and the **crown|logo crease is a ridge**; Astra measured A, C, D, H and F
  with depths (C 7.49) and declared what the measured values built.
- New problems: the back faces fade from d 48 (Crown, Lower and Tail fade 48-82; Tail is flat 0.7 mm before its fade,
  W_FADE_EARLY fired 4 times and Astra kept the early fade from a bracket judged on B2 smoothness and the top
  silhouette), so the upper-back and lower-back triangles do not read as crisp faces; Front|Logo (the face beside the
  hinge against the logo) is a valley from the measured depths, a ridge in runs 3-5; the top triangle's rise -0.85
  renders it bright in the side view. Top view still 1-2.5 mm slim at d10-40 (Astra's summary).
- Sheets: `data/wp6v2/result_facet-astra-006/` (top_triangle_run6.png, faces_run6.png, angled_run6.png, top_run6.png);
  the 3D viewer's default is run 6. The owner's verdict is pending.

**Owner feedback on facet-astra-006, verbatim:** "run 6 looks closer, but as you said theres this inward issue and the
top triangle is still not one piece and being smoothen and not flat. please review evidence thoroughly and the tools
and instructions. please plan heavily for yourself (no need to stop to share the plan) and fix, after the fixes come
back when youre ready for a paid run and illl authorize it. run 6 is better than run 5 but there are still issues as
mentioned above.."

**Found ($0; corners triangulated from photo pairs with the job's own cameras; `data/wp6v2/tooltest_run7/`):**
- The smoothed, "not one piece" top triangle is the fade. Crown, Lower and Tail fade over d 48-82, so the crown is
  flat only over d 0-48 and blends into the arm behind. Astra faded to hide a wing:
  - the crown's tip E was solved from the crown's plane at t 9.8 (d 90), about 4 mm proud of the arm;
  - top view −3.2 / −4.3 mm at d 70 / 80; B2 "an over-raised wing";
  - the crown's lower edge A-C-E is one straight line, so with A 3.55 and C 7.49 the tip took C's height.
- A 3.55 was a one-photo reading at an assumed (d 0, y 10.2).
  - In B2 a d error moves the pixel along the depth line, and the inner face turns 0.78 mm per mm of d at the hinge.
  - The same corner picked in B2 and B1 sits at d −1.58, X 70.71: t 4.77 at d 0.
  - The hinge-bottom corner H reads 4.8-5.4 the same way (Astra 5.52).
- The inward hinge-side edge (Front|Logo valley, H 1.6 mm outside the logo's plane) came from the same A reading.
  With A and H read in two photos and C solved from one flat crown, it is a 1.9° crease on the ridge side
  (H +0.4 / C +1.2), within the readings' error.
- Readings of one corner differ by 1-2 mm between cameras (02's fit: mean contour 7.2 px).
  - C reads 7.5-7.9 from the angled photos and at most 6.9 from the top photo's outline.
  - One flat crown through A, T and the measured tip (t 5.25 at d 81) solves C at 5.8.
  - The top view then has no wing (d 60-110 within 0.7 mm).
- My run-6 rule "measure, never solve, the corners that set a crease" pushed C's thick reading in and the tip out.
- Every paid run (2-6) faded the faces on both sides of a crease that runs on (the crown's back crease near d 90;
  runs 3 and 6 from d 47-48). The owner's earlier remarks match: run 2 "the each triangle line is very soft", run 6
  "smoothen and not flat".

**Built for run 7 ($0):**
- photo_compare `depths` items take `also`: the same point in 1-3 more photos with a camera (`lab.corner_reading`).
  - The host finds the point by least squares over the pixels.
  - It prints first the t to enter at the reading's own (d, y) ("t 4.77 here"), then where the point is, then each
    photo's residual.
  - It refuses photo pairs that see the point along nearly one line, using the pixel Jacobian's condition number
    over 20 (B2+B1 1.9; the opposite side views 05+01 about 940).
  - A one-photo reading says where the inner face turns fast.
- Builder (core `wp6-core-2.3`):
  - A crease + rise plane may hold the ends of a straight run of its polygon's edges (`spec.crease_run`, corners
    within 0.5 mm). This is how a thin band such as the crown gets its tilt from a rise, with both ends measured.
  - A run corner off the line that another facet meets on both run edges is `E_SPEC`, checked before and after snaps.
  - `E_FADE_CREASE`: a crease stands to its ends. Two facets both faded anywhere along their shared crease are
    refused. The fix offered: start the fades where it ends, or let one stand.
  - `E_FADE_CLIFF` and `W_FADE_EARLY` no longer advise fading both facets of a crease alike.
- Refusal notes:
  - `E_FACET_NOT_PLANAR` lists each given corner solved alone and its move, smallest first ("C -> 5.84 (−1.90),
    E -> 8.45 (+3.20)"). It also names the straight run a crease plane can hold ("; or a crease plane on the run A-E
    (through C)"). For one given corner well off a crease line, it names the rise_per_mm that meets it.
  - `E_ILL_CONDITIONED` names the corners without a value and the straight runs.
- Instructions (13,974 chars, under the 14,000 cap by cutting wording):
  - read corners also in a photo from another direction, and enter the t printed for your (d, y);
  - a crease plane on a straight run's two ends;
  - when a face's measured corners fit no plane, take the run or rise named, else solve the corner that moves least,
    and keep it solved;
  - a crease stands to its ends; never fade to fix a thickness (read the end corner instead);
  - a crease left open by its readings and by the views along it: enter values within the readings' error that
    build a ridge, unless a bracket shows a valley;
  - a crease built flat stays undeclared.
- Tool test (labelled copy of package_006, local Blender): run 6's layout with the two-photo corners, the crown on
  A-E and no fades, builds. In the black side view the top triangle is one flat band to its tip and the back faces are
  crisp (`tooltest_run7/owner_top_triangle.png`).
- Reviews:
  - round 1 (4 reviewers, 4 verifiers): 3 P1 and about 10 P2, all fixed. The P1s were:
    - the guard could be passed by moving a crease end inside the outline;
    - the corner text printed the misleading t first;
    - the tie-break, flat-crease and solve rules looped;
  - round 2 (2 + 2): 1 P1 (the "end it at a node, fade from there" advice rebuilt the smooth surface through a base
    strip) and 7 P2/P3, all fixed;
  - a final check of the round-2 fixes: 1 P1 (a printed rise missed by rounding), 1 P2 (the run named with no values
    at its ends) and 2 P3, all fixed.
- Tests:
  - T5 `t5_wip7b` 39/39 (new: `corner_reading_from_two_photos`).
  - Full suite from the repo root: 2295 passed. 4 failed: the stale WP5 test and three load-sensitive tests, which
    pass alone. Builder, lab and CLI suites rerun after the final fixes: 379 passed.
  - `FX.fade`'s ridge now ends where its fade starts. The former dying ridge (`ridge_to=60`, `legacy_fade`) is kept
    for the fade-value tests with the guard off.
  - Recorded runs 2-6 revisions with both-faded creases are now `E_FADE_CREASE`.

**Prepared for run 7 ($0, code `9fb64a5`):** T5 `t5_007` 39/39; `estimate_007` (40 replies at effort max: expected $10.82,
up to $13.84; runs 5 and 6 cost $6.77 and $5.08); `package_007` and the request `facet-astra-007` (cap $15 by the budget
guard, effort max, no reply cap, K 4), unapproved until the owner's words are recorded.

**Paid run `facet-astra-007` (2026-10-12).** Approval in the owner's words: "yes, run it with the $15 cap". Cap $15
(budget guard), effort max, no reply cap, K 4; code `9fb64a5`; T5 `t5_007` 39/39; estimate_007 (40 replies $10.82,
up to $13.84).
- 08:11-08:27, 20 requests, **$3.846039** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0007
  (revision 7); export `head-0a4b0e841a2bfdb2.glb` (sha f5895ca520…); validate_spec ok.
- Astra read every corner in B2 + B1 with `also` (the hinge corner: t 4.42 here, against run 6's 3.55). On
  `E_FACET_NOT_PLANAR` it solved the logo back corner for the crown's plane (6.15; its readings 7.6-7.8) and kept
  the crown whole, with the peak node at the triangulated position.
- **No fades.** The top triangle (crown A-K-E-H) is one flat face to its tip. Every crease on the front block is a
  ridge, including the hinge-side edge (logo|nose ridge 3.3°). As in runs 3-6, the edge between the two back
  triangles is a valley (7.6°).
- Still off, by Astra's summary: the logo's diagonals are too shallow; the top view is short near d 30-40 and wide
  near d 80; the rear arm is broad around d 90-110.
- Sheets: `data/wp6v2/result_facet-astra-007/` (top_triangle_run7.png, faces_run7.png, angled_run7.png, top_run7.png);
  the 3D viewer's default is run 7. The owner's verdict is pending.

No further paid call is authorized. Each one needs a cost estimate and the owner's explicit OK.

## WP6 v3 (2026-10-12/13)

**Investigation (owner, 2026-10-12: "please work on wp6 ... review all code related to wp6 very very thoroughly ...
review all the evidence of the runs ... what tools are actually good, how to write the prompt ... what astra did good
but our tools werent good for it. come back with findings").** Two review rounds, 117 agents, $0. Findings:
`data/wp6v2/review_20261012/FINDINGS.md` (reports beside it). In short: the real top triangle is not one plane (K
+1.5..+2.0 mm off the plane of its other corners, a 4.1-4.4 deg kink in the angled photos), the exact-planar builder
had to put that misfit in one place, and each run's newest rule chose the place (fold, proud tip + fades, K pushed
in); every v2 run put the crown's tip 8-20 mm too far back; photo 03's front-block outline cannot be matched with the
other photos under the camera model (up to +2.2 mm) and was printed as shape; no render light reproduces the photos'
facet tones; Astra measured crisp corners well (0.2-0.5 mm) and executed the newest rule literally.

**Corrections to this file (evidence in FINDINGS.md §5 and `review_20261012/w2/root-cause.md` §4, with
`w2/critic-skeptic.md`'s fixes):** the stop entry's "underlying limit was camera disagreement of 1-2 mm per corner"
is wrong for the crown (its readings agree within 0.3-0.45 mm; the run's 1-2 mm pair differences were mostly picks on
different features); "face 4's lower edge runs straight through the logo's back corner" holds only in the side views;
"front block slim from above at d30-40" (runs 3-7) is mostly the 03 camera-model conflict; the "true A 4.77 at d 0" is
the logo's front corner, not the crown's vertex, and the "measured tip t 5.25 at d 81" is 4-5 mm too far back; the
run-7 tool test log also showed Logo|Lower flat 1.7 deg; Astra did see B's solved value in run 5's charts; "fading
diagonals" (v1 entry, Decisions) were Astra's own reading of the cream photos, and the diagonals end where two edges
cross.

**Rebuild (owner, 2026-10-12: "please fix wp6. do very very hard work!!! come back to me when you think we are good
to go with a paid run. please do your work with the conclusions from before. +max utilize this machine for faster
runs").** Design [WP6_V3.md](WP6_V3.md). Built in three phases of parallel tracks with adversarial review after each
(phase 1: 17 agents; phase 2: 21; phase 3: 29; plus a consistency pass):
- builder: `{"coef"}` and `"warp"` (mean value coordinates) facets, soft facet|base borders, one planarity tolerance,
  facet|base shape against the base, `model_edges`; every existing spec builds bit-identically;
- `g3d/temple/fit.py`: joint weighted least squares over read / typed / solved corners, one camera-theta turn per
  camera fit, declared shapes as constraints accepted within chi2 +4, refusals and warnings as facts;
- `agent/temple_lab/readings.py`: corner and crossing readings (traced edges intersected), pick and camera error,
  slide-along-edge facts, pick sheets;
- seeing: native-scale zooms with a light-independent geometry view, the model's creases drawn by identity with
  their angles, side-view outline offsets;
- lab/tools: `photo_read`, object note, fit receipts, finish rules; removed fades, crease+rise planes, solve notes,
  one-photo depths, the 03/06 offset tables, the outline line and facet_restore; VARIANTS_MAX 4;
- prompt `agent/temple_lab/instructions.txt` rewritten (8,972 characters, was 13,974): the object, the evidence
  ranking, an order of reasoning, few rules;
- run safety: the context guard stubs only outputs present when it fires, a request payload limit, a machine-load
  preflight before a paid run.
Engineering choices (coordinator): ACCEPT_DCHI2 4, TYPED_SD 0.5 mm, PRIOR_SD 5 mm, MIN_CREASE 2.5 deg, THETA_SD 0.3 deg,
CAMERA_PX_SD 1 px, warp = mean value coordinates (a d-ruled surface gives the same turn between H and K), prompt cap
9,000 characters.
Proofs ($0): T5 v3 47/47 (`data/wp6v2/lab/t5_v3b`); full suite from the repo root 2642 passed, 7 Blender/timing
failures that pass alone, and the stale WP5 test_a4 (untouched, WP5 closed); estimate `data/wp6v2/lab/estimate_v3.json`
at effort max: 40 replies $15.02 (up to $21.67), 60 replies $32.22 (up to $46.11). Commits 8378bc4, 4c24124.

**Blind stand-in rehearsal (2026-10-13, $0, TOOL TEST).** A Claude agent played Astra with only instructions.txt, the
brief, the photos and the real registry (harness `review_20261012/v3/standin/drive.py`, report STANDIN_REPORT.md). It
found the photos' layout itself (logo, four faces around it, the top triangle as one warp face because its lower edge
bends at the logo corner, the faceting ending at d 74) and finished "matched" in 31 calls (c0013); its one typed depth
(the top triangle's peak P) made the top ridges 44.5/55.3 deg and the band render white. Prompt additions it showed
were missing: faces clipped by corners beyond a curved outline; the top strip's boundary next to the outer side is
that side's edge; an open edge sign is declared a ridge (the convex prior fills open signs only). With them it read P
(t 5.45; the review's independent 5.29-5.50) and finished again (c0015): top triangle|logo 22.6, top|upper-back 32.8,
logo back diagonal 6.5, hinge-side edge a 2.5 ridge (readings -3.5 +- 3.0: open). Owner-complaint checklist
(WP6_V3 §8, `data/wp6v2/result_v3_rehearsal2/checklist.md`): c0015 holds 9 of 10 (the tip at d 74.0, 1.5 mm short:
F unread), run 7 7 of 10. Options sheet: a fold adds a 10.6 deg line inside the top triangle; flat brings back the
logo corner 1.75 mm in and a 3.2 deg back diagonal. Sheets `data/wp6v2/result_v3_rehearsal2/`; 3D viewer default
`viewer/v3_rehearsal.glb`.
Final checks: full suite (10 workers) no WP6 failure (load-only failures pass alone; stale WP5 test_a4 untouched); T5
`t5_v3c` 47/47; estimate `estimate_v3.json` 40 replies $15.06 (up to $21.71), 60 replies $32.27 (up to $46.18).
Package `data/wp6v2/lab/package_008` and request `facet-astra-008` (cap $30, effort max, no reply cap, K 4) prepared,
UNAPPROVED (request sha 6d205b95…, registry 5613828a…).

**Paid run `facet-astra-008` (2026-10-13, the first v3 run).** Approval in the owner's words: "you may run it with max
18$ (20$ hard cap). im waiting here for the results, please let me see them in a 3d viewer". Cap $20 (budget guard), a
watcher stopping at $18 settled (`data/wp6v2/lab/stop_at_18_008.py`, never fired), effort max, no reply cap, K 4;
package_008 (code 7a3f295, request 6d205b95…; the earlier $30 request set aside unused under `runs-unused/`).
- 07:26-07:57 local, 40 requests, **$8.993582** settled. Astra finished on its own as **"stuck"**; best checkpoint
  c0015 (revision 25); export `head-681a8190c2cd3fd0.glb` (sha d8e6b88a…); validate_spec ok. In the 3D viewer as
  "Astra run 8 (v3 tools)", the default (`data/wp6v2/viewer/new_astra_run8.glb`).
- Behaviour: its object note named the photos' five faces (fore, logo, upper = the top triangle "one face even if it
  needs warp", lower, tail with a soft border into the arm), the top strip as the top surface, and the 03/06 conflicts
  as "not depth targets". It read all 7 corners as edge crossings in B1+B2 (0 typed values), built the top triangle as
  one warp face (1.48 mm off one plane), declared the open back-triangle line a ridge (2.5°), tried a ridge on the
  hinge-side edge and left it undeclared when the fit refused it (valley 2.2°, open), then spent its last third on the
  arm's end ("the oblique hook/end-cap shape in 02/B2" is its named remaining blocker).
- Final creases: top triangle|logo ridge 28.1° (26.6-38.8), logo back diagonal 7.9°, back-triangle line ridge 2.5°
  (declared, open), hinge-side valley 2.2° (open), tail|arm soft and flat 1.5°.
- Owner's review: the triangle closest to the lenses (the hinge-side face) does not show, its plane being barely
  different from the logo's (Decisions). Measured 2-4° from the readings; the photos' light turns that into a large
  tone step, our renders cannot.

**Closed (2026-10-13).** Owner: "you know what. im satisfied with wp6, lets wrap this". WP6 is complete on run 8 (c0015, revision 25). This
is the owner's acceptance, not an all-runs pass.

Open at closing (proposed after run 8, not built; the owner closed WP6 without them):
- **Image pile.** Astra's input is the whole conversation on every request. In run 8 it grew from 9 to 136 images
  (26k to 219k tokens) by request 40; images carry no revision or compare id and no current/superseded mark, and
  the context guard (240k tokens) never fired. Proposal: label every image, and replace superseded ones with short
  text notes every few replies (keep the photos, the latest compare per view, current pick sheets, the best
  checkpoint). This matters for any long Astra loop (WP7/WP8), not only WP6.
- **Visible faces.** An edge the photos show crisply can be measured at only a few degrees and is then built too
  faint to see (run 8's hinge-side face; the fit refused Astra's ridge declaration). Proposal: a declaration with a
  visible minimum angle, chosen from renders and honoured over the readings, plus one prompt rule.
- Astra's named blocker at its finish: the arm's end hook in photos 02/B2.
- Owner's later plan: a natural-language "more edits" box at the end of the automation, where Astra edits the
  model by the user's request (instead of parameter sliders).
- Leftovers, git-ignored: the $0 hinge-face tool test the owner stopped (`data/wp6v2/result_run8_hinge_face/`,
  with its never-committed script `wp6v3_hinge_face.py`) and its `run8_hinge_face_*.glb` entries in the viewer.

## Decisions (owner's words verbatim)

- **2026-10-15, Oakley run 4 visually accepted:** "as far as i can see the model is perfect". Accepted immutable
  GLB SHA `981314580a6b174546ebac0e42509b03a2e83044e4ae888aa729539277d198fb`, c0015, run
  `20261015T062433-662a82`; asset-bound ledger verdict `verdict-6cb0c4050a3b47b8bb09c0a7c419ecaa`.
  Preserve this accepted result. Seating/photo-fit diagnostics remain in the record; no other paid run approved.

- **2026-10-14, Oakley run 3:** "I approve Oakley run 3 on job oakley-oo9208-v2-3e057bab: a fresh run (not a
  resume), effort max, $20 settled stop, $23 maximum reserve. Create the run request and estimate. If the estimate
  is under $20, start it detached, as for run 2. If not, stop and tell me. Change no code, job, brief or photos
  before or during the run." The same message closes calibration research following ASSEMBLY's reviewed item 6
  status. Full verbatim message is preserved in `data/assembly/m5oakley3-owner-approval.txt` and run approval.
- **2026-10-13, decisions taken up front for the assembly (`ASSEMBLY.md` §4):** description vs photos: "Photos for the
  look" (photos win on shape and colour, the description on facts photos cannot show). Print tools: "astra can use it
  anytime it see fits. but we need the instructions to be good enough so astra will be able to decide to use the tool
  when it needs to (i.e. a tortoiseshell print and others means the tool needs to be used, while other frames dont need
  a print because their visuals arent complex, editing their material and color and finish is good)". Crystal frames:
  "Apply the check to all frames". Known-good product: "Oakley OO9208 v2". Per-run money: "20$ hard stop 23$ max
  reserve". Effort: max. Lens reflections: one fixed default. Total WP8 budget: "theres no reason to talk about this at
  the moment. this will be a thought when we'll have a full operational automation".
- **2026-10-14, after Oakley run 2:** lens seating becomes a report Astra weighs against the photos, not a finish
  requirement ("i agree with the seating part"); the check still runs on all frames. Money: "astra shouldnt under no
  circumstances decide how much to reserve, only the user" (the temple stage's Astra-chosen allowance is removed);
  capped tool output: "i like the tools point". No capability is taken from Astra ("i will not limit astras
  capabilities"). ASSEMBLY M5-fix-2.
- **2026-10-13, process:** Claude plans, Codex executes (`ASSEMBLY.md`).
- **2026-10-13, Oakley run 1 job corrected by Claude before approval:** the M5-prep job
  `oakley-oo9208-v2-cd068969` (smoke run done there) put two engineering lines into "Owner's specifications
  (verbatim)" (`assembly_run_1_scope: "frame + lenses + seating only."`, `assembly_scope_source`), which Astra would
  read as the owner's words and could take as "build no temples", and repeated the owner's note as both description
  and specs. Replaced by `oakley-oo9208-v2-3e057bab`: the same six photos (sha256 identical to the Studio job 3dff8674),
  the same labels (checked by eye: 01 front, 02 angled wearer-left, 03 side right, 04 rear, 05 side left, 06 top), the
  owner's original note verbatim as the description, specs `{}`. Brief fix `cdb52a2`: side labels are the wearer's
  side. Run request `20261013T163235-772629`: expected $14.96, range $13.20-$20.12, $20 settled stop, $23 reserve.
- **2026-10-13, smoke turn pre-approved:** "Yes, cap $1" — one real request on the Oakley job after ASSEMBLY M5-prep
  items 1-4 pass, $1 settled stop and $1 reserve cap. Oakley run 1 still needs its own OK and estimate.

- **2026-10-13, WP6 closed:** "you know what. im satisfied with wp6, lets wrap this".
- **2026-10-13, after run 8:** "the closest triangle to the lenses is not visible since its plane is barely
  different than the logo. you can also see in the evidence that our algorithm didnt catch that triangle in any of
  the layout images. correct me if im wrong." Then, on edits: "lets focus on astra, what if we just gave it more
  images? at the end of the entire automation i plan to add "more edits" iput box where the user can say "this
  facets meeting point should be further out, so the left facet would have more degrees " and then should edit buy
  the user request. i like this combo better, what do you think?"; and, stopping the $0 hinge-face variants:
  "focus. stop all youre doing i dont need a fixed temple i need wp6 to work!!!!!".
- **2026-10-13, run 8:** "you may run it with max 18$ (20$ hard cap). im waiting here for the results, please let me
  see them in a 3d viewer".
- **2026-10-12, after the investigation:** "please fix wp6. do very very hard work!!! come back to me when you think
  we are good to go with a paid run. please do your work with the conclusions from before. +max utilize this machine
  for faster runs". WP6 resumed as v3 (above).

- **2026-10-12, after run 7: STOP.** Owner, verbatim: "its not fucking good. i dont now what to tell you anymore, i dont
  think this is fucking possible anymore and youve given me zero reasons to believe that you can make it work. every
  model has an other defect this is fucking exhausting", then, on the offer of one $0 feasibility check: "it sounds
  stupid, because how can we improve on astras understanding?????? we cant so stop fucking easting my money and
  time?!!!!!!!!!!!!!!!!". WP6 Astra facet runs are stopped: no further paid runs, no further work on this approach,
  no new proposals unless the owner asks. Runs 2-7 each fixed the defect named last and showed another; the
  underlying limit was camera disagreement of 1-2 mm per corner against facets whose shape hinges on that much.

- **2026-10-11, light for photo-matched renders:** "c is definitely the best". The owner was shown A/B/C on the
  preferred and original models at all 7 photos, with neutral letters and identical lenses. C = `studio_dome_v1`
  (A = photo_env, B = catalog_band_v1). Record: `data/wp6v2/light_choice/owner_choice.json`. `studio_dome_v1` is
  the job rig for Astra and owner sheets. Its facet tone order is still not the photos' (WP6_FACETS §3.2), so
  shading stays a qualitative aid.

- **2026-10-10, WP6 rebuild:** "please rebuild wp6" (reply to the review that proposed judging WP6 by eye on
  photo-matched renders); "i just want astra to be able to handle facets and such, thats it. you can do whatever,
  even rebuild from scratch". Recorded design: [WP6_FACETS.md](WP6_FACETS.md) (62b25e4).
- **2026-10-10, Astra temple lab trial rejected:** "ok what did you learn? the results are still not good".
  Run `real-astra-001` (35 replies, 34 tool calls, $5.599202 settled of a $10 cap) exported c0003; one layout at
  two depths, hard full-length construction edges where the photos show fading diagonals. Evidence:
  `data/wp6/astra_temple_lab_20261010/` (owner-review-20261010.json, real-astra-report.md).
- **2026-10-08, WP5 closed:** "Observed-slope join + Poisson is the best, set it and mark wp5 as done".
- **Owner's edit to the v1 session prompt** (kept in the archived copy): "Use heavy CPU resources for faster runs."
  Process pools within free-commit memory (glasses3d/AGENTS.md).
- **Process (coding agent, 2026-10-10):** Codex sessions work sequentially; Claude sessions with ultracode on may run
  workflows with disjoint file ownership; the coordinator commits with explicit paths.

## Retired WP6 v1 gates (2026-10-10, owner decision)

S1, R1–R4, G1–G5, P2, the P3 decal limit, the STOP-I/C/R/S/T/P rules, `relief_rules.py` with its frozen thresholds
and the frozen S1 dependency table, and the delegated "KEEP STOP-I/R1/R3" decision. The review showed the true or
owner-preferred temple fails R1, R3 and P3, and that facet depth is not measurable from these photos through
silhouettes (WP6_FACETS §0). The v1 code is preserved under the local tag **`wp6-v1-final`** (3ad4459, never
pushed): `git show wp6-v1-final:glasses3d/<path>`.

The general rule stays: never loosen a gate to get a pass; if a gate is wrong, prove it (the true or owner-accepted
answer fails it) and ask the owner.

## Known-good references

**Oakley assembled run 4, visually accepted 2026-10-15**, is now an additional known-good end-to-end reference:
job `oakley-oo9208-v2-3e057bab`, run `20261015T062433-662a82`, c0015, GLB SHA
`981314580a6b174546ebac0e42509b03a2e83044e4ae888aa729539277d198fb`. Owner: "as far as i can see the model is perfect".
The job's `reviews/verdicts.jsonl` binds the acceptance to the immutable delivery. Earlier accepted assets and
frozen regression fixtures remain historical evidence and are not replaced by this record.

FT1123 crystal, Oakley OO9208 v2 and INVU IA22403B v2, re-accepted by the owner on 2026-10-06 (asset-bound verdicts
in `data/ledger/owner_verdicts.jsonl`). **Prada PR 17WS is an open failure**: its older "looks great" verdict
predates noticing the facets and the shortened-temple rendering defect.
The owner's 2026-10-13 WP6 acceptance (run 8's temples) closes WP6; it is not a whole-asset Prada verdict.

## Archive step (track F, 2026-10-10)

Removed (`git rm`): `g3d_host/relief_{anchors,crop,cue,edges,fit,hinge,junction,photo_mask,ridge,transition}.py`,
`g3d_host/camera_similarity.py` (restored 2026-10-11 in `bf0b699` for the B2/B3 camera transport of
`tests/proofs/wp6v2_basis.py`), every `tests/proofs/wp6_*` except the Blender driver, the camera/occlusion evidence
fixtures, and 33 test files that only exercised v1 code. Renamed `tests/proofs/wp6_bpy_run.py` →
`tests/proofs/temple_bpy_run.py` (the Blender driver of `test_temple_blender.py`; it imports no v1 module). Kept and
stripped: `test_temple_blender.py` (P0 instrument grade dropped), `test_temple_real.py` (glossy WP3b table test only),
`test_temple_design_checks.py` (builder warnings + the decal-standoff test moved in from `test_temple_relief_cue.py`).

The six relief modules then pending (`relief_trace`, `relief_check`, `relief_views`, `relief_rules`,
`relief_registration`, `relief_normal_residual`) were removed in build step 3. The stale doc links listed here were
fixed in the 2026-10-13 cleanup ([REVIEW_20261013.md](REVIEW_20261013.md) §7).

Checks (basetemp `data/pytest_tmp/f_archive`): `pytest --collect-only glasses3d/tests` collects 1904 tests with 0
errors; temple/spec/glb/lens_id suites 290 passed; `tests/agent` 420 passed; `test_wp2_*` 363 passed;
`test_temple_blender.py` 7 passed (renamed driver); all 105 host-importable modules of g3d, g3d_host and agent
import, and the 7 Blender-only modules import in Blender 5.2.

## Ledger (2026-10-10)

- `3e8e247` camera uncertainty + apex-gap diagnostics; `c9ad82f` owner accepted both apex comparison variants.
- `b46bda8` opt-in camera transfer + arm review checkpoint; `e846e0f` owner preference for the distal-arm revision.
- `dbebc7c` opt-in contour-normal refinement; `c97df92` retained replay failures + full diagnostic run;
  `db95572` fresh S1 floor + preferred export coverage audit; `8d6e006` full-proof progress; `e6b31a7` completed
  proof saved for the owner's review pause.
- `c99da5c` owner rejection + fresh-chat handoff; `70c9e90` opt-in smooth facet-to-arm comparison tool;
  `6c72c9f` owner decision to retain the preferred reference.
- `f0205a3` facet normals + complete P3 export checks; `b5add8f` preferred recovery + live photo diagnostic;
  `8a2113c` native recovery + R1 preservation checks; `b1c8651` all exported decals graded + preferred comparison.
- `166de93` isolated Astra temple lab; `008fc73` beige references + flat-temple views; `ddd6658` owner-authorized
  test without a reply cap; `62662f0` observed Astra test documented; `3ad4459` owner rejection and lessons
  (= `wp6-v1-final`).
- `62b25e4` WP6 v2 facets design (owner-approved rebuild).
- `4d84cf1` archive WP6 v1 + start docs; `fa857b0` seeing (pose basis, matched compare, white balance, photo_env);
  `04f8ceb` builder g3d.temple/2 + template + drawings + Blender layer; `c102efb` builder review fixes;
  `cf9cf1a` Astra facet tools, loop finish settings, rehearsals, light choice; `831a8a2` pre-flight (owner light C,
  gate/timeout fixes); next: surface-only distinct-head rule, rebuilt package and estimate.
- `8378bc4`, `4c24124` WP6 v3 build (fit, warp faces, crossing readings, seeing, lab, prompt); `be8d56d`,
  `7a3f295` blind stand-in rehearsal fixes + owner sheets; `7fcf037` paid run 8; then WP6 closed by the owner.
