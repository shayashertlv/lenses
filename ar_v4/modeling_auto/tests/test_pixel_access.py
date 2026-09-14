from array import array
from types import SimpleNamespace

import pytest

from app.script_validation import validate_script
from blender.pixel_access import PixelAccess


class Pixels:
    def __init__(self, values):
        self.values = array("f", values)
        self.copies = 0

    def __len__(self):
        return len(self.values)

    def __getitem__(self, key):
        value = self.values[key]
        return tuple(value) if isinstance(key, slice) else value

    def __setitem__(self, key, value):
        self.values[key] = array("f", value) if isinstance(key, slice) else value

    def foreach_get(self, target):
        if len(target) != len(self.values):
            raise ValueError("wrong length")
        self.copies += 1
        for i, value in enumerate(self.values):
            target[i] = value

    def foreach_set(self, values):
        if len(values) != len(self.values):
            raise ValueError("wrong length")
        self.values[:] = array("f", values)


class Image:
    def __init__(self, values):
        self._pixels = Pixels(values)
        self.removed = False
        self.label = "image"

    @property
    def pixels(self):
        if self.removed:
            raise ReferenceError("Image was removed")
        return self._pixels

    @pixels.setter
    def pixels(self, values):
        self._pixels.foreach_set(values)

    def as_pointer(self):
        return id(self)

    def scale(self, count):
        self._pixels = Pixels(range(count))

    def paint(self, value):
        self._pixels.values[0] = value

    def paint_then_fail(self, value):
        self.paint(value)
        raise ValueError("after mutation")


def run(script, access, **scope):
    validate_script(script)
    scope.update(access.bindings)
    scope.update(getattr=access.get_attribute, setattr=access.set_attribute)
    exec(access.compile(script), scope)
    return scope


def test_repeated_small_reads_make_one_exact_compact_copy():
    image = Image([i / 257 for i in range(1024)])
    access = PixelAccess(Image)
    original = image.pixels
    result = run("pixels = image.pixels\nvalues = [pixels[4:8] for i in range(1000)]",
                 access, image=image)
    assert result["values"] == [original[4:8]] * 1000
    assert original.copies == 1
    assert access.stats()["bulk_copies"] == 1
    assert access.stats()["cache_hits"] == 999
    assert access.stats()["cache_bytes"] == 4096


@pytest.mark.parametrize("key", [0, -1, True, slice(None), slice(-7, -1, 2),
                                 slice(None, None, -1), slice(4, 2), slice(20)])
def test_native_value_and_slice_shapes(key):
    image = Image([0.01 * i for i in range(12)])
    view = PixelAccess(Image).get_attribute(image, "pixels")
    assert view[key] == image.pixels[key]
    assert type(view[key]) is type(image.pixels[key])
    assert list(view) == list(image.pixels.values)
    assert len(view) == len(image.pixels)


def test_index_errors_are_retained():
    view = PixelAccess(Image).get_attribute(Image([1, 2]), "pixels")
    with pytest.raises(IndexError):
        view[8]
    with pytest.raises(ValueError):
        view[::0]
    with pytest.raises(TypeError):
        view["no"]


def test_pixel_alias_writes_and_foreach_set_invalidate_other_views():
    image = Image([0, 1, 2, 3])
    access = PixelAccess(Image)
    result = run("""
first = image.pixels
second = image.pixels
before = first[:]
second[0] = 8
after_item = first[:]
second[1:3] = [9, 10]
after_slice = first[:]
write = second.foreach_set
write([11, 12, 13, 14])
after_bulk = first[:]
copy = [0.0] * 4
first.foreach_get(copy)
""", access, image=image)
    assert result["before"] == (0, 1, 2, 3)
    assert result["after_item"] == (8, 1, 2, 3)
    assert result["after_slice"] == (8, 9, 10, 3)
    assert result["after_bulk"] == (11, 12, 13, 14)
    assert result["copy"] == [11, 12, 13, 14]
    assert access.stats()["bulk_copies"] == 4


def test_aliased_image_calls_same_size_updates_and_resize_invalidate():
    image = Image([1, 2, 3, 4])
    access = PixelAccess(Image)
    result = run("""
p = image.pixels
initial = p[:]
paint = image.paint
paint(9)
updated = p[:]
resize = image.scale
resize(6)
resized = p[:]
try:
    image.paint_then_fail(7)
except ValueError:
    failed = p[:]
""", access, image=image)
    assert result["updated"] == (9, 2, 3, 4)
    assert result["resized"] == tuple(range(6))
    assert result["failed"] == (7, 1, 2, 3, 4, 5)


def test_direct_dynamic_and_augmented_attribute_writes():
    image = Image([1, 2])
    access = PixelAccess(Image)
    result = run("""
p = getattr(image, 'pixels')
initial = p[:]
image.pixels = [3, 4]
direct = p[:]
setattr(image, 'pixels', [5, 6])
dynamic = p[:]
image.label += ' updated'
del image.label
missing = getattr(image, 'label', 'missing')
""", access, image=image)
    assert result["direct"] == (3, 4)
    assert result["dynamic"] == (5, 6)
    assert result["missing"] == "missing"


def test_removed_image_never_serves_cached_values():
    image = Image([1, 2])
    access = PixelAccess(Image)
    pixels = access.get_attribute(image, "pixels")
    assert pixels[:] == (1, 2)
    image.removed = True
    with pytest.raises(ReferenceError):
        pixels[:]
    with pytest.raises(ReferenceError):
        len(pixels)


