#!/usr/bin/env python3
"""Generate the development-history page (`docs/history.{ja,en}.html`).

This used to read merged pull requests (`gh pr list`). That silently drops
any commit that lands by a direct push to `main` — and that is exactly what
happened here: three commits in a row (one of them the v0.6.0 cut) never got
an entry, no matter how many times the rebuild workflow ran, because none of
them went through a PR. Reading from `git log` instead means every commit
gets a place on the page regardless of how it landed. cli2ui's history page
(github.com/MR-TABATA/cli2ui) was converted the same way first; this follows
the same shape.

Facts come from git at build time:

    * commit list, dates, sha       -> `git log`
    * lines added/removed           -> `git log --shortstat`
    * version boundaries            -> `git tag` (peeled to the commit each
                                        tag actually points at)

The bilingual text is the one thing a machine cannot derive: a commit
message here is written in whichever language was natural at the time, never
both. COMMITS below pairs every commit's sha with a ja/en translation written
by hand. A commit that lands without an entry here is not an error — it is
shown using its own raw subject line on both pages until someone adds a
translated pair.

The workflow's own "docs: rebuild the development-history page" commits are
excluded outright (EXCLUDE_GREP below) — they are not work, and including
them would mean every rebuild adds an entry for itself.

    python3 scripts/build_history.py

`.github/workflows/history.yml` re-runs this on every push to main and
commits the result if it changed.
"""

from __future__ import annotations

import datetime
import html
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO = 'MR-TABATA/SyncVey'

PR_SUFFIX_RE = re.compile(r'\s*\(#(\d+)\)\s*$')
EXCLUDE_GREP = '^docs: rebuild the development-history page'


# ---------------------------------------------------------------------------
# Editorial layer — the one thing a machine cannot derive: a translation of
# each commit's subject into the language it was not written in. (sha, ja, en)
# A PR reference embedded in the raw subject (" (#N)") is stripped before
# translation and re-attached as a separate link at build time, so it is not
# duplicated here.
# ---------------------------------------------------------------------------

