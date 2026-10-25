"""Render the landing page's tiling animation for the README, in GitHub's
light and dark colours: docs/images/tiling.gif and tiling-dark.gif.

Each frame is drawn by the page's own script (window.__tile.at), cloned
into a column on a temporary copy of docs/index.html, and the column is
screenshot by headless Firefox in batches, then sliced into frames.
Needs Firefox and Pillow; run after scripts/make_site.py.

    python3 scripts/render_tiling_gif.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile

from PIL import Image

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
sys.path.insert(0, str(REPO / "scripts"))
import make_site                                       # noqa: E402

# FPS frames per second of the page's own timing, played SPEED times faster
FPS, SPEED, W, H, BATCH = 4, 2, 760, 416, 40
BETWEEN, DWELL = 600, 2600          # the page's own pauses, milliseconds

_, data = make_site.tile_animation()
D = json.loads(data)
frames = []                          # (step, phase, t, duration ms)
for i, st in enumerate(D["steps"]):
    moving = [p for p, ph in enumerate(st["p"]) if not ph.get("i")]
    for n, p in enumerate(moving):
        ms = st["p"][p].get("ms", 1500)
        k = max(2, round(ms / 1000 * FPS))
        for j in range(k + 1):
            frames.append([i, p, j / k, round(1000 / FPS / SPEED)])
        frames[-1][3] = round((BETWEEN if n < len(moving) - 1 else DWELL) / SPEED)
frames[-1][3] = 3000
# open on the first phase's end (the cell in place), held, instead of its
# empty start: GitHub shows the first frame while loading and to readers
# who have reduced motion turned on
first = [f for f in frames if f[0] == 0 and f[1] == frames[0][1]]
frames[:len(first)] = [first[-1][:3] + [round(DWELL / SPEED)]]
print(len(frames), "frames")

# GitHub's own surfaces and text, and the drawing's neutral tokens to
# match; the coloured tokens (cells, column, measured values) stay as drawn
THEMES = {
    "tiling.gif": dict(dark=False, bg="#ffffff", ink="#1f2328", muted="#59636e",
                       cd="#eaeef2", cbn="#818b98"),
    "tiling-dark.gif": dict(dark=True, bg="#0d1117", ink="#f0f6fc", muted="#9198a1",
                            cd="#262c36", cbn="#656c76"),
}

CAP = """
<style>
  .side, .side-btn { display: none !important; }
  svg#tile { width: %(W)dpx !important; height: %(SH)dpx !important; }
  #cap { position: absolute; top: 0; left: 0; z-index: 9999; background: %(bg)s; }
  #cap .fr { width: %(W)dpx; height: %(H)dpx; overflow: hidden; position: relative;
             background: %(bg)s; }
  #cap .t { position: absolute; top: 14px; left: 20px;
            font: 600 15px/1 -apple-system, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
            color: %(ink)s; }
  #cap .n { font-weight: 400; color: %(muted)s; margin-right: 8px;
            font-variant-numeric: tabular-nums; }
  #cap svg.tile { position: absolute; left: 0; bottom: 0; width: %(W)dpx; height: %(SH)dpx;
                  margin: 0; border: 0; border-radius: 0; background: %(bg)s;
                  --tl-cd: %(cd)s; --tl-cbn: %(cbn)s; --tl-rgc: %(bg)s; }
  #cap svg.tile .ta, #cap svg.tile .ts { fill: %(muted)s; }
  #cap svg.tile .th { fill: %(ink)s; stroke: %(bg)s; }
  * { transition: none !important; animation: none !important; }
</style>
<div id="cap"></div>
<script>
window.addEventListener('load', function () {
  var F = %(F)s, cap = document.getElementById('cap'), T = window.__tile;
  var svg = document.getElementById('tile');
  F.forEach(function (f) {
    T.at(f[0], f[1], f[2]);
    var fr = document.createElement('div'); fr.className = 'fr';
    fr.innerHTML = '<div class="t"><span class="n">' + (f[0] + 1) + ' / ' +
      T.steps + '</span>' + T.data.steps[f[0]].t + '</div>';
    var c = svg.cloneNode(true); c.removeAttribute('id');
    fr.appendChild(c); cap.appendChild(fr);
  });
});
</script>
"""


def to_palette(imgs, hexcolour):
    """One shared 128-colour palette for every frame, with the background
    colour held exactly: its entry is pinned, and every pixel of exactly
    that colour is given that entry (Pillow's palette lookup is otherwise
    approximate, and turns white into near-white)."""
    import numpy as np
    want = [int(hexcolour[i:i + 2], 16) for i in (1, 3, 5)]
    pal = imgs[-1].quantize(colors=128, method=Image.MEDIANCUT)
    p = pal.getpalette()
    k = min(range(len(p) // 3),
            key=lambda i: sum((p[3 * i + c] - want[c]) ** 2 for c in range(3)))
    p[3 * k:3 * k + 3] = want
    pal.putpalette(p)
    out = []
    for im in imgs:
        idx = np.array(im.quantize(palette=pal, dither=Image.NONE))
        idx[(np.array(im) == want).all(axis=-1)] = k
        q = Image.fromarray(idx, "P")
        q.putpalette(p)
        out.append(q)
    return out


def render(name, theme, html, scratch, profile):
    if theme["dark"]:                # the page's dark mode, forced on
        html = html.replace("(prefers-color-scheme: dark)", "all")
    imgs = []
    for b in range(0, len(frames), BATCH):
        part = frames[b:b + BATCH]
        page = DOCS / f"_tilecap_{b // BATCH}.html"
        page.write_text(html.replace("</body>", CAP % dict(
            theme, W=W, H=H, SH=W // 2, F=json.dumps(part)) + "</body>", 1))
        out = scratch / f"{name}_{b // BATCH:02d}.png"
        subprocess.run(["timeout", "120", "firefox", "--headless", "--no-remote",
                        "--profile", profile,
                        "--window-size", f"{W},{H * len(part)}", "--screenshot",
                        str(out), page.as_uri()],
                       capture_output=True)
        page.unlink()
        if not out.exists():
            raise SystemExit(f"{name}: batch {b // BATCH} was not captured")
        im = Image.open(out).convert("RGB")
        imgs += [im.crop((0, k * H, W, (k + 1) * H)) for k in range(len(part))]
    q = to_palette(imgs, theme["bg"])
    gif = DOCS / "images" / name
    q[0].save(gif, save_all=True, append_images=q[1:], loop=0, optimize=True,
              duration=[f[3] for f in frames], disposal=1)
    print(gif, gif.stat().st_size // 1024, "kilobytes")


def main():
    import shutil
    html = (DOCS / "index.html").read_text()
    # scratch space inside out/ (ignored by git): a snap-packaged Firefox
    # cannot read or write the system temporary folder
    (REPO / "out").mkdir(exist_ok=True)
    scratch = pathlib.Path(tempfile.mkdtemp(dir=REPO / "out"))
    profile = tempfile.mkdtemp(dir=REPO / "out")
    try:
        for name, theme in THEMES.items():
            render(name, theme, html, scratch, profile)
    finally:
        shutil.rmtree(scratch)
        shutil.rmtree(profile)


if __name__ == "__main__":
    main()
