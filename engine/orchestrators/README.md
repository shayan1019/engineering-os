# Ruflo adapter (ADAPTER_ONLY)

Ruflo is available as a Claude Code plugin, not as a standalone CLI binary.
EOS cannot probe or execute it as a generic orchestrator provider.

## Status: ADAPTER_ONLY

- No `ruflo` binary on PATH.
- No `eos-orchestrator/1` execution contract implemented for Ruflo.
- Ruflo's agents are Claude Code subagents, not EOS specialists.

## To enable Ruflo integration (future work)

1. Implement a Ruflo-specific worker that translates Ruflo's MCP tool calls
   into the `eos-orchestrator/1` protocol.
2. Register the worker in `ORCHESTRATORS.json` with `execution.protocol: eos-orchestrator/1`.
3. Ensure Ruflo's agents are constrained to EOS's nine specialist roles.
4. Run a real nested delegation test in a disposable sandbox.

## Generic adapter template

See `engine/orchestrators/generic_worker.py` for the protocol implementation template.
