from __future__ import annotations


EXCEPTION_TYPES = [
    "NO_EXCEPTION",
    "LATE_SHIPMENT",
    "INVENTORY_SHORTAGE",
    "QUANTITY_SHORTFALL",
    "PRICE_MISMATCH",
    "DATA_QUALITY",
]

SEVERITIES = ["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

ACTION_CODES = [
    "NO_ACTION",
    "REQUEST_SUPPLIER_RECOVERY",
    "EXPEDITE_SHIPMENT",
    "SPLIT_ORDER",
    "SOURCE_ALTERNATE_SUPPLIER",
    "TRANSFER_INVENTORY",
    "HOLD_FOR_PRICE_REVIEW",
    "CORRECT_ERP_DATA",
    "ESCALATE_TO_PLANNER",
]

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "summary": {"type": "string"},
        "primary_exception": {"type": "string", "enum": EXCEPTION_TYPES},
        "severity": {"type": "string", "enum": SEVERITIES},
        "action_code": {"type": "string", "enum": ACTION_CODES},
        "recommended_action": {"type": "string"},
        "rationale": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "evidence_used": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "risk_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "requires_approval": {"type": "boolean"},
        "approval_reason": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "order_id",
        "summary",
        "primary_exception",
        "severity",
        "action_code",
        "recommended_action",
        "rationale",
        "evidence_used",
        "risk_flags",
        "requires_approval",
        "approval_reason",
        "confidence",
    ],
    "additionalProperties": False,
}


class ProposalGuardrailError(ValueError):
    """Raised when an LLM proposal violates deterministic business controls."""


def validate_proposal(proposal: dict, deterministic: dict, policy: dict) -> None:
    missing = [field for field in PROPOSAL_SCHEMA["required"] if field not in proposal]
    if missing:
        raise ProposalGuardrailError(f"Model proposal is missing fields: {', '.join(missing)}")
    if proposal["order_id"] != deterministic["order_id"]:
        raise ProposalGuardrailError("Model changed the order identifier")
    if proposal["primary_exception"] != deterministic["exception_type"]:
        raise ProposalGuardrailError("Model changed the deterministic exception classification")
    if proposal["severity"] != deterministic["severity"]:
        raise ProposalGuardrailError("Model changed the deterministic severity")
    if proposal["action_code"] not in policy["allowed_action_codes"]:
        raise ProposalGuardrailError(f"Action {proposal['action_code']} is not allowed by policy")

    mandatory_approval = deterministic["requires_approval"] or proposal["action_code"] in policy[
        "actions_requiring_approval"
    ]
    if mandatory_approval and not proposal["requires_approval"]:
        raise ProposalGuardrailError("Model attempted to bypass a required human approval")
    if proposal["requires_approval"] and not proposal["approval_reason"]:
        raise ProposalGuardrailError("Approval-required proposals must explain why approval is needed")
    if not isinstance(proposal["confidence"], (int, float)) or not 0 <= proposal["confidence"] <= 1:
        raise ProposalGuardrailError("Model confidence must be between 0 and 1")
