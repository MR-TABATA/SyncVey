"""
機密情報検出・スクラブのユニットテスト。
"""

from django.test import TestCase

from asset_manager.views import _detect_secrets, _scrub_secrets


class TestDetectSecrets(TestCase):

    def test_no_secrets_returns_empty(self):
        data = {
            'resources': [{
                'mode': 'managed', 'type': 'aws_instance', 'name': 'web',
                'instances': [{'attributes': {'id': 'i-123', 'instance_type': 't3.micro'}}],
            }]
        }
        self.assertEqual(_detect_secrets(data), {})

    def test_detects_password_field(self):
        data = {
            'resources': [{
                'mode': 'managed', 'type': 'aws_db_instance', 'name': 'db',
                'instances': [{'attributes': {'id': 'mydb', 'password': 'supersecret'}}],
            }]
        }
        found = _detect_secrets(data)
        self.assertIn('password', found)
        self.assertEqual(found['password'], 1)

    def test_detects_multiple_secret_fields(self):
        data = {
            'resources': [{
                'mode': 'managed', 'type': 'aws_db_instance', 'name': 'db',
                'instances': [{'attributes': {
                    'password': 'secret1',
                    'master_password': 'secret2',
                    'access_key': 'AKIAIOSFODNN7EXAMPLE',
                }}],
            }]
        }
        found = _detect_secrets(data)
        self.assertEqual(len(found), 3)

    def test_counts_across_multiple_resources(self):
        data = {
            'resources': [
                {
                    'mode': 'managed', 'type': 'aws_db_instance', 'name': 'db1',
                    'instances': [{'attributes': {'password': 'secret1'}}],
                },
                {
                    'mode': 'managed', 'type': 'aws_db_instance', 'name': 'db2',
                    'instances': [{'attributes': {'password': 'secret2'}}],
                },
            ]
        }
        found = _detect_secrets(data)
        self.assertEqual(found['password'], 2)

    def test_ignores_already_scrubbed_values(self):
        data = {
            'resources': [{
                'mode': 'managed', 'type': 'aws_db_instance', 'name': 'db',
                'instances': [{'attributes': {'password': '***'}}],
            }]
        }
        self.assertEqual(_detect_secrets(data), {})

    def test_ignores_data_sources(self):
        data = {
            'resources': [{
                'mode': 'data',  # data source, not managed
                'type': 'aws_db_instance', 'name': 'db',
                'instances': [{'attributes': {'password': 'secret'}}],
            }]
        }
        self.assertEqual(_detect_secrets(data), {})

    def test_empty_tfstate_returns_empty(self):
        self.assertEqual(_detect_secrets({}), {})
        self.assertEqual(_detect_secrets({'resources': []}), {})


class TestScrubSecrets(TestCase):

    def test_scrubs_password(self):
        attrs = {'id': 'mydb', 'password': 'supersecret', 'engine': 'mysql'}
        scrubbed = _scrub_secrets(attrs)
        self.assertEqual(scrubbed['password'], '***')
        self.assertEqual(scrubbed['id'], 'mydb')
        self.assertEqual(scrubbed['engine'], 'mysql')

    def test_scrubs_multiple_patterns(self):
        attrs = {
            'password':    'p1',
            'token':       't1',
            'secret_key':  'k1',
            'access_key':  'ak1',
            'normal_field': 'ok',
        }
        scrubbed = _scrub_secrets(attrs)
        self.assertEqual(scrubbed['password'],    '***')
        self.assertEqual(scrubbed['token'],       '***')
        self.assertEqual(scrubbed['secret_key'],  '***')
        self.assertEqual(scrubbed['access_key'],  '***')
        self.assertEqual(scrubbed['normal_field'], 'ok')

    def test_non_secret_fields_unchanged(self):
        attrs = {'id': 'i-123', 'instance_type': 't3.micro', 'ami': 'ami-12345'}
        self.assertEqual(_scrub_secrets(attrs), attrs)
