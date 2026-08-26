"""D3 state machine · D4 validation · D5 engine internals · D6 storage model."""
import sys; sys.path.insert(0, ".")
from style import *
OUT = "fig/"

# ============================================================ D3: state machine
fig, ax = canvas(9.45, 5.18, (0, 12.6), (0, 6.9))
label(ax, 6.3, 6.58, "The life of a plan  —  the only transitions the server will allow",
      size=13, color=INK, bold=True)
label(ax, 6.3, 6.28, "Every arrow is enforced in lifecycle.py. Any move not drawn here is answered with 409 Conflict.",
      size=8.6, italic=True)

NW, NH = 1.78, 0.88
R1, R2 = 4.55, 2.35
main = [("draft", 0.42, COG, COG_BG, "written down,\nnot yet judged"),
        ("validated", 2.82, DET, DET_BG, "passed every\nhard check"),
        ("approved", 5.22, HUM, HUM_BG, "a named person\nsigned for it"),
        ("queued", 7.62, DET, DET_BG, "waiting for\nthe rig"),
        ("running", 10.02, DET, DET_BG, "instruments\nare moving")]
pos = {}
for name, x, e, fc, sub in main:
    box(ax, x, R1, NW, NH, e, fc, name, sub, ts=10.5, ss=7.4)
    pos[name] = (x, R1)
term = [("rejected", 2.82, SAFE, SAFE_BG, "a hard limit\nwas broken"),
        ("aborted", 7.62, HUM, HUM_BG, "a human\nstopped it"),
        ("completed", 10.02, DET, "#cfe8d8", "finished\nnormally"),
        ("failed", 5.22, SAFE, SAFE_BG, "fault → safe\nstate forced")]
for name, x, e, fc, sub in term:
    box(ax, x, R2, NW, NH, e, fc, name, sub, ts=10.5, ss=7.4)
    pos[name] = (x, R2)
    label(ax, x+NW/2, R2-0.20, "terminal", size=7, color=MUTE, italic=True)

def across(a, b, color, text):
    (ax_, ay), (bx, by) = pos[a], pos[b]
    arrow(ax, (ax_+NW, ay+NH/2), (bx, by+NH/2), color=color, lw=1.7)
    label(ax, (ax_+NW+bx)/2, ay+NH/2+0.22, text, size=7.6, color=color, bold=True, bg="white")

def down(a, b, color, text, dx=0.0, tside=0.0, ty=None):
    (ax_, ay), (bx, by) = pos[a], pos[b]
    arrow(ax, (ax_+NW/2+dx, ay), (bx+NW/2+dx, by+NH), color=color, lw=1.7)
    label(ax, ax_+NW/2+dx+tside, ty if ty is not None else (ay+by+NH)/2, text,
          size=7.6, color=color, bold=True, bg="white")

across("draft", "validated", DET, "all checks pass")
across("validated", "approved", HUM, "POST /approve")
across("approved", "queued", DET, "POST /execute")
across("queued", "running", DET, "worker starts it")
down("validated", "rejected", SAFE, "any hard-limit error")
down("queued", "aborted", HUM, "abort while queued", dx=-0.45, tside=-0.05, ty=3.50)
down("running", "completed", DET, "all ops done")
def route(points, color, lw=1.7):
    for p, q in zip(points[:-1], points[1:-1] + [points[-1]]):
        pass
    for i in range(len(points)-2):
        ax.plot([points[i][0], points[i+1][0]], [points[i][1], points[i+1][1]],
                color=color, lw=lw, zorder=4, solid_capstyle="round")
    arrow(ax, points[-2], points[-1], color=color, lw=lw)

rx, ry = pos["running"]
# nearer target routes LOWER, farther routes HIGHER -> no crossings
route([(rx+0.28, ry), (rx+0.28, 3.98), (pos["failed"][0]+NW/2, 3.98),
       (pos["failed"][0]+NW/2, R2+NH)], SAFE)
label(ax, 7.55, 4.12, "any fault", size=7.6, color=SAFE, bold=True, bg="white")
route([(rx+0.62, ry), (rx+0.62, 3.72), (pos["aborted"][0]+NW/2+0.30, 3.72),
       (pos["aborted"][0]+NW/2+0.30, R2+NH)], HUM)
