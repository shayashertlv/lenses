"""Opt-in cropped-photo silhouette diagnostics; not used by the frozen WP6 fit.

A photograph's edge is a viewport boundary, not evidence of an object boundary.
The ordinary camera Level deliberately discourages out-of-frame geometry for
uncropped catalog images. This diagnostic instead scores the complete visible
viewport and excludes its cut edges from contour distances. It does not invent
evidence for the part of the object outside that viewport.
"""
import numpy as np

from . import cameras as C, raster


class CroppedPhotoLevel(C.Level):
    def __init__(self, fg, target_width, name='', margin=C.ROI_MARGIN, include_xyxy=None):
        height, width = fg.shape
        # Do not let a moving model or ROI silently hide any observed pixels.
        super().__init__(fg, target_width, name, margin, (0, 0, width, height))
        us, vs = raster.pixel_grid(self.shape, self.stride, self.roi)
        self.contour_valid = ((vs[:, None] >= self.stride) & (vs[:, None] < height-self.stride)
                              & (us[None, :] >= self.stride) & (us[None, :] < width-self.stride))
        self.edge = self.object_edge(self.ref >= .5)
        self.dist = C._dist_to(self.edge)

    def object_edge(self, mask):
        return C._edge(mask) & self.contour_valid

    def outside_share(self, scene, cam):
        # Cropping is explicit opt-in; no such override exists in production Level.
        return 0.

    def score(self, mask, optional=None):
        ref = self.ref
        if optional is None:
            inter = float(ref[mask].sum())
            union = float(mask.sum()) + self.ref_sum - inter
        else:
            hard = mask & ~optional
            inter = float(ref[mask].sum())
            union = float(hard.sum()) + float(ref[~hard].sum())
        if not mask.any() or union <= 0:
            return C._BAD, 0., 1.
        iou = inter / union
        edge = self.object_edge(self.effective(mask, optional))
        a = float(np.minimum(self.dist[edge], self.trunc).mean()) if edge.any() else self.trunc
        b = float(np.minimum(C._dist_to(edge)[self.edge], self.trunc).mean()) if self.edge.any() else self.trunc
        boundary = .5*(a+b)/self.width
        return 1.-iou+C.BOUNDARY_WEIGHT*boundary, iou, boundary
