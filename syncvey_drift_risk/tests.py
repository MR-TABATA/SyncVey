"""
Tests for the drift-risk plugin: severity rules (pure), the gated views, and
CloudTrail attribution parsing (mocked — no AWS).
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from asset_manager.models import (
    Organization, System, Environment, Membership, Asset, DriftSnapshot,
)
from syncvey_drift_risk import rules


# ---------------------------------------------------------------------------
# rules — severity classification (no AWS, no DB)
# ---------------------------------------------------------------------------

class TestRules(TestCase):

    def test_open_to_world_is_critical(self):
        sev, _ = rules.classify_change('SG', 'ingress_cidr', '10.0.0.0/8', '0.0.0.0/0')
        self.assertEqual(sev, rules.CRITICAL)

    def test_made_public_is_critical(self):
        sev, _ = rules.classify_change('RDS', 'publicly_accessible', 'false', 'true')
        self.assertEqual(sev, rules.CRITICAL)

    def test_encryption_disabled_is_high(self):
        sev, _ = rules.classify_change('S3', 'server_side_encryption', 'aws:kms', '')
        self.assertEqual(sev, rules.HIGH)

    def test_policy_change_is_high(self):
        sev, _ = rules.classify_change('IAM', 'assume_role_policy', '{...}', '{...changed}')
        self.assertEqual(sev, rules.HIGH)

    def test_safety_net_off_is_medium(self):
        sev, _ = rules.classify_change('RDS', 'deletion_protection', 'true', 'false')
        self.assertEqual(sev, rules.MEDIUM)

    def test_plain_change_is_low(self):
        sev, _ = rules.classify_change('EC2', 'instance_type', 't3.micro', 't3.small')
        self.assertEqual(sev, rules.LOW)

    def test_assess_takes_the_worst(self):
        changes = [
            {'field': 'instance_type', 'old': 't3.micro', 'new': 't3.small'},  # low
            {'field': 'ingress_cidr', 'old': '10.0.0.0/8', 'new': '0.0.0.0/0'},  # critical
        ]
        result = rules.assess('SG', changes)
        self.assertEqual(result['severity'], rules.CRITICAL)
        self.assertEqual(len(result['findings']), 2)


# ---------------------------------------------------------------------------
# rotation — standing "should have rotated but didn't" risk (no AWS, no DB)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone as _tz

from syncvey_drift_risk import rotation


class TestRotationRules(TestCase):

    NOW = datetime(2026, 7, 4, tzinfo=_tz.utc)

    def _ago(self, days):
        return (self.NOW - timedelta(days=days)).isoformat()

    def test_disabled_rotation_is_high(self):
        result = rotation.assess_rotation({'rotation_enabled': False}, self.NOW, 90)
        self.assertEqual(result['severity'], rules.HIGH)
        self.assertEqual(result['findings'][0]['field'], 'rotation_enabled')

    def test_enabled_but_never_rotated_is_medium(self):
        result = rotation.assess_rotation(
            {'rotation_enabled': True, 'last_rotated_date': ''}, self.NOW, 90)
        self.assertEqual(result['severity'], rules.MEDIUM)

    def test_recently_rotated_is_clean(self):
        result = rotation.assess_rotation(
            {'rotation_enabled': True, 'last_rotated_date': self._ago(10)}, self.NOW, 90)
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['severity'], rules.LOW)

    def test_overdue_is_high(self):
        result = rotation.assess_rotation(
            {'rotation_enabled': True, 'last_rotated_date': self._ago(100)}, self.NOW, 90)
        self.assertEqual(result['severity'], rules.HIGH)

    def test_severely_overdue_is_critical(self):
        result = rotation.assess_rotation(
            {'rotation_enabled': True, 'last_rotated_date': self._ago(200)}, self.NOW, 90)
        self.assertEqual(result['severity'], rules.CRITICAL)

    def test_truthy_string_flag_counts_as_enabled(self):
        # scan stores JSON; a "true"/"false" string must be read like the bool
        result = rotation.assess_rotation(
            {'rotation_enabled': 'true', 'last_rotated_date': self._ago(5)}, self.NOW, 90)
        self.assertEqual(result['findings'], [])


# ---------------------------------------------------------------------------
# views — gating + the risk list
# ---------------------------------------------------------------------------

class TestRiskViews(TestCase):

    def _setup(self, prev=None, cur=None, role='arn:aws:iam::1:role/r'):
        org = Organization.objects.create(name='o', slug='o')
        user = get_user_model().objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
        system = System.objects.create(name='s', code='s', organization=org, aws_role_arn=role)
        env = Environment.objects.create(system=system, name='prod', env_type='PROD')
        asset = Asset.objects.create(
            environment=env, name='sg-web', provider='AWS', asset_type='SG',
            asset_category='NETWORK', cloud_id='sg-123',
            raw_data_prev=prev if prev is not None else {'ingress_cidr': '10.0.0.0/8'},
            raw_data=cur if cur is not None else {'ingress_cidr': '0.0.0.0/0'},
        )
        self.client.force_login(user)
        return org, system, env, asset

    def test_list_flags_critical_change(self):
        self._setup()
        resp = self.client.get('/drift-risk/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total'], 1)
        self.assertEqual(resp.context['rows'][0]['severity'], rules.CRITICAL)
        self.assertEqual(resp.context['counts']['critical'], 1)

    def test_unchanged_resource_is_not_listed(self):
        self._setup(prev={'instance_type': 't3.micro'}, cur={'instance_type': 't3.micro'})
        resp = self.client.get('/drift-risk/')
        self.assertEqual(resp.context['total'], 0)

    def test_added_resource_is_not_listed(self):
        # no raw_data_prev → "added", not a graded *change*
        self._setup(prev={}, cur={'ingress_cidr': '0.0.0.0/0'})
        resp = self.client.get('/drift-risk/')
        self.assertEqual(resp.context['total'], 0)

    @override_settings(SYNCVEY_FEATURES={'drift_risk': False})
    def test_404_when_feature_disabled(self):
        self._setup()
        self.assertEqual(self.client.get('/drift-risk/').status_code, 404)

    @override_settings(SECRET_ROTATION_MAX_AGE_DAYS=90)
    def test_stale_secret_surfaces_without_a_diff(self):
        # a secret with no prior snapshot (no change) still lights up on its
        # standing rotation posture, and offers no "who changed this?" button.
        org, system, env, _ = self._setup(prev={}, cur={'ingress_cidr': '0.0.0.0/0'})
        old = (timezone.now() - timedelta(days=400)).isoformat()
        Asset.objects.create(
            environment=env, name='db-creds', provider='AWS',
            asset_type='SECRETS_MGR', asset_category='SECURITY', cloud_id='arn:secret:db',
            raw_data_prev={},
            raw_data={'_resource_type': 'aws_secretsmanager_secret',
                      'rotation_enabled': True, 'last_rotated_date': old},
        )
        resp = self.client.get('/drift-risk/')
        self.assertEqual(resp.context['total'], 1)
        row = resp.context['rows'][0]
        self.assertEqual(row['asset'].name, 'db-creds')
        self.assertEqual(row['severity'], rules.CRITICAL)
        self.assertFalse(row['has_change'])
        self.assertNotContains(resp, 'Who changed this?')

    def test_actor_no_role(self):
        _, system, _, asset = self._setup(role='')
        resp = self.client.get(f'/drift-risk/{asset.id}/actor/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['error'], 'no-role')

    def test_actor_returns_culprit(self):
        _, _, _, asset = self._setup()
        fake = {'user': 'tanaka', 'event': 'AuthorizeSecurityGroupIngress',
                'time': None, 'source_ip': '203.0.113.7'}
        with mock.patch('syncvey_drift_risk.views.get_session', return_value=object()), \
             mock.patch('syncvey_drift_risk.cloudtrail.lookup_actor', return_value=fake):
            resp = self.client.get(f'/drift-risk/{asset.id}/actor/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['actor']['user'], 'tanaka')

    def test_actor_404s_for_other_org(self):
        # asset belongs to org A; a B user must not attribute it
        _, _, _, asset = self._setup()
        other = get_user_model().objects.create_user(username='b', password='pw')
        org_b = Organization.objects.create(name='b', slug='b')
        Membership.objects.create(user=other, organization=org_b, role=Membership.Role.OWNER)
        self.client.force_login(other)
        self.assertEqual(self.client.get(f'/drift-risk/{asset.id}/actor/').status_code, 404)


# ---------------------------------------------------------------------------
# cloudtrail — event parsing (mocked client)
# ---------------------------------------------------------------------------

class TestCloudTrail(TestCase):

    def _session(self, events):
        client = mock.Mock()
        client.lookup_events.return_value = {'Events': events}
        session = mock.Mock()
        session.client.return_value = client
        return session

    def test_skips_read_events_and_parses_mutation(self):
        from syncvey_drift_risk.cloudtrail import lookup_actor
        events = [
            {'EventName': 'DescribeSecurityGroups', 'Username': 'reader'},
            {'EventName': 'AuthorizeSecurityGroupIngress', 'Username': 'tanaka',
             'CloudTrailEvent': '{"sourceIPAddress": "203.0.113.7", "userIdentity": {"userName": "tanaka"}}'},
        ]
        actor = lookup_actor(self._session(events), 'sg-123')
        self.assertEqual(actor['user'], 'tanaka')
        self.assertEqual(actor['event'], 'AuthorizeSecurityGroupIngress')
        self.assertEqual(actor['source_ip'], '203.0.113.7')

    def test_none_when_only_read_events(self):
        from syncvey_drift_risk.cloudtrail import lookup_actor
        events = [{'EventName': 'ListBuckets', 'Username': 'reader'}]
        self.assertIsNone(lookup_actor(self._session(events), 'sg-123'))

    def test_none_on_client_error(self):
        from syncvey_drift_risk.cloudtrail import lookup_actor
        session = mock.Mock()
        session.client.side_effect = Exception('AccessDenied')
        self.assertIsNone(lookup_actor(session, 'sg-123'))


# ---------------------------------------------------------------------------
# digest — the weekly briefing (build + send + scheduled-job seam)
# ---------------------------------------------------------------------------

from datetime import timedelta
from django.utils import timezone


class TestDigest(TestCase):

    def _system(self, webhook='https://hooks.slack.com/services/x', role='arn:aws:iam::1:role/r'):
        org = Organization.objects.create(name='o', slug='o')
        return System.objects.create(name='s', code='s', organization=org,
                                     slack_webhook_url=webhook, aws_role_arn=role)

    def _env(self, system, name='prod'):
        return Environment.objects.create(system=system, name=name, env_type='PROD')

    def _snap(self, env, changed_detail, changed=0, added=0, days_ago=0):
        snap = DriftSnapshot.objects.create(
            environment=env, changed_count=changed, added_count=added,
            detail={'changed': changed_detail, 'added': []})
        DriftSnapshot.objects.filter(pk=snap.pk).update(
            detected_at=timezone.now() - timedelta(days=days_ago))
        return snap

    def test_build_counts_severity_and_trend(self):
        from syncvey_drift_risk.digest import build_digest
        sys = self._system()
        env = self._env(sys)
        # baseline a week ago: 1 drift
        self._snap(env, [{'type': 'EC2', 'name': 'a', 'cloud_id': 'i-a',
                          'changes': [{'field': 'instance_type', 'old': 't3.micro', 'new': 't3.small'}]}],
                   changed=1, days_ago=8)
        # latest: a critical SG change (total 1)
        self._snap(env, [{'type': 'SG', 'name': 'web', 'cloud_id': 'sg-1',
                          'changes': [{'field': 'ingress_cidr', 'old': '10.0.0.0/8', 'new': '0.0.0.0/0'}]}],
                   changed=1, days_ago=0)
        d = build_digest(sys, attribute=False)
        self.assertEqual(d['severity_counts']['critical'], 1)
        self.assertEqual(d['total_now'], 1)
        self.assertEqual(d['delta'], 0)          # 1 now vs 1 at window start
        self.assertTrue(d['has_data'])
        self.assertEqual(d['top'][0]['severity'], rules.CRITICAL)

    def test_send_skips_when_no_drift(self):
        from syncvey_drift_risk.digest import send_digest
        sys = self._system()
        self._env(sys)  # no snapshots → nothing to report
        with mock.patch('asset_manager.notifications._post_to_slack', return_value=True) as post:
            self.assertFalse(send_digest(sys))
            post.assert_not_called()

    def test_send_posts_when_drift_present(self):
        from syncvey_drift_risk.digest import send_digest
        sys = self._system()
        env = self._env(sys)
        self._snap(env, [{'type': 'SG', 'name': 'web', 'cloud_id': 'sg-1',
                          'changes': [{'field': 'ingress_cidr', 'old': '10.0.0.0/8', 'new': '0.0.0.0/0'}]}],
                   changed=1)
        with mock.patch('asset_manager.notifications._post_to_slack', return_value=True) as post, \
             mock.patch('syncvey_drift_risk.digest._attach_actors'):
            self.assertTrue(send_digest(sys))
            post.assert_called_once()

    def test_send_skips_without_webhook(self):
        from syncvey_drift_risk.digest import send_digest
        sys = self._system(webhook='')
        env = self._env(sys)
        self._snap(env, [{'type': 'SG', 'name': 'w', 'cloud_id': 'sg-1',
                          'changes': [{'field': 'cidr', 'old': '', 'new': '0.0.0.0/0'}]}], changed=1)
        self.assertFalse(send_digest(sys))

    def test_preview_view_gating_and_render(self):
        sys = self._system()
        user = get_user_model().objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=sys.organization, role=Membership.Role.OWNER)
        env = self._env(sys)
        self._snap(env, [{'type': 'SG', 'name': 'web', 'cloud_id': 'sg-1',
                          'changes': [{'field': 'ingress_cidr', 'old': '10.0.0.0/8', 'new': '0.0.0.0/0'}]}],
                   changed=1)
        self.client.force_login(user)
        resp = self.client.get('/drift-digest/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['digests']), 1)
        self.assertEqual(resp.context['digests'][0]['severity_counts']['critical'], 1)

    @override_settings(SYNCVEY_FEATURES={'drift_risk': False})
    def test_preview_404_when_disabled(self):
        sys = self._system()
        user = get_user_model().objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=sys.organization, role=Membership.Role.OWNER)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/drift-digest/').status_code, 404)


class TestSchedulerSeam(TestCase):

    def test_digest_job_registered_only_when_enabled(self):
        from asset_manager.plugins import plugin_scheduled_jobs
        with override_settings(DRIFT_DIGEST_ENABLED=True):
            ids = {j['id'] for j in plugin_scheduled_jobs()}
            self.assertIn('drift_digest_weekly', ids)

    def test_no_digest_job_by_default(self):
        from asset_manager.plugins import plugin_scheduled_jobs
        with override_settings(DRIFT_DIGEST_ENABLED=False):
            ids = {j['id'] for j in plugin_scheduled_jobs()}
            self.assertNotIn('drift_digest_weekly', ids)
