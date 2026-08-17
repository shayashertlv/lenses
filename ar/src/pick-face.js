/**
 * Which of the detected faces is the wearer.
 *
 * This exists because of a trade the app makes deliberately. MediaPipe filters its
 * landmarks internally when — and only when — `numFaces` is 1: *"Smoothing is only
 * applied when num_faces is set to 1"*, from their own web guide. That filter runs
 * upstream of every number this pipeline sees, it cannot be tuned, and it cannot even
 * be measured from inside; what it can be is switched off, by asking for two faces.
 * Doing so was worth a large, immediately noticeable reduction in lag.
 *
 * The cost is that the detector may now hand back two faces, and `[0]` stops being a
 * safe thing to index. Nothing documents that ordering as stable, so a second person
 * wandering into frame — or a portrait on the wall behind the wearer — could swap the
 * glasses onto them, and swap them back a frame later.
 *
 * So the wearer is chosen rather than assumed, by the area their landmarks span in
 * the image. Whoever is being fitted is sitting at arm's length from their own
 * camera; anybody else in the room is further away and therefore smaller. It is a
 * stable choice frame to frame, needs no state, and degrades sensibly — with one face
 * in view it is exactly `[0]`, which is what it replaced.
 *
 * Returns the index, or -1 when there is no face at all.
 */
export function pickFace(faces) {
  if (!faces || faces.length === 0) return -1;
  if (faces.length === 1) return 0;

  let best = -1;
  let bestArea = -Infinity;

  for (let i = 0; i < faces.length; i++) {
    const landmarks = faces[i];
    if (!landmarks || landmarks.length === 0) continue;

    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const point of landmarks) {
      if (point.x < minX) minX = point.x;
      if (point.x > maxX) maxX = point.x;
      if (point.y < minY) minY = point.y;
      if (point.y > maxY) maxY = point.y;
    }

    // Area rather than width or height alone: a face turned to profile loses width
    // without getting further away, and picking on width would hand the wearer's
    // glasses to a more front-on bystander every time they looked sideways.
    const area = (maxX - minX) * (maxY - minY);
    if (area > bestArea) {
      bestArea = area;
      best = i;
    }
  }

  return best;
}
