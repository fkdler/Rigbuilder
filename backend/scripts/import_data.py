"""Legacy V2 importer tombstone.

Keeping this module prevents old deployment commands from silently writing V2
rows after the V3 cutover.
"""

import sys


def main() -> int:
    print("Legacy import_data is disabled. Use convert_v2_to_v3, review drafts, then import_v3.", file=sys.stderr)
    return 2


if __name__ == "__main__": raise SystemExit(main())
