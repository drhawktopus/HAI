"""Render a candidate Frigate zone polygon over a live frame so it can be judged by EYE.

Coordinates are NORMALISED (0-1) exactly as Frigate stores them, so the same string
pasted into config.yml is what was looked at here. Frigate tests an object's
BOTTOM-CENTRE against the polygon -- the dot grid drawn inside is a reminder that what
matters is where the PAWS land, not where the body is.
"""
import sys
from PIL import Image, ImageDraw

# candidate: the whole visible floor in front of the fireplace + the hearth stone
ZONE = [
    (0.175, 0.885),   # left, at the base of the tall cabinet
    (0.335, 0.845),   # hearth back-left, at the fireplace mouth
    (0.585, 0.830),   # hearth back-right, base of the bookcase
    (0.735, 0.885),   # right, floor toward the doorway
    (0.800, 1.000),   # bottom right
    (0.120, 1.000),   # bottom left
]


def main(src, dst, zone=ZONE):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    pts = [(x * w, y * h) for x, y in zone]

    # translucent fill needs its own layer; PIL cannot alpha-blend onto RGB directly
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(pts, fill=(0, 200, 255, 70), outline=(0, 220, 255, 255))
    for p in pts:
        d.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill=(255, 60, 60, 255))
    out = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")

    out.save(dst, quality=92)
    print(f"{dst}  {w}x{h}  {len(zone)} vertices")
    print("coordinates: " + ",".join(f"{x:g},{y:g}" for x, y in zone))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
