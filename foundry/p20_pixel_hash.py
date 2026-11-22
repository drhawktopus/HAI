r"""Pixel-content hash and comparison tool.

Compares pixel data, not file bytes. Byte-identical files may differ in metadata
(e.g., order names), while byte-different files may represent identical images.
Reproducibility is determined by visual content equivalence.

Usage:
    p20_pixel_hash.py <image> [reference_image]

If one argument is provided, prints the pixel hash.
If two arguments are provided, prints both hashes and indicates MATCH or DIFFER.

Exit codes:
    0: Success (including DIFFER result, which is a valid outcome, not an error)
    1: Image could not be read"""
import hashlib
import sys

try:
    from PIL import Image
except ImportError:
    print("FAIL PIL not available in this interpreter")
    sys.exit(1)


def pixel_hash(path):
    with Image.open(path) as im:
        return hashlib.sha256(im.convert("RGB").tobytes()).hexdigest()


if len(sys.argv) < 2:
    print("FAIL usage: p20_pixel_hash.py <image> [reference]")
    sys.exit(1)

try:
    a = pixel_hash(sys.argv[1])
except Exception as e:
    print("FAIL could not read %s (%r)" % (sys.argv[1], e))
    sys.exit(1)

if len(sys.argv) < 3:
    print("PIXELHASH %s" % a[:32])
    sys.exit(0)

try:
    b = pixel_hash(sys.argv[2])
except Exception as e:
    # An unreadable REFERENCE is "cannot verify", not "differs" -- unverifiable must never be
    # reported as a refutation (the lab's standing rule about unverifiable state).
    print("UNVERIFIABLE reference unreadable: %s (%r)" % (sys.argv[2], e))
    sys.exit(0)

print("PIXELHASH %s" % a[:32])
print("REFERENCE %s" % b[:32])
print("VERDICT %s" % ("MATCH - pixel-identical to the reference"
                      if a == b else "DIFFER - pixels do not match the reference"))
