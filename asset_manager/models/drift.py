from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class DriftSnapshot(BaseModel):
    """
    1回のスキャン／tfstate取込／S3同期で検出したドリフトの記録。
    時系列で積み上げることでドリフトの推移を追える。

    detail には描画に必要な差分の中身をそのまま保存する:
        {'changed': [{type, name, cloud_id, provider, changes:[...]}, ...],
         'added':   [{type, name, cloud_id, provider}, ...]}
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
        return self.changed_count + self.added_count

    @property
    def has_drift(self):
        return self.total_count > 0
