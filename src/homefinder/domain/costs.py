"""Conservative, explicit effective acquisition-cost calculations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostEstimate:
    purchase_price_minor: int
    mandatory_extras_minor: int = 0
    closing_costs_minor: int = 0
    renovation_low_minor: int = 0
    renovation_base_minor: int = 0
    renovation_high_minor: int = 0
    contingency_minor: int = 0
    financed_purchase_value_minor: int | None = None
    monthly_installment_minor: int | None = None

    def __post_init__(self) -> None:
        amounts = (
            self.purchase_price_minor,
            self.mandatory_extras_minor,
            self.closing_costs_minor,
            self.renovation_low_minor,
            self.renovation_base_minor,
            self.renovation_high_minor,
            self.contingency_minor,
        )
        if any(amount < 0 for amount in amounts):
            raise ValueError("cost amounts cannot be negative")
        if (
            self.financed_purchase_value_minor is not None
            and not 0
            <= self.financed_purchase_value_minor
            <= self.acquisition_price_minor
        ):
            raise ValueError(
                "financed purchase value must be between zero and acquisition price"
            )
        if (
            self.monthly_installment_minor is not None
            and self.monthly_installment_minor < 0
        ):
            raise ValueError("monthly installment cannot be negative")

    @property
    def acquisition_price_minor(self) -> int:
        return self.purchase_price_minor + self.mandatory_extras_minor

    @property
    def acquisition_cash_high_minor(self) -> int | None:
        """Cash needed for acquisition and immediate works after financing."""
        if self.financed_purchase_value_minor is None:
            return None
        return self.effective_all_in_high_minor - self.financed_purchase_value_minor

    @property
    def effective_all_in_low_minor(self) -> int:
        return (
            self.acquisition_price_minor
            + self.closing_costs_minor
            + self.renovation_low_minor
            + self.contingency_minor
        )

    @property
    def effective_all_in_base_minor(self) -> int:
        return (
            self.acquisition_price_minor
            + self.closing_costs_minor
            + self.renovation_base_minor
            + self.contingency_minor
        )

    @property
    def effective_all_in_high_minor(self) -> int:
        return (
            self.acquisition_price_minor
            + self.closing_costs_minor
            + self.renovation_high_minor
            + self.contingency_minor
        )

    def affordability(
        self, cash_budget_minor: int, max_installment_minor: int
    ) -> bool | None:
        if self.monthly_installment_minor is None:
            return None
        if self.acquisition_cash_high_minor is None:
            return None
        return (
            self.acquisition_cash_high_minor <= cash_budget_minor
            and self.monthly_installment_minor <= max_installment_minor
        )
