"""比较场景专用数据模型"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Any

from .common import Device


class DifferenceType(Enum):
    """差异类型"""
    MISSING_DESTINATION = "missing_destination"
    MISSING_INTERFACE = "missing_interface"
    INTERFACE_MISMATCH = "interface_mismatch"
    PRE_COST_DIFFERENCE = "pre_cost_diff"


@dataclass
class RouteDifference:
    """路由差异详情"""
    destination: str
    device1: str
    device2: str
    difference_type: DifferenceType
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "destination": self.destination,
            "device1": self.device1,
            "device2": self.device2,
            "difference_type": self.difference_type.value,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RouteDifference':
        return cls(
            destination=data["destination"],
            device1=data["device1"],
            device2=data["device2"],
            difference_type=DifferenceType(data["difference_type"]),
            details=data["details"],
        )


@dataclass
class ComparisonResult:
    """比较结果"""
    baseline_device: Device
    compared_devices: List[Device]
    differences: List[RouteDifference]
    summary: Dict[str, Any]

    def get_differences_by_type(self) -> Dict[DifferenceType, List[RouteDifference]]:
        grouped = {}
        for diff in self.differences:
            grouped.setdefault(diff.difference_type, []).append(diff)
        return grouped

    def get_statistics(self) -> Dict[str, int]:
        stats = {
            "total_differences": len(self.differences),
            "total_routes_baseline": len(self.baseline_device.routes),
            "compared_devices_count": len(self.compared_devices),
        }
        for diff_type in DifferenceType:
            stats[diff_type.value] = 0
        for diff in self.differences:
            stats[diff.difference_type.value] = stats.get(diff.difference_type.value, 0) + 1
        return stats
