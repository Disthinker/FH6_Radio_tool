import sys


if "--loopfinder-worker" in sys.argv:
    from fh6_radio_tool.loopfinder_worker import main

    argv = [x for x in sys.argv[1:] if x != "--loopfinder-worker"]
    raise SystemExit(main(argv))

from fh6_radio_tool.v2_ui import main

if __name__ == "__main__":
    raise SystemExit(main())
