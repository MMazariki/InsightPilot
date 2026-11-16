"""Generate assets/insightpilot.png and assets/insightpilot.ico (run once)."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
OUT = Path(__file__).resolve().parents[1] / "assets"


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


gradient = Image.new("RGB", (S, S))
pixels = gradient.load()
for y in range(S):
    for x in range(S):
        pixels[x, y] = lerp((11, 31, 75), (34, 81, 209), (x + y) / (2 * S))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=230, fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(gradient, (0, 0), mask)

draw = ImageDraw.Draw(icon, "RGBA")
for x, top, alpha in ((200, 600, 150), (425, 470, 205), (650, 340, 255)):
    draw.rounded_rectangle((x, top, x + 150, 830), radius=40,
                           fill=(255, 255, 255, alpha))
TEAL = (94, 234, 212, 255)
line = [(190, 440), (440, 290), (575, 365), (830, 190)]
draw.line(line, fill=TEAL, width=64, joint="curve")
for px, py in (line[0], line[-1]):
    draw.ellipse((px - 32, py - 32, px + 32, py + 32), fill=TEAL)
draw.line([(690, 180), (830, 190), (800, 330)], fill=TEAL, width=64, joint="curve")
for px, py in ((690, 180), (800, 330)):
    draw.ellipse((px - 32, py - 32, px + 32, py + 32), fill=TEAL)

OUT.mkdir(exist_ok=True)
icon.resize((256, 256), Image.LANCZOS).save(OUT / "insightpilot.png")
icon.save(OUT / "insightpilot.ico",
          sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
print("saved", OUT)
