"""The Plot Canvas's page, in plain Python: the papers, where a new figure goes, the panel
letters' order, a figure drawn as a picture, pictures added to the page, and saving the whole
page (figures, letters and drawings) with matplotlib as PDF, SVG or PNG, every line and label
its own object. The Qt Plot Canvas (plotcanvas.py) and the HTML front end's (web/figures.py)
both use it, so a page saves the same from either.

A page is in mm, from its top left corner; a figure's spec and data are figspec's.
"""
from __future__ import annotations

import base64
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from matplotlib.figure import Figure

from . import figspec as F
from . import stim as stimmod

PAPERS = {"Square": (200, 200), "A4 portrait": (210, 297), "A4 landscape": (297, 210),
          "Letter portrait": (216, 279), "Letter landscape": (279, 216), "Slide 16:9": (338, 190)}
PAPER = "Square"                  # a new page's
CUSTOM = "Custom"                 # a size of your own
SIDE_MM = (40, 1300)              # the shortest and longest a side can be (a poster is 1189)
MARGIN = 12.0                     # mm
ROW_MM = 15.0                     # figures with tops this close sit in one row (the shortest figure is 15 mm)
LINKS = {"text": True, "lines": True, "axes": False, "colors": False, "legend": False}   # the same on every figure
FORMATS = {"pdf": "PDF", "svg": "SVG", "png": "PNG"}


def page_of(paper, size=None) -> tuple[str, tuple[float, float]]:
    """A page's paper and its width and height in whole mm, from what was asked or saved: a
    paper's own size, or one typed in (named for the paper it matches, else Custom). A page
    saved before sizes were kept has only its paper's name."""
    if size is None and paper in PAPERS:
        return paper, PAPERS[paper]
    try:
        w, h = (float(min(SIDE_MM[1], max(SIDE_MM[0], round(float(v))))) for v in size)
    except (TypeError, ValueError, OverflowError):
        return PAPER, PAPERS[PAPER]
    return next((n for n, s in PAPERS.items() if s == (w, h)), CUSTOM), (w, h)


def png(spec: dict, data: dict, dpi: float) -> bytes:
    """The figure drawn at its size in mm, as PNG (a picture put on the page: as it was)."""
    if spec.get("kind") == "image":
        return base64.b64decode(data.get("png", ""))
    buf = io.BytesIO()
    _drawn(spec, data, dpi).savefig(buf, format="png", dpi=dpi, facecolor="white")
    return buf.getvalue()


def _drawn(spec: dict, data: dict, dpi: float) -> Figure:
    """The figure drawn and laid out at its size in mm (or saying why it can't be)."""
    fig = Figure(figsize=(spec["w_mm"] / 25.4, spec["h_mm"] / 25.4), dpi=dpi)
    try:
        F.draw(fig, spec, data)
        fig.tight_layout()
    except Exception as e:
        fig.clear()
        fig.text(0.5, 0.5, f"Can't draw: {e}", ha="center", va="center")
    return fig


