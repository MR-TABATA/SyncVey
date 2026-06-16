"""
コアビジネスロジックのユニットテスト。

- _compute_raw_diff   : Drift検知の核心（純粋関数）
- resolve_resource_type: tfstateリソース → (asset_type, category) 解決
- run_scan            : Boto3スキャン → DB書き込みまでの統合
"""

import boto3
from moto import mock_aws
from django.test import TestCase

from asset_manager.views import _compute_raw_diff
from asset_manager.resource_registry import resolve_resource_type, resolve_provider
from asset_manager.scanner import run_scan
from asset_manager.models import Asset, System, Environment, Organization


# ---------------------------------------------------------------------------
# _compute_raw_diff
# ---------------------------------------------------------------------------

class TestComputeRawDiff(TestCase):

    def test_identical_dicts_return_no_diff(self):
        old = {'instance_type': 't3.micro', 'ami': 'ami-12345'}
        new = {'instance_type': 't3.micro', 'ami': 'ami-12345'}
        self.assertEqual(_compute_raw_diff(old, new), [])

    def test_detects_changed_value(self):
        old = {'instance_type': 't3.micro'}
        new = {'instance_type': 't3.small'}
        diffs = _compute_raw_diff(old, new)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]['field'], 'instance_type')
        self.assertEqual(diffs[0]['old'],   't3.micro')
        self.assertEqual(diffs[0]['new'],   't3.small')

    def test_ignores_field_only_in_new(self):
        """片側にしか無いキーは比較対象外（積集合比較）。

        tfstate(全属性) と ライブスキャン(厳選キー) はスキーマが非対称で、
        片側だけに在るキーを差分扱いすると誤検知の山になる。実ドリフトは
        共通キー上で起きるため、増減は無視するのが正しい挙動。
        """
        old = {}
        new = {'subnet_id': 'subnet-abc'}
        self.assertEqual(_compute_raw_diff(old, new), [])

    def test_ignores_field_only_in_old(self):
        """片側(old)にしか無いキーも比較対象外（積集合比較）。"""
        old = {'public_ip': '1.2.3.4'}
        new = {}
        self.assertEqual(_compute_raw_diff(old, new), [])

    def test_excludes_noise_fields(self):
        """tags_all / arn / tags / timeouts は除外されること"""
        old = {'instance_type': 't3.micro', 'tags_all': {'env': 'prod'}, 'arn': 'arn:old'}
        new = {'instance_type': 't3.micro', 'tags_all': {'env': 'stg'},  'arn': 'arn:new'}
        self.assertEqual(_compute_raw_diff(old, new), [])

    def test_detects_multiple_changes(self):
        old = {'instance_type': 't3.micro', 'ami': 'ami-old', 'vpc_id': 'vpc-1'}
        new = {'instance_type': 't3.small', 'ami': 'ami-new', 'vpc_id': 'vpc-1'}
        diffs = _compute_raw_diff(old, new)
        self.assertEqual(len(diffs), 2)
        changed_fields = {d['field'] for d in diffs}
        self.assertIn('instance_type', changed_fields)
        self.assertIn('ami',           changed_fields)

    def test_empty_both_returns_empty(self):
        self.assertEqual(_compute_raw_diff({}, {}), [])

    def test_result_is_sorted_by_field_name(self):
        old = {'z_field': 'a', 'a_field': 'b'}
        new = {'z_field': 'x', 'a_field': 'y'}
        diffs = _compute_raw_diff(old, new)
        self.assertEqual(diffs[0]['field'], 'a_field')
        self.assertEqual(diffs[1]['field'], 'z_field')


# ---------------------------------------------------------------------------
# resolve_resource_type / resolve_provider
# ---------------------------------------------------------------------------

class TestResolveResourceType(TestCase):

    def test_ec2_instance(self):
        asset_type, category = resolve_resource_type('aws_instance')
        self.assertEqual(asset_type, 'EC2')
        self.assertEqual(category,   'COMPUTE')

    def test_rds_instance(self):
        asset_type, category = resolve_resource_type('aws_db_instance')
        self.assertEqual(asset_type, 'RDS')
        self.assertEqual(category,   'DATABASE')

    def test_s3_bucket(self):
        asset_type, category = resolve_resource_type('aws_s3_bucket')
        self.assertEqual(asset_type, 'S3')
        self.assertEqual(category,   'STORAGE')

    def test_lambda_function(self):
        asset_type, category = resolve_resource_type('aws_lambda_function')
        self.assertEqual(asset_type, 'LAMBDA')
        self.assertEqual(category,   'COMPUTE')

    def test_ecs_service_defaults_to_ecs(self):
        asset_type, _ = resolve_resource_type('aws_ecs_service', {})
        self.assertEqual(asset_type, 'ECS')

    def test_ecs_fargate_detection(self):
        """launch_type=FARGATE のとき asset_type が FARGATE になること"""
        asset_type, _ = resolve_resource_type('aws_ecs_service', {'launch_type': 'FARGATE'})
        self.assertEqual(asset_type, 'FARGATE')

    def test_ecs_fargate_case_insensitive(self):
        asset_type, _ = resolve_resource_type('aws_ecs_service', {'launch_type': 'fargate'})
        self.assertEqual(asset_type, 'FARGATE')

    def test_unknown_resource_returns_something(self):
        """未登録リソースはクラッシュせず OTHER 系を返すこと"""
        asset_type, category = resolve_resource_type('aws_some_brand_new_service')
        self.assertIsNotNone(asset_type)
        self.assertIsNotNone(category)

    def test_resolve_provider_aws(self):
        self.assertEqual(resolve_provider('aws_instance'), 'AWS')

    def test_resolve_provider_unknown_prefix(self):
        self.assertEqual(resolve_provider('azure_virtual_machine'), 'OTHER')


