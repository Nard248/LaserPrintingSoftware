"""D6 — the data model as it exists on disk."""
import sys; sys.path.insert(0, ".")
from style import *
OUT = "fig/"

fig, ax = canvas(9.85, 7.23, (0, 12.8), (0, 9.4))
label(ax, 6.4, 9.10, "The data model  —  what is written, where, and how it links together",
      size=13, color=INK, bold=True)
label(ax, 6.4, 8.80, "There is no database server. Everything is plain files under one folder, so the record survives the software.",
      size=8.6, italic=True)

MONO = "DejaVu Sans Mono"

def entity(x, y, w, h, title, path, fields, note="", edge=STOR, face=STOR_BG):
    box(ax, x, y, w, h, edge, face, "", "")
    ax.text(x+0.22, y+h-0.20, title, fontsize=10.5, weight="bold", color=INK, va="top", zorder=3)
    ax.text(x+w-0.22, y+h-0.22, path, fontsize=8, color=edge, ha="right",
            family=MONO, va="top", zorder=3)
    ax.text(x+0.22, y+h-0.62, fields, fontsize=8.1, color="#2a2a2a", va="top",
            zorder=3, family=MONO, linespacing=1.55)
    if note:
        ax.text(x+w-0.22, y+0.14, note, fontsize=7.5, color=MUTE, style="italic",
                ha="right", va="bottom", zorder=3, linespacing=1.3)

entity(0.35, 5.05, 6.15, 3.30, "PLAN RECORD", "plans/<plan_id>.json",
       "plan_id        plan_e163ba8b3336\n"
       "spec           the recipe, verbatim as submitted\n"
       "proposer       claude-planner\n"
       "approver       narek            ← must differ\n"
       "state          completed\n"
       "validation_report\n"
       "   checks[]    name · ok · severity · detail\n"
       "created_at     ISO-8601 UTC\n"
       "history[]      every transition, with actor + time",
       "one file per plan, rewritten only\nwhen its state legally changes")

entity(6.95, 5.05, 5.50, 3.30, "RUN FOLDER", "runs/<plan_id>/",
       "telemetry.jsonl\n"
       "   one line per event, in order:\n"
       "   run_started · op_started · op_finished\n"
       "   image_captured · safe_state_error\n"
       "   fault · aborted · run_completed\n"
       "\n"
       "artifacts/\n"
       "   dryrun_preview.png   the approved path\n"
       "   <label>.png          camera images",
       "append-only while the run is live;\nnever edited afterwards")

entity(0.35, 2.55, 6.15, 2.10, "AUDIT LEDGER", "audit.jsonl",
       "ts      2026-08-26T08:35:58.245612+00:00\n"
       "event   plan_approved\n"
       "actor   narek\n"
       "payload {\"plan_id\": \"plan_e163ba8b3336\"}",
       "append-only, process-wide, never rotated\nby the platform itself", edge=SAFE, face=SAFE_BG)

entity(6.95, 2.55, 5.50, 2.10, "MODEL STORE", "models/<model_id>.*",
       "<model_id>.stl    the uploaded mesh\n"
       "<model_id>.json   filename · size_bytes\n"
       "                  faces · bbox_file_units\n"
       "model_id = 'mdl_' + sha256(bytes)[:12]",
       "content-addressed: the same bytes always\ngive the same id, different bytes never do")

# links
arrow(ax, (6.50, 6.70), (6.90, 6.70), color=INK, lw=1.6, style="<|-|>")
label(ax, 6.70, 6.94, "same\nplan_id", size=7.4, color=INK, bold=True, bg="white")
arrow(ax, (3.42, 5.05), (3.42, 4.70), color=SAFE, lw=1.6)
label(ax, 3.42, 4.87, "every state change also appends one audit line", size=7.4,
      color=SAFE, bold=True, bg="white")
arrow(ax, (9.70, 4.65), (9.70, 5.00), color=STOR, lw=1.6)
label(ax, 9.70, 4.83, "spec's print_stl op references a model_id", size=7.4,
      color=STOR, bold=True, bg="white")

box(ax, 0.35, 0.35, 12.10, 1.80, DET, DET_BG, "", "")
ax.text(0.58, 1.98, "Why files and not a database — and what would change if that stops being enough",
        fontsize=10.5, weight="bold", color=INK, va="top", zorder=3)
ax.text(0.58, 1.60,
        "Today one rig runs one experiment at a time, so there is no concurrent-write problem to solve. Plain JSON survives a\n"
        "power cut, is readable in Notepad years later, diffs cleanly, and needs no server to administer on a lab machine. The\n"
        "code already hides this behind PlanStore / RunResults / AuditLog: moving to PostgreSQL later means rewriting those\n"
        "three classes and nothing else. The trigger to do so would be several rigs sharing one platform, or queries across\n"
        "thousands of runs — neither of which is true yet.",
        fontsize=8.2, color="#143d28", va="top", zorder=3, linespacing=1.55)
save(fig, OUT + "d6_storage_model.png")
print("D6 done")
