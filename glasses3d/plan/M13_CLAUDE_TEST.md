# M13: one Oakley run driven by the Claude API (written 2026-10-20 by Claude)

Claude plans and reviews; Codex builds from saved briefs in `claude_test/work/briefs/` with reports in
`claude_test/work/codex/`. Every step is $0 until step C5, which needs the owner's explicit OK, model choice and cutoff.

## 1. The owner's words (verbatim, 2026-10-20)
- "i want to test something, tell me your thoughts. i want to do an oakley run with claude api, would the test be hard
  to create? are there a lot of stuff to change for testing with claude instead of open ai agent api?"
- "look, i want the agent to have what it needs (always reference images, relevant edits and close ups not in shit
  res) do you think thats possible?"
- "the entire agent session needs to be under 32mb? isnt it very low? +youre right we do need to encourage models to
  look at the images it gets."
- "ok lets do this."
- "add to notes that we must encourage more the models to look at the images they get"
- "after we compact ill ask you to commit the automation to github and then give me the claude test building plan
  (you plan and workflow with codex)."

## 2. What the test is
The job is the same one used for the accepted max-effort rerun: `oakley-oo9208-v2-911a2b0e`. It holds the six owner
photos, the owner's description, empty specs and the view/side labels, and nothing is added
(`AGENTS.md`, "Jobs hold only the owner's inputs"). The test keeps the same Blender worker, tools, specialist stages,
instruction content, finish, delivery and review page. Only the model changes.

