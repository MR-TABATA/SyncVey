"""
scheduler.py
------------
django-apscheduler を使った定期 Boto3 スキャン。

設計:
  - 1分ごとに _tick() が走り、スキャン期限を超えた System を検出して run_scan() を呼ぶ
  - ジョブ重複防止: max_instances=1, coalesce=True
  - 開発サーバーの二重起動対策: os.environ['RUN_MAIN'] をチェック
"""

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.utils import timezone
from django_apscheduler.jobstores import DjangoJobStore

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------------------
# ジョブ本体
# ---------------------------------------------------------------------------

def _tick():
    """
    1分ごとに呼ばれる。scan_enabled=True かつ期限超過の System をスキャンする。
    """
    from .models import System, ScanJob
    from .scanner import run_scan

    now = timezone.now()

    for system in System.objects.filter(scan_enabled=True):
        if not system.aws_scan_regions:
            continue

        # 最後の完了スキャンからの経過分数を計算
        last = (
            ScanJob.objects
            .filter(system=system, status__in=[ScanJob.Status.DONE, ScanJob.Status.FAILED])
            .order_by('-finished_at')
            .first()
        )
        if last and last.finished_at:
            elapsed = (now - last.finished_at).total_seconds() / 60
            if elapsed < system.scan_interval_minutes:
                continue

        # スキャン実行
        for env in system.environments.all():
            job = ScanJob.objects.create(
                system=system,
                status=ScanJob.Status.RUNNING,
                regions=system.aws_scan_regions,
                started_at=now,
            )
            try:
                result = run_scan(system, env)
                job.status        = ScanJob.Status.DONE
                job.created_count = result['created']
                job.updated_count = result['updated']
                job.finished_at   = timezone.now()
                if result['errors']:
                    job.error_message = '\n'.join(result['errors'])
                # Drift 通知
                from .notifications import send_drift_notification
                send_drift_notification(system, env, result)
            except Exception as exc:
                logger.exception("Scheduled scan failed: system=%s env=%s", system.name, env.name)
                job.status        = ScanJob.Status.FAILED
                job.error_message = str(exc)
                job.finished_at   = timezone.now()
            finally:
                job.save()

    # S3 Remote State 自動同期（scan_enabled に依存しない独立ループ）
    from .models import Environment as _Env
    from .views import sync_s3_state_core
    for env in _Env.objects.filter(backend_type=_Env.BackendType.S3, s3_auto_sync=True).select_related('system'):
        try:
            result = sync_s3_state_core(env)
            if result['errors']:
                logger.warning("S3 auto-sync errors: env=%s %s", env.name, result['errors'])
            else:
                logger.info("S3 auto-sync: env=%s created=%s", env.name, result['created'])
        except Exception:
            logger.exception("S3 auto-sync failed: env=%s", env.name)


# ---------------------------------------------------------------------------
# 起動 / 停止
# ---------------------------------------------------------------------------

def start():
    """
    BackgroundScheduler を起動する。
    apps.py の AppConfig.ready() から呼ぶ。
    """
    global _scheduler

    if _scheduler and _scheduler.running:
        return

    from django.db import connection
    if 'django_apscheduler_djangojob' not in connection.introspection.table_names():
        logger.warning("Scheduler skipped: django_apscheduler tables not found — run migrate first.")
        return

    _scheduler = BackgroundScheduler(timezone=str(timezone.get_current_timezone()))
    _scheduler.add_jobstore(DjangoJobStore(), 'default')
    _scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(minutes=1),
        id='boto3_scan_tick',
        name='Boto3 scheduled scan',
        jobstore='default',
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # 日次 EOL 取得（オプトイン: EOL_REFRESH_ENABLED=true のときだけ登録）
    if getattr(settings, 'EOL_REFRESH_ENABLED', False):
        _scheduler.add_job(
            _eol_refresh_job,
            trigger=CronTrigger(hour=3, minute=15),   # 毎日 03:15
            id='eol_refresh_daily',
            name='Daily EOL refresh (endoflife.date)',
            jobstore='default',
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        logger.info("EOL daily refresh job registered.")

    # Plugin-contributed jobs (discovered via the seam — core never imports the
    # plugin). A plugin whose feature is off contributes nothing.
    from .plugins import plugin_scheduled_jobs
    for spec in plugin_scheduled_jobs():
        try:
            _scheduler.add_job(
                spec['func'],
                trigger=spec['trigger'],
                id=spec['id'],
                name=spec.get('name', spec['id']),
                jobstore='default',
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            logger.info("Plugin job registered: %s", spec['id'])
        except Exception:  # noqa: BLE001 - a bad plugin job must not block startup
            logger.exception("Failed to register plugin job %s", spec.get('id'))

    _scheduler.start()
    logger.info("Scheduler started.")


def _eol_refresh_job():
    """日次 EOL 取得ジョブ（EOL_REFRESH_ENABLED=true のときだけ実通信する）。"""
    from .eol_refresh import refresh_eol
    try:
        refresh_eol()
    except Exception:
        logger.exception("EOL refresh job failed")


def stop():
    """テスト・シャットダウン用。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
