"""A chart picture (PNG) of an answer, for emails: the report's chart.

Drawn with Pillow on this computer. The picture shows what the visual in
the Power BI report shows for the same answer:

* ``context["chart_data"]``: the visual's own points read from Power BI
  (``src/actions/visual_data.py``) - same type, categories, legend
  (series), filters and top N as the report. Used when available.
* otherwise the answer table, drawn as the report's visual type
  (``context["visual_type"]``), one series.

Types: column and bar (grouped when there is a legend), line (one line per
series), column-and-line combination, pie and donut. Like Power BI's
defaults: the default theme colours, columns and bars largest first, time
in time order, values shortened (412.3K), a legend when there are several
series. Visuals shown as a table or a card get no picture.
"""

import io
import math
import re
from pathlib import Path

from src.actions import render
from src.structured_agent import _column_label

MAX_CATEGORIES = 40
MAX_SERIES = 20
MAX_SLICES = 10
SCALE = 2                      # drawn at 2x and shown at half size: sharp text
WIDTH = 720                    # shown width in the email (pixels)

INK = (37, 36, 35)             # Power BI default text #252423
MUTED = (96, 94, 92)           # axis labels #605E5C
GRID = (225, 223, 221)
SURFACE = (255, 255, 255)
# Power BI default theme data colours, in order.
THEME = [(17, 141, 255), (18, 35, 158), (230, 108, 55), (107, 0, 123), (224, 68, 167),
         (116, 78, 194), (217, 179, 0), (214, 69, 80), (25, 125, 44), (180, 90, 0),
         (96, 174, 255), (88, 104, 196), (240, 152, 115), (166, 77, 176), (238, 143, 202),
         (163, 138, 216), (232, 207, 77), (229, 136, 143), (102, 175, 116), (214, 156, 92)]

NO_PICTURE = {"tableEx", "card", "multiRowCard"}
SHAPES = {"clusteredColumnChart": "column", "clusteredBarChart": "bar",
          "lineChart": "line", "pieChart": "pie", "donutChart": "donut",
          "lineClusteredColumnComboChart": "combo"}

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


def _words(name):
    """"StoreName" -> "Store Name" (model column names are written joined)."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def short(value, percent=False):
    """Power BI's automatic display units: 412.3K, 1.25M, 88.1%."""

    if value is None:
        return ""
    if percent:
        return f"{value:.1%}"
    size = abs(value)
    for limit, unit in ((1e9, "bn"), (1e6, "M"), (1e3, "K")):
        if size >= limit:
            text = f"{value / limit:.2f}".rstrip("0").rstrip(".")
            return text + unit
    return f"{value:,.2f}".rstrip("0").rstrip(".") if size < 100 else f"{value:,.0f}"


# ------------------------------------------------------------------- data

def _from_table(context):
    """The answer table as one series (when the visual's own points are not
    available)."""

    rows = context.get("rows") or []
    numeric = render._numeric_columns(context)
    # Percentages already written as text ("272.1%") are numbers, not labels.
    formatted = re.compile(r"^\s*[-+]?[\d,]+(?:\.\d+)?\s*%\s*$")
    labels = [c for c in context["columns"] if c not in set(numeric)
              and not all(formatted.match(str(r.get(c, ""))) for r in rows)]
    # Two breakdowns are a legend in the report: one series would mislead.
    if not numeric or len(labels) != 1 or not 2 <= len(rows) <= MAX_CATEGORIES:
        return None
    label, measure = labels[0], numeric[-1]
    name = _column_label(measure)
    points = [{"category": r.get(label), "label": render.cell(context, r, label),
               "series": None, "values": [float(r[measure])]}
              for r in rows if r.get(measure) is not None]
    if len(points) < 2:
        return None
    return {"type": context.get("visual_type"), "category": _words(_column_label(label)),
            "series": None, "measures": [name], "percent": [name.rstrip().endswith("%")],
            "points": points, "time": False, "keep_order": False}


def data(context):
    """What the picture shows (see the module docstring), or ``None``."""

    if context.get("visual_type") in NO_PICTURE:
        return None
    found = context.get("chart_data")
    if found and found.get("points"):
        return found
    return _from_table(context)


def kind(context):
    """column, bar, line, combo, pie or donut; ``None``: no picture."""

    found = data(context)
    if found is None:
        return None
    categories = {p["label"] for p in found["points"]}
    series = {p["series"] for p in found["points"]}
    if len(categories) > MAX_CATEGORIES or len(series) > MAX_SERIES or len(found["points"]) < 2:
        return None
    shape = SHAPES.get(found.get("type"))
    if shape in ("pie", "donut") and (len(series) > 1 or len(categories) > MAX_SLICES
                                       or any(p["values"][0] < 0 for p in found["points"])):
        shape = "bar"
    if shape == "combo" and len(found["measures"]) < 2:
        shape = "column"
    if shape is None:                     # not known: what the report would pick
        if found.get("time"):
            return "line"
        longest = max(len(c) for c in categories)
        return "bar" if longest > 14 or len(categories) > 12 else "column"
    return shape


