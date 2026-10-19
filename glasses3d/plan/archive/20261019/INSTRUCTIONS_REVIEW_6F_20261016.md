# Independent instruction and runtime review: M6 6f

2026-10-16. Reviewer: the separate `independent_review` agent. Scope: ASSEMBLY M6
6f(a–c), applied after the 6c–6e review at `051b5e7` and plan update `49c36e5`.
No outstanding code or instruction findings remain in the reviewed candidate.
The third walkthrough proof and final gallery checks passed as recorded below.
This verdict does not claim owner design acceptance or completion of the broader
suites, which were still running at this review. Full validation belongs in
PROGRESS.md.

## Instruction changes and verified implementation

| Change | Source checked |
| --- | --- |
| Remaining money is for visible fixes; finish only when no still-fixable visible difference remains within the owner's limits and finish reserve. Owner EDIT remains limited to confirmed operations; UNDERSTAND still stops. | `agent/instructions.py`: main and owner `finish` guidance, `_EDIT_OBJECT`; `agent/temple_lab/instructions.txt`: money/finish rules; existing budget and two-call finish contracts in `agent/loop.py`, `agent/tools/__init__.py`, and specialist `agent/temple_lab/tools.py` |
| Shape changes require full-size photo/render pairs and detail close-ups as well as geometry metrics. | `agent/instructions.py`: `compare_look_to_photos` main/owner guidance; `agent/tools/look.py`: tool description and `capture`; `agent/tools/look_panels.py` |
| UNDERSTAND offers two or three amount choices on isolated copies, with unchanged source, exact ordered operations, frozen cameras, native comparisons, shared target crops and following markings. | `agent/tools/owner_preview.py`: `_source`, `_unchanged`, `_prepare`, `_run_option`, `scaled_operations`, `_boxes`, `_triple`, `_pictures`, `preview`; `agent/owner_edits.py`: `expected_positions`, `verify_snapshots`; `g3d/owner_markings.py`: `seal` |
| Only a successfully delivered, unchanged preview can enter understanding. The owner explicitly chooses an option at confirmation. | `owner_preview.py`: `mark_delivered`, `load_verified`; `agent/tools/__init__.py`: `_output` and delivery callbacks; `agent/tools/owner_edit.py`: `understand`; `agent/edits.py`: `selected_amount`, `create_edit`, `brief_suffix`; `agent/cli.py`: `--option` |
| The chosen preview replaces base amounts and is applied as one complete ordered group. Its numbers cannot be replaced by a correction; another amount needs another shown preview. | `agent/edits.py`: `Authority.resolve`; `agent/owner_edits.py`: `corrected_operations`, `confirmed_transition`, `ScopeGuard.verify`; manual and specialist paths were included in review |
| Private owner-specialist UNDERSTAND previews are advisory. The parent must show its own map and obtain its own delivered preview receipt before sealing its understanding. | `agent/temple_lab/owner_stage.py`: `prepare`, `run`; `owner_preview.py`: `load_verified`; `agent/instructions.py`: owner `temple_stage` override |
| Short temporary render jobs and verified canonical archives keep preview evidence usable in nested Windows specialist rounds. Native crop headers remain legible without resizing the photographs. | `owner_preview.py`: `_prepare`, `_archive_evidence`, `_capture_record`, `_triple`, `preview`; nested-understand, archive, cleanup and header regression tests in `tests/test_owner_preview.py` |
| The brief keeps exact selected operation parameters and authority references while omitting repeated vertex and attachment arrays. | `agent/edits.py`: `brief_suffix`, `_choice_summary`; `tests/agent/test_owner_preview_selection.py` |

The complete instruction texts were read against the code and tool descriptions,
including the unchanged instructions reviewed for 6e. New requirements were
checked against ASSEMBLY M6 6f and the existing owner decisions. Appearance and
money-use obligations are instructions for Astra; they are not described as new
automatic visual-quality gates.

