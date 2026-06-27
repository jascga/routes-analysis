"""TextFSM 解析引擎 - 优先使用 TextFSM 模板解析"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import textfsm

from ..models import Route, RouteProtocol, Interface
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

    def parse_bgp_routes(self, content: str) -> Optional[List[Route]]:
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
                routes.append(Route(
                    destination=dest, next_hop=next_hop,
                    interface=interface, pre=pre, cost=cost,
                    protocol=route_proto,
                ))
            return routes if routes else None
        except Exception:
            return None

    def parse_interface_descriptions(self, content: str) -> List[Interface]:
        """用 TextFSM 模板解析接口描述，失败返回空列表"""
        result: List[Interface] = []
        try:
            template_path = self._template_dir / "huawei_interface_description.textfsm"
            with open(template_path, encoding='utf8') as f:
                fsm = textfsm.TextFSM(f)
            parsed = fsm.ParseText(content)
            for entry in parsed:
                intf_name = entry[0]
                status = entry[1]
                protocol_status = entry[2]
                desc = entry[3].strip() if len(entry) > 3 else ""
                peer_device, peer_interface = self._extract_peer_from_desc(desc)
                result.append(Interface(
                    name=intf_name,
                    description=desc,
                    status=status,
                    protocol_status=protocol_status,
                    peer_device=peer_device,
                    peer_interface=peer_interface,
                    peer_source="description" if peer_device else "none",
                ))
        except Exception:
            pass
        return result

    @staticmethod
    def _extract_peer_from_desc(description: str) -> Tuple[str, str]:
        """从接口描述中提取对端设备名和对端接口名
        描述格式：to_<对端设备名>_<对端接口名>
        """
        if not description:
            return ("", "")
        desc = description.strip()
        if desc.lower().startswith("to_"):
            peer_part = desc[3:]
        else:
            peer_part = desc
        # 最后一个 _ 分隔：之前是对端设备名，之后是对端接口名
        last_underscore = peer_part.rfind('_')
        if last_underscore > 0:
            peer_device = peer_part[:last_underscore]
            peer_interface = peer_part[last_underscore + 1:]
        else:
            peer_device = peer_part
            peer_interface = ""
        return (peer_device.strip(), peer_interface.strip())
