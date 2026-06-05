import threading

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
