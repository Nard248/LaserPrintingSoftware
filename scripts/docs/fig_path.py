"""D1 / D2 — the actual code path, module by module."""
import sys; sys.path.insert(0, ".")
from style import *
from flow import Flow
OUT = "fig/"

# ==================================================== D1: prompt -> approval
fig, ax = canvas(12.4, 15.6, (0, 12.4), (0, 15.6))
label(ax, 6.2, 15.28, "Code path, part 1:  from a typed sentence to an approved plan",
      size=13.5, color=INK, bold=True)
label(ax, 6.2, 14.94, "Every box names the file and the function that actually runs; arrows are real calls or HTTP requests.",
      size=8.6, italic=True)

f = Flow(ax, 1.35, 8.6, 14.62)

f.block(0.80, COG, COG_BG, "A person types into the chat",
        '"sweep 10 / 20 / 30 % over three 4 mm lines, then photograph it"',
        "chat.photonics.ai  ·  outside our code")
f.block(0.96, COG, COG_BG, "The AI planner grounds itself first",
        "GET /capabilities  →  app.py: capabilities()\n"
        "    → registry.snapshot() → adapter.capabilities() ×4",
        "learns the devices, the operations and\nevery hard limit — at run time",
        side="the AI is told what is\npossible; it never has\nto guess a limit")
f.block(0.80, COG, "#eef3fb", "The AI writes a recipe  (JSON, never code)",
        'POST /plans   { "spec": { "operations": [ ... ] } }',
        "the only artefact that crosses the boundary")
f.boundary(above="above: advisory, may be wrong",
           below="below: deterministic, enforced")
f.block(0.84, DET, DET_BG, "Who is calling?",
        "auth.py:  TokenStore.resolve(bearer)  →  Identity\n"
        "          require_role(identity, OPERATOR)",
        "401 unknown token   ·   403 wrong role", connect=False)
f.block(0.88, DET, DET_BG, "Is it even well-formed?",
        "spec.py:  pydantic parse of ExperimentSpec\n"
        "          extra='forbid' · finite floats only · known op names",
        "an invented field or a NaN dies here — 422")
f.block(0.74, STOR, STOR_BG, "Write it down before judging it",
        "lifecycle.py:  PlanStore.create()  →  plans/<plan_id>.json",
        "state = draft  ·  proposer recorded")
f.block(1.66, DET, DET_BG,
        "Is it physically allowed?    validation.py: ValidationEngine.validate()",
        "_check_bounds()               power %, speed, repetitions vs config limits\n"
        "_check_geometry()             → pathing.op_to_path() expands the recipe\n"
        "                                into the REAL trajectory, point by point\n"
        "                                (print_stl → geometry.slice_stl(), cached)\n"
        "_check_ordering()             power set before firing? white light off?\n"
        "_check_exposure_uniformity()  lines long enough to reach full speed?",
        "one written verdict per check",
        side="a model-written plan is\nchecked exactly as strictly\nas a hand-typed one")

# ---- fork
bw = 4.05
ytop = f.y
xL, xR = f.x, f.x + f.w - bw
arrow(ax, (f.x+f.w/2, ytop+0.42), (xL+bw/2, ytop-0.02), color=SAFE)
arrow(ax, (f.x+f.w/2, ytop+0.42), (xR+bw/2, ytop-0.02), color=DET)
label(ax, xL+bw/2-0.15, ytop+0.30, "any error", size=7.8, color=SAFE, bold=True, bg="white")
label(ax, xR+bw/2+0.15, ytop+0.30, "all clear", size=7.8, color=DET, bold=True, bg="white")
box(ax, xL, ytop-0.92, bw, 0.90, SAFE, SAFE_BG, "", "")
ax.text(xL+0.22, ytop-0.20, "REJECTED  ·  dead end", fontsize=10, weight="bold",
        color=INK, va="top", zorder=3)
ax.text(xL+0.22, ytop-0.50, "state = rejected", fontsize=8.4, color="#1e4620",
        family="DejaVu Sans Mono", va="top", zorder=3)
ax.text(xL+bw-0.22, ytop-0.86, "reasons go back to the AI, which\nmay submit a NEW plan",
        fontsize=7.6, color=MUTE, style="italic", ha="right", va="bottom", zorder=3)
