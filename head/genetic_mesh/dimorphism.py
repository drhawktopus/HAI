"""Stage 1 of the genetic-mesh subsystem: GENDER dimorphism on a (toddler) FLAME mesh.

The round-7 boy problem was never a prompt problem — the mesh is a gender-neutral toddler skull, so
gender had nowhere to live but the (bearded, adult) face-paint layer, and every prompt/IPAdapter lever
failed. The fix is here: nudge the actual bone geometry so boys read boyish from STRUCTURE.

Pure numpy over vertex arrays — no FLAME loading, no orchestrator, no ComfyUI. FLAME frame: +y up,
+z out of the face, x = left↔right width. Toddler dimorphism is SUBTLE (kids are far less dimorphic
than adults), so factors are small and dialed by `strength`; this is a first pass to be tuned against
renders, not final anthropometry.
"""
import numpy as np

# Per-feature dimorphism factors at strength=1.0, sign convention: boy = +, girl = −.
# Small on purpose (toddlers). Well-established dimorphic features only, for now.
_JAW_WIDTH = 0.05      # x-spread of the jaw oval: boys wider/squarer, girls narrower/softer
_BROW_PROJ = 0.06      # z-projection of the brow/forehead: boys heavier ridge, girls flatter
_CHIN_PROJ = 0.04      # z-projection of the lowest jaw (chin): boys more projected


def apply_gender_dimorphism(verts, regions, gender, strength=1.0):
    """Return a copy of `verts` nudged toward masculine ('boy') or feminine ('girl') bone structure.

    verts:    (N,3) float array, FLAME frame (+y up, +z out, x = width).
    regions:  dict of vertex-index arrays; uses 'jaw_oval' (required) and 'brows' (optional, the
              forehead/brow-ridge region).
    gender:   'boy' | 'girl' | anything else → no-op (returned unchanged).
    strength: overall dial (0 = off). Keep ≤ ~1.5; toddler dimorphism is subtle.
    """
    out = np.asarray(verts, dtype=float).copy()
    if gender not in ("boy", "girl") or strength == 0:
        return out
    s = strength * (1.0 if gender == "boy" else -1.0)

    jaw = np.asarray(regions["jaw_oval"], dtype=int)
    if len(jaw):
        cj = out[jaw].mean(axis=0)
        # widen/narrow the jaw oval about its centroid on the x (width) axis
        out[jaw, 0] = cj[0] + (out[jaw, 0] - cj[0]) * (1.0 + _JAW_WIDTH * s)
        # chin projection: the lowest ~15% of jaw verts by y, shifted out (+z for boys) by a
        # fraction of the jaw's depth
        y = out[jaw, 1]
        chin = jaw[y <= np.quantile(y, 0.15)]
        if len(chin):
            zspan = float(out[jaw, 2].max() - out[jaw, 2].min())
            out[chin, 2] += _CHIN_PROJ * s * zspan

    brows = np.asarray(regions.get("brows", []), dtype=int)
    if len(brows):
        cb = out[brows].mean(axis=0)
        # brow ridge projection about the brow centroid on z (out of face)
        out[brows, 2] = cb[2] + (out[brows, 2] - cb[2]) * (1.0 + _BROW_PROJ * s)

    return out
