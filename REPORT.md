# Design Report

## 1. Architecture

The system has six layers, each with a single responsibility:

**Portal** — a deliberately hostile legacy web app (table layouts, `<font>` tags, no IDs, no ARIA) that serves as the automation target. Built with FastAPI and Jinja2 server-rendered templates. Includes admin endpoints for fault injection (session expiry, error simulation).

**Agent** — the LLM-driven discovery layer. A `Surface` abstract base class defines `observe()` and `act()` methods that return typed models (`Observation`, `Action`, `ActionResult`). `PlaywrightSurface` implements this for browsers using DOM queries on interactive elements — not the deprecated accessibility snapshot API. The `LLMClient` wraps OpenRouter's OpenAI-compatible API with tool-calling (three tools: `perform_action`, `mark_complete`, `mark_stuck`). The discovery loop runs observe → decide → act with policy checks on every action.

**Artifact** — the recorded capability. Pydantic models define the schema (see Section 2). The emitter converts a discovery run into an artifact, detecting parameterized values and building locator strategies. The store is simple JSON file I/O — artifacts are git-tracked and human-reviewable.

**Replay** — the deterministic executor. Given an artifact and input params, it replays each step using the locator strategy (primary + fallbacks), resolves parameterized values, verifies checkpoints, and extracts outputs from page text. Returns a three-way `ReplayOutcome` (see Section 3). Zero LLM calls.

**Policy** — allowlist enforcement, action risk classification (safe/read/write/irreversible), and PII redaction. Every action passes through this layer before execution in both discovery and replay.

**Escalation** — stuck detection and human handoff. A `SessionController` manages ownership of the live browser page (automation vs. human). On session expiry or unrecoverable failure, it pauses automation, presents the browser to the human, waits for resolution, then resumes the replay on the same session.

### Key trade-off

The Surface abstraction uses DOM queries (`querySelectorAll`) rather than Playwright's deprecated accessibility snapshot API. This is more robust for legacy HTML where elements lack ARIA attributes, and the pattern extends naturally to desktop apps where an equivalent query mechanism (UI Automation tree, accessibility APIs) replaces the DOM query.

### Why not a single monolith?

Each layer is independently testable and replaceable. Swapping the LLM provider required changing one file (`agent/llm.py`). Adding a desktop surface means implementing one class. The replay engine has no dependency on the LLM layer — that separation is the entire point of the system.

## 2. Artifact Schema

The `CapabilityArtifact` is the centerpiece of the system. It represents a reusable, parameterized workflow that an AI agent can invoke by name with typed arguments.

**Identity**: `artifact_id` (UUID), `name`, `version` (semver), `description`. The artifact is the contract between the agent that calls it and the replay engine that executes it.

**Contract**: `inputs` (typed `InputParameter` list with name, type, required flag, example, sensitive flag) and `outputs` (typed `OutputField` list with name, type, optional locator for extraction).

**Workflow**: ordered `steps`, each containing:
- `StepAction` with `action_type`, `locator` (see below), `value`, and parameterization flags (`is_parameterized`, `param_key`)
- `LocatorStrategy` with `primary` selector, ranked `fallbacks`, semantic `role` and `name`
- Optional `Checkpoint` for post-step verification (`url_contains`, `text_visible`, `element_visible`, `url_equals`)
- `risk_level` (safe/read/write/irreversible) and `is_critical` flag

**Locator strategy**: The primary selector uses the most stable attribute available — `name` attributes for inputs (`input[name="q"]`), `value` for submit buttons, text content for links. Fallbacks provide graceful degradation. This is deliberately not tied to CSS paths or XPaths, which break on legacy apps where the DOM structure changes but element names stay stable.

**Parameterization**: Steps where `is_parameterized=True` have their `value` resolved from input params at replay time. Step 5 (click result link) uses `__APP_ID__` as a placeholder in its locator text, resolved via `resolve_locator_text()` to the actual application ID.

**Metadata**: `tenant_id` and `base_artifact_id` support multi-tenant reuse (see Section 4). `status` (draft/approved/deprecated) supports approval workflows. `tags` enable capability discovery.

### Why this shape

