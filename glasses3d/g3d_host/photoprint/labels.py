"""WP5 step 4 (labels and seams, WP5_DESIGN §6.4): which view paints each face, with label changes placed on creases.

Single-source mode starts every face at its region's anchor (tier 2) or its registered set (tier 1, a 'mix' label).
Where two regions with different labels meet, the boundary is free to move inside a band (``BAND_MM`` of geodesic
distance from the region boundary, over the face graph): an MRF per band with data cost n_f x (-log(w_l / w_best))
(n_f = the face's texels, w = the view's mean donor weight cos^2 x px/mm over them; a view that does not see a face
costs as if its weight were e^-20 of the best) and a Potts cost lambda x the shared edge length in texels, lambda = 1,
lowered to 0.2 across creases (dihedral >= 25 deg), per mm of seam x ``SEAM_MM`` (1 mm) of texel area. **Deviation
(measured):** the design's texel-unit Potts (lambda per neighbouring texel pair against data per texel) makes the
crease trade-off shrink with density: a boundary moves toward a crease only while the extra data cost of the strip it
crosses, d x tx^2 x dD per mm of length, stays below the 0.8 x tx saved, so at 16 tx/mm and a typical dD = 0.9 it
moves at most 0.05 mm, and on FT1119 at 6 tx/mm the share of boundary length on creases FELL from 0.51 to 0.27 (the
data term won everywhere). Counting the seam in mm (1 mm of seam = 1 mm^2 of data) makes the reach density-independent,
about 0.8 / dD mm; with that alone the share still fell (0.51 -> 0.30), because the cos^2 x px/mm preference between
two views that BOTH see a face at donor angles outweighed the crease discount. The data term therefore separates
"sees" from "does not see" at full strength (MAX_DATA) and keeps the weight preference at ``DATA_WEIGHT`` = 0.1, so
inside a band the Potts term with its crease discount places the boundary. It is minimised by alpha-expansion, each
move an exact binary
graph cut (scipy ``maximum_flow``, integer capacities x 100). Outside the bands the region's own label stays, so a
region is still painted from its anchor; only its boundary moves (the best-view mosaic is avoided, AP6).

**Fix round 1 (strict anchor-only was production until fix round 2; now ``mode='off'``):** the MRF lowered the share of label-boundary length on creases
(0.508 at the region split -> 0.325 after it on FT1119) and handed 8 % of the photo texels to views other than their
region's anchor (31.6 % of the -x temple's top from 02), which contradicts the owner's single-source decision ("each
region painted from its anchor photo only"). ``project_photo_texture`` therefore keeps the region labels fixed by
default (``label_moves='off'``: ``place_labels(..., move=False)`` returns the start labels and their boundary
statistics); texels the anchor cannot see are filled. The MRF stays available as a diagnostic (``label_moves='mrf'``).

**Fix round 2 (production = ``mode='crease'``, re-placed boundaries, each face one source view):** the reviewer measured
399 of 812 mm of FT1119's label boundary off creases. Measured here (2026-10-06, q001): the brows carry NO crease at
all near their boundaries (their section is a polygon of 15-20 deg steps; 0 of 320 mm of brow boundary is on an edge
>= 25 deg, and only 40 of the 399 mm off-crease boundary lies within 3 mm of any crease face); the temples' outer|top
and inner|top boundaries are 92 % on creases already. So creases cannot place the brow seams; there the seam goes where
the two adjacent anchors' gained colours agree best (the photometric seam of Lempitsky and Ivanov 2007 / Waechter et al.
2014, the §6.4 research solution, used here only to PLACE a hard single-source seam, never to blend). The MRF of
``mode='crease'``:
- candidates per band face: its region's anchor and the anchors of the regions ADJACENT to it in its band component
  (labels that share a start boundary with its label there), so a face is only ever handed to a neighbouring region's
  anchor;
- a face may take a label other than its start label only if that view SEES it as a donor (w > 0): never handed to a
  view that does not see it (forbidden, not penalised); a face its own anchor does not see may keep the anchor label
  (a hole, filled later) at a mild cost ``UNSEEN_COST`` per texel area, so covering unseen faces never drives a
  boundary off a crease (the round-1 MRF's failure: 8 % of photo texels handed to other views);
- data: ``DATA_WEIGHT`` x -log(w_l / w_best) among the views that see the face;
- pairwise between labels l1 != l2 on an edge: lambda_e x (1 + ``PHOTO_BETA`` x min(dE00, ``DE_CAP``) / ``DE_REF``),
  lambda_e = (0.2 across creases, 1 elsewhere) x length x 1 mm of texel area; dE00 = the colour difference of the two
  views' gained, face-averaged (one neighbour ring) colours on the edge's faces (``DE_CAP`` where either does not see
  them). A crease seam costs at most 0.2 x (1 + 30 / 10) = 0.8 < 1, the cheapest off-crease seam, so creases win where
  they exist; elsewhere the seam takes the line of best colour agreement.
Each face keeps one source view (single-source per face; no view is mixed with another). A region's boundary band
(<= ``BAND_MM``) may therefore be painted by an adjacent region's anchor: ``project`` reports that share per region and
the proofs check every photo texel's view is its region's anchor or an adjacent region's anchor.
"""
from __future__ import annotations

