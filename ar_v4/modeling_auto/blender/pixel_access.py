"""Bounded exact image-pixel reads for already-validated Blender editing code.

Blender's RNA array indexing copies the entire image even for a four-float
slice. Cache a compact float32 copy once, without changing the saved script or
resampling its images. The transformed code is an execution detail; callers
must validate the original source before compiling it and guard dynamic names.
"""
from __future__ import annotations

import ast
import builtins
from array import array
from collections import OrderedDict


_LOAD = "__modeling_auto_pixel_load"
_OWNER = "__modeling_auto_pixel_owner"
_HELPERS = frozenset({_LOAD, _OWNER})
PIXEL_ACCESS_VERSION = "float32-rna-cache-v1"


class _PixelTransform(ast.NodeTransformer):
    def visit_Attribute(self, node):
        owner = self.visit(node.value)
        if isinstance(node.ctx, ast.Load):
            result = ast.Call(ast.Name(_LOAD, ast.Load()),
                              [owner, ast.Constant(node.attr)], [])
        else:
            result = ast.Attribute(
                ast.Call(ast.Name(_OWNER, ast.Load()), [owner], []),
                node.attr, node.ctx)
        return ast.copy_location(result, node)


class PixelAccess:
    def __init__(self, image_type, max_cache_bytes=2 * 1024 ** 3):
        if not isinstance(max_cache_bytes, int) or max_cache_bytes < 0:
            raise ValueError("Pixel cache size must be a nonnegative integer")
        self.image_type = image_type
        self.max_cache_bytes = max_cache_bytes
        self._cache = OrderedDict()
        self._bytes = 0
        self._counts = dict(bulk_copies=0, bytes_copied=0, cache_hits=0,
                            uncached_reads=0, invalidations=0, evictions=0)

    @property
    def bindings(self):
        return {_LOAD: self.get_attribute, _OWNER: self._owner}

    def compile(self, script, filename="<astra>"):
        tree = ast.parse(script, filename=filename, mode="exec")
        # Also cover string-valued binding sites (import/except aliases). Raw
        # source validation rejects private names; this is a second safeguard.
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.arg):
                names.append(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef, ast.ExceptHandler)):
                names.append(node.name)
            elif isinstance(node, ast.alias):
                names.append(node.asname or node.name.split(".")[0])
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
                names.append(node.name)
            elif isinstance(node, ast.MatchMapping):
                names.append(node.rest)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                names.extend(node.names)
            if any(name in _HELPERS for name in names):
                raise ValueError("Pixel execution helper names are reserved")
        tree = ast.fix_missing_locations(_PixelTransform().visit(tree))
        return builtins.compile(tree, filename, "exec")

    def stats(self):
        return dict(self._counts, version=PIXEL_ACCESS_VERSION, cache_bytes=self._bytes,
                    cache_entries=len(self._cache),
                    max_cache_bytes=self.max_cache_bytes)

    def close(self):
        self._cache.clear()
        self._bytes = 0

    def _key(self, image):
        return image.as_pointer()

    def _invalidate(self, image):
        # Capture validity before a removal/mutation and tolerate removed IDs.
        try:
            key = self._key(image)
        except (ReferenceError, RuntimeError):
            return
        self._discard(key)

    def _discard(self, key):
        entry = self._cache.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry[1]) * entry[1].itemsize
            self._counts["invalidations"] += 1

    def _state(self, image):
        # These small nested properties can change how image pixels are
        # produced without calling an Image method or its direct setter.
        source = getattr(image, "source", None)
        colorspace = getattr(image, "colorspace_settings", None)
        state = (source, getattr(colorspace, "name", None),
                 getattr(image, "alpha_mode", None))
        if source == "GENERATED":
            state += (tuple(getattr(image, "generated_color", ())),
                      getattr(image, "generated_type", None),
                      getattr(image, "generated_width", None),
                      getattr(image, "generated_height", None),
                      getattr(image, "use_generated_float", None))
        if source == "TILED":
            tiles = getattr(image, "tiles", None)
            state += (getattr(tiles, "active_index", None),)
        return state

    def _same_image(self, previous, image, length):
        if previous is image:
            return True
        try:
            # Blender can return another Python wrapper for the same RNA ID.
            # Validate the retained wrapper before equality so a removed ID's
            # reused address cannot accidentally reuse its old pixels.
            return len(previous.pixels) == length and previous == image
        except (ReferenceError, RuntimeError):
            return False

    def _buffer(self, image):
        # Do not serve a cached buffer after removal or a size change. Reading
        # RNA length is cheap; only indexing/slicing copies its entire array.
        raw = image.pixels
        length = len(raw)
        key = self._key(image)
        state = self._state(image)
        entry = self._cache.get(key)
        if entry is not None:
            if (len(entry[1]) == length and entry[2] == state
                    and self._same_image(entry[0], image, length)):
                self._cache.move_to_end(key)
                self._counts["cache_hits"] += 1
                return entry[1]
            self._discard(key)
        size = length * array("f").itemsize
        if size > self.max_cache_bytes or not self.max_cache_bytes:
            self._counts["uncached_reads"] += 1
            return None
        # Evict before allocating, so retained snapshots stay within the bound.
        while self._cache and self._bytes + size > self.max_cache_bytes:
            _, evicted = self._cache.popitem(last=False)
            self._bytes -= len(evicted[1]) * evicted[1].itemsize
            self._counts["evictions"] += 1
        values = array("f", [0.0]) * length
        raw.foreach_get(values)
        self._cache[key] = (image, values, state)
        self._bytes += size
        self._counts["bulk_copies"] += 1
        self._counts["bytes_copied"] += size
        return values

    def get_attribute(self, obj, name, *default):
        value = builtins.getattr(obj, name, *default)
        if not isinstance(obj, self.image_type):
            return value
        if name == "pixels":
            return _PixelView(self, obj)
        if callable(value) and name != "as_pointer":
            # Aliased image methods remain guarded when called later. Even a
            # same-size update can change pixels, so size alone is insufficient.
            def image_call(*args, **kwargs):
                self._invalidate(obj)
                try:
                    return value(*args, **kwargs)
                finally:
                    self._invalidate(obj)
            return image_call
        return value

    def set_attribute(self, obj, name, value):
        if not isinstance(obj, self.image_type):
            return builtins.setattr(obj, name, value)
        self._invalidate(obj)
        try:
            return builtins.setattr(obj, name, value)
        finally:
            self._invalidate(obj)

    def _owner(self, obj):
        return _ImageOwner(self, obj) if isinstance(obj, self.image_type) else obj


