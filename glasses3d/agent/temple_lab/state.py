"""The facet lab's persistent state (``work/temple_lab/state.json``) and the host's procedural rules over it
(plan/WP6_FACETS.md §4.1): the judgement rule (E_JUDGEMENT) and the finish preconditions. The host logs judgements
and checks procedure; it never grades quality.

State (schema g3d.temple_lab_state/2):
    revisions   [{id, parent, sha256, file, kind: template | edit | variant, label, ops, note, call_id}] (one per
                distinct spec: a spec built again keeps its revision); revision 0 is the template of the original
    heads       [{checkpoint, fingerprint, revision, call_id}]: every scene a revision was applied to
    compares    [{compare_id, checkpoint, fingerprint, revision, photos, by: prepare | astra, call_id, report,
                judged, unseen?}]: Astra must judge the latest "astra" compare it was shown on its next action tool;
                a compare made by a call the SDK timed out is "unseen" (never shown, never judged)
    judgements  [{call_id, tool, compare_id, versus_compare_id, overall, photos, best_checkpoint, revision}]
    variants    [{id, call_id, parent, photos, items: [{label, revision | null, code | null, delta_head_mm}],
                pairwise: {"A|B": mm}, rendered: [labels rendered at every photo], spread: [3 rendered labels] |
                null}]
    images      {sha256: {kind, ref, revision, file}} of every image returned to Astra (the context guard's index)
    inherited   {revision, core, blender, hinge_leaf_gap_mm}: revision 0's warnings at prepare (lab.py module doc)
    events      SceneHooks journal entries; finish; best_checkpoint; seq (compare / variants ids)
"""
from __future__ import annotations

import json
from pathlib import Path

from glasses3d.agent.procs import write_json_atomic

STATE_SCHEMA = "g3d.temple_lab_state/2"
VERDICTS = ("better", "same", "worse", "mixed")
DIFFERENCE_MAX = 200
STUCK_LAST = 3


class LabRefusal(ValueError):
    """A procedural refusal returned to Astra (nothing changed): code E_JUDGEMENT, E_FINISH, E_ARGS, E_REVISION..."""

    def __init__(self, code: str, message: str, **fields):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.fields = code, message, fields

    def to_dict(self) -> dict:
        return {"status": "refused", "code": self.code, "message": self.message, **self.fields}


def new_state(rig: str) -> dict:
    return {"schema": STATE_SCHEMA, "rig": rig, "start_checkpoint": None, "start_fingerprint": None, "original": None,
            "revisions": [], "heads": [], "compares": [], "judgements": [], "variants": [], "images": {},
            "events": [], "best_checkpoint": None, "finish": None, "finish_refusals": 0, "seq": 0}


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


def astra_compares(state: dict) -> list[dict]:
    return [c for c in state["compares"] if c["by"] == "astra"]


def seen_compares(state: dict) -> list[dict]:
    """Astra's compares that reached it (not those of a call the SDK timed out)."""
    return [c for c in astra_compares(state) if not c.get("unseen")]


def compare(state: dict, compare_id: str) -> dict | None:
    return next((c for c in state["compares"] if c["compare_id"] == compare_id), None)


def pending_compare(state: dict) -> dict | None:
    """The latest compare Astra was shown and has not judged yet."""
    mine = seen_compares(state)
    return mine[-1] if mine and not mine[-1]["judged"] else None


