from django.db import models


class BaseModel(models.Model):
    note       = models.TextField(blank=True, null=True, help_text="管理用備考")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="作成日時")
    updated_at = models.DateTimeField(auto_now=True,     verbose_name="更新日時")

    class Meta:
        abstract = True
