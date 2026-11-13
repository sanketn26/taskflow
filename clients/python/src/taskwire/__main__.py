"""``python -m taskwire``: ``version`` now, ``run`` (serve pools) in Phase 3."""

import sys

import taskwire


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["version"]:
        print(taskwire.__version__)
        return 0
    print(
        "usage: python -m taskwire version  (`run` arrives in Phase 3)", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
