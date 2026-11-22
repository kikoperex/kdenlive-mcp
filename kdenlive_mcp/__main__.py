# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""Run the MCP server with ``python3 -m kdenlive_mcp``."""

import sys

from .server import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(0)
