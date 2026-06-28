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
    """Return (year, month, start_date, end_date) for the prior full calendar month."""
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - relativedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    return (
        last_month_end.year,
        last_month_end.month,
        last_month_start,
        last_month_end,
    )


def month_label(year: int, month: int) -> str:
    return datetime(year, month, 1).strftime("%b %Y")


def to_epoch_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp() * 1000)


def end_of_day_epoch_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 23, 59, 59).timestamp() * 1000)


def pct(numerator, denominator) -> str:
    if denominator and denominator != 0:
        return f"{(numerator / denominator) * 100:.1f}%"
    return "N/A"
