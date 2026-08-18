"""
消えたリソースの反映（prune）のテスト。

台帳は今まで upsert しかしておらず、AWS 側で消したリソースの行が
永久に残っていた。ここで見るのは主に 2 つ:

  1. 本当に消えたものに missing_since が立つこと（行は消さない）
  2. **スキャンが失敗した範囲では絶対に立たないこと** — 一過性の API
     エラーで台帳が壊れるのが、この機能で唯一許されない失敗だから。
"""

from unittest.mock import patch

import boto3
from django.test import TestCase
from django.utils import timezone
from moto import mock_aws

from asset_manager import scanner as scanner_mod
from asset_manager.models import Asset, Organization, System, Environment
from asset_manager.scanner import run_scan

REGION = 'ap-northeast-1'


def _make_env():
    org = Organization.objects.create(name='test-org')
    system = System.objects.create(
        name='test-system', code='test-system',
        organization=org, aws_scan_regions=[REGION],
    )
    env = Environment.objects.create(system=system, name='prod', env_type='PROD')
    return system, env


def _seed_scanned_asset(env, cloud_id, resource_type='aws_instance',
                        asset_type='EC2', region=REGION, extra=None):
    """live scan が書いたことになっている資産を 1 件仕込む。

    moto では EC2 の terminate が describe_instances から消えない等の癖が
    あるため、「前回スキャンでは見えていた」状態は直接作った方が正確。
    """
    raw = {
        'id':             cloud_id,
        '_resource_type': resource_type,
        '_scan_source':   'boto3',
    }
    raw.update(extra or {})
    return Asset.objects.create(
        environment=env, name=cloud_id, provider='AWS',
        asset_type=asset_type, asset_category='COMPUTE',
        cloud_id=cloud_id, region=region,
        raw_data=raw, last_imported_at=timezone.now(),
    )


def _broken_scanners(resource_type, exc=RuntimeError('throttled')):
    """指定した resource_type のスキャナだけが例外を投げる SCANNERS を返す。"""
    def boom(session):
        raise exc
    return [(rt, boom if rt == resource_type else fn)
            for rt, fn in scanner_mod.SCANNERS]


# ---------------------------------------------------------------------------
# 基本: 消えたら印がつく / 行は残る / 戻れば消える
# ---------------------------------------------------------------------------

@mock_aws
class TestMarksMissing(TestCase):

    def test_deleted_bucket_is_marked_missing(self):
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket='doomed-bucket')

        system, env = _make_env()
        run_scan(system, env)
        asset = Asset.objects.get(cloud_id='doomed-bucket')
        self.assertIsNone(asset.missing_since)

        s3.delete_bucket(Bucket='doomed-bucket')
        result = run_scan(system, env)

        asset.refresh_from_db()
        self.assertIsNotNone(asset.missing_since)
        self.assertEqual(result['missing'], 1)

    def test_row_is_kept_not_deleted(self):
        """ソフト削除であること。消えた事実ごと消してはいけない。"""
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket='doomed-bucket')

        system, env = _make_env()
        run_scan(system, env)
        s3.delete_bucket(Bucket='doomed-bucket')
        run_scan(system, env)

        self.assertTrue(Asset.objects.filter(cloud_id='doomed-bucket').exists())

    def test_reappearance_clears_the_flag(self):
        """誤検知しても再出現で自己修復すること。"""
        system, env = _make_env()
        asset = _seed_scanned_asset(env, 'back-again', 'aws_s3_bucket', 'S3')
        asset.missing_since = timezone.now()
        asset.save(update_fields=['missing_since'])

        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket='back-again')
        result = run_scan(system, env)

        asset.refresh_from_db()
        self.assertIsNone(asset.missing_since)
        self.assertEqual(result['reappeared'], 1)

    def test_missing_is_not_restamped_on_every_scan(self):
        """一度立てた時刻は動かさない（「いつ消えたか」が保たれる）。"""
        system, env = _make_env()
        asset = _seed_scanned_asset(env, 'long-gone', 'aws_s3_bucket', 'S3')

        run_scan(system, env)
        asset.refresh_from_db()
        first = asset.missing_since
        self.assertIsNotNone(first)

        result = run_scan(system, env)
        asset.refresh_from_db()
        self.assertEqual(asset.missing_since, first)
        self.assertEqual(result['missing'], 0)


