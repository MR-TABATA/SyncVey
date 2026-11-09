"""脆弱性アドバイザリの一覧の畳み方（templates/_advisory_items.html）。"""
from django.template.loader import render_to_string
from django.utils import translation


def _adv(n):
    return [{'id': f'X-{i:03d}', 'severity': 'HIGH', 'summary': f's{i}', 'kev': False,
             'fixed': ['1.0'], 'url': f'https://example.invalid/{i}'} for i in range(n)]


def _render(advisories, **extra):
    return render_to_string('_advisory_items.html', {'advisories': advisories, **extra})


def test_eight_or_fewer_are_shown_in_full_without_a_fold():
    html = _render(_adv(8))
    assert '<details' not in html
    assert all(f'X-{i:03d}' in html for i in range(8))


def test_more_than_eight_shows_the_top_five_and_folds_the_rest():
    html = _render(_adv(9))
    assert html.count('<details') == 1
    head, folded = html.split('<details', 1)
    assert [f'X-{i:03d}' in head for i in range(9)] == [True] * 5 + [False] * 4   # 上位 5 件だけが、畳みの外
    assert [f'X-{i:03d}' in folded for i in range(9)] == [False] * 5 + [True] * 4  # 残りは、畳みの中
    assert 'Show 4 more (lower severity)' in html


def test_a_long_list_keeps_every_advisory_and_the_given_order():
    html = _render(_adv(66))
    assert html.count('<li class="flex') == 66                                      # 1 件も落とさない
    assert 'Show 61 more (lower severity)' in html
    ids = [f'X-{i:03d}' for i in range(66)]
    assert sorted(ids, key=html.index) == ids                                      # 渡された順（重大度の高い順）のまま


def test_fixed_versions_only_when_asked_for():
    assert 'Fixed in' in _render(_adv(2), show_fixed=True)
    assert 'Fixed in' not in _render(_adv(2))


def test_summary_is_escaped():
    adv = _adv(1)
    adv[0]['summary'] = '<script>alert(1)</script>'
    html = _render(adv)
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_the_fold_label_is_translated_into_japanese():
    with translation.override('ja'):
        assert 'ほか 61 件を表示' in _render(_adv(66))
