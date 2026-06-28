"""
HubSpot data client — pulls all metrics from dashboard 16933888 on Hub 20300238.

Flow per run:
  1. GET /dashboard/v2/dashboards/16933888  — discover widget report IDs + names
     Fallback: GET /reporting/v2/dashboards/16933888
  2. GET /analytics/v2/reports/{id}/data   — fetch each widget's data for the period
  3. Match report names → metric keys via METRIC_PATTERNS (case-insensitive substring)
  4. All public functions draw from a module-level cache (_metric_values, _email_cache)
     so the dashboard is queried exactly once per process.

If any endpoint returns 4xx/5xx the error is logged and the metric stays None,
which the report builder renders as an amber manual-entry cell.
"""
import json
import os
import time
import logging
from datetime import date
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"
DASHBOARD_ID = "16933888"
RATE_LIMIT_DELAY = 0.15


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}",
        "Content-Type": "application/json",
    }


def _get(url: str, params: dict | None = None) -> dict:
    time.sleep(RATE_LIMIT_DELAY)
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ── Metric ↔ report-name patterns ───────────────────────────────────────────────
# Each value is a list of substrings matched case-insensitively against the widget
# report name returned by the dashboard API. First match wins per metric key.
METRIC_PATTERNS: dict[str, list[str]] = {
    "pipeline_created":   ["pipeline", "deals created", "deal amount", "revenue"],
    "leads":              ["lead", "marketing qualified"],
    "mqls":               ["fast track", "ft mql", "mql fast", "mql"],
    "six_qa_accounts":    ["6qa", "6sense qa", "account qa"],
    "sals_from_mqls":     ["sal", "sales accepted"],
    "meetings":           ["meeting", "handover", "booked"],
    # Email sub-metrics (mapped into _email_cache, not _metric_values)
    "emails_sent":        ["email sent", "emails sent", "total sent"],
    "delivery_rate":      ["delivery rate"],
    "open_rate":          ["open rate"],
    "click_through_rate": ["click through", "ctr"],
}

# ── In-process cache — populated once per run ────────────────────────────────────
_cache_loaded: bool = False
_metric_values: dict[str, Any] = {}  # scalar metrics for Sheet 1
_email_cache: dict[str, Any] = {}    # email metrics dict for Sheet 2


# ── Value extraction ─────────────────────────────────────────────────────────────

def _extract_numeric(data: dict) -> float | None:
    """
    Walk the most common HubSpot Reporting API response shapes and return
    the first numeric value found. Logs the full data dict so we can extend
    this if a new shape appears.
    """
    if not data:
        return None

    # Shape 1: top-level scalar
    for key in ("value", "total", "count"):
        if key in data:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                pass

    # Shape 2: nested under "data"
    inner = data.get("data")
    if isinstance(inner, (int, float)):
        return float(inner)
    if isinstance(inner, dict):
        for key in ("value", "total", "count"):
            if key in inner:
                try:
                    return float(inner[key])
                except (TypeError, ValueError):
                    pass
        # Shape 3: first element of aggregations/series/rows
        for list_key in ("aggregations", "series", "rows", "breakdown", "buckets"):
            items = inner.get(list_key)
            if items and isinstance(items, list):
                first = items[0]
                if isinstance(first, dict):
                    for k in ("value", "total", "y", "count", "sum"):
                        if k in first:
                            try:
                                return float(first[k])
                            except (TypeError, ValueError):
                                pass
    return None


# ── Dashboard discovery ──────────────────────────────────────────────────────────

def _discover_dashboard() -> list[dict]:
    """
    Try primary then fallback dashboard endpoint.
    Returns the list of widget dicts, or [] if both fail.
    Logs the full raw response from whichever endpoint succeeds.
    """
    endpoints = [
        f"{BASE_URL}/dashboard/v2/dashboards/{DASHBOARD_ID}",
        f"{BASE_URL}/reporting/v2/dashboards/{DASHBOARD_ID}",
    ]
    for url in endpoints:
        try:
            resp = _get(url)
            logger.info("Dashboard response from %s:\n%s", url,
                        json.dumps(resp, indent=2)[:5000])

            # Try every known shape for the widget list
            widgets = (
                resp.get("widgets")
                or resp.get("reports")
                or (resp.get("data") or {}).get("widgets")
                or (resp.get("data") or {}).get("reports")
            )
            if widgets and isinstance(widgets, list):
                logger.info("Found %d widget(s) on dashboard %s", len(widgets), DASHBOARD_ID)
                return widgets
            if isinstance(resp, list):
                logger.info("Response is a bare list with %d items", len(resp))
                return resp
            logger.warning("No widget list in response from %s — full keys: %s",
                           url, list(resp.keys()))
        except requests.HTTPError as exc:
            logger.warning("%s → HTTP %s — trying fallback", url, exc.response.status_code)
            logger.warning("Response body: %s", exc.response.text[:1000])
        except Exception as exc:
            logger.warning("%s failed: %s — trying fallback", url, exc)

    logger.error("All dashboard endpoints failed for dashboard %s", DASHBOARD_ID)
    return []


