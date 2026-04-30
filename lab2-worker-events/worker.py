import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from dotenv import load_dotenv
from mzinga_client import MzingaClient
import aio_pika
import asyncio
import json

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

RABBITMQ_URL = os.environ["RABBITMQ_URL"]
ROUTING_KEY = os.environ["ROUTING_KEY"]
EXCHANGE_NAME = os.environ["EXCHANGE_NAME"]
QUEUE_NAME = os.environ["QUEUE_NAME"]
MZINGA_URL = os.environ["MZINGA_URL"]
MZINGA_EMAIL = os.environ["MZINGA_EMAIL"]
MZINGA_PASSWORD = os.environ["MZINGA_PASSWORD"]
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))
EMAIL_FROM = os.getenv("EMAIL_FROM", "worker@mzinga.io")


# ---------------------------------------------------------------------------
# Slate AST -> HTML serialiser
# ---------------------------------------------------------------------------

def serialize_leaf(leaf: dict) -> str:
    """Convert a Slate leaf node (text node) to an HTML string."""
    text = leaf.get("text", "")
    if not text:
        return ""
    if leaf.get("bold"):
        text = f"<strong>{text}</strong>"
    if leaf.get("italic"):
        text = f"<em>{text}</em>"
    return text


def serialize_node(node: dict) -> str:
    """Recursively convert a Slate AST node to HTML."""
    # Leaf text node
    if "text" in node:
        return serialize_leaf(node)

    node_type = node.get("type", "")
    children_html = "".join(serialize_node(child) for child in node.get("children", [])) # Recursive call to serialize_node

    if node_type == "h1":
        return f"<h1>{children_html}</h1>"
    if node_type == "h2":
        return f"<h2>{children_html}</h2>"
    if node_type == "paragraph":
        return f"<p>{children_html}</p>"
    if node_type == "ul":
        return f"<ul>{children_html}</ul>"
    if node_type == "li":
        return f"<li>{children_html}</li>"
    if node_type == "link":
        url = node.get("url", "#")
        return f'<a href="{url}">{children_html}</a>'

    # Fallback: render children only
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
# Main polling loop
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


async def main():
    client = MzingaClient(MZINGA_URL, MZINGA_EMAIL, MZINGA_PASSWORD)
    client.login()

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        exchange = await channel.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC,
            durable=True, internal=True, auto_delete=False,
        )

        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        await queue.bind(exchange, routing_key=ROUTING_KEY)

        logger.info(f"Subscribed to {EXCHANGE_NAME} with key {ROUTING_KEY}. Waiting for messages.")

        async with queue.iterator() as messages:
            async for message in messages:
                async with message.process(requeue=True):
                    try:
                        body = json.loads(message.body.decode())
                        event_data = body.get("data", {})
                        operation = event_data.get("operation")
                        doc_id = (event_data.get("doc") or {}).get("id")

                        if not doc_id:
                            logger.warning("Message missing doc.id, skipping")
                            continue

                        if operation != "create":
                            logger.debug(f"Ignoring operation={operation} for {doc_id}")
                            continue

                        doc = client.fetch_doc(doc_id)

                        # Idempotency guard: skip if already processed
                        if doc.get("status") in ("sent", "processing"):
                            logger.info(f"Skipping {doc_id} — already {doc['status']}")
                            continue

                        process_document(client, doc)

                    except requests.HTTPError as e:
                        logger.error("HTTP error: %s", e)
            


if __name__ == "__main__":
    asyncio.run(main())
