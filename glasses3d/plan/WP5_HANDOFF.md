# WP5 quality repair handoff — active repairs 2026-10-08

**Live repair update:** the current session started clean at `8007c18`. This update is ongoing bookkeeping,
not an automatic context handoff. Read PROGRESS.md's Active resume for the newest run status. Local evidence is
`data/wp5/repairs_20261008/`; baseline replay exactly matches the accepted PNG and all 160 original A4 holes are
saved. A proved one-texel strip-coordinate adapter error has been repaired; 24 focused regression cases pass.
Failed `quilt-cut` experiments improve boundary joins but still fail A4; their runtime API was withdrawn and source
archived locally in `cut_experiment_v2/`. Whole-hole copying and boundary-corrected PatchMatch also fail overall.
A5 seam-field and curvature experiments still fail; curvature overshoots were rejected. Donor-only adaptive fill
passed ten truth-separation checks but still failed A4 on all original cases as a set. B3 view 01 confirms a
substantial left-brow projected-photo deficit that persists without gains and when bypassing the atlas; the frozen
result remains three failures / one pass. The coordinate fix is committed as `bd3b3eb`.

Two further seam defects have independent synthetic reproducers: edge lookup into empty/wrong-chart gutters,
and black gutter contamination of reflection. Their fixes have **27 focused tests passing**, with no change to
the six-texel reflection band or frozen proof. Fresh default bake exited 0: **A3/provenance pass**, A5 improves to
**1.184261 / 1.200921 but still fails**, all A4 rows still fail, protected support is 739,276 (143 fewer).
Repaired-graph gradient and log-radiance probes also still fail at offset 8. B3 views 03/05 decompositions are done;
their three original eligible rows still fail. View 05 right brow improves substantially without gains; a geometric
front-reference fallback exited 0 at 05:30:06 but still failed both eligible held-out-05 rows (left/right spectrum
0.398069 / 0.388148). No runtime gain policy changed. `adaptive_slope_probe.py` exited 0 at 05:37:33;
ten truth-separation checks passed, but A4 still fails overall. Seam repairs are committed as `ccf88df`.
Signed diagnosis confirms predominantly lost brow detail. Unscreened Poisson stitching passed five synthetic
checks but still fails A5 at 8 (**0.887166 / 1.038095**; exit 0, 05:41:47); no clipping/protected changes.
Conditional texture synthesis exited 0 but failed A4 and introduced ripples in inspected crops; rejected.
The seam diagnosis is complete: main strip-wrap pairs individually pass after Poisson, but the frozen pooled
verdict still fails; no criterion changed. Texture-objective selection exited 0 but still fails A4 as a set;
all original holes and ten truth-separation checks are retained. Gain-topology bake exited 0: A3/provenance pass,
739,664 protected texels, but all A4 rows fail and A5 worsened to **1.348161 / 1.345189**. The gradient-aware
seam diagnostic passed five synthetic checks but still fails offset 8 (**0.967042 / 1.062656**); no clipping or
protected changes. Contour transport also failed A4 over all original holes. The anchor inventory is complete;
01/05 omissions leave only one mutual brow-source comparison, insufficient to choose a better view. No policy
changed. A further independent gain defect was reproduced: comparing cell interiors treated a real surface
gradient as an exposure jump. Comparing the two extrapolated colours at their common physical boundary passes
the new tests and **32 focused regressions**. Its first bake exited 0 but still failed A5 (**1.257212 / 1.204647**)
and all A4 parts. A new narrow-group synthetic failure required estimating slopes from observed texels inside
each node, rather than only neighbouring nodes; **33 focused tests now pass**. Both versions and red checks are
retained locally. This checkpoint contains the correction, with no accepted production quality proof. Its within-node
bake exited 0 at 06:26:05: A3/provenance pass, 739,014 protected texels (405 fewer than baseline), but A5
**1.301842 / 1.289024** and all four A4 parts fail. This implementation fix does not establish quality acceptance.
The common-boundary correction is committed as `83243b7`. B3 views 01/03 exited 0, retaining their three
failures/one pass and one failure. View 05 also finished (06:35:42), retaining both failures: B3 still six of seven.
Affine-exemplar matching failed A4 overall; paired relative shading failed the frozen keep test, including after
enforcing the existing factor bounds during its fit (pooled dE00 drop 0.365, worst leave-one-pair-out -0.140).
No runtime API changed or model was adopted. Moment-normalized exemplar matching also finished (06:45:02) and
failed A4 overall, preserving all 160 original cases. Bounded RGB shading also failed keep (pooled dE00 drop
0.240, worst leave-one-pair-out -0.235), so no integration adapter or full bake is needed for it.
Full regression `regression_019d321_run.json` finished at 07:35:20: **340 passed, 1 expected xfail, no
failures/errors/skips**, 1,252.40 s, exit 0. Source hashes were unchanged at `019d321`; the documented A0 periodic
wrong-match xfail remains. Renamed-input check exited 0 at 07:44:44, all nine checks pass and source unchanged:
**738,664 protected texels**, protected mask and all source/tier/class maps match the new canonical bake;
1,323,383 fill pixels change with name-based seeds. No unseen-product claim or repeated G1 migration.
**No active run.** `consensus_probe_run.json`, PID 7708, ran 08:36:46–08:48:10, exit 0, source/instruments
unchanged. All 160 original cases converge, no fallback; the raw/observed-slope-joined comparison still fails A4
overall. Joined pooled fine errors BL/BR/TL/TR: **0.249001/0.219569/0.176812/0.149794**; only TR passes.
Fixed first-two-seed-0-hole crops were inspected: many block joins disappear but real stripe/blotch structure is
still lost. No production integration; saved radii were diagnostic, not this method's own radius-selection proof.
Complete production quality proof `quality_019d321_run.json` finished at 08:11:28, PID 31236, exit 0,
source unchanged: all six held-out views, five seeds and serial parity, no skips. A1, six A2, A3, provenance and
A7's six files pass. **A4 all four fail; A5 1.244709/1.269608 fails; B3 six of the same seven eligible rows fail**,
with no dropped original eligibility. The exact PNG is
`c00204d54d3a529c33417658e34e411d20d18c4ccf6dc01548671c98e830c7cb`.
`tooltest_019d321_run.json` exited 0 at 08:13:18 with 15/15 checks and untouched source scene.
`ar_019d321_run.json` exited 0 at 08:15:29: fresh worn/all-pose/black-probe capture, exact runtime-independent
measurement parity and controlled geometry/materials/cameras/fitting. `photo_ar_019d321_run.json` exited 0 at
08:19:44, verifying historical/current capture hashes and the frozen photo cameras. Inspected labelled
`quality_019d321/ar_comparison.png` and `photo_focus.png`; appearance is close to the accepted revision but
brow-top pattern and temple differences persist. No final owner verdict was requested while quality fails.
The new `quality_019d321/proof_summary.json` indexes all current evidence, including regression and reuse;
`appearance_summary.json` preserves every applicable role/form/group and the existing AR streak instrument.
Current print calibration L* p50 residuals -5.22/-5.39, held-out contrast 0.650/0.824, chroma failures at all six
pairs and front hue residual -5.25 degrees remain. AR streak indices -x +45 / +x -30 / +x -45 are
1.942/7.520/11.501, without an invented threshold. All immutable source/B/accepted hashes remain exact.
Next: use pending research at `data/wp5/repairs_20261008/research_request.txt` to specify and independently test
the next donor-only texture method. An independently derived L1 patch-consensus objective provided another
safe offline action, now completed and failed in its declared form. The local `consensus_fill.py` alternates
complete-original-donor assignment
and overlapping channel-median reconstruction, with a monotone objective, existing strip restrictions, fixed
descriptor shortlist and iteration cap. Nine contract controls pass; shifted-pattern initializers improve from
28.278378 RGB RMSE to exact reconstruction in both compact/long-thin controls. The labelled sheet was inspected.
No source-backed method claim, surface-chart integration or new-method radius proof exists yet.
No quality or final AR acceptance is implied. Runtime remains `019d321`; later runtime edits need fresh validation.
An independent synthetic texture-phase control exited 0 at 08:29:30: fixed-guide-gradient stitching preserves
the coherent control but leaves most copied-pattern misalignment (RGB RMSE **9.789507 -> 9.468284**); protected
pixels exact, no clipping. The labelled sheet was inspected. `seam_texture_phase_control.json` records the
solver evidence. This supports coordinating texture choices across charts, not tuning offset corrections to
A5 or claiming that protected observations make it impossible. No runtime change was adopted.
Context validation's first attempt
failed before real cases on unsupported long-thin
strip training; its global-donor fallback is repaired, failed evidence retained, and all ten poison/contract
checks passed in v2 before the 160 original holes. V2 exited 0 at 07:02:42 but still fails A4 overall. No
production filler was changed. An owner research question about donor-only multiscale inpainting is pending;
authorized offline seam/gain work continues independently. No waiver or stop permission was requested.
A further folded-surface test exposed a crease error in the gain leveller's chord midpoint. Comparing on the
intersection of the local tangent planes passes the new test across three fold angles/rigid placement and
**34 focused tests**. This checkpoint contains the gain correction. Its bake exited 0 at 07:07:22:
A3/provenance pass, 738,664 protected texels (755 fewer than baseline), but A5 **1.244709 / 1.269608** and all
four A4 parts still fail. Comparison-point displacement p95/max is 0.109326 / 0.555875 mm. The complete folded
variant proof above now includes B3, retaining its six failures. The separate
seam-gradient prototype has four synthetic checks; its prepared chord/folded comparison uses the older
within-node candidate cache and must not be called a proof of the folded-gain bake. Chord variant finished at
07:09:48, A5 **0.971405 / 1.055846**, still failing at 8. Folded variant finished at 07:11:34,
**0.981730 / 1.061984**, also failing at 8, slightly worse. No protected changes or clipping; all 347 components
are anchored, solver residuals below 1e-7. Labelled `seam_current_review.png` was inspected without obvious
coloured overshoot at that scale. This is no AR/appearance acceptance, and neither method is adopted.
The earlier prepared paired-shading full candidate is not run because its keep test fails.
No source-selection policy or quality criterion has changed. The gain-field fix is committed
as **`eda0840`** and removes repeated physical
node constraints: independent density/repeated-sample tests failed before and pass after; **29 focused tests pass**.
No weight/clip changed; the production quality regression above remains an open issue.
The separate fill-boundary prototype preserves a synthetic affine field that its zero-slope predecessor altered,
and improves both temples' saved-case A4 energy; both brows remain failures. All evidence is local and unaccepted.
No new candidate is accepted, no thresholds changed, no paid calls or WP6 work. The full regression passes;
final quality/reuse/appearance acceptance remains incomplete.

