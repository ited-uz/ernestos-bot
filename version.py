"""The one place the release number lives (audit #50).

The archive name, `/api/version`, the Mini App's settings footer and the
README all read it from here, so a v15 archive can no longer contain a v12.2
project. `BUILD` names the exact commit that is running, so "the build we
tested" and "the build that is deployed" can be compared.
"""
import os

VERSION = "13.0"
RELEASE_NAME = f"ErnestOS-v{VERSION}"
BUILD = (os.environ.get("BUILD_ID") or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
         or os.environ.get("SOURCE_COMMIT") or "dev")[:12]