class _ImageOwner:
    __slots__ = ("_access", "_image")

    def __init__(self, access, image):
        object.__setattr__(self, "_access", access)
        object.__setattr__(self, "_image", image)

    def __getattr__(self, name):
        return self._access.get_attribute(self._image, name)

    def __setattr__(self, name, value):
        return self._access.set_attribute(self._image, name, value)

    def __delattr__(self, name):
        self._access._invalidate(self._image)
        try:
            builtins.delattr(self._image, name)
        finally:
            self._access._invalidate(self._image)


class _PixelView:
    __slots__ = ("_access", "_image")

    def __init__(self, access, image):
        self._access = access
        self._image = image

    def __len__(self):
        return len(self._image.pixels)

    def __getitem__(self, key):
        if not isinstance(key, (int, slice)):
            return self._image.pixels[key]
        values = self._access._buffer(self._image)
        if values is None:
            return self._image.pixels[key]
        result = values[key]
        return tuple(result) if isinstance(key, slice) else result

    def __iter__(self):
        values = self._access._buffer(self._image)
        return iter(self._image.pixels if values is None else values)

    def __setitem__(self, key, value):
        self._access._invalidate(self._image)
        try:
            self._image.pixels[key] = value
        finally:
            self._access._invalidate(self._image)

    def foreach_get(self, values):
        return self._image.pixels.foreach_get(values)

    def foreach_set(self, values):
        self._access._invalidate(self._image)
        try:
            return self._image.pixels.foreach_set(values)
        finally:
            self._access._invalidate(self._image)

    def __getattr__(self, name):
        # Preserve RNA's other attributes; unfamiliar methods conservatively
        # invalidate because they may mutate through an aliased pixel handle.
        value = builtins.getattr(self._image.pixels, name)
        if callable(value):
            def pixel_call(*args, **kwargs):
                self._access._invalidate(self._image)
                try:
                    return value(*args, **kwargs)
                finally:
                    self._access._invalidate(self._image)
            return pixel_call
        return value
