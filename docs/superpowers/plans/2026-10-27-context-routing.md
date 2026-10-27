# Context Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let routed tasks declare full, selected, or no parent-session context, with selected context visible to CLI and tests.

**Architecture:** Extend the existing JSON task contract and prompt builder. Native dispatch maps `all` to `fork_turns="all"`; other modes use `fork_turns="none"`. CLI rejects execution of `all`, since it cannot retrieve parent-session history.

**Tech Stack:** Python 3.11 standard library, unittest, Markdown, TOML.

**Spec:** `docs/superpowers/specs/2026-10-27-context-routing-design.md`

## Global Constraints

- Keep current contracts compatible; omitted context mode means `none`.
- Selected context must be explicit, non-empty text entries in the task contract.
- Test authoring and execution use `economy` / `low` and separate contracts.
- Implementation edits production/docs only and does not run tests.
- CLI must not claim to preserve native session history.

## Review Focus

- Unknown or empty modes: reject; test invalid mode.
- `relevant` with absent, empty, or blank context: reject; test each invalid form.
- `all` or `none` with explicit context entries: reject; test invalid combinations.
- Dry-run prompt leaks unselected context: assert only selected entries appear.
- CLI `run` on `all` would lose session history: reject before subprocess; test with subprocess unavailable/mock-free validation.

---

### Task 1: Context contract tests

Resolved worker contract:

```json
{"id":"context-contract-tests","task_type":"test_authoring","complexity":"medium","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Test authoring always uses economy/low by policy.","instructions":"Add only unittest cases in tests/test_router.py for the context contract, selected prompt contents, and rejection of CLI execution in all mode. Do not run tests, alter production, or delegate.","acceptance_criteria":["Omitted mode resolves to none.","Relevant requires non-empty context list; all and none reject context.","Invalid mode is rejected.","Dry-run prompt exposes only explicitly selected context entries.","CLI execution in all mode is rejected before subprocess invocation."]}
```

**Files:**
- Modify: `tests/test_router.py`

**Interfaces:**
- Contract fields: optional `context_mode` (`all|relevant|none`) and `context` (list of non-empty strings).
- `relevant` requires non-empty context; `all` and `none` do not accept context.
- Omitted mode resolves as `none`.
- CLI prompt includes selected context; CLI execution rejects `all`.

- [x] Add tests for defaults, valid `relevant`, invalid modes/combinations, selected context in prompt, and `all` execution rejection.
- [x] Run only the new tests and confirm expected failures before implementation: 17/19 passed; the two new unsupported-context behaviors failed as expected.

RED worker contract:

```json
{"id":"context-router-red","task_type":"test_execution","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Test execution always uses economy/low by policy.","instructions":"Run focused router tests to observe RED for the newly authored context tests. Do not alter files or perform operations beyond test execution.","acceptance_criteria":["New context tests fail because context contract behavior is absent.","Report exact failure reasons and any pre-existing failures.","No files are modified."]}
```

### Task 2: Router implementation and docs

Resolved worker contract:

```json
{"id":"context-router-implementation","task_type":"implementation","complexity":"medium","profile":"standard","model":"gpt-6.1-sol","reasoning_effort":"medium","reason":"Contract validation, prompt construction, CLI restriction, and documentation span the router interface.","instructions":"Implement the approved context contract in model-router/scripts/router.py and document it in model-router/SKILL.md, model-router/references/integration.md, and README.md. Read existing tests, but do not write or run tests, lint, builds, or operations. Do not delegate.","acceptance_criteria":["Context fields validate as specified in the design.","Selected context is present in CLI prompt and not augmented with unselected history.","CLI run rejects all mode before starting subprocess.","Native dispatch mode mapping and CLI limitation are documented.","Existing contracts without context fields remain compatible."]}
```

**Files:**
- Modify: `model-router/scripts/router.py`
- Modify: `model-router/SKILL.md`
- Modify: `model-router/references/integration.md`
- Modify: `README.md`

**Interfaces:**
- `_validate_task` validates context fields.
- `resolve_task` supplies default `context_mode="none"`.
- `_build_prompt` emits only explicit selected context entries.
- `execute_task` rejects `context_mode="all"` before invoking CLI.

- [x] Implement exact contract behavior covered by tests; do not edit or execute tests.
- [x] Document native `fork_turns` mapping, explicit selection for `relevant`, and CLI limitation for `all`.

### Task 3: Verify

Resolved worker contract:

