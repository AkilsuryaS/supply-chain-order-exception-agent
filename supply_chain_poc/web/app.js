const form = document.querySelector("#triage-form");
const llmButton = document.querySelector("#llm-button");
const rulesButton = document.querySelector("#rules-button");
const resultPanel = document.querySelector("#result-panel");
const views = {
  empty: document.querySelector("#empty-state"),
  loading: document.querySelector("#loading-state"),
  error: document.querySelector("#error-state"),
  decision: document.querySelector("#decision"),
};

function showView(name) {
  Object.entries(views).forEach(([key, node]) => node.classList.toggle("hidden", key !== name));
  resultPanel.setAttribute("aria-busy", name === "loading" ? "true" : "false");
}

function setBusy(busy, message) {
  llmButton.disabled = busy;
  rulesButton.disabled = busy;
  document.querySelector("#loading-message").textContent = message;
  if (busy) showView("loading");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { error: `The service returned HTTP ${response.status}.` };
  }
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed with HTTP ${response.status}.`);
    error.status = response.status;
    error.code = payload.code;
    error.retryable = Boolean(payload.retryable);
    throw error;
  }
  return payload;
}

function replaceList(selector, values) {
  const list = document.querySelector(selector);
  list.replaceChildren();
  (values?.length ? values : ["No supporting evidence was returned."]).forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    list.appendChild(item);
  });
}

function renderDecision(result, mode) {
  const exceptionType = result.primary_exception || result.exception_type || "UNKNOWN";
  const actionCode = result.action_code || (result.exception_type === "NO_EXCEPTION" ? "NO_ACTION" : "POLICY_RECOMMENDATION");
  document.querySelector("#result-mode").textContent = mode;
  document.querySelector("#result-summary").textContent = result.summary || `${exceptionType.replaceAll("_", " ")} detected`;
  document.querySelector("#result-exception").textContent = exceptionType;
  document.querySelector("#result-action-code").textContent = actionCode;
  document.querySelector("#result-approval").textContent = result.requires_approval ? "Required" : "Not required";
  document.querySelector("#result-confidence").textContent = `${Math.round((result.confidence ?? 1) * 100)}%`;
  document.querySelector("#result-action").textContent = result.recommended_action || "No recommendation returned.";
  replaceList("#result-evidence", result.evidence_used || result.evidence);
  document.querySelector("#result-decision-id").textContent = result.decision_id || "n.a.";
  document.querySelector("#result-policy").textContent = result.policy_version || "n.a.";
  document.querySelector("#result-model").textContent = result.model || "Deterministic rules";
  document.querySelector("#result-tools").textContent = result.tools_called?.join(", ") || "Rule engine";
  const severity = document.querySelector("#result-severity");
  severity.textContent = result.severity || "UNKNOWN";
  severity.className = `severity ${(result.severity || "").toLowerCase()}`;
  showView("decision");
}

function renderError(error) {
  const configurationError = error.code === "configuration_error";
  const providerUnavailable = error.code === "provider_unavailable";
  document.querySelector("#error-title").textContent = configurationError
    ? "Hugging Face token required"
    : providerUnavailable
      ? "Hugging Face is temporarily unavailable"
      : "Unable to run investigation";
  document.querySelector("#error-message").textContent = error.message;
  document.querySelector("#error-hint").classList.toggle("hidden", !configurationError);
  showView("error");
}

async function runLlm(event) {
  event.preventDefault();
  setBusy(true, "The LLM is selecting tools, gathering evidence, and checking policy.");
  try {
    const token = document.querySelector("#hf-token").value.trim();
    const headers = { "Content-Type": "application/json" };
    if (token) headers["X-HF-Token"] = token;
    const result = await requestJson("/agent/llm-triage", {
      method: "POST",
      headers,
      body: JSON.stringify({
        order_id: document.querySelector("#order-id").value.trim(),
        planner_notes: document.querySelector("#planner-notes").value.trim(),
      }),
    });
    renderDecision(result, "Hugging Face agent result");
  } catch (error) {
    renderError(error);
  } finally {
    setBusy(false, "The agent is gathering evidence and checking policy.");
  }
}

async function runRules() {
  setBusy(true, "The rule engine is evaluating order fields and policy thresholds.");
  try {
    const orderId = encodeURIComponent(document.querySelector("#order-id").value.trim());
    const order = await requestJson(`/erp/orders/${orderId}`);
    const result = await requestJson("/agent/triage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(order),
    });
    renderDecision(result, "Deterministic baseline");
  } catch (error) {
    renderError(error);
  } finally {
    setBusy(false, "The agent is gathering evidence and checking policy.");
  }
}

async function checkService() {
  const dot = document.querySelector("#status-dot");
  const label = document.querySelector("#service-status");
  try {
    const ready = await requestJson("/ready");
    dot.className = "status-dot online";
    label.textContent = `${ready.order_count} orders ready`;
  } catch {
    dot.className = "status-dot offline";
    label.textContent = "Service unavailable";
  }
}

form.addEventListener("submit", runLlm);
rulesButton.addEventListener("click", runRules);
checkService();
