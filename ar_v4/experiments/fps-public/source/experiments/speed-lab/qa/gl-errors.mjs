/** Diagnostic-only: preserve the observable getError queue while attributing GL errors. */
export function observeGlErrors() {
  const prototype = WebGL2RenderingContext.prototype, getError = prototype.getError;
  const states = new WeakMap(), objects = new WeakMap(); let nextContext = 1, nextObject = 1;
  const rows = []; window.stage2GlErrors = rows;
  const state = gl => {
    if (!states.has(gl)) states.set(gl, {id: nextContext++, pending: []});
    return states.get(gl);
  };
  const scalar = value => {
    if (value === null || typeof value !== 'object') return value;
    if (ArrayBuffer.isView(value)) return {type: value.constructor.name, length: value.length};
    if (!objects.has(value)) objects.set(value, nextObject++);
    return {type: value.constructor.name, id: objects.get(value)};
  };
  const drain = gl => {
    const errors = [];
    for (let attempt = 0; attempt < 16; attempt++) {
      const code = getError.call(gl); if (code === gl.NO_ERROR) break;
      errors.push(`0x${code.toString(16)}`); state(gl).pending.push(code);
    }
    return errors;
  };
  prototype.getError = function () {return state(this).pending.shift() ?? getError.call(this);};
  for (const method of ['texStorage2D', 'framebufferTexture2D', 'bindFramebuffer', 'blitFramebuffer', 'copyTexSubImage2D', 'readPixels']) {
    const original = prototype[method];
    prototype[method] = function (...args) {
      const before = drain(this), result = original.apply(this, args), after = drain(this);
      if (before.length || after.length) rows.push({context: state(this).id, method, args: args.map(scalar), before, after,
        readFramebuffer: scalar(this.getParameter(this.READ_FRAMEBUFFER_BINDING)), drawFramebuffer: scalar(this.getParameter(this.DRAW_FRAMEBUFFER_BINDING)),
        drawingBufferWidth: this.drawingBufferWidth, drawingBufferHeight: this.drawingBufferHeight,
        attributes: this.getContextAttributes(), atMs: performance.now()});
      return result;
    };
  }
}
