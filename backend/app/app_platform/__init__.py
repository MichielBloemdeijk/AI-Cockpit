"""App platform: installable app packages that Jarvis builds, runs and maintains.

Design: docs/jarvis-tasks/13-app-platform.md. Callers use these names; the
modules behind them are internal to the package.
"""
from app.app_platform.check import check_package
from app.app_platform.errors import record_error, recent_errors
from app.app_platform.hosting import HOST_CSP, SDK_FILE
from app.app_platform.lifecycle import (
    ActiveApp,
    AppNotFound,
    checkout,
    find_app,
    get_active,
    install_package,
    list_apps,
    package_file,
    reset,
    rollback,
    status,
    uninstall,
)
from app.app_platform.manifest import ManifestError
from app.app_platform.records import (
    RecordError,
    RevisionConflict,
    delete_record,
    get_record,
    put_record,
    query_records,
)
from app.app_platform.store import close_apps_engine, ensure_apps_database
from app.app_platform.tokens import SCHEME as TOKEN_SCHEME, issue_token, verify_token

__all__ = [
    "ActiveApp",
    "AppNotFound",
    "HOST_CSP",
    "ManifestError",
    "RecordError",
    "RevisionConflict",
    "SDK_FILE",
    "TOKEN_SCHEME",
    "check_package",
    "checkout",
    "close_apps_engine",
    "delete_record",
    "ensure_apps_database",
    "find_app",
    "get_active",
    "get_record",
    "install_package",
    "issue_token",
    "list_apps",
    "package_file",
    "put_record",
    "query_records",
    "recent_errors",
    "record_error",
    "reset",
    "rollback",
    "status",
    "uninstall",
    "verify_token",
]