box(ax, xR, ytop-0.92, bw, 0.90, DET, DET_BG, "", "")
ax.text(xR+0.22, ytop-0.20, "VALIDATED  ·  may proceed", fontsize=10, weight="bold",
        color=INK, va="top", zorder=3)
ax.text(xR+0.22, ytop-0.50, "state = validated", fontsize=8.4, color="#1e4620",
        family="DejaVu Sans Mono", va="top", zorder=3)
ax.text(xR+bw-0.22, ytop-0.86, "only now can a human\nbe asked to sign",
        fontsize=7.6, color=MUTE, style="italic", ha="right", va="bottom", zorder=3)

# continue from the right branch
f.y = ytop - 0.92 - 0.58
f.last = None
elbow_y = f.y + 0.10
ax.plot([xR+bw/2, xR+bw/2], [ytop-0.92, elbow_y], color=MUTE, lw=1.5, zorder=4)
ax.plot([xR+bw/2, f.x+f.w/2], [elbow_y, elbow_y], color=MUTE, lw=1.5, zorder=4)
arrow(ax, (f.x+f.w/2, elbow_y), (f.x+f.w/2, f.y-0.02))

f.block(1.04, DET, DET_BG, "Show a human what will actually happen",
        "POST /plans/<id>/dry-run\n"
        "  dryrun.py: estimate()      duration · distance · exposures · envelope\n"
        "  preview.py: render_preview() → plot_path_3d → dryrun_preview.png",
        "the same expansion the checker used —\nso the picture IS the real path",
        connect=False)
f.block(1.10, HUM, HUM_BG, "A qualified person signs",
        "POST /plans/<id>/approve       (the approver's own token)\n"
        "  lifecycle.py: PlanStore.approve()\n"
        "      if approver == proposer:  refuse  →  403",
        "state = approved  ·  name stored on the record",
        side="the author of a plan\ncan never be the one\nwho authorises it")
save(fig, OUT + "d1_path_to_approval.png")
print("D1 done, bottom y =", round(f.y, 2))

# ==================================================== D2: approval -> photon
fig, ax = canvas(12.6, 16.9, (0, 12.6), (-1.7, 15.2))
label(ax, 6.3, 14.88, "Code path, part 2:  from an approved plan to a photon on the sample",
      size=13.5, color=INK, bold=True)
label(ax, 6.3, 14.54, "Nothing here consults the AI. This half is pure, testable machinery.",
      size=8.6, italic=True)

f = Flow(ax, 1.30, 8.7, 14.22)
f.block(0.92, DET, DET_BG, "Ask to run it",
        "POST /plans/<id>/execute  →  executor.py: ExecutionEngine.start()\n"
        "    refuses unless state == approved",
        "state = queued  ·  409 if not approved",
        side="an unapproved plan\ncannot start, no matter\nwho asks")
f.block(0.96, DET, DET_BG, "Join the queue  (one rig, one run at a time)",
        "queue.Queue.put(plan_id)   ·   _ensure_worker()\n"
        "single worker thread: _worker_loop()  →  _run(plan_id)",
        "strict first-in-first-out  ·  GET /queue shows position")
f.block(0.80, DET, DET_BG, "Wake the instruments",
        "for adapter in registry.adapters():  adapter.connect()",
        "state = running  ·  reconnect is idempotent")
f.block(0.84, SAFE, SAFE_BG, "Force a known-safe baseline BEFORE any motion",
        "_safe_state_all()   laser OFF  →  stage halt  →  white light OFF",
        "a beam left on by a previous session cannot\nsurvive into this run",
        side="fixed order, always:\nlaser first")
f.block(1.46, DET, DET_BG, "Walk the operations    executor.py: _dispatch()",
        "for i, op in enumerate(spec.operations):\n"
        "    if abort_flag.is_set():  raise _AbortRequested   ← abort checkpoint\n"
        "    match op:  set_laser_power → laser.set_power()\n"
        "               write_power_sweep_array → per line: set_power + _write_line()\n"
        "               capture_image → white light on, camera.capture(), restore",
        "every step appended to telemetry.jsonl")
