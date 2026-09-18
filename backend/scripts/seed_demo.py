"""V2 demo seeding is disabled because Truth V3 accepts release bundles only."""

import sys


def main() -> int:
    print("seed_demo is disabled for Truth V3; import an accepted demo Release with import_v3.", file=sys.stderr)
    return 2


if __name__ == "__main__": raise SystemExit(main())
