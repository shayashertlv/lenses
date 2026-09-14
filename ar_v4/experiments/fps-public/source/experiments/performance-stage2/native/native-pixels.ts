/** Native bytes are authoritative; a 2D canvas conversion is unnecessary. */
export class NativePixelReader {
  private bottomUp = new Uint8Array(0);

  read(native: HTMLCanvasElement): ImageData {
    const {width, height} = native;
    const gl = native.getContext('webgl2');
    if (!gl || gl.isContextLost()) throw new Error('The native comparison framebuffer is unavailable.');
    if (this.bottomUp.length !== width * height * 4) this.bottomUp = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, this.bottomUp);
    // WebGL reports most read failures through error state, without throwing or
    // overwriting the destination. Never turn old scratch into a new owned image.
    const error = gl.getError(), contextLost = gl.isContextLost();
    if (error !== gl.NO_ERROR || contextLost)
      throw new Error(`The native comparison readback failed (GL 0x${error.toString(16)}, context lost: ${contextLost}).`);
    const topDown = new Uint8ClampedArray(this.bottomUp.length);
    for (let y = 0; y < height; y++) {
      topDown.set(this.bottomUp.subarray((height - y - 1) * width * 4, (height - y) * width * 4), y * width * 4);
    }
    // The reusable readback scratch never escapes. Previous/held outputs own bytes.
    return new ImageData(topDown, width, height);
  }

  dispose(): void { this.bottomUp = new Uint8Array(0); }
}
