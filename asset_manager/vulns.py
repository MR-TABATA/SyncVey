"""
vulns.py
--------
台帳のライブラリ（ロックファイルから取り込んだ依存）の既知の脆弱性を OSV.dev で調べ、DB（VulnRecord）に保存する。

- 既定では無効（settings.VULN_REFRESH_ENABLED=false）。明示的に有効化したときだけ外部通信する。
- 送るのは (エコシステム, パッケージ名, 版) だけ。組織名・システム名・資産は送らない。
- OSV に問い合わせるのは「版まで分かっているライブラリ」だけ。手で登録した行（ecosystem 空）は対象外。
- RDS（PostgreSQL / MySQL）のエンジンは NVD で調べる。送るのは製品と版（CPE）だけ。結果は上流のその版に対する
  CVE であり、AWS が独自に直した分は拾えない（画面にもそう書く）。Aurora は版が上流と一致しないので対象外。
- 悪用の確認（CISA KEV）は印として添える。取得に失敗しても、脆弱性の取得は止めない。
- 事実だけを保存する（重大度・修正版・リンク）。「どうしろ」は持たない。
"""
import json
import logging
import re
import time
import urllib.error
import urllib.parse
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

NVD_API   = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_PAGE  = "https://nvd.nist.gov/vuln/detail/"
NVD_ECOSYSTEM = "NVD"
# RDS の Engine -> NVD の (vendor, product)。Aurora は含めない（版が上流と一致しない）。
_NVD_ENGINES = {'postgres': ('postgresql', 'postgresql'), 'mysql': ('oracle', 'mysql')}
_LEADING_VERSION = re.compile(r'^(\d+(?:\.\d+)*)')
_NVD_PAGE_SIZE = 2000

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


# --- NVD（RDS のエンジン）-------------------------------------------------------

def asset_cve_target(asset_type, raw):
    """資産 -> (ecosystem, name, version)。NVD で調べられないもの（Aurora など）は None。

    name は 'vendor:product'（NVD の CPE の一部）。VulnRecord の (ecosystem, name, version) に使う。
    """
    if (asset_type or '').upper() != 'RDS':
        return None
    raw = raw or {}
    vp = _NVD_ENGINES.get(str(raw.get('engine', '')).lower())
    m = _LEADING_VERSION.match(str(raw.get('engine_version', '')).strip())
    return (NVD_ECOSYSTEM, f"{vp[0]}:{vp[1]}", m.group(1)) if vp and m else None


def _nvd_request(url):
    headers = {"User-Agent": _USER_AGENT}
    key = getattr(settings, 'NVD_API_KEY', '')
    if key:
        headers["apiKey"] = key
    req = urllib.request.Request(url, headers=headers, method="GET")
    # nosec B310: URL は NVD_API 固定 + 組み立てた CPE（https）
    with urllib.request.urlopen(req, timeout=_TIMEOUT * 3) as resp:  # nosec B310
        return json.loads(resp.read())


def _nvd_pause():
    """NVD の速さ制限（API キーなし: 30 秒に 5 回、あり: 50 回）を守る。"""
    time.sleep(0.7 if getattr(settings, 'NVD_API_KEY', '') else 6.5)


def _query_nvd(name, version):
    """'vendor:product' と版 -> その版が脆弱とされる CVE の一覧（NVD の生データ）。"""
    vendor, product = name.split(':', 1)
    cpe = f"cpe:2.3:a:{vendor}:{product}:{version}:*:*:*:*:*:*:*"
    cves, start = [], 0
    while True:
        qs = urllib.parse.urlencode({'cpeName': cpe, 'resultsPerPage': _NVD_PAGE_SIZE, 'startIndex': start})
        data = _nvd_request(f"{NVD_API}?{qs}&isVulnerable")
        cves.extend(v['cve'] for v in data.get('vulnerabilities', []))
        start += data.get('resultsPerPage', 0)
        if not data.get('resultsPerPage') or start >= data.get('totalResults', 0):
            return cves
        _nvd_pause()


