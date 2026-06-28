import os
import yaml
import logging
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_report_period() -> tuple[int, int, date, date]:
    """
    Priority order:
    1. REPORT_MONTH_OVERRIDE=YYYY-MM  → that full calendar month
    2. USE_PRIOR_MONTH=true            → full prior calendar month (set by cron)
    3. default                         → current month, day 1 through today
    """
    override = os.environ.get("REPORT_MONTH_OVERRIDE", "").strip()
    if override:
        try:
            year, month = int(override[:4]), int(override[5:7])
            start = date(year, month, 1)
            end = (start + relativedelta(months=1)) - relativedelta(days=1)
            return year, month, start, end
        except (ValueError, IndexError):
            logger.warning("Invalid REPORT_MONTH_OVERRIDE %r — falling back to default", override)

    today = date.today()
    use_prior = os.environ.get("USE_PRIOR_MONTH", "").lower() in ("true", "1", "yes")

    if use_prior:
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - relativedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_end.year, last_month_end.month, last_month_start, last_month_end

    # Default: current month to date
    start = today.replace(day=1)
    return today.year, today.month, start, today


def month_label(year: int, month: int) -> str:
    return datetime(year, month, 1).strftime("%b %Y")


def period_label(start: date, end: date) -> str:
    """Human-readable column header for the report date range.

    Full calendar month  → "Jun 2026"
    Current month to date → "Jun 1–28, 2026"
    Cross-month range    → "May 15 – Jun 28, 2026"
    """
    if start.year == end.year and start.month == end.month:
        last_of_month = (start.replace(day=1) + relativedelta(months=1)) - relativedelta(days=1)
        if start.day == 1 and end == last_of_month:
            return start.strftime("%b %Y")
        return f"{start.strftime('%b')} {start.day}–{end.day}, {start.year}"
    return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"


def to_epoch_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp() * 1000)


def end_of_day_epoch_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 23, 59, 59).timestamp() * 1000)


def pct(numerator, denominator) -> str:
    if denominator and denominator != 0:
        return f"{(numerator / denominator) * 100:.1f}%"
    return "N/A"