def pixels(spec: dict, data: dict, dpi: float) -> tuple[bytes, int, int, tuple | None]:
    """The figure drawn for a screen, with no PNG in between (quicker than png): its pixels
    (RGBA, row by row), how many across and down, and where its plot sits in it ((x0, y0, w, h)
    as fractions, y from the bottom, as laid_out_axes; None if it couldn't be drawn)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = _drawn(spec, data, dpi)
    fig.patch.set_facecolor("white")
    canvas = FigureCanvasAgg(fig)
    try:
        canvas.draw()
    except Exception as e:                            # (a label that can't be set: found only now)
        fig.clear()
        fig.text(0.5, 0.5, f"Can't draw: {e}", ha="center", va="center")
        canvas.draw()
    w, h = canvas.get_width_height()
    box = fig.axes[0].get_position() if fig.axes else None
    axes = tuple(float(v) for v in (box.x0, box.y0, box.width, box.height)) if box is not None else None
    return bytes(canvas.buffer_rgba()), w, h, axes


def laid_out_axes(spec: dict, data: dict):
    """Where the axes sit in a figure of this spec's size, laid out tightly: (x0, y0, w, h)
    as fractions of the figure."""
    if spec.get("kind") == "image":
        return 0.0, 0.0, 1.0, 1.0
    fig = Figure(figsize=(spec["w_mm"] / 25.4, spec["h_mm"] / 25.4))
    F.draw(fig, spec, data)
    try:
        fig.tight_layout()
    except Exception:
        pass
    p = fig.axes[0].get_position()
    return p.x0, p.y0, p.width, p.height


def _overlap(a, b) -> bool:
    """Two (x, y, w, h) boxes sharing some area (touching edges don't)."""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def free_spot(spec: dict, taken: list, paper: tuple[float, float], tries: int = 200) -> tuple[float, float]:
    """The next place along the rows, left to right, top to bottom, clear of the boxes
    `taken` ((x, y, w, h) in mm), looking in 5 mm steps `tries` times (else the top left)."""
    W, _ = paper
    x, y = MARGIN, MARGIN
    for _ in range(tries):
        r = (x, y, spec["w_mm"], spec["h_mm"])
        if not any(_overlap(r, t) for t in taken) and x + spec["w_mm"] <= W - MARGIN + 1:
            return x, y
        x += 5
        if x + spec["w_mm"] > W - MARGIN:
            x, y = MARGIN, y + 5
    return MARGIN, MARGIN


def letters(places: list[tuple[float, float]]) -> list[str]:
    """Each figure's panel letter (A, B, C…) from where it sits ((x, y) in mm): in rows, top to
    bottom, then left to right. A row is the figures whose tops are less than ROW_MM below its
    highest one's (not fixed bands down the page: a small nudge never swaps two letters)."""
    rows, top = [], None
    for k in sorted(range(len(places)), key=lambda k: (places[k][1], places[k][0])):
        if top is None or places[k][1] - top >= ROW_MM:
            rows.append([])
            top = places[k][1]
        rows[-1].append(k)
    out = [""] * len(places)
    n = 0
    for row in rows:
        for k in sorted(row, key=lambda k: places[k][0]):
            out[k] = stimmod.letter(n)
            n += 1
    return out


def linked_keys(links: dict) -> list[str]:
    """The look's keys of the sections that are the same on every figure."""
    return [k for sec, on in links.items() if on and sec in F.SECTIONS for k in F.SECTIONS[sec]]


def draw_annotations(fig, annotations: list[dict], W: float, H: float) -> None:
    """The drawings in the saved page: text as text (V_m with a true subscript), lines and shapes
    as vector objects."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Ellipse, FancyArrowPatch, Rectangle
    for d in annotations:
        if d["kind"] == "text":
            fig.text(d["x_mm"] / W, 1 - d["y_mm"] / H, F.mathify(d["text"]), fontsize=d["size"], color=d["color"],
                     fontweight="bold" if d.get("bold") else "normal", va="top", ha="left")
            continue
        x0, y0, x1, y1 = d["x0"] / W, 1 - d["y0"] / H, d["x1"] / W, 1 - d["y1"] / H
        lw = d["width"]
        if d["kind"] == "line":
            fig.add_artist(Line2D([x0, x1], [y0, y1], transform=fig.transFigure, color=d["color"], linewidth=lw,
                                  solid_capstyle="round"))
        elif d["kind"] == "arrow":
            fig.add_artist(FancyArrowPatch((x0, y0), (x1, y1), transform=fig.transFigure, color=d["color"],
                                           linewidth=lw, arrowstyle="-|>", mutation_scale=8 + 3 * lw))
        elif d["kind"] == "rect":
            fig.add_artist(Rectangle((min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0),
                                     transform=fig.transFigure, fill=False, edgecolor=d["color"], linewidth=lw))
        else:
            fig.add_artist(Ellipse(((x0 + x1) / 2, (y0 + y1) / 2), abs(x1 - x0), abs(y1 - y0),
                                   transform=fig.transFigure, fill=False, edgecolor=d["color"], linewidth=lw))


def save_page(out, paper: tuple[float, float], panels: list, annotations: list[dict], with_letters: bool,
              dpi: int = 300, fmt: str | None = None) -> None:
    """The whole page into `out` (a path, or a file object with fmt "pdf", "svg" or "png"):
    panels are (spec, data, x_mm, y_mm, letter), each figure's axes where they sit in it."""
    W, H = paper
    fig = Figure(figsize=(W / 25.4, H / 25.4))
    for spec, data, px, py, letter in panels:
        x0, y0, w, h = laid_out_axes(spec, data)
        pw, ph = spec["w_mm"], spec["h_mm"]
        rect = [(px + x0 * pw) / W, 1 - (py + ph) / H + y0 * ph / H, w * pw / W, h * ph / H]
        ax = fig.add_axes(rect)
        F.draw(fig, spec, data, ax=ax)
        if with_letters:
            fig.text(px / W, 1 - py / H, letter, fontsize=F.paper_font(spec) * 1.4,
                     fontweight="bold", va="top", ha="left")
    draw_annotations(fig, annotations, W, H)
    fig.savefig(out, dpi=dpi, facecolor="white", **({"format": fmt} if fmt else {}))


def figure_bytes(spec: dict, data: dict, fmt: str, dpi: int = 300) -> bytes:
    """One figure on its own (Single Plot's Save figure…), as PDF, SVG or PNG."""
    fig = Figure(figsize=(spec.get("w_mm", 80) / 25.4, spec.get("h_mm", 60) / 25.4))
    F.draw(fig, spec, data)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, transparent=False, facecolor="white")
    return buf.getvalue()


