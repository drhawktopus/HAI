"""Mechanical framing: determine which body parts are inside the image frame via arithmetic.

VLMs cannot reliably judge frame boundaries. This module uses torchvision's Keypoint R-CNN
(regressing 17 COCO body keypoints) to check whether keypoints fall within image bounds.
This approach is deterministic and reproducible, unlike prompt-based methods which fail
to accurately assess framing.

Note: The model is not content-aware (no text head/classifier). It emits coordinates and
confidences only. Weights are from COCO; how it handles figures COCO under-represents is an
empirical question requiring validation against eye-verified test cases.

Fail-closed: If person detection confidence is low, no framing data is recorded rather
than guessing. Omitted data is preferred over invented/poisoned data."""
import json
import os
import sys

# COCO keypoint indices, in the order the model emits them.
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

HEAD = (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)
HANDS = (L_WRIST, R_WRIST)
KNEES = (L_KNEE, R_KNEE)
FEET = (L_ANKLE, R_ANKLE)
HIPS = (L_HIP, R_HIP)

# LIMB GROUPS, for the caption's "describe the limbs or say they are not in shot" rule. Deliberately
# coarser than the joint pairs above: a caption says "her legs" or "her arms", never "her knees but
# not her ankles", so the group is what the prose has to answer for.
ARMS = (L_ELBOW, R_ELBOW, L_WRIST, R_WRIST)
LEGS = (L_KNEE, R_KNEE, L_ANKLE, R_ANKLE)

KP_SCORE_MIN = 2.0        # torchvision keypoint logits; >2 is a confidently-placed joint
# The head bar is HIGHER than the limb bar on purpose: a genuinely visible face scores in double
# digits (12.79 on a real one), while a head the model is merely guessing at hovers around zero or
# goes negative (-0.54 on a head that is entirely outside the frame). 2.0 let both through.
HEAD_SCORE_MIN = 3.0
PERSON_SCORE_MIN = 0.85   # box score for "there is definitely a person here"

# Use "head to toe" instead of "full figure" because the latter collides with
# `full-figured`, a BUILD corpus term meaning heavier body type. Unlike other terms
# that are disambiguated by their noun suffixes (e.g., "average build"), crop terms
# stand alone, so ambiguity causes parsing errors.
CROP_FULL, CROP_KNEE, CROP_WAIST, CROP_TORSO = (
    "head to toe", "knee up", "waist up", "torso only")

# The TOP of the frame, reported separately from the bottom extent. Silent when the head is
# comfortably in shot -- only what varies gets named.
HEAD_PARTLY, HEAD_OUT = "head partly cropped", "head out of frame"

_MODEL = [None]


def _model(device):
    """Load once per process. Weights download on first use (~226 MB) into the torch hub cache."""
    if _MODEL[0] is None:
        import torch
        from torchvision.models.detection import (keypointrcnn_resnet50_fpn,
                                                  KeypointRCNN_ResNet50_FPN_Weights)
        m = keypointrcnn_resnet50_fpn(weights=KeypointRCNN_ResNet50_FPN_Weights.DEFAULT)
        m.eval().to(device)
        _MODEL[0] = m
    return _MODEL[0]


def _inside(x, y, w, h):
    return 0 <= x < w and 0 <= y < h


def _group_state(kps, scores, idxs, w, h):
    """'both' / 'one' / 'neither' for a pair, or yes/partly/no for the head group."""
    n = 0
    for i in idxs:
        if scores[i] >= KP_SCORE_MIN and _inside(kps[i][0], kps[i][1], w, h):
            n += 1
    return n


