"""Diagrams for the TECHNICAL BRIEF (non-specialist reader)."""
import sys; sys.path.insert(0, ".")
from style import *

OUT = "fig/"

# ============================================================ B1: at a glance
fig, ax = canvas(9.20, 5.92, (0, 11.5), (0, 7.4))
label(ax, 5.75, 7.15, "How the system is put together", size=14, color=INK, bold=True)
label(ax, 5.75, 6.82, "Everything above the dashed line is advisory. Everything below it is binding.",
      size=9, italic=True)

W, X = 8.4, 1.55
box(ax, X, 5.85, W, 0.72, COG, COG_BG, "1.  The person and the chat",
    "a scientist describes an experiment in ordinary words", ts=11.5)
box(ax, X, 4.92, W, 0.72, COG, COG_BG, "2.  The AI planner",
    "turns those words into a precise, written recipe  ·  proposes only", ts=11.5)
box(ax, X, 4.06, W, 0.62, COG, "#eef3fb", "3.  The recipe  (\"experiment specification\")",
    "a list of numbered operations with exact numbers and units", ts=10.5, ss=8)

yb = 3.86
ax.plot([X-0.5, X+W+0.5], [yb, yb], color=INK, lw=2.4, ls=(0, (6, 3)), zorder=6)
label(ax, X+W+0.55, yb, "THE GATE", size=9.5, color=INK, ha="left", bold=True)

box(ax, X, 2.30, W, 1.38, DET, DET_BG, "", "")
label(ax, X+W/2, 3.50, "4.  The platform  (\"labgate\")", size=11.5, color=INK, bold=True)
for i, t in enumerate([
        "checks the recipe against the real physical limits — and refuses it if it breaks one",
        "shows a human what will happen (time, path picture) and waits for a signature",
        "runs the approved recipe itself, and writes down everything that happened"]):
    label(ax, X+0.34, 3.18-i*0.29, "•  " + t, size=8.6, color="#143d28", ha="left")

box(ax, X, 1.44, W, 0.66, DET, "#e6f3ea", "5.  Device drivers",
    "one small translator per instrument: stage · laser · camera · white light", ts=10.5, ss=8)
box(ax, X, 0.72, W, 0.56, SAFE, SAFE_BG, "6.  The instruments  —  and the physical safety interlocks",
    "", ts=10.5, tcol=SAFE)
label(ax, X+W/2, 0.42, "e-stop, shutter key and enclosure cut the beam no matter what any software says",
      size=8.2, italic=True)

arrow(ax, (0.62, 6.2), (0.62, 0.95), color=MUTE, lw=1.3)
label(ax, 0.34, 3.55, "what the person wants   →   what the machine does", size=8.4, rot=90)
save(fig, OUT + "b1_at_a_glance.png")

# ======================================================= B2: journey of a request
fig, ax = canvas(9.85, 3.91, (0, 12.6), (0, 5.0))
label(ax, 6.3, 4.75, "The journey of one request", size=14, color=INK, bold=True)

steps = [
    ("1. Ask", "a person types\nwhat they want", HUM, HUM_BG),
    ("2. Plan", "the AI writes\nthe recipe", COG, COG_BG),
    ("3. Check", "platform tests it\nagainst hard limits", DET, DET_BG),
    ("4. Preview", "time, path picture,\nrisk summary", DET, DET_BG),
    ("5. Approve", "a qualified human\nsigns  (not the author)", HUM, HUM_BG),
    ("6. Run", "platform drives\nthe instruments", DET, DET_BG),
    ("7. Report", "images + full log\nreturned for analysis", DET, DET_BG),
]
bw, gap, yb, bh = 1.53, 0.20, 1.62, 1.62
x0 = (12.6 - (len(steps)*bw + (len(steps)-1)*gap)) / 2
xs = []
for i, (t, s, e, f) in enumerate(steps):
    x = x0 + i*(bw+gap); xs.append(x)
    box(ax, x, yb, bw, bh, e, f, t, s, ts=11, ss=8)
    if i < len(steps)-1:
        arrow(ax, (x+bw+0.015, yb+bh/2), (x+bw+gap-0.015, yb+bh/2), ms=12, lw=1.4)

xb = (xs[1]+bw + xs[2]) / 2
ax.plot([xb, xb], [yb-0.62, yb+bh+0.5], color=INK, lw=2.2, ls=(0, (5, 3)), zorder=6)
label(ax, xb, yb+bh+0.60, "THE GATE", size=9, color=INK, bold=True)
label(ax, xs[1]+bw/2, yb-0.40, "the AI proposes", size=9, color=COG, bold=True)
label(ax, xs[4]+bw/2, yb-0.40, "the platform decides, a human authorises", size=9, color=DET, bold=True)

label(ax, 6.3, 0.62, "If step 3 fails, the recipe is refused with a written reason and never reaches a human for approval.",
      size=8.6, italic=True)
label(ax, 6.3, 0.34, "The recipe from step 2 is kept and can be re-run later without any AI involved.",
      size=8.6, italic=True)
legend(ax, 0.55, 4.28, [("person", HUM, HUM_BG), ("AI", COG, COG_BG),
                        ("platform", DET, DET_BG)], dx=1.55)
save(fig, OUT + "b2_journey.png")

# ============================================================ B3: what we store
fig, ax = canvas(9.30, 5.29, (0, 11.6), (0, 6.6))
label(ax, 5.8, 6.32, "What the system remembers", size=14, color=INK, bold=True)
label(ax, 5.8, 6.00, "Four kinds of record, written as plain files on the lab computer — readable without any special tool",
      size=9, italic=True)

cards = [
    ("THE RECIPES", "plans/", "One file per experiment ever proposed.\nHolds the recipe itself, who wrote it,\nwho approved it, the verdict of every\nsafety check, and its current status.",
     "answers: what was asked for,\nand was it allowed?"),
    ("THE RESULTS", "runs/", "One folder per execution. A timestamped\ndiary of what actually happened, plus the\nimages taken — each stamped with the\nstage position and laser settings.",
     "answers: what actually happened,\nand what did it look like?"),
    ("THE LEDGER", "audit.jsonl", "One append-only line per consequential\naction: submitted, validated, approved,\nstarted, finished, aborted, failed —\neach with a named person or agent.",
     "answers: who authorised this,\nand when?"),
    ("THE MODELS", "models/", "Uploaded 3D shapes (STL files) plus their\nmeasured size, stored under a fingerprint\nof their content so the same shape can\nnever be confused with a different one.",
     "answers: exactly which shape\nwas printed?"),
]
bw, bh, gap = 5.35, 2.35, 0.42
for i, (title, path, body, q) in enumerate(cards):
    x = 0.35 + (i % 2) * (bw + gap)
    y = 3.28 - (i // 2) * (bh + gap)
    box(ax, x, y, bw, bh, STOR, STOR_BG, "", "")
    label(ax, x+0.3, y+bh-0.28, title, size=10.5, color=STOR, ha="left", bold=True)
    label(ax, x+bw-0.3, y+bh-0.28, path, size=9, color=MUTE, ha="right")
    label(ax, x+0.3, y+bh-0.95, body, size=8.4, color=INK, ha="left", va="center")
    label(ax, x+0.3, y+0.36, q, size=8.2, color=STOR, ha="left", va="center", italic=True)

label(ax, 5.8, 0.30, "Nothing is ever overwritten or deleted: a changed experiment becomes a new record, so the history stays true.",
      size=8.8, italic=True, color=INK)
save(fig, OUT + "b3_storage.png")
print("brief figures done")
