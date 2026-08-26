"""MCP wrapper over the labgate REST API.

Exposes the platform's lifecycle as Model Context Protocol tools so any
MCP-capable agent (Claude, chat.photonics.ai if Aram picks MCP for Q-P2,
etc.) can drive experiments. This is a PURE CLIENT of the HTTP API —
requirement N5 holds: MCP -> REST -> validation/approval/execution. The
wrapper adds no authority: it runs under ONE identity (its token), so
approval still requires a differently-authenticated approver, exactly as
over raw HTTP.

Run:  LABGATE_URL=http://127.0.0.1:8523 LABGATE_TOKEN=<token> labgate-mcp
Requires the optional dependency:  pip install -e ".[mcp]"
"""

from __future__ import annotations

import os

import httpx


def create_mcp_server(client: httpx.Client):
    """Build the FastMCP server around an httpx client (injectable for tests)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "MCP support needs the 'mcp' package: pip install -e '.[mcp]'"
        ) from exc

    mcp = FastMCP(
        "labgate",
        instructions=(
            "Deterministic control platform for a 2PP laser fabrication rig. "
            "Workflow: get_capabilities to ground yourself, submit_plan with a "
            "declarative spec, read the validation report, dry_run it, have a "
            "QUALIFIED HUMAN approve it (approve_plan works only for an "
            "approver identity distinct from the proposer), then execute_plan "
            "and poll get_plan until completed; fetch get_results."
        ),
    )

    def _call(method: str, path: str, **kwargs) -> dict | list:
        response = client.request(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            return {"error": response.status_code, "detail": body}
        return body

    @mcp.tool()
    def get_capabilities() -> dict:
        """Devices + operations with typed, bounded, unit-bearing parameters.
        Call this FIRST to ground your planning in what the rig can do."""
        return _call("GET", "/capabilities")

    @mcp.tool()
    def get_devices() -> list | dict:
        """Current device states (connection, positions, laser settings)."""
        return _call("GET", "/devices")

    @mcp.tool()
    def submit_plan(spec: dict) -> dict:
        """Submit an ExperimentSpec. It is auto-validated: returned state is
        'validated' (ready for approval) or 'rejected' (fix per the report,
        then submit a corrected spec as a NEW plan)."""
        return _call("POST", "/plans", json={"spec": spec})

    @mcp.tool()
    def get_plan(plan_id: str) -> dict:
        """Full plan record: state, validation report, history. Poll this
        during execution until state is completed/failed/aborted."""
        return _call("GET", f"/plans/{plan_id}")

    @mcp.tool()
    def dry_run(plan_id: str) -> dict:
        """Predicted duration, motion distance, exposure events, and spatial
        envelope — show this to the human alongside the plan."""
        return _call("POST", f"/plans/{plan_id}/dry-run")

    @mcp.tool()
    def approve_plan(plan_id: str) -> dict:
        """Approve a validated plan. Succeeds ONLY if this server's identity
        has the approver role and is not the plan's proposer."""
        return _call("POST", f"/plans/{plan_id}/approve")

    @mcp.tool()
    def execute_plan(plan_id: str) -> dict:
        """Start executing an approved plan (asynchronous; poll get_plan)."""
        return _call("POST", f"/plans/{plan_id}/execute")

    @mcp.tool()
    def abort_plan(plan_id: str) -> dict:
        """Request cooperative abort of a running plan (laser goes to safe
        state; stage halts)."""
        return _call("POST", f"/plans/{plan_id}/abort")

    @mcp.tool()
    def get_results(plan_id: str) -> dict:
        """Run telemetry events + artifact manifest for a finished plan."""
        return _call("GET", f"/plans/{plan_id}/results")

    return mcp


def main() -> None:  # console entry point: labgate-mcp
    base_url = os.environ.get("LABGATE_URL", "http://127.0.0.1:8523")
    token = os.environ.get("LABGATE_TOKEN")
    if not token:
        raise SystemExit("Set LABGATE_TOKEN (bearer token for the labgate API).")
    client = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    create_mcp_server(client).run()  # stdio transport
