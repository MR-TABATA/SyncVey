"""ロックファイルの内容を、環境（AppEnvConfig）のライブラリとして台帳へ入れる。元のファイルは保存しない。"""
from django.db import transaction

from .lockfiles import parse


def import_lockfile(cfg, filename: str, raw: bytes) -> dict:
    """解析して、同じエコシステムのライブラリを置き換える。

    ロックファイルは「いま入っているライブラリの全部」なので、同じエコシステムの行は丸ごと入れ替える
    （消えたライブラリが台帳に残らない）。手で登録した行（エコシステム空）は触らない。
    Returns: {'ecosystem', 'total', 'added', 'removed', 'skipped'}
    """
    from .models import AppDependency
    eco, found, skipped = parse(filename, raw)
    wanted = set(found)
    with transaction.atomic():
        existing = {(d.name, d.version): d for d in cfg.dependencies.filter(ecosystem=eco)}
        stale = [d.pk for key, d in existing.items() if key not in wanted]
        AppDependency.objects.filter(pk__in=stale).delete()
        new = [AppDependency(app_env_config=cfg, name=n, version=v, ecosystem=eco,
                             dep_type=AppDependency.DepType.LIBRARY)
               for n, v in found if (n, v) not in existing]
        AppDependency.objects.bulk_create(new)
    return {'ecosystem': eco, 'total': len(wanted), 'added': len(new), 'removed': len(stale), 'skipped': skipped}
