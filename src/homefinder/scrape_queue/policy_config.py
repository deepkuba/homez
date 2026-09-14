"""Bounded, reviewed coordinator policy configuration."""

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from homefinder.parsers.contracts import Portal
from homefinder.scrape_queue.contracts import SourceBudgetPolicy

PORTALS = frozenset({"gratka", "morizon", "otodom", "olx"})
MAX_POLICY_BYTES = 16_384


class _PortalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    minimum_interval_seconds: int = Field(ge=1, le=3600)
    daily_attempt_limit: int = Field(ge=1, le=100_000)
    daily_success_limit: int = Field(ge=1, le=100_000)
    policy_version: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_limits(self) -> "_PortalPolicy":
        if self.daily_success_limit > self.daily_attempt_limit:
            raise ValueError("success limit exceeds attempt limit")
        return self


class _PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    billing_cycle_anchor_day: int = Field(ge=1, le=28)
    proxy_route_ids: tuple[str, ...] = Field(default=(), max_length=64)
    portals: dict[Portal, _PortalPolicy]

    @model_validator(mode="after")
    def require_all_portals(self) -> "_PolicyFile":
        if (
            set(self.portals) != PORTALS
            or len(set(self.proxy_route_ids)) != len(self.proxy_route_ids)
            or any(
                re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", route) is None
                for route in self.proxy_route_ids
            )
        ):
            raise ValueError("source budget policy must contain all portals")
        return self


@dataclass(frozen=True)
class SourceBudgetConfiguration:
    policies: dict[str, SourceBudgetPolicy]
    billing_cycle_anchor_day: int
    proxy_route_ids: tuple[str, ...]


def load_source_budget_configuration(path: Path) -> SourceBudgetConfiguration:
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_POLICY_BYTES:
        raise ValueError("source budget policy file has invalid size")
    model = TypeAdapter(_PolicyFile).validate_json(raw)
    return SourceBudgetConfiguration(
        policies={
            source: SourceBudgetPolicy(
                timedelta(seconds=value.minimum_interval_seconds),
                value.daily_attempt_limit,
                value.daily_success_limit,
                value.policy_version,
            )
            for source, value in model.portals.items()
        },
        billing_cycle_anchor_day=model.billing_cycle_anchor_day,
        proxy_route_ids=model.proxy_route_ids,
    )
