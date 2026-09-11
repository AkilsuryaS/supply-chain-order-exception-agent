from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from supply_chain_poc.observability import record_decision, span


PRIORITY_WEIGHT = {"STANDARD": 1.0, "HIGH": 1.4, "CRITICAL": 2.0}
RISK_WEIGHT = {"LOW": 1.0, "MEDIUM": 1.2, "HIGH": 1.5}


def _number(order: dict, field: str) -> Decimal:
    try:
        return Decimal(str(order[field]))
    except (KeyError, InvalidOperation, TypeError):
        raise ValueError(f"Invalid or missing {field}")


def _date(order: dict, field: str) -> date:
    try:
        return date.fromisoformat(str(order[field]))
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Invalid or missing {field}")


def detect_exceptions(order: dict) -> list[dict]:
    required = ["order_id", "supplier_id", "sku", "promised_date", "current_eta"]
    missing = [field for field in required if not order.get(field)]
    if missing:
        return [{"type": "DATA_QUALITY", "detail": f"Missing required fields: {', '.join(missing)}"}]

    try:
        promised = _date(order, "promised_date")
        eta = _date(order, "current_eta")
        ordered = _number(order, "ordered_qty")
        confirmed = _number(order, "confirmed_qty")
        on_hand = _number(order, "on_hand")
        allocated = _number(order, "allocated")
        safety_stock = _number(order, "safety_stock")
        unit_price = _number(order, "unit_price")
        contract_price = _number(order, "contract_price")
    except ValueError as exc:
        return [{"type": "DATA_QUALITY", "detail": str(exc)}]

    issues: list[dict] = []
    if eta > promised:
        issues.append({"type": "LATE_SHIPMENT", "days_late": (eta - promised).days})
    if confirmed < ordered:
        issues.append({"type": "QUANTITY_SHORTFALL", "shortfall_qty": int(ordered - confirmed)})
    available = on_hand - allocated - safety_stock
    if available < ordered:
        issues.append({"type": "INVENTORY_SHORTAGE", "shortage_qty": int(ordered - available)})
    if contract_price > 0:
        variance = (unit_price - contract_price) / contract_price
        if abs(variance) > Decimal("0.02"):
            issues.append({"type": "PRICE_MISMATCH", "price_variance_pct": round(float(variance * 100), 2)})
    return issues


def _severity(order: dict, issue: dict) -> tuple[str, float]:
    priority = PRIORITY_WEIGHT.get(str(order.get("customer_priority", "STANDARD")), 1.0)
    risk = RISK_WEIGHT.get(str(order.get("supplier_risk", "LOW")), 1.0)
    issue_type = issue["type"]
    if issue_type == "LATE_SHIPMENT":
        score = issue["days_late"] * priority * risk
    elif issue_type in {"QUANTITY_SHORTFALL", "INVENTORY_SHORTAGE"}:
        ordered = max(float(order.get("ordered_qty") or 1), 1)
        affected = issue.get("shortfall_qty", issue.get("shortage_qty", 0))
        score = (affected / ordered) * 20 * priority
    elif issue_type == "PRICE_MISMATCH":
        score = abs(issue["price_variance_pct"]) * priority
    else:
        score = 9.0
    label = "CRITICAL" if score >= 16 else "HIGH" if score >= 9 else "MEDIUM" if score >= 4 else "LOW"
    return label, round(score, 2)


def _recommendation(issue_type: str, severity: str) -> tuple[str, bool]:
    actions = {
        "LATE_SHIPMENT": "Request supplier recovery date and evaluate an expedited shipment",
        "QUANTITY_SHORTFALL": "Split the order or source the missing quantity from an approved alternate supplier",
        "INVENTORY_SHORTAGE": "Check another plant for available inventory and propose a stock transfer",
        "PRICE_MISMATCH": "Place the order on commercial hold and validate the contract price",
        "DATA_QUALITY": "Correct the missing ERP fields before operational processing",
    }
    return actions[issue_type], severity in {"HIGH", "CRITICAL"} or issue_type == "PRICE_MISMATCH"


def _triage_order(order: dict) -> dict:
    order_id = order.get("order_id")
    with span("agent.classification", order_id=order_id):
        issues = detect_exceptions(order)
    if not issues:
        result = {
            "order_id": order.get("order_id"),
            "exception_type": "NO_EXCEPTION",
            "severity": "NONE",
            "score": 0,
            "confidence": 1.0,
            "evidence": ["No configured exception rule was triggered"],
            "recommended_action": "No action required",
            "requires_approval": False,
            "all_exceptions": [],
        }
        audit_event = record_decision(result)
        result["decision_id"] = audit_event["decision_id"]
        result["policy_version"] = audit_event["policy_version"]
        return result

    with span("agent.severity", order_id=order_id, exception_count=len(issues)):
        ranked = []
        for issue in issues:
            severity, score = _severity(order, issue)
            ranked.append((score, severity, issue))
        score, severity, primary = max(ranked, key=lambda item: item[0])
    with span("agent.recommendation", order_id=order_id, exception_type=primary["type"]):
        action, approval = _recommendation(primary["type"], severity)
    evidence = [f"{key.replace('_', ' ')}: {value}" for key, value in primary.items() if key != "type"]
    result = {
        "order_id": order.get("order_id"),
        "exception_type": primary["type"],
        "severity": severity,
        "score": score,
        "confidence": 1.0,
        "evidence": evidence,
        "recommended_action": action,
        "requires_approval": approval,
        "all_exceptions": [issue["type"] for _, _, issue in sorted(ranked, reverse=True)],
    }
    audit_event = record_decision(result)
    result["decision_id"] = audit_event["decision_id"]
    result["policy_version"] = audit_event["policy_version"]
    return result


def triage_order(order: dict) -> dict:
    order_id = order.get("order_id")
    with span("agent.order", order_id=order_id) as active_span:
        result = _triage_order(order)
        active_span.set_attribute("exception_type", result["exception_type"])
        active_span.set_attribute("severity", result["severity"])
        active_span.set_attribute("requires_approval", result["requires_approval"])
        return result


def triage_orders(orders: list[dict]) -> list[dict]:
    with span("agent.batch", order_count=len(orders)):
        return [triage_order(order) for order in orders]
