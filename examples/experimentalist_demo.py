"""End-to-end demo of the Milestone-2 workflow against the labgate platform.

Plays both agent roles from Mushegh's workflow as plain API clients:

    Experimentalist (operator token)  — grounds itself, submits the plan,
                                        reads validation + dry-run, executes,
                                        collects results
    Approver        (approver token)  — the qualified human's sign-off step

By default this runs fully in-process against a simulation-mode platform
(zero setup — just run it). Point it at a live server instead with:

    python examples/experimentalist_demo.py --base-url http://127.0.0.1:8523 \
        --operator-token ... --approver-token ...
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx


def build_in_process_client() -> tuple[httpx.Client, str, str]:
    """Spin up a sim platform in this process; returns (client, op_tok, ap_tok)."""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    from labgate.api.app import create_app
    from labgate.auth import Identity, Role
    from labgate.config import LabgateConfig

    cfg = LabgateConfig(mode="sim",
                        storage_dir=Path(tempfile.mkdtemp(prefix="labgate_demo_")))
    app = create_app(cfg)
    platform = app.state.platform
    op = platform.tokens.issue(
        Identity(user_id="demo-experimentalist", roles={Role.OPERATOR}))
    ap = platform.tokens.issue(
        Identity(user_id="demo-approver", roles={Role.APPROVER}))
    client = TestClient(app)  # sync httpx client wrapping the ASGI app
    client._demo_platform = platform  # keep a ref so the engine survives
    return client, op, ap


DEMO_SPEC = {
    "spec_version": "1.0",
    "title": "Demo: 5-line power array + inspection image",
    "description": "M2 workflow demo — parameter array then camera capture.",
    "operations": [
        {"op": "set_laser_power", "attenuator_percent": 30, "pp_divider": 1},
        {"op": "write_array", "x_start_mm": -5.0, "x_end_mm": 0.0,
         "y_start_mm": -5.0, "y_pitch_mm": 0.1, "line_count": 5,
         "z_mm": 6.0, "velocity_mm_s": 5.0},
        {"op": "capture_image", "label": "after_print"},
    ],
}


def step(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None,
                        help="URL of a running labgate server (default: in-process sim)")
    parser.add_argument("--operator-token", default=None)
    parser.add_argument("--approver-token", default=None)
    args = parser.parse_args()

    if args.base_url:
        if not (args.operator_token and args.approver_token):
            parser.error("--base-url requires --operator-token and --approver-token")
        client = httpx.Client(base_url=args.base_url, timeout=30.0)
        op_token, ap_token = args.operator_token, args.approver_token
    else:
        client, op_token, ap_token = build_in_process_client()

    as_op = {"Authorization": f"Bearer {op_token}"}
    as_ap = {"Authorization": f"Bearer {ap_token}"}

    step("1. Experimentalist grounds itself")
    caps = client.get("/capabilities", headers=as_op).json()
    print(f"devices: {[d['device_id'] for d in caps['devices']]}")
    print(f"operations available: {sorted({c['name'] for c in caps['capabilities']})}")

    step("2. Experimentalist submits the plan (auto-validated)")
    plan = client.post("/plans", json={"spec": DEMO_SPEC}, headers=as_op).json()
    plan_id = plan["plan_id"]
    print(f"plan {plan_id}: state={plan['state']}  ({plan['validation_summary']})")
    if plan["state"] != "validated":
        report = client.get(f"/plans/{plan_id}", headers=as_op).json()
        print("validation report:", report["validation_report"])
        return 1

    step("3. Dry-run — what the approver sees")
    dry = client.post(f"/plans/{plan_id}/dry-run", headers=as_op).json()
    print(f"estimated duration: {dry['total_duration_s']} s   "
          f"exposures: {dry['exposure_events']}   "
          f"envelope: {dry['bounding_box_mm']}")

    step("4. Proposer cannot self-approve (proposer != approver)")
    refused = client.post(f"/plans/{plan_id}/approve", headers=as_op)
    print(f"operator approving own plan -> HTTP {refused.status_code} (expected 403)")

    step("5. Qualified approver signs off")
    approved = client.post(f"/plans/{plan_id}/approve", headers=as_ap).json()
    print(f"state={approved['state']}  approver={approved['approver']}")

    step("6. Experimentalist executes; polls to completion")
    client.post(f"/plans/{plan_id}/execute", headers=as_op)
    for _ in range(200):
        state = client.get(f"/plans/{plan_id}", headers=as_op).json()["state"]
        if state in ("completed", "failed", "aborted"):
            break
        time.sleep(0.05)
    print(f"final state: {state}")

    step("7. Results back to the Specialist for analysis")
    results = client.get(f"/plans/{plan_id}/results", headers=as_op).json()
    print(f"telemetry events: {results['manifest']['events']}   "
          f"artifacts: {results['manifest']['artifacts']}")

    print("\nDemo complete — the full M2 loop ran against the platform "
          f"({'in-process sim' if not args.base_url else args.base_url}).")
    return 0 if state == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
