"""Shared test identities and auth helper."""

from labgate.auth import Identity, Role

ALICE = Identity(user_id="alice", display_name="Alice", roles={Role.OPERATOR})
BOB = Identity(user_id="bob", display_name="Bob", roles={Role.APPROVER})
ROOT = Identity(user_id="root", display_name="Root", roles={Role.ADMIN})


def auth(client, user: str) -> dict:
    return {"Authorization": f"Bearer {client.tokens[user]}"}