def framing_for_image(path, device="cpu"):
    """{head_in_frame, hands_in_frame, knees_in_frame, feet_in_frame, crop, person_score} or None.

    None means no confident person -- and NOTHING is recorded for that image. A framing tag lands
    in every caption of the corpus, so a wrong one is a named constant that is false; an absent one
    is merely absent."""
    import torch
    from PIL import Image
    import torchvision.transforms.functional as TF

    im = Image.open(path)
    if im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    t = TF.to_tensor(im).to(device)
    with torch.no_grad():
        out = _model(device)([t])[0]
    if not len(out["scores"]) or float(out["scores"][0]) < PERSON_SCORE_MIN:
        return None
    kps = out["keypoints"][0].cpu().numpy()
    ksc = out["keypoints_scores"][0].cpu().numpy()

    hands_n = _group_state(kps, ksc, HANDS, w, h)
    knees_n = _group_state(kps, ksc, KNEES, w, h)
    feet_n = _group_state(kps, ksc, FEET, w, h)

    # Keypoint R-CNN clamps out-of-frame joints to visible bounds.
    # A naive in-bounds check misses cropped heads. We require high confidence
    # (real faces score high; guesses hover near zero) and distance from top edge
    # (heads crammed into the first 2% are likely cut).
    strong = [i for i in HEAD
              if ksc[i] >= HEAD_SCORE_MIN and _inside(kps[i][0], kps[i][1], w, h)]
    edge = max(4.0, 0.02 * h)
    if not strong:
        head = "no"
    elif min(kps[i][1] for i in strong) <= edge or float(out["boxes"][0][1]) <= 1.0:
        head = "partly"
    else:
        head = "yes"
    pair = lambda n: "both" if n >= 2 else ("one" if n == 1 else "neither")

    # `crop` describes BOTTOM extent only. `head_crop` is a separate tag.
    # A single term cannot encode both (e.g., "waist up" is silent on head status).
    # Emitting two tags keeps each unambiguous.
    if feet_n >= 1:
        crop = CROP_FULL
    elif knees_n >= 1:
        crop = CROP_KNEE
    else:
        crop = CROP_WAIST
    if head == "no":
        crop = CROP_TORSO          # nothing above the shoulders: the bottom extent stops mattering

    head_crop = {"yes": None, "partly": HEAD_PARTLY, "no": HEAD_OUT}[head]

    # Caption rule: describe limbs present in the crop; explicitly state absent limbs.
    # Do not force limb descriptions for crops where they are out of frame,
    # as this causes the VLM to hallucinate anatomy.
    arms_n = _group_state(kps, ksc, ARMS, w, h)
    legs_n = _group_state(kps, ksc, LEGS, w, h)
    # R-CNN predicts coordinates for all joints regardless of visibility, causing phantom
    # knees in torso crops. `crop` uses a stricter confidence threshold than keypoint
    # presence checks, so it overrides raw keypoint counts to prevent false leg detection.
    if crop == CROP_TORSO or (crop == CROP_WAIST and knees_n == 0 and feet_n == 0):
        legs_n = 0
    limbs_in = [g for g, n in (("arms", arms_n), ("legs", legs_n)) if n >= 1]
    limbs_out = [g for g, n in (("arms", arms_n), ("legs", legs_n)) if n == 0]

    # Inversion detection requires all endpoints (head, shoulders, feet) to be confidently detected.
    # Logic: head AND shoulders below hips, AND feet above hips.
    # This geometry discriminates true inverted photos from kneeling/forward-lean poses,
    # where the upper body may dip but feet remain planted. Returns None if any joint group
    # is missing or low-confidence, to avoid false positives in downstream prompts.
    inverted = None
    if head == "yes":
        head_pts = [kps[i][1] for i in HEAD if ksc[i] >= HEAD_SCORE_MIN]
        hip_pts = [kps[i][1] for i in HIPS if ksc[i] >= KP_SCORE_MIN]
        sh_pts = [kps[i][1] for i in (L_SHOULDER, R_SHOULDER) if ksc[i] >= KP_SCORE_MIN]
        foot_pts = [kps[i][1] for i in FEET if ksc[i] >= KP_SCORE_MIN]
        if head_pts and hip_pts and sh_pts and foot_pts:
            top_below = min(head_pts) > max(hip_pts) and min(sh_pts) > max(hip_pts)
            feet_above = max(foot_pts) < min(hip_pts)      # ankles ABOVE the hips: a real flip
            inverted = bool(top_below and feet_above)

    # Caption-ready tags, in the order a dense caption should read them. `None` entries drop out,
    # so an intact head simply says nothing -- only naming what VARIES.
    tags = [t for t in (crop, head_crop) if t]

    return {"head_in_frame": head, "hands_in_frame": pair(hands_n),
            "knees_in_frame": pair(knees_n), "feet_in_frame": pair(feet_n),
            "arms_in_frame": arms_n >= 1, "legs_in_frame": legs_n >= 1,
            "limbs_in": limbs_in, "limbs_out": limbs_out, "inverted": inverted,
            "crop": crop, "head_crop": head_crop, "framing_tags": tags,
            "person_score": round(float(out["scores"][0]), 3)}


def main(argv):
    """CLI: p20_framing.py <folder> [--cpu]  ->  JSON on stdout, one object keyed by basename."""
    if not argv:
        print("usage: p20_framing.py <folder> [--cpu]", file=sys.stderr)
        return 2
    import glob
    device = "cpu" if "--cpu" in argv else None
    if device is None:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    src = argv[0]
    exts = (".jpg", ".jpeg", ".png", ".webp")
    out = {}
    files = sorted(p for p in glob.glob(os.path.join(src, "**", "*.*"), recursive=True)
                   if os.path.splitext(p)[1].lower() in exts)
    print("device=%s images=%d" % (device, len(files)), file=sys.stderr)
    sys.stderr.flush()
    # Progress monitoring via output lines, not timers. One line per image,
    # flushed immediately, so the caller can detect stalls without buffering.
    for i, p in enumerate(files, 1):
        print("PROGRESS %d/%d %s" % (i, len(files), os.path.basename(p)), file=sys.stderr)
        sys.stderr.flush()
        try:
            out[os.path.basename(p)] = framing_for_image(p, device)
        except Exception as e:
            out[os.path.basename(p)] = {"error": repr(e)[:160]}
    print("PROGRESS done %d/%d" % (len(files), len(files)), file=sys.stderr)
    sys.stderr.flush()
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
