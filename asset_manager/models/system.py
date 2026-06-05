from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import BaseModel


class System(BaseModel):
    class SlackLang(models.TextChoices):
        EN = 'en', 'English'
        JA = 'ja', '日本語'

    name = models.CharField(max_length=255, unique=True, verbose_name=_("System Name"))
    code = models.SlugField(max_length=50,  unique=True, verbose_name=_("System Code"))

    aws_role_arn          = models.CharField(max_length=255, blank=True, null=True, verbose_name=_("AWS Role ARN"))
    aws_scan_regions      = models.JSONField(default=list,   blank=True,            verbose_name=_("Scan Regions"))
    scan_enabled          = models.BooleanField(default=False, verbose_name=_("Auto Scan"))
    scan_interval_minutes = models.PositiveIntegerField(default=60, verbose_name=_("Scan Interval (min)"))
    slack_webhook_url     = models.URLField(blank=True, null=True, verbose_name=_("Slack Webhook URL"))
    slack_language        = models.CharField(
        max_length=5, choices=SlackLang.choices, default=SlackLang.EN,
        verbose_name=_("Slack Notification Language"),
        help_text=_("Language used for Slack drift notifications."),
    )
    organization     = models.ForeignKey(
        'asset_manager.Organization',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='systems',
        verbose_name=_("Organization"),
    )

    class Meta:
        verbose_name        = _("System")
        verbose_name_plural = _("Systems")
        ordering            = ['name']

    def __str__(self):
        return self.name

    @property
    def aws_configured(self):
        return bool(self.aws_role_arn)


class Environment(BaseModel):
    class EnvType(models.TextChoices):
        PROD = 'PROD', _('Production')
        STG  = 'STG',  _('Staging')
        DEV  = 'DEV',  _('Development')
        QA   = 'QA',   _('QA')

    class BackendType(models.TextChoices):
        MANUAL = 'manual', _('Manual Upload')
        S3     = 's3',     _('S3 Remote State')

    system = models.ForeignKey(
        System,
        on_delete=models.CASCADE,
        related_name='environments',
        verbose_name=_("System"),
    )
    name             = models.CharField(max_length=50, verbose_name=_("Environment Name"))
    tfstate_filename = models.CharField(
        max_length=255, blank=True, null=True,
        verbose_name=_("tfstate Filename"),
        help_text=_("Corresponding tfstate filename (e.g. ecsite-prod.tfstate)"),
    )
    env_type = models.CharField(
        max_length=20,
        choices=EnvType.choices,
        default=EnvType.DEV,
        verbose_name=_("Environment Type"),
    )
    backend_type = models.CharField(
        max_length=20,
        choices=BackendType.choices,
        default=BackendType.MANUAL,
        verbose_name=_("State Backend"),
    )
    s3_bucket    = models.CharField(max_length=255, blank=True, null=True, verbose_name=_("S3 Bucket"))
    s3_key       = models.CharField(max_length=1024, blank=True, null=True, verbose_name=_("S3 Key"), help_text=_("e.g. envs/prod/terraform.tfstate"))
    s3_region    = models.CharField(max_length=50, blank=True, null=True, verbose_name=_("S3 Region"), help_text=_("e.g. ap-northeast-1"))
    s3_auto_sync = models.BooleanField(default=False, verbose_name=_("Auto Sync on Schedule"))

    class Meta:
        verbose_name        = _("Environment")
        verbose_name_plural = _("Environments")
        unique_together     = ('system', 'name')
        ordering            = ['system', 'env_type']

    def __str__(self):
        return f"{self.system.name} - {self.name}"
