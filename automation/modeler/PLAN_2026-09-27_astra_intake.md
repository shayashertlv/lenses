# Plan: Astra at the start of the loop (the intake reading and the measurement review)

Status (2026-09-29): implemented 2026-09-27 (`modeler/intake_astra.py`, the `modeler/job.py` wiring, the kind tags
in `modeler/tags.py`). Two items of section 4 did not land as written: `decide_status(..., serious_flags=...)` does
not exist, because `gate_overrides` (`report_only`) was implemented in its place (section 3: nothing is downgraded);
and the `lens_count_mismatch` dead branch was not removed with this plan. It was removed on 2026-09-29, together with
the unreachable status `inconsistent_inputs`. `--intake` defaults to `none`.

Date: 2026-09-27. Owner's decision: replace every data guess at the start of the pipeline where a vision model is more
accurate than the code; plan first, then implement. Inputs: the three-reader inventory of the pre-author guesses (77 items
with file:line, consumers, values on tomford-astra1 vs rayban-astra1), the Astra client (`modeler/author_astra.py`), the
transport's strict-schema rules (`reconstruction/segmented_astra_transport.py:172-217`) and the observed costs
(`data/modeler/astra_ledgers/*.usd.json`).

## 0. What the inventory established

One root cause broke the Tom Ford job and it is not a guess the flags name: the contrast matte (`bsa/intake.py:585`)
cannot see a near-white crystal rim on a white backdrop (brow contrast p50 2.8 against a threshold of 7). Seven
downstream values inherited the hole and were handed to the author and the gate as measurements:

| guess | file:line | Tom Ford | Ray-Ban | consumer that was misled |
|---|---|---|---|---|
| frame mask | `bsa/front.py:1108` | 20 fragments, no brow, no bridge | one ring | rim widths, rim class, thickness, silhouette |
| rim class / widths | `bsa/front.py:960, 937` | `rimless`, 0.66 mm (real rim ~5 mm) | `full`, 3.69 mm | author (built a ~1 mm rim), tag `rim_rimless`, coverage |
| thickness brow/bottom/endpiece | `modeler/intake.py:110` | 1.87 / 0.97 / 4.46 mm | 3.56 / 2.44 / 4.79 | author (thin rims: the evaluator's first major) |
| lens outline top | `bsa/front.py:438-667` | pulled 1.3 mm into the lens under the flash reflection (55 % of points fell back to the bevel) | 18 % fallback | the ONLY gate (`lens_outline_mean_mm` 0.88 > 0.8) scored a correct round lens against a clipped reference |
| silhouette / front height / y = 0 | `modeler/intake.py:79-104` | includes the folded temple tips 15-22 mm above the brow; y = 0 is 10 mm too high; the author built the tips as "flared shoulders" | includes the tips too (7.8 mm) | author, side scale |
| mirror IoU flag | `bsa/intake.py:678` | 0.86 → `mirror_iou_low` → a "serious input" that caps the status | 0.997 | `decide_status` |
| scale 140 mm | `modeler/request.py:78` | assumed; the listing says lens 49, the measured lens is 46.9 at 140 → the front is ~146 mm | assumed | every millimetre, the AR width |

Two guesses were wrong on the healthy job as well (front height / y = 0 counting the folded temples; the side scale
equating the side matte's height to that inflated height: Ray-Ban's two temples measured 4 % apart). Three inputs are
typed by hand today and are pure vision work: the view labels, the product notes, the identity checklist. Code defects
found in passing: `job.py:677-678` drops the protocols' `conditions` dict; `lens_count_mismatch` (`evaluate.py:285,301`)
is produced by nothing; `low_contrast_high` tests a key the evidence never carries (`front.py:1167` vs
`intake.py:137-138`); `rimless_low_confidence` means "no ridge searched"; two mm-per-px definitions disagree
(`front.py:1001` vs `intake.py:619`).

## 1. Principle

Vision classifies and reads; code measures. Astra answers the questions the code cannot (what is this, which parts of
the photo are what, which measured arcs are trustworthy, what do the markings say); the code keeps every pixel-precise
measurement and every metric, but runs them on the vision's priors and reports the vision's reliability verdicts beside
its numbers. Every replaced value keeps its code value and its provenance in the evidence, so a wrong reading is visible.

## 2. Two calls per job, both before the author's first turn

### Call 1: `read_product` (raw photos, before any measurement)

Inputs: the request's non-held-out photos at author-crop resolution (≤ 1024 px; the held-out angled photo stays
evaluator-only, so the reading that reaches the author never sees it), the request's `notes` as `listing_text` (data),
the hand labels as `view_hint` per photo. One strict tool. Output:

- `photos[]`: `{id, view ∈ front|back|left|right|angled|rear_angled|top|other, straight_on, folded_temples_visible_above_front, floor_reflection_present, note}`.
- `product`: `layout ∈ pair|single`, `rim_class ∈ full|half|rimless`, `frame_material ∈ opaque_acetate|crystal|translucent_acetate|metal|mixed`,
  `frame_colour`, `rim_thickness_mm ∈ [0.5, 12] | null`, `front_shape`, `bridge`, `endpieces`, `temples`, `hardware`, `branding`,
  `lens_finish ∈ solid|gradient|flash_mirror|clear|other`, `lens_colour`, `mirror_colour | null`, `lens_print_location | null`.
