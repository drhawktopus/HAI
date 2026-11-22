import json
import pathlib
import random

import numpy as np
from PIL import Image

import config
import notes

# MediaPipe iris landmark indices (refine_landmarks=True adds 468-477: right 468-472, left 473-477)
_IRIS = [468, 469, 470, 471, 472, 473, 474, 475, 476, 477]
_CHEEKS = [50, 280, 205, 425]          # malar patches, usually shadow-free
_FOREHEAD_TOP = 10                     # topmost forehead landmark; hair sampled above it

# (name, classifier) — classify a mean RGB into a discrete bucket
EYE_COLORS = ["brown", "hazel/green", "blue/gray"]
HAIR_COLORS = ["black", "dark brown", "light brown", "blonde", "red", "gray"]
# Ordered loose->tight for dominance inheritance logic.
HAIR_TEXTURES = ["straight", "wavy", "curly", "coily"]
# Prompt phrasing per texture (what the renderer must actually draw).
_TEXTURE_PROMPT = {"straight": "straight hair", "wavy": "wavy hair",
                   "curly": "curly hair", "coily": "tightly coiled afro-textured hair"}

# cartoon dominance: P(child shows trait) given parents' buckets. Brown-eye dominance and
# dark-hair dominance, in spirit of the classic two-allele chart.
_EYE_DOMINANCE = {("brown", "brown"): [("brown", 0.9), ("hazel/green", 0.07), ("blue/gray", 0.03)],
                  ("brown", "hazel/green"): [("brown", 0.6), ("hazel/green", 0.33), ("blue/gray", 0.07)],
                  ("brown", "blue/gray"): [("brown", 0.6), ("hazel/green", 0.15), ("blue/gray", 0.25)],
                  ("hazel/green", "hazel/green"): [("hazel/green", 0.66), ("blue/gray", 0.17), ("brown", 0.17)],
                  ("hazel/green", "blue/gray"): [("hazel/green", 0.45), ("blue/gray", 0.5), ("brown", 0.05)],
                  ("blue/gray", "blue/gray"): [("blue/gray", 0.94), ("hazel/green", 0.05), ("brown", 0.01)]}

# Tier-2 traits rely on a VLM (config.VLM_MODEL/@config.VLM_HOST) for binary phenotypes
# like epicanthic folds that landmark-based analysis cannot detect.
# Behavior is fail-soft: if the VLM service is unavailable, Tier-2 rolls are skipped
# while Tier-1 trait generation proceeds normally.
TIER2_TRAITS = ("epicanthic_fold", "dimples", "cleft_chin", "widows_peak",
                "gap_teeth", "ear_protrusion", "beauty_mark")
_TIER2_PROMPT_PHRASE = {"epicanthic_fold": "epicanthic eye folds",
                        "dimples": "cheek dimples",
                        "cleft_chin": "a cleft chin",
                        "widows_peak": "a widow's peak hairline",
                        "gap_teeth": "a small gap between the front teeth",
                        "ear_protrusion": "slightly protruding ears",
                        "beauty_mark": "a small beauty mark"}
# cartoon dominance: P(child shows it) when (both parents, one parent, neither parent) show it.
# Fold/dimples/cleft/peak are the classically "dominant-ish" set; beauty marks barely inherit.
_TIER2_DOMINANCE = {"epicanthic_fold": (0.90, 0.60, 0.03),
                    "dimples": (0.80, 0.50, 0.05),
                    "cleft_chin": (0.75, 0.45, 0.04),
                    "widows_peak": (0.80, 0.50, 0.05),
                    "gap_teeth": (0.60, 0.30, 0.05),
                    "ear_protrusion": (0.70, 0.40, 0.05),
                    "beauty_mark": (0.30, 0.30, 0.05)}


