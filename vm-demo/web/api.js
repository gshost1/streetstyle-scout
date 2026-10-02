// ScoutAPI — the only thing the UI talks to.
//
// Two modes:
//   fixture  — answers from window.FIXTURES, in-page, with simulated latency.
//              No sponsor service is ever marked "ok" in this mode.
//   live     — calls the backend at baseUrl (see API.md). Service status comes
//              only from the backend's `meta.services` on successful responses.
//
// Sponsor status is never inferred. A service is "ok" only when a real response
// said so. CoreWeave is "unverified" until someone confirms the hosting.

window.ScoutAPI = (() => {
  const SERVICES = ["vast", "yolo", "cosmos", "wandb"];
  const SERVICE_LABELS = {
    vast: "VAST search",
    yolo: "YOLO person detection",
    cosmos: "Cosmos clothing analysis",
    wandb: "W&B request logging",
    coreweave: "CoreWeave hosting",
  };

  const state = {
    mode: "live", // "fixture" | "live"
    baseUrl: "/api",
    services: freshServices(),
    sim: { slow: false, error: false, empty: false }, // fixture-only
    log: [],
    listeners: new Set(),
  };

  function freshServices() {
    const s = {};
    for (const k of SERVICES) s[k] = { state: "not_connected", detail: "No call made yet", lastCall: null };
    s.coreweave = { state: "unverified", detail: "Hosting not confirmed. Not shown in credits.", lastCall: null };
    return s;
  }

  function emit() {
    for (const fn of state.listeners) fn(snapshot());
  }

  function snapshot() {
    return {
      mode: state.mode,
      baseUrl: state.baseUrl,
      services: JSON.parse(JSON.stringify(state.services)),
      labels: SERVICE_LABELS,
      sim: { ...state.sim },
      log: state.log.slice(-12).reverse(),
    };
  }

  function record(entry) {
    state.log.push({ t: Date.now(), ...entry });
    if (state.log.length > 50) state.log.shift();
  }

  // Merge backend-reported service outcomes. Only accepts known states.
  function applyServices(reported) {
    if (!reported || typeof reported !== "object") return;
    for (const k of SERVICES) {
      const r = reported[k];
      if (!r) continue;
      const reportedState = r.state || (typeof r.ok === "boolean" ? (r.ok ? "ok" : "error") : null);
      if (!["ok", "error", "not_connected"].includes(reportedState)) continue;
      state.services[k] = {
        state: reportedState,
        detail: r.detail || (reportedState === "ok" ? "Responded" : ""),
        ms: r.ms,
        lastCall: Date.now(),
      };
    }
  }

  function configure({ mode, baseUrl }) {
    if (mode && mode !== state.mode) {
      state.mode = mode;
      state.services = freshServices();
    }
    if (baseUrl) state.baseUrl = baseUrl.replace(/\/$/, "");
    emit();
  }

  function simulate(key, value) {
    if (key in state.sim) state.sim[key] = !!value;
    emit();
  }

  function onChange(fn) {
    state.listeners.add(fn);
    return () => state.listeners.delete(fn);
  }

  // ---------- fixture mode ----------

  const SYNONYMS = {
    bright: ["bright", "lime", "orange", "pink", "yellow", "red"],
    jacket: ["jacket", "coat", "parka", "windbreaker", "bomber"],
    jackets: ["jacket", "coat", "parka", "windbreaker", "bomber"],
    coat: ["coat", "parka", "trench"],
    bag: ["bag", "backpack", "tote"],
    bags: ["bag", "backpack", "tote"],
    backpack: ["backpack"],
    backpacks: ["backpack"],
    hat: ["beanie", "helmet", "hat"],
    hats: ["beanie", "helmet", "hat"],
    shoes: ["sneakers", "boots"],
    green: ["green", "olive", "lime"],
    blue: ["blue", "denim"],
    rain: ["rain", "umbrella", "trench"],
  };
  const STOP = new Set(["find", "show", "me", "a", "an", "the", "and", "or", "with", "in", "of", "people", "person", "wearing", "someone", "anyone", "any"]);

  function tokens(q) {
    return q.toLowerCase().replace(/[^a-z0-9\s-]/g, " ").split(/\s+/).filter((t) => t && !STOP.has(t));
  }

  function matchDetail(token, detail) {
    const terms = SYNONYMS[token] || [token];
    const v = detail.value.toLowerCase();
    return terms.some((t) => v.includes(t));
  }

  function scoreMoment(m, toks) {
    let score = 0;
    const hits = [];
    const maybe = [];
    for (const tok of toks) {
      const o = m.observed.find((d) => matchDetail(tok, d));
      if (o) {
        score += 1;
        hits.push(o);
        continue;
      }
      const u = m.uncertain.find((d) => matchDetail(tok, d));
      if (u) {
        score += 0.4;
        maybe.push(u);
      }
    }
    if (!toks.length) score = 0.5; // empty query: browse
    return { score: toks.length ? score / toks.length : score, hits, maybe };
  }

  function explain(hits, maybe, toks) {
    const parts = [];
    if (hits.length) parts.push("Matched " + hits.map((h) => `“${h.value}” (${h.kind})`).join(", "));
    if (maybe.length) parts.push("Possible match on " + maybe.map((h) => `“${h.value}”`).join(", ") + " — not clearly readable");
    if (!parts.length) return toks.length ? "" : "Browsing: no query terms";
    return parts.join(". ") + ".";
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function fixtureSearch(query, dataset) {
    const t0 = performance.now();
    await sleep(state.sim.slow ? 2800 : 550);

    if (state.sim.error) {
      state.sim.error = false; // one-shot
      record({ mode: "fixture", op: "search", ok: false, detail: "Simulated VAST error" });
      emit();
      const err = new Error("VAST search returned 503 (simulated)");
      err.code = "service_error";
      err.service = "vast";
      throw err;
    }

    const toks = tokens(query);
    let results = window.FIXTURES.moments
      .filter((m) => dataset === "both" || (dataset === "sf" && m.collection === "San Francisco") || (dataset === "to" && m.collection === "Toronto"))
      .map((m) => {
        const { score, hits, maybe } = scoreMoment(m, toks);
        return {
          ...m,
          score,
          match_reason: explain(hits, maybe, toks),
          provenance: { source: "fixture", detection: m.bounding_box ? "fixture" : null, analysis: "fixture" },
        };
      })
      .filter((m) => m.score > 0)
      .sort((a, b) => b.score - a.score);

    if (state.sim.empty) results = [];

    const ms = Math.round(performance.now() - t0);
    record({ mode: "fixture", op: "search", ok: true, detail: `${results.length} fixture results for “${query}”`, ms });
    emit();
    return { results, meta: { mode: "fixture", tookMs: ms, fixture: window.FIXTURES.label } };
  }

  async function fixtureMoment(id) {
    await sleep(120);
    const m = window.FIXTURES.moments.find((x) => x.id === id);
    if (!m) throw Object.assign(new Error("Moment not found"), { code: "not_found" });
    return { moment: { ...m, provenance: { source: "fixture", detection: m.bounding_box ? "fixture" : null, analysis: "fixture" } }, meta: { mode: "fixture" } };
  }

  // ---------- live mode ----------

  async function liveFetch(path, init, op) {
    const t0 = performance.now();
    let res;
    try {
      res = await fetch(state.baseUrl + path, init);
    } catch (e) {
      record({ mode: "live", op, ok: false, detail: `Network error: ${e.message}` });
      state.services.vast = { state: "error", detail: "Backend unreachable", lastCall: Date.now() };
      emit();
      throw Object.assign(new Error("Backend unreachable at " + state.baseUrl), { code: "network" });
    }
    const ms = Math.round(performance.now() - t0);
    let body = null;
    try {
      body = await res.json();
    } catch (_) {}
    if (!res.ok) {
      const msg = (body && (body.message || body.error)) || `HTTP ${res.status}`;
      record({ mode: "live", op, ok: false, detail: msg, ms });
      if (body && body.services) applyServices(body.services);
      else if (body && body.service && SERVICES.includes(body.service)) {
        state.services[body.service] = { state: "error", detail: msg, lastCall: Date.now() };
      }
      emit();
      throw Object.assign(new Error(msg), { code: body?.error || "service_error", service: body?.service });
    }
    if (body?.meta?.services) applyServices(body.meta.services);
    record({ mode: "live", op, ok: true, detail: `HTTP ${res.status}`, ms });
    emit();
    return body;
  }

  // ---------- public ----------

  async function search(query, dataset) {
    if (state.mode === "fixture") return fixtureSearch(query, dataset);
    return liveFetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, dataset }),
    }, "search");
  }

  async function getMoment(id) {
    if (state.mode === "fixture") return fixtureMoment(id);
    return liveFetch("/moments/" + encodeURIComponent(id), {}, "moment");
  }

  async function refreshStatus() {
    if (state.mode === "fixture") return snapshot();
    const body = await liveFetch("/status", {}, "status");
    if (body?.services) applyServices(body.services);
    return snapshot();
  }

  return { search, getMoment, refreshStatus, configure, simulate, onChange, snapshot };
})();
