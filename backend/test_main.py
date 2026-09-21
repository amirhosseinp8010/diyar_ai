import copy
import io

import pytest
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_property_store(tmp_path, monkeypatch):
    """Import/lead endpoints mutate + persist state — never touch the real files from tests."""
    snapshot = copy.deepcopy(main.PROPERTIES)
    leads_snapshot = copy.deepcopy(main.LEADS)
    monkeypatch.setattr(main, "DATA_PATH", tmp_path / "properties.json")
    monkeypatch.setattr(main, "LEADS_PATH", tmp_path / "leads.json")
    yield
    main.PROPERTIES[:] = snapshot
    main.PROPERTIES_BY_ID.clear()
    main.PROPERTIES_BY_ID.update({p["id"]: p for p in main.PROPERTIES})
    main.LEADS[:] = leads_snapshot


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_search_all():
    r = client.get("/v1/properties/search")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 18
    assert body[0]["price_aed"] <= body[1]["price_aed"]  # default sort: price_asc


def test_search_filters_area_and_price():
    r = client.get("/v1/properties/search", params={"area": "Marina", "min_price": 2_000_000})
    assert r.status_code == 200
    body = r.json()
    assert all("Marina" in p["area"] for p in body)
    assert all(p["price_aed"] >= 2_000_000 for p in body)


def test_property_not_found():
    r = client.get("/v1/properties/9999")
    assert r.status_code == 404


def test_valuation_forecast_shape():
    r = client.get("/v1/properties/1/valuation")
    assert r.status_code == 200
    body = r.json()
    assert len(body["forecast_3m_aed"]) == 3
    # real market data isn't guaranteed monotonic — just sanity-check the numbers are usable
    assert all(v > 0 for v in body["forecast_3m_aed"])
    assert 0 <= body["confidence_pct"] <= 100


def test_visa_advise_thresholds():
    below = client.post("/v1/visa/advise", json={"budget_aed": 400_000}).json()
    assert below["recommended"]["route"] == "golden_entrepreneur"

    mid = client.post("/v1/visa/advise", json={"budget_aed": 1_000_000}).json()
    assert mid["recommended"]["route"] == "property_2y"

    high = client.post("/v1/visa/advise", json={"budget_aed": 3_000_000}).json()
    assert high["recommended"]["route"] == "golden_property"
    assert len(high["matched_properties"]) == 3


def test_visa_rules_are_ordered_by_investment():
    r = client.get("/v1/visa/rules")
    rules = {rule["route"]: rule["min_investment_aed"] for rule in r.json()}
    assert rules["golden_entrepreneur"] < rules["property_2y"] < rules["golden_property"]


def test_lifestyle_match_school():
    # property 1 sits in Jumeirah Village Circle, which is in the school-proximity whitelist
    r = client.get("/v1/properties/1/lifestyle-match", params={"near_school": True})
    assert r.status_code == 200
    body = r.json()
    assert body["match_score"] > 60


def test_import_requires_admin_token():
    r = client.post("/v1/properties/import", json=[])
    assert r.status_code == 401


def test_import_json_append_and_replace():
    headers = {"X-Admin-Token": main.ADMIN_TOKEN}
    new_property = {
        "name": "ملک واقعی تست", "area": "Test Area", "type": "آپارتمان",
        "price_aed": 999000, "size_sqft": 800, "roi_pct": 6.0,
    }

    before = len(main.PROPERTIES)
    r = client.post("/v1/properties/import", json=[new_property], headers=headers)
    assert r.status_code == 200
    assert r.json()["total_properties"] == before + 1
    assert any(p["name"] == "ملک واقعی تست" for p in main.PROPERTIES)

    r = client.post("/v1/properties/import?mode=replace", json=[new_property], headers=headers)
    assert r.status_code == 200
    assert r.json()["total_properties"] == 1


def test_import_csv():
    headers = {"X-Admin-Token": main.ADMIN_TOKEN}
    csv_text = "name,area,type,price_aed,size_sqft,roi_pct\nملک CSV,JVC,استودیو,610000,420,7.9\n"
    files = {"file": ("listings.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")}
    before = len(main.PROPERTIES)
    r = client.post("/v1/properties/import/csv", files=files, headers=headers)
    assert r.status_code == 200
    assert r.json()["total_properties"] == before + 1
    assert any(p["name"] == "ملک CSV" for p in main.PROPERTIES)


def test_create_lead_without_property():
    r = client.post("/v1/leads", json={"name": "سارا احمدی", "contact": "sara@example.com", "message": "علاقه‌مندم"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "new"
    assert body["property_id"] is None
    assert body["property_name"] is None
    assert body["created_at"]


def test_create_lead_with_property_resolves_name():
    r = client.post("/v1/leads", json={"name": "رضا", "contact": "+971501234567", "property_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["property_id"] == 1
    assert body["property_name"] == main.PROPERTIES_BY_ID[1]["name"]


def test_create_lead_with_unknown_property_404():
    r = client.post("/v1/leads", json={"name": "رضا", "contact": "reza@example.com", "property_id": 9999})
    assert r.status_code == 404


def test_create_lead_rejects_blank_fields():
    r = client.post("/v1/leads", json={"name": "  ", "contact": "reza@example.com"})
    assert r.status_code == 422


def test_leads_listing_requires_admin_and_is_newest_first():
    client.post("/v1/leads", json={"name": "اول", "contact": "a@example.com"})
    client.post("/v1/leads", json={"name": "دوم", "contact": "b@example.com"})

    r = client.get("/v1/leads")
    assert r.status_code == 401

    r = client.get("/v1/leads", headers={"X-Admin-Token": main.ADMIN_TOKEN})
    assert r.status_code == 200
    names = [l["name"] for l in r.json()]
    assert names[:2] == ["دوم", "اول"]