# ---------------------------------------------------------------------------
# 肝: 見えていない範囲を「消えた」と言わない
# ---------------------------------------------------------------------------

@mock_aws
class TestNeverPrunesWhatItCouldNotSee(TestCase):

    def test_scanner_error_does_not_mark_anything_missing(self):
        """スキャナが落ちた resource_type は判定対象から丸ごと外れること。

        これが壊れると、AWS 側の一時的なスロットリングで台帳が全滅する。
        """
        system, env = _make_env()
        asset = _seed_scanned_asset(env, 'i-survivor')

        with patch.object(scanner_mod, 'SCANNERS', _broken_scanners('aws_instance')):
            result = run_scan(system, env)

        asset.refresh_from_db()
        self.assertIsNone(asset.missing_since)
        self.assertEqual(result['missing'], 0)
        self.assertTrue(any('aws_instance' in e for e in result['errors']))

    def test_error_in_one_type_does_not_block_another(self):
        """落ちたのは落ちた種別だけ。健全な種別の判定は普通に進む。"""
        system, env = _make_env()
        ec2_asset = _seed_scanned_asset(env, 'i-survivor')
        s3_asset  = _seed_scanned_asset(env, 'gone-bucket', 'aws_s3_bucket', 'S3')

        with patch.object(scanner_mod, 'SCANNERS', _broken_scanners('aws_instance')):
            run_scan(system, env)

        ec2_asset.refresh_from_db()
        s3_asset.refresh_from_db()
        self.assertIsNone(ec2_asset.missing_since)      # 見えなかったので触らない
        self.assertIsNotNone(s3_asset.missing_since)    # 見た上で無かった

    def test_session_error_marks_nothing_in_that_region(self):
        """リージョンごと繋がらなかった時、その region は判定しない。"""
        system, env = _make_env()
        asset = _seed_scanned_asset(env, 'i-unreachable')

        with patch.object(scanner_mod, 'get_session',
                          side_effect=RuntimeError('no credentials')):
            result = run_scan(system, env)

        asset.refresh_from_db()
        self.assertIsNone(asset.missing_since)
        self.assertEqual(result['missing'], 0)

    def test_tfstate_imported_assets_are_never_marked(self):
        """tfstate 由来の行は live scan の守備範囲外。勝手に消えたことにしない。"""
        system, env = _make_env()
        asset = Asset.objects.create(
            environment=env, name='from-tfstate', provider='AWS',
            asset_type='EC2', asset_category='COMPUTE',
            cloud_id='i-fromtfstate', region=REGION,
            # tfstate 取込は _scan_source を付けない
            raw_data={'id': 'i-fromtfstate', '_resource_type': 'aws_instance'},
            last_imported_at=timezone.now(),
        )

        run_scan(system, env)

        asset.refresh_from_db()
        self.assertIsNone(asset.missing_since)

    def test_other_environments_are_untouched(self):
        """スキャン対象の environment 以外は巻き込まない。"""
        system, env = _make_env()
        other_env = Environment.objects.create(
            system=system, name='stg', env_type='STG',
        )
        other = _seed_scanned_asset(other_env, 'i-otherenv')

        run_scan(system, env)

        other.refresh_from_db()
        self.assertIsNone(other.missing_since)


# ---------------------------------------------------------------------------
# ドリフトへの出方（ASG のスケールインは churn 扱い）
# ---------------------------------------------------------------------------

