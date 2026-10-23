"""Command-line entry point for the ai-memory-mcp distribution."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ai-memory-mcp",
        description="Run the local Markdown memory MCP server over stdio.",
    )
    parser.add_argument(
        "--vault",
        default=None,
        help="memory directory (defaults to ~/ai-memory or AI_MEMORY_DIR)",
    )
    args = parser.parse_args(argv)

    # The server resolves its storage directory during import, so set the
    # explicit CLI override first. Empty values are treated as no override;
    # this lets DSH pass an unset environment variable without selecting cwd.
    if args.vault and args.vault.strip():
        os.environ["AI_MEMORY_DIR"] = str(Path(args.vault).expanduser().resolve())

    from server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
