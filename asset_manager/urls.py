from django.urls import path
from .views import (
    dashboard_view,
    system_list_view,
    environment_list_view,
    asset_list_view,
    upload_tfstate_view,
    confirm_upload_tfstate_view,
    upload_form_view,
    sync_s3_state_view,
    # System CRUD
    create_system_form_view,
    create_system_view,
    edit_system_form_view,
    update_system_view,
    delete_system_view,
    # Environment CRUD
    create_environment_form_view,
    create_environment_view,
    edit_environment_form_view,
    update_environment_view,
    delete_environment_view,
    # Asset
    asset_detail_view,
    create_asset_form_view,
    create_asset_view,
    delete_asset_view,
    # Application
    application_list_view,
    all_applications_view,
    # Diagram / Drift
    diagram_view,
    drift_report_view,
    # Profile
    profile_view,
    profile_update_view,
    password_change_view,
    totp_setup_view,
    totp_confirm_view,
    totp_disable_view,
    account_delete_view,
    membership_update_view,
    # Boto3 Scan
    trigger_scan_view,
    # Sample library
    sample_list_view,
    sample_viewer_view,
    sample_download_view,
    import_sample_view,
    # Audit Log
    audit_log_view,
    # Auth
    login_view,
    logout_view,
    totp_verify_view,
)

urlpatterns = [
    path('', dashboard_view, name='dashboard'),

    # System
    path('systems/', system_list_view, name='system-list'),
    path('systems/create/', create_system_form_view, name='system-create-form'),
    path('systems/new/', create_system_view, name='system-create'),
    path('systems/<int:system_id>/environments/', environment_list_view, name='environment-list'),
    path('systems/<int:system_id>/edit/', edit_system_form_view, name='system-edit-form'),
    path('systems/<int:system_id>/update/', update_system_view, name='system-update'),
    path('systems/<int:system_id>/delete/', delete_system_view, name='system-delete'),
    path('systems/<int:system_id>/applications/', application_list_view, name='application-list'),
    path('applications/', all_applications_view, name='application-list-all'),

    # Environment
    path('systems/<int:system_id>/environments/create/', create_environment_form_view, name='environment-create-form'),
    path('systems/<int:system_id>/environments/new/', create_environment_view, name='environment-create'),
    path('environments/<int:environment_id>/edit/', edit_environment_form_view, name='environment-edit-form'),
    path('environments/<int:environment_id>/update/', update_environment_view, name='environment-update'),
    path('environments/<int:environment_id>/delete/', delete_environment_view, name='environment-delete'),

    # Asset
    path('assets/', asset_list_view, name='asset-list'),
    path('assets/create/', create_asset_form_view, name='asset-create-form'),
    path('assets/new/', create_asset_view, name='asset-create'),
    path('assets/<int:asset_id>/', asset_detail_view, name='asset-detail'),
    path('assets/<int:asset_id>/delete/', delete_asset_view, name='asset-delete'),

    # Diagram
    path('environments/<int:environment_id>/diagram/', diagram_view, name='env-diagram'),
    # Drift Report
    path('environments/<int:environment_id>/drift/', drift_report_view, name='env-drift'),
    # Boto3 Scan
    path('environments/<int:environment_id>/scan/', trigger_scan_view, name='env-scan'),
    # S3 Remote State sync
    path('environments/<int:environment_id>/sync-s3/', sync_s3_state_view, name='env-sync-s3'),
    # Sample library
    path('samples/',                                    sample_list_view,     name='sample-list'),
    path('samples/<str:filename>/view/',                sample_viewer_view,   name='sample-view'),
    path('samples/<str:filename>/download/',            sample_download_view, name='sample-download'),
    path('samples/<str:filename>/import/',              import_sample_view,   name='sample-import'),

    # tfstate import
    path('upload-tfstate/',         upload_tfstate_view,         name='upload-tfstate'),
    path('upload-tfstate/confirm/', confirm_upload_tfstate_view, name='upload-tfstate-confirm'),
    path('upload-tfstate-form/',    upload_form_view,            name='upload-tfstate-form'),

    # Profile
    path('profile/', profile_view, name='profile'),
    path('profile/update/', profile_update_view, name='profile-update'),
    path('profile/password/', password_change_view, name='profile-password'),
    path('profile/totp/setup/', totp_setup_view, name='profile-totp-setup'),
    path('profile/totp/confirm/', totp_confirm_view, name='profile-totp-confirm'),
    path('profile/totp/disable/', totp_disable_view, name='profile-totp-disable'),
    path('profile/delete/', account_delete_view, name='profile-delete'),
    path('memberships/<int:membership_id>/update/', membership_update_view, name='membership-update'),

    # Audit Log
    path('audit-log/', audit_log_view, name='audit-log'),

    # Auth
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('totp-verify/', totp_verify_view, name='totp-verify'),
]
