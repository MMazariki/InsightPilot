"""A chart picture (PNG) of an answer, for emails.

Drawn with Pillow, on this computer. A horizontal bar chart: one bar per
row of the answer in the table's order, the answer's main measure, its
value written at the end of each bar. Horizontal bars keep long store and
product names readable on a phone. One series, so no legend: the title
names the measure. Returns ``None`` when the answer has no label column, no
numeric column, one row only or more than ``MAX_BARS`` rows (the table
says it better then).
"""

import io
import re
from pathlib import Path

from src.actions import render
from src.structured_agent import _column_label

MAX_BARS = 25
SCALE = 2                      # drawn at 2x and shown at half size: sharp text
WIDTH = 720                    # shown width in the email (pixels)

INK = (16, 24, 40)             # values and title
MUTED = (71, 84, 103)          # row labels, subtitle
LINE = (208, 213, 221)         # baseline
BAR = (34, 81, 209)            # brand blue
BAR_NEGATIVE = (217, 45, 32)   # a negative value (a decline)
SURFACE = (255, 255, 255)

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


def series(context):
    """``(label column, measure column, rows)`` the chart would show, or ``None``."""

    rows = context.get("rows") or []
    numeric = render._numeric_columns(context)
    label = render._label_column(context)
    if not numeric or not label or not 2 <= len(rows) <= MAX_BARS:
        return None
    measure = numeric[-1]
    rows = [r for r in rows if r.get(measure) is not None]
    return (label, measure, rows) if len(rows) >= 2 else None


def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def chart_png(context):
    """PNG bytes of the chart, or ``None`` (see the module docstring)."""

    picked = series(context)
    if picked is None:
        return None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    label, measure, rows = picked
    s = SCALE
    title_font, text_font, small_font = _font(15, bold=True), _font(12), _font(11)
    bar_height, gap, top, bottom, side = 20 * s, 10 * s, 64 * s, 22 * s, 16 * s
    width = WIDTH * s
    height = top + len(rows) * (bar_height + gap) + bottom

    image = Image.new("RGB", (width, height), SURFACE)
    draw = ImageDraw.Draw(image)
    draw.text((side, 14 * s), _fit(draw, _title(label, measure), title_font,
                                   width - 2 * side), font=title_font, fill=INK)
    subtitle = (context.get("question") or "").strip()
    if subtitle:
        draw.text((side, 36 * s), _fit(draw, subtitle, small_font, width - 2 * side),
                  font=small_font, fill=MUTED)

    label_width = min(max(draw.textlength(render.cell(context, r, label), font=text_font)
                          for r in rows) + 12 * s, width * 0.32)
    value_texts = [render.cell(context, r, measure) for r in rows]
    value_width = max(draw.textlength(v, font=text_font) for v in value_texts) + 10 * s
    values = [float(r[measure]) for r in rows]
    low, high = min(0.0, min(values)), max(0.0, max(values))
    span = (high - low) or 1.0
    plot_left = side + label_width
    plot_right = width - side - value_width
    if low < 0:                          # room for the value left of a negative bar
        plot_left += value_width
    plot = plot_right - plot_left
    zero = plot_left + (0 - low) / span * plot

    for index, (row, value, text) in enumerate(zip(rows, values, value_texts)):
        y = top + index * (bar_height + gap)
        middle = y + bar_height / 2
        name_text = _fit(draw, render.cell(context, row, label), text_font, label_width - 12 * s)
        draw.text((side, middle), name_text, font=text_font, fill=MUTED, anchor="lm")
        end = zero + value / span * plot
        x0, x1 = sorted((zero, end))
        if x1 - x0 >= 1:
            draw.rounded_rectangle((x0, y, x1, y + bar_height), radius=4 * s,
                                   fill=BAR if value >= 0 else BAR_NEGATIVE)
        if value >= 0:
            draw.text((x1 + 6 * s, middle), text, font=text_font, fill=INK, anchor="lm")
        else:
            draw.text((x0 - 6 * s, middle), text, font=text_font, fill=INK, anchor="rm")
    # The zero line the bars grow from.
    draw.line((zero, top - 4 * s, zero, top + len(rows) * (bar_height + gap) - gap + 4 * s),
              fill=LINE, width=s)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _words(name):
    """"StoreName" -> "Store Name" (model column names are written joined)."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def _title(label, measure):
    return f"{_words(_column_label(measure))} by {_words(_column_label(label))}"


def alt_text(context):
    picked = series(context)
    if picked is None:
        return ""
    return "Bar chart: " + _title(picked[0], picked[1])
