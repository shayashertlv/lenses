# M13 C0: Claude runner design

**F1 amendment, 2026-10-20:** server-side context editing is removed after C5's
repeated clearing invalidated cached prefixes. Standard Messages, fixed top-level
one-hour caching and the existing fresh-conversation renewal are the active
policy. This amendment supersedes D6's clearing proposal and the historical C0
profiling below. F1 evidence and estimates live in `work/f1/`; no paid run is
authorized by them.

Survey date: 2026-10-20. Source baseline: `ac00f92bf4a7577ea52416dba41587aa1ff25557`,
branch `glasses3d`. References below are current repository paths and line numbers,
not ASSEMBLY's historical line numbers. Paths starting `agent/` are relative to
`glasses3d/`. This is a design for coordinator review, not implemented behavior.
Final plan review used HEAD `3377ab0e59d0a370dcf91b222da4d1c64718c170`;
the surveyed agent/M1 code has no diff from the initial baseline. The reviewed
`M13_CLAUDE_TEST.md` SHA-256 is
`9e58f45bec9f6d912bec4ee02c8aec35c814ace5ad718db45770f4950141211b`.

The model swap can stay entirely in `claude_test/`. Reuse the scene, worker,
modeling tools, finish checks, evidence selection and immutable delivery machinery.
Replace the provider loop and its protocol/accounting adapters. Do not call
`run_astra` with a Claude client, patch module globals, or disguise an Anthropic
ledger as an OpenAI ledger. No shared source edit is necessary for the proposed
personal runner and review wrapper. The existing dev UI cannot gain Claude run
discovery or provider labels merely from new result fields; section 6 states that
boundary explicitly.

M13 D1–D9 and `work/briefs/m13_common.md` govern. The plan was updated during
this survey; the current owner decision selects **Opus 5.5 at max effort**.
C2/C3 use fake HTTP only. C4 uploads/counting are already authorized after C3
passes, but are outside C0. C5 still requires an estimate and explicit cutoff
approval. The key file is passed as a path, never
read by a coding agent. C5 starts from the empty template with the same six
Oakley owner photos, verbatim description, empty specifications and view/side
labels. A C3 seeded tool test is labelled separately and supplies no product
geometry to C5. M14 evidence-push changes are excluded.

## 1. Orchestration map

### Existing Astra order

| Order | Current code and behavior |
|---|---|
| 1. Prepare | `agent/job_run.py:61` `prepare_run`: validate job/input hashes, settings and owner-edit state; construct `JobScene`, tools, stage contracts, instructions, exact brief, references, notes and optional resume binding; build the approval request. `jobs.load_job` verifies owner inputs at `agent/jobs.py:213`; `brief.build_brief` is at `agent/brief.py:38`; `jobs.references` at `agent/jobs.py:263`. No worker or inference starts during preparation. |
| 2. Enter/admit | `agent/job_run.py:196` `execute_run` reconstructs the prepared request and calls `run_astra`. `agent/loop.py:1514` validates the spec, chooses run/session paths and creates the blob store. Under `JobLedger` (`agent/budget.py:1480`), it checks approval, recomputes current input/instruction/tool/settings bindings, checks the two-way tool contract, session/resume agreement, disk space, optional machine load and client configuration (`agent/loop.py:1529`). Stop files arriving during startup are preserved (`agent/loop.py:1554`); the old module docstring's stale-stop removal is not the current implementation. |
| 3. Start scene | `agent/loop.py:1558` calls `scene.start`. `JobScene.start` creates an empty seed only if needed and starts/verifies the journaled worker (`agent/job_scene.py:127`; `agent/journal.py:133`). `JobScene` wraps worker mutations, checkpoint events and rollback barriers, not just Blender startup. |
| 4. Plan recovery, consume | Resume reads durable history and plans rollback before repairing dangling calls (`agent/loop.py:1573`). `JobLedger.open_paid_run` acquires the global paid-run lock and consumes approval (`agent/budget.py:1700`; call at `agent/loop.py:1586`). The scene has started before this acquisition in today's code. Nothing paid has happened. |
| 5. Freeze and construct | Capture exact source (`agent/loop.py:1605`; `agent/snapshot.py:46`), perform planned rollback/reviewed-checkpoint restore, repair unanswered calls, build invocation and opening input (`agent/loop.py:1606`, `:1630`). Create session, clients, guard, log and stage runtime; bind the scene log (`:1632`–`:1644`). Write `run-config.json` and `input-receipt.json` (`:1645`, `:1670`). |
| 6. Heartbeat and loop | Start heartbeat and load watch (`agent/loop.py:1680`), build the Agent and renewal controller, then `_drive`/`_drive_loop` (`:1429`, `:1442`, `:1683`). Heartbeat reports every 2 seconds with worker PID/CPU and prolonged states (`:1131`). The SDK is configured for one function-tool execution at a time (`:528`). |
| 7. Request admission | Model hooks count interactions; `ReviewLog.before_request` checks Stop and calls `BudgetGuard.before_request` before archiving (`agent/receipts.py:367`; `agent/budget.py:1057`). Guard checks endpoint, settled cutoff/unknown outcome, actual body size/image count and free token count. Pressure may request renewal before inference admission. The response is archived and settled at transport, then SDK usage is cross-checked (`agent/receipts.py:407`, `:451`; `agent/budget.py:1156`, `:1272`). |
| 8. Tools | The registry's FunctionTools validate arguments and enter the existing async host-call wrapper; real work runs under the scene lock in a thread. Calls journal before/after checkpoints, return bounded text and labelled images, and invoke tool log hooks (`agent/tools/__init__.py:277`; `agent/receipts.py:458`, `:485`). Inner worker deadlines and outer SDK timeouts are distinct. Stage calls enter a private scene/history and share parent money/interaction limits (`agent/loop.py:744`; `agent/temple_lab/stage.py:271`). |
| 9. Context | Old `context_guard_*` fields are compatibility metadata, not active history pruning (`agent/loop.py:193`; `agent/job_run.py:85`). `ConversationRenewal` asks for a state summary, carries photos/current evidence and atomically switches conversation (`agent/renewal.py:140`; `agent/loop.py:973`). `_drive_loop` handles the never-sent pressure attempt and resumes the new epoch (`agent/loop.py:1456`). |
| 10. Finish | First `finish` exports/checks/captures and returns the per-photo review evidence; second call must name its actual review call and all required photos (`agent/tools/__init__.py:684`; `agent/tools/finish_review.py`). Mutation, camera refit, lost output or renewal invalidates the pending review. `FinishOnAccepted` ends only on `status == "accepted"` from the named finish tool (`agent/loop.py:574`). An ordinary text reply is not a finish. |
| 11. Terminate | `_drive_loop` maps completion, bounded text/output continuations, turn limits and renewal. Exceptions become the closed status vocabulary (`agent/loop.py:1287`; `agent/run_statuses.py:3`). In `finally`, cancel/await child tasks, mark unresolved requests unknown, verify exit checkpoint, close scene/session/clients, finish heartbeat/load watch, close accounting and remove only an observed Stop file (`agent/loop.py:1706`). Write authoritative `result.json` even on failure (`:1752`–`:1808`). |
| 12. Publish/review | Outside `run_astra`, `_publish_result` tries finished delivery, otherwise a draft, and records the attempt (`agent/job_run.py:245`). Finished preparation validates matching scene/export/checks (`agent/delivery.py:432`); draft salvages a verified checkpoint without a model call (`:955`, `:1086`). Package/publish immutable artifacts (`:494`, `:837`); review verifies and serves those exact bytes (`agent/review.py:165`). Owner verdicts are explicitly written later and hash-bound (`agent/verdicts.py:217`). |

### Claude sequence and differences

Keep the same phases. The personal runner acquires the private job's existing
`budget/.job-ledger.lock` using the original OS-lock primitives, performs pure
preflight, then acquires `PaidRunLock(PAID_RUN_LOCK_ROOT)` **before** starting the
worker. This small ordering improvement prevents a concurrent Astra admission
from racing worker startup; it does not change Astra. Production must use the
actual shared lock root from `agent/budget.py:64`, never a work-local substitute.
Scripted tests inject an isolated lock root and prove cross-provider exclusion
separately with lock-only fixtures.

