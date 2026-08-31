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
    monthly_installment_minor: int | None = None

    @property
    def acquisition_price_minor(self) -> int:
        return self.purchase_price_minor + self.mandatory_extras_minor

    @property
    def cash_needed_at_closing_minor(self) -> int:
        return self.acquisition_price_minor + self.closing_costs_minor

    @property
    def effective_all_in_low_minor(self) -> int:
        return (
            self.acquisition_price_minor
            + self.renovation_low_minor
            + self.contingency_minor
        )

    @property
    def effective_all_in_base_minor(self) -> int:
        return (
            self.acquisition_price_minor
            + self.renovation_base_minor
            + self.contingency_minor
        )

    @property
    def effective_all_in_high_minor(self) -> int:
        return (
            self.acquisition_price_minor
            + self.renovation_high_minor
            + self.contingency_minor
        )

    def affordability(
        self, cash_budget_minor: int, max_installment_minor: int
    ) -> bool | None:
        if self.monthly_installment_minor is None:
            return None
        return (
            self.cash_needed_at_closing_minor <= cash_budget_minor
            and self.monthly_installment_minor <= max_installment_minor
        )
