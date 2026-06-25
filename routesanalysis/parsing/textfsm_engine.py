"""TextFSM 解析引擎 - 优先使用 TextFSM 模板解析"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import textfsm

from ..models import BgpRoute, RouteProtocol
from .core import extract_device_name


class TextfsmParser:
    """TextFSM 解析器，加载模板执行匹配"""

    def __init__(self):
        self._template_dir = self._resolve_template_dir()

    def _resolve_template_dir(self) -> Path:
        """找模板目录，兼容开发/打包场景"""
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            return Path(sys._MEIPASS) / "routesanalysis" / "templates"
        return Path(__file__).parent.parent / "templates"

    def parse_bgp_routes(self, content: str) -> Optional[List[BgpRoute]]:
        """用 TextFSM 模板解析 BGP 路由表，失败返回 None"""
        try:
            template_path = self._template_dir / "huawei_bgp_routing_table.textfsm"
            with open(template_path, encoding='utf8') as f:
                fsm = textfsm.TextFSM(f)
            parsed = fsm.ParseText(content)

            routes = []
            for entry in parsed:
                dest = entry[0] if entry[0] else ""
                proto = entry[1]
                if proto == "IBGP":
                    route_proto = RouteProtocol.IBGP
                elif proto == "EBGP":
                    route_proto = RouteProtocol.EBGP
                else:
                    route_proto = RouteProtocol.BGP
                try:
                    pre = int(entry[2]) if entry[2] else 0
                    cost = int(entry[3]) if entry[3] else 0
                except (ValueError, IndexError):
                    pre = 0
                    cost = 0
                next_hop = entry[4] if len(entry) > 4 and entry[4] else ""
                interface = entry[5] if len(entry) > 5 and entry[5] else ""
                routes.append(BgpRoute(
                    destination=dest, next_hop=next_hop,
                    interface=interface, pre=pre, cost=cost,
                    protocol=route_proto,
                ))
            return routes if routes else None
        except Exception:
            return None

    def parse_interface_descriptions(self, content: str) -> Dict[str, str]:
        """用 TextFSM 模板解析接口描述，失败返回空字典"""
        result: Dict[str, str] = {}
        try:
            template_path = self._template_dir / "huawei_interface_description.textfsm"
            with open(template_path, encoding='utf8') as f:
                fsm = textfsm.TextFSM(f)
            parsed = fsm.ParseText(content)
            for entry in parsed:
                intf = entry[0]
                desc = entry[3].strip() if len(entry) > 3 else ""
                peer = self._extract_peer_device(desc)
                if peer:
                    result[intf] = peer
        except Exception:
            pass
        return result

    @staticmethod
    def _extract_peer_device(description: str) -> Optional[str]:
        """从接口描述中提取对端设备名
        描述格式：to_<对端设备名>_<接口名> 或 <对端设备名>_<接口名>
        """
        if not description:
            return None
        desc = description.strip()
        if desc.lower().startswith("to_"):
            # 去掉 to_，取最后一个 _ 之前的部分（去掉接口名）
            peer = desc[3:]
            last_underscore = peer.rfind('_')
            if last_underscore > 0:
                peer = peer[:last_underscore]
        elif "_" in desc:
            # 没有 to_ 前缀，取最后一个 _ 之前的部分
            last_underscore = desc.rfind('_')
            if last_underscore > 0:
                peer = desc[:last_underscore]
            else:
                peer = desc
        else:
            peer = desc
        return peer.strip() if peer else None
