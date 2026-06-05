from django.db import models
from django.utils.translation import gettext_lazy as _


class EolSnapshot(models.Model):
    """
    endoflife.date から取得した EOL データのスナップショット。

    data の形: {product: {cycle: "YYYY-MM-DD" | None}}
      - "YYYY-MM-DD": その cycle の EOL 日付
      - None:        サポート中（EOL なし）
    判定（eol_data.get_eol_status）は最新スナップショットを参照し、
    無ければハードコード辞書 _EOL にフォールバックする。
    """
    data       = models.JSONField(default=dict, verbose_name=_("EOL Data"))
    fetched_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Fetched At"))
    source     = models.CharField(max_length=255, default="endoflife.date", verbose_name=_("Source"))

    class Meta:
        ordering            = ['-fetched_at']
        verbose_name        = _("EOL Snapshot")
        verbose_name_plural = _("EOL Snapshots")

    def __str__(self):
        return f"EOL snapshot @ {self.fetched_at:%Y-%m-%d %H:%M} ({len(self.data)} products)"
