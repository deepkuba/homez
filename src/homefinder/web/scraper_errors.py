"""Authenticated scraper-error history and live-stream presentation."""

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    ListingRecord,
    SourceRecord,
    WorkflowJobAttemptRecord,
    WorkflowJobRecord,
)

_SCRAPER_JOB_KINDS = ("poll", "normalize")
_STAGE_LABELS = {
    "poll": "Pobieranie alertów",
    "normalize": "Scraping i normalizacja",
}
_DEFAULT_PAGE_SIZE = 50
_MAX_CURSOR_LENGTH = 256
_STREAM_BATCH_SIZE = 100


class InvalidScraperErrorCursor(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScraperErrorItem:
    cursor: str
    occurred_at: datetime
    source: str
    stage: str
    attempt: int
    outcome: str
    code: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "cursor": self.cursor,
            "occurred_at": _aware(self.occurred_at).isoformat(),
            "source": self.source,
            "stage": self.stage,
            "attempt": self.attempt,
            "outcome": self.outcome,
            "code": self.code,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ScraperErrorPage:
    items: tuple[ScraperErrorItem, ...]
    next_cursor: str | None


def load_scraper_errors(
    sessions: sessionmaker[Session],
    *,
    before: str | None = None,
    after: str | None = None,
    limit: int = _DEFAULT_PAGE_SIZE,
) -> ScraperErrorPage:
    """Load a stable page of errors, newest-first or newer-than-cursor."""
    if before is not None and after is not None:
        raise InvalidScraperErrorCursor("choose either before or after")
    if limit < 1 or limit > _STREAM_BATCH_SIZE:
        raise ValueError("limit must be between 1 and 100")
    before_key = _decode_cursor(before) if before is not None else None
    after_key = _decode_cursor(after) if after is not None else None
    occurred_at = func.coalesce(
        WorkflowJobAttemptRecord.finished_at,
        WorkflowJobAttemptRecord.started_at,
    )
    query = (
        select(
            WorkflowJobAttemptRecord,
            WorkflowJobRecord.kind,
            WorkflowJobRecord.payload_json,
            occurred_at.label("occurred_at"),
        )
        .join(
            WorkflowJobRecord, WorkflowJobRecord.id == WorkflowJobAttemptRecord.job_id
        )
        .where(
            WorkflowJobRecord.kind.in_(_SCRAPER_JOB_KINDS),
            or_(
                WorkflowJobAttemptRecord.error_code.is_not(None),
                and_(
                    WorkflowJobAttemptRecord.outcome.is_not(None),
                    WorkflowJobAttemptRecord.outcome != "succeeded",
                ),
            ),
        )
    )
    if before_key is not None:
        query = query.where(_key_condition(occurred_at, before_key, newer=False))
    if after_key is not None:
        query = query.where(_key_condition(occurred_at, after_key, newer=True))
    ascending = after_key is not None
    order = (
        (
            occurred_at.asc(),
            WorkflowJobAttemptRecord.job_id.asc(),
            WorkflowJobAttemptRecord.attempt_number.asc(),
        )
        if ascending
        else (
            occurred_at.desc(),
            WorkflowJobAttemptRecord.job_id.desc(),
            WorkflowJobAttemptRecord.attempt_number.desc(),
        )
    )
    with sessions() as session:
        rows = session.execute(query.order_by(*order).limit(limit + 1)).all()
        visible_rows = rows[:limit]
        source_by_listing = _sources_for_rows(session, visible_rows)
    items = tuple(
        _item_from_row(row, source_by_listing=source_by_listing) for row in visible_rows
    )
    next_cursor = items[-1].cursor if not ascending and len(rows) > limit else None
    return ScraperErrorPage(items, next_cursor)


def render_scraper_error_dashboard(
    sessions: sessionmaker[Session], *, nonce: str, now: datetime
) -> str:
    page = load_scraper_errors(sessions)
    rows = "".join(_render_row(item) for item in page.items) or (
        "<tr id=empty-row><td colspan=7>"
        "Brak zarejestrowanych błędów scraperów.</td></tr>"
    )
    newest_cursor = (
        page.items[0].cursor
        if page.items
        else _encode_cursor(_aware(now), _max_uuid(), 2**31 - 1)
    )
    next_cursor = page.next_cursor or ""
    return (
        "<!doctype html><html lang=pl><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width'>"
        "<title>Błędy scraperów</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:86rem;margin:2rem auto;"
        "padding:0 1rem;color:#202124}header{display:flex;align-items:end;"
        "justify-content:space-between;gap:1rem;flex-wrap:wrap}.meta{color:#5f6368}"
        ".live{color:#137333;font-weight:650}.error{color:#b3261e;font-weight:650}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{text-align:left;"
        "vertical-align:top;border-bottom:1px solid #ddd;padding:.65rem}th{white-space:"
        "nowrap}code{white-space:normal;overflow-wrap:anywhere}button{font:inherit;"
        "padding:.6rem .9rem}a{color:inherit}@media(max-width:50rem){"
        "table,thead,tbody,tr,"
        "th,td{display:block}thead{display:none}tr{border:1px solid #ddd;border-radius:"
        ".6rem;margin:.8rem 0;padding:.4rem}td{border:0;padding:.3rem}}</style>"
        "<body><main><p><a href='/feedback/queue'>← Kolejka</a> · "
        "<a href='/feedback/offers'>Oferty</a></p><header><div><h1>Błędy scraperów</h1>"
        "<p class=meta>Najnowsze błędy są na górze. Historia jest ładowana na żądanie."
        "</p></div><p id=stream-state class=live>● Strumień aktywny</p></header>"
        "<table><thead><tr><th>Czas</th><th>Portal</th><th>Etap</th><th>Próba</th>"
        "<th>Stan</th><th>Kod</th><th>Szczegóły</th></tr></thead>"
        f"<tbody id=error-rows>{rows}</tbody></table>"
        f"<button id=load-more data-before='{escape(next_cursor, quote=True)}'"
        f"{' hidden' if not next_cursor else ''}>Załaduj starsze</button>"
        f"<script nonce='{escape(nonce, quote=True)}'>"
        "const rows=document.getElementById('error-rows');"
        "const button=document.getElementById('load-more');"
        "const state=document.getElementById('stream-state');"
        "const seen=new Set([...rows.querySelectorAll('[data-cursor]')]"
        ".map(r=>r.dataset.cursor));"
        "function add(item,prepend){if(seen.has(item.cursor))return;"
        "seen.add(item.cursor);document.getElementById('empty-row')?.remove();"
        "const row=document.createElement('tr');row.dataset.cursor=item.cursor;"
        "for(const value of [item.occurred_at,item.source,item.stage,"
        "String(item.attempt),item.outcome,item.code,item.detail]){const cell="
        "document.createElement('td');cell.textContent=value;row.appendChild(cell);}"
        "prepend?rows.prepend(row):rows.append(row);}"
        "button.addEventListener('click',async()=>{button.disabled=true;"
        "const response=await fetch('/feedback/scraper-errors/history?before='+"
        "encodeURIComponent(button.dataset.before));if(response.ok){"
        "const page=await response.json();page.items.forEach(i=>add(i,false));"
        "button.dataset.before=page.next_cursor||'';button.hidden=!page.next_cursor;}"
        "button.disabled=false;});"
        f"const stream=new EventSource('/feedback/scraper-errors/stream?after="
        f"{escape(newest_cursor, quote=True)}');"
        "stream.addEventListener('scraper-error',event=>"
        "add(JSON.parse(event.data),true));"
        "stream.onopen=()=>{state.textContent='● Strumień aktywny';"
        "state.className='live';};stream.onerror=()=>{"
        "state.textContent='● Ponowne łączenie';state.className='error';};"
        "</script></main></body></html>"
    )


async def stream_scraper_errors(
    sessions: sessionmaker[Session],
    *,
    after: str | None,
    poll_interval: float = 2.0,
) -> AsyncIterator[str]:
    cursor = after or _encode_cursor(datetime.now(timezone.utc), _max_uuid(), 2**31 - 1)
    last_heartbeat = time.monotonic()
    while True:
        page = load_scraper_errors(sessions, after=cursor, limit=_STREAM_BATCH_SIZE)
        if page.items:
            for item in page.items:
                cursor = item.cursor
                payload = json.dumps(
                    item.as_dict(), ensure_ascii=False, separators=(",", ":")
                )
                yield f"id: {cursor}\nevent: scraper-error\ndata: {payload}\n\n"
            last_heartbeat = time.monotonic()
        elif time.monotonic() - last_heartbeat >= 15:
            yield ": heartbeat\n\n"
            last_heartbeat = time.monotonic()
        await asyncio.sleep(poll_interval)


def _key_condition(occurred_at, key: tuple[datetime, UUID, int], *, newer: bool):  # type: ignore[no-untyped-def]
    timestamp, job_id, attempt = key
    compare_time = occurred_at > timestamp if newer else occurred_at < timestamp
    compare_job = (
        WorkflowJobAttemptRecord.job_id > job_id
        if newer
        else WorkflowJobAttemptRecord.job_id < job_id
    )
    compare_attempt = (
        WorkflowJobAttemptRecord.attempt_number > attempt
        if newer
        else WorkflowJobAttemptRecord.attempt_number < attempt
    )
    return or_(
        compare_time,
        and_(occurred_at == timestamp, compare_job),
        and_(
            occurred_at == timestamp,
            WorkflowJobAttemptRecord.job_id == job_id,
            compare_attempt,
        ),
    )


def _sources_for_rows(session: Session, rows: Sequence[object]) -> dict[UUID, str]:
    listing_ids = {
        listing_id
        for row in rows
        if (listing_id := _payload_listing_id(row.payload_json)) is not None  # type: ignore[attr-defined]
    }
    if not listing_ids:
        return {}
    return {
        listing_id: display_name
        for listing_id, display_name in session.execute(
            select(ListingRecord.id, SourceRecord.display_name)
            .join(SourceRecord, SourceRecord.id == ListingRecord.source_id)
            .where(ListingRecord.id.in_(listing_ids))
        )
    }


def _item_from_row(row, *, source_by_listing: dict[UUID, str]) -> ScraperErrorItem:  # type: ignore[no-untyped-def]
    attempt = row[0]
    occurred_at = _aware(row.occurred_at)
    payload = _payload(row.payload_json)
    listing_id = _payload_listing_id(row.payload_json)
    source = source_by_listing.get(listing_id) if listing_id is not None else None
    if source is None and isinstance(payload.get("source_key"), str):
        source = str(payload["source_key"])
    return ScraperErrorItem(
        cursor=_encode_cursor(occurred_at, attempt.job_id, attempt.attempt_number),
        occurred_at=occurred_at,
        source=source or "Nieznany",
        stage=_STAGE_LABELS.get(row.kind, row.kind),
        attempt=attempt.attempt_number,
        outcome=attempt.outcome or "błąd",
        code=attempt.error_code or attempt.outcome or "nieznany-błąd",
        detail=attempt.error_detail or "Brak dodatkowych szczegółów.",
    )


def _render_row(item: ScraperErrorItem) -> str:
    values = (
        _aware(item.occurred_at).strftime("%Y-%m-%d %H:%M:%S UTC"),
        item.source,
        item.stage,
        str(item.attempt),
        item.outcome,
        item.code,
        item.detail,
    )
    cells = "".join(f"<td>{escape(value)}</td>" for value in values)
    return f'<tr data-cursor="{escape(item.cursor, quote=True)}">{cells}</tr>'


def _payload(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _payload_listing_id(value: str) -> UUID | None:
    listing_id = _payload(value).get("listing_id")
    try:
        return UUID(listing_id) if isinstance(listing_id, str) else None
    except ValueError:
        return None


def _encode_cursor(occurred_at: datetime, job_id: UUID, attempt: int) -> str:
    raw = json.dumps(
        [_aware(occurred_at).isoformat(), str(job_id), attempt], separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID, int]:
    if not value or len(value) > _MAX_CURSOR_LENGTH:
        raise InvalidScraperErrorCursor("invalid scraper error cursor")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        parsed = json.loads(raw)
        timestamp = datetime.fromisoformat(parsed[0])
        job_id = UUID(parsed[1])
        attempt = parsed[2]
        if timestamp.tzinfo is None or not isinstance(attempt, int) or attempt < 1:
            raise ValueError
        return _aware(timestamp), job_id, attempt
    except (ValueError, TypeError, IndexError, json.JSONDecodeError) as error:
        raise InvalidScraperErrorCursor("invalid scraper error cursor") from error


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _max_uuid() -> UUID:
    return UUID(int=(1 << 128) - 1)
