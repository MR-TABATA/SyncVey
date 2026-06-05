"""
通知モジュールのユニットテスト。
Slack への HTTP リクエストは unittest.mock でモックする。
"""

from unittest.mock import MagicMock, patch
from django.test import TestCase

from asset_manager.notifications import send_drift_notification, _build_slack_message
from asset_manager.models import Organization, System, Environment


def _make_system_env(slack_url=None):
    org    = Organization.objects.create(name='notify-test-org')
    system = System.objects.create(
        name='決済システム', code='payment',
        organization=org,
        slack_webhook_url=slack_url,
    )
    env = Environment.objects.create(system=system, name='prod', env_type='PROD')
    return system, env


class TestSendDriftNotification(TestCase):

    def test_no_webhook_returns_false(self):
        system, env = _make_system_env(slack_url=None)
        result = {'created': 1, 'updated': 2, 'errors': []}
        self.assertFalse(send_drift_notification(system, env, result))

    def test_no_drift_returns_false(self):
        system, env = _make_system_env(slack_url='https://hooks.slack.com/fake')
        result = {'created': 0, 'updated': 0, 'errors': []}
        self.assertFalse(send_drift_notification(system, env, result))

    def test_sends_when_drift_detected(self):
        system, env = _make_system_env(slack_url='https://hooks.slack.com/fake')
        result = {'created': 0, 'updated': 3, 'errors': []}

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch('urllib.request.urlopen', return_value=mock_resp) as mock_post:
            sent = send_drift_notification(system, env, result)

        self.assertTrue(sent)
        mock_post.assert_called_once()

    def test_sends_when_new_resources_found(self):
        system, env = _make_system_env(slack_url='https://hooks.slack.com/fake')
        result = {'created': 2, 'updated': 0, 'errors': []}

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch('urllib.request.urlopen', return_value=mock_resp):
            sent = send_drift_notification(system, env, result)

        self.assertTrue(sent)

    def test_slack_error_returns_false(self):
        import urllib.error
        system, env = _make_system_env(slack_url='https://hooks.slack.com/fake')
        result = {'created': 1, 'updated': 1, 'errors': []}

        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')):
            sent = send_drift_notification(system, env, result)

        self.assertFalse(sent)

    def test_rejects_ssrf_and_non_slack_webhooks(self):
        """SSRF / file:// / 非Slackホスト / 非https は送信せず urlopen も呼ばない。"""
        org = Organization.objects.create(name='ssrf-org')
        result = {'created': 1, 'updated': 1, 'errors': []}
        bad_urls = [
            'http://169.254.169.254/latest/meta-data/',  # メタデータSSRF
            'file:///etc/passwd',                         # ローカルファイル
            'https://evil.example.com/hook',              # 非Slackホスト
            'http://hooks.slack.com/services/x',          # 非https
        ]
        for i, bad in enumerate(bad_urls):
            system = System.objects.create(
                name=f'ssrf-sys-{i}', code=f'ssrf-{i}',
                organization=org, slack_webhook_url=bad,
            )
            env = Environment.objects.create(system=system, name='prod', env_type='PROD')
            with patch('urllib.request.urlopen') as mock_post:
                sent = send_drift_notification(system, env, result)
            self.assertFalse(sent, f"should block: {bad}")
            mock_post.assert_not_called()


class TestBuildSlackMessage(TestCase):

    def test_message_contains_system_name(self):
        system, env = _make_system_env()
        msg = _build_slack_message(system, env, created=0, updated=2)
        text = str(msg)
        self.assertIn('決済システム', text)

    def test_message_contains_env_info(self):
        system, env = _make_system_env()
        msg = _build_slack_message(system, env, created=0, updated=1)
        text = str(msg)
        self.assertIn('prod', text)
        self.assertIn('PROD', text)

    def test_message_mentions_changed_count(self):
        system, env = _make_system_env()
        msg = _build_slack_message(system, env, created=0, updated=5)
        text = str(msg)
        self.assertIn('5', text)
        self.assertIn('changed', text)

    def test_message_mentions_added_count(self):
        system, env = _make_system_env()
        msg = _build_slack_message(system, env, created=3, updated=0)
        text = str(msg)
        self.assertIn('3', text)
        self.assertIn('added', text)
