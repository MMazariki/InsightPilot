"""A chart picture (PNG) of an answer, for emails.

Drawn with Pillow on this computer. The picture follows the visual the app
drew in the Power BI report for the same answer (``context["visual_type"]``,
the chart type selector included), so the email looks like the report:

* ``clusteredColumnChart``  vertical columns, highest first
* ``clusteredBarChart``     horizontal bars, highest at the top
* ``lineChart``             a line in the answer's (time) order
* ``pieChart`` / ``donutChart``  slices, largest first, with a legend
* anything else (no visual known, a combo, a card) -> columns, or bars for
  long names

Like Power BI's default theme: one series in #118DFF, slices in the theme's
colour order, values on the marks. Answers the report shows with a second
breakdown (a legend) or as a table get no picture: one series cannot show
them honestly. ``None`` also when there is no label column, no number,
one row only or more than ``MAX_BARS`` rows.
"""

import io
import math
import re
from pathlib import Path

from src.actions import render
from src.structured_agent import _column_label

MAX_BARS = 25
MAX_SLICES = 10
SCALE = 2                      # drawn at 2x and shown at half size: sharp text
WIDTH = 720                    # shown width in the email (pixels)

INK = (37, 36, 35)             # Power BI default text #252423
MUTED = (96, 94, 92)           # axis labels #605E5C
GRID = (225, 223, 221)
SURFACE = (255, 255, 255)
# Power BI default theme data colours, in order.
THEME = [(17, 141, 255), (18, 35, 158), (230, 108, 55), (107, 0, 123), (224, 68, 167),
         (116, 78, 194), (217, 179, 0), (214, 69, 80), (25, 125, 44), (180, 90, 0)]
SERIES = THEME[0]

NO_PICTURE = {"tableEx", "card", "multiRowCard"}
KNOWN = {"clusteredColumnChart", "clusteredBarChart", "lineChart", "pieChart",
         "donutChart"}

