"""App platform: installable app packages that Jarvis builds, runs and maintains.

Design: docs/jarvis-tasks/13-app-platform.md. Callers use these names; the
modules behind them are internal to the package.
"""
from app.app_platform.builtin import install_builtin_apps
from app.app_platform.check import check_package
from app.app_platform.errors import record_error, recent_errors, repair_goal
from app.app_platform.files import FileError, delete_file, get_file, list_files, put_file
from app.app_platform.function_host import FunctionError, RunResult, run_function
from app.app_platform.hosting import HOST_CSP, SDK_FILE
from app.app_platform.lifecycle import (
    ActiveApp,
    AppNotFound,
    checkout,
    consumers,
    data_catalog,
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
from app.app_platform.runtime import RepairStarter, app_runtime
from app.app_platform.store import close_apps_engine, ensure_apps_database
from app.app_platform.tokens import SCHEME as TOKEN_SCHEME, issue_token, verify_token
from app.app_platform.views import last_view, record_view
from app.app_platform.workflows import installed_workflows

__all__ = [
    "ActiveApp",
    "AppNotFound",
    "FileError",
    "FunctionError",
    "RepairStarter",
    "RunResult",
    "app_runtime",
    "delete_file",
    "get_file",
    "list_files",
    "put_file",
    "repair_goal",
    "run_function",
    "HOST_CSP",
    "ManifestError",
    "RecordError",
    "RevisionConflict",
    "SDK_FILE",
    "TOKEN_SCHEME",
    "check_package",
    "checkout",
    "consumers",
    "data_catalog",
    "close_apps_engine",
    "delete_record",
    "ensure_apps_database",
    "find_app",
    "get_active",
    "get_record",
    "install_builtin_apps",
    "install_package",
    "installed_workflows",
    "issue_token",
    "last_view",
    "list_apps",
    "package_file",
    "put_record",
    "query_records",
    "recent_errors",
    "record_error",
    "record_view",
    "reset",
    "rollback",
    "status",
    "uninstall",
    "verify_token",
]
