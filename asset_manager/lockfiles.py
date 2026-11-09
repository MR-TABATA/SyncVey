"""ロックファイルから (エコシステム, 名前, 版) を取り出す。

対応: requirements.txt（== で固定したもの）/ poetry.lock / package-lock.json / yarn.lock（v1 と Berry）/ composer.lock / Gemfile.lock / go.mod。
ここは純粋な解析だけ（DB もネットワークも触らない）。元のファイルは保存しない — 呼び出し側は
解析結果の行だけを台帳に入れ、ファイルは捨てる。

エコシステム名は OSV.dev のもの: PyPI / npm / Packagist / RubyGems / Go。
"""
import json
import re
import tomllib

MAX_BYTES = 5 * 1024 * 1024
MAX_PACKAGES = 10000


class LockfileError(ValueError):
    """ユーザーに見せてよい文言を持つ解析失敗。"""


def parse(filename: str, raw: bytes):
    """-> (ecosystem, [(name, version), ...], skipped)。

    skipped は、版が固定されていない等で拾えなかった行の数（requirements.txt の `flask>=2` など）。
    """
    if len(raw) > MAX_BYTES:
        raise LockfileError('file too large')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise LockfileError('not a UTF-8 text file')

    base = (filename or '').rsplit('/', 1)[-1].lower()
    if base.endswith('.txt') and 'requirements' in base or base == 'requirements.txt':
        eco, found, skipped = 'PyPI', *_requirements(text)
    elif base == 'poetry.lock':
        eco, found, skipped = 'PyPI', *_poetry_lock(text)
    elif base == 'package-lock.json':
        eco, found, skipped = 'npm', *_package_lock(text)
    elif base == 'yarn.lock':
        eco, found, skipped = 'npm', *_yarn_lock(text)
    elif base == 'composer.lock':
        eco, found, skipped = 'Packagist', *_composer_lock(text)
    elif base == 'gemfile.lock':
        eco, found, skipped = 'RubyGems', *_gemfile_lock(text)
    elif base == 'go.mod':
        eco, found, skipped = 'Go', *_go_mod(text)
    else:
        raise LockfileError('unsupported file')

    unique = sorted(set(found))
    if len(unique) > MAX_PACKAGES:
        raise LockfileError('too many packages')
    return eco, unique, skipped


_REQ_LINE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*([A-Za-z0-9][A-Za-z0-9.+!_-]*)')


def _requirements(text):
    found, skipped = [], 0
    for line in text.splitlines():
        line = line.split(' #', 1)[0].split('\t#', 1)[0].strip()
        if not line or line.startswith(('#', '-', 'git+', 'http://', 'https://')):
            continue                                  # -r / -e / --hash / URL は対象外（数えない）
        line = line.split(';', 1)[0].strip()          # 環境マーカー
        m = _REQ_LINE.match(line)
        if m:
            found.append((re.sub(r'[-_.]+', '-', m.group(1)).lower(), m.group(2)))
        else:
            skipped += 1                              # flask>=2 など、版が固定されていない
    return found, skipped


def _poetry_lock(text):
    """poetry.lock（TOML）。`[[package]]` の name / version。

    `source` 表を持つもの（git・パス・URL・PyPI 以外のインデックス）は、PyPI の版と一致するとは限らない
    ので拾わず、skipped に数える（誤検知を避ける）。PyPI から入れたものは `source` を持たない。
    """
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):  # 深くネストした入力は RecursionError
        raise LockfileError('invalid TOML')
    packages = data.get('package')
    if not isinstance(packages, list):
        raise LockfileError('invalid lockfile')
    found, skipped = [], 0
    for pkg in packages:
        if not isinstance(pkg, dict):                 # package = ["x"] など。.get の前に確かめる
            skipped += 1
            continue
        name, version = pkg.get('name'), pkg.get('version')
        if not isinstance(name, str) or not isinstance(version, str) or pkg.get('source'):
            skipped += 1
            continue
        found.append((re.sub(r'[-_.]+', '-', name).lower(), version))
    return found, skipped


def _json(text):
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):               # 深くネストした入力は RecursionError
        raise LockfileError('invalid JSON')
    if not isinstance(data, dict):
        raise LockfileError('invalid lockfile')
    return data


def _package_lock(text):
    data = _json(text)
    found, skipped = [], 0
    packages = data.get('packages')
    if isinstance(packages, dict):                    # lockfileVersion 2 / 3
        for path, meta in packages.items():
            if not path or not isinstance(meta, dict):
                continue                              # '' は自分自身
            if meta.get('link') or 'node_modules/' not in path:
                continue                              # workspace のリンク
            name = path.rsplit('node_modules/', 1)[-1]
            version = meta.get('version')
            if name and isinstance(version, str):
                found.append((name, version))
            else:
                skipped += 1
    else:                                             # lockfileVersion 1: 入れ子の dependencies
        def walk(deps):
            for name, meta in (deps or {}).items():
                if not isinstance(meta, dict):
                    continue
                if isinstance(meta.get('version'), str):
                    found.append((name, meta['version']))
                walk(meta.get('dependencies'))
        walk(data.get('dependencies'))
    return found, skipped


