"""Run Vibe-Trading's stdio MCP server under Trading Copilot policy.

Vibe enables shell-capable tools by default for its native stdio launcher.
This adapter keeps the research/swarm interface available to Codex but prevents
those tools from reaching swarm workers. Trading connector tools are separately
excluded by the MCP allowlist in config.toml.
"""

from pathlib import Path
import sys


VIBE_AGENT_DIR = Path("/Volumes/personal_folder/repo/Vibe-Trading/agent")
sys.path.insert(0, str(VIBE_AGENT_DIR))

import mcp_server  # noqa: E402


mcp_server._include_shell_tools = False
mcp_server._registry = None
mcp_server._get_registry()
mcp_server.mcp.run(transport="stdio")