def _mean_rgb(img_arr, points, radius=3):
    H, W = img_arr.shape[:2]
    vals = []
    for x, y in points:
        xi, yi = int(x * W), int(y * H)
        patch = img_arr[max(0, yi - radius):yi + radius, max(0, xi - radius):xi + radius]
        if patch.size:
            vals.append(patch.reshape(-1, 3).mean(axis=0))
    return np.mean(vals, axis=0) if vals else np.array([128.0, 128.0, 128.0])


def _classify_eye(rgb):
    r, g, b = rgb
    lum = (r + g + b) / 3
    if b > r * 1.08 and lum > 70:
        return "blue/gray"
    if g > r * 0.92 and lum > 60 and r < 120:
        return "hazel/green"
    return "brown"


def _classify_hair(rgb):
    r, g, b = rgb
    lum = (r + g + b) / 3
    if lum < 60:
        return "black"
    if lum < 105:
        return "dark brown"
    if r > 140 and g > 110 and b < 110 and lum < 175:
        return "blonde" if g > 130 else "red"
    if lum > 175 and abs(r - b) < 25:
        return "gray"
    return "light brown"


_SKIN_CUTS = ((90, "deep"), (125, "rich brown"), (160, "medium brown"),
              (190, "olive/tan"), (255, "fair"))


def _skin_tone_word(rgb):
    lum = (rgb[0] + rgb[1] + rgb[2]) / 3
    for cut, word in _SKIN_CUTS:
        if lum < cut:
            return word
    return "fair"


def _skin_lum(rgb):
    return (rgb[0] + rgb[1] + rgb[2]) / 3


def _lum_to_skin_word(lum):
    for cut, word in _SKIN_CUTS:
        if lum < cut:
            return word
    return "fair"


LIP_FULLNESS = ["thin", "medium", "full"]
NOSE_WIDTH = ["narrow", "medium", "wide"]
BROW_THICKNESS = ["thin", "medium", "thick"]


def _px(pts, i, arr):
    H, W = arr.shape[:2]
    return np.array([pts[i][0] * W, pts[i][1] * H])


def _measure_lips(pts, arr):
    mouth_w = np.linalg.norm(_px(pts, 61, arr) - _px(pts, 291, arr)) + 1e-6
    lip_h = (np.linalg.norm(_px(pts, 0, arr) - _px(pts, 13, arr))
             + np.linalg.norm(_px(pts, 14, arr) - _px(pts, 17, arr)))
    r = lip_h / mouth_w
    return "thin" if r < 0.28 else "medium" if r < 0.42 else "full"


def _measure_nose(pts, arr):
    face_w = np.linalg.norm(_px(pts, 234, arr) - _px(pts, 454, arr)) + 1e-6
    nose_w = np.linalg.norm(_px(pts, 48, arr) - _px(pts, 278, arr))
    r = nose_w / face_w
    return "narrow" if r < 0.24 else "medium" if r < 0.31 else "wide"


def _measure_brows(pts, arr):
    # darkness of the brow band vs the forehead skin just above it
    brow = _mean_rgb(arr, [(pts[i][0], pts[i][1]) for i in (105, 66, 334, 296)], radius=4)
    fx, fy = pts[_FOREHEAD_TOP][0], pts[_FOREHEAD_TOP][1]
    skin = _mean_rgb(arr, [(fx, fy + 0.02)], radius=4)
    contrast = (_skin_lum(skin) - _skin_lum(brow)) / (_skin_lum(skin) + 1e-6)
    return "thin" if contrast < 0.15 else "medium" if contrast < 0.32 else "thick"


def _hair_patch(arr, cx, cy, half):
    H, W = arr.shape[:2]
    xi, yi = int(cx * W), int(cy * H)
    return arr[max(0, yi - half):yi + half, max(0, xi - half):xi + half]


def _classify_texture(patch):
    """Estimate hair curl from a patch.
    Tighter curl yields higher, less-directional high-frequency edge energy;
    straight hair is smoother and directionally coherent."""
    if patch.size < 300:
        return "wavy"
    g = patch.mean(axis=2)
    # Laplacian-ish edge energy (curl density) normalized by local brightness
    lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1]
                 - g[1:-1, :-2] - g[1:-1, 2:])
    energy = lap.mean() / (g.mean() + 1e-6)
    if energy < 0.055:
        return "straight"
    if energy < 0.085:
        return "wavy"
    if energy < 0.125:
        return "curly"
    return "coily"