def test_lru_is_byte_bounded_and_retains_most_recent_image():
    images = [Image([i] * 4) for i in range(3)]
    access = PixelAccess(Image, max_cache_bytes=32)
    views = [access.get_attribute(image, "pixels") for image in images]
    assert views[0][0] == 0
    assert views[1][0] == 1
    assert views[0][0] == 0
    assert views[2][0] == 2
    assert access.stats()["cache_bytes"] == 32
    assert views[0][0] == 0
    assert images[0].pixels.copies == 1
    assert views[1][0] == 1
    assert images[1].pixels.copies == 2
    assert access.stats()["evictions"] == 2


@pytest.mark.parametrize("budget", [0, 4])
def test_images_larger_than_budget_keep_native_behavior(budget):
    image = Image([1, 2, 3, 4])
    access = PixelAccess(Image, max_cache_bytes=budget)
    pixels = access.get_attribute(image, "pixels")
    assert pixels[1:3] == (2, 3)
    assert access.stats()["cache_bytes"] == 0
    assert image.pixels.copies == 0


def test_non_image_attributes_and_pixels_are_unchanged():
    other = SimpleNamespace(pixels=[1, 2], label="other")
    access = PixelAccess(Image)
    result = run("""
p = other.pixels
p.append(3)
other.label += ' changed'
values = p[:]
del other.label
""", access, other=other)
    assert result["p"] is other.pixels
    assert result["values"] == [1, 2, 3]
    assert not hasattr(other, "label")
    assert access.stats()["bulk_copies"] == 0


@pytest.mark.parametrize("source", [
    "__modeling_auto_pixel_load = 3",
    "def f(__modeling_auto_pixel_owner):\n    pass",
    "import math as __modeling_auto_pixel_load",
    "try:\n    pass\nexcept Exception as __modeling_auto_pixel_owner:\n    pass",
    "match 1:\n    case __modeling_auto_pixel_load:\n        pass",
    "match [1]:\n    case [*__modeling_auto_pixel_owner]:\n        pass",
    "match {}:\n    case {**__modeling_auto_pixel_owner}:\n        pass",
])
def test_source_cannot_shadow_private_execution_helpers(source):
    with pytest.raises(ValueError, match="reserved"):
        PixelAccess(Image).compile(source)


def test_raw_validator_rejects_access_to_execution_helpers():
    with pytest.raises(ValueError, match="unavailable"):
        validate_script("__modeling_auto_pixel_load(image, 'pixels')")


def test_identity_queries_reuse_snapshot_and_close_releases_cache():
    image = Image([1, 2, 3, 4])
    access = PixelAccess(Image)
    result = run("""
p = image.pixels
before = p[:]
identity = image.as_pointer()
after = p[:]
""", access, image=image)
    assert result["identity"] == id(image)
    assert result["before"] == result["after"]
    assert access.stats()["bulk_copies"] == 1
    access.close()
    assert access.stats()["cache_bytes"] == 0
    assert access.stats()["cache_entries"] == 0
    assert access.stats()["bulk_copies"] == 1


def test_nested_image_settings_invalidate_without_direct_image_setter():
    class DerivedImage(Image):
        def __init__(self):
            super().__init__([0, 0, 0, 1])
            self.source = "GENERATED"
            self.colorspace_settings = SimpleNamespace(name="sRGB")
            self.generated_color = [0, 0, 0, 1]
            self.tiles = SimpleNamespace(active_index=0)

        @property
        def pixels(self):
            value = list(self.generated_color)
            value[1] = 1 if self.colorspace_settings.name == "Raw" else 0
            value[2] = self.tiles.active_index
            self._pixels.values[:] = array("f", value)
            return self._pixels

    image = DerivedImage()
    access = PixelAccess(Image)
    result = run("""
p = image.pixels
initial = p[:]
image.generated_color[0] = 0.5
generated = p[:]
image.colorspace_settings.name = 'Raw'
color = p[:]
image.source = 'TILED'
image.tiles.active_index = 2
tiled = p[:]
image.tiles.active_index = 3
tiled_again = p[:]
""", access, image=image)
    assert result["generated"] == (0.5, 0, 0, 1)
    assert result["color"] == (0.5, 1, 0, 1)
    assert result["tiled"] == (0.5, 1, 2, 1)
    assert result["tiled_again"] == (0.5, 1, 3, 1)
    assert access.stats()["bulk_copies"] == 5


def test_distinct_valid_rna_wrappers_share_one_cache():
    class Wrapper(Image):
        def __init__(self, owner):
            self.owner = owner

        @property
        def pixels(self):
            return self.owner.pixels

        def as_pointer(self):
            return self.owner.as_pointer()

        def __eq__(self, other):
            return isinstance(other, Wrapper) and self.owner is other.owner

    owner = Image([1, 2, 3, 4])
    access = PixelAccess(Image)
    one = access.get_attribute(Wrapper(owner), "pixels")
    two = access.get_attribute(Wrapper(owner), "pixels")
    assert one[:] == two[:]
    assert owner.pixels.copies == 1
    assert access.stats()["cache_entries"] == 1


def test_stats_include_adapter_version():
    assert PixelAccess(Image).stats()["version"] == "float32-rna-cache-v1"
