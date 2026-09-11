from __future__ import annotations

import argparse
import csv
import json
import logging
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from supply_chain_poc import __version__
from supply_chain_poc.engine import triage_order, triage_orders
from supply_chain_poc.observability import (
    AUDIT_EVENTS,
    METRICS,
    SERVICE_NAME,
    TRACES,
    current_span_id,
    current_trace_id,
    log_event,
    parse_traceparent,
    request_context,
    span,
)


DATA_FILE = Path("data/synthetic_orders.csv")


def load_orders() -> list[dict]:
    with span("data.pull", source="synthetic_csv", path=str(DATA_FILE)) as active_span:
        try:
            with DATA_FILE.open(encoding="utf-8") as handle:
                orders = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            METRICS.increment("supply_chain_data_pulls_total", {"source": "synthetic_csv", "status": "failure"})
            raise
        active_span.set_attribute("record_count", len(orders))
        METRICS.increment("supply_chain_data_pulls_total", {"source": "synthetic_csv", "status": "success"})
        return orders


def route_template(path: str) -> str:
    if path.startswith("/erp/orders/"):
        return "/erp/orders/{order_id}"
    known = {
        "/health",
        "/ready",
        "/metrics",
        "/erp/orders",
        "/agent/exceptions",
        "/agent/triage",
        "/observability/traces",
        "/observability/audit",
    }
    return path if path in known else "/not-found"


class Handler(BaseHTTPRequestHandler):
    request_id = ""
    response_status = HTTPStatus.INTERNAL_SERVER_ERROR

    def _headers(self, content_type: str, length: int, status: HTTPStatus) -> None:
        self.response_status = status
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Request-ID", self.request_id)
        trace_id = current_trace_id()
        span_id = current_span_id()
        if trace_id and span_id:
            self.send_header("traceparent", f"00-{trace_id}-{span_id}-01")
        self.end_headers()

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode()
        self._headers("application/json", len(body), status)
        self.wfile.write(body)

    def _text(self, payload: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = payload.encode()
        self._headers(content_type, len(body), HTTPStatus.OK)
        self.wfile.write(body)

    @staticmethod
    def _limit(query: dict[str, list[str]], default: int = 50) -> int:
        try:
            return min(max(int(query.get("limit", [default])[0]), 0), 500)
        except ValueError:
            return default

    def _dispatch_get(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._json({"status": "ok", "service": SERVICE_NAME, "version": __version__})
            return
        if parsed.path == "/ready":
            try:
                order_count = len(load_orders())
                self._json({"status": "ready", "checks": {"data_source": "ok"}, "order_count": order_count})
            except (OSError, csv.Error) as exc:
                self._json(
                    {"status": "not_ready", "checks": {"data_source": type(exc).__name__}},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return
        if parsed.path == "/metrics":
            self._text(METRICS.render_prometheus(), "text/plain; version=0.0.4; charset=utf-8")
            return
        if parsed.path == "/observability/traces":
            traces = TRACES.recent(self._limit(query))
            self._json({"count": len(traces), "traces": traces})
            return
        if parsed.path == "/observability/audit":
            events = AUDIT_EVENTS.recent(self._limit(query))
            self._json({"count": len(events), "events": events})
            return

        orders = load_orders()
        if parsed.path == "/erp/orders":
            limit = self._limit(query)
            expected = query.get("expected_exception", [None])[0]
            if expected:
                orders = [row for row in orders if row["expected_exception"] == expected]
            self._json({"count": min(len(orders), limit), "orders": orders[:limit]})
            return
        if parsed.path.startswith("/erp/orders/"):
            order_id = parsed.path.rsplit("/", 1)[-1]
            order = next((row for row in orders if row["order_id"] == order_id), None)
            self._json(order or {"error": "Order not found"}, HTTPStatus.OK if order else HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/agent/exceptions":
            results = triage_orders(orders)
            exceptions = [result for result in results if result["exception_type"] != "NO_EXCEPTION"]
            log_event(logging.INFO, "agent.batch_completed", orders=len(orders), exceptions=len(exceptions))
            self._json({"count": len(exceptions), "exceptions": exceptions})
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _dispatch_post(self) -> None:
        if urlparse(self.path).path != "/agent/triage":
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            order = json.loads(self.rfile.read(length))
            self._json(triage_order(order))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _observe_request(self, method: str, dispatch) -> None:
        parsed = urlparse(self.path)
        route = route_template(parsed.path)
        self.request_id = self.headers.get("X-Request-ID") or str(uuid.uuid4())
        incoming_trace = parse_traceparent(self.headers.get("traceparent"))
        started = time.perf_counter()
        with request_context(self.request_id, incoming_trace):
            with span("http.request", method=method, route=route) as request_span:
                try:
                    dispatch()
                except Exception as exc:
                    log_event(
                        logging.ERROR,
                        "http.request_failed",
                        method=method,
                        route=route,
                        error_type=type(exc).__name__,
                    )
                    self._json(
                        {"error": "Internal server error", "request_id": self.request_id},
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                finally:
                    request_span.set_attribute("http.status_code", int(self.response_status))
            duration = time.perf_counter() - started
            status = str(int(self.response_status))
            METRICS.increment(
                "supply_chain_http_requests_total",
                {"method": method, "route": route, "status": status},
            )
            METRICS.observe_duration(
                "supply_chain_http_request_duration_seconds",
                duration,
                {"method": method, "route": route},
            )
            log_event(
                logging.INFO,
                "http.request_completed",
                method=method,
                route=route,
                status=int(self.response_status),
                duration_ms=round(duration * 1000, 3),
            )

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._observe_request("GET", self._dispatch_get)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._observe_request("POST", self._dispatch_post)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the mock ERP and exception-agent API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not DATA_FILE.exists():
        raise SystemExit(f"Missing {DATA_FILE}. Run: python -m supply_chain_poc.data_generator")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log_event(logging.INFO, "service.started", host=args.host, port=args.port, version=__version__)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log_event(logging.INFO, "service.stopped")


if __name__ == "__main__":
    main()
