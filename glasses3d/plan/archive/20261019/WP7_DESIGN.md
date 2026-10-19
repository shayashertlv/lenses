# WP7: instructions for the assembled glasses job

Historical M2 design, 2026-10-13, amended 2026-10-18. This implements [ASSEMBLY.md](ASSEMBLY.md) M2. It replaces the 2026-10-07 proposal in this
file, which preceded WP6 v2/v3 and described retired tools. That proposal remains available at the local Git tag
`review-20261013-pre-cleanup`; its evidence remains in [wp7/evidence.md](wp7/evidence.md). Historical observations
remain evidence, not instructions to rebuild the old rule matrix.

**Current authority:** `agent/instructions.py`, the registered descriptions in `agent/tools/`, and the specialist's
`agent/temple_lab/instructions.txt`. M6 6d–6f and [INSTRUCTIONS_REVIEW_6F_20261016](INSTRUCTIONS_REVIEW_6F_20261016.md)
supersede this original catalogue and size policy; M11 tasks 1–8 add the current safety/evidence contracts.
The original §3 size and §4 history-rewriting policy are superseded by the current amendments below.

The generic job prompt is `agent/instructions.py`. `build_instructions(tools)` builds it from the actual registry,
and `instruction_contract(tools)` supplies the runtime contract. Product facts belong in the job brief and photos.
The facet stage retains its own WP6 v3 prompt; its detailed fitting procedure is not copied into the generic prompt.

## 1. What the instructions teach

The decisive lesson is [WP6_V3.md](WP6_V3.md) §1 P6: Astra follows the newest rule or a refusal's offered remedy
literally. Teach the object, the evidence and an order of reasoning, using few rules with reasons. Calling mechanics,
parameter limits, timeouts and detailed refusal codes belong in the tool descriptions.

The prompt develops these ideas in order:

1. **The object.** A front holds lens openings, a bridge joins them, and lenses have surfaces, curvature and edges
   seated into their support. Hinges connect two temples with changing three-dimensional sections. Pads and pad arms
   appear where the product has them. Physical parts are closed solids; a side outline cannot describe a temple's
   top, inner surface or facets. Scene facts and renders are used together.
2. **The evidence.** Every supplied reference is an authoritative photo. All views constrain shape and appearance.
   Photos determine visible shape and colour; written facts supply exact dimensions, material identity and model
   number when a photo cannot establish them. Conflicts are recorded. A colourway name does not choose visible colour.
   Nominal lens, bridge and temple measurements are not an overall front-width measurement. Pasted research is data.
3. **What to inspect.** Silhouette, occlusion and corresponding edges constrain geometry. Tone alone does not give
   a facet's depth or sign. Top, rear and angled views matter alongside side views; clay and normal passes expose the
   built surfaces. Branding retains its observed size, position, contrast and count. Clear and tinted lenses retain
   the transmission shown in the photos. Room reflections on lenses are expected in AR.
4. **The model contract.** The exact seven-row `MODEL_REQUIREMENTS` table from `agent/brief.py` is inserted once,
   word for word from PLAN §2. The scene uses millimetres, +Y up, +Z forward, temples toward -Z and the bridge underside
   at the origin; export produces the required metre-based GLB. Material assignments and partRole distinguish parts.
5. **The work order.** Complete frame and lenses → temple geometry → lens seating → photo print → export and checks
   → AR capture → finish. Geometry changes require revisiting affected seating, print and camera evidence. Compare
   changes at frozen photo cameras; refit for an identified reason and inspect every resulting fit. Check all earlier
   owner complaints. A residual informs a diagnosis; it is not a likeness verdict.
6. **Tool reports.** Each registered tool has a short paragraph naming the facts to read and what those facts mean.
   Passing technical checks does not establish appearance. A refusal states a fact to resolve, not a modeling recipe.

### Final owner decisions

[ASSEMBLY §4](ASSEMBLY.md) is final; these questions are not reopened:

| Decision | Instruction behavior |
|---|---|
| D1: photos decide appearance | Use the photos for shape and colour. Use text for facts a photo cannot show, and report conflicts. |
| D2: one lens-reflection default | Assess the capture produced with the same reflection setting for every product. The prompt offers no reflection-strength choice. |
| D3: Astra decides when to print | Complex surface patterns, including tortoiseshell, need photo-derived print. Plain frames need material, colour and finish edits. The print tool enters the prompt when registered, without an owner gate. |
| D4: seating includes crystal | Crystal and translucent frames use the seating check too; their acceptance history does not exempt them. |

