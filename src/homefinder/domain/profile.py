"""Versioned buyer-profile values used by matching and ranking."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BuyerProfile:
    """An immutable, auditable snapshot of the active buyer preferences."""

    version: int = 1
    effective_from: date = date(2026, 8, 30)
    destination: str = "ul. Podbrzezie 6, 31-054 Krakow"
    max_commute_minutes: int = 45
    min_area_sqm: Decimal = Decimal("40")
    min_rooms: int = 2
    max_purchase_price_minor: int = 80_000_000
    core_purchase_price_minor: int = 75_000_000
    max_monthly_installment_minor: int = 400_000
    cash_budget_minor: int = 20_000_000
    max_building_dwellings: int = 80
    excluded_localities: frozenset[str] = frozenset({"skawina"})
    ideal_area_low_sqm: Decimal = Decimal("48")
    ideal_area_high_sqm: Decimal = Decimal("55")
    score_weights: tuple[tuple[str, Decimal], ...] = (
        ("ready_to_move", Decimal("25")),
        ("quiet", Decimal("20")),
        ("green_space", Decimal("15")),
        ("balcony", Decimal("10")),
        ("separate_kitchen", Decimal("10")),
        ("small_building", Decimal("10")),
        ("area_fit", Decimal("10")),
    )

    def weights(self) -> dict[str, Decimal]:
        return dict(self.score_weights)


DEFAULT_PROFILE = BuyerProfile()
