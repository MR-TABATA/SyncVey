"""
Tests for blast-radius step 1: reference extraction from raw_data.

Pure — no DB, no AWS. Placed under asset_manager/tests so pytest's
``python_files = test_*.py`` collects it while the plugin package stays a plain,
detachable module.
"""

import glob
import json

from syncvey_blast_radius import references
from syncvey_blast_radius.references import (
    extract_references,
    extract_reference_edges,
    looks_like_reference,
)
from syncvey_blast_radius.graph import (
    Edge,
    build_adjacency,
    build_edges,
    build_reverse_index,
)


class FakeAsset:
    """Duck-typed stand-in for an ``Asset`` row: what graph.py reads off it."""

    def __init__(self, cloud_id, raw_data=None, environment_id=1):
        self.cloud_id = cloud_id
        self.raw_data = raw_data or {}
        self.environment_id = environment_id


# ---------------------------------------------------------------------------
# looks_like_reference — scalar classification
# ---------------------------------------------------------------------------

class TestLooksLikeReference:
    def test_short_ids(self):
        for v in ('subnet-008b4d8b401', 'sg-0abc123def', 'vpc-06622a8f20001',
                  'i-0123456789abcdef', 'vol-0ff00ff00', 'igw-0a1b2c3d',
                  'nat-0deadbeef', 'eipalloc-0aabbcc11'):
            assert looks_like_reference(v), v

    def test_arns(self):
        assert looks_like_reference(
            'arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:'
            'loadbalancer/app/web/abc123'
        )
        assert looks_like_reference('arn:aws-us-gov:s3:::my-bucket')

    def test_non_references(self):
        for v in ('t3.micro', 'mysql', '8.0.35', 'ap-northeast-1', 'available',
                  '', 'web-server', 'i-am-a-name', 'subnet group', None, 42, True):
            assert not looks_like_reference(v), v

    def test_short_id_needs_hex_suffix(self):
        # 'sg-' with a non-hex name must not match (avoids false positives on
        # arbitrary hyphenated strings).
        assert not looks_like_reference('sg-production')
        assert not looks_like_reference('i-am-not-an-id')


# ---------------------------------------------------------------------------
# extract_references — the step-1 deliverable
# ---------------------------------------------------------------------------

class TestExtractReferences:
    def test_excludes_own_identity(self):
        raw = {
            'id': 'i-0self0000000000',
            'arn': 'arn:aws:ec2:ap-northeast-1:123:instance/i-0self0000000000',
            'subnet_id': 'subnet-0aaa11122',
            'vpc_id': 'vpc-0bbb33344',
        }
        refs = extract_references(raw)
        assert refs == {'subnet-0aaa11122', 'vpc-0bbb33344'}
        assert 'i-0self0000000000' not in refs

    def test_list_valued_references(self):
        raw = {
            'id': 'lb-x',
            'security_groups': ['sg-0aaa11122', 'sg-0bbb33344'],
            'subnets': ['subnet-0ccc55566', 'subnet-0ddd77788'],
        }
        assert extract_references(raw) == {
            'sg-0aaa11122', 'sg-0bbb33344',
            'subnet-0ccc55566', 'subnet-0ddd77788',
        }

    def test_ignores_meta_and_scalar_noise(self):
        raw = {
            'id': 'i-0self0000000000',
            'instance_type': 't3.micro',
            'ami': 'ami-0123456789',       # a reference-shaped value → kept
            'availability_zone': 'ap-northeast-1a',
            '_resource_type': 'aws_instance',
            '_scan_source': 'boto3',
        }
        assert extract_references(raw) == {'ami-0123456789'}

    def test_nested_blocks(self):
        raw = {
            'id': 'i-0self0000000000',
            'network_interface': [
                {'subnet_id': 'subnet-0aaa11122', 'device_index': 0},
            ],
            'root_block_device': {'volume_id': 'vol-0ff00ff00'},
        }
        assert extract_references(raw) == {'subnet-0aaa11122', 'vol-0ff00ff00'}

    def test_edges_keep_source_field(self):
        raw = {
            'id': 'lb-x',
            'vpc_id': 'vpc-0bbb33344',
            'security_groups': ['sg-0aaa11122'],
        }
        edges = extract_reference_edges(raw)
        assert ('vpc_id', 'vpc-0bbb33344') in edges
        assert ('security_groups', 'sg-0aaa11122') in edges

    def test_non_dict_input_is_safe(self):
        assert extract_references(None) == set()
        assert extract_references([]) == set()
        assert extract_reference_edges('nope') == []

    def test_deduplicates_field_id_pairs(self):
        raw = {'id': 'x', 'a': ['sg-0aaa11122', 'sg-0aaa11122']}
        assert extract_reference_edges(raw) == [('a', 'sg-0aaa11122')]


# ---------------------------------------------------------------------------
# Coverage sanity-check against the real fixture tfstates (imported shape)
# ---------------------------------------------------------------------------

class TestAgainstFixtures:
    def _import_shape_assets(self):
        """Mimic the importer: one raw_data dict per managed instance."""
        assets = []
        for path in glob.glob('fixtures/tfstates/aws/*.tfstate'):
            with open(path) as fh:
                data = json.load(fh)
            for res in data.get('resources', []):
                if res.get('mode') != 'managed' or not res.get('instances'):
                    continue
                attrs = dict(res['instances'][0].get('attributes') or {})
                attrs['_resource_type'] = res['type']
                assets.append(attrs)
        return assets

    def test_edges_are_recovered_from_attributes(self):
        assets = self._import_shape_assets()
        assert assets, 'expected fixture tfstates to load'

        total_refs = sum(len(extract_references(a)) for a in assets)
        # The fixtures are reference-dense (subnets, SGs, VPCs, ARNs); a healthy
        # graph must recover many edges purely from attribute values.
        assert total_refs > 50, total_refs

    def test_never_emits_a_resources_own_id(self):
        for attrs in self._import_shape_assets():
            own = {attrs.get(k) for k in references.SELF_KEYS}
            assert extract_references(attrs).isdisjoint(own)


