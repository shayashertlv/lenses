# WP5 photo-projected print: final design

Written 2026-10-05 from the feasibility probe and three independent designs, each prototyped at $0 on q001
(`data/reference/projection/q001.glb`), the six FT1119 photos and the WP3b cameras, in scratch under
`C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp5/`: registration-first **R** (`design_reg/`), appearance-first **A**
(`ap*`), coverage-first **C** (`cov/`). **Base = R**, the only design whose prototype beats Astra on both metrics on
held-out pixels. From **A**: the chroma registration signal, the energy floor against the blur trap, the highlight and
flatten rules. From **C**: the UV layout, the Blender side, the byte-identity guarantee, the fill settings, the
independent instrument and the real-vs-real envelope. [Bracketed] numbers are prototype results to be re-measured.

## 0. Go / no-go

**NO-GO for WP5 as PLAN §4 writes it. The stop rule has fired, and this section is the report it requires.** With q001
and the WP3b silhouette cameras the brow print is **1.88–2.39 mm** off between the only two views whose print matches
(01↔05, Brow_Right; NCC 0.68–0.80 against a null maximum of 0.35–0.41, `step2b_global.json`). Leaving one view out,
band-pass 1–4 px r beats its null only on 04, and gained ΔE00 ties Astra on 01, 04 and 05 (`step1_baseline.json`).

**How much registration a pass needs** (probe step 4, `step4_tolerance.json`: the registered Brow_Right displaced by δ;
Astra on the same brow scores bp 0.11–0.17, gained ΔE00 7.6–7.9):

| δ (mm) | 0 | 0.42 | 0.62 | 0.83 | 1.25 | 2.5 |
|---|---|---|---|---|---|---|
| bp 1–4 px r | 0.61–0.74 | 0.49–0.58 | 0.35–0.41 | 0.22–0.27 | 0.00–0.07 | ≈ −0.05 |
| ΔE00 gained | 5.0–5.4 | 5.3–5.8 | 5.8–6.4 | 6.2–6.9 | 7.2–8.2 | 8.8–10.5 |

Winning on both metrics needs **≤ 0.8 mm** (0.5 mm with margin); ΔE00 alone holds to about 1.1–1.2 mm. Silhouette
error (0.66–0.79 mm) does not predict print error (about 2 mm), so the per-view WP3b recommendation in PROGRESS (mean
≤ 1.0, p90 ≤ 2.5 mm) would pass views whose print is 2 mm off.

| Registration method (q001) | Print residual | Held out vs Astra | Source |
|---|---|---|---|
| WP3b cameras | 1.88–2.39 mm | bp at noise, ΔE00 tied | probe |
| Camera-only photometric refit (guarded or not; luminance or chroma) | 1.78–2.47 mm; carries over one way only | 1 of 8 cross-brow cases clears the margins | probe R3, R P1, A AP7 |
| Shift fitted on the other brow | the two brows differ by 0.66–0.86 mm | wins 1 of 4 | A AP5 |
| Texture-space stretch warp | not reported | carries over in 2 of 8 cross-half folds | C p5 |
| **Joint (R P2g):** camera per view + bounded per-brow parallax + silhouette guards; fit on even 4 mm stripes, tested on odd, both folds | **Brow_R 0.00–0.21 mm** | **Brow_R bp 0.58–0.69 vs 0.02–0.15; gained ΔE00 4.68–6.74 vs 5.50–12.76** | `p2g_joint.json`, `p2g_swap.json` |

Under the joint model **Brow_Left fails** (not significant in 3 of 4 direction × fold combinations; gained ΔE00 ties or
loses in both folds: 7.08/7.13, 7.95/7.96, 7.16/6.87, 9.56/9.56), and **the temples fail** (no pair registers on
luminance, R P7; Temple_R 01↔06 with hinge plus parallax loses ΔE00 10.03/12.59 against 9.07/9.44, R P8).

**Verdict: conditional GO, on three terms (decisions D1–D6, §12).**
1. **Threshold:** the stop-rule threshold becomes photometric print registration ≤ 0.8 mm per region (target 0.5),
   measured by the §5.4 gate. Silhouettes only admit colour donors, per (view, part).
2. **Registration:** §5's joint model on chroma plus luminance. Camera-only refinement is dropped.
3. **Proof:** leave one view out × 4 mm stripes, both folds, required only on regions that register. Pure
   leave-one-view-out is impossible on FT1119 (only 01 and 05 share brow print that registers). Other regions ship
   flagged and are judged by realism statistics and the owner's eye.

