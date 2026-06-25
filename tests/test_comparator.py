"""
测试BGP路由比较器
"""

import pytest
from pathlib import Path
from routesanalysis.comparator import (
    BgpRouteComparator, OptimizedBgpComparator,
    compare_bgp_files, compare_two_bgp_files
)
from routesanalysis.parsing import parse_bgp_file
from routesanalysis.models import (
    Device, BgpRoute, RouteProtocol,
    RouteDifference, DifferenceType
)


class TestBgpRouteComparator:
    """测试BGP路由比较器"""

    @pytest.fixture
    def sample_devices(self):
        """创建示例设备用于测试"""
        # 设备1的路由
        routes1 = [
            BgpRoute(
                destination="10.0.0.0/24",
                next_hop="192.168.1.1",
                interface="GigabitEthernet0/0/1",
                pre=60,
                cost=0,
                protocol=RouteProtocol.BGP
            ),
            BgpRoute(
                destination="10.0.1.0/24",
                next_hop="192.168.1.2",
                interface="GigabitEthernet0/0/2",
                pre=60,
                cost=0,
                protocol=RouteProtocol.IBGP
            ),
            BgpRoute(
                destination="10.0.2.0/24",
                next_hop="192.168.1.3",
                interface="GigabitEthernet0/0/3",
                pre=60,
                cost=100,
                protocol=RouteProtocol.EBGP
            ),
        ]

        # 设备2的路由（有些差异）
        routes2 = [
            BgpRoute(
                destination="10.0.0.0/24",  # 相同
                next_hop="192.168.2.1",
                interface="GigabitEthernet0/0/1",
                pre=60,
                cost=0,
                protocol=RouteProtocol.BGP
            ),
            # 缺少 10.0.1.0/24（缺少Destination）
            BgpRoute(
                destination="10.0.2.0/24",  # Cost不同
                next_hop="192.168.2.3",
                interface="GigabitEthernet0/0/3",
                pre=60,
                cost=150,  # 不同Cost
                protocol=RouteProtocol.EBGP
            ),
            BgpRoute(
                destination="10.0.3.0/24",  # 额外Destination
                next_hop="192.168.2.4",
                interface="GigabitEthernet0/0/4",
                pre=60,
                cost=0,
                protocol=RouteProtocol.BGP
            ),
        ]

        device1 = Device(name="Switch-01", filename="file1.txt", routes=routes1)
        device2 = Device(name="Switch-02", filename="file2.txt", routes=routes2)

        return device1, device2

    @pytest.fixture
    def comparator(self):
        """创建比较器实例"""
        return BgpRouteComparator(max_workers=1)  # 单线程测试

    def test_add_device(self, comparator, sample_devices):
        """测试添加设备"""
        device1, device2 = sample_devices

        comparator.add_device(device1)
        comparator.add_device(device2)

        assert len(comparator.devices) == 2
        assert comparator.devices[0].name == "Switch-01"
        assert comparator.devices[1].name == "Switch-02"

    def test_set_baseline(self, comparator, sample_devices):
        """测试设置基准设备"""
        device1, device2 = sample_devices

        comparator.add_device(device1)
        comparator.add_device(device2)

        # 设置第一个设备为基准
        comparator.set_baseline(0)
        assert comparator.baseline_index == 0

        # 设置第二个设备为基准
        comparator.set_baseline(1)
        assert comparator.baseline_index == 1

        # 测试无效索引
        with pytest.raises(IndexError):
            comparator.set_baseline(2)

    def test_compare_two_devices(self, comparator, sample_devices):
        """测试比较两个设备"""
        device1, device2 = sample_devices

        differences = comparator.compare_two_devices(device1, device2)

        # 应该有3个差异：
        # 1. Switch-02缺少10.0.1.0/24（缺少Destination）
        # 2. Switch-01缺少10.0.3.0/24（缺少Destination）
        # 3. 10.0.2.0/24的Cost不同（Pre/Cost差异）
        assert len(differences) == 3

        # 检查差异类型
        diff_types = [diff.difference_type for diff in differences]
        assert DifferenceType.MISSING_DESTINATION in diff_types
        assert DifferenceType.PRE_COST_DIFFERENCE in diff_types

        # 检查具体差异
        missing_dest_diffs = [d for d in differences if d.difference_type == DifferenceType.MISSING_DESTINATION]
        assert len(missing_dest_diffs) == 2

        pre_cost_diffs = [d for d in differences if d.difference_type == DifferenceType.PRE_COST_DIFFERENCE]
        assert len(pre_cost_diffs) == 1

        # 检查Pre/Cost差异的详细信息
        pre_cost_diff = pre_cost_diffs[0]
        assert pre_cost_diff.destination == "10.0.2.0/24"
        assert pre_cost_diff.details["device1_cost"] == 100
        assert pre_cost_diff.details["device2_cost"] == 150

    def test_compare_all_with_baseline(self, comparator, sample_devices):
        """测试使用基准设备比较所有设备"""
        device1, device2 = sample_devices

        comparator.add_device(device1)
        comparator.add_device(device2)
        comparator.set_baseline(0)  # Switch-01为基准

        result = comparator.compare_all()

        assert result.baseline_device.name == "Switch-01"
        assert len(result.compared_devices) == 1
        assert result.compared_devices[0].name == "Switch-02"
        assert len(result.differences) == 3

        # 检查汇总信息
        assert result.summary["baseline_device"] == "Switch-01"
        assert result.summary["compared_devices"] == ["Switch-02"]
        assert result.summary["total_differences"] == 3

    def test_compare_all_with_multiple_devices(self):
        """测试比较多个设备"""
        # 创建三个设备
        routes1 = [
            BgpRoute("10.0.0.0/24", "192.168.1.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
        ]
        routes2 = [
            BgpRoute("10.0.0.0/24", "192.168.2.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.1.0/24", "192.168.2.2", "GE0/0/2", 60, 0, RouteProtocol.BGP),  # 额外
        ]
        routes3 = [
            BgpRoute("10.0.0.0/24", "192.168.3.1", "GE0/0/1", 70, 0, RouteProtocol.BGP),  # Pre不同
        ]

        device1 = Device("Switch-01", "file1.txt", routes1)
        device2 = Device("Switch-02", "file2.txt", routes2)
        device3 = Device("Switch-03", "file3.txt", routes3)

        comparator = BgpRouteComparator()
        comparator.add_device(device1)
        comparator.add_device(device2)
        comparator.add_device(device3)
        comparator.set_baseline(0)  # Switch-01为基准

        result = comparator.compare_all()

        # 应该比较Switch-01与Switch-02、Switch-01与Switch-03
        assert len(result.compared_devices) == 2
        assert {d.name for d in result.compared_devices} == {"Switch-02", "Switch-03"}

        # 差异应该包括：
        # - Switch-02有额外Destination 10.0.1.0/24
        # - Switch-03的10.0.0.0/24 Pre不同
        assert len(result.differences) >= 2

    def test_compare_all_insufficient_devices(self, comparator, sample_devices):
        """测试设备数量不足的情况"""
        device1, _ = sample_devices

        comparator.add_device(device1)

        with pytest.raises(ValueError, match="至少需要两个设备"):
            comparator.compare_all()

    def test_find_missing_destinations(self, comparator, sample_devices):
        """测试查找缺少的Destination"""
        device1, device2 = sample_devices

        # 手动调用内部方法进行测试
        routes1_tuples = device1.route_tuples
        routes2_tuples = device2.route_tuples

        differences = comparator._find_missing_destinations(
            device1, device2, routes1_tuples, routes2_tuples
        )

        # 应该有2个缺少Destination的差异
        assert len(differences) == 2

        # 检查差异详情
        for diff in differences:
            assert diff.difference_type == DifferenceType.MISSING_DESTINATION
            assert diff.destination in ["10.0.1.0/24", "10.0.3.0/24"]
            if diff.destination == "10.0.1.0/24":
                assert diff.details["missing_in"] == "Switch-02"
            elif diff.destination == "10.0.3.0/24":
                assert diff.details["missing_in"] == "Switch-01"

    def test_find_common_destination_differences(self, comparator):
        """测试查找相同Destination的差异"""
        # 创建有相同Destination但不同Interface和Pre/Cost的设备
        routes1 = [
            BgpRoute("10.0.0.0/24", "192.168.1.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.1.2", "GE0/0/2", 60, 10, RouteProtocol.BGP),
            BgpRoute("10.0.1.0/24", "192.168.1.3", "GE0/0/3", 60, 0, RouteProtocol.BGP),
        ]

        routes2 = [
            BgpRoute("10.0.0.0/24", "192.168.2.1", "GE0/0/1", 70, 0, RouteProtocol.BGP),  # Pre不同
            # 缺少 GE0/0/2 接口
            BgpRoute("10.0.1.0/24", "192.168.2.3", "GE0/0/3", 60, 0, RouteProtocol.BGP),  # 相同
            BgpRoute("10.0.1.0/24", "192.168.2.4", "GE0/0/4", 60, 0, RouteProtocol.BGP),  # 额外接口
        ]

        device1 = Device("Switch-01", "file1.txt", routes1)
        device2 = Device("Switch-02", "file2.txt", routes2)

        differences = comparator._find_common_destination_differences(device1, device2)

        # 应该有3个差异：
        # 1. 10.0.0.0/24 缺少 GE0/0/2 接口（在Switch-02中缺失）
        # 2. 10.0.0.0/24 GE0/0/1 接口Pre不同
        # 3. 10.0.1.0/24 缺少 GE0/0/4 接口（在Switch-01中缺失）
        assert len(differences) == 3

        # 检查差异类型
        missing_if_diffs = [d for d in differences if d.difference_type == DifferenceType.MISSING_INTERFACE]
        pre_cost_diffs = [d for d in differences if d.difference_type == DifferenceType.PRE_COST_DIFFERENCE]

        assert len(missing_if_diffs) == 2
        assert len(pre_cost_diffs) == 1

        # 检查Pre/Cost差异
        pre_cost_diff = pre_cost_diffs[0]
        assert pre_cost_diff.destination == "10.0.0.0/24"
        assert pre_cost_diff.details["interface"] == "GE0/0/1"
        assert pre_cost_diff.details["device1_pre"] == 60
        assert pre_cost_diff.details["device2_pre"] == 70

    def test_performance_stats(self, comparator, sample_devices):
        """测试性能统计"""
        device1, device2 = sample_devices

        comparator.add_device(device1)
        comparator.add_device(device2)
        comparator.set_baseline(0)

        result = comparator.compare_all()
        stats = comparator.get_performance_stats()

        # 检查性能统计包含时间信息
        assert "total_comparison_time" in stats
        assert stats["total_comparison_time"] > 0
        assert "routes_per_second" in stats

    def test_clear(self, comparator, sample_devices):
        """测试清空比较器"""
        device1, device2 = sample_devices

        comparator.add_device(device1)
        comparator.add_device(device2)
        comparator.set_baseline(0)

        assert len(comparator.devices) == 2
        assert comparator.baseline_index == 0

        comparator.clear()

        assert len(comparator.devices) == 0
        assert comparator.baseline_index == 0
        assert len(comparator.get_performance_stats()) == 0

    def test_ecmp_equal_cost_multipath(self, comparator):
        """测试等价路由（ECMP）处理

        场景：同一Destination有多个等价路径，比较不同设备的等价路由数量差异
        """
        # 设备A有3条等价路由到10.0.0.0/24（3个不同的接口，Pre/Cost相同）
        routes_a = [
            BgpRoute("10.0.0.0/24", "192.168.1.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.1.2", "GE0/0/2", 60, 0, RouteProtocol.BGP),  # 等价路径2
            BgpRoute("10.0.0.0/24", "192.168.1.3", "GE0/0/3", 60, 0, RouteProtocol.BGP),  # 等价路径3
            BgpRoute("10.0.1.0/24", "192.168.1.4", "GE0/0/4", 60, 0, RouteProtocol.IBGP),
        ]

        # 设备B只有2条等价路由到10.0.0.0/24（少1条）
        routes_b = [
            BgpRoute("10.0.0.0/24", "192.168.2.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.2.2", "GE0/0/2", 60, 0, RouteProtocol.BGP),  # 只有2条
            BgpRoute("10.0.1.0/24", "192.168.2.4", "GE0/0/4", 60, 0, RouteProtocol.IBGP),
        ]

        # 设备C有2条等价路由，但Pre不同（非等价路由）
        routes_c = [
            BgpRoute("10.0.0.0/24", "192.168.3.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.3.2", "GE0/0/2", 70, 0, RouteProtocol.BGP),  # Pre不同，不是等价
        ]

        # 设备D有2条等价路由，但Cost不同（非等价路由）
        routes_d = [
            BgpRoute("10.0.0.0/24", "192.168.4.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.4.2", "GE0/0/2", 60, 50, RouteProtocol.BGP),  # Cost不同，不是等价
        ]

        device_a = Device("Device-A", "a.txt", routes_a)
        device_b = Device("Device-B", "b.txt", routes_b)
        device_c = Device("Device-C", "c.txt", routes_c)
        device_d = Device("Device-D", "d.txt", routes_d)

        # 测试1：A vs B - 等价路由数量差异
        diffs_ab = comparator.compare_two_devices(device_a, device_b)

        # 应该检测到 Device-B 缺少 GE0/0/3 接口（等价路由不完整）
        missing_if_diffs = [d for d in diffs_ab
                           if d.difference_type == DifferenceType.MISSING_INTERFACE
                           and d.destination == "10.0.0.0/24"]
        assert len(missing_if_diffs) == 1
        assert missing_if_diffs[0].details["interface"] == "GE0/0/3"
        assert missing_if_diffs[0].details["missing_in"] == "Device-B"

        # 测试2：A vs C - Pre不同（非等价），检测Pre差异
        diffs_ac = comparator.compare_two_devices(device_a, device_c)
        pre_cost_diffs_ac = [d for d in diffs_ac
                            if d.difference_type == DifferenceType.PRE_COST_DIFFERENCE]
        assert len(pre_cost_diffs_ac) >= 1  # GE0/0/2的Pre不同

        # 测试3：A vs D - Cost不同（非等价），检测Cost差异
        diffs_ad = comparator.compare_two_devices(device_a, device_d)
        pre_cost_diffs_ad = [d for d in diffs_ad
                            if d.difference_type == DifferenceType.PRE_COST_DIFFERENCE]
        assert len(pre_cost_diffs_ad) >= 1  # GE0/0/2的Cost不同

        # 测试4：A vs A - 完全相同的等价路由配置，应该无差异
        device_a2 = Device("Device-A2", "a_copy.txt", routes_a[:])
        diffs_aa = comparator.compare_two_devices(device_a, device_a2)
        assert len(diffs_aa) == 0  # 完全相同的等价路由配置应无差异

    def test_interface_mismatch_same_count(self, comparator):
        """测试接口数量相同但名称不同 → INTERFACE_MISMATCH"""
        # 设备A: 10.0.0.0/24 有2条ECMP (Eth-Trunk1, Eth-Trunk2)
        routes_a = [
            BgpRoute("10.0.0.0/24", "192.168.1.1", "Eth-Trunk1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.1.2", "Eth-Trunk2", 60, 0, RouteProtocol.BGP),
        ]

        # 设备B: 10.0.0.0/24 也有2条ECMP，但接口不同 (Eth-Trunk3, Eth-Trunk4)
        routes_b = [
            BgpRoute("10.0.0.0/24", "192.168.2.1", "Eth-Trunk3", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.2.2", "Eth-Trunk4", 60, 0, RouteProtocol.BGP),
        ]

        device_a = Device("Device-A", "a.txt", routes_a)
        device_b = Device("Device-B", "b.txt", routes_b)

        differences = comparator.compare_two_devices(device_a, device_b)

        # 应该产生1条 INTERFACE_MISMATCH
        mismatch_diffs = [d for d in differences
                          if d.difference_type == DifferenceType.INTERFACE_MISMATCH]
        assert len(mismatch_diffs) == 1

        mismatch = mismatch_diffs[0]
        assert mismatch.destination == "10.0.0.0/24"
        assert set(mismatch.details["device1_interfaces"]) == {"Eth-Trunk1", "Eth-Trunk2"}
        assert set(mismatch.details["device2_interfaces"]) == {"Eth-Trunk3", "Eth-Trunk4"}

        # 不应该产生 MISSING_INTERFACE（数量相同）
        missing_if = [d for d in differences
                      if d.difference_type == DifferenceType.MISSING_INTERFACE]
        assert len(missing_if) == 0

    def test_ecmp_multiple_device_comparison(self):
        """测试多设备场景下的等价路由比较"""
        routes1 = [
            BgpRoute("10.0.0.0/24", "192.168.1.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.1.2", "GE0/0/2", 60, 0, RouteProtocol.BGP),
        ]
        routes2 = [
            BgpRoute("10.0.0.0/24", "192.168.2.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
        ]
        routes3 = [
            BgpRoute("10.0.0.0/24", "192.168.3.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.3.2", "GE0/0/2", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.0.0/24", "192.168.3.3", "GE0/0/3", 60, 0, RouteProtocol.BGP),
        ]

        device1 = Device("Device-1", "f1.txt", routes1)
        device2 = Device("Device-2", "f2.txt", routes2)
        device3 = Device("Device-3", "f3.txt", routes3)

        cmp = BgpRouteComparator(max_workers=1)
        cmp.add_device(device1)
        cmp.add_device(device2)
        cmp.add_device(device3)
        cmp.set_baseline(0)

        result = cmp.compare_all()
        assert result.summary["compared_devices"] == ["Device-2", "Device-3"]

        # Device-2缺少GE0/0/2, Device-3多出GE0/0/3
        missing_ifs = [d for d in result.differences
                      if d.difference_type == DifferenceType.MISSING_INTERFACE]
        assert len(missing_ifs) == 2

        # Device-2 vs Device-1: 缺少GE0/0/2
        # Device-3 vs Device-1: 多出GE0/0/3（在Device-1中缺失）
        interfaces_involved = {d.details["interface"] for d in missing_ifs}
        assert "GE0/0/2" in interfaces_involved
        assert "GE0/0/3" in interfaces_involved


class TestOptimizedBgpComparator:
    """测试优化版BGP比较器"""

    @pytest.fixture
    def sample_devices(self):
        """创建示例设备"""
        routes1 = [
            BgpRoute("10.0.0.0/24", "192.168.1.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.1.0/24", "192.168.1.2", "GE0/0/2", 60, 0, RouteProtocol.BGP),
        ]
        routes2 = [
            BgpRoute("10.0.0.0/24", "192.168.2.1", "GE0/0/1", 60, 0, RouteProtocol.BGP),
            BgpRoute("10.0.2.0/24", "192.168.2.2", "GE0/0/2", 60, 0, RouteProtocol.BGP),
        ]

        device1 = Device("Switch-01", "file1.txt", routes1)
        device2 = Device("Switch-02", "file2.txt", routes2)

        return device1, device2

    def test_optimized_comparator_creation(self):
        """测试创建优化比较器"""
        comparator = OptimizedBgpComparator(
            max_workers=2,
            chunk_size=5000,
            use_bloom_filter=False,  # 测试时不使用布隆过滤器（避免额外依赖）
            cache_results=True
        )

        assert comparator.max_workers == 2
        assert comparator.chunk_size == 5000
        assert comparator.use_bloom_filter is False
        assert comparator.cache_results is True

    def test_optimized_comparator_cache(self, sample_devices):
        """测试优化比较器的缓存功能"""
        device1, device2 = sample_devices

        comparator = OptimizedBgpComparator(cache_results=True)
        comparator.add_device(device1)
        comparator.add_device(device2)
        comparator.set_baseline(0)

        # 第一次比较
        result1 = comparator.compare_all()
        diffs1 = result1.differences

        # 第二次比较应该使用缓存
        result2 = comparator.compare_all()
        diffs2 = result2.differences

        # 结果应该相同
        assert len(diffs1) == len(diffs2)

    def test_clear_cache(self, sample_devices):
        """测试清空缓存"""
        comparator = OptimizedBgpComparator(cache_results=True)
        device1, device2 = sample_devices

        comparator.add_device(device1)
        comparator.add_device(device2)

        # 比较以填充缓存
        comparator.compare_two_devices(device1, device2)

        # 清空缓存
        comparator.clear_cache()

        # 缓存应该为空（通过内部属性检查）
        assert len(comparator._comparison_cache) == 0


class TestComparatorWithFixtureFiles:
    """测试使用fixture文件的比较器"""

    def test_compare_two_fixture_files(self):
        """测试比较两个fixture文件"""
        fixture_dir = Path(__file__).parent / "fixtures"
        file1 = fixture_dir / "sample_huawei_bgp.txt"
        file2 = fixture_dir / "sample_huawei_bgp2.txt"

        # 使用快捷函数比较
        differences = compare_two_bgp_files(str(file1), str(file2))

        # 检查差异
        assert len(differences) > 0

        # 应该包含以下差异：
        # - 缺少Destination: 10.0.3.0/24 在file2中缺失
        # - Pre/Cost差异: 10.0.2.0/24 Cost不同 (100 vs 150)
        # - Pre/Cost差异: 10.0.4.0/24 Pre不同 (60 vs 70)
        # - 接口不同: 10.0.4.0/24 Interface不同 (GE0/0/5 vs GE0/0/6)

        diff_types = [diff.difference_type for diff in differences]
        assert DifferenceType.MISSING_DESTINATION in diff_types
        assert DifferenceType.PRE_COST_DIFFERENCE in diff_types
        assert DifferenceType.INTERFACE_MISMATCH in diff_types

    def test_compare_multiple_fixture_files(self):
        """测试比较多个fixture文件"""
        fixture_dir = Path(__file__).parent / "fixtures"
        files = [
            fixture_dir / "sample_huawei_bgp.txt",
            fixture_dir / "sample_huawei_bgp2.txt",
            fixture_dir / "sample_huawei_bgp3.txt",
        ]

        filepaths = [str(f) for f in files]

        # 使用快捷函数比较
        result = compare_bgp_files(filepaths, baseline_index=0)

        assert result.baseline_device.name == "HUAWEI-SWITCH-01"
        assert len(result.compared_devices) == 2
        assert len(result.differences) > 0

        # 检查汇总信息
        assert result.summary["total_differences"] == len(result.differences)
        assert len(result.summary["differences_by_type"]) > 0

    def test_compare_bgp_files_function(self):
        """测试compare_bgp_files快捷函数"""
        fixture_dir = Path(__file__).parent / "fixtures"
        files = [
            fixture_dir / "sample_huawei_bgp.txt",
            fixture_dir / "sample_huawei_bgp2.txt",
        ]

        filepaths = [str(f) for f in files]

        result = compare_bgp_files(filepaths, baseline_index=0, max_workers=1)
        assert result is not None
        assert len(result.differences) > 0

    def test_ecmp_compare_with_fixture_files(self):
        """端到端测试：使用包含ECMP路由的fixture文件进行比较

        ECMP文件1 (HUAWEI-SWITCH-ECMP): 6条路由
          - 10.0.0.0/24: 3条ECMP (GE0/0/1~3)
          - 10.0.1.0/24: 1条 (GE0/0/4)
          - 10.0.2.0/24: 2条ECMP (GE0/0/5~6)

        ECMP文件2 (HUAWEI-SWITCH-ECMP2): 4条路由
          - 10.0.0.0/24: 2条ECMP (GE0/0/1~2) → 比ECMP1少GE0/0/3
          - 10.0.1.0/24: 1条 (GE0/0/4)
          - 10.0.2.0/24: 1条 (GE0/0/5) → 比ECMP1少GE0/0/6
        """
        fixture_dir = Path(__file__).parent / "fixtures"
        file1 = str(fixture_dir / "sample_huawei_bgp_ecmp.txt")
        file2 = str(fixture_dir / "sample_huawei_bgp_ecmp2.txt")

        # 解析两个文件
        from routesanalysis.parsing import parse_bgp_file
        device1 = parse_bgp_file(file1)
        device2 = parse_bgp_file(file2)

        # 验证解析正确
        assert device1.name == "HUAWEI-SWITCH-ECMP"
        assert len(device1.routes) == 6
        assert device2.name == "HUAWEI-SWITCH-ECMP2"
        assert len(device2.routes) == 4

        # 比较两个设备
        comparator = BgpRouteComparator(max_workers=1)
        diffs = comparator.compare_two_devices(device1, device2)

        # 验证差异

        # 1. 缺少Interface：ECMP2缺少GE0/0/3（10.0.0.0/24 ECMP路径不完整）
        missing_if_in_2 = [d for d in diffs
                          if d.difference_type == DifferenceType.MISSING_INTERFACE
                          and d.details.get("missing_in") == "HUAWEI-SWITCH-ECMP2"]
        assert len(missing_if_in_2) == 2
        missing_interfaces = {d.details["interface"] for d in missing_if_in_2}
        assert "GigabitEthernet0/0/3" in missing_interfaces  # ECMP路径缺失
        assert "Eth-Trunk2" in missing_interfaces  # ECMP路径缺失

        # 2. 缺少Destination：无（所有destination都出现在两个文件中）
        missing_dest = [d for d in diffs
                       if d.difference_type == DifferenceType.MISSING_DESTINATION]
        assert len(missing_dest) == 0

    def test_ecmp_three_way_compare_with_fixtures(self):
        """使用fixture文件进行三方ECMP比较

        场景：
          - 基准: HUAWEI-SWITCH-ECMP (10.0.0.0/24有3条ECMP)
          - 设备2: HUAWEI-SWITCH-ECMP2 (10.0.0.0/24有2条ECMP)
          - 设备3: sample_huawei_bgp3.txt (10.0.0.0/24有1条非ECMP)
        """
        fixture_dir = Path(__file__).parent / "fixtures"
        files = [
            str(fixture_dir / "sample_huawei_bgp_ecmp.txt"),
            str(fixture_dir / "sample_huawei_bgp_ecmp2.txt"),
            str(fixture_dir / "sample_huawei_bgp3.txt"),
        ]

        from routesanalysis.parsing import parse_multiple_bgp_files
        devices = parse_multiple_bgp_files(files)
        assert len(devices) == 3

        comparator = BgpRouteComparator(max_workers=1)
        for device in devices:
            comparator.add_device(device)
        comparator.set_baseline(0)  # ECMP文件1为基准

        result = comparator.compare_all()

        # 验证汇总信息
        assert result.summary["baseline_device"] == "HUAWEI-SWITCH-ECMP"
        assert result.summary["compared_devices"] == ["HUAWEI-SWITCH-ECMP2", "HUAWEI-SWITCH-03"]

        # 验证差异
        diffs = result.differences
        assert len(diffs) > 0

        # ECMP2 vs ECMP1: 缺少GE0/0/3和GE0/0/6（2个缺少Interface）
        # sample3 vs ECMP1: 缺少10.0.0.0/24的GE0/0/2和GE0/0/3（2个缺少Interface）
        #                   还缺少10.0.2.0/24（1个缺少Destination）
        #                   多出10.0.5.0/24和10.0.6.0/24（2个缺少Destination反向）
        missing_if = [d for d in diffs if d.difference_type == DifferenceType.MISSING_INTERFACE]
        missing_dest = [d for d in diffs if d.difference_type == DifferenceType.MISSING_DESTINATION]

        assert len(missing_if) >= 3  # 至少3个interface差异
        assert len(missing_dest) >= 1  # 至少1个destination差异

    def test_peer_device_comparison_same_peers(self):
        """对端设备相同，本地接口不同 → 无差异

        ecmp5 (PEER1): Eth-Trunk1→BJ-DSW-01, Eth-Trunk2→BJ-DSW-02
        ecmp6 (PEER2): Eth-Trunk3→BJ-DSW-01, Eth-Trunk4→BJ-DSW-02
        虽然本地接口名不同，但对端设备相同 → 应视为等价
        """
        fixture_dir = Path(__file__).parent / "fixtures"
        file5 = fixture_dir / "sample_huawei_bgp_ecmp5.txt"
        file6 = fixture_dir / "sample_huawei_bgp_ecmp6.txt"

        differences = compare_two_bgp_files(str(file5), str(file6))

        # 对端设备相同，本地接口不同但映射到相同对端 → 不应有 INTERFACE_MISMATCH
        mismatch_diffs = [d for d in differences
                          if d.difference_type == DifferenceType.INTERFACE_MISMATCH]
        assert len(mismatch_diffs) == 0

        # 也不应有 MISSING_INTERFACE（对端设备数量相同）
        missing_if = [d for d in differences
                      if d.difference_type == DifferenceType.MISSING_INTERFACE]
        assert len(missing_if) == 0

        # 所有路由应完全匹配
        assert len(differences) == 0

    def test_peer_device_comparison_diff_peers(self):
        """对端设备不同 → 应检测到 MISSING_INTERFACE（按对端设备比较）

        ecmp5 (PEER1): Eth-Trunk1→BJ-DSW-01, Eth-Trunk2→BJ-DSW-02
        ecmp7 (PEER3): Eth-Trunk1→SH-DSW-03, Eth-Trunk2→SH-DSW-04
        对端设备不同 → 对端设备缺失（MISSING_INTERFACE），不产生 INTERFACE_MISMATCH
        """
        fixture_dir = Path(__file__).parent / "fixtures"
        file5 = fixture_dir / "sample_huawei_bgp_ecmp5.txt"
        file7 = fixture_dir / "sample_huawei_bgp_ecmp7.txt"

        differences = compare_two_bgp_files(str(file5), str(file7))

        # 按对端设备比较，对端不同 → 逐条报告对端设备缺失
        missing_peer_diffs = [d for d in differences
                              if d.difference_type == DifferenceType.MISSING_INTERFACE
                              and d.details.get("compared_by") == "peer_device"]
        assert len(missing_peer_diffs) >= 1

        # 不应产生 INTERFACE_MISMATCH（按对端比较时归入 MISSING_INTERFACE）
        mismatch_diffs = [d for d in differences
                          if d.difference_type == DifferenceType.INTERFACE_MISMATCH]
        assert len(mismatch_diffs) == 0

    def test_peer_device_vs_interface_fallback(self):
        """一台有接口描述，另一台没有 → 回退到接口名比较

        ecmp5 有接口描述，ecmp1 没有 → 应使用接口名比较
        """
        fixture_dir = Path(__file__).parent / "fixtures"
        file5 = fixture_dir / "sample_huawei_bgp_ecmp5.txt"
        file_ecmp = fixture_dir / "sample_huawei_bgp_ecmp.txt"
        # ecmp 的路由接口和 ecmp5 不同 → 按接口名比较应产生差异

        from routesanalysis.parsing import parse_bgp_file
        from routesanalysis.comparator import BgpRouteComparator

        device1 = parse_bgp_file(str(file5))
        device2 = parse_bgp_file(str(file_ecmp))

        comparator = BgpRouteComparator()
        differences = comparator.compare_two_devices(device1, device2)

        # 有差异是正常的（接口名不同且只有一方有接口描述）
        assert len(differences) > 0

        # 差异应按接口名比较（不是对端设备）
        for d in differences:
            if d.details.get("compared_by"):
                assert d.details["compared_by"] == "interface"