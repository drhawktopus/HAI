"""Identity pipeline: generates simulated children from two parent portraits.

Geometry branch:
  MediaPipe 478-pt 3D landmarks -> per-region mosaic inheritance (each facial region
  derived from one parent) -> structural toddler growth transform ->
  Delaunay depth-map render -> union-ControlNet DEPTH conditioning.

Appearance branch:
  Both parents as chained IPAdapter PLUS-FACE references.
"""
import atexit
import pathlib
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import comfy_client
import config
import notes
import person_record

# IPAdapter bleeds reference clothing/headwear/facial hair into output.
# Negatives forbid these classes to prevent attribute contamination.
CHILD_NEGATIVE = ("adult, elderly, blurry, deformed, extra faces, "
                  "beard, mustache, goatee, facial hair, stubble, "
                  # SDXL interprets depth blobs as mirrors/portholes; forbid to prevent artifacting
                  "mirror, reflection, framed picture, picture frame, porthole, "
                  "hat, cap, hood, costume, headwear, helmet, "
                  "collage, multiple panels, grid, split image, photo montage, border")
# Measured band: 0.9 clones the reference scene wholesale; below ~0.3 identity washes out.
IPADAPTER_FACE_WEIGHT = 0.4
SAME_GENDER_FACE_WEIGHT = 0.44
OPP_GENDER_FACE_WEIGHT = 0.44
# genetic-mesh subsystem (its own thing at ../genetic-mesh) — Stage 1: gender dimorphism ON THE BONE
# GEOMETRY (the real boy fix). Imported fail-soft so nothing breaks if it isn't present.
try:                                    # in-tree subpackage; imported fail-soft so nothing breaks if absent
    from genetic_mesh import dimorphism as _dimorphism
except Exception as _gm_e:              # LOUD: a missing subpackage means the whole subsystem is silently off
    _dimorphism = None
    try:
        notes.log(f"IMG_PIPE genetic-mesh dimorphism import FAILED — subsystem OFF: {_gm_e!r}")
    except Exception:
        pass
try:                                    # Stages 2-4: measured structural inheritance (fail-soft)
    from genetic_mesh import structure as _structure, structure_vlm as _structure_vlm
    from genetic_mesh import inheritance as _inheritance, structure_apply as _structure_apply
except Exception as _gm_e:
    _structure = _structure_vlm = _inheritance = _structure_apply = None
    try:
        notes.log(f"IMG_PIPE genetic-mesh structure import FAILED — structural inheritance OFF: {_gm_e!r}")
    except Exception:
        pass
CN_DEPTH_STRENGTH = 0.55       # geometry guides but doesn't rigidify the render
CN_DEPTH_END = 0.8             # release the last steps for natural skin/hair detail

_LANDMARKER = None


def _landmarker():
    global _LANDMARKER
    if _LANDMARKER is None:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        model = pathlib.Path(__file__).parent / "face_landmarker.task"
        _LANDMARKER = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model)),
                num_faces=1))
    return _LANDMARKER


class NoFaceError(RuntimeError):
    pass


def landmarks(image_path):
    """478 x 3 normalized (x, y, z) landmarks for the single face in the image."""
    import mediapipe as mp
    res = _landmarker().detect(mp.Image.create_from_file(str(image_path)))
    if not res.face_landmarks:
        raise NoFaceError(str(image_path))
    return np.array([[p.x, p.y, p.z] for p in res.face_landmarks[0]], dtype=np.float64)


def landmarks_img(pil_img):
    """Same as landmarks() for an in-memory PIL image (scene-face crops during matching)."""
    import mediapipe as mp
    arr = np.asarray(pil_img.convert("RGB"))
    res = _landmarker().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=arr))
    if not res.face_landmarks:
        raise NoFaceError("in-memory image")
    return np.array([[p.x, p.y, p.z] for p in res.face_landmarks[0]], dtype=np.float64)


def normalize(pts):
    """Center on the nose bridge, scale by interocular distance — a shared frame so two
    parents' geometry is comparable."""
    return (pts - pts[168]) / np.linalg.norm(pts[33] - pts[263])


# Facial regions for mosaic inheritance (MediaPipe canonical indices).
REGIONS = {
    "jaw_oval":  list(range(0, 17)) + [132, 58, 172, 136, 150, 149, 176, 148, 152, 377,
                                       400, 378, 379, 365, 397, 288, 361, 323],
    "brows":     [70, 63, 105, 66, 107, 55, 65, 52, 53, 46,
                  300, 293, 334, 296, 336, 285, 295, 282, 283, 276],
    "eyes":      [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
                  263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466],
    "nose":      [1, 2, 4, 5, 6, 19, 94, 168, 195, 197, 141, 125, 44, 45, 51,
                  275, 274, 354, 370, 462, 250, 458, 461, 439, 219],
    "mouth":     [61, 84, 17, 314, 405, 320, 307, 375, 321, 308, 324, 318,
                  78, 95, 88, 178, 87, 14, 317, 402, 319, 325,
                  0, 267, 269, 270, 409, 291, 37, 39, 40, 185, 146, 91, 181],
}


def mosaic_child(p_a, p_b, seed):
    """Per-region inheritance: each region picks a dominant parent (70/30 blend toward it so
    features read as inherited but transitions stay smooth); everything else blends 50/50.
    Returns (child_points, {region: 'a'|'b'}) — the picks are the audit trail."""
    rng = random.Random(seed)
    child = (p_a + p_b) / 2.0
    picks = {}
    for region, idx in REGIONS.items():
        from_a = rng.random() < 0.5
        picks[region] = "a" if from_a else "b"
        src, other = (p_a, p_b) if from_a else (p_b, p_a)
        child[idx] = src[idx] * 0.70 + other[idx] * 0.30
    return child, picks


def toddlerize(pts):
    """Structural growth-curve transform: short mandible, proportionally larger eyes,
    smaller nose, rounder jaw — a toddler skull, not a shrunken adult."""
    out = pts.copy()
    nose_y = out[4, 1]
    below = out[:, 1] > nose_y
    out[below, 1] = nose_y + (out[below, 1] - nose_y) * 0.78
    for eye in (REGIONS["eyes"][:16], REGIONS["eyes"][16:]):
        c = out[eye].mean(axis=0)
        out[eye] = c + (out[eye] - c) * 1.12
    c = out[REGIONS["nose"]].mean(axis=0)
    out[REGIONS["nose"]] = c + (out[REGIONS["nose"]] - c) * 0.85
    jaw = REGIONS["jaw_oval"]
    c = out[jaw].mean(axis=0)
    out[jaw, 0] = c[0] + (out[jaw, 0] - c[0]) * 1.06
    return out


