"""Identity, roles, and token resolution.

Any client (chat platform, script, GUI) authenticates the same way: a
bearer token that resolves to an Identity with roles. chat.photonics.ai
maps its users onto tokens issued here — the platform stays UI-agnostic.

Proposer != approver is enforced in the plan lifecycle, not here; this
module only answers "who is calling and what may they do".
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .errors import AuthError


class Role(StrEnum):
    OPERATOR = "operator"    # may propose and execute approved plans
    APPROVER = "approver"    # may approve plans proposed by others
    ADMIN = "admin"          # may do both plus manage tokens


class Identity(BaseModel):
    user_id: str
    display_name: str = ""
    roles: set[Role] = Field(default_factory=set)

    def has_role(self, role: Role) -> bool:
        return role in self.roles or Role.ADMIN in self.roles


class TokenStore:
    def __init__(self) -> None:
        self._tokens: dict[str, Identity] = {}

    @classmethod
    def load(cls, path: str | Path | None) -> "TokenStore":
        """Load tokens from YAML: {tokens: {<token>: {user_id, display_name, roles}}}."""
        store = cls()
        if path is None:
            return store
        raw = yaml.safe_load(Path(path).read_text()) or {}
        for token, ident in (raw.get("tokens") or {}).items():
            store._tokens[str(token)] = Identity(
                user_id=ident["user_id"],
                display_name=ident.get("display_name", ident["user_id"]),
                roles={Role(r) for r in ident.get("roles", [])},
            )
        return store

    def issue(self, identity: Identity) -> str:
        token = secrets.token_urlsafe(24)
        self._tokens[token] = identity
        return token

    def resolve(self, token: str) -> Identity:
        try:
            return self._tokens[token]
        except KeyError:
            raise AuthError("unknown token") from None


def require_role(identity: Identity, role: Role) -> None:
    if not identity.has_role(role):
        raise AuthError(f"user '{identity.user_id}' lacks required role '{role}'")
