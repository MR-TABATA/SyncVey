from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class Asset(BaseModel):
    """
    クラウド資産の中心モデル。
    asset_type / asset_category / provider はすべて自由文字列。
    TextChoices を廃止したため、models/*.py を変更せずに
    新サービス・新プロバイダーを resource_registry.py だけで追加できる。
    """

    environment = models.ForeignKey(
        'asset_manager.Environment',
        on_delete=models.CASCADE,
        related_name='assets',
        null=True, blank=True,
        verbose_name=_("Environment"),
    )
    name           = models.CharField(max_length=255, verbose_name=_("Asset Name"))
    provider       = models.CharField(
        max_length=20,
        default='AWS',
        verbose_name=_("Provider"),
    )
    asset_type     = models.CharField(
        max_length=50,
        verbose_name=_("Asset Type"),
    )
    asset_category = models.CharField(
        max_length=20,
        default='OTHER',
        db_index=True,
        verbose_name=_("Asset Category"),
    )
    cloud_id         = models.CharField(max_length=255, unique=True, verbose_name=_("Cloud ID"), default='-')
    region           = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("Region"))
    endpoint         = models.TextField(blank=True, null=True, verbose_name=_("Endpoint"))
    raw_data         = models.JSONField(default=dict, blank=True, verbose_name=_("Raw Data"))
    raw_data_prev    = models.JSONField(default=dict, blank=True, verbose_name=_("Previous Raw Data"))
    last_imported_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Last Imported At"))
    memo             = models.TextField(blank=True, null=True, verbose_name=_("Memo"))

    class Meta:
        verbose_name        = _("Asset")
        verbose_name_plural = _("Assets")
        ordering            = ['-created_at']

    def __str__(self):
        return f"[{self.asset_type}] {self.name}"
