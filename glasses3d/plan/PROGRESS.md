# glasses3d progress

## Active resume — WP6 (2026-10-08)

The owner requested a new-session handoff for **resuming WP6**, including removal of obsolete instructions.
This is an explicit handoff request, not a measured 80% context event. No run is active.

Read [WP6_HANDOFF.md](WP6_HANDOFF.md) fully, then [WP6_DESIGN.md](WP6_DESIGN.md). Use
[WP6_SESSION_PROMPT.md](WP6_SESSION_PROMPT.md) to begin the next session. The next agent is authorized to
continue offline WP6 diagnosis and repairs under unchanged gates. Start with the saved S1 instrument-floor
failure, reproduce it from saved inputs and isolate its cause before another full product fit.
**STOP-I blocks acceptance and downstream promotion, not diagnosis or repairs intended to resolve it.**
KEEP R1 and every frozen R3 clause; no waiver. Do not repeat the resolved waiver decision.

## Current status

| Work | State |
|---|---|
| WP0 / WP1 / WP3 / WP3b / WP4 | Complete and tested |
| A–E repairs, option-B promotion | Complete and verified; do not redo |
| WP5 reusable library G1 | Complete and verified at `f020b57` |
| WP5 | Complete by owner visual acceptance; observed-slope join + Poisson default at `4304c1d`. Historical numerical failures remain recorded. |
| WP6 | Core/proof infrastructure built; STOP-I still fails. This is the next active repair task. |
| WP2 | A–C verified; D–F and agent wrappers remain unfinished/gated |
| WP7 / WP8 | Design only / not started |

## Latest committed checkpoints

- `4304c1d`: exact owner-selected observed-slope + Poisson default and WP5 closeout.
- `d5e5917`: code review/cleanup for WP6: accurate CLI failure receipts, explicit-camera refusal, cache ignore
  correction, current module map and resolved R1 diagnostic wording.
- The documentation-only commit containing this handoff follows `d5e5917`: active guidance now targets WP6;
  superseded progress/resume text is archived, the obsolete WP5 session prompt is removed, and WP2's stale WP5
  prerequisite wording is corrected. Runtime, thresholds and local evidence are unchanged.

## Validation already completed

WP5 default: **54 focused tests passed**, followed by a production bake reproducing the approved PNG exactly
in **243.53 s**. The viewed TOOLTEST copy already passed all 15 Blender/export checks. See
[WP5_HANDOFF.md](WP5_HANDOFF.md) for the closed record and honest proof limitations. Do not redo G1 or reopen WP5.

WP6 readiness at `d5e5917`: **227 Python tests passed, 2 existing strict xfails**, no skips (541.85 s).
AR type-check passed, **500/500 unit tests** and **106/106 QA tests** passed. R1 wording was separately rechecked:
one expected failure, exit 0. The xfails preserve R1 calibration and F1-rise identifiability failures; they are
not quality passes. Static review covered 294 Python files / 108 runtime modules. Runtime/frozen-input and
approved-asset hash audits passed. Logs: `data/readiness_20261008/`; PID 3344 finished 19:46:42 +03, exit 0.
Do not rerun the completed suites merely to resume; rerun affected checks after an implementation change.

## Handoff verification and history

**48 handoff checks passed.** `data/handoff_20261008/audit.json` records documentation links, snapshot fidelity,
frozen/evidence hashes, saved S1 values, runtime unchanged and current instruction routing. No algorithm test
or paid run was needed for this documentation-only handoff. Review the commit and working-tree status before
beginning work.

The long mixed progress log and superseded prompts are preserved in [the archive](archive/20261008/README.md).
Historical instructions are not active; consult specific evidence there only when needed. Never apply an old
"RESUME HERE", start a retired workflow, or revert to an earlier texture by following an archived instruction.
