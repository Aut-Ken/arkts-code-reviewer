from __future__ import annotations

import sys

from arkts_code_reviewer.hybrid_analysis.campaign_live_smoke import (
    main,
)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
