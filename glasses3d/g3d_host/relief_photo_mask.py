"""Explicit dark-product/white-studio diagnostic matte, including cropped photos.

This is not a replacement for WP6's frozen glossy-floor matte. Bright colored
lenses can touch the viewport; small interior bright marks (logos/highlights)
are not silhouette holes. Large background gaps remain open. Callers must keep
threshold sensitivities and inspect the resulting mask against the photograph.
"""
import cv2
import numpy as np
from scipy import ndimage


def dark_studio_mask(rgb, *, threshold_L, chroma_min=8., small_hole_fraction=.002):
    rgb=np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError('Expected uint8 RGB image')
    if not (0 < threshold_L < 100 and 0 < chroma_min < 150 and 0 <= small_hole_fraction < 1):
        raise ValueError('Invalid matte thresholds')
    lab=cv2.cvtColor(rgb.astype(np.float32)/255.,cv2.COLOR_RGB2LAB)
    chroma=np.hypot(lab[...,1],lab[...,2])
    mask=(lab[...,0] < threshold_L) | (chroma > chroma_min)
    holes=ndimage.binary_fill_holes(mask)&~mask
    labels,count=ndimage.label(holes)
    if count:
        sizes=np.bincount(labels.ravel())
        small=np.flatnonzero((sizes <= small_hole_fraction*mask.size)&(np.arange(len(sizes))!=0))
        mask |= np.isin(labels,small)
    return mask
