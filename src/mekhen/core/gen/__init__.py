"""Baseline generators. Each writes `<store>/baseline/<name>.mmd`, starting with a header that
records the exact `mekhen gen ...` arguments, so the file can be checked for staleness later."""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass
from typing import Any, Callable

from mekhen.core.gen import imports
from mekhen.core.gen.base import GenError

__all__ = ["GENERATORS", "GenError", "Generator", "command_args", "options_for", "parse_command", "parser_for"]


@dataclass(frozen=True)
class Generator:
    kind: str
    help: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    generate: Callable[..., str]  # (root, options, command) -> Mermaid text, header included
    fingerprint: Callable[..., tuple]  # (root, options) -> cheap tuple that changes when inputs change
    default_name: str


GENERATORS: dict[str, Generator] = {
    "imports": Generator(
        "imports",
        "Python import graph, grouped by package (reads files with ast, never imports them)",
        imports.add_arguments,
        imports.generate,
        imports.fingerprint,
        "imports",
    ),
}


def parser_for(kind: str) -> argparse.ArgumentParser:
    gen = GENERATORS[kind]
    p = argparse.ArgumentParser(prog=f"mekhen gen {kind}", add_help=False, exit_on_error=False)
    p.add_argument("--name", default=gen.default_name)
    gen.add_arguments(p)
    return p


def options_for(kind: str, args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Config section [gen.<kind>] overridden by any flag given on the command line."""
    options = dict(config.get(kind, {}) or {})
    for key, value in vars(args).items():
        if value is not None:
            options[key] = value
    options.setdefault("name", GENERATORS[kind].default_name)
    return options


def command_args(kind: str, args: argparse.Namespace) -> str:
    """The `mekhen gen ...` arguments to record in the header: kind plus only the flags given."""
    parts = [kind]
    gen_parser = parser_for(kind)
    for action in gen_parser._actions:
        value = getattr(args, action.dest, None)
        if value is None or not action.option_strings or value == action.default:
            continue
        flag = action.option_strings[0]
        if isinstance(value, bool):
            parts.append(flag)
        elif isinstance(value, list):
            parts.extend(f"{flag}={shlex.quote(str(v))}" for v in value)
        else:
            parts.append(f"{flag}={shlex.quote(str(value))}")
    return " ".join(parts)


def parse_command(command: str) -> tuple[str, argparse.Namespace]:
    """Inverse of command_args, for header lines read back from a generated file."""
    try:
        words = shlex.split(command)
    except ValueError as e:
        raise GenError(f"unreadable generator command {command!r}: {e}") from e
    if not words or words[0] not in GENERATORS:
        raise GenError(f"unknown generator in header: {command!r}")
    kind = words[0]
    try:
        args, extra = parser_for(kind).parse_known_args(words[1:])
    except argparse.ArgumentError as e:
        raise GenError(f"bad generator arguments {command!r}: {e}") from e
    if extra:
        raise GenError(f"unknown generator arguments {extra!r}")
    return kind, args
