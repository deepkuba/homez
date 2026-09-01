import base64
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from homefinder.application.gmail_labels import GmailLabelManager
from homefinder.catalog.orm import Base, GmailLabelBindingRecord
from homefinder.sources.gmail import (
    GMAIL_MODIFY_SCOPE,
    EncryptedTokenStore,
    GmailLabel,
    OAuthRefreshResult,
    RefreshableOAuthTokenProvider,
    TokenError,
    load_encryption_key,
    read_secret_text,
)


class FakeRefreshClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def refresh(
        self, *, refresh_token: str, client_id: str, client_secret: str
    ) -> OAuthRefreshResult:
        self.calls.append((refresh_token, client_id, client_secret))
        refreshed_access = "new-access-token"
        return OAuthRefreshResult(
            access_token=refreshed_access,
            expires_in=3600,
            scope=GMAIL_MODIFY_SCOPE,
        )


class FakeLabelClient:
    def __init__(self) -> None:
        self.labels: list[GmailLabel] = []
        self.created: list[str] = []

    def list_labels(self) -> list[GmailLabel]:
        return list(self.labels)

    def create_label(self, name: str) -> GmailLabel:
        self.created.append(name)
        label = GmailLabel(id=f"Label_{len(self.labels) + 1}", name=name)
        self.labels.append(label)
        return label


def _write_private(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def test_expired_oauth_token_refreshes_and_is_reencrypted(tmp_path: Path) -> None:
    key = b"k" * 32
    path = tmp_path / "oauth.enc"
    store = EncryptedTokenStore(key)
    store.save(
        path,
        {
            "access_token": "expired-access-token",
            "refresh_token": "refresh-secret",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scope": GMAIL_MODIFY_SCOPE,
            "expires_at": 0,
        },
    )
    refresh = FakeRefreshClient()
    provider = RefreshableOAuthTokenProvider(
        store=store,
        token_path=path,
        refresh_client=refresh,
        now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert provider.access_token() == "new-access-token"
    assert refresh.calls == [("refresh-secret", "client-id", "client-secret")]
    assert "new-access-token" not in path.read_text(encoding="utf-8")
    saved = store.load(path)
    assert saved["access_token"] == "new-access-token"
    assert saved["refresh_token"] == "refresh-secret"


def test_oauth_store_and_key_reject_insecure_permissions(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    _write_private(key_path, base64.urlsafe_b64encode(b"k" * 32).decode())
    assert load_encryption_key(key_path) == b"k" * 32

    token_path = tmp_path / "oauth.enc"
    EncryptedTokenStore(b"k" * 32).save(token_path, {"token": "secret"})
    token_path.chmod(0o644)

    with pytest.raises(TokenError, match="permissions"):
        EncryptedTokenStore(b"k" * 32).load(token_path)
    key_path.chmod(0o640)
    with pytest.raises(TokenError, match="permissions"):
        load_encryption_key(key_path)


def test_secret_reader_rejects_symlinks_and_redacts_errors(tmp_path: Path) -> None:
    password = "database-password"
    secret = tmp_path / "database-url"
    _write_private(secret, f"postgresql://user:{password}@db/homez")
    link = tmp_path / "database-link"
    os.symlink(secret, link)

    with pytest.raises(TokenError) as captured:
        read_secret_text(link)

    assert password not in str(captured.value)


def test_refresh_rejects_non_minimal_scope_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oauth.enc"
    store = EncryptedTokenStore(b"k" * 32)
    secret = "refresh-secret"
    store.save(
        path,
        {
            "access_token": "access-secret",
            "refresh_token": secret,
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scope": f"{GMAIL_MODIFY_SCOPE} https://mail.google.com/",
            "expires_at": 0,
        },
    )

    with pytest.raises(TokenError) as captured:
        RefreshableOAuthTokenProvider(
            store=store,
            token_path=path,
            refresh_client=FakeRefreshClient(),
        ).access_token()

    assert "least-privilege" in str(captured.value)
    assert secret not in str(captured.value)


def test_label_ids_are_resolved_and_persisted_per_mailbox() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeLabelClient()
    with Session(engine) as session:
        first = GmailLabelManager(session, gmail).resolve(
            mailbox_key="primary", source_key="otodom"
        )
        second = GmailLabelManager(session, gmail).resolve(
            mailbox_key="primary", source_key="otodom"
        )

        assert first == second
        assert gmail.created == [
            "HOMEZ/otodom/ALERT",
            "HOMEZ/otodom/PROCESSED",
            "HOMEZ/otodom/QUARANTINE",
            "HOMEZ/otodom/RETRY",
        ]
        assert first.alert == "Label_1"
        bindings = session.scalars(select(GmailLabelBindingRecord)).all()
        assert {(item.mailbox_key, item.source_key) for item in bindings} == {
            ("primary", "otodom")
        }


def test_same_source_labels_are_namespaced_to_mailbox() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeLabelClient()
    with Session(engine) as session:
        GmailLabelManager(session, gmail).resolve(
            mailbox_key="primary", source_key="gratka"
        )
        GmailLabelManager(session, gmail).resolve(
            mailbox_key="sandbox", source_key="gratka"
        )

        bindings = session.scalars(select(GmailLabelBindingRecord)).all()
        assert {item.mailbox_key for item in bindings} == {"primary", "sandbox"}