def render_depth(pts, size=1024):
    """Rasterize the landmark cloud as a smooth depth image (near = bright): Delaunay over
    the 2D projection, painter's-algorithm triangle fill by mean depth, Gaussian soften."""
    from scipy.spatial import Delaunay
    p = pts.copy()
    p -= p.min(axis=0)
    span = p[:, :2].max()
    p[:, :2] = p[:, :2] / span * (size * 0.62) + size * 0.19
    z = p[:, 2]
    zmin, zmax = z.min(), z.max()
    img = Image.new("F", (size, size), 0.0)
    draw = ImageDraw.Draw(img)
    tris = [tuple(t) for t in Delaunay(p[:, :2]).simplices]
    tri_depth = lambda t: (z[t[0]] + z[t[1]] + z[t[2]]) / 3   # noqa: E731
    for t in sorted(tris, key=tri_depth, reverse=True):
        val = 1.0 - (tri_depth(t) - zmin) / (zmax - zmin + 1e-9)
        draw.polygon([tuple(p[i, :2]) for i in t], fill=0.15 + 0.85 * val)
    arr = np.asarray(img)
    out = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "L")
    return out.filter(ImageFilter.GaussianBlur(6)).convert("RGB")


def depth_png_bytes(depth_img):
    from io import BytesIO
    b = BytesIO()
    depth_img.save(b, format="PNG")
    return b.getvalue()


DEFAULT_CHILD_PROMPT = ("portrait photo of a happy toddler, 2 years old, soft natural light, "
                        "facing camera, family photo, high detail")
# Outfits vary by child index to prevent simulated siblings from looking identical
# when tile outfits are composited into the family scene.
KID_OUTFITS = ["a red top", "a teal top", "a green top", "a mustard-yellow top"]


def build_child_workflow(depth_name, ref_a_name, ref_b_name, seed, prompt=None,
                         use_depth=True, negative=None, weight_a=None, weight_b=None):
    """SDXL graph: depth-ControlNet geometry + dual-parent IPAdapter PLUS-FACE appearance.
    use_depth=False is RUNG-1: appearance blending only, no geometry conditioning — the
    commodity baby-app approach, kept as the bake-off baseline.
    negative overrides CHILD_NEGATIVE (feminine-exclusion for boys). weight_a/weight_b override
    the per-parent IPAdapter face weight — gender-conditional: boost the SAME-gender parent so
    boys read boyish (the dual-parent face-blend was averaging toward feminine). Depth path only."""
    prompt = prompt or DEFAULT_CHILD_PROMPT
    negative = negative or CHILD_NEGATIVE
    weight_a = IPADAPTER_FACE_WEIGHT if weight_a is None else weight_a
    weight_b = IPADAPTER_FACE_WEIGHT if weight_b is None else weight_b
    if not use_depth:
        return {"prompt": {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": config.IMG_SDXL_CHECKPOINT}},
            "30": {"class_type": "IPAdapterUnifiedLoader",
                   "inputs": {"model": ["1", 0], "preset": "PLUS FACE (portraits)"}},
            "40": {"class_type": "LoadImage", "inputs": {"image": ref_a_name}},
            "41": {"class_type": "LoadImage", "inputs": {"image": ref_b_name}},
            "31": {"class_type": "IPAdapter",
                   "inputs": {"model": ["30", 0], "ipadapter": ["30", 1], "image": ["40", 0],
                              "weight": IPADAPTER_FACE_WEIGHT, "start_at": 0.0, "end_at": 0.9,
                              "weight_type": "standard"}},
            "32": {"class_type": "IPAdapter",
                   "inputs": {"model": ["31", 0], "ipadapter": ["30", 1], "image": ["41", 0],
                              "weight": IPADAPTER_FACE_WEIGHT, "start_at": 0.0, "end_at": 0.9,
                              "weight_type": "standard"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": negative}},
            "4": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "5": {"class_type": "KSampler",
                  "inputs": {"model": ["32", 0], "positive": ["2", 0], "negative": ["3", 0],
                             "latent_image": ["4", 0], "seed": seed, "steps": 25, "cfg": 7.0,
                             "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0],
                                                        "filename_prefix": "haigen"}},
        }}
    wf = {"prompt": {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": config.IMG_SDXL_CHECKPOINT}},
        "30": {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": ["1", 0], "preset": "PLUS FACE (portraits)"}},
        "40": {"class_type": "LoadImage", "inputs": {"image": ref_a_name}},
        "41": {"class_type": "LoadImage", "inputs": {"image": ref_b_name}},
        "31": {"class_type": "IPAdapter",
               "inputs": {"model": ["30", 0], "ipadapter": ["30", 1], "image": ["40", 0],
                          "weight": IPADAPTER_FACE_WEIGHT, "start_at": 0.0, "end_at": 0.9,
                          "weight_type": "standard"}},
        "32": {"class_type": "IPAdapter",
               "inputs": {"model": ["31", 0], "ipadapter": ["30", 1], "image": ["41", 0],
                          "weight": IPADAPTER_FACE_WEIGHT, "start_at": 0.0, "end_at": 0.9,
                          "weight_type": "standard"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": negative}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "8": {"class_type": "LoadImage", "inputs": {"image": depth_name}},
        "21": {"class_type": "ControlNetLoader",
               "inputs": {"control_net_name": comfy_client.CONTROLNET_MODEL}},
        "22": {"class_type": "SetUnionControlNetType",
               "inputs": {"control_net": ["21", 0], "type": "depth"}},
        "23": {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": ["2", 0], "negative": ["3", 0], "control_net": ["22", 0],
                          "image": ["8", 0], "strength": CN_DEPTH_STRENGTH,
                          "start_percent": 0.0, "end_percent": CN_DEPTH_END,
                          "vae": ["1", 2]}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["32", 0], "positive": ["23", 0], "negative": ["23", 1],
                         "latent_image": ["4", 0], "seed": seed, "steps": 25, "cfg": 7.0,
                         "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0],
                                                    "filename_prefix": "haigen"}},
    }}
    wf["prompt"]["31"]["inputs"]["weight"] = weight_a   # gender-conditional (same-gender parent)
    wf["prompt"]["32"]["inputs"]["weight"] = weight_b
    return wf


def _aligned_face(image_path):
    """(112,112,3) RGB uint8 arcface-aligned crop of the single face in the image — the
    input MICA's neural fitter expects. SCRFD detects + gives the 5 keypoints; insightface's
    canonical norm_crop does the alignment."""
    from insightface.utils import face_align
    app = _scrfd()
    if app is None:
        raise NoFaceError("scrfd unavailable for neural alignment")
    rgb = np.asarray(Image.open(image_path).convert("RGB"))
    faces = app.get(rgb[:, :, ::-1])                  # insightface expects BGR
    if not faces:
        raise NoFaceError(str(image_path))
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    crop_bgr = face_align.norm_crop(rgb[:, :, ::-1], face.kps)
    return crop_bgr[:, :, ::-1].copy()                # back to RGB


def latest_uploads(user, n=2):
    """The user's n most recent uploaded images (newest first), [] if none."""
    d = comfy_client.uploads_dir(user)
    if not d.exists():
        return []
    files = sorted((f for f in d.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg")),
                   key=lambda f: f.name, reverse=True)
    return [f.name for f in files[:n]]


# --- family photo: scene-first + per-face identity inpainting -------------------------------------
# The collage-then-unify approach failed live 4 ways (photo-wall tiles, ghost rectangles, washed
# haze): at identity-safe denoise, img2img PRESERVES global structure, and a collage's structure
# is separate portraits. Correct architecture: txt2img generates a REAL group scene (natural
# bodies, arms around each other), then each generated face is INPAINTED with that one person's
# photo as an IPAdapter reference — one identity per pass, no bleed, composition guaranteed.

FACE_INPAINT_DENOISE = 0.62    # inside the mask only: enough to imprint the reference identity
FACE_INPAINT_WEIGHT = 0.75     # IPAdapter weight per face (single-subject pass tolerates high)

_MULTI_LANDMARKER = None


def _multi_landmarker():
    global _MULTI_LANDMARKER
    if _MULTI_LANDMARKER is None:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        model = pathlib.Path(__file__).parent / "face_landmarker.task"
        _MULTI_LANDMARKER = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model)),
                num_faces=6,
                # default 0.5 is too strict for small/semi-profile faces; lowering to
                # 0.3 prevents detection failure that triggers infinite rerolls in exact-count gate
                min_face_detection_confidence=0.3))
    return _MULTI_LANDMARKER


