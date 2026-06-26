"""
Tests for the drift-risk plugin: severity rules (pure), the gated views, and
CloudTrail attribution parsing (mocked — no AWS).
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from asset_manager.models import (
    Organization, System, Environment, Membership, Asset,
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
