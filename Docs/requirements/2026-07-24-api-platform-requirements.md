# 2PP Setup API Platform — Requirements (v1)

**Status:** Draft for team review · derived from Mushegh's milestone feedback (2026-07-24) and the approved architecture proposal (`Docs/AI-Driven_Lab_Control_Architecture_Proposal.docx`)
**Owner:** Narek · **Reviewers:** Mushegh, Tatevik, Tetiana, Aram

---

## 1. Context

The architecture proposal was reviewed and the general plan approved. Two milestones are green-lit:

- **Milestone 1 — The 2PP Setup API Platform (layers L0–L3):** one unified API platform integrating the **laser**, **3D stage**, **camera**, and **white-light (WL) source** on the custom 2PP setup. All four units are already automated individually; documentation and SDKs live on the setup computer.
- **Milestone 2 — Proof-of-principle AI interface & validation loop:** connect the platform to **chat.photonics.ai** (built by Aram), where an *Optical Microfabrication Specialist* agent (user-facing) and an *Experimentalist* agent (background) plan, cross-validate, obtain user approval, execute via our API, and analyze results.

**Overriding design goal (Narek):** the platform must be *integratable into any environment* — any UI, any chat platform, any agent framework, any scripting language. chat.photonics.ai is the **first client**, never a dependency.

## 2. Scope

**In scope (M1):** device adapters for the four units; capability registry; declarative Experiment Specification; validation engine; plan lifecycle with approval; execution engine (owns synchronization); results/artifacts; audit log; auth with roles; simulation mode; REST API + OpenAPI spec.

**In scope (M2):** integration contract + guide for chat.photonics.ai; example client; support for the two-agent workflow; result transfer back to the platform agents.

**Out of scope (for now):** structure-design assistance (Phase F of the roadmap); replacing the hardware sync mechanism (it gets *encapsulated*, improved later); building any UI of our own beyond API docs.

## 3. Functional requirements — Milestone 1

| ID | Requirement |
|----|-------------|
| F1 | The platform SHALL expose four device classes behind one API: motion stage, laser, camera, white-light source. |
| F2 | Each device adapter SHALL declare its **capabilities** (operations with typed parameters, units, hard bounds, pre/postconditions) in machine-readable form; the registry SHALL be queryable (`GET /capabilities`) so any AI client can ground itself without prior knowledge. |
| F3 | Experiments SHALL be described by a **declarative Experiment Specification** (JSON/YAML, schema-versioned): an ordered list of high-level operations. Specs are stored, versioned, and re-runnable without any AI. |
| F4 | The API SHALL implement a server-enforced **plan lifecycle**: `draft → validated → approved → queued → running → completed / failed / aborted`, with endpoints to create, validate, dry-run, approve, execute, monitor, abort, and fetch results. Invalid transitions SHALL be rejected server-side. |
| F5 | A deterministic **validation engine** SHALL check every spec against: parameter bounds, stage travel range, exposure/power policy, mutual device constraints, and current device state. Validation SHALL produce a human-readable report (feeds the approval step and the agents). |
| F6 | **Dry-run** SHALL simulate a validated plan without hardware: estimated duration, toolpath envelope, exposure summary. |
| F7 | The **execution engine** SHALL be the only component that commands hardware. It expands specs into device commands, owns stage↔laser synchronization, holds per-device single-writer locks, and on any fault drives devices to **safe state (laser off first)**. |
| F8 | Runs SHALL produce retrievable **results**: telemetry/event log and camera artifacts (images), via `GET /plans/{id}/results`. |
| F9 | An **append-only audit log** SHALL record every request, validation outcome, approval (with approver identity), execution event, and fault. |
| F10 | **AuthN/AuthZ:** token-based authentication; roles `operator` (propose/execute), `approver` (approve), `admin`. The platform SHALL enforce **proposer ≠ approver** server-side. |
| F11 | **Simulation mode:** the entire platform SHALL run with simulated adapters (no hardware), enabling development off the rig, CI testing, and safe agent-integration testing for M2. |
| F12 | Device **health/state** SHALL be queryable (`GET /devices`), including connection status and last-known positions/settings. |

## 4. Non-functional requirements (integratability first)

| ID | Requirement |
|----|-------------|
| N1 | **UI-agnostic contract:** pure HTTP/JSON REST with an auto-generated **OpenAPI 3** document. No assumption about the client (chat platform, script, GUI, agent framework). |
| N2 | **MCP-ready:** the capability registry and endpoints SHALL map cleanly onto Model Context Protocol tools so a thin MCP wrapper can be added later without touching the core. |
| N3 | **Extensible devices:** adding a device = writing one adapter implementing the adapter interface + registering it. Zero changes to core code. |
| N4 | **Deployable on the setup computer**: Python, minimal dependency footprint, single-process service; no cloud dependency to operate locally. |
| N5 | **The API is the only path to hardware.** Agents, UIs, and scripts never import drivers directly. |
| N6 | Status via polling first (simple, universal); streaming (SSE/WebSocket) optional later. |
| N7 | All timestamps, units, and coordinates explicit in the schema (µm, mm/s, mW, ISO-8601) — agents must never guess units. |

## 5. Milestone 2 — integration contract (mapping the two-agent workflow onto the API)

