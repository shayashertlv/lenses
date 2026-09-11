import assert from 'node:assert/strict';
import {test} from 'node:test';
import {requireSharedCamera} from '../background.ts';

test('shared camera consumption validates the exact original sizing contract and rejects a missing current pair', () => {
  const image = {width: 3, height: 2, data: new Uint8ClampedArray(24)} as ImageData;
  assert.equal(requireSharedCamera(image, 3, 2, 3, 2, null).pixels, image.data);
  assert.throws(() => requireSharedCamera(null, 3, 2, 3, 2, 'native read failed'), /native read failed/);
  assert.throws(() => requireSharedCamera(image, 3, 1, 3, 2, null), /viewport/);
  assert.throws(() => requireSharedCamera({...image, height: 3} as ImageData, 3, 2, 3, 2, null), /current pair/);
});
