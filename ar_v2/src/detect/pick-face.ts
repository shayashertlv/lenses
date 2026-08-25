/**
 * Which of the detected faces is the wearer.
 *
 * Ported from v1 essentially unchanged, because the reasoning was right and the
 * situation is identical: asking MediaPipe for two faces (to disable its own
 * untunable smoothing) costs the right to index `[0]`, since nothing documents
 * that ordering as stable. A second person walking in, or a portrait on the wall
 * behind the wearer, could take the glasses and hand them back a frame later.
 *
 * Chosen by the **area** the landmarks span, not width or height. Whoever is
 * being fitted is at arm's length from their own camera; anybody else is further
 * away and therefore smaller. Area rather than width because a face turned to
 * profile loses width without getting further away, and picking on width would
 * hand the wearer's glasses to a more front-on bystander every time they looked
 * sideways.
 *
 * Stateless, stable frame to frame, and with one face in view it is exactly
 * `[0]` — which is what it replaced.
 */

export interface PointLike { x: number; y: number }

export function pickFace(faces: readonly (readonly PointLike[])[] | undefined): number {
  if (!faces || faces.length === 0) return -1;
  if (faces.length === 1) return 0;

  let best = -1;
  let bestArea = -Infinity;

  for (let i = 0; i < faces.length; i++) {
    const landmarks = faces[i];
    if (!landmarks || landmarks.length === 0) continue;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const point of landmarks) {
      if (point.x < minX) minX = point.x;
      if (point.x > maxX) maxX = point.x;
      if (point.y < minY) minY = point.y;
      if (point.y > maxY) maxY = point.y;
    }

    const area = (maxX - minX) * (maxY - minY);
    if (area > bestArea) { bestArea = area; best = i; }
  }

  return best;
}