The platform stays agent-agnostic; Mushegh's workflow maps onto plain API calls:

| Workflow step (Mushegh) | API interaction |
|---|---|
| Specialist + user discuss; Experimentalist formulates plan | `GET /capabilities`, `GET /devices` for grounding; `POST /plans` with the spec (state `draft`) |
| Experimentalist sends plan back to Specialist for validation | `POST /plans/{id}/validate` → platform's deterministic report; agents review it *plus* their own domain check |
| Specialist presents plan to user; user confirms in chat | `POST /plans/{id}/approve` — must carry an identity distinct from the proposer (see Q-A3) |
| Experimentalist executes | `POST /plans/{id}/execute`; poll `GET /plans/{id}` |
| Results transferred back; Specialist analyzes | `GET /plans/{id}/results` (telemetry + images) |

Note: the agents' internal cross-validation is a *quality* layer above the trust boundary. It complements — never replaces — the platform's deterministic validation (F5) and role-enforced approval (F10).

## 6. Constraints & assumptions

- Stage: Standa 8MTL120XY via ACS SPiiPlus (`SPiiPlusPython` from local ACS ADK wheel; Ethernet TCP 10.0.0.100:701; ±25 mm; 1 µm).
- Laser: Phoebe PH2 via HTTP REST (192.168.244.10/phoebe/v0/…).
- Camera & WL source: automated per Mushegh; SDKs on the setup computer — **models/interfaces unknown until we get the docs (Q-H1/Q-H2)**. Adapters ship as simulated + stub-real until then.
- Current sync between stage and laser is timing-calibrated (sleep-based); it will be encapsulated behind a synchronizer interface, not fixed, in M1.
- Independent hardware interlocks exist on the rig (confirmed earlier) — the software safety layer sits above them.
- `pyproject.toml` targets Python ≥3.12; the checked-in `.venv` is 3.11 and stale → environment rebuild is part of M1 setup.

## 7. Questions for the team (as requested by Mushegh)

**Hardware / current automation — for Tatevik & Tetiana**
- **Q-H1:** Camera — exact model, SDK (vendor lib? GenICam? OpenCV-compatible?), and the current automation script. What capture parameters matter (exposure, gain, ROI)?
- **Q-H2:** WL source — model and control interface (serial? USB? vendor DLL?), controllable parameters (on/off? intensity?), and the current script.
- **Q-H3:** Are there mutual-constraint rules we must encode? (e.g., WL must be off during 2PP exposure; camera capture requires WL on; shutter states.)
- **Q-H4:** What is the definitive safe-state per device (laser: off vs. shutter closed; stage: stop vs. home; WL: off)?
- **Q-H5:** Setup computer: OS + version, Python available, can we run a persistent local service on it?
- **Q-H6:** Safe Z/focal-plane approach: is a 3-axis diagonal move to the focus position acceptable, or must approach be sequenced (XY first, then Z — or Z-first)? Is there an objective-collision risk, and should the park position stay [0,0,0] or be configurable?

**Safety & policy — for Mushegh**
- **Q-S1:** Who signs off the hard bounds table (max laser power, max stage speed, exposure limits) that the validation engine enforces?
- **Q-S2:** Confirm the interlock inventory on this specific setup (e-stop, shutter/key, enclosure).

**Approval & identity — for Mushegh & Aram**
- **Q-A1:** How does chat.photonics.ai authenticate users, and can it pass the user's identity through to our API (needed for audit + proposer≠approver)?
- **Q-A2:** Who are the designated *approvers*? Is the chatting user ever also the approver, or must a qualified person approve (the architecture recommends proposer ≠ approver)?
- **Q-A3:** Is a chat reply ("I confirm…") acceptable as the approval *UI*, provided the platform still enforces an authenticated approve call with a distinct approver identity underneath?

**Platform integration — for Aram**
- **Q-P1:** Where does chat.photonics.ai run (cloud/on-prem)? What network path exists to the setup computer (VPN? tunnel? same LAN?) — do we need to expose the API beyond the lab network?
- **Q-P2:** How do the Specialist/Experimentalist agents call external tools — OpenAPI/function calling, MCP, or custom HTTP? (The platform provides OpenAPI now, MCP wrapper on request.)
- **Q-P3:** How should large artifacts (images) be transferred — inline base64, download URLs, or a shared store?
- **Q-P4:** Who authors/owns the two agents' prompts and tool definitions — Aram's side, or do we supply a "tool manifest" they load?

**Process — for Mushegh**
- **Q-M1:** Definition of done for M1: is *simulation-mode demo + stage/laser live on the rig* sufficient, with camera/WL following once SDK docs are in hand?
- **Q-M2:** For M2's proof of principle, which experiment class should the demo run — parameter-sweep arrays (recommended: bounded, well-understood) or STL prints?

## 8. Traceability

Architecture layers (proposal §4) → this platform: L0–L1 = rig + interlocks (exist); **L2 = device adapters (F1–F2)**; **L3 = API-Gate core (F4–F10, F12)**; L4 = Experiment Spec (F3); L5–L6 = chat.photonics.ai agents + chat UI (Aram's side, M2). The four design principles (AI proposes / deterministic disposes; AI output is untrusted input; intent not trajectories; value before AI) remain binding on every implementation decision.