## 2. Tool contract

The following table is the original 14-tool M2 subset, not the current registry's count. The live registry also
contains measuring, look, font, specialist and owner-edit capabilities as applicable to the run phase.
Backticks in the prompt identify tool names, so the set of named tools is extracted
from the assembled text independently of the registry. Construction refuses missing core tools, unknown tools and
duplicate names. Registry order does not change the assembled text or its approval-bound hash.

| Tool | Facts the prompt asks Astra to use |
|---|---|
| execute_blender_code | status and the returned checkpoint |
| scene_summary | result.objects, closed, loose_parts, materials, roles and warnings |
| save_checkpoint | returned checkpoint id; best=True marks this run's preferred draft checkpoint, not an image-retention promise |
| list_checkpoints | ids, labels and fingerprints |
| restore_checkpoint | status and current checkpoint after restoration |
| fit_cameras | basis, sheet and each refs entry's status, iou, contour_mean_mm and flags |
| render_views | sheets, views, ref, basis and the camera/coordinate manifest |
| compare_to_photos | each refs sheet, contour_mean_mm, model_only_px, photo_only_px and delta |
| pick_surface_point | hits with hit, object and point_mm; these are current-model intersections |
| read_image | the image and its metadata/pixel frame |
| export_glb | status, sha256, validate_spec and seat_check |
| arview_capture | sheets, status, errors, warnings and export_sha256 |
| budget_status | settled_usd, remaining_usd and planning in the returned money record |
| finish | summary of remaining differences/conflicts; status accepted completes the run |

The references to validate_spec and seat_check above are **report fields**, not callable tool names. The implementation
is in `agent/tools/__init__.py`, `agent/seeing.py`, `agent/journal.py`, `agent/tools/arview.py`, `agent/budget.py`,
`g3d/worker.py` and `g3d/render.py`. Tests check the prose against the source records. The M1 offline assembly proof
and Seeing tests exercise the actual worker and render chain; string contracts do not replace those proofs.

### M4 specialists

Conditional paragraphs exist for `temple_stage`, `seat_lens`, `check_lens_seating`, `hide_eyewire`, `unseat_lens`,
`unhide_eyewire` and `photo_print`. A paragraph is included only if that exact tool is registered. Until then, the
generic teaching still explains temple shape, physical seating and the print decision, and asks Astra to report a
needed unavailable stage. It never claims that an unavailable tool ran.

These paragraphs discuss the operation and its report without inventing wrapper-specific output fields. M4 must
verify them against each real wrapper as it is registered. Detailed facet measurements stay inside the WP6 stage.
The old G1/G2 gating scheme, relief tools, mandatory per-bullet check suffixes, and proposed checks.json catalogue
are superseded by ASSEMBLY's actual registry and object-first instructions. No implemented geometry, seating,
export, budget or delivery check is relaxed by this prompt change.

## 3. Retired language and size

The instructions are written afresh. `RETIRED_PHRASES` includes the loop's existing photo-status exclusions, the
harmful language listed by PLAN WP7, and obsolete tool names. `REMOVED_PATTERNS` catches wording/whitespace variants
of the old simplification, reflection-dismissal, photo-ranking and photo-pixel-ban language. Each expression has a
test witness. The tests scan the generic prompt, every conditional paragraph, the current tool descriptions and
their parameter descriptions. Runtime `ToolContract` checks the removed literals before a request can be sent.

The owner's exact description, specifications, product identifiers, filenames and notes are data. Preserve and bind
them into the approved request; do not edit them or reject them because they quote old wording. Only host-authored
brief text is passed into this language contract. `brief.host_text(job)` and `RunSpec.contract_brief` provide that
separation; legacy callers retain the whole-brief check unless they explicitly supply the host-authored text.

