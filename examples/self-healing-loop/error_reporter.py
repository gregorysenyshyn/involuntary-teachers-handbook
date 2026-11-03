"""
Reference Pattern: Backend Lambda @github_error_reporter Decorator.
Catches unhandled exceptions (or explicit 5xx responses), masks all PII,
and publishes a structured traceback payload to the ErrorTicketsTopic SNS topic.
An downstream ErrorTicketCreator Lambda deduplicates by stack-trace fingerprint
and opens a GitHub Issue labeled `auto-heal` and `Jules`.
"""

import os
import re
import json
import hashlib
import traceback
from functools import wraps
import boto3

sns_client = boto3.client("sns")
ERROR_TOPIC_ARN = os.environ.get("ERROR_TICKETS_TOPIC_ARN", "")

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def mask_pii(text: str) -> str:
    """Strips raw emails and phone numbers before any traceback leaves the function."""
    if not text:
        return ""
    masked = EMAIL_RE.sub("[MASKED_EMAIL]", text)
    return PHONE_RE.sub("[MASKED_PHONE]", masked)


def publish_error_ticket(service_name: str, exc: Exception, event_meta: dict) -> None:
    if not ERROR_TOPIC_ARN:
        return

    raw_tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    sanitized_tb = mask_pii(raw_tb)
    fingerprint = hashlib.sha256(f"{service_name}:{type(exc).__name__}:{sanitized_tb[-400:]}".encode()).hexdigest()[:16]

    payload = {
        "source": "lambda_exception",
        "service": service_name,
        "exception_type": type(exc).__name__,
        "exception_message": mask_pii(str(exc)),
        "traceback": sanitized_tb,
        "fingerprint": fingerprint,
        "route": event_meta.get("rawPath") or event_meta.get("path", "unknown"),
        "labels": ["auto-heal", "Jules"],
    }

    sns_client.publish(
        TopicArn=ERROR_TOPIC_ARN,
        Subject=f"[Prod Error] {service_name}: {type(exc).__name__} ({fingerprint})",
        Message=json.dumps(payload),
    )


def github_error_reporter(service_name: str):
    """Decorator for AWS Lambda handlers that fails fast while triggering the self-healing loop."""
    def decorator(handler_fn):
        @wraps(handler_fn)
        def wrapper(event, context):
            try:
                return handler_fn(event, context)
            except Exception as exc:
                publish_error_ticket(service_name, exc, event if isinstance(event, dict) else {})
                raise
        return wrapper
    return decorator
