"""Allow running as: python -m trcc.cli"""

import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())
