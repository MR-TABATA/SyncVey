from django import template
from asset_manager.eol_data import get_eol_status
from asset_manager.resource_registry import get_icon

register = template.Library()


@register.filter
def eol_status(dep) -> str:
    """Return EOL status for an AppDependency: 'eol' | 'warning' | 'ok' | 'unknown'."""
    return get_eol_status(dep.name, dep.version)


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
