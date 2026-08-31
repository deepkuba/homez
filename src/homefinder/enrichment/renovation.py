"""Conservative renovation estimates and comparable-home review contracts."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from enum import Enum
from statistics import median

from homefinder.domain.matching import TriState


def _money(value: int, field: str) -> None:
    if value < 0:
        raise ValueError(f"{field} cannot be negative")


@dataclass(frozen=True, slots=True)
class RenovationItem:
    """One work package; all amounts are minor currency units."""

    name: str
    low_minor: int
    base_minor: int
    high_minor: int
    required: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("renovation item name is required")
        for name, value in (
            ("low", self.low_minor),
            ("base", self.base_minor),
            ("high", self.high_minor),
        ):
            _money(value, f"{name} renovation estimate")
        if not self.low_minor <= self.base_minor <= self.high_minor:
            raise ValueError("renovation estimates must be low <= base <= high")


@dataclass(frozen=True, slots=True)
class RenovationEstimate:
    items: tuple[RenovationItem, ...]
    contingency_rate: Decimal = Decimal("0.15")

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.contingency_rate <= Decimal("1"):
            raise ValueError("contingency rate must be between 0 and 1")

    @property
    def low_minor(self) -> int:
        return sum(item.low_minor for item in self.items)

    @property
    def base_minor(self) -> int:
        return sum(item.base_minor for item in self.items)

    @property
    def high_minor(self) -> int:
        return sum(item.high_minor for item in self.items)

    @property
    def contingency_minor(self) -> int:
        return int(
            (Decimal(self.high_minor) * self.contingency_rate).quantize(
                Decimal("1"), rounding=ROUND_CEILING
            )
        )

    @property
    def high_with_contingency_minor(self) -> int:
        return self.high_minor + self.contingency_minor


@dataclass(frozen=True, slots=True)
class Comparable:
    id: str
    effective_move_in_minor: int
    similarity: Decimal
    evidence_source: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _money(self.effective_move_in_minor, "comparable price")
        if not Decimal("0") <= self.similarity <= Decimal("1"):
            raise ValueError("comparable similarity must be between 0 and 1")
        if not self.id.strip() or not self.evidence_source.strip():
            raise ValueError("comparable id and evidence source are required")


@dataclass(frozen=True, slots=True)
class HabitabilityChecklist:
    utilities_connected: bool | None = None
    bathroom_usable: bool | None = None
    kitchen_usable: bool | None = None
    heating_operational: bool | None = None
    windows_secure: bool | None = None

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "utilities_connected",
                "bathroom_usable",
                "kitchen_usable",
                "heating_operational",
                "windows_secure",
            )
            if getattr(self, name) is not True
        )

    @property
    def complete(self) -> bool:
        return not self.missing


class AttachmentKind(str, Enum):
    CONTRACTOR_QUOTE = "contractor_quote"
    INSPECTION_REPORT = "inspection_report"
    RENOVATION_DOCUMENT = "renovation_document"


@dataclass(frozen=True, slots=True)
class AttachmentMetadata:
    storage_key: str
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.storage_key.strip() or not self.filename.strip():
            raise ValueError("attachment key and filename are required")
        if self.filename != self.filename.split("/")[-1] or "\\" in self.filename:
            raise ValueError("attachment filename must not contain a path")
        if self.size_bytes < 0:
            raise ValueError("attachment size cannot be negative")
        if self.kind not in {kind.value for kind in AttachmentKind}:
            raise ValueError("unsupported attachment kind")
        if len(self.sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.sha256
        ):
            raise ValueError("attachment sha256 must be lowercase hexadecimal")


@dataclass(frozen=True, slots=True)
class RenovationAssessment:
    estimate: RenovationEstimate
    selected_comparables: tuple[Comparable, ...]
    adjusted_advantage_minor: int | None
    normal_eligibility: TriState
    confidence: Decimal
    manual_review: bool
    explanation: str


class RenovationWorkflow:
    """Select sufficiently similar evidence and apply the conservative rule."""

    def __init__(self, *, minimum_similarity: Decimal = Decimal("0.70")) -> None:
        if not Decimal("0") <= minimum_similarity <= Decimal("1"):
            raise ValueError("minimum similarity must be between 0 and 1")
        self.minimum_similarity = minimum_similarity

    def assess(
        self,
        *,
        purchase_price_minor: int,
        estimate: RenovationEstimate,
        comparables: tuple[Comparable, ...] = (),
        mandatory_extras_minor: int = 0,
        habitability: HabitabilityChecklist | None = None,
    ) -> RenovationAssessment:
        _money(purchase_price_minor, "purchase price")
        _money(mandatory_extras_minor, "mandatory extras")
        selected = tuple(
            comparable
            for comparable in comparables
            if comparable.similarity >= self.minimum_similarity
        )
        if not selected:
            return RenovationAssessment(
                estimate,
                (),
                None,
                TriState.UNKNOWN,
                Decimal("0.00"),
                True,
                "no sufficiently similar move-in-ready comparables; manual review",
            )

        comparable_price = int(median(c.effective_move_in_minor for c in selected))
        advantage = (
            comparable_price
            - purchase_price_minor
            - mandatory_extras_minor
            - estimate.high_with_contingency_minor
        )
        checklist_review = habitability is not None and not habitability.complete
        state = (
            TriState.UNKNOWN
            if checklist_review
            else (TriState.PASS if advantage >= 0 else TriState.FAIL)
        )
        average_similarity = sum(
            (c.similarity for c in selected), Decimal("0")
        ) / Decimal(len(selected))
        confidence = average_similarity.quantize(Decimal("0.01"))
        return RenovationAssessment(
            estimate,
            selected,
            advantage,
            state,
            confidence,
            checklist_review,
            "habitability checklist incomplete; manual review"
            if checklist_review
            else (
                "high renovation estimate plus contingency compared with selected homes"
            ),
        )
