from django.apps import AppConfig


class AssetManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'asset_manager'
    verbose_name = 'SyncVey'

    def ready(self):
        import os
        from django.db.models.signals import pre_save, post_save, post_delete
        from .signals import handle_pre_save, handle_post_save, handle_post_delete
        from .models import Asset, System, Environment, Application, AppEnvConfig, AppDependency

        for model in [Asset, System, Environment, Application, AppEnvConfig, AppDependency]:
            pre_save.connect(handle_pre_save, sender=model, weak=False)
            post_save.connect(handle_post_save, sender=model, weak=False)
            post_delete.connect(handle_post_delete, sender=model, weak=False)

        # runserver は2プロセス起動するので子プロセス(RUN_MAIN=true)のみで起動
        # pytest / manage.py test では起動しない
        from .scheduler_guard import should_start_scheduler
        if should_start_scheduler():
            from .scheduler import start
            start()