Prepared during the source-frozen regression: `priority_exemplar_probe.py` compared confidence/isophote-priority
patch propagation, with/without the already tested observed-slope join, on all 160 original holes at saved radii.
Nine synthetic contract checks pass; both this method and existing quilt reconstruct the periodic control exactly,
so no advantage is established. The real-case run exited 0 at 07:38:57 with source unchanged: joined pooled fine
errors **0.130544 / 0.266228 / 0.280017 / 0.218029**; only BL passes A4. The fixed diagnostic crops show visible
block joins. Rejected, no runtime filler change. `seam_protected_audit.py` finished at 07:39:25: 5,162 of 28,952
offset-8 pairs are fully protected. The across-p95 lower bound with editable pairs zero is 5.993749, below the
current within-p95 11.470301; protected pixels do not prove A5 impossible. Frozen criteria remain unchanged.
`reuse_019d321.py` tested the repaired code on G1's existing frozen renamed job without rebuilding its historical
proof; this check passed as recorded above.
`gain_folded_review.png` was inspected against B and the accepted revision: broad blurred/smeared fills remain.
This is an atlas-only diagnostic with source hashes, not a final AR review or appearance pass.

This handoff was explicitly requested by the owner. It is not a context-limit event. Implementation baseline:
`f020b57e99f9a7eaba77606d3748a4724998d018` on branch `glasses3d`. The later documentation-only commit containing
this file prepares the next session; read `git log -3` for its hash. No repair/proof process was left running.