import heapq

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph

CREASE_DEG = 25.0
LAMBDA = 1.0
LAMBDA_CREASE = 0.2
BAND_MM = 3.0
SEAM_MM = 1.0                    # a seam costs lambda x (1 mm^2 of data) per mm of length (density-independent)
MAX_DATA = 20.0
DATA_WEIGHT = 0.1                # weight of the -log(w / w_best) preference among views that see a face
CAP_SCALE = 100.0
MAX_CYCLES = 4
MIX = -2                         # label of a tier-1 (registered, mixed) face
NONE = -1                        # no view (a hole: filled later)
FORBID = MAX_DATA * 1e12         # a label a face may never take (excluded from every expansion move)
UNSEEN_COST = DATA_WEIGHT * 3.0  # per texel: keeping the start label on a face its anchor does not see (crease mode)
PHOTO_BETA = 1.0                 # photometric seam weight (crease mode)
DE_REF = 10.0
DE_CAP = 30.0
MODES = ("off", "mrf", "crease")


def weld_ids(P: np.ndarray, q: float = 1e-4) -> np.ndarray:
    key = np.round(np.asarray(P, np.float64) / q).astype(np.int64)
    _u, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1)


def face_graph(V: np.ndarray, F: np.ndarray, faces: np.ndarray, tx: float) -> dict:
    """Edge adjacency of ``faces`` (local ids) on welded positions: pairs (a, b), shared edge length (mm), dihedral
    angle (deg), crease flag, and the centroid distance (mm)."""
    faces = np.asarray(faces, np.int64)
    tri = np.asarray(V, float)[np.asarray(F)[faces]]
    w = weld_ids(tri.reshape(-1, 3)).reshape(-1, 3)
    e = np.concatenate([w[:, [0, 1]], w[:, [1, 2]], w[:, [2, 0]]])
    ev = np.concatenate([np.stack([tri[:, 0], tri[:, 1]], 1), np.stack([tri[:, 1], tri[:, 2]], 1),
                         np.stack([tri[:, 2], tri[:, 0]], 1)])
    e.sort(1)
    fidx = np.tile(np.arange(len(faces)), 3)
    key = e[:, 0] * (np.int64(1) << 32) + e[:, 1]
    o = np.argsort(key, kind="stable")
    key, fidx, ev = key[o], fidx[o], ev[o]
    same = np.nonzero(key[1:] == key[:-1])[0]
    a, b = fidx[same], fidx[same + 1]
    keep = a != b
    a, b, same = a[keep], b[keep], same[keep]
    length = np.linalg.norm(ev[same, 1] - ev[same, 0], axis=1)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    dih = np.degrees(np.arccos(np.clip((n[a] * n[b]).sum(1), -1, 1)))
    c = tri.mean(1)
    return {"a": a, "b": b, "len_mm": length, "len_tx": length * tx, "dihedral": dih, "crease": dih >= CREASE_DEG,
            "dist": np.linalg.norm(c[a] - c[b], axis=1), "n": len(faces)}


def band_faces(g: dict, labels: np.ndarray, band_mm: float) -> np.ndarray:
    """Faces within ``band_mm`` (Dijkstra over centroid distances) of an edge between two different valid labels."""
    a, b = g["a"], g["b"]
    bnd = (labels[a] != labels[b]) & (labels[a] != NONE) & (labels[b] != NONE)
    dist = np.full(g["n"], np.inf)
    adj = [[] for _ in range(g["n"])]
    for x, y, d in zip(a, b, g["dist"]):
        adj[x].append((y, d))
        adj[y].append((x, d))
    h = []
    for f in np.unique(np.r_[a[bnd], b[bnd]]):
        dist[f] = 0.0
        h.append((0.0, int(f)))
    heapq.heapify(h)
    while h:
        d, f = heapq.heappop(h)
        if d > dist[f] or d > band_mm:
            continue
        for y, dd in adj[f]:
            nd = d + dd
            if nd < dist[y] and nd <= band_mm:
                dist[y] = nd
                heapq.heappush(h, (nd, y))
    return np.isfinite(dist) & (labels != NONE) & (labels != MIX)


