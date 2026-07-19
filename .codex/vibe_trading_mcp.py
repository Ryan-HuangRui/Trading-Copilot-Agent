"""Run Vibe-Trading's stdio MCP server under Trading Copilot policy.

The upstream stdio launcher enables shell tools and every bundled swarm preset.
This adapter keeps shell tools disabled, moves swarm state into this repository,
and exposes only the repo-owned research preset. Trading connector tools remain
excluded by the MCP allowlist in config.toml.
"""

import json
import os
from pathlib import Path
import sys

from fastmcp import Context


TCA_ROOT = Path(__file__).resolve().parents[1]
VIBE_ROOT = TCA_ROOT.parent / "Vibe-Trading"
VIBE_AGENT_DIR = VIBE_ROOT / "agent"
SWARM_RUNTIME_ROOT = TCA_ROOT / "runtime" / "vibe-swarm"
SWARM_RUNS_ROOT = SWARM_RUNTIME_ROOT / "runs"
SWARM_PRESETS_DIR = Path(__file__).resolve().parent / "vibe_swarm_presets"
ALLOWED_SWARM_PRESETS = frozenset({"tca_research_review"})
DEFAULT_SWARM_PROVIDER = "openai-codex"
DEFAULT_SWARM_MODEL = "openai-codex/gpt-5.6-sol"
EFFECTIVE_SWARM_MODEL = "gpt-5.6-sol"
DEFAULT_SWARM_REASONING_EFFORT = "medium"

# Apply repo-owned defaults before importing the Vibe runtime. Explicit
# deployment environment values may still override these defaults.
os.environ.setdefault("LANGCHAIN_PROVIDER", DEFAULT_SWARM_PROVIDER)
os.environ.setdefault("LANGCHAIN_MODEL_NAME", DEFAULT_SWARM_MODEL)
os.environ.setdefault("LANGCHAIN_REASONING_EFFORT", DEFAULT_SWARM_REASONING_EFFORT)

sys.path.insert(0, str(VIBE_AGENT_DIR))

import mcp_server  # noqa: E402
from src.memory import persistent as persistent_memory  # noqa: E402
from src.swarm import presets as swarm_presets  # noqa: E402
from src.swarm import store as swarm_store  # noqa: E402


SWARM_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
(SWARM_RUNTIME_ROOT / "memory").mkdir(parents=True, exist_ok=True)
SWARM_PRESETS_DIR.mkdir(parents=True, exist_ok=True)

# Keep all Vibe state reachable by this repo-only MCP inside ignored runtime
# paths, while leaving the upstream checkout untouched.
persistent_memory.MEMORY_BASE = SWARM_RUNTIME_ROOT / "memory"
swarm_presets.USER_PRESETS_DIR = SWARM_PRESETS_DIR
swarm_store.swarm_runs_root = lambda: SWARM_RUNS_ROOT


def _swarm_policy_error(preset_name: str) -> str:
    return json.dumps(
        {
            "status": "error",
            "error_type": "policy",
            "error": f"swarm preset is not allowed by Trading Copilot policy: {preset_name}",
            "allowed_presets": sorted(ALLOWED_SWARM_PRESETS),
        },
        ensure_ascii=False,
        indent=2,
    )


_upstream_run_swarm = mcp_server.run_swarm
_upstream_retry_run = mcp_server.retry_run

mcp_server.mcp.local_provider.remove_tool("list_swarm_presets")
mcp_server.mcp.local_provider.remove_tool("run_swarm")
mcp_server.mcp.local_provider.remove_tool("retry_run")


@mcp_server.mcp.tool(name="list_swarm_presets")
def list_swarm_presets() -> str:
    """List only swarm presets approved for this Trading Copilot repository."""
    rows = [
        row
        for row in swarm_presets.list_presets()
        if row.get("name") in ALLOWED_SWARM_PRESETS
    ]
    for row in rows:
        row["policy"] = "independent_research_only"
        row["start_mode"] = "start_only"
        row["default_provider"] = DEFAULT_SWARM_PROVIDER
        row["default_model"] = DEFAULT_SWARM_MODEL
        row["effective_model"] = EFFECTIVE_SWARM_MODEL
        row["reasoning_effort"] = DEFAULT_SWARM_REASONING_EFFORT
    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp_server.mcp.tool(name="run_swarm")
async def run_swarm(
    preset_name: str,
    variables: dict[str, str],
    ctx: Context | None = None,
) -> str:
    """Start one approved research swarm and return its run id immediately.

    The result is research evidence only. It cannot update watchlists, trade
    plans, execution status, broker state, or the canonical rulebook.
    """
    normalized = preset_name.strip().lower().replace("-", "_")
    if normalized not in ALLOWED_SWARM_PRESETS:
        return _swarm_policy_error(preset_name)
    return await _upstream_run_swarm(
        preset_name=normalized,
        variables=variables,
        wait_seconds=0,
        start_only=True,
        ctx=ctx,
    )


@mcp_server.mcp.tool(name="retry_run")
def retry_run(run_id: str) -> str:
    """Retry a failed approved research swarm run."""
    store = mcp_server._get_swarm_store()
    try:
        run = store.load_run(run_id)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
    if run is None:
        return json.dumps(
            {"status": "error", "error": f"Run {run_id} not found"},
            ensure_ascii=False,
        )
    if run.preset_name not in ALLOWED_SWARM_PRESETS:
        return _swarm_policy_error(run.preset_name)
    return _upstream_retry_run(run_id)


mcp_server._include_shell_tools = False
mcp_server._registry = None
mcp_server._get_registry()


if __name__ == "__main__":
    mcp_server.mcp.run(transport="stdio")
