import os
import sys
import logging
from pathlib import Path

# Load .env for local development (no-op in GitHub Actions)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Allow imports from src/ when running as `python src/main.py`
sys.path.insert(0, str(Path(__file__).parent))

from utils import setup_logging, load_config, get_report_period, month_label
from hubspot_client import (
    get_product_pipeline_created,
    get_leads,
    get_mqls,
    get_6qa_accounts,
    get_mqls_from_6qa_accounts,
    get_meetings_from_6qa_accounts,
    get_saos_from_6qa_accounts,
    get_sals_from_mqls,
    get_meetings,
    get_email_metrics,
    get_linkedin_metrics,
    get_google_metrics,
)
from report_builder import build_report
from email_sender import send_report

setup_logging()
logger = logging.getLogger(__name__)


def collect_sheet1(start, end) -> dict:
    logger.info("=== Collecting Sheet 1: Lead/MQL Metrics ===")
    data = {}
    calls = [
        ("pipeline_created",    get_product_pipeline_created, (start, end)),
        ("leads",               get_leads,                    (start, end)),
        ("mqls",                get_mqls,                     (start, end)),
        ("six_qa_accounts",     get_6qa_accounts,             (start, end)),
        ("mqls_from_6qa",       get_mqls_from_6qa_accounts,  (start, end)),
        ("meetings_from_6qa",   get_meetings_from_6qa_accounts, (start, end)),
        ("saos_from_6qa",       get_saos_from_6qa_accounts,  (start, end)),
        ("sals_from_mqls",      get_sals_from_mqls,           (start, end)),
        ("meetings",            get_meetings,                  (start, end)),
    ]
    for key, fn, args in calls:
        try:
            data[key] = fn(*args)
        except Exception as exc:
            logger.error("FAILED %s: %s", key, exc, exc_info=True)
            data[key] = "API ERROR"
    return data


def main():
    logger.info("Starting BillingPlatform Weekly Report generation")

    config = load_config()
    year, month, start, end = get_report_period()
    label = month_label(year, month)
    logger.info("Report period: %s (%s → %s)", label, start, end)

    # Collect all data — each section is isolated so one failure doesn't block others
    sheet1_data = collect_sheet1(start, end)

    try:
        sheet2_data = get_email_metrics(start, end)
    except Exception as exc:
        logger.error("Email metrics failed: %s", exc, exc_info=True)
        sheet2_data = {"error": str(exc)}

    try:
        sheet3_data = get_linkedin_metrics(start, end)
    except Exception as exc:
        logger.error("LinkedIn metrics failed: %s", exc, exc_info=True)
        sheet3_data = {"error": str(exc)}

    try:
        sheet4_data = get_google_metrics(start, end)
    except Exception as exc:
        logger.error("Google metrics failed: %s", exc, exc_info=True)
        sheet4_data = {"error": str(exc)}

    # Build output path in project root
    prefix = config["report"]["filename_prefix"]
    filename = f"{prefix}_{year}_{month:02d}.xlsx"
    output_path = str(Path(__file__).parent.parent / filename)

    build_report(
        sheet1_data=sheet1_data,
        sheet2_data=sheet2_data,
        sheet3_data=sheet3_data,
        sheet4_data=sheet4_data,
        config=config,
        year=year,
        month=month,
        output_path=output_path,
    )

    # Email delivery
    recipients_env = os.environ.get("REPORT_RECIPIENTS", "")
    recipients = [r.strip() for r in recipients_env.split(",") if r.strip()]
    if not recipients:
        recipients = config["email"].get("recipients", [])

    subject_tpl = config["email"]["subject"]
    body_tpl = config["email"]["body"]
    subject = subject_tpl.format(month=label.split()[0], year=year)
    body = body_tpl.format(month=label.split()[0], year=year)

    try:
        send_report(output_path, subject, body, recipients)
    except Exception as exc:
        logger.error("Email send failed: %s", exc, exc_info=True)
        logger.info("Report file still available at: %s", output_path)

    logger.info("Done. Output: %s", output_path)


if __name__ == "__main__":
    main()
