# Privacy

## The short version

A face scan is biometric data. In v2 it is **one immutable object under one
storage key on the wearer's own device**, and that fact is what makes this
document short.

- The scan is solved in the browser. No image, no landmark, and no geometry
  leaves the device.
- The result is one `FaceModel`, serialised to `localStorage` under
  `ar-v2.facemodel`.
- **Two other keys exist and neither holds geometry**: `ar-v2.knownpd`, the one
  number the wearer types in, and `ar-v2.scanhistory`, which is legacy and is
  deleted on sight (below).
- "Delete my measurements" removes all three. That is the whole deletion path,
  and it is in the interface rather than in a policy document.
- No camera frame is ever stored. Frames live in three canvases that are
  overwritten thirty times a second and are gone when the tab closes.

### This section was false for as long as it existed, and here is how

**The claim above — one object, one key, and that is what makes the document
short — was the load-bearing sentence, and it was wrong by six times.** There
were three keys, and `ar-v2.scanhistory` held **up to five further complete
`FaceModel`s**: five more sets of 468 vertex positions, per-vertex uncertainty,
shape coefficients and PD, one per scan, oldest dropped at five.

It was there for a real reason — scanning a face twice and diffing the results
is the only repeatability test that needs no ground truth, and the synthetic
harness cannot do it — but **it was never built**. `loadHistory` had no caller
anywhere in the tree. Nothing read a single stored scan. The app told the wearer
in words that *"Compare scans" will diff them*, about a control that did not
exist, and retained the data to keep that promise.

It is gone as of 2026-08-26. The key is removed at boot as well as by "Delete
my measurements", because deleting the code alone would strand the data: every
browser that had already run this app was holding five scans that nothing would
ever clear.

Two things worth taking from it, since a privacy document that only records its
own correctness is not worth much:

1. **An inventory is the one part of a privacy statement that cannot be
   approximately right.** This one was written from the design rather than from
   the code, and the design was true when it was written.
2. **Retention needs a reader.** The history was justified by a feature, the
   feature was never written, and nothing removed the justification. If
   repeatability comes back it should start from a comparison UI and a stated
   retention period, not from a store that accumulates and waits.

## Why the architecture makes this easy

This is a side effect of the rewrite rather than a feature added to it. In v1
"who this wearer is" lived in a median window, an information filter, an EMA, a
latch and four eased channels, all mutating every frame inside the per-frame
path. There was nothing to hold still, nothing to serialise, and nothing to
delete.

Here there is one value. It can be tested, cached, and deleted, and all three
follow from the same property.

## What is in a `FaceModel`

Enough to identify a face geometrically. Treat it accordingly:

- 468 vertex positions in millimetres — a personal facial geometry
- per-vertex uncertainty
- shape coefficients
- pupillary distance, if the iris resolved
- the camera's solved intrinsics

It contains **no image data and no texture.** You cannot reconstruct a
photograph from it. You can, in principle, match it against another scan of the
same person, which is why it stays on the device.

## The thing v1 got wrong here, so it does not happen again

v1's README states that its telemetry fixtures are *"deliberately not committed:
they are that person's facial geometry, and a repository is a poor place to keep
it."*

They are committed. Three `.ndjson.gz` files — 35 MB of per-frame 478-point
facial landmarks and head-pose matrices for a named subject over a scripted
90-second protocol — are tracked in `ar/tests/fixtures/`, were added in commit
`a845d80`, and are present on `origin/ar-tryon`. Nothing in `ar/.gitignore`
covers `fixtures/`. The diag stills *are* correctly excluded, by a
repository-root `*.png` rule, which is probably why the gap went unnoticed: the
sentence is true of one fixture set and false of the other.

Two things follow for v2:

1. **`.gitignore` refuses the file shapes, not the directory.** `*.facemodel.json`
   and `fixtures/private/` are excluded by pattern, so a fixture has to be
   deliberately renamed to get committed rather than deliberately ignored.
2. **The synthetic population is the default fixture.** Every accuracy number in
   this tree comes from generated faces with known ground truth. There is no
   real person's geometry in this repository and nothing needs one to run.

If real recordings are ever added — and Q8 says they should be, because nothing
here has seen a real camera — they belong outside the repository, with the
subject's written consent, and the path they load from should be `.gitignore`d
before the first one is written.

## Regulatory note, briefly

Facial geometry is biometric data under GDPR Art. 9 and under Illinois BIPA,
among others. On-device processing with explicit deletion is the posture that
keeps the surface small, but it is not a legal opinion and a deployment should
get one — particularly around consent language for the scan and retention of the
stored model.

## If you add a server

Nothing in this design needs one. If a future version syncs models across
devices, the properties to preserve are:

- the model is encrypted with a key the server never sees
- deletion is a delete, not a flag
- the scan itself still happens on the device — never upload frames
