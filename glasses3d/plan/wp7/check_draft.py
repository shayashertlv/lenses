"""Scratch check for a WP7 draft (not the contract test; a $0 measurement of the draft text).

Usage: python check_draft.py <draft.md>
Measures: token estimate per assembly, removed-phrase hits, tool/helper/check inventory against the WP2 §10 registry,
rule-has-check, form rule, and shared n-grams against the old instruction corpus (paths relative to the repo's parent).
"""
import glob
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))          # .../lenses
PARENT = os.path.dirname(REPO)

REGISTERED = {"execute_blender_code", "scene_summary", "save_checkpoint", "list_checkpoints", "restore_checkpoint",
              "seat_lens", "hide_eyewire", "unseat_lens", "unhide_eyewire", "check_lens_seating",
              "configure_lens_coating", "load_font", "render_views", "pick_surface_point", "fit_cameras",
              "compare_to_photos", "export_glb", "arview_capture", "read_image", "read_report", "budget_status",
              "deliver"}
GATED = {"G1": {"apply_photo_print", "remove_photo_print", "check_print"},
         "G2": {"faceted_temple", "check_relief", "trace_temple", "fit_temple"}}
HELPERS = {"polygon_section", "loft_sections", "check_round_corners"}
PHANTOMS = {"photo_x_range", "project_photo_texture", "attach_print", "detach_print", "print_uv", "validate_spec",
            "compare_temple_views"}

REMOVED = [r"hypothes[ie]s", r"generated\W+(\w+\W+){0,3}(view|image|photo)s?", r"(view|image|photo)s?\W+(\w+\W+){0,3}generated",
           r"supplementar", r"unverified", r"weaker evidence", r"original (photo|view|source|image)",
           r"primary (evidence|target)", r"secondary", r"provenance", r"rounded tubes?", r"tube_along_path", r"never square",
           r"keep\W+(\w+\W+){0,3}simple", r"simple plausible", r"simplify unobserved", r"before decorative",
           r"decorative detail", r"a (line|band|streak)\W+(\w+\W+){0,4}may be", r"may be (a )?reflect",
           r"reflected photo streak", r"partly illumination", r"camera is a", r"artist interpretation",
           r"source rgb is used only", r"lenses_part_visibility", r"part_visibility", r"spending target", r"finali[sz]e",
           r"leave room for", r"preserve the best candidate", r"checkpoint_candidate", r"select_candidate", r"record_review"]
REQUIRED = ["room reflections", "authoritative", "closed", "colourway", "few millimetres", "mdl_bridge_underside",
            "partRole"]
NGRAM_ALLOW = ["y up z front", "bridge underside", "numeric millimetres", "closed 2 manifold", "lens width bridge"]

HDR = re.compile(r"<!-- block: (\S+) \| describes: (.*?) \| requires: (.*?) \| gate: (\S+) -->")
TOOL = re.compile(r"`([a-z][a-z0-9_]*)\(")
HELP = re.compile(r"`g3d\.([a-z][a-z0-9_]*)")
CHECK = re.compile(r"`(?!g3d\.)([a-z][a-z_]*)\.([a-z_]+)`")


def names(s):
    return set() if s.strip() == "-" else {x.strip() for x in s.split(",") if x.strip()}


def parse(md):
    start = md.index("<!-- block:")
    end = md.index("<!-- end of instruction text -->")
    body = md[start:end]
    blocks = []
    parts = HDR.split(body)
    for i in range(1, len(parts), 5):
        blocks.append({"name": parts[i], "describes": names(parts[i + 1]), "requires": names(parts[i + 2]),
                       "gate": parts[i + 3], "text": parts[i + 4].strip("\n")})
    return blocks


def rules(text):
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("- "):
            cur = [line]
            out.append(cur)
        elif line.startswith("  - ") or not line.strip() or line.startswith("#"):
            cur = None
        elif cur is not None and line.startswith("  "):
            cur.append(line)
    return [" ".join(x.strip() for x in r) for r in out]


def norm(s):
    s = unicodedata.normalize("NFKC", s).lower().replace("`", "").replace("**", "")
    return re.sub(r"[^a-z0-9±°□]+", " ", s).strip()


def ngrams(words, n):
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def corpus_files():
    pats = [os.path.join(REPO, "automation/data/blender_agent/studio/jobs/*/agent/runs/*/instructions.txt"),
            os.path.join(PARENT, "lenses-recovery/automation-cleanup-*/moved/data/blender_agent/*/agent/runs/*/instructions.txt"),
            os.path.join(PARENT, "lenses-recovery/automation-cleanup-*/source-before/blender_agent/instructions.py"),
            os.path.join(PARENT, "lenses-recovery/automation-cleanup-*/source-before/blender_agent/studio_jobs.py"),
            os.path.join(PARENT, "lenses-recovery/automation-cleanup-*/source-before/blender_agent/budget.py"),
            os.path.join(PARENT, "lenses-recovery/automation-cleanup-*/source-before/blender_agent/studio_description.py"),
            os.path.join(REPO, "automation/modeler/author.py"),
            os.path.join(REPO, "automation/modeler/agentic/AUTHOR_PROMPT.md"),
            os.path.join(REPO, "automation/modeler/evaluate.py")]
    files = []
    for p in pats:
        files += glob.glob(p)
    return sorted(set(files))


