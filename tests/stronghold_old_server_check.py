"""
tests/stronghold_old_server_check.py — superseded by tests/stronghold_server_check.py
(1.16.1 → 26.3); kept as an alias: runs it for 1.16.1 and 1.17.1.
    py tests/stronghold_old_server_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stronghold_server_check as t  # noqa: E402

if __name__ == "__main__":
    sys.exit(t.main({19, 21}))