## 1. Owner intent, authority and scope

The owner said: "i want to complete all the issues you mentioned" and asked for a new session to work on WP5
"until its good". **Repair all remaining WP5 quality issues. Do not offer a waiver or scoped completion again.**
The earlier verdict "Close enough—continue with the library proof" accepted one exact revised appearance and
authorized G1. G1 is now done. That verdict neither passes failed numeric checks nor approves later changed assets.

Read root README.md, `glasses3d/AGENTS.md`, PLAN.md fully, PROGRESS.md's Active resume, this file fully,
WP5_DESIGN.md (including its current addendum), and the relevant PORT_REVIEW §10 protocols / §12 library review.
Latest owner instructions override repository prose. Active resume and this handoff supersede historical pending
actions; retain historical measurements as evidence. WP2_DESIGN §10 records the completed library dependency.
RULES_AUDIT.md reviews old source rules; its live-site limits do not override PLAN §2's development-arview spec.

Work sequentially: no workflows, subagents or TaskStop. Keep work in `glasses3d/`; do not alter the root app or `ar/`.
Never push, stage/commit only explicit paths, and never apply/pop the old stash. Keep data, photos, GLBs, Blender
files and renders out of Git. Local labelled proof images may be inspected and shown. Never read `automation/.env`
or credentials. No web research; ask the owner a precise research question only if it is genuinely needed.
All owner images, including generated views, count as real photos. Astra builds product models; our offline outputs
are labelled TOOL TEST copies. Do not hand-author a product model or painted exceptions to satisfy the fixture.

