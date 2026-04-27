import os
import time
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient, errors

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://admin:admin@localhost:27017/mzinga?authSource=admin&directConnection=true")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
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

def resolve_emails(db, field) -> list[str]:
    """Resolve a tos/ccs/bccs relationship array to a list of email addresses."""
    if not field:
        return []

    user_ids = []
    for ref in field:
        value = ref.get("value")
        if value is not None:
            user_ids.append(ObjectId(value))

    if not user_ids:
        return []

    users = db["users"].find({"_id": {"$in": user_ids}}, {"email": 1}) # Binds userIds with emails

    emails = []
    for u in users:
        if u.get("email"):
            emails.append(u["email"])

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

def process_document(db, doc):
    """Claim, process, and finalise a single communications document."""
    doc_id = doc["_id"]
    try:
        db["communications"].update_one(
            {"_id": doc_id},
            {"$set": {"status": "processing"}},
        )
        logger.info("Processing document %s", doc_id)

   
        # Resolve recipients
        to_addrs = resolve_emails(db, doc.get("tos"))
        cc_addrs = resolve_emails(db, doc.get("ccs"))
        bcc_addrs = resolve_emails(db, doc.get("bccs"))

        # Serialize body to HTML
        body = doc.get("body", [])
        html_body = serialize_body(body) if body else ""

        subject = doc.get("subject", "")

        # Send email
        send_email(to_addrs, cc_addrs, bcc_addrs, subject, html_body)

        # Mark as sent
        db["communications"].update_one(
            {"_id": doc_id},
            {"$set": {"status": "sent"}},
        )
        logger.info("Document %s marked as sent", doc_id)

    except (errors.PyMongoError, smtplib.SMTPException, ValueError) as e:
        logger.error("Failed to process document %s: %s", doc_id, e)
        db["communications"].update_one(
            {"_id": doc_id},
            {"$set": {"status": "failed"}},
        )


def main():
    client = MongoClient(MONGODB_URI) # Connection to the MongoDB
    db = client.get_default_database() # Retrive database
    logger.info("Connected to MongoDB — polling every %ds", POLL_INTERVAL)

    while True:
        try:
            doc = db["communications"].find_one({"status": "pending"}) # Find document with status = pending
            if doc:
                process_document(db, doc)
            else:
                time.sleep(POLL_INTERVAL)
        except errors.PyMongoError as e:
            logger.error("MongoDB error: %s", e)
            time.sleep(POLL_INTERVAL)



if __name__ == "__main__":
    main()