def measure_traits(image_path, landmarks_478):
    """Discrete phenotype estimates.

    The VLM consensus overrides pixel-sampled color fields when reachable,
    as pixel sampling is unreliable for real photos (shadows, artifacts).
    This function measures observed visual traits from the photo;
    user-stated heritage does not override observed colors."""
    arr = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float64)
    pts = landmarks_478
    eye_rgb = _mean_rgb(arr, [(pts[i][0], pts[i][1]) for i in _IRIS], radius=2)
    cheek_rgb = _mean_rgb(arr, [(pts[i][0], pts[i][1]) for i in _CHEEKS], radius=5)
    fx, fy = pts[_FOREHEAD_TOP][0], pts[_FOREHEAD_TOP][1]
    face_h = abs(pts[152][1] - fy)                      # chin-to-forehead extent
    hy = max(0.01, fy - 0.35 * face_h)
    hair_rgb = _mean_rgb(arr, [(fx, hy)], radius=8)
    texture = _classify_texture(_hair_patch(arr, fx, hy, int(0.09 * arr.shape[0])))
    traits = {"eyes": _classify_eye(eye_rgb),
              "hair": _classify_hair(hair_rgb),
              "hair_texture": texture,
              "skin": _skin_tone_word(cheek_rgb),
              "_skin_lum": _skin_lum(cheek_rgb),         # kept for the real between-parents blend
              "lips": _measure_lips(pts, arr),
              "nose": _measure_nose(pts, arr),
              "brows": _measure_brows(pts, arr)}
    traits.update(vlm_color_traits(image_path))          # fail-soft {} keeps pixel values
    return traits


_COLOR_CACHE = {}
_SKIN_LUM_BUCKETS = (("fair", 210), ("olive", 175), ("tan", 175), ("medium", 142),
                     ("rich", 107), ("deep", 75))


def _ensure_vlm_host():
    'The VLM host sleeps after 45 min; wake-on-LAN is required, or reads fail silently and degrade to pixel colors.'
    import httpx
    try:
        httpx.get(f"{config.VLM_HOST}/api/version", timeout=4)
        return True
    except Exception:
        pass
    try:
        import wol
        # Wake the host the VLM lives on: match the configured host, never an address literal.
        host = (config.VLM_HOST or "").rstrip("/")
        mac = config.device_mac(config.LEAD_BOX) if host == (config.HEAVY_HOST or "").rstrip("/") else config.role_mac("brain")
        wol.wake(mac)
    except Exception:
        return False
    import time
    for _ in range(12):
        time.sleep(5)
        try:
            httpx.get(f"{config.VLM_HOST}/api/version", timeout=4)
            return True
        except Exception:
            continue
    return False


