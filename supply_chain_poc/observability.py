"""Dependency-light observability primitives for the POC.

The interfaces mirror production concepts: structured logs, W3C-compatible trace
identifiers, bounded in-memory trace/audit stores, and Prometheus text metrics.
They can later be replaced by OpenTelemetry and a durable audit sink without
changing the business rules.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Iterator


SERVICE_NAME = os.getenv("SERVICE_NAME", "supply-chain-exception-poc")
POLICY_VERSION = os.getenv("POLICY_VERSION", "2026-09-11.v1")
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("span_id", default=None)
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_TRACEPARENT = re.compile(r"^[\da-f]{2}-([\da-f]{32})-([\da-f]{16})-[\da-f]{2}$", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": _utc_now(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event_data = getattr(record, "event_data", None)
        if event_data:
            payload.update(event_data)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("supply_chain_poc")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    return logger


LOGGER = configure_logging()


def log_event(level: int, event: str, **fields: object) -> None:
    context = {
        "event": event,
        "trace_id": _trace_id.get(),
        "span_id": _span_id.get(),
        "request_id": _request_id.get(),
    }
    context.update(fields)
    LOGGER.log(level, event, extra={"event_data": {key: value for key, value in context.items() if value is not None}})


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._duration_count: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._duration_sum: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()

    @staticmethod
    def _key(name: str, labels: dict[str, object] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted((key, str(value)) for key, value in (labels or {}).items()))

    def increment(self, name: str, labels: dict[str, object] | None = None, value: int = 1) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def observe_duration(self, name: str, seconds: float, labels: dict[str, object] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._duration_count[key] += 1
            self._duration_sum[key] += seconds

    @staticmethod
    def _labels(labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ""
        rendered = []
        for key, value in labels:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            rendered.append(f'{key}="{escaped}"')
        return "{" + ",".join(rendered) + "}"

    def render_prometheus(self) -> str:
        with self._lock:
            counters = sorted(self._counters.items())
            counts = sorted(self._duration_count.items())
            sums = dict(self._duration_sum)
        lines = ["# Supply Chain Exception Agent metrics"]
        emitted: set[str] = set()
        for (name, labels), value in counters:
            if name not in emitted:
                lines.append(f"# TYPE {name} counter")
                emitted.add(name)
            lines.append(f"{name}{self._labels(labels)} {value}")
        for (name, labels), count in counts:
            if name not in emitted:
                lines.append(f"# TYPE {name} summary")
                emitted.add(name)
            suffix = self._labels(labels)
            lines.append(f"{name}_count{suffix} {count}")
            lines.append(f"{name}_sum{suffix} {sums[(name, labels)]:.6f}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._duration_count.clear()
            self._duration_sum.clear()


class BoundedStore:
    def __init__(self, maxlen: int) -> None:
        self._items: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, item: dict) -> None:
        with self._lock:
            self._items.append(item)

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._items)[-max(0, limit) :][::-1]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


METRICS = MetricsRegistry()
TRACES = BoundedStore(int(os.getenv("TRACE_BUFFER_SIZE", "2000")))
AUDIT_EVENTS = BoundedStore(int(os.getenv("AUDIT_BUFFER_SIZE", "1000")))


def parse_traceparent(value: str | None) -> str | None:
    if not value:
        return None
    match = _TRACEPARENT.match(value.strip())
    return match.group(1).lower() if match else None


def current_trace_id() -> str | None:
    return _trace_id.get()


def current_span_id() -> str | None:
    return _span_id.get()


@contextlib.contextmanager
def request_context(request_id: str | None = None, trace_id: str | None = None) -> Iterator[None]:
    trace_token = _trace_id.set(trace_id or uuid.uuid4().hex)
    span_token = _span_id.set(None)
    request_token = _request_id.set(request_id or str(uuid.uuid4()))
    try:
        yield
    finally:
        _request_id.reset(request_token)
        _span_id.reset(span_token)
        _trace_id.reset(trace_token)


class Span:
    def __init__(self, name: str, attributes: dict[str, object]) -> None:
        self.name = name
        self.attributes = attributes

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


@contextlib.contextmanager
def span(name: str, **attributes: object) -> Iterator[Span]:
    generated_context = _trace_id.get() is None
    trace_token = _trace_id.set(uuid.uuid4().hex) if generated_context else None
    parent_span_id = _span_id.get()
    span_id = uuid.uuid4().hex[:16]
    span_token = _span_id.set(span_id)
    started_at = _utc_now()
    started = time.perf_counter()
    status = "OK"
    error_type: str | None = None
    active = Span(name, dict(attributes))
    try:
        yield active
    except Exception as exc:
        status = "ERROR"
        error_type = type(exc).__name__
        raise
    finally:
        duration = time.perf_counter() - started
        record = {
            "trace_id": _trace_id.get(),
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "request_id": _request_id.get(),
            "name": name,
            "started_at": started_at,
            "duration_ms": round(duration * 1000, 3),
            "status": status,
            "attributes": active.attributes,
        }
        if error_type:
            record["error_type"] = error_type
        TRACES.add(record)
        METRICS.increment("supply_chain_spans_total", {"span": name, "status": status.lower()})
        METRICS.observe_duration("supply_chain_span_duration_seconds", duration, {"span": name, "status": status.lower()})
        log_event(logging.DEBUG, "span.completed", span_name=name, status=status, duration_ms=record["duration_ms"])
        _span_id.reset(span_token)
        if trace_token is not None:
            _trace_id.reset(trace_token)


def record_decision(result: dict) -> dict:
    event = {
        "decision_id": str(uuid.uuid4()),
        "timestamp": _utc_now(),
        "trace_id": current_trace_id(),
        "request_id": _request_id.get(),
        "order_id": result.get("order_id"),
        "exception_type": result.get("exception_type"),
        "severity": result.get("severity"),
        "score": result.get("score"),
        "requires_approval": result.get("requires_approval"),
        "policy_version": POLICY_VERSION,
    }
    AUDIT_EVENTS.add(event)
    METRICS.increment(
        "supply_chain_triage_decisions_total",
        {"exception_type": result.get("exception_type"), "severity": result.get("severity")},
    )
    return event


def record_agentic_decision(
    proposal: dict,
    *,
    model: str,
    response_id: str | None,
    deterministic_decision_id: str,
) -> dict:
    event = {
        "decision_id": str(uuid.uuid4()),
        "decision_type": "LLM_ACTION_PROPOSAL",
        "timestamp": _utc_now(),
        "trace_id": current_trace_id(),
        "request_id": _request_id.get(),
        "order_id": proposal.get("order_id"),
        "exception_type": proposal.get("primary_exception"),
        "severity": proposal.get("severity"),
        "action_code": proposal.get("action_code"),
        "requires_approval": proposal.get("requires_approval"),
        "policy_version": POLICY_VERSION,
        "model": model,
        "response_id": response_id,
        "deterministic_decision_id": deterministic_decision_id,
    }
    AUDIT_EVENTS.add(event)
    METRICS.increment(
        "supply_chain_llm_proposals_total",
        {"action_code": proposal.get("action_code"), "status": "accepted"},
    )
    return event
