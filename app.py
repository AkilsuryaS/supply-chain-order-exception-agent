from __future__ import annotations

import csv
import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from supply_chain_poc import __version__
from supply_chain_poc.agentic.runtime import (
    AgentConfigurationError,
    AgentRunError,
    HuggingFaceResponseAgent,
    ProposalGuardrailError,
)
from supply_chain_poc.api import load_orders, route_template
from supply_chain_poc.engine import triage_order, triage_orders
from supply_chain_poc.observability import (
    AUDIT_EVENTS,
    METRICS,
    SERVICE_NAME,
    current_span_id,
    current_trace_id,
    log_event,
    parse_traceparent,
    request_context,
    span,
)


WEB_DIR = Path(__file__).resolve().parent / "supply_chain_poc" / "web"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

app = FastAPI(
    title="Supply Chain Order Exception Agent",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def observe_request(request: Request, call_next):
    route = route_template(request.url.path)
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    incoming_trace = parse_traceparent(request.headers.get("traceparent"))
    started = time.perf_counter()
    with request_context(request_id, incoming_trace):
        with span("http.request", method=request.method, route=route) as request_span:
            try:
                response = await call_next(request)
            except Exception as exc:
                log_event(
                    logging.ERROR,
                    "http.request_failed",
                    method=request.method,
                    route=route,
                    error_type=type(exc).__name__,
                )
                response = JSONResponse(
                    {"error": "Internal server error", "request_id": request_id}, status_code=500
                )
            request_span.set_attribute("http.status_code", response.status_code)
            response_trace_id = current_trace_id()
            response_span_id = current_span_id()
        duration = time.perf_counter() - started
        status = str(response.status_code)
        METRICS.increment(
            "supply_chain_http_requests_total",
            {"method": request.method, "route": route, "status": status},
        )
        METRICS.observe_duration(
            "supply_chain_http_request_duration_seconds",
            duration,
            {"method": request.method, "route": route},
        )
        log_event(
            logging.INFO,
            "http.request_completed",
            method=request.method,
            route=route,
            status=response.status_code,
            duration_ms=round(duration * 1000, 3),
        )
        response.headers.update(SECURITY_HEADERS)
        response.headers["X-Request-ID"] = request_id
        if response_trace_id and response_span_id:
            response.headers["traceparent"] = f"00-{response_trace_id}-{response_span_id}-01"
        return response


@app.get("/", include_in_schema=False)
@app.get("/preview", include_in_schema=False)
@app.get("/agent/llm-triage", include_in_schema=False)
def preview() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE_NAME, "version": __version__}


@app.get("/ready")
def ready():
    try:
        orders = load_orders()
        return {"status": "ready", "checks": {"data_source": "ok"}, "order_count": len(orders)}
    except (OSError, csv.Error) as exc:
        return JSONResponse(
            {"status": "not_ready", "checks": {"data_source": type(exc).__name__}},
            status_code=503,
        )


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(METRICS.render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/erp/orders")
def orders(limit: int = Query(50, ge=0, le=500), expected_exception: str | None = None) -> dict:
    records = load_orders()
    if expected_exception:
        records = [row for row in records if row["expected_exception"] == expected_exception]
    return {"count": min(len(records), limit), "orders": records[:limit]}


@app.get("/erp/orders/{order_id}")
def order(order_id: str):
    record = next((row for row in load_orders() if row["order_id"] == order_id), None)
    if record is None:
        return JSONResponse({"error": "Order not found"}, status_code=404)
    return record


@app.get("/agent/exceptions")
def exceptions() -> dict:
    records = load_orders()
    results = triage_orders(records)
    detected = [result for result in results if result["exception_type"] != "NO_EXCEPTION"]
    log_event(logging.INFO, "agent.batch_completed", orders=len(records), exceptions=len(detected))
    return {"count": len(detected), "exceptions": detected}


@app.post("/agent/triage")
def deterministic_triage(payload: dict) -> dict:
    return triage_order(payload)


@app.post("/agent/llm-triage")
def llm_triage(payload: dict, x_hf_token: str | None = Header(None, alias="X-HF-Token")):
    try:
        agent = HuggingFaceResponseAgent.from_env(token_override=x_hf_token)
        return agent.run(payload.get("order_id", ""), payload.get("planner_notes", ""))
    except AgentConfigurationError as exc:
        return JSONResponse(
            {"error": str(exc), "code": "configuration_error", "retryable": False},
            status_code=503,
        )
    except AgentRunError as exc:
        status_code = 503 if exc.retryable else 502
        code = "provider_unavailable" if exc.retryable else "agent_run_error"
        return JSONResponse(
            {"error": str(exc), "code": code, "retryable": exc.retryable},
            status_code=status_code,
        )
    except ProposalGuardrailError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/observability/traces")
def traces(limit: int = Query(50, ge=0, le=500)) -> dict:
    records = TRACES.recent(limit)
    return {"count": len(records), "traces": records}


@app.get("/observability/audit")
def audit(limit: int = Query(50, ge=0, le=500)) -> dict:
    records = AUDIT_EVENTS.recent(limit)
    return {"count": len(records), "events": records}


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")
