"""
資産1件のドリフト区分を決める `asset_manager.drift.classify` のテスト。

この判定はかつて 4 箇所（環境バッジ・スナップショット記録・ドリフト
レポート・CLI）に手書きされていて、コアに区分を足すたびに一部だけが
取り残された。ここには 2 種類のテストがある:

  1. 規則そのもの（存在を先に、属性を後に）
  2. **4 つの呼び出し口が同じ答えを返すこと** — 写経が復活したら落ちる
"""

from django.test import TestCase
from django.utils import timezone

from asset_manager.drift import (
    classify, CHANGED, ADDED, REMOVED, AUTOSCALING, UNCHANGED,
)
from asset_manager.models import (
    Asset, DriftSnapshot, Environment, Organization, System,
)


class Base(TestCase):
    def setUp(self):
        org = Organization.objects.create(name='org', slug='org')
        self.system = System.objects.create(
            name='sys', code='sys', organization=org,
            aws_role_arn='arn:aws:iam::1:role/r', aws_scan_regions=['ap-northeast-1'],
        )
        self.env = Environment.objects.create(
            system=self.system, name='prod', env_type='PROD')

    def _asset(self, cloud_id, prev, cur, missing=False, asset_type='SG'):
        asset = Asset.objects.create(
            environment=self.env, name=cloud_id, provider='AWS',
            asset_type=asset_type, asset_category='NETWORK',
            cloud_id=cloud_id, raw_data_prev=prev, raw_data=cur,
        )
        if missing:
            # missing_since はスキャナが立てる印。テストからは直接更新する。
            Asset.objects.filter(pk=asset.pk).update(missing_since=timezone.now())
            asset.refresh_from_db()
        return asset


class TestClassifyRules(Base):
    def test_attribute_change_is_changed(self):
        asset = self._asset('sg-1', {'cidr': '10.0.0.0/8'}, {'cidr': '0.0.0.0/0'})
        category, changes = classify(asset)
        self.assertEqual(category, CHANGED)
        self.assertEqual(changes[0]['new'], '0.0.0.0/0')

    def test_identical_data_is_unchanged(self):
        asset = self._asset('sg-1', {'cidr': '10.0.0.0/8'}, {'cidr': '10.0.0.0/8'})
        self.assertEqual(classify(asset), (UNCHANGED, []))

    def test_first_sighting_is_added(self):
        asset = self._asset('sg-1', {}, {'cidr': '10.0.0.0/8'})
        self.assertEqual(classify(asset)[0], ADDED)

    def test_gone_from_aws_is_removed_not_added(self):
        # 消えた資産も raw_data_prev を持たない。存在を先に見なければ、
        # 削除が「初めて見た」と同じ枝に落ちて `+ added` と報告される。
        asset = self._asset('sqs-1', {}, {'name': 'queue'}, missing=True)
        self.assertEqual(classify(asset)[0], REMOVED)

    def test_existence_wins_over_attributes(self):
        # 差分もあり、かつ消えてもいる資産。消滅の方が上位。
        asset = self._asset('i-1', {'state': 'running'}, {'state': 'stopped'},
                            missing=True)
        self.assertEqual(classify(asset)[0], REMOVED)

    def test_asg_scale_out_is_churn(self):
        asset = self._asset('i-asg', {}, {'autoscaling_group': 'web', 'size': 't3.micro'})
        self.assertEqual(classify(asset)[0], AUTOSCALING)

    def test_asg_scale_in_is_churn(self):
        asset = self._asset('i-asg', {}, {'autoscaling_group': 'web'}, missing=True)
        self.assertEqual(classify(asset)[0], AUTOSCALING)

    def test_asg_attribute_change_is_still_real_drift(self):
        # 抑制するのは存在次元だけ。生きている ASG インスタンスの属性変更は
        # 本物のドリフト。
        asset = self._asset('i-asg',
                            {'autoscaling_group': 'web', 'instance_type': 't3.micro'},
                            {'autoscaling_group': 'web', 'instance_type': 't3.large'})
        self.assertEqual(classify(asset)[0], CHANGED)


class TestAllCallersAgree(Base):
    """環境バッジ・スナップショット・レポート・CLI が同じ数字を出すこと。"""

    def setUp(self):
        super().setUp()
        self._asset('sg-changed', {'cidr': '10.0.0.0/8'}, {'cidr': '0.0.0.0/0'})
        self._asset('sg-same', {'cidr': '10.0.0.0/8'}, {'cidr': '10.0.0.0/8'})
        self._asset('s3-new', {}, {'bucket': 'new'})
        self._asset('sqs-gone', {}, {'name': 'queue'}, missing=True)
        self._asset('i-asg', {}, {'autoscaling_group': 'web'})

    def test_badge_snapshot_and_cli_report_the_same_counts(self):
        from asset_manager.views import _get_env_drift_summary, _record_drift_snapshot
        from syncvey_cli.service import drift_for

        badge = _get_env_drift_summary(self.env)
        snapshot = _record_drift_snapshot(self.env, DriftSnapshot.Source.SCAN)
        cli = drift_for(self.env)

        self.assertEqual(badge['changed'], snapshot.changed_count, 'badge vs snapshot')
        self.assertEqual(badge['added'], snapshot.added_count)
        self.assertEqual(badge['removed'], snapshot.removed_count)

        self.assertEqual(len(cli['changed']), snapshot.changed_count, 'cli vs snapshot')
        self.assertEqual(len(cli['added']), snapshot.added_count)
        self.assertEqual(len(cli['removed']), snapshot.removed_count)
        self.assertEqual(len(cli['autoscaling']), len(snapshot.detail['autoscaling']))

        # 実データ: 変更1 / 追加1 / 削除1 / churn1 / 無変化1
        self.assertEqual((badge['changed'], badge['added'], badge['removed']), (1, 1, 1))
        self.assertEqual(badge['autoscaling'], 1)

    def test_drift_report_view_puts_each_asset_in_the_same_bucket(self):
        from asset_manager.views import _record_drift_snapshot

        snapshot = _record_drift_snapshot(self.env, DriftSnapshot.Source.SCAN)
        buckets = {}
        for asset in self.env.assets.all():
            buckets.setdefault(classify(asset)[0], []).append(asset.cloud_id)

        self.assertEqual(buckets[CHANGED], ['sg-changed'])
        self.assertEqual(buckets[ADDED], ['s3-new'])
        self.assertEqual(buckets[REMOVED], ['sqs-gone'])
        self.assertEqual(buckets[AUTOSCALING], ['i-asg'])
        self.assertEqual(len(buckets[UNCHANGED]), snapshot.unchanged_count)
