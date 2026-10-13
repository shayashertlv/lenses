"""The facet lab's persistent state (``work/temple_lab/state.json``) and the host's procedural rules over it
(plan/WP6_V3.md §6, on plan/WP6_FACETS.md §4.1): the judgement rule (E_JUDGEMENT), the object note and its rule
(E_OBJECT), and the finish preconditions (finish_missing: one head-distinctness rule, geometry.distinct_heads of the
built temples, for preconditions 1 and 5; only compares and variants that reached Astra count). The host logs
judgements and checks procedure; it never grades quality. (The top photo's outline line, information only since the
owner's 2026-10-11 decision and out of Astra's view in v3, WP6_V3 P5, was deleted 2026-10-13; see git tag
review-20261013-pre-cleanup.) Lab mutates and saves the state only under its state lock (Lab.state_lock).

State (schema g3d.temple_lab_state/3; /3 = WP6 v3: readings and object notes, judgements with up to 3 differences per
photo; a /2 state is refused by load, its revisions carry no fit):
    revisions   [{id, parent, sha256, file, kind: template | edit | variant, label, ops, note, call_id}] (one per
                distinct design spec: a spec built again keeps its revision); revision 0 is the template of the original
    heads       [{checkpoint, fingerprint, revision, call_id, seq?}]: every scene a revision was applied to; seq = the
                state's seq when it was applied (orders it against compares and variants: precondition 5)
    compares    [{compare_id, checkpoint, fingerprint, revision, photos, stacked?, by: prepare | astra, call_id,
                report, seq, judged, unseen?}]: Astra must judge the latest "astra" compare it was shown on its next
                action tool; a compare made by a call the SDK timed out is "unseen" (never shown, never judged);
                photos = every photo shown (zoomed ones included: the judgement covers them), stacked = those shown in
                full (shown_in_full: what facet_variants and finish preconditions 1 and 3 count)
    judgements  [{call_id, tool, compare_id, versus_compare_id, overall, photos: [{photo, verdict, differences:
                [{what, d_mm, y_mm}] (0..DIFFERENCES_MAX)}], best_checkpoint, revision, compare_revision}]: one per
                compare (a judgement when none is due is not stored: check_judgement)
    variants    [{id, call_id, parent, photos, seq, items: [{label, revision | null, code | null, ...}], pairwise:
                {"A|B": mm}, rendered: [labels rendered at every photo], spread: [3 rendered labels] | null, unseen?}]:
                an unseen result (a call the SDK timed out) never meets finish precondition 2 or 5
    readings    {name: readings.read_point record}: photo_read's stored readings (a reading replaces one of its name;
                set_node t mode "reading" takes the one of the node's name, geometry.apply_edits)
    objects     [{text, call_id, layout, parent?, revision?}]: every object note in order (record_object);
                object_text = the latest; layout = layout_key of the design the call made, or null; parent = the
                revision the call edited, revision = the revision its design became (object_origin)
    images      {sha256: {kind, ref, revision, file}} of every image returned to Astra (the context guard's index)
    inherited   {revision, core, blender, hinge_leaf_gap_mm}: revision 0's warnings at prepare (lab.py module doc)
    events      SceneHooks journal entries; finish (the accepted facet_finish; finish_unseen: one the SDK timed
                out, which never ends the run); best_checkpoint; seq (compare / variants ids)

The object note (WP6_V3 §6, facet_edit ``object``, <= OBJECT_MAX characters): the faces, corners, edges with shape and
evidence, where the faceting ends, what is uncertain. Why: Astra misread how faces end and which feature a pick is
on (FINDINGS §0.6); writing the object down before building it is the prompt's step 2. It is required on the first
facet_edit and whenever the model's layout changes (``object_due``): a design whose layout differs from the one the
latest note was written for needs a new note, whatever its parent (applying a variant arm or an older revision is a
facet_edit of it with no edits), so the note facet_inspect shows describes the head's layout. The layout
(``layout_key``) is the node names and each facet's label and polygon (a polygon read the same from any start or
either way round); node moves, t values, plane modes, soft borders and crease declarations are not layout (they are
what readings and brackets settle, and a refusal for each would cost a call: 22 % of v2 spend went on refused calls,
FINDINGS §0.7). E_OBJECT carries ``latest_object``: the revision the latest note was given with (object_origin; an
SDK call id named nothing Astra could map) and how this design's layout differs from it (layout_diff).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from glasses3d.agent.procs import write_json_atomic

STATE_SCHEMA = "g3d.temple_lab_state/3"
VERDICTS = ("better", "same", "worse", "mixed")
DIFFERENCE_MAX = 200             # characters of one named difference
DIFFERENCES_MAX = 3              # differences named per photo of a judgement (WP6_V3 §6: the 3 largest)
OBJECT_MAX = 3000                # characters of the object note (WP6_V3 §6)
OBJECT_REFUSAL = "write the object note (facet_edit object) first"       # E_OBJECT (WP6_V3 §6, I4)
READ_POINTS_MAX = 8              # photo_read: points per call (WP6_V3 §6)
READ_DROP_MAX = 64               # photo_read: names dropped per call
ZOOM_MAX = 3                     # photo_compare: zoom boxes per call (= agent/seeing.py ZOOM_MAX; a test pins it)
SCALE_MAX = 3                    # photo_image: the largest upscale of a crop
STUCK_LAST = 3
# = geometry.distinct_heads (HEAD_DISTINCT_MM; the cli test pins the number)
DIFFERENT_ATTEMPT = "the built temple surface or outline moves by at least 0.2 mm somewhere"
LAYOUT_NAMES_MAX = 6             # layout_diff: names listed per kind before "+N more" (= geometry.DIFF_NAMES_MAX)
SPREAD_VARIANTS = 3              # finish precondition 2: rendered variants pairwise apart (geometry.spread_triple) ...
SPREAD_MM = 0.3                  # ... by at least this (= geometry.VARIANT_DISTINCT_MM; state imports no geometry, a
#                                  test pins the two)


class LabRefusal(ValueError):
    """A procedural refusal returned to Astra: code E_JUDGEMENT, E_OBJECT, E_FINISH, E_ARGS, E_REVISION..."""

    def __init__(self, code: str, message: str, **fields):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.fields = code, message, fields

    def to_dict(self) -> dict:
        return {"status": "refused", "code": self.code, "message": self.message, **self.fields}


def new_state(rig: str) -> dict:
    return {"schema": STATE_SCHEMA, "rig": rig, "start_checkpoint": None, "start_fingerprint": None, "original": None,
            "revisions": [], "heads": [], "compares": [], "judgements": [], "variants": [], "readings": {},
            "objects": [], "images": {}, "events": [], "best_checkpoint": None, "finish": None, "finish_refusals": 0,
            "seq": 0}


def load(path: Path, rig: str) -> dict:
    path = Path(path)
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") != STATE_SCHEMA:
            raise ValueError(f"{path}: state schema {state.get('schema')!r} is not {STATE_SCHEMA}")
        return state
    return new_state(rig)


def save(path: Path, state: dict) -> None:
    write_json_atomic(path, state)


# --------------------------------------------------------------------------------------------- lookups
def revision(state: dict, rid) -> dict:
    if isinstance(rid, bool) or not isinstance(rid, int) or not 0 <= rid < len(state["revisions"]):
        raise LabRefusal("E_REVISION", f"there is no revision {rid!r}; revisions are 0..{len(state['revisions']) - 1}")
    return state["revisions"][rid]


def head_by_fingerprint(state: dict, fingerprint: str | None) -> dict | None:
    for h in reversed(state["heads"]):
        if h["fingerprint"] == fingerprint:
            return h
    return None


def head_of_checkpoint(state: dict, checkpoint, fingerprint_of) -> dict | None:
    """The applied head a checkpoint id names (best_checkpoint in a judgement or a finish): the latest heads entry made
    AT that checkpoint, else (a checkpoint no apply made, e.g. a restore's) the latest entry with its fingerprint
    (head_by_fingerprint; ``fingerprint_of(checkpoint id)`` -> its fingerprint or None). Why (v3p3 e2e P3-1): two
    designs with one fitted spec build one scene, so their checkpoints share a fingerprint; resolved by fingerprint,
    c0015 (revision 6, compared at all 9 photos) named revision 10, a re-read applied later at c0017, and the finish
    was refused for want of a full compare of revision 10."""
    if not isinstance(checkpoint, str):
        return None
    for h in reversed(state["heads"]):
        if h["checkpoint"] == checkpoint:
            return h
    return head_by_fingerprint(state, fingerprint_of(checkpoint))


def astra_compares(state: dict) -> list[dict]:
    return [c for c in state["compares"] if c["by"] == "astra"]


def seen_compares(state: dict) -> list[dict]:
    """Astra's compares that reached it (not those of a call the SDK timed out)."""
    return [c for c in astra_compares(state) if not c.get("unseen")]


def seen_variants(state: dict) -> list[dict]:
    """The facet_variants results that reached Astra (not those of a call the SDK timed out)."""
    return [v for v in state["variants"] if not v.get("unseen")]


def compare(state: dict, compare_id: str) -> dict | None:
    return next((c for c in state["compares"] if c["compare_id"] == compare_id), None)


def pending_compare(state: dict) -> dict | None:
    """The latest compare Astra was shown and has not judged yet."""
    mine = seen_compares(state)
    return mine[-1] if mine and not mine[-1]["judged"] else None


# --------------------------------------------------------------------------------------------- judgements
def _differences(pid: str, diffs) -> list[dict]:
    """One photo's named differences, checked (0..DIFFERENCES_MAX of {what, d_mm, y_mm}) and normalised."""
    if not isinstance(diffs, list) or len(diffs) > DIFFERENCES_MAX:
        raise LabRefusal("E_JUDGEMENT", f"photo {pid}: differences must list at most {DIFFERENCES_MAX} "
                                        "{what, d_mm, y_mm}")
    out = []
    for k, x in enumerate(diffs):
        what = x.get("what") if isinstance(x, dict) else None
        if not isinstance(what, str) or not what.strip() or len(what) > DIFFERENCE_MAX:
            raise LabRefusal("E_JUDGEMENT", f"photo {pid}: differences[{k}].what must be 1..{DIFFERENCE_MAX} "
                                            "characters")
        at = []
        for key in ("d_mm", "y_mm"):
            v = x.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise LabRefusal("E_JUDGEMENT", f"photo {pid}: differences[{k}].{key} must be a finite number")
            at.append(float(v))
        out.append({"what": what, "d_mm": at[0], "y_mm": at[1]})
    return out


def check_judgement(state: dict, judgement: dict | None, *, fingerprint_of) -> dict | None:
    """The E_JUDGEMENT rule (WP6_V3 §6): after a compare, the next action tool carries a judgement naming the latest
    compare_id, with one entry per photo of that compare (its verdict and up to DIFFERENCES_MAX differences, each with
    its (d, y): the 3 largest; none where the photo shows none) and a best_checkpoint that holds an applied revision.
    ``fingerprint_of(checkpoint id)`` -> its fingerprint or None. Returns the normalised record (not yet stored) or
    None: no judgement and none due, or a judgement when none is due (the latest seen compare is judged already, e.g.
    by the first try of a retried call: the lab records the judgement before its edit can be refused). Such a judgement
    is not stored again, so the judgements stay one per compare: three copies of one head's judgement filled finish
    precondition 5's last STUCK_LAST (review 2026-10-12; package_003's state.json holds cmp004 twice from a refused
    facet_edit retried). Unseen compares (a timed-out call's) are never "the latest"."""
    mine = seen_compares(state)
    latest = mine[-1] if mine else None
    if judgement is None:
        if latest is not None and not latest["judged"]:
            raise LabRefusal("E_JUDGEMENT", f"judge compare {latest['compare_id']} first: pass judgement with "
                                            f"compare_id {latest['compare_id']} and one entry per photo "
                                            f"{latest['photos']}", compare_id=latest["compare_id"])
        return None
    if latest is None:
        raise LabRefusal("E_JUDGEMENT", "there is no compare to judge yet; pass judgement null")
    if latest["judged"]:
        return None
    if judgement.get("compare_id") != latest["compare_id"]:
        raise LabRefusal("E_JUDGEMENT", f"judgement.compare_id must be the latest compare, {latest['compare_id']}",
                         compare_id=latest["compare_id"])
    versus = judgement.get("versus_compare_id")
    if versus is not None and (versus == latest["compare_id"] or compare(state, versus) is None):
        raise LabRefusal("E_JUDGEMENT", f"versus_compare_id {versus!r} must be an earlier compare_id or null")
    if judgement.get("overall") not in VERDICTS:
        raise LabRefusal("E_JUDGEMENT", f"overall must be one of {list(VERDICTS)}")
    photos = judgement.get("photos")
    if not isinstance(photos, list):
        raise LabRefusal("E_JUDGEMENT", f"judgement.photos must list one entry per photo {latest['photos']}")
    seen, entries = [], []
    for p in photos:
        pid = p.get("photo") if isinstance(p, dict) else None
        if pid not in latest["photos"] or pid in seen:
            raise LabRefusal("E_JUDGEMENT", f"judgement.photos: {pid!r} is not a photo of {latest['compare_id']} "
                                            f"{latest['photos']} or is repeated")
        if p.get("verdict") not in VERDICTS:
            raise LabRefusal("E_JUDGEMENT", f"photo {pid}: verdict must be one of {list(VERDICTS)}")
        entries.append({"photo": pid, "verdict": p["verdict"], "differences": _differences(pid, p.get("differences"))})
        seen.append(pid)
    missing = [pid for pid in latest["photos"] if pid not in seen]
    if missing:
        raise LabRefusal("E_JUDGEMENT", f"judgement.photos misses {missing} of {latest['compare_id']}",
                         missing=missing)
    best = judgement.get("best_checkpoint")
    head = head_of_checkpoint(state, best, fingerprint_of)
    if head is None:
        raise LabRefusal("E_JUDGEMENT", f"best_checkpoint {best!r} is not a checkpoint holding an applied revision; "
                                        f"applied (latest): {applied_checkpoints(state)[-12:]}")
    return {"compare_id": latest["compare_id"], "versus_compare_id": versus, "overall": judgement["overall"],
            "photos": entries, "best_checkpoint": best, "revision": head["revision"],
            "compare_revision": latest["revision"]}


def judgement_not_due(state: dict, judgement: dict | None) -> str | None:
    """The fact a tool result states when check_judgement returned None for a judgement it was given (none was due:
    not stored), else None: "no judgement was due: the latest compare, cmp017, was judged already by the judgement a
    facet_variants call carried (overall: same; best_checkpoint: c0011); this judgement of cmp017 was not stored (one
    judgement per compare; the next compare makes one due)". The earlier judgement is named by its tool and its own
    words, never the SDK's call id (2026-10-13 blind stand-in: "compare cmp017 was judged already (call s25)" named an
    id it had never seen, and did not say a judgement carried by facet_variants is the compare's judgement)."""
    if judgement is None:
        return None
    mine = seen_compares(state)
    latest = mine[-1] if mine else None
    if latest is None or not latest["judged"]:
        return None
    cid = latest["compare_id"]
    prior = next((j for j in reversed(state["judgements"]) if j["compare_id"] == cid), None)
    by = (f" by the judgement a {prior.get('tool') or 'tool'} call carried (overall: {prior.get('overall')}; "
          f"best_checkpoint: {prior.get('best_checkpoint')})") if prior else ""
    given = judgement.get("compare_id") if isinstance(judgement, dict) else None
    this = f"this judgement of {given[:24]}" if isinstance(given, str) and given.strip() else "this judgement"
    return (f"no judgement was due: the latest compare, {cid}, was judged already{by}; {this} was not stored (one "
            "judgement per compare; the next compare makes one due)")


def record_judgement(state: dict, rec: dict | None, *, call_id: str, tool: str) -> None:
    if rec is None:
        return
    state["judgements"].append({"call_id": call_id, "tool": tool, **rec})
    compare(state, rec["compare_id"])["judged"] = True
    state["best_checkpoint"] = rec["best_checkpoint"]


def applied_checkpoints(state: dict) -> list[str]:
    return [h["checkpoint"] for h in state["heads"]]


# --------------------------------------------------------------------------------------------- the object note
def _ring(poly) -> list[str]:
    """A polygon's corners read one canonical way: from its smallest name, toward the smaller of that corner's two
    neighbours (a polygon listed from another start or the other way round is the same face)."""
    p = [str(n) for n in poly]
    if len(p) < 3:
        return p
    i = p.index(min(p))
    fwd = p[i:] + p[:i]
    back = [fwd[0]] + fwd[1:][::-1]
    return fwd if fwd[1] <= back[1] else back


def layout_key(spec: dict | None) -> dict | None:
    """The layout of a (design or built) spec: {"nodes": sorted node names, "facets": {label: polygon (_ring)}}; None
    for None. Node positions and values, plane modes, soft borders and creases are not layout (module doc)."""
    if spec is None:
        return None
    facets = {}
    for label, f in (spec.get("facets") or {}).items():
        poly = f["poly"] if isinstance(f, dict) else f
        facets[str(label)] = _ring(poly)
    return {"nodes": sorted(str(n) for n in (spec.get("nodes") or {})), "facets": dict(sorted(facets.items()))}


def layout_changed(prev_spec: dict | None, new_spec: dict) -> bool:
    """True when new_spec's facets (labels and polygons) or node set differ from prev_spec's (a parent of None: the
    first layout, changed)."""
    return prev_spec is None or layout_key(prev_spec) != layout_key(new_spec)


def _names(items: list, limit: int = LAYOUT_NAMES_MAX) -> str:
    return ", ".join(items if len(items) <= limit else items[:limit - 1] + [f"+{len(items) - limit + 1} more"])


def layout_diff(old: dict | None, new: dict | None) -> str:
    """How layout ``new`` differs from layout ``old`` (two layout_key values), compact: "+node M; -facet F2; ~facet
    crown poly"; "" when they are equal."""
    old = old or {"nodes": [], "facets": {}}
    new = new or {"nodes": [], "facets": {}}
    parts = []
    for sign, items in (("+node", sorted(set(new["nodes"]) - set(old["nodes"]))),
                        ("-node", sorted(set(old["nodes"]) - set(new["nodes"]))),
                        ("+facet", sorted(set(new["facets"]) - set(old["facets"]))),
                        ("-facet", sorted(set(old["facets"]) - set(new["facets"]))),
                        ("~facet", [f"{k} poly" for k in sorted(set(old["facets"]) & set(new["facets"]))
                                    if old["facets"][k] != new["facets"][k]])):
        if items:
            parts.append(f"{sign} {_names(items)}")
    return "; ".join(parts)


def record_object(state: dict, text, call_id: str, *, spec: dict | None = None, parent: int | None = None) -> dict:
    """Store an object note (OBJECT_MAX characters, not blank; E_ARGS otherwise) as the latest, with the layout of
    ``spec`` (the design the call made; None when it made none) for object_due and the revision the call edited
    (``parent``); the lab adds "revision" once the call's design is a revision (object_origin). Returns the entry."""
    if not isinstance(text, str) or not text.strip() or len(text) > OBJECT_MAX:
        raise LabRefusal("E_ARGS", f"object must be text of 1..{OBJECT_MAX} characters, or null")
    entry = {"text": text, "call_id": call_id, "layout": layout_key(spec)}
    if parent is not None:
        entry["parent"] = parent
    state.setdefault("objects", []).append(entry)
    return entry


def object_origin(entry: dict) -> str:
    """Which call gave an object note, in the revisions Astra knows: "given with revision 4" (the call's design is
    revision 4), "given in a facet_edit of revision 3 that made no revision" (its fit or build was refused), or, for an
    entry recorded without either (an older state), "of call X". Why (v3p3 astra P3-5): E_OBJECT and facet_inspect
    named the SDK call id ("call s04 wrote the latest object note"), a call_... token in a paid run that Astra cannot
    map to any note it wrote."""
    if entry.get("revision") is not None:
        return f"given with revision {entry['revision']}"
    if entry.get("parent") is not None:
        return f"given in a facet_edit of revision {entry['parent']} that made no revision"
    return f"of call {entry.get('call_id')}"


def object_text(state: dict) -> str | None:
    """The latest object note, or None."""
    objects = state.get("objects") or []
    return objects[-1]["text"] if objects else None


def object_due(state: dict, prev_spec: dict | None, new_spec: dict, text=None) -> bool:
    """The E_OBJECT rule (WP6_V3 §6: the note is required on the first facet_edit and whenever the MODEL's layout
    changes): a facet_edit making new_spec without an object note in the call (``text`` None or blank) is refused when
    no note exists yet, or when the latest note was written for another layout than new_spec's (layout_key; a note
    given in an earlier call that made this layout, e.g. one whose fit or build was refused, holds for it). The latest
    note decides, not the parent: v3 has no restore tool, so applying a facet_variants arm or going back to an older
    revision is a facet_edit of that revision with no edits, whose parent has the new design's layout while the latest
    note may describe another (review 2026-10-12: a note for four faces, then a three-face arm applied, passed with
    the four-face note shown as the head's). Only a note recorded without a layout (``layout`` None: a call that made
    no design) falls back to the parent: due when new_spec's layout differs from ``prev_spec``'s (layout_changed)."""
    if isinstance(text, str) and text.strip():
        return False
    objects = state.get("objects") or []
    if not objects:
        return True
    written = objects[-1].get("layout")
    if written is None:
        return layout_changed(prev_spec, new_spec)
    return written != layout_key(new_spec)


def check_object(state: dict, prev_spec: dict | None, new_spec: dict, text=None) -> None:
    """object_due as the refusal: LabRefusal E_OBJECT (OBJECT_REFUSAL, the fixed text of I4) with the fact behind it
    when a note exists, ``latest_object``: which revision the latest note was given with (object_origin) and how this
    design's layout differs from the one it was written for (layout_diff; for a note without a layout, from the
    parent's), so a refused Astra that wrote a note can tell why."""
    if not object_due(state, prev_spec, new_spec, text):
        return
    objects = state.get("objects") or []
    if not objects:
        raise LabRefusal("E_OBJECT", OBJECT_REFUSAL)
    last = objects[-1]
    if last.get("layout") is None:
        fact = (f"the latest object note ({object_origin(last)}) has no design; this design's layout differs from its "
                f"parent's: {layout_diff(layout_key(prev_spec), layout_key(new_spec))}")
    else:
        fact = (f"the latest object note ({object_origin(last)}) was written for another layout; this design: "
                f"{layout_diff(last['layout'], layout_key(new_spec))}")
    raise LabRefusal("E_OBJECT", OBJECT_REFUSAL, latest_object=fact)


# --------------------------------------------------------------------------------------------- finish
def unique_heads(revisions, distinct, limit: int | None = None) -> list[int]:
    """The revisions in order, each kept only when ``distinct(kept, revision)`` holds against EVERY revision kept
    before it (going back to an earlier head, or to one geometrically equal to it, never counts again); stops once
    ``limit`` are kept. ``distinct`` is the one head-distinctness rule (Lab.distinct: geometry.distinct_heads of the
    two built temples)."""
    chain: list[int] = []
    for r in revisions:
        if r is None or r in chain:
            continue
        if all(distinct(kept, r) for kept in chain):
            chain.append(r)
            if limit is not None and len(chain) >= limit:
                break
    return chain


def shown_in_full(c: dict) -> list:
    """The photos a compare record showed in full (its ``stacked`` rows; a record without the key: its ``photos``).
    A zoom shows a box of a photo, never the whole temple: the judgement covers zoomed photos (``photos``), but
    facet_variants (lab._variant_photos) and finish preconditions 1 and 3 count only full compares (review
    2026-10-12: one 40 x 40 px zoom at B1 met precondition 3 for B1)."""
    return list(c.get("stacked", c["photos"]))


def distinct_compared_heads(state: dict, distinct, limit: int | None = None) -> list[int]:
    """Precondition 1: the unique distinct heads (unique_heads) among the revisions of Astra's seen compares that
    showed at least one photo in full (shown_in_full; a zoom-only compare does not count)."""
    return unique_heads([c["revision"] for c in seen_compares(state) if shown_in_full(c)], distinct, limit)


def last_judgements(state: dict, n: int = STUCK_LAST) -> list[dict]:
    """The last ``n`` judgements, one per compare (the latest of each compare_id), in order. check_judgement stores
    one per compare; a state written before that rule can hold a compare's judgement twice (package_003: cmp004)."""
    out, seen = [], set()
    for j in reversed(state.get("judgements") or []):
        if j["compare_id"] in seen:
            continue
        seen.add(j["compare_id"])
        out.append(j)
        if len(out) >= n:
            break
    return out[::-1]


def head_seq(state: dict, head: dict) -> float:
    """Where an applied head lies in the state's seq order (compares and variants carry the seq of their own id): its
    ``seq`` (the state's seq when it was applied); for a head recorded without one, just before the first compare its
    own call made (facet_edit applies, then compares); else 0 (it orders before everything: a stuck finish is then not
    refused for want of an order the state does not hold)."""
    if isinstance(head.get("seq"), (int, float)) and not isinstance(head.get("seq"), bool):
        return float(head["seq"])
    own = [c["seq"] for c in state.get("compares", []) if c.get("call_id") == head.get("call_id")
           and isinstance(c.get("seq"), (int, float))]
    return float(min(own)) - 0.5 if own else 0.0


def variants_since_head(state: dict) -> list[dict]:
    """The seen facet_variants results with at least one variant rendered at every photo (a sheet Astra could judge),
    made after the latest applied head (head_seq); [] when no head is applied."""
    if not state.get("heads"):
        return []
    after = head_seq(state, state["heads"][-1])
    return [v for v in seen_variants(state) if v.get("rendered") and isinstance(v.get("seq"), (int, float))
            and v["seq"] > after]


def finish_missing(state: dict, *, status: str, best_revision: int, k: int, distinct, camera_photos: list,
                   has_object: bool) -> list:
    """The unmet finish preconditions (WP6_V3 §6), each as {precondition, message} (facts only: what is missing, no
    remedy); [] = all met. Only what reached Astra counts: seen compares (1, 3, 5) and seen variants (2, 5); only
    full compares count for 1 and 3 (shown_in_full: a zoom alone does not).
        1  >= k distinct applied heads compared in full (distinct_compared_heads; ``distinct(ra, rb)`` is the one
           head-distinctness rule, also used by 5);
        2  one facet_variants call with >= SPREAD_VARIANTS built and rendered variants pairwise >= SPREAD_MM apart
           (its "spread");
        3  a full compare of best_revision shown at every photo of ``camera_photos`` (every target photo and every
           photo of another colour with a camera: Lab.compare_ids);
        4  an object note exists (``has_object``);
        5  for stuck: a facet_variants result with a rendered variant since the latest applied head
           (variants_since_head), and the last STUCK_LAST judgements, one per compare (last_judgements), cover
           STUCK_LAST distinct heads with none "better"."""
    out = []
    chain = distinct_compared_heads(state, distinct, limit=k)
    if len(chain) < k:
        out.append({"precondition": 1, "message": f"{len(chain)} of {k} distinct applied heads compared in full "
                                                  f"(revisions {chain}); a head counts only when it differs from "
                                                  f"every head counted: {DIFFERENT_ATTEMPT}"})
    if not any(v.get("spread") for v in seen_variants(state)):
        out.append({"precondition": 2, "message": f"no facet_variants call has rendered {SPREAD_VARIANTS} variants "
                                                  f"pairwise >= {SPREAD_MM:g} mm apart"})
    covered = {p for c in seen_compares(state) if c["revision"] == best_revision for p in shown_in_full(c)}
    missing = [p for p in camera_photos if p not in covered]
    if missing:
        out.append({"precondition": 3, "message": f"no full compare of revision {best_revision} (best_checkpoint's) "
                                                  f"was shown at photos {missing} (a zoom alone does not count)",
                    "photos": missing})
    if not has_object:
        out.append({"precondition": 4, "message": "there is no object note"})
    if status == "stuck":
        parts = []
        if not variants_since_head(state):
            head = state["heads"][-1] if state.get("heads") else None
            parts.append("no facet_variants result with a rendered variant was shown since the latest applied head" +
                         (f" (revision {head['revision']}, checkpoint {head['checkpoint']})" if head else ""))
        last = last_judgements(state, STUCK_LAST)
        heads = unique_heads([j["compare_revision"] for j in last], distinct)
        better = [j["compare_id"] for j in last if j["overall"] == "better"]
        if len(last) < STUCK_LAST or len(heads) < STUCK_LAST or better:
            parts.append(f"the last {len(last)} judgements (one per compare) cover {len(heads)} distinct heads (stuck "
                         f"needs the last {STUCK_LAST} on {STUCK_LAST}, none 'better')" +
                         (f"; {better} said better" if better else ""))
        if parts:
            out.append({"precondition": 5, "message": "; ".join(parts)})
    return out
