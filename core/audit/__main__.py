"""Audit log CLI entrypoint."""

import sys

from core.audit.verify import main

if __name__ == "__main__":
    sys.exit(main())
