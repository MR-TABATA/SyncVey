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


class TestDriftHistoryRender(TestCase):
    """Happy-path render of the drift-history views with real snapshots."""

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

    def _snapshot(self, env, **kwargs):
        from asset_manager.models import DriftSnapshot
        return DriftSnapshot.objects.create(environment=env, **kwargs)

    def test_history_renders_snapshots(self):
        env = self._env()
        self._snapshot(env, source='scan', changed_count=2, added_count=1, unchanged_count=5)
        self._snapshot(env, source='tfstate', changed_count=0, added_count=0, unchanged_count=7)

        resp = self.client.get(f'/environments/{env.id}/drift/history/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, '_drift_history.html')
        self.assertEqual(len(resp.context['snapshots']), 2)
        # peak は total_count(changed+added) の最大 = 3
        self.assertEqual(resp.context['peak'], 3)

    def test_history_empty_has_zero_peak(self):
        env = self._env()
        resp = self.client.get(f'/environments/{env.id}/drift/history/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['peak'], 0)

    def test_snapshot_detail_renders_saved_diff(self):
        env = self._env()
        snap = self._snapshot(
            env, source='scan', changed_count=1, added_count=1,
            detail={
                'changed': [{'type': 'EC2', 'name': 'web', 'cloud_id': 'i-1',
                             'provider': 'AWS', 'changes': [{'key': 'instance_type'}]}],
                'added':   [{'type': 'S3', 'name': 'bucket', 'cloud_id': 'b-1',
                             'provider': 'AWS'}],
            },
        )
        resp = self.client.get(f'/environments/{env.id}/drift/history/{snap.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, '_drift_snapshot.html')
        self.assertEqual(len(resp.context['changed']), 1)
        self.assertEqual(len(resp.context['added']), 1)

    def test_snapshot_detail_handles_empty_detail(self):
        env = self._env()
        snap = self._snapshot(env, source='scan')  # detail defaults to {}
        resp = self.client.get(f'/environments/{env.id}/drift/history/{snap.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['changed'], [])
        self.assertEqual(resp.context['added'], [])

    def test_snapshot_detail_404_for_other_environment(self):
        from asset_manager.models import System, Environment
        env = self._env()
        other_system = System.objects.create(
            name='s2', code='s2', organization=env.system.organization)
        other_env = Environment.objects.create(
            system=other_system, name='stg', env_type='STG')
        snap = self._snapshot(other_env, source='scan')
        # snapshot belongs to a different environment in the same org -> 404
        resp = self.client.get(f'/environments/{env.id}/drift/history/{snap.id}/')
        self.assertEqual(resp.status_code, 404)

    @override_settings(SYNCVEY_FEATURES={'drift_history': False})
    def test_snapshot_detail_404_when_disabled(self):
        env = self._env()
        snap = self._snapshot(env, source='scan')
        resp = self.client.get(f'/environments/{env.id}/drift/history/{snap.id}/')
        self.assertEqual(resp.status_code, 404)