The owner authorizes continued $0 diagnosis and repair. Ask before **each paid run**, with a cost estimate. STOP B
blocks completion/acceptance/advancement, not repair work or useful comparisons. Ask for missing information or a
new final appearance verdict only when needed; do not ask again whether to continue repairs.

## 2. Finished work — preserve, do not repeat

| Work | Evidence / state |
|---|---|
| WP0, WP1, WP3, WP3b, WP4 | Complete and tested; individual proofs in PROGRESS |
| A–E repair groups | `e3fd8fe`, `9a9e540`, `f6fa360`, `b3df813`, `82c5623`; verified and committed |
| WP5 owner-selected B promotion | `4ca2da3`; B selection bound to original verdict, original A backed up |
| Supported-pale donor-mask repair | `a8211ed`; accepted revised print on a TOOL TEST copy; B baseline preserved |
| Conversation context rule | `476fc0c`; automatic handoff only at reliably measured 80% of this chat's current window |
| G1 reusable library | `f020b57`; explicit inputs, negative checks, migration identity, renamed inputs and runtime capture parity verified |
| WP2 | Stages A–C verified; G1 library dependency ready, agent wrappers remain gated |
| WP6 | Delegated decision resolved: KEEP STOP-I, R1 and every frozen R3 clause; no waiver; outside this task |
| WP7 / WP8 | Design only / not started; do not advance as part of this WP5 request |

Runtime entry points are `inputs.job_from_dir(job_dir, glb=..., basis=...)`, `production.bake_print(...)` and
`production.calibrate_level(...)` under `g3d_host/photoprint/`. Explicit thresholds and a named frozen camera basis
are required; no runtime fixture imports or FT1119 defaults. Calibration cannot overwrite a bound owner selection.
The bake receipt says independent quality proof `not_run`; baking alone never grants acceptance.

G1 proved canonical equality of four maps; A7 serial/pool equality of four maps, registration and stable report;
15/15 Blender-copy checks; and exact fresh runtime/independent AR measurement parity. Renaming four parts and six
photos preserved source/tier/class maps and **739,419 protected texels**. **1,324,055 fill pixels changed** because
existing seeds include part names. This is same-product reuse evidence, not proof on an unseen product.

