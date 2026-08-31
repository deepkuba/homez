from __future__ import annotations

import base64
import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TokenError(ValueError):
    """Raised when an encrypted OAuth token cannot be loaded."""


class EncryptedTokenStore:
    """Small AES-GCM file store; the key is supplied by the secret manager."""

    def __init__(self, key: bytes) -> None:
        if len(key) not in {16, 24, 32}:
            raise TokenError("OAuth encryption key must be 16, 24, or 32 bytes")
        self._key = key

    def save(self, path: Path, token: dict[str, object]) -> None:
        nonce = secrets.token_bytes(12)
        payload = json.dumps(token, sort_keys=True, separators=(",", ":")).encode()
        encrypted = AESGCM(self._key).encrypt(nonce, payload, None)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"nonce": _encode(nonce), "ciphertext": _encode(encrypted)}),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def load(self, path: Path) -> dict[str, object]:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            plaintext = AESGCM(self._key).decrypt(
                _decode(envelope["nonce"]), _decode(envelope["ciphertext"]), None
            )
            value = json.loads(plaintext)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            InvalidTag,
        ) as error:
            raise TokenError("encrypted OAuth token could not be loaded") from error
        if not isinstance(value, dict):
            raise TokenError("encrypted OAuth token must contain a JSON object")
        return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("encrypted value must be text")
    return base64.urlsafe_b64decode(value.encode("ascii"))


@dataclass(frozen=True, slots=True)
class GmailMessage:
    provider_message_id: str
    raw_message: bytes
    label_ids: tuple[str, ...] = ()


class GmailClient(Protocol):
    def list_messages(self, *, label_id: str, limit: int) -> list[str]: ...

    def get_message(self, message_id: str) -> GmailMessage: ...

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...], remove: tuple[str, ...]
    ) -> None: ...


class GmailApiClient:
    """Minimal Gmail REST client. OAuth consent is deliberately external."""

    def __init__(self, access_token: str, timeout_seconds: float = 10.0) -> None:
        self._token = access_token
        self._timeout = timeout_seconds
        self._base = "https://gmail.googleapis.com/gmail/v1/users/me"

    def list_messages(self, *, label_id: str, limit: int) -> list[str]:
        query = urllib.parse.urlencode({"labelIds": label_id, "maxResults": limit})
        data = self._request("GET", f"/messages?{query}")
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            return []
        return [
            item["id"]
            for item in messages
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]

    def get_message(self, message_id: str) -> GmailMessage:
        encoded_id = urllib.parse.quote(message_id, safe="")
        data = self._request("GET", f"/messages/{encoded_id}?format=raw")
        raw = data.get("raw")
        provider_id = data.get("id")
        label_ids = data.get("labelIds", [])
        if (
            not isinstance(raw, str)
            or not isinstance(provider_id, str)
            or not isinstance(label_ids, list)
        ):
            raise ValueError("Gmail returned an invalid message")
        return GmailMessage(
            provider_message_id=provider_id,
            raw_message=base64.urlsafe_b64decode(raw + "=="),
            label_ids=tuple(value for value in label_ids if isinstance(value, str)),
        )

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...], remove: tuple[str, ...]
    ) -> None:
        self._request(
            "POST",
            f"/messages/{urllib.parse.quote(message_id, safe='')}/modify",
            {"addLabelIds": list(add), "removeLabelIds": list(remove)},
        )

    def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        request = urllib.request.Request(  # noqa: S310 - fixed Gmail HTTPS endpoint
            self._base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return cast(dict[str, object], json.loads(response.read()))
