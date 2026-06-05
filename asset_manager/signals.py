TRACKED_FIELDS = {
    'Asset':        ['name', 'asset_type', 'cloud_id', 'region', 'status'],
    'System':       ['name', 'code', 'description'],
    'Environment':  ['name', 'env_type'],
    'Application':  ['name', 'language', 'framework', 'description'],
    'AppEnvConfig': ['language_version', 'framework_version', 'deploy_method', 'runtime', 'runtime_version', 'branch'],
    'AppDependency':['name', 'version', 'dep_type'],
}


def _diff(old, new, fields):
    result = {}
    for f in fields:
        ov = str(getattr(old, f, '') or '')
        nv = str(getattr(new, f, '') or '')
        if ov != nv:
            result[f] = {'old': ov, 'new': nv}
    return result


def _write_log(action, instance, diff=None):
    from .models import AuditLog
    from .middleware import get_current_user
    try:
        AuditLog.objects.create(
            user=get_current_user(),
            action=action,
            model_name=instance.__class__.__name__,
            object_id=str(instance.pk),
            object_repr=str(instance)[:255],
            diff=diff or {},
        )
    except Exception:  # nosec B110 - 監査ログは best-effort（マイグレーション時のテーブル未作成等でも本処理を止めない）
        pass  # DB not ready (e.g. during migrations)


def handle_pre_save(sender, instance, **kwargs):
    if not instance.pk:
        return
    if sender.__name__ not in TRACKED_FIELDS:
        return
    try:
        instance._audit_old = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        instance._audit_old = None


def handle_post_save(sender, instance, created, **kwargs):
    if sender.__name__ not in TRACKED_FIELDS:
        return
    fields = TRACKED_FIELDS[sender.__name__]
    if created:
        _write_log('create', instance, {})
    else:
        old = getattr(instance, '_audit_old', None)
        if old:
            diff = _diff(old, instance, fields)
            if diff:
                _write_log('update', instance, diff)


def handle_post_delete(sender, instance, **kwargs):
    if sender.__name__ not in TRACKED_FIELDS:
        return
    _write_log('delete', instance, {})
