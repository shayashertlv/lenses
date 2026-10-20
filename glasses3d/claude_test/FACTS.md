# M13 Claude API facts

Checked **2026-10-20** for the direct Claude Messages API, specifically `claude-opus-5-5` and
`claude-fable-5-1`. Every citation below was checked on that date; dates beside citations apply to
the preceding paragraph/table. This is documentation research, not an account-access or live-API
proof. No credentials, photos or repository content were sent to a provider.

The principal corrections are refusal-aware settlement, a counting path that cannot use file
references, and fresh-conversation renewal that must omit old bound thinking. Several requested
billing guarantees are **not documented** in the permitted sources. “Unsupported” below means
not established by those sources, not proved false. Local implementation choices are labelled.

The requested [Anthropic pricing URL](https://www.anthropic.com/pricing) redirects to
`claude.com/pricing`; its destination was not used because the brief limits sources to
`platform.claude.com` documentation and `anthropic.com` pricing. Platform pricing supplies the
tariffs below. Checked 2026-10-20.

## 1. Models, prices and billing

| Direct API model | Context | Maximum Messages output | Default effort |
|---|---:|---:|---|
| `claude-opus-5-5` | 1M tokens | 128,000 tokens | `medium` |
| `claude-fable-5-1` | 1M tokens | 128,000 tokens | `high` |

Both accept images and tools and have adaptive thinking always on.
[Models overview](https://platform.claude.com/docs/en/models/overview), checked 2026-10-20.

Anthropic positions Fable 5.1 as its most capable model available to all customers. Its
documented vision improvements compare it with Fable 5, not with Opus 5.5 or Astra on eyewear.
[Choosing a model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model) and
[Fable 5.1 improvements](https://platform.claude.com/docs/en/models/fable-5-1/whats-new-fable-5-1),
checked 2026-10-20.

USD per million tokens, standard speed, global inference:

| Model | Uncached input | Output | Cache read | Cache write, 5m | Cache write, 1h |
|---|---:|---:|---:|---:|---:|
| Opus 5.5 | $4 | $20 | $0.20 | $5 | $8 |
| Fable 5.1 | $10 | $50 | $0.25 | $12.50 | $20 |

Neither has a long-context price step above 200K: standard rates cover the full 1M context.
US inference (`inference_geo: "us"`) costs 1.1 times every token category. Cache writes cost
1.25 times base input for 5m and twice base input for 1h; cache reads are model-specific.
Batch input/output rates are half the standard rates.
[Pricing](https://platform.claude.com/docs/en/about-claude/pricing), checked 2026-10-20.

Opus 5.5 fast mode (`speed: "fast"`) is generally available without a beta header: $8 input /
$40 output. Applying its documented cache multipliers gives $0.40 read / $10 5m write /
$16 1h write (calculated). Residency charges apply additionally. Fable 5.1 does not support
fast mode. Fast mode has separate rate limits; `usage.speed` identifies the actual mode.
[Fast mode](https://platform.claude.com/docs/en/build-with-claude/fast-mode), checked 2026-10-20.

Neither candidate supports Priority Tier. New Priority commitments are unavailable; existing
contracts continue until expiry. Request `service_tier` accepts `auto` (default) or
`standard_only`. **M13 implementation recommendation:** send `standard_only`, standard speed
and explicit global inference; do not rely on account defaults.
[Service tiers](https://platform.claude.com/docs/en/api/service-tiers) and
[Messages request schema](https://platform.claude.com/docs/en/api/messages/create), checked 2026-10-20.

Thinking is billed at the output rate, even when hidden or summarized. The charged count is
the generated reasoning, not the displayed summary's length. It consumes `max_tokens`.
Preserved thinking becomes input on later requests.
[Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking), checked 2026-10-20.

Refusal billing depends on when the refusal happened and `stop_details.category`:

| Refusal | Billing |
|---|---|
| Before output; `bio`, `frontier_llm`, `reasoning_extraction` | Normal model rates |
| Before output; other category or null | No charge, although `usage` still reports tokens |
| During streaming after output | Input and output already streamed are billed |

Refusals still consume rate limits. **Consequence:** a function receiving only `model, usage`
cannot always determine the settled bill. The transport also needs refusal category and
whether output preceded it; otherwise the charge is unknown.
[Refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback),
checked 2026-10-20.

**Not established:** the exact bill after a client cancels a Messages stream; ordinary
mid-stream SSE errors; a blanket guarantee that initial HTTP 429/529 responses are unbilled.
The pricing, error and streaming pages reviewed do not state these guarantees. A mid-stream
refusal is a separate, documented case above. Missing final usage/error/cancellation must
not be represented as a confirmed zero bill.
[Errors](https://platform.claude.com/docs/en/api/errors),
[Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming),
[Pricing](https://platform.claude.com/docs/en/about-claude/pricing), checked 2026-10-20.

## 2. Final usage and settlement

This inventory covers the current standard and beta response schemas, including fields M13
does not intend to activate. Nullable fields are not guarantees of presence. Token counters
are nonnegative. A new field still requires review.

| `usage` field | Cost significance |
|---|---|
| `input_tokens` | Uncached input |
| `output_tokens` | Inclusive billed output |
| `cache_creation_input_tokens` | Total cache writes; do not also charge the TTL breakdown |
| `cache_read_input_tokens` | Cached input |
| `cache_creation.ephemeral_5m_input_tokens` | Writes at 5m tariff |
| `cache_creation.ephemeral_1h_input_tokens` | Writes at 1h tariff |
| `output_tokens_details.thinking_tokens` | Informational subset of output; never add again |
| `service_tier` | `standard`, `priority`, `batch`; pricing selector |
| `speed` | `standard`, `fast`; pricing selector |
| `inference_geo` | Served region; possible multiplier |
| `server_tool_use.web_search_requests` | Additional tool charges |
| `server_tool_use.web_fetch_requests` | Tool count; fetched content costs input |
| `iterations` | Per-iteration/model counters; see below |
| `fallback_credit.status` | Credit/repricing outcome; see below |

The table enumerates all twelve top-level keys (nested keys expanded).
[Standard response](https://platform.claude.com/docs/en/api/messages/create) and
[Beta response schema](https://platform.claude.com/docs/en/api/cli/beta/messages/create),
checked 2026-10-20.

`iterations` entries have `type`, `input_tokens`, `output_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`, and `cache_creation` with the same
two TTL counters. Types are `message`, `compaction`, `advisor_message`, `fallback_message`;
all except `compaction` also have `model` (nullable on `message`). A compaction entry measures
the summarization operation, not the removed context, and is excluded from top-level totals.
Do not add duplicated message totals to their breakdown.
[Expanded beta schema](https://platform.claude.com/docs/en/api/beta/messages/create),
checked 2026-10-20.

Compaction itself is separately billable and rate-limited. With threshold compaction,
use all iteration charges without adding duplicated top-level totals. Reusing an existing
compaction block does not trigger another summarization charge.
[Threshold compaction](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold),
checked 2026-10-20.

`fallback_credit.status.type` is `redeemed` or `not_applied`. The latter includes `reason`
and may include `remove_to_redeem` (request-field names). Reasons currently include
`body_mismatch`, `continuation_excluded`, `continuation_only`, `expired`,
`invalid_target_model`, `not_enabled`, `reprice_unavailable`, `temporarily_unavailable`,
`variant_fields_present`, `wrong_organization`, `wrong_platform`, `wrong_workspace`.
Credit shifts eligible cache-write tokens to cache-read tokens in reported usage.
[Expanded beta schema](https://platform.claude.com/docs/en/api/beta/messages/create) and
[Fallback credit](https://platform.claude.com/docs/en/build-with-claude/fallback-credit),
checked 2026-10-20.

For fallback, top-level usage describes the serving attempt, while `iterations` records
the attempts separately. Advisor/compaction/fallback usage is outside M13's intended
single-model path and cannot be treated as harmless metadata.
[Refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback),
checked 2026-10-20.

Web search costs $10/1,000 searches plus normal token charges. Web fetch has no additional
per-call fee, but fetched content is input. **M13 recommendation:** refuse unexpected server
tool activity rather than silently broadening the frozen tariff contract.
[Web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool) and
[Web fetch](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool),
checked 2026-10-20.

Context-edit reporting is top-level `context_management.applied_edits`, not a new usage
charge. Thinking-binding diagnostics are top-level `input_transformations`, also outside
`usage`; dropped blocks are absent from input-token billing. `model`, `stop_reason` and
`stop_details` are likewise outside usage but matter for settlement.
[Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing) and
[Beta response schema](https://platform.claude.com/docs/en/api/cli/beta/messages/create),
checked 2026-10-20.

Streaming supplies initial usage in `message_start`, then cumulative counters through
`message_delta`, ending with `message_stop`. Merge updates by field; do not sum successive
output-token deltas. Preserve the final served model and stop details as well. SSE `error`
is not successful final usage.
[Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming), checked 2026-10-20.

**M13 implementation inference:** validate TTL totals, model identity and standard/global
selectors; accept understood informational subsets without double billing. Reject unknown
charge-bearing values. Keep refusal/error evidence so a ledger can distinguish settled
zero, priced positive, and unknown. The frozen `price_usage(model, usage)` interface alone
does not express every documented billing outcome.

## 3. Effort, thinking and preserved history

Both models accept `output_config.effort` values `low`, `medium`, `high`, `xhigh`, `max`;
no beta is needed for the top-level setting. Defaults are medium for Opus and high for Fable.
Effort is a preference, not a hard output budget. Both also support per-message effort with
`mid-conversation-output-config-2026-07-01`. Changing top-level effort preserves thinking
validity but invalidates prompt caching; per-message effort preserves existing cached prefixes.
[Effort](https://platform.claude.com/docs/en/build-with-claude/effort), checked 2026-10-20.

Use `thinking: {"type":"adaptive","display":"summarized"}`. Omission still enables adaptive
thinking; `enabled`, `disabled` and `between_tools` are invalid on these two models.
[Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/overview) and
[Fable 5.1](https://platform.claude.com/docs/en/models/fable-5-1/overview), checked 2026-10-20.

| `thinking.display` | Behavior | Beta |
|---|---|---|
| `omitted` (default) | Empty thinking text plus signature | None |
| `summarized` | Readable thinking summaries | None |
| `updates` | Hides reasoning summaries, retains progress updates between tool calls | `thinking-display-updates-2026-08-18` |

Keep even empty signed thinking blocks. Summaries do not expose raw reasoning. Interleaved
adaptive thinking requires no additional beta.
[Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking), checked 2026-10-20.

Binding covers preceding messages plus `system` and `tools`. Editing/reordering/removing
earlier messages, shortening results, changing tools, re-encoding earlier images, changing
bytes returned by image URLs, or dropping intermediate thinking invalidates later bound blocks.
A changed URL returning identical image bytes is allowed. New accounts from
2026-08-31 00:00 UTC enforce prefix mismatches as 400 by default; older accounts need explicit
enforcement. Beta `thinking-binding-controls-2026-08-01` exposes
`thinking.block_binding.prefix_mismatch_behavior: "error"|"drop_block"` and
`input_transformations`. Server context editing checks the original client history and is
safe. Fresh renewal is safe **only without stale thinking from the replaced prefix**;
finish a pending tool call/result pair before renewal.
[Preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking),
checked 2026-10-20.

**Implementation inference:** assert append-only messages within each conversation and
unchanged system/tools; reset that assertion deliberately at renewal. Carry the state
summary, owner inputs and current evidence into a new conversation without transplanting
old signed thinking. This qualifies D6's blanket renewal claim.

## 4. Tools and schemas

Both accept `tool_choice: {"type":"auto"}` and `{"type":"none"}`. Forced `any` or named
`tool` choices produce 400, including during token counting. Default is auto when tools are
provided, none otherwise. Names match `^[a-zA-Z0-9_-]{1,128}$`.
[Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) and
[Forced-tool errors](https://platform.claude.com/docs/en/api/errors#forced-tool-use-not-supported),
checked 2026-10-20.

The serial setting is `tool_choice: {"type":"auto","disable_parallel_tool_use":true}`.
It allows at most one call; it does not require one.
[Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use),
checked 2026-10-20.

Both support `strict: true` on individual user-defined tools beside `name` and
`input_schema`. This guarantees constrained names/inputs subject to refusal/truncation;
it does not force tool use.
[Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use),
checked 2026-10-20.

Strict schemas reject recursion, external `$ref`, complex enum values, numerical constraints
(`minimum`, `maximum`, `multipleOf`), string length constraints, array constraints except
`minItems` 0 or 1, `additionalProperties` other than false, and `allOf` combined with `$ref`.
Regex excludes backreferences, lookarounds, word boundaries and complex wide-range quantifiers.
Request-wide limits are **20 strict tools, 24 optional parameters, 16 union parameters**;
strict tools and structured response schemas share the parameter limits. Additional grammar
complexity limits are unpublished; compilation can time out after 180 seconds. No fixed
schema byte cap is stated. Unsupported schemas produce 400. String `enum`/`const` capitalization
can differ even on a normal completion. Validate original restrictions locally if an adapter
removes them for the constrained schema.
[Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
checked 2026-10-20.

No overall maximum for ordinary non-deferred Messages tools was found in the reviewed
reference. Do not substitute a Managed Agents limit. Tool search separately supports up to
10,000 deferred tools with at least one non-deferred tool; this is not M13's ordinary registry.
[Messages reference](https://platform.claude.com/docs/en/api/messages/create) and
[Tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool),
checked 2026-10-20.

Fine-grained inputs: set per-tool `eager_input_streaming: true` with request streaming.
It works on both models without a beta; buffering is the default. The legacy
`fine-grained-tool-streaming-2025-05-14` header enables it when that field is absent;
explicit false overrides the header. Partial JSON can be invalid or truncated; accumulate
and validate before invocation.
[Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming),
checked 2026-10-20.

`tool_result.content` may contain images. Combining its documented image-block contract with
the vision file-source contract supports `{"type":"image","source":{"type":"file","file_id":"..."}}`
inside a tool result. This is a **schema-composition conclusion**, not a live request test.
Return results immediately after their assistant calls and before free text in the user turn.
[Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls) and
[Vision](https://platform.claude.com/docs/en/build-with-claude/vision), checked 2026-10-20.

## 5. Images and rejection of resizing

Both models use the high-resolution tier: **2576 px maximum long edge before resizing,
4784 visual tokens**, with `ceil(width/28) * ceil(height/28)` tokens and no beta header.
Hard accepted dimensions are at most 8000×8000. Their 1M context permits **600 images per
request**. With more than 20 images, stricter dimensions apply to every image, including
history/tool results; the guide recommends **≤2000 px on each side across platforms**.
It does not give one universal exact many-image rejection threshold.
Formats: JPEG, PNG, GIF, WebP; animation contributes only its first frame. Direct-API image
size is described as **10 MB, base64-encoded**. No separate numeric Files-image inference
byte limit is established by this page. Calculated: 1920×1280 costs 69×46 = **3174** visual tokens.
[Vision](https://platform.claude.com/docs/en/build-with-claude/vision), checked 2026-10-20.

The stable image-block option is:

```json
{
  "type": "image",
  "source": {"type": "file", "file_id": "file_..."},
  "transformations": {"oversized_image": "error"}
}
```

Default `oversized_image` is `downsize`. `error` returns 400 if resizing would occur or
dimensions cannot be determined. It works with embedded, URL and file sources, including
nested image blocks; remote sources are checked after retrieval. It checks explicitly
named fallback models too. It prevents resizing, while allowing padding, orientation
correction and format conversion. Hard rejection limits remain. No beta is documented.
[Coordinates and bounding boxes](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates),
checked 2026-10-20.

**M13 implication:** the frozen `image_block(file_id)` shape needs this additional
`transformations` member before requests are sent. Native tiling plus an actual server
rejection setting is stronger evidence than assuming the server never resizes.

## 6. Request size, output and timeout

Messages and token counting each allow **32 MB per request**; Files requests allow 500 MB.
Oversize requests receive 413. The docs' MB wording does not establish an exact
decimal/binary boundary. The brief's strict `<32,000,000` check is a conservative local choice.
File IDs reduce wire bytes but do not remove image-count, token-context or growing-text limits.
[API overview](https://platform.claude.com/docs/en/api/overview), checked 2026-10-20.

Both candidates support `max_tokens:128000` for streaming Messages. The SDK requires streaming
above **21,333** requested output tokens; that is client validation, not the API's output
ceiling. Opus additionally offers a **300K batch-only** output beta
(`output-300k-2026-03-24`); it is irrelevant to M13's manual Messages loop.
[Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) and
[Opus 5.5 output limits](https://platform.claude.com/docs/en/models/opus-5-5/overview),
checked 2026-10-20.

Use streaming or batches for long requests, particularly over ten minutes. SDKs validate
non-streaming calls against an expected ten-minute timeout and use TCP keep-alive. This is
not a guarantee of a maximum streamed generation time.
[Errors](https://platform.claude.com/docs/en/api/errors), checked 2026-10-20.

**Critical C4 limit:** counting rejects `image`/`document` sources of type `file` or `url`;
use base64 for counting. Embedded `oversized_image:error` is validated there. Counting is
free, estimates tokens, and does not populate/test prompt caching. Thus counting an uploaded
file-ID request cannot validate file references. A counting-only representation still
has to fit 32 MB; a large late-run request may not.
[Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting),
checked 2026-10-20.

## 7. Files API

| Operation | Method and path on `https://api.anthropic.com` |
|---|---|
| Upload | `POST /v1/files` |
| List | `GET /v1/files` |
| Metadata | `GET /v1/files/{file_id}` |
| Download generated output | `GET /v1/files/{file_id}/content` |
| Delete | `DELETE /v1/files/{file_id}` |

File IDs are opaque. Delete returns the ID and `type:"file_deleted"`.
[Files reference](https://platform.claude.com/docs/en/api/files), checked 2026-10-20.

Files is GA: use `client.files`, no beta header. Legacy `files-api-2025-04-14` selects older
response shapes. From Python SDK 1.2.0, `client.beta.files` also stops automatically attaching
that header. Current pagination uses `page`/`next_page`.
Limits: **500 MB per file, 1 TB per organization**, roughly 500 Files requests/minute.
Operations are free; file content consumed in Messages costs input tokens. Files are
workspace-scoped and immutable. Uploaded files cannot be downloaded; download serves
generated code-execution/skill outputs. Files persist until deletion or expiration.
Deletion removes subsequent access, although in-flight calls may retain it. Files is not
ZDR-eligible. The upload ceiling does **not** establish a 500 MB vision-image inference limit.
[Files guide](https://platform.claude.com/docs/en/build-with-claude/files), checked 2026-10-20.

Upload optionally accepts multipart `expires_in_seconds` from **3600 to 7,776,000**
(1 hour–90 days). Response `expires_at` is null without expiry, otherwise calculated from
upload time; expiry is set at upload.
[Upload File](https://platform.claude.com/docs/en/api/files/upload), checked 2026-10-20.

## 8. Context editing and temporary system messages

Both candidates support `context-management-2025-06-27`. Place this strategy under
`context_management.edits`:

| Field | Accepted form / documented default |
|---|---|
| `type` | `clear_tool_uses_20250919` |
| `trigger` | `{type:"input_tokens",value:N}` or `{type:"tool_uses",value:N}`; default 100,000 input tokens |
| `keep` | `{type:"tool_uses",value:N}`; default 3 |
| `clear_at_least` | `{type:"input_tokens",value:N}`; default no minimum |
| `exclude_tools` | Tool-name array; default none |
| `clear_tool_inputs` | Boolean or string array selecting specific tool inputs; default false |

True clears inputs alongside results. Oldest tool-result contents become explanatory placeholders.
If the requested minimum cannot be cleared, clearing does not occur.
The client keeps original history, including the opening
owner message. Responses report `context_management.applied_edits` entries with `type`,
`cleared_tool_uses`, `cleared_input_tokens`; streaming reports them in the final `message_delta`.
Clearing invalidates affected cached prefixes, requiring cache rewrites. The proposed
350K/150K/8 values are supported choices, not defaults or an empirically validated fit here.
[Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing) and
[Beta edit schema](https://platform.claude.com/docs/en/api/beta/messages/create), checked 2026-10-20.

`clear_at` is supported on **both** candidates. Turn-scoped system messages are text-only;
their clearing option requires `mid-conversation-system-clear-at-2026-08-21`.
Use `{"role":"system","content":"...","clear_at":"next_user_message"}` inside `messages`;
default `clear_at:"never"` persists. A subsequent user message, including tool results,
stops the earlier text being rendered/billed. Keep its original record in history.
Such messages follow a user turn, precede an assistant turn or end the array, and cannot
carry `output_config` or block-level `cache_control`. Automatic caching skips them; clearing
requires reprocessing the preceding assistant turn.
**Assessment:** suitable for transient operational reminders, unsuitable for permanent
instructions/photos. It is optional for M13.
[Mid-conversation system messages](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages),
checked 2026-10-20.

## 9. Prompt caching

Both models support automatic top-level `cache_control` and explicit content-block
breakpoints. Automatic caching selects the last eligible block; at most four breakpoints
exist, counting the automatic one. One-hour syntax:
`"cache_control":{"type":"ephemeral","ttl":"1h"}`; default TTL is 5m. No beta is required.
Minimum cacheable prefix: **512 tokens on each candidate**; shorter inputs silently
remain uncached. Cache hits use `cache_read_input_tokens`, writes
`cache_creation_input_tokens`, other input `input_tokens`; sum those for total input.
TTL runs from request start and refreshes on use, so generation consumes TTL. The 20-block
lookback also applies to automatic caching. Expiry, first writes, clearing and lookback
misses can all yield zero cache reads.
[Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
checked 2026-10-20.

## 10. Rate limits and account admission

Published standard limits; each model cell is **RPM / ITPM / OTPM**:

| Tier | Opus 5.5 | Fable 5.x shared | Monthly spend cap |
|---|---|---|---:|
| Evaluation | Not published | Not published | Not published |
| Start | 1,000 / 2,000,000 / 400,000 | 1,000 / 500,000 / 100,000 | $500 |
| Build | 5,000 / 5,000,000 / 1,000,000 | 2,000 / 1,500,000 / 300,000 | $1,000 |
| Scale | 10,000 / 10,000,000 / 2,000,000 | 4,000 / 4,000,000 / 800,000 | $200,000 |
| Custom | Negotiated | Negotiated | No tier cap |

Cache reads do not consume ITPM; uncached input/cache writes do. OTPM uses generated output,
not the requested maximum. Fable 5 and 5.1 share limits. Placement depends on usage history
and account standing: **no fixed credit-purchase amount or waiting schedule is published**.
[Rate limits](https://platform.claude.com/docs/en/api/rate-limits), checked 2026-10-20.

Calculated lowest **published standard** tier with enough ITPM for one otherwise-idle request:

| Uncached input | Opus 5.5 | Fable 5.1 |
|---:|---|---|
| 150K | Start | Start |
| 400K | Start | Start |
| 800K | Start | Build |

These are arithmetic comparisons, not account-access guarantees. Evaluation is unquantified;
workspace restrictions, bucket availability and account eligibility still apply.
Basis: [Rate limits](https://platform.claude.com/docs/en/api/rate-limits), checked 2026-10-20.

A separate documented eligibility route: linked Max 5x grants $100/month API credit,
Max 20x $200/month; new subscribers qualify after seven days. Linking raises an organization
to at least Start; over $200 in linked monthly credits raises it to at least Build.
This is not a purchased-credit tier threshold. Exhausted credits require other credit/funding.
[API credits for subscribers](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers),
checked 2026-10-20.

## 11. Errors and stop reasons

HTTP 429 indicates rate/acceleration limits or a tier monthly spend cap; 529 indicates
temporary overload. Respect `retry-after` seconds when provided. A tier-spend-cap 429 has
`error.details.error_code:"enforced_spend_limit_reached"` and no retry-after; it persists
until access resumes. A default 30-second resend cannot fix that case. The SDK normally
retries transient errors twice; M13's `max_retries=0` disables those retries.
[Errors](https://platform.claude.com/docs/en/api/errors) and
[Rate limits](https://platform.claude.com/docs/en/api/rate-limits), checked 2026-10-20.

After HTTP 200, SSE may still emit `event:error`, including `overloaded_error`.
This is different from an initial HTTP 529 and can follow partial work. Retain the raw
stream and mark unknown if final billable usage is missing.
[Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming), checked 2026-10-20.

| `stop_reason` | Meaning |
|---|---|
| `end_turn` | Natural completion |
| `max_tokens` | Output limit |
| `stop_sequence` | Requested stop string |
| `tool_use` | Tool invocation |
| `pause_turn` | Server loop paused; ordinarily after ten server-tool iterations |
| `refusal` | Declined response, HTTP 200 |
| `model_context_window_exceeded` | Context exhausted; output truncated |

Initial stream stop reason is null; final reason arrives through `message_delta`.
[Stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons),
checked 2026-10-20.

The beta response enum additionally includes `compaction`; it is outside M13's intended
configuration. Unknown stop reasons must not silently mean a successful finish.
[Beta response schema](https://platform.claude.com/docs/en/api/cli/beta/messages/create),
checked 2026-10-20.

## 12. Server-side fallbacks

Omitting `fallbacks` keeps fallback off. Explicit `fallbacks:"default"` with
`server-side-fallback-2026-07-01` enables Anthropic-selected retries for classifier refusals;
explicit model lists permit up to three targets. HTTP overload/rate/server errors do not
trigger this model routing. Without it, a refusal remains a normal HTTP 200 refusal response
for the client to handle. M13 should stop as a draft as planned.
[Refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback) and
[Beta request schema](https://platform.claude.com/docs/en/api/beta/messages/create),
checked 2026-10-20.

Both candidates' documented fallback targets are Opus 4.8 and Opus 5. A fallback would
therefore change the experiment's model and tariff.
[Fallback credit](https://platform.claude.com/docs/en/build-with-claude/fallback-credit),
checked 2026-10-20.

## 13. Data handling

The platform page says prompts and outputs are not retained by default, with Covered
Models as the exception. Fable 5.1 is a **Covered Model requiring 30-day retention**; ZDR use needs express
Anthropic authorization. Requests from an organization/workspace without the required
retention setting return 400. Opus 5.5 is not in the Covered Models list. Retained data is
not used for training without express permission. This stated default is not a guarantee
of account-specific ZDR eligibility. The platform page defers full standard
retention terms to `privacy.claude.com`, outside this brief's allowed sources; this audit
does not claim to establish that entire contract for Opus. Files retention is separately
stateful and not ZDR-eligible (section 7).
[API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) and
[Fable migration](https://platform.claude.com/docs/en/models/fable-5-1/migration-guide),
checked 2026-10-20.

## Differences from M13_CLAUDE_TEST.md

This comparison covers D1 and D4–D7, with adjacent corrections explicitly identified.
References and check date **2026-10-20** are given in the numbered sections above.
No plan, interface or other task's file was changed.

| Plan claim | Verdict / necessary qualification |
|---|---|
| D1 model IDs, $4/$20/$0.20 and $10/$50/$0.25; 1M/128K; always-on adaptive thinking; forced-tool 400 | Confirmed. Add 5m/1h tariffs and different effort defaults (sections 1, 3, 4). |
| D1 Fable is most capable widely released, stronger for vision/long-horizon work, and costs the same as Astra | Anthropic's positioning supports the capability claim; published improvements compare Fable 5.1 with Fable 5. Superiority over Opus 5.5/Astra on this job is unverified. OpenAI tariff parity is outside the permitted sources (section 1). |
| D4 each reply can be settled from `usage` alone | Incomplete: unbilled refusals still report tokens. Category/timing matter; served model is outside usage too (sections 1–2). |
| D4 all initial 429/529 are refused before work and unbilled | Not established by the permitted billing/error sources. Preserve as an unresolved money assumption, not a verified guarantee (sections 1, 11). |
| D4 resend every 429 after a wait | Spend-cap 429 lacks retry-after and does not recover on ordinary backoff. The 30-second fallback and 20-minute cap are local policy, not provider guarantees (section 11). |
| D4 128,000 is the model maximum | Correct for this Messages loop; Opus has a separate 300K batch-only beta (section 6). |
| D5 2000 px is the exact required threshold above 20 images | The current guide presents it as the safe cross-platform recommendation; exact universal rejection-threshold wording is unsupported (section 5). |
| D5 reject-instead-of-resize option (implementation qualification) | Confirmed as `transformations.oversized_image:error`; orientation/padding/format transformations remain. The frozen helper lacks this field (section 5). |
| D5 Files keeps requests far below 32 MB however many images accumulate | Not guaranteed: text/schema/history grow, and image count/context limits persist (sections 5–6). |
| D5 / common brief 10,000,000-byte Files-image limit | No such inference-specific bound found. The documented 10 MB wording concerns base64, while 500 MB is upload capacity (sections 5, 7). |
| D6 renewal preserves thinking without qualification | Fresh history must omit stale signed thinking; retained tail thinking can fail prefix binding. Server context editing itself is safe (section 3). |
| D6 350K trigger / 150K clearing / 8 kept | Valid settings, not defaults or proof that the request profile supports them. No saved-run profiling was authorized/performed by R (section 8). |
| D6 clearing plus renewal (implementation qualification) | Tool clearing does not shrink the client payload; original history must still be sent. Keep the planned local payload/image-count renewal backstop (sections 6, 8). |
| D6 receipts show cache reads every request (qualification) | Compatible as field reporting. A positive hit is not guaranteed; first write, expiry, clearing and lookback misses are legitimate zeros (section 9). |
| D7 previous request is a prefix of next request | Apply to messages within a conversation, with stable system/tools; not the entire request JSON or across a fresh renewal (section 3). |
| D7 strict schemas wherever accepted | Requires actual schema adaptation and request-wide 20/24/16 limits; cannot assume the whole existing registry can be strict (section 4). |
| D7 summarized thinking, serial auto tools, fixed host line, refusal draft, fallback off | Compatible. The host line is project policy; it cannot guarantee eventual tool use. Preserve thinking blocks, including empty ones (sections 3–4, 12). |
| Adjacent C4: count uploaded file references to validate the request | Contradicted: token counting rejects file/URL image sources. A base64 counting representation is needed and cannot verify file-ID access; large histories may exceed 32 MB (section 6). |
| Adjacent D8: files remain until deleted | Also support configured expiration (section 7). |
| Adjacent D9: Max never covers API use; buying a known credit amount buys a tier | Outdated/incomplete: linked eligible Max subscriptions grant API credit; no universal purchase/wait tier schedule is published (section 10). |

The unresolved cancellation/error billing guarantees, account-specific eligibility and live
file-image validation need coordinator disposition before a paid run. This research does not
authorize one.
