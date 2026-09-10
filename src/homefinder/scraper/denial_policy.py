"""Pure route outcomes; the coordinator owns persistence and aggregate pacing.

Evidence is bounded metadata supplied by transport, never an exception message,
proxy address, header collection, or source response body. A retry grant is
consumed transactionally by the coordinator before it schedules the one direct
attempt. An immediate grant does not exempt that attempt from source pacing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

RouteClass = Literal["direct", "proxy"]


class FailureEvidence(str, Enum):
    PROXY_CONNECT_TIMEOUT = "proxy-connect-timeout"
    PROXY_TLS_FAILURE = "proxy-tls-failure"
    PROXY_AUTHENTICATION = "proxy-authentication"
    VERIFIED_PROXY_GATEWAY_ERROR = "verified-proxy-gateway-error"
    TRANSPORT_FAILURE = "transport-failure"


class ResponseClassification(str, Enum):
    SUCCESS = "success"
    PROXY_INFRASTRUCTURE_FAILURE = "proxy-infrastructure-failure"
    PORTAL_DENIAL = "portal-denial"
    PORTAL_RESPONSE = "portal-response"
    TRANSPORT_FAILURE = "transport-failure"


@dataclass(frozen=True)
class ResponseEvidence:
    status_code: int | None = None
    failure: FailureEvidence | None = None
    portal_responded: bool = False
    retry_after_seconds: int | None = None
    challenge: bool = False

    def __post_init__(self) -> None:
        if self.status_code is not None and not 100 <= self.status_code <= 599:
            raise ValueError("invalid response status")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("invalid retry delay")


def classify_response(
    route: RouteClass, evidence: ResponseEvidence
) -> ResponseClassification:
    """Require positive pre-portal evidence before granting infrastructure relief.

    A gateway error must have been positively identified by transport using a
    reviewed provider contract; a generic 502/503 alone is never such evidence.
    Conflicting denial evidence wins over a claimed infrastructure failure.
    """
    if (
        evidence.status_code in (403, 429)
        or evidence.retry_after_seconds is not None
        or evidence.challenge
        or (evidence.status_code == 407 and evidence.portal_responded)
    ):
        return ResponseClassification.PORTAL_DENIAL
    pre_response_failure = evidence.status_code is None and evidence.failure in (
        FailureEvidence.PROXY_CONNECT_TIMEOUT,
        FailureEvidence.PROXY_TLS_FAILURE,
        FailureEvidence.PROXY_AUTHENTICATION,
    )
    if (
        route == "proxy"
        and not evidence.portal_responded
        and (
            evidence.status_code == 407
            or pre_response_failure
            or evidence.failure is FailureEvidence.VERIFIED_PROXY_GATEWAY_ERROR
        )
    ):
        return ResponseClassification.PROXY_INFRASTRUCTURE_FAILURE
    if evidence.status_code is not None:
        if 200 <= evidence.status_code < 300:
            return ResponseClassification.SUCCESS
        return ResponseClassification.PORTAL_RESPONSE
    if evidence.portal_responded:
        return ResponseClassification.PORTAL_RESPONSE
    return ResponseClassification.TRANSPORT_FAILURE


@dataclass(frozen=True)
class RouteDecision:
    retry_direct_at: datetime | None
    source_cooldown_until: datetime | None
    mark_proxy_unhealthy: bool
    quarantine_proxy_source: bool
    direct_attempt_used: bool
    consecutive_direct_denials: int


def decide_route_outcome(
    route: RouteClass,
    classification: ResponseClassification,
    *,
    now: datetime,
    direct_attempt_used: bool = False,
    consecutive_direct_denials: int = 0,
    retry_after_seconds: int | None = None,
) -> RouteDecision:
    """Return one direct grant or a source cooldown, never another proxy route.

    ``direct_attempt_used`` means the fallback allowance has already been
    granted, including while its attempt is pending. The caller persists it
    with the outcome under the current task lease. Source cooldown deadlines
    must be merged monotonically with existing state by the coordinator.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("policy time must be timezone aware")
    if consecutive_direct_denials < 0:
        raise ValueError("invalid denial count")
    if retry_after_seconds is not None and retry_after_seconds < 0:
        raise ValueError("invalid retry delay")
    denial = classification is ResponseClassification.PORTAL_DENIAL
    infrastructure_failure = (
        classification is ResponseClassification.PROXY_INFRASTRUCTURE_FAILURE
    )
    retry_at = None
    cooldown_until = None
    if route == "proxy" and (denial or infrastructure_failure):
        if not direct_attempt_used:
            delay = max(900, retry_after_seconds or 0) if denial else 0
            retry_at = now + timedelta(seconds=delay)
            direct_attempt_used = True
    elif route == "direct" and denial:
        hours = (6, 12, 24)[min(consecutive_direct_denials, 2)]
        cooldown_until = now + timedelta(
            seconds=max(hours * 3600, retry_after_seconds or 0)
        )
        consecutive_direct_denials += 1
    return RouteDecision(
        retry_direct_at=retry_at,
        source_cooldown_until=cooldown_until,
        mark_proxy_unhealthy=route == "proxy" and infrastructure_failure,
        quarantine_proxy_source=route == "proxy" and denial,
        direct_attempt_used=direct_attempt_used,
        consecutive_direct_denials=consecutive_direct_denials,
    )
