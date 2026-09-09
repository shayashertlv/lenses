/** Bounded diagnosis only. Adds synchronous pixel reads outside runtime counters.
 * Preserves actual READ binding without touching Three's framebuffer state cache. */
export function installNativeCaptureProbe(NativeGpuFrames) {
  const captures = window.stage3NativeCaptureProbe = [], original = NativeGpuFrames.prototype.capture;
  const identities = new WeakMap(); let nextIdentity = 1;
  const id = object => object === null ? null : identities.get(object) ?? (identities.set(object, nextIdentity), nextIdentity++);
  NativeGpuFrames.prototype.capture = function (variant) {
    const gl = this.gl, width = this.width, height = this.height;
    const state = () => ({read: id(gl.getParameter(gl.READ_FRAMEBUFFER_BINDING)), draw: id(gl.getParameter(gl.DRAW_FRAMEBUFFER_BINDING)),
      program: id(gl.getParameter(gl.CURRENT_PROGRAM)), vao: id(gl.getParameter(gl.VERTEX_ARRAY_BINDING)),
      viewport: Array.from(gl.getParameter(gl.VIEWPORT)), colors: gl.getParameter(gl.COLOR_WRITEMASK),
      depth: gl.getParameter(gl.DEPTH_WRITEMASK), clearColor: Array.from(gl.getParameter(gl.COLOR_CLEAR_VALUE)),
      drawBuffer: gl.getParameter(gl.DRAW_BUFFER0), readBuffer: gl.getParameter(gl.READ_BUFFER),
      capabilities: Object.fromEntries(['BLEND','CULL_FACE','DEPTH_TEST','SCISSOR_TEST','STENCIL_TEST','DITHER','RASTERIZER_DISCARD'].map(name => [name, gl.isEnabled(gl[name])]))});
    const read = framebuffer => {
      const previous = gl.getParameter(gl.READ_FRAMEBUFFER_BINDING), points = [[0,0],[Math.floor(width/2),Math.floor(height/2)],[width-1,height-1]];
      try {
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER, framebuffer);
        return points.map(([x,y]) => {const bytes = new Uint8Array(4); gl.readPixels(x,y,1,1,gl.RGBA,gl.UNSIGNED_BYTE,bytes);
          return {x,y,rgba:Array.from(bytes),error:gl.getError()};});
      } finally {gl.bindFramebuffer(gl.READ_FRAMEBUFFER, previous);}
    };
    const row = {variant,generation:this.generation,width,height,beforeState:state(),defaultBefore:read(null)};
    captures.push(row);
    try {const result = original.call(this, variant);
      row.afterState = state(); row.capturedAfter = read(this.textures.get(variant).framebuffer);
      row.restoredState = state(); return result;
    } catch (error) {row.error=String(error);throw error;}
  };
}
