"""Stage 2: VLM cross-check for PROJECTION features.

Mesh is blind to z-projection from single frontal photos, so we query the VLM
for exactly these four features (cheek, brow, chin, nasal) via shading analysis.
This module contains only domain logic (prompting, parsing, fusion); it makes NO
network calls. The caller handles VLM orchestration and passes raw reply text
to parse_projection_reply().

Fusion rule:
- If VLM yields a definite read: use VLM signal with confidence 0.7.
- If VLM is 'average'/'unknown': fallback to mesh value with confidence 0.4.
- VLM never affects solid in-plane features."""
from collections import OrderedDict

# projection feature  ->  (VLM JSON key, {answer-token: ordinal})
PROJECTION_PROBES = OrderedDict([
    ("cheek_projection", ("cheekbones", {"prominent": +1, "high": +1, "average": 0, "moderate": 0, "flat": -1, "low": -1})),
    ("brow_projection", ("brow_ridge", {"heavy": +1, "strong": +1, "pronounced": +1, "average": 0, "moderate": 0, "flat": -1, "light": -1})),
    ("chin_projection", ("chin", {"projecting": +1, "prominent": +1, "neutral": 0, "average": 0, "receding": -1, "weak": -1})),
    ("nasal_projection", ("nose_bridge", {"high": +1, "prominent": +1, "medium": 0, "average": 0, "low": -1, "flat": -1})),
])
VLM_CONF, MESH_FALLBACK_CONF = 0.7, 0.4
_ORD_TO_VALUE = {+1: 0.5, 0: 0.0, -1: -0.5}

PROMPT = (
    "Look at this face photo and judge BONE STRUCTURE (ignore skin, hair, expression). "
    "Answer each strictly with one word from the allowed set, or 'unknown' if the angle hides it.\n"
    '- cheekbones: prominent | average | flat\n'
    '- brow_ridge: heavy | average | flat\n'
    '- chin: projecting | neutral | receding\n'
    '- nose_bridge: high | medium | low\n'
    'Reply ONLY with compact JSON: {"cheekbones":"...","brow_ridge":"...","chin":"...","nose_bridge":"..."}. No prose.')


def parse_projection_reply(text):
    """Raw VLM reply text -> {projection_feature: ordinal(+1/0/-1)}; omit features it left unknown."""
    import json
    out = {}
    try:
        obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:
        return out
    for feat, (key, mapping) in PROJECTION_PROBES.items():
        ans = str(obj.get(key, "unknown")).strip().lower()
        if ans in mapping:
            out[feat] = mapping[ans]
    return out


def fuse_projection(mesh_feats, ordinals):
    """Return a COPY of a mesh fingerprint with its 4 projection features updated from the VLM read.
    Definite VLM answer -> ordinal value @0.7; otherwise keep the mesh value but stay low-confidence."""
    fused = OrderedDict((k, dict(v)) for k, v in mesh_feats.items())
    for feat in PROJECTION_PROBES:
        if feat not in fused:
            continue
        if feat in ordinals:
            fused[feat]["mesh_value"] = fused[feat]["value"]
            fused[feat]["value"] = _ORD_TO_VALUE[ordinals[feat]]
            fused[feat]["confidence"] = VLM_CONF
            fused[feat]["source"] = "vlm"
        else:
            fused[feat]["confidence"] = MESH_FALLBACK_CONF
            fused[feat]["source"] = "mesh(uncertain)"
    return fused
