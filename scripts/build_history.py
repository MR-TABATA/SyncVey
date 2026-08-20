#!/usr/bin/env python3
"""Generate the development-history page (`docs/history.{ja,en}.html`).

The site ships one file per language, which means any hand-written page has two
copies of the same content drifting apart the moment one side is edited — the
exact failure `scripts/check_consistency.py` exists to catch. So this page is
generated: the narrative lives here once, as (ja, en) pairs, and both files come
out of the same run.

Facts come from git and the GitHub API rather than from memory:

    * merged pull requests   -> `gh pr list`
    * commit count and dates -> `git`
    * releases               -> `gh release list`

The phase grouping and the one-line summaries are editorial — they are the part
a machine cannot derive — so they are spelled out in PHASES below. Everything
else (dates, sizes, counts, the bar chart) is measured at build time.

    python3 scripts/build_history.py

Re-run it after merging anything you want listed, and commit the two HTML files.
"""

from __future__ import annotations

import datetime
import html
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO = 'MR-TABATA/SyncVey'
START = datetime.date(2026, 6, 5)   # first commit


# ---------------------------------------------------------------------------
# Editorial layer — the part that is judgement, not data
# ---------------------------------------------------------------------------

# (anchor, ja title, en title, ja lead, en lead, [pr numbers])
PHASES = [
    (
        'foundation',
        '土台をつくる', 'Laying the foundation',
        '資産台帳・AWS スキャン・tfstate 取込・構成図まで、まず動くものを一気に作った時期。'
        '最初の PR が「任意機能を疎結合にするプラグイン機構」だったのは偶然ではなく、'
        '後から機能を足しても本体が太らないようにするため。',
        'The stretch that produced something that runs end to end: the asset ledger, the AWS '
        'scan, tfstate import, the diagram. It is not an accident that the first pull request '
        'was a plugin seam — optional features had to be able to arrive later without the core '
        'swelling to meet them.',
        [1],
    ),
    (
        'drift',
        'ドリフトを深める', 'Going deeper on drift',
        '「差分が出る」だけでは使えない。推移が追えること、危険度が分かること、'
        '誰がやったか辿れること。ドリフト検知を一段ずつ実用に寄せた。',
        '"It reports a diff" is not yet useful. This is where drift detection grew the things '
        'that make a diff actionable: a history to compare against, a severity grade, and a '
        'name attached to the change.',
        [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    ),
    (
        'reach',
        '届く範囲を広げる', 'Widening the reach',
        'ダッシュボードの外へ。CI から叩ける CLI、日本語 UI の穴埋め、'
        'そして「Auto Scaling の増減で騒がない」という、現場で使うなら避けて通れない調整。',
        'Out beyond the dashboard: a CLI a pipeline can call, the holes in the Japanese UI, and '
        'the adjustment nobody can skip if the tool is going to run against a real account — '
        'not treating Auto Scaling churn as drift.',
        [16, 17],
    ),
    (
        'ground',
        '足場を固める → v0.1.0', 'Firming up the ground → v0.1.0',
        'テストが通るかは手元でしか分からず、リリースもゼロ、脆弱性の報告先も無かった。'
        '機能を足す前に、その状態を先に潰した。'
        '同じ期間に「AWS から消えたリソースが台帳に残り続ける」実バグも直している。',
        'Whether the tests passed was knowable only on one laptop; there were no releases and '
        'nowhere to report a vulnerability. That got closed out before any more features went '
        'in — alongside a real bug: resources deleted in AWS lingered in the ledger forever.',
        [18, 19, 20, 21, 22],
    ),
    (
        'backlog',
        '積み残しを回収 → v0.2.0', 'Clearing the backlog → v0.2.0',
        '7月に書いたまま開きっぱなしだった 3 本を、衝突を解いて片付けた。'
        'その過程で「削除されたリソースがドリフト総数から抜け落ちる」不具合と、'
        '未翻訳のまま出ていた 15 文字列が見つかり、どちらも塞いだ。',
        'Three pull requests written in July had been sitting open; this is where the conflicts '
        'were resolved and they landed. Doing so surfaced two more problems — deleted resources '
        'were being dropped from the drift totals, and fifteen strings were still rendering in '
        'English — and both were closed.',
        [13, 14, 15, 23, 24, 25, 26, 27],
    ),
]

# pr number -> (ja one-liner, en one-liner)
SUMMARIES = {
    1:  ('フィーチャーフラグ＋プラグイン機構。任意機能を疎結合な Django アプリとして着脱可能にした',
         'Feature flags and a plugin seam — optional features became detachable Django apps'),
    2:  ('ドリフト履歴が無限に伸びるのを環境ごとの保持件数で止めた',
         'Capped drift history per environment so the table stops growing forever'),
    3:  ('ドリフト履歴の日本語訳が fuzzy のまま出ていたのを修正',
         'Fixed fuzzy Japanese translations that had shipped for the drift-history strings'),
    4:  ('ドリフト履歴ビューのテストを追加、EFS のアイコン欠けも解消',
         'Covered the drift-history view with tests and closed an EFS icon gap'),
    5:  ('LP の記述を実装（EOL・2FA）に合わせた',
         'Aligned the landing page with what EOL and 2FA actually do'),
    6:  ('ダッシュボードにヒーロー行。ドリフト推移・EOL・スキャン鮮度を一目で',
         'A hero row on the dashboard — drift trend, EOL, and scan freshness at a glance'),
    7:  ('ドリフトをセキュリティ影響度で採点し、CloudTrail で変更者を特定（プラグイン）',
         'Grade drift by security impact and trace who changed it via CloudTrail (plugin)'),
    8:  ('drift-risk を README と LP に記載',
         'Documented drift-risk in the README and landing page'),
    9:  ('週次ドリフト・ブリーフィング。プラグインが定期ジョブを生やす継ぎ目も用意',
         'A weekly drift briefing, plus the seam that lets a plugin register a scheduled job'),
    10: ('週次ブリーフィングを README と LP に記載',
         'Documented the weekly briefing in the README and landing page'),
    11: ('ダウンロード時アンケートで流入経路と目的を取得',
         'Captured referral source and intent on the download survey'),
    12: ('「されるべきなのにされていない」シークレットのローテーションを検出。差分ではなく現在の状態を採点する',
         'Flag secret rotation that should have happened but did not — graded on standing state, not on a diff'),
    13: ('未レビュー（fuzzy・空）の翻訳でビルドを落とす CI ゲート',
         'A CI gate that fails the build on unreviewed translations — fuzzy or empty'),
    14: ('makemessages を一度も通っていなかった 50 文字列を翻訳',
         'Translated 50 strings that makemessages had never been run against'),
    15: ('影響波及範囲。ドリフトを起点に参照グラフを辿り、届く範囲を影響度順に出す（プラグイン）',
         'Blast radius — walk the reference graph out from each drift and rank what it reaches (plugin)'),
    16: ('着脱可能な CLI プラグイン。`syncvey drift --exit-code` で CI から叩ける',
         'A detachable CLI plugin — `syncvey drift --exit-code` makes a pipeline fail on drift'),
    17: ('Auto Scaling の増減はドリフトではなく churn として別枠に。オオカミ少年をやめた',
         'Auto Scaling churn stopped being counted as drift — the tool stopped crying wolf'),
    18: ('非公開ロードマップ用に /.private/ を gitignore',
         'Gitignored /.private/ for the non-public roadmap'),
    19: ('設定駆動のドキュメント整合性チェッカー。公表値の食い違いを機械的に検出',
         'A config-driven documentation consistency checker for numbers that disagree across docs'),
    20: ('GitHub Actions でテストスイートを回すようにした。それまで main に CI は無かった',
         'Put the test suite on GitHub Actions — until then main had no CI at all'),
    21: ('AWS から消えたリソースを検出。スキャンできた範囲に限って判定するので、'
         'API エラーで台帳が吹き飛ぶことはない',
         'Detect resources that vanished from AWS — judged only where the scan succeeded, so a '
         'transient API error can never be read as a mass deletion'),
    22: ('SECURITY.md と CHANGELOG.md を追加し、初回リリース v0.1.0 の準備を整えた',
         'Added SECURITY.md and CHANGELOG.md ahead of the first release'),
    23: ('独立していた i18n ワークフローを CI に統合。1 PR で 8 回走っていたのを 1 回に',
         'Folded the standalone i18n workflow into CI — it had been running eight times per pull request'),
    24: ('削除されたリソースがドリフト総数から抜け落ちる不具合を修正。'
         'ヒーロー帯と週次 Slack 通知の両方が過少報告していた',
         'Fixed deleted resources being dropped from the drift totals — both the dashboard hero '
         'band and the weekly Slack briefing were under-reporting'),
    25: ('影響波及範囲・Auto Scaling 節・Missing Since の未翻訳 15 文字列を翻訳',
         'Translated 15 strings still rendering in English — blast radius, the Auto Scaling section, Missing Since'),
    26: ('抽出すらされていない文字列でビルドを落とす CI ゲート。'
         '既存ゲートでは原理的に見えなかった穴を塞いだ',
         'A CI gate for strings never extracted into the catalogue — a hole the existing gate could not see'),
    27: ('v0.2.0 の変更内容を CHANGELOG に記録',
         'Logged the v0.2.0 changes in the changelog'),
}

# Pull requests that sat open long enough to be worth explaining.
LATE_MERGE_DAYS = 7


# ---------------------------------------------------------------------------
# Measured layer
# ---------------------------------------------------------------------------

def _gh_json(args):
    proc = subprocess.run(['gh', *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'gh {" ".join(args)} failed:\n{proc.stderr}')
    return json.loads(proc.stdout)


def _git(args):
    return subprocess.run(['git', *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()


def collect():
    prs = _gh_json([
        'pr', 'list', '--repo', REPO, '--state', 'merged', '--limit', '200',
        '--json', 'number,title,createdAt,mergedAt,additions,deletions,files',
    ])
    by_number = {}
    for pr in prs:
        by_number[pr['number']] = {
            'number':    pr['number'],
            'title':     pr['title'],
            'created':   datetime.date.fromisoformat(pr['createdAt'][:10]),
            'merged':    datetime.date.fromisoformat(pr['mergedAt'][:10]),
            'additions': pr['additions'],
            'deletions': pr['deletions'],
            'files':     len(pr['files']),
        }

    releases = _gh_json(['release', 'list', '--repo', REPO, '--json', 'tagName,publishedAt'])
    releases = sorted(
        ({'tag': r['tagName'], 'date': datetime.date.fromisoformat(r['publishedAt'][:10])}
         for r in releases),
        key=lambda r: r['date'],
    )

    return {
        'prs':      by_number,
        'releases': releases,
        'commits':  int(_git(['rev-list', '--count', 'HEAD'])),
        'first':    datetime.date.fromisoformat(_git(['log', '--reverse', '--format=%ad',
                                                      '--date=short']).split('\n')[0]),
        'last':     datetime.date.fromisoformat(_git(['log', '-1', '--format=%ad',
                                                      '--date=short'])),
    }


def weekly_counts(prs, first, last):
    """[(week_start, merged_pr_count), ...] covering the whole span."""
    weeks = {}
    total_weeks = ((last - first).days // 7) + 1
    for index in range(total_weeks):
        weeks[index] = 0
    for pr in prs.values():
        weeks[(pr['merged'] - first).days // 7] = weeks.get((pr['merged'] - first).days // 7, 0) + 1
    return [(first + datetime.timedelta(days=i * 7), weeks[i]) for i in sorted(weeks)]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def e(text):
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
    .chart { display: flex; align-items: flex-end; gap: 6px; height: 140px; }
    .bar-col { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
               align-items: center; gap: 6px; height: 100%; }
    .bar { width: 100%; border-radius: 5px 5px 0 0; background: var(--gradient); min-height: 3px; }
    .bar.zero { background: var(--surface2); }
    .bar-n { font-size: 11px; font-weight: 700; color: var(--muted); }
    .chart-axis { display: flex; gap: 6px; margin-top: 10px; }
    .chart-axis span { flex: 1; text-align: center; font-size: 10px; color: var(--muted);
                       white-space: nowrap; overflow: hidden; }

    .phase { margin-bottom: 52px; }
    .phase-head { border-left: 4px solid var(--indigo); padding-left: 18px; margin-bottom: 24px; }
    .phase-dates { font-size: 12px; font-weight: 700; color: var(--indigo); letter-spacing: 0.05em; }
    .phase-head h2 { font-size: clamp(20px, 3vw, 28px); font-weight: 700; margin: 6px 0 10px; }
    .phase-head p { color: var(--muted); font-size: 15px; margin: 0; max-width: 760px; }

    .entries { display: flex; flex-direction: column; gap: 12px; }
    .entry { background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
             padding: 18px 20px; display: grid; grid-template-columns: 92px 1fr auto; gap: 18px;
             align-items: baseline; transition: border-color 0.2s, transform 0.2s; }
    .entry:hover { border-color: var(--indigo); transform: translateY(-2px); }
    .entry-meta { font-size: 12px; color: var(--muted); white-space: nowrap; }
    .entry-pr { font-weight: 700; color: var(--indigo); }
    .entry-body h3 { font-size: 15px; font-weight: 700; margin: 0 0 4px; }
    .entry-body p { font-size: 14px; color: var(--muted); margin: 0; }
    .entry-note { display: inline-block; margin-top: 8px; font-size: 12px; color: var(--coral-dark);
                  background: rgba(255,107,107,0.08); border: 1px solid rgba(255,107,107,0.25);
                  border-radius: 6px; padding: 2px 8px; }
    .entry-diff { font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                  color: var(--muted); white-space: nowrap; }
    .entry-diff .add { color: #15803d; } .entry-diff .del { color: #b91c1c; }

    .release-row { display: flex; align-items: center; gap: 14px; margin: 22px 0 0;
                   padding: 16px 20px; border-radius: 14px; background: var(--slate); color: #fff; }
    .release-tag { font-weight: 800; font-size: 16px; color: var(--coral); }
    .release-row .when { font-size: 12px; color: rgba(255,255,255,0.55); margin-left: auto; }
    .release-row .what { font-size: 14px; color: rgba(255,255,255,0.8); }

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
      .chart-axis span { font-size: 9px; }
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
        'lead': 'マージされたプルリクエストを時系列で並べたもの。'
                '日付・規模・件数は git と GitHub API から取得しており、手で書いていない。',
        'nav_home': 'トップ', 'nav_features': '機能', 'nav_setup': '導入',
        'stat_days': '開発日数', 'stat_prs': 'マージ済み PR', 'stat_commits': 'コミット',
        'stat_releases': 'リリース',
        'chart_title': '週ごとのマージ数',
        'released': 'リリース',
        'note_head': 'このページについて',
        'notes': [
            '日付は GitHub 上でマージされた日（UTC）。',
            '「作成から N 日」は、書かれてからマージされるまで開いていた期間。'
            '長いものは他の作業を先に通していたか、衝突の解消が必要だったもの。',
            '区切りと一行要約は後から付けた解釈で、それ以外の数値はすべて生成時に計測している。',
        ],
        'open_days': '作成から {n} 日',
        'week_label': '{m}/{d}',
    },
    'en': {
        'lang': 'en', 'other': 'history.ja.html', 'index': 'index.en.html',
        'title': 'SyncVey — Development history',
        'eyebrow': 'Development history',
        'h1_a': 'What has actually', 'h1_span': 'been built so far',
        'lead': 'Every merged pull request, in order. Dates, sizes and counts are read from git '
                'and the GitHub API at build time rather than written by hand.',
        'nav_home': 'Home', 'nav_features': 'Features', 'nav_setup': 'Setup',
        'stat_days': 'Days', 'stat_prs': 'Merged PRs', 'stat_commits': 'Commits',
        'stat_releases': 'Releases',
        'chart_title': 'Pull requests merged per week',
        'released': 'Released',
        'note_head': 'About this page',
        'notes': [
            'Dates are the day the pull request was merged on GitHub (UTC).',
            '"open N days" is how long a pull request stood between being written and being '
            'merged. The long ones were waiting behind other work, or needed conflicts resolved.',
            'The phase grouping and the one-line summaries are editorial. Every number on this '
            'page is measured at build time.',
        ],
        'open_days': 'open {n} days',
        'week_label': '{m}/{d}',
    },
}


def render(lang, data):
    s = STRINGS[lang]
    idx = 0 if lang == 'ja' else 1
    prs, releases = data['prs'], data['releases']
    days = (data['last'] - data['first']).days + 1

    weeks = weekly_counts(prs, data['first'], data['last'])
    peak = max(count for _start, count in weeks) or 1

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

    # nav
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

    # head + stats
    add('<header class="page-head"><div class="container">')
    add(f'<span class="badge">{e(s["eyebrow"])}</span>')
    add(f'<h1>{e(s["h1_a"])}<br><span>{e(s["h1_span"])}</span></h1>')
    add(f'<p>{e(s["lead"])}</p>')
    add('<div class="stats">')
    for value, label in (
        (days, s['stat_days']), (len(prs), s['stat_prs']),
        (data['commits'], s['stat_commits']), (len(releases), s['stat_releases']),
    ):
        add(f'<div class="stat"><div class="stat-value">{value}</div>'
            f'<div class="stat-label">{e(label)}</div></div>')
    add('</div></div></header>')

    # chart
    add('<section><div class="container"><div class="chart-wrap">')
    add(f'<div class="chart-title">{e(s["chart_title"])}</div>')
    add('<div class="chart">')
    for _start, count in weeks:
        height = round(count / peak * 100)
        cls = 'bar zero' if count == 0 else 'bar'
        add(f'<div class="bar-col"><span class="bar-n">{count}</span>'
            f'<div class="{cls}" style="height:{height}%"></div></div>')
    add('</div><div class="chart-axis">')
    for start, _count in weeks:
        add(f'<span>{s["week_label"].format(m=start.month, d=start.day)}</span>')
    add('</div></div></div></section>')

    # phases
    shown_releases = set()
    add('<section><div class="container">')
    for anchor, ja_t, en_t, ja_lead, en_lead, numbers in PHASES:
        listed = [prs[n] for n in numbers if n in prs]
        if not listed:
            continue
        lo = min(p['merged'] for p in listed)
        hi = max(p['merged'] for p in listed)
        span = f'{lo:%Y-%m-%d}' if lo == hi else f'{lo:%Y-%m-%d} – {hi:%Y-%m-%d}'
        add(f'<div class="phase" id="{anchor}"><div class="phase-head">')
        add(f'<div class="phase-dates">{span}</div>')
        add(f'<h2>{e((ja_t, en_t)[idx])}</h2>')
        add(f'<p>{e((ja_lead, en_lead)[idx])}</p>')
        add('</div><div class="entries">')
        for pr in sorted(listed, key=lambda p: (p['merged'], p['number'])):
            summary = SUMMARIES.get(pr['number'], (pr['title'], pr['title']))[idx]
            open_days = (pr['merged'] - pr['created']).days
            add('<div class="entry">')
            add(f'<div class="entry-meta">{pr["merged"]:%Y-%m-%d}<br>'
                f'<a class="entry-pr" href="https://github.com/{REPO}/pull/{pr["number"]}">'
                f'#{pr["number"]}</a></div>')
            add(f'<div class="entry-body"><h3>{e(pr["title"])}</h3><p>{e(summary)}</p>')
            if open_days >= LATE_MERGE_DAYS:
                add(f'<span class="entry-note">{e(s["open_days"].format(n=open_days))}</span>')
            add('</div>')
            add(f'<div class="entry-diff"><span class="add">+{pr["additions"]}</span> '
                f'<span class="del">-{pr["deletions"]}</span></div>')
            add('</div>')
        add('</div>')
        # Any release published inside this phase's window. Phase windows can
        # touch (v0.1.0 shipped the same day the next phase started), so each
        # release is emitted once, in the first phase that contains it.
        for rel in releases:
            if rel['tag'] in shown_releases:
                continue
            if lo <= rel['date'] <= hi:
                shown_releases.add(rel['tag'])
                add('<div class="release-row">'
                    f'<span class="release-tag">{e(rel["tag"])}</span>'
                    f'<span class="what">{e(s["released"])}</span>'
                    f'<span class="when">{rel["date"]:%Y-%m-%d}</span></div>')
        add('</div>')

    # notes
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

    unknown = set(SUMMARIES) - set(data['prs'])
    if unknown:
        print(f'warning: summaries for pull requests that are not merged: '
              f'{sorted(unknown)}', file=sys.stderr)
    placed = {n for _a, _jt, _et, _jl, _el, numbers in PHASES for n in numbers}
    missing = sorted(set(data['prs']) - placed)
    if missing:
        print(f'warning: merged pull requests not placed in any phase, so they will not '
              f'appear: {missing}\n  Add them to PHASES in {Path(__file__).name}.',
              file=sys.stderr)

    for lang in ('ja', 'en'):
        path = REPO_ROOT / 'docs' / f'history.{lang}.html'
        path.write_text(render(lang, data), encoding='utf-8')
        print(f'wrote {path.relative_to(REPO_ROOT)}')

    return 1 if missing else 0


if __name__ == '__main__':
    raise SystemExit(main())
