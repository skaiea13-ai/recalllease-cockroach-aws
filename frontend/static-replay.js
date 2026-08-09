(() => {
  "use strict";

  const sessionUseLimit = 8;
  let fixture = null;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function uuid() {
    return crypto.randomUUID();
  }

  function randomToken() {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  async function sha256(value) {
    const encoded = new TextEncoder().encode(value);
    const digest = await crypto.subtle.digest("SHA-256", encoded);
    return Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
  }

  async function memoryRecord({
    tenantId,
    kind,
    effect,
    subject,
    content,
    source,
    validFrom,
    validUntil = null,
    supersedesId = null,
    createdAt,
  }) {
    return {
      id: uuid(),
      tenant_id: tenantId,
      kind,
      effect,
      subject,
      content,
      source,
      valid_from: validFrom,
      valid_until: validUntil,
      supersedes_id: supersedesId,
      status: "active",
      content_sha256: await sha256(content),
      created_at: createdAt,
    };
  }

  async function createFixture() {
    const createdAt = new Date();
    const expiresAt = new Date(createdAt.getTime() + 2 * 60 * 60 * 1000);
    const tenantId = `replay-${uuid()}`;
    const permission = await memoryRecord({
      tenantId,
      kind: "permission",
      effect: "allow",
      subject: "Public weekly status publishing",
      content:
        "The agent may publish the weekly project status after it removes credentials, private paths, and personal phone numbers.",
      source: "demo-user",
      validFrom: createdAt.toISOString(),
      validUntil: expiresAt.toISOString(),
      createdAt: createdAt.toISOString(),
    });
    const context = await memoryRecord({
      tenantId,
      kind: "fact",
      effect: "context",
      subject: "Status report audience",
      content: "The weekly status is intended for a public project page.",
      source: "demo-user",
      validFrom: createdAt.toISOString(),
      createdAt: createdAt.toISOString(),
    });
    const session = {
      tenant_id: tenantId,
      token: randomToken(),
      expires_at: expiresAt.toISOString(),
      uses_remaining: sessionUseLimit,
      initial_permission_id: permission.id,
    };
    fixture = {
      session,
      memories: [permission, context],
      receipts: [],
      usesRemaining: sessionUseLimit,
    };
    return clone(session);
  }

  function assertSessionPath(path) {
    if (!fixture || !path.includes(fixture.session.tenant_id)) {
      throw new Error("The browser-only replay session is unavailable.");
    }
    fixture.usesRemaining -= 1;
    if (fixture.usesRemaining < 0) {
      throw new Error("The browser-only replay session budget is exhausted.");
    }
  }

  function state() {
    return clone({
      tenant_id: fixture.session.tenant_id,
      uses_remaining: fixture.usesRemaining,
      memories: fixture.memories,
      receipts: fixture.receipts,
    });
  }

  async function addMemory(options) {
    const payload = JSON.parse(options.body || "{}");
    const createdAt = new Date().toISOString();
    const superseded = fixture.memories.find(
      (memory) => memory.id === payload.supersedes_id && memory.status === "active",
    );
    if (!superseded) {
      throw new Error("The replay could not find the active memory to supersede.");
    }
    superseded.status = "superseded";
    const record = await memoryRecord({
      tenantId: fixture.session.tenant_id,
      kind: payload.kind,
      effect: payload.effect,
      subject: payload.subject,
      content: payload.content,
      source: payload.source,
      validFrom: payload.valid_from,
      validUntil: payload.valid_until || null,
      supersedesId: payload.supersedes_id,
      createdAt,
    });
    fixture.memories.push(record);
    return clone(record);
  }

  async function evaluateAction(options) {
    const payload = JSON.parse(options.body || "{}");
    const denial = fixture.memories.find(
      (memory) => memory.effect === "deny" && memory.status === "active",
    );
    const context = fixture.memories.find(
      (memory) => memory.effect === "context" && memory.status === "active",
    );
    if (!denial || !context) {
      throw new Error("The replay fixture is missing active decision evidence.");
    }

    const createdAt = new Date().toISOString();
    const query = `${payload.action}. ${payload.intent}`;
    const recalledMemoryIds = [denial.id, context.id];
    const memorySetDigest = await sha256(
      JSON.stringify(
        [denial, context].map((memory) => ({
          id: memory.id,
          status: memory.status,
          content_sha256: memory.content_sha256,
        })),
      ),
    );
    const canonical = {
      id: uuid(),
      tenant_id: fixture.session.tenant_id,
      action: payload.action,
      intent: payload.intent,
      decision: "deny",
      reason: "Blocked by active memory: Public weekly status publishing.",
      recalled_memory_ids: recalledMemoryIds,
      agent_instance_id: `agent-replay-${uuid().slice(0, 8)}`,
      retrieval_query_sha256: await sha256(query),
      memory_set_digest_sha256: memorySetDigest,
      created_at: createdAt,
      s3_key: null,
    };
    const receipt = {
      ...canonical,
      digest_sha256: await sha256(JSON.stringify(canonical)),
    };
    fixture.receipts.push(receipt);
    return clone(receipt);
  }

  async function request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    if (path === "/health" && method === "GET") {
      return {
        status: "ok",
        environment: "static-replay",
        memory_backend: "static-replay",
        embedding_backend: "deterministic",
      };
    }
    if (path === "/api/demo/sessions" && method === "POST") {
      return createFixture();
    }

    assertSessionPath(path);
    if (method === "GET" && path === `/api/demo/sessions/${fixture.session.tenant_id}`) {
      return state();
    }
    if (method === "POST" && path.endsWith("/memories")) {
      return addMemory(options);
    }
    if (method === "POST" && path.endsWith("/actions")) {
      return evaluateAction(options);
    }
    throw new Error("The browser-only replay does not implement this request.");
  }

  window.RecallLeaseStaticReplay = Object.freeze({ request });
})();
