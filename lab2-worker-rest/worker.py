import os
import time
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from dotenv import load_dotenv
from mzinga_client import MzingaClient

load_dotenv()

MZINGA_URL = os.environ["MZINGA_URL"]
MZINGA_EMAIL = os.environ["MZINGA_EMAIL"]
MZINGA_PASSWORD = os.environ["MZINGA_PASSWORD"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 5))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))
EMAIL_FROM = os.getenv("EMAIL_FROM", "worker@mzinga.io")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slate AST -> HTML serialiser
# ---------------------------------------------------------------------------

def serialize_leaf(leaf: dict) -> str:
    """Convert a Slate leaf node (text node) to an HTML string."""
    text = leaf.get("text", "")
    if not text:
        return ""
    text = text.replace("\n", "<br>")
    if leaf.get("bold"):
        text = f"<strong>{text}</strong>"
    if leaf.get("italic"):
        text = f"<em>{text}</em>"
    if leaf.get("underline"):
        text = f"<u>{text}</u>"
    if leaf.get("strikethrough"):
        text = f"<s>{text}</s>"
    if leaf.get("code"):
        text = f"<code>{text}</code>"
    return text


def serialize_node(node: dict) -> str:
    """Recursively convert a Slate AST node to HTML."""
    # Leaf text node
    if "text" in node:
        return serialize_leaf(node)

    children_html = "".join(serialize_node(child) for child in node.get("children", []))

    node_type = node.get("type", "")
    if node_type in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return f"<{node_type}>{children_html}</{node_type}>"
    if node_type in ("paragraph", ""):
        # Payload omits "type" on plain paragraphs; wrap in <p> to preserve newlines
        return f"<p>{children_html}</p>"
    if node_type == "ul":
        return f"<ul>{children_html}</ul>"
    if node_type == "ol":
        return f"<ol>{children_html}</ol>"
    if node_type == "li":
        return f"<li>{children_html}</li>"
    if node_type == "link":
        url = node.get("url", "#")
        return f'<a href="{url}">{children_html}</a>'

    return children_html


def serialize_body(body: list) -> str:
    """Serialize an entire Slate AST (list of top-level nodes) to HTML."""
    return "".join(serialize_node(node) for node in body)


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

def resolve_emails(field) -> list[str]:
    """Resolve a tos/ccs/bccs relationship array to a list of email addresses."""
    if not field:
        return []

    emails = []
    for user in field:
        if user.get("value"):
            value = user["value"]
            if value.get("email"):
                emails.append(value["email"])

    return emails


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(to_addrs: list[str], cc_addrs: list[str], bcc_addrs: list[str],
               subject: str, html_body: str):
    """Send an HTML email via SMTP."""
    all_recipients = to_addrs + cc_addrs + bcc_addrs
    if not all_recipients:
        raise ValueError("No recipients to send to")

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = subject or "(no subject)"

    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.sendmail(EMAIL_FROM, all_recipients, msg.as_string())

    logger.info("Email sent to %s", all_recipients)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def process_document(client: MzingaClient, doc: dict):
    """Claim, process, and finalise a single communications document."""
    doc_id = doc["id"]
    try:
        client.update_status(doc_id, "processing")
        logger.info("Processing document %s", doc_id)

        # Resolve recipients
        to_addrs = resolve_emails(doc.get("tos"))
        cc_addrs = resolve_emails(doc.get("ccs"))
        bcc_addrs = resolve_emails(doc.get("bccs"))

        # Serialize body to HTML
        body = doc.get("body", [])
        html_body = serialize_body(body) if body else ""

        subject = doc.get("subject", "")

        # Send email
        send_email(to_addrs, cc_addrs, bcc_addrs, subject, html_body)

        # Mark as sent
        client.update_status(doc_id, "sent")
        logger.info("Document %s marked as sent", doc_id)

    except (requests.HTTPError, smtplib.SMTPException, ValueError) as e:
        logger.error("Failed to process document %s: %s", doc_id, e)
        client.update_status(doc_id, "failed")


def main():
    client = MzingaClient(MZINGA_URL, MZINGA_EMAIL, MZINGA_PASSWORD)
    client.login()
    logger.info("Connected to Mzinga API — polling every %ds", POLL_INTERVAL)

    while True:
        try:
            docs = client.fetch_docs_pending()
            for doc in docs:
                process_document(client, doc)
            if not docs:
                time.sleep(POLL_INTERVAL)
        except requests.HTTPError as e:
            logger.error("HTTP error: %s", e)
            time.sleep(POLL_INTERVAL)
            


if __name__ == "__main__":
    main()