def has_chart(context):
    try:
        return kind(context) is not None
    except Exception:
        return False


def _title(found):
    names = found["measures"]
    what = names[0] if len(names) == 1 else " and ".join(names)
    by = found["category"] + (f" and {found['series']}" if found.get("series") else "")
    return f"{_words(what)} by {by}"


def alt_text(context):
    shape = kind(context)
    if shape is None:
        return ""
    return f"{shape.capitalize()} chart: " + _title(data(context))


# -------------------------------------------------------------- drawing

def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _layout(found, shape):
    """Categories (in drawing order), series names, and a value lookup."""

    points = found["points"]
    series = sorted({p["series"] for p in points}, key=lambda s: (s is None, str(s)))
    categories, seen = [], set()
    for p in points:
        if p["label"] not in seen:
            seen.add(p["label"])
            categories.append(p["label"])
    value = {(p["label"], p["series"]): p["values"] for p in points}
    if shape in ("column", "bar", "pie", "donut", "combo") and not found.get("time"):
        # Power BI sorts these by the (first) value, largest first.
        total = {c: sum((value.get((c, s)) or [0])[0] or 0 for s in series)
                 for c in categories}
        categories.sort(key=lambda c: total[c], reverse=True)
    return categories, series, value


def chart_png(context):
    """PNG bytes of the chart, or ``None`` (see the module docstring)."""

    shape = kind(context)
    if shape is None:
        return None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    found = data(context)
    categories, series, value = _layout(found, shape)
    s = SCALE
    fonts = {"title": _font(15, bold=True), "text": _font(12), "small": _font(11)}
    width = WIDTH * s
    side = 16 * s
    # A legend for several series (or the two measures of a combo).
    legend = ([str(x) for x in series] if len(series) > 1 else
              found["measures"] if shape == "combo" else [])
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    legend_rows = _legend_rows(probe, legend, fonts["small"], width - 2 * side)
    legend_height = len(legend_rows) * 20 * s
    group = 20 if len(series) == 1 else 12 * len(series)      # one bar group, px
    body = {"bar": len(categories) * (group + 10) + 10, "column": 300, "line": 290,
            "combo": 300, "pie": 280, "donut": 280}[shape] * s
    height = 64 * s + legend_height + body + 16 * s
    image = Image.new("RGB", (width, height), SURFACE)
    draw = ImageDraw.Draw(image)
    draw.text((side, 14 * s), _fit(draw, _title(found), fonts["title"], width - 2 * side),
              font=fonts["title"], fill=INK)
    subtitle = (context.get("question") or "").strip()
    if subtitle:
        draw.text((side, 36 * s), _fit(draw, subtitle, fonts["small"], width - 2 * side),
                  font=fonts["small"], fill=MUTED)
    y = 62 * s
    for row in legend_rows:
        x = side
        for index, name in row:
            colour = THEME[index % len(THEME)]
            if shape == "combo" and index == 1:
                draw.line((x, y + 8 * s, x + 14 * s, y + 8 * s), fill=colour, width=3 * s)
            else:
                draw.ellipse((x + 2 * s, y + 3 * s, x + 12 * s, y + 13 * s), fill=colour)
            draw.text((x + 18 * s, y + 8 * s), name, font=fonts["small"], fill=MUTED,
                      anchor="lm")
            x += 18 * s + draw.textlength(name, font=fonts["small"]) + 16 * s
        y += 20 * s
    box = (side, y + 6 * s, width - side, height - 12 * s)
    {"bar": _bars, "column": _columns, "line": _lines, "combo": _combo,
     "pie": _pie, "donut": _pie}[shape](draw, box, found, categories, series, value,
                                         fonts, donut=shape == "donut")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _legend_rows(draw, names, font, width):
    rows, row, used = [], [], 0
    for index, name in enumerate(names):
        name = _fit(draw, name, font, width / 2)
        size = 18 * SCALE + draw.textlength(name, font=font) + 16 * SCALE
        if row and used + size > width:
            rows.append(row)
            row, used = [], 0
        row.append((index, name))
        used += size
    if row:
        rows.append(row)
    return rows


def _colour(series, name):
    return THEME[series.index(name) % len(THEME)] if len(series) > 1 else THEME[0]


def _nice_range(values):
    low, high = min(0.0, min(values)), max(0.0, max(values))
    if high == low:
        high = low + 1
    step = 10 ** math.floor(math.log10((high - low) / 4))
    for multiple in (1, 2, 2.5, 5, 10):
        if (high - low) / (step * multiple) <= 5:
            step *= multiple
            break
    return math.floor(low / step) * step, math.ceil(high / step) * step, step