COMMITS: list[tuple[str, str, str]] = [
    ('6a81d23', 'SyncVey — 自己ホスト型の AWS 資産台帳、Terraform ドリフト検知付き', 'SyncVey — self-hosted AWS asset ledger with Terraform drift detection'),
    ('a6ac99e', 'fix: 組織スコープの認可を強化', 'fix: organization-scope authorization hardening'),
    ('ce2a434', 'docs: ドキュメントを英語主＋日本語（.ja.md）従に統一し、AWS 手順のリンク切れを修正', 'docs: standardize docs as English-primary + Japanese (.ja.md) secondary, fix broken AWS setup links'),
    ('0a93b43', 'ux: チップ溢れ修正・環境カードのアクションをケバブ集約・サイドバーをモバイル対応', 'ux: fix chip overflow, collapse environment-card actions into a kebab menu, make the sidebar mobile-friendly'),
    ('8bfcd54', 'chore(seed): デモデータを完全英語化し、superuser=管理専用モデルを維持', 'chore(seed): fully English demo data, keeping superuser as the admin-only model'),
    ('db76750', 'feat(lp): ブランド準拠の 404 ページを追加（言語自動判定・橋メタファー）', 'feat(lp): add an on-brand 404 page (auto language detection, a bridge metaphor)'),
    ('1fa4be3', 'improve(audit-log): レスポンシブなカードレイアウト＋変更列を分かりやすく', 'improve(audit-log): responsive card layout + clearer change column'),
    ('7b79bde', 'ux(assets): リソース一覧をテーブル→カードのレスポンシブ切り替えに', 'ux(assets): responsive table→card split for resource list'),
    ('416e36e', 'fix(lp): ダウンロードページの導入手順を修正', 'fix(lp): repair install steps on the download page'),
    ('bedc512', 'fix(docker): クローン直後でもどこでも動く自己完結構成に', 'fix(docker): make a clean clone run anywhere, self-contained'),
    ('3d0becf', 'docs(lp): ダウンロードページにデモ用ログイン情報を表示', 'docs(lp): show the demo login on the download page'),
    ('cfea9f8', 'chore(lp): サイトを syncvey.com のカスタムドメインへ向ける', 'chore(lp): point the site at the syncvey.com custom domain'),
    ('a229d2f', 'fix(drift): 共通キーのみを比較し、誤検知を無くす', 'fix(drift): compare only shared keys to kill false positives'),
    ('be751f9', 'feat: ライブスキャン対象の拡張 + ドリフト履歴・推移', 'feat: expand live-scan coverage + drift history and trend'),
    ('806dfa3', 'docs: README/LP にスキャン対象拡大とドリフト履歴を反映', 'docs: reflect scan-coverage expansion + drift history in README/LP'),
    ('046b026', 'feat(core): 任意機能のフィーチャーフラグ＋プラグイン発見の継ぎ目', 'feat(core): optional-feature flags + plugin discovery seam'),
    ('97b5ae3', 'feat(drift): 環境ごとに DriftSnapshot 履歴の保持件数を制限', 'feat(drift): cap DriftSnapshot history per environment'),
    ('e25a7d5', 'fix(i18n): ドリフト履歴文字列の fuzzy な日本語訳を修正', 'fix(i18n): correct fuzzy ja translations for drift-history strings'),
    ('51d6139', 'test(drift): ドリフト履歴ビューの描画をテストし、EFS アイコンの欠落も解消', 'test(drift): cover drift-history view render + close EFS icon gap'),
    ('d013047', 'docs(lp): ランディングページを実際のスキャン範囲に合わせ、EOL/2FA も明記', 'docs(lp): align landing page with actual scan coverage + surface EOL/2FA'),
    ('93cb166', 'feat(dashboard): ヒーロー行を追加 — ドリフト推移・EOL・鮮度', 'feat(dashboard): hero-signal row — drift trend, EOL, freshness'),
    ('788e737', 'feat(drift-risk): セキュリティリスクでの選別＋ CloudTrail による変更者特定プラグイン', 'feat(drift-risk): security-risk triage + CloudTrail attribution plugin'),
    ('66b4291', 'docs: drift-risk 機能を README とランディングページに記載', 'docs: document drift-risk feature in README + landing page'),
    ('ea9f77c', 'feat(drift-digest): 週次ドリフト・ブリーフィング＋プラグイン用の定期ジョブの継ぎ目', 'feat(drift-digest): weekly drift briefing + plugin scheduled-job seam'),
    ('7656b24', 'docs: 週次ドリフト・ブリーフィングを README とランディングページに記載', 'docs: document the weekly drift briefing in README + landing page'),
    ('bb5905d', 'feat(survey): ダウンロード時アンケートで流入経路と目的を取得', 'feat(survey): capture attribution + intent on the download survey'),
    ('749e24b', 'feat(drift-risk): されるべきなのにされていない Secrets Manager のローテーションを検出', "feat(drift-risk): flag Secrets Manager rotation that should have happened but didn't"),
    ('f08dbb2', 'feat(cli): 着脱可能な `syncvey` コマンドラインプラグインを追加', 'feat(cli): add a detachable `syncvey` command-line plugin'),
    ('56ba789', 'feat(drift): Auto Scaling の増減をオオカミ少年扱いしないように', "feat(drift): don't cry wolf on Auto Scaling churn"),
    ('e829634', 'chore: 非公開ロードマップ用に /.private/ を gitignore', 'chore: gitignore /.private/ for the non-public roadmap'),
    ('8026520', 'chore: 設定駆動のドキュメント整合性チェッカーを追加', 'chore: add config-driven doc-consistency checker'),
    ('a2a2fe6', 'chore(ci): テストスイートを GitHub Actions で実行', 'chore(ci): run the test suite on GitHub Actions'),
    ('dd597e5', 'feat(scan): AWS から消えたリソースを検出', 'feat(scan): detect resources that disappeared from AWS'),
    ('36f3fdb', 'docs: v0.1.0 に先立ち SECURITY.md と CHANGELOG.md を追加', 'docs: add SECURITY.md and CHANGELOG.md ahead of v0.1.0'),
    ('9316d02', 'ci(i18n): 未レビューの翻訳でビルドを失敗させる', 'ci(i18n): fail the build on unreviewed translations'),
    ('6edea30', 'docs(i18n): 抽出されていなかった 50 文字列を翻訳し、ローテーションドリフトを記載', 'docs(i18n): translate the 50 never-extracted strings + document rotation drift'),
    ('4f153e8', 'feat(blast-radius): 参照グラフとドリフトの波及（ステップ 1〜3）', 'feat(blast-radius): reference graph + drift propagation (steps 1–3)'),
    ('83c689b', 'chore(ci): i18n チェックを CI に統合し、v0.1.0 以降の変更を記録', 'chore(ci): fold the i18n check into CI and log v0.1.0+ changes'),
    ('32a0f2f', 'fix(drift): 削除されたリソースをドリフト総数に数える', 'fix(drift): count removed resources in the drift totals'),
    ('5a5174f', 'i18n(ja): 影響波及範囲・Auto Scaling・Missing Since の文字列を翻訳', 'i18n(ja): translate blast-radius, Auto Scaling and missing-since strings'),
    ('d4cb488', 'ci(i18n): 一度も抽出されていない文字列でビルドを失敗させる', 'ci(i18n): fail the build on strings that were never extracted'),
    ('cde3045', 'docs: v0.2.0 の変更を CHANGELOG に記録', 'docs: log v0.2.0 in the changelog'),
    ('b1f9374', 'docs: 生成される開発の記録ページを追加', 'docs: add a generated development-history page'),
    ('421bceb', 'ci: main への push で開発の記録ページを自動的に再生成', 'ci: rebuild the development-history page automatically on main'),
    ('57ea059', 'fix(ci): 記録ページの再生成時にフル履歴を checkout するように', 'fix(ci): check out full history when rebuilding the history page'),
    ('ce55d90', 'feat(demo): LocalStack オンランプ — AWS アカウント無しで試せる（CLI の削除ドリフトの修正も併せて）', 'feat(demo): LocalStack on-ramp — try it without an AWS account (+ fix removed drift in the CLI)'),
    ('bfaff43', 'refactor(drift): 「ドリフトとは何か」の判定を 1 か所に集約', 'refactor(drift): decide what drift is in one place'),
    ('0690290', 'chore(metrics): イメージを Docker Hub に公開し、期限切れになるトラフィック数を保持', 'chore(metrics): publish the image to Docker Hub and keep the traffic numbers that expire'),
    ('4302f0e', 'docs: 0.3.0 をカット', 'docs: cut 0.3.0'),
    ('c275698', 'feat(dist): ビルドせず公開済みイメージから起動するように', 'feat(dist): start from the published image instead of building'),
    ('7b24b7a', 'docs: 開発の記録ページへの入口を足し、最初の 2 週間の空白を説明する', "docs: add an entry point to the development-history page, and explain the first two weeks' gap"),
    ('2b58c8f', '0.4.0: 起動が pull になったことを記録する', '0.4.0: record that startup now pulls instead of building'),
    ('b00782d', '導入の手前からアンケートを外す', 'Remove the survey from just before onboarding'),
    ('3aa6b50', '時刻を自分のタイムゾーンで、どこのものか分かる形で出す', "Show timestamps in your own timezone, labelled so it's clear whose clock they're on"),
    ('a2debb3', '0.5.0: 版上げと CHANGELOG', '0.5.0: version bump and CHANGELOG'),
    ('342a23f', 'CLAUDE.md を git 管理から外す', 'Stop tracking CLAUDE.md in git'),
    ('1bdc2e3', '配布ポリシーに EBS/EFS/SNS/SQS の読み取り権限を足す', 'Add read access for EBS/EFS/SNS/SQS to the distribution policy'),
    ('79e5fb8', 'ダッシュボードのヒーロー 3 枚を同じ骨組みに揃え、htmx 断片の直接アクセスを包む', "Align the dashboard's three hero cards on the same skeleton, and guard direct access to htmx fragments"),
    ('45cc9e0', 'v0.6.0 の CHANGELOG を確定し、README/LP のスクリーンショットを撮り直す', 'Finalize the v0.6.0 CHANGELOG, and re-take the README/LP screenshots'),
]

