"""Apply structural nudges to the child FLAME mesh based on inherited targets.

Nudges are multiplicative toward the target values from `inheritance.inherit_structure`,
softened by `gain`, and capped at `cap` to prevent over-correction (the pipeline regularizes large deltas).

Parameters
----------
verts : np.ndarray
    Mesh vertices.
regions : dict
    Mapping of region names to vertex indices (e.g., jaw, cheekbone).
hand : array-like
    The target structural values dealt by inheritance.
base_fp : np.ndarray
    The base child's fingerprint; nudges are computed relative to this state,
    not as absolute positions.
gain : float, optional
    Scaling factor for the nudge strength (default 0.6).
cap : float, optional
    Maximum allowed deviation per step (default 0.18).
"""
import numpy as np


def _idx(regions, k):
    return np.asarray(regions[k], dtype=int)


def apply_structural_hand(verts, regions, hand, base_fp, gain=0.6, cap=0.18):
    """Return a copy of `verts` nudged toward the dealt structural `hand`. `base_fp` is the child mesh's
    OWN measured fingerprint (structure.measure_structure) — nudges close the gap from base -> hand."""
    out = np.asarray(verts, dtype=float).copy()
    face_idx = np.unique(np.concatenate([_idx(regions, k) for k in regions]))
    fy, fx, fz = out[face_idx, 1], out[face_idx, 0], out[face_idx, 2]
    face_h = float(fy.max() - fy.min()) or 1e-9
    face_w = float(fx.max() - fx.min()) or 1e-9
    face_d = float(fz.max() - fz.min()) or 1e-9
    eyes, mouth, jaw_ids = out[_idx(regions, "eyes")], out[_idx(regions, "mouth")], _idx(regions, "jaw_oval")
    eye_y, eye_z, mouth_y = eyes[:, 1].mean(), eyes[:, 2].mean(), mouth[:, 1].mean()
    jaw = out[jaw_ids]

    def ratio(feat):
        if feat not in hand or feat not in base_fp:
            return 1.0
        cur = base_fp[feat]["value"]
        if abs(cur) < 1e-6:
            return 1.0
        r = 1.0 + gain * (hand[feat] / cur - 1.0)
        return float(np.clip(r, 1.0 - cap, 1.0 + cap))

    cjx = jaw[:, 0].mean()
    jy = jaw[:, 1]; jlo, jhi = jy.min(), jy.max()
    # gonial width: scale upper-jaw (near mouth level) x about the jaw centroid
    gon = jaw_ids[np.abs(jy - mouth_y) < 0.15 * face_h]
    out[gon, 0] = cjx + (out[gon, 0] - cjx) * ratio("gonial_width")
    # chin taper: scale the lowest 20% of the jaw x
    chin = jaw_ids[jy < jlo + 0.20 * (jhi - jlo)]
    out[chin, 0] = cjx + (out[chin, 0] - cjx) * ratio("jaw_taper")
    # fwhr: scale cheekbone-band (eye-height) x about its centroid
    band = face_idx[np.abs(out[face_idx, 1] - eye_y) < 0.12 * face_h]
    cbx = out[band, 0].mean()
    out[band, 0] = cbx + (out[band, 0] - cbx) * ratio("fwhr")
    # cheekbone projection: additive z push on the SIDE cheek verts (projection is a z feature)
    if "cheek_projection" in hand and "cheek_projection" in base_fp:
        cheek = band[np.abs(out[band, 0] - out[band, 0].mean()) > 0.30 * face_w]
        dz = (hand["cheek_projection"] - base_fp["cheek_projection"]["value"]) * face_d * 0.15 * gain
        out[cheek, 2] += dz
    return out
