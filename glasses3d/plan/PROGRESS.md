# glasses3d progress

## 2026-10-21 — Task 21b: batch differences and inline finish reconciliation ($0; complete)

`record_difference(items=[...])` validates the whole batch before appending any item and names failing IDs,
including schema-invalid items. Existing one-item calls, delivered/current/run-bound evidence, retained refs,
and specific limitation reasons remain required. Confirming `finish(difference_updates=[...])` defaults omitted
evidence to the delivered first review, evaluates acceptance over staged updates, and commits them only after
accepted output and receipts succeed. Refusal commits no inline changes and leaves the review usable; renewal
carries the updated list. History stays append-only. Only the two authorized decision-index sentences and matching
tool descriptions changed; legacy text/schema/behavior and the 24,000-character cap are preserved.

For N items needing reconciliation after the final export, finish requests fall from N + 2 to 2 with inline
updates (or 3 with a separate batch). No paid-run savings were measured. The scripted M1 proof retains its
intentional refused confirmation, so its request count falls separately from 16 to an asserted 15.

Built and committed in the detached worktree at `ee1fb5fe5a0eae948328ffb202dc6ac77f155dfa`, based on `22f3980`.
Task 21's `exit 0 2026-10-21T14:55:07+03:00` preceded all main-checkout edits; cherry-picked onto `glasses3d`
as `74142c2f721a3ab83da1753c62c6b0c13954dc0f`. Focused task21/task21b plus finish/renewal regressions passed:
151 in the worktree / 44.25 s and 151 in main / 49.89 s. All 37 new cases failed as expected on an exact
`22f3980` archive / 14.13 s. The first detached run (145 passed / 4 failed) exposed SDK error-detail redaction
and two incorrect legacy test assumptions; fixes preserve SDK logging policy and historical legacy behavior.

Fresh `assembly_m1`, run once in main, passed in 193.875 s: open item → refused confirmation → accepted inline
limitation, 15 requests, 35 verified AR images, clean receipts, $0 actual spend. Proof:
`data/assembly/m1-proof-9dbf0e8b/proof.json`. Checks ran sequentially, offline, with BLAS 1, prescribed test
basetemp, measured memory and process receipts; no gate or deadline was loosened. Report:
`data/review_20261018/codex/task21b_report.md`; logs/status/arguments in `codex/task21b_checks/` beside it.
No paid calls, push, or edits to existing jobs, runs, deliveries or verdicts.

## 2026-10-21 — Task 21: decision profiles and host-kept differences ($0; complete)

Owner's words, verbatim:
- "it seems like a small issue to be sent on its own, or am i wrong here? id just rather sending you on bigger quests than changing a couple of words"
- "id like all of this work to be as quick as it can (not fast, but efficient!)"
- "go ahead ... please max utilize this machine for fastest overall work."

Implemented task 20 proposals #2–#10, the restructure and host-kept differences; #1 (part plan before construction)
remains excluded. The default `decision-20261021` main is 9,991 characters, down from 16,321; tool descriptions hold
mechanics. It connects mismatch/cause/evidence to improvement/regression, keep/restore and formal-best saving.
Worn lens colour has consistent priority; photo-background measurements cannot overrule it. Paging follows the
decision; the no-tool note is neutral. The 24,000 cap, owner hard rules and canonical cutoff sentence remain.
`legacy-20261021` preserves 28abe77 main/tool metadata, notes, renewal request and finish behaviour, including the
owner specialist's scoped description. CLI estimate/run selection binds the approval and run records.

`record_difference` keeps append-only per-run items with delivered current-run comparison evidence. First-finish
faults map to items. Open items and stale resolved evidence refuse acceptance; fixed needs a current comparison,
and limitation also needs a specific reason. Astra classifies items; no appearance score, reserve or forced spend
was added. Renewal carries the list and formal best; Task 17 scheduling is unchanged. Main-tool specialists have
private durable lists; ordinary temple specialists retain their separate state. Cutoff drafts still use formal best.

E2's final exit line (12:29:22+03:00, commit `da3cd0c`) preceded all Task 21 heavy checks. Its files were not edited
or staged. Functional pass, once: 6,030 passed / 2 skipped / 3 failed in 1,204.99 s, 8 workers after measuring
34.144 GiB free commit and considering E2's 14-worker deadline failure. Two test expectations needed profile-aware
updates; the third caught a paraphrased cutoff sentence, restored verbatim without weakening its assertion.
Affected checks passed: 104 prompt/money, 35 look, and 1 desktop renewal. Photoprint: 367 passed / 425.69 s;
serial timing: 29 passed / 285.77 s, unchanged deadlines. All heavy jobs ran sequentially with BLAS 1 and receipts.

Fresh `assembly_m1` passed in 173.219 s: open item → refused finish → supported limitation → accepted finish,
16 scripted requests, 35 verified AR images, $0 actual spend; proof `data/assembly/m1-proof-cc4c474f/proof.json`.
The earlier E2 assembly found a Task 21 proof assertion reading a paged field; it now verifies the full report's
hash before reading that field. Runtime finish checks were unchanged. Current/old-finish falsification: 17 current
passes versus 8 expected old failures and 9 evidence controls; legacy text hashes match 28abe77.

Implementation commit: `22f398021dc97b91c59cc3535e1f7f3dc18fb7f5`. The post-commit HEAD loader check passed
60 tests. Four baseline lens trials then ran on that unchanged commit in
`data/lens_lab/trials/baseline/20261021T101327Z-7518f1d3`, from 10:13:27Z to 11:49:01Z; exit 0,
4/4 valid completions, 117 tool calls and 34/34 successful look comparisons. Each trial cited worn images;
each final lens matched its marked best checkpoint and byte-identical corresponding export. Diagnostic
six-region Delta E76 (information only) and explicit regression observations before a best save:

| Trial | Six-region error | Regression named before keeping | Final = best |
|---|---:|---|---|
| 001 | 26.1286160645 | No; 0/3 best writes | Yes, c0007 |
| 002 | 27.0894188963 | Yes; 1/5, including final retain | Yes, c0009 |
| 003 | 23.3931564642 | No; 0/4; one separate inferred tradeoff | Yes, c0009 |
| 004 | 32.5873795212 | Yes; 1/3, including final retain | Yes, c0008 |

Trial 003's alternative-brightness wording is a qualified tradeoff inference, not an explicit regression.
Trials 003 and 004 each recovered from one unsupported Weight-socket edit: host rollback, then successful
agent correction, without outside intervention. All trial receipt/source/export/score integrity checks passed;
no original job or implementation file changed during the batch. These observations do not establish an
appearance improvement or owner acceptance. The lab's terminal finish does not exercise production finish,
renewal or the list; the scripted proof and focused tests cover that mechanism.

Report, exact export paths and full texts: `data/review_20261018/codex/task21_report.md` and
`task21_texts.json`. No paid call, push, original-job edit or owner appearance judgement; ignored evidence stays
out of Git. Claude prepares the on-face views; task 21 stops here for review.

## 2026-10-21 — Task 20: Astra instruction-thinking review ($0, read-only)

Reviewed the actual product-run instructions at task-18 HEAD `77aad88`, the built brief and 26-tool registry,
host notes/money footer, renewal request, three Oakley run traces and the valid lens-lab trials. Main text is
16,321 characters; 10,258 (62.9%) are appended tool guidance. Tool descriptions add 22,050 characters.
Verdict: Astra often sees and names the faults; the weaker step is choosing the next correction, retaining a
compared best checkpoint and justifying completion. The report separates the current task-17 finish repair
from the historical restart, and the cutoff draft's formal c0006 best from its later c0010 head.

Report: `data/review_20261018/codex/task20_report.md`; exact texts/counts and verification are adjacent
`task20_*` artifacts. Includes a proposed ~9,000-character outline and ten concrete unapplied text changes,
each with evidence, risk and $0-versus-paid validation limits. Task 18's sentence is reviewed separately;
the owner keep verdict committed during this review (`d28d0f3`) supersedes its earlier pending status and
metric-based revert recommendation. The on-face priority should be consistent across the remaining guidance.
No runtime wording, E2 file or original job/run/delivery/verdict changed; no tests, renders, paid calls or push.
Only this PROGRESS entry is a tracked change. Stop for coordinator review; no next task started.

## 2026-10-21 — Owner verdict on the task 18 lab lenses (on-face AR): task 18 kept

- Owner, verbatim, after trying them on himself in the AR preview: "new trial 4 is absolutely perfect, its the best
  result we got so far (even better than run4). trail 3 is slightly better than run4."
  - The trials are lab lenses, batch `baseline/20261021T061019Z-35e0a497`, trials 004 and 003. They are not products.
- **Task 18 is kept (`77aad88`).** Its worn-image colour judging produced the two best lenses by his eye, although the
  lab's white-background metric ranked them below his earlier picks (25.04 and 24.85, against 23.66–24.59).
- **Lesson.** The lab's six-region metric agrees with his eye on white-background views, but not on-face. Judge lens
  colour on-face, by him. The metric is information only, never a gate.
- Next: E2 (the lens-colour panel) and task 20 (instruction review) are running. A confirming paid Astra Oakley run on
  this code is his call.

## 2026-10-21 — Task 18 (worn images lead the colour review) committed `77aad88`; lab check worse; owner to decide

- What Astra sees now: `compare_look_to_photos` leads with the native worn front image and the closest worn angles,
  plus one sentence: "Judge lens colour on the worn, on-face AR images against the lens colour in the owner photos;
  use the white-background panels for shape and see-through."
- Checks: full suite 6,368 passed / 2 skips, AR 509 + 113, M1 passed.
- Lab, 4 trials on the new HEAD: errors 26.05 / 27.32 / 24.85 / 25.04, so **0 of 4 at or below 24.6**, against 3 of 4
  before. The mean is about the same: 25.81 against 25.64.
- Codex recommends reverting; the change has not been reverted. The four trials are in the lab gallery (8290) beside
  the earlier ones, and the contact sheet is `data/review_20261018/codex/task18_contact_sheet.png`.
- Task 20 (instruction-thinking review) started.

## 2026-10-21 — AR capture TEMP moved to a short private folder (Claude, $0)

- Owner, verbatim: "i want you to implement the fix, then send codex make astra look at the face worn ar images ...
  the build the user edits option in the dev ui (think about good ux). then perform a codex review about astra
  instructions from how we make it think perspective."
- **The fix.** `agent/tools/arview.py` `run_capture` used to set TEMP inside the job folder; Chromium's GPU cache
  then reached 254 of Windows' 260 characters. It now gives each capture tree a private `g3d-ar-*` folder under the
  system temp and records it in `temp-path.txt`.
  - Clean captures remove the folder.
  - Failed or timed-out captures keep it for diagnosis, unless it is empty.
- **Checks.** The capture, look and timeout tests pass (146 focused, plus the timing kill test), with new tests for
  the margin and the clean-up. `assembly_m1` passed with 35 verified AR images and left no temp folders.
- Next: task 18 (worn-image colour judging, owner's decision), E2 (the dev UI lens-colour panel) and task 20 (the
  instruction-thinking review).

## 2026-10-21 — Owner's eye on the 12 lab lenses (gallery, 3D + AR)

- Owner, verbatim: "all of the following have perfect lens (the same level as accepted run 4)": baseline trials
  002, 003 and 004, and pre14 trial 004.
- **His four picks are exactly the four lowest lab errors** (23.66–24.59). All eight others score 25.64 or more.
  The lab's six-region metric separates his choice perfectly in this cohort, n = 12, so it is information worth
  keeping. It is not a gate.
- By variant:
  - baseline, with task 14's lines: 3 of 4 perfect;
  - pre14, without them: 1 of 4;
  - regional: 0 of 4.
- So task 14's lines did not cause the grey, and **the regional lines are not to be ported**; the patch stays
  unapplied.
- Owner also wrote: "i think astra might needs to only base its opinion on the on face ar images since only they show
  the color really". Claude's answer is recorded in the conversation; no change has been made.

## 2026-10-21 — Lens lab overnight results ($0; Claude-reviewed)

- **Lab and ceiling** (`6c204bf`). 489 lens-material settings in our exporter and renderer: none beat run 4 on the
  front regional metric. Run 4 is near the renderer's ceiling, and the photo's broad purple sides and green centre
  were not reproduced.
- **First lab batches invalid.** Every lab look call failed (E_LOOK): Chromium cache paths above 260 characters under
  deep trial directories. L2d (`2066782`) fixed the lab with a short profile root and now marks zero-look trials
  invalid.
- **Repaired, matched batches**, 4 trials each, 117/117 look calls succeeded; six-region error mean / min / max:

  | Variant | Six-region error | Pattern signs |
  |---|---:|---:|
  | baseline | 25.6 / 23.7 / 30.1 | 0/4 |
  | regional | 27.2 / 25.6 / 28.6 | 2/4 |
  | pre14 | 27.2 / 24.6 / 28.5 | 0/4 |

  - Run 4 scores 23.5, and no trial beat it.
  - The ranges overlap. No variant is established as better, and task 14 is not shown to cause grey lenses.
- **Production risk found (read-only).** The longest AR-capture browser-cache path for a real job measures 254
  characters, a margin of only 6 below Windows' 260. A longer job or run id could make look captures fail. To be fixed
  before the next paid run (owner to be told).
- Owner's proposal, 2026-10-21, verbatim: "it seems the only thing it still gets wrong are the colors, isnt it possible
  to have in the edit page editable option for the user to just do?". Claude agreed and asked whether to build a
  lens-colour panel.

## 2026-10-20 - L1: isolated $0 lens lab, research, ceiling and one Codex smoke

