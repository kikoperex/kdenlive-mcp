#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""Zero-install launcher for the kdenlive-mcp package.

Keeps `python3 server.py` working from any cwd (clone, tests, pi registration)
while the actual implementation lives in the `kdenlive_mcp` package.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kdenlive_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(0)
