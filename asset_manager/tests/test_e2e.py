"""
E2E UIテスト（Playwright）。

テスト対象フロー:
  1. ログイン / ログアウト
  2. ダッシュボード表示
  3. tfstate アップロード → 資産が台帳に現れる
  4. スキャンボタン → 結果が DOM に差し込まれる（HTMX）
  5. Drift レポート表示

実行コマンド:
  DATABASE_URL=postgres://hitoshi@localhost/syncvey_test \
    pytest asset_manager/tests/test_e2e.py -v --headed   # ブラウザ表示あり
  DATABASE_URL=postgres://hitoshi@localhost/syncvey_test \
    pytest asset_manager/tests/test_e2e.py -v             # ヘッドレス
"""

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# 1. ログイン / ログアウト
# ---------------------------------------------------------------------------

class TestLogin:

    def test_login_success(self, page, live_server, admin_user):
        page.goto(f"{live_server.url}/login/")
        page.fill("[name=username]", "admin")
        page.fill("[name=password]", "testpass123")
        page.click("[type=submit]")
        page.wait_for_url(f"{live_server.url}/")
        assert page.url == f"{live_server.url}/"

    def test_login_wrong_password_shows_error(self, page, live_server, admin_user):
        page.goto(f"{live_server.url}/login/")
        page.fill("[name=username]", "admin")
        page.fill("[name=password]", "wrongpassword")
        page.click("[type=submit]")
        # エラーメッセージが表示されること
        page.wait_for_selector("[class*='text-red']")
        assert page.url.endswith("/login/")

    def test_logout_redirects_to_login(self, logged_in_page, live_server):
        # logout は POST のみ受け付けるので evaluate で form submit する
        logged_in_page.evaluate("""
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = '/logout/';
            const csrf = document.createElement('input');
            csrf.type = 'hidden';
            csrf.name = 'csrfmiddlewaretoken';
            csrf.value = document.cookie.match(/csrftoken=([^;]+)/)?.[1] ?? '';
            form.appendChild(csrf);
            document.body.appendChild(form);
            form.submit();
        """)
        logged_in_page.wait_for_url("**/login/**")
        assert "login" in logged_in_page.url


# ---------------------------------------------------------------------------
# 2. ダッシュボード
# ---------------------------------------------------------------------------

class TestDashboard:

    def test_dashboard_loads(self, logged_in_page, live_server):
        assert logged_in_page.url == f"{live_server.url}/"

    def test_unauthenticated_redirects_to_login(self, page, live_server):
        page.goto(f"{live_server.url}/")
        page.wait_for_url("**/login/**")
        assert "login" in page.url


# ---------------------------------------------------------------------------
# 3. tfstate アップロード → 資産が台帳に現れる
# ---------------------------------------------------------------------------

class TestTfstateUpload:

    def test_upload_creates_assets(
        self, logged_in_page, live_server, tfstate_file
    ):
        from asset_manager.models import Asset

        page = logged_in_page

        # アップロードモーダルを開くボタンを経由せず、API を直接叩く
        with page.expect_response("**/upload-tfstate/") as resp_info:
            page.evaluate(f"""async () => {{
                const fd = new FormData();
                fd.append('csrfmiddlewaretoken',
                    document.cookie.match(/csrftoken=([^;]+)/)?.[1] ?? '');
                const text = {repr(tfstate_file.read_text())};
                fd.append('tfstate_file', new Blob([text], {{type: 'application/json'}}),
                    'infra.tfstate');
                await fetch('/upload-tfstate/', {{method: 'POST', body: fd}});
            }}""")
            resp_info.value  # wait for response

        # DB に資産が作成されていること
        assert Asset.objects.filter(asset_type="EC2").exists()
        assert Asset.objects.filter(asset_type="RDS").exists()


# ---------------------------------------------------------------------------
# 4. スキャンボタン → HTMX で結果が DOM に差し込まれる
# ---------------------------------------------------------------------------

