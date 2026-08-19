"""
Auto Scaling drift suppression (cry-wolf fix).

An ASG-owned instance appearing in a scan is churn, not drift. Ownership comes
from the reserved `aws:autoscaling:groupName` tag we already scan — no extra API
call or IAM permission.
"""

import boto3
from moto import mock_aws
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from asset_manager.autoscaling import (
    autoscaling_group_of, is_autoscaling_managed, is_autoscaling_churn,
)
from asset_manager.models import (
    Asset, DriftSnapshot, Environment, Membership, Organization, System,
)
from asset_manager.scanner import scan_ec2

REGION = 'ap-northeast-1'
ASG_TAG = 'aws:autoscaling:groupName'


# ---------------------------------------------------------------------------
# helper — the single source of truth
# ---------------------------------------------------------------------------

class TestOwnershipHelper(TestCase):

    def test_explicit_key(self):
        self.assertEqual(autoscaling_group_of({'autoscaling_group': 'web-asg'}), 'web-asg')

    def test_tag_fallback_for_older_assets(self):
        # rows scanned before the explicit key existed still classify correctly
        self.assertEqual(autoscaling_group_of({'tags': {ASG_TAG: 'legacy-asg'}}), 'legacy-asg')

    def test_none_when_unmanaged(self):
        self.assertIsNone(autoscaling_group_of({'tags': {'Name': 'hand-made'}}))
        self.assertIsNone(autoscaling_group_of({}))
        self.assertIsNone(autoscaling_group_of(None))

    def test_is_managed(self):
        self.assertTrue(is_autoscaling_managed({'autoscaling_group': 'a'}))
        self.assertFalse(is_autoscaling_managed({'autoscaling_group': ''}))

    @override_settings(DRIFT_SUPPRESS_AUTOSCALING=True)
    def test_churn_when_enabled(self):
        self.assertTrue(is_autoscaling_churn({'autoscaling_group': 'a'}))

    @override_settings(DRIFT_SUPPRESS_AUTOSCALING=False)
    def test_not_churn_when_disabled(self):
        self.assertFalse(is_autoscaling_churn({'autoscaling_group': 'a'}))


# ---------------------------------------------------------------------------
# scanner — surfaces the tag with no extra call
# ---------------------------------------------------------------------------

@mock_aws
class TestScannerSurfacesAsg(TestCase):

    def test_unmanaged_instance_has_empty_group(self):
        boto3.client('ec2', region_name=REGION).run_instances(
            ImageId='ami-12345678', MinCount=1, MaxCount=1, InstanceType='t3.micro',
        )
        self.assertEqual(scan_ec2(boto3.Session(region_name=REGION))[0]['autoscaling_group'], '')

    def test_asg_tag_becomes_autoscaling_group(self):
        boto3.client('ec2', region_name=REGION).run_instances(
            ImageId='ami-12345678', MinCount=1, MaxCount=1,
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [{'Key': ASG_TAG, 'Value': 'web-asg'}],
            }],
        )
        result = scan_ec2(boto3.Session(region_name=REGION))[0]
        self.assertEqual(result['autoscaling_group'], 'web-asg')
        self.assertTrue(is_autoscaling_managed(result))


# ---------------------------------------------------------------------------
# drift snapshot + summary chokepoints
# ---------------------------------------------------------------------------

class TestDriftSuppression(TestCase):

    def _env(self):
        org = Organization.objects.create(name='asg-org', slug='asg-org')
        system = System.objects.create(name='s', code='s', organization=org)
        return Environment.objects.create(system=system, name='prod', env_type='PROD')

    def _asg_instance(self, env, cloud_id='i-asg'):
        # first sighting (no raw_data_prev) that an ASG owns
        return Asset.objects.create(
            environment=env, name=cloud_id, provider='AWS', asset_type='EC2',
            asset_category='COMPUTE', cloud_id=cloud_id,
            raw_data={'instance_type': 't3.micro', 'autoscaling_group': 'web-asg'},
        )

    def test_snapshot_buckets_asg_churn_out_of_added(self):
        from asset_manager.views import _record_drift_snapshot
        env = self._env()
        self._asg_instance(env)
        snap = _record_drift_snapshot(env, DriftSnapshot.Source.SCAN)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.added_count, 0)
        self.assertEqual(snap.total_count, 0)          # not drift
        self.assertEqual(len(snap.detail['autoscaling']), 1)

    def test_summary_excludes_asg_from_added(self):
        from asset_manager.views import _get_env_drift_summary
        env = self._env()
        self._asg_instance(env, 'i-asg1')
        # one genuinely-new hand-made instance
        Asset.objects.create(
            environment=env, name='i-manual', provider='AWS', asset_type='EC2',
            asset_category='COMPUTE', cloud_id='i-manual',
            raw_data={'instance_type': 't3.micro'},
        )
        s = _get_env_drift_summary(env)
        self.assertEqual(s['added'], 1)
        self.assertEqual(s['autoscaling'], 1)
        self.assertEqual(s['total'], 1)                # only the hand-made add

    @override_settings(DRIFT_SUPPRESS_AUTOSCALING=False)
    def test_disabling_suppression_counts_asg_as_added(self):
        from asset_manager.views import _get_env_drift_summary
        env = self._env()
        self._asg_instance(env)
        s = _get_env_drift_summary(env)
        self.assertEqual(s['added'], 1)
        self.assertEqual(s['autoscaling'], 0)

    def test_attribute_change_on_asg_instance_is_still_drift(self):
        # suppression is only for the *existence* dimension; a real change stays
        from asset_manager.views import _get_env_drift_summary
        env = self._env()
        Asset.objects.create(
            environment=env, name='i-asg', provider='AWS', asset_type='EC2',
            asset_category='COMPUTE', cloud_id='i-asg',
            raw_data_prev={'instance_type': 't3.micro', 'autoscaling_group': 'web-asg'},
            raw_data={'instance_type': 't3.large', 'autoscaling_group': 'web-asg'},
        )
        s = _get_env_drift_summary(env)
        self.assertEqual(s['changed'], 1)
        self.assertEqual(s['total'], 1)


# ---------------------------------------------------------------------------
# drift report view — transparent, separate section
# ---------------------------------------------------------------------------

class TestDriftReportView(TestCase):

    def _login_env(self):
        org = Organization.objects.create(name='v-org', slug='v-org')
        user = get_user_model().objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
        system = System.objects.create(name='s', code='s', organization=org)
        env = Environment.objects.create(system=system, name='prod', env_type='PROD')
        self.client.force_login(user)
        return env

    def test_asg_churn_lands_in_its_own_bucket_not_added(self):
        env = self._login_env()
        Asset.objects.create(
            environment=env, name='i-asg', provider='AWS', asset_type='EC2',
            asset_category='COMPUTE', cloud_id='i-asg',
            raw_data={'instance_type': 't3.micro', 'autoscaling_group': 'web-asg'},
        )
        resp = self.client.get(f'/environments/{env.id}/drift/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['added']), 0)
        self.assertEqual(len(resp.context['autoscaling']), 1)
        self.assertEqual(resp.context['autoscaling'][0]['group'], 'web-asg')
