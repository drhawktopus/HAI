"""Hard Bench - vision (vlm) task pool: candidate cells for calibration, each rendered from code.

Each task: (image file, question, gold, match, category) - the same tuple the vlm payload's VLM_TASKS uses.
Gold is true BY CONSTRUCTION: the renderer below draws exactly the fact the question asks for, plus
distractor facts (a second number, other colours, other labels) so the cell needs the right fact read,
not just any fact. Numeric and single-letter golds use `word` match; `contains` would let gold "7" pass
on an answer of "17".

`render(outdir)` draws every image and asserts every drawn element sits inside the canvas with a margin
and that no two text boxes overlap (the t_word.png lesson: a gold that cannot be read from the picture
measures the test, not the model). `selftest()` also grades the gold and a wrong answer per task through
the payload's own scorer. LOOK at the PNGs before they are staged.

    python hard_vlm_tasks.py <outdir>
"""
import os
import re
import sys

W, H = 480, 320
MARGIN = 10

# taskid: (image, question, gold, match, category)
TASKS = {
    "h_count_red7": ("h_count_red7.png", "How many red circles are in this image? Answer with only the number.", "7", "word", "scene-count"),
    "h_count_star6": ("h_count_star6.png", "How many stars are in this image? Answer with only the number.", "6", "word", "scene-count"),
    "h_count_tri9": ("h_count_tri9.png", "How many triangles are in this image? Answer with only the number.", "9", "word", "scene-count"),
    "h_num4827": ("h_num4827.png", "What number is shown? Answer with only the number.", "4827", "word", "read"),
    "h_label_b": ("h_label_b.png", "Two numbers are shown, labelled A and B. What is the number labelled B? Answer with only the number.", "58", "word", "read"),
    "h_receipt_total": ("h_receipt_total.png", "This is a receipt. What is the Total amount? Answer with only the number.", "27", "word", "read"),
    "h_thermo_humid": ("h_thermo2.png", "This thermostat screen shows several numbers. What is the humidity percentage? Answer with only the number.", "45", "word", "device-screen"),
    "h_thermo_set": ("h_thermo2.png", "This thermostat screen shows several numbers. What is the SET temperature? Answer with only the number.", "68", "word", "device-screen"),
    "h_battery": ("h_battery.png", "This is a phone status bar. What battery percentage is shown? Answer with only the number.", "23", "word", "device-screen"),
    "h_clock_min": ("h_clock_min.png", "This digital clock shows hours and minutes. What are the minutes? Answer with only the number.", "45", "word", "device-screen"),
    "h_ui_red": ("h_ui_red.png", "This is an app screen with three buttons. What is the label on the red button? Answer one word.", "delete", "word", "ui"),
    "h_ui_checked": ("h_ui_checked.png", "This is a settings list with checkboxes. Which option is checked? Answer one word.", "email", "word", "ui"),
    "h_ui_tab": ("h_ui_tab.png", "This is an app with three tabs. Which tab is selected (highlighted in blue)? Answer one word.", "profile", "word", "ui"),
    "h_bars_short": ("h_bars_short.png", "Which bar is the shortest: B, C, D, or E? Answer with one letter.", "d", "word", "chart"),
    "h_bars_value": ("h_bars_value.png", "Each bar has its value written on top. What value is written on bar C? Answer with only the number.", "35", "word", "chart"),
    "h_table_bob": ("h_table.png", "This table lists names and ages. How old is Bob? Answer with only the number.", "27", "word", "table"),
    "h_table_oldest": ("h_table.png", "This table lists names and ages. Who is the oldest? Answer with only the name.", "cara", "word", "table"),
    "h_shape_color": ("h_shapes.png", "There is a circle, a square and a triangle. What colour is the square? Answer one word.", "blue", "word", "scene-color"),
    "h_corner": ("h_corner.png", "A letter is printed in each corner. Which letter is in the bottom-right corner? Answer with one letter.", "w", "word", "scene-layout"),
    "h_arrow": ("h_arrow.png", "Which way does the arrow point: up, down, left, or right? Answer one word.", "left", "word", "scene-layout"),
    "h_sign_level": ("h_sign_level.png", "What level number is shown on this parking sign? Answer with only the number.", "3", "word", "read"),
    "h_word_second": ("h_word_second.png", "Read the text in the image. What is the second word? Answer one word.", "apples", "word", "read"),
    "h_plate": ("h_plate.png", "This is a licence plate with letters and digits. What are the digits? Answer with only the digits.", "492", "word", "read"),
    "h_light_yellow": ("h_light_yellow.png", "Which light is lit (brightest): red, yellow, or green? Answer one word.", "yellow", "word", "scene-color"),
}