Built only `glasses3d/lens_lab/`: private run-4 c0015/basis-04 copies, neutral
clear-glass lens reset, real tool CLI, material-only scope guards, frozen
photo-region scoring, systematic ceiling search and Codex trial runner. Geometry,
UVs, normals, transforms, other materials and scene settings are protected.
All 25 recorded original fixture/input files remained hash-identical. Output and
trial/job paths are confined to lab DATA, including resolved junctions; insufficient
pool memory refuses before creating output. No production code changed.

Research confirms that thickness textures survive the installed Blender exporter
and pinned Three 0.185.1. The convenience coating helper is uniform-only; direct
Blender nodes reach the existing texture capability. Thin-film reflection lacks
complementary transmitted-film colour, and the material is not a measured
multilayer spectral stack. Unknown studio illumination is another fidelity limit;
these are not experimentally isolated causes of the observed mismatch.

The predeclared 489-state search measured 488 cases in the primary run; one
Chromium-close/libuv infrastructure failure was retained (primary exit 1), then
that exact state passed once in a fresh serial replay. All 489 measured states
have labelled, hash-verified comparison sheets. Aggregate mean regional CIE76:
run 4 23.4904, best 0153 22.4065, best texture 0430 27.8805. The aggregate gain
came from the angle: front error worsened from 15.0393 to 17.0067. No searched
setting beat run 4 on the front metric. Weak green-centre/purple-side ordering
exists, but a faithful broad saturated photo match was not demonstrated. This
is a finite fixed-room search, not an impossibility proof or product recipe.

One Codex smoke used the real HEAD 4f0ec35 main instructions and brief, one lab
paragraph and six photos; inherited gpt-6-astra/ultra, no model override. It exited
0 after 1686.016 s: 39 lab calls, six material edits, eight photo comparisons.
The native session proves 44 rendered-image views before finish, with nine
byte-exact delivered image payloads. All 79 actual shell commands stayed within
scope. The CLI internally fell back from WebSockets to HTTPS. The initial checker
missed wrapped-exec image delivery and exited 1; its receipt remains unchanged.
A tested parser fix reconciled the same saved session and scored the same final
export (24.062 s), without another Codex run. Smoke error 26.4567; front 23.7027,
L* 69.27: muted green-grey, still lacking the photo's purple sides. The lab loop
works; this one sample establishes no quality improvement or repeatability.

Final complete lab suite: **126 passed in 193.55 s**, including nine native cases,
using its own `pytest.ini`; implementation hashes stayed unchanged. Review and
refusal regressions cover source sharing, scene scope, output containment,
low-memory startup and direct/wrapped native image delivery and finish ordering.
Task 17's outer exit 0 was recorded at 22:34:40 +03:00; L1 first launched native
work at 22:34:41. Heavy work was sequential, maximum 14 ceiling workers under
measured free-commit capacity, BLAS 1 and opt-out limited to owned processes.

Report: `data/lens_lab/codex/l1_report.md`; research: `data/lens_lab/RESEARCH.md`;
ceiling: `data/lens_lab/ceiling/final-assessment.md`; smoke and preserved initial
failure: `data/lens_lab/trials/l1-smoke/codex/reconciliation/receipt.json`;
final suite: `data/lens_lab/verification/final-native-20261020T204032Z.json`.
Original experiment code is retained under `verification/pre-hardening-source/`;
post-smoke guards did not change search/material/scoring calculations. No paid
pipeline API call, .env read, original job/run/delivery/verdict edit or push.
Codex usage is separate from the API ledger. Only this entry and the new package
are committed. Claude L2 owns subsequent variant trials; L1 stops here.

Primary external sources consulted **2026-10-20** (local pinned-source evidence
and exact citations are in RESEARCH.md and trial_runner/EXEC_IMAGE_TRANSPORT.md):
- https://www.oakley.com/en-us/lp/lens-technology
- https://www.edmundoptics.com/knowledge-center/application-notes/optics/optical-filter-orientation/
- https://www.edmundoptics.com/knowledge-center/application-notes/optics/optical-filters/
- https://belcour.github.io/blog/research/publication/2017/05/01/brdf-thin-film.html
- https://raw.githubusercontent.com/KhronosGroup/glTF/main/extensions/2.0/Khronos/KHR_materials_iridescence/README.md
- https://raw.githubusercontent.com/KhronosGroup/glTF/main/extensions/2.0/Khronos/KHR_materials_transmission/README.md
- https://raw.githubusercontent.com/KhronosGroup/glTF/main/extensions/2.0/Khronos/KHR_materials_specular/README.md
- https://raw.githubusercontent.com/KhronosGroup/glTF/main/extensions/2.0/Khronos/KHR_materials_ior/README.md
- https://raw.githubusercontent.com/KhronosGroup/glTF/main/extensions/2.0/Khronos/KHR_materials_volume/README.md
- https://threejs.org/docs/pages/MeshPhysicalMaterial.html
- https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/exec_events.rs
- https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs
- https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/config/src/config_toml.rs

## 2026-10-20 — Claude accepted tasks 16 (`a9cf6ef`) and 17 (`9b26c42`); owner asleep, $0 work only

- **Task 16 (read-only).** Requests grew because each look check now returns about 13 native images instead of 1:
  +78K tokens per request against run 4, from `83e3656`, `d39f954`, `25cc366` and `25dcd54`. Renewal (`ded8b77`)
  came after run 4.
  - Recommended next change, not applied: tighten blank padding on narrow native full-pair pages, about 6.5% fewer
    input tokens with no pixel loss.
  - Rejected as automatic: dropping old view comparisons after renewal, which loses evidence.
- **Task 17.** While a two-call finish review is pending, the soft renewal waits one request within the hard limits.
  - Claude reread the diff and reran the 18 new tests; all pass.
  - Full suite: one CLI subprocess timeout under load, which passed alone, as in task 14. M1 passed.
- **No paid run** has used tasks 14–17 together. The owner said "one change at a time"; the next paid run is his
  call.

## 2026-10-20 — Task 17: pending finish gets one request before soft renewal ($0)

Owner, verbatim: "Yes: have Codex find why requests grew 34% since run 4 and fix the restart-during-finish collision,
at $0, one change at a time."

`ConversationRenewal.check` now gives a pending two-call finish review one request in the same conversation
under soft pressure, only within the existing price-step, model-input, image and payload limits. The next
request resumes normal renewal if the review is refused, omitted or replaced by another first finish.
Each deferral is recorded in `renewals.json` and `conversation_renewal_deferred` events. Thresholds, money
guards, carried evidence, summary text, instructions and tool descriptions are unchanged.

The recorded candidate after request 51 was **270,844 tokens / 100 images / 75,136,300 bytes**: below the
272,000 price step by **1,156 tokens**, and below all other hard limits. Its pending review would now be
sent without the intervening renewal. The measurement replay verifies this decision and the following
request's renewal; it does not predict Astra's response, acceptance or dollar savings. Four evidence-source
files remained hash-identical.

Final focused checks: **75 passed**, 47.40 s, including real scripted SDK/finish calls under renewal
pressure. Against the old check, the new module gives **10 expected failures / 8 passes**. Required
functional pass: **5,876 passed / 2 skipped / 1 timeout**, 841.78 s. The unchanged CLI-output module
then passed **18/18** serially in 47.30 s; the timed-out stderr-flush case took 4.86 s under its original
60 s deadline. This is consistent with load sensitivity, not proof of its cause or a second clean full run.
Photoprint: **367 passed**, 398.81 s. Serial timing: **29 passed**, 314.40 s. Total across passes and
the scoped retry: **6,273 distinct passes / 2 skips**, with the initial timeout retained.

`assembly_m1` passed once in **182.422 s**, 13 scripted requests, 35 verified AR images, clean receipts
and **$0 actual spend** (`data/assembly/m1-proof-e2d4c1db/proof.json`). Heavy checks were sequential:
functional 14 workers, photoprint 4, timing serial, BLAS 1, measured free commit and no external QoS sweep.
Task 16 had no heavy children at preflight; its committed PROGRESS entry is preserved.

Report, replay, process receipts and final commit: `data/review_20261018/codex/task17_report.md`.
No original job/run/delivery/verdict edits, paid calls, `.env` reads, gate changes or push. The unrelated
untracked `glasses3d/lens_lab/` remains untouched. Next: Claude reviews Task 17; no next task started.

## 2026-10-20 — Task 16: request growth traced to native evidence ($0 audit)

Across the four saved Oakley runs, all **231 request totals** reconcile to provider receipts. Run 4 to
today: mean input **105,084 → 141,239 tokens (+34.4%)**; estimated images **26,595 → 104,687**,
non-image remainder **78,489 → 36,553**. Look-image exposure contributes **+78,484 tokens/request**,
offset by less other content. Astra made fewer ordinary look calls (**11 → 8**) and selected fewer photo
views (**40 → 15**); the dominant change is richer native pairs/details and side-light output per action.
A front-only comparison grew from one image / 730 image tokens to seven / about 15,337.

Renewal began with `ded8b77` on October 18, after run 4; run 4's peak was also below the later
217,600 trigger. Current renewal already selects the latest complete group per photo. Today's requests
contain **zero within-request duplicate image hashes**. Older checkpoint pages remain where no newer
comparison covers that photo. A fixed-trace replay shows renewal pruning reduces image history; it is
not the sole cause of growth. Task 17 owns the finish/renewal collision.

Proposal only: trimming native tiles' blank padding, preserving source pixels and labels, projects
**485,607 tokens total / 9,162 per request (6.5%)** saved on today's unchanged request trace. Removing
repeated photo columns offers more but separates photos from renders. Neither proposal was applied.
Report: `data/review_20261018/codex/task16_report.md`; per-request composition, all-tool breakdowns,
per-call average/max tables, historical commit/file:line evidence and reproducible projections accompany it.

The report uses receipt-increment estimates for all **221 consumed tool outputs**, with framing and
masked-reasoning limits explicit; four unconsumed terminal outputs have no invented text count.
The image rule in `agent/look_cost.py` was checked against official documentation on **2026-10-20**:
https://developers.openai.com/api/docs/guides/images-vision (32-pixel patches, high-detail limit, 1.2 multiplier).
All **1,372 protected original files** remain hash-identical; independent measurement/history reviews pass.
Only this PROGRESS entry is committed. No runtime code, original job/run/delivery/verdict changes,
paid calls, `.env` reads, pools, Blender, suites or push. Next: Claude reviews task 16; stop here.

## 2026-10-20 — Task 15: new Oakley lens and renewal costs analysed ($0)

The task-14 test run `oakley-oo9208-v2-aaaa5ffe` / `20261020T163834-3ec617` stopped at **$20.178866**
after 53 requests and three renewals. Its draft GLB is `1dbf3535…`. Same-camera comparison with unchanged
accepted models: common-mask lens L* **photo 63.60 / run 4 58.80 / max-effort rerun 42.58 / new draft 58.56**.
Brightness moved close to run 4; the new lens is still grey-green and lacks the photo's purple sides.
No owner acceptance or causal proof of task 14's effect is claimed.

All eight immediate look results contained the front colour-gap line. Astra's renewal summaries cited the
numbers and metallic's transmission effect. The delivered metallic **.55** is the earlier formally marked
best `c0006` (restored `c0004`); its later head `c0010` used **.72**, but the formal best was never updated
before the cutoff. The draft followed the recorded best, not the last material trial.

Requests **46 / 52 / 53** cost **$1.580473 / $1.530671 / $1.519410**. Requests 46 and 53 rebuilt the cache
after renewals; 52 was renewal 3's summary, preserving the cached prefix while ingesting new finish images.
Zero-cache requests were exactly the initial opening and three renewal openings (**1 / 32 / 46 / 53**).
No unintended cache-loss defect was demonstrated; changing finish/renewal scheduling is an unimplemented
optimization to investigate, not an established bug fix.

Report: `data/review_20261018/codex/task15_report.md`; owner figure: `task15_front_comparison.png`.
Three local captures exited 0; run-4/rerun beauty images match task 13 byte-for-byte. All **336 protected
original files** remained hash-identical. Analysis only: no runtime code change, suite run, paid call,
original job/run/delivery/verdict write, or push. Only this PROGRESS entry is committed; the report records
its commit hash. Next: Claude reviews task 15; no next coordinator task started.

## 2026-10-20 — Paid Astra Oakley run started (task 14 test)

- Owner, verbatim: "Yes, start the Astra Oakley run at max effort with a $20 settled cutoff." The estimate shown
  before was $15.70 (60 requests, effort-blind); the accepted rerun cost $19.67.
- Fresh job `oakley-oo9208-v2-aaaa5ffe`, made by `data/assembly/oakley-regression-20261020/make_job.py`. Its owner
  inputs, view/side labels, description and specs are identical to `oakley-oo9208-v2-911a2b0e`, and its brief is
  byte-identical.
- Run `20261020T163834-3ec617`, started 16:39:39 UTC, at HEAD `75c8cce` with task 14's text (instructions
  `3211ccde…`).

## 2026-10-20 — Claude accepted task 14 (`897672b`)

- Report: `data/review_20261018/codex/task14_report.md`.
- What Astra now sees: the immediate result of `compare_look_to_photos` lists one factual lens line per photo. Replay
  of the accepted rerun's lens shows 20.6 / 29.9 / 27.5 / 25.9 / 33.5 / 1.8 L* darker than photos 1–6; run 4's front
  line shows 4.5 darker. There is no score, gate or wording beyond sign and units.
- The metallic sentence is in the main instructions, now 16,168 / 24,000 characters. UNDERSTAND and EDIT text is
  unchanged.
- Checks: photoprint and timing passed, and M1 passed. The functional pass had 2 timeouts under load (the dev UI
  `/api/config` poll and the CLI stderr-flush subprocess); both modules passed 57/57 serially, unchanged. That is not
  a second clean full run, and it is recorded as such.

