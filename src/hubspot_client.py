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
ASSOC_BATCH_SIZE = 100   # v4 batch associations endpoint limit
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


def _get_6qa_company_ids(start: date, end: date) -> set[str]:
    payload = {
        "filterGroups": [{"filters": _date_range_filter("n6sense_account_6qa_start_date", start, end)}],
        "properties": ["hs_object_id"],
    }
    return {c["id"] for c in _paginate_companies(payload)}


def _get_ever_6qa_company_ids() -> set[str]:
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "has_ever_been_6qa", "operator": "EQ", "value": "Yes"}]}],
        "properties": ["hs_object_id"],
    }
    return {c["id"] for c in _paginate_companies(payload)}


def _batch_company_ids_for(from_type: str, object_ids: list[str]) -> dict[str, set[str]]:
    """
    Given a list of object IDs, return a map of object_id -> set of associated company IDs.
    Uses the v4 batch associations endpoint: ceil(N/100) calls instead of N calls.
    """
    url = f"{BASE_URL}/crm/v4/associations/{from_type}/companies/batch/read"
    mapping: dict[str, set[str]] = {}
    for i in range(0, len(object_ids), ASSOC_BATCH_SIZE):
        chunk = object_ids[i : i + ASSOC_BATCH_SIZE]
        try:
            data = _post(url, {"inputs": [{"id": oid} for oid in chunk]})
            for item in data.get("results", []):
                from_id = str(item["from"]["id"])
                mapping[from_id] = {str(r["toObjectId"]) for r in item.get("to", [])}
        except Exception as exc:
            logger.warning("Batch association fetch failed (chunk %d): %s", i, exc)
    return mapping


def _filter_by_company(object_ids: list[str], from_type: str, allowed_company_ids: set[str]) -> int:
    """
    Bulk-resolve company associations for object_ids, then count how many
    have at least one associated company in allowed_company_ids.
    """
    if not object_ids or not allowed_company_ids:
        return 0
    mapping = _batch_company_ids_for(from_type, object_ids)
    return sum(1 for oid in object_ids if mapping.get(oid, set()) & allowed_company_ids)


def get_mqls_from_6qa_accounts(start: date, end: date) -> int:
    logger.info("Fetching MQLs from 6QA accounts")
    ever_6qa = _get_ever_6qa_company_ids()
    if not ever_6qa:
        return 0

    # Bulk fetch all MQL contacts for the month — same query as get_leads()
    filter_groups = [
        {"filters": _date_range_filter("lead_mql_date", start, end) + _not_rejected_filter()},
        {"filters": _date_range_filter("mql_date_stamp", start, end) + _not_rejected_filter()},
    ]
    contacts = _paginate_contacts({
        "filterGroups": filter_groups,
        "properties": ["lead_mql_date", "mql_date_stamp", "hs_lead_status"],
    })
    contact_ids = list({c["id"] for c in contacts})
    logger.info("MQL contacts this month: %d — resolving company associations in bulk", len(contact_ids))

    count = _filter_by_company(contact_ids, "contacts", ever_6qa)
    logger.info("MQLs from 6QA accounts: %d", count)
    return count


def get_meetings_from_6qa_accounts(start: date, end: date) -> int:
    logger.info("Fetching meetings from 6QA accounts")
    ever_6qa = _get_ever_6qa_company_ids()
    if not ever_6qa:
        return 0

    contacts = _paginate_contacts({
        "filterGroups": [{"filters": _date_range_filter("latest_meeting_handover", start, end)}],
        "properties": ["latest_meeting_handover"],
    })
    contact_ids = list({c["id"] for c in contacts})
    logger.info("Meeting contacts this month: %d — resolving company associations in bulk", len(contact_ids))

    count = _filter_by_company(contact_ids, "contacts", ever_6qa)
    logger.info("Meetings from 6QA accounts: %d", count)
    return count


def get_saos_from_6qa_accounts(start: date, end: date) -> int:
    logger.info("Fetching SAOs from 6QA accounts")
    month_6qa = _get_6qa_company_ids(start, end)
    if not month_6qa:
        return 0

    # Bulk fetch all SAO-qualified deals for the month, then filter by company in-memory
    deals = _paginate_deals({
        "filterGroups": [{"filters": _date_range_filter("date_sal_qualification__c", start, end)}],
        "properties": ["date_sal_qualification__c"],
    })
    deal_ids = list({d["id"] for d in deals})
    logger.info("SAO deals this month: %d — resolving company associations in bulk", len(deal_ids))

    count = _filter_by_company(deal_ids, "deals", month_6qa)
    logger.info("SAOs from 6QA accounts: %d", count)
    return count


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
        "limit": 100,
    }
    aggregated = {
        "numSent": 0,
        "delivered": 0,
        "open": 0,
        "click": 0,
        "total_emails": 0,
    }
    after = None
    while True:
        if after:
            params["after"] = after
        try:
            data = _get(url, params)
        except Exception as exc:
            logger.error("Email metrics API error: %s", exc)
            return {"error": str(exc)}

        for item in data.get("results", []):
            stats = item.get("statistics", {})
            aggregated["numSent"] += stats.get("numSent", 0) or 0
            aggregated["delivered"] += stats.get("numDelivered", 0) or 0
            aggregated["open"] += stats.get("numOpened", 0) or 0
            aggregated["click"] += stats.get("numClicked", 0) or 0
            aggregated["total_emails"] += 1

        paging = data.get("paging", {})
        after = paging.get("next", {}).get("after")
        if not after:
            break

    sent = aggregated["numSent"]
    delivered = aggregated["delivered"]
    opens = aggregated["open"]
    clicks = aggregated["click"]

    delivery_rate = f"{(delivered / sent * 100):.1f}%" if sent else "N/A"
    open_rate = f"{(opens / delivered * 100):.1f}%" if delivered else "N/A"
    ctr = f"{(clicks / delivered * 100):.1f}%" if delivered else "N/A"

    logger.info("Email metrics: sent=%d, delivered=%d, opens=%d, clicks=%d", sent, delivered, opens, clicks)
    return {
        "emails_sent": sent,
        "delivery_rate": delivery_rate,
        "open_rate": open_rate,
        "click_through_rate": ctr,
    }


