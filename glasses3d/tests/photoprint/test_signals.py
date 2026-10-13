"""Photo pixel masks (WP5_DESIGN §2): the acetate rule and the valid (eroded matte and acetate) mask."""
import numpy as np

from glasses3d.g3d_host.photoprint import signals as S


def test_acetate_mask_rule():
    px = np.array([[[205, 128, 52],      # amber: lum 128, sat 153 -> acetate
                    [58, 34, 22],        # dark brown: lum 38 -> acetate
                    [150, 150, 150],     # grey metal: not
                    [238, 238, 238],     # background: not
                    [200, 180, 150],     # bright, lum 177: not
                    [100, 95, 90]]], float)   # lum 95, sat 10: not
    assert S.acetate_mask(px).tolist() == [[True, True, False, False, False, False]]


def test_valid_mask_is_eroded_matte_and_acetate():
    img = np.full((30, 30, 3), 40.0)
    matte = np.zeros((30, 30), bool)
    matte[5:25, 5:25] = True
    v = S.valid_mask(img, matte)
    assert v.sum() == 18 * 18 and not v[5].any() and v[6, 6]