## Findings fixed before the clean review

- The look tool description still framed the comparison as colour/material work.
  It now includes shape details.
- Duplicate operation IDs and distinct numeric scales could yield unchanged,
  cancelling or identical float32 geometry. Such choices now refuse before
  private workers start.
- Preview confirmation needed complete source, visibility and evidence checks.
  It now rechecks the exact source head/checkpoint, persisted and in-memory unseen
  calls, delivered map support, sealed base operations, scale-only options and
  raw/composite image hashes.
- Subsets, reordered or split operations could differ from the chosen preview.
  Authority and scope checks now require the whole ordered result, including
  manual edits. Changed correction parameters cannot replace selected amounts.
- Exact vertex positions alone did not preserve preview shading. Scope checks
  now also require the unchanged source or selected preview scene fingerprint.
- The owner instructions now distinguish selected-preview edits from ordinary
  same-support corrections and state the parent/child receipt limitation.
- Real preview crops exposed overlapping long headers. Compact PHOTO / BEFORE /
  OPTION headings now have enough white padding for narrow crops and long IDs;
  native crop pixels remain exact and full descriptions remain in metadata.
- A real specialist handoff exposed paths exceeding Windows' limit. Baseline
  captures, private option workers and composites now use short temporary job
  folders. Verified copies of images, exports, reports and actual before/after
  mesh snapshots use short `work/pv-assets` paths; the receipt uses `work/pv`.
  Original paths remain provenance, all usable evidence paths are canonical,
  and temporary jobs close and clean up on success and failure. Canonicalization
  treats evidence fields as paths without misinterpreting owner labels.
- The initial brief repeated sealed mesh arrays for every option. Its compact
  projection now preserves exact ordered IDs, kinds, targets, parameters and
  receipt/fingerprint references, while full immutable authority stays separate.
- The proof harness now derives preview operations from the same ordered items
  it submits for understanding, takes child operation IDs from host authority,
  and checks the selected fingerprint at `delivery.report_data.fingerprint`.
  Failed attempts are retained; these fixes do not change amounts or hard checks.

## Exact texts and review evidence

The runtime-generated exports match the reviewed texts exactly. The four owner
instruction bodies (UNDERSTAND, EDIT, and both private specialist phases) are
identical; the host brief supplies phase, verbatim owner words, understanding and
selected option. The main/owner limit remains 16,000 characters and the ordinary
specialist limit remains 19,000.

| Effective text | Characters | SHA-256 of exported UTF-8 text |
| --- | ---: | --- |
| Main Astra | 13,453 | `e9adeabef9d50e5d89d7ae6da2bb2ac1e596bda4142404ff9a84d7a5b8ff8399` |
| Owner instructions | 15,719 | `60d0d41c38c544e80756665a3fbeb8ca1aecf167de3d786ebbed6436d60fb794` |
| Ordinary temple specialist, K=4 | 14,231 | `a92cc57f86264d3b870098d2b46ad65b29709d020c1d99a6dc077c7fa05a5d27` |

Local evidence is under `data/assembly/m6-6f/`:

- `review/architecture-checklist.md`, `review/pass-1-instructions-ab.md`: initial
  scope, source map and first-pass findings.
- `review/final-code-review.md` and `.json`: final verdict, resolved findings,
  exact reviewed production/test hashes and independently checked export hashes.
- `review/final-code-review-before-path-fix.*`, `review/pass-2-real-images.md`,
  and `review/proof-harness-review.json`: preserved earlier review evidence and
  the late presentation/path/proof findings, superseded by the final code review.
- `review/final_code_review.py`: offline export-equality and hash receipt script.
- `instructions/instruction-audit.json`: statement-block source/decision map.
- `instructions/*.txt` and `instructions/full-instructions.html`: complete texts and host brief
  suffix templates.
- `review/native-preview-review.json` and `review/gallery-review.json`: native
  pixels, narrow headers, lettering, final gallery captions/amounts, visible
  markings/legend and exact full instruction page checks.
