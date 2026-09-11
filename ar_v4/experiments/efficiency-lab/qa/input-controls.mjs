/** Browser-only exact canvas/hash comparisons. No camera or worker replacement. */
import {InputCanvasPool, inputContext, hashInput} from '../input-resources.ts';
export async function inputControls() {
  const pooled = new InputCanvasPool(true), fresh = new InputCanvasPool(false), results = [];
  const equal = (left, right) => left.length === right.length && left.every((value, index) => value === right[index]);
  const source = document.createElement('canvas'); source.width = 97; source.height = 61;
  const context = source.getContext('2d'), image = context.createImageData(source.width, source.height);
  for (let index = 0; index < image.data.length; index += 4) {
    image.data[index] = index % 251; image.data[index + 1] = index % 197; image.data[index + 2] = index % 127;
    image.data[index + 3] = index % 3 ? 127 : 0;
  }
  context.putImageData(image, 0, 0);
  try {
    // Same-size reuse and resized reuse must retain a fresh opaque-black backdrop
    // and browser downsampling of the exact same transparent source.
    for (const [width, height] of [[97,61],[97,61],[47,29],[47,29],[101,67],[97,61]]) {
      const old = pooled.acquire(width, height), first = fresh.acquire(width, height);
      for (const canvas of [old, first]) inputContext(canvas).drawImage(source, 0, 0, width, height);
      const a = inputContext(old).getImageData(0,0,width,height), b = inputContext(first).getImageData(0,0,width,height);
      const [leanHash, gHash, leanBitmap, gBitmap] = await Promise.all([
        hashInput(a.data,true), hashInput(b.data,false), createImageBitmap(old), createImageBitmap(first)]);
      const decode = bitmap => {
        const canvas = document.createElement('canvas'); canvas.width=width; canvas.height=height;
        inputContext(canvas).drawImage(bitmap,0,0); bitmap.close();
        const result=inputContext(canvas).getImageData(0,0,width,height).data; canvas.width=canvas.height=0; return result;
      };
      const result = {width,height,canvasExact:equal(a.data,b.data),bitmapExact:equal(decode(leanBitmap),decode(gBitmap)),
        hashExact:leanHash.value===gHash.value,sourceBytes:width*height*4,leanExplicitHashCopyBytes:leanHash.explicitCopyBytes,
        gExplicitHashCopyBytes:gHash.explicitCopyBytes};
      results.push(result);
      if (!result.canvasExact||!result.bitmapExact||!result.hashExact||result.leanExplicitHashCopyBytes!==0
        ||result.gExplicitHashCopyBytes!==result.sourceBytes) throw new Error('Fresh/reused exact input pixels or hash differs.');
      // Poison the old pixels before release to ensure next acquire clears them.
      inputContext(old).fillStyle='#ed21af'; inputContext(old).fillRect(0,0,width,height);
      pooled.release(old); fresh.release(first);
    }
    return {passed:true,cases:results,pooled:pooled.stats,fresh:fresh.stats,
      scope:'Procedural transparent inputs, unchanged canvas downsampling, exact source/bitmap/hash bytes. Not face inference or wearer evidence.'};
  } finally {pooled.dispose();fresh.dispose();source.width=source.height=0;}
}
