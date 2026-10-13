"""
vulns.py
--------
台帳のライブラリ（ロックファイルから取り込んだ依存）の既知の脆弱性を OSV.dev で調べ、DB（VulnRecord）に保存する。

- 既定では無効（settings.VULN_REFRESH_ENABLED=false）。明示的に有効化したときだけ外部通信する。
- 送るのは (エコシステム, パッケージ名, 版) だけ。組織名・システム名・資産は送らない。
- OSV に問い合わせるのは「版まで分かっているライブラリ」だけ。手で登録した行（ecosystem 空）は対象外。
- 悪用の確認（CISA KEV）は印として添える。取得に失敗しても、脆弱性の取得は止めない。
- 事実だけを保存する（重大度・修正版・リンク）。「どうしろ」は持たない。
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

OSV_QUERY = "https://api.osv.dev/v1/query"
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
KEV_URL   = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
OSV_PAGE  = "https://osv.dev/vulnerability/"
_USER_AGENT = "SyncVey-Vuln/1.0 (+https://github.com/MR-TABATA/SyncVey)"
_TIMEOUT = 20
_BATCH_SIZE = 500
_MAX_PAGES = 5            # 1 パッケージの脆弱性が多すぎるときの上限（Django の古い版で数十件）

_SEVERITY_ORDER = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3, 'CRITICAL': 4, 'UNKNOWN': 0}


def _request(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": _USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    # nosec B310: URL は上の固定値のみ（https）
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
        return json.loads(resp.read())


def fetch_kev_ids() -> set:
    """CISA KEV（悪用が確認された CVE）の ID 集合。取得できなければ空（印が付かないだけで続行する）。"""
    try:
        data = _request(KEV_URL)
        return {v['cveID'] for v in data.get('vulnerabilities', []) if 'cveID' in v}
    except (urllib.error.URLError, ValueError, KeyError, OSError):
        logger.warning("KEV fetch failed; continuing without the exploited-in-the-wild flag.")
        return set()


def _query_batch(pairs):
    """[(eco, name, version)] -> 同じ並びの「該当した脆弱性 ID の数」。"""
    counts = []
    for i in range(0, len(pairs), _BATCH_SIZE):
        chunk = pairs[i:i + _BATCH_SIZE]
        body = {'queries': [{'package': {'name': n, 'ecosystem': e}, 'version': v} for e, n, v in chunk]}
        results = _request(OSV_BATCH, body).get('results', [])
        if len(results) != len(chunk):
            raise ValueError("OSV batch result length mismatch")
        counts.extend(len(r.get('vulns', [])) for r in results)
    return counts


def _query_full(eco, name, version):
    """1 パッケージの脆弱性の詳細（要約・重大度・修正版）。"""
    vulns, token = [], None
    for _ in range(_MAX_PAGES):
        body = {'package': {'name': name, 'ecosystem': eco}, 'version': version}
        if token:
            body['page_token'] = token
        data = _request(OSV_QUERY, body)
        vulns.extend(data.get('vulns', []))
        token = data.get('next_page_token')
        if not token:
            break
    return vulns


def _severity(vuln) -> str:
    label = str((vuln.get('database_specific') or {}).get('severity', '')).upper()
    label = {'MODERATE': 'MEDIUM'}.get(label, label)
    return label if label in _SEVERITY_ORDER else 'UNKNOWN'


def _fixed_versions(vuln, eco, name):
    fixed = []
    for aff in vuln.get('affected', []):
        pkg = aff.get('package') or {}
        if pkg.get('ecosystem') != eco or str(pkg.get('name', '')).lower() != name.lower():
            continue
        for rng in aff.get('ranges', []):
            fixed.extend(e['fixed'] for e in rng.get('events', []) if 'fixed' in e)
    return sorted(set(fixed))


def _advisory(vuln, eco, name, kev_ids) -> dict:
    aliases = vuln.get('aliases', [])
    return {
        'id': vuln.get('id', ''),
        'aliases': aliases,
        'summary': (vuln.get('summary') or '')[:300],
        'severity': _severity(vuln),
        'fixed': _fixed_versions(vuln, eco, name),
        'kev': bool(kev_ids.intersection(aliases) or vuln.get('id') in kev_ids),
        'url': OSV_PAGE + vuln.get('id', ''),
    }


def build_record(eco, name, version, vulns, kev_ids) -> dict:
    advisories = sorted(
        (_advisory(v, eco, name, kev_ids) for v in vulns),
        key=lambda a: (-_SEVERITY_ORDER[a['severity']], a['id']),
    )
    top = advisories[0]['severity'] if advisories else ''
    return {'advisories': advisories, 'max_severity': top, 'has_kev': any(a['kev'] for a in advisories)}


def refresh_vulns(force: bool = False) -> dict:
    """台帳のライブラリを OSV で調べ、VulnRecord を更新する。

    Returns: {'ok', 'skipped', 'packages', 'vulnerable', 'failed'}
    """
    if not settings.VULN_REFRESH_ENABLED and not force:
        logger.info("Vulnerability refresh skipped (VULN_REFRESH_ENABLED=false).")
        return {'ok': False, 'skipped': True, 'packages': 0, 'vulnerable': 0, 'failed': 0}

    from .models import AppDependency, VulnRecord
    pairs = sorted(set(
        AppDependency.objects.exclude(ecosystem='').exclude(version='')
        .values_list('ecosystem', 'name', 'version')
    ))
    if not pairs:
        VulnRecord.objects.all().delete()
        return {'ok': True, 'skipped': False, 'packages': 0, 'vulnerable': 0, 'failed': 0}

    try:
        counts = _query_batch(pairs)
    except (urllib.error.URLError, ValueError, OSError):
        logger.exception("OSV batch query failed")
        return {'ok': False, 'skipped': False, 'packages': len(pairs), 'vulnerable': 0, 'failed': len(pairs)}

    kev_ids = fetch_kev_ids() if any(counts) else set()
    vulnerable = failed = 0
    for (eco, name, version), n in zip(pairs, counts):
        vulns = []
        if n:
            try:
                vulns = _query_full(eco, name, version)
            except (urllib.error.URLError, ValueError, OSError):
                logger.warning("OSV detail fetch failed for %s %s", name, version)
                failed += 1
                continue                      # 前回の結果を残す（空で上書きして「問題なし」にしない）
        VulnRecord.objects.update_or_create(
            ecosystem=eco, name=name, version=version,
            defaults=build_record(eco, name, version, vulns, kev_ids),
        )
        vulnerable += 1 if vulns else 0

    # もう台帳に無いライブラリの記録は消す
    keep = set(pairs)
    for rec in VulnRecord.objects.all():
        if (rec.ecosystem, rec.name, rec.version) not in keep:
            rec.delete()
    return {'ok': True, 'skipped': False, 'packages': len(pairs), 'vulnerable': vulnerable, 'failed': failed}


def attach_vulns(dependencies):
    """依存（AppDependency）に `.vuln`（VulnRecord か None）を付ける。表示用。1 クエリ。"""
    from .models import VulnRecord
    deps = [d for d in dependencies if d.ecosystem]
    if not deps:
        return
    wanted = {(d.ecosystem, d.name, d.version) for d in deps}
    records = {
        (r.ecosystem, r.name, r.version): r
        for r in VulnRecord.objects.filter(ecosystem__in={e for e, _, _ in wanted})
    }
    for d in deps:
        d.vuln = records.get((d.ecosystem, d.name, d.version))
