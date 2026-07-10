"""
翻訳カタログのゲート。

`#, fuzzy`（機械が推測しただけの未承認訳）と空の msgstr を落とす。
どちらも静かに壊れる: fuzzy は compilemessages に捨てられて msgid（英語）へ
フォールバックし、フラグを剥がせば誤訳がそのまま出荷される。空 msgstr も英語で出る。
英語でレビューする人にはどちらも見えないので、記憶ではなく CI で強制する。

DB も Django 設定も要らない純粋なファイル検査。
"""

from asset_manager.i18n_check import CATALOGUES, find_unreviewed


def test_catalogues_have_no_unreviewed_entries():
    problems = [p for catalogue in CATALOGUES for p in find_unreviewed(catalogue)]
    assert not problems, (
        'Unreviewed translations would ship:\n'
        + '\n'.join(f'  {p}' for p in problems)
        + '\n\nFix each msgstr, delete its "#, fuzzy" line, then recompile.'
    )
