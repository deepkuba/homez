from __future__ import annotations

import base64
import json
import os
import secrets
import stat
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TokenError(ValueError):
    """Raised when an encrypted OAuth token cannot be loaded."""


GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
MAX_SECRET_BYTES = 128_000


def _read_secret_bytes(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise TokenError("secret path must be a regular file")
            if not _secret_permissions_allowed(
                path,
                owner_uid=metadata.st_uid,
                mode=stat.S_IMODE(metadata.st_mode),
                current_uid=os.getuid(),
            ):
                raise TokenError("secret file ownership or permissions are unsafe")
            value = os.read(descriptor, MAX_SECRET_BYTES + 1)
        finally:
            os.close(descriptor)
    except TokenError:
        raise
    except OSError as error:
        raise TokenError("secret file could not be read safely") from error
    if len(value) > MAX_SECRET_BYTES:
        raise TokenError("secret file exceeds the size limit")
    return value


def _secret_permissions_allowed(
    path: Path, *, owner_uid: int, mode: int, current_uid: int
) -> bool:
    private_user_file = owner_uid == current_uid and mode & 0o077 == 0
    container_secret = (
        owner_uid == 0
        and path.is_absolute()
        and path.parts[:3] == ("/", "run", "secrets")
        and mode & 0o022 == 0
    )
    return private_user_file or container_secret


def read_secret_text(path: Path) -> str:
    try:
        return _read_secret_bytes(path).decode("utf-8").strip()
    except UnicodeError as error:
        raise TokenError("secret file must contain UTF-8 text") from error


def load_encryption_key(path: Path) -> bytes:
    try:
        key = base64.urlsafe_b64decode(read_secret_text(path).encode("ascii"))
    except TokenError:
        raise
    except (UnicodeError, ValueError) as error:
        raise TokenError("OAuth encryption key file is invalid") from error
    if len(key) not in {16, 24, 32}:
        raise TokenError("OAuth encryption key must be 16, 24, or 32 bytes")
    return key


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
        envelope = json.dumps(
            {"nonce": _encode(nonce), "ciphertext": _encode(encrypted)}
        ).encode()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            _read_secret_bytes(path)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(envelope)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise TokenError("encrypted OAuth token could not be saved") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def load(self, path: Path) -> dict[str, object]:
        try:
            envelope = json.loads(_read_secret_bytes(path))
            plaintext = AESGCM(self._key).decrypt(
                _decode(envelope["nonce"]), _decode(envelope["ciphertext"]), None
            )
            value = json.loads(plaintext)
        except TokenError:
            raise
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


@dataclass(frozen=True, slots=True)
class OAuthRefreshResult:
    access_token: str = field(repr=False)
    expires_in: int
    scope: str | None


class OAuthRefreshClient(Protocol):
    def refresh(
        self, *, refresh_token: str, client_id: str, client_secret: str
    ) -> OAuthRefreshResult: ...


class GoogleOAuthRefreshClient:
    """Refresh OAuth access without exposing request or response secrets."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds
        self._endpoint = "https://oauth2.googleapis.com/token"

    def refresh(
        self, *, refresh_token: str, client_id: str, client_secret: str
    ) -> OAuthRefreshResult:
        body = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310 - fixed Google endpoint
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed Google endpoint
                request, timeout=self._timeout
            ) as response:
                value = json.loads(response.read())
            access_token = value.get("access_token")
            expires_in = value.get("expires_in")
            scope = value.get("scope")
            if (
                not isinstance(access_token, str)
                or not access_token
                or not isinstance(expires_in, int)
                or expires_in <= 0
                or (scope is not None and not isinstance(scope, str))
            ):
                raise ValueError
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise TokenError("OAuth access token refresh failed") from error
        return OAuthRefreshResult(access_token, expires_in, scope)


class AccessTokenProvider(Protocol):
    def access_token(self) -> str: ...


class StaticAccessTokenProvider:
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    def access_token(self) -> str:
        return self._access_token


class RefreshableOAuthTokenProvider:
    def __init__(
        self,
        *,
        store: EncryptedTokenStore,
        token_path: Path,
        refresh_client: OAuthRefreshClient,
        now: Callable[[], datetime] | None = None,
        refresh_skew: timedelta = timedelta(minutes=1),
    ) -> None:
        self._store = store
        self._token_path = token_path
        self._refresh_client = refresh_client
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._refresh_skew = refresh_skew

    def access_token(self) -> str:
        token = self._store.load(self._token_path)
        access_token = _required_token_text(token, "access_token")
        scope = _required_token_text(token, "scope")
        if scope != GMAIL_MODIFY_SCOPE:
            raise TokenError(
                "OAuth token must use the least-privilege gmail.modify scope"
            )
        expires_at = token.get("expires_at")
        if not isinstance(expires_at, (int, float)):
            raise TokenError("encrypted OAuth token has invalid expiry metadata")
        if expires_at > (self._now() + self._refresh_skew).timestamp():
            return access_token

        refreshed = self._refresh_client.refresh(
            refresh_token=_required_token_text(token, "refresh_token"),
            client_id=_required_token_text(token, "client_id"),
            client_secret=_required_token_text(token, "client_secret"),
        )
        if refreshed.scope is not None and refreshed.scope != GMAIL_MODIFY_SCOPE:
            raise TokenError("OAuth refresh returned a non-minimal scope")
        updated = dict(token)
        updated.update(
            access_token=refreshed.access_token,
            expires_at=int(self._now().timestamp()) + refreshed.expires_in,
            scope=GMAIL_MODIFY_SCOPE,
        )
        self._store.save(self._token_path, updated)
        return refreshed.access_token


def _required_token_text(token: dict[str, object], key: str) -> str:
    value = token.get(key)
    if not isinstance(value, str) or not value:
        raise TokenError("encrypted OAuth token is incomplete")
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


@dataclass(frozen=True, slots=True)
class GmailLabel:
    id: str
    name: str


class GmailClient(Protocol):
    def list_messages(self, *, label_id: str, limit: int) -> list[str]: ...

    def get_message(self, message_id: str) -> GmailMessage: ...

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...], remove: tuple[str, ...]
    ) -> None: ...

    def list_labels(self) -> list[GmailLabel]: ...

    def create_label(self, name: str) -> GmailLabel: ...


class GmailApiClient:
    """Minimal Gmail REST client. OAuth consent is deliberately external."""

    def __init__(
        self,
        token_provider: AccessTokenProvider | str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._token_provider = (
            StaticAccessTokenProvider(token_provider)
            if isinstance(token_provider, str)
            else token_provider
        )
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

    def list_labels(self) -> list[GmailLabel]:
        data = self._request("GET", "/labels")
        labels = data.get("labels", [])
        if not isinstance(labels, list):
            raise ValueError("Gmail returned invalid labels")
        return [
            GmailLabel(id=item["id"], name=item["name"])
            for item in labels
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("name"), str)
        ]

    def create_label(self, name: str) -> GmailLabel:
        data = self._request(
            "POST",
            "/labels",
            {
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        label_id = data.get("id")
        label_name = data.get("name")
        if not isinstance(label_id, str) or not isinstance(label_name, str):
            raise ValueError("Gmail returned an invalid created label")
        return GmailLabel(label_id, label_name)

    def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        request = urllib.request.Request(  # noqa: S310 - fixed Gmail HTTPS endpoint
            self._base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._token_provider.access_token()}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return cast(dict[str, object], json.loads(response.read()))
