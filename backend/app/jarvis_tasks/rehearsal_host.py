"""Own the isolated pytest/API tree before starting any rehearsal child."""
from __future__ import annotations

import os
import sys

from app.services.windows_job import contain_current_process_tree, exit_when_parent_exits


def main() -> int:
    # These acceptance fixtures target the pinned Windows route. Fail closed on
    # unsupported hosts instead of leaving an API detached after timeout/crash.
    contain_current_process_tree()
    exit_when_parent_exits(os.getppid())
    import pytest
    return pytest.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
