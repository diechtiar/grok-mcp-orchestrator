# Contributing

## Layout

- `peer_bus.py` — library + CLI
- `mcp_server.py` — stdio MCP (no third-party deps)
- `tests/` — stdlib `unittest`
- `scripts/smoke.sh` — CLI + hook + MCP smoke
- `hooks/` — Claude wake-drop consumer
- `docs/` — playbook and Claude hook notes

## Checks before push

```bash
python3 -m unittest discover -s tests -v
bash scripts/smoke.sh
```

CI runs both on push/PR to `main`.

## Version

Bump `PEER_BUS_VERSION` in `peer_bus.py`, `SERVER_INFO["version"]` in `mcp_server.py`,
`README` version line, and `CHANGELOG.md` in the same commit.

## Plans

`plan-*.md` is local-only (gitignored). Do not commit private plans.
