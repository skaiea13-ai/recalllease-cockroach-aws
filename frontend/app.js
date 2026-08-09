(() => {
  "use strict";

  const action = "Publish the weekly status publicly";
  const intent =
    "Publish a sanitized weekly project status to the public project page.";
  const staticMode =
    document.querySelector('meta[name="recalllease-mode"]')?.content === "static-replay";
  const staticReplay = staticMode ? window.RecallLeaseStaticReplay : null;
  const loopbackCapability = staticMode ? null : readLoopbackCapability();

  let session = null;
  let backend = "in-memory";
  let embeddingBackend = "deterministic";
  let running = false;

  const elements = {
    environmentLabel: document.querySelector("#environment-label"),
    runState: document.querySelector("#run-state"),
    runButton: document.querySelector("#run-button"),
    resetButton: document.querySelector("#reset-button"),
    grantTime: document.querySelector("#grant-time"),
    revokeTime: document.querySelector("#revoke-time"),
    restartTime: document.querySelector("#restart-time"),
    agentId: document.querySelector("#agent-id"),
    revokeStep: document.querySelector('[data-step="revoke"]'),
    restartStep: document.querySelector('[data-step="restart"]'),
    evidenceList: document.querySelector("#evidence-list"),
    evidenceCount: document.querySelector("#evidence-count"),
    decisionResult: document.querySelector("#decision-result"),
    decisionWord: document.querySelector("#decision-word"),
    decisionReason: document.querySelector("#decision-reason"),
    receiptDecision: document.querySelector("#receipt-decision"),
    receiptTime: document.querySelector("#receipt-time"),
    receiptAgent: document.querySelector("#receipt-agent"),
    receiptQueryHash: document.querySelector("#receipt-query-hash"),
    receiptMemoryHash: document.querySelector("#receipt-memory-hash"),
    receiptEvidenceCount: document.querySelector("#receipt-evidence-count"),
    receiptHash: document.querySelector("#receipt-hash"),
    apiSchemaLink: document.querySelector("#api-schema-link"),
    memoryProvider: document.querySelector("#memory-provider"),
    memoryProviderDetail: document.querySelector("#memory-provider-detail"),
    embeddingProvider: document.querySelector("#embedding-provider"),
    embeddingProviderDetail: document.querySelector("#embedding-provider-detail"),
  };

  function readLoopbackCapability() {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const capability = fragment.get("capability");
    if (capability) {
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}`,
      );
    }
    return capability;
  }

  async function api(path, options = {}) {
    if (staticMode) {
      if (!staticReplay || typeof staticReplay.request !== "function") {
        throw new Error("The browser-only replay adapter did not load.");
      }
      return staticReplay.request(path, options);
    }

    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-RecallLease-Client": "browser-v1",
        ...(loopbackCapability
          ? { "X-RecallLease-Loopback-Capability": loopbackCapability }
          : {}),
        ...(session ? { "X-Demo-Token": session.token } : {}),
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === "string") detail = payload.detail;
      } catch {
        // Keep the bounded status-only fallback.
      }
      throw new Error(detail);
    }
    return response.json();
  }

  function formatTime(value) {
    if (!value) return "—";
    return new Intl.DateTimeFormat("en-GB", {
      dateStyle: "medium",
      timeStyle: "medium",
      timeZone: "UTC",
    }).format(new Date(value));
  }

  function shortHash(value, size = 12) {
    if (!value || value.length <= size * 2 + 1) return value || "—";
    return `${value.slice(0, size)}…${value.slice(-size)}`;
  }

  function setBusy(value, label) {
    running = value;
    elements.runButton.disabled = value;
    elements.resetButton.disabled = value;
    elements.runState.textContent = label;
  }

  function resetVisualState() {
    elements.revokeStep.classList.add("is-pending");
    elements.restartStep.classList.add("is-pending");
    elements.revokeTime.textContent = "Not recorded";
    elements.restartTime.textContent = "Not started";
    elements.agentId.textContent = "No instance assigned";
    elements.evidenceList.replaceChildren(
      makeElement("div", "empty-evidence", "Run the replay to retrieve active policy memory."),
    );
    elements.evidenceCount.textContent = "0 records";
    elements.decisionResult.className = "decision-result is-idle";
    elements.decisionWord.textContent = "WAITING";
    elements.decisionReason.textContent = "No action has been evaluated yet.";
    elements.receiptDecision.className = "receipt-decision";
    elements.receiptDecision.textContent = "Pending";
    elements.receiptTime.textContent = "—";
    elements.receiptAgent.textContent = "—";
    elements.receiptQueryHash.textContent = "—";
    elements.receiptMemoryHash.textContent = "—";
    elements.receiptEvidenceCount.textContent = "0";
    elements.receiptHash.textContent = "Not generated";
  }

  function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text;
    return element;
  }

  function renderEvidence(memories) {
    const evidence = memories
      .filter((memory) => memory.effect === "allow" || memory.effect === "deny")
      .sort((left, right) => new Date(right.created_at) - new Date(left.created_at));

    elements.evidenceList.replaceChildren();
    evidence.forEach((memory, index) => {
      const row = document.createElement("article");
      row.className = `evidence-row evidence-row--${memory.effect}`;

      const rank = makeElement("div", "evidence-rank", String(index + 1));
      const stripe = makeElement("div", "evidence-stripe", "");
      const copy = makeElement("div", "evidence-copy", "");
      const title = document.createElement("strong");
      title.textContent =
        memory.effect === "deny" ? "Permission revoked" : "Permission recorded";
      const time = document.createElement("time");
      time.textContent = formatTime(memory.created_at);
      const description = document.createElement("p");
      description.textContent =
        memory.status === "superseded"
          ? "Superseded by the newer revocation."
          : memory.content;
      copy.append(title, time, description);

      const effect = makeElement("div", "evidence-effect", "");
      const effectLabel = document.createElement("span");
      effectLabel.textContent =
        memory.status === "superseded" ? "superseded" : memory.effect;
      const digest = document.createElement("code");
      digest.textContent = shortHash(memory.content_sha256, 8);
      effect.append(effectLabel, digest);
      row.append(rank, stripe, copy, effect);
      elements.evidenceList.append(row);
    });
    elements.evidenceCount.textContent = `${evidence.length} records`;
  }

  function renderReceipt(receipt) {
    const denied = receipt.decision === "deny";
    elements.decisionResult.className = `decision-result ${denied ? "is-denied" : ""}`;
    elements.decisionWord.textContent = denied ? "BLOCKED" : receipt.decision.toUpperCase();
    elements.decisionReason.textContent = receipt.reason;
    elements.receiptDecision.className = `receipt-decision ${denied ? "is-denied" : ""}`;
    elements.receiptDecision.textContent = denied ? "Blocked" : receipt.decision;
    elements.receiptTime.textContent = formatTime(receipt.created_at);
    elements.receiptAgent.textContent = receipt.agent_instance_id;
    elements.receiptQueryHash.textContent = shortHash(receipt.retrieval_query_sha256);
    elements.receiptMemoryHash.textContent = shortHash(receipt.memory_set_digest_sha256);
    elements.receiptEvidenceCount.textContent = String(receipt.recalled_memory_ids.length);
    elements.receiptHash.textContent = receipt.digest_sha256;
    elements.agentId.textContent = receipt.agent_instance_id;
    elements.restartTime.textContent = formatTime(receipt.created_at);
  }

  function renderProviders() {
    if (staticMode) {
      elements.memoryProvider.textContent = "Browser-only replay";
      elements.memoryProviderDetail.textContent = "Deterministic fixture; no database call";
      elements.embeddingProvider.textContent = "Recorded/local replay";
      elements.embeddingProviderDetail.textContent = "Client-side SHA-256; no AWS call";
      elements.environmentLabel.textContent =
        "Public replay: browser-only deterministic fixture; no cloud calls.";
      elements.apiSchemaLink.hidden = true;
      return;
    }

    const cloud = backend === "cockroachdb";
    const bedrock = cloud && embeddingBackend === "bedrock";
    elements.memoryProvider.textContent = cloud ? "CockroachDB Cloud" : "Local simulator";
    elements.memoryProviderDetail.textContent = cloud
      ? "Distributed vector index"
      : "Deterministic in-memory store";
    elements.embeddingProvider.textContent = bedrock
      ? "Amazon Bedrock"
      : cloud
        ? "Lambda-local embeddings"
        : "Offline embeddings";
    elements.embeddingProviderDetail.textContent = bedrock
      ? "Titan Text Embeddings V2 + S3 receipt"
      : cloud
        ? "Deterministic 1,024D vectors + S3 receipt"
        : "Feature hashing + local receipt";
    elements.environmentLabel.textContent = cloud
      ? bedrock
        ? "Live proof: CockroachDB retrieval, Bedrock embeddings, S3 receipts."
        : "Live proof: CockroachDB retrieval, Lambda-local embeddings, S3 receipts."
      : "Local proof mode: the same policy path with deterministic offline adapters.";
  }

  async function createSession() {
    setBusy(true, "Creating session");
    resetVisualState();
    const [health, created] = await Promise.all([
      api("/health"),
      api("/api/demo/sessions", { method: "POST", body: "{}" }),
    ]);
    backend = health.memory_backend;
    embeddingBackend = health.embedding_backend;
    session = created;
    const state = await api(`/api/demo/sessions/${session.tenant_id}`);
    const initialPermission = state.memories.find((memory) => memory.effect === "allow");
    elements.grantTime.textContent = formatTime(initialPermission?.created_at);
    renderProviders();
    setBusy(false, "Ready");
  }

  async function runReplay() {
    if (running || !session) return;
    try {
      setBusy(true, "Recording revocation");
      const revoked = await api(`/api/demo/sessions/${session.tenant_id}/memories`, {
        method: "POST",
        body: JSON.stringify({
          kind: "permission",
          effect: "deny",
          subject: "Public weekly status publishing",
          content:
            "The agent must not publish the weekly project status. This revocation supersedes every earlier publication permission.",
          source: "demo-user:policy-update",
          valid_from: new Date().toISOString(),
          supersedes_id: session.initial_permission_id,
        }),
      });
      elements.revokeStep.classList.remove("is-pending");
      elements.revokeTime.textContent = formatTime(revoked.created_at);

      setBusy(true, "Restarting agent");
      await new Promise((resolve) => window.setTimeout(resolve, 420));
      elements.restartStep.classList.remove("is-pending");

      setBusy(true, "Retrieving memory");
      const receipt = await api(`/api/demo/sessions/${session.tenant_id}/actions`, {
        method: "POST",
        body: JSON.stringify({ action, intent }),
      });
      const state = await api(`/api/demo/sessions/${session.tenant_id}`);
      renderEvidence(state.memories);
      renderReceipt(receipt);
      setBusy(false, receipt.decision === "deny" ? "Replay verified" : "Review required");
    } catch (error) {
      setBusy(false, "Replay failed");
      elements.decisionReason.textContent = error instanceof Error ? error.message : "Replay failed";
    }
  }

  elements.runButton.addEventListener("click", runReplay);
  elements.resetButton.addEventListener("click", async () => {
    try {
      await createSession();
    } catch (error) {
      setBusy(false, "Reset failed");
      elements.decisionReason.textContent = error instanceof Error ? error.message : "Reset failed";
    }
  });

  createSession().catch((error) => {
    setBusy(false, "Session failed");
    elements.environmentLabel.textContent = "The demo session could not be created.";
    elements.decisionReason.textContent = error instanceof Error ? error.message : "Session failed";
  });
})();
