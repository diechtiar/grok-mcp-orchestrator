# plan — Claude wake hook vs CLI 2.1.238

Private. Do not stage/commit.

## DO (Rick latest, later mail wins)
Review hook; tighten Claude settings schema against this CLI; report; idle.

## Measured
- Claude Code 2.1.238
- Official hooks: UserPromptSubmit has **no matcher**; command default timeout 30s; stdin JSON includes `session_id` / `hook_event_name`
- Env injected into hook + stdio MCP: **CLAUDE_CODE_SESSION_ID** (not CLAUDE_SESSION_ID)
- UserPromptSubmit context: JSON `hookSpecificOutput.additionalContext` (plain stdout also works)
- Local working settings (`~/.claude/settings.json`): nested `"hooks": { "UserPromptSubmit": [ { "hooks": [ { type, command, timeout } ] } ] }` — no matcher
- peer_bus `_session_id()` still only GROK_SESSION_ID / CLAUDE_SESSION_ID

## Corrections in this branch
1. Hook reads stdin.session_id, then CLAUDE_CODE_SESSION_ID, then CLAUDE_SESSION_ID
2. Slug via same regex as peer_bus._slug
3. JSON additionalContext; always exit 0
4. Docs schema: timeout 5, env PEER_BUS_CLAUDE_WAKE, omit matcher, SessionStart matcher values if used
5. Do not change SECURITY table / trust model; report MCP CLAUDE_CODE_SESSION_ID gap

## NOT
no close, no merge, no vida
