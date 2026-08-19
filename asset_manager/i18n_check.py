"""
i18n_check.py
-------------
Gate the translation catalogue on the two ways gettext fails *silently*.

``makemessages`` never leaves a new string blank: it copies the nearest existing
translation and flags the guess ``#, fuzzy`` — "a machine wrote this, a human
must confirm it." ``compilemessages`` then drops fuzzy entries from the ``.mo``
by default, so the string quietly falls back to its msgid (English) inside an
otherwise-translated page. Strip the flag without reading it and the *wrong*
translation ships instead, looking entirely intentional.

An empty ``msgstr`` fails the same way: the msgid renders untranslated.

Both failures are invisible to a reviewer reading the diff in the source
language — the catalogue looks full of confident translations. So the gate has
to be enforced, not remembered.

Pure stdlib, no Django import, so CI can run it with no settings module and no
database::

    python -m asset_manager.i18n_check
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Catalogues the gate protects. Add a locale here when you add one.
CATALOGUES = ('locale/ja/LC_MESSAGES/django.po',)

_MSGID = re.compile(r'^msgid\s+"(.*)"$')
_MSGSTR = re.compile(r'^msgstr(?:\[\d+\])?\s+"(.*)"$')
_CONTINUATION = re.compile(r'^"(.*)"$')
_PREVIOUS = re.compile(r'^#\|\s*msgid\s+"(.*)"$')


class Unreviewed:
    """One catalogue entry that must not reach a build."""

    def __init__(self, path, line, kind, msgid, previous=None):
        self.path, self.line, self.kind = path, line, kind
        self.msgid, self.previous = msgid, previous

    def __str__(self):
        if self.kind == 'fuzzy':
            bled = f' (machine-guessed from "{self.previous}")' if self.previous else ' (machine-guessed)'
            return f'{self.path}:{self.line}: unreviewed fuzzy translation for "{self.msgid}"{bled}'
        return f'{self.path}:{self.line}: empty translation for "{self.msgid}" — will render as English'


def _blocks(text):
    """Yield ``(start_line, [lines])`` for each blank-line-separated entry."""
    block, start = [], 1
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if not block:
                start = lineno
            block.append(line)
        elif block:
            yield start, block
            block = []
    if block:
        yield start, block


def _parse(block):
    """Return ``(fuzzy, msgid, previous, msgstrs)``, or ``None`` if obsolete."""
    fuzzy, msgid, previous, msgstrs = False, None, None, []
    target = None
    for line in block:
        if line.startswith('#~'):        # commented-out (obsolete) entry
            return None
        if line.startswith('#,'):
            fuzzy = 'fuzzy' in line
            continue
        match = _PREVIOUS.match(line)
        if match:
            previous = match.group(1)
            continue
        if line.startswith('#'):
            continue
        if line.startswith('msgid_plural'):
            target = None                 # its continuations belong to neither
            continue
        match = _MSGID.match(line)
        if match:
            msgid, target = match.group(1), 'msgid'
            continue
        match = _MSGSTR.match(line)
        if match:
            msgstrs.append(match.group(1))
            target = 'msgstr'
            continue
        match = _CONTINUATION.match(line)
        if match:
            if target == 'msgid':
                msgid += match.group(1)
            elif target == 'msgstr' and msgstrs:
                msgstrs[-1] += match.group(1)
    return fuzzy, msgid, previous, msgstrs


def find_unreviewed(catalogue):
    """Return every :class:`Unreviewed` entry in one ``.po`` file."""
    path = Path(catalogue)
    if not path.is_absolute():
        path = REPO_ROOT / path
    problems = []
    for start, block in _blocks(path.read_text(encoding='utf-8')):
        parsed = _parse(block)
        if parsed is None:
            continue
        fuzzy, msgid, previous, msgstrs = parsed
        if msgid is None or msgid == '':   # the catalogue header
            continue
        if fuzzy:
            problems.append(Unreviewed(catalogue, start, 'fuzzy', msgid, previous))
        elif msgstrs and all(s == '' for s in msgstrs):
            problems.append(Unreviewed(catalogue, start, 'empty', msgid))
    return problems


def main(catalogues=None):
    problems = []
    for catalogue in (catalogues or CATALOGUES):
        problems.extend(find_unreviewed(catalogue))

    if not problems:
        print(f'i18n: {len(CATALOGUES)} catalogue(s) clean — no fuzzy or empty entries.')
        return 0

    for problem in problems:
        print(str(problem), file=sys.stderr)
    print(
        f'\n{len(problems)} unreviewed entr{"y" if len(problems) == 1 else "ies"}. '
        f'Fix the msgstr and delete the "#, fuzzy" line, then recompile:\n'
        f'  django-admin compilemessages',
        file=sys.stderr,
    )
    return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:] or None))