After worker verification, recheck the sealed request and consume A's approval.
Freeze sources, create the Claude conversation and log, and build
`anthropic.Anthropic(http_client=transport.guarded_client(...), max_retries=0)`.
The frozen transport is synchronous: keep the orchestration async for the
existing tools, but run each complete SDK streaming transaction in
`asyncio.to_thread`. One request owns the client at a time, including specialists.
Do not block the event loop needed by `StageBridge`.

Before each inference: observe Stop; check the shared settled ledger; build the
immutable-prefix Messages body; run image/body checks and the context-pressure
policy in section 4; run soft renewal logic; then send. Full-history free counting
is conditional on a valid counting representation, not assumed to support file
IDs. Transport independently rechecks money and endpoints. Consume the entire
SSE and finish transport settlement before invoking any returned tool. Known,
settled usage that crosses the cutoff still permits that response's tool,
including a valid second `finish`; it forbids the next inference. Unknown charge
stops the loop before executing its proposed tools and produces a draft.

Use `tool_choice={"type":"auto","disable_parallel_tool_use":true}`. Receive and
persist full assistant content, invoke the real tool, then append one user
message whose first block is its matching `tool_result`. Defensively serialize
multiple returned tools in order and append **one** user message containing all
matching results before any other text; never run two Blender operations
concurrently. Recheck finish validity after the whole batch: a later mutation
invalidates an earlier accepted finish. An accepted finish remains the only
successful ending. No additional model call is needed after accepting it.

| Claude outcome | Run status/action |
|---|---|
| Accepted `finish` / child `facet_finish` | `completed`; publish only if all existing delivery checks hold. |
| `end_turn` without tool | Append exactly `Continue the task; the run ends only through the finish tool.` once for that response; count `finish_notes`. Preserve existing maximum of two such continuations, then `ended_without_finish`. No invented coaching. |
| `max_tokens` | `output_limit` draft; preserve complete raw content but never execute partial tool JSON. D4 says every other error stops; do not add an automatic extra paid reply for truncated output. |
| `model_context_window_exceeded` | `output_limit` draft with its actual stop reason; no truncated tool execution or output-cap reduction. |
| `refusal` or explicit refusal details | `provider_rejected`; current D4 prices usage at normal rates as an upper bound, retains that qualification and stop details/public text, then drafts. No fallback model. |
| Before-body HTTP 429/529 with `retry-after` | A's reconciled transport waits/retries the same body, logs each wait, max 20 minutes. SDK retries stay zero. |
| HTTP 429/529 without `retry-after` | Stop as draft under current D4; no invented 30-second resend. A must reconcile its older frozen implementation policy before C2 live use. |
| Other HTTP 4xx | `provider_rejected`, no retry. |
| Error SSE, truncated SSE, missing/unpriceable usage, served-model mismatch, other uncertain transport outcome | `provider_unknown`; durable unknown ledger record, no next request. |
| Cutoff / Stop / global turn limit / hard payload failure / fatal worker failure | `budget_limit` / `stopped` / `turn_limit` / `payload_limit` / `failed_worker`. Preserve cause; ordinary tool refusals are not run failures. |
| Unexpected protocol/host error | `failed`, or `provider_unknown` if a charge is unresolved; draft and all teardown receipts. |

Cancellation must join/drain or close the actual synchronous stream and persist
its settled/unknown outcome before releasing locks. Cancelling `to_thread` alone
does not stop its thread. Do not replay a timed-out inference or mutating tool.

Current D4 deliberately uses refusal usage as a conservative upper bound:
some pre-output refusals are free despite nonzero usage. Preserve the category
and timing and label the upper-bound accounting; do not claim it is a verified
invoice charge. Initial 429/529 being unbilled remains the plan's logged
assumption, checked against Console usage after the run. See `FACTS.md` §§1–2,
11 and [refusal billing](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback)
(checked 2026-10-20). C0 does not change A's interface or files.

## 2. Reuse map and approval binding

| Step | Decision and exact reusable surface |
|---|---|
| Owner job | Import `jobs.load_job`, `jobs.references`, `brief.build_brief`, `brief.host_text` unchanged. Copy only verified manifest and referenced input files into `work/jobs/`; retain original job ID in a distinct root and record copy provenance outside `job.json`. No old session, approval, runs, inferred product facts or credentials are copied. |
| Preparation | Own `runner.prepare_run`; `job_run.prepare_run/execute_run` bind OpenAI model/settings/stages and hard-call `run_astra`. Import `job_run.new_run_id` if a short ID is wanted. Reject owner-edit/fallback jobs for M13. |
| Settings/contract | Reuse `RunSettings` only as a tool-construction carrier and `ToolContract`, `contract_problems`, `instructions_sha256`, `tool_registry_sha256` as source-contract helpers (`agent/loop.py:146`, `:156`, `:536`, `:835`). Own immutable Claude settings and effective Messages-tool hash; bind both source and effective descriptors. Never change a shared FunctionTool in place. |
| Approval | Import `agent.approvals.input_digests`, `run_input_binding`, `build_request`, `canonical_json`, `request_sha256` (`:74`, `:94`, `:201`, `:224`). Wrap A's frozen approval API as below. Do not reuse OpenAI `JobLedger.preflight/open_paid_run`. |
| Locks | Import `PaidRunLock` unchanged (`agent/budget.py:1390`); wrap `_open_locked`/`_os_unlock` for the private job lock (`:1316`–`:1351`). A's ledger remains the sole charge authority. |
| Source receipt | Import `snapshot.capture` unchanged. It includes nonignored new package source and excludes environments/data. Run it after parallel source edits have finished; its source snapshot is evidence of what was executed. |
| Scene/worker | Import `JobScene`, `BlenderSession`, `Seeing` and `SceneHooks` unchanged. Bind review log, preserve run-scoped best checkpoint, journals, `exit_checkpoint`, `worker_info`, close and recovery semantics. |
| Photos | Reuse `ReferenceInput`, `plan_session_input([], ...)`, `image_identity`; convert output blocks. Existing planner's history scan is OpenAI-specific, so never pass Claude messages to it expecting deduplication (`agent/session_inputs.py:166`). |
| Tool work | Import `build_tools`, real FunctionTools, text bounding/image construction and SDK invocation helper. Adapt only protocol blocks, runtime context, omission of strict grammar, and the temple money entry described below. Validate original schemas locally. |
| Money | Import A's `Ledger`, `price_usage`, `guarded_client`, approval functions unchanged. Own a compatibility view for legacy tool footers. Neither `RunLedger` nor `BudgetGuard` can price Anthropic usage. |
| History | Wrap `BlobSession` with root session ID `"claude"`; reuse storage and atomic renewal, forbid popping/rewriting rows. Own Claude recovery/protocol checks. Do not use `history_replay.reprice` or `restore_append_only`: they parse Responses items and OpenAI image/token math (`agent/history_replay.py:61`, `:103`, `:133`). |
| Receipts | Wrap `ReviewLog(guard=None)`; reuse event, note, Stop, tool hooks and journal storage. Own Messages/SSE model receipts, thinking summary extraction, provider-labelled transcript intro and money updates. Never use its OpenAI transport hooks or Cleaner as replay storage. |
| Heartbeat/load | Wrap/subclass `Heartbeat` and `LoadWatch` to label Claude and expose exact settled/cutoff/left, retaining original scheduling, CPU sampling and prolonged-state handling (`agent/loop.py:410`, `:1131`). Omit legacy held/reserved display fields; never invent a reserve. |
| Specialist | Import `temple_lab.stage.run`, `StageBridge`, `StageLab`, package creation, registry, finish/apply and failure-progress routines unchanged. Own `ClaudeStageRuntime` replacing `loop.StageRuntime`'s OpenAI execution internals. |
| Renewal | Own provider controller; import the exact summary request and neutral notices from `renewal.py`, and scene/lab evidence/state hooks. Existing `ConversationRenewal` calls Responses and uses OpenAI thresholds. |
| Final/result | Own provider exception classification and result assembly; import `checked_terminal_status`, neutral checkpoint helpers and atomic JSON writers. Preserve the existing result schema; add provider metadata. |
| Delivery | Import `delivery.deliver_finished` unchanged. Wrap draft orchestration using unchanged `_draft_eligible`, `_draft_export`, `_draft_checks`, `_draft_capture`, `_read`, `_package`, `_publish`, `_last_summary`. This is necessary because `deliver_draft` itself enters OpenAI `JobLedger` (`agent/delivery.py:1102`). Do not call `job_run._publish_result` unchanged for Claude. |
| Review/verdict | Import `load_delivery`, `review_job`, `reviewed_delivery`, `append_verdict` unchanged; personal presentation wrapper supplies provider/model outside immutable deliveries. No automated owner acceptance. |
| Offline | Import `require_offline` and `ProductionTransportTripwire` unchanged (`agent/offline.py:190`); it already intercepts both httpx and httpx2. Assert the Anthropic client's httpx path is covered. Own `ScriptedClaude`; `replay.ScriptedOpenAI` is Responses-specific (`agent/replay.py:218`). |
| Resume | C5 is fresh. Do not expose cross-provider resume. Own crash audit of Claude history; any new paid resumed run would require a new sealed approval. `agent/resume.saved_items/binding` use `"astra"` and Responses data (`:14`–`:65`). |