# The cells are drawn at 480x320 (where the margin/overlap assertions run) and SERVED at 2x (960x640, nearest-neighbour
# so no detail is invented): at 480x320 the Qwen 27Bs' vision encoder spends ~130 tokens on the image and cannot read
# digits; at 2x both read every probe cell. New file names on purpose - tablets
# cache ~/vlm_set/<img> and only stage an absent file.
SERVE_SCALE = int(os.environ.get("HB_VLM_SCALE", "2"))
TASKS = {k: ((v[0] if SERVE_SCALE == 1 else v[0].replace(".png", "_%dx.png" % SERVE_SCALE)),) + v[1:]
         for k, v in TASKS.items()}                          # 1x is the drawn file itself; only rescaled copies carry _Nx

# A plausible WRONG answer per task: the distractor fact the image also carries. The scorer must refuse it.
WRONG = {
    "h_count_red7": "5", "h_count_star6": "4", "h_count_tri9": "6", "h_num4827": "4872", "h_label_b": "31",
    "h_receipt_total": "24", "h_thermo_humid": "72", "h_thermo_set": "72", "h_battery": "12", "h_clock_min": "7",
    "h_ui_red": "save", "h_ui_checked": "sms", "h_ui_tab": "home", "h_bars_short": "b", "h_bars_value": "50",
    "h_table_bob": "34", "h_table_oldest": "alice", "h_shape_color": "red", "h_corner": "k", "h_arrow": "right",
    "h_sign_level": "13", "h_word_second": "green", "h_plate": "kxr", "h_light_yellow": "green",
}


def _font(size):
    from PIL import ImageFont
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise RuntimeError("no TrueType font available - refusing the bitmap default (unreadable at size)")