**Predicted: a pass on Brow_Right's front face only: at most 3.5% of the print area (255.6 of 7,350 mm²).**
(Corrected 2026-10-05, build review: the first figure, about 16% = 1,156 mm², counted the whole Brow_Right part. Regions
are split by facing class (§5.1) and only the front face has a partner view: the brow tops' 01/05 pairs give the gate
fewer than 400 pixels, the backs are seen by 02 alone and the bottoms by no view at donor angles, so the rest of the
part cannot be pixel-verified.) Two untested points decide
STOP A. **(a)** P2g kept view 01's front-only IoU (0.911 → 0.891), but its whole-view IoU fell 0.870 → 0.812 (contour
0.79 → 1.19 mm) with the temples unhinged; the guard here forbids that, so the hinge fit must win it back. **(b)** Warmth
already sees the Brow_Left offset (1.48–1.78 mm, NCC 0.50–0.53 against a null of 0.22–0.27) where luminance stays at
null (A AP2); whether the chroma objective registers it is unknown. Under the PLAN's literal every-region rule, WP5
stays stopped on FT1119 because of Brow_Left. **Build nothing until the owner answers D1–D6.**

## 1. Scores (0–10) and what was taken

| Design | Beats both | Stop honesty | PLAN coverage | Testability | Simplicity | Taken |
|---|---|---|---|---|---|---|
| R | 7 | 8 | 9 | 8 | 4 | base: step 0, decoy gate, tiers, stripes protocol, statuses, STOP A/B |
| A | 3 | 6 | 7 | 7 | 7 | chroma signal, energy floor, highlight detect-and-replace, normal-space flatten, PatchMatch pitfall |
| C | 2 | 8 | 9 | 9 | 5 | strip + cap charts, Blender side and exporter, byte-identical compose, fill test, independent instrument, real-vs-real envelope |

## 2. Conflicts resolved

| Topic | R | A | C | Decision and reason |
|---|---|---|---|---|
| Model | camera + parallax + guards | texture-space shift | camera-only + warp | **R**: the only model that carried over to held-out stripes in both folds (§0) |
| Signal | luminance | warmth R−B, irange max−min | luminance | **Objective: warmth + luminance. Gate: warmth, with irange agreeing.** A white highlight adds equally to every channel. Warmth registers 10 of 22 view-pair regions, luminance 4 [AP2]. The check grades luminance, which the gate does not decide on |
| Donors | edge per (view, region) | silhouette as sanity check | per part | **Edge per (view, part)** (§3). A's cases (06 carries temple print; 04 never registers) are handled by the print gate |
| Proof | leave one view out × stripes | tolerant ±2.5 mm | strict + realism | **Stripes + realism.** The tolerant form passes a known 1.5–1.9 mm offset by construction, where step 4 shows bp ≈ 0. Diagnostic only |
| Blur | unguarded | energy floor | energy envelope | **Floor in every pixel verdict.** A σ = 4 px blur beat Astra on gained ΔE00 on 4/4 brows and on bp on 3/4, erasing 36–50% of the contrast [AP8] |
| Weights | tiers, cos² × px/mm, cos ≥ 0.5 | factors 1 / 0.5 / 0.1 | MRF −log(w/w_best) | **R's tiers choose donors; C's crease MRF places label changes.** PLAN's cos × px/mm took 43–68% of the brow front from top view 04 and halved the contrast [R P3] |
| Highlights | Shen–Zheng (S–Z) on chromatic | detect, replace; S–Z tested | S–Z + keep test | **A.** S–Z helped 05→01 (7.55 → 6.65), hurt 01→05 (7.50 → 8.84) and greyed the amber [AP1] |
| Flatten | σ ≥ 4 mm low-pass | normal-space spherical harmonics (SH) | σ 4 mm ratio | **A.** The image low-pass "won" ΔE while losing 27% of the energy [AP4] |
| UV layout | strip on host | strip | strip + caps in Blender, layer last | **C.** Strip-only flips 3.1–4.5% of brow area; strip + caps gives 0 flips, 0 overlaps [p1c]; bake and renderer share UVs |
| Density | 2 × max px/mm | 10/mm | 10/mm | **2 × the part's max donor px/mm, capped by 4096².** C's lossless 10/mm was shown only on 4.8 px/mm views; 02/04 are at 7.6–8.0. FT1119: 16 |
| Seams | multi-band in tier 1 | blend inside band | 2-texel band + repair | **Blend only registered pairs, in a 2- or 4-texel band chosen by test; elsewhere a hard crease seam plus PatchMatch repair.** C: 3-texel bands cost 0.1–0.2 energy; A: blending cut the seam step 19.7 → 7.2 |
| Radius | {5, 9, 15} px | {3, 5, 9} px | 3 px | **{0.3, 0.5, 0.9} mm per part, by knockout**: a texel radius changes meaning with density |
| Package | `photoprint` | `print` | `print` | **`photoprint`**: no clash with the builtin |