def _vlm_color_read(path):
    """One VLM read of eyes/hair/texture/skin, mapped into our buckets. {} on any failure."""
    import base64
    import httpx
    out = {}
    try:
        img_b64 = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
        with __import__("ollama_client").track(config.VLM_HOST, config.VLM_MODEL, purpose="genetics vlm read", user="system"):
          r = __import__("ollama_client").post_supervised(f"{config.VLM_HOST}/api/chat", timeout=300,
                       json={"model": config.VLM_MODEL, "stream": False, "think": False,
                             "messages": [{"role": "user", "content":
                                 "What are this person's eye color, hair color, hair texture "
                                 "(straight/wavy/curly/coily), and skin tone (fair/olive-tan/"
                                 "medium brown/rich brown/deep)? Account for lighting and "
                                 "color grading. Reply ONLY compact JSON: "
                                 '{"eyes": "...", "hair": "...", "texture": "...", '
                                 '"skin": "..."}',
                                 "images": [img_b64]}],
                             "options": {"num_ctx": config.ctx_for(config.VLM_HOST, config.VLM_MODEL, 4096)}})
        r.raise_for_status()
        text = r.json()["message"]["content"]
        obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
        e = str(obj.get("eyes", "")).lower()
        if "blue" in e or "gray" in e or "grey" in e:
            out["eyes"] = "blue/gray"
        elif "green" in e or "hazel" in e:
            out["eyes"] = "hazel/green"
        elif "brown" in e or "amber" in e or "dark" in e:
            out["eyes"] = "brown"
        h = str(obj.get("hair", "")).lower()
        for bucket in ("black", "dark brown", "light brown", "blonde", "red", "gray"):
            if bucket in h:
                out["hair"] = bucket
                break
        else:
            if "auburn" in h or "ginger" in h or "strawberry" in h:
                out["hair"] = "red"
            elif "brown" in h or "brunette" in h:
                out["hair"] = "light brown"
        t = str(obj.get("texture", "")).lower()
        for tex in HAIR_TEXTURES:
            if tex in t:
                out["hair_texture"] = tex
                break
        sk = str(obj.get("skin", "")).lower()
        for k, lum in _SKIN_LUM_BUCKETS:
            if k in sk:
                out["skin"] = _lum_to_skin_word(lum)
                out["_skin_lum"] = lum
                break
    except Exception:
        pass
    return out


def has_facial_hair(image_path):
    """VLM check: does this person have a beard/mustache/goatee/stubble? True/False. Fail-soft to
    False (on uncertainty DON'T de-beard — never shave a clean face, and skip needless Kontext cost).
    Used to gate the child-ref de-beard: a bearded parent's beard imports onto the toddler kids."""
    import base64
    import httpx
    try:
        img_b64 = base64.b64encode(pathlib.Path(image_path).read_bytes()).decode()
        with __import__("ollama_client").track(config.VLM_HOST, config.VLM_MODEL, purpose="genetics vlm facial-hair read", user="system"):
          r = __import__("ollama_client").post_supervised(f"{config.VLM_HOST}/api/chat", timeout=120,
                       json={"model": config.VLM_MODEL, "stream": False, "think": False,
                             "messages": [{"role": "user", "content":
                                 "Does this person have any facial hair — a beard, mustache, goatee, "
                                 'or noticeable stubble? Reply ONLY compact JSON: {"facial_hair": '
                                 'true} or {"facial_hair": false}.',
                                 "images": [img_b64]}],
                             "options": {"num_ctx": config.ctx_for(config.VLM_HOST, config.VLM_MODEL, 2048)}})
        r.raise_for_status()
        text = r.json()["message"]["content"]
        return bool(json.loads(text[text.index("{"):text.rindex("}") + 1]).get("facial_hair"))
    except Exception:
        return False


def vlm_color_traits(image_path, k=3):
    """k independent VLM reads, majority vote per field — single reads flip-flopped on
    borderline features (a hazel eye can vote brown/hazel/brown across runs). Cached per
    (path, mtime); WoL-first; fail-soft {}."""
    p = pathlib.Path(image_path)
    try:
        key = (str(p), p.stat().st_mtime)
    except OSError:
        return {}
    if key in _COLOR_CACHE:
        return _COLOR_CACHE[key]
    if not _ensure_vlm_host():
        notes.log(f"IMG_PIPE genetics vlm-color: host unreachable, pixel values stand "
                  f"({p.name[:40]})")
        return {}
    votes = [v for v in (_vlm_color_read(p) for _ in range(max(1, k))) if v]
    out = {}
    if votes:
        from collections import Counter
        for field in set().union(*votes):
            vals = [str(v[field]) for v in votes if field in v]
            out[field] = Counter(vals).most_common(1)[0][0]
        if "_skin_lum" in out:
            out["_skin_lum"] = int(float(out["_skin_lum"]))
        _COLOR_CACHE[key] = out
        notes.log(f"IMG_PIPE genetics vlm-color {p.name[:40]}: {len(votes)} reads -> "
                  + ", ".join(f"{f}={out[f]}" for f in sorted(out) if f != "_skin_lum"))
    return out


