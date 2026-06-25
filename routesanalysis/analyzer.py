"""
路由表分析器

场景 1：多组平行设备负载分担分析
- 平行设备分组规则：设备名 `aaaa-b...-dddd-管理IP`，取最后一个 `-` 之前的部分作为分组键
- 命中条件：同一 Destination 的所有路径，对端设备归属的"分组键"数量 >= min_groups
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Any
from collections import defaultdict

from routesanalysis.models import Device, BgpRoute
from routesanalysis.config import load_config, get_parallel_group_config, get_separator, get_ignore_segments, get_segment_rules


# ---------------------------------------------------------------------------
# 设备名分组
# ---------------------------------------------------------------------------

def parallel_group_key(peer_name: str) -> str:
    """
    从对端设备名提取平行设备分组键。
    规则由 config.yaml 驱动，默认行为维持不变。
    """
    if not peer_name:
        return peer_name

    cfg = get_parallel_group_config()
    sep = get_separator(cfg)
    ignore_segments = get_ignore_segments(cfg)
    segment_rules = get_segment_rules(cfg)

    idx = peer_name.rfind(sep)
    if idx <= 0:
        return peer_name
    head = peer_name[:idx]

    segments = head.split(sep)

    # 应用段处理规则
    for rule in segment_rules:
        seg_idx = rule.get("segment_index", -1)
        prefix = rule.get("prefix", "")
        suffix = rule.get("strip_suffix", "")
        if 0 <= seg_idx < len(segments):
            seg = segments[seg_idx]
            if seg.lower().startswith(prefix.lower()) and suffix:
                new_seg = re.sub(rf'({re.escape(suffix)}).*$', r'\1', seg, flags=re.IGNORECASE)
                if new_seg != seg:
                    segments[seg_idx] = new_seg

    # 去掉需要忽略的段（只在段数 ≥ 4 时生效，对齐旧行为）
    if ignore_segments and len(segments) >= 4:
        segments = [s for i, s in enumerate(segments) if i not in ignore_segments]

    return sep.join(segments)


def is_well_formed_peer_name(peer_name: str) -> bool:
    """设备名是否符合 `aaaa-...-管理IP` 格式（至少有一个 '-'）"""
    return bool(peer_name) and '-' in peer_name and not peer_name.startswith('-')


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class PeerPath:
    """一条路径的对端信息"""
    interface: str          # 原始接口名
    peer_device: str        # 对端设备名（若无映射则等于 interface）
    group_key: str          # 平行设备分组键
    well_formed: bool       # 对端设备名是否规范
    pre: int
    cost: int
    protocol: str


@dataclass
class MultiGroupHit:
    """命中：路由负载分担到多组平行设备"""
    destination: str
    paths: List[PeerPath]
    group_keys: List[str]   # 涉及的分组键（去重、有序）

    @property
    def path_count(self) -> int:
        return len(self.paths)

    @property
    def group_count(self) -> int:
        return len(self.group_keys)

    @property
    def protocols(self) -> List[str]:
        return sorted({p.protocol for p in self.paths})

    @property
    def has_unparseable_peer(self) -> bool:
        return any(not p.well_formed for p in self.paths)


@dataclass
class MultiGroupAnalysisResult:
    """场景 1 分析结果"""
    device: Device
    min_groups: int
    total_destinations: int
    total_routes: int
    hits: List[MultiGroupHit]
    # 所有 Destination → 路径列表（含未命中的）
    all_destinations: Dict[str, List[PeerPath]] = field(default_factory=dict)
    # 分组键 -> 该组下出现过的对端设备名集合（用于核对分组）
    group_members: Dict[str, Set[str]] = field(default_factory=dict)
    # 警告：对端设备名不规范的对端集合
    unparseable_peers: Set[str] = field(default_factory=set)

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    def summary(self) -> Dict[str, Any]:
        return {
            "device_name": self.device.name,
            "filename": self.device.filename,
            "min_groups": self.min_groups,
            "total_routes": self.total_routes,
            "total_destinations": self.total_destinations,
            "hit_count": self.hit_count,
            "group_count": len(self.group_members),
            "unparseable_peer_count": len(self.unparseable_peers),
            "has_interface_descriptions": self.device.has_interface_descriptions(),
        }


# ---------------------------------------------------------------------------
# 分析器
# ---------------------------------------------------------------------------

class MultiGroupAnalyzer:
    """场景 1：多组平行设备负载分担分析"""

    def __init__(self, min_groups: int = 2):
        if min_groups < 2:
            raise ValueError(f"min_groups 必须 >= 2，当前: {min_groups}")
        self.min_groups = min_groups

    def analyze(self, device: Device) -> MultiGroupAnalysisResult:
        """对单台设备执行分析"""
        # 按 destination 聚合所有路径
        dest_to_paths: Dict[str, List[PeerPath]] = defaultdict(list)
        group_members: Dict[str, Set[str]] = defaultdict(set)
        unparseable_peers: Set[str] = set()

        for route in device.routes:
            peer = device.get_peer_device(route.interface)
            group_key = parallel_group_key(peer)
            well_formed = is_well_formed_peer_name(peer)

            if not well_formed:
                unparseable_peers.add(peer)

            path = PeerPath(
                interface=route.interface,
                peer_device=peer,
                group_key=group_key,
                well_formed=well_formed,
                pre=route.pre,
                cost=route.cost,
                protocol=route.protocol.value,
            )
            dest_to_paths[route.destination].append(path)
            group_members[group_key].add(peer)

        # 找出命中：分组键数量 >= min_groups
        hits: List[MultiGroupHit] = []
        for dest, paths in dest_to_paths.items():
            unique_groups = []
            seen = set()
            for p in paths:
                if p.group_key not in seen:
                    seen.add(p.group_key)
                    unique_groups.append(p.group_key)
            if len(unique_groups) >= self.min_groups:
                hits.append(MultiGroupHit(
                    destination=dest,
                    paths=sorted(paths, key=lambda x: (x.group_key, x.peer_device, x.interface)),
                    group_keys=sorted(unique_groups),
                ))

        # 按 destination 排序，便于阅读
        hits.sort(key=lambda h: _ip_sort_key(h.destination))

        # 构建 all_destinations（按 destination 排序）
        all_destinations = dict(sorted(dest_to_paths.items(), key=lambda kv: _ip_sort_key(kv[0])))

        return MultiGroupAnalysisResult(
            device=device,
            min_groups=self.min_groups,
            total_destinations=len(dest_to_paths),
            total_routes=len(device.routes),
            hits=hits,
            all_destinations=all_destinations,
            group_members=dict(group_members),
            unparseable_peers=unparseable_peers,
        )


def _ip_sort_key(dest: str) -> Tuple[int, int, int, int, int]:
    """把 `a.b.c.d/m` 转成排序键"""
    try:
        ip, mask = dest.split('/')
        a, b, c, d = (int(x) for x in ip.split('.'))
        return (a, b, c, d, int(mask))
    except (ValueError, AttributeError):
        return (999, 999, 999, 999, 999)
