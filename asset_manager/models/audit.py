from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = 'create', 'Create'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Delete'

    user        = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='audit_logs',
    )
    action      = models.CharField(max_length=10, choices=Action.choices)
    model_name  = models.CharField(max_length=100)
    object_id   = models.CharField(max_length=100)
    object_repr = models.CharField(max_length=255)
    diff        = models.JSONField(default=dict, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name        = _("Audit log")
        verbose_name_plural = _("Audit logs")

    def __str__(self):
        return f"[{self.action}] {self.model_name} #{self.object_id}"
