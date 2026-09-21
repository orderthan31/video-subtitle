"""Payload-free diagnostics; attribution describes evidence, not assumed blame."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import socket
import ssl
import time
from uuid import uuid4

import httpx
from .content_block import ContentBlockedError


attempt_context = ContextVar("llm_attempt_diagnostics", default=None)


def exception_details(error):
    chain = []
    seen = set()
    current = error
    category = "unclassified_exception"
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        item = {"type": type(current).__name__}
        for name in ("errno", "winerror"):
            value = getattr(current, name, None)
            if isinstance(value, int):
                item[name] = value
        chain.append(item)
        if isinstance(current, httpx.TransportError):
            category = "network_path_unknown"
        if isinstance(current, httpx.PoolTimeout):
            category = "local_connection_pool_timeout"
        if isinstance(current, socket.gaierror):
            category = "dns_resolution_failure"
        if isinstance(current, ssl.SSLError):
            category = "tls_failure"
        current = current.__cause__ or current.__context__
    reasons = {
        "Invalid STT sentence timestamps": "stt_sentence_timestamps",
        "Invalid STT sentence object": "stt_sentence_object",
        "STT must return a list of sentence segments": "stt_sentence_list",
        "Invalid STT sentence text": "stt_sentence_text",
        "Translation output does not match input segments": "translation_output_shape",
        "Translation output IDs do not match input targets": "translation_output_ids",
    }
    details = {"category": category, "exception_chain": chain}
    if isinstance(error, ContentBlockedError):
        details.update(category="remote_content_blocked", block_reason=error.reason)
    if isinstance(error, ValueError) and str(error) in reasons:
        details.update(category="output_rejected_by_local_validator", reason=reasons[str(error)])
    return details


def emit(event, **fields):
    # Diagnostics must never turn a successful paid request into a retry.
    try:
        context = attempt_context.get()
        record = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        if context is not None:
            record = {**context["identity"], **record}
            context["events"].append(record)
        logging.getLogger(__name__).info("llm_diagnostic %s", json.dumps(record))
        return record
    except Exception:
        return None


def new_context(**identity):
    return {"identity": {"attempt_id": uuid4().hex, **identity}, "events": [],
            "started": time.monotonic()}
