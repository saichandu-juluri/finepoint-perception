"""The cloud stand-in (cloud/dev_server.py) with every piece slowed down, so the e2e can see an upload running and pause
it half way. Standard library only.

    python tests/slow_cloud.py SECONDS_PER_PIECE [dev_server.py arguments]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cloud"))
import dev_server

DELAY = float(sys.argv.pop(1))
put = dev_server.Handler.do_PUT


def slow_put(self):
    time.sleep(DELAY)
    put(self)


dev_server.Handler.do_PUT = slow_put
dev_server.main()
