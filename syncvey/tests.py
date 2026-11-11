"""Tests for SyncVey standalone CLI and uvx execution."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.core.exceptions import ImproperlyConfigured

from syncvey.cli import (
    find_available_port,
    format_url,
    get_default_data_dir,
    main,
    normalize_csrf_origins,
    resolve_subcommand_argv,
)


class TestCliStandalone(unittest.TestCase):
    def test_default_data_dir(self):
        d = get_default_data_dir()
        self.assertIsInstance(d, Path)
        self.assertTrue(str(d).endswith("syncvey"))

    def test_format_url(self):
        self.assertEqual(format_url("127.0.0.1", 8000), "http://127.0.0.1:8000/")
        self.assertEqual(format_url("0.0.0.0", 8000), "http://127.0.0.1:8000/")
        self.assertEqual(format_url("localhost", 8080), "http://localhost:8080/")
        self.assertEqual(format_url("::1", 8000), "http://[::1]:8000/")

    def test_normalize_csrf_origins(self):
        norm = normalize_csrf_origins("127.0.0.1", 8000, "http://example.com, http://test.org")
        self.assertIn("http://example.com", norm)
        self.assertIn("http://test.org", norm)
        self.assertIn("http://127.0.0.1:8000", norm)
        self.assertIn("http://localhost:8000", norm)

        ipv6_norm = normalize_csrf_origins("::1", 8000, "")
        self.assertIn("http://[::1]:8000", ipv6_norm)

        # Host with whitespace is cleaned to 127.0.0.1
        bad_host_norm = normalize_csrf_origins("evil.com \n", 8000, "")
        self.assertIn("http://127.0.0.1:8000", bad_host_norm)

    def test_find_available_port(self):
        port = find_available_port("127.0.0.1", 19200, max_attempts=5)
        self.assertIsNotNone(port)
        self.assertGreaterEqual(port, 19200)

        # When all attempts fail
        with mock.patch("socket.socket") as mock_sock:
            mock_inst = mock_sock.return_value.__enter__.return_value
            mock_inst.bind.side_effect = OSError("Address in use")
            exhausted = find_available_port("127.0.0.1", 19200, max_attempts=3)
            self.assertIsNone(exhausted)

    def test_version_flag(self):
        with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stdout"):
            main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_help_flag(self):
        with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stdout"):
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_cannot_create_data_dir(self):
        with tempfile.NamedTemporaryFile() as tf:
            with mock.patch("sys.stderr"):
                code = main(["--data-dir", tf.name])
            self.assertEqual(code, 1)

    def test_secret_key_file_resolution(self):
        from config.settings import _get_django_secret_key

        with tempfile.TemporaryDirectory() as td:
            key_file = Path(td) / "test_secret.key"
            with mock.patch.dict(os.environ, {"SYNCVEY_SECRET_KEY_FILE": str(key_file)}, clear=False):
                os.environ.pop("SECRET_KEY", None)
                # First call generates key file with 0600
                k1 = _get_django_secret_key()
                self.assertTrue(key_file.exists())
                self.assertTrue(k1.startswith("django-managed-secure-key-"))

                # Check file permission is 0600 on POSIX
                if os.name == "posix":
                    mode = os.stat(key_file).st_mode & 0o777
                    self.assertEqual(mode, 0o600)

                # Second call loads the exact same key
                k2 = _get_django_secret_key()
                self.assertEqual(k1, k2)

    def test_secret_key_env_precedence(self):
        from config.settings import _get_django_secret_key

        with mock.patch.dict(os.environ, {"SECRET_KEY": "env-explicit-key-12345"}, clear=False):
            key = _get_django_secret_key()
            self.assertEqual(key, "env-explicit-key-12345")

    def test_secret_key_db_path_fallback(self):
        from config.settings import _get_django_secret_key

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "custom" / "db.sqlite3"
            env_vars = {"SYNCVEY_DB_PATH": str(db_path)}
            # Remove any SYNCVEY_SECRET_KEY_FILE or SECRET_KEY
            with mock.patch.dict(os.environ, env_vars, clear=False):
                os.environ.pop("SYNCVEY_SECRET_KEY_FILE", None)
                os.environ.pop("SECRET_KEY", None)
                key = _get_django_secret_key()
                expected_key_file = Path(td) / "custom" / "secret.key"
                self.assertTrue(expected_key_file.exists())
                self.assertTrue(key.startswith("django-managed-secure-key-"))

    def test_secret_key_empty_or_corrupt_file_raises(self):
        from config.settings import _get_django_secret_key

        with tempfile.TemporaryDirectory() as td:
            key_file = Path(td) / "empty.key"
            key_file.write_text("# only comment line\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SYNCVEY_SECRET_KEY_FILE": str(key_file)}, clear=False):
                os.environ.pop("SECRET_KEY", None)
                with self.assertRaises(ImproperlyConfigured):
                    _get_django_secret_key()

    def test_secret_key_write_failure_raises(self):
        from config.settings import _get_django_secret_key

        with tempfile.NamedTemporaryFile() as tf:
            # File exists as a file, so trying to create a directory with same name will fail
            key_file = Path(tf.name) / "secret.key"
            with mock.patch.dict(os.environ, {"SYNCVEY_SECRET_KEY_FILE": str(key_file)}, clear=False):
                os.environ.pop("SECRET_KEY", None)
                with self.assertRaises(ImproperlyConfigured):
                    _get_django_secret_key()

    def test_path_resolution(self):
        from config import settings

        self.assertTrue(settings._TEMPLATES_DIR.exists())
        self.assertTrue(settings._LOCALE_DIR.exists())
        self.assertTrue(settings._STATIC_DIR.exists())

    def test_web_execution_flow(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.find_available_port", return_value=8000), \
                 mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("asset_manager.models.Organization") as mock_org, \
                 mock.patch("sys.stdout"):
                # Organization count > 0 means seed won't be called
                mock_org.objects.count.return_value = 1
                mock_call.side_effect = [None, KeyboardInterrupt]
                code = main(["--data-dir", str(td_path), "--no-browser"])
                self.assertEqual(code, 0)
                self.assertEqual(mock_call.call_args_list[0][0][0], "migrate")
                self.assertEqual(mock_call.call_args_list[1][0][0], "runserver")

    def test_web_initial_seed_and_admin_generation(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.find_available_port", return_value=8000), \
                 mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("asset_manager.models.Organization") as mock_org, \
                 mock.patch("django.contrib.auth.get_user_model") as mock_get_user, \
                 mock.patch("sys.stdout"):
                mock_org.objects.count.return_value = 0
                mock_user_model = mock.MagicMock()
                mock_user_model.objects.filter.return_value.exists.return_value = False
                mock_get_user.return_value = mock_user_model

                # migrate succeeds, seed succeeds, runserver interrupted
                mock_call.side_effect = [None, None, KeyboardInterrupt]
                code = main(["--data-dir", str(td_path), "--no-browser"])
                self.assertEqual(code, 0)

                # migrate called
                self.assertEqual(mock_call.call_args_list[0][0][0], "migrate")
                # seed called
                self.assertEqual(mock_call.call_args_list[1][0][0], "seed")
                # create_superuser called with admin
                mock_user_model.objects.create_superuser.assert_called_once()
                args, _ = mock_user_model.objects.create_superuser.call_args
                self.assertEqual(args[0], "admin")
                self.assertEqual(args[1], "admin@example.com")
                # Password must be random, not hardcoded 'admin'
                self.assertNotEqual(args[2], "admin")

    def test_web_no_seed_flag(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.find_available_port", return_value=8000), \
                 mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("asset_manager.models.Organization") as mock_org, \
                 mock.patch("sys.stdout"):
                mock_org.objects.count.return_value = 0
                mock_call.side_effect = [None, KeyboardInterrupt]
                code = main(["--data-dir", str(td_path), "--no-browser", "--no-seed"])
                self.assertEqual(code, 0)
                # Only migrate and runserver, no seed
                calls = [c[0][0] for c in mock_call.call_args_list]
                self.assertNotIn("seed", calls)

    def test_web_seed_failure_aborts(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.find_available_port", return_value=8000), \
                 mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("asset_manager.models.Organization") as mock_org, \
                 mock.patch("sys.stderr"), \
                 mock.patch("sys.stdout"):
                mock_org.objects.count.return_value = 0
                # migrate succeeds, seed raises
                mock_call.side_effect = [None, Exception("Seed failed")]
                code = main(["--data-dir", str(td_path), "--no-browser"])
                self.assertEqual(code, 1)

    def test_web_migrate_failure_aborts(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.find_available_port", return_value=8000), \
                 mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command", side_effect=Exception("DB lock")), \
                 mock.patch("sys.stderr"), \
                 mock.patch("sys.stdout"):
                code = main(["--data-dir", str(td_path), "--no-browser"])
                self.assertEqual(code, 1)

    def test_web_port_exhaustion_aborts(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.find_available_port", return_value=None), \
                 mock.patch("sys.stderr"):
                code = main(["--data-dir", str(td_path)])
                self.assertEqual(code, 1)

    def test_web_runserver_exception_returns_one(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.find_available_port", return_value=8000), \
                 mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("asset_manager.models.Organization") as mock_org, \
                 mock.patch("sys.stderr"), \
                 mock.patch("sys.stdout"):
                mock_org.objects.count.return_value = 1
                mock_call.side_effect = [None, RuntimeError("Crash")]
                code = main(["--data-dir", str(td_path), "--no-browser"])
                self.assertEqual(code, 1)

    def test_cli_subcommand_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("sys.stdout"):
                code = main(["--data-dir", str(td_path), "status", "--format", "json"])
                self.assertEqual(code, 0)
                self.assertEqual(mock_call.call_args_list[0][0][0], "migrate")
                self.assertEqual(mock_call.call_args_list[1][0], ("syncvey", "status", "--format", "json"))

    def test_cli_subcommand_all_arguments_passed(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("sys.stdout"):
                code = main([
                    "--data-dir", str(td_path),
                    "export",
                    "--system", "ecsite",
                    "--env", "Production",
                    "--format", "csv",
                    "--output", "out.csv",
                    "--no-raw",
                ])
                self.assertEqual(code, 0)
                cmd_args = mock_call.call_args_list[1][0]
                self.assertEqual(cmd_args[0], "syncvey")
                self.assertEqual(cmd_args[1], "export")
                self.assertIn("--system", cmd_args)
                self.assertIn("ecsite", cmd_args)
                self.assertIn("--env", cmd_args)
                self.assertIn("Production", cmd_args)
                self.assertIn("--format", cmd_args)
                self.assertIn("csv", cmd_args)
                self.assertIn("--output", cmd_args)
                self.assertIn("out.csv", cmd_args)
                self.assertIn("--no-raw", cmd_args)

    def test_cli_drift_exit_code_propagation(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch("syncvey.cli.setup_django_environment"), \
                 mock.patch("django.core.management.call_command") as mock_call, \
                 mock.patch("sys.stdout"):
                # migrate succeeds, drift raises SystemExit(1)
                mock_call.side_effect = [None, SystemExit(1)]
                code = main(["--data-dir", str(td_path), "drift", "--exit-code"])
                self.assertEqual(code, 1)

                # when drift exits 0
                mock_call.side_effect = [None, SystemExit(0)]
                code_ok = main(["--data-dir", str(td_path), "drift", "--exit-code"])
                self.assertEqual(code_ok, 0)

                # when drift raises SystemExit(None)
                mock_call.side_effect = [None, SystemExit(None)]
                code_none = main(["--data-dir", str(td_path), "drift"])
                self.assertEqual(code_none, 0)

    def test_resolve_subcommand_argv(self):
        # Empty argv defaults to web
        self.assertEqual(resolve_subcommand_argv([]), ["web"])

        # Flag only defaults to web
        self.assertEqual(resolve_subcommand_argv(["--port", "8080"]), ["web", "--port", "8080"])
        self.assertEqual(resolve_subcommand_argv(["--no-browser"]), ["web", "--no-browser"])

        # --data-dir with option value
        self.assertEqual(
            resolve_subcommand_argv(["--data-dir", "/tmp/sync", "--port", "8080"]),
            ["web", "--data-dir", "/tmp/sync", "--port", "8080"],
        )
        self.assertEqual(
            resolve_subcommand_argv(["--data-dir=/tmp/sync", "--port", "8080"]),
            ["web", "--data-dir=/tmp/sync", "--port", "8080"],
        )

        # --data-dir value happens to be named 'status'
        self.assertEqual(
            resolve_subcommand_argv(["--data-dir", "status"]),
            ["web", "--data-dir", "status"],
        )

        # Explicit subcommands remain unchanged
        self.assertEqual(resolve_subcommand_argv(["status"]), ["status"])
        self.assertEqual(resolve_subcommand_argv(["scan", "--system", "ecsite"]), ["scan", "--system", "ecsite"])
        self.assertEqual(resolve_subcommand_argv(["web", "--port", "8000"]), ["web", "--port", "8000"])

        # Help / version flags untouched
        self.assertEqual(resolve_subcommand_argv(["--help"]), ["--help"])
        self.assertEqual(resolve_subcommand_argv(["-v"]), ["-v"])