label(ax, 9.55, 3.86, "POST /abort", size=7.6, color=HUM, bold=True, bg="white")

label(ax, 0.42, 1.72, "A rejected, completed, failed or aborted plan can never be re-opened.\n"
      "Re-running means POST /plans/<id>/rerun, which creates a NEW plan that must be\n"
      "validated and approved all over again — so the record of what was authorised stays true.",
      size=8.4, color=INK, ha="left", va="top")
save(fig, OUT + "d3_state_machine.png")

# ============================================================ D4: validation
fig, ax = canvas(9.30, 4.99, (0, 12.4), (1.55, 8.2))
label(ax, 6.2, 7.90, "What the checker actually checks", size=13, color=INK, bold=True)
label(ax, 6.2, 7.60, "Four families of test, run on every submission. One error anywhere = the whole plan is refused.",
      size=8.6, italic=True)

checks = [
    ("1.  Bounds", "_check_bounds()",
     "Every number against the limit table that the\ndrivers themselves publish.",
     "attenuator 150 %\n→ \"150.0% outside [0.0, 100.0]\""),
    ("2.  Geometry", "_check_geometry()",
     "Expands the recipe into the real trajectory and\ntests EVERY point — including sliced STL paths.",
     "line ending at x = 40 mm\n→ \"outside travel range [-25.0, 25.0]\""),
    ("3.  Ordering & interlocks", "_check_ordering()",
     "Rules between devices: power must be set before\nfiring; white light must be off during exposure.",
     "write before set_laser_power\n→ \"write operation before any power\""),
    ("4.  Exposure quality", "_check_exposure_uniformity()",
     "Is each line long enough to reach full speed before\nthe beam opens?  Warning only — never a refusal.",
     "0.5 mm line vs 1.58 mm ramp\n→ warning: \"may be non-uniform\""),
]
bw, bh = 5.70, 1.95
for i, (title, fn, what, ex) in enumerate(checks):
    x = 0.35 + (i % 2) * (bw + 0.30)
    y = 5.30 - (i // 2) * (bh + 0.30)
    edge = HUM if i == 3 else DET
    face = HUM_BG if i == 3 else DET_BG
    box(ax, x, y, bw, bh, edge, face, "", "")
    ax.text(x+0.24, y+bh-0.18, title, fontsize=10, weight="bold", color=INK, va="top", zorder=3)
    ax.text(x+0.24, y+bh-0.52, fn, fontsize=7.6, color=MUTE,
            family="DejaVu Sans Mono", va="top", zorder=3)
    ax.text(x+0.24, y+bh-0.86, what, fontsize=8.2, color=INK, va="top", zorder=3, linespacing=1.35)
    ax.text(x+0.24, y+0.14, ex, fontsize=7.8, color="#7a3030" if i != 3 else "#7a5a1a",
            va="bottom", zorder=3, family="DejaVu Sans Mono", linespacing=1.45)

box(ax, 0.35, 1.72, 11.75, 1.10, STOR, STOR_BG, "", "")
ax.text(0.60, 3.02, "The verdict is written into the plan record and returned to the caller",
        fontsize=10, weight="bold", color=INK, va="top", zorder=3)
ax.text(0.60, 2.28, 'so the AI can read exactly what it got wrong and submit a corrected plan,\n'
        'and so a human reviewer can see, months later, what was checked and what it said.',
        fontsize=8.2, color=MUTE, va="top", zorder=3, style="italic", linespacing=1.4)
save(fig, OUT + "d4_validation.png")
print("D3, D4 done")

# ==================================================== D5: engine internals
fig, ax = canvas(9.45, 6.00, (0, 12.6), (0, 8.0))
label(ax, 6.3, 7.70, "Inside the execution engine  —  how one rig stays sane with many callers",
      size=13, color=INK, bold=True)
label(ax, 6.3, 7.40, "executor.py.  Web requests never touch hardware; they only hand work to a single worker thread.",
      size=8.6, italic=True)

box(ax, 0.35, 5.18, 3.55, 1.79, COG, COG_BG, "", "")
ax.text(0.58, 6.80, "Web request threads", fontsize=10.5, weight="bold", color=INK, va="top", zorder=3)
ax.text(0.58, 6.44, "many at once — from agents,\nscripts, the chat platform\n\n"
        "POST /execute → start()\nPOST /abort   → set a flag",
        fontsize=8.2, color=MUTE, va="top", zorder=3, linespacing=1.45)

box(ax, 4.55, 5.55, 3.20, 1.22, STOR, STOR_BG, "", "")
ax.text(4.78, 6.62, "The queue", fontsize=10.5, weight="bold", color=INK, va="top", zorder=3)
ax.text(4.78, 6.28, "queue.Queue  (FIFO)\n\nplan → plan → plan …",
        fontsize=8.2, color=MUTE, va="top", zorder=3, family="DejaVu Sans Mono", linespacing=1.5)
arrow(ax, (3.95, 6.16), (4.50, 6.16))

box(ax, 8.40, 5.35, 3.85, 1.62, DET, DET_BG, "", "")
ax.text(8.63, 6.80, "ONE worker thread", fontsize=10.5, weight="bold", color=INK, va="top", zorder=3)
ax.text(8.63, 6.44, "_worker_loop() → _run(plan)\n\nthe only thread that is ever\nallowed to command a device",
        fontsize=8.2, color=MUTE, va="top", zorder=3, linespacing=1.45)
arrow(ax, (7.80, 6.16), (8.35, 6.16))
label(ax, 6.15, 5.34, "one run at a time, in the order they were approved", size=7.8, italic=True)

# locks
box(ax, 8.40, 3.35, 3.85, 1.58, DET, "#e6f3ea", "", "")
ax.text(8.63, 4.76, "One lock per device", fontsize=10, weight="bold", color=INK, va="top", zorder=3)
ax.text(8.63, 4.40, "with lock(stage), lock(laser):\n    expose one line",
        fontsize=8.2, color="#1e4620", va="top", zorder=3,
        family="DejaVu Sans Mono", linespacing=1.5)
ax.text(8.63, 3.48, "a second writer can never interleave\nmid-exposure", fontsize=7.6,
        color=MUTE, style="italic", va="bottom", zorder=3, linespacing=1.3)
arrow(ax, (10.32, 5.35), (10.32, 4.97))

# abort ladder
box(ax, 0.35, 2.42, 7.55, 2.56, HUM, HUM_BG, "", "")
ax.text(0.58, 4.74, "Where an abort is noticed", fontsize=10.5, weight="bold", color=INK, va="top", zorder=3)
ax.text(0.58, 4.38,
        "while still queued          →  never starts; no hardware touched\n"
        "between two operations      →  raise _AbortRequested\n"
        "between array lines         →  raise _AbortRequested\n"
        "between line repetitions    →  backend returns False\n"
        "during a wait               →  the wait returns early",
        fontsize=8.2, color="#5a4010", va="top", zorder=3,
        family="DejaVu Sans Mono", linespacing=1.6)
ax.text(0.58, 2.60, "The one thing never interrupted is a single line traverse — stopping mid-line would\n"
        "leave a half-written feature, and the beam cannot physically stop faster anyway.",
        fontsize=7.8, color=MUTE, style="italic", va="bottom", zorder=3, linespacing=1.35)

# safe state
box(ax, 0.35, 0.35, 11.90, 1.80, SAFE, SAFE_BG, "", "")
ax.text(0.58, 1.98, "However a run ends — success, fault, or abort — the same closing sequence runs",
        fontsize=10.5, weight="bold", color=INK, va="top", zorder=3)
seq = [("1", "LASER", "output forced off\n(force=True: never trusts\na cached state)"),
       ("2", "STAGE", "motion halted"),
       ("3", "WHITE LIGHT", "switched off"),
       ("4", "RECORD", "state written, telemetry\nand audit closed out")]
for i, (n, dev, what) in enumerate(seq):
    x = 0.75 + i*2.90
    box(ax, x, 0.55, 2.55, 0.95, SAFE, "#f8e8e8", "", "")
    ax.text(x+0.16, 1.38, f"{n}.  {dev}", fontsize=9, weight="bold", color=SAFE, va="top", zorder=3)
    ax.text(x+0.16, 1.10, what, fontsize=7.6, color=MUTE, va="top", zorder=3, linespacing=1.3)
    if i < 3:
        arrow(ax, (x+2.55+0.03, 1.02), (x+2.90-0.03, 1.02), color=SAFE, ms=11)
save(fig, OUT + "d5_engine.png")
print("D5 done")
