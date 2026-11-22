"""Inherit bone structure from two parents' fingerprints.

Polygenic blending: additive per-feature with Gaussian variance, bounded near
the parental envelope. No population/heritage priors; structure derives ONLY from
the two measured parents (unlike discrete traits which use recessive-carrier
priors). Low-confidence features (e.g., frontal-blind projections) pull harder
toward the central blend to avoid committing to shaky measurements.
"""
import random

LEAN_STRONG, LEAN_BLEND = 0.70, 0.50   # a leaned feature is 70/30 toward one parent; else ~50/50
_LEAN_PROB = 0.5                         # per feature, coin-flip which parent (if any) it favours


def inherit_structure(fp_a, fp_b, seed, names=("A", "B")):
    """fp_a/fp_b: {feature: {value, confidence, kind}}. Returns (child {feature: value}, story lines)."""
    rng = random.Random(seed)
    child, story = {}, []
    for feat in fp_a:
        va, vb = fp_a[feat]["value"], fp_b[feat]["value"]
        conf = min(fp_a[feat]["confidence"], fp_b[feat]["confidence"])
        # per-feature lean: pick a favoured parent, blend toward them; low confidence softens the lean
        favour_a = rng.random() < _LEAN_PROB
        strength = LEAN_BLEND + (LEAN_STRONG - LEAN_BLEND) * conf     # high conf -> commit to the lean
        wa = strength if favour_a else (1.0 - strength)
        base = wa * va + (1.0 - wa) * vb
        # polygenic spread: proportional to how far the parents differ (siblings vary). NOT inflated by
        # low confidence — uncertainty is handled by pulling toward the blend (weaker lean above), not
        # by adding randomness we can't justify.
        spread = abs(va - vb) * 0.25 + 1e-4
        val = base + rng.gauss(0.0, spread)
        # keep the child within a small margin of the parental envelope (no invented structure)
        lo, hi = min(va, vb), max(va, vb)
        margin = 0.15 * (hi - lo) + 1e-4
        val = max(lo - margin, min(hi + margin, val))
        child[feat] = round(val, 4)
        # narrate only the features that meaningfully lean (skip near-ties / tiny parental gaps)
        if abs(va - vb) > 1e-3:
            lean_to = names[0] if abs(val - va) < abs(val - vb) else names[1]
            story.append(f"{feat}: {val:.3f} (leans {lean_to}; {names[0]} {va:.3f} / {names[1]} {vb:.3f}"
                         + (", low-conf" if conf < 0.7 else "") + ")")
    return child, story