# --------------------------------------------------------------------------------------------- judgements
def check_judgement(state: dict, judgement: dict | None, *, fingerprint_of) -> dict | None:
    """The E_JUDGEMENT rule (§4.1): after a compare, the next action tool carries a judgement naming the latest
    compare_id, with one entry per photo of that compare and a best_checkpoint that holds an applied revision.
    ``fingerprint_of(checkpoint id)`` -> its fingerprint or None. Returns the normalised record (not yet stored) or
    None (no judgement, none due). Unseen compares (a timed-out call's) are never "the latest"."""
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
    if judgement.get("compare_id") != latest["compare_id"]:
        raise LabRefusal("E_JUDGEMENT", f"judgement.compare_id must be the latest compare, {latest['compare_id']}",
                         compare_id=latest["compare_id"])
    versus = judgement.get("versus_compare_id")
    if versus is not None and (versus == latest["compare_id"] or compare(state, versus) is None):
        raise LabRefusal("E_JUDGEMENT", f"versus_compare_id {versus!r} must be an earlier compare_id or null")
    if judgement.get("overall") not in VERDICTS:
        raise LabRefusal("E_JUDGEMENT", f"overall must be one of {list(VERDICTS)}")
    photos = judgement.get("photos") or []
    seen = []
    for p in photos:
        pid = p.get("photo")
        if pid not in latest["photos"] or pid in seen:
            raise LabRefusal("E_JUDGEMENT", f"judgement.photos: {pid!r} is not a photo of {latest['compare_id']} "
                                            f"{latest['photos']} or is repeated")
        if p.get("verdict") not in VERDICTS:
            raise LabRefusal("E_JUDGEMENT", f"photo {pid}: verdict must be one of {list(VERDICTS)}")
        text = p.get("largest_difference")
        if not isinstance(text, str) or not text.strip() or len(text) > DIFFERENCE_MAX:
            raise LabRefusal("E_JUDGEMENT", f"photo {pid}: largest_difference must be 1..{DIFFERENCE_MAX} characters")
        seen.append(pid)
    missing = [pid for pid in latest["photos"] if pid not in seen]
    if missing:
        raise LabRefusal("E_JUDGEMENT", f"judgement.photos misses {missing} of {latest['compare_id']}",
                         missing=missing)
    best = judgement.get("best_checkpoint")
    head = head_by_fingerprint(state, fingerprint_of(best)) if isinstance(best, str) else None
    if head is None:
        raise LabRefusal("E_JUDGEMENT", f"best_checkpoint {best!r} is not a checkpoint holding an applied revision; "
                                        f"applied (latest): {applied_checkpoints(state)[-12:]}")
    return {"compare_id": latest["compare_id"], "versus_compare_id": versus, "overall": judgement["overall"],
            "photos": [dict(p) for p in photos], "best_checkpoint": best, "revision": head["revision"],
            "compare_revision": latest["revision"]}


def record_judgement(state: dict, rec: dict | None, *, call_id: str, tool: str) -> None:
    if rec is None:
        return
    state["judgements"].append({"call_id": call_id, "tool": tool, **rec})
    compare(state, rec["compare_id"])["judged"] = True
    state["best_checkpoint"] = rec["best_checkpoint"]


def applied_checkpoints(state: dict) -> list[str]:
    return [h["checkpoint"] for h in state["heads"]]


# --------------------------------------------------------------------------------------------- finish
def distinct_compared_heads(state: dict, distinct, limit: int | None = None) -> list[int]:
    """The revisions of Astra's (seen) compares in order, each kept only when ``distinct(kept, revision)`` holds against
    EVERY revision kept before it (§4.1 precondition 1: unique distinct heads; going back to an earlier head, or to
    one geometrically equal to it, never counts again). Stops once ``limit`` heads are kept."""
    chain: list[int] = []
    for c in seen_compares(state):
        r = c["revision"]
        if r is None or r in chain:
            continue
        if all(distinct(kept, r) for kept in chain):
            chain.append(r)
            if limit is not None and len(chain) >= limit:
                break
    return chain


def finish_missing(state: dict, *, status: str, best_revision: int, k: int, distinct, target_photos: list) -> list:
    """The unmet finish preconditions (§4.1), each as {precondition, message}; [] = all met."""
    out = []
    chain = distinct_compared_heads(state, distinct, limit=k)
    if len(chain) < k:
        out.append({"precondition": 1, "message": f"{len(chain)} of {k} distinct applied heads compared (revisions "
                                                  f"{chain}); each must differ from every other one counted by "
                                                  ">= 0.2 mm max|dX| or a facet / crease-shape change"})
    if not any(v.get("spread") for v in state["variants"]):
        out.append({"precondition": 2, "message": "no facet_variants call yet with >= 3 built and rendered variants "
                                                  "pairwise >= 0.3 mm apart"})
    covered = {p for c in state["compares"] if c["revision"] == best_revision for p in c["photos"]}
    missing = [p for p in target_photos if p not in covered]
    if missing:
        out.append({"precondition": 3, "message": f"revision {best_revision} (best_checkpoint) has no compare at "
                                                  f"photos {missing}: apply or restore it, then photo_compare those "
                                                  "photos", "photos": missing})
    if status == "stuck":
        last = state["judgements"][-STUCK_LAST:]
        revs = {j["compare_revision"] for j in last}
        better = [j["compare_id"] for j in last if j["overall"] == "better"]
        if len(last) < STUCK_LAST or len(revs) < STUCK_LAST or better:
            out.append({"precondition": 4, "message": f"stuck needs the last {STUCK_LAST} judgements on "
                                                      f"{STUCK_LAST} distinct heads, none 'better'; they cover "
                                                      f"{len(revs)} heads" + (f" and {better} said better"
                                                                              if better else "")})
    return out