- `size_marking`: `{lens_mm, bridge_mm, temple_mm (numbers or null), source ∈ temple_print|listing_text|none, confidence 0..1}` — read, never estimated.
- `symmetric_product`, `photos_consistent` (one product, usable studio photos).
- `identity_features[]` (5-9 strings, the evaluator's checklist), `product_description` (≤ 1200 chars, replaces the hand-written notes),
  `author_cautions[]` (e.g. "the lobes above the endpieces in the front photo are the folded temple tips, not the front").

### Call 2: `review_measurement` (after the code has measured, with the overlay)

Inputs: `front_measured.jpg` (lens outlines, axis, y = 0), the front crop, the reading's product block, the compact
front measurement (layout, rim class, rim widths, thickness, lens boxes, flags). Output:

- `rim_class_matches`, `rim_widths_trustworthy`, `thickness_trustworthy`, `silhouette_includes_temple_tips`, `silhouette_complete`.
- per lens: `outline_trust` for `top | bottom | inner | outer ∈ trusted|clipped_by_reflection|missing_edge|inside_rim`,
  `lens_shape_family`, `lens_height_over_width` estimate.
- `notes` (what the author must know about this evidence).

### Cost and ledger

Observed turn-0 author calls with 5-6 images: 12-16k input tokens, 3-4k output, $0.31-0.39. The intake calls carry the
same photos and far less text, with `max_output_tokens` 4,000 so the pre-call estimate stays near $0.5 (the default
24,000 ceiling would make the estimate $1.5-2 and refuse under a small cap). Expected: call 1 $0.30-0.40, call 2
$0.25-0.35, together under $0.80 per job. The two calls get their own call ledger (`<ledger>.intake.json`, 3 slots)
sharing the job's dollar ledger, so the owner's cumulative cap holds and the author keeps its 10 call slots. Charging
follows the driver's existing rule: a 4xx (the HTTP 429 seen twice) charges nothing; a refusal or a schema-invalid
answer after an HTTP 200 charges the worst-case estimate (about $0.4); the transport never retries a sent request.
Whatever fails, the job continues on code-only evidence with the failure in the journal and the protocol
(`intake_stage`): the calls improve the evidence, they never block a job. A re-run of a folder that already holds a sent
request is REPLAYED by the transport and now charged nothing (until 2026-09-27 every Astra role re-charged a replay).

## 3. What each answer changes in the code path (applied by `apply_reading` / `apply_review`, pure functions)

| answer | code action | provenance recorded |
|---|---|---|
| `photos[].view` differs from the hand label, or the hand label is `unknown` | the vision label wins for measurement and camera seeds (`rear_angled`/`top`/`other` map to no camera fit, like `unknown`, but keep the descriptive label for the author and evaluator); the job's `request.json` is rewritten with `view_hint` kept | `views[id].view_provenance` |
| `size_marking` read with confidence ≥ 0.6 | with lens AND bridge: `front_width_mm = nominal × (lens + bridge) / (R.x_max − L.x_max)` (the outer edge of one aperture to the inner edge of the other: the rim lip insets both edges equally, so the span is lip-invariant), uncertainty 3 %; with the lens size alone: `nominal × lens / visible aperture width`, uncertainty 5 % (the lip bias); intake is RE-RUN with that width and the residual against the marking is recorded | `scale.source = size_marking`, `method`, both values |
| `frame_material ∈ crystal|translucent_acetate` or `rim_class_matches = false` | `front.rim_class` := vision class; rim widths and `thickness_mm` := vision `rim_thickness_mm`; the code values move to `front.code_measured` | `front.provenance.rim = vision` |
| `folded_temples_visible_above_front` on the front photo (call 1; the review's `silhouette_includes_temple_tips` is informational, the re-measure happens once) | `front_height_mm`, `y = 0` and the silhouette from the lens band (lens rows plus rim thickness above and below), AND the front's FIT mask loses the tips too (a model's open temples never show above the front, so a matte with tips penalised every correct candidate); the side views are now scaled by the front piece's height over their thick-end columns, not the whole side matte (the old rule put one product's two temples 4 % apart) | `front.height_band`, `views.front.fit_mask` |
| the measured silhouette is not one front (a half or fragments: \|min x + max x\| beyond 10 % of the width) or the review says `silhouette_complete = false` | a constructed silhouette: the lens outlines offset by the rim thickness plus a bridge between them, marked `constructed` (the Tom Ford author had received the right half only) | `front.silhouette_source` |
| any `outline_trust ≠ trusted` | the lens gate becomes report-only for THIS job (`protocol.gate_overrides`), the lens outline leaves the incumbent score (`incumbent_rule.weights.lens_outline = 0`), and the calibration's automatic verdict reads the same override from the job's protocol, so one asset gets one verdict everywhere | `protocol.gate_overrides` |
| `symmetric_product`, `photos_consistent` | recorded for the author and the evaluator only: `mirror_iou_low` never capped a status (the serious-flag branch only adds a reason), so nothing is downgraded | — |
| `layout` from vision | `front.layout` := vision when the code's component count disagrees (flag `lens_components_N`) | `front.provenance.layout` |
| `identity_features` | the protocol's identity checklist when no `--checklist` is given (an owner file still wins); the `conditions` bug is fixed on the way | `protocol.checklist_source` |
| `product_description`, `author_cautions` | `evidence.notes = listing_text + description`; cautions and the review's notes go to the author as `evidence_reliability` and to the evaluator beside `product_notes` | — |
| tags | `tags.kind_tags` prefers the vision classes (`rim_class`, `layout`, `frame_material` → `translucent`) | manifest `tags_source` |

Not changed: backdrop model, the matte itself, the symmetry axis, edge refinement where contrast exists, symmetrisation,
carve/vents, floor-reflection cut, camera fits and every metric. The lens outlines stay the code's; the review only
decides how much they may be trusted.

## 4. Where it lives

- `modeler/intake_astra.py` (new): the two tool schemas (strict: object roots, every property required,
  `additionalProperties: false`, nullable as `["number", "null"]`), `reading_instructions_text()`,
  `review_instructions_text()`, `validate_reading()`, `validate_review()`, `AstraReadingDriver` /
  `AstraReviewDriver` (subclasses of `AstraAuthorDriver` in the `AstraEvaluatorDriver` pattern, `max_output_tokens`
  4,000), `PackageIntake` (a fresh agent answers a folder, the same file protocol as the author/evaluator packages,
  for free tests) and `ScriptedIntake` (JSON answers, for unit tests and dry runs), `apply_reading()`,
  `apply_review()`, and a CLI `python -m modeler.intake_astra --job <dir> --intake scripted|package|astra ...`
  that runs or re-runs the stage on an existing job.
- `modeler/job.py`: `ensure_evidence` becomes intake → reading → (scale re-run) → review → apply; the journal records
  `intake_reading` / `intake_review` with usage and cost; `write_protocol` freezes the checklist source, the gate
  overrides and the scale provenance; `finalize` reads the gate override and the downgraded flags. CLI: `--intake
  astra|package|scripted|none` (default `none` until validated), `--intake-script`, the intake ledger derived from
  `--astra-ledger`.
- `modeler/request.py`: `VIEWS` gains `rear_angled` and `other` (`top` existed); the hand label is kept as the
  `view_hint` of the reading package and the reading's own label lives in `evidence.intake_reading.photos`.
- `modeler/intake.py`: `measure_front` accepts a `height_band` (lens band) and `run_intake` accepts `view_overrides`;
  `front.code_measured` keeps the replaced values.
- `modeler/evaluate.py`: `decide_status(..., serious_flags=...)` so the job can downgrade `mirror_iou_low`; per-job gate
  mode from the protocol; `lens_count_mismatch` dead branch removed.
- `modeler/author.py`: `compact_evidence` adds `evidence_reliability` and `provenance`; one RULES line on reading them.
- `modeler/tags.py`: kind tags from `evidence.intake_reading.product` when present.
- Tests: `tests/test_modeler_intake_astra.py` (schemas pass the transport validator; validators; `apply_*` on the Tom
  Ford and Ray-Ban evidence files; the scale re-run arithmetic; gate override and flag downgrade in `decide_status`).

## 5. Validation before any paid call

1. Unit tests (`tests/test_modeler_intake_astra.py`), plus the existing suites.
2. Scripted answers run the stage offline on a FRESH job built from the Tom Ford photos (never on a finished job: a
   job that already has candidates only gets a preview, `evidence/intake_astra/evidence.preview.json`, because its
   candidates, metrics and calibration rows were built from its evidence): rim class `full`, rim 5 mm, front height
   from the lens band (~44 mm instead of 61.6), y = 0 at the lens band, scale from the size code, the constructed
   silhouette spanning both halves, the fit mask without the temple tips, the lens gate report-only, the checklist
   from the reading. The tags of the existing calibration rows are frozen into the rows first
   (`python -m modeler.calibration --freeze-tags`), so no later evidence change can move the coverage table.
3. The `package` intake answered by a fresh agent on the Tom Ford photos: the prompts and schemas exercised end to end
   at no API cost, the answers compared with the scripted ones.
4. One paid validation of the two calls on the Tom Ford photos (about $0.70, up to $1.50 if an answer fails
   validation) from the $2.38 left of the owner's $5 authorization for this product, on the same ledger; then the
   author rerun is the owner's call.

## 6. Order of work

1. `intake_astra.py`: schemas + validators + drivers + apply functions + CLI, with tests (no job changes yet).
2. `intake.py` / `request.py` hooks (height band, view overrides, `code_measured`).
3. `job.py` wiring, protocol fields, `evaluate.py` gate mode and flag downgrade, `author.py` and `evaluate.py` packaging,
   `tags.py`.
4. Scripted validation on tomford-astra1 and rayban-astra1 (the healthy job must not change except the folded-tip
   height fix); full suites.
5. Package (agent) validation; then the paid validation.
