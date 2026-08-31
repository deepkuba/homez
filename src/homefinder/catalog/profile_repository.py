"""Persistent, approval-gated buyer-profile versions."""

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from homefinder.catalog.orm import BuyerProfileRecord
from homefinder.domain.profile import BuyerProfile


class NoApprovedBuyerProfile(LookupError):
    pass


class ProfileAlreadyApproved(ValueError):
    pass


class SqlAlchemyBuyerProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_draft(self, profile: BuyerProfile, *, created_at: datetime) -> None:
        record = BuyerProfileRecord(
            version=profile.version,
            effective_from=profile.effective_from.isoformat(),
            profile_json=_serialize(profile),
            created_at=created_at,
            approved_at=None,
            approved_by=None,
        )
        try:
            self._session.add(record)
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise ValueError(
                f"buyer profile version {profile.version} already exists"
            ) from error

    def approve(self, version: int, *, approved_by: str, approved_at: datetime) -> None:
        if not approved_by.strip():
            raise ValueError("approved_by is required")
        record = self._session.get(BuyerProfileRecord, version)
        if record is None:
            raise LookupError(f"buyer profile version {version} does not exist")
        if record.approved_at is not None:
            raise ProfileAlreadyApproved(
                f"buyer profile version {version} is already approved"
            )
        record.approved_by = approved_by
        record.approved_at = approved_at
        self._session.commit()

    def get(self, version: int) -> BuyerProfile:
        record = self._session.get(BuyerProfileRecord, version)
        if record is None:
            raise LookupError(f"buyer profile version {version} does not exist")
        return _deserialize(record.profile_json)

    def active(self) -> BuyerProfile:
        record = self._session.scalar(
            select(BuyerProfileRecord)
            .where(BuyerProfileRecord.approved_at.is_not(None))
            .order_by(BuyerProfileRecord.version.desc())
        )
        if record is None:
            raise NoApprovedBuyerProfile("buyer approval is required before activation")
        return _deserialize(record.profile_json)


def _serialize(profile: BuyerProfile) -> str:
    payload = {
        "version": profile.version,
        "effective_from": profile.effective_from.isoformat(),
        "destination": profile.destination,
        "max_commute_minutes": profile.max_commute_minutes,
        "min_area_sqm": str(profile.min_area_sqm),
        "min_rooms": profile.min_rooms,
        "max_purchase_price_minor": profile.max_purchase_price_minor,
        "core_purchase_price_minor": profile.core_purchase_price_minor,
        "max_monthly_installment_minor": profile.max_monthly_installment_minor,
        "cash_budget_minor": profile.cash_budget_minor,
        "max_building_dwellings": profile.max_building_dwellings,
        "excluded_localities": sorted(profile.excluded_localities),
        "ideal_area_low_sqm": str(profile.ideal_area_low_sqm),
        "ideal_area_high_sqm": str(profile.ideal_area_high_sqm),
        "score_weights": [
            [name, str(weight)] for name, weight in profile.score_weights
        ],
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _deserialize(value: str) -> BuyerProfile:
    payload = json.loads(value)
    return BuyerProfile(
        version=int(payload["version"]),
        effective_from=date.fromisoformat(payload["effective_from"]),
        destination=str(payload["destination"]),
        max_commute_minutes=int(payload["max_commute_minutes"]),
        min_area_sqm=Decimal(payload["min_area_sqm"]),
        min_rooms=int(payload["min_rooms"]),
        max_purchase_price_minor=int(payload["max_purchase_price_minor"]),
        core_purchase_price_minor=int(payload["core_purchase_price_minor"]),
        max_monthly_installment_minor=int(payload["max_monthly_installment_minor"]),
        cash_budget_minor=int(payload["cash_budget_minor"]),
        max_building_dwellings=int(payload["max_building_dwellings"]),
        excluded_localities=frozenset(
            str(item) for item in payload["excluded_localities"]
        ),
        ideal_area_low_sqm=Decimal(payload["ideal_area_low_sqm"]),
        ideal_area_high_sqm=Decimal(payload["ideal_area_high_sqm"]),
        score_weights=tuple(
            (str(name), Decimal(weight)) for name, weight in payload["score_weights"]
        ),
    )
