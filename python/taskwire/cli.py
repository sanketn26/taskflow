"""The ``taskwire`` command.

Only ``version`` works so far. The other subcommands are reserved so that later
phases fill them in without changing the entry point.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import taskwire

# subcommand -> (help text, phase that implements it)
_PLANNED = {
    "migrate": ("Install or upgrade the backend schema", 1),
    "check": ("Verify the backend meets the Taskwire contract", 1),
    "worker": ("Run a worker", 2),
    "controller": ("Run the controller", 4),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskwire",
        description="Kubernetes-native distributed task execution.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("version", help="Print the Taskwire version")
    for name, (help_text, _phase) in _PLANNED.items():
        commands.add_parser(name, help=f"{help_text} (not implemented yet)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(taskwire.__version__)
        return 0

    _help, phase = _PLANNED[args.command]
    print(
        f"taskwire {args.command}: not implemented yet (planned for Phase {phase})",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
