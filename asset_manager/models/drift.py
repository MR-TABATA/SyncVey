from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class DriftSnapshot(BaseModel):
    """
    1回のスキャン／tfstate取込／S3同期で検出したドリフトの記録。
    時系列で積み上げることでドリフトの推移を追える。

    detail には描画に必要な差分の中身をそのまま保存する:
        {'changed': [{type, name, cloud_id, provider, changes:[...]}, ...],
         'added':   [{type, name, cloud_id, provider}, ...],
         'removed': [{type, name, cloud_id, provider}, ...]}
    """

    class Source(models.TextChoices):
        SCAN    = 'scan',    _('AWS Scan')
        TFSTATE = 'tfstate', _('tfstate Import')
        S3SYNC  = 's3-sync', _('S3 Sync')

    environment = models.ForeignKey(
        'asset_manager.Environment',
        on_delete=models.CASCADE,
        related_name='drift_snapshots',
        verbose_name=_("Environment"),
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.SCAN,
        verbose_name=_("Source"),
    )
    changed_count   = models.PositiveIntegerField(default=0, verbose_name=_("Changed"))
    added_count     = models.PositiveIntegerField(default=0, verbose_name=_("Added"))
    # AWS 側から消えたリソース。added の裏返しで、これが無いと台帳は
    # 幽霊を溜め続ける（消滅は差分ゼロ＝unchanged として黙殺されていた）。
    removed_count   = models.PositiveIntegerField(default=0, verbose_name=_("Removed"))
    unchanged_count = models.PositiveIntegerField(default=0, verbose_name=_("Unchanged"))
    detail          = models.JSONField(default=dict, blank=True, verbose_name=_("Detail"))
    detected_at     = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Detected At"))

    class Meta:
        verbose_name        = _("Drift Snapshot")
        verbose_name_plural = _("Drift Snapshots")
        ordering            = ['-detected_at']

    def __str__(self):
        return f"Drift {self.environment_id} @ {self.detected_at:%Y-%m-%d %H:%M}"

    @property
    def total_count(self):
        return self.changed_count + self.added_count + self.removed_count

    @property
    def has_drift(self):
        return self.total_count > 0

    @classmethod
    def prune(cls, environment, keep=None):
        """
        environment ごとに最新 keep 件だけ残し、古いスナップショットを削除する。

        keep が None のときは settings.DRIFT_SNAPSHOT_RETENTION を使う。
        keep が 0 以下（無制限）のときは何もしない。
        戻り値: 削除した件数。
        """
        if keep is None:
            from django.conf import settings
            keep = getattr(settings, 'DRIFT_SNAPSHOT_RETENTION', 0)
        if not keep or keep <= 0:
            return 0

        keep_ids = list(
            cls.objects.filter(environment=environment)
            .order_by('-detected_at')
            .values_list('pk', flat=True)[:keep]
        )
        deleted, _ = (
            cls.objects.filter(environment=environment)
            .exclude(pk__in=keep_ids)
            .delete()
        )
        return deleted