@atexit.register
def _close_landmarkers():
    """Close the cached MediaPipe FaceLandmarkers while the interpreter is still alive.

    MediaPipe's close() is not shutdown-safe: left to garbage collection at interpreter teardown it
    runs after mediapipe's C-utils module globals are already cleared, dereferences a None dispatcher
    callback, and prints a `TypeError: 'NoneType' object is not callable` traceback (cosmetic — it
    fires after results are tallied, so the exit code is unaffected, but a traceback in test output
    is a defect). Closing at atexit runs while the runtime is intact, so teardown is silent. Fail-safe
    by contract: a close error here must never itself raise.
    """
    for _lm in (_LANDMARKER, _MULTI_LANDMARKER):
        if _lm is not None:
            try:
                _lm.close()
            except Exception:
                pass


_SCRFD = None


def _scrfd():
    """Return insightface's SCRFD detector for small faces.

    MediaPipe is blind to faces <120px; SCRFD handles crowds/small faces.
    Returns None if unavailable."""
    global _SCRFD
    if _SCRFD is None:
        try:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name="antelopev2",
                               root=str(pathlib.Path(config.DOCS_DIR) / "State" / "insightface"),
                               allowed_modules=["detection", "genderage"],
                               providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=-1, det_size=(640, 640))
            _SCRFD = app
        except Exception as exc:
            notes.log(f"IMG_PIPE scrfd unavailable ({type(exc).__name__}: {exc}) "
                      "-> mediapipe fallback")
            _SCRFD = False
    return _SCRFD or None


_RECOG = None


def _recog():
    """A recognition-capable antelopev2 instance (the glintr100 head → arcface embedding).
    Separate from _scrfd() (detection+genderage) so the hot detect path stays fast; the model
    files already live in State/insightface. CPU is plenty for single crops. None if unavailable."""
    global _RECOG
    if _RECOG is None:
        try:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name="antelopev2",
                               root=str(pathlib.Path(config.DOCS_DIR) / "State" / "insightface"),
                               allowed_modules=["detection", "recognition"],
                               providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=-1, det_size=(640, 640))
            _RECOG = app
        except Exception as exc:
            notes.log(f"IMG_PIPE recog unavailable ({type(exc).__name__}: {exc}) "
                      "-> identity-guarded finisher disabled, fused output stands")
            _RECOG = False
    return _RECOG or None


def face_embedding(pil_img):
    """L2-normed arcface embedding of the largest face, or None if no face / no recognizer."""
    app = _recog()
    if app is None:
        return None
    arr = np.asarray(pil_img.convert("RGB"))
    faces = app.get(arr[:, :, ::-1])                 # insightface expects BGR
    if not faces:
        return None
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
    e = faces[0].normed_embedding
    return e / np.linalg.norm(e)


def _guarded_finish(fused, swapped, ref_path, idx):
    """Identity-guarded finisher (#19 round-4 fix ⑧): the 0.22 fuse pass melts the seam + fixes
    gaze but ERODES identity on weak binds. Keep whichever of {post-fuse, post-swap} scores
    higher arcface vs the reference. Strong binds → fused wins (prettier, gaze fixed); weak
    binds → raw swap wins (identity preserved). Fails soft to the fused output."""
    from io import BytesIO
    ref_emb = face_embedding(Image.open(BytesIO(tight_ref_bytes(ref_path))))
    if ref_emb is None:
        return fused
    swap_emb = face_embedding(swapped)
    fuse_emb = face_embedding(fused)
    s_fuse = float(np.dot(ref_emb, fuse_emb)) if fuse_emb is not None else -1.0
    s_swap = float(np.dot(ref_emb, swap_emb)) if swap_emb is not None else -1.0
    winner, tag = (fused, "fused") if s_fuse >= s_swap else (swapped, "swap")
    notes.log(f"IMG_PIPE finisher face {idx + 1}: fused {s_fuse:.3f} vs swap {s_swap:.3f} "
              f"-> kept {tag}")
    return winner


