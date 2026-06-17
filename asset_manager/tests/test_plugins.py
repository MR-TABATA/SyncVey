"""
Feature-flag / plugin discovery tests (asset_manager/plugins.py).

These exercise the seams that keep optional features detachable:
flag resolution order, the template-facing feature map, and graceful behavior
when no plugin apps are installed.
"""

from django.test import TestCase, override_settings

from asset_manager import plugins


class TestFeatureEnabled(TestCase):

    def test_core_default_is_used_when_no_override(self):
        # drift_history ships enabled in core by default
        self.assertTrue(plugins.feature_enabled('drift_history'))

    def test_unknown_feature_is_disabled(self):
        self.assertFalse(plugins.feature_enabled('does_not_exist'))

    @override_settings(SYNCVEY_FEATURES={'drift_history': False})
    def test_settings_override_can_disable_a_core_feature(self):
        self.assertFalse(plugins.feature_enabled('drift_history'))

    @override_settings(SYNCVEY_FEATURES={'cost_estimate': True})
    def test_settings_override_can_enable_an_unknown_feature(self):
        self.assertTrue(plugins.feature_enabled('cost_estimate'))


class TestAvailableFeatures(TestCase):

    def test_includes_core_defaults(self):
        feats = plugins.available_features()
        self.assertIn('drift_history', feats)
        self.assertTrue(feats['drift_history'])

    @override_settings(SYNCVEY_FEATURES={'drift_history': False})
    def test_reflects_overrides(self):
        self.assertFalse(plugins.available_features()['drift_history'])


class TestPluginDiscovery(TestCase):

    def test_no_plugin_apps_yields_no_nav_items(self):
        # No app in this project sets syncvey_plugin = True yet.
        self.assertEqual(plugins.plugin_nav_items(), [])

    def test_plugin_feature_names_empty_without_plugins(self):
        self.assertEqual(plugins._plugin_feature_names(), set())


class TestDriftHistoryGating(TestCase):
    """The drift-history views must 404 when the feature flag is off."""

    def _env(self):
        from asset_manager.models import Organization, System, Environment, Membership
        from django.contrib.auth import get_user_model
        User = get_user_model()
        org = Organization.objects.create(name='org')
        user = User.objects.create_user(username='u', password='pw')
        Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
        system = System.objects.create(name='s', code='s', organization=org)
        env = Environment.objects.create(system=system, name='prod', env_type='PROD')
        self.client.force_login(user)
        return env

    def test_history_reachable_when_enabled(self):
        env = self._env()
        resp = self.client.get(f'/environments/{env.id}/drift/history/')
        self.assertEqual(resp.status_code, 200)

    @override_settings(SYNCVEY_FEATURES={'drift_history': False})
    def test_history_404_when_disabled(self):
        env = self._env()
        resp = self.client.get(f'/environments/{env.id}/drift/history/')
        self.assertEqual(resp.status_code, 404)
