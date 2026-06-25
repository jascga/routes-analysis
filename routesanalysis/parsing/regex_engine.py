"""正则 fallback 解析器 - TextFSM 解析失败时回退到内置正则"""
from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple

from ..models import BgpRoute, RouteProtocol, Device
from .core import extract_device_name, read_file_lines, normalize_path, FileScanResult

logger = logging.getLogger(__name__)


class RegexParser:
    """正则解析器，TextFSM 解析失败时回退到此引擎"""

    # 路由条目正则（华为标准格式）
    ROUTE_LINE_PATTERN = re.compile(
        r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\s+'
        r'(\w+)\s+'
        r'(\d+)\s+'
        r'(\d+)\s+'
        r'(?:\S+\s+){1,2}'
        r'(\S+)'
    )

    # 接口描述相关正则
    INTERFACE_DESC_COMMAND = re.compile(r'display\s+interface\s+description', re.IGNORECASE)
    INTERFACE_DESC_HEADER = re.compile(r'^Interface\s+PHY\s+Protocol\s+Description')
    INTERFACE_DESC_LINE = re.compile(
        r'^(\S+)\s+(up|down)\s+(up|down)\s+(.+)$'
    )

    def __init__(self, encoding: str = 'auto'):
        self.current_file: str = ""
        self._in_routing_table = False
        self._encoding = encoding

    def parse_file(self, filepath: str, encoding: str = 'auto') -> Device:
        """解析单个文件"""
        self.current_file = normalize_path(filepath)
        lines = read_file_lines(filepath, encoding)
        device_name = self._find_device_name(lines) or Path(filepath).stem
        return self.parse_lines(lines, device_name)

    def parse_lines(self, lines: List[str], device_name: str = "unknown") -> Device:
        """解析文本行"""
        routes: List[BgpRoute] = []
        interface_peer_map: Dict[str, str] = {}
        self._in_routing_table = False
        last_destination = None
        _in_interface_desc = False
        _looking_for_desc = False

        for line_num, line in enumerate(lines, 1):
            line = line.rstrip('\n\r')

            if extract_device_name(line):
                continue

            # 接口描述解析
            if self.INTERFACE_DESC_COMMAND.search(line):
                _in_interface_desc = True
                _looking_for_desc = True
                continue
            if _looking_for_desc and self.INTERFACE_DESC_HEADER.match(line.strip()):
                continue
            if _looking_for_desc and not line.strip():
                _in_interface_desc = False
                continue
            if _in_interface_desc and _looking_for_desc:
                match = self.INTERFACE_DESC_LINE.match(line)
                if match:
                    intf = match.group(1).strip()
                    desc = match.group(4).strip()
                    peer = self._extract_peer_device(desc)
                    if peer:
                        interface_peer_map[intf] = peer

            # 路由表解析
            if self._is_routing_table_start(line):
                self._in_routing_table = True
                _in_interface_desc = False
                _looking_for_desc = False
                continue
            if self._is_routing_table_end(line):
                self._in_routing_table = False
                continue
            if not self._in_routing_table:
                continue

            route = self._parse_route_line(line, line_num)
            if route:
                routes.append(route)
                last_destination = route.destination
                continue

            ecmp = self._parse_ecmp_continuation(line, last_destination, line_num)
            if ecmp:
                routes.append(ecmp)

        return Device(
            name=device_name,
            filename=self.current_file or "unknown",
            routes=routes,
            interface_peer_map=interface_peer_map,
        )

    def parse_files_streaming(self, filepaths: List[str], encoding: str = 'auto') -> Iterator[Device]:
        """流式解析多个文件"""
        for fp in filepaths:
            yield self.parse_file(fp, encoding)

    # ---- 内部方法 ----

    @staticmethod
    def _find_device_name(lines: List[str]) -> Optional[str]:
        for line in lines:
            name = extract_device_name(line)
            if name:
                return name
        return None

    @staticmethod
    def _is_routing_table_start(line: str) -> bool:
        return 'Destination/Mask' in line and 'Proto' in line

    @staticmethod
    def _is_routing_table_end(line: str) -> bool:
        return line.strip().startswith('---') and len(line.strip()) > 10

    def _parse_route_line(self, line: str, line_num: int) -> Optional[BgpRoute]:
        match = self.ROUTE_LINE_PATTERN.match(line)
        if not match:
            return None
        destination = match.group(1)
        protocol_str = match.group(2)
        pre = int(match.group(3))
        cost = int(match.group(4))
        interface = match.group(5)
        return BgpRoute(
            destination=destination,
            next_hop="",
            interface=interface,
            pre=pre,
            cost=cost,
            protocol=self._determine_protocol(protocol_str),
        )

    def _parse_ecmp_continuation(self, line: str, last_destination: Optional[str], line_num: int) -> Optional[BgpRoute]:
        if not last_destination or not line.strip():
            return None
        if not line[0].isspace():
            return None
        parts = line.strip().split()
        if len(parts) < 4:
            return None
        protocol_str = parts[0]
        try:
            pre = int(parts[1])
            cost = int(parts[2])
        except (ValueError, IndexError):
            return None
        interface = parts[-1]
        return BgpRoute(
            destination=last_destination,
            next_hop="",
            interface=interface,
            pre=pre,
            cost=cost,
            protocol=self._determine_protocol(protocol_str),
        )

    @staticmethod
    def _determine_protocol(protocol_str: str) -> RouteProtocol:
        upper = protocol_str.upper()
        if upper == "IBGP":
            return RouteProtocol.IBGP
        elif upper == "EBGP":
            return RouteProtocol.EBGP
        return RouteProtocol.BGP

    @staticmethod
    def _extract_peer_device(description: str) -> Optional[str]:
        if not description:
            return None
        desc = description.strip()
        if desc.lower().startswith("to_"):
            peer = desc[3:].split("_")[0] if "_" in desc else desc[3:]
        elif "_" in desc:
            peer = desc.split("_")[0]
        else:
            peer = desc
        return peer.strip() if peer else None

    @staticmethod
    def validate_file_format(filepath: str) -> Tuple[bool, str]:
        """验证文件格式是否有效"""
        try:
            with open(filepath, 'rb') as f:
                raw = f.read(4096)
            content = raw.decode('utf-8', errors='replace')
            if '<' in content and '>' in content:
                return True, ""
            return False, "未找到设备名（< > 格式）"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def scan_file(filepath: str) -> FileScanResult:
        """扫描单个文件"""
        path = Path(filepath)
        try:
            with open(filepath, 'rb') as f:
                raw = f.read(4096)
            content = raw.decode('utf-8', errors='replace')
            lines = content.splitlines()
            device_name = None
            for line in lines:
                name = extract_device_name(line)
                if name:
                    device_name = name
                    break
            route_count = sum(1 for line in lines if RegexParser.ROUTE_LINE_PATTERN.match(line))
            is_valid = device_name is not None
            return FileScanResult(
                filepath=str(path.resolve()),
                filename=path.name,
                is_valid=is_valid,
                device_name=device_name or path.stem,
                route_count=route_count,
                file_size=path.stat().st_size,
                error_message="" if is_valid else "未找到设备名",
            )
        except Exception as e:
            return FileScanResult(
                filepath=str(path.resolve()),
                filename=path.name,
                is_valid=False,
                device_name=path.stem,
                route_count=0,
                file_size=path.stat().st_size,
                error_message=str(e),
            )