def detect_faces_ex(pil_img):
    """[{'box': (cx, cy, w, h) normalized, 'age': int|None}] for every face, largest first.
    Age comes from antelopev2's genderage head — the matcher partitions parents to ADULT
    faces and children to CHILD faces (v7: pure shape distance left the two LARGEST faces
    unmatched while extras photobombed)."""
    arr = np.asarray(pil_img.convert("RGB"))
    app = _scrfd()
    out = []
    if app is not None:
        H, W = arr.shape[:2]
        for f in app.get(arr[:, :, ::-1]):               # insightface expects BGR
            x1, y1, x2, y2 = f.bbox
            out.append({"box": (((x1 + x2) / 2) / W, ((y1 + y2) / 2) / H,
                                (x2 - x1) / W, (y2 - y1) / H),
                        "age": int(f.age) if getattr(f, "age", None) is not None else None,
                        "sex": getattr(f, "sex", None)})   # 'M'/'F' from genderage
    else:
        import mediapipe as mp                            # selfie-range fallback, no ages
        res = _multi_landmarker().detect(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=arr))
        for lms in res.face_landmarks:
            xs = np.array([p.x for p in lms])
            ys = np.array([p.y for p in lms])
            out.append({"box": ((xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2,
                                xs.max() - xs.min(), ys.max() - ys.min()),
                        "age": None, "sex": None})
    return sorted(out, key=lambda d: d["box"][2] * d["box"][3], reverse=True)


def detect_faces(pil_img):
    """Back-compat: normalized (cx, cy, w, h) boxes only, largest first."""
    return [d["box"] for d in detect_faces_ex(pil_img)]


def face_mask_image(size, box, grow=1.7):
    """White oval over the (normalized) face box on black — the inpaint region. `grow`
    dilates past the chin/hairline so the identity pass can reshape the whole head."""
    W, H = size
    cx, cy, w, h = box
    rx, ry = w * W * grow / 2, h * H * grow / 2
    mask = Image.new("RGB", size, (0, 0, 0))
    d = ImageDraw.Draw(mask)
    d.ellipse([cx * W - rx, cy * H - ry * 1.15, cx * W + rx, cy * H + ry],
              fill=(255, 255, 255))
    return mask.filter(ImageFilter.GaussianBlur(int(min(rx, ry) * 0.15)))


def _face_tile(scene, box, grow=1.9):
    """Generous crop of one detected face from the scene, for FLAME fitting."""
    W, H = scene.size
    cx, cy, w, h = box
    r = max(w * W, h * H) * grow / 2
    return scene.crop((int(max(0, cx * W - r)), int(max(0, cy * H - r)),
                       int(min(W, cx * W + r)), int(min(H, cy * H + r))))


CHILD_MAX_AGE = 20   # scene-composition gate: after the two oldest (the parents), every other
                     # face must read as a child, with margin for genderage noise

FACE_FUSE_DENOISE = 0.22   # the post-swap fuse pass: enough to melt the seam + fix gaze,
                           # not enough to erode the swapped identity
HERITAGE_HINTS = {}        # upload-name -> style hint ("loose Indian-German waves");
                           # consent-provided, refines rendering STYLE only (#20 owns storage)
FACE_DETAIL_RES = 1024   # identity inpaints require an upscaled face region (not full scene);
# at scene res (~150px) identity detail is lost; 1024 is the quality ceiling.


def tight_ref_bytes(image_path, grow=2.2):
    """Return PNG bytes of a face-tight crop for identity adapters.

    Identity swaps require jaw and hair context; full-frame refs leak composition
    artifacts (e.g., blended subjects from couple photos).

    `grow` controls the crop margin:
    - 2.2 (default): Includes shoulders, preserving necessary facial context.
    - ~1.35: Face-only, excluding clothing.

    Falls back to original image bytes if no face is detected."""
    from io import BytesIO
    try:
        img = Image.open(image_path).convert("RGB")
        faces = detect_faces_ex(img)
        if not faces:
            raise ValueError("no face")
        tile = _face_tile(img, faces[0]["box"], grow=grow)
        buf = BytesIO()
        tile.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return pathlib.Path(image_path).read_bytes()


def face_only_ref_bytes(image_path):
    """FACE-ONLY crop (no shoulders/torso) — for the child IPAdapter refs. The 2.2-grow crop
    pulled a parent's CLOTHING color into the child (the children inherited a parent's top colour; the
    KID_OUTFITS text couldn't override the visual signal — round-5 finding 3b). Face-only removes
    the clothing source, so the outfit prompt wins, and PLUS-FACE IPAdapter wants the face anyway."""
    return tight_ref_bytes(image_path, grow=1.35)


_DEBEARD_PROMPT = ("Remove the man's beard and mustache completely — fully clean-shaven, smooth bare "
                   "skin on the jaw, chin, cheeks and upper lip, no goatee or stubble. Keep his face "
                   "shape, eyes, nose, hair, smile, clothing and background exactly the same. "
                   "Photorealistic.")


def _build_debeard_kontext(ref_name, seed):
    """Requires flux1-dev-kontext model to be staged."""
    return {"prompt": {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "flux1-dev-kontext_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors",
              "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "8": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "9": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["8", 0]}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["3", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": _DEBEARD_PROMPT}},
        "11": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["4", 0], "latent": ["10", 0]}},
        "12": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["11", 0], "guidance": 2.5}},
        "5neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "13": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1216, "height": 832, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["12", 0],
              "negative": ["5neg", 0], "latent_image": ["13", 0], "seed": seed, "steps": 20,
              "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "debeard"}},
    }}


