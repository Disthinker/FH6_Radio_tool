from __future__ import annotations

from .loop_engine.seamless_loopfinder import worker_main


def main(argv: list[str] | None = None) -> int:
    return worker_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