```json
{"id":"context-router-verification","task_type":"test_execution","complexity":"medium","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Test execution always uses economy/low by policy.","instructions":"Run focused router tests and full repository test suite. Do not edit files or perform operations outside test execution. Report command output summary and unexpected failures.","acceptance_criteria":["New context tests pass.","Full test suite passes or failures are clearly reported.","No files are modified."]}
```

**Files:**
- Read/execute: `tests/test_router.py` and repository test suite.

- [x] Run focused router tests and full test suite; do not modify files.
- [x] Report results and any failures: 19/19 focused and 89/89 repository tests passed.

### Follow-up Task 4: Skill context scenario

Resolved worker contract:

```json
{"id":"context-skill-scenario","task_type":"test_authoring","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Test authoring always uses economy/low by policy.","instructions":"Extend tests/skill-scenarios.md with scenario coverage for context_mode relevant, all, and none, including native fork_turns mapping and CLI all-mode limitation. Do not run tests, edit production, or delegate.","acceptance_criteria":["Scenario gives relevant context plus unrelated conversation and requires only selected entries with fork_turns=none.","Rubric verifies all maps to fork_turns=all and none maps to fork_turns=none with empty context.","Rubric rejects CLI run for all mode because it cannot access parent session history.","Existing scenario cases and style remain intact."]}
```

Completed: scenario H added to `tests/skill-scenarios.md`; no executable tests run for this documentation-only scenario update.

### Follow-up Task 5: Default context mode to relevant

Updated design decision: omitted `context_mode` resolves to `relevant`; omitted `context` resolves to `[]`. Empty context means no extra parent-session text was selected for a self-contained task. The coordinator must include relevant session decisions explicitly when present. `none` remains an explicit opt-out.

Test authoring contract:

```json
{"id":"context-default-tests","task_type":"test_authoring","complexity":"medium","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Test authoring always uses economy/low by policy.","instructions":"Update tests/test_router.py and tests/skill-scenarios.md to specify relevant as the omitted default, allow empty selected context, and preserve explicit none behavior. Do not run tests, change production, or delegate.","acceptance_criteria":["Omitted context_mode resolves to relevant and omitted context resolves to empty list.","Explicit relevant with empty context is accepted for self-contained tasks.","Explicit none still resolves as none without selected context.","Skill scenario rubric says relevant is default and coordinator includes relevant session entries explicitly."]}
```

RED worker contract:

```json
{"id":"context-default-red","task_type":"test_execution","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Test execution always uses economy/low by policy.","instructions":"Run focused router tests to observe RED for default relevant and empty selected context. Do not alter files or perform operations beyond test execution.","acceptance_criteria":["New default assertions fail against current none default.","No files are modified."]}
```

Implementation contract:

```json
{"id":"context-default-implementation","task_type":"implementation","complexity":"medium","profile":"standard","model":"gpt-6.1-sol","reasoning_effort":"medium","reason":"Default contract behavior and its operational documentation require coordinated code and docs changes.","instructions":"Change omitted context_mode default to relevant while allowing empty context; update contract docs and examples. Do not write or run tests, lint, builds, or operations. Do not delegate.","acceptance_criteria":["resolve_task returns relevant when context_mode is omitted.","Empty context is allowed in relevant mode; malformed entries and invalid combinations remain rejected.","Relevant remains native fork_turns=none with explicit selection only.","README, skill, integration guide, design and plan state relevant as default and coordinator selection responsibility.","All mode remains native-only and CLI rejection remains intact."]}
```

GREEN worker contract:

```json
{"id":"context-default-green","task_type":"test_execution","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Test execution always uses economy/low by policy.","instructions":"Run focused router tests and full repository test suite after implementation. Do not edit files or perform operations outside test execution.","acceptance_criteria":["Focused router tests pass.","Full suite passes or failures are clearly reported.","No files are modified."]}
```

Completed: default behavior changed to `relevant`; focused suite passed 19/19 and full suite passed 89/89.

### Follow-up Task 6: Commit and push

Resolved operations contract:

```json
{"id":"push-context-routing","task_type":"operations","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Operations always use economy/low by policy.","instructions":"Stage only the eight context-routing files listed in the dispatch, create one Conventional Commit using per-command identity alesjr / 9462203+alesjr@users.noreply.github.com, and push main to origin. Verify remote is git@github-meu:alesjr/model-router.git. Do not change global config, force push, bypass hooks, or run tests. Stop if remote or personal authentication does not match.","acceptance_criteria":["README.md is included with the router, docs, plan/spec, and test changes.","One commit uses the requested personal author identity.","Commit is pushed to origin/main using github-meu/alesjr remote.","No other files are staged or changed by the operation."]}
```
