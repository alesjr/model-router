# Context routing design

## Intent

Let each routed task choose how much parent-session context a worker receives, while making selected context explicit and testable.

## Goals and constraints

- Support `all`, `relevant`, and `none` context modes.
- Keep task context visible in the JSON contract and CLI dry-run prompt so tests can assert exactly what a worker receives.
- Native dispatch maps `all` to `fork_turns="all"`; `relevant` and `none` use `fork_turns="none"` and include only explicitly selected context.
- The CLI cannot retrieve a native parent session. `all` therefore cannot be faithfully executed through `router.py run` and must be rejected there.
- Default omitted `context_mode` to `relevant`, per updated user direction.
- Keep `context` optional: an empty list means no extra parent-session text was selected for a self-contained task; populate it whenever relevant session context exists.

## Approaches

- **Native fork only:** simple, but cannot represent selected context or make it visible in CLI tests.
- **Explicit context in task contract (chosen):** add mode and selected context to JSON; native dispatch can still fork all history when requested, while CLI prompts expose selected context. This makes boundaries testable and preserves existing contracts.
- **Automatic history summarization:** not supported by available dispatch schema; would require a separate context provider.

## Design

Add optional `context_mode` and `context` fields to task contracts. `context_mode` accepts `all`, `relevant`, or `none`; omitted means `relevant`. `context` defaults to an empty list, including for `relevant`; non-empty entries must be non-empty strings. `all` and `none` reject non-empty context entries. Include selected entries in `_build_prompt` and dry-run output. Document native dispatch mapping and CLI limitation. Tests cover validation, prompt content, and unsupported CLI execution for `all`.

## Acceptance

Existing contracts keep working. Invalid mode/context combinations fail with useful validation errors. Dry-run output exposes exactly selected context. Native instructions state how to apply each mode.