- `review/proof-fixture-review.json`: the later test-only routing fixture fix
  and independent focused recheck; production and proof source hashes unchanged.

Independent focused validation: `tests/test_owner_preview.py`,
`tests/agent/test_owner_preview_selection.py`, and
`tests/agent/test_instructions.py` — **143 passed in 11.89 seconds, exit 0**. This
includes actual SDK output-wrapper success/failure coverage without a provider
or Blender. The tests check native image dimensions, delivery refusal,
immutable option selection, noncommuting operation order and instruction
contracts, plus short nested paths, archive integrity, temporary cleanup and
narrow-header pixel preservation. No network call, paid run or estimate was made
for this review.

CLI coverage checks parsing and selection wiring only. The offline `create_edit`
helper prepares the confirmation copy without estimating or running it. The real
`job edit` CLI continues into `prepare_run` and requests an estimate; that command
was not executed for this review.

The broad suite later exposed an outdated proof-routing unit fixture: it invoked
the script with an empty child directory although the proof now obtains ordered
operations from confirmed child authority. The test-only correction provides an
authority stub tied to the exact child path and returns ordered IDs deliberately
different from the plan. It verifies that the script forwards those confirmed
IDs and preserves the report/two-review route. A separate test uses real authority
without confirmation and requires refusal. Independent recheck of
`tests/test_assembly_m6_proof.py` and `tests/test_owner_edit_proof.py`: **6 passed in
7.31 seconds, exit 0**. No production, instruction or proof source changed; all
previously reviewed hashes still match. Timing and render-golden rechecks remain
separate validation results owned by the root reviewer.

## Completed walkthrough and owner gallery

The third local walkthrough, `data/assembly/m6-6f/walkthrough-final-3/report.json`,
passed in **1,108.075 seconds**. It used scripted tool calls rather than Astra,
made zero provider calls and spent $0. All **417 protected original files stayed
unchanged**. Its scripted confirmation selected B; this is proof coverage, not
the owner's design choice or approval for a paid run.

The separate saved-array audit confirms that A/B/C start from the same seed and
source arrays. Only the two temples and 14 declared markings change; the other
30 objects remain exact. The selected B preview, private specialist result and
parent result have identical object arrays, scene state and fingerprint. The
audit also verifies 302 immutable authority hashes and 285 preview evidence
hashes in both rounds. The walkthrough report hash is
`a6024e3fa7204935729bfd3347f58f3f23355101d83569783ba4539856808496`;
the array audit hash is
`43d7a34b7da4c77ba6c6a7434dc1496b699e34a022e5f62b2bef6442f8dffda3`.

Independent native-image review checked all **81 comparisons** against their
source photo/before/option arrays pixel for pixel, with identical cameras and
target crop boxes across options. The actual narrow 81px and 90px B panels have
readable padded headers. All three native lettering crops visibly preserve the
complete PRADA, MILANO and crest. No image content was resized in those native
comparisons. The native review hash is
`e4931f682a7c6962e2159ed7ed8076c2570d76c5b8a4386fa056ce32ae54e792`.

The final local gallery links all 81 comparisons and three lettering crops to
their exact original image files. Its A/B/C amount table matches the sealed
operation parameters, and all 14 markings plus 14 plain-language legend entries
are visible outside collapsed sections. The full instruction page contains the
exact three reviewed instruction texts and three host brief suffix templates;
the four exported owner variants remain identical. No owner acceptance is
claimed. Gallery SHA-256:
`2e8d28c948e38116e6879e71744040bef31024fdc42053ae88020fbd7cf38135`.
Full instruction page SHA-256:
`8b90dff421254fa21cdf5491af752e9a77112b08d2aefd2441068c2d612f117f`.

`tests.proofs.assembly_m1` and the broader affected suites still require their
separate recorded results; this review does not pre-approve them.
