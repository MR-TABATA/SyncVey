#!/usr/bin/env python3
"""Fail when translatable strings exist in the code but not in the catalogue.

`asset_manager.i18n_check` guards the entries that *are* in the `.po`: fuzzy
guesses and empty msgstrs. It cannot see the other failure — a string wrapped in
`{% trans %}` or `gettext()` that `makemessages` was never run against. That
string is absent from the catalogue entirely, so nothing flags it, and it
renders in English inside an otherwise-translated page.

That is not a hypothetical. It has happened three times in this repo:

  * PR #14  — 50 strings (drift-risk templates and python, dashboard hero band)
  * PR #25  — 15 strings (blast-radius, Auto Scaling section, Missing Since)

Both were found by a human noticing English on a Japanese screen, which is the
worst way to find it. This gate finds it on the branch that introduces it.

How: run `makemessages` against a throwaway copy of the catalogue, then compare
**msgid sets** with the committed one. Comparing whole files would be useless —
`makemessages` rewrites every `#: file:line` comment whenever code moves, so a
plain diff is noise. The set of msgids is stable under that churn and is exactly
what "was this string extracted?" means.

The committed catalogue is restored afterwards; this script never leaves the
working tree modified.

    python3 scripts/check_i18n_extraction.py

Needs Django and gettext (`xgettext`) — unlike `asset_manager.i18n_check`,
which is deliberately dependency-free.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from asset_manager.i18n_check import CATALOGUES, _blocks, _parse  # noqa: E402


def _msgids(po_text: str) -> set[str]:
    """Every real msgid in a catalogue (header and obsolete entries excluded)."""
    found = set()
    for _start, block in _blocks(po_text):
        parsed = _parse(block)
        if parsed is None:          # obsolete "#~" entry
            continue
        _fuzzy, msgid, _previous, _msgstrs = parsed
        if msgid:                   # "" is the catalogue header
            found.add(msgid)
    return found


def _locale_of(catalogue: str) -> str:
    """'locale/ja/LC_MESSAGES/django.po' -> 'ja'."""
    return Path(catalogue).parts[1]


def _run_makemessages(locale: str) -> None:
    env = dict(os.environ)
    # makemessages loads settings, and settings insist on these two. No database
    # is touched, so an in-memory sqlite URL is enough to get past the parser.
    env.setdefault('DATABASE_URL', 'sqlite://:memory:')
    env.setdefault('SECRET_KEY', 'i18n-extraction-gate-not-a-real-secret')
    proc = subprocess.run(
        [sys.executable, 'manage.py', 'makemessages', '-l', locale, '--no-obsolete'],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f'makemessages failed:\n{proc.stdout}\n{proc.stderr}'
        )


def check(catalogue: str) -> list[str]:
    """Return msgids present in the code but missing from the committed .po."""
    path = REPO_ROOT / catalogue
    committed = _msgids(path.read_text(encoding='utf-8'))

    with tempfile.TemporaryDirectory() as tmp:
        backup = Path(tmp) / 'django.po'
        shutil.copy2(path, backup)
        try:
            _run_makemessages(_locale_of(catalogue))
            extracted = _msgids(path.read_text(encoding='utf-8'))
        finally:
            # Always put the committed catalogue back, even if makemessages
            # blew up halfway through rewriting it.
            shutil.copy2(backup, path)

    return sorted(extracted - committed)


def main() -> int:
    if shutil.which('xgettext') is None:
        print(
            'i18n: xgettext not found — install gettext to run this gate.',
            file=sys.stderr,
        )
        return 2

    missing_by_catalogue = {}
    for catalogue in CATALOGUES:
        try:
            missing = check(catalogue)
        except RuntimeError as exc:
            print(f'i18n: {exc}', file=sys.stderr)
            return 2
        if missing:
            missing_by_catalogue[catalogue] = missing

    if not missing_by_catalogue:
        print(
            f'i18n: {len(CATALOGUES)} catalogue(s) complete — '
            f'every translatable string is extracted.'
        )
        return 0

    total = 0
    for catalogue, missing in missing_by_catalogue.items():
        total += len(missing)
        print(f'{catalogue}: {len(missing)} string(s) never extracted:', file=sys.stderr)
        for msgid in missing:
            shown = msgid if len(msgid) <= 70 else msgid[:67] + '...'
            print(f'  - "{shown}"', file=sys.stderr)
    print(
        f'\n{total} translatable string(s) exist in the code but not in the '
        f'catalogue, so they render in English.\n'
        f'Extract and translate them:\n'
        f'  python manage.py makemessages -l ja --no-obsolete\n'
        f'  # review every "#, fuzzy" — those are machine guesses, often wrong\n'
        f'  python manage.py compilemessages -l ja',
        file=sys.stderr,
    )
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
