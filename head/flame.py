"""FLAME engine: fits the 300-dim FLAME 3DMM to MediaPipe landmarks in closed form
(ridge least-squares - linear in shape parameters, no torch/GPU, milliseconds), then runs the
mosaic/toddler/depth pipeline on the full 5023-vertex mesh.

Assets live on the docs share under State/flame/ (generic_model.pkl is chumpy-pickled and
loads via the shim; mediapipe_landmark_embedding.npz is the official 105-point bridge)."""
import pathlib
import pickle
import random
import sys
import types

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import config

FLAME_DIR = pathlib.Path(config.DOCS_DIR) / "State" / "flame"
N_SHAPE = 100          # shape components used in the fit (of FLAME's 300; rest are tiny)
RIDGE_REL = 0.05       # ridge as a FRACTION of B'B's mean eigenvalue (the basis entries are
                       # ~1e-5 scale: a fixed ridge of 5e-3 was 250x the signal and crushed
                       # every fit toward the mean face — caught by the roundtrip test)
MP_Z_SCALE = 0.5       # MediaPipe's z is noisier/differently scaled than x/y

_M = None              # lazy model cache


def available():
    return all((FLAME_DIR / p).exists() for p in
               ("FLAME2020/generic_model.pkl", "mediapipe_landmark_embedding.npz",
                "FLAME_masks.pkl"))


def _chumpy_shim():
    class _Ch:
        def __setstate__(self, state):
            self.__dict__.update(state if isinstance(state, dict) else {})
    ch = types.ModuleType("chumpy")
    sub = types.ModuleType("chumpy.ch")
    sub.Ch = _Ch
    ch.Ch = _Ch
    ch.ch = sub
    for name in ("chumpy", "chumpy.ch", "chumpy.ch_ops", "chumpy.reordering"):
        sys.modules.setdefault(name, sub if name != "chumpy" else ch)


def _raw(x):
    if isinstance(x, np.ndarray):
        return x
    for v in getattr(x, "__dict__", {}).values():
        if isinstance(v, np.ndarray):
            return v
    raise ValueError("no ndarray inside chumpy object")


def _model():
    global _M
    if _M is not None:
        return _M
    _chumpy_shim()
    with open(FLAME_DIR / "FLAME2020" / "generic_model.pkl", "rb") as f:
        d = pickle.load(f, encoding="latin1")
    template = np.asarray(_raw(d["v_template"]), dtype=np.float64)          # (5023, 3)
    shapedirs = np.asarray(_raw(d["shapedirs"]), dtype=np.float64)          # (5023, 3, 400)
    faces = np.asarray(d["f"], dtype=np.int64)                              # (9976, 3)
    shape_basis = shapedirs[:, :, :N_SHAPE]                                 # shape block only
    emb = np.load(FLAME_DIR / "mediapipe_landmark_embedding.npz")
    tri_ids = faces[emb["lmk_face_idx"].astype(int)]                        # (105, 3) vertex ids
    bary = emb["lmk_b_coords"]                                              # (105, 3)
    mp_idx = emb["landmark_indices"].astype(int)                            # (105,) of 478
    # surface landmarks are barycentric combos of vertices -> everything stays linear
    lmk_template = (template[tri_ids] * bary[..., None]).sum(axis=1)        # (105, 3)
    lmk_basis = (shape_basis[tri_ids] * bary[..., None, None]).sum(axis=1)  # (105, 3, N_SHAPE)
    with open(FLAME_DIR / "FLAME_masks.pkl", "rb") as f:
        masks = {k: np.asarray(v, dtype=np.int64)
                 for k, v in pickle.load(f, encoding="latin1").items()}
    _M = {"template": template, "shape_basis": shape_basis, "faces": faces,
          "lmk_template": lmk_template, "lmk_basis": lmk_basis, "mp_idx": mp_idx,
          "masks": masks}
    return _M