Historical G1 migration regression: **317 passed, 1 expected xfail, 6 fixture setup errors, no skips**, 1,191.70 s. The camera
argument omission was repaired, followed by **34 affected tests passed**. All 30 G1 tests passed in the full run;
seven fitmesh tests passed separately. Net coverage after repair is 323 passes + 1 expected xfail over 324 cases.
Do not describe that first full invocation as a clean exit 0. The current repair session's fresh successful full
regression is recorded at the top of this file; further runtime changes require appropriate fresh validation.

## 3. Evidence and immutable reference assets

Paths below are relative to `C:/Users/Shay/PycharmProjects/lenses/glasses3d/`. All `data/` contents remain local-only.
Do not overwrite evidence directories or historical owner sheets. Use a fresh dated run directory per candidate.

| Path | Meaning |
|---|---|
| `data/wp5/repairs_20261008/quality_019d321/proof_summary.json` | Latest repair proof index; failed A4/A5/B3, passing regression/reuse/export, diagnostic AR |
| `data/wp5/repairs_20261008/quality_019d321/bake/bake.json` | Latest complete frozen quality report; all seeds/views, A7, STOP B remains |
| `data/wp5/g1_20261007/proof/bake/bake.json` | Immutable pre-repair G1 quality report: A4, A4_fillers, A5, all B3 rows, B3_summary, STOP_B |
| `data/wp5/g1_20261007/proof/bake/` | Four maps, registration/report, source/class maps, six held-out renders, sheets, serial proof |
| `data/wp5/g1_20261007/proof/accepted_identity.json` | Exact canonical migration proof |
| `data/wp5/g1_20261007/proof/renamed_identity.json` | Renamed-input proof and its limits |
| `data/wp5/g1_20261007/proof/tooltest/tooltest.json` | 15/15 Blender checks, tied to the revised PNG |
| `data/wp5/g1_20261007/proof/capture_parity.json` | Fresh all-pose/portrait/black-control parity |
| `data/wp5/g1_20261007/tests_run.json`, `tests.log` | Full invocation, timings and original setup errors |
| `data/wp5/g1_20261007/repair_tests_run.json`, `repair_tests.log` | Successful focused repair rerun |
| `data/wp5/g1_20261007/migration_ast_audit.json`, `final_audit.json`, `commit_receipt.json` | Migration, evidence identity and implementation commit |
| `data/wp5/resume_20261007/evidence/owner_focus.png` | Exact revised comparison accepted by the owner |
| `data/wp5/resume_20261007/` | Pale-blotch diagnosis, bake, appearance comparison and revised tool-test copy |
| `data/wp5/ft1119_bake/`, `data/wp5/owner_B_20261007/` | Preserved owner-selected B and promotion proof; A backup is `ft1119_bake/pre_owner_B/` |
| `data/wp5/ar_calibration/calibration.json` | Owner-B `production_selection` and original recommendation/candidates |
| `data/ledger/owner_verdicts.jsonl` | Row 4 chooses B; row 5 binds its promotion; row 6 accepts the revised appearance |
| `data/wp5/all_angles_sent_1042/` | Frozen original A/B/C/photo comparisons; never regenerate in place |
| `data/wp5/ft1119_strip_null/report.json`, `data/wp5/a0/a0.json` | Registration and A0 diagnostics |
| `data/wp3b/ft1119/cameras.json`, `data/reference/projection/q001.glb` | Explicit fixture camera/model inputs |

Full SHA-256 identities:

| Asset | SHA-256 |
|---|---|
| Revised `resume_20261007/bake/print.png` (same as G1) | `c39869cee1e1caf11f99141a3f93983485806900c73c8a7f977588cf2e11f9c8` |
| Revised `resume_20261007/tooltest/q001_painted_TOOLTEST.glb` (same as G1) | `5497ed451c3409d0e780fe6f39d6d9528cf1efc5cf7554118e4334c0a15a554d` |
| Preserved production-B `ft1119_bake/print.png` | `b52f31129af3e760195dc2fcde59fafc891340efad9dd0b9b6598c4fae8813b6` |
| Source `data/reference/projection/q001.glb` | `af6e9d100fcf7a30ee3862884bff6418f7166a7395c34834559c2da6fcad6cd7` |