# ---------------------------------------------------------------------------
# graph — step 2: reverse-index references into edges within an environment
# ---------------------------------------------------------------------------

class TestReverseIndex:
    def test_keyed_by_environment_then_cloud_id(self):
        a = FakeAsset('subnet-0aaa11122', environment_id=1)
        b = FakeAsset('vpc-0bbb33344', environment_id=2)
        index = build_reverse_index([a, b])
        assert index[1]['subnet-0aaa11122'] is a
        assert index[2]['vpc-0bbb33344'] is b
        assert 'vpc-0bbb33344' not in index[1]

    def test_skips_placeholder_cloud_ids(self):
        index = build_reverse_index([
            FakeAsset('-'), FakeAsset(''), FakeAsset(None),
            FakeAsset('i-0realdead00'),
        ])
        assert index == {1: {'i-0realdead00': index[1]['i-0realdead00']}}

    def test_first_seen_wins_on_duplicate(self):
        first = FakeAsset('vol-0dupe0000')
        second = FakeAsset('vol-0dupe0000')
        index = build_reverse_index([first, second])
        assert index[1]['vol-0dupe0000'] is first


class TestBuildEdges:
    def test_resolves_reference_to_sibling(self):
        inst = FakeAsset('i-0inst000000', {'subnet_id': 'subnet-0aaa11122'})
        subnet = FakeAsset('subnet-0aaa11122', {'vpc_id': 'vpc-0bbb33344'})
        vpc = FakeAsset('vpc-0bbb33344')
        edges = build_edges([inst, subnet, vpc])
        assert Edge('i-0inst000000', 'subnet-0aaa11122', 'subnet_id') in edges
        assert Edge('subnet-0aaa11122', 'vpc-0bbb33344', 'vpc_id') in edges

    def test_drops_dangling_references(self):
        # References a subnet that isn't among the imported assets.
        inst = FakeAsset('i-0inst000000', {'subnet_id': 'subnet-0notheer0'})
        assert build_edges([inst]) == []

    def test_does_not_cross_environments(self):
        # cloud_id is globally unique, but the target lives in another env.
        inst = FakeAsset('i-0inst000000', {'subnet_id': 'subnet-0aaa11122'},
                         environment_id=1)
        subnet = FakeAsset('subnet-0aaa11122', environment_id=2)
        assert build_edges([inst, subnet]) == []

    def test_deduplicates_on_src_dst_field(self):
        inst = FakeAsset('i-0inst000000',
                         {'security_groups': ['sg-0aaa11122', 'sg-0aaa11122']})
        sg = FakeAsset('sg-0aaa11122')
        assert build_edges([inst, sg]) == [
            Edge('i-0inst000000', 'sg-0aaa11122', 'security_groups'),
        ]

    def test_skips_self_reference(self):
        # A field echoing the asset's own cloud_id must not become a self-edge.
        a = FakeAsset('i-0self000000', {'clone_of': 'i-0self000000'})
        assert build_edges([a]) == []

    def test_ignores_placeholder_source(self):
        a = FakeAsset('-', {'subnet_id': 'subnet-0aaa11122'})
        subnet = FakeAsset('subnet-0aaa11122')
        assert build_edges([a, subnet]) == []


class TestBuildAdjacency:
    def test_undirected_by_default(self):
        inst = FakeAsset('i-0inst000000', {'subnet_id': 'subnet-0aaa11122'})
        subnet = FakeAsset('subnet-0aaa11122')
        adj = build_adjacency([inst, subnet])
        assert adj['i-0inst000000'] == {'subnet-0aaa11122'}
        assert adj['subnet-0aaa11122'] == {'i-0inst000000'}

    def test_directed_keeps_reference_direction_only(self):
        inst = FakeAsset('i-0inst000000', {'subnet_id': 'subnet-0aaa11122'})
        subnet = FakeAsset('subnet-0aaa11122')
        adj = build_adjacency([inst, subnet], undirected=False)
        assert adj == {'i-0inst000000': {'subnet-0aaa11122'}}


class TestGraphAgainstFixtures(TestAgainstFixtures):
    """Build a real environment out of the fixture tfstates and wire it up."""

    def _fake_assets(self, environment_id=1):
        assets = []
        for attrs in self._import_shape_assets():
            cloud_id = attrs.get('id') or attrs.get('arn')  # importer's rule
            if not cloud_id:
                continue
            assets.append(FakeAsset(cloud_id, attrs, environment_id))
        return assets

    def test_edges_are_wired_between_real_assets(self):
        assets = self._fake_assets()
        edges = build_edges(assets)
        # Every edge must land on an asset we actually hold, in the same env.
        held = {a.cloud_id for a in assets}
        assert edges, 'expected the fixture environment to wire up some edges'
        for e in edges:
            assert e.src in held and e.dst in held

    def test_cross_environment_reference_does_not_resolve(self):
        # An asset in env 2 that references a real env-1 cloud_id must not wire
        # an edge to it: references only resolve to same-environment siblings.
        env1 = self._fake_assets(environment_id=1)
        a_target = env1[0].cloud_id
        intruder = FakeAsset('i-0intruder00', {'subnet_id': a_target},
                             environment_id=2)
        edges = build_edges(env1 + [intruder])
        assert all(e.src != 'i-0intruder00' for e in edges)
