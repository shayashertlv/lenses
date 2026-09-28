# Effective optical groups: isolated feasibility experiment

> Historical prototype evidence. The production implementation is `src/render/effective-optical-topology.ts`
> and `src/render/nearest-optical-groups.ts`; see [production-optical-groups.md](production-optical-groups.md).

Run from `ar/`:

```powershell
node qa/effective-optical-groups.mjs --output=qa/output/effective-optical-groups-reproduced
```

This starts a local Vite server and headless Chromium/ANGLE D3D11, writes a
SHA-bound report and contact sheet, then closes both processes. It requests no
camera access and changes no production render files. Output is local/ignored.
The command currently exits nonzero because the predeclared numeric target is
not fully met; this is intentional, not a completed production profile.

The QA-only renderer keeps closed box geometry, derives one height coordinate
over each semantic group, renders each group's nearest hit, and filters global
camera/light peels against those exact-depth maps. It reuses the canonical lens
response GLSL and CPU evaluator. Groups apply the complete effective response
once, including when an opaque surface or receiver lies inside the group. Both
sides use the same response with a face-forward normal. No refraction, physical
path-length absorption, internal reflections, or distinct rear coating is modeled.

The independent reference uses analytic box slab rays and a separate analytic
box-face raster reference for the identified D3D11 backend. It does not consume
renderer geometry or GPU depth. The latter applies the specified n.8 nearest-even
vertex snapping; continuous-ray residuals remain in the report. Other GPU backends
are explicitly unvalidated. Geometry margins are selected before residuals.

The frozen `qa/output/effective-optical-groups-v4-diagnostic/report.json` contains:

- 14 controlled fixtures, 84 camera cases, 36,470 camera samples (5,598 optical).
- 28 light cases, 47,804 layer samples and 35,851 unfiltered receiver samples.
- Closed/reversed/disconnected geometry; multiple intersections in one group;
  distinct overlapping groups; shared descriptors across different IDs; front,
  posed and back views; opaque stops before/between/inside; total mirrors; four
  groups retained and a fifth rejected on both camera and light paths.
- Exact order invariance for identical coincident attributes. Conflicting
  same-group normals change output by 0.0813169 linear RGB when draw order changes.
  This ambiguity and coincident distinct groups remain **unsupported**: the QA
  oracle identifies them, but this renderer prototype does not implement a
  general importer rejection mechanism.

Strict results remain **failed** with the original 5e-5 linear RGB and 2e-6 depth
targets. Maximum camera RGB error is 8.2661e-5, camera depth error 9.9261e-8,
light/receiver RGB error 1.6886e-5, and light depth error 2.5615e-6. At the worst
camera sample, GPU normals/view directions agree with the reference to about
8e-8, but GPU `acos` reports 84.676506° instead of 84.673271°. The resulting
canonical angular response explains the camera residual. The remaining light
depth discrepancy is unresolved. Neither threshold was increased.

For the measured final camera RGB error, the maximum linear-to-sRGB encoding slope gives a
conservative bound of `3294.6 * error = 0.27234` 8-bit sRGB code values. This bound
does not establish product fidelity or excuse the conflicting-attribute case.

On the recorded Intel Arc 140T/D3D11 desktop, the synthetic 24,576-triangle,
two-group camera+512²-light workload (two synchronous overflow flags included)
averaged 6.58 ms at 480×320 and 8.97 ms at 1280×720 over ten measured iterations
after three warmups. HD float camera/light targets require approximately 152.5 MB
in this simple implementation, of which 47.35 MB is group maps. Estimates exclude
driver padding, reduction targets, geometry and browser framebuffers. No mobile
coverage or real-asset performance is claimed.

The evidence supports continued investigation of nearest-group transport. It
does not authorize arbitrary meshes, establish semantic grouping, implement a
production profile, or alter `front_sheet_v1`.