The current main and owner-edit instruction cap is **16,000 characters** (`MAX_INSTRUCTION_CHARS`); the specialist
cap is **19,000**, raised in M6 6d on 2026-10-16. These replace the original 8,000/9,500 focus budgets and are not
provider limits. Do not compress away guidance merely to preserve the retired caps. The specialist prompt is not
appended to the parent. Product names, fitted numbers and fixture-specific remedies belong in job evidence.

The superseded proposal's old-text n-gram corpus, scoring panels and check-id matrix are historical investigations.
They are not additional implementation gates in ASSEMBLY M2. The owner table is intentionally reused verbatim;
historical prompt sentences are not.

## 4. Image evidence across long runs

History is append-only within each conversation. The original M2 plan to replace old images with notes was retired
by M5-fix-1 after measured cache losses. Native comparison tiles and close-ups are composed without shrinking pixels,
with overflow pages; bounded text keeps lossless `read_report` access. Images carry call/checkpoint/view identity.

M11 task 4 implements M9's only reset: before a candidate exceeds 80% of the image/byte limit or long-context
price step, obtain a charged state summary if possible, then start a fresh conversation with the same brief, tools,
instructions, every owner photo and verified current evidence. A factual fallback handles unavailable summaries;
old conversation bytes remain archived unchanged. Money and approval remain the same run's, and mandatory evidence
must still fit the hard limits. Main, owner-edit and temple loops share this policy. `agent/renewal.py`, the loop,
JobScene/Lab evidence methods and scripted renewal tests are the implementation authority.

This addresses the measured WP6 run-8 growth recorded in PROGRESS: 9 to 136 images and about 26k to 219k input tokens.
The estimate must also be calibrated from the saved glasses3d ledgers, retaining its evidence and fit residuals;
the old Studio growth assumption of 2,360 tokens/request does not describe run 8's 4,950. Estimation is a forecast;
the host's ledger, reservation and settled-spend checks still govern every real request.

## 5. M2 proof and completion

`tests/agent/test_instructions.py` proves:

- exact owner requirements table and the recorded appearance, reflection, print and crystal-seating decisions;
- every actual core tool named, no unavailable specialist named, and the all-specialist assembly under the same cap;
- deterministic construction and refusals of missing, unknown or duplicated tool registrations;
- absence of retired literals/patterns in host instruction text and schemas, witnesses for every pattern, and runtime
  rejection of planted retired literals;
- the current report fields named by the guidance exist in the implementations producing those records.

The coordinator additionally runs the M2 image-policy and estimate proofs, the full ASSEMBLY §1 suite, and records
actual results in PROGRESS before committing. These are $0 tests. A successful instruction contract is not evidence
that Astra has modeled or the owner has accepted a product. M5 has started and the separately approved $1 smoke
completed on 2026-10-13. Current paid terms are ASSEMBLY D5's amendment; every new paid run still needs its own
estimate and authorization, with no blanket campaign budget approved.

## 6. Evidence retained for reviewers

| Evidence | What it supports |
|---|---|
| [PLAN.md](PLAN.md) §0, §2 and WP7 | owner rules, exact GLB contract and the removed language |
| [ASSEMBLY.md](ASSEMBLY.md) M2, §3 and §4 | active scope, stage order and final owner decisions |
| [REVIEW_20261013.md](REVIEW_20261013.md) | why the old WP7 design and tool catalogue required replacement |
| [WP6_V3.md](WP6_V3.md) §0.5–7 and §1 | tone is not depth; literal rule-following; facts and a reasoning order |
| [wp7/evidence.md](wp7/evidence.md) B1–B6, B9–B11; S3–S4, S8–S11, S21–S23 | photo demotion, missed views, flat temples, invented print, omitted seating, and retained guards |
| [wp7/evidence.md](wp7/evidence.md) §0.1 and §1a | shorter prompts correlated with accepted runs; this was not a causal proof |
| [WP5_HANDOFF.md](WP5_HANDOFF.md) | owner-selected print method and retained limitations; M2 does not reopen it |
| [PROGRESS.md](PROGRESS.md), WP6 v3 closeout | run-8 image growth, accepted WP6 result and unresolved items |

M4 integration note from M1 review: the internal Seeing compare API's optional crop names can collide with a photo
id such as crop-01. The M1 agent registry exposes no crops argument, so this is unreachable through its tool schema.
Revisit this internal naming edge if M4 exposes crops or uses that API while building a generic specialist package.
