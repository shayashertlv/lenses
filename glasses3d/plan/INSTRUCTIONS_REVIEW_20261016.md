# M6 6c–6e instruction and implementation review — 2026-10-16

The owner requested repair and a complete instruction rewrite at $0, with no API
call, estimate request or paid run. This is a review record, not authorization for
a run or owner acceptance of a design. ASSEMBLY.md remains the active plan.

## Reviewed texts

| Effective text | Source | Characters | Limit |
| --- | --- | ---: | ---: |
| Astra main | `agent/instructions.py` | 13,126 | 16,000 |
| Ordinary temple specialist, default K=4 | `agent/temple_lab/instructions.txt` | 13,992 | 19,000 |
| Owner understand/edit, including private owner specialist | `agent/instructions.py` | 13,200 | 16,000 |

The owner phases share one complete instruction text. `agent/edits.py:brief_suffix` appends
the actual phase, verbatim request and, for editing, the sealed understanding and
verbatim confirmation to the brief. The private owner specialist uses these owner
instructions and the general tool registry; it does not use the ordinary facet
lab's construction workflow. The ordinary specialist's `{K}` is resolved by its
instruction loader; the table records the default assembled text.

All texts were rewritten in workflow order, with explicit field names and
limitations. The author produced an 88-block statement-to-source audit, including
source hashes. A separate reviewer read the complete texts, tool descriptions,
brief construction, relevant code and owner decisions, returned findings, and
rechecked the corrected texts. Final result: **no remaining findings**.

## Code and decision coverage

| Subject | Code / authority checked |
| --- | --- |
| Photo appearance versus factual brief; later confirmed edit priority | ASSEMBLY D1 and M6; `agent/owner_edits.py`, `g3d/owner_mesh.py` |
| Full-size source/render evidence, crops, frozen cameras and per-photo arm openings | `agent/tools/__init__.py`, `agent/tools/look.py`, `agent/tools/look_panels.py`, `g3d_host/fit_views.py`, `agent/tools/photo_read.py` |
| Measured depth, rounding, solid geometry and lens markings | `g3d/worker.py`, `g3d/temple/measure.py`, `g3d/temple/loft.py`, tool descriptions and the recorded M5 run lessons |
| Specialist applicability and retained tools | `agent/tools/temple.py`, `agent/temple_lab/package.py`, `agent/temple_lab/cli.py`, `agent/tools/__init__.py` |
| Specialist readings, visible-angle evidence, solve/build/compare and finish | `agent/temple_lab/`, `g3d/temple/`; WP6_V3 and its cited WP6_FACETS decisions |
| Fixed reflection policy, exported optical materials, patterned versus plain surfaces | ASSEMBLY D2/D3; `agent/tools/__init__.py`, `agent/tools/lens_response.py`, `agent/tools/photo_print.py`, `g3d/export_glb.py` |
| Seating on all frames, reported rather than a finish gate | ASSEMBLY D4 as amended by M5-fix-2; `agent/tools/__init__.py`, `agent/tools/seating.py` |
| Money belongs to owner; settled/held distinction, shared specialist authority and runway | ASSEMBLY D5 and later money decisions; `agent/budget.py`, `agent/loop.py`, `agent/tools/temple.py`, `agent/temple_lab/cli.py` |
| Two finish calls, delivered images, print freshness, export/spec/AR checks | `agent/tools/__init__.py`, `agent/tools/finish_review.py`, `agent/tools/photo_print.py`, `agent/delivery.py` |
| Understand/stop/confirm/edit separation, supported finite operations and unchanged source | `agent/edits.py`, `agent/owner_edits.py`, `agent/tools/owner_edit.py`, `g3d/owner_mesh.py` |
| Short labels, plain legends and exact capture authority | `agent/tools/model_map.py`, `agent/tools/model_map_labels.py`, `g3d/model_map_bpy.py` |
| Markings discovered, disclosed, sealed and moved with their surfaces | `g3d/owner_markings.py`, `g3d/owner_mesh.py`, `agent/tools/owner_edit.py`, `agent/edits.py` |

The full statement audit identifies exact symbols and source ranges; this table
is a navigation summary. Normative appearance guidance comes from owner decisions
and recorded lessons, not invented numerical acceptance gates. Product names and
fixture measurements appear in proof evidence only, not the instruction texts or
marking/label runtime.

## Findings corrected and rechecked

- Scene warnings are a sibling result field, not a field on each object. The
  automatic matte guidance now includes the optional-lens/open-frame exception.
- Label descriptions become stable at first display. Attachment discovery happens
  at `understand_finish`, whose response seals and lists `following_markings`.
- Owner edits must reproduce the exact confirmed finite operations. Merely staying
  inside a selected region is insufficient. Owner mode cannot refit cameras or
  change material, UVs or topology, and has no general deformation-preview tool.
- Ordinary specialist layout changes include polygon boundaries and corner/node
  membership. Solve guidance now names surrounding constraints and the weak prior.
  Money guidance covers both standalone and nested execution.
- Stored readings are snapshots. Crease states, unresolved visible-angle choices,
  stale evidence and finish/export requirements now match their implementations.
- Crowded labels no longer collide; later leader lines cannot cross opaque text
  boxes. Long legend words wrap without clipping. Native image tiles remain native
  pixels and legends use separate readable pages.
- Marking scope includes the sealed dependent vertices. Unsupported or ambiguous
  attachments, new burial/intersection and old confirmations missing required
  dependencies refuse instead of silently expanding an edit's authority.

## Evidence and limits

The local evidence directory is `data/assembly/m6-6cde/`: `instructions/` contains
all complete assembled texts, both brief suffix templates and
`instruction-audit.json`; `review/final-review.json` binds reviewed production
files and exports to exact hashes, and `review/final-review.md` records the
independent reasoning. The reviewer also ran the instruction/CLI/marking suites
(133 passed), label suite (23 passed) and final mesh/marking candidate (31 passed).
An independent 24-label collision stress image and long-legend check passed.

The reviewer also checked the final `owner-review/` artifact: all three unique
texts and both phase suffix templates match the reviewed exports, all 16 images
match their source hashes, and both lettering composites preserve the exact
native photo/before/after pixels in the correct order. A missing right-arm legend
on the first gallery was corrected by displaying the full sealed legend; two
actual corner maps were also added. All 14 marking dependencies are disclosed
before confirmation. The final artifact review has no remaining findings and is
recorded in `review/artifact-review.md` and its exact-hash JSON companion.

The broad suite exposed an old contract test requiring the specialist's exact
previous prose. Its replacement checks the five shared finish requirements using
the code's actual counts and distances, and explicitly rejects omitted clauses
and the impossible claim that a variants sheet was judged. All behavioral finish
tests remain. The complete `test_temple_lab_v3.py` module passed 74 tests; the
independent supplemental review found no remaining issue. This was a test-only
correction: the reviewed production instruction hashes did not change.

PROGRESS.md records the completed real-tool walkthrough and broad validation.
The scripted walkthrough demonstrates host behavior, not Astra reasoning or owner
design approval. Clean review means that no finding remains after these passes;
it is not a claim of mathematical perfection. Actual images still determine
lettering legibility and occlusion by other objects.
