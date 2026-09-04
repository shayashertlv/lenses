# Privacy

## What stays on the device

Face detection, enrollment, fitting, and rendering run in the browser. The app
does not upload camera frames, landmarks, or face models.

After a successful scan, the browser may store:

- `ar-v2.facemodel` — the face geometry and its fitting metadata
- `ar-v2.knownpd` — an optional PD entered by the wearer

The saved model contains geometry, uncertainty, scale information, and camera
intrinsics. It does not contain a camera image or texture.

Capture frames are held only in memory while the current tab needs them. Choosing
**Save this scan** explicitly creates a local download; the app does not upload
or retain that download itself.

## Delete my measurements

The delete control removes the stored face model, optional PD, and legacy scan
history. It also clears active in-memory model, capture, tracking, and rendering
state, and invalidates an enrollment that is still running so it cannot restore
deleted data afterward.

For an additional browser-level reset, clear this site's storage in the browser
settings.