### Full request binding with A's narrow approval interface

A's frozen `write_approval` accepts words, amount, model, effort and run ID; it
does not accept the entire execution-request hash. Its own
`approval-request.json`/`approval.json` must not be overwritten by C2.

1. Prepare an exclusive `runs/<id>/run-request.json` using the existing
   `build_request` and input digest functions. Include provider/model, effort,
   exact Decimal cutoff, max output, source/effective instruction and tool hashes,
   stage contracts, image policy, cache/context policy, finish/continuation
   policy, fresh-session binding and client mode. `run_id` is a confined token.
2. Only the explicitly authorized approval CLI recomputes this document, checks
   it matches the prepared bytes, invokes A's `write_approval`, then exclusively
   writes `approval-binding.json`. This seal contains job/run IDs, provider/model,
   execution-request canonical SHA, raw `run-request.json` SHA and raw hashes of
   A's two approval files. It records the same owner words/amount, not invented
   approval text.
3. Under job/global locks, require the complete seal, verify all hashes and the
   current recomputed execution request, then call A's `consume_approval`.
   Any missing seal, mismatch or already-consumed approval refuses before network.
   A crash during seal creation leaves an unusable approval; do not auto-repair
   it. Consumption never occurs during prepare/count-only planning.

This preserves the original stale-input rule (`agent/approvals.py:307`,
`:370`) without expanding A's API. C3 uses explicitly offline-only approvals,
synthetic words and keys; production rejects these independently of transport
selection. Record approval binding in both run-config and result.

### Tool money and specialist runtime

`Ledger.money_status()` stays exactly `{settled_usd, cutoff_usd, left_usd}`.
Legacy tools expect `settled_stop_usd` and `remaining_to_stop_usd`
(`agent/money.py:11`; `agent/tools/__init__.py:675`); provide a private view that
maps those names without rounding admission arithmetic. `ClaudeRunStatus.footer`
renders only settled/cutoff/left; context measurements belong in receipts.

`temple_stage` additionally performs legacy micro-dollar conversions
(`agent/tools/temple.py:43`–`:61`). A supports Decimal precision beyond six
places, so blindly supplying a BudgetGuard-shaped dictionary can stop early.
Create a **private replacement of this one FunctionTool**: keep its name,
description, schema and timeout; replace only the callable's money-entry
logic with `Ledger.may_start_request()` and exact Decimal left.
Retain the existing scene-call/journal/output behavior and `StageBridge`
cancellation pattern; factor that private callable through the adapter's
`call_scene` helper rather than reaching into a closure or patching globals.
Its operation is the unchanged `stage.run(scene, runtime, bridge, cid, left,
child_settings)`. No child reserve or extra cutoff is introduced.
Re-decorate the no-argument entry with the original
`fatal_tool_errors(*FATAL_TOOL_ERRORS)` callback and no outer timeout, then
apply `bounded_tool` and assert/copy the original descriptor. The SDK embeds
its failure callback inside `on_invoke_tool`; replacing that callable on a
shallow clone does **not** preserve error handling automatically.

`ClaudeStageRuntime.run_stage` accepts the `StageSpec` generated by `stage.run`,
checks its canonical source contract and the separately approved effective
Claude contract, transforms the narrowly allowed instruction lines, and runs
the same Messages loop. It shares the parent's chosen model, ledger, transport,
file cache and global request/turn counters. It has a private `"claude"` session,
tools, scene and finish tool `facet_finish`. Preserve task tracking,
`StageRunFailed`, cancelled-child unwind, exact parent-head apply, child progress
and outcome receipts (`agent/loop.py:744`; `agent/temple_lab/stage.py:222`,
`:271`). The child retains `finish_k=4` and all existing physical/finish gates.
Nested unsupported stages refuse normally; no path constructs an OpenAI client.

## 3. Tool adapter

### Descriptor, invocation and result contract

Each Messages tool is `{name, description, input_schema}`. Copy
`params_json_schema` and description exactly; **omit `strict` for every tool**
under the current D7 decision.
Preserve registry order for stable prefixes and hash the effective JSON.
Host validation remains active for every tool.

Before invocation, validate against the original `params_json_schema` using
`jsonschema.Draft202012Validator`, in addition to rejecting malformed/non-finite
JSON. Then retain the original Pydantic and domain checks. The SDK's generated
Pydantic model alone can accept omitted defaults or ignore extra fields even
though its advertised strict schema forbids those inputs
(`agents/function_schema.py:521`–`:526`). Return a bounded matching `E_ARGS`
tool result on schema failure, without entering the worker. Verify `jsonschema`
is present in C2's environment; it is already pinned to 4.26.0 by
`glasses3d/requirements-agent.txt:44` and `requirements.txt:67`, which A installs.
No new dependency or A-owned file edit is needed.

Create `ToolContext.from_agent_context(RunContextWrapper(context=status),
tool_use_id, tool_name=name, tool_arguments=canonical_arguments)` and invoke
`agents.tool.invoke_function_tool(function_tool=tool, context=ctx,
arguments=canonical_arguments)`. This helper calls `on_invoke_tool` **and**
enforces `FunctionTool.timeout_seconds`; direct `on_invoke_tool` alone misses
the SDK timeout. Installed SDK references:
`glasses3d/.venv/Lib/site-packages/agents/tool.py:2198` and
`agents/tool_context.py:233` (C2 rechecks these against its pinned venv).

Call shared tool-start/end/journal hooks. Preserve raw tool IDs in its scene and
conversation; namespace child IDs only in the shared log, as
`_SharedHooks._tool_context` does (`agent/loop.py:695`). Reject unknown names,
duplicate/inconsistent IDs and non-object/malformed/non-finite arguments before
mutation. Validation failures become matching `tool_result` errors.

Map string/`ToolOutputText` to text blocks, and `ToolOutputImage` to labelled
Files image blocks. Preserve order, JSON report text and footer; do not stringify
a mixed output into one string. A normal refused result remains useful JSON;
set `is_error=true` for validation/executor failures while retaining its
code/message. `FATAL_TOOL_ERRORS` (`agent/loop.py:676`–`:677`) and cancellation
escape to run classification; never turn a dead worker or unknown charge into a
model-visible retry hint. Use existing timeouts (`agent/tool_timeouts.py:156`)
and `bounded_tool`/paged `read_report` (`agent/tool_reports.py:427`), with no new
short blanket timeout. `temple_stage` and `photo_print` keep their deliberate
outer `None` timeout and real inner deadlines. A timed-out thread may still own
the scene lock; do not launch a replacement mutation.

Image preparation is a private `tool_adapter.prepare_images` operation, not a
new requirement on B: **the C0 phrase `images.prepare` is absent from the frozen
common interface**. It means decode the actual emitted image bytes, call B's
`tile(bytes,label)`, resolve every tile via `FileCache.file_id_for`, and emit
`label_block` then `image_block`. Do not read a higher-resolution source path
instead of the bytes Astra actually received: some shared tools intentionally
compose previews, and D2 requires evidence parity. Store source/tile SHA,
dimensions, native coordinates, file ID, originating call/checkpoint and label
in an image manifest. Preserve PNG/WebP bytes when fitting; no resampling.
JPEG/GIF references, if ever encountered, need lossless decoded-pixel PNG
normalization before B's PNG/WebP tiler, with separate original/transport hashes.

Apply the many-image bound from the outset to non-reference images so passing
20 images never requires changing an earlier block. Oakley's six photos fit
whole; verify that and refuse if they do not. Every repeated file-ID occurrence
counts toward the request image limit. File IDs do not remove context/image
limits. Check the final encoded request is strictly below 32,000,000 bytes,
at most 600 images, has no inline base64, and all IDs have known dimensions.
Respect per-file byte limits without lossy compression; an unencodable image
is an explicit refusal. Add `transformations:{"oversized_image":"error"}` to a
copy of each image block, as confirmed by FACTS §5; B's
`image_block` interface remains unchanged. This is the documented
[reject-resize mechanism](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)
(checked 2026-10-20).

