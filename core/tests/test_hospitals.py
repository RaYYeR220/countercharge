from pathlib import Path

import pytest

from countercharge_core.hospitals import Hospital, load_hospitals, recipient_allowed

ENGINE_HOSPITALS_PATH = (
    Path(__file__).resolve().parents[2] / "engine" / "data" / "hospitals.json"
)


def test_load_hospitals_from_engine_data():
    hospitals = load_hospitals(ENGINE_HOSPITALS_PATH)
    assert "nyp" in hospitals
    assert "ccf" in hospitals
    nyp = hospitals["nyp"]
    assert isinstance(nyp, Hospital)
    assert nyp.billing_email_domain == "nyp.org"
    assert nyp.demo_billing_email == "countercharge.demo+billing@gmail.com"


def test_load_hospitals_all_have_demo_billing_email():
    hospitals = load_hospitals(ENGINE_HOSPITALS_PATH)
    for hospital in hospitals.values():
        assert hospital.demo_billing_email == "countercharge.demo+billing@gmail.com"


@pytest.fixture
def hospital():
    return Hospital(
        hospital_id="nyp",
        name="NewYork-Presbyterian Hospital",
        nonprofit=True,
        billing_email_domain="nyp.org",
        demo_billing_email="countercharge.demo+billing@gmail.com",
    )


def test_recipient_allowed_for_domain_match(hospital):
    assert recipient_allowed(hospital, "billing@nyp.org")
    assert recipient_allowed(hospital, "Someone@NYP.ORG")


def test_recipient_allowed_for_exact_demo_email(hospital):
    assert recipient_allowed(hospital, "countercharge.demo+billing@gmail.com")


def test_recipient_not_allowed_for_other_domain(hospital):
    assert not recipient_allowed(hospital, "billing@evil.example")


def test_recipient_not_allowed_for_lookalike_domain(hospital):
    assert not recipient_allowed(hospital, "billing@notnyp.org")
    assert not recipient_allowed(hospital, "billing@nyp.org.evil.example")