def _debeard_cache_path(image_path):
    import hashlib
    p = pathlib.Path(image_path)
    key = hashlib.md5(f"{p.resolve()}:{p.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    return pathlib.Path(config.STATE_DIR) / "debeard-cache" / f"{key}.png"


def debeard_ref_bytes(image_path):
    """Return a face-only reference image, de-bearded if the source has facial hair.

    De-bearding is required to prevent beard artifacts from bleeding into child
    generations via IPAdapter. The result is cached by (path, mtime) and falls
    back to the original file on failure."""
    import genetics
    try:
        if not genetics.has_facial_hair(image_path):
            return face_only_ref_bytes(image_path)
        cache = _debeard_cache_path(image_path)
        if not cache.exists():
            up = comfy_client.upload_to_comfy(f"debeard-src-{cache.stem}.png",
                                              pathlib.Path(image_path).read_bytes())
            r = comfy_client.generate(_build_debeard_kontext(up, seed=7))
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(comfy_client.fetch(r["filename"]))
            comfy_client.free_memory()      # unload Flux Kontext before the SDXL kid renders
            notes.log(f"IMG_PIPE debeard: shaved {pathlib.Path(image_path).name} -> cache")
        return face_only_ref_bytes(cache)
    except Exception as exc:
        comfy_client.free_memory()
        notes.log(f"IMG_PIPE debeard failed (soft, using original): {exc}")
        return face_only_ref_bytes(image_path)


def torso_hint(depth_img):
    """Add a soft torso wedge to ground the head depth blob.

    Without this, SDXL may interpret the bare oval as a mirror and hallucinate artifacts inside it."""
    arr = np.asarray(depth_img.convert("L"))
    ys, xs = np.nonzero(arr > 8)
    if len(ys) == 0:
        return depth_img
    bottom, cx = int(ys.max()), int(xs.mean())
    w = max(8, int((xs.max() - xs.min()) * 0.9))
    H, W = arr.shape
    v = int(arr[ys, xs].mean() * 0.55)
    base = depth_img.copy()
    d = ImageDraw.Draw(base)
    d.ellipse([cx - w, bottom - w // 6, cx + w, bottom + int(w * 1.6)], fill=(v, v, v))
    soft = base.filter(ImageFilter.GaussianBlur(6))
    soft.paste(depth_img.crop((0, 0, W, max(1, bottom - 4))), (0, 0))  # face stays crisp
    return soft


def _face_region(scene, box, grow=2.6):
    """Square crop AROUND a detected face (for detail-res inpainting) + its scene coords.
    Returns (crop_img, (l, t, r, b))."""
    W, H = scene.size
    cx, cy, w, h = box
    r = max(w * W, h * H) * grow / 2
    l, t = int(max(0, cx * W - r)), int(max(0, cy * H - r))
    rr, b = int(min(W, cx * W + r)), int(min(H, cy * H + r))
    return scene.crop((l, t, rr, b)), (l, t, rr, b)


def _box_in_region(box, region, size):
    """Re-express a normalized scene box in a crop region's own normalized coords."""
    W, H = size
    l, t, r, b = region
    rw, rh = (r - l) or 1, (b - t) or 1
    cx, cy, w, h = box
    return ((cx * W - l) / rw, (cy * H - t) / rh, w * W / rw, h * H / rh)


def _pool_assign(faces, refs_idx, ref_betas, scene, tag):
    """Optimal FLAME-distance assignment of one pool (returns [(box, ref_index)])."""
    import flame
    if not faces or not refs_idx:
        return []
    scene_b = [flame.fit_betas(landmarks_img(_face_tile(scene, d["box"]))) for d in faces]
    cost = np.array([[np.linalg.norm(sb - ref_betas[j]) for j in refs_idx]
                     for sb in scene_b])
    from scipy.optimize import linear_sum_assignment
    rows, cols = linear_sum_assignment(cost)
    pairs = [(faces[i]["box"], refs_idx[j], cost[i, j]) for i, j in zip(rows, cols)]
    notes.log(f"IMG_PIPE match[{tag}]: " + " ".join(
        f"age{faces[i]['age']}->ref{refs_idx[j]}(d={cost[i, j]:.1f})"
        for i, j in zip(rows, cols)))
    return [(box, j) for box, j, _ in pairs]


def match_faces(scene, faces, ref_paths, ref_sexes=None):
    """Pair each scene face with the reference it most resembles.

        Age-partitioned (adults->parents, children->children) using FLAME shape
    distance. If `ref_sexes` is provided and adult sexes are detected, parents are
    assigned sex-first to prevent cross-gender mismatches (e.g., male faces on
    female figures). Falls back to positional order if matching fails.

        Args:
            scene: Scene object.
            faces: List of detect_faces_ex dicts or bare box tuples.
            ref_paths: Paths to reference images.
            ref_sexes: Optional ordered sexes for first two refs."""
    import flame
    faces = [f if isinstance(f, dict) else {"box": f, "age": None} for f in faces]
    try:
        if not flame.available():
            raise RuntimeError("flame assets unavailable")
        ref_betas = [flame.fit_betas(landmarks(p)) for p in ref_paths]
        n_parents = min(2, len(ref_paths))
        ages_known = all(f["age"] is not None for f in faces)
        kid_refs = list(range(n_parents, len(ref_paths)))
        if ages_known and len(faces) >= len(ref_paths):
            # RELATIVE age rule (genderage is noisy: a teen read as 27 once): the two OLDEST
            # faces are the parent pool, everyone else is the kid pool
            by_age = sorted(faces, key=lambda f: f["age"], reverse=True)
            adults, children = by_age[:n_parents], by_age[n_parents:]
            adult_sexes = [f.get("sex") for f in adults]
            if (ref_sexes and n_parents == 2 and all(adult_sexes)
                    and sorted(adult_sexes) == sorted(ref_sexes)
                    and adult_sexes[0] != adult_sexes[1]):
                # sex-first parent assignment: each parent ref lands on the adult face of
                # the SAME presented sex; shape distance only breaks same-sex ties above
                pairs = []
                for j in range(n_parents):
                    face = adults[adult_sexes.index(ref_sexes[j])]
                    pairs.append((face["box"], j))
                notes.log(f"IMG_PIPE match[adults]: SEX-FIRST "
                          + " ".join(f"{s}->ref{j}" for j, s in
                                     ((j, ref_sexes[j]) for j in range(n_parents))))
                pairs += _pool_assign(children, kid_refs, ref_betas, scene, "kids")
            else:
                pairs = (_pool_assign(adults, list(range(n_parents)), ref_betas, scene,
                                      "adults")
                         + _pool_assign(children, kid_refs, ref_betas, scene, "kids"))
        else:   # ages missing: global assignment (cross-age pairing can't be priced then)
            pairs = _pool_assign(faces, list(range(len(ref_paths))), ref_betas, scene,
                                 "global")
        return [(box, ref_paths[j], j) for box, j in pairs]
    except Exception as exc:
        notes.log(f"IMG_PIPE match FALLBACK positional ({type(exc).__name__}: {exc})")
        return [(f["box"], p, j) for j, (f, p) in enumerate(zip(faces, ref_paths))]


_SWAP_OK = None


def swap_available():
    global _SWAP_OK
    if _SWAP_OK is None:
        try:
            import httpx
            r = httpx.get(comfy_client._url("/object_info/ReActorFaceSwap"), timeout=10)
            info = r.json().get("ReActorFaceSwap", {})
            models = info["input"]["required"]["swap_model"][0]
            _SWAP_OK = "inswapper_128.onnx" in models
        except Exception:
            _SWAP_OK = False
    return _SWAP_OK


def build_face_inpaint_workflow(scene_name, mask_name, ref_name, seed, subject="person",
                                swap_source=False):
    """Resample ONLY the masked face region, conditioned on one person's reference photo.
    swap_source=True chains a ReActor identity swap AFTER the inpaint — the inpaint blends
    pose/lighting into the scene, the swap makes the face actually BE the person."""
    wf = _face_inpaint_graph(scene_name, mask_name, ref_name, seed, subject)
    if swap_source:
        wf["prompt"]["50"] = {"class_type": "ReActorFaceSwap", "inputs": {
            "enabled": True, "input_image": ["6", 0], "source_image": ["40", 0],
            "swap_model": "inswapper_128.onnx", "facedetection": "retinaface_resnet50",
            "face_restore_model": "GFPGANv1.4.pth", "face_restore_visibility": 0.8,
            "codeformer_weight": 0.5, "detect_gender_input": "no",
            "detect_gender_source": "no", "input_faces_index": "0",
            "source_faces_index": "0", "console_log_level": 1}}
        wf["prompt"]["51"] = {"class_type": "VAEEncode",
                              "inputs": {"pixels": ["50", 0], "vae": ["1", 2]}}
        wf["prompt"]["52"] = {"class_type": "SetLatentNoiseMask",
                              "inputs": {"samples": ["51", 0], "mask": ["11", 0]}}
        wf["prompt"]["53"] = {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["1", 1],
            "text": f"face of a {subject} looking directly at the camera, natural skin "
                    "texture, matching the scene lighting"}}
        wf["prompt"]["54"] = {"class_type": "KSampler", "inputs": {
            "model": ["31", 0], "positive": ["53", 0], "negative": ["3", 0],
            "latent_image": ["52", 0], "seed": seed + 1, "steps": 12, "cfg": 5.0,
            "sampler_name": "euler", "scheduler": "normal",
            "denoise": FACE_FUSE_DENOISE}}
        wf["prompt"]["55"] = {"class_type": "VAEDecode",
                              "inputs": {"samples": ["54", 0], "vae": ["1", 2]}}
        wf["prompt"]["7"]["inputs"]["images"] = ["55", 0]   # node 7 = post-FUSE (default output)
        # node 56 = post-SWAP (pre-fuse) — saved so the identity-guarded finisher can keep
        # whichever scores higher arcface vs the ref (fuse erodes weak binds; #19 fix ⑧)
        wf["prompt"]["56"] = {"class_type": "SaveImage",
                              "inputs": {"images": ["50", 0], "filename_prefix": "haigen"}}
    return wf


def _face_inpaint_graph(scene_name, mask_name, ref_name, seed, subject="person"):
    return {"prompt": {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": config.IMG_SDXL_CHECKPOINT}},
        "30": {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": ["1", 0], "preset": "PLUS FACE (portraits)"}},
        "40": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "31": {"class_type": "IPAdapter",
               "inputs": {"model": ["30", 0], "ipadapter": ["30", 1], "image": ["40", 0],
                          "weight": FACE_INPAINT_WEIGHT, "start_at": 0.0, "end_at": 1.0,
                          "weight_type": "standard"}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1],
                         "text": f"face of a {subject}, natural skin detail, photo, "
                                 "matching the scene lighting"}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1],
                         # base SDXL's oldest flaw is eyes; name every failure mode
                         "text": "deformed, blurry, extra faces, mismatched lighting, "
                                 "cross-eyed, lazy eye, asymmetric eyes, wonky eyes, "
                                 "misaligned pupils, wall-eyed"}},
        "8": {"class_type": "LoadImage", "inputs": {"image": scene_name}},
        "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "11": {"class_type": "ImageToMask", "inputs": {"image": ["10", 0], "channel": "red"}},
        "12": {"class_type": "SetLatentNoiseMask",
               "inputs": {"samples": ["9", 0], "mask": ["11", 0]}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["31", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["12", 0], "seed": seed, "steps": 25, "cfg": 7.0,
                         "sampler_name": "euler", "scheduler": "normal",
                         "denoise": FACE_INPAINT_DENOISE}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0],
                                                    "filename_prefix": "haigen"}},
    }}