Shared tools mark images/finish output emitted before this conversion
(`agent/tools/__init__.py:217`–`:247`, `:363`–`:393`). If upload, conversion or
durable result append fails, call `scene.mark_unseen(call_id)`, invalidate its
pending/accepted finish, and persist the failure. Never accept a finish whose
review images failed to enter the conversation.

### Strict-schema compatibility audit; all declarations omit strict

The official [structured-output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
(checked 2026-10-20) limits a request to 20 strict tools, 24 optional parameters
and 16 union-type parameters across strict schemas. Several schema bounds are
unsupported. This is a protocol constraint, not permission to relax a modeling
gate. The current parent registry has 26 tools and 42 `anyOf` nodes. Current D7
chooses ordinary tools for the entire registry. Preserve every input schema;
only server grammar enforcement is omitted. The table records the audit
requested by C0: an otherwise possible 20-tool allocation would exclude six
tools and leave 16 union nodes, with zero optional object properties. It is
**not** the selected implementation. No schema constraints are removed.
Re-audit resolved schemas when the registry changes; no paid schema probes.

| Main tool | Definition (`agent/`) | Union nodes | Hypothetical strict eligibility |
|---|---|---:|---|
| execute_blender_code | `tools/__init__.py:397` | 0 | true |
| load_font | `tools/__init__.py:471` | 0 | true |
| scene_summary | `tools/__init__.py:485` | 1 | true |
| save_checkpoint | `tools/__init__.py:492` | 0 | true |
| list_checkpoints | `tools/__init__.py:510` | 0 | true |
| restore_checkpoint | `tools/__init__.py:515` | 0 | true |
| fit_cameras | `tools/__init__.py:520` | 3 | false; aggregate quota |
| render_views | `tools/__init__.py:567` | 4 | false; aggregate quota |
| compare_to_photos | `tools/__init__.py:585` | 2 | true |
| pick_surface_point | `tools/__init__.py:600` | 2 | true |
| photo_read | `tools/photo_read.py:32` | 5 | false; array bounds |
| export_glb | `tools/__init__.py:616` | 0 | true |
| arview_capture | `tools/__init__.py:623` | 0 | true |
| compare_look_to_photos | `tools/look.py:279` | 2 | true |
| read_image | `tools/__init__.py:642` | 1 | true |
| budget_status | `tools/__init__.py:675` | 0 | true |
| finish | `tools/__init__.py:684` | 2 | true |
| seat_lens | `tools/seating.py:35` | 3 | false; aggregate quota |
| hide_eyewire | `tools/seating.py:70` | 1 | true |
| check_lens_seating | `tools/seating.py:93` | 5 | false; aggregate quota |
| unseat_lens | `tools/seating.py:121` | 0 | true |
| unhide_eyewire | `tools/seating.py:128` | 1 | true |
| temple_stage | `tools/temple.py:23` | 0 | true |
| photo_print | `tools/photo_print.py:150` | 2 | true |
| read_report | `tool_reports.py:427` | 2 | true |
| labelled_model_map | `tools/model_map.py:644` | 6 | false; aggregate quota |

Parent `photo_read` has fixed-length UV/edge coordinates in
`$defs.Pick.properties.uv/edges`, bounded pick counts in
`$defs.Point.properties.picks` (2–4), and `properties.points` max 8.
Do not remove those constraints locally.

The ordinary specialist has 10 tools and 33 union nodes:

| Specialist tool | Definition (`agent/`) | Unions | Hypothetical strict eligibility / unsupported bounds |
|---|---|---:|---|
| facet_inspect | `temple_lab/tools.py:682` | 0 | true |
| photo_image | `temple_lab/tools.py:693` | 1 | false; crop length 4, scale range 1–3 |
| photo_read | `temple_lab/tools.py:700` | 3 | false; UV/edge tuples, 2–4 picks, max 8 points/64 drops |
| photo_compare | `temple_lab/tools.py:707` | 4 | false; point tuples/count, zoom box length 4/max 3 zooms |
| facet_edit | `temple_lab/tools.py:715` | 9 | false; judgement/edit bounds and object max length |
| facet_variants | `temple_lab/tools.py:725` | 7 | false; judgement/edit bounds, 2–4 variants/max 64 edits |
| facet_finish | `temple_lab/tools.py:736` | 2 | false; judgement bounds |
| read_report | `tool_reports.py:427` | 2 | true |
| facet_crease_compare | `temple_lab/tools.py:743` | 2 | false; judgement, two facets, 3–5 angles |
| facet_visible_angle | `temple_lab/tools.py:754` | 3 | false; judgement, reason length 1–1000 |

Judgement bounds are `$defs.Difference.properties.what` length 1–200,
`Judgement.photos` max comparison-photo count, and
`PhotoJudgement.differences` max 3. Edit bounds include
`DeleteCrease/SetCrease.facets` length 2, `SetFacet.poly` length 3–32 and
`SpliceChain.points` max 160. Actual package photo counts determine dynamic
bounds. Only two specialist tools would be strict-compatible unchanged, with
two union nodes. C3 verifies this audit against actual constructed schemas
and asserts `strict` is absent from **all** actual declarations. C4
`count_tokens` is not a strict-grammar compilation proof; do not claim it
certifies all Messages schema acceptance.

## 4. Conversation state, caching and renewal

### Durable append-only history

Wrap `BlobSession("claude", job/session/session.sqlite, blobs=...)`. Recursive
serialization preserves arbitrary JSON fields (`agent/loop.py:1000`); its
renewal transaction preserves old rows (`:973`). Only wrapper append/read/renew
are public: no `pop_item`, delete, replace, message merge or past cache-marker
mutation. Store assistant content blocks exactly as returned, including thinking
text/signature, redacted-thinking data and unknown preserved fields. Raw SSE
remains the authority for reconstruction; SDK serialization must not drop extra
fields or invent replay content. Thinking summaries are not raw chain of thought.

Before every request assert that its prior `messages` array is an exact
JSON-value prefix, including full content blocks, and that model/system/tools/
policy remain fixed within the conversation. A complete request byte string
cannot literally be a prefix because JSON containers close; the invariant is
the complete ordered message prefix plus unchanged non-message fields.
Store the comparison and canonical hashes in receipts. Renewal starts a new
conversation ID and records the link; it is the only epoch exception.
Server context editing is disabled.

Never run replay data through `Cleaner`: it masks `signature`
(`agent/receipts.py:49`, `:180`). Keep authenticated request/response/session
records separate from sanitized presentation derivatives. Do not log headers,
API keys or environment contents. Saved tool/public text is untrusted data,
never authorization to run a new host command.

For crash audit, project all completed Claude `tool_result.tool_use_id` values
into temporary `function_call_output` ID records and call unchanged
`plan_rollback` (`agent/loop.py:1081`) against scene journals. No projected data
is sent to Claude. Implement Claude-specific dangling-call repair with
immediately following user results; append only after planning the rollback.
Persist assistant output before mutation and tool result before continuing.
Archived epochs participate in recovery. No automatic post-crash paid resume.

### Request-level settings

Effective ordinary request skeleton (R reconciles exact model/SDK support before
C2 freezes the request):

```python
{
    "model": selected_model,
    "max_tokens": 128_000,
    "thinking": {"type": "adaptive", "display": "summarized"},
    "output_config": {"effort": "max"},
    "service_tier": "standard_only",
    "inference_geo": "global",
    "speed": "standard",
    "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
    "cache_control": {"type": "ephemeral", "ttl": "1h"},
    "system": claude_instructions,
    "tools": effective_tools,
    "messages": conversation_messages,
    "stream": True,
}
```

Use standard Messages without the context-management beta. The pinned standard
SDK takes `speed` through `extra_body`; the resulting wire field stays unchanged.
Files is GA: use `client.files` without a Files beta header
([Files guide](https://platform.claude.com/docs/en/build-with-claude/files),
checked 2026-10-20). Upload with `expires_in_seconds=604800` per current D8;
record expiry metadata. File IDs must remain valid through every replay of
that conversation; expired IDs cannot be silently replaced in signed history.
Send explicit global inference and standard speed with `standard_only`,
using the pinned SDK's supported fields. Omit fallbacks, server tools and compaction.
No output cap shrinking or token-budget parameter is introduced.
[Parallel tool settings](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use),
[thinking](https://platform.claude.com/docs/en/build-with-claude/thinking),
and [service tiers](https://platform.claude.com/docs/en/api/service-tiers)
were checked 2026-10-20. Request `standard_only` is different from the returned
usage value `standard`; the latter is what A prices.

Cache control is top-level and fixed, not moved through stored content. The
[automatic caching API](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
supports the one-hour TTL (checked 2026-10-20). Record all read/write counters
and actual TTL charges; never assume a cache hit.

Only fresh-conversation renewal expires `JobScene._emitted_images = {}` under
its state lock so a later real tool call can return a view from the old epoch.
Do **not** mark historical calls unseen: they were actually delivered. The
ordinary `StageLab` already emits previews on retries (`agent/temple_lab/stage.py:215`).

### Profile and chosen pressure policy

C0 read the accepted rerun's local
`data/jobs/oakley-oo9208-v2-911a2b0e/runs/20261019T172111-465930/`
`budget.json`, `events.jsonl` and `renewals.json`, without making a request:

| Measured Astra profile | Value |
|---|---:|
| Settled requests | 70 |
| Counted input min / max | 25,150 / 224,953 |
| Mean counted input | 127,069.93 |
| Largest emitted request | 61,745,180 bytes (base64-era transport) |
| First renewal pressure | 218,104 input tokens; 66 images; 51,556,579 bytes |
| First rebuilt conversation | 41,582 tokens; 12 images; 11,139,183 bytes |
| Second renewal pressure | 224,785 tokens; 76 images; 61,744,320 bytes |
| Second rebuilt conversation | 117,225 tokens; 40 images; 33,858,253 bytes |
| Current evidence carried | 6 then 34 images, plus all six owner photos |

The original 350K trigger / 150K minimum clear / 8 retained tools proposal was
not validated by this Astra profile. C5 later showed repeated clearing with
zero cache reads and full prefix rewrites. F1 removes those settings from
requests and approval binding. Reported `cleared_input_tokens` are inconsistent
with request sizes and must not be used to estimate saved tokens.

Provider-specific soft renewal is strictly above 480 image occurrences,
at or above 25,600,000 serialized bytes, or strictly above 80% of verified usable
input capacity: for Opus 5.5's 1M window the context trigger is **800,000**.
Do not subtract the 128K output cap: current models accept input plus requested
output exceeding the window; input alone exceeding it is rejected, and actual
generation reaching it ends with `model_context_window_exceeded`.
[Context overflow](https://platform.claude.com/docs/en/build-with-claude/context-windows)
(checked 2026-10-20). Never reuse OpenAI's 272K tariff threshold.
Apply hard checks to the full submitted request, which
still contains all file-ID blocks. The context meter below is a forecast,
not an exact admission guarantee; do not fabricate a zero count or promise a
future cache hit.

### Separate free counting representation and runtime context meter

`count_tokens` rejects file/URL image sources; it accepts base64 images and
checks `oversized_image:error`, but does not validate file access or caching.
[Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)
(checked 2026-10-20). Thus C4 counts an **unsigned opening projection** with the
same native image bytes embedded. Strip fields unsupported by the count
endpoint using the pinned SDK schema, retaining model/system/tools and supported
thinking/context settings. Store projection hashes and its limitations.

Do not send converted signed history for counting: file-to-base64 binding
equivalence is not established. Inference messages remain unchanged file-ID
messages. Maintain local content-addressed tile bytes alongside the file
mapping, because uploaded source files cannot be downloaded through Files.

Proposed C2 context meter, an engineering estimate:

1. Count the unsigned opening or fresh epoch if its base64 projection fits
   strictly below 32 MB. B's `check_request` remains **inference-only**; write
   a private count-projection validator that allows native base64.
2. After a settled reply, anchor the meter to actual
   `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
   Keep actual output tokens separately.
3. Forecast the next input as that effective-input anchor plus the prior
   reply's actual output count plus an estimate for only the new tool/user
   content. Count the unsigned new text/images in bounded, self-contained
   probe messages when useful. Render tool-result text as ordinary probe text,
   preserving image bytes; never send an unmatched standalone `tool_result`.
   Wrapper/tool overhead makes this a forecast, not an exact full-history
   count. B's visual-token math is available for image components.
4. If an unsigned epoch itself exceeds the count payload limit, use separate
   bounded text/image probes and label their combined estimate. Do not
   translate that projection's size into an inference payload failure,
   mandatory renewal, or new spending rule. Never drop carried evidence.
5. Record method, anchor, new-content estimate, projection bytes, uncertainty,
   actual next usage and forecast residual. The 800K soft trigger uses the
   forecast. Provider rejection/context exhaustion ends as draft under D4;
   it is never automatically retried with a smaller output cap.

This avoids requiring a 60 MB base64 history merely to admit a small Files
request. The recorded rerun's second rebuilt request already exceeded 32 MB
with base64, so a full-count-before-every-inference design would be unusable.
The coordinator should review this estimated pressure policy before C2; it
does not change settled-only money admission or claim a token upper bound.

### Renewal algorithm

1. Stop before the pressured inference; no charge/turn is counted for that
   unsent attempt. Complete any pending tool-result append first.
2. Append the exact `renewal.SUMMARY_REQUEST` (`agent/renewal.py:18`–`:25`) to
   the old conversation and request a summary through the same ledger/client.
   Keep the same system/tools/configuration and auto choice so earlier thinking
   remains valid. The summary instruction says no tools; never execute tool
   calls from this special reply. If it nevertheless returns calls, append
   matching error results recording that the summary phase did not execute
   them before closing the old epoch. Preserve the whole reply but use only its
   public text for the new attributed summary. No forced-tool setting.
3. Skip a summary that is not admissible under hard limits or settled cutoff;
   record the explicit no-summary reason. Unknown charges/refusal/provider
   failure stop the run under D4/D7, not a free pass into another paid request.
   An empty/tool-only summary is recorded as unavailable, not fabricated.
4. Preserve old history hash; take `scene.renewal_evidence()`, then
   `scene.renewal_started()`, then `scene.renewal_state()` as the existing
   controller does (`agent/renewal.py:181`–`:198`). Reuse evidence selection:
   latest full comparison **group** per photo, all its native panels/details/
   side-light pages and current picks (`agent/job_scene.py:357`); specialist
   pending judgement and reading evidence (`agent/temple_lab/lab.py:1298`).
5. New opening order: exact brief/photos/notes via the planner, renewal notice,
   attributed Claude summary or explicit absence, host factual checkpoint/tool/
   finish/money state, verified current evidence converted via the same cache.
   All source pixels and all owner photos survive. No old signed thinking block
   is transplanted behind a new prefix.
6. Atomically insert new epoch items, active pointer and
   `g3d.conversation_renewal/1` receipt using `BlobSession.renew`. Export
   `renewals.json`, retaining old/new IDs, measurements/reasons, hashes, summary
   outcome, evidence hashes and host state. `renewal.durable_receipts` remains
   usable because the SQLite receipt table is unchanged.
7. Allow the first rebuilt request to bypass only the soft trigger once; hard
   body/image/context/money limits still apply. Record irreducible pressure and
   stop rather than loop through summaries indefinitely. A renewed main finish
   must obtain first-call review images again; the specialist retains its
   existing pending-judgement policy.

## 5. Exact instruction changes

Build the shared product instruction text first; apply an assertion-checked,
single-occurrence replacement table in the Claude path. Freeze original and
effective hashes. Fail preflight if an expected source fragment changes.
No general search-and-replace of owner descriptions, summaries or tool results.

| Current source and exact fragment | Claude-path replacement |
|---|---|
| `agent/instructions.py:41`: `You are Astra.` | `You are Claude.` |
| `agent/instructions.py:175`–`:176`: `Only status accepted completes the run; a refusal names the failed checks.` | `The run ends only when the finish tool returns status accepted; a text reply does not end it. A refusal names the failed checks.` |
| `agent/temple_lab/instructions.txt:1`: `You are Astra, the temple specialist.` | `You are Claude, the temple specialist.` |
| `agent/temple_lab/instructions.txt:171`–`:172`: `A refusal names unmet conditions or validation failure\nand the run continues. Only an accepted finish completes this specialist session.` | `A refusal names unmet conditions or validation failure\nand the run continues. This specialist session ends only when facet_finish returns status accepted; a text reply does not end it.` |
| `agent/renewal.py:26`: `Astra's own state summary from the previous conversation:\n` | `Claude's own state summary from the previous conversation:\n` |
| `agent/renewal.py:33`: `No Astra summary was obtained; continue from the host's factual state and the attached evidence.` | `No Claude summary was obtained; continue from the host's factual state and the attached evidence.` |
| `agent/loop.py:114`: `Your reply called no tool. The run ends only when ` + backticked `{tool}` + ` returns status accepted.` | M13 D7 literal: `Continue the task; the run ends only through the finish tool.` |

The specialist table's `\n` means an actual newline. Its text loader preserves
that newline (`agent/temple_lab/cli.py:751`), unlike the adjacent Python string
literals in the main instruction builder.

The generated product/specialist instruction text, brief and ordinary tool
descriptions contain no other OpenAI or forced-tool-choice sentence requiring
replacement. Forced choice is configuration at `agent/job_run.py:23`,
`agent/temple_lab/stage.py:25` and `agent/temple_lab/tools.py:345`, not a missing
prompt line. The Claude request uses auto choice; the original configuration and
Astra request construction stay unchanged. `agent/instructions.py:275` belongs
to owner-edit mode, outside M13; do not transform or expose it.

Keep every other instruction byte, including seeing guidance, shape/material
rules, finish-review procedure and all tool descriptions. Do not opportunistically
improve prompts or encourage new evidence behavior during the model-only test.

## 6. Run outputs, delivery and read-only review

Use the existing job directory structure inside the personal work root:

```text
work/jobs/<job-id>/
  job.json, images/, reference_images/       # immutable copied owner inputs
  inputs/seed.blend, checkpoints/, work/     # normal scene/tool artifacts
  blobs/
  session/session.sqlite                    # claude namespace; archived epochs
  budget/.job-ledger.lock                    # same locking convention, own accounting
  heartbeat.json, stop.requested
  runs/<run-id>/
    run-request.json, approval-binding.json
    approval-request.json, approval.json     # A's files; single use
    ledger.jsonl                            # A's sole settled/unknown money source
    run-config.json, input-receipt.json
    implementation.json, src.tar.gz
    transport/                              # A request/SSE receipts; assembled response JSON
    events.jsonl, reasoning-summaries.jsonl, transcript.md
    tools/NNNN-<tool>/                       # arguments/result/code/journal
    file-cache.json, image-manifest.jsonl
    renewals.json, stages/<stage>-<call-id>/
    result.json, delivery.json, summary.json, summary.md
    runner-status.json                      # if detached
    review/                                 # personal page, outside sealed delivery
  deliveries/<delivery-id>/, deliveries/current.json
  deliverable/                              # existing compatibility copy
  review/current.json, review/servers/       # native review binding/server state
  reviews/                                  # owner verdict ledger, when authorized
```

A's actual receipt filenames may differ within `transport/`; add separate
derived `NNNN-request.json`/`NNNN-response.json` only when needed, never overwrite
its authority. Preserve request body with file IDs plus an image-hash manifest,
raw SSE, assembled raw response JSON, usage, exact Decimal cost, HTTP status,
request ID, stop reason/details, timestamps and retries.
Record original/effective schema hashes and count results. Complete thinking
blocks/signatures live in private replay records; readable summaries go in the
existing reasoning-summary/transcript locations.

Keep `g3d.run_config/1`, `g3d.run_result/1`, `g3d.heartbeat/1` and the native
delivery/review/verdict schemas. Add `provider:"anthropic"` and exact `model` to
run request, approval binding, run config, result, request/response metadata,
stage result and summary. Config names the API/SDK version, max_tokens, max
effort, summarized thinking, effective tools, automatic cache TTL,
no fallback, one worker and zero SDK retries. Do not write `use_responses:true`.

Result retains `job_id`, `run_id`, `status`, `error`, `started`, `finished`,
`seconds`, `settings`, `requests`, `turns_used`, `tool_calls`, `finish_notes`,
`continuations`, `stages`, `renewals`, input counts, rollback/dangling evidence,
verified exit checkpoint, teardown/transcript errors, heartbeat stats,
`stop_observed`, and `transcript` (`agent/loop.py:1752`). `budget` contains A's
three exact money fields; unknown outcome is a separate explicit error/status,
not a guessed dollar amount. Populate `final_output` with accepted finish text
or the last **public** Claude text: delivery's fallback scanner reads only
OpenAI responses (`agent/delivery.py:928`). Include ledger path/hash and counts
separately; do not forge OpenAI `job_ledger` totals.

Summary also gives total elapsed time, model requests versus retry attempts/
counts/uploads, settled cost, finish/draft status, delivery ID/GLB hash, lens
material choices from actual tool evidence, image receipt counts, and cited
image references in public responses. Report the image-reference counting
method and uncertainty; receipt counts cannot prove what the model internally
looked at.

Finished delivery uses `deliver_finished`. Draft wrapper holds the same job
lock through `_draft_export` → `_draft_checks` → `_draft_capture` → `_package`
→ `_publish`, with existing refusal/failure records and no model call. Follow
`deliver_draft`'s behavior exactly except for its incompatible `JobLedger`
entry. Keep best-checkpoint selection scoped to this run; failed integrity can
select another verified checkpoint, a failed quality check cannot.

`_package` includes immutable model, report, checks, AR/contact/finish/look
images and SHA manifest (`agent/delivery.py:494`). Provider metadata is not
copied by that function. Do not add files inside a published delivery:
`load_delivery` rejects undeclared files (`:702`). Leave report/manifest bytes
and existing verifier unchanged. The personal `review` command calls
`load_delivery` and `review.review_job` unchanged, then creates a separate
provider-labelled page linking the verified AR URL, owner photos, comparison
evidence and run summary. `reviewed_delivery` and `append_verdict` still operate
on the native delivery identity. A C3 scripted run is labelled a tool test
outside the delivery and never records an invented owner verdict.

The dev UI's status reader consumes `runner-status.json`, `result.json`,
`heartbeat.json` and `delivery.json` (`glasses3d/dev_ui/status.py:100`, `:228`);
retain these shapes so its read-only readers can be reused. But:

- Runs are discovered through `registry.list("runs")`, not by scanning arbitrary
  work directories (`glasses3d/dev_ui/api.py:567`).
- `RunDTO` and delivery DTO omit provider/model (`dev_ui/status.py:234`,
  `dev_ui/api.py:231`); the current page cannot display those fields unchanged.
- Repointing a private `Config` can discover jobs/deliveries, but starting the
  stock server would also expose paid/edit routes. It is not a read-only adapter.

C2's personal wrapper therefore projects read-only history from the private
run manifests, reusing status/delivery readers and adding provider/model. It
must not register into, restart or alter the owner's 8260 service, nor repoint
its paid buttons. If Claude requires literal integration into that existing
page, that is a separate shared-source task; it is not silently claimed here.
No shared edit is required for M13's manual run and labelled review workflow.

## 7. C2 module contract

These six new modules are all under `claude_test/`; later task briefs assign
their tests. A/B files remain untouched. Types shown here are private to C2
unless imported explicitly. All output paths are confined under its work root.

### `runner.py`

```python
@dataclass(frozen=True)
class ClaudeSettings:
    model: str
    cutoff_usd: Decimal
    effort: str = "max"
    max_tokens: int = 128_000
    max_turns: int | None = None
    max_finish_notes: int = 2
    # frozen context/cache/image policy values from sections 3–4

@dataclass(frozen=True)
class ClaudeRunSpec:
    job_dir: Path
    run_id: str
    settings: ClaudeSettings
    instructions: str
    tools: tuple[FunctionTool, ...]
    brief: str
    photos: tuple[ReferenceInput, ...]
    request_sha256: str
    stage_contracts: tuple[dict, ...]
    env_file: Path | None
    offline: bool

def prepare_run(job_dir: Path, *, settings: ClaudeSettings,
                run_id: str | None = None, env_file: Path | None = None,
                write_request: bool = False, offline: bool = False
                ) -> tuple[ClaudeRunSpec, JobScene]: ...

def approve_run(spec: ClaudeRunSpec, *, words: str) -> dict: ...
async def run_claude(spec: ClaudeRunSpec, *, scene: SceneHooks | None = None,
                     inner: httpx.BaseTransport | None = None) -> dict: ...
async def execute_run(spec: ClaudeRunSpec, *, scene: SceneHooks | None = None,
                      inner: httpx.BaseTransport | None = None) -> dict: ...
# execute_run returns {"result": ..., "delivery": ...}; handles interruptions too.
def publish_result(job_dir: Path, scene: SceneHooks, result: dict) -> dict: ...

class ClaudeRunStatus:
    ledger: Ledger
    guard: ToolMoneyView
    runtime: ClaudeStageRuntime | None
    def numbers(self) -> dict: ...  # exactly A's three money facts
    def footer(self) -> str: ...
    async def run_stage(self, spec: StageSpec, scene: SceneHooks,
                        cap_usd: Decimal, call_id: str) -> dict: ...

class ClaudeStageRuntime:
    async def run_stage(self, spec: StageSpec, scene: SceneHooks,
                        cap_usd: Decimal, call_id: str) -> dict: ...
    async def close(self) -> None: ...
```

`prepare_run` is network-free and does not load credentials/start Blender.
`approve_run` is called only with explicit authorization; fake mode creates
unspendable approvals. `run_claude` owns locks/lifecycle, uses A's transport and
ledger, and always persists a result after consumption. Client construction
uses the given env-file path only at authorized production execution; tests
refuse any real-key lookup. A's transport owns settlement, never the SDK loop.
For this task sequence, require `model == "claude-opus-5-5"` and max effort.
Retaining Fable's frozen tariff entry does not authorize a Fable run or
Fable-specific implementation work.

### `tool_adapter.py`

```python
def claude_instructions(text: str, *, specialist: bool = False) -> str: ...
def tool_definitions(tools: Sequence[FunctionTool], *, specialist: bool = False
                     ) -> list[dict]: ...
def effective_registry_sha256(definitions: Sequence[dict]) -> str: ...

class ToolAdapter:
    def __init__(self, tools, *, scene, status, log, files: FileCache,
                 image_manifest: Path, specialist: bool = False): ...
    async def invoke(self, block: dict) -> dict: ...  # complete tool_result block
    def accepted_finish(self, name: str, raw_output: Any) -> bool: ...
    def expire_image_dedup(self) -> None: ...
    async def call_scene(self, ctx, name: str, operation: Callable) -> Any: ...
    # call_scene preserves shared lock/thread/journal/image/bounded-result behavior

def prepare_images(raw_output: Any, *, files: FileCache, metadata: dict
                   ) -> tuple[list[dict], list[dict]]: ...
# ordered content blocks + source/tile/file provenance; B has no prepare() export
```

`prepare_images` calls B's `tile`, `FileCache.file_id_for`, `label_block` and
`image_block`. Its SDK uploader is injected from runner and uses the same
guarded client with seven-day expiry; B imports no SDK. All pre-existing tools retain real
validation/execution; the sole callable replacement is temple money entry.

### `conversation.py`

```python
class Conversation:
    def __init__(self, db_path: Path, *, blobs: BlobStore,
                 root_id: str = "claude"): ...
    async def messages(self) -> list[dict]: ...
    async def append(self, message: dict) -> None: ...
    async def assert_request_prefix(self, body: dict) -> dict: ...
    async def renew(self, messages: list[dict], *, receipt: dict) -> str: ...
    async def recovery_results(self) -> list[dict]: ...
    def close(self) -> None: ...

def opening_content(spec: ClaudeRunSpec, *, files: FileCache
                    ) -> tuple[list[dict], dict]: ...
def request_body(spec: ClaudeRunSpec, messages: list[dict], *,
                 tools: list[dict]) -> dict: ...
def pressure(body: dict, *, dims: dict[str, tuple[int, int]],
             measurement: dict, settings: ClaudeSettings) -> list[dict]: ...
def count_probes(unsigned_content: list[dict], *, tile_store: Path,
                 spec: ClaudeRunSpec) -> list[dict]: ...
def context_measurement(*, last_usage: dict | None,
                        opening_count: dict | None,
                        appended_estimate: dict) -> dict: ...
async def renew_conversation(runtime, trigger: dict) -> dict: ...

class ClaudeReviewLog(ReviewLog):
    def record_message(self, *, request_no: int, request_id: str | None,
                       raw_response: dict, transport_receipt: Path) -> None: ...
    def publish_money(self, facts: dict) -> None: ...
```

Store raw JSON before deriving display records. Wrap, do not replace,
`BlobSession`'s atomic epoch transaction. The `runtime` passed to renewal
supplies its scene, conversation, spec, file cache, guarded request operation,
ledger and log; summary requests use that same operation/counter.

### `scripted.py`

```python
@dataclass(frozen=True)
class ScriptTurn:
    content: list[dict]             # complete fake assistant blocks
    stop_reason: str = "tool_use"
    usage: dict | None = None
    http_status: int = 200
    headers: dict | None = None
    context_management: dict | None = None

class ScriptedClaude:
    def __init__(self, turns: Sequence[ScriptTurn | Callable], *,
                 token_counts: Sequence[dict] = ()): ...
    def __call__(self, request: httpx.Request) -> httpx.Response: ...
    def transport(self) -> httpx.MockTransport: ...
    def assert_complete(self) -> dict: ...

def sse_response(turn: ScriptTurn, *, request_id: str, model: str,
                 message_id: str) -> httpx.Response: ...
```

Callbacks consume actual preceding request/tool results and return a turn, so
proofs never invent checkpoint/review IDs. Fake uploads retain/verify bytes
locally and assign stable fake IDs; fake counts are explicitly scripted or
local estimates. No network fallback if the script is exhausted.

### `cli.py` and `__main__.py`

```python
def build_parser() -> argparse.ArgumentParser: ...
def main(argv: Sequence[str] | None = None) -> int: ...
def review_run(job_dir: Path, run_id: str, *, port: int = 0) -> dict: ...
def history(work_root: Path) -> list[dict]: ...
# __main__.py: raise SystemExit(cli.main())
```

Commands: `prepare` (fresh private input copy/request; no approval),
`approve` (explicit words/model/cutoff), `run` (consumed approval, no defaults
imply authorization), `scripted` (offline fixture only), `status`, `stop`,
`review`, `history`. `--env-file` is a path and never echoed with contents.
Live upload/count commands are C4 work, not an implicit part of `prepare`.
UTF-8 stdout/stderr and truthful final exit status follow the existing CLI
pattern; planning/approval/run failures stay distinguishable.

## 8. C3 scripted Claude proof

### Real SDK and fake wire

Use the pinned official SDK, A's actual `guarded_client`, and an
`httpx.MockTransport` as its `inner`. Do not mock `messages.create/stream`,
settlement, tiling, tool execution, worker output or delivery.
Construct real `text/event-stream` responses: `message_start`, indexed
`content_block_start`, thinking/text/tool-input deltas, `signature_delta`,
`content_block_stop`, cumulative `message_delta` usage/stop reason and
`message_stop`. Include pings and split JSON/string chunks across reads.
The [streaming protocol](https://platform.claude.com/docs/en/build-with-claude/streaming)
was checked 2026-10-20. Signatures are clearly synthetic opaque test strings,
preserved exactly; this proves round-tripping, not server cryptographic validity.

Support before-body 429/529 plus Retry-After, refusal, text-only end_turn,
unknown usage/model, partial stream and error-event scripts. Fake Files/count
handlers implement only R's SDK-observed allowlisted paths. Check every method,
host, path, query and body; unknown endpoints fail immediately. F1 also computes
fake usage from the deterministic cache model and reconciles the guarded ledger
exactly. This tests accounting, not real provider caching or image interpretation.
Disable production HTTP paths and DNS outside
loopback, covering both httpx and httpx2. No owner key is loaded.

### Native Oakley scenario

Create `work/c3/<proof-id>/jobs/oakley-oo9208-v2-911a2b0e` from only verified
owner manifest/photos. Record pre/post hashes of original inputs and selected
source checkpoint. For this **tool test only**, copy the accepted rerun's
verified checkpoint into the copy's `work/` and initialize
`JobScene(captured_seed=..., captured_fingerprint=...)`; constructor confinement
is at `agent/job_scene.py:110`. Verify the source checkpoint's recorded SHA
before copying, verify the copied bytes, then explicitly assert the started
scene's head fingerprint equals the source fingerprint **before any tool**.
For an ordinary job, merely passing `captured_fingerprint` does not perform
this check: the built-in start check at `:139` is conditional on owner-edit mode.
No absolute-path scene state/session is copied.
C5 still starts empty. This avoids re-authoring a product merely to exercise
protocol plumbing.

Drive these real tools with scripted replies:

1. `scene_summary`, read all six photos with `read_image`, `budget_status`, and
   `save_checkpoint(best=True)`. Record actual returned IDs/fingerprint.
2. `fit_cameras` on the private copy, then `render_views` and
   `compare_to_photos`. Use actual frozen basis IDs.
3. `execute_blender_code` for one generic reversible frame-material roughness
   change selected from actual scene metadata; save a checkpoint and run
   `compare_look_to_photos`. At least one real panel must require B's tiling;
   assert split/reconstruction rather than relying solely on a synthetic image.
4. Repeat a comparison while retaining earlier evidence. Assert all requests
   omit context editing and keep automatic top-level one-hour caching.
5. Force one renewal with deterministic fake count pressure (test-only
   configuration), a scripted state summary and real current evidence. Verify
   both epoch records, all six photos, pixel/file hashes and finish-reset state.
6. `restore_checkpoint` to the actual saved source, refit only if its basis is
   invalid, `export_glb`, `arview_capture`, `compare_look_to_photos`.
7. First `finish` with no fabricated review; extract its real review call ID and
   required refs, using `read_report` if paged. Second `finish` supplies one
   per-photo item for every required ref and an explicit scripted tool-test
   statement. It certifies procedure/checks, not automated likeness.
8. Publish through the real immutable delivery helpers, validate it with
   `load_delivery`, launch review on an ephemeral private port, fetch its
   bound AR report/model and personal provider-labelled page, verify the
   delivery manifest locally, then stop only
   the proof's owned server. Do not write an owner acceptance.

Explicitly invoke the adapted `temple_stage` entry in C3. If Oakley's
multi-part temples refuse it, that is the correct scope and must not change.
Also drive that replacement entry through a controlled child/bridge fixture,
covering exact Decimal left below one micro-dollar, settled cutoff, unknown
charges, callback routing and fatal errors. A small no-Blender runtime fixture
proves shared ledger/model and stage namespace behavior; use an existing
eligible specialist fixture for real adapter invocation where necessary.
Do not force a specialist geometry success onto Oakley.

### Required assertions and negative scenarios

- Exact prior Messages prefixes and immutable thinking/signature blocks,
  with no server context edits; separate append-only renewal epochs,
  atomic pointer/receipt recovery, exact tool-use/result IDs and ordering.
- Original and emitted/tiled pixels remain exact; no inline inference images; all
  dimensions/visual-token counts valid; <=600 image occurrences; serialized
  body <32 MB on every inference and count request. Upload once per SHA with
  seven-day expiry. Count probes may use base64, inference never does.
  A >32 MB full-count projection does not block a valid Files request;
  the forecast/anchor and its uncertainty are recorded instead.
- Original tool names/descriptions/schemas unchanged; no wire declaration has
  `strict`; original JSON Schema, Pydantic and domain refusals still work,
  including missing required/default-valued properties and extra keys. Timeout and fatal
  worker semantics preserved; no tool invocation on truncated input.
- Two-call finish refuses a wrong review ID/missing photo, unseen images or
  changed checkpoint. A successful second finish needs no extra model reply.
- A synthetic usage sequence begins below cutoff, crosses it in flight, and
  blocks the next request exactly. A valid finish in that crossing response
  can complete. Repeat the stop with a child sharing the same A ledger.
- Unknown nonzero usage, missing usage, served-model mismatch, SSE error and
  truncated SSE each persist unknown and stop; no billed-response retry.
  429/529 waits with `retry-after` are logged with identical resent body and
  bounded total wait, using injected sleep to avoid real delays. Missing-header
  cases stop under updated D4. Refusal costs carry their upper-bound qualifier.
- Text-only replies use exactly the fixed note/count; refusal produces an
  honestly labelled draft; malformed/unknown endpoints are never forwarded.
  An unexpected multi-tool reply is serialized into one result message and a
  mutation after an earlier accepted finish prevents completion.
- Crash between worker mutation/result persistence yields the original
  rollback barrier; failure after first finish image conversion cannot accept
  the second finish. Stop during model wait/tool/child teardown leaves no
  ambiguous charge silently cleared.
- Native delivery checks/hashes and review binding pass; draft failures are
  reported rather than hidden; no original job/model/approval/verdict changed.
  Actual provider requests and spend are zero.

Follow the audit structure in `glasses3d/tests/proofs/assembly_m1.py:111`,
and its actual tool sequence at `:281`; its Responses receipt parser is not a
Claude audit. Recent M1 evidence is 163.031 seconds
(`glasses3d/data/m12/codex/e1_report.md:31`). Planning estimate for this fuller
native Oakley proof is **5–15 minutes**, an inference awaiting C3 measurement;
camera fitting and native comparisons dominate. Unit protocol cases should be
seconds to a few minutes. Do not raise any real tool timeout to fit this estimate.

C3 then runs the required full suite and `assembly_m1` separately, once the
coordinator has ended parallel heavy work, per `tests/README.md`. Record commands,
PIDs/times/exits and source hashes. The full-suite/M1 requirement belongs to C3,
not C0 or the concurrent C1 tasks. M1 is a regression check, not by itself proof
of identical Astra request bytes: also construct a fixed offline Astra opening/
tool-followup with original and current tracked source and compare canonical
request bytes under the same deterministic IDs/settings. No shared edit and
no monkeypatch/import side effect should change either request.

## 9. Ranked risks and questions for Claude

1. **Current plan versus frozen brief needs coordinator reconciliation.**
   Current D4 retries only with `retry-after`, prices refusals as an explicitly
   labelled upper bound, and requires standard/global responses. The older
   common brief gives a 30-second no-header fallback. A must align its policy;
   C2 must not bypass transport or repair its files. Current D7 omits all strict
   flags; D8 sets seven-day expiry. Verify the pinned SDK against FACTS.
   C4 counts base64 opening projections and cannot validate file access,
   strict compilation or live caching. No paid probe is authorized.
2. **Approval/accounting adapters are mandatory.** A's approval API does not
   bind the whole job request; use the separate execution seal. OpenAI
   `JobLedger`, `BudgetGuard`, result cleaner and `deliver_draft` cannot be
   imported wholesale. The design above supplies the missing bindings, exact
   Decimal semantics and lock-preserving draft wrapper.
3. **Signed history has two easy corruption paths.** Cleaner redacts signatures,
   and changing earlier request blocks/cache markers can invalidate them.
   Persist raw blocks separately and check every request prefix. A fake
   signature test proves preservation only.
4. **A fresh renewal needs a dedup reset.** Otherwise the shared tool
   returns “already delivered” text for evidence left in the old conversation.
   Expire only the emission cache; do not rewrite history or falsify unseen
   journals. Upload failures must invalidate an actual unreceived finish.
5. **Non-strict tools require complete local validation.** All declarations
   omit `strict`. Pydantic alone does not enforce every original advertised
   requirement. Validate the complete original JSON Schema, then the existing
   tool/domain checks; retain all physical gates.
6. **Context pressure is estimated after signed turns.** Counting does not
   accept file IDs; converting past images may invalidate thinking, and
   base64 histories can exceed 32 MB. Section 4 proposes usage-anchored growth
   estimates without changing inference history. F1 keeps these soft thresholds.
   C3/C4 cannot prove real server cache behavior. A huge actual result or irreducible carried evidence
   can still require a draft.
7. **Specialist concurrency/cancellation can leak work.** Retain StageBridge's
   true unwind and one shared ledger/client; never substitute a child OpenAI
   runner or release locks while a sync streaming thread is alive.
8. **Review scope must be stated honestly.** Proposed default is the personal,
   provider-labelled read-only history/review wrapper. Literal integration into
   the owner's existing 8260 history is not available unchanged and is outside
   these task-owned files. Claude should dispatch it separately only if needed.
9. **Proof vs product evidence.** C3 starts from a copied accepted seed for a
   tool test; C5 starts empty. A scripted successful finish is not a new owner
   product verdict, and one paid sample cannot rank providers generally.
10. **Interface wording discrepancy.** Implement preparation in C2's adapter
    using B's `tile`/cache/block functions; do not invent a frozen
    `images.prepare` API. The source line references/private helper dependencies
    should be checked again after R/A/B finish, before C2 implementation.

C0 executes no test suite, Blender job, upload/count call or paid call. This
document and its short report are its only outputs. The coordinator can now
review the identified adapters and dispatch C2/C3 with concrete file ownership.
