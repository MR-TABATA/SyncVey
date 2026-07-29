import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from asset_manager.models import (
    Asset, DriftSnapshot, Environment, Organization, ScanJob, System,
)


def _run(*args):
    """Call `manage.py syncvey <args>` capturing stdout; returns (stdout, exit_code)."""
    out = StringIO()
    code = 0
    try:
        call_command('syncvey', *args, stdout=out, stderr=StringIO())
    except SystemExit as exc:
        code = exc.code
    return out.getvalue(), code


class Base(TestCase):
    def _env(self, sys_name='sys-a', code='sys-a', env_name='prod', role='arn:aws:iam::1:role/r'):
        org = Organization.objects.create(name=f'org-{code}', slug=f'org-{code}')
        system = System.objects.create(
            name=sys_name, code=code, organization=org,
            aws_role_arn=role, aws_scan_regions=['ap-northeast-1'],
        )
        env = Environment.objects.create(system=system, name=env_name, env_type='PROD')
        return system, env

    def _asset(self, env, cloud_id, prev, cur, **kw):
        return Asset.objects.create(
            environment=env, name=kw.get('name', cloud_id), provider='AWS',
            asset_type=kw.get('asset_type', 'SG'), asset_category='NETWORK',
            cloud_id=cloud_id, raw_data_prev=prev, raw_data=cur,
        )


class TestDrift(Base):
    def test_clean_environment_reports_no_drift_and_exits_zero(self):
        _, env = self._env()
        self._asset(env, 'sg-1', {'ingress_cidr': '10.0.0.0/8'}, {'ingress_cidr': '10.0.0.0/8'})
        out, code = _run('drift', '--exit-code')
        self.assertIn('No drift', out)
        self.assertEqual(code, 0)

    def test_changed_asset_is_reported(self):
        _, env = self._env()
        self._asset(env, 'sg-1', {'ingress_cidr': '10.0.0.0/8'}, {'ingress_cidr': '0.0.0.0/0'})
        out, code = _run('drift')
        self.assertIn('drifted', out)
        self.assertIn('ingress_cidr: 10.0.0.0/8 -> 0.0.0.0/0', out)
        self.assertEqual(code, 0)  # no --exit-code

    def test_exit_code_flag_fails_on_drift(self):
        _, env = self._env()
        self._asset(env, 'sg-1', {'ingress_cidr': '10.0.0.0/8'}, {'ingress_cidr': '0.0.0.0/0'})
        _, code = _run('drift', '--exit-code')
        self.assertEqual(code, 1)

    def test_added_asset_counts_as_drift(self):
        # no raw_data_prev → first sighting → ADDED, mirrors the core
        _, env = self._env()
        self._asset(env, 'sg-1', {}, {'ingress_cidr': '0.0.0.0/0'})
        out, code = _run('drift', '--exit-code')
        self.assertIn('added=1', out)
        self.assertEqual(code, 1)

    def test_json_output_shape(self):
        _, env = self._env()
        self._asset(env, 'sg-1', {'ingress_cidr': '10.0.0.0/8'}, {'ingress_cidr': '0.0.0.0/0'})
        out, _code = _run('drift', '--format', 'json')
        data = json.loads(out)
        self.assertEqual(data[0]['environment'], 'prod')
        self.assertEqual(data[0]['changed'][0]['cloud_id'], 'sg-1')

    def test_diff_uses_core_intersection_rule(self):
        # tfstate-only keys absent from a scan must NOT read as drift — the core
        # compares the key intersection, and the CLI must inherit that.
        _, env = self._env()
        self._asset(env, 'sg-1',
                    {'ingress_cidr': '10.0.0.0/8', 'tf_only': 'x'},
                    {'ingress_cidr': '10.0.0.0/8'})
        _, code = _run('drift', '--exit-code')
        self.assertEqual(code, 0)

    def test_unknown_system_exits_two(self):
        self._env()
        _, code = _run('drift', '--system', 'nope')
        self.assertEqual(code, 2)

    def test_asg_churn_does_not_fail_the_build(self):
        # an ASG-owned first-sighting is churn, not drift — --exit-code must pass
        _, env = self._env()
        self._asset(env, 'i-asg', {}, {'instance_type': 't3.micro', 'autoscaling_group': 'web-asg'})
        out, code = _run('drift', '--exit-code')
        self.assertEqual(code, 0)
        out_json, _ = _run('drift', '--format', 'json')
        data = json.loads(out_json)
        self.assertEqual(data[0]['added'], [])
        self.assertEqual(len(data[0]['autoscaling']), 1)


class TestScan(Base):
    def test_scan_creates_job_and_records_snapshot(self):
        system, env = self._env()
        self._asset(env, 'sg-1', {'ingress_cidr': '10.0.0.0/8'}, {'ingress_cidr': '0.0.0.0/0'})
        fake = {'scanned': 3, 'created': 1, 'updated': 2, 'errors': []}
        with mock.patch('asset_manager.scanner.run_scan', return_value=fake) as m:
            out, code = _run('scan', '--system', 'sys-a')
        m.assert_called_once()
        self.assertEqual(code, 0)
        self.assertIn('scanned=3', out)
        self.assertEqual(ScanJob.objects.filter(status=ScanJob.Status.DONE).count(), 1)
        self.assertEqual(DriftSnapshot.objects.filter(environment=env).count(), 1)

    def test_scan_failure_exits_two_and_marks_job_failed(self):
        system, env = self._env()
        with mock.patch('asset_manager.scanner.run_scan', side_effect=RuntimeError('boom')):
            out, code = _run('scan', '--system', 'sys-a')
        self.assertEqual(code, 2)
        self.assertIn('FAILED', out)
        self.assertEqual(ScanJob.objects.filter(status=ScanJob.Status.FAILED).count(), 1)

    def test_scan_unknown_env_is_skipped(self):
        self._env()
        with mock.patch('asset_manager.scanner.run_scan') as m:
            _, code = _run('scan', '--system', 'sys-a', '--env', 'ghost')
        m.assert_not_called()
        self.assertEqual(code, 0)


class TestStatus(Base):
    def test_status_lists_environments_with_counts(self):
        system, env = self._env()
        self._asset(env, 'sg-1', {}, {'x': 1})
        self._asset(env, 'sg-2', {}, {'x': 1})
        out, code = _run('status')
        self.assertEqual(code, 0)
        self.assertIn('sys-a', out)
        self.assertIn('prod', out)

    def test_status_json(self):
        system, env = self._env()
        self._asset(env, 'sg-1', {}, {'x': 1})
        out, _code = _run('status', '--format', 'json')
        data = json.loads(out)
        self.assertEqual(data[0]['assets'], 1)
        self.assertEqual(data[0]['environment'], 'prod')


class TestDetachableSeam(TestCase):
    def test_cli_feature_is_advertised_by_the_plugin(self):
        from asset_manager.plugins import available_features, feature_enabled
        self.assertTrue(feature_enabled('cli'))
        self.assertIn('cli', available_features())

    def test_command_is_discoverable(self):
        from django.core.management import get_commands
        self.assertEqual(get_commands().get('syncvey'), 'syncvey_cli')