The artifact is not a raw step list — it's a typed function contract. An AI agent can read the schema, understand what it does, what it needs, and what it returns, without reading the steps. The steps are the implementation; the inputs/outputs are the interface. This mirrors how APIs work — the contract matters more than the internals.

## 3. Determinism & Error Handling

**Replay determinism**: The engine executes each step sequentially using the artifact's locator strategy. No LLM reasoning, no decision-making. For each step: resolve parameterized values → try primary locator → try fallbacks → execute action → verify checkpoint → proceed or fail.

**Wait strategy**: 500ms pause after each action for page settlement. Playwright's built-in wait-for-selector handles dynamic content within 5-second timeouts per action.

**Three-way result contract** (the brief calls conflating these "the most common design mistake"):

- **success**: all steps passed, success checkpoint verified, outputs extracted. The caller gets a dict of typed outputs.
- **business_outcome**: a legitimate result that is not an error. "Application not found" is a valid answer, not a crash. Detected by matching page text against known outcome patterns (`not_found`, `access_denied`, `validation_error`, `session_expired`). The caller handles this as data, not an exception.
- **failure**: something broke and needs debugging. Includes `error_type` (locator_failed, checkpoint_failed, timeout, policy_violation, missing_parameter), `failed_step`, `expected` vs `observed`, and a screenshot of the final state. The caller can pinpoint exactly what went wrong.

**Error detection logic**: On step failure, the engine first checks whether the current page shows a business outcome before declaring a hard failure. This prevents "application not found" from surfacing as a `locator_failed` error when the real situation is that the search returned no results and there's no link to click.

**UI drift** (secondary concern per the brief): The locator fallback chain handles minor drift. If the primary selector fails (`input[name="q"]`), fallbacks try alternatives (`textarea[name="q"]`). If all fail, it's a hard failure with diagnostic details. For the stable enterprise UIs described in the brief, this is sufficient — the hard part is runtime conditions (validation errors, session expiry, not-found results), not layout drift.

## 4. Heterogeneity & Multi-Tenant

### Surface abstraction — how it extends

The `Surface` ABC defines the seam between "how we perceive and act on a surface" and "the recorded flow." The artifact schema stores actions and locators, not surface-specific implementation details.

To add a desktop application surface:

1. Implement `DesktopSurface(Surface)` with `observe()` returning `UIElement` models from the OS accessibility tree (UI Automation on Windows, AT-SPI on Linux) and `act()` sending OS-level input events.
2. Replace the DOM query in `observe()` with accessibility tree traversal. The `UIElement` model (role, name, value, ref) maps directly to accessibility tree nodes — the same data model works for both.
3. The `LocatorStrategy` gains a new selector type (e.g., `AutomationId` or accessibility path) in `primary`, with text-based fallbacks.
4. The replay engine does not change — it resolves locators and executes actions through the Surface interface regardless of what's behind it.

For a legacy web app with framesets or deeply nested iframes, the same `PlaywrightSurface` works — the DOM query reaches into iframes by default. The hostile portal already demonstrates this with nested tables and non-semantic markup.

### Multi-tenant reuse — how one artifact serves many institutions

The schema supports this through `tenant_id` and `base_artifact_id`:

- A **base artifact** (`tenant_id=""`) defines the canonical workflow for a vendor product (e.g., "LoanPro application lookup").
- A **tenant override** (`base_artifact_id=<base-id>`, `tenant_id="credit-union-42"`) inherits the base steps but overrides specific locators, URLs, or credential values that differ per institution.
- At runtime, artifact resolution loads the tenant-specific artifact if it exists, else falls back to the base.

**Drift detection**: Run the base artifact against each tenant's instance on a schedule. If checkpoint failures cluster on specific tenants, those need overrides. The replay result's `failed_step` + `expected` vs `observed` pinpoints exactly what drifted — no manual investigation needed.

This was not built — it's a storage and resolution pattern, not a runtime system. The schema fields exist and support it; the infrastructure (a resolution layer, a drift runner) is a deployment concern I would build next.

## 5. Escalation & Handoff

**Detection**: The `SessionController.detect_escalation_needed()` checks page text for known escalation patterns after a failed replay step: session expired, access denied, unknown error state. The escalation demo forces this by setting a 2-second session TTL.

**Control transfer model**: The `SessionController` manages a state machine with three states:

