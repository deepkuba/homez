from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from homefinder.catalog.orm import Base
from homefinder.catalog.profile_repository import (
    NoApprovedBuyerProfile,
    ProfileAlreadyApproved,
    SqlAlchemyBuyerProfileRepository,
)
from homefinder.domain.profile import BuyerProfile


def test_profile_is_versioned_persisted_and_inactive_until_human_approval() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    profile = BuyerProfile(
        version=2,
        effective_from=date(2026, 9, 1),
        min_area_sqm=Decimal("42.5"),
    )
    approved_at = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)

    with Session(engine) as session:
        repository = SqlAlchemyBuyerProfileRepository(session)
        repository.add_draft(profile, created_at=approved_at)
        with pytest.raises(NoApprovedBuyerProfile):
            repository.active()

        repository.approve(
            profile.version, approved_by="buyer", approved_at=approved_at
        )

        assert repository.active() == profile
        assert repository.get(profile.version) == profile


def test_approved_profile_cannot_be_replaced_or_reapproved() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    at = datetime(2026, 9, 1, tzinfo=timezone.utc)

    with Session(engine) as session:
        repository = SqlAlchemyBuyerProfileRepository(session)
        repository.add_draft(BuyerProfile(), created_at=at)
        repository.approve(1, approved_by="buyer", approved_at=at)

        with pytest.raises(ProfileAlreadyApproved):
            repository.approve(1, approved_by="someone-else", approved_at=at)
        with pytest.raises(ValueError, match="already exists"):
            repository.add_draft(BuyerProfile(), created_at=at)