# ── Per-report data fetch ────────────────────────────────────────────────────────

def _fetch_report(report_id: str, start: date, end: date) -> dict:
    url = f"{BASE_URL}/analytics/v2/reports/{report_id}/data"
    params = {
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
    }
    try:
        resp = _get(url, params)
        logger.info("Report %-12s | %s → %s | response:\n%s",
                    report_id, start, end, json.dumps(resp, indent=2)[:2000])
        return resp
    except requests.HTTPError as exc:
        logger.warning("Report %s → HTTP %s: %s",
                       report_id, exc.response.status_code, exc.response.text[:500])
    except Exception as exc:
        logger.warning("Report %s fetch failed: %s", report_id, exc)
    return {}


# ── Cache loader (called once per process) ───────────────────────────────────────

def _load_all(start: date, end: date) -> None:
    global _cache_loaded, _metric_values, _email_cache
    if _cache_loaded:
        return

    logger.info("=== Loading dashboard %s  period: %s → %s ===", DASHBOARD_ID, start, end)
    widgets = _discover_dashboard()

    if not widgets:
        logger.error("No widgets discovered — all metrics will be None (amber in report)")
        _cache_loaded = True
        return

    raw: dict[str, Any] = {}  # metric_key → first matched value

    for w in widgets:
        report_id = str(w.get("reportId") or w.get("id") or "").strip()
        name = (w.get("reportName") or w.get("name") or w.get("title") or "").strip()
        rtype = str(w.get("reportType") or w.get("type") or "")
        logger.info("Widget → id=%-12s  name=%r  type=%s", report_id, name, rtype)

        if not report_id:
            logger.warning("  Skipping widget with no reportId — raw widget: %s",
                           json.dumps(w)[:400])
            continue

        report_data = _fetch_report(report_id, start, end)
        value = _extract_numeric(report_data)
        logger.info("  Extracted scalar: %s", value)

        name_lower = name.lower()
        matched = False
        for metric_key, patterns in METRIC_PATTERNS.items():
            if metric_key not in raw and any(p in name_lower for p in patterns):
                raw[metric_key] = value
                logger.info("  ✓ Matched → metric %r = %s", metric_key, value)
                matched = True
                break
        if not matched:
            logger.info("  — No metric pattern matched for %r", name)

    # ── Scalar metrics (Sheet 1) ─────────────────────────────────────────────────
    _metric_values = {k: raw.get(k) for k in (
        "pipeline_created", "leads", "mqls",
        "six_qa_accounts", "sals_from_mqls", "meetings",
    )}

    # ── Email metrics (Sheet 2) ──────────────────────────────────────────────────
    email_raw = {k: raw.get(k) for k in (
        "emails_sent", "delivery_rate", "open_rate", "click_through_rate"
    )}
    # Rates come back as 0–1 floats; format as percentages
    for key in ("delivery_rate", "open_rate", "click_through_rate"):
        v = email_raw.get(key)
        if isinstance(v, float):
            email_raw[key] = f"{v * 100:.1f}%" if v <= 1.0 else f"{v:.1f}%"

    if not any(v is not None for v in email_raw.values()):
        _email_cache = {"manual_fallback": True}
        logger.warning("No email metrics matched any widget — email sheet will be amber")
    else:
        _email_cache = email_raw

    logger.info("=== Dashboard load complete ===")
    logger.info("Scalar metrics: %s", _metric_values)
    logger.info("Email cache:    %s", _email_cache)
    _cache_loaded = True


# ── Public API — same signatures as the previous CRM-based client ────────────────

def get_product_pipeline_created(start: date, end: date) -> float:
    _load_all(start, end)
    v = _metric_values.get("pipeline_created")
    return float(v) if v is not None else 0.0


def get_leads(start: date, end: date) -> int:
    _load_all(start, end)
    v = _metric_values.get("leads")
    return int(v) if v is not None else 0


def get_mqls(start: date, end: date) -> int:
    _load_all(start, end)
    v = _metric_values.get("mqls")
    return int(v) if v is not None else 0


def get_6qa_accounts(start: date, end: date) -> int:
    _load_all(start, end)
    v = _metric_values.get("six_qa_accounts")
    return int(v) if v is not None else 0


def get_sals_from_mqls(start: date, end: date) -> int:
    _load_all(start, end)
    v = _metric_values.get("sals_from_mqls")
    return int(v) if v is not None else 0


def get_meetings(start: date, end: date) -> int:
    _load_all(start, end)
    v = _metric_values.get("meetings")
    return int(v) if v is not None else 0


def get_email_metrics(start: date, end: date) -> dict[str, Any]:
    _load_all(start, end)
    return _email_cache if _email_cache else {"manual_fallback": True}
