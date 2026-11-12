try:
    from .main import main
except Exception:
    # The mod's own files are broken (an update stopped half way, a file missing): write why in
    # the log, and put the newest release back if this copy came from one.
    import os
    import sys
    import traceback
    from . import update
    if update.repair(traceback.format_exc()):
        sys.exit(update.RESTART)
    raise
main()