_ODDS_CACHE = {}


def static_eye_odds(eye_a, eye_b):
    """The offline table lookup — Normal effort uses this directly (P3d: static odds), and the
    researched path falls back to it on any failure."""
    key = tuple(sorted([eye_a, eye_b]))
    return (_EYE_DOMINANCE.get(key) or _EYE_DOMINANCE.get((key[1], key[0]))
            or [(eye_a, 0.5), (eye_b, 0.5)])


def researched_eye_odds(eye_a, eye_b):
    """A probability distribution over child eye color for this parent pairing, derived by
    web-searching the inheritance genetics and having the brain model reason it into JSON.
    Cached per pairing. Falls back to the static dominance table on any failure."""
    key = tuple(sorted([eye_a, eye_b]))
    if key in _ODDS_CACHE:
        return _ODDS_CACHE[key]
    fallback = static_eye_odds(eye_a, eye_b)
    try:
        import websearch
        evidence = websearch.search(
            f"eye color inheritance genetics probability child parents "
            f"{eye_a} and {eye_b} eyes dominant recessive")[:2500]
    except Exception:
        evidence = "(web search unavailable; reason from standard polygenic inheritance)"
    prompt = (
        "You are a genetics educator. Using the reference notes and standard polygenic "
        "eye-color inheritance, give the probability a child will have each eye color when "
        f"one parent has {eye_a} eyes and the other has {eye_b} eyes.\n\n"
        f"REFERENCE NOTES:\n{evidence}\n\n"
        'Reply ONLY with compact JSON: {"brown": p, "hazel/green": p, "blue/gray": p} '
        "where the three probabilities sum to 1.0. No prose.")
    import httpx
    for host, model in ((config.BRAIN_HOST, config.BRAIN_MODEL),
                        (config.HEAVY_HOST, config.LEAD_BOX_BRAIN_MODEL)):
        try:
            import scheduler
            if scheduler.occupied_for(host, model):
                continue        # a factory order's card, or a live HAI job running another model there (B12)
        except Exception:
            pass
        try:
            import ollama_client
            with ollama_client.track(host, model, purpose="genetics traits", user="system"):
                r = ollama_client.post_supervised(
                    f"{host}/api/chat", timeout=90,
                    json={"model": model, "stream": False, "think": False,
                          "keep_alive": config.KEEP_ALIVE_OVERRIDES.get(host),
                          "messages": [{"role": "user", "content": prompt}],
                          "options": {"num_ctx": config.ctx_for(host, model, 4096)}})
            r.raise_for_status()
            text = r.json()["message"]["content"]
            obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
            dist = [(k, float(obj[k])) for k in EYE_COLORS if k in obj and obj[k] > 0]
            total = sum(p for _, p in dist)
            if dist and total > 0:
                table = sorted([(k, p / total) for k, p in dist], key=lambda t: -t[1])
                _ODDS_CACHE[key] = table
                notes.log(f"IMG_PIPE genetics researched {key} via {model}: "
                          f"{{{', '.join(f'{k}:{p:.2f}' for k, p in table)}}}")
                return table
        except Exception as exc:
            notes.log(f"IMG_PIPE genetics {model} unavailable ({type(exc).__name__})")
    _ODDS_CACHE[key] = fallback
    return fallback


