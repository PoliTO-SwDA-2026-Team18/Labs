import os
import time
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
import structlog
from dotenv import load_dotenv
from mzinga_client import MzingaClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from prometheus_client import start_http_server

load_dotenv()

MZINGA_URL = os.environ["MZINGA_URL"]
MZINGA_EMAIL = os.environ["MZINGA_EMAIL"]
MZINGA_PASSWORD = os.environ["MZINGA_PASSWORD"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 5))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))
EMAIL_FROM = os.getenv("EMAIL_FROM", "worker@mzinga.io")
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
SERVICE_NAME_VALUE = os.getenv("OTEL_SERVICE_NAME", "email-worker")
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", 8000))

# ---------------------------------------------------------------------------
# OpenTelemetry: Tracing
# ---------------------------------------------------------------------------

resource = Resource(attributes={
    SERVICE_NAME: SERVICE_NAME_VALUE,
    SERVICE_VERSION: "1.0.0",
})

otlp_span_exporter = OTLPSpanExporter(endpoint=f"{OTLP_ENDPOINT}/v1/traces")
batch_span_processor = BatchSpanProcessor(otlp_span_exporter)
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(batch_span_processor)
trace.set_tracer_provider(tracer_provider)

RequestsInstrumentor().instrument()

tracer = trace.get_tracer(SERVICE_NAME_VALUE)


# ---------------------------------------------------------------------------
# OpenTelemetry: Metrics
# ---------------------------------------------------------------------------

# 1. Start the HTTP server to expose the /metrics endpoint for Prometheus to scrape
start_http_server(port=PROMETHEUS_PORT)

# 2. Use PrometheusMetricReader (Pull-based) instead of PeriodicExportingMetricReader
metric_reader = PrometheusMetricReader()
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)

meter = metrics.get_meter(SERVICE_NAME_VALUE)

emails_processed_total = meter.create_counter(
    name="emails_processed_total",
    description="Total number of communications processed",
    unit="1"
)
email_processing_duration_seconds = meter.create_histogram(
    name="email_processing_duration_seconds",
    description="End-to-end duration of processing one communication",
    unit="s"
)
smtp_send_duration_seconds = meter.create_histogram(
    name="smtp_send_duration_seconds",
    description="Duration of the SMTP send call",
    unit="s"
)
worker_poll_total = meter.create_counter(
    name="worker_poll_total",
    description="Number of poll cycles",
    unit="1"
)


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

def add_otel_context(logger, method, event_dict):
    """Inject active trace_id and span_id into every log entry."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_otel_context,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
logger = structlog.get_logger(service=SERVICE_NAME_VALUE)


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


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def process_document(client: MzingaClient, doc: dict):
    """Claim, process, and finalise a single communications document."""
    doc_id = doc["id"]
    structlog.contextvars.bind_contextvars(doc_id=doc_id)

    with tracer.start_as_current_span("process_communication") as span_process_communication:
        span_process_communication.set_attribute("doc_id", doc_id)
        t0_process_communication = time.perf_counter()

        error = None
        to_addrs, cc_addrs, bcc_addrs = [], [], []
        try:
            client.update_status(doc_id, "processing")
            logger.info("processing_started")

            # Resolve recipients
            to_addrs = resolve_emails(doc.get("tos"))
            cc_addrs = resolve_emails(doc.get("ccs"))
            bcc_addrs = resolve_emails(doc.get("bccs"))

            # Serialize body to HTML
            with tracer.start_as_current_span("serialize_body") as span_serialize_body:
                body = doc.get("body", [])
                span_serialize_body.set_attribute("node_count", len(body))
                html_body = serialize_body(body) if body else ""

            subject = doc.get("subject", "")

            # Send email
            with tracer.start_as_current_span("send_email") as span_send_email:
                span_send_email.set_attribute("recipient_count", len(to_addrs) + len(cc_addrs) + len(bcc_addrs))
                t0_send_email = time.perf_counter()

                send_email(to_addrs, cc_addrs, bcc_addrs, subject, html_body)

                duration_send_email = time.perf_counter() - t0_send_email
                smtp_send_duration_seconds.record(duration_send_email)
                logger.info("email_sent", duration_s=round(duration_send_email, 3))

        except (requests.RequestException, smtplib.SMTPException, ConnectionRefusedError, ValueError) as e:
            error = e
        finally:
            duration_process_communication = time.perf_counter() - t0_process_communication
            email_processing_duration_seconds.record(duration_process_communication)

            status = "sent" if error is None else "failed"
            duration_round = round(duration_process_communication, 3)

            if error is not None:
                span_process_communication.set_status(trace.StatusCode.ERROR, str(error))
                span_process_communication.record_exception(error)
                logger.error("processing_failed", status=status, duration_s=duration_round, error_type=type(error).__name__, error=str(error))
            else:
                logger.info("processing_completed", status=status, duration_s=duration_round)

            emails_processed_total.add(1, {"status": status, "recipient_count": len(to_addrs) + len(cc_addrs) + len(bcc_addrs)})
            client.update_status(doc_id, status) 


    structlog.contextvars.unbind_contextvars("doc_id")


def main():
    client = MzingaClient(MZINGA_URL, MZINGA_EMAIL, MZINGA_PASSWORD, logger)
    client.login()
    logger.info("worker-observable_started", poll_interval_s=POLL_INTERVAL, prometheus_port=PROMETHEUS_PORT)

    while True:
        try:
            docs = client.fetch_docs_pending()
            if docs:
                worker_poll_total.add(1, {"result": "found"})
                for doc in docs:
                    process_document(client, doc)
            else:
                worker_poll_total.add(1, {"result": "empty"})
                time.sleep(POLL_INTERVAL)
        except requests.HTTPError as e:
            logger.error("http_error", status_code=e.response.status_code, error=str(e))
            time.sleep(POLL_INTERVAL)
        except requests.RequestException as e:
            logger.error("request_error", error_type=type(e).__name__, error=str(e))
            time.sleep(POLL_INTERVAL)
            


if __name__ == "__main__":
    main()
