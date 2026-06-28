"""ANSI 256-color terminal formatting shared by scan and tag."""

import sys

# Resets
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def _c(code: int) -> str:
    return f"\033[38;5;{code}m"


def colored(text: str, code: int, *, bold: bool = False, dim: bool = False) -> str:
    parts = [_c(code)]
    if bold:
        parts.append(_BOLD)
    if dim:
        parts.append(_DIM)
    parts.append(text)
    parts.append(_RESET)
    return "".join(parts)


def bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


def dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def clear_line() -> None:
    sys.stdout.write("\033[K")


def cursor_up(n: int) -> None:
    sys.stdout.write(f"\033[{n}A")


def clear_below() -> None:
    sys.stdout.write("\033[J")
