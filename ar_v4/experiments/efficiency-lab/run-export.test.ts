import assert from 'node:assert/strict';
import {test} from 'node:test';
import {blobCRC32, createRunArchive} from './run-export.ts';

test('streamed CRC matches an independent standard vector and empty input', async () => {
  assert.equal(await blobCRC32(new Blob(['123456789'])), 0xcbf43926);
  assert.equal(await blobCRC32(new Blob([])), 0);
});

test('ZIP stores complete JSON and original video, with matching central offsets, CRC and sizes', async () => {
  const video = new Blob([Uint8Array.of(0, 255, 4, 17, 88)], {type: 'video/webm'});
  const expected = {schema: 'test', windows: [{completedArFps: 0}], text: 'שלום'};
  const zip = await createRunArchive(expected, {filename: 'recording.webm', blob: video});
  assert.equal(zip.type, 'application/zip');
  const bytes = new Uint8Array(await zip.arrayBuffer()), view = new DataView(bytes.buffer), decoder = new TextDecoder();
  const files: {offset: number; name: string; bytes: Uint8Array; crc: number}[] = [];
  let position = 0;
  while (view.getUint32(position, true) === 0x04034b50) {
    const size = view.getUint32(position + 18, true), nameLength = view.getUint16(position + 26, true);
    const name = decoder.decode(bytes.slice(position + 30, position + 30 + nameLength));
    const data = bytes.slice(position + 30 + nameLength, position + 30 + nameLength + size);
    assert.equal(view.getUint16(position + 8, true), 0);
    const crc = view.getUint32(position + 14, true);
    assert.equal(await blobCRC32(new Blob([data])), crc);
    files.push({offset: position, name, bytes: data, crc}); position += 30 + nameLength + size;
  }
  const centralOffset = position;
  assert.deepEqual(files.map(file => file.name), ['telemetry.json', 'recording.webm']);
  assert.deepEqual(JSON.parse(decoder.decode(files[0]!.bytes)), expected);
  assert.deepEqual(files[1]!.bytes, new Uint8Array(await video.arrayBuffer()));
  for (const file of files) {
    assert.equal(view.getUint32(position, true), 0x02014b50);
    assert.equal(view.getUint32(position + 42, true), file.offset);
    assert.equal(view.getUint32(position + 16, true), file.crc);
    assert.equal(view.getUint32(position + 20, true), file.bytes.byteLength);
    position += 46 + view.getUint16(position + 28, true);
  }
  assert.equal(view.getUint32(position, true), 0x06054b50);
  assert.equal(view.getUint16(position + 10, true), 2);
  assert.equal(view.getUint32(position + 16, true), centralOffset);
  assert.equal(position + 22, bytes.byteLength);
});

test('telemetry-only ZIP works and recording path injection is rejected', async () => {
  const zip = await createRunArchive({partial: true});
  assert.ok(zip.size > 22);
  await assert.rejects(createRunArchive({}, {blob: new Blob(), filename: '../private.webm'}), /filename/);
  await assert.rejects(createRunArchive(undefined), /serialized/);
});