f.block(1.00, DET, DET_BG, "Expose one line    executor.py: _write_line()",
        "with lock(stage), lock(laser):        ← single-writer per device\n"
        "    self._exposure.write_line(start, end, velocity, reps, abort_flag)",
        "abort is honoured between repetitions")

# ---- exposure fork
ytop = f.y
bw = 4.15
xL, xR = f.x, f.x + f.w - bw
arrow(ax, (f.x+f.w/2, ytop+0.42), (xL+bw/2, ytop-0.02), color=GREY)
arrow(ax, (f.x+f.w/2, ytop+0.42), (xR+bw/2, ytop-0.02), color=SAFE)
label(ax, xL+bw/2-0.35, ytop+0.30, "mode: sim", size=7.8, color=GREY, bold=True, bg="white")
label(ax, xR+bw/2+0.35, ytop+0.30, "mode: rig", size=7.8, color=SAFE, bold=True, bg="white")

box(ax, xL, ytop-1.46, bw, 1.44, GREY, GREY_BG, "", "")
ax.text(xL+0.20, ytop-0.18, "SimExposure", fontsize=10, weight="bold", color=INK, va="top", zorder=3)
ax.text(xL+0.20, ytop-0.48,
        "laser.on()\nstage.move_absolute(end)\nlaser.off()      ← in a finally block",
        fontsize=8.2, color="#333", family="DejaVu Sans Mono", va="top", zorder=3, linespacing=1.5)
ax.text(xL+bw-0.20, ytop-1.40, "no hardware · used by CI and\nby the agent integration work",
        fontsize=7.6, color=MUTE, style="italic", ha="right", va="bottom", zorder=3)

box(ax, xR, ytop-1.46, bw, 1.44, SAFE, SAFE_BG, "", "")
ax.text(xR+0.20, ytop-0.18, "SyncExposure", fontsize=10, weight="bold", color=INK, va="top", zorder=3)
ax.text(xR+0.20, ytop-0.48,
        "set_velocity(write speed)\nPrintSynchronizer.execute_path()\n  _print_velocity_timed()",
        fontsize=8.2, color="#5a1d1d", family="DejaVu Sans Mono", va="top", zorder=3, linespacing=1.5)
ax.text(xR+bw-0.20, ytop-1.40, "fires the beam only inside the\nconstant-speed part of the move",
        fontsize=7.6, color=MUTE, style="italic", ha="right", va="bottom", zorder=3)

f.y = ytop - 1.46 - 0.58
f.last = None
elbow_y = f.y + 0.10
ax.plot([xR+bw/2, xR+bw/2], [ytop-1.46, elbow_y], color=MUTE, lw=1.5, zorder=4)
ax.plot([xR+bw/2, f.x+f.w/2], [elbow_y, elbow_y], color=MUTE, lw=1.5, zorder=4)
arrow(ax, (f.x+f.w/2, elbow_y), (f.x+f.w/2, f.y-0.02))

f.block(1.04, SAFE, SAFE_BG, "Down through the driver stack",
        "devices/rig.py: RigStage / RigLaser\n"
        "  → StageController → StageTcp → SPiiPlusPython → ACS controller (TCP)\n"
        "  → LaserController → LaserHttp → REST → Phoebe laser head",
        "the only code in the system that\ntouches a wire", connect=False)
f.block(0.72, SAFE, "#f8e8e8", "THE INSTRUMENTS   —   and, underneath, the interlocks that software cannot override",
        "", "", hs=9.6)
f.block(0.98, DET, DET_BG, "Close the run out",
        "_safe_state_all()  again   ·   state = completed | failed | aborted\n"
        "telemetry.jsonl + artifacts written   ·   audit.jsonl appended")
f.block(0.92, COG, COG_BG, "Hand the results back",
        "GET /plans/<id>/results        events + artifact manifest\n"
        "GET /plans/<id>/results/artifacts/<name>     the images themselves",
        "the AI analyses these and replies to the person",
        side="a read needs no approval —\nbut still needs a token")
save(fig, OUT + "d2_path_to_photon.png")
print("D2 done, bottom y =", round(f.y, 2))
