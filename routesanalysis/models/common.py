"""公共数据模型 - 两个场景共用"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Set
from collections import namedtuple


class RouteProtocol(Enum):
    """路由协议类型"""
    BGP = "BGP"
    IBGP = "IBGP"
    EBGP = "EBGP"
    OSPF = "OSPF"
    DIRECT = "Direct"
    STATIC = "Static"
    ISIS = "IS-IS"
    RIP = "RIP"
    UNR = "UNR"
    UNKNOWN = "UNKNOWN"


# 紧凑的路由元组，用于内存优化
RouteTuple = namedtuple('RouteTuple', ['destination', 'interface', 'pre', 'cost', 'protocol'])


@dataclass(frozen=True, eq=True)
class Route:
    """路由条目，使用frozen dataclass确保可哈希"""
    destination: str
    next_hop: str
    interface: str
    pre: int
    cost: int
    protocol: RouteProtocol

    def __post_init__(self):
        if self.pre < 0 or self.pre > 255:
            raise ValueError(f"Pre值必须在0-255范围内: {self.pre}")
        if self.cost < 0:
            raise ValueError(f"Cost值必须非负: {self.cost}")

    def to_tuple(self) -> RouteTuple:
        return RouteTuple(
            destination=self.destination,
            interface=self.interface,
            pre=self.pre,
            cost=self.cost,
            protocol=self.protocol.value,
        )

    def get_comparison_key(self) -> Tuple:
        return (self.destination, self.interface, self.pre, self.cost, self.protocol)

    @classmethod
    def from_tuple(cls, route_tuple: RouteTuple, next_hop: str = "") -> 'Route':
        protocol_map = {p.value: p for p in RouteProtocol}
        protocol = protocol_map.get(route_tuple.protocol, RouteProtocol.BGP)
        return cls(
            destination=route_tuple.destination,
            next_hop=next_hop,
            interface=route_tuple.interface,
            pre=route_tuple.pre,
            cost=route_tuple.cost,
            protocol=protocol,
        )


@dataclass
class Device:
    """网络设备"""
    name: str
    filename: str
    routes: List[Route]
    interfaces: List[Interface] = field(default_factory=list)
    route_tuples: Set[RouteTuple] = field(default_factory=set, init=False)

    def __post_init__(self):
        self.route_tuples = {route.to_tuple() for route in self.routes}
        self.routes.sort(key=lambda r: (r.destination, r.interface))

    def get_peer_device(self, interface_name: str) -> str:
        """获取接口对应的对端设备名，若未知则返回接口名本身"""
        for intf in self.interfaces:
            if intf.name == interface_name and intf.peer_device:
                return intf.peer_device
        return interface_name

    def has_interface_descriptions(self) -> bool:
        return any(intf.peer_device for intf in self.interfaces)

    def get_routes_by_destination(self) -> Dict[str, List[Route]]:
        grouped = {}
        for route in self.routes:
            grouped.setdefault(route.destination, []).append(route)
        return grouped

    def get_routes_by_interface(self) -> Dict[str, List[Route]]:
        grouped = {}
        for route in self.routes:
            grouped.setdefault(route.interface, []).append(route)
        return grouped

    def has_route(self, route_tuple: RouteTuple) -> bool:
        return route_tuple in self.route_tuples

    def find_route(self, destination: str, interface: str) -> Optional[Route]:
        for route in self.routes:
            if route.destination == destination and route.interface == interface:
                return route
        return None


@dataclass
class Interface:
    """设备接口信息"""
    name: str                     # 接口号，如 "GigabitEthernet0/0/1"
    description: str = ""         # 原始接口描述文本
    status: str = ""              # 物理状态 up/down
    protocol_status: str = ""     # 协议状态 up/down
    peer_device: str = ""         # 对端设备名
    peer_interface: str = ""      # 对端接口名
    peer_source: str = "none"     # 数据来源："description" / "lldp" / "none"


def create_route_index(routes: List[Route]) -> Dict[str, Dict[str, Route]]:
    """创建两层索引：destination -> interface -> route"""
    index = {}
    for route in routes:
        index.setdefault(route.destination, {})[route.interface] = route
    return index