@mock_aws
class TestRemovedShowsUpInDrift(TestCase):

    def _summary(self, env):
        from asset_manager.views import _get_env_drift_summary
        return _get_env_drift_summary(env)

    def test_removed_counts_as_drift(self):
        system, env = _make_env()
        _seed_scanned_asset(env, 'i-deleted-by-hand')

        run_scan(system, env)

        summary = self._summary(env)
        self.assertEqual(summary['removed'], 1)
        self.assertEqual(summary['autoscaling'], 0)
        self.assertIn('removed', str(summary))

    def test_autoscaling_scale_in_is_not_drift(self):
        """ASG が消したインスタンスで叩き起こさない（cry-wolf 対策の対称側）。"""
        system, env = _make_env()
        _seed_scanned_asset(
            env, 'i-scaledin',
            extra={'autoscaling_group': 'web-asg',
                   'tags': {'aws:autoscaling:groupName': 'web-asg'}},
        )

        run_scan(system, env)

        summary = self._summary(env)
        self.assertEqual(summary['removed'], 0)
        self.assertEqual(summary['autoscaling'], 1)

    def test_snapshot_records_removed(self):
        from asset_manager.views import _record_drift_snapshot

        system, env = _make_env()
        _seed_scanned_asset(env, 'i-deleted-by-hand')
        run_scan(system, env)

        snapshot = _record_drift_snapshot(env, 'scan')
        self.assertEqual(snapshot.removed_count, 1)
        self.assertEqual(
            [r['cloud_id'] for r in snapshot.detail['removed']],
            ['i-deleted-by-hand'],
        )
        self.assertTrue(snapshot.has_drift)


# ---------------------------------------------------------------------------
# 台帳の見え方（既定で伏せる / 数は知らせる / 出せる）
# ---------------------------------------------------------------------------

class TestLedgerHidesMissing(TestCase):

    def setUp(self):
        from django.contrib.auth import get_user_model
        from asset_manager.models import Membership

        self.system, self.env = _make_env()
        User = get_user_model()
        user = User.objects.create_user(username='u', password='pw')
        Membership.objects.create(
            user=user, organization=self.system.organization,
            role=Membership.Role.OWNER,
        )
        self.client.force_login(user)

        self.alive = _seed_scanned_asset(self.env, 'i-alive')
        self.gone  = _seed_scanned_asset(self.env, 'i-gone')
        self.gone.missing_since = timezone.now()
        self.gone.save(update_fields=['missing_since'])

    def test_missing_asset_is_hidden_by_default(self):
        resp = self.client.get('/assets/')
        body = resp.content.decode()
        self.assertIn('i-alive', body)
        self.assertNotIn('i-gone', body)

    def test_count_is_reported_even_when_hidden(self):
        """黙って減らさない — 何件消えたかは必ず伝える。"""
        resp = self.client.get('/assets/')
        self.assertEqual(resp.context['missing_count'], 1)

    def test_show_missing_reveals_them(self):
        resp = self.client.get('/assets/?show_missing=1')
        self.assertIn('i-gone', resp.content.decode())

    def test_toggle_query_drops_show_missing(self):
        """「隠す」リンクが show_missing を引きずらないこと。"""
        resp = self.client.get('/assets/?show_missing=1&provider=AWS')
        self.assertNotIn('show_missing', resp.context['toggle_query'])
        self.assertIn('provider=AWS', resp.context['toggle_query'])


class TestDriftReportRendersRemoved(TestCase):
    """テンプレートまで実際に届いているかを見る（集計だけ直っても意味がない）。"""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from asset_manager.models import Membership

        self.system, self.env = _make_env()
        User = get_user_model()
        user = User.objects.create_user(username='u', password='pw')
        Membership.objects.create(
            user=user, organization=self.system.organization,
            role=Membership.Role.OWNER,
        )
        self.client.force_login(user)

    def test_removed_asset_appears_in_report(self):
        gone = _seed_scanned_asset(self.env, 'i-vanished')
        gone.missing_since = timezone.now()
        gone.save(update_fields=['missing_since'])

        body = self.client.get(f'/environments/{self.env.id}/drift/').content.decode()
        self.assertIn('i-vanished', body)
        self.assertIn('Removed', body)

    def test_scaled_in_asset_is_not_in_the_removed_section(self):
        gone = _seed_scanned_asset(
            self.env, 'i-scaledin',
            extra={'autoscaling_group': 'web-asg',
                   'tags': {'aws:autoscaling:groupName': 'web-asg'}},
        )
        gone.missing_since = timezone.now()
        gone.save(update_fields=['missing_since'])

        resp = self.client.get(f'/environments/{self.env.id}/drift/')
        self.assertEqual(len(resp.context['removed']), 0)
        self.assertEqual(len(resp.context['autoscaling']), 1)
