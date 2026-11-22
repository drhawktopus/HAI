"""Stage 2 — structural taxonomy measurement (genetic-mesh subsystem, #19).

Turn a parent's FLAME mesh into a continuous, scale-invariant STRUCTURAL FINGERPRINT: a set of
bone-structure proportions measured directly off the geometry — NEVER an ancestry category
(granular, never continental). Each feature carries a (value, confidence, kind):

  kind="inplane"  — width/height proportions in the frontal plane. A single frontal photo's FLAME
                    fit measures these reliably -> HIGH confidence. Mesh is authoritative.
  kind="proj"     — depth/z-projection (cheekbone/brow/chin/nasal forwardness). A frontal-only fit
                    constrains z weakly -> LOW confidence; Stage-2 VLM cross-check is scoped to
                    exactly these (rescue the mesh's blind spot, never override the solid features).

Design: decouple APPEARANCE (measured, here) from INHERITANCE DYNAMICS (researched, Stage 3). Pure
numpy; caller passes verts (N,3) + a regions dict (flame._regions(): eyes/nose/mouth/brows/jaw_oval)
so the subsystem never imports the orchestrator. All proportions are scale-free (the FLAME fit's
absolute scale is arbitrary), so two people's fingerprints compare feature-by-feature."""
import numpy as np
from collections import OrderedDict

_EPS = 1e-9
INPLANE_CONF = 0.9      # frontal FLAME fit measures in-plane geometry reliably
PROJ_CONF = 0.4         # z/projection weakly constrained by a frontal photo -> VLM cross-check territory


def _idx(regions, key):
    return np.asarray(regions[key], dtype=int)


def measure_structure(verts, regions):
    """Return an OrderedDict feature -> {"value", "confidence", "kind"} for one FLAME mesh."""
    V = np.asarray(verts, dtype=float)
    face_idx = np.unique(np.concatenate([_idx(regions, k) for k in regions]))
    F = V[face_idx]
    face_h = float(F[:, 1].max() - F[:, 1].min()) or _EPS
    face_w = float(F[:, 0].max() - F[:, 0].min()) or _EPS
    face_d = float(F[:, 2].max() - F[:, 2].min()) or _EPS
    eyes, nose = V[_idx(regions, "eyes")], V[_idx(regions, "nose")]
    mouth, brows, jaw = V[_idx(regions, "mouth")], V[_idx(regions, "brows")], V[_idx(regions, "jaw_oval")]
    eye_c = eyes.mean(axis=0); eye_y, eye_z = eye_c[1], eyes[:, 2].mean()
    mouth_y, mouth_z = mouth[:, 1].mean(), mouth[:, 2].mean()

    # cheekbone (bizygomatic) width: face x-extent in a band at eye height
    band = np.abs(F[:, 1] - eye_y) < 0.12 * face_h
    cheek_w = float(F[band, 0].max() - F[band, 0].min()) if band.sum() > 3 else face_w
    midface_h = max(eye_y - mouth_y, _EPS)

    # jaw: gonial (upper, at ~mouth level) vs chin (lowest 20%) width -> taper (square vs V)
    jy = jaw[:, 1]; jlo, jhi = jy.min(), jy.max()
    chin = jaw[jy < jlo + 0.20 * (jhi - jlo)]
    gon = jaw[np.abs(jy - mouth_y) < 0.15 * face_h]
    chin_w = float(chin[:, 0].max() - chin[:, 0].min()) if len(chin) > 3 else _EPS
    gon_w = float(gon[:, 0].max() - gon[:, 0].min()) if len(gon) > 3 else face_w

    # inter-eye spacing: split the eyes region by x about its centroid
    ex = eyes[:, 0]
    lc = eyes[ex > eye_c[0]].mean(axis=0)[0] if (ex > eye_c[0]).sum() else eye_c[0]
    rc = eyes[ex < eye_c[0]].mean(axis=0)[0] if (ex < eye_c[0]).sum() else eye_c[0]

    # projections (z): reference each region against a neighbouring plane, normalise by face depth
    cheek_side = band & (np.abs(F[:, 0] - F[:, 0].mean()) > 0.35 * face_w)
    cheek_z = float(F[cheek_side, 2].mean()) if cheek_side.sum() > 3 else float(F[:, 2].mean())
    chin_z = float(chin[:, 2].mean()) if len(chin) > 3 else float(F[:, 2].mean())

    feats = OrderedDict()

    def add(name, value, kind):
        feats[name] = {"value": round(float(value), 4),
                       "confidence": INPLANE_CONF if kind == "inplane" else PROJ_CONF, "kind": kind}

    # --- in-plane (mesh authoritative) ---
    add("fwhr", cheek_w / midface_h, "inplane")                 # facial width-to-height
    add("face_wh", face_w / face_h, "inplane")                  # overall aspect
    add("gonial_width", gon_w / face_w, "inplane")              # jaw-angle prominence
    add("jaw_taper", chin_w / max(gon_w, _EPS), "inplane")      # 1=square, <1=tapered/V
    add("forehead_ratio", (brows[:, 1].max() - brows[:, 1].min()) / face_h, "inplane")
    add("midface_ratio", midface_h / face_h, "inplane")         # midface length share
    add("eye_spacing", abs(lc - rc) / face_w, "inplane")
    # --- projection (low conf -> VLM cross-check scoped here) ---
    add("cheek_projection", (cheek_z - eye_z) / face_d, "proj")
    add("brow_projection", (brows[:, 2].mean() - eye_z) / face_d, "proj")
    add("chin_projection", (chin_z - mouth_z) / face_d, "proj")
    add("nasal_projection", (nose[:, 2].max() - float(F[:, 2].mean())) / face_d, "proj")
    return feats


def fingerprint_values(feats):
    """Bare feature->value dict (drops confidence/kind), for quick comparison/inheritance input."""
    return {k: v["value"] for k, v in feats.items()}