class Canvas:
    """Draws onto a white W x H image and records every element's box for the fit/overlap assertions."""

    def __init__(self, bg="white"):
        from PIL import Image, ImageDraw
        self.im = Image.new("RGB", (W, H), bg)
        self.d = ImageDraw.Draw(self.im)
        self.boxes = []      # (kind, box)

    def _keep(self, kind, box):
        x0, y0, x1, y1 = box
        assert x0 >= MARGIN and y0 >= MARGIN and x1 <= W - MARGIN and y1 <= H - MARGIN, \
            "%s %r leaves the canvas margin" % (kind, box)
        self.boxes.append((kind, tuple(box)))

    def text(self, xy, s, size, fill="black", anchor="mm"):
        f = _font(size)
        box = self.d.textbbox(xy, s, font=f, anchor=anchor)
        self._keep("text:" + s, box)
        self.d.text(xy, s, fill=fill, font=f, anchor=anchor)
        return box

    def ellipse(self, box, fill, outline=None, width=1):
        self._keep("ellipse", box)
        self.d.ellipse(box, fill=fill, outline=outline, width=width)

    def rect(self, box, fill, outline=None, width=1):
        self._keep("rect", box)
        self.d.rectangle(box, fill=fill, outline=outline, width=width)

    def poly(self, pts, fill, outline=None):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self._keep("poly", (min(xs), min(ys), max(xs), max(ys)))
        self.d.polygon(pts, fill=fill, outline=outline)

    def line(self, pts, fill, width):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self._keep("line", (min(xs) - width, min(ys) - width, max(xs) + width, max(ys) + width))
        self.d.line(pts, fill=fill, width=width)

    def check_text_overlap(self):
        t = [b for k, b in self.boxes if k.startswith("text:")]
        for i in range(len(t)):
            for j in range(i + 1, len(t)):
                a, b = t[i], t[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    raise AssertionError("text boxes overlap: %r %r" % (a, b))

    def save(self, path):
        self.check_text_overlap()
        self.im.save(path)


def _grid(n, cols, x0, y0, dx, dy):
    return [(x0 + (i % cols) * dx, y0 + (i // cols) * dy) for i in range(n)]


def _star(cx, cy, r):
    import math
    pts = []
    for k in range(10):
        rr = r if k % 2 == 0 else r * 0.45
        a = -math.pi / 2 + k * math.pi / 5
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _tri(cx, cy, r):
    return [(cx, cy - r), (cx - r, cy + r * 0.8), (cx + r, cy + r * 0.8)]


def render(outdir):
    os.makedirs(outdir, exist_ok=True)
    out = {}

    def save(c, name):
        p = os.path.join(outdir, name)
        c.save(p)                                  # the 480x320 master (fit/overlap asserted in Canvas)
        out[name] = p
        big = name.replace(".png", "_%dx.png" % SERVE_SCALE)
        pb = os.path.join(outdir, big)
        c.im.resize((W * SERVE_SCALE, H * SERVE_SCALE), resample=0).save(pb)   # 0 = NEAREST
        out[big] = pb

    # counts: the target shape interleaved with a distractor shape/colour on a fixed grid
    c = Canvas()
    cells = _grid(12, 4, 70, 60, 110, 100)
    colours = ["red", "blue", "red", "red", "blue", "red", "blue", "red", "blue", "red", "blue", "red"]
    assert colours.count("red") == 7
    for (x, y), col in zip(cells, colours):
        c.ellipse((x - 30, y - 30, x + 30, y + 30), fill=col)
    save(c, "h_count_red7.png")

    c = Canvas()
    kinds = ["star", "sq", "star", "star", "sq", "star", "sq", "star", "sq", "star"]
    assert kinds.count("star") == 6
    for (x, y), k in zip(_grid(10, 5, 60, 90, 90, 140), kinds):
        if k == "star":
            c.poly(_star(x, y, 34), fill="orange")
        else:
            c.rect((x - 26, y - 26, x + 26, y + 26), fill="purple")
    save(c, "h_count_star6.png")

    c = Canvas()
    kinds = ["t", "c", "t", "t", "c", "t", "t", "t", "t", "c", "t", "t"]
    assert kinds.count("t") == 9
    for (x, y), k in zip(_grid(12, 4, 70, 60, 110, 100), kinds):
        if k == "t":
            c.poly(_tri(x, y, 30), fill="green")
        else:
            c.ellipse((x - 28, y - 28, x + 28, y + 28), fill="green")
    save(c, "h_count_tri9.png")

    c = Canvas()
    c.text((W // 2, H // 2), "4827", 120)
    save(c, "h_num4827.png")

    c = Canvas()
    c.text((130, 110), "A", 44, fill="gray")
    c.text((130, 200), "31", 80)
    c.text((350, 110), "B", 44, fill="gray")
    c.text((350, 200), "58", 80)
    save(c, "h_label_b.png")

    c = Canvas()
    c.text((W // 2, 50), "RECEIPT", 36)
    for y, k, v in ((120, "Subtotal", "$24"), (175, "Tax", "$3"), (245, "Total", "$27")):
        c.text((90, y), k, 34, anchor="lm")
        c.text((390, y), v, 34, anchor="rm")
    c.line([(90, 208), (390, 208)], fill="black", width=3)
    save(c, "h_receipt_total.png")

    c = Canvas(bg="#1e2a38")
    c.text((W // 2, 110), "72°", 110, fill="white")
    c.text((110, 250), "SET 68", 34, fill="#9fd3ff")
    c.text((350, 250), "HUMIDITY 45%", 30, fill="#9fd3ff")
    save(c, "h_thermo2.png")

    c = Canvas()
    c.rect((20, 40, 460, 110), fill="#222222")
    c.text((80, 75), "12:40", 36, fill="white")
    c.text((330, 75), "Battery 23%", 34, fill="white")
    c.text((W // 2, 210), "Tuesday", 44, fill="gray")
    save(c, "h_battery.png")

    c = Canvas(bg="black")
    c.text((W // 2, 140), "07:45", 130, fill="#ff3b3b")
    c.text((W // 2, 265), "MON 14", 36, fill="#aaaaaa")
    save(c, "h_clock_min.png")

    c = Canvas(bg="#f4f4f4")
    for x, col, lab in ((85, "#9e9e9e", "Cancel"), (240, "#d32f2f", "Delete"), (395, "#2e7d32", "Save")):
        c.rect((x - 70, 130, x + 70, 190), fill=col)
        c.text((x, 160), lab, 30, fill="white")
    save(c, "h_ui_red.png")

    c = Canvas()
    for y, lab, on in ((80, "Email", True), (160, "SMS", False), (240, "Push", False)):
        c.rect((90, y - 25, 140, y + 25), fill="white", outline="black", width=4)
        if on:
            c.line([(98, y), (112, y + 15), (134, y - 18)], fill="black", width=6)
        c.text((170, y), lab, 40, anchor="lm")
    save(c, "h_ui_checked.png")

    c = Canvas()
    for x, lab, sel in ((90, "Home", False), (240, "Search", False), (390, "Profile", True)):
        c.rect((x - 72, 120, x + 72, 190), fill="#1565c0" if sel else "#e0e0e0")
        c.text((x, 155), lab, 30, fill="white" if sel else "black")
    save(c, "h_ui_tab.png")

    c = Canvas()
    base = 280
    for x, lab, h in ((80, "B", 170), (180, "C", 120), (280, "D", 70), (380, "E", 200)):
        c.rect((x - 30, base - h, x + 30, base), fill="#4a78c2")
        c.text((x, base + 20), lab, 26)
    save(c, "h_bars_short.png")

    c = Canvas()
    base = 270
    for x, lab, v in ((80, "A", 20), (180, "B", 50), (280, "C", 35), (380, "D", 45)):
        h = v * 4
        c.rect((x - 30, base - h, x + 30, base), fill="#e08a1e")
        c.text((x, base - h - 18), str(v), 26)
        c.text((x, base + 22), lab, 26)
    save(c, "h_bars_value.png")

    c = Canvas()
    c.text((150, 50), "Name", 36)
    c.text((340, 50), "Age", 36)
    c.line([(60, 80), (420, 80)], fill="black", width=3)
    for y, n, a in ((125, "Alice", "34"), (190, "Bob", "27"), (255, "Cara", "41")):
        c.text((150, y), n, 36)
        c.text((340, y), a, 36)
    save(c, "h_table.png")

    c = Canvas()
    c.ellipse((40, 100, 150, 210), fill="red")
    c.rect((185, 100, 295, 210), fill="blue")
    c.poly(_tri(385, 160, 58), fill="green")
    save(c, "h_shapes.png")

    c = Canvas()
    for (x, y), ch in (((50, 50), "K"), ((430, 50), "M"), ((50, 270), "R"), ((430, 270), "W")):
        c.text((x, y), ch, 56)
    save(c, "h_corner.png")

    c = Canvas()
    c.poly([(90, 160), (200, 80), (200, 130), (390, 130), (390, 190), (200, 190), (200, 240)], fill="black")
    save(c, "h_arrow.png")

    c = Canvas(bg="#0d47a1")
    c.text((W // 2, 110), "PARKING", 70, fill="white")
    c.text((W // 2, 215), "LEVEL 3", 70, fill="white")
    save(c, "h_sign_level.png")

    c = Canvas()
    c.text((W // 2, H // 2), "green apples taste sour", 36)
    save(c, "h_word_second.png")

    c = Canvas(bg="#dddddd")
    c.rect((60, 95, 420, 225), fill="#fdfdfd", outline="black", width=5)
    c.text((W // 2, 160), "KXR 492", 80)
    save(c, "h_plate.png")

    c = Canvas()
    c.rect((190, 20, 290, 300), fill="#222222")
    for y, lit, on in ((65, "#ff3030", False), (160, "#ffd21f", True), (255, "#20c040", False)):
        col = lit if on else "#4a4a4a"
        c.ellipse((205, y - 35, 275, y + 35), fill=col)
    save(c, "h_light_yellow.png")

    missing = {t[0] for t in TASKS.values()} - set(out)
    assert not missing, "tasks reference images that were not rendered: %s" % sorted(missing)
    return out


def _score(ans, expected, match):
    """Verbatim copy of the payload's _vlm_score, re-checked against the generated payload by the generator."""
    toks = re.sub(r"[^a-z0-9 ]", " ", ans.lower()).split()
    joined = " ".join(toks)
    if match == "word":
        return expected in toks
    return expected in joined


def selftest(outdir, score=_score):
    imgs = render(outdir)
    assert set(WRONG) == set(TASKS), "WRONG must cover every task exactly"
    bad = []
    for tid, (_img, _q, gold, match, _cat) in TASKS.items():
        if not score(gold, gold, match):
            bad.append((tid, "gold fails its own scorer"))
        if not score("The answer is %s." % gold.upper(), gold, match):
            bad.append((tid, "a sentence-wrapped gold fails"))
        if score(WRONG[tid], gold, match):
            bad.append((tid, "wrong answer %r passes" % WRONG[tid]))
    print("rendered %d images for %d tasks into %s" % (len(imgs), len(TASKS), outdir))
    print("scorer controls: %d/%d tasks clean" % (len(TASKS) - len({b[0] for b in bad}), len(TASKS)))
    for b in bad:
        print("  FAIL", b)
    assert not bad
    return imgs


if __name__ == "__main__":
    selftest(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "vlm_hard_imgs"))