def _umeyama(src, dst):
    """Similarity transform (R, s, t) minimizing ||s*R@src + t - dst||."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    sc, dc = src - mu_s, dst - mu_d
    cov = dc.T @ sc / len(src)
    U, S, Vt = np.linalg.svd(cov)
    sign = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        sign[2, 2] = -1
    R = U @ sign @ Vt
    s = np.trace(np.diag(S) @ sign) / (sc ** 2).sum() * len(src)
    t = mu_d - s * R @ mu_s
    return R, s, t


def fit_betas(mp478, iters=4):
    """FLAME shape fit from 478 MediaPipe landmarks. Alternates Umeyama alignment and a ridge
    least-squares shape solve (a single-pass alignment lets ~1% scale error project the whole
    TEMPLATE into the residual, wrecking the recovered direction — caught by the roundtrip
    test). Everything stays closed-form per iteration; 4 rounds converge. Returns betas."""
    m = _model()
    obs = np.asarray(mp478, dtype=np.float64)[m["mp_idx"]].copy()
    obs[:, 1] *= -1.0                      # MediaPipe y points down; FLAME y points up
    obs[:, 2] *= -MP_Z_SCALE               # ...and z toward the camera is +z in FLAME
    B = m["lmk_basis"].reshape(-1, N_SHAPE)                    # (315, N_SHAPE)
    BtB = B.T @ B
    ridge = RIDGE_REL * np.trace(BtB) / N_SHAPE
    solve = np.linalg.inv(BtB + ridge * np.eye(N_SHAPE)) @ B.T
    betas = np.zeros(N_SHAPE)
    for _ in range(iters):
        target = m["lmk_template"] + m["lmk_basis"] @ betas    # align to CURRENT estimate
        R, s, t = _umeyama(obs, target)
        aligned = (s * (R @ obs.T)).T + t
        betas = solve @ (aligned - m["lmk_template"]).reshape(-1)
    return betas


def decode(betas):
    """(5023, 3) mesh for the given shape parameters."""
    m = _model()
    return m["template"] + m["shape_basis"] @ np.asarray(betas, dtype=np.float64)


# Inheritance regions in FLAME vertex space (official masks; jaw = the face oval minus the
# other feature regions).
def _regions():
    masks = _model()["masks"]
    eyes = np.union1d(masks["left_eye_region"], masks["right_eye_region"])
    named = {"eyes": eyes, "nose": masks["nose"], "mouth": masks["lips"],
             "brows": masks["forehead"]}
    taken = np.concatenate(list(named.values()))
    named["jaw_oval"] = np.setdiff1d(masks["face"], taken)
    return named


def mosaic_mesh(verts_a, verts_b, seed):
    """Per-region inheritance on the full mesh: each region 70/30 toward one parent, the rest
    50/50. Returns (child_verts, picks)."""
    rng = random.Random(seed)
    child = (verts_a + verts_b) / 2.0
    picks = {}
    for region, idx in _regions().items():
        from_a = rng.random() < 0.5
        picks[region] = "a" if from_a else "b"
        src, other = (verts_a, verts_b) if from_a else (verts_b, verts_a)
        child[idx] = src[idx] * 0.70 + other[idx] * 0.30
    return child, picks


def toddlerize_mesh(verts):
    """The structural growth transform on the mesh (FLAME frame: +y up, +z out of the face):
    short mandible, larger eyes, smaller nose, rounder lower face."""
    m = _model()
    masks = m["masks"]
    out = verts.copy()
    nose_y = out[masks["nose"], 1].min()
    below = out[:, 1] < nose_y
    out[below, 1] = nose_y + (out[below, 1] - nose_y) * 0.78
    for key in ("left_eye_region", "right_eye_region"):
        idx = masks[key]
        c = out[idx].mean(axis=0)
        out[idx] = c + (out[idx] - c) * 1.12
    idx = masks["nose"]
    c = out[idx].mean(axis=0)
    out[idx] = c + (out[idx] - c) * 0.85
    jaw = _regions()["jaw_oval"]
    c = out[jaw].mean(axis=0)
    out[jaw, 0] = c[0] + (out[jaw, 0] - c[0]) * 1.06
    return out



NEURAL_FILES = ("mica_arcface.onnx", "mica_regressor.npz", "mica_reference.npz")
_NEURAL = None


def neural_available():
    return all((FLAME_DIR / f).exists() for f in NEURAL_FILES)


def _neural():
    global _NEURAL
    if _NEURAL is None:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(FLAME_DIR / "mica_arcface.onnx"),
                                    providers=["CPUExecutionProvider"])
        reg = np.load(FLAME_DIR / "mica_regressor.npz")
        idxs = reg["layer_indices"].tolist()
        # self-verify against the conversion's reference pair before trusting anything
        ref = np.load(FLAME_DIR / "mica_reference.npz")
        emb = sess.run(None, {"image": ref["image"]})[0]
        emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
        assert np.abs(emb - ref["embedding"]).max() < 1e-4, "mica onnx drift"
        h = emb
        for i, li in enumerate(idxs):
            h = h @ reg[f"{li}.weight"].T + reg[f"{li}.bias"]
            if i < len(idxs) - 1:
                h = np.where(h > 0, h, 0.2 * h)
        assert np.abs(h - ref["betas"]).max() < 1e-3, "mica regressor drift"
        _NEURAL = {"sess": sess, "reg": reg, "idxs": idxs}
    return _NEURAL


def betas_from_aligned(aligned_rgb_112):
    """(112,112,3) RGB uint8 arcface-aligned crop -> FLAME betas via MICA. Returns the first
    N_SHAPE components (MICA emits 300; our decode uses shape_basis[:N_SHAPE])."""
    n = _neural()
    x = (aligned_rgb_112.astype(np.float32) - 127.5) / 127.5
    x = np.transpose(x, (2, 0, 1))[None]
    emb = n["sess"].run(None, {"image": x})[0]
    emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    h = emb
    for i, li in enumerate(n["idxs"]):
        h = h @ n["reg"][f"{li}.weight"].T + n["reg"][f"{li}.bias"]
        if i < len(n["idxs"]) - 1:
            h = np.where(h > 0, h, 0.2 * h)
    return h[0][:N_SHAPE]


def render_depth(verts, size=1024):
    """Front-view depth render of the head mesh (near = bright) with FLAME's real topology."""
    m = _model()
    p = verts.copy()
    xy = np.stack([p[:, 0], -p[:, 1]], axis=1)          # image y grows downward
    xy -= xy.min(axis=0)
    xy = xy / xy.max() * (size * 0.62) + size * 0.19
    z = p[:, 2]
    zmin, zmax = z.min(), z.max()
    img = Image.new("F", (size, size), 0.0)
    draw = ImageDraw.Draw(img)
    faces = m["faces"]
    order = np.argsort(z[faces].mean(axis=1))            # far first
    for t in faces[order]:
        val = (z[t].mean() - zmin) / (zmax - zmin + 1e-9)
        draw.polygon([tuple(xy[i]) for i in t], fill=0.15 + 0.85 * val)
    arr = np.asarray(img)
    out = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "L")
    return out.filter(ImageFilter.GaussianBlur(3)).convert("RGB")
