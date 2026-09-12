from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from supply_chain_poc.agentic.prompts import AGENT_INSTRUCTIONS
from supply_chain_poc.agentic.schemas import PROPOSAL_SCHEMA, ProposalGuardrailError, validate_proposal
from supply_chain_poc.agentic.tools import SupplyChainTools
from supply_chain_poc.observability import (
    METRICS,
    log_event,
    record_agentic_decision,
    span,
)


class AgentConfigurationError(RuntimeError):
    pass


class AgentRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    provider: str = "huggingface"
    model: str = "Qwen/Qwen3-32B:cheapest"
    max_turns: int = 8
    max_output_tokens: int = 1200

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            provider="huggingface",
            model=os.getenv("HF_MODEL", "Qwen/Qwen3-32B:cheapest"),
            max_turns=int(os.getenv("LLM_MAX_TURNS", "8")),
            max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1200")),
        )


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


class HuggingFaceResponsesClient:
    """Small dependency-free client for Hugging Face's Responses-compatible API."""

    def __init__(self, token: str, base_url: str, timeout: float = 45) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.responses = self

    def create(self, **payload: Any) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            message = f"Hugging Face returned HTTP {exc.code}"
            try:
                details = json.loads(exc.read()).get("error")
                if isinstance(details, dict):
                    details = details.get("message")
                if details:
                    message = f"{message}: {details}"
            except (json.JSONDecodeError, AttributeError):
                pass
            raise RuntimeError(message) from exc
        except URLError as exc:
            raise RuntimeError("Could not reach the configured Hugging Face inference endpoint") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Hugging Face returned an invalid response")
        return result


def _output_text(response: Any) -> str:
    direct = _field(response, "output_text", "")
    if direct:
        return direct
    parts: list[str] = []
    for item in _field(response, "output", []) or []:
        if _field(item, "type") != "message":
            continue
        for content in _field(item, "content", []) or []:
            if _field(content, "type") == "output_text":
                parts.append(_field(content, "text", ""))
    return "".join(parts)


