"""Opaque id generation for plans and runs."""

import secrets


def new_id(prefix: str) -> str:
    """Return an id like 'plan_9f3a2c1b0d4e'. 12 hex chars = 48 bits."""
    return f"{prefix}_{secrets.token_hex(6)}"
