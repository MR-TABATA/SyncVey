"""
`python manage.py syncvey <subcommand>` — the terminal / CI front door.

Subcommands:
  scan    Run a live AWS scan and record a drift snapshot.
  drift   Print the current drift (optionally fail the build on any drift).
  status  List systems / environments with asset counts and last-scan time.

Design notes:
  - Output is human text by default and machine JSON with `--format json`, so
    the same command serves an operator at a prompt and a pipeline step.
  - Exit codes are the CI contract:
        0  ok / no drift
        1  drift found          (only `drift --exit-code`)
        2  a scan job failed, or the selector matched nothing
  - All real work lives in syncvey_cli.service; this class is just parsing,
    formatting and exit codes.
"""

import json

from django.core.management.base import BaseCommand

from ... import service


class Command(BaseCommand):
    help = "SyncVey command-line interface: scan, drift, status."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='subcommand', title='subcommands', required=True)

        p_scan = sub.add_parser('scan', help="Run a live AWS scan and record drift.")
        p_scan.add_argument('--system', help="System code or name (default: all systems).")
        p_scan.add_argument('--env', dest='env', help="Environment name (default: all of the system).")
        p_scan.add_argument('--format', choices=('text', 'json'), default='text')

        p_drift = sub.add_parser('drift', help="Print the current drift.")
        p_drift.add_argument('--system', help="System code or name (default: all systems).")
        p_drift.add_argument('--env', dest='env', help="Environment name (default: all of the system).")
        p_drift.add_argument('--format', choices=('text', 'json'), default='text')
        p_drift.add_argument(
            '--exit-code', action='store_true',
            help="Exit 1 if any drift is found — use as a CI gate.",
        )

        p_status = sub.add_parser('status', help="List systems / environments.")
        p_status.add_argument('--format', choices=('text', 'json'), default='text')

    def handle(self, *args, **options):
        return getattr(self, f"_cmd_{options['subcommand']}")(**options)

    # -- selection helpers --------------------------------------------------

    def _selected_systems(self, system_selector):
        systems = service.resolve_systems(system_selector)
        if not systems:
            which = f" matching {system_selector!r}" if system_selector else ""
            self.stderr.write(self.style.ERROR(f"No system found{which}."))
        return systems

    # -- scan ---------------------------------------------------------------

    def _cmd_scan(self, system, env, format, **_):
        systems = self._selected_systems(system)
        if not systems:
            raise SystemExit(2)

        runs = []
        any_failed = False
        for sys_obj in systems:
            envs = service.resolve_environments(sys_obj, env)
            if not envs:
                self.stderr.write(self.style.WARNING(
                    f"{sys_obj.name}: no environment"
                    + (f" named {env!r}" if env else "") + " — skipped."
                ))
                continue
            for entry in service.scan(sys_obj, envs):
                any_failed = any_failed or entry['failed']
                runs.append((sys_obj, entry))

        if format == 'json':
            payload = [
                {
                    'system':      sys_obj.name,
                    'environment': entry['environment'].name,
                    'failed':      entry['failed'],
                    'scanned':     entry['result']['scanned'],
                    'created':     entry['result']['created'],
                    'updated':     entry['result']['updated'],
                    'errors':      entry['result']['errors'],
                }
                for sys_obj, entry in runs
            ]
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            if not runs:
                self.stdout.write("Nothing scanned.")
            for sys_obj, entry in runs:
                r = entry['result']
                mark = self.style.ERROR("FAILED") if entry['failed'] else self.style.SUCCESS("ok")
                self.stdout.write(
                    f"{sys_obj.name} / {entry['environment'].name}: {mark}  "
                    f"scanned={r['scanned']} new={r['created']} updated={r['updated']}"
                )
                for err in r['errors']:
                    self.stdout.write(self.style.WARNING(f"    ! {err}"))

        if any_failed:
            raise SystemExit(2)

    # -- drift --------------------------------------------------------------

    def _cmd_drift(self, system, env, format, exit_code, **_):
        systems = self._selected_systems(system)
        if not systems:
            raise SystemExit(2)

        reports = []  # (system, environment, drift_dict)
        for sys_obj in systems:
            for env_obj in service.resolve_environments(sys_obj, env):
                reports.append((sys_obj, env_obj, service.drift_for(env_obj)))

        total_drift = sum(len(d['changed']) + len(d['added']) for _, _, d in reports)

        if format == 'json':
            payload = [
                {
                    'system':      sys_obj.name,
                    'environment': env_obj.name,
                    'changed':     d['changed'],
                    'added':       d['added'],
                    'autoscaling': d['autoscaling'],
                    'unchanged':   d['unchanged'],
                }
                for sys_obj, env_obj, d in reports
            ]
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self._print_drift_text(reports, total_drift)

        if exit_code and total_drift:
            raise SystemExit(1)

    def _print_drift_text(self, reports, total_drift):
        if not reports:
            self.stdout.write("No environments to check.")
            return
        if total_drift == 0:
            self.stdout.write(self.style.SUCCESS("No drift. All environments match their last snapshot."))
            return
        for sys_obj, env_obj, d in reports:
            n = len(d['changed']) + len(d['added'])
            if n == 0:
                self.stdout.write(f"{sys_obj.name} / {env_obj.name}: {self.style.SUCCESS('clean')}")
                continue
            asg = len(d['autoscaling'])
            asg_note = f" [+{asg} autoscaling, not drift]" if asg else ""
            self.stdout.write(
                f"{sys_obj.name} / {env_obj.name}: "
                f"{self.style.WARNING(str(n) + ' drifted')} "
                f"(changed={len(d['changed'])} added={len(d['added'])}){asg_note}"
            )
            for item in d['changed']:
                self.stdout.write(f"    ~ {item['type']} {item['name']} ({item['cloud_id']})")
                for ch in item['changes']:
                    self.stdout.write(f"        {ch['field']}: {ch['old']} -> {ch['new']}")
            for item in d['added']:
                self.stdout.write(f"    + {item['type']} {item['name']} ({item['cloud_id']})")

    # -- status -------------------------------------------------------------

    def _cmd_status(self, format, **_):
        rows = service.status_rows()

        if format == 'json':
            payload = [
                {
                    'system':       r['system'],
                    'code':         r['code'],
                    'environment':  r['environment'],
                    'assets':       r['assets'],
                    'last_scan':    r['last_scan'].isoformat() if r['last_scan'] else None,
                    'scan_enabled': r['scan_enabled'],
                }
                for r in rows
            ]
            self.stdout.write(json.dumps(payload, indent=2))
            return

        if not rows:
            self.stdout.write("No systems configured.")
            return
        self.stdout.write(f"{'SYSTEM':<20} {'ENV':<12} {'ASSETS':>7}  {'AUTO':<5} LAST SCAN")
        for r in rows:
            last = r['last_scan'].strftime('%Y-%m-%d %H:%M') if r['last_scan'] else '-'
            auto = 'on' if r['scan_enabled'] else 'off'
            self.stdout.write(
                f"{r['system']:<20.20} {r['environment']:<12.12} {r['assets']:>7}  {auto:<5} {last}"
            )
