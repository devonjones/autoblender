"""Stdout isolation. bpy prints to fd 1 (C printf) and to sys.stdout (Python),
both of which we redirect to stderr so they don't corrupt binary STL pipes.
Use the `out` text stream (or `out.buffer` for binary) for intentional output."""

import os
import sys

_real_stdout_fd = os.dup(1)
os.dup2(2, 1)
sys.stdout = sys.stderr

out = os.fdopen(_real_stdout_fd, "w", encoding="utf-8", closefd=False, buffering=1)