The local `g1_20261007/run.py`, `sequence.py`, `repair_tests.py` and audit scripts are run records with fixed paths,
not general-purpose safe rerun commands. The sequence's failed-test status refers to the first run; consult the
later focused repair record. Retain `failed_names_*`, `failed_fixture_*` and archived job directories as history.
Likewise `data/handoff_20261007/results_*.json` are old A–E review evidence; its Claude JS workflows and stopped-work
patch/tar are historical recovery material. Do not run those workflows or reapply the old patch.

## 4. Remaining issues and frozen protocols

### A4 — fill quality, all four parts fail

Latest production-density, seed-0 eight-hole results from STOP_B (rounded):

| Part | Median absolute log energy, fine / coarse | Mean-colour ΔE00 / limit | Median seam ratio |
|---|---|---|---|
| Brow_Left | 0.487 / 0.158 | 1.89 / 4.63 | 2.21 |
| Brow_Right | 0.334 / 0.136 | 2.17 / 4.64 | 2.29 |
| Temple_Left | 0.218 / 0.147 | 2.13 / 4.35 | 1.60 |
| Temple_Right | 0.188 / 0.061 | 1.87 / 2.47 | 1.37 |

`tests/instruments/print_metrics.py:a4_criteria` requires log-energy <= 0.15 in both bands, seam ratio <= 1.2,
mean colour <= max(2, median donor-ring baseline). The literal <= 2 result is retained separately. The neighbouring
real-vs-real floor is diagnostic only; the old claim that no filler can pass was withdrawn in PORT_REVIEW.
Read the full five-seed comparison in `A4_fillers`, not just these eight-hole rows. Both quilting and PatchMatch
must be evaluated fairly at their own selected radii. Quilting was chosen to avoid the owner's observed wood-grain
streaks; simply switching to PatchMatch is not an established fix. If a hybrid is justified, test both compact and
long thin holes so the test covers the actual branches. Preserve protected observations and exclude hole truth,
hardware, lettering and glare from donors. Separate parameter selection from evaluation; never replace failing
seeds/holes with favourable ones. Additional checks may supplement, not replace, the frozen checks.

### A5 — continuity outside the copied seam band

At **15.94969 texels/mm**, offsets 7 and 8 have across/within p95 ratios **1.288293 / 1.300100**, with across p95
**15.52555 / 16.88483 ΔE00**. Both fail. The current reflection band is 6 texels. Independent `seam_steps` samples
must grade outside it: across p95 <= max(3, within-chart p95 at the same separation). Same-point equality after
harmonisation is constructed; the 8 texels/mm fixture passing does not prove production-density continuity.
Localize high-error seams by chart, side and photo/fill class. Repair the cause without expanding the reflection
band merely to hide samples, blurring the whole texture or changing protected pixels. Keep current offsets and
thresholds as regression evidence if the seam algorithm changes. PORT_REVIEW §10 build-step-3 fix round 1 and
build-step-4 fix round 1 explain why the original design's literal same-point <= 3 test was replaced.

### B3 — held-out realism, 6 of 7 eligible region/view rows fail

All six held-out bakes ran; eligible rows occur in views 01, 03 and 05. Views 02, 04 and 06 currently report no
eligible row, not passes. Read every row's criterion, support and projected share. Do not remove hard views,
silently shrink eligibility or use held-out pixels to fit donors, shading or gains.

The frozen verdict is protocol `10bf932`: **atlas before AR levelling, no re-lighting**. `relit_h` uses the held-out
photo's own shading and is diagnostic only (latest 1/4 passes). It cannot replace the verdict. `print_metrics.evaluate_b3`
and `b3` own the current spectrum, correlation-length, decile and energy criteria; WP5_DESIGN §8 describes
them. Verify sampling/support and distinguish fill, donor projection, diffuse shading and registration effects
before proposing a fix. A different B calibration alone cannot fix this pre-level test.

### Appearance and registration — keep the limitations visible

