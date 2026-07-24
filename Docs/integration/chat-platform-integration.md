# Integrating chat.photonics.ai with the labgate Platform (Milestone 2)

**Audience:** Aram (chat.photonics.ai) + the agent authors.
**Contract:** everything here is plain HTTP/JSON — no SDK required. The
machine-readable contract is the OpenAPI document at `GET /openapi.json`
on a running platform; interactive docs at `/docs`.

## 0. Principles you can rely on

- The platform is **deterministic and final**: whatever an agent submits is
  re-validated against hard bounds server-side. Agents can plan freely; the
  platform is the safety net, not the agents' prompts.
- The platform is **UI-agnostic**: nothing below assumes a chat interface.
- **Identity is real**: every call carries a bearer token that maps to a
  known person with roles. The chat platform must call with the token of
  the *actual human* driving the conversation (see §4).

## 1. Run it

```bash
pip install -e ".[dev]"        # in this repo
labgate-serve                  # http://127.0.0.1:8523, simulation mode
# LABGATE_CONFIG=config/default.yaml labgate-serve   # explicit config
```

Simulation mode behaves identically to rig mode (same validation, same
lifecycle, same results shape) — integrate against it freely; nothing can
fire a real laser.

## 2. The lifecycle every experiment follows

| # | Step | Call | Notes |
|---|------|------|-------|
| 1 | Ground the planner | `GET /capabilities` | devices + operations + typed, bounded, unit-bearing params — feed this to the Experimentalist agent's context |
| 2 | Submit plan | `POST /plans` `{"spec": {...}}` | auto-validates; returns state `validated` or `rejected` + report |
| 3 | Inspect | `GET /plans/{id}` | includes full validation report (agents self-correct on `rejected`) |
| 4 | Predict | `POST /plans/{id}/dry-run` | duration, exposure events, bounding box — show this to the user with the plan |
| 5 | Approve | `POST /plans/{id}/approve` | **approver token, different user than proposer** — see §4 |
| 6 | Execute | `POST /plans/{id}/execute` | 202; run happens in background |
| 7 | Poll | `GET /plans/{id}` | until `completed` / `failed` / `aborted` |
| 8 | Results | `GET /plans/{id}/results` | telemetry events + artifact names |
| 9 | Artifacts | `GET /plans/{id}/results/artifacts/{name}` | e.g. camera images (binary) |

### Example spec (what the Experimentalist agent produces)

```json
{
  "spec_version": "1.0",
  "title": "Power sweep array, 5 lines",
  "operations": [
    {"op": "set_laser_power", "attenuator_percent": 30, "pp_divider": 1},
    {"op": "write_array", "x_start_mm": -5, "x_end_mm": 0, "y_start_mm": -5,
     "y_pitch_mm": 0.1, "line_count": 5, "z_mm": 6.0, "velocity_mm_s": 5.0},
    {"op": "capture_image", "label": "after_print"}
  ]
}
```

Operation vocabulary (v1): `set_laser_power`, `write_line`, `write_array`,
`move_stage`, `set_white_light`, `capture_image`, `wait`. All units explicit
(`_mm`, `_mm_s`, `_percent`, `seconds`). The AI authors *intent*; motion
expansion and stage/laser synchronization happen inside the platform.

## 3. Mapping Mushegh's two-agent workflow

| Workflow step | API interaction |
|---|---|
| Specialist + user discuss; Experimentalist formulates plan | `GET /capabilities` for grounding; `POST /plans` |
| Experimentalist → Specialist validation | read the plan's `validation_report` + `dry-run`; agents add domain judgment on top |
| Specialist presents to user; user confirms in chat | chat confirmation triggers `POST /plans/{id}/approve` **with an approver identity** |
| Experimentalist executes | `POST /plans/{id}/execute`, poll `GET /plans/{id}` |
| Results → Specialist analyzes | `GET /plans/{id}/results` (+ artifact downloads) |

The agents' internal cross-validation is a quality layer; the platform's
deterministic validation and role checks run regardless.

## 4. Identity & approval — the one thing we must design together

The platform enforces **proposer ≠ approver** and requires the `approver`
role to approve. A chat reply ("I confirm…") is a fine *UI* for approval,
but underneath, the chat platform must call `/approve` with a token that
resolves to a *qualified approver who is not the plan's proposer*.

Open questions for you (also in the requirements doc, Q-A1..Q-P4):
1. Can chat.photonics.ai pass through per-user identity (so we issue one
   token per lab member rather than one shared bot token)? A shared bot
   token would defeat the audit trail and the proposer≠approver rule.
2. How do your agents call external tools — OpenAPI/function-calling,
   MCP, or raw HTTP? (We can generate an MCP wrapper over `/capabilities`
   if that's your native mechanism.)
3. Artifact transfer: download-URL pull (current design) OK, or do you
   need push/webhooks?
4. Network path from your deployment to the rig computer (the platform
   binds to localhost by default; exposing it beyond the lab LAN needs a
   deliberate decision + TLS).

## 5. Error semantics

| HTTP | Meaning |
|---|---|
| 401 | missing/unknown token |
| 403 | role missing, or proposer tried to approve their own plan |
| 404 | unknown plan / artifact |
| 409 | illegal lifecycle transition (e.g. execute before approve) |
| 422 | malformed spec (schema level; bound violations come back as state `rejected` with a report instead) |
| 502 | device-level failure (rig mode) |

Every state change is audit-logged server-side with the acting identity.
