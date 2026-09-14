import pytest

from app.script_validation import safe_builtins, validate_script
from blender.pixel_access import PixelAccess


class SyntheticImage:
    pass


def execute(script):
    validate_script(script)
    pixels = PixelAccess(SyntheticImage)
    scope = {'__builtins__': safe_builtins(attribute_getter=pixels.get_attribute,
                                         attribute_setter=pixels.set_attribute)}
    scope.update(pixels.bindings)
    try:
        exec(pixels.compile(script), scope, scope)
    finally:
        pixels.close()
    return scope


def test_generated_lens_occupancy_masks_execute_in_actual_edit_namespace():
    result = execute('''
ys = [0.0, None, 2.0, None, 3.0, 4.0]
occupied = bytearray(1 if y is not None else 0 for y in ys)
seen = bytearray(len(occupied))
stack = [0]
component = []
while stack:
    i = stack.pop()
    if seen[i] or not occupied[i]:
        continue
    seen[i] = 1
    component.append(i)
    for neighbor in (i-1, i+1):
        if 0 <= neighbor < len(occupied) and not seen[neighbor]:
            stack.append(neighbor)
holes = bytearray(1 if not value else 0 for value in occupied)
copy = bytearray(occupied)
copy[1:3] = bytes([1, 1])
unchanged = list(occupied)
''')
    assert result['unchanged'] == [1, 0, 1, 0, 1, 1]
    assert list(result['seen']) == [1, 0, 0, 0, 0, 0]
    assert result['component'] == [0]
    assert list(result['holes']) == [0, 1, 0, 1, 0, 0]
    assert list(result['copy']) == [1, 1, 1, 0, 1, 1]


@pytest.mark.parametrize('script', ['mask = bytearray(-1)', 'mask = bytearray([256])'])
def test_bytearray_keeps_standard_value_checks(script):
    with pytest.raises(ValueError):
        execute(script)


@pytest.mark.parametrize('script', [
    'bytearray.from_address(1)', 'bytearray.from_pointer(1)',
    'bytearray.__new__(bytearray)', "getattr(bytearray, '__class__')",
    "open('file', 'wb')", 'memoryview(bytearray(4))',
])
def test_mutable_masks_do_not_expand_external_or_private_capabilities(script):
    with pytest.raises(ValueError):
        validate_script(script)