## 3. Two thresholds, two gates

| Quantity | Controls | Measured by | Threshold (owner, pending) |
|---|---|---|---|
| **Edge** e(v, p) | whether view v may donate colour to part p | contour distance on p's own contour (R P6), mm at the front pivot, re-measured after registration | mean ≤ 1.0, p90 ≤ 2.5 mm |
| **Print** r(v, g) | whether v joins region g's registered set (mixing, blending, pixel proof) | §5.4, both directions vs the anchor, both folds | ≤ 0.8 mm; target 0.5 |

FT1119 edges, mean/p90 mm [R P6; C p7 reaches the same decisions]:

| View | Brow_L | Brow_R | Temple_L | Temple_R | Admitted for |
|---|---|---|---|---|---|
| 01 | 0.71/1.13 | 0.70/1.79 | 0.68/1.87 | 0.96/1.79 | all |
| 02 | 0.41/0.79 | 0.41/0.88 | 0.76/1.59 | 0.77/1.63 | all |
| 03 | 2.20/3.92 | 2.70/3.40 | 0.58/1.39 | 3.27/13.73 | Temple_L |
| 04 | 0.60/1.33 | 0.59/1.34 | 0.76/1.58 | 0.73/1.50 | all |
| 05 | 0.35/0.86 | 0.35/0.83 | 0.83/1.40 | 1.04/2.49 | brows, Temple_L |
| 06 | 2.50/4.31 | 2.23/3.81 | 0.73/1.69 | 0.80/1.71 | temples |

- **Per-part gating raises AR-visible photo coverage** [C p10]: Temple_L 0.39 → 0.72; Temple_R 0.37 → 0.45, plus 0.26
  mirrored.
- **Edges are re-measured after registration.** After P2g, view 01's brow edges improved (0.71 → 0.54 mm) while its
  temple edges worsened (0.68 → 1.40 mm).

## 4. Host tool and Blender side

**`g3d.print_uv(parts, *, density='auto', layer='g3d_print', dry_run=False)`** (Blender) runs the `g3d/printuv` core
(§6.1) in world mm, appends the UV layer **last** and never changes the active render UV. It stores
`g3d_print_uv = {texcoord, tx_per_mm, geometry_sha}`, which `g3d/export_glb.py` copies to node extras via WP4's
`g3d_seat` allowlist (`texcoord = mesh.uv_layers.find('g3d_print')`). It refuses Mirror modifiers and topology
modifiers that change the exported layout.

**`g3d_host.photoprint.project_photo_texture(job, parts, views='all', *, print_reg_mm=None, edge_mean_mm=None,
edge_p90_mm=None, atlas_max=4096, mirror='auto', keep_photo_text=False, fill='patchmatch', highlights='auto',
flatten='test', alpha_from_lightness=False, check=True, seed=0, workers=None) -> dict`**
- **Reads** the job's `images/`, view kinds, WP3b `cameras.json` and the latest export GLB, and bakes into that GLB's
  TEXCOORD.
- **Refuses** when thresholds are missing (no silent default), on parts without `g3d_print_uv`, on lens parts and on
  parts with metallic ≥ 0.5.
- **Writes** `job/print/<geom_sha8>/`: `print.png` (sRGB), optional `alpha.png`, `classes.png`, `source.png`,
  `tier.png`, `registration.json` (camera changes from WP3b, hinges, parallax per region and fold, guards, gate rows),
  `check_print.json`, `sheets/`, `report.json` (status shares, parallax discrepancy and fold spread, warnings, timings,
  input hashes, scipy version).
- **Cache key:** geometry, camera and photo hashes plus thresholds; a material-only change skips step 0.

