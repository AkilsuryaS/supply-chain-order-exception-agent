from __future__ import annotations

import argparse
import csv
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from supply_chain_poc.engine import triage_order, triage_orders


DATA_FILE = Path("data/synthetic_orders.csv")


def load_orders() -> list[dict]:
    with DATA_FILE.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json({"status": "ok", "service": "supply-chain-exception-poc"})
            return

        orders = load_orders()
        if parsed.path == "/erp/orders":
            query = parse_qs(parsed.query)
            limit = min(int(query.get("limit", [50])[0]), 500)
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
            self._json({"count": len(exceptions), "exceptions": exceptions})
            return

        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/agent/triage":
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            order = json.loads(self.rfile.read(length))
            self._json(triage_order(order))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the mock ERP and exception-agent API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not DATA_FILE.exists():
        raise SystemExit(f"Missing {DATA_FILE}. Run: python -m supply_chain_poc.data_generator")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mock ERP API: http://{args.host}:{args.port}")
    print("Endpoints: /health, /erp/orders, /erp/orders/{id}, /agent/exceptions, POST /agent/triage")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

