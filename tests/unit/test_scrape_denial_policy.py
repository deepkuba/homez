from datetime import datetime, timedelta, timezone

import pytest

from homefinder.scraper.denial_policy import (
    FailureEvidence,
    ResponseClassification,
    ResponseEvidence,
    classify_response,
    decide_route_outcome,
)

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "failure",
    [
        FailureEvidence.PROXY_CONNECT_TIMEOUT,
        FailureEvidence.PROXY_TLS_FAILURE,
        FailureEvidence.PROXY_AUTHENTICATION,
        FailureEvidence.VERIFIED_PROXY_GATEWAY_ERROR,
    ],
)
def test_verified_proxy_failure_permits_exactly_one_immediate_direct_attempt(
    failure: FailureEvidence,
) -> None:
    classification = classify_response("proxy", ResponseEvidence(failure=failure))
    assert classification is ResponseClassification.PROXY_INFRASTRUCTURE_FAILURE
    decision = decide_route_outcome("proxy", classification, now=NOW)
    assert decision.retry_direct_at == NOW
    assert decision.direct_attempt_used
    assert decision.mark_proxy_unhealthy
    assert not decision.quarantine_proxy_source
    repeated = decide_route_outcome(
        "proxy", classification, now=NOW, direct_attempt_used=True
    )
    assert repeated.retry_direct_at is None


@pytest.mark.parametrize("seconds", [None, 0, 60, 900, 1800])
def test_proxy_denial_waits_fifteen_minutes_or_longer_retry_after(
    seconds: int | None,
) -> None:
    classification = classify_response(
        "proxy", ResponseEvidence(status_code=429, retry_after_seconds=seconds)
    )
    decision = decide_route_outcome(
        "proxy", classification, now=NOW, retry_after_seconds=seconds
    )
    assert classification is ResponseClassification.PORTAL_DENIAL
    assert decision.retry_direct_at == NOW + timedelta(seconds=max(900, seconds or 0))
    assert decision.quarantine_proxy_source
    assert not decision.mark_proxy_unhealthy
    assert decision.source_cooldown_until is None
    assert decision.direct_attempt_used
    assert (
        decide_route_outcome(
            "proxy", classification, now=NOW, direct_attempt_used=True
        ).retry_direct_at
        is None
    )


@pytest.mark.parametrize("prior,hours", [(0, 6), (1, 12), (2, 24), (3, 24), (1000, 24)])
def test_direct_denial_applies_capped_source_wide_cooldown(
    prior: int, hours: int
) -> None:
    decision = decide_route_outcome(
        "direct",
        ResponseClassification.PORTAL_DENIAL,
        now=NOW,
        consecutive_direct_denials=prior,
        direct_attempt_used=True,
    )
    assert decision.source_cooldown_until == NOW + timedelta(hours=hours)
    assert decision.consecutive_direct_denials == prior + 1
    assert decision.retry_direct_at is None
    assert not decision.quarantine_proxy_source
    assert not decision.mark_proxy_unhealthy


def test_longer_direct_retry_after_is_respected() -> None:
    decision = decide_route_outcome(
        "direct",
        ResponseClassification.PORTAL_DENIAL,
        now=NOW,
        retry_after_seconds=30 * 3600,
    )
    assert decision.source_cooldown_until == NOW + timedelta(hours=30)


@pytest.mark.parametrize(
    "evidence",
    [
        ResponseEvidence(status_code=403),
        ResponseEvidence(status_code=429),
        ResponseEvidence(status_code=200, challenge=True),
        ResponseEvidence(status_code=200, retry_after_seconds=0),
        ResponseEvidence(status_code=503, retry_after_seconds=1200),
        ResponseEvidence(status_code=403, failure=FailureEvidence.PROXY_TLS_FAILURE),
        ResponseEvidence(status_code=407, portal_responded=True),
    ],
)
def test_portal_denial_evidence_never_becomes_proxy_infrastructure_failure(
    evidence: ResponseEvidence,
) -> None:
    assert classify_response("proxy", evidence) is ResponseClassification.PORTAL_DENIAL


@pytest.mark.parametrize("status", [301, 404, 410, 500, 502, 503])
def test_ambiguous_http_responses_do_not_grant_immediate_direct_retry(
    status: int,
) -> None:
    classification = classify_response("proxy", ResponseEvidence(status_code=status))
    assert classification is ResponseClassification.PORTAL_RESPONSE
    assert (
        decide_route_outcome("proxy", classification, now=NOW).retry_direct_at is None
    )


def test_proxy_authentication_407_is_positive_infrastructure_evidence() -> None:
    assert (
        classify_response("proxy", ResponseEvidence(status_code=407))
        is ResponseClassification.PROXY_INFRASTRUCTURE_FAILURE
    )


def test_generic_transport_failure_cannot_claim_proxy_provenance() -> None:
    classification = classify_response(
        "proxy", ResponseEvidence(failure=FailureEvidence.TRANSPORT_FAILURE)
    )
    assert classification is ResponseClassification.TRANSPORT_FAILURE
    assert (
        decide_route_outcome("proxy", classification, now=NOW).retry_direct_at is None
    )


@pytest.mark.parametrize("status", [200, 301, 502])
def test_http_response_disproves_claimed_pre_response_connection_failure(
    status: int,
) -> None:
    classification = classify_response(
        "proxy",
        ResponseEvidence(
            status_code=status, failure=FailureEvidence.PROXY_CONNECT_TIMEOUT
        ),
    )
    assert classification is not ResponseClassification.PROXY_INFRASTRUCTURE_FAILURE


def test_success_preserves_denial_streak_for_coordinator_reset_policy() -> None:
    classification = classify_response("direct", ResponseEvidence(status_code=200))
    assert classification is ResponseClassification.SUCCESS
    decision = decide_route_outcome(
        "direct", classification, now=NOW, consecutive_direct_denials=2
    )
    assert decision.consecutive_direct_denials == 2
    assert decision.source_cooldown_until is None
    assert decision.retry_direct_at is None


def test_invalid_policy_inputs_fail_closed() -> None:
    with pytest.raises(ValueError):
        ResponseEvidence(retry_after_seconds=-1)
    with pytest.raises(ValueError):
        ResponseEvidence(status_code=700)
    with pytest.raises(ValueError):
        decide_route_outcome(
            "proxy", ResponseClassification.PORTAL_DENIAL, now=NOW.replace(tzinfo=None)
        )
    with pytest.raises(ValueError):
        decide_route_outcome(
            "direct",
            ResponseClassification.PORTAL_DENIAL,
            now=NOW,
            consecutive_direct_denials=-1,
        )