def main(path):
    md = open(path, encoding="utf-8").read()
    blocks = parse(md)
    res = {"blocks": {}, "errors": [], "assemblies": {}}
    for b in blocks:
        refs = set(TOOL.findall(b["text"]))
        helps = set(HELP.findall(b["text"]))
        checks = {".".join(m) for m in CHECK.findall(b["text"])}
        rl = rules(b["text"])
        for r in rl:
            if "**Check:**" not in r:
                res["errors"].append(f"{b['name']}: rule without Check: {r[:70]}")
            elif not CHECK.search(r.split("**Check:**", 1)[1]) and not HELP.search(r.split("**Check:**", 1)[1]):
                res["errors"].append(f"{b['name']}: Check clause names no check id: {r[:70]}")
            if not TOOL.search(r) and not HELP.search(r):
                res["errors"].append(f"{b['name']}: rule names no tool: {r[:70]}")
        extra = refs - b["describes"] - b["requires"]
        if extra:
            res["errors"].append(f"{b['name']}: tool refs outside describes/requires: {sorted(extra)}")
        for t in b["describes"] | b["requires"]:
            if t not in REGISTERED and not any(t in g for g in GATED.values()):
                res["errors"].append(f"{b['name']}: phantom tool {t}")
        for t in helps - HELPERS:
            res["errors"].append(f"{b['name']}: unknown helper {t}")
        # form rule: every tool-name occurrence must be a tool reference
        alltools = REGISTERED | set().union(*GATED.values()) | PHANTOMS
        for t in alltools:
            for m in re.finditer(r"(?<![A-Za-z0-9_.])" + t + r"(?![A-Za-z0-9_])", b["text"]):
                a, z = m.start(), m.end()
                ok = b["text"][a - 1:a] == "`" and b["text"][z:z + 1] == "("
                if not ok:
                    res["errors"].append(f"{b['name']}: form rule: '{t}' not written as `{t}(`")
        res["blocks"][b["name"]] = {"rules": len(rl), "tools": sorted(refs), "helpers": sorted(helps),
                                    "checks": sorted(checks), "chars": len(b["text"])}

    for label, open_g in (("closed", set()), ("G1", {"G1"}), ("G2", {"G2"}), ("both", {"G1", "G2"})):
        avail = REGISTERED | set().union(*(GATED[g] for g in open_g)) if open_g else set(REGISTERED)
        inc = [b for b in blocks if (b["gate"] == "-" or b["gate"] in open_g) and (b["describes"] | b["requires"]) <= avail]
        text = "\n\n".join(b["text"] for b in inc)
        described = [t for b in inc for t in b["describes"]]
        missing = sorted(avail - set(described))
        dup = sorted({t for t in described if described.count(t) > 1})
        closed_names = set().union(*(GATED[g] for g in GATED if g not in open_g)) if len(open_g) < 2 else set()
        leaked = sorted(t for t in closed_names if re.search(r"(?<![A-Za-z0-9_])" + t + r"(?![A-Za-z0-9_])", text))
        removed = [p for p in REMOVED if re.search(p, text, re.I)]
        absent = [p for p in REQUIRED if p.lower() not in text.lower()]
        words = len(text.split())
        res["assemblies"][label] = {"blocks": [b["name"] for b in inc], "chars": len(text), "words": words,
                                    "tokens_est_chars4": round(len(text) / 4), "tokens_est_words": round(words * 1.33),
                                    "undescribed_available": missing, "described_twice": dup, "closed_gate_leaks": leaked,
                                    "removed_phrase_hits": removed, "required_absent": absent}
        if label == "both":
            full = text

    files = corpus_files()
    cw = []
    for f in files:
        try:
            cw.append(norm(open(f, encoding="utf-8", errors="replace").read()).split())
        except OSError:
            pass
    dw = norm(full).split()
    out = {}
    for n in (5, 6, 8):
        cg = set()
        for w in cw:
            cg |= ngrams(w, n)
        shared = sorted(g for g in ngrams(dw, n) & cg if not any(a in g for a in NGRAM_ALLOW))
        out[n] = shared
    res["corpus_files"] = len(files)
    res["shared_8grams"] = out[8]
    res["shared_6grams"] = out[6]
    res["shared_5grams"] = out[5]
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