# Trailing-bucket title for commits on main that have not been tagged yet.
UNRELEASED_TITLE = ('未リリース', 'Unreleased')


# ---------------------------------------------------------------------------
# Measured layer
# ---------------------------------------------------------------------------

def _git(args: list[str]) -> str:
    return subprocess.run(['git', *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()


def _tag_commits() -> list[tuple[str, str]]:
    """[(tag, short_sha), ...] in version order, peeled to the commit each
    annotated tag actually points at (not the tag object's own sha)."""
    tags = _git(['tag', '--sort=v:refname']).splitlines()
    out = []
    for t in tags:
        if not t.startswith('v'):
            continue
        sha = _git(['rev-parse', '--short', f'{t}^{{commit}}'])
        out.append((t, sha))
    return out


def collect():
    if _git(['rev-parse', '--is-shallow-repository']) == 'true':
        raise RuntimeError(
            'the repository is a shallow clone, so commit history is not '
            'available and the statistics would be wrong.\n'
            '  In CI, check out with `fetch-depth: 0`. Locally, run '
            '`git fetch --unshallow`.'
        )

    log = _git(['log', '--reverse', '--format=%h|%H|%ad|%s', '--date=short',
                '--invert-grep', f'--grep={EXCLUDE_GREP}'])
    stats = _git(['log', '--reverse', '--format=@@%h', '--shortstat',
                  '--invert-grep', f'--grep={EXCLUDE_GREP}'])

    added: dict[str, int] = {}
    removed: dict[str, int] = {}
    cur = None
    for line in stats.splitlines():
        if line.startswith('@@'):
            cur = line[2:]
        elif line.strip() and cur is not None:
            m_ins = re.search(r'(\d+) insertion', line)
            m_del = re.search(r'(\d+) deletion', line)
            added[cur] = int(m_ins.group(1)) if m_ins else 0
            removed[cur] = int(m_del.group(1)) if m_del else 0

    translations = {sha: (ja, en) for sha, ja, en in COMMITS}

    commits = []
    for line in log.splitlines():
        short, full, date_s, subject = line.split('|', 3)
        m = PR_SUFFIX_RE.search(subject)
        pr = int(m.group(1)) if m else None
        clean_subject = PR_SUFFIX_RE.sub('', subject)
        ja, en = translations.get(short, (clean_subject, clean_subject))
        commits.append({
            'sha': short,
            'date': datetime.date.fromisoformat(date_s),
            'ja': ja,
            'en': en,
            'pr': pr,
            'added': added.get(short, 0),
            'removed': removed.get(short, 0),
        })

    unknown = set(translations) - {c['sha'] for c in commits}
    if unknown:
        print(f'warning: translations exist for commits not in history: {sorted(unknown)}',
              file=sys.stderr)
    missing = [c['sha'] for c in commits if c['sha'] not in translations]
    if missing:
        print(f'note: {len(missing)} commit(s) shown with their raw subject on both '
              f'pages — add a translated pair to COMMITS in {Path(__file__).name}: '
              f'{missing}', file=sys.stderr)

    tags = _tag_commits()
    first = commits[0]['date']
    last = commits[-1]['date']

    return {'commits': commits, 'tags': tags, 'first': first, 'last': last}


def group_by_version(data):
    """[(ja_title, en_title, tag_or_none, [commit, ...]), ...] in chronological
    order. A bucket runs up to and including the commit a tag points at, and
    is titled after that tag — so "v0.1.0" is everything that had landed by
    the time v0.1.0 shipped, first commit included. Whatever is left after
    the last tag (commits not yet released) becomes its own trailing bucket."""
    commits = data['commits']
    tag_shas = {sha: tag for tag, sha in data['tags']}
    groups = []
    bucket = []
    for c in commits:
        bucket.append(c)
        if c['sha'] in tag_shas:
            tag = tag_shas[c['sha']]
            groups.append((tag, tag, tag, bucket))
            bucket = []
    if bucket:
        ja_t, en_t = UNRELEASED_TITLE
        groups.append((ja_t, en_t, None, bucket))
    return groups


def weekly_counts(commits, first, last):
    weeks: dict[int, int] = {}
    total_weeks = ((last - first).days // 7) + 1
    for index in range(total_weeks):
        weeks[index] = 0
    for c in commits:
        idx = (c['date'] - first).days // 7
        weeks[idx] = weeks.get(idx, 0) + 1
    return [(first + datetime.timedelta(days=i * 7), weeks[i]) for i in sorted(weeks)]


# ---------------------------------------------------------------------------
# Rendering — same look as before (dark nav, indigo/coral), only the data
# layer and the entry markup (a single translated line instead of a
# PR-title + written-summary pair) changed.
# ---------------------------------------------------------------------------

def e(text) -> str:
    return html.escape(str(text), quote=True)


STYLE = """
    :root {
      --bg: #f8f9fb; --surface: #ffffff; --surface2: #f1f3f7; --border: rgba(0,0,0,0.08);
      --text: #1E2030; --muted: #6b7280;
      --coral: #FF6B6B; --coral-dark: #e84040; --indigo: #5641C4; --indigo-dark: #4331a0; --slate: #221848;
      --grad-coral: linear-gradient(135deg, #FF6B6B, #e84040);
      --gradient: linear-gradient(135deg, #5641C4, #4331a0);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: var(--bg); color: var(--text); line-height: 1.7; -webkit-font-smoothing: antialiased; }
    a { color: inherit; text-decoration: none; }
    .container { max-width: 1080px; margin: 0 auto; padding: 0 24px; }
    .badge { display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 12px; font-weight: 600;
             background: rgba(86,65,196,0.10); color: var(--indigo); border: 1px solid rgba(86,65,196,0.3);
             text-transform: uppercase; letter-spacing: 0.05em; }

    nav { position: fixed; top: 0; left: 0; right: 0; z-index: 100; background: rgba(34,24,72,0.97);
          backdrop-filter: blur(12px); border-bottom: 1px solid rgba(255,255,255,0.08); color: #fff; }
    .nav-inner { display: flex; align-items: center; justify-content: space-between; height: 60px; }
    .nav-logo { display: flex; align-items: center; gap: 10px; font-size: 15px; font-weight: 700; }
    .nav-logo svg { flex-shrink: 0; }
    .nav-logo-text { display: flex; align-items: baseline; gap: 0; }
    .nav-logo-text .n1 { color: var(--coral); }
    .nav-logo-text .n2 { color: #fff; font-weight: 400; }
    .nav-links { display: flex; gap: 28px; list-style: none; font-size: 14px; color: rgba(255,255,255,0.6);
                 margin: 0; padding: 0; }
    .nav-links a:hover { color: #fff; }
    .lang-switcher select { background: transparent; color: rgba(255,255,255,0.7);
                            border: 1px solid rgba(255,255,255,0.2); border-radius: 4px;
                            padding: 4px 8px; cursor: pointer; font-size: 13px; }

    .page-head { padding: 120px 0 56px; text-align: center; }
    .page-head h1 { font-size: clamp(26px, 4.5vw, 44px); font-weight: 800; line-height: 1.2; margin: 12px 0 16px; }
    .page-head h1 span { background: var(--grad-coral); -webkit-background-clip: text;
                         -webkit-text-fill-color: transparent; background-clip: text; }
    .page-head p { color: var(--muted); max-width: 680px; margin: 0 auto; }

    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px;
             max-width: 900px; margin: 44px auto 0; }
    .stat { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 22px 18px; }
    .stat-value { font-size: 30px; font-weight: 800; line-height: 1.1; }
    .stat-label { font-size: 12px; color: var(--muted); margin-top: 6px; letter-spacing: 0.02em; }

    section { padding: 56px 0; }
    .chart-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 28px; }
    .chart-title { font-size: 13px; font-weight: 700; color: var(--muted); letter-spacing: 0.05em;
                   text-transform: uppercase; margin-bottom: 20px; }
    .chart { display: flex; align-items: flex-end; gap: 4px; height: 140px; }
    .bar-col { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
               align-items: center; gap: 6px; height: 100%; }
    .bar { width: 100%; border-radius: 5px 5px 0 0; background: var(--gradient); min-height: 3px; }
    .bar.zero { background: var(--surface2); }

    .version { margin-bottom: 52px; }
    .version-head { border-left: 4px solid var(--indigo); padding-left: 18px; margin-bottom: 24px; }
    .version-tag { font-size: 12px; font-weight: 700; color: var(--indigo); letter-spacing: 0.05em; }
    .version-head h2 { font-size: clamp(20px, 3vw, 28px); font-weight: 700; margin: 6px 0 0; }

    .entries { display: flex; flex-direction: column; gap: 10px; }
    .entry { background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
             padding: 16px 20px; display: grid; grid-template-columns: 92px 1fr auto; gap: 18px;
             align-items: baseline; transition: border-color 0.2s, transform 0.2s; }
    .entry:hover { border-color: var(--indigo); transform: translateY(-2px); }
    .entry-date { font-size: 12px; color: var(--muted); white-space: nowrap; }
    .entry-pr { display: block; font-weight: 700; color: var(--indigo); }
    .entry-text { font-size: 14px; }
    .entry-diff { font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                  color: var(--muted); white-space: nowrap; }
    .entry-diff .add { color: #15803d; } .entry-diff .del { color: #b91c1c; }

    .note { margin-top: 40px; padding: 22px 24px; background: var(--surface2);
            border-radius: 14px; font-size: 13px; color: var(--muted); }
    .note h3 { font-size: 13px; font-weight: 700; color: var(--text); margin: 0 0 10px;
               text-transform: uppercase; letter-spacing: 0.05em; }
    .note ul { margin: 0; padding-left: 20px; } .note li { margin-bottom: 6px; }

    footer { border-top: 1px solid rgba(255,255,255,0.08); padding: 32px 0; text-align: center;
             font-size: 13px; color: rgba(255,255,255,0.4); background: var(--slate); }
    footer a { color: rgba(255,255,255,0.7); }

    @media (max-width: 720px) {
      .entry { grid-template-columns: 1fr; gap: 8px; }
      .entry-diff { display: none; }
    }
"""

LOGO_SVG = (
    '<svg width="28" height="28" viewBox="0 0 100 100" fill="none" stroke="#fff" '
    'stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">'
    '<line x1="10" y1="86" x2="90" y2="86" stroke-width="6"/>'
    '<line x1="26" y1="16" x2="26" y2="86" stroke-width="7"/>'
    '<line x1="74" y1="16" x2="74" y2="86" stroke-width="7"/>'
    '<line x1="10" y1="64" x2="90" y2="64" stroke-width="6"/>'
    '<path d="M 10 64 L 26 16 Q 50 86 74 16 L 90 64" stroke-width="6"/>'
    '<line x1="38" y1="64" x2="38" y2="44" stroke-width="3"/>'
    '<line x1="50" y1="64" x2="50" y2="52" stroke-width="3"/>'
    '<line x1="62" y1="64" x2="62" y2="44" stroke-width="3"/></svg>'
)

STRINGS = {
    'ja': {
        'lang': 'ja', 'other': 'history.en.html', 'index': 'index.ja.html',
        'title': 'SyncVey — 開発の記録',
        'eyebrow': '開発の記録',
        'h1_a': 'ここまでに', 'h1_span': '何をしてきたか',
        'lead': '全コミットを時系列で並べたもの。マージ済みプルリクエストだけを見ると、'
                'main へ直接積んだ変更が抜け落ちる ── 日付・行数・件数は git から取得しており、手で書いていない。',
        'nav_home': 'トップ', 'nav_features': '機能', 'nav_setup': '導入',
        'stat_days': '開発日数', 'stat_commits': 'コミット', 'stat_releases': 'リリース',
        'chart_title': '週ごとのコミット数',
        'released': 'リリース',
        'note_head': 'このページについて',
        'notes': [
            '単位はコミット。プルリクエストの一覧ではないので、main へ直接積んだ変更も漏れない'
            '（以前の版はこれが原因で直近 3 件が欠けていた）。',
            '区切りはタグ（バージョン）が付いた地点のみ。以前あった「フェーズ」のような'
            '主観的な区切りは廃止した ── 客観的に決まる境界だけを使っている。',
            '日英の文面は手作業の翻訳（コミットメッセージは英語期・日本語期のどちらか一方で'
            'しか書かれていない）。それ以外の数値はすべて生成時に git から計測している。',
            '翻訳がまだ無いコミットは、原文をそのまま両言語に出す。抜けにはならない。',
            '開発の記録ページ自身の自動更新コミット（"docs: rebuild the development-history '
            'page"）は集計から除外している。含めると再生成のたびにページ自身の分の記事が増える。',
        ],
        'pr_link': '#{n}',
    },
    'en': {
        'lang': 'en', 'other': 'history.ja.html', 'index': 'index.en.html',
        'title': 'SyncVey — Development history',
        'eyebrow': 'Development history',
        'h1_a': 'What has actually', 'h1_span': 'been built so far',
        'lead': 'Every commit, in order. A merged-pull-request-only list drops whatever landed by a '
                'direct push to main — dates, sizes and counts are read from git at build time '
                'rather than written by hand.',
        'nav_home': 'Home', 'nav_features': 'Features', 'nav_setup': 'Setup',
        'stat_days': 'Days', 'stat_commits': 'Commits', 'stat_releases': 'Releases',
        'chart_title': 'Commits per week',
        'released': 'Released',
        'note_head': 'About this page',
        'notes': [
            'The unit is the commit, not the pull request — so a direct push to main is never '
            'dropped (the previous version of this page missed its three most recent changes for '
            'exactly that reason).',
            'Sections break at tagged versions only. The earlier "phase" grouping was editorial and '
            'has been retired — only boundaries that are objectively decidable remain.',
            'The bilingual text is translated by hand (each commit message was written in one '
            'language, not both). Every other number on this page is measured from git at build time.',
            'A commit without a translation yet shows its original subject line on both pages — '
            'it is never simply missing.',
            "This page's own rebuild commits (\"docs: rebuild the development-history page\") are "
            'excluded from the count — otherwise every rebuild would add an entry for itself.',
        ],
        'pr_link': '#{n}',
    },
}


def render(lang, data, groups):
    s = STRINGS[lang]
    idx = 0 if lang == 'ja' else 1
    commits = data['commits']
    days = (data['last'] - data['first']).days + 1
    n_releases = len(data['tags'])

    weeks = weekly_counts(commits, data['first'], data['last'])
    peak = max((count for _start, count in weeks), default=0) or 1

    out = []
    add = out.append

    add(f'<!DOCTYPE html>\n<html lang="{s["lang"]}">\n<head>')
    add('<meta charset="UTF-8" />')
    add('<meta name="viewport" content="width=device-width, initial-scale=1" />')
    add(f'<title>{e(s["title"])}</title>')
    add(f'<meta name="description" content="{e(s["lead"])}" />')
    add('<link rel="icon" href="favicon.svg" type="image/svg+xml" />')
    add(f'<style>{STYLE}</style>')
    add('</head>\n<body>')

    add('<nav><div class="container nav-inner">')
    add(f'<a href="{s["index"]}" class="nav-logo">{LOGO_SVG}'
        '<div class="nav-logo-text"><span class="n1">Sync</span><span class="n2">Vey</span></div></a>')
    add('<ul class="nav-links">'
        f'<li><a href="{s["index"]}">{e(s["nav_home"])}</a></li>'
        f'<li><a href="{s["index"]}#features">{e(s["nav_features"])}</a></li>'
        f'<li><a href="{s["index"]}#setup">{e(s["nav_setup"])}</a></li>'
        '</ul>')
    other_label = 'EN' if lang == 'ja' else 'JP'
    this_label = 'JP' if lang == 'ja' else 'EN'
    add('<div class="lang-switcher"><select onchange="location.href=this.value;">'
        f'<option value="history.{s["lang"]}.html" selected>{this_label}</option>'
        f'<option value="{s["other"]}">{other_label}</option>'
        '</select></div>')
    add('</div></nav>')

    add('<header class="page-head"><div class="container">')
    add(f'<span class="badge">{e(s["eyebrow"])}</span>')
    add(f'<h1>{e(s["h1_a"])}<br><span>{e(s["h1_span"])}</span></h1>')
    add(f'<p>{e(s["lead"])}</p>')
    add('<div class="stats">')
    for value, label in ((days, s['stat_days']), (len(commits), s['stat_commits']),
                         (n_releases, s['stat_releases'])):
        add(f'<div class="stat"><div class="stat-value">{value}</div>'
            f'<div class="stat-label">{e(label)}</div></div>')
    add('</div></div></header>')

    add('<section><div class="container"><div class="chart-wrap">')
    add(f'<div class="chart-title">{e(s["chart_title"])}</div>')
    add('<div class="chart">')
    for _start, count in weeks:
        height = round(count / peak * 100)
        cls = 'bar zero' if count == 0 else 'bar'
        add(f'<div class="bar-col"><div class="{cls}" style="height:{height}%"></div></div>')
    add('</div></div></div></section>')

    add('<section><div class="container">')
    for ja_t, en_t, tag, listed in groups:
        title = (ja_t, en_t)[idx]
        lo, hi = listed[0]['date'], listed[-1]['date']
        span = f'{lo:%Y-%m-%d}' if lo == hi else f'{lo:%Y-%m-%d} – {hi:%Y-%m-%d}'
        anchor = tag or 'unreleased'
        add(f'<div class="version" id="{e(anchor)}"><div class="version-head">')
        add(f'<div class="version-tag">{e(span)}</div><h2>{e(title)}</h2>')
        add('</div><div class="entries">')
        for c in listed:
            text = (c['ja'], c['en'])[idx]
            add('<div class="entry">')
            add(f'<div class="entry-date">{c["date"]:%Y-%m-%d}')
            if c['pr']:
                add(f'<br><a class="entry-pr" href="https://github.com/{REPO}/pull/{c["pr"]}">'
                    f'{e(s["pr_link"].format(n=c["pr"]))}</a>')
            add('</div>')
            add(f'<div class="entry-text">{e(text)}</div>')
            add(f'<div class="entry-diff"><span class="add">+{c["added"]}</span> '
                f'<span class="del">-{c["removed"]}</span></div>')
            add('</div>')
        add('</div></div>')

    add(f'<div class="note"><h3>{e(s["note_head"])}</h3><ul>')
    for line in s['notes']:
        add(f'<li>{e(line)}</li>')
    add('</ul></div>')
    add('</div></section>')

    add('<footer><div class="container"><p>© 2026 SyncVey &nbsp;·&nbsp; '
        f'<a href="https://github.com/{REPO}">GitHub</a></p></div></footer>')
    add('</body>\n</html>')
    return '\n'.join(out) + '\n'


def main():
    data = collect()
    groups = group_by_version(data)

    for lang in ('ja', 'en'):
        path = REPO_ROOT / 'docs' / f'history.{lang}.html'
        path.write_text(render(lang, data, groups), encoding='utf-8')
        print(f'wrote {path.relative_to(REPO_ROOT)}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
