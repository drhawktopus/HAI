"""Render the Fellowship VLM cell `t_word.png` so that the WHOLE sentence fits the canvas.

Why this exists: the original 08-29 image was drawn at a fixed offset with no width check;
"the quick brown fox" overflowed the 480 px canvas and the last word was cut off ("...brown fo"), so every
VLM's "brown" was the last complete word in the picture while the gold said "fox". A cell whose gold cannot
be read from the picture measures the test, not the model.

Rules baked in: measure the rendered text, shrink the font until it fits with a margin, ASSERT that the ink
never touches an edge, and print the margin so the number is in the log. Look at the PNG before it becomes gold.
    python make_t_word.py [out.png]
"""
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
TEXT = "the quick brown fox"
MARGIN = 24
out = sys.argv[1] if len(sys.argv) > 1 else "t_word.png"

font = None
for size in range(64, 12, -2):
    try:
        f = ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        try:
            f = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except OSError:
            f = ImageFont.load_default()
    x0, y0, x1, y1 = ImageDraw.Draw(Image.new("RGB", (W, H))).textbbox((0, 0), TEXT, font=f)
    if (x1 - x0) <= W - 2 * MARGIN and (y1 - y0) <= H - 2 * MARGIN:
        font, tw, th = f, x1 - x0, y1 - y0
        break
assert font is not None, "no font size fits the canvas"

im = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(im)
d.text(((W - tw) // 2 - x0, (H - th) // 2 - y0), TEXT, fill="black", font=font)
im.save(out)

# positive control on the artifact itself: ink must be clear of every edge
g = im.convert("L")
px = g.load()
cols = [x for x in range(W) if any(px[x, y] < 128 for y in range(H))]
rows = [y for y in range(H) if any(px[x, y] < 128 for x in range(W))]
left, right, top, bottom = min(cols), W - 1 - max(cols), min(rows), H - 1 - max(rows)
assert min(left, right, top, bottom) >= MARGIN // 2, "ink touches an edge: L%d R%d T%d B%d" % (left, right, top, bottom)
print("wrote %s  font=%dpx  text=%dx%d  margins L%d R%d T%d B%d" % (out, font.size, tw, th, left, right, top, bottom))
