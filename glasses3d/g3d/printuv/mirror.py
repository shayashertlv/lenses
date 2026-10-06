"""Mirror partners: a part whose geometry is another part reflected in x = 0 reuses its layout by INDEX COPY
(WP5_DESIGN §6.1 "mirroring = index copy"). numpy only.

``find_partner`` welds each part's corner positions (glTF splits vertices at uv / normal seams), matches every welded
point of part B to a welded point of part A reflected (x -> -x) within ``tol_mm``, then every face of B to the face of
A with the same (reflected) point set, and records the corner permutation. Faces left over are accepted only as
**re-triangulated patches**: the unmatched faces of B and of A must be equal in number and every corner of an unmatched
B face must be a corner of an unmatched A face (the same patch triangulated differently, e.g. a quad split along the
other diagonal or a fan at an end; q001: 28 of 4,668 brow faces and 20 of 4,268 temple faces). Anything else and the
parts are not partners.

A reflection reverses orientation, so B's outward faces list A's corners in reversed order; ``copy_layout`` copies the
uv of each corresponding corner with u NEGATED, which restores a non-flipped, seen-from-outside chart: B's chart is A's
chart mirrored and B's surface is A's surface mirrored, so text in B's chart reads correctly on B. A re-triangulated
face takes each corner's uv from the A quad's corner at the same point (vertex copy; both A triangles must lie in one
chart and agree on the shared points). Each partner gets its own atlas boxes (its own texels: the print of the two
sides differs), and the A1 check verifies index identity on the matched faces and vertex identity on the rest.
"""
from __future__ import annotations

import numpy as np

MIRROR_TOL_MM = 0.01


def match_points(A: np.ndarray, B: np.ndarray, tol: float, chunk: int = 1024) -> np.ndarray | None:
    """For each row of B, the index of the row of A within ``tol`` (nearest); None when any B point has none or two B
    points take the same A point."""
    A = np.asarray(A, np.float64)
    B = np.asarray(B, np.float64)
    if len(A) != len(B):
        return None
    idx = np.empty(len(B), np.int64)
    a2 = (A * A).sum(1)
    for s in range(0, len(B), chunk):
        b = B[s:s + chunk]
        d2 = (b * b).sum(1)[:, None] - 2.0 * b @ A.T + a2[None]
        j = np.argmin(d2, axis=1)
        dd = np.sqrt(np.maximum(d2[np.arange(len(b)), j], 0.0))
        if np.any(dd > tol):
            return None
        idx[s:s + chunk] = j
    if len(np.unique(idx)) != len(idx):
        return None
    return idx


def weld(P: np.ndarray, q: float) -> tuple[np.ndarray, np.ndarray]:
    """Exact weld of points on a grid of ``q`` mm: (unique representative points, id of each input point)."""
    key = np.round(np.asarray(P, np.float64) / q).astype(np.int64)
    _u, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return np.asarray(P, np.float64)[first], inv.reshape(-1)


def _keys(Fw: np.ndarray, big: np.int64) -> np.ndarray:
    k = np.sort(Fw, 1)
    return (k[:, 0] * big + k[:, 1]) * big + k[:, 2]