_FONTS = [Path(r"C:\Windows\Fonts\segoeui.ttf"), Path(r"C:\Windows\Fonts\arial.ttf"),
          Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
_BOLD = [Path(r"C:\Windows\Fonts\segoeuib.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]


def _font(size, bold=False):
    from PIL import ImageFont

    for path in (_BOLD if bold else _FONTS):
        if path.exists():
            return ImageFont.truetype(str(path), size * SCALE)
    return ImageFont.load_default(size * SCALE)


def _label_columns(context):
    numeric = set(render._numeric_columns(context))
    return [c for c in context["columns"] if c not in numeric]


def series(context):
    """``(label column, measure column, rows)`` the chart would show, or ``None``."""

    rows = context.get("rows") or []
    numeric = render._numeric_columns(context)
    labels = _label_columns(context)
    if not numeric or len(labels) != 1 or not 2 <= len(rows) <= MAX_BARS:
        return None
    if context.get("visual_type") in NO_PICTURE:
        return None
    label, measure = labels[0], numeric[-1]
    rows = [r for r in rows if r.get(measure) is not None]
    return (label, measure, rows) if len(rows) >= 2 else None


def kind(context):
    """The chart drawn for ``context``: column, bar, line, pie or donut."""

    picked = series(context)
    visual = context.get("visual_type")
    if picked is None:
        return None
    label, measure, rows = picked
    if visual == "clusteredBarChart":
        return "bar"
    if visual == "lineChart":
        return "line"
    if visual in ("pieChart", "donutChart"):
        if len(rows) <= MAX_SLICES and all(r[measure] >= 0 for r in rows):
            return "pie" if visual == "pieChart" else "donut"
        return "bar"
    if visual == "clusteredColumnChart":
        return "column"
    # Unknown visual: what the report would pick for one breakdown.
    longest = max(len(render.cell(context, r, label)) for r in rows)
    return "bar" if longest > 14 or len(rows) > 12 else "column"


def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _words(name):
    """"StoreName" -> "Store Name" (model column names are written joined)."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def _title(label, measure):
    return f"{_words(_column_label(measure))} by {_words(_column_label(label))}"


def chart_png(context):
    """PNG bytes of the chart, or ``None`` (see the module docstring)."""

    shape = kind(context)
    if shape is None:
        return None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    label, measure, rows = series(context)
    if shape != "line":
        # Power BI sorts these by the value, largest first.
        rows = sorted(rows, key=lambda r: r[measure], reverse=True)
    s = SCALE
    fonts = {"title": _font(15, bold=True), "text": _font(12), "small": _font(11)}
    width = WIDTH * s
    height = {"bar": 64 + len(rows) * 30 + 22, "column": 380, "line": 360,
              "pie": 340, "donut": 340}[shape] * s
    image = Image.new("RGB", (width, height), SURFACE)
    draw = ImageDraw.Draw(image)
    side = 16 * s
    draw.text((side, 14 * s), _fit(draw, _title(label, measure), fonts["title"],
                                   width - 2 * side), font=fonts["title"], fill=INK)
    subtitle = (context.get("question") or "").strip()
    if subtitle:
        draw.text((side, 36 * s), _fit(draw, subtitle, fonts["small"], width - 2 * side),
                  font=fonts["small"], fill=MUTED)
    box = (side, 64 * s, width - side, height - 12 * s)
    {"bar": _bars, "column": _columns, "line": _line, "pie": _pie,
     "donut": _pie}[shape](draw, box, context, label, measure, rows, fonts,
                           donut=shape == "donut")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _scale(values):
    low, high = min(0.0, min(values)), max(0.0, max(values))
    return low, high, (high - low) or 1.0


def _bars(draw, box, context, label, measure, rows, fonts, **_):
    s = SCALE
    left, top, right, _bottom = box
    text = fonts["text"]
    names = [render.cell(context, r, label) for r in rows]
    shown = [render.cell(context, r, measure) for r in rows]
    label_width = min(max(draw.textlength(n, font=text) for n in names) + 12 * s,
                      (right - left) * 0.34)
    value_width = max(draw.textlength(v, font=text) for v in shown) + 10 * s
    values = [float(r[measure]) for r in rows]
    low, high, span = _scale(values)
    plot_left = left + label_width + (value_width if low < 0 else 0)
    plot = right - value_width - plot_left
    zero = plot_left + (0 - low) / span * plot
    bar, gap = 20 * s, 10 * s
    for index, (name, value, value_text) in enumerate(zip(names, values, shown)):
        y = top + index * (bar + gap)
        middle = y + bar / 2
        draw.text((left, middle), _fit(draw, name, text, label_width - 12 * s), font=text,
                  fill=MUTED, anchor="lm")
        end = zero + value / span * plot
        x0, x1 = sorted((zero, end))
        if x1 - x0 >= 1:
            draw.rectangle((x0, y, x1, y + bar), fill=SERIES)
        if value >= 0:
            draw.text((x1 + 6 * s, middle), value_text, font=text, fill=INK, anchor="lm")
        else:
            draw.text((x0 - 6 * s, middle), value_text, font=text, fill=INK, anchor="rm")
    draw.line((zero, top - 4 * s, zero, top + len(rows) * (bar + gap) - gap + 4 * s),
              fill=GRID, width=s)


def _axis_labels(draw, context, label, rows, slot, font):
    """Category names under columns/points; shortened to fit their slot."""
    return [_fit(draw, render.cell(context, r, label), font, max(slot - 4 * SCALE, 10))
            for r in rows]


def _columns(draw, box, context, label, measure, rows, fonts, **_):
    s = SCALE
    left, top, right, bottom = box
    small = fonts["small"]
    values = [float(r[measure]) for r in rows]
    low, high, span = _scale(values)
    plot_top, plot_bottom = top + 18 * s, bottom - 26 * s
    plot = plot_bottom - plot_top
    zero = plot_bottom - (0 - low) / span * plot
    slot = (right - left) / len(rows)
    width = min(slot * 0.62, 56 * s)
    names = _axis_labels(draw, context, label, rows, slot, small)
    for index, (row, value) in enumerate(zip(rows, values)):
        middle = left + slot * (index + 0.5)
        end = zero - value / span * plot
        y0, y1 = sorted((zero, end))
        if y1 - y0 >= 1:
            draw.rectangle((middle - width / 2, y0, middle + width / 2, y1), fill=SERIES)
        text = render.cell(context, row, measure)
        if draw.textlength(text, font=small) <= slot:
            draw.text((middle, y0 - 4 * s if value >= 0 else y1 + 4 * s), text, font=small,
                      fill=INK, anchor="mb" if value >= 0 else "mt")
        draw.text((middle, plot_bottom + 8 * s), names[index], font=small, fill=MUTED,
                  anchor="mt")
    draw.line((left, zero, right, zero), fill=GRID, width=s)


def _line(draw, box, context, label, measure, rows, fonts, **_):
    s = SCALE
    left, top, right, bottom = box
    small = fonts["small"]
    values = [float(r[measure]) for r in rows]
    low, high = min(values), max(values)
    pad = (high - low) * 0.12 or abs(high) * 0.1 or 1.0
    low, high = low - pad, high + pad
    # Room for the first and last labels, which are centred on the end points.
    ends = [render.cell(context, rows[i], c) for i in (0, -1) for c in (label, measure)]
    margin = max(draw.textlength(t, font=small) for t in ends) / 2 + 4 * s
    plot_left, plot_right = left + margin, right - margin
    plot_top, plot_bottom = top + 18 * s, bottom - 26 * s
    for step in range(5):                                  # light gridlines
        y = plot_top + (plot_bottom - plot_top) * step / 4
        draw.line((plot_left, y, plot_right, y), fill=GRID, width=1)
    step_x = (plot_right - plot_left) / max(len(rows) - 1, 1)
    points = [(plot_left + i * step_x,
               plot_bottom - (v - low) / (high - low) * (plot_bottom - plot_top))
              for i, v in enumerate(values)]
    draw.line(points, fill=SERIES, width=2 * s, joint="curve")
    every = max(1, math.ceil(len(rows) / 12))              # at most ~12 axis labels
    names = _axis_labels(draw, context, label, rows, step_x * every, small)
    extremes = {values.index(max(values)), values.index(min(values)), len(values) - 1}
    for index, (x, y) in enumerate(points):
        draw.ellipse((x - 3 * s, y - 3 * s, x + 3 * s, y + 3 * s), fill=SERIES,
                     outline=SURFACE, width=s)
        if index in extremes:                              # selective labels
            draw.text((x, y - 7 * s), render.cell(context, rows[index], measure),
                      font=small, fill=INK, anchor="mb")
        if index % every == 0:
            draw.text((x, plot_bottom + 8 * s), names[index], font=small, fill=MUTED,
                      anchor="mt")


def _pie(draw, box, context, label, measure, rows, fonts, donut=False, **_):
    s = SCALE
    left, top, right, bottom = box
    text, small = fonts["text"], fonts["small"]
    total = sum(float(r[measure]) for r in rows) or 1.0
    size = min(bottom - top, (right - left) * 0.45)
    cx, cy = left + size / 2 + 8 * s, top + (bottom - top) / 2
    angle = -90.0
    for index, row in enumerate(rows):
        sweep = float(row[measure]) / total * 360
        draw.pieslice((cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2),
                      angle, angle + sweep, fill=THEME[index % len(THEME)],
                      outline=SURFACE, width=2 * s)
        angle += sweep
    if donut:
        hole = size * 0.3
        draw.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), fill=SURFACE)
    # Legend: colour, name, value and share.
    x = cx + size / 2 + 28 * s
    line = 24 * s
    y = cy - line * len(rows) / 2
    for index, row in enumerate(rows):
        middle = y + index * line + line / 2
        draw.rectangle((x, middle - 6 * s, x + 12 * s, middle + 6 * s),
                       fill=THEME[index % len(THEME)])
        share = float(row[measure]) / total
        entry = (f"{render.cell(context, row, label)}  ·  {render.cell(context, row, measure)}"
                 f"  ({share:.0%})")
        draw.text((x + 20 * s, middle), _fit(draw, entry, text, right - x - 20 * s),
                  font=text, fill=INK, anchor="lm")


def alt_text(context):
    picked = series(context)
    if picked is None:
        return ""
    shape = {"bar": "Bar", "column": "Column", "line": "Line", "pie": "Pie",
             "donut": "Donut"}[kind(context)]
    return f"{shape} chart: " + _title(picked[0], picked[1])
