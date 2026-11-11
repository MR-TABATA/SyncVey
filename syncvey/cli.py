"""Command-line entrypoint for running SyncVey via `uvx syncvey` or `pipx run syncvey`."""
import argparse
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

from syncvey import __version__


def get_default_data_dir() -> Path:
    """Return an OS-idiomatic directory for storing SyncVey's database and keys.

    - macOS:   ~/Library/Application Support/syncvey
    - Windows: %APPDATA%/syncvey
    - Linux:   $XDG_DATA_HOME/syncvey (or ~/.local/share/syncvey)
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "syncvey"


def find_available_port(host: str, start_port: int, max_attempts: int = 10) -> Optional[int]:
    """Find an available TCP port starting from start_port, supporting both IPv4 and IPv6."""
    for port in range(start_port, start_port + max_attempts):
        try:
            addrinfo = socket.getaddrinfo(
                host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
            )
        except socket.gaierror:
            return None

        for family, socktype, proto, _, sockaddr in addrinfo:
            try:
                with socket.socket(family, socktype, proto) as sock:
                    sock.bind(sockaddr)
                    return port
            except OSError:
                break
    return None


def open_browser_when_ready(check_url: str, open_url: str, timeout_seconds: float = 3.0) -> None:
    """Poll until the server starts responding, then open the browser."""
    def _poll_and_open():
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                req = urllib.request.Request(check_url, method="HEAD")
                with urllib.request.urlopen(req, timeout=0.5):
                    webbrowser.open(open_url)
                    return
            except Exception:
                time.sleep(0.15)
        sys.stderr.write(
            f"Note: Server did not respond within {timeout_seconds}s. Please open {open_url} manually.\n"
        )

    thread = threading.Thread(target=_poll_and_open, daemon=True)
    thread.start()


def format_url(host: str, port: int) -> str:
    """Format HTTP URL handling IPv6 addresses with brackets."""
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    if ":" in display_host and not display_host.startswith("["):
        return f"http://[{display_host}]:{port}/"
    return f"http://{display_host}:{port}/"


def normalize_csrf_origins(host: str, port: int, existing_origins: str) -> str:
    """Merge and normalize CSRF trusted origins with host validation."""
    clean_host = host.strip()
    if any(c in clean_host for c in " \t\r\n/"):
        clean_host = "127.0.0.1"

    origins = set()
    if existing_origins:
        for item in existing_origins.split(","):
            item = item.strip()
            if item and not any(c in item for c in " \t\r\n"):
                origins.add(item)

    origins.add(f"http://127.0.0.1:{port}")
    origins.add(f"http://localhost:{port}")

    if clean_host not in ("0.0.0.0", "127.0.0.1", "localhost"):
        if ":" in clean_host and not clean_host.startswith("["):
            origins.add(f"http://[{clean_host}]:{port}")
        else:
            origins.add(f"http://{clean_host}:{port}")

    return ",".join(sorted(origins))


def ensure_sys_path():
    """Ensure package roots are in sys.path so apps can be imported in all contexts."""
    pkg_dir = Path(__file__).resolve().parent
    for p in (str(pkg_dir), str(pkg_dir.parent)):
        if p not in sys.path:
            sys.path.insert(0, p)


def setup_django_environment(data_dir: Path, debug: bool = True):
    """Set up environment variables and initialize Django."""
    ensure_sys_path()

    db_path = data_dir / "db.sqlite3"
    secret_key_file = data_dir / "secret.key"

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SYNCVEY_DB_PATH", str(db_path))
    os.environ.setdefault("SYNCVEY_SECRET_KEY_FILE", str(secret_key_file))

    if "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    if debug:
        os.environ.setdefault("DEBUG", "True")
    else:
        os.environ.setdefault("DEBUG", "False")

    import django
    django.setup()


def run_web(args, data_dir: Path) -> int:
    """Run the local SyncVey web UI server."""
    if args.host not in ("127.0.0.1", "localhost"):
        sys.stderr.write(
            f"Warning: binding to non-localhost address '{args.host}'. "
            "Traffic and credentials are transmitted unencrypted over HTTP.\n"
        )

    port = find_available_port(args.host, args.port)
    if port is None:
        sys.stderr.write(
            f"Error: all ports in range {args.port}-{args.port + 9} on host '{args.host}' are already in use.\n"
        )
        return 1

    if port != args.port:
        print(f"Port {args.port} was busy; using port {port} instead.")

    existing_csrf = os.environ.get("SYNCVEY_EXTRA_CSRF_ORIGINS", "")
    os.environ["SYNCVEY_EXTRA_CSRF_ORIGINS"] = normalize_csrf_origins(args.host, port, existing_csrf)
    os.environ["SYNCVEY_EXTRA_ALLOWED_HOSTS"] = args.host

    try:
        setup_django_environment(data_dir, debug=True)
    except Exception as exc:
        sys.stderr.write(f"Error: failed to initialize Django settings: {exc}\n")
        return 1

    from django.core.management import call_command
    from asset_manager.models import Organization

    db_path = data_dir / "db.sqlite3"
    print(f"SyncVey v{__version__}")
    print(f"Data directory: {data_dir}")
    print(f"Database: {db_path}")
    print("Applying database migrations...")
    try:
        call_command("migrate", interactive=False, verbosity=0)
    except Exception as exc:
        sys.stderr.write(
            f"Error: failed to initialize database at '{db_path}': {exc}\n"
            f"Hint: check migration output above, or remove '{db_path}' to recreate from scratch.\n"
        )
        return 1

    # If database is fresh (no organizations), seed default demo data
    if not args.no_seed and Organization.objects.count() == 0:
        print("Seeding initial demo data (systems, environments, sample users)...")
        try:
            call_command("seed", verbosity=0)
            from django.contrib.auth import get_user_model
            user_model = get_user_model()
            if not user_model.objects.filter(username="admin").exists():
                admin_password = secrets.token_urlsafe(16)
                user_model.objects.create_superuser("admin", "admin@example.com", admin_password)
                admin_info = f"admin / {admin_password} (at /admin/)"
            else:
                admin_info = "admin (password already set, at /admin/)"
            print("  Initial setup complete.")
            print("  Credentials:")
            print("    App (asset ledger):  tabata_hiroshi / Passw0rd!")
            print(f"    Django Admin:        {admin_info}")
        except Exception as exc:
            sys.stderr.write(f"Error: failed to seed demo data: {exc}\n")
            return 1

    url = format_url(args.host, port)
    check_url = f"http://127.0.0.1:{port}/" if args.host == "0.0.0.0" else url

    print(f"\nSyncVey is running at: {url}")
    print("Press Ctrl+C to stop.\n")

    if not args.no_browser:
        open_browser_when_ready(check_url, url)

    try:
        call_command("runserver", f"{args.host}:{port}", use_reloader=False)
    except KeyboardInterrupt:
        print("\nShutting down SyncVey.")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error: server terminated unexpectedly: {exc}\n")
        return 1

    return 0


def run_cli_subcommand(subcommand: str, sub_args, data_dir: Path) -> int:
    """Run an existing syncvey CLI management subcommand (scan, drift, status, export)."""
    try:
        setup_django_environment(data_dir, debug=False)
    except Exception as exc:
        sys.stderr.write(f"Error: failed to initialize Django settings: {exc}\n")
        return 2

    from django.core.management import call_command
    from django.core.management.base import CommandError

    db_path = data_dir / "db.sqlite3"
    # Ensure migrations are applied before running CLI commands
    try:
        call_command("migrate", interactive=False, verbosity=0)
    except Exception as exc:
        sys.stderr.write(
            f"Error: failed to initialize database at '{db_path}': {exc}\n"
            f"Hint: check migration output above, or remove '{db_path}' to recreate from scratch.\n"
        )
        return 2

    # Build command args to pass to call_command('syncvey', subcommand, ...)
    cmd_args = [subcommand]
    if hasattr(sub_args, "system") and sub_args.system:
        cmd_args.extend(["--system", sub_args.system])
    if hasattr(sub_args, "env") and sub_args.env:
        cmd_args.extend(["--env", sub_args.env])
    if hasattr(sub_args, "format") and sub_args.format:
        cmd_args.extend(["--format", sub_args.format])
    if hasattr(sub_args, "exit_code") and sub_args.exit_code:
        cmd_args.append("--exit-code")
    if hasattr(sub_args, "output") and sub_args.output:
        cmd_args.extend(["--output", sub_args.output])
    if hasattr(sub_args, "no_raw") and sub_args.no_raw:
        cmd_args.append("--no-raw")

    try:
        res = call_command("syncvey", *cmd_args)
        return 0 if res is None else (int(res) if isinstance(res, (int, bool)) else 0)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except CommandError as exc:
        sys.stderr.write(f"Command error: {exc}\n")
        return 2
    except Exception as exc:
        sys.stderr.write(f"Error executing syncvey {subcommand}: {exc}\n")
        return 2


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for syncvey with shared options."""
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory to store db.sqlite3 and secret.key (default: OS app data folder)",
    )

    parser = argparse.ArgumentParser(
        prog="syncvey",
        description="SyncVey: AWS inventory and drift detection ledger.",
        parents=[common_parser],
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="subcommand", title="subcommands")

    # web subcommand
    p_web = subparsers.add_parser(
        "web",
        help="Launch the local SyncVey web UI server (default).",
        parents=[common_parser],
    )
    p_web.add_argument("--host", default="127.0.0.1", help="Host address to bind to (default: 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    p_web.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    p_web.add_argument("--no-seed", action="store_true", help="Do not seed initial demo data if database is fresh")

    # scan subcommand
    p_scan = subparsers.add_parser(
        "scan",
        help="Run a live AWS scan and record drift.",
        parents=[common_parser],
    )
    p_scan.add_argument("--system", help="System code or name (default: all systems).")
    p_scan.add_argument("--env", dest="env", help="Environment name (default: all of the system).")
    p_scan.add_argument("--format", choices=("text", "json"), default="text")

    # drift subcommand
    p_drift = subparsers.add_parser(
        "drift",
        help="Print the current drift.",
        parents=[common_parser],
    )
    p_drift.add_argument("--system", help="System code or name (default: all systems).")
    p_drift.add_argument("--env", dest="env", help="Environment name (default: all of the system).")
    p_drift.add_argument("--format", choices=("text", "json"), default="text")
    p_drift.add_argument("--exit-code", action="store_true", help="Exit 1 if any drift is found — use as a CI gate.")

    # status subcommand
    p_status = subparsers.add_parser(
        "status",
        help="List systems / environments.",
        parents=[common_parser],
    )
    p_status.add_argument("--format", choices=("text", "json"), default="text")

    # export subcommand
    p_export = subparsers.add_parser(
        "export",
        help="Write the asset ledger as JSON or CSV.",
        parents=[common_parser],
    )
    p_export.add_argument("--system", help="System code or name (default: all systems).")
    p_export.add_argument("--env", dest="env", help="Environment name (default: all of the system).")
    p_export.add_argument("--format", choices=("json", "csv"), default="json")
    p_export.add_argument("--output", "-o", help="Write to this file instead of stdout.")
    p_export.add_argument("--no-raw", action="store_true", help="JSON only: leave out each asset's stored raw_data.")

    return parser


def resolve_subcommand_argv(argv: list[str]) -> list[str]:
    """Ensure a subcommand (defaulting to 'web') is present in argv without misinterpreting option values."""
    known_commands = {"web", "scan", "drift", "status", "export"}
    help_version_flags = {"-h", "--help", "-v", "--version"}
    options_with_value = {
        "--data-dir", "--host", "--port", "--system", "--env", "--format", "--output", "-o"
    }

    if any(flag in argv for flag in help_version_flags):
        return argv

    # Find the first true positional argument
    i = 0
    first_pos_arg = None
    first_pos_idx = None
    while i < len(argv):
        arg = argv[i]
        if "=" in arg and arg.startswith("-"):
            i += 1
            continue
        if arg in options_with_value:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        first_pos_arg = arg
        first_pos_idx = i
        break

    if first_pos_arg is not None:
        if first_pos_arg in known_commands:
            return argv
        return argv[:first_pos_idx] + ["web"] + argv[first_pos_idx:]
    else:
        return ["web"] + argv


def main(argv=None) -> int:
    """CLI entrypoint."""
    if argv is None:
        argv = sys.argv[1:]

    argv = resolve_subcommand_argv(argv)

    parser = build_parser()
    args = parser.parse_args(argv)

    data_dir = args.data_dir.resolve() if args.data_dir else get_default_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"Error: failed to create data directory '{data_dir}': {exc}\n")
        return 1

    subcommand = args.subcommand or "web"
    if subcommand == "web":
        return run_web(args, data_dir)
    else:
        return run_cli_subcommand(subcommand, args, data_dir)


if __name__ == "__main__":
    sys.exit(main())
