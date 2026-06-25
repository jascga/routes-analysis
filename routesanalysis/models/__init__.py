"""数据模型"""
from .common import Device, BgpRoute, RouteProtocol, RouteTuple, create_route_index
from .compare import ComparisonResult, RouteDifference, DifferenceType

__all__ = [
    "Device",
    "BgpRoute",
    "RouteProtocol",
    "RouteTuple",
    "create_route_index",
    "ComparisonResult",
    "RouteDifference",
    "DifferenceType",
]
