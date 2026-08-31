"""Evidence-based primary-market project and developer risk dossiers."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import Lock
from uuid import uuid4

from homefinder.domain.matching import TriState


class EntityRole(str, Enum):
    CONTRACTING_SPV = "contracting_spv"
    PARENT_GROUP = "parent_group"
    CONTRACTOR = "contractor"
    PROJECT = "project"


class EvidenceKind(str, Enum):
    KRS_EXTRACT = "krs_extract"
    FINANCIAL_STATEMENT = "financial_statement"
    PERMIT = "permit"
    PROSPECTUS = "prospectus"
    TRUST_ACCOUNT = "trust_account"
    DFG_CONFIRMATION = "dfg_confirmation"
    CONTRACT = "contract"
    SITE_REPORT = "site_report"
    OFFICIAL_NOTICE = "official_notice"
    OTHER = "other"


class RiskDimension(str, Enum):
    BUYER_FUNDS = "buyer_funds_protection"
    LEGAL_PERMIT = "legal_permit_readiness"
    FINANCIAL_CORPORATE = "developer_financial_corporate"
    CONSTRUCTION_SCHEDULE = "construction_schedule"
    CONTRACTUAL = "contractual_protection"
    EVIDENCE = "evidence_completeness_freshness"


class RiskLevel(str, Enum):
    LOWER_CONCERN = "lower concern"
    WATCH = "watch"
    HIGHER_CONCERN = "higher concern"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProjectEntity:
    id: str
    name: str
    role: EntityRole
    registration_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("entity id and name are required")


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    subject_id: str
    kind: EvidenceKind
    source: str
    observed_at: datetime
    reference: str
    permitted: bool
    summary: str = ""

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (self.id, self.subject_id, self.source, self.reference)
        ):
            raise ValueError("evidence identity, source, and reference are required")
        if not self.permitted:
            raise ValueError("evidence must be obtained through a permitted method")


@dataclass(frozen=True, slots=True)
class CriticalCheck:
    name: str
    state: TriState
    requirement: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.requirement.strip():
            raise ValueError("critical check name and requirement are required")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    dimension: RiskDimension
    level: RiskLevel
    facts: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PrimaryMarketDossier:
    project_id: str
    project_name: str
    entities: tuple[ProjectEntity, ...]
    evidence: tuple[Evidence, ...]
    critical_checks: tuple[CriticalCheck, ...]
    risks: tuple[RiskAssessment, ...]

    @property
    def missing_critical_checks(self) -> tuple[str, ...]:
        return tuple(
            check.name
            for check in self.critical_checks
            if check.state is not TriState.PASS
        )

    @property
    def serious_legal_risk(self) -> bool:
        return any(
            risk.dimension is RiskDimension.LEGAL_PERMIT
            and risk.level is RiskLevel.HIGHER_CONCERN
            for risk in self.risks
        )

    @property
    def normal_eligibility(self) -> TriState:
        if self.serious_legal_risk:
            return TriState.FAIL
        if self.missing_critical_checks:
            return TriState.UNKNOWN
        if any(risk.level is RiskLevel.HIGHER_CONCERN for risk in self.risks):
            return TriState.FAIL
        return TriState.PASS

    @property
    def manual_review(self) -> bool:
        return self.normal_eligibility is not TriState.PASS or bool(
            self.missing_critical_checks
        )

    @property
    def overall_concern(self) -> RiskLevel:
        if self.serious_legal_risk:
            return RiskLevel.HIGHER_CONCERN
        levels = {risk.level for risk in self.risks}
        if RiskLevel.HIGHER_CONCERN in levels:
            return RiskLevel.HIGHER_CONCERN
        if RiskLevel.UNKNOWN in levels or self.missing_critical_checks:
            return RiskLevel.UNKNOWN
        if RiskLevel.WATCH in levels:
            return RiskLevel.WATCH
        return RiskLevel.LOWER_CONCERN

    @property
    def summary(self) -> str:
        missing = ", ".join(self.missing_critical_checks)
        if missing:
            return f"{self.overall_concern.value}; critical checks missing: {missing}"
        return f"{self.overall_concern.value}; completion is not guaranteed"


@dataclass(frozen=True, slots=True)
class ManualVerificationTask:
    id: str
    project_id: str
    subject: str
    reason: str
    created_at: datetime
    status: str = "pending"


class ManualTaskQueue:
    """Small development contract; production persistence is migration-backed."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str, str], ManualVerificationTask] = {}
        self._lock = Lock()

    def enqueue(
        self,
        project_id: str,
        subject: str,
        reason: str,
        *,
        created_at: datetime | None = None,
    ) -> ManualVerificationTask:
        key = (project_id, subject, reason)
        with self._lock:
            return self._tasks.setdefault(
                key,
                ManualVerificationTask(
                    str(uuid4()),
                    project_id,
                    subject,
                    reason,
                    created_at or datetime.now().astimezone(),
                ),
            )

    def pending(self) -> tuple[ManualVerificationTask, ...]:
        with self._lock:
            return tuple(
                task for task in self._tasks.values() if task.status == "pending"
            )


@dataclass(frozen=True, slots=True)
class PermittedDocument:
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str


def ingest_document(
    *,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    storage_key: str,
    permitted: bool,
) -> PermittedDocument:
    """Record safe metadata only; callers must separately store protected content."""
    if not permitted:
        raise PermissionError("document source is not permitted")
    if filename != filename.split("/")[-1] or "\\" in filename:
        raise ValueError("document filename must not contain a path")
    if (
        size_bytes < 0
        or len(sha256) != 64
        or any(char not in "0123456789abcdef" for char in sha256)
    ):
        raise ValueError("invalid document metadata")
    return PermittedDocument(filename, content_type, size_bytes, sha256, storage_key)