class HuggingFaceResponseAgent:
    """Hugging Face Responses tool loop with deterministic post-model guardrails."""

    def __init__(self, client: Any, tools: SupplyChainTools | None = None, config: AgentConfig | None = None) -> None:
        self.client = client
        self.tools = tools or SupplyChainTools()
        self.config = config or AgentConfig.from_env()

    @classmethod
    def from_env(cls, tools: SupplyChainTools | None = None) -> "HuggingFaceResponseAgent":
        base_url = os.getenv("HF_BASE_URL", "https://router.huggingface.co/v1").rstrip("/")
        token = os.getenv("HF_TOKEN", "")
        if base_url == "https://router.huggingface.co/v1" and not token:
            raise AgentConfigurationError("HF_TOKEN is not configured")
        timeout = float(os.getenv("HF_TIMEOUT_SECONDS", "45"))
        client = HuggingFaceResponsesClient(token=token, base_url=base_url, timeout=timeout)
        return cls(client, tools=tools, config=AgentConfig.from_env())

    def _create_response(self, **kwargs: Any) -> Any:
        started = time.perf_counter()
        with span("llm.model", provider=self.config.provider, model=self.config.model) as active_span:
            try:
                response = self.client.responses.create(
                    model=self.config.model,
                    instructions=AGENT_INSTRUCTIONS,
                    tools=self.tools.specs,
                    parallel_tool_calls=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "supply_chain_action_proposal",
                            "strict": True,
                            "schema": PROPOSAL_SCHEMA,
                        }
                    },
                    max_output_tokens=self.config.max_output_tokens,
                    store=False,
                    **kwargs,
                )
            except Exception as exc:
                METRICS.increment("supply_chain_llm_model_calls_total", {"model": self.config.model, "status": "failure"})
                raise AgentRunError(f"Model request failed: {type(exc).__name__}") from exc
            duration = time.perf_counter() - started
            active_span.set_attribute("response_id", _field(response, "id"))
            METRICS.increment("supply_chain_llm_model_calls_total", {"model": self.config.model, "status": "success"})
            METRICS.observe_duration("supply_chain_llm_model_duration_seconds", duration, {"model": self.config.model})
            self._record_usage(response)
            return response

    def _record_usage(self, response: Any) -> None:
        usage = _field(response, "usage")
        if not usage:
            return
        for direction, field_name in (("input", "input_tokens"), ("output", "output_tokens")):
            tokens = int(_field(usage, field_name, 0) or 0)
            if tokens:
                METRICS.increment(
                    "supply_chain_llm_tokens_total",
                    {"model": self.config.model, "direction": direction},
                    tokens,
                )

    @staticmethod
    def _function_calls(response: Any) -> list[Any]:
        return [item for item in (_field(response, "output", []) or []) if _field(item, "type") == "function_call"]

    def run(self, order_id: str, planner_notes: str = "") -> dict:
        if not order_id or len(order_id) > 128:
            raise ValueError("order_id must be between 1 and 128 characters")
        if len(planner_notes) > 4000:
            raise ValueError("planner_notes must be 4000 characters or fewer")

        input_payload = json.dumps(
            {
                "task": "Investigate this order and propose the next allowed action.",
                "order_id": order_id,
                "planner_notes": planner_notes,
            },
            separators=(",", ":"),
        )
        called_tools: list[str] = []
        deterministic: dict | None = None
        policy_context: dict | None = None
        requested_order_loaded = False
        response_ids: list[str] = []

        with span(
            "llm.agent_run", order_id=order_id, provider=self.config.provider, model=self.config.model
        ) as run_span:
            response = self._create_response(input=input_payload)
            for turn_index in range(self.config.max_turns):
                response_id = _field(response, "id")
                if response_id:
                    response_ids.append(response_id)
                calls = self._function_calls(response)
                if not calls:
                    break
                if turn_index == self.config.max_turns - 1:
                    raise AgentRunError(f"Agent exceeded the maximum of {self.config.max_turns} model turns")

                tool_outputs = []
                for call in calls:
                    name = _field(call, "name")
                    call_id = _field(call, "call_id")
                    try:
                        arguments = json.loads(_field(call, "arguments", "{}"))
                        output = self.tools.call(name, arguments)
                        called_tools.append(name)
                        if name == "get_order" and arguments.get("order_id") == order_id:
                            requested_order_loaded = True
                        if name == "run_deterministic_triage":
                            deterministic = output["decision"]
                        if name == "get_action_policy":
                            policy_context = output["policy"]
                        serialized = {"ok": True, **output}
                    except Exception as exc:
                        serialized = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
                    tool_outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(serialized, separators=(",", ":")),
                        }
                    )
                response = self._create_response(previous_response_id=response_id, input=tool_outputs)
            else:
                raise AgentRunError(f"Agent exceeded the maximum of {self.config.max_turns} model turns")

            mandatory_tools = {"get_order", "run_deterministic_triage", "get_action_policy"}
            missing_tools = mandatory_tools.difference(called_tools)
            if missing_tools or deterministic is None or policy_context is None:
                raise AgentRunError(f"Agent did not call mandatory tools: {', '.join(sorted(missing_tools))}")
            if not requested_order_loaded:
                raise ProposalGuardrailError("Model did not load the requested order")
            output_text = _output_text(response)
            try:
                proposal = json.loads(output_text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise AgentRunError("Model did not return a valid structured proposal") from exc

            policy = self.tools.policy_for(deterministic["exception_type"], deterministic["severity"])
            if deterministic["order_id"] != order_id:
                raise ProposalGuardrailError("Model investigated a different order than the requested order")
            if policy_context["exception_type"] != deterministic["exception_type"] or policy_context[
                "severity"
            ] != deterministic["severity"]:
                raise ProposalGuardrailError("Model retrieved policy for a different exception or severity")
            try:
                validate_proposal(proposal, deterministic, policy)
            except ProposalGuardrailError:
                METRICS.increment(
                    "supply_chain_llm_proposals_total",
                    {"action_code": proposal.get("action_code", "UNKNOWN"), "status": "rejected"},
                )
                raise
            audit_event = record_agentic_decision(
                proposal,
                model=self.config.model,
                response_id=_field(response, "id"),
                deterministic_decision_id=deterministic["decision_id"],
            )
            result = {
                **proposal,
                "decision_id": audit_event["decision_id"],
                "deterministic_decision_id": deterministic["decision_id"],
                "policy_version": deterministic["policy_version"],
                "agent_mode": "llm_tool_calling",
                "provider": self.config.provider,
                "model": self.config.model,
                "response_id": _field(response, "id"),
                "tools_called": called_tools,
            }
            run_span.set_attribute("decision_id", result["decision_id"])
            run_span.set_attribute("action_code", result["action_code"])
            run_span.set_attribute("tool_call_count", len(called_tools))
            log_event(
                logging.INFO,
                "llm.agent_completed",
                provider=self.config.provider,
                order_id=order_id,
                action_code=result["action_code"],
                tool_call_count=len(called_tools),
                response_count=len(response_ids),
            )
            return result


__all__ = [
    "AgentConfig",
    "AgentConfigurationError",
    "AgentRunError",
    "HuggingFaceResponseAgent",
    "HuggingFaceResponsesClient",
    "ProposalGuardrailError",
]