# ── Ads Helpers ─────────────────────────────────────────────────────────────────

def _get_ad_accounts() -> list[dict]:
    data = _get(f"{BASE_URL}/ads/v3/accounts", {"limit": 100})
    return data.get("results", [])


def _get_campaigns_for_account(account_id: str, channel: str) -> list[dict]:
    params = {"accountId": account_id, "limit": 100}
    data = _get(f"{BASE_URL}/ads/v3/campaigns", params)
    results = data.get("results", [])
    return [c for c in results if channel.lower() in c.get("type", "").lower()]


def _get_campaign_stats(campaign_id: str, account_id: str, start: date, end: date) -> dict:
    params = {
        "campaignId": campaign_id,
        "accountId": account_id,
        "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d"),
    }
    try:
        data = _get(f"{BASE_URL}/ads/v3/statistics/campaign", params)
        return data.get("statistics", {})
    except Exception as exc:
        logger.warning("Campaign stats failed for %s: %s", campaign_id, exc)
        return {}


def _aggregate_ads(accounts: list[dict], channel: str, campaign_type_hint: str, start: date, end: date) -> dict:
    totals = {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0, "cpm_sum": 0.0, "ctr_sum": 0.0, "n": 0}

    for account in accounts:
        acct_id = account.get("id") or account.get("accountId")
        if not acct_id:
            continue
        campaigns = _get_campaigns_for_account(str(acct_id), channel)
        for camp in campaigns:
            if campaign_type_hint and campaign_type_hint.lower() not in camp.get("name", "").lower():
                continue
            camp_id = camp.get("id")
            stats = _get_campaign_stats(str(camp_id), str(acct_id), start, end)
            totals["spend"] += float(stats.get("spend", 0) or 0)
            totals["impressions"] += int(stats.get("impressions", 0) or 0)
            totals["clicks"] += int(stats.get("clicks", 0) or 0)
            totals["conversions"] += int(stats.get("conversions", 0) or 0)
            cpm = stats.get("cpm") or 0
            ctr = stats.get("ctr") or 0
            if cpm or ctr:
                totals["cpm_sum"] += float(cpm)
                totals["ctr_sum"] += float(ctr)
                totals["n"] += 1

    n = totals["n"] or 1
    return {
        "spend": round(totals["spend"], 2),
        "impressions": totals["impressions"],
        "clicks": totals["clicks"],
        "conversions": totals["conversions"],
        "avg_cpm": round(totals["cpm_sum"] / n, 2),
        "avg_ctr": f"{(totals['ctr_sum'] / n * 100):.2f}%",
    }


# ── Sheet 3: LinkedIn Metrics ───────────────────────────────────────────────────

def get_linkedin_metrics(start: date, end: date) -> dict[str, Any]:
    logger.info("Fetching LinkedIn ad metrics")
    try:
        accounts = _get_ad_accounts()
        brand = _aggregate_ads(accounts, "LINKEDIN", "brand", start, end)
        abm = _aggregate_ads(accounts, "LINKEDIN", "abm", start, end)

        brand_spend = brand["spend"]
        brand_mqls = brand["conversions"]
        abm_spend = abm["spend"]
        abm_mqls = abm["conversions"]
        total_spend = brand_spend + abm_spend
        total_mqls = brand_mqls + abm_mqls

        cpl = round(brand_spend / brand_mqls, 2) if brand_mqls else "N/A"
        total_cpl = round(total_spend / total_mqls, 2) if total_mqls else "N/A"

        return {
            "brand_spend": brand_spend,
            "brand_mqls": brand_mqls,
            "avg_ctr": brand["avg_ctr"],
            "clicks": brand["clicks"],
            "cpl": cpl,
            "impressions": brand["impressions"],
            "avg_cpm": brand["avg_cpm"],
            "abm_spend": abm_spend,
            "abm_mqls": abm_mqls,
            "total_paid_social_spend": total_spend,
            "total_cpl": total_cpl,
        }
    except Exception as exc:
        logger.error("LinkedIn metrics error: %s", exc)
        return {"error": str(exc)}


# ── Sheet 4: Google Metrics ─────────────────────────────────────────────────────

def get_google_metrics(start: date, end: date) -> dict[str, Any]:
    logger.info("Fetching Google ad metrics")
    try:
        accounts = _get_ad_accounts()
        google = _aggregate_ads(accounts, "GOOGLE", "", start, end)

        total_cost = google["spend"]
        total_mqls = google["conversions"]
        cpl = round(total_cost / total_mqls, 2) if total_mqls else "N/A"

        return {
            "total_cost": total_cost,
            "total_mqls": total_mqls,
            "cost_per_lead": cpl,
            "conversion_rate": google["avg_ctr"],
            "clicks": google["clicks"],
            "ctr": google["avg_ctr"],
            "avg_cpc": round(total_cost / google["clicks"], 2) if google["clicks"] else "N/A",
            "impressions": google["impressions"],
            "total_cpl": cpl,
        }
    except Exception as exc:
        logger.error("Google metrics error: %s", exc)
        return {"error": str(exc)}