The brow-top repair removed isolated pale donor seeds that dilation/fill surrounded with dark ragged holes.
Photo-04 protected anchors and gain fitting also affect those regions; gain values were recomputed from changed
donor support, not held identical. Reassess any remaining pale blotches, off-angle lightness, flat/grey +x temple
in turned views, and rounder/oranger -x temple spots versus photo 03. Use source photos, original B, accepted
revised print and the candidate in labelled crops and worn/turned AR views. +x is the wearer's left; q001 names
use the viewer's side, so use geometry/sign rather than assuming names encode anatomy.

Pixel-verified area remains **0%**. A0's literal bound and B1 fail; B2 is diagnostic under the owner's already
approved single-source mode. This is not a hidden A4/A5/B3 waiver. Do not call registration passed, invent a new
exemption, or silently require all regions to register before proceeding. Diagnose relevant registration or
instrument defects with independent evidence; otherwise retain exact failed/unverified statuses at closeout.
One additional known A0 defect is the strict expected-failure test
`test_register_synthetic.py:test_x3d_periodic_wrong_match_is_refused`: an 8 mm shifted print periodic in 3D x can
pass the strip-null gate. This is distinct from the literal A0 null-mean bound. Keep it visible and review it if
registration/null code is changed; remove the xfail only after a real fix and an independent proof, never to tidy
the test summary. The approved single-source mode does not assert this registration instrument is defect-free.

Silver metal/cream lenses versus gold/beige photos, the one-sided tip medallion and silhouette differences are
source-model/material observations, not proven photo-print algorithm defects. Keep them in the final limitations
and material note for Astra. Prove tool behaviour on copies if needed; do not hand-edit the delivered product or
claim WP5 fixes those unrelated model contents. Any actual Astra run needs its own prior paid approval.

## 5. Ordered repair and verification work

1. Confirm branch, working-tree state and baseline hashes. Read the saved report and independent instruments
   before launching anything expensive. Create a failure map by part/hole/seam/view/source class and write a
   short root-cause hypothesis. Start with the highest shared cause revealed by the evidence; A4/A5 may interact.
2. Implement a general, bounded correction in the existing tool. Relevant code: `g3d_host/photoprint/fill.py`,
   `quilt_t.py`, `project.py`, `seams.py`, `labels.py`, `texels.py`, `gains.py`, `shading.py`, `metal.py` and the
   supported-pale mask implementation identified by the appearance commit. No product-coordinate paint rules.
   Keep independent instruments independent. A suspected instrument defect needs a separate reproducer and
   review, not a weaker threshold. Preserve failing evidence and explain why the correction is general.
3. Use focused offline checks to test the hypothesis, then run the complete current quality proof on a fresh
   candidate. Preserve A1, all six A2, protected A3/provenance, A6/B5, A7 and owner-level identity. Inspect STOP_B
   and every eligible quality row; exit code 0 alone is insufficient. Do not merely re-pin known-failure assertions.
4. Complete A4, A5 and B3 repairs, then fresh appearance comparison on the actual candidate GLB with its exact
   embedded PNG. Use `appearance_proof.py`, `step6_proof.py`, `all_angles.py` and the runtime capture API as relevant.
   Review the existing lightness, contrast, chroma, hue and streak measurements on all applicable matched and
   held-out rows; retain eligibility and distinguish print defects from measured source-material effects.
   Keep B selected unless evidence requires a new candidate, and never apply level twice. Preserve source scene,
   geometry, UV/material-control invariants and accepted historical artefacts. A materially changed final result
   needs a fresh owner verdict tied to its hashes; present one clear question with labelled images after the
   technical evidence is ready. Do not re-ask the verdict for the old accepted asset.
5. Preserve the reusable API and negative checks. G1 need not be rebuilt; test affected runtime paths and renamed
   inputs after algorithm changes. `tests/proofs/wp5_library.py` canonical mode asserts the OLD accepted texture
   identity: do not use that old hash as a requirement that intentional improvements cannot change the image,
   or rewrite historical identity evidence to make it pass. Add candidate-specific reuse/serial/parity evidence.
   The FT1119 proof driver's `_heldout_job` intentionally reloads the fixture: it is not a generic unseen-job B3
   harness. Keep that scope explicit; if adding a generic proof, bind its actual job/parts/cameras throughout.