def family_photo(user, kids=2, prompt=None, seed_base=None, scene_quality="fast", effort=None,
                 routing=None):
    """Two uploads + freshly simulated children -> ONE coherent family portrait.
    Scene-first: txt2img a natural family group, detect the generated faces, then inpaint
    each face with the matching person's reference. scene_quality="best" = the HYBRID:
    Flux paints the theater (composition/hands/lighting), SDXL casts the actors (the
    identity adapters are SDXL-architecture). effort + routing ride through to the child
    sim (the P3d dials). Returns {name, seconds, children}."""
    from io import BytesIO
    children = simulate_children(user, count=kids, seed_base=seed_base, effort=effort,
                                 routing=routing)
    d = comfy_client.uploads_dir(user)
    ups = latest_uploads(user, 2)
    img_dir = comfy_client._images_dir() / comfy_client._safe(user)
    n = 2 + len(children)
    if seed_base is None:
        import time
        seed_base = int(time.time()) % (2 ** 28)
    kid_word = ("one small young child (toddler)" if kids == 1
                else f"{kids} small young children (toddlers)")
    family_word = {3: "family of three", 4: "family of four", 5: "family of five",
                   6: "family of six"}.get(n, f"family of {n}")
    # SDXL requires spelled-out family sizes (e.g., "family of four"); numerals fail the count gate.
    # Caller appends FLAVOR to this skeleton; do not replace it.
    flavor = str(prompt).strip(" ,.") if prompt else "warm studio backdrop"
    # Read sex once; fail-open if undetectable. Must run before scene_prompt
    # so parent_desc feeds it correctly.
    ref_sexes, ref_sexes_ordered, parent_desc = None, None, ""
    try:
        det = [detect_faces_ex(Image.open(d / up).convert("RGB")) for up in ups[:2]]
        if all(x and x[0].get("sex") for x in det):
            ref_sexes_ordered = [x[0]["sex"] for x in det]     # ordered like ups/ref_paths
            ref_sexes = sorted(ref_sexes_ordered)
        import genetics
        descs = []
        for up, faces_det in zip(ups[:2], det):
            t = genetics.measure_traits(d / up, landmarks(d / up))
            noun = {"M": "father", "F": "mother"}.get(
                faces_det[0].get("sex") if faces_det else None, "parent")
            hair = f"{t['hair']} {t['hair_texture']} hair"
            if noun == "father":
                hair = "short " + hair
            # Heritage refines style only; runtime override wins over persistent record.
            hint = HERITAGE_HINTS.get(up) or person_record.get_heritage(up)
            if hint:
                hair += f" ({hint})"
            descs.append(f"the {noun} has {hair} and {t['skin']} skin")
        parent_desc = ", " + ", ".join(descs) if descs else ""
    except Exception:
        ref_sexes = ref_sexes or None
    # composition language SDXL can't misread (v9 drew BOARDROOMS: 5-7 adults aged 38-69 for
    # "family of four with toddlers"): a couple kneeling beside toddlers hard-implies the cast
    scene_prompt = (
        f"professional family portrait photo, a young mother and father kneeling close "
        f"beside their {kid_word}{parent_desc}, just the {family_word} and nobody else, "
        f"everyone facing the camera with clearly visible faces, soft even lighting, "
        f"high detail, {flavor}")
    notes.log(f"IMG_PIPE scene prompt: {scene_prompt[:160]!r}")
    total_s, faces, scene = 0.0, [], None
    sexes_ok = True
    if scene_quality == "best":
        comfy_client.unload_lead_llms()   # Flux needs the whole card
    try:
        for attempt in range(6):    # scenes hide faces or invent extra people; reroll until
            wf = comfy_client.build_workflow(   # Require exactly `n` faces (hard gate)
                scene_prompt,
                negative="deformed, extra limbs, blurry, collage, split image, "
                         "extra people, additional person, crowd, five people, six people, "
                         "strangers, background people, grandparents, elderly, "
                         "extended family, group of adults, office, boardroom, "
                         "afro, dreadlocks, tightly coiled hair, kinky hair texture, "
                         "curly hair, frizzy hair, coily hair",
                quality=scene_quality, size="landscape", seed=seed_base + 977 * attempt)
            r = comfy_client.generate(wf)
            total_s += r["seconds"]
            scene = Image.open(BytesIO(comfy_client.fetch(r["filename"]))).convert("RGB")
            faces = detect_faces_ex(scene)
            ages = [f["age"] for f in faces]
            # composition gate, not just count: v8 rolled 4 faces aged [49,42,27,7] — three
            # adults and one child — and a toddler identity landed on the 49-year-old. The
            # two OLDEST are the parents; everyone else must plausibly be a child (<=20,
            # genderage noise margin).
            ages_ok = (all(a is not None for a in ages)
                       and all(a <= CHILD_MAX_AGE for a in sorted(ages, reverse=True)[2:]))
            sexes_ok = True
            if ref_sexes and all(f.get("sex") for f in faces):
                oldest = sorted(faces, key=lambda f: f["age"] or 0, reverse=True)[:2]
                sexes_ok = sorted(f["sex"] for f in oldest) == ref_sexes
            notes.log(f"IMG_PIPE scene attempt {attempt + 1}: {len(faces)} faces "
                      f"(need {n}) ages={ages} ages_ok={ages_ok} sexes_ok={sexes_ok} "
                      f"q={scene_quality} s={r['seconds']}")
            if len(faces) == n and ages_ok and sexes_ok:
                break
    finally:
        if scene_quality == "best":
            comfy_client.free_vram()            # back to SDXL for the identity passes
    if not (len(faces) == n and ages_ok and sexes_ok):
        # HARD exact-count gate: better an honest retry-message than four expensive identity
        # passes spent decorating a five-person miscast
        raise comfy_client.ComfyError(
            f"no scene with the right family shape after 6 tries "
            f"(last: {len(faces)} faces, need {n} with {n - 2} children)")
    # identity assignment by MEASURED resemblance (FLAME shape distance), not position
    ref_paths = [d / up for up in ups[:2]] + [img_dir / c["name"] for c in children]
    subjects = ["parent", "parent"] + ["young child"] * len(children)
    matched = match_faces(scene, faces, ref_paths, ref_sexes=ref_sexes_ordered)
    refs = [(box, ref_path, subjects[j]) for box, ref_path, j in matched]
    safe = comfy_client._safe(user)
    for i, (box, ref_path, subject) in enumerate(refs):
        region_img, region = _face_region(scene, box)
        rel_box = _box_in_region(box, region, scene.size)
        scene_name = comfy_client.upload_to_comfy(
            f"{safe}-family-scene-{i}.png",
            depth_png_bytes(region_img.resize((FACE_DETAIL_RES, FACE_DETAIL_RES))))
        mask = face_mask_image((FACE_DETAIL_RES, FACE_DETAIL_RES), rel_box)
        mask_name = comfy_client.upload_to_comfy(f"{safe}-family-mask-{i}.png",
                                                 depth_png_bytes(mask))
        ref_name = comfy_client.upload_to_comfy(f"{safe}-family-ref-{i}.png",
                                                tight_ref_bytes(ref_path))
        use_swap = swap_available()
        wf = build_face_inpaint_workflow(scene_name, mask_name, ref_name,
                                         seed=seed_base * 3 + i, subject=subject,
                                         swap_source=use_swap)
        if use_swap:
            # identity-guarded finisher: fetch BOTH post-fuse (7) and post-swap (56),
            # keep whichever scores higher arcface vs the ref (fuse erodes weak binds; fix ⑧)
            r = comfy_client.generate(wf, want_nodes=["7", "56"])
            imgs = r["images"]
            fused = Image.open(BytesIO(comfy_client.fetch(
                imgs.get("7") or imgs["56"]))).convert("RGB")
            if "7" in imgs and "56" in imgs:
                swapped = Image.open(BytesIO(comfy_client.fetch(imgs["56"]))).convert("RGB")
                detailed = _guarded_finish(fused, swapped, ref_path, i)
            else:
                detailed = fused
        else:
            r = comfy_client.generate(wf)
            detailed = Image.open(BytesIO(comfy_client.fetch(r["filename"]))).convert("RGB")
        total_s += r["seconds"]
        notes.log(f"IMG_PIPE inpaint {i + 1}/{len(refs)}: {subject} detail@{FACE_DETAIL_RES} "
                  f"ref={pathlib.Path(ref_path).name[:40]} s={r['seconds']}")
        l, t, rr, b = region
        patch = detailed.resize((rr - l, b - t))
        feather = mask.convert("L").resize((rr - l, b - t))
        scene.paste(patch, (l, t), feather)
    out = comfy_client.save_image(user, "family portrait", depth_png_bytes(scene))
    return {"name": out, "seconds": round(total_s, 1), "children": children}