def data_costs(wf: np.ndarray, nf: np.ndarray, labels_set: list[int], view_index: dict,
               data_weight: float = None) -> np.ndarray:
    """(n_faces, len(labels_set)) data cost n_f x (DATA_WEIGHT x -log(w_l / w_best)) for a view that sees the face,
    n_f x MAX_DATA for one that does not."""
    data_weight = DATA_WEIGHT if data_weight is None else data_weight
    W = np.stack([wf[:, view_index[l]] for l in labels_set], 1)
    best = W.max(1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(best > 0, W / np.maximum(best, 1e-300), 0.0)
        cost = np.where(r > 0, data_weight * -np.log(np.maximum(r, 1e-300)), MAX_DATA)
    return np.minimum(cost, MAX_DATA) * nf[:, None]


def _min_cut(n_nodes: int, rows, cols, caps) -> np.ndarray:
    """Source side (True) of a min s-t cut; node n_nodes = source, n_nodes + 1 = sink."""
    N = n_nodes + 2
    rows = np.asarray(rows, np.int64)
    cols = np.asarray(cols, np.int64)
    caps = np.asarray(np.rint(np.asarray(caps, float) * CAP_SCALE), np.int64)
    keep = caps > 0
    rows, cols, caps = rows[keep], cols[keep], caps[keep]
    C = sparse.coo_matrix((caps, (rows, cols)), shape=(N, N)).tocsr()
    C.sum_duplicates()
    if C.data.size and C.data.max() > np.iinfo(np.int32).max // 4:
        raise ValueError("graph-cut capacities overflow int32")
    C = C.astype(np.int32)
    # residual structure needs both directions present
    Cs = (C + sparse.csr_matrix((np.zeros(C.nnz, np.int32), (C.indices, np.repeat(np.arange(N), np.diff(C.indptr)))),
                                shape=(N, N))).tocsr()
    Cs.sum_duplicates()
    res = csgraph.maximum_flow(Cs, n_nodes, n_nodes + 1, method="dinic")
    R = (Cs - res.flow).tocsr()
    R.data = (R.data > 0).astype(np.int8)
    R.eliminate_zeros()
    order = csgraph.breadth_first_order(R, n_nodes, directed=True, return_predecessors=False)
    side = np.zeros(N, bool)
    side[order] = True
    return side[:n_nodes]


def expansion(labels: np.ndarray, free: np.ndarray, cand: list[int], D: dict, g: dict, lam: np.ndarray,
              max_cycles: int = MAX_CYCLES, pair=None) -> tuple[np.ndarray, list[float]]:
    """Alpha-expansion over ``cand`` labels on the ``free`` faces (others fixed). ``D[l]`` = (n_faces,) data cost of
    label l; ``lam`` = per-edge Potts weight (already x length). ``pair`` (optional) = callable (edge ids, labels 1,
    labels 2) -> per-edge cost (0 where equal) replacing the Potts term (crease mode); a move whose pairwise term is
    not submodular has those edges truncated, and every move is accepted only if the true energy falls. Returns the
    labels and the energy after each cycle."""
    lab = labels.copy()
    a, b = g["a"], g["b"]
    eall = np.arange(len(a))
    if pair is None:
        def pair(e, l1, l2):
            return lam[e] * (np.asarray(l1) != np.asarray(l2))

    def energy(L):
        e = sum(float(D[l][free & (L == l)].sum()) for l in cand)
        return e + float(pair(eall, L[a], L[b]).sum())

    hist = [energy(lab)]
    for _ in range(max_cycles):
        changed = False
        for alpha in cand:
            nodes = np.nonzero(free & (lab != alpha) & (D[alpha] < MAX_DATA * 1e9))[0]
            if not len(nodes):
                continue
            pos = -np.ones(len(lab), np.int64)
            pos[nodes] = np.arange(len(nodes))
            S, Tn = len(nodes), len(nodes) + 1
            rows, cols, caps = [], [], []
            U0 = np.array([D[int(lab[f])][f] for f in nodes])
            U1 = D[alpha][nodes].astype(float).copy()
            U0 = U0.astype(float).copy()
            ia, ib = pos[a], pos[b]
            # edges to fixed neighbours become unaries
            m1 = (ia >= 0) & (ib < 0)
            m2 = (ib >= 0) & (ia < 0)
            for m, pin, qout in ((m1, ia, b), (m2, ib, a)):
                p_ = pin[m]
                lq = lab[qout[m]]
                lp = lab[nodes[p_]]
                em = eall[m]
                np.add.at(U0, p_, pair(em, lp, lq))
                np.add.at(U1, p_, pair(em, np.full(len(lq), alpha), lq))
            mb = (ia >= 0) & (ib >= 0)
            pa, pb, emb = ia[mb], ib[mb], eall[mb]
            la, lb = lab[nodes[pa]], lab[nodes[pb]]
            A = pair(emb, la, lb)
            B = pair(emb, la, np.full(len(la), alpha))
            Cc = pair(emb, np.full(len(lb), alpha), lb)
            Dd = np.zeros(len(emb))
            # E = A + (C - A) x_p + (D - C) x_q + (B + C - A - D) [x_p = 0, x_q = 1]
            lin_p = Cc - A
            lin_q = Dd - Cc
            np.add.at(U1, pa, np.clip(lin_p, 0, None))
            np.add.at(U0, pa, np.clip(-lin_p, 0, None))
            np.add.at(U1, pb, np.clip(lin_q, 0, None))
            np.add.at(U0, pb, np.clip(-lin_q, 0, None))
            pq = B + Cc - A - Dd
            rows += list(pa)
            cols += list(pb)
            caps += list(pq)
            # unaries: s -> p cut when x_p = 1 (cost U1), p -> t cut when x_p = 0 (cost U0)
            mn = np.minimum(U0, U1)
            rows += [S] * len(nodes) + list(range(len(nodes)))
            cols += list(range(len(nodes))) + [Tn] * len(nodes)
            caps += list(U1 - mn) + list(U0 - mn)
            src = _min_cut(len(nodes), rows, cols, caps)
            switch = ~src
            if switch.any():
                new = lab.copy()
                new[nodes[switch]] = alpha
                if energy(new) < energy(lab) - 1e-9:
                    lab = new
                    changed = True
        hist.append(energy(lab))
        if not changed:
            break
    return lab, hist


def face_colour_de(g: dict, colours: np.ndarray, seen: np.ndarray, cand: list[int], edges: np.ndarray) -> np.ndarray:
    """(len(edges), L, L) dE00 between the candidate views' face colours on each listed edge (mean over its two faces
    where both views see the face; ``DE_CAP`` where no face is seen by both). ``colours`` (n_faces, n_views, 3) sRGB
    face means, ``seen`` (n_faces, n_views)."""
    from .. import texture_utils as T
    L = len(cand)
    out = np.full((len(edges), L, L), DE_CAP, np.float32)
    a, b = g["a"][edges], g["b"][edges]
    for i in range(L):
        out[:, i, i] = 0.0
        for j in range(i + 1, L):
            vi, vj = cand[i], cand[j]
            both = seen[:, vi] & seen[:, vj]
            de = np.full(len(colours), np.nan)
            if both.any():
                de[both] = T.de00(colours[both, vi], colours[both, vj])
            pa, pb = de[a], de[b]
            cnt = np.isfinite(pa).astype(float) + np.isfinite(pb).astype(float)
            m = (np.nan_to_num(pa) + np.nan_to_num(pb)) / np.maximum(cnt, 1)
            d = np.where(cnt > 0, np.minimum(m, DE_CAP), DE_CAP).astype(np.float32)
            out[:, i, j] = d
            out[:, j, i] = d
    return out


def smooth_face_colours(g: dict, colours: np.ndarray, seen: np.ndarray, nf: np.ndarray, rings: int = 1
                        ) -> np.ndarray:
    """Face colours averaged with their edge neighbours (``rings`` times, texel-count weighted, per view, only over
    faces the view sees): a misregistration-tolerant colour for the photometric seam."""
    c = np.where(seen[..., None], colours, 0.0) * nf[:, None, None]
    w = seen * nf[:, None]
    a, b = g["a"], g["b"]
    for _ in range(int(rings)):
        c2, w2 = c.copy(), w.copy()
        np.add.at(c2, a, c[b])
        np.add.at(c2, b, c[a])
        np.add.at(w2, a, w[b])
        np.add.at(w2, b, w[a])
        c, w = c2, w2
    return np.where(w[..., None] > 0, c / np.maximum(w, 1e-12)[..., None], colours)


def _crease_data(wf: np.ndarray, nf: np.ndarray, start: np.ndarray, cand: list[int]) -> np.ndarray:
    """(n_faces, len(cand)) crease-mode data cost: DATA_WEIGHT x -log(w / w_best) x n_f for a view that sees the face,
    FORBID for one that does not, except the face's own start label (UNSEEN_COST x n_f where its view is blind)."""
    W = np.stack([wf[:, l] for l in cand], 1)
    best = W.max(1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(best > 0, W / np.maximum(best, 1e-300), 0.0)
        dc = np.where(r > 0, DATA_WEIGHT * -np.log(np.maximum(r, 1e-300)), FORBID)
    for k, l in enumerate(cand):
        own = (start == l) & ~(W[:, k] > 0)
        dc[own, k] = UNSEEN_COST
    return np.where(dc >= FORBID, FORBID, dc * nf[:, None])


def place_labels(V, F, faces: np.ndarray, region_of_face: np.ndarray, region_label: dict, wf: np.ndarray,
                 nf: np.ndarray, views: list[str], tx: float, *, band_mm: float = BAND_MM, move: bool = True,
                 mode: str | None = None, face_colours: np.ndarray | None = None, part_of_face=None) -> dict:
    """Face labels for the layout's ``faces``: start at the region label (view index, MIX or NONE), then move them in
    the bands near boundaries between different view labels. ``mode``: 'off' keeps the start labels; 'mrf' = the
    round-1 diagnostic MRF; 'crease' = the production MRF (fix round 2, see the module docstring; needs
    ``face_colours`` (n_faces, n_views, 3) sRGB face means of the gained photos where ``wf`` > 0). For older callers
    ``mode=None`` follows ``move`` ('mrf' / 'off'). ``wf`` (n_faces, n_views) mean donor weight, ``nf`` texels per
    face, ``part_of_face`` (optional) for the per-part boundary statistics. Returns {'labels', 'start', 'band',
    'graph', 'energy', 'boundary', ...}."""
    mode = mode if mode is not None else ("mrf" if move else "off")
    if mode not in MODES:
        raise ValueError(f"unknown label mode {mode!r} (one of {MODES})")
    g = face_graph(V, F, faces, tx)
    a, b = g["a"], g["b"]
    start = np.array([region_label.get(int(r), NONE) for r in region_of_face], np.int64)
    lab = start.copy()
    band = band_faces(g, start, band_mm)
    vidx = {i: i for i in range(len(views))}
    cand = sorted(int(x) for x in np.unique(start[band]) if x >= 0)
    lam = np.where(g["crease"], LAMBDA_CREASE, LAMBDA) * g["len_tx"] * (tx * SEAM_MM)
    hist = []
    pair = None
    ph = None                      # photometric tables (crease mode): {'tab', 'erow', 'lidx', 'lmin'}
    if mode == "crease" and len(cand) >= 2 and band.any():
        if face_colours is None:
            raise ValueError("mode='crease' needs face_colours")
        seen = wf > 0
        fc = smooth_face_colours(g, face_colours, seen, nf, rings=1)
        near = band.copy()
        near[a[band[b]]] = True
        near[b[band[a]]] = True
        eband = np.nonzero(near[a] | near[b])[0]
        erow = -np.ones(len(a), np.int64)
        erow[eband] = np.arange(len(eband))
        lmin = min(min(cand), NONE, MIX)
        lidx = -np.ones(max(cand) - lmin + 1, np.int64)
        for k, l in enumerate(cand):
            lidx[l - lmin] = k
        ph = {"tab": face_colour_de(g, fc, seen, cand, eband), "erow": erow, "lidx": lidx, "lmin": lmin}

        def pair(e, l1, l2, _ph=ph):
            l1 = np.asarray(l1)
            l2 = np.asarray(l2)
            i1 = _ph["lidx"][np.clip(l1 - _ph["lmin"], 0, len(_ph["lidx"]) - 1)]
            i2 = _ph["lidx"][np.clip(l2 - _ph["lmin"], 0, len(_ph["lidx"]) - 1)]
            r_ = _ph["erow"][e]
            ok = (i1 >= 0) & (i2 >= 0) & (r_ >= 0)
            de = np.full(len(e), DE_CAP)
            de[ok] = _ph["tab"][r_[ok], i1[ok], i2[ok]]
            return np.where(l1 == l2, 0.0, lam[e] * (1.0 + PHOTO_BETA * np.minimum(de, DE_CAP) / DE_REF))

    if mode != "off" and len(cand) >= 2 and band.any():
        dc = data_costs(wf, nf, cand, vidx) if mode == "mrf" else _crease_data(wf, nf, start, cand)
        D = {l: np.array(dc[:, k], float) for k, l in enumerate(cand)}
        # a face may only take a label present in its own band component ('mrf'), or (crease mode) its own label or
        # one ADJACENT to its own label in that component
        bl = np.nonzero(band)[0]
        pos = -np.ones(len(faces), np.int64)
        pos[bl] = np.arange(len(bl))
        m = (pos[a] >= 0) & (pos[b] >= 0)
        comp = csgraph.connected_components(sparse.coo_matrix((np.ones(int(m.sum())), (pos[a[m]], pos[b[m]])),
                                                              shape=(len(bl), len(bl))), directed=False)[1]
        allowed = {}
        for c in np.unique(comp):
            allowed[int(c)] = set(int(x) for x in np.unique(start[bl[comp == c]]) if x >= 0)
        adj_pairs: dict = {}
        if mode == "crease":
            bd = m & (start[a] != start[b]) & (start[a] >= 0) & (start[b] >= 0)
            for x, y in zip(a[bd], b[bd]):
                c = int(comp[pos[x]])
                adj_pairs.setdefault(c, set()).update({(int(start[x]), int(start[y])), (int(start[y]), int(start[x]))})
        for l in cand:
            if mode == "crease":
                bad = np.array([not (l == start[f] or (int(start[f]), l) in adj_pairs.get(int(comp[pos[f]]), ()))
                                for f in bl])
            else:
                bad = np.array([l not in allowed[int(comp[pos[f]])] for f in bl])
            D[l][bl[bad]] = FORBID                         # never taken (excluded from the move's nodes)
        lab, hist = expansion(start, band, cand, D, g, lam, pair=pair)

    def boundary(L, sel=None):
        bd = (L[a] != L[b]) & (L[a] != NONE) & (L[b] != NONE)
        if sel is not None:
            bd &= sel
        tot = float(g["len_mm"][bd].sum())
        cr = float(g["len_mm"][bd & g["crease"]].sum())
        out = {"length_mm": tot, "on_crease_mm": cr, "on_crease_share": cr / tot if tot > 0 else None}
        if ph is not None:
            ok = bd & (ph["erow"] >= 0)
            i1 = ph["lidx"][np.clip(L[a] - ph["lmin"], 0, len(ph["lidx"]) - 1)]
            i2 = ph["lidx"][np.clip(L[b] - ph["lmin"], 0, len(ph["lidx"]) - 1)]
            ok &= (i1 >= 0) & (i2 >= 0)
            if ok.any():
                de = ph["tab"][ph["erow"][ok], i1[ok], i2[ok]]
                ln = g["len_mm"][ok]
                out["photometric_de00_mean"] = float((de * ln).sum() / max(ln.sum(), 1e-12))
        return out

    vi = np.clip(start, 0, max(len(views) - 1, 0))
    start_unseen = band & (start >= 0) & (wf[np.arange(len(start)), vi] <= 0)
    moved = lab != start
    li = np.clip(lab, 0, max(len(views) - 1, 0))
    moved_to_unseen = moved & (lab >= 0) & (wf[np.arange(len(lab)), li] <= 0)
    per_part = {}
    if part_of_face is not None:
        pf = np.asarray(part_of_face)
        same = pf[a] == pf[b]
        for p_ in np.unique(pf):
            sel = same & (pf[a] == p_)
            per_part[int(p_)] = {"start": boundary(start, sel), "final": boundary(lab, sel)}
    return {"labels": lab, "start": start, "band": band, "graph": g, "energy": hist, "candidates": cand,
            "moves": mode != "off", "mode": mode, "boundary_start": boundary(start), "boundary": boundary(lab),
            "boundary_per_part": per_part, "moved_faces": int(moved.sum()), "band_faces": int(band.sum()),
            "band_faces_start_label_unseen": int(start_unseen.sum()),
            "moved_faces_start_label_unseen": int((moved & start_unseen).sum()),
            "moved_to_unseen_faces": int(moved_to_unseen.sum()),
            "moved_texels": float(nf[moved].sum()), "moved_texels_start_label_unseen": float(nf[moved & start_unseen].sum())}
