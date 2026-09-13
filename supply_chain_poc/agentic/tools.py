from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from supply_chain_poc.engine import triage_order
from supply_chain_poc.observability import METRICS, span


class ToolExecutionError(RuntimeError):
    pass


POLICIES = {
    "NO_EXCEPTION": {
        "allowed_action_codes": ["NO_ACTION"],
        "actions_requiring_approval": [],
    },
    "LATE_SHIPMENT": {
        "allowed_action_codes": ["REQUEST_SUPPLIER_RECOVERY", "EXPEDITE_SHIPMENT", "ESCALATE_TO_PLANNER"],
        "actions_requiring_approval": ["EXPEDITE_SHIPMENT"],
    },
    "INVENTORY_SHORTAGE": {
        "allowed_action_codes": ["TRANSFER_INVENTORY", "ESCALATE_TO_PLANNER"],
        "actions_requiring_approval": ["TRANSFER_INVENTORY"],
    },
    "QUANTITY_SHORTFALL": {
        "allowed_action_codes": ["SPLIT_ORDER", "SOURCE_ALTERNATE_SUPPLIER", "ESCALATE_TO_PLANNER"],
        "actions_requiring_approval": ["SPLIT_ORDER", "SOURCE_ALTERNATE_SUPPLIER"],
    },
    "PRICE_MISMATCH": {
        "allowed_action_codes": ["HOLD_FOR_PRICE_REVIEW", "ESCALATE_TO_PLANNER"],
        "actions_requiring_approval": ["HOLD_FOR_PRICE_REVIEW"],
    },
    "DATA_QUALITY": {
        "allowed_action_codes": ["CORRECT_ERP_DATA", "ESCALATE_TO_PLANNER"],
        "actions_requiring_approval": [],
    },
}


TOOL_SPECS = [
    {
        "type": "function",
        "name": "get_order",
        "description": "Fetch one normalized ERP order by order ID. Read-only. Always call this first.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "Exact ERP order ID"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_deterministic_triage",
        "description": "Run authoritative exception rules and severity scoring for one order. Read-only and mandatory.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "Exact ERP order ID"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_action_policy",
        "description": "Return action codes and approval constraints for an authoritative exception and severity.",
        "parameters": {
            "type": "object",
            "properties": {
                "exception_type": {"type": "string", "enum": list(POLICIES)},
                "severity": {"type": "string", "enum": ["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]},
            },
            "required": ["exception_type", "severity"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "find_inventory_alternatives",
        "description": "Find other plants with positive available inventory for a SKU. Read-only; availability is not a reservation.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "exclude_plant": {"type": ["string", "null"]},
            },
            "required": ["sku", "exclude_plant"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_supplier_summary",
        "description": "Summarize observed synthetic order and late-shipment counts for one supplier. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {"supplier_id": {"type": "string"}},
            "required": ["supplier_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class CsvOrderRepository:
    def __init__(self, path: Path | str = Path("data/synthetic_orders.csv")) -> None:
        self.path = Path(path)

    def all_orders(self) -> list[dict]:
        with self.path.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def get_order(self, order_id: str) -> dict:
        order = next((row for row in self.all_orders() if row.get("order_id") == order_id), None)
        if order is None:
            raise ToolExecutionError(f"Order {order_id} was not found")
        return order


class SupplyChainTools:
    def __init__(self, repository: CsvOrderRepository | None = None) -> None:
        self.repository = repository or CsvOrderRepository()

    @property
    def specs(self) -> list[dict]:
        return TOOL_SPECS

    @property
    def optional_specs(self) -> list[dict]:
        return [
            spec
            for spec in TOOL_SPECS
            if spec["name"] in {"find_inventory_alternatives", "get_supplier_summary"}
        ]

    @staticmethod
    def policy_for(exception_type: str, severity: str) -> dict:
        if exception_type not in POLICIES:
            raise ToolExecutionError(f"Unknown exception type {exception_type}")
        policy = dict(POLICIES[exception_type])
        policy.update(
            {
                "exception_type": exception_type,
                "severity": severity,
                "minimum_approval_required": severity in {"HIGH", "CRITICAL"} or exception_type == "PRICE_MISMATCH",
            }
        )
        return policy

    def call(self, name: str, arguments: dict) -> dict:
        with span("llm.tool", tool=name):
            try:
                result = self._call(name, arguments)
            except Exception:
                METRICS.increment("supply_chain_llm_tool_calls_total", {"tool": name, "status": "failure"})
                raise
            METRICS.increment("supply_chain_llm_tool_calls_total", {"tool": name, "status": "success"})
            return result

    def _call(self, name: str, arguments: dict) -> dict:
        if name == "get_order":
            order = dict(self.repository.get_order(arguments["order_id"]))
            order.pop("expected_exception", None)
            return {"order": order}
        if name == "run_deterministic_triage":
            order = self.repository.get_order(arguments["order_id"])
            return {"decision": triage_order(order)}
        if name == "get_action_policy":
            return {"policy": self.policy_for(arguments["exception_type"], arguments["severity"])}
        if name == "find_inventory_alternatives":
            alternatives = []
            for order in self.repository.all_orders():
                if order.get("sku") != arguments["sku"] or order.get("plant") == arguments.get("exclude_plant"):
                    continue
                try:
                    available = int(order["on_hand"]) - int(order["allocated"]) - int(order["safety_stock"])
                except (KeyError, TypeError, ValueError):
                    continue
                if available > 0:
                    alternatives.append({"plant": order["plant"], "available_qty": available})
            alternatives.sort(key=lambda item: item["available_qty"], reverse=True)
            return {"sku": arguments["sku"], "alternatives": alternatives[:5], "availability_is_reserved": False}
        if name == "get_supplier_summary":
            orders = [row for row in self.repository.all_orders() if row.get("supplier_id") == arguments["supplier_id"]]
            late = 0
            risk_levels: set[str] = set()
            for order in orders:
                risk_levels.add(order.get("supplier_risk", "UNKNOWN"))
                try:
                    late += date.fromisoformat(order["current_eta"]) > date.fromisoformat(order["promised_date"])
                except (KeyError, TypeError, ValueError):
                    continue
            return {
                "supplier_id": arguments["supplier_id"],
                "observed_orders": len(orders),
                "late_orders": late,
                "late_rate": round(late / len(orders), 4) if orders else None,
                "observed_risk_levels": sorted(risk_levels),
                "source": "synthetic_order_history",
            }
        raise ToolExecutionError(f"Unknown tool {name}")
