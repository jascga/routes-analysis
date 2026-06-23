"""analyzer 单元测试"""

import pytest
from routesanalysis.analyzer import (
    parallel_group_key,
    is_well_formed_peer_name,
    MultiGroupAnalyzer,
)
from routesanalysis.models import Device, BgpRoute, RouteProtocol


# ---------------------------------------------------------------------------
# 分组键
# ---------------------------------------------------------------------------

class TestParallelGroupKey:
    def test_standard_format(self):
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") == "BJ-DC-SPINE-01"

    def test_two_devices_same_group(self):
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") == \
               parallel_group_key("BJ-DC-SPINE-01-10.1.1.2")

    def test_different_groups(self):
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") != \
               parallel_group_key("BJ-DC-LEAF-01-10.1.2.1")

    def test_no_dash(self):
        assert parallel_group_key("UNKNOWN") == "UNKNOWN"

    def test_empty(self):
        assert parallel_group_key("") == ""


class TestWellFormed:
    def test_ok(self):
        assert is_well_formed_peer_name("BJ-DC-SPINE-01-10.1.1.1") is True

    def test_no_dash(self):
        assert is_well_formed_peer_name("UNKNOWN") is False

    def test_empty(self):
        assert is_well_formed_peer_name("") is False


# ---------------------------------------------------------------------------
# 分析器
# ---------------------------------------------------------------------------

def _make_route(dest: str, intf: str, pre: int = 60, cost: int = 0,
                proto: RouteProtocol = RouteProtocol.BGP) -> BgpRoute:
    return BgpRoute(
        destination=dest, next_hop="1.1.1.1",
        interface=intf, pre=pre, cost=cost, protocol=proto,
    )


def _make_device(name: str, routes, peer_map) -> Device:
    return Device(name=name, filename=f"{name}.txt", routes=routes, interface_peer_map=peer_map)


class TestMultiGroupAnalyzer:

    def test_two_groups_hit(self):
        """两组平行设备 → 命中"""
        device = _make_device(
            "ME",
            routes=[
                _make_route("10.0.0.0/24", "GE0/0/1"),
                _make_route("10.0.0.0/24", "GE0/0/2"),
            ],
            peer_map={
                "GE0/0/1": "BJ-DC-SPINE-01-10.1.1.1",
                "GE0/0/2": "BJ-DC-LEAF-01-10.1.2.1",
            },
        )
        result = MultiGroupAnalyzer(min_groups=2).analyze(device)
        assert result.hit_count == 1
        hit = result.hits[0]
        assert hit.destination == "10.0.0.0/24"
        assert hit.group_count == 2
        assert set(hit.group_keys) == {"BJ-DC-SPINE-01", "BJ-DC-LEAF-01"}

    def test_same_group_no_hit(self):
        """两条路径都到同一组平行设备 → 不命中"""
        device = _make_device(
            "ME",
            routes=[
                _make_route("10.0.0.0/24", "GE0/0/1"),
                _make_route("10.0.0.0/24", "GE0/0/2"),
            ],
            peer_map={
                "GE0/0/1": "BJ-DC-SPINE-01-10.1.1.1",
                "GE0/0/2": "BJ-DC-SPINE-01-10.1.1.2",
            },
        )
        result = MultiGroupAnalyzer(min_groups=2).analyze(device)
        assert result.hit_count == 0
        # 但分组识别要正确
        assert "BJ-DC-SPINE-01" in result.group_members
        assert len(result.group_members["BJ-DC-SPINE-01"]) == 2

    def test_min_groups_3(self):
        """min_groups=3 时，2 组不命中、3 组命中"""
        device = _make_device(
            "ME",
            routes=[
                _make_route("10.0.0.0/24", "GE0/0/1"),
                _make_route("10.0.0.0/24", "GE0/0/2"),
                _make_route("10.0.1.0/24", "GE0/1/1"),
                _make_route("10.0.1.0/24", "GE0/1/2"),
                _make_route("10.0.1.0/24", "GE0/1/3"),
            ],
            peer_map={
                "GE0/0/1": "GroupA-01-1.1.1.1",
                "GE0/0/2": "GroupB-01-2.2.2.2",
                "GE0/1/1": "GroupA-01-1.1.1.1",
                "GE0/1/2": "GroupB-01-2.2.2.2",
                "GE0/1/3": "GroupC-01-3.3.3.3",
            },
        )
        result = MultiGroupAnalyzer(min_groups=3).analyze(device)
        assert result.hit_count == 1
        assert result.hits[0].destination == "10.0.1.0/24"

    def test_unparseable_peer_a_strategy(self):
        """方案 A：不规范的对端名按单独成组处理"""
        device = _make_device(
            "ME",
            routes=[
                _make_route("10.0.0.0/24", "GE0/0/1"),
                _make_route("10.0.0.0/24", "Eth-Trunk1"),  # 没有接口描述
            ],
            peer_map={
                "GE0/0/1": "BJ-DC-SPINE-01-10.1.1.1",
                # Eth-Trunk1 没映射 → 用接口名当对端
            },
        )
        result = MultiGroupAnalyzer(min_groups=2).analyze(device)
        # Eth-Trunk1 也是 "Eth-Trunk1" 当对端，含 '-'，所以是规范名
        # 但分组键是 "Eth" → 与 "BJ-DC-SPINE-01" 不同 → 命中
        assert result.hit_count == 1

    def test_unparseable_no_dash(self):
        """完全没有 '-' 的对端名"""
        device = _make_device(
            "ME",
            routes=[
                _make_route("10.0.0.0/24", "GE0/0/1"),
                _make_route("10.0.0.0/24", "ABCDEF"),
            ],
            peer_map={
                "GE0/0/1": "BJ-DC-SPINE-01-10.1.1.1",
                # ABCDEF 没映射，对端就是 "ABCDEF"
            },
        )
        result = MultiGroupAnalyzer(min_groups=2).analyze(device)
        assert result.hit_count == 1
        assert "ABCDEF" in result.unparseable_peers
        assert result.hits[0].has_unparseable_peer is True

    def test_no_interface_descriptions(self):
        """完全没有接口描述时，对端=接口名，每个接口独立成组（会命中，但全部标警告）"""
        device = _make_device(
            "ME",
            routes=[
                _make_route("10.0.0.0/24", "GE0/0/1"),
                _make_route("10.0.0.0/24", "GE0/0/2"),
            ],
            peer_map={},
        )
        result = MultiGroupAnalyzer(min_groups=2).analyze(device)
        # 接口名 "GE0/0/1" 不含 '-' → 分组键 = "GE0/0/1"（独立组）
        assert result.hit_count == 1
        assert result.hits[0].has_unparseable_peer is True
        assert "GE0/0/1" in result.unparseable_peers
        assert "GE0/0/2" in result.unparseable_peers

    def test_invalid_min_groups(self):
        with pytest.raises(ValueError):
            MultiGroupAnalyzer(min_groups=1)
