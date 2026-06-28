import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

logger = logging.getLogger(__name__)


def send_report(attachment_path: str, subject: str, body: str, recipients: list[str]):
    sender = os.environ["GMAIL_SENDER"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]

    if not recipients:
        logger.warning("No recipients configured — skipping email send")
        return

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    attachment_file = Path(attachment_path)
    with open(attachment_file, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        f'attachment; filename="{attachment_file.name}"',
    )
    msg.attach(part)

    logger.info("Connecting to Gmail SMTP for %d recipient(s)", len(recipients))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, recipients, msg.as_string())

    logger.info("Email sent successfully to: %s", ", ".join(recipients))
