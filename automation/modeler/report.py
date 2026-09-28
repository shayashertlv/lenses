"""Result report of a finished job: manifest + journal + candidates -> REPORT.md beside the manifest.

    python -m modeler.report --job data/modeler/jobs/<name>

The report states what was delivered, what was measured, what the evaluator saw, what the author claimed, every
candidate's fate, interventions and provenance. It never upgrades a status.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def fmt(v, nd=3):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def report(job_dir: Path) -> str:
    job_dir = Path(job_dir)
    m = load(job_dir / "manifest.json")
    if m is None:
        raise FileNotFoundError("manifest.json missing: the job did not finish")
    protocol = load(job_dir / "evaluation_protocol.json") or {}
    lines = [f"# {m['product_id']} — modeler job `{m['job']}` ({m['written']})", ""]
    sd = m["status_detail"]
    by = f", accepted by {sd['accepted_by']}" if sd.get("accepted_by") else ""
    lines += [f"**Status: `{m['status']}`** (automatic visual bar calibrated: {m['visual_bar_calibrated']}{by}).",
              "Reasons: " + ("; ".join(sd.get("reasons") or []) or "none recorded") + "."]
    if m.get("tags"):
        cov = m.get("calibration_coverage") or {}
        lines.append("Tags: " + ", ".join(f"`{t}`" + (" (owner verdicts: {a} accept / {r} reject{d})".format(
            a=c.get("accepts", 0), r=c.get("rejects", 0), d=", disagreements" if c.get("disagreements") else "")
            if (c := cov.get(t)) else " (no owner verdict yet)") for t in m["tags"]) + ".")
    lines.append("")
    ov = m.get("owner_verdict")
    if ov:
        lines += ["## Owner verdict",
                  f"* **{ov['verdict']}** on {ov['when'][:10]} in the {ov['medium']}, judging asset `{ov['asset_sha256'][:12]}…`"
                  + (f": \"{ov['note']}\"" if ov.get("note") else "") + ".",
                  f"* Status before the verdict: `{ov['status_before']}`"
                  + ("; automatic reasons then: " + "; ".join(sd.get("previous_reasons") or []) if sd.get("previous_reasons") else "") + ".",
                  "* The independent evaluator's verdict below is unchanged; the pair is one row of the calibration set "
                  "`data/modeler/calibration/owner_verdicts.jsonl`.", ""]
    stage = protocol.get("intake_stage") or {}
    if stage.get("stage"):
        ev = load(job_dir / "evidence" / "evidence.json") or {}
        lines += ["## Intake reading (vision) and measurement review",
                  f"* Reading: {(stage.get('reading') or {}).get('status')}; review: {(stage.get('review') or {}).get('status')}"
                  + ("; re-measured after the reading" if stage.get("remeasured") else "") + "."]
        for act in stage.get("actions") or []:
            lines.append(f"* {act}")
        for p in ev.get("provenance") or []:
            lines.append(f"* `{p['field']}`: code {json.dumps(p['code'])[:60]} -> vision {json.dumps(p['vision'])[:60]} ({p['reason']})")
        for err in stage.get("errors") or []:
            lines.append(f"* error: {err}")
        lines.append("")
    a = m.get("asset")
    if a:
        lines += ["## Delivered asset", f"* `{a['path']}` — sha256 `{a['sha256']}`, {a['bytes']} bytes, {a['triangles']} triangles, contract ok = {a['contract']['ok']}.",
                  f"* Delivered candidate `{m['delivered_candidate']}` by rule: {m['delivery_rule']}.",
                  f"* Mounting: {m['mounting']['units']}, up {m['mounting']['up']}, front {m['mounting']['front']}, origin {m['mounting']['origin']}; measured front width {m['mounting']['front_width_mm_measured']} mm; recommended temple clip {m['mounting']['temple_clip_z_m_recommended']} m.",
                  f"* Scale: {m['scale'].get('source')} {m['scale'].get('front_width_mm')} mm (uncertainty {m['scale'].get('uncertainty_mm')} mm) — {m['scale'].get('note')}", ""]
    else:
        lines += ["## Delivered asset", "None: no candidate passed the contract and the AR renderer.", ""]
    meas = m.get("measurements") or {}
    av, ho = meas.get("author_visible") or {}, meas.get("held_out") or {}
    lines += ["## Measurements (delivered candidate)", "| metric | value |", "|---|---|"]
    for k in ("front_iou", "front_contour_mean_mm", "front_contour_p95_mm", "lens_outline_mean_mm", "lens_outline_p95_mm", "back_contour_mean_mm",
              "side_contour_mean_mm", "mean_contour_mm_all_fit_views", "ar_runtime_compatible", "ar_optical_meshes", "ar_continuity_failure"):
        if k in av:
            lines.append(f"| {k} | {fmt(av[k])} |")
    if ho:
        lines.append(f"| held-out ({', '.join(protocol.get('held_out_views', []))}) contour mean mm | {fmt(ho.get('mean_contour_mm_all_fit_views'))} |")
    prov = sd.get("provisional") or {}
    if prov:
        lines += ["", "Gates and report-only levels" + (f" ({sd['threshold_provenance']})" if sd.get("threshold_provenance") else " (v1 provisional thresholds)") + ":",
                  "| check | value | limit mm | pass | gate |", "|---|---|---|---|---|"]
        for k, v in prov.items():
            lines.append(f"| {k} | {fmt(v['value'])} | {v.get('limit_mm')} | {v['pass']} | {v.get('gate', True)} |")
    lines.append("")
    baselines = sorted(job_dir.glob("baselines/*/baseline.json"))
    if baselines:
        lines += ["## Same-protocol comparison with previous routes",
                  "Measured with this job's cameras, photos, metrics and AR harness (`modeler.baseline`); lower is better.", "",
                  "| asset | front mm | visible lens mm | back mm | sides mm | all fit views mm | held-out mm | AR | continuity |", "|---|---|---|---|---|---|---|---|---|"]

        def row(name, s, h):
            return (f"| {name} | {fmt(s.get('front_contour_mean_mm'))} | {fmt(s.get('lens_outline_mean_mm'))} | {fmt(s.get('back_contour_mean_mm'))} | "
                    f"{fmt(s.get('side_contour_mean_mm'))} | {fmt(s.get('mean_contour_mm_all_fit_views'))} | {fmt((h or {}).get('mean_contour_mm_all_fit_views'))} | "
                    f"{s.get('ar_runtime_compatible')} | {'failed' if s.get('ar_continuity_failure') else 'ok'} |")
        if av:
            lines.append(row(f"this job, delivered `{m['delivered_candidate']}`", av, ho))
        for p in baselines:
            b = load(p) or {}
            lines.append(row(b.get("name", p.parent.name), b.get("summary") or {}, b.get("held_out")))
        lines.append("")
    ev = m.get("evaluation")
    lines += ["## Independent evaluation"]
    if ev:
        lines += [f"Overall: **{ev['overall']}**. {ev.get('summary', '')}", ""]
        if ev.get("resemblance_0_10"):
            lines.append("Resemblance (0-10): " + ", ".join(f"{k} {v:g}" for k, v in ev["resemblance_0_10"].items()) + ".")
        if ev.get("discrepancies"):
            lines += ["", "Discrepancies:"]
            for d in ev["discrepancies"]:
                lines.append(f"* [{d['severity']}] {d['part']} / {d['view']}: {d['description']}")
        if ev.get("identity_checklist"):
            lines += ["", "Identity checklist:"]
            for c in ev["identity_checklist"]:
                lines.append(f"* {c['verdict']}: {c['feature']}" + (f" — {c['note']}" if c.get("note") else ""))
        if ev.get("runtime_notes"):
            lines += ["", f"Runtime notes: {ev['runtime_notes']}"]
    else:
        lines.append("No independent evaluation recorded.")
    lines.append("")
    v2 = job_dir / "candidates" / str(m.get("delivered_candidate")) / "evaluation_v2" / "evaluation.json" if m.get("delivered_candidate") else None
    if v2 and v2.exists():
        e2 = load(v2) or {}
        ev2, meta2 = e2.get("evaluation") or {}, e2.get("meta") or {}
        majors2 = [d for d in ev2.get("discrepancies", []) if d.get("severity") == "major"]
        lines += [f"## Evaluation under the current protocol ({meta2.get('protocol', 'evaluation_v2')}, {str(meta2.get('collected', ''))[:10]})",
                  f"Overall: **{ev2.get('overall')}**, {len(majors2)} major. {ev2.get('summary', '')}", ""]
        if ev2.get("resemblance_0_10"):
            lines.append("Resemblance (0-10): " + ", ".join(f"{k} {v:g}" for k, v in ev2["resemblance_0_10"].items()) + ".")
        for d in ev2.get("discrepancies", []):
            lines.append(f"* [{d['severity']}] {d['part']} / {d['view']}: {d['description']}")
        lines += ["", "The section above is the evaluation the job ran at the time; this one re-judges the same asset with the evaluator "
                  "protocol in force now (`modeler.evaluate_asset`), for the calibration set.", ""]
    fin = m.get("author_finish")
    lines += ["## Author's finish", (f"claim `{fin['status_claim']}`, deliver `{fin['deliver']}`: {fin.get('note', '')}" if fin else "The author did not finish (turn or time limit, or a failure)."), ""]
    lines += ["## Candidates", "| id | turn | changed modules | status | valid | score mm | front mm | lens mm | AR |", "|---|---|---|---|---|---|---|---|---|"]
    for c in m.get("candidates", []):
        mt = c.get("metrics") or {}
        err = c.get("build_error") or {}
        status = c["status"] + (f" ({err.get('module')}: {str(err.get('error'))[:60]})" if err else "")
        lines.append(f"| {c['id']} | {c.get('turn')} | {', '.join(c.get('changed_modules') or [])} | {status} | {c.get('valid')} | {fmt(c.get('score_mm'))} | "
                     f"{fmt(mt.get('front_contour_mean_mm'))} | {fmt(mt.get('lens_outline_mean_mm'))} | {mt.get('ar_runtime_compatible', 'n/a')} |")
    lines.append("")
    turns = (m.get("provenance") or {}).get("turns") or []
    lines += ["## Turns", "| turn | decision | candidate | valid | score mm | author s | rationale |", "|---|---|---|---|---|---|---|"]
    for t in turns:
        lines.append(f"| {t['turn']} | {t['decision']} | {t.get('candidate', '')} | {t.get('valid', '')} | {fmt(t.get('score_mm'))} | {t.get('author_seconds')} | {(t.get('rationale') or '').replace('|', '/')[:160]} |")
    lines.append("")
    pv = m.get("provenance") or {}
    lines += ["## Provenance and interventions",
              f"* Author driver: `{pv.get('author_driver')}`; author inputs: {pv.get('author_inputs')}",
              f"* Evaluator inputs: {pv.get('evaluator_inputs')}",
              f"* Donor assets: {pv.get('donor_assets')}; previously solved assets: {pv.get('previously_solved_assets')}",
              f"* Inputs: " + "; ".join(f"{r['id']} ({r['view']}{', held out' if r['held_out'] else ''}) sha256 {r['sha256'][:12]}…" for r in (m.get('evidence') or {}).get('inputs', [])),
              f"* Runtime: {m['runtime']['elapsed_min']} min wall, {m['runtime']['turns']} turns.",
              ""]
    audits = sorted(job_dir.glob("turns/turn-*/author_audit.json")) + sorted(job_dir.glob("evaluation/*_audit.json"))
    if audits:
        lines += ["Transcript audits (paths the answering agent touched outside its package):"]
        for p in audits:
            a = load(p) or {}
            lines.append(f"* {p.parent.name}: {a.get('tool_calls')} tool calls, clean = {a.get('clean')}, outside = {len(a.get('outside_package') or [])}")
        lines.append("")
    sheets = []
    if m.get("delivered_candidate"):
        od = job_dir / "candidates" / m["delivered_candidate"] / "observe"
        for name in ("sheet_photo_match.png", "sheet_clay.png", "sheet_textured.png", "sheet_ar.png"):
            if (od / name).exists():
                sheets.append(str(od / name))
        if (job_dir / "evaluation" / "sheet_heldout_match.png").exists():
            sheets.append(str(job_dir / "evaluation" / "sheet_heldout_match.png"))
    if sheets:
        lines += ["## Evidence images", *[f"* `{s}`" for s in sheets], ""]
    lines += ["## Limits of this evidence",
              "* The scale is nominal unless a physical dimension was supplied; millimetre metrics compare shapes at that nominal scale.",
              "* Metrics measure silhouettes and lens outlines from fitted cameras; they do not measure material finish, bevel profile or hardware detail, which only the evaluator and the images cover.",
              "* AR renders use the harness's synthetic canonical face pose; no real wearer or device test is included.",
              "* The AR 'back' panel is the harness's asset-back inspection (asset rotated 180 degrees, occluders hidden): the canonical "
              "front_sheet_v1 lens is single-sided, so lenses vanish and rims show their inner faces there by the runtime's design; "
              "findings about tint or thin rims in that panel describe the runtime, not the candidate.",
              "* EEVEE preview sheets (photo-match, textured) are exact for shape but approximate for colour and lens transparency; "
              "the AR front/angled/rolled panels are authoritative for materials and optics.",
              "* A job awards `accepted` on its own only with an automatic visual bar calibrated against owner verdicts, which does not "
              "exist yet; an `accepted` with `accepted_by: owner` records the owner's own judgement of the delivered asset "
              "(`modeler.owner_verdict`).", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", type=Path, required=True)
    args = ap.parse_args(argv)
    text = report(args.job)
    out = args.job / "REPORT.md"
    out.write_text(text, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