def _composer_lock(text):
    data = _json(text)
    found, skipped = [], 0
    for key in ('packages', 'packages-dev'):
        for pkg in data.get(key) or []:
            name, version = (pkg or {}).get('name'), (pkg or {}).get('version')
            if isinstance(name, str) and isinstance(version, str):
                found.append((name.lower(), version[1:] if version[:1] == 'v' else version))
            else:
                skipped += 1
    return found, skipped


_YARN_V1_VERSION = re.compile(r'^  version "?([^"\s]+)"?\s*$')


def _yarn_name_and_range(key):
    """'@scope/name@^1.0.0' -> ('@scope/name', '^1.0.0')。範囲が無ければ None。"""
    at = key.find('@', 1)                             # 先頭の @ はスコープ
    if at <= 0:
        return None
    return key[:at], key[at + 1:]


def _yarn_lock(text):
    """yarn.lock。v1（`# yarn lockfile v1`）と Berry（v2 以降・`__metadata:`）の両方。

    registry から入れたものだけを拾う。git・file・link・GitHub の略記・workspace・patch・別名は、
    registry の版と一致するとは限らないので拾わず、skipped に数える。
    """
    if '__metadata:' in text:
        return _yarn_berry(text)
    if '# yarn lockfile v1' in text:
        return _yarn_v1(text)
    raise LockfileError('invalid lockfile')


def _yarn_v1(text):
    found, skipped = [], 0
    keys, version = None, None

    def flush():
        nonlocal skipped
        if keys is None:
            return
        parsed = [_yarn_name_and_range(k) for k in keys]
        # 範囲に ':' か '/' があるものは registry の版ではない（git:・file:・link:・user/repo・npm:別名）
        if (not parsed or version is None or any(p is None or ':' in p[1] or '/' in p[1] for p in parsed)
                or len({p[0] for p in parsed}) != 1):
            skipped += 1
            return
        found.append((parsed[0][0], version))

    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith('#') and line.rstrip().endswith(':'):
            flush()
            keys = [k.strip().strip('"') for k in line.rstrip()[:-1].split(',')]
            version = None
        elif keys is not None:
            m = _YARN_V1_VERSION.match(line)
            if m:
                version = m.group(1)
    flush()
    return found, skipped


_YARN_BERRY_RESOLUTION = re.compile(r'^  resolution: "?([^"\s]+)"?\s*$')


def _yarn_berry(text):
    found, skipped = [], 0
    for line in text.splitlines():
        m = _YARN_BERRY_RESOLUTION.match(line)        # 2 スペースだけ（依存の名前が resolution でも 4 スペース以上）
        if not m:
            continue
        parsed = _yarn_name_and_range(m.group(1))     # 'name@npm:1.2.6'。別名は実体の名前に解決済み
        if parsed and parsed[1].startswith('npm:') and re.match(r'^[0-9][^@\s]*$', parsed[1][4:]):
            found.append((parsed[0], parsed[1][4:]))
        else:
            skipped += 1                              # workspace:・patch:・git など
    return found, skipped


_GEM_SPEC = re.compile(r'^    ([A-Za-z0-9][A-Za-z0-9._-]*) \(([0-9][0-9A-Za-z.]*)(?:-[A-Za-z0-9_.-]+)?\)\s*$')   # -x86_64-linux などの platform は捨てる


def _gemfile_lock(text):
    found = []
    in_specs = False
    for line in text.splitlines():
        if line.strip() == 'specs:':
            in_specs = True
            continue
        if in_specs and line and not line.startswith(' '):
            in_specs = False                          # 次のセクション（PLATFORMS など）
        if in_specs:
            m = _GEM_SPEC.match(line)                 # 4 スペース = 本体。6 スペースの依存の制約行は拾わない
            if m:
                found.append((m.group(1), m.group(2)))
    return found, 0


_GO_REQ = re.compile(r'^\s*(?:require\s+)?([^\s]+)\s+(v\d[^\s]*)\s*$')


def _go_mod(text):
    found = []
    in_block = False
    for line in text.splitlines():
        line = line.split('//', 1)[0].rstrip()          # '// indirect' などのコメント
        s = line.strip()
        if s.startswith('require ('):
            in_block = True
            continue
        if in_block and s == ')':
            in_block = False
            continue
        if in_block or s.startswith('require '):
            m = _GO_REQ.match(line)
            if m:
                found.append((m.group(1), m.group(2)))
    return found, 0
