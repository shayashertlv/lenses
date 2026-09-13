/** Explicit held export only. OffscreenCanvas has a native asynchronous encoder. */
export async function canvasPng(canvas: OffscreenCanvas): Promise<string> {
  const bytes = new Uint8Array(await (await canvas.convertToBlob({type: 'image/png'})).arrayBuffer());
  let value = '';
  for (let offset = 0; offset < bytes.length; offset += 0x4000)
    value += String.fromCharCode(...bytes.subarray(offset, offset + 0x4000));
  return 'data:image/png;base64,' + btoa(value);
}
