"""场景 2：多设备 BGP 路由表比较（从 routescompare 迁移而来）"""

from .comparator import BgpRouteComparator, compare_bgp_files
from ..export.comparison import export_comparison_result

__all__ = [
    "BgpRouteComparator",
    "compare_bgp_files",
    "export_comparison_result",
]