def vlm_traits(image_path):
    """Tier-2: binary phenotypes READ by the vision model from one parent photo. Returns
    {trait: True/False} for traits it answered definitively; unknowns are omitted so the
    dealer stays silent on them. Fail-soft {} — tier-1 never depends on this."""
    import base64
    import httpx
    prompt = (
        "Look at this face photo. For each facial trait answer strictly yes, no, or unknown "
        "(unknown when the feature is hidden — e.g. teeth behind closed lips, ears under hair).\n"
        'Reply ONLY with compact JSON: {"epicanthic_fold": "...", "dimples": "...", '
        '"cleft_chin": "...", "widows_peak": "...", "gap_teeth": "...", '
        '"ear_protrusion": "...", "beauty_mark": "..."}. No prose.')
    try:
        img_b64 = base64.b64encode(pathlib.Path(image_path).read_bytes()).decode()
        import ollama_client
        with ollama_client.track(config.VLM_HOST, config.VLM_MODEL, purpose="genetics vision", user="system",
                                 kind="vlm"):
            r = ollama_client.post_supervised(
                f"{config.VLM_HOST}/api/chat", timeout=180,   # the fallback clock only (cold load ~100 s on the 3070)
                json={"model": config.VLM_MODEL, "stream": False, "think": False,
                      "keep_alive": config.KEEP_ALIVE_OVERRIDES.get(config.VLM_HOST),
                      "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
                      "options": {"num_ctx": config.ctx_for(config.VLM_HOST, config.VLM_MODEL, 4096)}})
        r.raise_for_status()
        text = r.json()["message"]["content"]
        obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
        out = {}
        for trait in TIER2_TRAITS:
            v = str(obj.get(trait, "unknown")).strip().lower()
            if v in ("yes", "true"):
                out[trait] = True
            elif v in ("no", "false"):
                out[trait] = False
        notes.log(f"IMG_PIPE genetics vlm {pathlib.Path(image_path).name[:40]}: "
                  + (", ".join(f"{k}={'y' if v else 'n'}" for k, v in out.items()) or "(all unknown)"))
        return out
    except Exception as exc:
        notes.log(f"IMG_PIPE genetics vlm unavailable ({type(exc).__name__})")
        return {}


def _roll(rng, table):
    x, acc = rng.random(), 0.0
    for value, p in table:
        acc += p
        if x <= acc:
            return value, p
    return table[-1][0], table[-1][1]


