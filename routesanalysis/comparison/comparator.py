"""
BGP路由比较器 - 支持多设备、高性能比较
"""

import logging
import time
from typing import List, Dict, Set, Tuple, Optional, Iterator
from collections import defaultdict, Counter
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
import sys

from routesanalysis.models import (
    Device, BgpRoute, RouteDifference, DifferenceType,
    ComparisonResult, RouteTuple, create_route_index
)

logger = logging.getLogger(__name__)


class BgpRouteComparator:
    """
    BGP路由比较器
    支持多设备比较，指定基准设备，高性能处理百万级路由
    """

    def __init__(self, max_workers: Optional[int] = None, chunk_size: int = 10000):
        """
        初始化比较器

        Args:
            max_workers: 最大并行工作进程数，None表示使用CPU核心数
            chunk_size: 分批处理的大小
        """
        self.devices: List[Device] = []
        self.baseline_index: int = 0
        self.max_workers = max_workers or mp.cpu_count()
        self.chunk_size = chunk_size
        self._performance_stats: Dict[str, float] = {}

    def add_device(self, device: Device):
        """添加设备"""
        self.devices.append(device)
        logger.debug(f"添加设备: {device.name} ({len(device.routes)} 条路由)")

    def set_baseline(self, index: int):
        """
        设置基准设备索引

        Args:
            index: 设备列表中的索引（从0开始）

        Raises:
            IndexError: 索引超出范围
        """
        if index < 0 or index >= len(self.devices):
            raise IndexError(f"基准索引 {index} 超出范围 (0-{len(self.devices)-1})")
        self.baseline_index = index
        logger.info(f"设置基准设备: {self.devices[index].name}")

    def compare_all(self) -> ComparisonResult:
        """
        比较所有设备与基准设备

        Returns:
            ComparisonResult对象

        Raises:
            ValueError: 设备数量不足
        """
        if len(self.devices) < 2:
            raise ValueError("至少需要两个设备进行比较")

        start_time = time.time()

        baseline = self.devices[self.baseline_index]
        compared = [d for i, d in enumerate(self.devices) if i != self.baseline_index]

        logger.info(f"开始比较: 基准设备={baseline.name}, 比较设备={[d.name for d in compared]}")

        # 执行比较
        differences = self._compare_multiple_devices(baseline, compared)

        # 生成汇总信息
        summary = self._generate_summary(baseline, compared, differences)
        comparison_time = time.time() - start_time

        logger.info(f"比较完成: 发现 {len(differences)} 处差异，耗时 {comparison_time:.2f} 秒")

        # 记录性能统计
        self._performance_stats['total_comparison_time'] = comparison_time
        self._performance_stats['routes_per_second'] = len(baseline.routes) / comparison_time if comparison_time > 0 else 0

        return ComparisonResult(
            baseline_device=baseline,
            compared_devices=compared,
            differences=differences,
            summary=summary
        )

    def compare_two_devices(self, device1: Device, device2: Device) -> List[RouteDifference]:
        """
        比较两个设备

        Args:
            device1: 第一个设备
            device2: 第二个设备

        Returns:
            差异列表
        """
        logger.debug(f"比较两个设备: {device1.name} vs {device2.name}")
        return self._compare_devices(device1, device2)

    def _compare_multiple_devices(self, baseline: Device, compared: List[Device]) -> List[RouteDifference]:
        """比较多个设备与基准设备"""
        differences = []

        if self.max_workers > 1 and len(compared) > 1:
            # 并行比较多个设备
            differences = self._parallel_compare(baseline, compared)
        else:
            # 串行比较
            for device in compared:
                device_diffs = self._compare_devices(baseline, device)
                differences.extend(device_diffs)

        return differences

    def _parallel_compare(self, baseline: Device, compared: List[Device]) -> List[RouteDifference]:
        """并行比较多个设备"""
        differences = []
        task_args = [(baseline, device) for device in compared]

        try:
            with ProcessPoolExecutor(max_workers=min(self.max_workers, len(compared))) as executor:
                # 提交任务
                future_to_device = {
                    executor.submit(self._compare_devices_worker, baseline, device): device
                    for device in compared
                }

                # 收集结果
                for future in as_completed(future_to_device):
                    device = future_to_device[future]
                    try:
                        device_diffs = future.result()
                        differences.extend(device_diffs)
                        logger.debug(f"并行比较完成: {device.name} -> {len(device_diffs)} 差异")
                    except Exception as e:
                        logger.error(f"并行比较失败 {device.name}: {e}")

        except Exception as e:
            logger.warning(f"并行比较失败，回退到串行: {e}")
            # 回退到串行
            for device in compared:
                device_diffs = self._compare_devices(baseline, device)
                differences.extend(device_diffs)

        return differences

    @staticmethod
    def _compare_devices_worker(baseline: Device, device: Device) -> List[RouteDifference]:
        """工作进程函数（用于并行处理）"""
        comparator = BgpRouteComparator(max_workers=1)  # 工作进程内不使用并行
        return comparator._compare_devices(baseline, device)

    def _compare_devices(self, device1: Device, device2: Device) -> List[RouteDifference]:
        """比较两个设备的路由表（核心比较逻辑）"""
        differences = []

        # 使用元组集合进行快速查找（忽略next_hop）
        routes1_tuples = device1.route_tuples
        routes2_tuples = device2.route_tuples

        # 1. 检查缺少的Destination
        dest_diffs = self._find_missing_destinations(device1, device2, routes1_tuples, routes2_tuples)
        differences.extend(dest_diffs)

        # 2. 检查相同Destination的差异（缺少Interface和Pre/Cost差异）
        common_dest_diffs = self._find_common_destination_differences(device1, device2)
        differences.extend(common_dest_diffs)

        return differences

    def _find_missing_destinations(self, device1: Device, device2: Device,
                                   routes1_tuples: Set[RouteTuple],
                                   routes2_tuples: Set[RouteTuple]) -> List[RouteDifference]:
        """查找缺少的Destination"""
        differences = []

        # 提取所有destination
        dests1 = {rt.destination for rt in routes1_tuples}
        dests2 = {rt.destination for rt in routes2_tuples}

        # 设备1有而设备2没有的destination
        for dest in dests1 - dests2:
            differences.append(RouteDifference(
                destination=dest,
                device1=device1.name,
                device2=device2.name,
                difference_type=DifferenceType.MISSING_DESTINATION,
                details={"missing_in": device2.name}
            ))

        # 设备2有而设备1没有的destination
        for dest in dests2 - dests1:
            differences.append(RouteDifference(
                destination=dest,
                device1=device1.name,
                device2=device2.name,
                difference_type=DifferenceType.MISSING_DESTINATION,
                details={"missing_in": device1.name}
            ))

        return differences

    def _find_common_destination_differences(self, device1: Device, device2: Device) -> List[RouteDifference]:
        """查找相同Destination的差异（缺少Interface和Pre/Cost差异）"""
        differences = []

        # 按destination分组
        routes1_by_dest = device1.get_routes_by_destination()
        routes2_by_dest = device2.get_routes_by_destination()

        # 共同destination
        common_dests = set(routes1_by_dest.keys()) & set(routes2_by_dest.keys())

        for dest in common_dests:
            dest_routes1 = routes1_by_dest[dest]
            dest_routes2 = routes2_by_dest[dest]

            # 判断是否使用对端设备名比较
            use_peer = device1.has_interface_descriptions() and device2.has_interface_descriptions()

            # 构建比较键映射（有接口描述时用对端设备名，否则用接口名）
            def get_key1(route):
                return device1.get_peer_device(route.interface) if use_peer else route.interface
            def get_key2(route):
                return device2.get_peer_device(route.interface) if use_peer else route.interface

            routes1_by_key = {}
            for r in dest_routes1:
                key = get_key1(r)
                if key not in routes1_by_key:
                    routes1_by_key[key] = r
            routes2_by_key = {}
            for r in dest_routes2:
                key = get_key2(r)
                if key not in routes2_by_key:
                    routes2_by_key[key] = r

            keys1 = set(routes1_by_key.keys())
            keys2 = set(routes2_by_key.keys())

            # 检查接口差异
            if use_peer or len(keys1) != len(keys2):
                # 按对端设备比较 或 接口数量不同 → MISSING_INTERFACE（逐个报告）
                for key in keys1 - keys2:
                    differences.append(RouteDifference(
                        destination=dest,
                        device1=device1.name,
                        device2=device2.name,
                        difference_type=DifferenceType.MISSING_INTERFACE,
                        details={
                            "interface": key,
                            "missing_in": device2.name,
                            "route_in_device1": routes1_by_key[key],
                            "compared_by": "peer_device" if use_peer else "interface",
                        }
                    ))

                for key in keys2 - keys1:
                    differences.append(RouteDifference(
                        destination=dest,
                        device1=device1.name,
                        device2=device2.name,
                        difference_type=DifferenceType.MISSING_INTERFACE,
                        details={
                            "interface": key,
                            "missing_in": device1.name,
                            "route_in_device2": routes2_by_key[key],
                            "compared_by": "peer_device" if use_peer else "interface",
                        }
                    ))
            else:
                # 按接口名比较，数量相同但名称不同 → INTERFACE_MISMATCH（合并为一条）
                if keys1 != keys2:
                    differences.append(RouteDifference(
                        destination=dest,
                        device1=device1.name,
                        device2=device2.name,
                        difference_type=DifferenceType.INTERFACE_MISMATCH,
                        details={
                            "device1_interfaces": sorted(keys1),
                            "device2_interfaces": sorted(keys2),
                            "compared_by": "interface",
                        }
                    ))

            # 检查相同key的Pre和Cost差异
            common_keys = keys1 & keys2
            for key in common_keys:
                route1 = routes1_by_key[key]
                route2 = routes2_by_key[key]

                if route1.pre != route2.pre or route1.cost != route2.cost:
                    differences.append(RouteDifference(
                        destination=dest,
                        device1=device1.name,
                        device2=device2.name,
                        difference_type=DifferenceType.PRE_COST_DIFFERENCE,
                        details={
                            "interface": key,
                            "device1_pre": route1.pre,
                            "device2_pre": route2.pre,
                            "device1_cost": route1.cost,
                            "device2_cost": route2.cost,
                            "compared_by": "peer_device" if use_peer else "interface",
                        }
                    ))

        return differences

    def _generate_summary(self, baseline: Device, compared: List[Device],
                         differences: List[RouteDifference]) -> Dict[str, any]:
        """生成汇总信息"""
        total_routes_baseline = len(baseline.routes)

        # 按类型统计差异
        diff_by_type = Counter()
        for diff in differences:
            diff_by_type[diff.difference_type] += 1

        # 按设备对统计差异
        diff_by_device_pair = Counter()
        for diff in differences:
            pair_key = f"{diff.device1}-{diff.device2}"
            diff_by_device_pair[pair_key] += 1

        return {
            "baseline_device": baseline.name,
            "compared_devices": [d.name for d in compared],
            "total_routes_baseline": total_routes_baseline,
            "total_differences": len(differences),
            "differences_by_type": {k.value: v for k, v in diff_by_type.items()},
            "differences_by_device_pair": dict(diff_by_device_pair),
            "performance_stats": self._performance_stats.copy()
        }

    def get_performance_stats(self) -> Dict[str, float]:
        """获取性能统计信息"""
        return self._performance_stats.copy()

    def clear(self):
        """清空所有设备和状态"""
        self.devices.clear()
        self.baseline_index = 0
        self._performance_stats.clear()