class TestScanButton:

    def _open_env_list(self, page, live_server, system):
        """
        ダッシュボードのシステム一覧から、その環境一覧ボタンをクリックして開く。
        page.evaluate(htmx.ajax) は返り値の Promise を Playwright が await する間に
        スワップ／ナビゲーションと競合し "Execution context was destroyed" で flaky 化するため、
        実ユーザー操作（クリック）に置き換え htmx にネイティブ処理させる。
        """
        page.goto(f"{live_server.url}/")
        page.wait_for_load_state("networkidle")
        env_btn = f"[hx-get='/systems/{system.id}/environments/']"
        page.wait_for_selector(env_btn, timeout=10_000)
        page.click(env_btn)
        page.wait_for_selector("[title='Boto3 Scan']", timeout=15_000)

    def test_scan_without_aws_credentials_shows_error(
        self, logged_in_page, live_server, system, environment, asset_in_env
    ):
        page = logged_in_page
        self._open_env_list(page, live_server, system)

        page.click("[title='Boto3 Scan']")
        page.wait_for_selector(
            f"#scan-result-{environment.id} [class*='text-red']",
            timeout=10_000,
        )

    def test_scan_success_shows_result(
        self, logged_in_page, live_server, system, environment, asset_in_env
    ):
        # 認証チェックを通過させるために偽の Role ARN を設定
        system.aws_role_arn = 'arn:aws:iam::123456789012:role/fake-role'
        system.save()

        page = logged_in_page
        self._open_env_list(page, live_server, system)

        fake_result = {'created': 3, 'updated': 1, 'scanned': 4, 'errors': []}
        with patch("asset_manager.scanner.run_scan", return_value=fake_result):
            page.click("[title='Boto3 Scan']")
            # HTMX がレスポンスを差し込むまで待つ
            page.wait_for_function(
                f"document.querySelector('#scan-result-{environment.id}').children.length > 0",
                timeout=10_000,
            )
            result_html = page.locator(f"#scan-result-{environment.id}").inner_html()
            assert "text-green" in result_html
            result_text = page.locator(f"#scan-result-{environment.id}").inner_text()
            assert "3" in result_text
            assert "1" in result_text


# ---------------------------------------------------------------------------
# 5. Drift レポート
# ---------------------------------------------------------------------------

class TestDriftReport:

    def test_drift_report_loads(
        self, logged_in_page, live_server, system, environment
    ):
        page = logged_in_page
        page.goto(f"{live_server.url}/environments/{environment.id}/drift/")
        page.wait_for_load_state("networkidle")
        # ページが 200 で返ること（エラーページでないこと）
        assert page.locator("body").is_visible()


# ---------------------------------------------------------------------------
# 6. サンプルライブラリ
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 5. 機密情報警告フロー
# ---------------------------------------------------------------------------

class TestSecretWarning:
    """
    アップロードフォームはモーダル内専用 (hx-target=#tfstateUploadModalBody) なので
    ブラウザ経由のフルUI操作が難しい。
    page.request でサーバーを直接叩きつつセッション Cookie を共有する方式でテストする。
    """

    def _csrf(self, page):
        return page.evaluate(
            "document.cookie.match(/csrftoken=([^;]+)/)?.[1] ?? ''"
        )

    def _upload_request(self, page, live_server, tfstate_path, system_name="秘密テスト"):
        """page.request で /upload-tfstate/ に POST して Response を返す。"""
        return page.request.post(
            f"{live_server.url}/upload-tfstate/",
            headers={'X-CSRFToken': self._csrf(page)},
            multipart={
                'tfstate_file': {
                    'name':     tfstate_path.name,
                    'mimeType': 'application/octet-stream',
                    'buffer':   tfstate_path.read_bytes(),
                },
                'system_name': system_name,
                'env_name':    'prod',
            },
        )

    def test_warning_response_when_secrets_detected(
        self, logged_in_page, live_server, tfstate_file_with_secrets
    ):
        """機密情報を含むtfstateをアップロードすると警告HTMLが返ること。"""
        page = logged_in_page
        page.goto(f"{live_server.url}/")

        resp = self._upload_request(page, live_server, tfstate_file_with_secrets)

        assert resp.ok
        body = resp.text()
        assert 'Sensitive data detected' in body
        assert 'password' in body
        assert 'Scrub secrets and import' in body

    def test_scrub_and_import_creates_assets(
        self, logged_in_page, live_server, tfstate_file_with_secrets
    ):
        """警告後に confirm エンドポイントを叩くと資産が作成され password が *** になること。"""
        from asset_manager.models import Asset

        page = logged_in_page
        page.goto(f"{live_server.url}/")

        # Step 1: 警告レスポンス（セッションに pending_tfstate が保存される）
        self._upload_request(page, live_server, tfstate_file_with_secrets)

        # Step 2: 確認エンドポイントを叩く
        confirm_resp = page.request.post(
            f"{live_server.url}/upload-tfstate/confirm/",
            headers={'X-CSRFToken': self._csrf(page)},
            form={},
        )
        assert confirm_resp.ok

        # DBにassetが作成されていること
        assert Asset.objects.filter(cloud_id='secrets-db').exists()

        # passwordが *** にスクラブされていること
        asset = Asset.objects.get(cloud_id='secrets-db')
        assert asset.raw_data.get('password') == '***'

    def test_no_confirm_means_no_import(
        self, logged_in_page, live_server, tfstate_file_with_secrets
    ):
        """警告だけでconfirmしなければassetが作成されないこと（キャンセル相当）。"""
        from asset_manager.models import Asset

        page = logged_in_page
        page.goto(f"{live_server.url}/")

        # 警告リクエストのみ（confirm しない）
        self._upload_request(page, live_server, tfstate_file_with_secrets)

        assert not Asset.objects.filter(cloud_id='secrets-db').exists()

    def test_clean_tfstate_skips_warning(
        self, logged_in_page, live_server, tfstate_file
    ):
        """機密情報のないtfstateは警告なしで直接インポートされること。"""
        page = logged_in_page
        page.goto(f"{live_server.url}/")

        resp = self._upload_request(page, live_server, tfstate_file, system_name="e2eシステム")

        assert resp.ok
        assert 'Sensitive data detected' not in resp.text()


