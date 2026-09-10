"""Production result calculations that are independent of portal extraction."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

PriceBand = Literal["low", "normal", "high", "unknown"]


def resolve_price_per_sqm(
    price_minor: int | None,
    area_sqm: str | None,
    *,
    explicit_minor: int | None,
) -> tuple[int | None, bool]:
    """Return the explicit/derived value and whether page values conflict."""
    calculated = None
    if price_minor is not None and area_sqm is not None:
        try:
            area = Decimal(area_sqm)
            if price_minor > 0 and area > 0:
                calculated = int(
                    (Decimal(price_minor) / area).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                )
        except (InvalidOperation, ValueError):
            calculated = None
    if explicit_minor is not None:
        if explicit_minor <= 0:
            raise ValueError("explicit price per square metre must be positive")
        inconsistent = (
            calculated is not None and abs(explicit_minor - calculated) > 10_000
        )
        return explicit_minor, inconsistent
    return calculated, False


@dataclass(frozen=True)
class PricePerSqmBands:
    low_upper_bound_minor: int
    high_lower_bound_minor: int
    currency: Literal["PLN", "EUR", "USD"]

    def __post_init__(self) -> None:
        if not 0 < self.low_upper_bound_minor < self.high_lower_bound_minor:
            raise ValueError("price-per-square-metre boundaries must be ordered")

    def classify(self, value_minor: int | None) -> PriceBand:
        if value_minor is None:
            return "unknown"
        if value_minor <= self.low_upper_bound_minor:
            return "low"
        if value_minor >= self.high_lower_bound_minor:
            return "high"
        return "normal"
