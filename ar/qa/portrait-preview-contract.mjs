/** QA-only capture framing, independent of model geometry and lighting. */
export function eyeCrop(landmarks, width, height) {
  const a = landmarks?.[33], b = landmarks?.[263];
  if (![width, height].every(n => Number.isInteger(n) && n > 0)
      || !a || !b || ![a.x, a.y, b.x, b.y].every(Number.isFinite)) throw Error('Invalid portrait landmarks or dimensions');
  const span = Math.hypot((a.x - b.x) * width, (a.y - b.y) * height);
  if (span < width * .08 || span > width * .8) throw Error('Portrait eye span is unsuitable for a detail capture');
  const cropWidth = Math.min(width, Math.ceil(span * 2.15));
  const cropHeight = Math.min(height, Math.ceil(span * 1.05));
  const x = Math.max(0, Math.min(width - cropWidth, Math.floor((a.x + b.x) * width / 2 - cropWidth / 2)));
  const y = Math.max(0, Math.min(height - cropHeight, Math.floor((a.y + b.y) * height / 2 + span * .1 - cropHeight / 2)));
  return {x, y, width: cropWidth, height: cropHeight};
}

export function validatePortraitManifest(manifest) {
  if (manifest?.schema_version !== 1 || !/^[a-f0-9]{64}$/.test(manifest.model_sha256)
      || !/^[a-f0-9]{64}$/.test(manifest.fixture?.sha256)
      || manifest.fixture.id !== 'face-a' || manifest.fixture.synthetic !== true
      || !Number.isFinite(manifest.width_mm) || manifest.width_mm < 60 || manifest.width_mm > 250) {
    throw Error('Invalid pinned portrait preview manifest');
  }
  return manifest;
}

/** MediaPipe emits this known successful CPU initialization notice through stderr. Keep all other errors fatal. */
export function consoleLevel(type, text) {
  return type === 'error' && text === 'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.' ? 'info' : type;
}