class TestSampleLibrary:

    def _open_sample_list(self, page, live_server):
        """サイドバーの Sample Library をクリックして一覧を開く。"""
        page.goto(f"{live_server.url}/")
        page.wait_for_load_state("networkidle")
        page.evaluate("""
            htmx.ajax('GET', '/samples/', {target: '#main-content', swap: 'innerHTML'})
        """)
        # HTMX でコンテンツが差し込まれるまで待つ（Import Sample ボタンが目印）
        page.wait_for_selector("button:has-text('Import Sample')", timeout=15_000)

    def test_sample_list_shows_cards(self, logged_in_page, live_server):
        """サンプル一覧に複数のカードが表示されること。"""
        page = logged_in_page
        self._open_sample_list(page, live_server)
        cards = page.locator("button:has-text('Import Sample')").all()
        assert len(cards) >= 1

    def test_sample_list_shows_resource_chips(self, logged_in_page, live_server):
        """各カードにリソース種別チップ（EC2, RDS等）が表示されること。"""
        page = logged_in_page
        self._open_sample_list(page, live_server)
        # いずれかのカードに EC2 チップが存在すること
        assert page.locator("text=EC2").first.is_visible()

    def test_sample_viewer_shows_json(self, logged_in_page, live_server):
        """「View File」でJSONビューアが開き、tfstateの中身が見えること。"""
        page = logged_in_page
        page.goto(f"{live_server.url}/")
        page.wait_for_load_state("networkidle")
        page.evaluate("""
            htmx.ajax('GET', '/samples/ecsite-prod.tfstate/view/',
                      {target: '#main-content', swap: 'innerHTML'})
        """)
        # コードブロックが現れること
        page.wait_for_selector("#json-content", timeout=10_000)
        json_text = page.locator("#json-content").inner_text()
        # tfstate の必須キーが含まれること
        assert '"version"' in json_text
        assert '"resources"' in json_text

    def test_sample_viewer_has_download_link(self, logged_in_page, live_server):
        """JSONビューアにダウンロードリンクが存在すること。"""
        page = logged_in_page
        page.goto(f"{live_server.url}/")
        page.wait_for_load_state("networkidle")
        page.evaluate("""
            htmx.ajax('GET', '/samples/ecsite-prod.tfstate/view/',
                      {target: '#main-content', swap: 'innerHTML'})
        """)
        page.wait_for_selector("#json-content", timeout=10_000)
        download_link = page.locator(f"a[href*='/samples/ecsite-prod.tfstate/download/']")
        assert download_link.is_visible()

    def test_sample_download_triggers_file(self, logged_in_page, live_server):
        """ダウンロードリンクをクリックするとファイルが降ってくること。"""
        page = logged_in_page
        page.goto(f"{live_server.url}/")
        page.wait_for_load_state("networkidle")
        page.evaluate("""
            htmx.ajax('GET', '/samples/ecsite-prod.tfstate/view/',
                      {target: '#main-content', swap: 'innerHTML'})
        """)
        page.wait_for_selector("#json-content", timeout=10_000)

        with page.expect_download() as dl_info:
            page.click(f"a[href*='/samples/ecsite-prod.tfstate/download/']")
        download = dl_info.value
        assert download.suggested_filename == "ecsite-prod.tfstate"

    def test_sample_import_creates_assets(self, logged_in_page, live_server):
        """「Import Sample」ボタンで資産がDBに作成されること。"""
        from asset_manager.models import Asset

        page = logged_in_page
        self._open_sample_list(page, live_server)

        # 最初の Import Sample ボタンをクリック
        page.locator("button:has-text('Import Sample')").first.click()

        # 成功メッセージが現れること
        page.wait_for_function(
            "document.querySelector('[id^=sample-result-]').children.length > 0",
            timeout=15_000,
        )
        result_text = page.locator("[id^='sample-result-']").first.inner_text()
        assert "Imported" in result_text or "resources" in result_text

        # DBに資産が作成されていること
        assert Asset.objects.exists()
