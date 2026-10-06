from django import template
from asset_manager.eol_data import asset_eol_info, get_eol_status
from asset_manager.resource_registry import PROVIDER_LABELS, get_icon

register = template.Library()


@register.filter
def eol_status(dep) -> str:
    """Return EOL status for an AppDependency: 'eol' | 'warning' | 'ok' | 'unknown'."""
    return get_eol_status(dep.name, dep.version)


@register.filter
def asset_eol(asset):
    """EOL of the middleware an Asset runs (RDS engine / Lambda runtime / EKS version).

    {'status', 'label', 'version', ...} or None when it cannot be judged.
    """
    return asset_eol_info(asset.asset_type, asset.raw_data)


@register.filter
def provider_label(code) -> str:
    """Provider code -> display name ('AWS'); an unknown code is shown as it is.

    Asset.provider has no `choices`, so Django generates no `get_provider_display`.
    """
    return PROVIDER_LABELS.get(code, code or '')


@register.filter
def dict_get(d, key):
    return d.get(key)


@register.simple_tag
def asset_icon_url(provider, asset_type):
    """
    provider と asset_type に対応する static 相対パスを返す。
    ICON_MAP に登録されていない組み合わせは空文字を返す。
    使用例:
        {% asset_icon_url asset.provider asset.asset_type as icon_path %}
        {% if icon_path %}<img src="{% static icon_path %}">{% endif %}
    """
    return get_icon(provider, asset_type) or ''