def _structure_projections(image_path):
    """Stage-2 cross-check: cached VLM read of the 4 projection features for one parent photo ->
    {feature: ordinal}. Per-person cache keyed on (path, mtime); fail-soft {} (mesh-only projections)."""
    if _structure_vlm is None:
        return {}
    try:
        import hashlib, base64, json as _json, httpx
        p = pathlib.Path(image_path)
        key = hashlib.md5(f"{p.resolve()}:{p.stat().st_mtime_ns}".encode()).hexdigest()[:16]
        cache = pathlib.Path(config.STATE_DIR) / "structure-vlm-cache" / f"{key}.json"
        if cache.exists():
            return {k: int(v) for k, v in _json.loads(cache.read_text()).items()}
        img_b64 = base64.b64encode(p.read_bytes()).decode()
        with __import__("ollama_client").track(config.VLM_HOST, config.VLM_MODEL, purpose="identity structure vlm read", user="system"):
          r = __import__("ollama_client").post_supervised(f"{config.VLM_HOST}/api/chat", timeout=180,
                       json={"model": config.VLM_MODEL, "stream": False, "think": False,
                             "keep_alive": config.KEEP_ALIVE_OVERRIDES.get(config.VLM_HOST),
                             "messages": [{"role": "user", "content": _structure_vlm.PROMPT, "images": [img_b64]}],
                             "options": {"num_ctx": config.ctx_for(config.VLM_HOST, config.VLM_MODEL, 4096)}})
        r.raise_for_status()
        ords = _structure_vlm.parse_projection_reply(r.json()["message"]["content"])
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(_json.dumps(ords))
        notes.log(f"IMG_PIPE structure vlm {p.name[:40]}: {ords}")
        return ords
    except Exception as exc:
        notes.log(f"IMG_PIPE structure vlm unavailable ({type(exc).__name__})")
        return {}


def _parent_fingerprints(meshes, ups, d, use_vlm):
    """Both parents' structural fingerprints (mesh in-plane + optional VLM projection cross-check).
    None if the subsystem is absent or measurement fails (fail-soft -> plain mosaic inheritance)."""
    if _structure is None:
        return None
    try:
        import flame
        regions = flame._regions()
        fps = []
        for mesh, up in zip(meshes[:2], ups[:2]):
            fp = _structure.measure_structure(mesh, regions)
            if use_vlm and _structure_vlm is not None:
                fp = _structure_vlm.fuse_projection(fp, _structure_projections(d / up))
            fps.append(fp)
        return fps
    except Exception as exc:
        notes.log(f"IMG_PIPE structure fingerprints skipped ({type(exc).__name__})")
        return None


