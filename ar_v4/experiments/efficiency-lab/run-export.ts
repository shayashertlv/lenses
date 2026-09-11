export interface RunVideoFile {blob: Blob; filename: string;}
const crcTable = Uint32Array.from({length: 256}, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit++) crc = (crc & 1) ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  return crc >>> 0;
});

/** Streaming CRC avoids materializing a second full recording byte array. */
export async function blobCRC32(blob: Blob): Promise<number> {
  let crc = 0xffffffff;
  const reader = blob.stream().getReader();
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      for (const byte of value) crc = crcTable[(crc ^ byte) & 255]! ^ (crc >>> 8);
    }
  } finally {reader.releaseLock();}
  return (crc ^ 0xffffffff) >>> 0;
}

/** A local, uncompressed ZIP containing telemetry and optional visible-output video. No uploads or image copies. */
export async function createRunArchive(report: unknown, video?: RunVideoFile): Promise<Blob> {
  if (video && (!/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$/.test(video.filename)
    || video.filename === 'telemetry.json' || video.filename.includes('..'))) throw new Error('Invalid recording filename.');
  const json = JSON.stringify(report);
  if (json === undefined) throw new Error('The run report cannot be serialized.');
  const files = [{filename: 'telemetry.json', blob: new Blob([json], {type: 'application/json'})}, ...(video ? [video] : [])];
  const encoder = new TextEncoder(), localParts: BlobPart[] = [], centralParts: Uint8Array<ArrayBuffer>[] = [];
  let offset = 0;
  for (const file of files) {
    const name = encoder.encode(file.filename), size = file.blob.size;
    if (size > 0xffffffff || offset + size + name.byteLength + 30 > 0xffffffff) throw new Error('Recording exceeds the local ZIP size limit.');
    const crc = await blobCRC32(file.blob);
    const local = new Uint8Array(30 + name.byteLength), header = new DataView(local.buffer);
    header.setUint32(0, 0x04034b50, true); header.setUint16(4, 20, true); header.setUint16(6, 0x0800, true);
    header.setUint16(12, 0x0021, true); header.setUint32(14, crc, true);
    header.setUint32(18, size, true); header.setUint32(22, size, true); header.setUint16(26, name.byteLength, true);
    local.set(name, 30);
    const central = new Uint8Array(46 + name.byteLength), record = new DataView(central.buffer);
    record.setUint32(0, 0x02014b50, true); record.setUint16(4, 20, true); record.setUint16(6, 20, true);
    record.setUint16(8, 0x0800, true); record.setUint16(14, 0x0021, true); record.setUint32(16, crc, true);
    record.setUint32(20, size, true); record.setUint32(24, size, true); record.setUint16(28, name.byteLength, true);
    record.setUint32(42, offset, true); central.set(name, 46);
    localParts.push(local, file.blob); centralParts.push(central); offset += local.byteLength + size;
  }
  const centralSize = centralParts.reduce((sum, part) => sum + part.byteLength, 0);
  if (offset + centralSize + 22 > 0xffffffff) throw new Error('Recording exceeds the local ZIP size limit.');
  const end = new Uint8Array(22), record = new DataView(end.buffer);
  record.setUint32(0, 0x06054b50, true); record.setUint16(8, files.length, true); record.setUint16(10, files.length, true);
  record.setUint32(12, centralSize, true); record.setUint32(16, offset, true);
  return new Blob([...localParts, ...centralParts, end], {type: 'application/zip'});
}