**`g3d.attach_print(report, parts=None, *, gain=(1, 1, 1), transmission_map=False)`** / `g3d.detach_print` (Blender)
pack `print.png` (sRGB, Linear, Extend) and wire Image ← UV Map `g3d_print` → Mix Multiply (B = gain) → Base Color;
Blender 5.2 folds the Mix into `baseColorFactor` (`io_scene_gltf2/blender/exp/material/search_node_tree.py:425-450`).
The previous Base Color is kept in `g3d_print_prev`; a shared material is made single-user first. **No other BSDF input
is touched** (opacity, transmission, roughness, clearcoat, metalness, IOR stay Astra's, PLAN §2). `geometry_sha` is
stored, so a later geometry edit makes `validate_spec` and `check_print` report "print stale; re-project". arview
already validates `texCoord` (`arview/src/model/load.ts:269-275`).

## 5. Step 0: registration (before the PLAN's seven steps)

**5.1 Groups, regions, anchors.** Rigid groups: the front piece, plus one per temple (WP1 rule: behind the lens at
|x| > 45 mm) hinged at the screw axis. Regions: each selected part split by facing class; elongated parts get 4 mm
stripes (|x| for brows, |z| for temples) with 0.5 mm guard bands, even = fold A, odd = fold B (print correlation length
1.04 mm [R P3]). Anchor: the admitted view with the largest Σ cos² × px/mm (FT1119: brow fronts 05, Temple_R outer 06,
Temple_L outer 03, inner faces 02, tops 04).

**5.2 Unknowns** (Powell, started from WP3b)
- **Per non-anchor view:** yaw ±6°, pitch and roll ±4°, pivot u/v ±3 mm. Magnification and perspective stay fixed
  (freeing magnification only added drift [R P1 g6]).
- **Per view and temple:** hinge α ±6° (0° at the WP3b cameras [R P5]; it matters once a camera is refitted).
- **Per region:** parallax d_g(s) = a + b·s along the anchor's view axis, |a|, |b| ≤ 4 mm, s ∈ [−1, 1] (8 mm became a
  free warp [R P2]). The region's vertices move by d_g to give S′; bake and check both sample S′ (same triangles and
  UVs) while the GLB keeps S. Moving along the anchor's axis leaves the anchor's projection unchanged.

**5.3 Objective, guards and folds**
- **Objective:** Σ over view pairs of n·(1 − NCC), on warmth and on luminance. 30k area-uniform samples per region
  (fixed seed); bands 1.6–3.2, then 0.8–1.6, then 0.4–0.8 mm; visibility recomputed per band; ≤ 2,500 evaluations per
  band.
- **Guards:** front-group IoU, each temple's IoU with its hinge, and whole-view IoU on S′. Each may fall at most 0.02
  below WP3b; beyond that the penalty is 0.5 NCC per 0.01.
- **Folds:** fit on A and evaluate on B, then the reverse; the bake refits on all stripes. An extrapolation fold (fit
  inner halves, evaluate outer halves) is reported but never gates.
- **Runtime:** 748–903 s per region pair and fold on one process. Target ≤ 5 min per job, using a process pool (EcoQoS
  opted out per process), stride-3 guards and 15k samples. A test must show the FT1119 gate decisions do not change.

**5.4 Gate** for region g and ordered pair (w → v)
1. Render v's pixels of g on S′, textured from w alone.
2. Band-pass the render and the photo with a masked DoG, σ 0.2 and 1.6 mm.
3. E = the rendered region eroded 2 px ∩ the acetate mask eroded 1 px ∩ the evaluated fold.
4. Search integer shifts up to R = ⌈4 mm × px/mm⌉, then refine with a parabola.
5. **Decoys:** displace the photo along g's principal image axis by ±(R + 3), ±(R + 6) and ±(R + 9) mm, each with ≥ 60%
   overlap. A decoy inside the window returns the true peak (R P7b: null 0.68 = peak 0.68).
6. **Significant** when the warmth peak ≥ max(0.45, decoy max + 0.15) and the irange peak lies within 1 px of it. The
   luminance gate is reported alongside.
7. **Residual** = |best shift| in mm.

**Registered(v, g)** = significant and residual ≤ `print_reg_mm`, against the anchor in both directions and both folds.
R's luminance version of this rule matched check_print on all 8 brow cases. As a cross-check, the held-out bp is
converted to a shift through the job's step-4 curve.

## 6. Steps 1–7 (PLAN §4 WP5), rebuilt around registration

