"""Context ranking rules, without a database."""

from datetime import date

import pytest

from kb import context
from kb.context import UserContext, assess

TODAY = date(2026, 9, 30)
BRAM = UserContext(country="BE", location="Antwerp", department="Payroll", position="Payroll consultant")


def doc(**meta):
    base = {"country": "BE", "department": "Payroll", "owner": "Legal Desk", "updated_at": date(2026, 3, 1)}
    return base | meta


def test_matching_country_and_department_raise_the_score():
    a = assess(doc(), BRAM, TODAY)
    assert a.factor == pytest.approx(1 + context.COUNTRY_MATCH + context.DEPARTMENT_MATCH + context.FRESH)
    assert "applies to your country (BE)" in a.reasons
    assert "written for your department (Payroll)" in a.reasons
    assert a.warnings == []


def test_other_country_is_a_warning():
    a = assess(doc(country="NL"), BRAM, TODAY)
    assert "applies to NL, you work in BE" in a.warnings
    assert a.adjustment < 0.5


def test_global_document_is_neutral():
    a = assess(doc(country=None), BRAM, TODAY)
    assert "applies to all countries" in a.reasons
    assert not any("country" in w for w in a.warnings)


def test_expired_document_is_demoted_and_explained():
    fresh = assess(doc(), BRAM, TODAY)
    expired = assess(doc(valid_until=date(2024, 12, 31)), BRAM, TODAY)
    assert "expired on 2024-12-31" in expired.warnings
    assert expired.factor < fresh.factor


def test_not_yet_valid_and_in_force():
    assert "only valid from 2027-01-01" in assess(doc(valid_from=date(2027, 1, 1)), BRAM, TODAY).warnings
    assert "currently in force" in assess(doc(valid_from=date(2025, 1, 1)), BRAM, TODAY).reasons


def test_validity_boundaries_are_inclusive():
    assert "currently in force" in assess(doc(valid_until=TODAY), BRAM, TODAY).reasons
    assert "currently in force" in assess(doc(valid_from=TODAY), BRAM, TODAY).reasons


def test_age_of_the_last_update():
    stale = assess(doc(updated_at=date(2019, 6, 1)), BRAM, TODAY)
    assert "last updated 7 years ago" in stale.warnings
    assert "no update date" in assess(doc(updated_at=None), BRAM, TODAY).warnings


def test_source_owner_and_uploader_signals():
    teams = assess(doc(source="teams", owner=None), BRAM, TODAY)
    assert {"informal source (teams)", "no accountable owner"} <= set(teams.warnings)
    policy = assess(doc(source="Policy", uploader_is_manager=True, uploader_position="Payroll BE lead"), BRAM, TODAY)
    assert "official policy document" in policy.reasons
    assert "uploaded by a manager of its group (Payroll BE lead)" in policy.reasons
    assert policy.factor > teams.factor


def test_user_without_profile_gets_no_personal_boost():
    a = assess(doc(), UserContext(), TODAY)
    assert not any("your" in r for r in a.reasons)


def test_factor_never_reaches_zero():
    worst = doc(country="NL", department="HR", owner=None, source="teams",
                updated_at=date(2010, 1, 1), valid_until=date(2011, 1, 1))
    assert assess(worst, BRAM, TODAY).factor == context.MIN_FACTOR


def test_signals_can_be_switched_off_one_by_one():
    worst = doc(country="NL", owner=None, source="teams", valid_until=date(2020, 1, 1), updated_at=date(2010, 1, 1))
    assert assess(worst, BRAM, TODAY, signals=()).adjustment == 0
    only_country = assess(worst, BRAM, TODAY, signals=("country",))
    assert only_country.warnings == ["applies to NL, you work in BE"]
    assert only_country.adjustment == context.COUNTRY_OTHER