def _cvss_label(cve) -> str:
    scores = []
    for key in ('cvssMetricV40', 'cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
        scores.extend((m.get('cvssData') or {}).get('baseScore') for m in (cve.get('metrics') or {}).get(key, []))
    scores = [x for x in scores if isinstance(x, (int, float))]
    if not scores:
        return 'UNKNOWN'
    top = max(scores)
    return 'CRITICAL' if top >= 9 else 'HIGH' if top >= 7 else 'MEDIUM' if top >= 4 else 'LOW'


def _cve_advisory(cve, kev_ids) -> dict:
    desc = next((d['value'] for d in cve.get('descriptions', []) if d.get('lang') == 'en'), '')
    return {
        'id': cve['id'], 'aliases': [], 'summary': desc[:300],
        'severity': _cvss_label(cve), 'fixed': [],
        'kev': bool(cve.get('cisaExploitAdd')) or cve['id'] in kev_ids,
        'url': NVD_PAGE + cve['id'],
    }


def _nvd_targets():
    from .models import Asset
    found = set()
    for t, raw in (Asset.objects.filter(asset_type='RDS', missing_since__isnull=True)
                   .values_list('asset_type', 'raw_data')):
        target = asset_cve_target(t, raw)
        if target:
            found.add(target)
    return found


def _osv_targets():
    from .models import AppDependency
    return set(AppDependency.objects.exclude(ecosystem='').exclude(version='')
               .values_list('ecosystem', 'name', 'version'))


def _store(eco, name, version, vulns, advisories_builder):
    from .models import VulnRecord
    VulnRecord.objects.update_or_create(
        ecosystem=eco, name=name, version=version, defaults=advisories_builder(vulns))


def _refresh_osv(pairs, kev_ids_loader):
    """OSV で照合して保存。Returns (ok, vulnerable, failed)。"""
    if not pairs:
        return True, 0, 0
    pairs = sorted(pairs)
    try:
        counts = _query_batch(pairs)
    except (urllib.error.URLError, ValueError, OSError):
        logger.exception("OSV batch query failed")
        return False, 0, len(pairs)
    kev_ids = kev_ids_loader() if any(counts) else set()
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
        _store(eco, name, version, vulns, lambda v: build_record(eco, name, version, v, kev_ids))
        vulnerable += 1 if vulns else 0
    return True, vulnerable, failed


def _refresh_nvd(targets, kev_ids_loader):
    """RDS のエンジンを NVD で照合して保存。Returns (ok, vulnerable, failed)。"""
    if not targets:
        return True, 0, 0
    kev_ids = kev_ids_loader()
    vulnerable = failed = 0
    for i, (eco, name, version) in enumerate(sorted(targets)):
        if i:
            _nvd_pause()
        try:
            cves = _query_nvd(name, version)
        except (urllib.error.URLError, ValueError, OSError):
            logger.warning("NVD query failed for %s %s", name, version)
            failed += 1
            continue                          # 前回の結果を残す
        def build(v):
            advisories = sorted((_cve_advisory(c, kev_ids) for c in v),
                                key=lambda a: (-_SEVERITY_ORDER[a['severity']], a['id']))
            return {'advisories': advisories,
                    'max_severity': advisories[0]['severity'] if advisories else '',
                    'has_kev': any(a['kev'] for a in advisories)}
        _store(eco, name, version, cves, build)
        vulnerable += 1 if cves else 0
    return failed < len(targets), vulnerable, failed


def refresh_vulns(force: bool = False) -> dict:
    """台帳のライブラリ（OSV）と RDS のエンジン（NVD）を調べ、VulnRecord を更新する。

    Returns: {'ok', 'skipped', 'packages', 'vulnerable', 'failed', 'engines', 'engines_vulnerable', 'engines_failed'}
    """
    empty = {'ok': False, 'skipped': True, 'packages': 0, 'vulnerable': 0, 'failed': 0,
             'engines': 0, 'engines_vulnerable': 0, 'engines_failed': 0}
    if not settings.VULN_REFRESH_ENABLED and not force:
        logger.info("Vulnerability refresh skipped (VULN_REFRESH_ENABLED=false).")
        return empty

    from .models import VulnRecord
    osv_pairs, nvd_targets = _osv_targets(), _nvd_targets()

    # KEV は 1 回だけ取る（失敗しても印が付かないだけ）。どちらの取得でも使う。
    cache = {}
    def kev_ids():
        if 'ids' not in cache:
            cache['ids'] = fetch_kev_ids()
        return cache['ids']

    osv_ok, vulnerable, failed = _refresh_osv(osv_pairs, kev_ids)
    nvd_ok, e_vulnerable, e_failed = _refresh_nvd(nvd_targets, kev_ids)

    # もう台帳に無いものの記録は消す。照合の成否に関わらず「いま台帳にあるもの」だけを残す。
    keep = osv_pairs | nvd_targets
    for rec in VulnRecord.objects.all():
        if (rec.ecosystem, rec.name, rec.version) not in keep:
            rec.delete()
    return {'ok': osv_ok and nvd_ok, 'skipped': False,
            'packages': len(osv_pairs), 'vulnerable': vulnerable, 'failed': failed,
            'engines': len(nvd_targets), 'engines_vulnerable': e_vulnerable, 'engines_failed': e_failed}


def asset_vuln(asset):
    """資産の VulnRecord（RDS のエンジン）。対象外・未取得なら None。表示用。"""
    target = asset_cve_target(asset.asset_type, asset.raw_data)
    if target is None:
        return None
    from .models import VulnRecord
    eco, name, version = target
    return VulnRecord.objects.filter(ecosystem=eco, name=name, version=version).first()


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