```
AUTOMATION → PAUSED → HUMAN → AUTOMATION
```

The critical invariant: the same `Page` object (same browser context, same cookies, same session state) is used throughout. No fresh browser, no context loss. When automation pauses, the human sees the exact page the automation was stuck on.

**Handoff flow**:
1. Replay fails → detect escalation needed → create `EscalationRequest` with context (reason, URL, step, screenshot)
2. `SessionController.escalate()` sets owner to HUMAN, prints context to terminal, blocks on `input()`
3. Human operates the live headed browser — logs in, dismisses dialogs, whatever is needed
4. Human presses Enter → owner returns to AUTOMATION → full replay retries on the now-fixed session
5. `HandoffRecord` captures timestamps, duration, resolution, and is saved as `escalation_log.json`

**What's mocked vs real**: The operator UI is a terminal `input()` prompt. In production, this would be a web console showing the live browser session via Chrome DevTools Protocol (CDP) or noVNC, with an intervention queue, operator assignment, and SLA tracking. The handoff mechanism itself — same-session pause/resume with recorded context and evidence — is real and working.

## 6. Safety

**Allowlist enforcement**: `PolicyConfig` defines allowed domains (`127.0.0.1`, `localhost`), allowed ports (`8080`), URL patterns, and blocked patterns (`/admin/*`). Blocked patterns are checked first. Every action in both discovery and replay passes through `check_url_allowed()` before execution.

**Risk classification**: Actions are classified into four levels based on element names:
- `safe` — navigation, waiting, scrolling
- `read` — clicking search, reading data (default for click/type)
- `write` — submitting forms, creating records (detected by keywords: submit, save, create, confirm)
- `irreversible` — deleting, transferring, closing accounts (detected by keywords: delete, transfer, finalize)

Write and irreversible actions require confirmation and are blocked by default in the policy.

**PII redaction**: `redact_text()` applies regex patterns to strip SSNs, emails, phone numbers, account numbers, and dates of birth before any text reaches logs or evidence files. `redact_dict()` recursively redacts values under sensitive keys (password, token, SSN, credit_card). Credentials in artifacts are fixed values (`"agent"`), never exposed as input parameters that callers would supply.

**Limitations I'm aware of**: The risk classifier uses keyword heuristics. A button labeled "Confirm Order" is correctly classified as `write`, but a button labeled "Next" that happens to submit a transfer would be classified as `read`. In production, the artifact's `risk_level` field would be manually reviewed and corrected during the approval process (draft → approved) before the artifact runs unattended.

## 7. Cuts

**What was deliberately cut and why:**

- **Emitter checkpoint logic**: The auto-emitter (`artifact/emitter.py`) uses pre-action URLs for checkpoints instead of post-action URLs. The shipped artifact was hand-crafted for correctness. Fix: capture page state after each action during discovery, not before.
- **Output extraction locators**: Outputs are extracted from page text using heuristic string matching, not element-specific locators. Works for the demo but would be fragile on pages with ambiguous layout. Fix: record which DOM elements contain each output during discovery.
- **Step-level resume**: After human handoff, the replay retries from step 0 rather than resuming from the failed step. The session state (human logged in) carries over, so it works, but it's wasteful. Fix: track which steps completed and resume from the failure point.
- **Operator console**: The handoff uses `input()` in the terminal. Fix: web UI with live session streaming via CDP, intervention queue, operator assignment.
- **Desktop surface**: The `Surface` ABC supports it; no implementation exists. Fix: implement `DesktopSurface` using `pywinauto` or `pyatom` for Windows/macOS accessibility trees.
- **Multi-tenant resolution**: The schema has `tenant_id` and `base_artifact_id` fields; no resolution logic exists. Fix: artifact store returns the most specific match for a given tenant, falling back to the base.
- **Approval workflow**: The `status` field exists but nothing enforces it. Fix: gate unattended replay on `status == "approved"`.
- **Multi-run stability scoring**: No stability metric. Fix: replay N times, compute success rate, gate deployment on threshold.

**What I would build next with more time (in priority order):**

1. Step-level resume after escalation
2. Output extraction with recorded locators
3. Operator console with live session streaming
4. Multi-tenant artifact resolution with drift detection
5. Approval workflow gating unattended execution
---

*— Datta Sai Palaparthi*