def _y_axis(draw, left, right, top, bottom, values, percent, font):
    """Gridlines with shortened labels; returns ``(to_y, plot_left)``."""

    low, high, step = _nice_range(values)
    ticks = [low + i * step for i in range(int(round((high - low) / step)) + 1)]
    labels = [short(t, percent) for t in ticks]
    plot_left = left + max(draw.textlength(t, font=font) for t in labels) + 8 * SCALE

    def to_y(v):
        return bottom - (v - low) / (high - low) * (bottom - top)

    for tick, text in zip(ticks, labels):
        y = to_y(tick)
        draw.line((plot_left, y, right, y), fill=GRID, width=1)
        draw.text((plot_left - 6 * SCALE, y), text, font=font, fill=MUTED, anchor="rm")
    return to_y, plot_left


def _axis_labels(draw, categories, slot, font, y, centres):
    every = max(1, math.ceil(len(categories) / max(1, int(slot * len(categories) /
                                                            (60 * SCALE)))))
    for index, (name, x) in enumerate(zip(categories, centres)):
        if index % every == 0:
            draw.text((x, y), _fit(draw, str(name), font, slot * every - 4 * SCALE),
                      font=font, fill=MUTED, anchor="mt")


def _columns(draw, box, found, categories, series, value, fonts, **_):
    s = SCALE
    left, top, right, bottom = box
    small = fonts["small"]
    percent = found["percent"][0]
    numbers = [v[0] for v in value.values() if v and v[0] is not None]
    to_y, plot_left = _y_axis(draw, left, right, top + 16 * s, bottom - 24 * s, numbers,
                              percent, small)
    slot = (right - plot_left) / len(categories)
    group = min(slot * 0.7, 64 * s * max(1, len(series)))
    bar = group / len(series)
    centres = []
    for index, category in enumerate(categories):
        centre = plot_left + slot * (index + 0.5)
        centres.append(centre)
        for position, name in enumerate(series):
            number = (value.get((category, name)) or [None])[0]
            if number is None:
                continue
            x0 = centre - group / 2 + position * bar
            y0, y1 = sorted((to_y(0), to_y(number)))
            draw.rectangle((x0, y0, x0 + bar - (s if len(series) > 1 else 0), y1),
                           fill=_colour(series, name))
            if len(series) == 1:
                text = short(number, percent)
                if draw.textlength(text, font=small) <= slot:
                    draw.text((centre, y0 - 4 * s if number >= 0 else y1 + 4 * s), text,
                              font=small, fill=INK, anchor="mb" if number >= 0 else "mt")
    _axis_labels(draw, categories, slot, small, bottom - 18 * s, centres)


def _bars(draw, box, found, categories, series, value, fonts, **_):
    s = SCALE
    left, top, right, _bottom = box
    text, small = fonts["text"], fonts["small"]
    percent = found["percent"][0]
    numbers = [v[0] for v in value.values() if v and v[0] is not None]
    label_width = min(max(draw.textlength(str(c), font=text) for c in categories) + 12 * s,
                      (right - left) * 0.34)
    value_width = max(draw.textlength(short(n, percent), font=small) for n in numbers) + 10 * s
    low, high = min(0.0, min(numbers)), max(0.0, max(numbers))
    span = (high - low) or 1.0
    plot_left = left + label_width + (value_width if low < 0 else 0)
    plot = right - value_width - plot_left
    zero = plot_left + (0 - low) / span * plot
    bar = 20 * s if len(series) == 1 else 12 * s
    gap = 10 * s
    y = top
    for category in categories:
        group_top = y
        for name in series:
            number = (value.get((category, name)) or [None])[0]
            if number is not None:
                end = zero + number / span * plot
                x0, x1 = sorted((zero, end))
                draw.rectangle((x0, y, x1, y + bar - (s if len(series) > 1 else 0)),
                               fill=_colour(series, name))
                label = short(number, percent)
                if number >= 0:
                    draw.text((x1 + 5 * s, y + bar / 2), label, font=small, fill=INK,
                              anchor="lm")
                else:
                    draw.text((x0 - 5 * s, y + bar / 2), label, font=small, fill=INK,
                              anchor="rm")
            y += bar
        draw.text((left, (group_top + y) / 2), _fit(draw, str(category), text,
                                                    label_width - 12 * s),
                  font=text, fill=MUTED, anchor="lm")
        y += gap
    draw.line((zero, top - 4 * s, zero, y - gap + 4 * s), fill=GRID, width=s)