**6.1 UV layout** (C's method; numpy core `g3d/printuv`, run in Blender).
- **Strip chart** per elongated part: principal axis, 2 mm slices, a centreline of degree ≤ 5; s = arc length along
  it, t = arc length around the section from a rotation-minimising frame seeded mid-curve. A face stays in the strip
  only if unflipped, anisotropy < 3, |∇s| > 0.5 and in the largest component.
- **Cap charts** take every other face by the nearest of 6 axes (n·d ≥ 0.577, so none can flip), laid out as seen from
  outside so text reads correctly.
- **Cuts** on the brow's back face and the temple's inner-bottom edge (an AR-visibility cut without a head occluder
  landed on the outer face [C p1d]). Mirror partners reuse the mirrored layout (mirroring = index copy); shelf packing,
  8-texel gutters.
- **Refuses** unless 0 overlaps, 0 flips, anisotropy p95 ≤ 2.0, density p1 ≥ 0.6 × target. [C at 10/mm: 87 charts,
  2048×988, anisotropy p95 1.52. At 16/mm about 3300×1580.]

**6.2 Texels and tiers.** Each texel gets its point and normal on S and S′. A view counts at a texel only if it sees the
point past the whole scene (lenses, eyewire, hinges and inlays occlude; 0.5 mm tolerance), the point is inside the matte
eroded 1 px and more than 0.5 mm from an occluder contact, and the **per-pixel** class is acetate (40–65% of frame
pixels are achromatic: the print's dark phase [AP1]). Sampling is bicubic at π_v(S′).

| Tier | Members | Where | Weight |
|---|---|---|---|
| 1 | registered set | cos ≥ 0.5 | cos² × px/mm |
| 2 | admitted but unregistered (anchor first) | only where tier 1 sees nothing, cos ≥ 0.5 | cos² × px/mm |
| 3 | unseen | everywhere else | filled |

Mixing every qualified view gives energy 0.48–0.68; the registered partner alone gives 0.87–1.19 [AP5]. The probe's
min cos 0.2 dotted the brow top edges [R].

**6.3 Gains and highlights.** `quantile_gain` per (view, region) to the anchor, from same-surface overlaps after
registration (≥ 200 pairs, clipped and specular texels excluded); gains run first because the highlight test needs gained
colours. Highlights are **detected and replaced, never subtracted**:
- clipped pixels (any channel ≥ 250, dilated 1 px) are holes for that view;
- a texel is flagged by the specular rule (luminance > 175, saturation < 60), or when it is > 25 levels brighter than
  the lowest gained tier-1 view and less saturated; it takes that lowest value **on the flagged set only**, or becomes a
  hole (a whole-texture robust low erased 32–39% of the amber [AP4]);
- Shen–Zheng runs only where max−min ≥ 12, and only if registered overlaps then agree better by ΔE ≥ 0.3 in both
  directions with bp down ≤ 0.01. FT1119: off.

**6.4 Labels and seams.** An MRF per tier: data cost −log(w/w_best), Potts λ = 1, lowered to 0.2 across creases
(dihedral ≥ 25°). This avoids the best-view mosaic, where an 8 px band covered 37–47% of seen texels [AP6].
- **Tier-1 pairs:** `GraphCutSeamFinder` inside near-best overlaps (≥ 0.7 × best), on crops, then a 3-band
  `MultiBandBlender` on nearest-padded int16 sources, composited only inside the band.
- **Every other boundary:** a hard seam on a crease plus PatchMatch band repair, never blended.
- **Band:** 2 or 4 texels, kept if the seam step ratio is ≤ 3.0 and band energy ≥ 0.85; seams ≤ 15% of projected texels.

**6.5 Fill.** Mirror first, outside the text mask, re-synthesising a 3-texel boundary; report the mirrored share. Then
PyPatchMatch with an explicit hole and a `global_mask` of donors only (same part, same Lab family k ≤ 3 picked by the
hole's ring, same face class):
- the mask **never contains the hole** (a silent no-op [AP6]; a test asserts the hole changed);
- radius per part from {0.3, 0.5, 0.9} mm, by knocking out 2–16 mm bands and an 8×5 mm block;
- periodic padding across the strip cut; a warning above 25% PatchMatch share of a part's AR-visible area.

Text mask: the brand-print geometry (q001 `Left_inner_brand_print`, 11,494 texels at 10/mm) dilated 1.5 mm, plus photo
strokes. Text is never mirrored, and is filled where a decal exists unless `keep_photo_text` (D6).

**6.6 Flatten.** Order-2 normal-space SH on log-luminance, kept only if registered overlaps gain ΔE ≥ 0.3 in both
directions with bp down ≤ 0.01 and energy ≥ 0.95. FT1119: R² 0.085/0.061, so off. Image low-pass is banned.

**6.7 Compose and bake.** `final = where(protected, P0, edited)` on uint8. Checked: protected texels byte-identical, and
PatchMatch changes nothing outside its hole. MultiBandBlender output is never copied wholesale (it moved pixels > 100 px
from the seam by up to 6 levels [C]). PNG ≤ 4096, 8-texel padding; alpha or transmission only on Astra's request.

## 7. check_print

For each region with ≥ 2 registered views, each registered held-out view h and each fold: registration uses fold-A
pixels of every view; production steps 2–7 bake the texture from every view except h; it is rendered at h through h's
registration on S′. Astra's texture (q001's own images and UVs) is rendered at the same camera, and both are graded on
identical pixels: E (fold B) ∩ projected texels ∩ the S render ∩ the S′ render. **B0**, strict leave one view out (no
pixel of h anywhere, h at its WP3b camera), is always reported and never gates.

**Metrics** (the probe's definitions): luminance = mean of sRGB; masked Gaussian G_σ(l·S)/G_σ(S); lum r at σ = 1 px;
**bp 1–4 px r** (DoG 1 − 4 px, Pearson on E); bp mm r (0.25 − 1.0 mm); the 18-shift null; ΔE00 raw and gained (one
`quantile_gain` per render on E, applied the same way to both methods). **Added:** energy ratio at 0.4–1.6 mm and 1–4 px;
spectrum error (mean |log₂| band contrast over octaves 0.2–3.2 mm); correlation length; decile ΔE00 raw and gained;
contamination share; an 8×8 px block-bootstrap 95% CI of the gained-ΔE00 difference; a cross-fitted gain; the tolerant
±2.5 mm bp (diagnostic only).

**Independence.** The metrics live in `tests/instruments/print_metrics.py`, which never imports `photoprint` (a test
enforces it). The tool's own numbers are never the proof (WP4 rule).

**Sheets** (≤ 1600×1200, nearest 2× for the 4.8 px/mm views), rows: photo; held-out render; the same texture on S at the
WP3b camera (what AR shows); Astra; |ΔE00| 0–20; tier and source; stripe mask. Every criterion is in the header; a
strip-space sheet adds class overlays.

## 8. $0 proof (FT1119: q001 sha256 af6e9d10…, six photos, WP3b cameras, scipy 1.16.0)

| # | Test | Pass criterion | Prototype |
|---|---|---|---|
| A0 | Synthetic: q001 with a known print at the 6 cameras; injected 01 +1° yaw, 2 mm pivot, 2 mm parallax | photo-space residual ≤ 0.2 mm at every view (parameter recovery reported, risk 2); unperturbed bp ≥ 0.9; mean \|null r\| ≤ 0.05; the gate refuses Astra's texture as photos, a +2 mm camera, photos 01↔06 swapped; a σ = 4 px blurred bake fails C3 | not run |
| A1 | UV layout | 0 overlaps, 0 flips, anisotropy p95 ≤ 2.0, density p1 ≥ 0.6 × target, mirror partners index-identical | 0, 0, 1.52 [C] |
| A2 | Round trip: bake from v, render at v (including the 8 px/mm views 02, 04) | bp ≥ 0.99, energy ≥ 0.95, raw ΔE00 ≤ 1.0 | 05/06: 0.997–1.000, 0.97–1.00, 0.10–0.33 [C p9] |
| A3 | Compose | protected byte-identical; PatchMatch outside-hole changes 0; the hole changed | true × 7; 0 [C] |
| A4 | Fill knockout | median \|log energy\| ≤ 0.15 in both bands, mean-colour ΔE00 ≤ 2.0, seam ratio ≤ 1.2 | r = 3 at 10/mm: 0.08–0.10 / 0.05–0.08, 0.73–1.58, 0.94–1.02 [C p4] |
| A5 | UV-seam continuity | p95 ΔE00 ≤ 3 between texels of the same 3D point | not run |
| A6 | Blender round trip | TEXCOORD_k; `baseColorTexture.texCoord == k`; other material JSON bit-identical; `validate_spec` passes | not built |
| A7 | Determinism | two runs give identical PNG sha256 and registration.json | not run |
| B1 | Registration | ≥ 1 brow front registered, both directions, both folds; every guard within 0.02, including 01 whole-view with the hinge; parallax ≤ 4 mm | Brow_R 0.00–0.21 mm; **01 whole-view 0.870 → 0.812 unhinged** [R P2g] |
| B2 | Pixels: each (held-out view ∈ {01, 05}, registered region, fold) with ≥ 1,000 E px | **C1** gained ΔE00(P) ≤ ΔE00(A) − 0.5, bootstrap CI < 0; **C2** bp(P) − bp(A) ≥ max(0.15, 2σ_null) and bp(P) ≥ μ_null + 2σ_null; **C3** energy at 0.4–1.6 mm in [0.8, 1.25] and at 1–4 px ≥ 0.7; **C4** contamination ≤ 2% | Brow_R: C1 margins 0.82–6.02; C2 margins 0.43–0.67 vs 2σ ≤ 0.36; C3/C4 unmeasured |
| B3 | Realism: every held-out render with projected share ≥ 0.5 and ≥ 1,500 E px, registered or not | spectrum error ≤ 0.25; correlation-length ratio 0.8–1.25; raw decile ΔE00 ≤ 4.0 and ≤ Astra − 3.0; C3 | partner-only 0.055–0.068 / 1.00 / 2.6–2.9 pass; Astra 0.43–0.94 / 2.2–2.4× / 8.9–11.1 fail; C's mixed bake fails energy (0.47–0.69) |
| B4 | arview (WP1 stage E): front, yaw ±30/±45, pitch ±20, three backgrounds, worn portrait; brow crops vs the nearest photo (front → 05, +30° → 01) | spectrum ≤ 0.25; correlation-length ratio 0.8–1.25; gained decile ΔE00 ≤ 3.5; energy closer to 1 than Astra's; owner verdict "reads as the real havana" in the ledger | not run |
| B5 | Material control | the GLB differs only in baseColorTexture/Factor (and alpha if asked) | not built |

**B3 calibration.** Two real photos of the same brow differ by EMD L\* 1.45–4.61, with energy 0.87–1.12 at 0.25–1 mm
[C p8]. B3's limits sit at that real-vs-real spread.

## 9. Stop logic

- **STOP 0 (now):** the probe fired the PLAN stop rule. Report and wait for D1–D6.
- **Build order:** (1) `print_metrics`, signals, gate, A0 → (2) registration and the check harness (B1, B2, A7) →
  **STOP A** → (3) UV layout and Blender side (A1, A6), then steps 2–7 (A2–A5, B3, B5) → **STOP B** → (4) B4 with the
  owner.
- **STOP A** fires if A0 fails, if no brow front registers in both folds, if a registered region fails C1–C4 in either
  fold, or if a guard must be broken (including view 01's whole silhouette with the hinge). After a STOP A failure,
  WP5 can deliver only single-source print checked by B3, and only if the owner accepts that scope (D4).
- **STOP B** fires if the full bake loses any C1–C4 margin on a region that passed A, or if A1–A6 or B3 fails.
- **Region statuses for Astra:** **registered** (tier-1 mixing, check passed; the only status called "real print,
  verified"); **anchor-only** (views overlap but do not register; flagged with the residual and the parallax
  discrepancy); **single-view** (pixel-unverifiable; B3 only); **unseen** (filled). Overall `ok`, `partial` or
  `check_failed`. A part whose anchor covers < 30% gets no texture unless Astra overrides. Nothing is dropped silently.
- **Predicted FT1119:** Brow_R front registered; Brow_L front anchor-only (05), or registered if chroma succeeds; tops
  (04) and backs (02) single-view; Temple_R outer anchor-only (06); Temple_L outer single-view (03), or registered on
  01↔06 if the gate confirms AP2 (warmth 0.59/0.86 mm, one direction > 0.8); inner faces single-view (02).

## 10. Module layout

| Path | Contents |
|---|---|
| `glasses3d/g3d/printuv/{strip,charts,pack,check,mirror}.py` | numpy core shared by Blender and the host |
| `glasses3d/g3d/print_bpy.py` | `print_uv`, `attach_print`, `detach_print` (receipts and backups; the MCP has no undo) |
| `glasses3d/g3d/export_glb.py` | allowlist `g3d_print_uv` + TEXCOORD index (WP3's invariance snapshot must still pass) |
| `glasses3d/g3d_host/photoprint/` | `__init__` (`project_photo_texture`), `inputs`, `regions`, `signals`, `edges`, `register`, `gate`, `texels`, `gains`, `highlights`, `labels`, `fill`, `shading`, `compose`, `bake`, `check_print`, `sheets`, `report` |
| `glasses3d/tests/instruments/print_metrics.py` | the independent metrics |
| `glasses3d/tests/photoprint/` | `test_signals`, `test_gate` (injected shifts; decoys never reach the peak), `test_register_synthetic` (A0), `test_printuv`, `test_compose`, `test_fill`, `test_check_print`, `test_determinism`, `test_no_import` |
| `glasses3d/tests/blender/print_roundtrip.py`, `glasses3d/tests/proofs/wp5_ft1119.py` | A6; the proof run, written to `data/wp5/ft1119/` |

**Ports** (reviewed, copied in with tests, listed in PORT_REVIEW):
- `wp5lib` metrics → `print_metrics` and `signals`;
- `refine` sampling, `design_reg/p2_parallax` (`AnchorRayWarp`, `stripe_masks`) and the `p2g_joint` guards →
  `register` and `regions`;
- `p7b` with the decoy fix → `gate`; `p6_region_edges` → `edges`;
- `cov/stripuv` and `cov/charts` → `printuv`; `cov/p4_fill` → `fill`; `aplib` signals → `signals`.

WP3b's texture utilities are reused unchanged.

## 11. Open risks

1. **One view pair (01, 05) carries every pixel proof.** Stripes are spatial cross-validation, not view
   cross-validation, and every threshold comes from one product.
2. **Camera and parallax trade off.** Between folds, Brow_R's parallax differs by 0.65–1.63 mm and camera 01's yaw is
   0.76° vs 1.71°, at the same ≤ 0.21 mm photo residual. S′ is therefore not unique, and AR angles outside the photos
   inherit about 1 mm of uncertainty. Astra hears of a discrepancy only when |d| > 2 × the fold spread.
3. **View 01's whole silhouette under the hinge fit is untested.** It is the first STOP A measurement.
4. **Brow_Left's cause is unresolved.** Its viewing geometry (cos 0.71 vs 0.79; 3.4 vs 3.8 px/mm) does not explain
   it, and chroma inside the joint model is untested.
5. **Live runs use Astra's geometry.** Registration is re-measured each run; other shape errors may need more than a
   linear parallax.
6. **Ceilings and cost.** At 4.8 px/mm bp tops out at 0.6–0.74 even when perfectly registered. Runtime is 748–903 s per
   region pair and fold on one process (the 5-minute target is unproven). scipy 1.18.1 drifts the fits by up to
   0.19° / 1.07 px, so 1.16.0 stays pinned.
7. **Double lighting.** Photo shading is baked in and flatten is off on FT1119; only B4 and the owner judge it.
8. **Low bar.** Astra's unlit noise is easy to beat on ΔE00; the CI, C2, the energy floor and B3 keep it honest.
9. **Fill.** PatchMatch is smoother than real print (an 8×5 mm block kept 0.30–0.79 of the energy [AP6]). Text is
   doubled if both the photo text and the decal stay (D6).
10. **Owner views.** Gates are measurements, identical for every view. A view that fails a print gate still drives
    cameras, silhouettes and colour, and donates where it alone sees the surface. It is never demoted.

## 12. Owner decisions and PLAN corrections

- **D1.** `print_reg_mm` = 0.8 mm to pass, 0.5 mm target, measured photometrically per region (§5.4). It replaces the
  silhouette threshold in WP5's stop rule.
- **D2.** The edge gate is 1.0 mm mean / 2.5 mm p90 **per (view, part)**, and only decides colour donors.
- **D3.** The proof is leave one view out × 4 mm stripes, both folds, on registered regions. B0 and the tolerant form
  are reported only.
- **D4.** Unregistered regions are printed from their anchor and flagged. Is WP5 acceptable with at most Brow_Right's
  front face (255.6 of 7,350 mm², **3.5%** of the area) pixel-verified? If not, WP5 stops on FT1119. (Corrected
  2026-10-05: this question first said "about 16%", which counted the whole Brow_Right part; the owner's "continue"
  was given against 16% and must be asked again with 3.5%.)
- **D5.** The parallax is a sampling correction: the GLB is unchanged and the discrepancy goes to Astra.
- **D6.** Text: either the decal carries it and the photo text is filled (default), or the photo text stays and the
  decal is hidden.

**PLAN text to fix after the decisions** (not edited here):
- **§1/§4:** "0.017 vs 0.066" cannot be reproduced; its filter was never recorded. The probe's filter gives 0.15 vs
  0.035 (null std 0.07–0.11).
- **§4 WP5 step 2:** cos × px/mm becomes tiers with cos².
- **§3:** Shen–Zheng only behind its test; "achromatic" decided per texel; `global_mask` never contains the hole; the
  flatten keep test includes energy and bp.
- **PROGRESS (WP3b):** the per-view threshold becomes D1 + D2.