def deal_hand(traits_a, traits_b, seed, researched=True, vlm_a=None, vlm_b=None):
    """Roll one child's discrete trait hand from the parents' measured traits.
    researched=False keeps the roll fully offline (Normal effort per the P3d dial); vlm_a/vlm_b
    are each parent's tier-2 VLM readings ({} or None = skip those rolls entirely).
    Returns (hand dict, story lines)."""
    rng = random.Random(seed)
    story, hand = [], {}
    eye_table = (researched_eye_odds(traits_a["eyes"], traits_b["eyes"]) if researched
                 else static_eye_odds(traits_a["eyes"], traits_b["eyes"]))
    hand["eyes"], p = _roll(rng, eye_table)
    story.append(f"eyes: {hand['eyes']} ({int(p * 100)}% odds from "
                 f"{traits_a['eyes']} x {traits_b['eyes']})")
    # hair color: darker parent's color dominates 70/30
    order = sorted([traits_a["hair"], traits_b["hair"]],
                   key=lambda h: HAIR_COLORS.index(h) if h in HAIR_COLORS else 0)
    hand["hair"], p = _roll(rng, [(order[0], 0.7), (order[1], 0.3)])
    story.append(f"hair color: {hand['hair']} ({int(p * 100)}% odds)")
    # hair TEXTURE: tighter-curl ancestry expresses strongly; child never looser than
    # looser parent by more than one step.
    ta_t = HAIR_TEXTURES.index(traits_a["hair_texture"])
    tb_t = HAIR_TEXTURES.index(traits_b["hair_texture"])
    tight, loose = max(ta_t, tb_t), min(ta_t, tb_t)
    mid = HAIR_TEXTURES[min(3, (tight + loose + 1) // 2 + (1 if tight - loose >= 2 else 0))]
    tex_table = [(HAIR_TEXTURES[tight], 0.55), (mid, 0.35),
                 (HAIR_TEXTURES[max(loose, tight - 1)], 0.10)]
    hand["hair_texture"], p = _roll(rng, tex_table)
    story.append(f"hair texture: {hand['hair_texture']} ({int(p * 100)}% — "
                 f"{traits_a['hair_texture']} x {traits_b['hair_texture']})")
    # skin: a REAL between-parents blend (weighted toward one parent), not a coin-flip word
    la, lb = traits_a.get("_skin_lum", 128), traits_b.get("_skin_lum", 128)
    w = rng.uniform(0.30, 0.70)                          # where the child lands between them
    hand["skin"] = _lum_to_skin_word(la * w + lb * (1 - w))
    nearer = "first" if w >= 0.5 else "second"
    story.append(f"skin tone: {hand['skin']} (blend, closer to {nearer} parent)")

    # distinct from parent 'sex'/'M'/'F' (insightface); uses 'gender' project-wide
    hand["gender"], _ = _roll(rng, [("boy", 0.5), ("girl", 0.5)])
    story.append(f"gender: {hand['gender']} (50/50)")

    # tier-1 landmark features: child lands between the parents, biased to one (60/40)
    for trait, scale in (("lips", LIP_FULLNESS), ("nose", NOSE_WIDTH), ("brows", BROW_THICKNESS)):
        va, vb = scale.index(traits_a[trait]), scale.index(traits_b[trait])
        lo, hi = min(va, vb), max(va, vb)
        favor_hi = rng.random() < 0.5
        mid = hi if favor_hi else lo
        table = [(scale[mid], 0.6), (scale[(lo + hi + 1) // 2], 0.25),
                 (scale[hi if not favor_hi else lo], 0.15)]
        hand[trait], p = _roll(rng, table)
    story.append(f"features: {hand['lips']} lips, {hand['nose']} nose, {hand['brows']} brows")

    # tier-2 (VLM-read) binary traits: roll only what at least one parent was READ for —
    # a trait unknown for BOTH parents stays out of the hand (silence beats a coin flip)
    va, vb = vlm_a or {}, vlm_b or {}
    shown = []
    for trait in TIER2_TRAITS:
        if trait not in va and trait not in vb:
            continue
        n_yes = int(bool(va.get(trait))) + int(bool(vb.get(trait)))
        p_show = _TIER2_DOMINANCE[trait][2 - n_yes]
        hand[trait] = rng.random() < p_show
        if hand[trait]:
            shown.append(f"{trait.replace('_', ' ')} ({int(p_show * 100)}% odds)")
    if shown:
        story.append("inherited features: " + ", ".join(shown))
    return hand, story


def hand_prompt(hand, include_gender=True, short_hair=False):
    """Construct a prompt fragment for facial features from `hand`.

    Args:
        hand: Dict containing hair texture, color, eyes, skin, lips, etc.
        include_gender: If True (default), includes gender in the trait list.
            If False, caller should handle gender as subject noun to avoid
            SDXL's feminine-toddler prior on buried traits.
        short_hair: If True, prefixes hair description with 'short' to prevent
            renderer defaults for specific demographics."""
    tex = _TEXTURE_PROMPT.get(hand.get("hair_texture", ""), "")
    hair = (f"{hand['hair']} {tex}" if tex else f"{hand['hair']} hair")
    if short_hair:
        hair = "short " + hair
    bits = [hand.get("gender", "") if include_gender else "",
            f"{hand['eyes']} eyes", hair, f"{hand['skin']} skin tone"]
    if hand.get("lips"):
        bits.append(f"{hand['lips']} lips")
    if hand.get("nose"):
        bits.append(f"{hand['nose']} nose")
    if hand.get("brows"):
        bits.append(f"{hand['brows']} eyebrows")
    for trait in TIER2_TRAITS:      # rolled-true tier-2 traits render; absence stays unprompted
        if hand.get(trait):
            bits.append(_TIER2_PROMPT_PHRASE[trait])
    return ", " + ", ".join(b for b in bits if b)