def find_partner(V: np.ndarray, F: np.ndarray, faces_a: np.ndarray, faces_b: np.ndarray,
                 tol_mm: float = MIRROR_TOL_MM) -> dict | None:
    """Correspondence of part B's faces to part A's under x -> -x, or None. Returns {'face_of_b': (nb,) index into
    faces_a (-1: re-triangulated), 'perm': (nb, 3) corner of A for each corner of B (-1 rows when re-triangulated),
    'quad': {b face: [[(a face, a corner), ...] per B corner]}, 'matched': int, 'retriangulated': int}."""
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    faces_a = np.asarray(faces_a, np.int64)
    faces_b = np.asarray(faces_b, np.int64)
    if len(faces_a) != len(faces_b) or not len(faces_a):
        return None
    q = max(tol_mm / 10.0, 1e-6)
    Pa, wa = weld(V[F[faces_a]].reshape(-1, 3), q)
    Pb, wb = weld(V[F[faces_b]].reshape(-1, 3), q)
    if len(Pa) != len(Pb):
        return None
    j = match_points(Pa * np.array([-1.0, 1.0, 1.0]), Pb, tol_mm)
    if j is None:
        return None
    Fa = wa.reshape(-1, 3)                                  # A faces in A's welded ids
    Fb = j[wb].reshape(-1, 3)                               # B faces in A's welded ids
    if np.any((Fa[:, 0] == Fa[:, 1]) | (Fa[:, 1] == Fa[:, 2]) | (Fa[:, 0] == Fa[:, 2])):
        return None                                         # degenerate (welded) faces: no unique corner map
    big = np.int64(len(Pa) + 1)
    keya, keyb = _keys(Fa, big), _keys(Fb, big)
    if len(np.unique(keya)) != len(keya):
        return None
    oa = np.argsort(keya, kind="stable")
    pos = np.clip(np.searchsorted(keya[oa], keyb), 0, len(oa) - 1)
    hit = keya[oa][pos] == keyb
    fa = np.where(hit, oa[pos], -1)
    perm = -np.ones((len(faces_b), 3), np.int64)
    for c in range(3):
        perm[hit, c] = np.argmax(Fa[fa[hit]] == Fb[hit, c:c + 1], axis=1)
    quad = {}
    if not hit.all():
        used = np.zeros(len(faces_a), bool)
        used[fa[hit]] = True
        free_a = np.nonzero(~used)[0]
        if len(free_a) != int((~hit).sum()):
            return None
        sets_a = {int(ia): set(int(x) for x in Fa[ia]) for ia in free_a}
        for ib in np.nonzero(~hit)[0]:
            corners = set(int(x) for x in Fb[ib])
            cand = [ia for ia, s in sets_a.items() if corners & s]
            if not cand:
                return None
            src = []
            for c in range(3):
                v = int(Fb[ib, c])
                opts = [(ia, int(np.nonzero(Fa[ia] == v)[0][0])) for ia in cand if v in sets_a[ia]]
                if not opts:
                    return None
                src.append(opts)                            # every A corner at this point (they must agree)
            quad[int(ib)] = src
    return {"face_of_b": fa, "perm": perm, "quad": quad, "matched": int(hit.sum()), "retriangulated": len(quad)}


def copy_layout(charts_a: list, n_faces_b: int, corr: dict) -> list:
    """Charts of B from A's charts [(kind, local faces of A, uv mm (k, 3, 2))]: each matched B face joins the chart of
    its A face with A's corner uv (index copy), each re-triangulated face joins the chart of its A quad with the quad's
    corner uv at the same points (vertex copy); u is negated in both. Raises when a quad spans two charts or its two
    A triangles disagree on a shared point."""
    where = {}
    for ci, (kind, fl_a, uv_a) in enumerate(charts_a):
        for row, ia in enumerate(fl_a):
            where[int(ia)] = (ci, row)
    rows = [[] for _ in charts_a]
    uvs = [[] for _ in charts_a]
    for ib in range(n_faces_b):
        ia = int(corr["face_of_b"][ib])
        if ia >= 0:
            ci, row = where[ia]
            uv = charts_a[ci][2][row][corr["perm"][ib]]
        else:
            uv, ci = quad_uv(charts_a, where, corr["quad"][ib])
        rows[ci].append(ib)
        uvs[ci].append(uv)
    out = []
    for ci, (kind, _fl, _uv) in enumerate(charts_a):
        if not rows[ci]:
            continue
        uv_b = np.stack(uvs[ci]).astype(np.float64)
        uv_b[..., 0] *= -1.0
        out.append((kind, np.asarray(rows[ci], np.int64), uv_b))
    return out


def quad_uv(charts_a: list, where: dict, src: list) -> tuple[np.ndarray, int]:
    """(uv (3, 2) mm, chart index) of a re-triangulated face: per corner, the uv of the A quad corners at that point;
    raises when they span two charts or disagree (the vertex copy would tear the chart)."""
    cis = {where[a][0] for opts in src for a, _c in opts}
    if len(cis) != 1:
        raise ValueError("a re-triangulated quad spans two charts of the source part")
    ci = cis.pop()
    out = []
    for opts in src:
        cand = np.stack([charts_a[ci][2][where[a][1]][c] for a, c in opts])
        if np.abs(cand - cand[0]).max() > 1e-9:
            raise ValueError("the source quad's triangles disagree on a shared point's uv")
        out.append(cand[0])
    return np.stack(out), ci