# ----------------------------------------------------------------------------- pictures
def pdf_png(path: str | Path, dpi: int = 300) -> bytes:
    """A PDF's first page drawn as PNG: with pypdfium2, else macOS's own sips."""
    try:
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(str(path))[0]
        pil = page.render(scale=dpi / 72).to_pil()
        b = io.BytesIO()
        pil.save(b, "PNG")
        return b.getvalue()
    except ImportError:
        pass
    if shutil.which("sips"):                      # macOS draws PDFs itself
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "page.png"
            subprocess.run(["sips", "-s", "format", "png", "-s", "dpiWidth", str(dpi), "-s", "dpiHeight",
                            str(dpi), str(path), "--out", str(out)], check=True, capture_output=True)
            if out.exists() and out.stat().st_size:
                return out.read_bytes()
    raise ValueError("PDFs need the pypdfium2 package here (pip install pypdfium2)")


def picture_png(raw: bytes, name: str, dpi: int = 300) -> tuple[bytes, int, int]:
    """A picture file's bytes (PNG, JPEG, TIFF, BMP, GIF or PDF's first page) as PNG bytes
    and its size in pixels, without Qt (SVG is drawn by the page that sends it)."""
    from PIL import Image
    if Path(name).suffix.lower() == ".pdf":
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "page.pdf"
            p.write_bytes(raw)
            raw = pdf_png(p, dpi)
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise ValueError("not a picture NEWT can read") from e
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA")
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue(), img.width, img.height


def view_png(spec: dict, data: dict, dpi: float, size: tuple[float, float]) -> tuple[bytes, dict | None]:
    """Single Plot's picture of the figure, drawn at `dpi` (sharp on the screen), and where its
    plot sits in it, for grab and zoom: {left, top, w, h} in a picture `size` (w, h) big on the
    screen, the axes' limits and scales, and whether x moves (not on bars)."""
    w_in, h_in = spec["w_mm"] / 25.4, spec["h_mm"] / 25.4
    fig = Figure(figsize=(w_in, h_in), dpi=dpi)
    try:
        F.draw(fig, spec, data)
        fig.tight_layout()
    except Exception as e:                      # a bad range or label: say so, don't crash
        fig.clear()
        fig.text(0.5, 0.5, f"Can't draw this: {e}", ha="center", va="center", wrap=True)
    W, H = size
    geo = None
    if fig.axes and spec.get("kind") != "image":        # where the plot is, for grab and zoom
        ax = fig.axes[0]
        pos = ax.get_position()
        geo = {"left": pos.x0 * W, "top": (1 - pos.y1) * H, "w": pos.width * W, "h": pos.height * H,
               "xlim": tuple(ax.get_xlim()), "ylim": tuple(ax.get_ylim()),
               "xlog": ax.get_xscale() == "log", "ylog": ax.get_yscale() == "log",
               "x_moves": spec.get("kind") != "bar"}
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    return buf.getvalue(), geo
