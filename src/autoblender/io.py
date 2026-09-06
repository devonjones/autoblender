import os
import sys
import shutil
import tempfile
from contextlib import contextmanager

from . import out


def _canonical(p):
    return os.path.realpath(os.path.expandvars(os.path.expanduser(p)))


def reject_same_path(input_arg, output_arg):
    if input_arg == "-" or output_arg == "-":
        return
    if _canonical(input_arg) == _canonical(output_arg):
        raise RuntimeError(
            f"input and output resolve to the same file: {input_arg!r}"
        )


@contextmanager
def input_stl_path(arg):
    if arg == "-":
        fd, path = tempfile.mkstemp(suffix=".stl")
        try:
            with os.fdopen(fd, "wb") as f:
                shutil.copyfileobj(sys.stdin.buffer, f)
            yield path
        finally:
            os.unlink(path)
    else:
        yield arg


@contextmanager
def output_stl_path(arg):
    if arg == "-":
        fd, path = tempfile.mkstemp(suffix=".stl")
        os.close(fd)
        try:
            yield path
            with open(path, "rb") as f:
                shutil.copyfileobj(f, out.buffer)
            out.buffer.flush()
        finally:
            os.unlink(path)
    else:
        yield arg