def _lines(draw, box, found, categories, series, value, fonts, measure=0, to_y=None,
           plot=None, colour=None, labels=True, **_):
    s = SCALE
    left, top, right, bottom = box
    small = fonts["small"]
    percent = found["percent"][measure]
    if to_y is None:
        numbers = [v[measure] for v in value.values() if v and v[measure] is not None]
        to_y, plot_left = _y_axis(draw, left, right, top + 16 * s, bottom - 24 * s,
                                  numbers, percent, small)
        margin = draw.textlength(str(categories[-1]), font=small) / 2
        plot = (plot_left + 12 * s, right - max(margin, 12 * s))
    step = (plot[1] - plot[0]) / max(len(categories) - 1, 1)
    xs = [plot[0] + i * step for i in range(len(categories))]
    for name in series:
        points = [(x, to_y(value[(c, name)][measure])) for x, c in zip(xs, categories)
                  if (c, name) in value and value[(c, name)][measure] is not None]
        tone = colour or _colour(series, name)
        if len(points) > 1:
            draw.line(points, fill=tone, width=2 * s, joint="curve")
        if len(series) == 1 and len(points) <= 24:
            for x, y in points:
                draw.ellipse((x - 3 * s, y - 3 * s, x + 3 * s, y + 3 * s), fill=tone,
                             outline=SURFACE, width=s)
    if labels and len(series) == 1:              # highest, lowest and last: selective
        name = series[0]
        numbers = [(i, value[(c, name)][measure]) for i, c in enumerate(categories)
                   if (c, name) in value and value[(c, name)][measure] is not None]
        if numbers:
            marks = {max(numbers, key=lambda p: p[1])[0], min(numbers, key=lambda p: p[1])[0],
                     numbers[-1][0]}
            for i, number in numbers:
                if i in marks:
                    draw.text((xs[i], to_y(number) - 7 * s), short(number, percent),
                              font=small, fill=INK, anchor="mb")
    if to_y is not None and labels is not None and measure == 0:
        _axis_labels(draw, categories, step if len(categories) > 1 else plot[1] - plot[0],
                     small, bottom - 18 * s, xs)


def _combo(draw, box, found, categories, series, value, fonts, **_):
    """Columns for the first measure, a line for the second (its own scale,
    as Power BI's combination chart)."""

    s = SCALE
    left, top, right, bottom = box
    small = fonts["small"]
    single = [series[0]]
    _columns(draw, box, {**found, "percent": found["percent"][:1]}, categories, single,
             {k: v[:1] for k, v in value.items()}, fonts)
    numbers = [v[1] for v in value.values() if len(v) > 1 and v[1] is not None]
    if not numbers:
        return
    low, high = min(numbers), max(numbers)
    pad = (high - low) * 0.15 or abs(high) * 0.1 or 1.0
    low, high = low - pad, high + pad
    plot_top, plot_bottom = top + 16 * s, bottom - 24 * s

    def to_y(v):
        return plot_bottom - (v - low) / (high - low) * (plot_bottom - plot_top)

    # The same x positions as the columns.
    probe_low, probe_high, _ = _nice_range([v[0] for v in value.values() if v[0] is not None])
    labels = [short(t, found["percent"][0]) for t in (probe_low, probe_high)]
    plot_left = left + max(draw.textlength(t, font=small) for t in labels) + 8 * s
    slot = (right - plot_left) / len(categories)
    plot = (plot_left + slot / 2, right - slot / 2)
    _lines(draw, box, found, categories, single, value, fonts, measure=1, to_y=to_y,
           plot=plot, colour=THEME[1], labels=None)


def _pie(draw, box, found, categories, series, value, fonts, donut=False, **_):
    s = SCALE
    left, top, right, bottom = box
    text = fonts["text"]
    name = series[0]
    numbers = [(c, value[(c, name)][0]) for c in categories if (c, name) in value]
    total = sum(n for _, n in numbers) or 1.0
    size = min(bottom - top, (right - left) * 0.45)
    cx, cy = left + size / 2 + 8 * s, top + (bottom - top) / 2
    angle = -90.0
    for index, (_, number) in enumerate(numbers):
        sweep = number / total * 360
        draw.pieslice((cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2),
                      angle, angle + sweep, fill=THEME[index % len(THEME)],
                      outline=SURFACE, width=2 * s)
        angle += sweep
    if donut:
        hole = size * 0.3
        draw.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), fill=SURFACE)
    x = cx + size / 2 + 28 * s
    line = 24 * s
    y = cy - line * len(numbers) / 2
    for index, (category, number) in enumerate(numbers):
        middle = y + index * line + line / 2
        draw.rectangle((x, middle - 6 * s, x + 12 * s, middle + 6 * s),
                       fill=THEME[index % len(THEME)])
        entry = (f"{category}  ·  {short(number, found['percent'][0])}"
                 f"  ({number / total:.0%})")
        draw.text((x + 20 * s, middle), _fit(draw, entry, text, right - x - 20 * s),
                  font=text, fill=INK, anchor="lm")
