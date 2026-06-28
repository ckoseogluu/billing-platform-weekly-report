import os
import time
import logging
from datetime import date
from typing import Any

import requests

from utils import to_epoch_ms, end_of_day_epoch_ms

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"
RATE_LIMIT_DELAY = 0.15  # seconds between API calls
SEARCH_PAGE_SIZE = 200   # CRM search max page size


def _headers() -> dict:
    token = os.environ["HUBSPOT_API_KEY"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get(url: str, params: dict | None = None) -> dict:
    time.sleep(RATE_LIMIT_DELAY)
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _post(url: str, payload: dict) -> dict:
    time.sleep(RATE_LIMIT_DELAY)
    resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _paginate_search(object_type: str, payload: dict) -> list[dict]:
    url = f"{BASE_URL}/crm/v3/objects/{object_type}/search"
    payload.setdefault("limit", SEARCH_PAGE_SIZE)
    results = []
    after = None
    while True:
        if after:
            payload["after"] = after
        data = _post(url, payload)
        results.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return results


def _paginate_contacts(payload: dict) -> list[dict]:
    return _paginate_search("contacts", payload)


def _paginate_deals(payload: dict) -> list[dict]:
    return _paginate_search("deals", payload)


def _paginate_companies(payload: dict) -> list[dict]:
    return _paginate_search("companies", payload)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _date_range_filter(prop: str, start: date, end: date) -> list[dict]:
    return [
        {"propertyName": prop, "operator": "GTE", "value": str(to_epoch_ms(start))},
        {"propertyName": prop, "operator": "LTE", "value": str(end_of_day_epoch_ms(end))},
    ]


REJECTED_STATUSES = ["REJECTED", "DISQUALIFIED"]

def _not_rejected_filter() -> list[dict]:
    return [
        {"propertyName": "hs_lead_status", "operator": "NOT_IN", "values": REJECTED_STATUSES}
    ]


# ── Sheet 1: Lead / MQL Metrics ────────────────────────────────────────────────

def get_product_pipeline_created(start: date, end: date) -> float:
    logger.info("Fetching product pipeline created")
    payload = {
        "filterGroups": [{
            "filters": _date_range_filter("createdate", start, end)
        }],
        "properties": ["amount", "createdate"],
        "limit": 100,
    }
    deals = _paginate_deals(payload)
    total = sum(
        float(d["properties"].get("amount") or 0)
        for d in deals
    )
    logger.info("Pipeline created: $%.2f from %d deals", total, len(deals))
    return total


def get_leads(start: date, end: date) -> int:
    logger.info("Fetching leads (MQL)")
    filter_groups = [
        {"filters": _date_range_filter("lead_mql_date", start, end) + _not_rejected_filter()},
        {"filters": _date_range_filter("mql_date_stamp", start, end) + _not_rejected_filter()},
    ]
    payload = {
        "filterGroups": filter_groups,
        "properties": ["lead_mql_date", "mql_date_stamp", "hs_lead_status"],
        "limit": 100,
    }
    contacts = _paginate_contacts(payload)
    seen = {c["id"] for c in contacts}
    logger.info("Leads count: %d", len(seen))
    return len(seen)


def get_mqls(start: date, end: date) -> int:
    logger.info("Fetching MQLs (fast-track)")
    filter_groups = [
        {"filters": _date_range_filter("lead_ft_mql_date", start, end) + _not_rejected_filter()},
        {"filters": _date_range_filter("ft_mql_date_stamp", start, end) + _not_rejected_filter()},
        {"filters": _date_range_filter("date_mql_fast_track__c", start, end) + _not_rejected_filter()},
    ]
    payload = {
        "filterGroups": filter_groups,
        "properties": ["lead_ft_mql_date", "ft_mql_date_stamp", "date_mql_fast_track__c", "hs_lead_status"],
        "limit": 100,
    }
    contacts = _paginate_contacts(payload)
    seen = {c["id"] for c in contacts}
    logger.info("MQLs count: %d", len(seen))
    return len(seen)


def get_6qa_accounts(start: date, end: date) -> int:
    logger.info("Fetching 6QA accounts")
    payload = {
        "filterGroups": [{
            "filters": _date_range_filter("n6sense_account_6qa_start_date", start, end)
        }],
        "properties": ["n6sense_account_6qa_start_date"],
        "limit": 100,
    }
    companies = _paginate_companies(payload)
    logger.info("6QA accounts: %d", len(companies))
    return len(companies)



def get_sals_from_mqls(start: date, end: date) -> int:
    logger.info("Fetching SALs from MQLs")
    filter_groups = [
        {"filters": _date_range_filter("date_sal_engaged__c", start, end) + [
            {"propertyName": "lead_mql_date", "operator": "HAS_PROPERTY"}
        ]},
        {"filters": _date_range_filter("date_sal_engaged__c", start, end) + [
            {"propertyName": "lead_ft_mql_date", "operator": "HAS_PROPERTY"}
        ]},
        {"filters": _date_range_filter("date_sal_engaged__c", start, end) + [
            {"propertyName": "ft_mql_date_stamp", "operator": "HAS_PROPERTY"}
        ]},
    ]
    payload = {
        "filterGroups": filter_groups,
        "properties": ["date_sal_engaged__c", "lead_mql_date"],
        "limit": 100,
    }
    contacts = _paginate_contacts(payload)
    seen = {c["id"] for c in contacts}
    logger.info("SALs from MQLs: %d", len(seen))
    return len(seen)


def get_meetings(start: date, end: date) -> int:
    logger.info("Fetching meetings")
    payload = {
        "filterGroups": [{
            "filters": _date_range_filter("latest_meeting_handover", start, end)
        }],
        "properties": ["latest_meeting_handover"],
        "limit": 100,
    }
    contacts = _paginate_contacts(payload)
    logger.info("Meetings: %d", len(contacts))
    return len(contacts)


# ── Sheet 2: Email Metrics ──────────────────────────────────────────────────────

def get_email_metrics(start: date, end: date) -> dict[str, Any]:
    logger.info("Fetching email metrics")
    url = f"{BASE_URL}/marketing/v3/emails/statistics/list"
    params = {
        "startTimestamp": to_epoch_ms(start),
        "endTimestamp": end_of_day_epoch_ms(end),
        "limit": 50,
    }
    total_sent = 0
    delivery_sum = 0.0
    open_sum = 0.0
    ctr_sum = 0.0
    weighted_n = 0

    after = None
    while True:
        if after:
            params["after"] = after
        try:
            data = _get(url, params)
        except Exception as exc:
            logger.error("Email metrics API error: %s — falling back to manual entry", exc)
            return {"manual_fallback": True}

        for item in data.get("results", []):
            s = item.get("statistics", {})
            # HubSpot returns numSent at the top level or inside statistics
            sent = int(s.get("numSent", s.get("sent", 0)) or 0)
            total_sent += sent
            # Rates are 0–1 floats; weight by sent count for a meaningful aggregate
            dr = s.get("deliveryRate")
            if dr is not None and sent > 0:
                delivery_sum += float(dr) * sent
                open_sum += float(s.get("openRate", 0) or 0) * sent
                ctr_sum += float(s.get("clickThroughRate", 0) or 0) * sent
                weighted_n += sent

        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    if total_sent == 0:
        logger.info("Email metrics: no emails found for period")
        return {"emails_sent": 0, "delivery_rate": "N/A", "open_rate": "N/A", "click_through_rate": "N/A"}

    n = weighted_n or 1
    delivery_rate = f"{(delivery_sum / n * 100):.1f}%"
    open_rate = f"{(open_sum / n * 100):.1f}%"
    ctr = f"{(ctr_sum / n * 100):.1f}%"

    logger.info("Email metrics: sent=%d, delivery=%s, open=%s, ctr=%s", total_sent, delivery_rate, open_rate, ctr)
    return {
        "emails_sent": total_sent,
        "delivery_rate": delivery_rate,
        "open_rate": open_rate,
        "click_through_rate": ctr,
    }