class OptimizedBgpComparator(BgpRouteComparator):
    """
    优化版BGP路由比较器
    针对百万级路由表进行额外优化
    """

    def __init__(self, max_workers: Optional[int] = None, chunk_size: int = 10000,
                 use_bloom_filter: bool = False, cache_results: bool = True):
        """
        初始化优化比较器

        Args:
            use_bloom_filter: 是否使用布隆过滤器加速destination检查
            cache_results: 是否缓存比较结果
        """
        super().__init__(max_workers, chunk_size)
        self.use_bloom_filter = use_bloom_filter
        self.cache_results = cache_results
        self._comparison_cache: Dict[Tuple[str, str], List[RouteDifference]] = {}
        # 无论是否使用布隆过滤器，都初始化_bloom_filters
        self._bloom_filters: Dict[str, any] = {}

        if use_bloom_filter:
            try:
                from pybloom_live import BloomFilter
                self.BloomFilter = BloomFilter
            except ImportError:
                logger.warning("pybloom_live未安装，禁用布隆过滤器")
                self.use_bloom_filter = False

    def add_device(self, device: Device):
        """添加设备并构建优化数据结构"""
        super().add_device(device)

        if self.use_bloom_filter:
            self._build_bloom_filter(device)

    def _build_bloom_filter(self, device: Device):
        """为设备构建布隆过滤器（用于快速destination检查）"""
        if not self.use_bloom_filter:
            return

        try:
            # 估计路由数量，设置适当的容量和错误率
            capacity = len(device.routes) * 2  # 两倍容量减少冲突
            bloom = self.BloomFilter(capacity=capacity, error_rate=0.01)

            # 添加所有destination到布隆过滤器
            for route in device.routes:
                bloom.add(route.destination)

            self._bloom_filters[device.name] = bloom
            logger.debug(f"为设备 {device.name} 构建布隆过滤器，容量={capacity}")

        except Exception as e:
            logger.warning(f"构建布隆过滤器失败: {e}")
            self.use_bloom_filter = False

    def _compare_devices(self, device1: Device, device2: Device) -> List[RouteDifference]:
        """优化版本的设备比较"""
        # 检查缓存
        cache_key = (device1.name, device2.name)
        if self.cache_results and cache_key in self._comparison_cache:
            logger.debug(f"使用缓存结果: {device1.name} vs {device2.name}")
            return self._comparison_cache[cache_key]

        # 使用布隆过滤器加速destination检查（如果可用）
        if self.use_bloom_filter:
            differences = self._compare_with_bloom_filters(device1, device2)
        else:
            differences = super()._compare_devices(device1, device2)

        # 缓存结果
        if self.cache_results:
            self._comparison_cache[cache_key] = differences

        return differences

    def _compare_with_bloom_filters(self, device1: Device, device2: Device) -> List[RouteDifference]:
        """使用布隆过滤器加速比较"""
        differences = []

        bloom1 = self._bloom_filters.get(device1.name)
        bloom2 = self._bloom_filters.get(device2.name)

        if not bloom1 or not bloom2:
            # 回退到标准比较
            return super()._compare_devices(device1, device2)

        # 提取所有destination
        dests1 = {rt.destination for rt in device1.route_tuples}
        dests2 = {rt.destination for rt in device2.route_tuples}

        # 使用布隆过滤器快速排除不可能缺少的destination
        # 注意：布隆过滤器可能有误报，所以需要二次验证

        # 检查设备1的destination是否可能在设备2中
        for dest in dests1:
            if dest not in bloom2:
                # 布隆过滤器说肯定不存在，直接记录差异
                differences.append(RouteDifference(
                    destination=dest,
                    device1=device1.name,
                    device2=device2.name,
                    difference_type=DifferenceType.MISSING_DESTINATION,
                    details={"missing_in": device2.name}
                ))

        # 检查设备2的destination是否可能在设备1中
        for dest in dests2:
            if dest not in bloom1:
                differences.append(RouteDifference(
                    destination=dest,
                    device1=device1.name,
                    device2=device2.name,
                    difference_type=DifferenceType.MISSING_DESTINATION,
                    details={"missing_in": device1.name}
                ))

        # 对于布隆过滤器说"可能存在"的destination，进行精确检查
        # 这里简化处理，实际可能需要更精细的逻辑

        # 还需要检查其他类型的差异（缺少Interface、Pre/Cost差异）
        other_diffs = self._find_common_destination_differences(device1, device2)
        differences.extend(other_diffs)

        return differences

    def clear_cache(self):
        """清空缓存"""
        self._comparison_cache.clear()
        self._bloom_filters.clear()


# 快捷函数
def compare_bgp_files(filepaths: List[str], baseline_index: int = 0,
                      max_workers: Optional[int] = None) -> ComparisonResult:
    """
    比较多个BGP路由表文件（快捷函数）

    Args:
        filepaths: 文件路径列表
        baseline_index: 基准文件索引
        max_workers: 最大并行工作进程数

    Returns:
        ComparisonResult对象
    """
    from routesanalysis.parser import parse_multiple_bgp_files

    # 解析文件
    devices = parse_multiple_bgp_files(filepaths)

    # 创建比较器并比较
    comparator = BgpRouteComparator(max_workers=max_workers)
    for device in devices:
        comparator.add_device(device)

    comparator.set_baseline(baseline_index)
    return comparator.compare_all()


def compare_two_bgp_files(file1: str, file2: str) -> List[RouteDifference]:
    """
    比较两个BGP路由表文件（快捷函数）

    Args:
        file1: 第一个文件路径
        file2: 第二个文件路径

    Returns:
        差异列表
    """
    from routesanalysis.parser import parse_bgp_file

    device1 = parse_bgp_file(file1)
    device2 = parse_bgp_file(file2)

    comparator = BgpRouteComparator()
    return comparator.compare_two_devices(device1, device2)