6. Run a fresh full required-data WP5 suite after final edits, plus affected independent instrument, export,
   fitmesh and comparison-driver checks. Require no unexpected failures/errors/skips; document the existing A0
   xfail, if still present, rather than treating it as quality success. Update PROGRESS and this handoff, re-read
   the full diff and every new file against the findings, then commit explicit paths and report exact test/proof
   outcomes. Never push.

## 6. Run safely and preserve useful records

Use `C:/Users/Shay/PycharmProjects/lenses/glasses3d/.venv/Scripts/python.exe`, repository root working directory.
Blender is `C:/Program Files/Blender Foundation/Blender 5.2/blender.exe`. Check free commit before pools:
`(Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory` (KB). Only one pool-heavy job at a time. Three workers
worked with about 12 GB free commit; choose conservatively from current availability, not CPU count alone.

Use a fresh local Python runner file with a `__main__` guard. Launch hidden/detached from PowerShell, redirect logs,
and record arguments, PID, timestamps and the true exit code in JSON in `finally`. Follow the structure of the
historical runner, not its fixed output paths. Set `G3D_REQUIRE_DATA=1` for tests. Inside the runner, set the
photoprint `procs` default worker budget as well as explicit CLI worker counts; pytest otherwise creates its own
pools. No shell timeout, no `| tail`, and no killing a healthy silent run. Full suites have taken 20–46 minutes.
Inspect CPU/children/log timestamps to investigate a stall; compare dates as well as clock times.

The existing FT1119 proof commands below have verified flags. `<new-run>` means a new absolute directory beneath
`glasses3d/data/wp5/`; substitute it in the runner, not literally at the shell. Use 3 only if current memory permits.

```text
python -u -B -m glasses3d.tests.photoprint.bake_ft1119
  --out <new-run>/bake --workers 3 --heldout-workers 3 --a4-seeds 5 --a7-serial
  --level C:/Users/Shay/PycharmProjects/lenses/glasses3d/data/wp5/ar_calibration/calibration.json
  --tooltest <new-run>/tooltest/tooltest.json

python -u -B -m glasses3d.tests.photoprint.print_tooltest
  --bake <new-run>/bake --out <new-run>/tooltest
```

These are argument lists split for readability, not multi-line PowerShell commands. Bake first, then tool test:
the tool test records A6/B5 against the exact print and refreshes STOP_B in that candidate's bake.json. Inspect its
15 checks and source-scene identity. Do not pass `--no-record` for final closeout. A bake may exit 0 with STOP_B
fired; the report is authoritative. No `--skip-checks`, `--skip-heldout` or quick modes in final proof.

The last full suite used `pytest.main(['-rfE', '-v', '-p', 'no:cacheprovider', '--basetemp=<fresh-temp>',
'glasses3d/tests/photoprint', 'glasses3d/tests/test_print_blender.py'])` from the bounded runner. Add
`glasses3d/tests/instruments` and the affected export/fitmesh tests when their behaviour is touched. Do not rerun
the whole suite repeatedly without a changed implementation, failure or unresolved concern.

## 7. Completion and handoff conditions

WP5 is done only when A4, production-density A5 and every eligible B3 row pass under the existing independent
protocol; all preserved invariants/reuse checks pass; the actual final GLB has fresh AR evidence and the required
owner verdict; and review, regression results and commits are recorded. Keep single-source registration limitations
explicit; completion does not claim pixel-verified/unseen-product accuracy. If a genuine external blocker remains,
record the exact failed checks, diagnosis and input needed. Do not replace proof with a waiver request or mark the
WP/goal complete because a run ended or the chat is long.

Only automatically hand off at reliably measured **80% of this conversation's current context window**. Cumulative
goal/account tokens, model-call totals and compaction are not occupancy measurements. If unavailable, continue
with progress notes. An explicit owner handoff request also applies. Before handoff update docs, review and commit
explicit paths; report the actual hash, working-tree state and any active process. Never claim all WP0–WP4 complete
as a range: WP2 is still partial. The ready-to-paste next-session prompt is [WP5_SESSION_PROMPT.md](WP5_SESSION_PROMPT.md).
