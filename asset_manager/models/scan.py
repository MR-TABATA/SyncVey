from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class ScanJob(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        RUNNING = 'running', _('Running')
        DONE    = 'done',    _('Done')
        FAILED  = 'failed',  _('Failed')

    system = models.ForeignKey(
        'asset_manager.System',
        on_delete=models.CASCADE,
        related_name='scan_jobs',
        verbose_name=_("System"),
    )
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name=_("Status"))
    regions       = models.JSONField(default=list, verbose_name=_("Scan Regions"))
    started_at    = models.DateTimeField(null=True, blank=True, verbose_name=_("Started At"))
    finished_at   = models.DateTimeField(null=True, blank=True, verbose_name=_("Finished At"))
    created_count = models.PositiveIntegerField(default=0, verbose_name=_("New Assets"))
    updated_count = models.PositiveIntegerField(default=0, verbose_name=_("Updated Assets"))
    error_message = models.TextField(blank=True, null=True, verbose_name=_("Error Message"))

    class Meta:
        verbose_name        = _("Scan Job")
        verbose_name_plural = _("Scan Jobs")
        ordering            = ['-created_at']

    def __str__(self):
        return f"ScanJob [{self.status}] {self.system.name}"
