import json

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy

from .models import (
    Organization, Membership, UserProfile,
    System, Environment,
    Asset,
    ScanJob,
    Application, AppEnvConfig, AppDependency,
    AuditLog,
    EolSnapshot,
)


# ---------------------------------------------------------------------------
# Inline helpers
# ---------------------------------------------------------------------------

class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    fields = ('user', 'role')
    autocomplete_fields = ('user',)


class EnvironmentInline(admin.TabularInline):
    model = Environment
    extra = 0
    fields = ('name', 'env_type', 'tfstate_filename')
    show_change_link = True


class AppEnvConfigInline(admin.TabularInline):
    model = AppEnvConfig
    extra = 0
    fields = ('environment', 'language_version', 'framework_version', 'deploy_method', 'runtime', 'runtime_version')
    show_change_link = True


class AppDependencyInline(admin.TabularInline):
    model = AppDependency
    extra = 0
    fields = ('name', 'version', 'dep_type')


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display  = ('name', 'slug', 'created_at')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')
    inlines = [MembershipInline]


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display  = ('user', 'organization', 'role', 'created_at')
    list_filter   = ('role', 'organization')
    search_fields = ('user__username', 'user__email', 'organization__name')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('user', 'organization')


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ('user', 'two_factor_enabled', 'created_at')
    list_filter   = ('two_factor_enabled',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at', 'totp_secret')


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@admin.register(System)
class SystemAdmin(admin.ModelAdmin):
    list_display  = ('name', 'code', 'organization', 'aws_configured_badge', 'created_at')
    list_filter   = ('organization',)
    search_fields = ('name', 'code', 'organization__name')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [EnvironmentInline]

    @admin.display(description='AWS', boolean=True)
    def aws_configured_badge(self, obj):
        return bool(obj.aws_role_arn)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

@admin.register(Environment)
class EnvironmentAdmin(admin.ModelAdmin):
    list_display  = ('__str__', 'system', 'env_type', 'tfstate_filename', 'created_at')
    list_filter   = ('env_type', 'system__organization')
    search_fields = ('name', 'system__name')
    readonly_fields = ('created_at', 'updated_at')


# ---------------------------------------------------------------------------
# Asset（中心モデル・JSONプロパティ表示）
# ---------------------------------------------------------------------------

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display         = ('name', 'asset_type', 'asset_category', 'provider', 'cloud_id', 'region', 'environment', 'created_at')
    list_filter          = ('asset_type', 'asset_category', 'provider', 'environment__env_type', 'environment__system__organization')
    search_fields        = ('name', 'cloud_id', 'region', 'environment__name', 'environment__system__name')
    readonly_fields      = ('created_at', 'updated_at', 'raw_data_pretty')
    list_select_related  = ('environment', 'environment__system')

    @admin.display(description=gettext_lazy('Raw data (JSON)'))
    def raw_data_pretty(self, obj):
        if not obj.raw_data:
            return '—'
        formatted = json.dumps(obj.raw_data, ensure_ascii=False, indent=2)
        return format_html(
            '<pre style="font-size:11px;max-height:300px;overflow:auto;'
            'background:#f8f9fa;padding:8px;border-radius:4px;white-space:pre-wrap;">'
            '{}</pre>',
            formatted,
        )


# ---------------------------------------------------------------------------
# ScanJob
# ---------------------------------------------------------------------------

@admin.register(ScanJob)
class ScanJobAdmin(admin.ModelAdmin):
    list_display    = ('system', 'status', 'created_count', 'updated_count', 'started_at', 'finished_at', 'created_at')
    list_filter     = ('status', 'system__organization')
    search_fields   = ('system__name', 'error_message')
    readonly_fields = ('created_at', 'updated_at', 'started_at', 'finished_at', 'created_count', 'updated_count', 'error_message')


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display  = ('name', 'system', 'language', 'framework', 'repository_url', 'created_at')
    list_filter   = ('language', 'framework', 'system__organization')
    search_fields = ('name', 'system__name', 'description', 'repository_url')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [AppEnvConfigInline]


# ---------------------------------------------------------------------------
# AppEnvConfig
# ---------------------------------------------------------------------------

@admin.register(AppEnvConfig)
class AppEnvConfigAdmin(admin.ModelAdmin):
    list_display  = ('application', 'environment', 'language_version', 'framework_version', 'deploy_method', 'runtime', 'runtime_version')
    list_filter   = ('deploy_method', 'runtime', 'environment__env_type')
    search_fields = ('application__name', 'environment__name', 'language_version', 'framework_version', 'branch')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [AppDependencyInline]


# ---------------------------------------------------------------------------
# AppDependency
# ---------------------------------------------------------------------------

@admin.register(AppDependency)
class AppDependencyAdmin(admin.ModelAdmin):
    list_display  = ('name', 'version', 'dep_type', 'app_env_config')
    list_filter   = ('dep_type',)
    search_fields = ('name', 'version', 'app_env_config__application__name')
    readonly_fields = ('created_at', 'updated_at')


# ---------------------------------------------------------------------------
# AuditLog (read-only)
# ---------------------------------------------------------------------------

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display  = ('created_at', 'user', 'action_badge', 'model_name', 'object_repr', 'diff_summary')
    list_filter   = ('action', 'model_name')
    search_fields = ('user__username', 'object_repr', 'model_name')
    readonly_fields = ('user', 'action', 'model_name', 'object_id', 'object_repr', 'diff', 'created_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description='Action')
    def action_badge(self, obj):
        colors = {'create': '#16a34a', 'update': '#2563eb', 'delete': '#dc2626'}
        color = colors.get(obj.action, '#64748b')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:700">{}</span>',
            color, obj.action.upper()
        )

    @admin.display(description='Changes')
    def diff_summary(self, obj):
        if not obj.diff:
            return '—'
        parts = [f'{k}: {v["old"]} → {v["new"]}' for k, v in list(obj.diff.items())[:3]]
        return ', '.join(parts)


# ---------------------------------------------------------------------------
# EOL Snapshot（読み取り専用・観測用）
# ---------------------------------------------------------------------------

@admin.register(EolSnapshot)
class EolSnapshotAdmin(admin.ModelAdmin):
    list_display    = ('fetched_at', 'source', 'product_count')
    readonly_fields = ('fetched_at', 'source', 'data')

    @admin.display(description=gettext_lazy('Products'))
    def product_count(self, obj):
        return len(obj.data or {})

    def has_add_permission(self, request):
        # スナップショットはジョブ/コマンドが作る。手動追加は不可。
        return False

    def has_change_permission(self, request, obj=None):
        return False