## 2026-10-20 — Task 14: lens colour gap visible immediately, factual metallic guidance ($0)

Owner, verbatim: "Yes, build both Astra changes at $0: show the colour/brightness gap up front in the
colour check, and add the one factual metallic sentence."

`compare_look_to_photos` now places one `lens_colour_gap` line per compared photo in immediate text.
It uses the existing masked RGB and L* medians, reports the raw-median lightness difference, preserves
fractional RGB values and reports empty masks as unavailable. The pager retains every line, including
inside finish reviews; all existing full-report fields remain unchanged. No ΔE00, score, ranking, recipe
or appearance gate was added. Main instructions contain the exact approved metallic sentence, with the
pinned Three 0.185.1 shader cited in its contract test. Main text is 16,168 / 24,000 characters;
UNDERSTAND and EDIT remain byte-identical at 19,062 characters each.

The three unchanged Oakley deliveries were replayed through the current tool on fresh copies, with
their frozen final bases. Front-photo gaps now visible immediately: run 4 **4.5 L* darker**, afternoon
**22.9 darker**, rerun **20.6 darker**. All 18 colour records and AR photo-camera image hashes match
Task 13. No existing first-page field moved in any replay; aggregate text is 6,297 / 6,155 / 6,157
characters against the unchanged 12,000 limit. All **1,082 original files** remain hash-identical after
the checks. The report records the fixed-envelope limit for very large synthetic photo/image sets.

New instruction and pager regressions fail against the old code; focused checks pass **146 / 1 skip**.
Required functional pass: **5,857 passed / 2 skipped / 2 process timeouts**, 900.33 s. The unchanged UI
launcher and CLI-output modules then pass **57/57** serially, 42.53 s, with their original deadlines;
the failures' causes are not proved. Photo-print **367 passed**, 389.47 s; serial timing **29 passed**,
272.08 s. Across the passes and scoped retry: **6,255 distinct passes / 2 skips**, preserving the
initial timeouts rather than claiming a clean second full run. M1 passed once in **156.110 s**,
13 scripted requests, 35 verified AR images, **$0 actual spend** (`data/assembly/m1-proof-6c9e6893/proof.json`).
Heavy checks were sequential, BLAS 1, functional 14 workers / photo-print 4 / timing serial, with no
external process-QoS sweep and no budget or timeout change.

Main instruction SHA-256: `3211ccdece4665c7ced0b4bba7d8536e32cd9e13c3d36ec78df4691695bf0d59`.
Production default tool-registry/behavior SHA-256: `082ad6b804c21baa1c02adf4d0f9a114ca4eb16e4817cb1d7b894d223c1a180c`.
Report, exact text, replay links, hashes, process receipts and final commit:
`data/review_20261018/codex/task14_report.md`. Next: Claude's review of Task 14; no next task started.
No original job/run/delivery/verdict edits, paid calls, `.env` reads or push.

## 2026-10-20 — Claude accepted task 13 (`90d48c6`)

- Report: `data/review_20261018/codex/task13_report.md`; comparison image `task13_front_comparison.png`, sent to the
  owner.
- Verdict: Astra's own material choice. Its look tool measured the darkness correctly: the L* gap to the photo was
  4.5 for run 4, 20.6 for the rerun and 22.9 for the afternoon run. Those numbers were paged out of the immediate tool
  text, and no run read them.
- The code change is a helper-text clarification only, with no behaviour change. Full suite and M1 passed. The one
  functional failure came from the launcher's external QoS sweep, was reproduced, and passed 17/17 without it.
- Codex disclosed an accidental broad repository search at the end of the task. Claude checked the task log: no key
  strings and no `.env` contents appear, and its `.env` mentions are code and rule text only.

## 2026-10-20 — Task 13: Astra's dark metallic lens choices diagnosed at $0

Compared accepted run 4, the rejected afternoon run and the accepted max-effort rerun, preserving the owner's
rerun lens-colour caveat. The later runs deliberately retained dark metallic materials after trying alternatives;
the record does not explain the exact final .92 metallic/.10 base choice. Run 4 also tried a dark metallic lens,
then returned to metallic 0. The unchanged shader reduces transmitted base-colour contribution by `(1 - metallic)`;
the look tool reports medians without ranking candidates. No new rule prescribing dark metallic lenses was found.

All three encountered the same white-coating-tint override. Clarified the existing helper contract in its description
and output: `tint` replaces Principled Base Color RGB, preserves both alpha inputs and other optics, and refuses
linked Base Color. Material writes, recipes, gates and the fixed reflection default are unchanged. Additional
material-selection guidance is proposal-only in the report.

Current six-view replays on copies: front AR L* run 4 **58.89**, afternoon **40.30**, rerun **42.79**, versus photos
about **63.4**. Before/after numbers match exactly and all **18** photo-camera AR images are byte-identical.
The labelled front-photo/three-lens comparison is the primary visual output. All **97** original input hashes match.

Proof: old helper/output-description regressions fail and new versions pass, with equal native/exported material
values; **137** focused tests pass. Functional run: **5,850 passed, 2 skipped, 1 failed** (the launcher's external
QoS sweep can contaminate the deliberately unmanaged process-test child). The unchanged whole process module
passes **17/17** without that sweep; no process-test assertion, process runtime or timeout changed. Photo-print **367** and serial
timing **29** pass: **6,247 distinct passing tests**, with the initial failure retained in the report.
Offline M1 passes in **164.59 s**, 13 scripted requests, 35 verified AR images, **$0 actual spend**
(`data/assembly/m1-proof-836cd343/proof.json`). Heavy jobs were sequential, BLAS 1, memory-bounded, with EcoQoS opt-out.

Report: `data/review_20261018/codex/task13_report.md`; image: `task13_front_comparison.png` beside it.
The report also records a discarded accidental broad read-only search during final verification.
No original job, run, delivery or verdict changed; no paid call or push.

## 2026-10-20 — M13 closed: back to Astra

Owner, verbatim: "lets end this test and go back to focusing on astra". The Claude test is closed and no Claude
delivery is accepted: C5 was rejected at $19.65, and run 2 was stopped at $21.25. The `claude_test/` code stays
committed and inactive; Astra's code, instructions and requests were never changed (C3 byte check). Lessons that may
matter for Astra:
- factual host notes after scene-changing scripts raised Opus's comparisons from 3 to 16;
- the cache simulator (`claude_test/cache_sim.py`) reproduced real Anthropic bills to the cent.

Neither is applied to Astra without the owner's word. Open Astra items, unchanged: task 13 (why post-M11 Oakley runs
chose a dark metallic lens; $0, stopped earlier by the owner), Prada (no accepted assembled delivery) and FT1119
(never run assembled).

## 2026-10-20 — M13 second paid Claude run: stopped by the owner, $21.25

The run had F1 (no server clearing) and T1/T1b (tool-use and paged-result host notes). Owner OK: "Raise the cutoff
for the second paid Claude run to $35 settled; everything else the same settings."

**Run.** Job copy `claude_test/work/c6-20261020/jobs/oakley-oo9208-v2-911a2b0e`, run `20261020T130337-bb18b4`.
- The owner stopped it after Claude showed him a mid-run snapshot (request-80 comparison: green centre fading to
  violet, white glow behind the top lettering, flatter lens lower edge, pale lens). His words: "ok i think we can stop
  the run".
- Result: **stopped**, 85 requests in 101.7 min, **$21.250687 settled** of the $35 cutoff, with no unknown charges.
  One renewal happened at request 75.
- A draft delivery was published: `…-9a58745f…`, GLB `cffb9ea2…`. 396 uploads were made and 396 deleted.

**Tool use.**
- 84 calls; 62% were dedicated tools.
- 32 scripts against 16 look comparisons; 0 `compare_to_photos`.
- 29 photo measurements in 5 calls.
- 6 `read_report` calls: 3 of the 24 paged results were later read.
- 46 change-count notes and 23 paged notes were sent.
- The longest no-comparison streak was 5.

**Cost.** The fix worked: requests at about 340–750K context cost $0.09–0.20 each, against $3.40 under the C5
defect. The renewal's first request cost $1.69.

## 2026-10-20 — M13 C5: the paid Claude Oakley run, completed and awaiting the owner's eye

Owner OK, verbatim: "Yes, start the paid Claude Opus 5.5 Oakley run at max effort with a $20 settled cutoff."

