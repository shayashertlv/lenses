"""Best-Source Assembly: product photos -> AR-ready glasses GLB.

Each part comes from the source that measures it best: the front outline, lens
outlines and lens/frame boundary from the photos; depth, lens curvature, temples
and wall texture from one cached multiview generation; lens appearance from photo
photometry. Parts are assembled by construction and every candidate is scored
against the photos. See DESIGN.md.
"""
