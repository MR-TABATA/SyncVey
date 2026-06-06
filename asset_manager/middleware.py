import threading

from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect

_thread_local = threading.local()


def get_current_user():
    return getattr(_thread_local, 'user', None)


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_local.user = request.user if request.user.is_authenticated else None
        try:
            return self.get_response(request)
        finally:
            # リクエスト後はスレッドローカルを必ずクリア（スレッド再利用時のユーザー混線防止）
            _thread_local.user = None


# Content-Security-Policy。
# 本アプリは Tailwind(Play CDN) / htmx / lucide を CDN から読み込み、
# テンプレートに多数のインライン <script> / style 属性を持つため 'unsafe-inline' / 'unsafe-eval' を許容する。
# （本番強化案: アセットを self-host/プリコンパイルして CDN と unsafe-* を外す）
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=(), payment=(), usb=()"


class SecurityHeadersMiddleware:
    """CSP / Permissions-Policy を全レスポンスに付与（Django 標準で未対応のヘッダ）。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Content-Security-Policy', _CSP)
        response.setdefault('Permissions-Policy', _PERMISSIONS_POLICY)
        return response


class OrgRequiredMiddleware:
    """組織未所属の認証済みユーザーを org スコープのアプリから締め出す。

    本アプリのデータは全て組織スコープ。Membership を持たないユーザー
    （例: 組織未割当の superuser=root）はアプリ側で意味のある操作ができず、
    書き込むと organization=None の孤児データを生むだけなので入口で弾く。
    superuser/staff は Django admin へ、それ以外はログアウトさせる。
    """

    EXEMPT_PREFIXES = ('/admin', '/login', '/logout', '/totp-verify', '/static', '/__debug__')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        path = request.path
        if (user is not None and user.is_authenticated
                and not any(path.startswith(p) for p in self.EXEMPT_PREFIXES)):
            from .models import Membership
            if not Membership.objects.filter(user=user).exists():
                if user.is_staff or user.is_superuser:
                    return redirect('/admin/')
                auth_logout(request)
                return redirect('/login/?no_org=1')
        return self.get_response(request)
