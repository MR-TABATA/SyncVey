"""ロックファイルの解析（純粋な関数。DB もネットワークも使わない）。"""
import json

import pytest

from asset_manager.lockfiles import LockfileError, parse


def test_requirements_pinned_only_and_skips_the_rest():
    txt = b"""# comment
Django==3.2.0   # pinned
Flask[async]==2.0.1 ; python_version >= "3.8"
requests>=2.0
-r other.txt
--hash=sha256:abc
git+https://github.com/x/y.git
Pillow===9.0.0
Some_Package==1.0.0
"""
    eco, found, skipped = parse('requirements.txt', txt)
    assert eco == 'PyPI'
    assert found == [('django', '3.2.0'), ('flask', '2.0.1'), ('pillow', '9.0.0'), ('some-package', '1.0.0')]
    assert skipped == 1                                  # requests>=2.0 だけ（-r や URL は数えない）


def test_requirements_variants_by_filename():
    assert parse('requirements-dev.txt', b'a==1')[0] == 'PyPI'
    assert parse('sub/dir/requirements.txt', b'a==1')[1] == [('a', '1')]


def test_package_lock_v3_includes_nested_copies_but_not_root_or_links():
    lock = {'lockfileVersion': 3, 'packages': {
        '': {'name': 'app', 'version': '1.0.0'},
        'node_modules/lodash': {'version': '4.17.15'},
        'node_modules/a/node_modules/lodash': {'version': '3.0.0'},
        'node_modules/@scope/pkg': {'version': '2.1.0'},
        'packages/ws': {'link': True},
    }}
    eco, found, _ = parse('package-lock.json', json.dumps(lock).encode())
    assert eco == 'npm'
    assert found == [('@scope/pkg', '2.1.0'), ('lodash', '3.0.0'), ('lodash', '4.17.15')]


def test_package_lock_v1_walks_nested_dependencies():
    lock = {'lockfileVersion': 1, 'dependencies': {
        'a': {'version': '1.0.0', 'dependencies': {'b': {'version': '2.0.0'}}}}}
    assert parse('package-lock.json', json.dumps(lock).encode())[1] == [('a', '1.0.0'), ('b', '2.0.0')]


def test_composer_lock_strips_v_and_reads_dev_packages():
    lock = {'packages': [{'name': 'Laravel/Framework', 'version': 'v8.0.0'}],
            'packages-dev': [{'name': 'phpunit/phpunit', 'version': '9.5.0'}]}
    eco, found, _ = parse('composer.lock', json.dumps(lock).encode())
    assert eco == 'Packagist'
    assert found == [('laravel/framework', '8.0.0'), ('phpunit/phpunit', '9.5.0')]


def test_gemfile_lock_takes_specs_not_constraint_lines():
    txt = b"""GEM
  remote: https://rubygems.org/
  specs:
    rails (6.1.0)
      actionpack (= 6.1.0)
    nokogiri (1.13.0-x86_64-linux)
    rack (2.2.3)

PLATFORMS
  ruby

DEPENDENCIES
  rails (~> 6.1)
"""
    eco, found, _ = parse('Gemfile.lock', txt)
    assert eco == 'RubyGems'
    assert found == [('nokogiri', '1.13.0'), ('rack', '2.2.3'), ('rails', '6.1.0')]


def test_go_mod_block_and_single_line_requires():
    txt = b"""module example.com/app

go 1.21

require github.com/gin-gonic/gin v1.7.0

require (
	golang.org/x/text v0.3.7 // indirect
	github.com/pkg/errors v0.9.1
)

replace github.com/pkg/errors => ./errors
"""
    eco, found, _ = parse('go.mod', txt)
    assert eco == 'Go'
    assert found == [('github.com/gin-gonic/gin', 'v1.7.0'), ('github.com/pkg/errors', 'v0.9.1'),
                     ('golang.org/x/text', 'v0.3.7')]


@pytest.mark.parametrize('name,raw', [
    ('package-lock.json', b'not json'),
    ('package-lock.json', b'[]'),
    ('Dockerfile', b'FROM python'),
    ('requirements.txt', b'\xff\xfe\x00bad'),
    ('requirements.txt', b'x' * (5 * 1024 * 1024 + 1)),
])
def test_bad_input_is_a_clean_error(name, raw):
    with pytest.raises(LockfileError):
        parse(name, raw)