# ---------------------------------------------------------------------------
# run_scan  (Moto + DB)
# ---------------------------------------------------------------------------

def _make_env():
    """テスト用の System / Environment を作成して返す。"""
    org = Organization.objects.create(name='test-org')
    system = System.objects.create(
        name='test-system',
        code='test-system',
        organization=org,
        aws_scan_regions=['ap-northeast-1'],
    )
    env = Environment.objects.create(
        system=system,
        name='prod',
        env_type='PROD',
    )
    return system, env


@mock_aws
class TestRunScan(TestCase):

    def test_creates_assets_from_ec2(self):
        ec2 = boto3.client('ec2', region_name='ap-northeast-1')
        ec2.run_instances(ImageId='ami-12345678', MinCount=2, MaxCount=2, InstanceType='t3.micro')

        system, env = _make_env()
        result = run_scan(system, env)

        self.assertEqual(result['errors'], [])
        self.assertEqual(Asset.objects.filter(environment=env, asset_type='EC2').count(), 2)

    def test_creates_assets_from_rds(self):
        rds = boto3.client('rds', region_name='ap-northeast-1')
        rds.create_db_instance(
            DBInstanceIdentifier='prod-db',
            DBInstanceClass='db.t3.micro',
            Engine='mysql',
            MasterUsername='admin',
            MasterUserPassword='password',
            AllocatedStorage=20,
        )

        system, env = _make_env()
        result = run_scan(system, env)

        self.assertEqual(result['errors'], [])
        asset = Asset.objects.get(environment=env, asset_type='RDS')
        self.assertEqual(asset.cloud_id, 'prod-db')

    def test_second_scan_updates_raw_data_prev(self):
        """2回目のスキャンで raw_data_prev に前回値が保存されること（Drift比較の前提）"""
        ec2 = boto3.client('ec2', region_name='ap-northeast-1')
        ec2.run_instances(ImageId='ami-12345678', MinCount=1, MaxCount=1, InstanceType='t3.micro')

        system, env = _make_env()
        run_scan(system, env)         # 1回目: created
        run_scan(system, env)         # 2回目: updated

        asset = Asset.objects.get(environment=env, asset_type='EC2')
        self.assertNotEqual(asset.raw_data_prev, {})  # 前回値が保存されている

    def test_empty_aws_account_has_no_ec2_or_rds(self):
        """手動作成リソースがなければEC2・RDSは0件（VPC等のデフォルトリソースは除く）"""
        system, env = _make_env()
        run_scan(system, env)
        self.assertEqual(Asset.objects.filter(environment=env, asset_type='EC2').count(), 0)
        self.assertEqual(Asset.objects.filter(environment=env, asset_type='RDS').count(), 0)

    def test_result_counts_are_consistent(self):
        ec2 = boto3.client('ec2', region_name='ap-northeast-1')
        ec2.run_instances(ImageId='ami-12345678', MinCount=3, MaxCount=3, InstanceType='t3.micro')

        system, env = _make_env()
        result = run_scan(system, env)

        self.assertEqual(result['scanned'], result['created'] + result['updated'])


# ---------------------------------------------------------------------------
# _record_drift_snapshot  (Drift履歴)
# ---------------------------------------------------------------------------

class TestRecordDriftSnapshot(TestCase):

    def _env_with_asset(self):
        org = Organization.objects.create(name='drift-org')
        system = System.objects.create(name='s', code='s', organization=org)
        env = Environment.objects.create(system=system, name='prod', env_type='PROD')
        return env

    def test_no_snapshot_for_empty_environment(self):
        from asset_manager.views import _record_drift_snapshot
        from asset_manager.models import DriftSnapshot
        env = self._env_with_asset()
        self.assertIsNone(_record_drift_snapshot(env, DriftSnapshot.Source.SCAN))
        self.assertEqual(DriftSnapshot.objects.count(), 0)

    def test_new_asset_counts_as_added(self):
        from asset_manager.views import _record_drift_snapshot
        from asset_manager.models import DriftSnapshot
        env = self._env_with_asset()
        Asset.objects.create(
            environment=env, name='ec2-1', provider='AWS', asset_type='EC2',
            asset_category='COMPUTE', cloud_id='i-aaa',
            raw_data={'instance_type': 't3.micro'},  # raw_data_prev は空 → ADDED
        )
        snap = _record_drift_snapshot(env, DriftSnapshot.Source.SCAN)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.added_count, 1)
        self.assertEqual(snap.changed_count, 0)
        self.assertEqual(len(snap.detail['added']), 1)

    def test_changed_asset_captures_field_diff(self):
        from asset_manager.views import _record_drift_snapshot
        from asset_manager.models import DriftSnapshot
        env = self._env_with_asset()
        Asset.objects.create(
            environment=env, name='ec2-1', provider='AWS', asset_type='EC2',
            asset_category='COMPUTE', cloud_id='i-bbb',
            raw_data_prev={'instance_type': 't3.micro'},
            raw_data={'instance_type': 't3.small'},
        )
        snap = _record_drift_snapshot(env, DriftSnapshot.Source.TFSTATE)
        self.assertEqual(snap.changed_count, 1)
        self.assertEqual(snap.added_count, 0)
        self.assertEqual(snap.detail['changed'][0]['changes'][0]['field'], 'instance_type')
        self.assertEqual(snap.source, DriftSnapshot.Source.TFSTATE)
