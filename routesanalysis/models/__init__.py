"""数据模型"""
from .common import Device, Route, RouteProtocol, RouteTuple, Interface, create_route_index
from .compare import ComparisonResult, RouteDifference, DifferenceType

__all__ = [
    "Device",
    "Route",
    "RouteProtocol",
    "RouteTuple",
    "Interface",
    "create_route_index",
    "ComparisonResult",
    "RouteDifference",
    "DifferenceType",
]
