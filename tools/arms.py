r"""Refuse to emit invalid comparisons.

Enforces that comparisons are valid experiments by rejecting:
  - Arms differing in >1 declared variable
  - n=1 samples (hypothesis, not finding)
  - Buckets with rounds from only one arm (UNPAIRED; reported, not dropped)
  - Overlapping ranges (reported as OVERLAP; never treated as delta)

    from arms import Arm, compare
    a = Arm("cpu-decode",    samples=[27.7, 31.2, 35.4], decoder="sundae", stream="main")
    b = Arm("tablet-decode", samples=[48.6, 52.1, 58.2], decoder="tablet", stream="main")
    compare(a, b, unit="% of one core")
"""
import statistics
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Arm:
    """One side of a comparison. `samples` is the metric; everything else is a declared variable."""

    def __init__(self, label, samples, **variables):
        self.label = label
        self.samples = list(samples)
        self.variables = dict(variables)

    def summary(self):
        s = self.samples
        if not s:
            return None
        return {"median": statistics.median(s), "min": min(s), "max": max(s), "n": len(s)}


class NotAComparison(Exception):
    """Raised instead of returning a number that would be wrong."""


def _differing(a, b):
    keys = set(a.variables) | set(b.variables)
    return {k: (a.variables.get(k, "<unset>"), b.variables.get(k, "<unset>"))
            for k in sorted(keys) if a.variables.get(k) != b.variables.get(k)}


def compare(a, b, unit="", min_n=2, strict=True):
    """Print a verdict. Returns the delta, or raises NotAComparison when it would be meaningless."""
    diff = _differing(a, b)

    print(f"ARM A  {a.label:<22} {a.variables}")
    print(f"ARM B  {b.label:<22} {b.variables}")
    print("-" * 78)

    if not diff:
        raise NotAComparison(
            "the two arms declare IDENTICAL variables - there is nothing under test. "
            "If something really did differ, it was not declared, which is the same bug.")
    if len(diff) > 1:
        detail = "; ".join(f"{k}: {v[0]!r} vs {v[1]!r}" for k, v in diff.items())
        raise NotAComparison(
            f"{len(diff)} variables differ, so any delta is unattributable -- {detail}.\n"
            f"Hold all but one fixed and re-run. Naming a confound is not resolving it.")

    var, (va, vb) = next(iter(diff.items()))
    sa, sb = a.summary(), b.summary()
    if not sa or not sb:
        raise NotAComparison("an arm has no samples - an empty comparison must not look like a null result.")
    if strict and (sa["n"] < min_n or sb["n"] < min_n):
        raise NotAComparison(
            f"n={sa['n']} vs n={sb['n']} - below the {min_n}-sample floor. Run-to-run variance is "
            f"real; two of three single-round claims died on the second round (2026-08-10).")

    print(f"under test : {var}   {va!r} -> {vb!r}")
    print(f"ARM A      : median {sa['median']:.2f} [{sa['min']:.2f}-{sa['max']:.2f}] n={sa['n']}")
    print(f"ARM B      : median {sb['median']:.2f} [{sb['min']:.2f}-{sb['max']:.2f}] n={sb['n']}")

    separated = sa["max"] < sb["min"] or sb["max"] < sa["min"]
    delta = sb["median"] - sa["median"]
    print("-" * 78)
    if separated:
        print(f"DELTA      : {delta:+.2f} {unit}   ranges SEPARATED - this one is clean")
    else:
        print(f"DELTA      : {delta:+.2f} {unit}   ⚠ RANGES OVERLAP - report the spread, not the "
              f"delta. This does not yet distinguish the arms.")
    return delta


def buckets(arms, key, edges, unit=""):
    """Match arms inside comparable conditions before comparing. Unpaired buckets are REPORTED."""
    print(f"{'bucket':<18} | " + " | ".join(f"{a.label:^24}" for a in arms))
    print("-" * (20 + 27 * len(arms)))
    for name, lo, hi in edges:
        cells = []
        for a in arms:
            sel = [s for s, k in zip(a.samples, a.variables.get(key, [])) if lo <= k < hi]
            cells.append(Arm(a.label, sel).summary())
        row = " | ".join("      --  n=0        " if not c else
                         f"{c['median']:7.2f} [{c['min']:.1f}-{c['max']:.1f}] n={c['n']}"
                         for c in cells)
        print(f"{name:<18} | {row}")
        if any(c is None for c in cells):
            print(f"{'':<18} | >> UNPAIRED: only one arm has rounds here - PROVES NOTHING")
    print(f"\n(unit: {unit})" if unit else "")


if __name__ == "__main__":
    print(__doc__)
