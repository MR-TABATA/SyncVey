from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings

from .base import BaseModel


class Organization(BaseModel):
    name = models.CharField(max_length=255, unique=True, verbose_name=_("Organization Name"))
    slug = models.SlugField(max_length=50, unique=True, verbose_name=_("Slug"))

    class Meta:
        verbose_name        = _("Organization")
        verbose_name_plural = _("Organizations")
        ordering            = ['name']

    def __str__(self):
        return self.name


class Membership(BaseModel):
    class Role(models.TextChoices):
        OWNER       = 'OWNER',       _('Owner')
        INFRA_ADMIN = 'INFRA_ADMIN', _('Infra Admin')
        APP_ADMIN   = 'APP_ADMIN',   _('App Admin')
        VIEWER      = 'VIEWER',      _('Viewer')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name=_("User"),
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name=_("Organization"),
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
        verbose_name=_("Role"),
    )

    class Meta:
        verbose_name        = _("Membership")
        verbose_name_plural = _("Memberships")
        unique_together     = ('user', 'organization')

    def __str__(self):
        return f"{self.user.username} @ {self.organization.name} ({self.role})"


class UserProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name=_("User"),
    )
    two_factor_enabled = models.BooleanField(default=False, verbose_name=_("Two-Factor Auth"))
    totp_secret        = models.CharField(max_length=64, blank=True, verbose_name=_("TOTP Secret"))

    class Meta:
        verbose_name        = _("User Profile")
        verbose_name_plural = _("User Profiles")

    def __str__(self):
        return f"Profile: {self.user.username}"
