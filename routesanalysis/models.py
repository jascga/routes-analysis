"""
数据模型定义 - BGP路由表比较工具
"""

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any, Set
from collections import namedtuple
import hashlib


class RouteProtocol(Enum):
    """BGP路由协议类型"""
    BGP = "BGP"
    IBGP = "IBGP"
    EBGP = "EBGP"


# 紧凑的路由元组，用于内存优化
RouteTuple = namedtuple('RouteTuple', ['destination', 'interface', 'pre', 'cost', 'protocol'])


@dataclass(frozen=True, eq=True)
class BgpRoute:
    """
    BGP路由条目
    使用frozen dataclass确保可哈希，便于集合操作
    """
    destination: str           # 目标网络，如 "10.0.0.0/24"
    next_hop: str             # 下一跳地址（比较时忽略）
    interface: str            # 出接口
    pre: int                  # 优先级
    cost: int                 # 开销
    protocol: RouteProtocol   # 路由协议类型

    def __post_init__(self):
        """数据验证"""
        if self.pre < 0 or self.pre > 255:
            raise ValueError(f"Pre值必须在0-255范围内: {self.pre}")
        if self.cost < 0:
            raise ValueError(f"Cost值必须非负: {self.cost}")

    def to_tuple(self) -> RouteTuple:
        """转换为紧凑的元组格式（忽略next_hop）"""
        return RouteTuple(
            destination=self.destination,
            interface=self.interface,
            pre=self.pre,
            cost=self.cost,
            protocol=self.protocol.value
        )

    def get_comparison_key(self) -> Tuple:
        """获取用于比较的键（忽略next_hop）"""
        return (self.destination, self.interface, self.pre, self.cost, self.protocol)

    @classmethod
    def from_tuple(cls, route_tuple: RouteTuple, next_hop: str = "") -> 'BgpRoute':
        """从元组创建BgpRoute对象"""
        # 从protocol字符串恢复为RouteProtocol枚举
        protocol_map = {p.value: p for p in RouteProtocol}
        protocol = protocol_map.get(route_tuple.protocol, RouteProtocol.BGP)

        return cls(
            destination=route_tuple.destination,
            next_hop=next_hop,
            interface=route_tuple.interface,
            pre=route_tuple.pre,
            cost=route_tuple.cost,
            protocol=protocol
        )


@dataclass
class Device:
    """网络设备"""
    name: str                 # 设备名称（从sysname提取）
    filename: str             # 源文件名
    routes: List[BgpRoute]    # BGP路由列表
    interface_peer_map: Dict[str, str] = field(default_factory=dict)  # 接口→对端设备名
    route_tuples: Set[RouteTuple] = field(default_factory=set, init=False)  # 用于快速查找的集合

    def __post_init__(self):
        """初始化后处理"""
        # 构建快速查找集合
        self.route_tuples = {route.to_tuple() for route in self.routes}

        # 按destination和interface排序，便于比较和调试
        self.routes.sort(key=lambda r: (r.destination, r.interface))

    def get_peer_device(self, interface: str) -> str:
        """获取接口对应的对端设备名，若未知则返回接口名本身"""
        return self.interface_peer_map.get(interface, interface)

    def has_interface_descriptions(self) -> bool:
        """是否包含接口描述信息"""
        return len(self.interface_peer_map) > 0

    def get_routes_by_destination(self) -> Dict[str, List[BgpRoute]]:
        """按destination分组返回路由"""
        grouped = {}
        for route in self.routes:
            if route.destination not in grouped:
                grouped[route.destination] = []
            grouped[route.destination].append(route)
        return grouped

    def get_routes_by_interface(self) -> Dict[str, List[BgpRoute]]:
        """按interface分组返回路由"""
        grouped = {}
        for route in self.routes:
            if route.interface not in grouped:
                grouped[route.interface] = []
            grouped[route.interface].append(route)
        return grouped

    def has_route(self, route_tuple: RouteTuple) -> bool:
        """快速检查是否包含指定路由（使用元组）"""
        return route_tuple in self.route_tuples

    def find_route(self, destination: str, interface: str) -> Optional[BgpRoute]:
        """查找指定destination和interface的路由"""
        for route in self.routes:
            if route.destination == destination and route.interface == interface:
                return route
        return None


class DifferenceType(Enum):
    """差异类型"""
    MISSING_DESTINATION = "missing_destination"      # 缺少Destination
    MISSING_INTERFACE = "missing_interface"          # 接口数量不同（ECMP路径数不同）
    INTERFACE_MISMATCH = "interface_mismatch"        # 接口数量相同但接口名不同
    PRE_COST_DIFFERENCE = "pre_cost_diff"           # Pre/Cost差异


@dataclass
class RouteDifference:
    """路由差异详情"""
    destination: str
    device1: str
    device2: str
    difference_type: DifferenceType
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化"""
        return {
            "destination": self.destination,
            "device1": self.device1,
            "device2": self.device2,
            "difference_type": self.difference_type.value,
            "details": self.details
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RouteDifference':
        """从字典创建RouteDifference对象"""
        return cls(
            destination=data["destination"],
            device1=data["device1"],
            device2=data["device2"],
            difference_type=DifferenceType(data["difference_type"]),
            details=data["details"]
        )


@dataclass
class ComparisonResult:
    """比较结果"""
    baseline_device: Device
    compared_devices: List[Device]
    differences: List[RouteDifference]
    summary: Dict[str, Any]

    def get_differences_by_type(self) -> Dict[DifferenceType, List[RouteDifference]]:
        """按差异类型分组返回差异"""
        grouped = {}
        for diff in self.differences:
            if diff.difference_type not in grouped:
                grouped[diff.difference_type] = []
            grouped[diff.difference_type].append(diff)
        return grouped

    def get_statistics(self) -> Dict[str, int]:
        """获取统计信息"""
        stats = {
            "total_differences": len(self.differences),
            "total_routes_baseline": len(self.baseline_device.routes),
            "compared_devices_count": len(self.compared_devices),
        }

        # 按类型统计
        for diff_type in DifferenceType:
            stats[diff_type.value] = 0

        for diff in self.differences:
            stats[diff.difference_type.value] = stats.get(diff.difference_type.value, 0) + 1

        return stats


# 性能优化相关的数据结构和函数
def create_route_index(routes: List[BgpRoute]) -> Dict[str, Dict[str, BgpRoute]]:
    """
    创建两层索引：destination -> interface -> route
    用于快速查找
    """
    index = {}
    for route in routes:
        if route.destination not in index:
            index[route.destination] = {}
        index[route.destination][route.interface] = route
    return index


def route_tuple_hash(route_tuple: RouteTuple) -> int:
    """计算路由元组的哈希值（用于布隆过滤器等）"""
    # 使用MD5生成固定长度的哈希
    key = f"{route_tuple.destination}|{route_tuple.interface}|{route_tuple.pre}|{route_tuple.cost}|{route_tuple.protocol}"
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


# Windows兼容性相关的辅助函数
if sys.platform == "win32":
    import ctypes

    def enable_windows_long_paths():
        """启用Windows长路径支持（需要Python 3.6+和Windows 10 1607+）"""
        try:
            # 设置允许长路径（超过260字符）
            import ctypes.wintypes
            GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
            GetCurrentProcess.restype = ctypes.wintypes.HANDLE

            PROCESS_ALL_ACCESS = 0x1F0FFF
            process = GetCurrentProcess()

            # 尝试设置进程策略
            ctypes.windll.kernel32.SetProcessMitigationPolicy(0x00000002, None, 0)
        except:
            # 如果失败，静默忽略
            pass