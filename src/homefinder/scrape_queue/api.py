"""Private, source-scoped worker API. No enqueue, activation, or raw-content API."""

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from homefinder.parsers.contracts import (
    FieldCandidate,
    FieldState,
    PageFacts,
    ParserResult,
    Portal,
    ResolvedField,
)
from homefinder.scrape_queue.budget import SourceBudgetRepository
from homefinder.scrape_queue.contracts import (
    CaptureOutcome,
    LostLease,
    NetworkPermit,
    ScrapeLease,
    TaskClass,
    WorkerIdentity,
)
from homefinder.scrape_queue.repository import ScrapeQueueRepository
from homefinder.scraper.denial_policy import ResponseClassification
from homefinder.sources.gmail import read_secret_text

Model = TypeVar("Model", bound=BaseModel)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Credential(Payload):
    identity: WorkerIdentity
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


class ClaimPayload(Payload):
    task_classes: tuple[TaskClass, ...] = tuple(TaskClass)


class CapabilityPayload(Payload):
    expected_identity: WorkerIdentity | None = None
    release_hashes: tuple[str, ...] = Field(max_length=64)
    healthy: bool = True


class LeasePayload(Payload):
    task_id: UUID
    source: Portal
    snapshot_id: UUID
    canonical_url: str = Field(max_length=2048, repr=False)
    task_class: TaskClass
    release_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    activation_epoch: int = Field(gt=0)
    lease_token: UUID = Field(repr=False)
    lease_expires_at: datetime
    attempt_number: int = Field(gt=0)

    def lease(self) -> ScrapeLease:
        return ScrapeLease(**self.model_dump())


class LeaseMutation(Payload):
    lease: LeasePayload


class SuccessPayload(LeaseMutation):
    code: Literal["downloaded", "partial", "artifact-unavailable"] = "downloaded"
    response_bytes: int = Field(default=0, ge=0, le=2_000_000)


class FailurePayload(LeaseMutation):
    code: Literal["transport-error", "parser-error", "invalid-target"]


class DeferPayload(LeaseMutation):
    available_at: datetime
    code: Literal["portal-denied", "source-cooldown", "budget-exhausted"]


class CandidatePayload(Payload):
    name: str = Field(max_length=80)
    value: str | int | bool | None = Field(repr=False)
    origin: str = Field(max_length=80)
    locator: str = Field(max_length=200)
    release_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedFieldPayload(Payload):
    name: str = Field(pattern=r"^[a-z_]{1,50}$")
    state: FieldState
    value: str | int | bool | None = Field(default=None, repr=False)
    selected_origin: str | None = Field(default=None, max_length=80)

    def field(self) -> ResolvedField:
        return ResolvedField(self.name, self.state, self.value, self.selected_origin)


class ResultPayload(Payload):
    capture_id: UUID
    release_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    candidates: tuple[CandidatePayload, ...] = Field(max_length=64, repr=False)
    missing_fields: tuple[str, ...] = Field(max_length=32)
    facts: PageFacts
    fields: tuple[ResolvedFieldPayload, ...] = Field(default=(), max_length=12)

    def result(self) -> ParserResult:
        return ParserResult(
            self.capture_id,
            self.release_hash,
            self.variant,
            tuple(FieldCandidate(**item.model_dump()) for item in self.candidates),
            self.missing_fields,
            facts=self.facts,
            fields=tuple(item.field() for item in self.fields),
        )


class OutcomePayload(Payload):
    capture_id: UUID
    fetched_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=2_000_000)
    result: ResultPayload = Field(repr=False)
    artifact_id: UUID | None = Field(default=None, repr=False)

    def outcome(self) -> CaptureOutcome:
        return CaptureOutcome(
            self.capture_id,
            self.fetched_at,
            self.content_hash,
            self.size_bytes,
            self.result.result(),
            str(self.artifact_id) if self.artifact_id is not None else None,
        )


class CompletePayload(LeaseMutation):
    outcome: OutcomePayload = Field(repr=False)


class NetworkOutcomePayload(LeaseMutation):
    permit: NetworkPermit
    classification: ResponseClassification
    transferred_bytes: int = Field(ge=0, le=2_000_000)
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)


