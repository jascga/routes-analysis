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
        # 第三段（型号）被忽略：保留 段1-段2-段4
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") == "BJ-DC-01"

    def test_two_devices_same_group(self):
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") == \
               parallel_group_key("BJ-DC-SPINE-01-10.1.1.2")

    def test_different_model_same_group(self):
        """第三段（型号）不同也视为同组"""
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") == \
               parallel_group_key("BJ-DC-LEAF-01-10.1.2.1")

    def test_different_region_different_group(self):
        """第一段不同 → 不同组"""
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") != \
               parallel_group_key("SH-DC-SPINE-01-10.1.1.1")

    def test_different_serial_different_group(self):
        """第四段（序号）不同 → 不同组"""
        assert parallel_group_key("BJ-DC-SPINE-01-10.1.1.1") != \
               parallel_group_key("BJ-DC-SPINE-02-10.1.1.2")

    def test_no_dash(self):
        assert parallel_group_key("UNKNOWN") == "UNKNOWN"

    def test_empty(self):
        assert parallel_group_key("") == ""

    # ---- nc 例外规则 ----
    def test_nc_underscore_normalized(self):
        """第二段 nc01_cnt01 → 保留 nc01_cnt，去掉 cnt 后的 01；型号也被忽略"""
        assert parallel_group_key("BJ-nc01_cnt01-LEAF-01-10.1.1.1") == "BJ-nc01_cnt-01"

    def test_nc_underscore_same_group_different_cnt(self):
        """不同 cnt 但同 nc → 同组"""
        assert parallel_group_key("BJ-nc01_cnt01-LEAF-01-10.1.1.1") == \
               parallel_group_key("BJ-nc01_cnt02-LEAF-01-10.1.1.2")

    def test_nc_underscore_same_group_different_model(self):
        """同 nc，不同 cnt，不同型号 → 同组"""
        assert parallel_group_key("BJ-nc01_cnt01-LEAF-01-10.1.1.1") == \
               parallel_group_key("BJ-nc01_cnt02-SPINE-01-10.1.1.2")

    def test_nc_underscore_different_nc_different_group(self):
        """不同 nc → 不同组"""
        assert parallel_group_key("BJ-nc01_cnt01-LEAF-01-10.1.1.1") != \
               parallel_group_key("BJ-nc02_cnt01-LEAF-01-10.1.1.3")

    def test_nc_case_insensitive(self):
        """NC / Nc / nc 都生效"""
        assert parallel_group_key("BJ-NC01_CNT01-LEAF-01-10.1.1.1") == "BJ-NC01_CNT-01"
        assert parallel_group_key("BJ-Nc01_cnt01-LEAF-01-10.1.1.1") == "BJ-Nc01_cnt-01"

    def test_non_nc_segment_not_affected(self):
        """第二段不以 nc 开头 → 第二段不变（但型号仍被去掉）"""
        assert parallel_group_key("BJ-abc_cnt01-LEAF-01-10.1.1.1") == "BJ-abc_cnt01-01"
        assert parallel_group_key("BJ-abc_xyz-LEAF-01-10.1.1.1") == "BJ-abc_xyz-01"

    def test_nc_in_other_segment_not_affected(self):
        """nc 出现在第三段→nc 规则不生效；第三段会被忽略"""
        assert parallel_group_key("BJ-DC-nc01_cnt01-01-10.1.1.1") == "BJ-DC-01"

    def test_nc_without_cnt_not_affected(self):
        """第二段是 nc01（无 _cnt）→ 不变"""
        assert parallel_group_key("BJ-nc01-LEAF-01-10.1.1.1") == "BJ-nc01-01"

    def test_nc_underscore_non_cnt_not_affected(self):
        """第二段是 nc01_xyz（不是 _cnt）→ 不变"""
        assert parallel_group_key("BJ-nc01_xyz-LEAF-01-10.1.1.1") == "BJ-nc01_xyz-01"

    def test_short_name_without_4_segments(self):
        """不足 4 段（去 IP 后），不应用型号删除"""
        assert parallel_group_key("BJ-DC-LEAF-10.1.1.1") == "BJ-DC-LEAF"


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
        """两组平行设备（不同第一段）→ 命中"""
        device = _make_device(
            "ME",
            routes=[
                _make_route("10.0.0.0/24", "GE0/0/1"),
                _make_route("10.0.0.0/24", "GE0/0/2"),
            ],
            peer_map={
                "GE0/0/1": "BJ-DC-SPINE-01-10.1.1.1",
                "GE0/0/2": "SH-DC-LEAF-01-10.1.2.1",
            },
        )
        result = MultiGroupAnalyzer(min_groups=2).analyze(device)
        assert result.hit_count == 1
        hit = result.hits[0]
        assert hit.destination == "10.0.0.0/24"
        assert hit.group_count == 2
        assert set(hit.group_keys) == {"BJ-DC-01", "SH-DC-01"}

    def test_same_group_no_hit(self):
        """两条路径都到同一组平行设备（同第一段、同第二段、同第四段）→ 不命中"""
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
        assert "BJ-DC-01" in result.group_members
        assert len(result.group_members["BJ-DC-01"]) == 2

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
