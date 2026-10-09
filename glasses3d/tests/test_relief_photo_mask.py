"""Independent colored lens / lettering / floor / crop controls."""
import numpy as np
import pytest
from glasses3d.g3d_host.relief_photo_mask import dark_studio_mask


@pytest.mark.parametrize('threshold',[45.,60.,75.])
def test_cropped_colored_lens_lettering_and_neutral_floor(threshold):
    im=np.full((160,200,3),255,np.uint8)
    im[20:95,:140]=30              # dark product touches left viewport
    im[35:70,:30]=[220,205,242]    # bright colored lens also touches it
    im[40:43,65:68]=240           # small bright lettering, not a through-hole
    im[110:140,20:160]=210        # detached light neutral floor reflection
    mask=dark_studio_mask(im,threshold_L=threshold)
    assert mask[50,0] and mask[50,20] and mask[41,66]
    assert mask[22,0] and not mask[5,0]
    assert not mask[125,80] and not mask[100,170]


def test_large_background_gap_stays_open():
    im=np.full((160,200,3),255,np.uint8);im[20:140,20:180]=20
    im[40:120,50:150]=255
    mask=dark_studio_mask(im,threshold_L=60)
    assert mask[25,25] and not mask[70,90]


def test_grayscale_cut_edge_is_preserved_without_morphological_border_loss():
    im=np.full((80,100,3),255,np.uint8);im[:40,:50]=25
    mask=dark_studio_mask(im,threshold_L=60)
    assert mask[0,0] and mask[0,49] and not mask[0,50]


def test_float_rgb_is_refused():
    with pytest.raises(ValueError,match='uint8'):
        dark_studio_mask(np.ones((10,10,3)),threshold_L=60)