def simulate_children(user, count=2, seed_base=None, prompt=None, engine=None, effort=None,
                      routing=None):
    """Simulate children by blending the user's two most recent uploads.

    Args:
        user: The chat user whose uploads serve as parent images.
        count: Number of child simulations to generate (default 2).
        seed_base: Base seed for reproducibility.
        prompt: Optional text prompt.
        engine: Estimator selection strategy. None=auto (routing determines estimator based on assets,
            degrading neural->flame->lmk). Specific values:
            - 'quick_8b': Closed-form coordinate fit.
            - 'force_27b'/'auto': MICA neural fitter.
            - 'baseline': Pure appearance blending without geometry (control case).
        effort: Genetics depth level. Options: 'normal' (static odds + tier-1),
            'think_hard' or 'absurd' (adds researched odds + tier-2 VLM reads).
        routing: Routing axis configuration.

    Returns:
        List of dicts with keys: name, picks, seconds, engine.

    Raises:
        NoFaceError: If face detection fails.
        ValueError: If fewer than two uploads are found or other input validation fails.
    """
    import flame
    effort = effort or "normal"
    ups = latest_uploads(user, 2)
    if len(ups) < 2:
        raise ValueError("need two uploaded photos (one of each parent)")
    d = comfy_client.uploads_dir(user)
    raw = [landmarks(d / up) for up in ups[:2]]     # FLAME fit wants the raw frame
    if engine is None:
        want_neural = routing != "quick_8b"     # auto/heavy = best instrument; quick = coordinates
        if want_neural and flame.neural_available():
            engine = "neural"
        elif flame.available():
            engine = "flame"
        else:
            engine = "lmk"
    notes.log(f"IMG_PIPE children: engine={engine} count={count} effort={effort} "
              f"routing={routing or 'auto'} user={user!r}")
    if engine == "neural":       # rung 3: MICA reads the whole photo, not 478 points
        meshes = [flame.decode(flame.betas_from_aligned(_aligned_face(d / up)))
                  for up in ups[:2]]
    elif engine == "flame":
        meshes = [flame.decode(flame.fit_betas(r)) for r in raw]
    elif engine == "lmk":
        pts = [normalize(r) for r in raw]
    # FACE-ONLY parent refs for the child IPAdapter (real wardrobe fix, round 6): keeps the
    # face-appearance blend, drops the clothing-color bleed so KID_OUTFITS determines the outfit
    refs = [comfy_client.upload_to_comfy(f"{comfy_client._safe(user)}-{up}",
                                         debeard_ref_bytes(d / up)) for up in ups[:2]]
    # parent sexes (antelopev2 genderage) for gender-conditional IPAdapter weighting: boost the
    # SAME-gender parent per rolled kid so boys read boyish (the equal dual-parent face-blend
    # averaged feminine — round 7). Fail-open to balanced weights when undetectable.
    psex = []
    for up in ups[:2]:
        try:
            det = detect_faces_ex(Image.open(d / up).convert("RGB"))
            psex.append(det[0].get("sex") if det else None)
        except Exception:
            psex.append(None)
    if seed_base is None:
        import time
        seed_base = int(time.time()) % (2 ** 28)
    traits, vlm, deep = None, [{}, {}], effort in ("think_hard", "absurd")
    struct_fp = None
    if engine in ("flame", "neural"):
        import genetics
        traits = [genetics.measure_traits(d / up, r) for up, r in zip(ups[:2], raw)]
        notes.log(f"IMG_PIPE genetics: parentA={traits[0]} parentB={traits[1]}")
        if deep:                    # tier-2: the VLM looks at each parent once (fail-soft {})
            vlm = [genetics.vlm_traits(d / up) for up in ups[:2]]
        # Stages 2-4: measure both parents' bone-structure fingerprints ONCE (VLM projection
        # cross-check only on deep effort, cached per person). Feeds structural inheritance in the loop.
        struct_fp = _parent_fingerprints(meshes, ups, d, use_vlm=deep)
    results = []
    for i in range(max(1, min(4, count))):
        depth_name, picks, hand, story = None, {}, None, []
        # deal the hand FIRST so the kid's rolled GENDER can shape the bone geometry (Stage 1):
        # gender dimorphism lives in the mesh now, not in over-weighting a bearded adult face.
        if traits is not None:
            import genetics
            hand, story = genetics.deal_hand(traits[0], traits[1], seed=seed_base * 13 + i,
                                             researched=deep, vlm_a=vlm[0], vlm_b=vlm[1])
        gender = (hand or {}).get("gender")
        if engine in ("flame", "neural"):
            child, picks = flame.mosaic_mesh(meshes[0], meshes[1], seed=seed_base + i)
            child = flame.toddlerize_mesh(child)
            if _dimorphism is not None and gender:
                try:
                    child = _dimorphism.apply_gender_dimorphism(child, flame._regions(), gender)
                    notes.log(f"IMG_PIPE mesh: gender dimorphism ({gender}) on bones")
                except Exception as _e:
                    notes.log(f"IMG_PIPE mesh: dimorphism skipped ({type(_e).__name__})")
            # Stages 2-4: deal this kid's structural hand from the parent fingerprints and nudge the
            # mesh toward it (polygenic, subtle, measured-parents-only — no ancestry priors). Fail-soft.
            if _structure_apply is not None and struct_fp is not None:
                try:
                    _rg = flame._regions()
                    hand_s, _ = _inheritance.inherit_structure(struct_fp[0], struct_fp[1],
                                                               seed=seed_base * 29 + i)
                    child = _structure_apply.apply_structural_hand(
                        child, _rg, hand_s, _structure.measure_structure(child, _rg))
                    notes.log("IMG_PIPE mesh: structural inheritance applied")
                except Exception as _e:
                    notes.log(f"IMG_PIPE mesh: structural inheritance skipped ({type(_e).__name__})")
            depth = flame.render_depth(child)
        elif engine == "lmk":
            child, picks = mosaic_child(pts[0], pts[1], seed=seed_base + i)
            depth = render_depth(toddlerize(child))
        if engine in ("flame", "lmk", "neural"):
            depth_name = comfy_client.upload_to_comfy(
                f"{comfy_client._safe(user)}-child-depth-{i}.png",
                depth_png_bytes(torso_hint(depth)))
        child_prompt, kid_neg, wa, wb = prompt, None, None, None
        if traits is not None:
            import genetics
            base = prompt or DEFAULT_CHILD_PROMPT
            # sex LEADS as the subject noun — "a young child with boy, brown eyes" rendered
            # every boy girl-ish (the noun wins the composition, the trait list doesn't)
            subject = hand.get("gender") or "child"
            lead = genetics.hand_prompt(hand, include_gender=False,
                                        short_hair=(subject == "boy")).lstrip(", ")
            # "toddler … about three years old" — bare "young child" rendered 6-9yo
            outfit = KID_OUTFITS[i % len(KID_OUTFITS)]
            # SDXL/Flux has a strong feminine prior for toddlers; bare "boy" prompts render
            # girl-ish. We apply explicit gender lean + opposite-gender negation to counter bias.
            if subject == "boy":
                subj = "a little boy, (boyish masculine face:1.25), short cropped boy's haircut"
                kid_neg = (CHILD_NEGATIVE + ", girl, feminine, feminine face, long hair, dress, "
                           "hair bow, pigtails, earrings, goatee, soul patch, chin hair, "
                           "hair on chin, stubble")   # the masculine push sneaks a chin tuft in
            elif subject == "girl":
                subj = "a little girl, (girlish feminine face:1.15)"
                kid_neg = CHILD_NEGATIVE + ", boy, boyish, masculine face, buzz cut"
            else:
                subj, kid_neg = "a toddler child", None
            child_prompt = (f"{subj}, about three years old, with {lead}, "
                            f"wearing {outfit}, {base}")
            # boost the same-gender parent's face (0.55 vs 0.3) when both sexes read + differ —
            # the visual lever the prompt alone couldn't pull (round 7 boys stayed girl-ish)
            if subject in ("boy", "girl") and psex[0] and psex[1] and psex[0] != psex[1]:
                want = "M" if subject == "boy" else "F"
                wa = SAME_GENDER_FACE_WEIGHT if psex[0] == want else OPP_GENDER_FACE_WEIGHT
                wb = SAME_GENDER_FACE_WEIGHT if psex[1] == want else OPP_GENDER_FACE_WEIGHT
            notes.log(f"IMG_PIPE genetics child {i + 1}: " + " | ".join(story))
        wf = build_child_workflow(depth_name, refs[0], refs[1],
                                  seed=seed_base * 7 + i, prompt=child_prompt,
                                  use_depth=depth_name is not None, negative=kid_neg,
                                  weight_a=wa, weight_b=wb)
        r = comfy_client.generate(wf)
        data = comfy_client.fetch(r["filename"])
        from io import BytesIO
        if len(detect_faces_ex(Image.open(BytesIO(data)).convert("RGB"))) != 1:
            # uniparent guard: any EXTRA face is an unconstrained 50/50 parent blend
            # (both IPAdapter refs apply to the whole canvas) — one reroll kills it
            notes.log(f"IMG_PIPE child {i + 1}: extra/no face -> reroll")
            wf = build_child_workflow(depth_name, refs[0], refs[1],
                                      seed=seed_base * 7 + i + 1013, prompt=child_prompt,
                                      use_depth=depth_name is not None, negative=kid_neg,
                                      weight_a=wa, weight_b=wb)
            r = comfy_client.generate(wf)
            data = comfy_client.fetch(r["filename"])
        name = comfy_client.save_image(user, f"simulated child {i + 1}", data)
        results.append({"name": name, "picks": picks, "seconds": r["seconds"],
                        "engine": engine, "genetics": hand, "story": story})
    return results