The owner judges the result by eye beside the photos, Oakley run 4 (GLB `98131458…`) and the rerun (GLB `ba6216a4…`,
whose lens colour he called worse than run 4's).

A single run is a single sample. Run-to-run variation is not measured for either provider, so one good or bad run
does not settle which model is better.

Out of scope:
- owner edit mode (M6–M10);
- the dev UI's paid buttons, which stay on Astra;
- every Astra code path. Astra's behaviour and its tests must stay unchanged.

## 3. Decisions (Claude's, technical; the owner may override any)

**D1. Model.** The runner supports both Claude models below; the owner picks one at C5. Both have a 1M-token context
and 128K-token output, adaptive thinking that is always on, and forced `tool_choice` returning 400.
- `claude-opus-5-5`: $4 input / $20 output per MTok; cache reads $0.20.
- `claude-fable-5-1`: $10 / $50; cache reads $0.25. It is Anthropic's most capable widely released model, with stronger
  vision and long-horizon agent work, and costs the same per token as gpt-6-astra.

**Owner decision, 2026-10-20 (verbatim): "for now were set on opus 5.5 with maximum possible effort".** The test model
is `claude-opus-5-5` with `output_config.effort: "max"`, the top level. That replaces Claude's earlier Fable 5.1
recommendation. Tests and the C3/C4 checks target Opus 5.5. The Fable 5.1 tariff may stay in the price table, but no
work goes into Fable-only features. C1 re-checks every figure on the official pages, with URLs and dates in PROGRESS.

**D2. Pure model swap first.** The first paid Claude run uses exactly the tools and evidence Astra had, so any
difference can be put down to the model. The owner's standing rule "we must encourage more the models to look at the
images they get" (the evidence-push design in memory/PROGRESS: automatic before/after close-ups, cited per-photo checks)
becomes M14. M14 is built once for both providers, after this run. The tile splitting in D5 is not M14: Claude's image
limits require it, so the model gets the same pixels Astra gets.

**D3. Code layout: one folder for the whole test** (owner, 2026-10-20: "i want you and codex to work in a directory
which is personal for this test").
- `glasses3d/claude_test/` holds all new code, its tests, `README.md`, `DESIGN.md` and `FACTS.md`.
- `glasses3d/claude_test/work/` (git-ignored) holds briefs, Codex reports, the job copy, runs and receipts.
- `glasses3d/claude_test/.venv` is the test's own venv: the glasses3d pins plus the official `anthropic` Python SDK,
  pinned to an exact version with its sha256. The main venv, the dev UI and Astra runs are not touched.
- `glasses3d/claude_test/.env` (git-ignored) is the owner's key file.
- The runner is a manual loop on the Messages API with streaming, started as `python -m glasses3d.claude_test`.
- An adapter turns the existing tool registry (each FunctionTool's `params_json_schema` and `on_invoke_tool`, text and
  image outputs) into Claude tools and `tool_result` blocks.
- Existing glasses3d modules are imported, not edited. A shared edit happens only if `DESIGN.md` proves it necessary,
  and Astra's request bytes stay identical.
- The instruction text is shared. The Claude path changes only the model's name and the sentences about how the run ends
  (finish tool, no forced tool choice). Codex lists every changed line.

**D4. Money: the owner's rules and nothing added.**
- Settled-only: each finished reply is priced from its own `usage` at the official tariff.
- No new request starts once the settled total reaches the owner's cutoff; the reply in flight may cross it.
- Spending stops on any usage the code cannot price: unknown fields, a different served model, a service tier, a
  region multiplier, or a missing usage block.
- The SDK's `max_retries` is 0. HTTP 429 and 529 responses, which are refused before any work and not billed, are sent
  again after the server's wait. Every other error stops the run as a draft, as Astra's does.
- `max_tokens` is the model maximum, 128,000; there is no output shrinking.
- The model's money status shows only settled spend, the cutoff and money left, as Astra's does.
- The HTTP guard allows only `api.anthropic.com`, on `POST /v1/messages`, `POST /v1/messages/count_tokens` and the Files
  API endpoints. Everything else is refused, mirroring `budget.py`'s OpenAI-only guard.
- One ledger serves the parent run and every specialist.
- The single-use approval records the owner's chat words and amount in the existing approval format.

**D5. Images: what Shay asked for ("always reference images, relevant edits and close ups not in shit res").**
- The owner photos go whole, at native pixels, in the opening message, and are never cleared. Oakley's are 1920×1280 at
  most, which is about 3,200 visual tokens, under the limits.
- Every other image must be no larger than 2,000 px per side (required once a request holds more than 20 images) and
  4,784 visual tokens (ceil(w/28)·ceil(h/28)). A larger image is split into labelled native tiles and never downscaled.
  Today's widest comparison panels are about 2,924×864 and become two tiles.
- If the API offers a reject-instead-of-resize option (`oversized_image`), C1 verifies it and the runner uses it, so the
  server can never shrink silently.
- Each image is uploaded once through the Files API (sha256 → `file_id`), so requests stay far below the 32 MB request
  limit however many images a run collects. 32 MB is a per-request limit, not a per-session one.
- Before every request the runner checks: at most 600 images, under 32 MB, and every image within the limits above.

**D6. Long conversations.**
- **Primary: server-side context editing** (`clear_tool_uses_20250919`). Old tool results, images included, are cleared
  in large batches, because each clearing breaks the cache once.
  - It starts when input passes about 350K tokens, clears at least 150K, and keeps the 8 most recent tool results.
  - Codex sets the final numbers from the rerun's request profile and records them.
  - The opening message is never cleared, and the model can re-run a tool to get an old view back.
- **Backstop: the existing renewal** (`agent/renewal.py`). It starts a fresh conversation carrying the model's state
  summary, every owner photo and the current evidence. Astra renewed twice in the rerun. Server compaction is not used.
- Context editing and renewal are not history edits, so preserved thinking stays valid. C1 confirms both features on
  both models.
- **Caching:** automatic prompt caching with the 1-hour TTL, because Blender tools can run longer than 5 minutes.
  Receipts show `cache_read_input_tokens` for every request.

**D7. Thinking and tool calls.**
- Thinking uses `display: "summarized"`, so the summaries land in receipts like Astra's reasoning summaries.
- Thinking blocks are passed back unchanged, and the history is append-only. The runner asserts the previous request is
  a prefix of the next one.
- `tool_choice` is `auto` with parallel tool use disabled (one Blender worker, as for Astra). Tools use `strict` schemas
  where the API accepts them; the adapter otherwise validates inputs itself, as today.
- A reply with no tool call gets one fixed host line, and receipts count those lines. The line is "Continue the task;
  the run ends only through the finish tool."
- A `refusal` stops the run as a draft and is reported. Server-side fallbacks stay off: a fallback would switch models
  mid-test.

**D8. Receipts and delivery.**
- The run directory, receipts, ledger, delivery, `job review` and the owner-verdict files keep their layouts.
- Each request receipt holds the request with images as file ids plus sha256, the response JSON, usage, priced cost,
  the `request-id` and the stop reason.
- The run summary and the review page show provider and model.
- Uploaded copies stay in the owner's Anthropic account until deleted. Claude asks him after the run.

**D9. The key.**
- The owner makes an API key at platform.claude.com, buys prepaid credits at least as large as his cutoff, and adds
  `ANTHROPIC_API_KEY=…` to `glasses3d/claude_test/.env` himself (Claude created the empty file 2026-10-20). A Claude Max plan does not cover API use.
- Agents never read the file; the runner gets its path only (`--env-file`).
- Rate limits are settled: the owner's Console (2026-10-20, his screenshot) shows these for Claude Opus 5.5:
  - 5.0M input tokens, not counting cache reads;
  - 1.0M output tokens;
  - 5.0K requests.

  These are read as per-minute. They are far above any single request (at most the 1M context), so no extra credit is
  needed. His balance is $24.74 in credits plus $200 in promotional credits.

## 4. Steps (Claude reviews every report and diff and reruns the tests)
- **C1, foundations ($0): four Codex tasks in parallel, with disjoint files.** Owner: "please max utilize this machine
  for fastest runs". The contract lives in `work/briefs/m13_common.md`.
  - **R, facts:** collect the official facts with URLs and dates in `FACTS.md`. They cover prices (including 5-minute
    and 1-hour cache writes and any long-context tier), rate-limit tiers, image limits, `oversized_image`, context
    editing, the Files API, effort levels, thinking display and usage fields, for both models.
  - **A, money:** create the venv and pin the SDK, then write `pricing.py`, `money.py` (guard, ledger, approval, 429/529
    resend, receipts at the transport) and their tests on a fake HTTP transport.
  - **B, images:** write `images.py` (native tiling, visual-token math, upload cache, request checks) and its tests.
  - **C0, design:** survey `run_astra` and the modules it composes, then write `DESIGN.md`, the reuse map and the
    runner's contract.
- **C2, runner ($0).**
  - `claude_test/runner.py`: the loop, tool adapter, context editing and the renewal backstop.
  - The `python -m glasses3d.claude_test` CLI.
  - A scripted-Claude transport: a fake Messages API behind the real SDK, replaying scripted replies with thinking and
    `tool_use` blocks.
- **C3, $0 proof.** On a private copy of the Oakley job, scripted Claude drives the real Blender tools end to end:
  edits, tiled comparisons, a context-editing event, a renewal, finish, delivery and the review page. It asserts:
  - append-only requests and thinking passed back unchanged;
  - no image over the limits and every request under 32 MB;
  - a stop at the cutoff (the reply in flight may cross);
  - a stop on unknown usage;
  - correct 429 handling;
  - no other endpoint called.

  Then the full suite and `assembly_m1`, which proves the Astra path is unchanged.
- **C4, $0 live check (needs the owner's key and OK; no model reply, no charge).**
  - Upload the six photos, then call `count_tokens` on the real first request and on a large late-run request from C3.
    This checks the tool schemas, file references and image sizes on the real API.
  - Estimate: the rerun's request profile priced at each model's tariff, with the input sizes taken from those token
    counts.
- **C5, paid run (the owner's OK, model and cutoff).** Claude prepares and verifies the request, then starts it
  detached (EcoQoS opt-out). Live status shows settled spend, cutoff and money left.
- **C6, review.** The review page beside the photos and the run 4 / rerun renders. The report gives:
  - cost, requests and time;
  - the lens material it chose;
  - how many of the images it received the model actually referred to.

  The owner's eye decides.
- **Later, M14 (separate):** the evidence push for both models (memory `make-models-look-at-images`).

## 5. Working rules for these tasks
- Codex is run as `codex exec -s danger-full-access -C C:/Users/Shay/PycharmProjects/lenses` with the brief pointer.
  Capacity errors are resumed with `data/review_20261018/codex/resume_retry.sh`.
- Use all cores within the memory rule; never run two pool-heavy jobs at once. Owner, 2026-10-20: "claude desktop app
  will be the only app which is open while you run so please max utilize this machine for fastest runs."
- Parallel tasks do not commit. Claude reviews each task and commits its files with explicit paths.
- Commits use explicit paths on `glasses3d`. No push without the owner's word: the branch is public on GitHub since
  2026-10-20.
- Never read `.env`. Never make a paid call.
- C4's `count_tokens` and Files uploads are free, and the owner cleared them on 2026-10-20: "i thought step 4 is free
  why do you need me to okay the images? arent this the products images?". C4 therefore runs as soon as C3 passes. He
  added the key the same day ("ive added the key to .env").