**Run.** Private job copy `claude_test/work/c5-20261020/jobs/oakley-oo9208-v2-911a2b0e` (owner inputs only, hashes
equal to the accepted rerun's), run `20261020T102312-ca458f`, `claude-opus-5-5`, effort max.
- Result: **completed**, accepted finish. 45 requests in 45.7 min, **$19.6487 settled**.
- Delivery `…-daabc1f6…`, GLB `fac153f7…`.
- 173 uploads made and 173 deleted at teardown. No retries, no unknown charges.
- Tools: 21 `execute_blender_code`, 9 `read_image`, 3 `compare_look_to_photos`, 1 each of `fit_cameras`,
  `render_views`, `export_glb`, `arview_capture` and `check_lens_seating`, 3 `finish`. It made no
  `compare_to_photos` and no `temple_stage` call.
- Lens: violet base [0.40, 0.18, 0.45], metallic 0, transmission 1, IOR 2.33, iridescence 300–380 nm.
- Comparison sheets: `claude_test/work/c5-20261020/compare/`.

**Cost defect (Claude's design, D6).** Requests 1–40 cost $7.64.
- From request 41 the conversation stayed above the 350K context-editing trigger even after clearing. Each request
  then cleared a slightly different set of old results, which invalidated the cache, so the whole prefix (about
  400–420K tokens) was rewritten at the 1-hour write rate.
- Requests 41 and 43–45 cost $1.92, $3.40, $3.38 and $3.22: about $12 of the $19.65 in four requests.
- Fix before any further Claude run: clearing must leave the context well below the trigger, or happen once in a
  batch, or be replaced by the existing renewal. Prove the fix on the scripted wire with a cost assertion.

**Owner verdict, 2026-10-20, verbatim: "its shit no need to wait for my verdict."** The Claude delivery is rejected.
No owner acceptance is recorded, and the accepted references (run 4 and the rerun) are unchanged.

**Claude's own visual read**, information only; the owner decides:
- The lens is in the violet family, like run 4's rather than the rerun's dark one. But the photo's green centre is
  mostly missing, and a bright white glow sits behind the top-centre OAKLEY lettering.
- The temples are a straight bar with a boxy front block, plainer than the photo's slim angled arm. Astra's were closer.

## 2026-10-20 — M13 steps C1–C4 done ($0); paid run C5 awaits the owner

- **Built** (all in `glasses3d/claude_test/`, no shared file changed):
  - money guard A/A2, `7d5bbf1`;
  - images B, `bdbebf4`;
  - facts R, `3377ab0`;
  - design C0, `8464dc4`;
  - runner C2, `eb252c8`;
  - upload deletion, Stop-race fix and livecheck C2b/C2c, `52bfa80`;
  - estimate C4a, `e833c12`;
  - Astra regression C3, `fc19e0d`.
- **The Stop race was a real defect:** a Stop during a streaming reply leaked a transport refusal and misreported the
  status. It is fixed, and the claude_test suite then passed 20 runs in a row (Codex) plus 5 more (Claude). That
  suite has 849 tests.
- **The scripted proof** (real SDK, guarded transport, fake wire, real Blender on a private Oakley copy) passed in
  18 min, and all 131 uploads were deleted at teardown.
- **C3** passed on the M13 head: full main suite 5,849 / 2 skips, plus 367 photoprint and 29 timing; `assembly_m1`
  passed; Astra's opening and tool follow-up requests are byte-identical to `700b673`.
- **C4 live check** (free only; Messages refused by construction):
  - The first attempt got HTTP 400, because the key was not scoped to a workspace.
  - The owner then made a key in workspace "automation": "you got it. done under workspace "automation"".
  - The rerun made 19/19 HTTP 200 calls: 6 uploads, 6 metadata reads, 1 count and 6 deletions. $0.
  - The opening request (instructions, 26 tools, six whole native photos) counts 38,448 tokens, vs Astra's 25,150.
- **Owner-facing estimate** for Opus 5.5 at max effort, from the rerun's 70-request profile scaled ×1.53. These are
  scenarios, not bounds:

  | Output (incl. thinking) vs Astra | Estimate |
  |---|---:|
  | 0.5× | $10.91 |
  | 1× | $11.90 |
  | 2× | $13.87 |
  | 5× | $19.80 |
  | 10× | $29.69 |

- **Not yet tested:** file-ID images inside Messages, and four request fields the count endpoint does not take
  (`max_tokens`, `service_tier`, `inference_geo`, `stream`). The paid run's first request tests them; a 400 there is
  refused before any model work.

## 2026-10-20 — M13: model set to Opus 5.5 at max effort; task B landed

Owner, verbatim: "for now were set on opus 5.5 with maximum possible effort" (`claude-opus-5-5`, effort `max`).
His Console shows Opus 5.5 limits of 5.0M input tokens (cache reads excluded), 1.0M output tokens and 5.0K requests,
read as per minute. That is enough for any single request, so no credit purchase is needed. Task B (native-pixel tiles,
upload cache, request checks) was committed in `bdbebf4`; Claude reran its 145 tests. Tasks A (money + venv), R (facts)
and C0 (design) are running in parallel.

## 2026-10-20 — M13 planned: one Oakley run on the Claude API

Owner: "i want to do an oakley run with claude api", then "ok lets do this", and "give me the claude test building plan
(you plan and workflow with codex)". Plan: `plan/M13_CLAUDE_TEST.md` (ASSEMBLY M13). Same job as the accepted rerun
(`oakley-oo9208-v2-911a2b0e`), same tools; a separate runner on the Messages API; settled-only money exactly as for
Astra; native-pixel images (tiles ≤ 2,000 px and ≤ 4,784 visual tokens, Files API); server context editing with the
existing renewal as backstop. Model choice at the paid step: `claude-opus-5-5` ($4/$20) or `claude-fable-5-1`
($10/$50, Claude's recommendation). Next action: Codex task C1 ($0). Branch `glasses3d` was pushed to GitHub on the
owner's word the same day ("please commit and push the branch"; 304 commits to `700b673`, scanned first: no media,
binaries or keys).

2026-10-20 — E1 ($0): dev UI product runs prepare/check max effort, including unlaunched retries; paid edit sends stay high; config/page/docs updated; 259 backend tests and 20 browser tests passed, M1 passed once (163.03 s, 13 scripted requests, $0); baseline fails the saved-high regression as required. Report: `data/m12/codex/e1_report.md`; restart port 8260 on the committed code, then stop.

## 2026-10-20 — Owner decision: max effort is the standing setting for product runs

Owner, verbatim: "Yes: max effort is the standing setting for product runs from now on. Record it in AGENTS.md and
PROGRESS with my words." Asked after Claude's check of the API settings (only reasoning effort clearly changes results;
max is the model's top level; no reply in the last three Oakley runs came near the 16,384-token output cap, largest
8,346). Evidence behind it: both accepted assembled Oakleys (run 4, $10.61; rerun, $19.67) used max; the rejected
2026-10-19 run used high. Replaces the 2026-10-16 "effort=high" term for product runs; edit sends keep high unless the
owner says otherwise. AGENTS.md updated. The dev UI still hard-codes high for "Start a paid run"
(`dev_ui/launcher.py:138, 148`, `dev_ui/api.py:53`); switching it to max is the follow-up.

## 2026-10-20 — Owner accepts the max-effort Oakley rerun

Owner verdict (ledger record `b8413eb4…`, bound to delivery `20261019T172111-465930-1e20f85b…`, GLB `ba6216a4…`):
"The max-effort Oakley rerun looks right, accept it." This is the first owner-accepted assembled delivery made from a
job holding only the owner's inputs, on the M11 code with settled-only money, no finish forecast and camera-ambiguity
reporting ($19.670825 settled, 70 requests, max effort). It joins Oakley run 4 as a regression reference. The
afternoon high-effort run (`oakley-oo9208-v2-878ee407`, $12.28) keeps its "changes" verdict.

## 2026-10-20 — Claude accepted N1 ($0)

N1 (`411e352`) reviewed by Claude: report `data/m12/codex/n1_report.md`. Diff read: shading-only writes keep
every unchanged normal fan's stored codes; sharp splits only on selected-face edges whose endpoint fans lie inside
the influence; planning uses verified intent; automatic sources use free float normals, and only meshes carrying
the new owner marker take the new export branch. Unmarked exports are unchanged: Oakley run 4 and M10 re-export
byte-identically. Prada `n1-c2` records checked directly. I1 B + I2 B: `per_item_geometry`, both items exact,
zero native and export overlaps across 82 comparisons. I1 alone: `single_glb_bytes`, equal. Claude's rerun:
**316 passed** (normal composition 63/63, no xfails; edge-radius/review; M12 choices), plus the timing-pass heartbeat
test alone. The seven unrelated test edits made under the one-Blender rule were reverted before the commit. The owner
UI on port 8260 still runs pre-N1 code until it is restarted.

## 2026-10-20 — N1: exact local normals ($0)

Verified, disjoint `repair_shading` picks on one part now compose exactly. Stored/decoded native normals and
exported NORMAL rows outside each influence stay bit-identical; other kinds, overlap and unproved previews still
refuse. Fresh Prada dual/single immutable deliveries pass; Oakley run 4 and M10 re-exports retain their exact bytes.

Checks: **770** focused; **63** normal cases in five runs; **6,239** full-suite passes / **2** Windows symlink skips;
**20** browser tests; **125** real UI assertions; M1; **509** AR unit / **113** QA, type check and build. Earlier
heartbeat-read and launcher failures remain in the report; unchanged serial timing passed after recorder removal,
and M1 passed with a guarded Windows launcher. No test or gate weakened; actual provider requests **0**, spend **$0**.
Report: `data/m12/codex/n1_report.md`. Sources unchanged; no push, root-app or `ar/` change.

## 2026-10-19 — Paid Oakley rerun at max effort: delivered, awaiting the owner's eye

Owner OK: "Yes, start the Oakley rerun at max effort: estimate $15.69, $20 settled cutoff." Fresh job
`oakley-oo9208-v2-911a2b0e` (owner inputs and view/side labels only), run `20261019T172111-465930` at HEAD `48ea9e6`
(the exact-local-normals session paused its edits and started no heavy job for the run): spent **$19.670825 settled**,
$0 held, 70 requests all settled, 92 min, two conversation renewals, finish accepted by Astra, delivered (GLB
`ba6216a4…`), runner exit 0. Labelled comparison: `data/assembly/oakley-regression-20261019/run4_vs_afternoon_vs_rerun.jpg`.

## 2026-10-19 — Claude accepted task 12 ($0)

Task 12 (`1238a21`) reviewed by Claude: report `data/review_20261018/codex/task12_report.md`. Historical temple names now
come from the producing builder's frozen lineage or the run's authenticated `src.tar.gz`, never today's builder; the
exact M11 M10 plan B rebinds, previews and applies byte-identical to the retained export (`7d2d5b27…`); census 40/54
temple objects verified, 14 already-reshaped run-4 objects correctly refused. No existing assertion removed; reran new
and related tests: 120 passed, 11 xfailed and 1 xpassed (M12's documented exact-normals cases).

## 2026-10-19 — Task 12: authenticated historical temple aliases ($0)

Historical names now come from frozen producer lineage or authenticated saved run source, never today's
builder. New temple writes bind the exact spec, side, vertex count, ordered raw polygons, facets and corners,
including UV splits and checked-triangle repairs. Legacy readers verify run-config/implementation/archive/source
SHA-256 bindings and require exact connectivity; ambiguous, corrupt or unmatched evidence still refuses names.
Coordinates always come from the current mesh. Copied edits, separate checkpoint runtimes and private previews
carry the source evidence. Guarded health helpers authenticate it before arming; losing names does not drop a
valid hashed spec's declared thickness minimum. No producer geometry or hard gate changed.

Census: all **50 jobs, 287 blends (194 distinct hashes), 50 deliveries and 64 source archives** inspected;
all **242 byte-backed checkpoint-index rows** accounted for. **27 blends / 54 temple objects / seven specs**,
all Prada: current construction matches **2/54** exactly; authenticated lineage verifies **40/54** (38 recovered,
two retained). Fourteen run-4 objects remain refused because their topology was already changed beyond the
archived constructor. Six historical delivery checkpoint hashes (Prada and Oakley runs 1/2/3) have no retained
raw bytes; their saved GLBs cannot establish raw facet lineage. All **501 original census files unchanged**.
Task 8's 77-row manifest did include all 12 assembled Prada stage specs; it omitted historical raw-mesh alias
compatibility, not the specs. The complete path/count and delivery tables are in the report.

Exact M11 M10 frame-side **B = 3 mm** rebound, previewed and applied on copies. Prepare **46.953 s**, three-option
preview **300.266 s**, apply **156.328 s**, all exit 0. Both preview and final immutable delivery are byte-identical
to M11: **`7d2d5b2791a38dfd975a6460481ceef8fe7405e4c1b61ddbacbeb3b909c76207`**; all 34 primitives, geometry,
normals, UVs and materials match. All **205 runtime files** stayed fixed, and **1,003 original/copied source files**
plus retained M11 artifacts stayed unchanged. The first attempt's preview refusal is preserved: a separate
baseline-function probe proved private preview copies dropped all six source chains; carrying them fixes it
without changing the physical checks. Evidence: `data/t12m10b/proof-summary.json` and
`data/review_20261018/codex/task12_m10/report.md`.

Focused checks: **144** lineage/map/preview/lifecycle, **41** archive/guarded-worker, **15** declared-constraint,
**54** existing owner-geometry and **6** native UV/tessellation tests passed. Actual baseline alias/preview
controls fail; baseline owner modules fail all 15 constraint regressions. M1 passed in **162.703 s**:
13 scripted requests, 26 transport files, 12 verified image blobs, 35 AR images, clean receipts, actual **$0**;
`data/assembly/m1-proof-1c898cfc/proof.json`. Heavy checks ran separately, BLAS 1, detached logs and EcoQoS opt-out.
Report: `data/review_20261018/codex/task12_report.md`. No paid call, push, original job/run/checkpoint/delivery/verdict
edit, root-app or `ar/` change. No new product verdict. Stop for coordinator review.

## 2026-10-19 — Claude accepted task 11 ($0)

Task 11 (`2340e95`, follow-ups `8f9d848`, `3193ca3`) reviewed by Claude: report
`data/review_20261018/codex/task11_report.md`. Findings accepted: the Oakley lens and temples were Astra's own
authoring choices; the removed finish forecast ($13.19 projected vs $8.02 left) plausibly ended the run early; the
side-camera flip follows the model, not the matte change or code; run 4 used max effort, the new run high. Money tests
read: the settled cutoff, unknown-charge stop and approval binding are still asserted; only reservation admission and
output shrinking are gone, as the owner decided. Claude's reproducer of the offline-isolation failure (13 failed) now
passes (285 passed) under pytest 9.0.3.

## 2026-10-19 — Task 11 follow-up: approved guidance and repaired test isolation ($0)

Applied the report's exact lens and temple paragraphs to Astra's construction instructions; contracts verify
wording, placement and exclusion from scoped owner edits. The all-specialist prompt is 15,940 characters,
below the unchanged 24,000 cap. Existing capabilities and owner-edit/no-refit rules remain intact.

**Isolation failure reproduced and fixed after Claude supplied the exact nine-file order.** At clean `8f9d848`,
his `-n 2 --dist loadfile` command failed **13 / passed 270 in 13.84 s**. Pytest 9.1.1 creates a second
`tests/agent` Package when a root test file interposes between agent file arguments; conftest fixtures stay
attached to the first object. Collection metadata proves **241 later agent cases lack `offline_isolation`**,
including its credential scrub, temporary paid-run lock root and network guards. This happens before test
execution, not through a later environment restore; `agent/loop.py:935` correctly refuses the missing flag.
The defect predates task 11: its parent `fb7f5d5` has byte-identical conftests, package initializers, pytest
configuration and dependency lock. Saved hashes and a minimal reproducer using that fixture establish this.

Pinned and installed **pytest 9.0.3** with its verified PyPI wheel SHA-256; the runtime guard and fixture are
unchanged. `test_pytest_isolation.py` checks the real fixture across one/two root-file interpositions, every
protection and restoration outside agent tests: **2 failed on 9.1.1 (1.92 s), 2 passed on 9.0.3 (1.44 s)**.
Claude's identical nine-file command now passes **283/283 in 31.59 s**, with the same basetemp/order and no
inherited `G3D_OFFLINE`. The affected nine modules plus offline/credential/approval/infrastructure/regression
checks pass **323/323 serially in 47.79 s**; the original two-file command passes **37/37 in 28.47 s**.
These runs overlap. Full collection finds **6,031 cases in 12.27 s**; `pip check` and installed-lock contracts pass.
The earlier serial 37-pass and grouped-agent 848-pass runs did not exercise the failing interleaved order;
their three initial 261-character Windows-path failures remain separate historical harness evidence.

Primary sources read 2026-10-19: [pytest #14635](https://github.com/pytest-dev/pytest/issues/14635),
[9.1.x backport #14968](https://github.com/pytest-dev/pytest/pull/14968),
[unreleased](https://docs.pytest.org/en/latest/changelog.html) and
[released changelogs](https://docs.pytest.org/en/stable/changelog.html),
[9.0.3 wheel metadata](https://pypi.org/pypi/pytest/9.0.3/json).
The upstream fix is merged but absent from released 9.1.1. Tests README records the pin and regression.

Earlier verification for `8f9d848` with pytest 9.1.1 (not rerun for this dependency repair):
functional **5,619 passed / 2 skipped / 11 xfailed / 1 non-strict XPASS**, 965.98 s, exit 0.
Photoprint: **367 passed**, 431.16 s. Serial timing: **29 passed**, 298.94 s. Total **6,015 distinct
Python passes** across 6,029 cases. No failing case remains in these checks; M12's authored normal-composition
case XPASSed under its existing mark, which does not establish deterministic exactness. Its mark, assertions
and runtime were untouched. AR unit/QA/check/build and dev-UI browser commands all exited 0; their launch/exit
receipts are retained, but the initial local Node wrapper lost child console output, so no case counts are claimed.

M1 ran once after the suites and passed in **181.250 s** (process 185.938 s): 13 scripted requests, 26 transport
files, 12 verified image blobs, 35 AR images, clean receipts, actual **$0**; `data/assembly/m1-proof-8ea16947/proof.json`.
Process inventories confirmed no competing pool-heavy job; checks ran sequentially, BLAS 1, memory-bounded
14/4 workers and idle serial timing, with original budgets. The final audit verifies **395 original files unchanged**.
No paid call, push, original job/run/delivery/verdict change or accepted-asset change. Report, exact final guidance,
commands, logs, receipts and the explicit-path commit are in `data/review_20261018/codex/task11_report.md` Follow-up.
Stop for coordinator review; the reproduced isolation defect is resolved.

## 2026-10-19 — M12-D3 final verification and closing documents ($0)

M12 implementation and required $0 verification are complete with the retained limits below. Baseline `2340e95`; no D3 commit. D1 (`b81f923`, `0da06ba`) completed all six real-browser
scenarios at $0: `data/m12/proofs/d1b/proof.json`. The follow-up verified the early same-part shading refusal
and reauthenticated the immutable byte-exact single-pick delivery; it did not turn the original native dual-pick
failure into a successful composition. Six scripted runs retained 31 inference requests plus 31 token counts,
simulated $0.325, actual provider requests 0 and actual $0. Follow-up added no SDK run or native apply.

C2 follow-up 3 (`1a79b26`) restored the normal writer after the real Prada preview regression and refuses separate
same-part shading picks before copy/worker/publication. The owner can put both marks in one item or apply one now
and send the other as a new edit. Exact local normal writes remain future work. D3 marked the intermittently
passing authored zero-overlap case `xfail(strict=False)` as the brief required, citing task 11's four changed
corners and 4.3153763e-5 drift; the eleven other future goals remain strict xfails. No assertion, tolerance or
normal writer changed. D3's authored case XPASS is not proof of deterministic exactness.

| D3 check | Result |
|---|---|
| Functional, including dev UI, parallel | 5,614 passed / 2 skipped / 11 xfailed / 1 non-strict xpassed / 4 errors; 823.83 s, exit 1. |
| Unchanged camera module, alone | 8 passed, 108.47 s, exit 0; clears all four documented WinError 6 fixture errors. Four rerun passes duplicate the first pass: 5,618 distinct functional passes. |
| Photoprint | 367 passed, 381.35 s, exit 0. Four pytest workers; at most two inner bake workers per fixture. |
| Serial timing, idle machine | 29 passed, 260.41 s, exit 0; original budgets/deadlines unchanged. |
| Dev UI Chromium | 20 passed, 23.923 s, exit 0. |
| arview unit / QA / type check / build | 509 unit (20.559 s) / 113 QA (39.104 s) passed; check/build exit 0 (receipt intervals 1 s / 3 s); production capture 35/35 images. |
| Assembly M1 | Passed, 152.266 s, exit 0; 13 scripted requests, 26 transport receipts, 12 image blobs, 35 AR images, clean receipts, actual $0. `data/assembly/m1-proof-79a0b97e/proof.json`. |
| Final README/instruction contracts | 87 passed, 7.09 s, exit 0; supplementary checks overlap the functional pass. |

Final Python accounting: 6,014 distinct passes / 2 skips / 11 xfails / 1 non-strict XPASS across 6,028 collected cases; four duplicate camera-retry passes are not added. The first local launcher attempt ran no tests and exited 3 because its
temporary-directory parent was absent; only the ignored launcher script was repaired. All commands, times,
PIDs, exit codes, logs and retained failures are in `data/m12/codex/m12_D3_report.md`. Checks ran detached with
BLAS 1, EcoQoS opt-out and memory-bounded pools; pool-heavy work was serialized. D1 proofs were not rerun.

B follow-up 2 (`fb7f5d5`) shows only settled spend, the owner's cutoff and money left. M11 task 11 (`2340e95`),
not M12, makes settled-only the default and sole new-run/edit mode and removes Astra's finish-cost projections.
Docs retain the owner's dated money words, typed dev UI authorization, final proof scope and remaining limits.
Still open: exact local normals; the pre-M12 task-8 historical temple-alias regression, queued as M11 task 12;
Prada UNDERSTAND sealing latency (1,147.484 s, about 19 minutes, in C2's two-item proof).
No paid call, real approval, push, original-asset change or owner product acceptance. The owner tries the UI himself and decides on any paid run.

## 2026-10-19 — Task 11: Oakley regression diagnosis, camera alternatives and settled-only money ($0)

The matched model/matte/source controls reproduce the side flips with the new model under both matte
policies and both source versions; they do not establish the photographs' physical orientation. The
fitter now reports unresolved side/top alternatives with native overlays and frozen hashes. Existing
eligible camera candidates precede refused candidates; off-axis alignment includes eye offsets, and
failed partial refits preserve separately hashed attempt images. Prada's six installed cameras and the
new Oakley's three final-option cameras remain identical. The top automatic control clears the label
refusal but has lower raw IoU; this is not an appearance or orientation acceptance.

The archived-source and decision audit separates Astra's grey/metallic lens and temple choices from
code effects. A misleading finish forecast plausibly encouraged the early finish; the owner's decision
below replaces the proposed forecast repair. All Astra-facing forecasts/advice are removed. New runs
and edit rounds use only settled spend against the owner's cutoff; old records remain readable and
reconcilable. Provider limits, unknown-charge stops and approval binding remain. The saved-Oakley
scripted proof reaches the cutoff, accepts second finish below it, and contains no projection text.
Lens/temple judgement text is proposed in the report only, not applied.

Validation: full functional **5,583 passed / 32 failed / 2 skipped / 11 xfailed** (913.82 s).
Thirty failures were old money-policy fixtures; affected-file checks reached 215 passes with two fixture
errors, then both corrected files passed 26/26. The unchanged CLI timeout case passed 4/4 serially.
One M12 authored-normal overlap failure remains in protected files; its native runtime matches the
pre-task baseline. No assertion or threshold was relaxed, and this is not a green full suite.
Photoprint **367 passed** (396.50 s); serial timing **29 passed** (273.89 s); M1 **passed** (158.891 s),
13 scripted requests and 35 verified AR images. The explicit-path commit and full evidence are recorded in
`data/review_20261018/codex/task11_report.md`, with the controlled refit tables, two labelled overlays,
source/input hashes, exact money text and all suite/M1 results. Suspended launcher 39648 was cleaned up
after its Prada child exited successfully; detached controls completed. No paid call, push, original
job/run/delivery/verdict change, accepted-asset change or WP5/WP6 method change. Task 11 ends for
coordinator review; no next task or paid run is authorized here.

### Decisions — 2026-10-19

Owner, verbatim:

> "we are keeping on encountering this issue. i dont understand what the problem is, why is our algo reserving
> money??????? if i say 20$ cutoff than thats it why do you always layer on top of this??"

## 2026-10-19 — Paid Oakley regression run: delivered, owner wants changes; task 10 accepted

Owner OK: "Yes, start the Oakley run: estimate $15.71, $20 settled cutoff, effort high." Fresh job
`oakley-oo9208-v2-878ee407` (Claude created it from the 2026-10-13 job's photos, verbatim description, specs and
view/side labels, without that job's agent-era per-photo `matte_mode`), run `20261019T111142-19467a`: spent
**$12.28 settled**, $0 held, 48 requests, finish accepted by Astra, delivered (GLB `f34113e4…`), one conversation
renewal at 266,616 input tokens. Owner verdict (ledger): "not only the lens looks too dark the temples look much worse
than they were in accepted 4. what has changed?" Claude's first facts: both side cameras fitted on the other side of
the profile (reference-03 yaw -107.7 → -62.3, reference-05 65.9 → 105.2), top IoU 0.589 → 0.487, automatic mattes
instead of the declared open_frame, lens grey and 0.56-0.62 metallic (run 4 violet, 0), a new money-projection line
after every tool (39 vs 0). Diagnosis is Codex task 11 (`data/review_20261018/briefs/task11_oakley_regression.md`).
Task 10 (`93f9735`) accepted by Claude: report read, no assertion removed, focused tests rerun 73 passed + 1 load flake
that passes alone.

## 2026-10-19 — Task 10: UTF-8 runner output and truthful exit status ($0)

Replayed the saved completed Oakley run: CLI output to cp1252 failed on U+2212 at character 17901
after delivery, then the broad exception handler falsely reported refusal/exit 2. CLI streams and detached
children now use UTF-8; reporting/flush failures retain the real run status, including through interpreter
shutdown. The SDK's separate error line was a handled renewal before request 41; all 48 requests settled.
Final focused checks: **131 passed in 60.62 s**; M1 **passed in 163.688 s**, 13 scripted requests,
35 AR images, clean receipts, actual **$0**. Saved-input replay now exits 0 with exact Unicode text;
old-code failures and harness/concurrent-edit retries are recorded in `data/review_20261018/codex/task10_report.md`,
which also records the commit. Existing text writers were already UTF-8. No paid call, push, gate change or
unrelated-session edit. Next: Claude reviews task 10; no next coordinator task started.

## Current state — 2026-10-19

The assembled job, specialist, seeing, delivery and owner-edit paths work; M6–M10 implementations are present.
WP0/1/3/3b are built and tested, WP2 A–F and specialist stages are assembled, and WP7's main/specialist/edit
instructions and contracts are implemented. M11 tasks 1–9 have landed and Claude accepted them (task 9:
`81ef7b2`); M11 is complete. Its final baseline is **5,236 Python passes / 2 Windows symlink skips**, arview
**512 unit / 113 QA passes**, type check/build and M1 passing. Cleanup verification is recorded separately.
M12's local dev UI, capture intake, per-item choices and full-item fallback are built. D1's six real-browser
scenarios passed (`b81f923`, `0da06ba`) under the same-part shading refusal, with immutable single-pick exactness
and the fallback-to-new-edit loop verified. M12 status: complete with documented limits. Final D3 suite: 6,014 distinct Python passes / 2 skips / 11 xfails / 1 non-strict XPASS; 20 UI browser / 509 arview unit / 113 QA passes; type check, build and M1 passed. The four parallel camera setup errors cleared in isolation.

**Next action:** The owner tries the UI himself and decides on any paid run; M5 paid validation remains his call.
No build or proof authorizes spending.
Standing dev UI terms are **max effort for product runs (owner, 2026-10-20; edit sends high), the owner's typed
settled-only cutoff, cap = stop**, shared by parent and
specialist; no reservation cutoff or output shrinking. No next request starts at or above that amount; the
in-flight response may cross it, and unknown charges stop spending. His typed amount and paid-send button press
authorize that one run or edit send, recorded as a single-use approval with the button's words. Coding agents
never start paid calls under build tasks and still need his explicit OK and an estimate for anything they would
run. New job-run CLI requests default to a $20 settled cutoff and max effort; settled-only is the sole new-run/edit
money mode. Historical reservation-mode approvals and ledgers remain readable, not new-run authority.
M11 task 11 (`2340e95`) committed that sole money mode and removed Astra's finish-cost projections. The UI and
Astra's money status show only settled spend, cutoff and money left; no estimates, held amounts, projected costs
or finish-soon figures. Local CLI planning estimates remain available. No total WP8 budget is approved.

### Accepted assets and preserved scope

- **WP5 FT1119, 2026-10-08:** "Observed-slope join + Poisson is the best, set it and mark wp5 as done".
  Default `4304c1d`; PNG SHA `ac4280e9d0a2b85696c1d64f7c9508f56d81f2fc2857f7925af58fd844ddd5b4`,
  tool-test GLB SHA `09a421be248f43b7dd888d48e10c1ffb8fa3297931743b1ef5599df0e6dac9eb`.
  Sources/verdict: `data/wp5/quilt_joins_20261008/slope/`, `data/wp5/slope_default_20261008/` and
  `data/ledger/owner_verdicts.jsonl`; see [WP5 closeout](WP5_HANDOFF.md).
- **WP6 v3 run 8, 2026-10-13:** "you know what. im satisfied with wp6, lets wrap this".
  `facet-astra-008`, c0015/revision 25, $8.993582 settled; `data/wp6v2/viewer/new_astra_run8.glb`,
  SHA `d8e6b88aa8dac1afa79fca265678e36fe2248e928063547519646bc3f0b405be`.
  This closes WP6 by eye, not every proof or the complete assembled Prada product.
- **Assembled Oakley run 4, 2026-10-15:** "as far as i can see the model is perfect".
  Job `oakley-oo9208-v2-3e057bab`, run `20261015T062433-662a82`, c0015,
  GLB SHA `981314580a6b174546ebac0e42509b03a2e83044e4ae888aa729539277d198fb`;
  asset-bound verdict `verdict-6cb0c4050a3b47b8bb09c0a7c419ecaa`. Keep this regression asset unchanged.
- **M10 combined copy, 2026-10-18:** "looks good!"; "we were testing the new tools and their optional effects,
  our goal wasnt to fix the model". Tool-test verdict only, GLB SHA
  `f44492b9054f3c19de2a38f05f180820d9b065bc0675c9e1b8fad70df79fe534`,
  `data/assembly/m10/combined/owner-verdict-combined.json`.
- FT1123 crystal, Oakley OO9208 v2 and INVU IA22403B v2 retain their 2026-10-06 asset-bound re-acceptances in
  `data/ledger/owner_verdicts.jsonl`. Prada has **no accepted assembled product**; earlier Studio praise and the
  WP6/M10 scoped verdicts do not establish one.

### Open work and retained limits

- **M12 retained limits:** exact local normal writes and ordered same-part shading composition remain future
  work. C2 follow-up 3 refuses two such picked items early; one item containing both marks or a later edit from
  the single-pick delivery remains available. Eleven strict expected failures preserve the future normal goals;
  the authored zero-overlap case is non-strict because task 11 observed failure where C2 observed a pass.
  D1 proves that refusal and byte-exact single apply, not successful separate same-part dual composition.
  UNDERSTAND sealing is slow on Prada (1,147.484 s for C2's two-item proof). The pre-M12 historical temple-alias
  regression introduced by M11 task 8 remains open, queued as M11 task 12; D1 did not repair it.
- M5/WP8's smoke and accepted Oakley are complete; assembled Prada, assembled FT1119 and three unseen products
  remain. **M9.4** (desktop c0016 beside run-4 c0015 and photos for the owner's judgment) is not recorded complete.
  The review-page authored-size choice is parked until the hard technical work is finished.
- **WP4:** seating is a report, not a finish gate. Latest P2 **71/71**, P3 **34/40**, P4 **109/112**, P5 **31/32**;
  ten historical quality failures, report-only/pending/N/A rows and photo-derived hide ranges remain. The earlier
  metal 0.425 < 0.44, INVU 0.4489 < 0.45, FOLD cascades, narrow-eyewire knife edge and `CORRECTION_AIM_MM`
  evidence remains; no threshold was loosened. The check still runs for crystal/translucent frames.
- **WP5:** the selected observed-slope + Poisson variant has no full independent A4/A5/B3 quality proof or full
  regression. Prior regular-Poisson failures remain A4 all four; A5 8 fails / 7 passes; B3 six of seven. Byte-identical
  accepted-print replay does not convert those quality failures into passes. RGB-only neutral-pigment/glare ambiguity
  remains. Print tools are open to Astra; WP5 is closed by owner acceptance and is not automatically reopened.
- **WP6 v3 at closure:** the hinge-side face was faint (2.2 degrees off the logo), and Astra ended "stuck" on the
  arm-end hook in photos 02/B2. Later M5-fix-8 supplied visible-angle comparisons/declarations; M6–M10 supplied the
  planned "more edits" flow. M9/M11 supplied native evidence and fresh-conversation renewal for the image pile;
  old history-rewriting proposals are superseded. These tools do not imply a newly accepted Prada product or reopen
  WP6. The stopped hinge-face experiment remains local evidence.
- **Cameras:** CC-12 remains partly open (full posed scenes rebuild outside the small LRU); CC-13 cropped-photo
  objective and CC-17 selection/native-metric inconsistencies remain. Travel sampling does not recover mechanical
  hinge stops or arm-arm constraints. Task 6's report records the scope and uncertainty assumptions.

M11 entries below preserve the repair/verification record. Older entries and their owner words, hashes, failures
and spend history are [archived verbatim with a heading index](archive/20261019/PROGRESS_2026-10-05_to_2026-10-17.md).
The [2026-10-19 archive index](archive/20261019/README.md) maps moved documents and removed proof scripts to their
recovery sources. Historical resume steps and paid approvals are not standing instructions.

## 2026-10-19 — M12 dev UI built through A/B/C; D2 documents prepared ($0)

Implemented the owner's local UI: photo/description/spec intake with explicit view and wearer's side, typed
cutoff and one-use paid-start approval, detached run status/spend/Stop, orbit results viewer and AR link, labelled
point/line/closed-shape captures with 3D anchors, per-item interpretation/options, local Apply my picks and a
separately approved single-item full-edit fallback. Captures stay model evidence, never product photos; UNDERSTAND
admits them unresolved without invented coverage. Existing support, physical, export and print refusals remain.
New owner instructions: [dev UI README](../dev_ui/README.md); contracts: [M12 §7](M12_DEV_UI.md#7-contracts-frozen-for-the-parallel-build-changes-go-through-the-coordinator).

Committed tasks at D2 entry (`76639f4`): A `248d2a1`; B `3ea7218` + follow-up `a1acafd`; C1 `a4600de`;
C2 `76639f4`; C3 `e0873f0` + capture-discovery follow-up `275b37c`. Reports:
`data/m12/codex/m12_A_report.md`, `m12_B_report.md`, `m12_C1_report.md`, `m12_C2_report.md`, `m12_C3_report.md`.

| Recorded task check | Result |
|---|---|
| A backend and detached server lifecycle | 249 passed, 11.18 s. |
| B final viewer/money/dialog follow-up | 16 browser tests passed, 22.672 s, mock API. |
| C1 focused intake/transport/map/money regressions | 541 passed, 70.56 s; later text/transport check 104 passed, 8.80 s. |
| C2 options/apply regressions | 459 passed, 50.69 s; after later repairs, 141 sealing/apply passed, 16.89 s, and 120 apply regressions passed, 6.27 s. 144 new cases total. |
| C3 initial focused fallback tests | 121 passed, 33.07 s; follow-up with C1 regressions 244 passed, 36.01 s. |
| C3 scripted SDK/Blender fallback proof | Passed, 113.672 s; finished result, reported outside-region changes and cutoff draft; actual $0. |

Batches overlap; do not add them into a full-suite total. C2's native mixed UNDERSTAND took 407.407 s and the
single-pick apply retry 139.093 s (162.125 s phase): exact preview GLB bytes, scope/physical/export/print/AR passed,
but publication refused the then-unsupported application schema and the private destination was cleaned up.
The temple option hit the existing folded-chart refusal; neither a successful two-item native apply nor an
immutable applied-picks delivery is claimed from that report. C3's concurrent follow-up owns the publication
hook and a fresh capture round after fallback. D1 verifies those seams and the full browser path; no D1 pass is
claimed here. No owner product verdict, paid call or new campaign approval is implied.

D2 updates standing money terms to the owner's 2026-10-19 request, instruction caps to 24,000 (C2 main 16,430,
UNDERSTAND/EDIT 19,455; C3 main plus fallback 17,770), CLI `job edit-item`, capture/options/fallback documentation,
the owner UI guide and test routing. Dated history remains unchanged. D2's README CLI and instruction contracts
passed **87 tests in 8.19 s**; checks and exact changed text are in `data/m12/codex/m12_D2_report.md`.
No D2 commit. Next: D1 proof and final suite,
then the owner tries the UI; M5 paid validation remains his decision.

## 2026-10-19 — Cleanup applied; returned for Claude review ($0)

Applied `data/review_20261018/briefs/cleanup2_apply.md` after creating local recovery tag
`pre-cleanup-20261019` at `7ac91651ec5fad65a232e05f8a77afaef407b51c`. Removed the approved dead helpers,
retired context pruning/stubs, unsupported planarizer and 29 historical proof scripts. Approval compatibility,
append-only renewal, live print groups/regions and crystal rendering remain. Twelve historical documents and
4,527 older PROGRESS lines are archived verbatim under `plan/archive/20261019/`; active references were updated.

Stopped the 22 identity-checked processes in the two superseded task-8 trees. Moved and verified
**51.008439 GB** of selected untracked output into the sibling
`lenses-recovery/glasses3d-cleanup-20261019/` archive, with a manifest and durable move journal; nothing was
hard-deleted. All 116 protected `.env` files retain their metadata and original locations; their contents were
never read. All 37 initially refused renames succeeded on one retry; no blocked scratch subtree remains. The archive audit
checks every moved count/byte total and reparse target without traversing links. See the report for exact paths,
keeps, hashes, source-recovery commands, commits and digest limits; same-disk moves do not free disk space.

Verification accounts for **5,220 distinct Python passes / 2 Windows symlink skips**: final functional
**4,821 passed / 3 camera failures / 2 skips** at `-n14`, then all three ordered camera cases **passed** at
`-n0` (143.41 s), photoprint **367 passed**, serial timing **29 passed**. The parallel camera failure is the
pre-existing Windows `WinError 6` pool crash plus two missing-basis cascades; unchanged cases pass in isolation.
The first functional attempt also exposed a source-edit fingerprint race and a runner QoS sweep error, both
cleared by the frozen-source rerun. No assertion or budget was relaxed. The 16 retired Python cases were A02
eight, A03 six and H04 two. Arview **509 unit / 113 QA** pass; check/build pass (three retired V02 viewer cases).
M1 passes (13 requests, 35 AR images, $0); WP6 T5 passes **49/49** checks with zero stubs or network attempts.
The accepted FT1119 replay remains byte-identical at SHA `ac4280e9d0a2b85696c1d64f7c9508f56d81f2fc2857f7925af58fd844ddd5b4`.
Full logs, the initial failures and final receipts are retained. No paid call, push, `ar/` or root-app change.

## 2026-10-18 — Claude accepted M11 task 9; M11 complete ($0)

Task 9 (`81ef7b2`) reviewed by Claude: report `data/review_20261018/codex/task9_report.md`. First fully green suite:
5,236 passed, 2 Windows symlink skips (functional, photoprint and serial timing passes), arview 512 + QA 113, check and
build, M1. Removed assertions read: replaced by self-contained or stricter ones (SDK import now required, Blender
skip removed, the live-`main` pricing comparison dropped for the frozen hash); the photo_v1 golden was recaptured with
both differences recorded (environment drift; task 8's zero-dither normal pass), limits unchanged. Claude added
`!.env.example` to the root `.gitignore` so the root app's tracked example file is not newly ignored.

## 2026-10-18 — ASSEMBLY M11 task 9: suite health, arview and current documents ($0)

Implemented `data/review_20261018/briefs/task9_tests_docs.md`. Seeing/checkpoint/camera tests establish their own
state; wall-clock gates have a separate serial `timing` pass with unchanged budgets. SDK import failures now fail
collection. Pricing pins an immutable source revision. Offline proofs use unspendable fixture approvals; M1 creates
jobs inside its proof workspace. Fixture temporaries clean up; the basis proof fully builds/verifies a candidate
before publication, retaining/restoring the old directory if the Windows rename sequence fails. Ignore-policy and
failure-injection tests cover these paths. Requirement files lock the installed Windows CPython 3.12 dependency
closure with exact wheel hashes; no versions were upgraded. Official hash source, read 2026-10-18:
`https://pypi.org/pypi/<package>/<version>/json`; a clean-resolution hash-checked dry run resolved 98 packages.

The photo_v1 golden was recaptured with preserved evidence and unchanged tolerance. Replaying the original
`62b25e4` renderer now differs from the original golden by 33–199 native / 30–232 clay pixels (at most six levels),
with 0–9 normal pixels: environment drift. Comparing current code with that recapture separately exposed task 8's
intentional zero-dither numeric-normal change (39,547–65,393 mostly one-level pixels). The new golden is the first
capture of reviewed task-8 baseline `308f4bf`; its independent repeat differs by at most 11 pixels. Original and both
old-code captures remain intact. Exact input hashes, Blender build and per-view differences are in
`data/review_20261018/codex/task9_golden.json` and `task9_golden_task8.json`.

Crystal twins preserve transmission/thickness maps and opacity; shadow reports reflect the pass result. Absolute
resource URIs are missing without a fetch, QA routes match origin and path, and malformed-GLB agreement cases use
the stricter answer. Viewer assets/fixtures are an independent SHA-pinned cache sourced from immutable Git objects;
routine live `ar/` edits cannot change them. Documents now distinguish current implementation, dated plans and owner
acceptance. README parser contracts cover job/lab/capture/serve commands; `job edit` exposes `--settled-only`.

Verification: root functional **4,838 passed / 2 Windows symlink skips in 1077.65 s** (`-n 8 --dist loadfile`),
photoprint **369 passed in 408.69 s** (`-n 4 --dist loadfile`), serial timing **29 passed in 266.62 s**. All **5,238**
collected cases are accounted for: **5,236 passed, 2 environment skips**, no slow-test exclusions. Initial full run
had one real-lab variant-render failure; saved output/timestamps indicate its unchanged 37.5 s deadline expired
under load. Its serial proof renders both variants and passes the original image/fingerprint checks. Earlier
failures remain recorded. Arview **512 unit / 113 QA passed**, check/build exit 0; production capture **35/35** images.
M1 passed in **169.344 s**, **13** scripted requests/tools, **26** transport files, **12** image blobs, **35** AR images,
clean receipts; its job is under `data/assembly/m1-proof-3893162f/jobs/`. Per-item changes, limits, evidence and commit
hash: `data/review_20261018/codex/task9_report.md`. No paid call, push, `ar/`/root-app change or data/binary commit;
actual spend **$0**. Task 9 awaits Claude's review, not a new product acceptance.

## 2026-10-18 — Claude accepted M11 task 8 ($0)

Task 8 (`4c5bf5c`) reviewed by Claude: report `data/review_20261018/codex/task8_report.md`. All 77 accepted temple
geometries (fixtures, run 8 revisions, assembled stage) compare exactly equal before/after; T5 rehearsal 49/49, M1 and
`assembly_m5fix8` passed. Changed assertions in existing tests read: visible-angle refusals still fire (now only for
bound dependencies, message "connected layout"), the M5 proof's stale control now changes a bound reading, the
off-reading warning compares the built corner; none weakened. Reran new and touched tests: 593 passed, 1 skipped
(Windows symlink). Codex left its superseded `t8geometry2` experiment paused (about 1.1 GB, resume/stop receipt in
`task8_experiment_final_pause.json`). Task 9 brief extended by Claude: `test_wp2_fit_cameras.py` order dependency and
a serial pass for timing tests that fail under full-suite load.

## 2026-10-18 — ASSEMBLY M11 task 8: temple geometry, fitting and stage tools ($0)

Completed `data/review_20261018/briefs/task8_temple_tools.md`. Builder arrangements use a 0.001 mm feature floor,
scale-aware ears and strictly improving internal diagonals. Float32-unsafe inner ears are repaired only through
coplanar interior fans; real kinks still refuse. Exact-minimum gaps survive coordinate rounding. Native stored and
exported tessellation is checked with delivery's rule. All 96 eligible review-fuzz layouts and 12 steep-edge cases
have zero bad triangles. Helpers preserve rounded vertices, check all corners and refuse split/holed/open bodies.

Fitting warnings use actual fitted positions; visible-angle choices support typed/solved local corners while
protecting unrelated strong evidence. Choices bind their connected geometry/readings and effective fit dependencies;
unrelated changes survive, bound changes refuse. Receipts/instructions reflect this. Zero taper has a finite local
join; unchanged outline edges carry no corners. Variant normals, decal warnings/refusals, shared-mesh undo and
UV/opening consistency are repaired. Bounded crease reports retain exact candidate metrics even when table summaries
force pagination; the strict native proof exposed and verifies this correction. Sign-certain, manifold, thickness,
declared-shape and strict-spec gates remain.

Exact-Git accepted-geometry census: 77 rows, 48 supported builds, 19 same refusals, 10 historically unsafe subminimum-roll
intermediate refusals. Accepted run8 r0025 maximum sampled/all-vertex surface difference is 0.0000634117634 mm; selected
assembled r0006 is 0.000107402787 mm, thickness unchanged. All assembled intermediates max 0.00131354698 mm. The selected
face-only crease-range endpoint change of 0.819051676 degrees is disclosed with unchanged all-edge extrema and tiny mean
change; no new geometry acceptance gate is invented. Four real accepted GLB temple primitives have zero bad triangles.

Full non-print: **4,822 passed, 2 skipped, 13 failed in 895.94 s** (14 workers, loadfile). The exact-floor snap defect is
fixed without changing the old test; post-fix serial coverage is **405 passed / 1 slow deselected in 129.14 s**. All 13 failures
were rerun alone: 11 pass; the camera-refit test's existing order dependency passes in the isolated full camera module
(3 passed in 140.18s (0:02:20)); the pre-existing photo_v1 golden mismatch remains 199 pixels vs 114.24 allowed. No threshold or
golden changed. Full photoprint: **372 passed in 387.19 s** (4 workers). Final fresh T5: **49/49 checks**, 24 requests / 23 tools,
prepare 33.2 s / run 104.0 s. Final M1: **166.860 s**, 13 requests/tools, 26 receipts, 12 image blobs, 35 AR images.
M5 passes with independent selected angle **11.729716499015 degrees**, certain-sign refusal,
bound-reading invalidation and unrelated-reading retention. Its archived camera-provenance wording was re-frozen
through production code on disposable copies; all 385 original and 407 compatibility-source files remain unchanged.

Final 77-row replay and accepted native export pass on frozen sources. Existing full-scene etch/hallmark warnings
are disclosed and reproduced with the old writer; temple bodies are safe. Per-finding lines, old-failing tests,
exact changed Astra text, process receipts, accepted diffs and commit hash are in
`data/review_20261018/codex/task8_report.md`. One superseded diagnostic experiment is preserved paused under the
no-kill rule, with resume receipt. No paid call, push, root-app/`ar/` edit, source-asset change or data/binary commit.
WP6 method unchanged; no new owner/Claude visual acceptance claimed. Actual spend **$0**.

## 2026-10-18 — Claude accepted M11 task 7 ($0)

Task 7 (`df9fe23`) reviewed by Claude: report `data/review_20261018/codex/task7_report.md`. The owner-accepted FT1119
`print.png` replays byte-identical (`ac4280e9…`), so the stop rule did not apply; WP4 grades unchanged. Changed
assertions in existing tests read: the level-compare tests are stricter (neutral pixels stay in unless independently
identified as reflections), the matte test follows the SEAT-3 cap recognition, part names match exactly; none
weakened. Reran new and touched tests: 70 + 237 passed (including the slow accepted-print replay), no skips.

## 2026-10-18 — ASSEMBLY M11 task 7: print and seating generality ($0)

Completed `data/review_20261018/briefs/task7_print_seating.md`. Photo projection, admission, occlusion and calibration
support now follow each photo's frozen temple opening; absent openings keep authored geometry. Print diagnostics
report inward/inconsistent winding without repairing it. Mirror UV copies and sparse elongated charts have checked
fallbacks; one-piece fronts retain separate sides, multi-material UV post-checks inspect every primitive, and bake
options replay in Blender. Empty gain supports, packed-key overflow and unavailable quilt donors refuse explicitly.

Colour/highlight/calibration rules use measured pigment distributions and explicit reflection evidence. Synthetic
donor shares improve from **14.44% to 100% for blonde** and **5.09% to 100% for honey**; havana, blue, green, grey,
white and crystal stay at 100% before painted highlights. Independent colour metrics use the reference-photo palette
and circular hue excess; thresholds remain unchanged. RGB-only neutral-pigment/glare ambiguity remains disclosed.
Product/colourway constants moved to test fixtures. Missing FT1119 evidence skips with named paths, or fails when
explicitly required. The new slow pytest replays the actual production bake and independent audit.

Final accepted-output replay passed: **329.62 s bake / 330.560 s replay**, two inner workers, exact owner PNG SHA
`ac4280e9d0a2b85696c1d64f7c9508f56d81f2fc2857f7925af58fd844ddd5b4`. Classes/source/tier/layout and gain arrays match;
all sources remain unchanged. Registration differs only by pre-task removal of historical product-specific prose;
the test explicitly migrates that expected suffix and compares every registration datum. No runtime change followed
the successful replay. Historical WP5 A4/A5/B3 failures and the unrun full independent quality proof remain recorded.

Both crystal lenses retain separate groove Booleans; untouched clear supports no longer refuse, n-gon/fan caps are
recognized, host/Blender translucency rules agree, explicit inspection construction wins, and post-write hide errors
roll back. Seating stays a report. Fresh WP4 grades before/after are identical: **P2 71/71; P3 34/40; P4 109/112;
P5 31/32**, with the same report-only/owner-pending/N/A rows and ten historical quality failures. All 262 records and
four source scenes remain unchanged except one timing value. Native two-lens, cap, material and rollback tests pass.

Full non-print suite: **4,648 passed, 2 skipped, 6 failed in 902.75 s**, 14 workers, `--dist loadfile`. Four timing or
timeout failures pass alone. The known photo_v1 golden mismatch remains **199 pixels vs 114.24 allowed**. The sixth
failure was an old default-matte expectation: newly recognized coarse browline lenses correctly select open-frame
mattes. Both real lens/frame-only variants now retain every empty-input refusal check; **35 tests pass in 4.24 s**.
Full photoprint: **370 passed, 2 failed in 390.27 s**, four workers, including the passing production pin. The two
old grey-as-glare assertions now require bad washed prints to fail and preserve explicit reflection-mask checks;
the entire corrected module passes **20 tests in 65.69 s**. Fresh assembly M1 passed **173.828 s**, 13 requests/tools,
26 transport receipts, 12 image blobs and 35 AR images; `data/assembly/m1-proof-f4eebbbf/proof.json`.

Complete changes and tests were re-read, with independent reviews of the metric and integration assertions.
Per-finding locations, failing-old controls, donor table, full run/retry receipts, limitations and commit hash are in
`data/review_20261018/codex/task7_report.md`. No acceptance threshold or saved hash changed. Logged detached checks
used BLAS1 and memory-bounded pools without heavy overlap. No paid call, push, root-app/`ar/` change, owner-input edit
or data/binary commit. No new visual acceptance claimed; actual spend **$0**.

## 2026-10-18 — Claude accepted M11 task 6 ($0)

Task 6 (`91487b7`) reviewed by Claude: report `data/review_20261018/codex/task6_report.md`; changed assertions in
existing tests read, all follow the new behaviour (14-worker ceiling, both floor candidates, provenance-based opening
uncertainty, a photo-bounded pose kept instead of the contradicted input), none weakened. Reran new and touched tests:
318 Python passed, arview 509 passed, `npm run check` clean. Open: CC-12/13/17 (report "Limits and skips"); the ARR-1
asymmetry warning fires above 0.1 mm.

## 2026-10-18 — ASSEMBLY M11 task 6: arm openings, cameras and comparisons ($0)

Completed the task-6 implementation from `data/review_20261018/briefs/task6_arms_cameras.md`. Opening fits now use
a rearward geometric domain, reject front/lens crossings along sampled hinge travel, favour the input on ties,
and separate native model visibility from photo support. A photo-bounded hidden opening can survive without an
observed claim. Thin-arm front/back/top controls cover the old impossible poses. Comparisons, crop bounds and
Blender views use each frozen opening; AR reports requested/applied deltas, actual moved meshes and frame-tagged
arm candidates. Shared worn-view clipping warns when it can hide authored arm-length asymmetry.

Lab and main readings use per-photo/per-side opening provenance: disclosed 0.3-degree observed and 10-degree
unmeasured assumptions, with shared source-fit uncertainty for transported photos. Automatic floor alternatives
now include rimless construction and actual clear-lens interiors; comparisons retain raw and lens-optional IoU.
Transparent photos composite onto white. Near-orthographic camera conversion uses a reported distant-pinhole
approximation. Historical product constants/helpers moved to test fixtures. Chunked float64 matte evaluation
preserves historical pixels; the 40 MP default-matte check used **520,271,396 traced peak bytes, 5.641 s**. One
memory-bounded pool shares photo/matte work, at most 14 workers with one BLAS thread per spawned worker.

Fresh fits on unchanged owner input copies: **Prada 987.969 s; accepted Oakley run 4 286.360 s**, all 12 photos
usable; all 16 owner job/model/photo file hashes remain unchanged. Prada stays below the unchanged 1800-second
tool deadline. Its front openings are now unobserved; ten other side/photo values are observed. Run-8 disagreements
remain in the report, including the angled +X delta **10.42 vs 6.68 degrees** on different authored meshes.
Oakley has no recognized temple axes; its six openings remain unavailable and its raw IoUs are unchanged. The
independent reference angles were read after final fits; no product refit or fitting-code change followed.

Final non-print suite: **4,608 passed, 2 skipped, 11 failed in 859.06 s**, 14 workers, `--dist loadfile`. All ten
timing/process failures pass in isolation; the known photo_v1 golden failure remains (**199 pixels vs 114.24
allowed** alone; 195 in the full run). The two skips need Windows symlink privileges. Photoprint: **314 passed in
170.03 s**, four workers. Arview: **509 passed**; QA: **111 passed**; TypeScript check passed. M1 passed **167.031 s**,
13 requests/tools, 26 transport receipts, 12 image blobs and 35 AR images; `data/assembly/m1-proof-042c64c5/proof.json`.
Earlier full-run precision/native-count and AR reset defects were fixed and verified; no golden, numerical,
condition, print or timing threshold changed. Old-code failing controls, full refit table, retries, exact changed
Astra text, source locations, limitations and the commit hash are in `data/review_20261018/codex/task6_report.md`.

CC-12 scene rebuilding remains partly open; CC-13/17 were outside changed lines and CC-14 was already fixed.
Geometric travel sampling does not recover mechanical hinge stops or arm-arm collision constraints. The shared
clip is retained because shader, geometry and hair endpoints use it together. Checks were detached with logs,
PID/arguments/UTC/exit receipts and EcoQoS opt-out; heavy pools did not overlap. No paid call, push, owner-input edit,
root-app/`ar/` change or data/binary commit. No visual acceptance claimed; actual spend **$0**.

## 2026-10-18 — Claude accepted M11 tasks 2-5; coordination moves to a fresh session ($0)

Codex tasks 2 (`62638eb`), 3 (`25dcd54`), 4 (`ded8b77`, `74426bf`) and 5 (`46a7054`) reviewed by Claude: reports in
`data/review_20261018/codex/`, diffs read (task 4's removed assertions only covered the retired history-pruning
policy; replacements assert byte-identical history and every image kept), new and touched tests rerun: 328, 655 (+3
arview qa), 264 and 263 passed. Task 5 was interrupted twice by OpenAI "model at capacity" and resumed in the same
Codex conversation (`data/review_20261018/codex/resume_retry.sh`). Remaining: tasks 6-9 (briefs in
`data/review_20261018/briefs/`), coordinated from a fresh Claude session to keep context small.

## 2026-10-18 — ASSEMBLY M11 task 5: owner edit safety, previews and delivery ($0)

Completed `data/review_20261018/briefs/task5_owner_edit.md`. Generic translate/rotate/taper now preserve the
source minimum and reject new crossings on actual triangle coordinates. Source certificates are reused;
changed-region proofs retain exact multiplicity and the unchanged remainder, while higher requested floors
check the whole target. Float32 chart normalization preserves thickness heights and cannot discard narrow
nonwall surfaces. Physical outside, normal transport and batch/sequential behavior agree under reflection;
valid normals stay unchanged. Tilted decimation/fillets, evaluated-normal refusals, and open-mesh material edits
are covered. Main-run mesh-health helpers preload the crossing checker before the worker audit guard.

Every executable group requires delivered two-or-three-amount previews. Revealing views use fitted camera
directions. Model maps page candidates/ray samples and reuse projections without removing catalogue access.
Failed applies leave understanding retryable; each selected choice gets a distinct copy and is recorded only
after success. Fixed-depth runtime/stage paths avoid owner-edit generation growth. Copies retain hash-bound
print evidence and still run the current print checker. Exact shown bytes become immutable local deliveries
with AR/spec/seating/print reports; review works and scripted edit-tool verdicts stay in `tool_test` scope.
Nearby touched P3s cover actual tessellation, marking sink depth, partial backward normals and exact export bytes.

Native 62,016-triangle apply passed the unchanged 180-second deadline: **123.202 s cold / 43.001 s cached**;
the worker independently computed its source certificate once. Real Prada run 6 wall-piercing and missing-preview
calls refuse. Fresh M10: preparation **34.359 s**, three previews / 54 images **274.234 s**, exact B apply
**145.922 s**; delivery/review/copy-only verdict checks pass and all 985 source files stay unchanged. M6 with
amount previews passed **2,714.146 s**, eight original operations, separate UNDERSTAND/EDIT approvals, exact
selected fingerprint and unchanged source/archive files. Its historical checkpoint/capability/path harness
adapters are documented. These owner proofs predate only the final preload and plain-float diagnostic fixes;
neither changes geometry math. Fresh final-code M1 passed **174.766 s**, 13 requests/tools, 26 transport receipts,
12 image blobs, 35 AR images; `data/assembly/m1-proof-0f675e4c/proof.json`.

Final non-print suite: **4,575 passed, 2 skipped, 5 failed in 770.01 s**, 14 workers. All four timing/timeout
failures pass when rerun individually; only the known photo_v1 golden mismatch remains (**199 pixels vs 114.24
allowed**, unchanged). The two skips require unavailable Windows symlink privileges. Photoprint: **314 passed
in 171.57 s**, four workers. An earlier run exposed two real facade/JSON integration errors; both failed alone,
passed after the two-line fix, and pass in the final suite. Its three long temporary-path failures also pass
with the shorter test-run path. No golden, tolerance or timing gate was changed. Baseline failing regressions,
all retries, source hashes, exact changed Astra/owner text and per-item file/test evidence are in
`data/review_20261018/codex/task5_report.md`.

Long checks were detached/logged with PID/arguments/UTC/exit receipts, BLAS 1, EcoQoS opt-out, approximately
41 GiB free commit and no overlapping heavy pools. Arview JavaScript suites were skipped because no arview
file changed; the assembly proofs exercise production AR. No paid call, push, delivered-model edit, root-app/ar
change, product constant, weakened gate or data/binary commit. No owner visual acceptance was claimed. Actual
spend **$0**.

## 2026-10-18 — ASSEMBLY M11 task 4: M9 safety valve and desktop parity ($0)

Completed `data/review_20261018/briefs/task4_safety_valve.md`; implementation `ded8b775ca7fc531f84b67e5e6771cdb39ace470`.
Main, owner-edit and temple requests renew strictly past 80% of imported image/payload limits or the frozen
long-context price step. A normally admitted summary is followed by a fresh conversation carrying every owner
photo, unchanged evidence pixels, Astra's attributed summary and host facts. An unaffordable summary uses facts.
Old conversation rows remain unchanged; the new session pointer and receipt commit atomically. Recovery retains
archived tool outputs. Pending main finish reviews restart; specialist pending judgement survives. Run/delivery
reports and `job show` list renewals, including a SQLite fallback after interrupted receipt export.

Desktop counts are local estimates; simulated usage settles at the real frozen tariff through the unchanged
stop/reserve rules, including explicit settled-only terms. Reports label the simulation and unavailable reasoning.
The mailbox wait is asynchronous. Legacy context-filter settings remain approval-readable but no longer rewrite
runtime history. No static instructions/tool descriptions changed; exact new renewal text is in the task report.

Final focused proofs and instruction contracts: **124 passed in 36.24 s**. Main image/payload/token cases each
renew once and finish/deliver; old SQLite bytes and all owner photos/evidence are checked. Summary fallback,
real finish restart, specialist shared accounting, crash recovery, both desktop money modes and a genuine
UNDERSTAND round pass. The latter uses offline-only approval, preserves source/approval bytes and publishes
awaiting owner confirmation; it makes no geometry or visual-quality claim. `job show` normal/fallback checks pass.

Full non-print: **4,482 passed, 2 skipped, 10 failed in 869.27 s** (14 workers). Four obsolete pruning/fixed-history
money expectations were updated; their files then passed all **27 tests** without changing money assertions.
The other six reran serially: **5 passed, 1 failed in 177.19 s**. Only the known photo_v1 golden mismatch remains:
**199 changed pixels vs 114.24 allowed**; golden, tolerance and timing gates unchanged. Photoprint: **314 passed
in 211.43 s**, 4 workers. `assembly_m1`: **passed in 201.594 s**, 13 scripted requests/tools, 26 transport receipts,
12 image blobs and 35 AR images verified, clean receipts; `data/assembly/m1-proof-ea7508f9/proof.json`.

Saved desktop re-pricing, using existing M9 lossless compositions and illustrative 1,000-token summaries:
renewals before requests **49/88/140/177**, peak **98 images / 54,847,032 bytes / 231,057 estimated tokens**.
Equal-work simulated exposure falls from **$211.047981 to $46.026766**; this comparison ignores admission.
The historical $20/$23 terms instead admit 93 saved replies plus 2 summaries, then stop before 94 at
**$20.001541 simulated**. Actual reasoning/cache usage is unavailable; no finish is inferred from saved replies.

Report, exact Astra text, proof numbers, re-pricing scripts and logs: `data/review_20261018/codex/task4_report.md`.
Long checks were detached/logged with PID/arguments/UTC/exit receipts, BLAS 1 and EcoQoS opt-out; about 40 GiB
free commit supported the capped 14-worker pool. Pool-heavy jobs ran sequentially. Arview suites were skipped
because no arview code changed; the assembly proof still checks production AR capture. No paid API call, push,
root-app/ar change, product constant, owner-input addition, weakened gate or data/binary commit. Actual spend **$0**.

## 2026-10-18 — ASSEMBLY M11 item 3: what Astra sees and reads ($0)

Completed all seven items in `data/review_20261018/briefs/task3_what_astra_sees.md` on `62638eb`.
New EXIF-oriented inputs get upright transport copies; temple stages reuse recorded transport bytes,
including oversized photos and GIF, with both original and sent hashes bound. Legacy bytes stay intact.
Public side names use the wearer (+X left, -X right) across Python/arview reports, crops, worn sides and
warnings; old part-region identifiers retain their meaning. Astra now has a checkpointed, allowlisted
`load_font` tool. Sandbox hints name real tools; every format-field segment and nested spec uses the
attribute policy, with direct generator-frame/code audit coverage. Comparisons tile at native pixels,
compose edit close-ups and wrap labels; the 501-pixel corner defect is fixed. Required review fields and
surface diagnostics survive the text cap with explicit lossless paging when needed. Camera overlays are
readable through confined `read_image` paths. Capture integrity errors exit nonzero; unavailable validators,
verified portrait evidence and private child temp paths are reported accurately. Intake/history documents
now follow owner-only floor fields and append-only history; the old matte-at-intake instruction is superseded.

Focused photo tests: 105 + 57 passed; native delivery: 150 passed; side/spec/capture Python: 172 passed;
final instruction contracts: 77 passed. Old-code proofs fail for every assigned item; independent reviews
and an 18-case extreme report-paging audit passed. Main/understand/edit prompts are 15,950/15,756/15,756
characters, below the unchanged 16,000 cap. Exact changed Astra instructions/tool descriptions are printed
in `data/review_20261018/codex/task3_report.md`, with per-item file/test evidence and the commit hash.

Full non-print: 4,452 passed, 2 skipped, 6 failed in 821.20 s (14 workers, exit 1). All five timing/Blender
timeout failures passed in isolation: 5 passed, 1 failed in 165.06 s. Only the known photo_v1 golden mismatch
remains: 204 changed pixels versus 114.24 allowed (195 under load); golden and threshold unchanged.
Photoprint: 314 passed in 171.49 s (4 workers, exit 0). Arview: 507 unit tests, 111 QA tests and TypeScript
check passed, including the actual 35-image production capture. `assembly_m1` passed in 175.375 s: 13
scripted requests/tools, 26 transport receipts, 12 image blobs and 35 AR images verified; clean receipts,
actual spend $0. Proof: `data/assembly/m1-proof-f0b2bada/proof.json`.

Long checks ran sequentially, detached/logged with PID, arguments, UTC times and exit receipts, BLAS 1,
EcoQoS opt-out and basetemp under `data/ptc`; about 42 GiB free commit supported the capped 14-worker pool.
Complete scoped diff and independent reviews done. No paid API calls, push, root-app/ar changes, product
constants, owner-input additions, history rewrites or weakened gates. All evidence remains ignored.

## 2026-10-18 — ASSEMBLY M11 item 2: resume, sessions, recovery and timeouts ($0)

Completed `data/review_20261018/briefs/task2_resume_sessions.md` on task 1's accepted baseline.
Best checkpoints are scoped to the run; drafts prefer this run's best, with fatal endings still
reopening/verifying it. New owner notes bind the reviewed delivery's checkpoint; resume restores it
after interrupted-call recovery and preserves all prior history. Local resume estimates include saved
history, and approval binds resume mode, history and reviewed state. Startup stop files survive preflight.
Host rollback barriers survive crashes; failed post-call/drift saves and mutation/read recovery are
journaled stop conditions, and scope checks preserve fatal errors. Export caches bind actual scene and
exporter identity. Partial refits keep every owner photo through compatible-camera merge or full refit;
print refusals name missing photos. JSONL recovery preserves torn-tail evidence, rejects complete-record
corruption on reopening/reconciliation, and uses physical LF. JSON replacement retries/fsync are shared.
The verdict Unicode-separator concern did not reproduce: its old bytes.splitlines already preserved it.
Tool budgets compose their actual inner steps and recovery; RPC deadlines are absolute, busy replies
identify the running call, and cancellation retains its lock. Every public terminal status is checked.

Focused combined batch: 300 passed; final corrected delivery/approval fixtures: 39 passed. Independent
session/seeing/timeout slices and isolated old-code regressions are recorded in the report. Full non-print:
4,400 passed, 2 skipped, 6 failed in 788.41 s (14 workers, exit 1). All five timing/Blender-timeout failures
passed in isolation (29 passed, 1 failed in 290.42 s, including all 24 seating tests). The known photo_v1
golden mismatch remains: 199 pixels against 114.24 allowed, with reference and threshold unchanged.
Photoprint: 314 passed in 169.21 s (4 workers, exit 0). Arview: 507 unit tests, 106 capture/serve tests and
TypeScript check passed. `assembly_m1` passed in 176.641 s: 13 scripted requests/tools, 26 transport
receipts, 12 image blobs and 35 AR images verified; clean receipts, actual spend $0.
Proof: `data/assembly/m1-proof-0981bddd/proof.json`.

Fresh WP4 proof/grade: 27 cases, 262 criteria, exit 0. Substantive measurements and grade results match
before/after: P2 71/71; P3 34/40 (+2 report-only); P4 109/112 (+2 owner-pending); P5 31/32
(+1 N/A, +2 report-only). The ten known quality failures remain. Source hashes were regenerated by the
proof, never edited; the 11 saved-proof checks no longer skip as stale. Long checks ran detached with
PID/arguments/timestamps/exit receipts, BLAS 1, EcoQoS opt-out and basetemp under `data/ptc`; free commit
was about 42 GiB, pools capped at 14, with no overlapping pool-heavy jobs. Complete diff and independent
reviews done. Per-item evidence, test receipts and commit hash: `data/review_20261018/codex/task2_report.md`.
No paid API calls, push, root-app/ar changes, owner-input additions, geometry deliverables or weakened gates.

## 2026-10-18 — Owner decisions recorded by Claude ($0)

- Combined Prada edit (3 mm temples + 60% corners, GLB `f44492b9054f…9fe534`): owner "looks good!". It was a tool
  test of the M10 edit tools, not a model to deliver (owner: "we were testing the new tools and their optional effects,
  our goal wasnt to fix the model"). Record: `data/assembly/m10/combined/owner-verdict-combined.json`.
- Review page size (`arview serve` fits the frame to the face like the live site; REVIEW_20261018 section 7): parked by
  the owner until the hard technical work is done ("this is stuff to handle after we'll finish the hard technical part").
- Working method from 2026-10-18: Claude dispatches Codex directly (`codex exec`, full access as the owner allowed);
  ASSEMBLY M11. Task 1 (`9ef5cea`, `c8a1836`) reviewed by Claude: diff read, 371 new/touched tests rerun, accepted.

## 2026-10-18 — ASSEMBLY M11 item 1: run endings, money and crash safety ($0)

Completed the six items in `data/review_20261018/briefs/task1_run_endings.md`.
Draft eligibility follows the loop's status vocabulary; failed final delivery falls back to a draft,
and immutable publication survives a locked compatibility copy. Nested temple calls refuse cleanly;
the parent keeps its specialist. Trusted numeric reports refuse NaN/inf with scene-integrity metadata
preserved, hide distances are bounded, and zero ramps refuse before writes. Finish planning uses
stop minus settled/held/unknown in both modes; spend displays round down. Temple rollback preserves
fatal causes, child cutoff remains `budget_limit`, and drafts retain private stage progress with the
parent's best geometry. Verified AR reports read UTF-8. Money limits, admission and hard gates unchanged.

Focused batches passed (overlapping): root 125, delivery follow-up 29, worker 133 plus 39 existing,
temple 131, facet/instruction 151 (1 deselected), scripted child endings 4. Full non-photoprint:
4,285 passed, 13 skipped, 6 failed in 773.95 s (14 workers, exit 1). Serial rerun and final instruction
checks: 190 passed, 1 failed in 252.56 s; all five load-sensitive timing/timeouts cleared. Photoprint:
314 passed in 168.35 s (4 workers, exit 0). Fresh `assembly_m1`: passed in 171.42 s, exit 0, 13 scripted
requests/tools, 12 image blobs and 35 AR images verified; receipts clean, actual spend $0.
Proof: `data/assembly/m1-proof-55373ce4/proof.json`. About 40 GiB free commit, pools capped at 14,
BLAS 1, detached EcoQoS opt-out; arguments, PIDs, timestamps and exit codes saved with long-run logs.

Open verification limits: known photo_v1 golden drift remains (199 changed pixels, unchanged allowance
114.24); 11 of the 13 skips are saved WP4 proof checks whose source hashes this task invalidates.
That historical proof was not regenerated. No arview changes, so npm checks do not apply. The brief's
path-less pytest invocation collected outside glasses3d; stopped before tests, then ran from the repo
root with explicit `glasses3d/tests`. Complete diff and independent reviews done. Report and evidence:
`data/review_20261018/codex/task1_report.md`. Implementation/test commit: `9ef5cea2e123d9db46e8b7a849cc23e71380232f`
(`fix(glasses3d): preserve drafts and crash safety at run endings`); this progress-only follow-up records its hash.
No paid API call, push, root-app/ar change or task 2 work.
