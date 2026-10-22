"""Owner version chips keep radial rings distinguishable from linear looks."""
from glasses3d.dev_ui.owner_lists import lens_swatch


def test_radial_swatch_keeps_centre_roundness_and_ring_order():
    gradient = {"direction": "around_point", "centre": [.25, -.5], "roundness": 2,
                "blend": .5, "colours": [{"colour": [0, 1, 0], "share": 40},
                                          {"colour": [1, 0, 1], "share": 60}]}
    row = {"material_index": 1, "role": "lens", "values": {"gradient": gradient}}
    swatch = lens_swatch([row])
    assert swatch["direction"] == "around_point"
    assert swatch["colors"][0] == "#00ff00"
    assert swatch["colors"][-1] == "#ff00ff"
    assert swatch["css"].startswith("radial-gradient(ellipse 20px 10px at 62.5% 75%, ")
    gradient.update(centre=[0, 0], roundness=1)
    assert lens_swatch([row])["css"].startswith("radial-gradient(ellipse 10px 10px at 50% 50%, ")