def create_coordinator_router(
    repository: ScrapeQueueRepository,
    *,
    budget: SourceBudgetRepository | None = None,
    credentials_file: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> APIRouter:
    repository = repository.with_clock(clock)
    router = APIRouter(prefix="/internal/scrape/v1", include_in_schema=False)

    def authorize(request: Request) -> WorkerIdentity:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 4096:
            raise HTTPException(
                401, "unauthorized", headers={"WWW-Authenticate": "Bearer"}
            )
        presented = hashlib.sha256(authorization[7:].encode()).hexdigest()
        try:
            raw = read_secret_text(credentials_file)
            if len(raw) > 16_384:
                raise ValueError("oversize identities")
            credentials = TypeAdapter(list[Credential]).validate_json(raw)
            if (
                not 1 <= len(credentials) <= 64
                or len({item.identity.worker_id for item in credentials})
                != len(credentials)
                or len({item.token_sha256 for item in credentials}) != len(credentials)
            ):
                raise ValueError("duplicate or missing identities")
        except (ValueError, RuntimeError, OSError) as error:
            raise HTTPException(
                503, "coordinator authentication unavailable"
            ) from error
        selected = None
        for item in credentials:
            if hmac.compare_digest(presented, item.token_sha256):
                selected = item.identity
        if selected is None:
            raise HTTPException(
                401, "unauthorized", headers={"WWW-Authenticate": "Bearer"}
            )
        return selected

    async def execute(operation: Callable[[], object]) -> JSONResponse:
        try:
            result = await run_in_threadpool(operation)
        except LostLease as error:
            raise HTTPException(409, "lease is no longer valid") from error
        except ValueError:
            raise HTTPException(422, "request contract is invalid") from None
        except Exception:
            raise HTTPException(503, "coordinator operation unavailable") from None
        return JSONResponse(
            jsonable_encoder(result), headers={"Cache-Control": "no-store"}
        )

    @router.post("/workers/heartbeat")
    async def capabilities(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, CapabilityPayload)
        if (
            payload.expected_identity is not None
            and payload.expected_identity != worker
        ):
            raise HTTPException(403, "worker configuration does not match credential")
        return await execute(
            lambda: repository.register_worker(
                worker,
                release_hashes=payload.release_hashes,
                healthy=payload.healthy,
                now=clock(),
            )
        )

    @router.post("/claim")
    async def claim(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, ClaimPayload)
        return await execute(
            lambda: repository.claim(
                worker, task_classes=payload.task_classes, now=clock()
            )
        )

    @router.post("/heartbeat")
    async def heartbeat(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, LeaseMutation)
        return await execute(
            lambda: repository.heartbeat(worker, payload.lease.lease(), now=clock())
        )

    @router.post("/network/reserve")
    async def reserve_network_start(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, LeaseMutation)
        if budget is None:
            raise HTTPException(503, "source budget configuration unavailable")
        return await execute(
            lambda: budget.reserve_start(worker, payload.lease.lease(), now=clock())
        )

    @router.post("/network/outcome")
    async def record_network_outcome(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, NetworkOutcomePayload)
        if budget is None:
            raise HTTPException(503, "source budget configuration unavailable")
        return await execute(
            lambda: budget.record_outcome(
                worker,
                payload.lease.lease(),
                payload.permit,
                payload.classification,
                now=clock(),
                transferred_bytes=payload.transferred_bytes,
                retry_after_seconds=payload.retry_after_seconds,
            )
        )

    @router.post("/succeed")
    async def succeed(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, SuccessPayload)
        return await execute(
            lambda: repository.succeed(
                worker,
                payload.lease.lease(),
                now=clock(),
                code=payload.code,
                response_bytes=payload.response_bytes,
            )
        )

    @router.post("/complete")
    async def complete(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, CompletePayload, maximum=128_000)
        return await execute(
            lambda: repository.complete(
                worker, payload.lease.lease(), payload.outcome.outcome(), now=clock()
            )
        )

    @router.post("/fail")
    async def fail(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, FailurePayload)
        return await execute(
            lambda: repository.fail(
                worker, payload.lease.lease(), now=clock(), code=payload.code
            )
        )

    @router.post("/defer")
    async def defer(request: Request) -> JSONResponse:
        worker = authorize(request)
        payload = await bounded_payload(request, DeferPayload)
        return await execute(
            lambda: repository.defer(
                worker,
                payload.lease.lease(),
                now=clock(),
                code=payload.code,
                available_at=payload.available_at,
            )
        )

    @router.get("/status")
    async def status(request: Request) -> JSONResponse:
        worker = authorize(request)
        try:
            if set(request.query_params) - {"limit", "before"}:
                raise ValueError
            limit = int(request.query_params.get("limit", "50"))
        except ValueError as error:
            raise HTTPException(422, "invalid status query") from error
        return await execute(
            lambda: repository.status(
                source=worker.source,
                limit=limit,
                before=request.query_params.get("before"),
            )
        )

    return router


async def bounded_payload(
    request: Request, model: type[Model], *, maximum: int = 16_384
) -> Model:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if not 0 <= int(content_length) <= maximum:
                raise HTTPException(413, "request body is too large")
        except ValueError as error:
            raise HTTPException(422, "invalid content length") from error
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise HTTPException(413, "request body is too large")
        body.extend(chunk)
    try:
        return model.model_validate_json(body)
    except (ValidationError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(422, "request contract is invalid") from error
