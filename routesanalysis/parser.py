"""
BGP路由表解析器 - 支持华为设备格式
双解析策略：优先用TextFSM + ntc-templates解析，解析失败回退到内置正则
"""

import re
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Iterator, TextIO, Tuple, Dict, Any
import logging
import chardet
import textfsm

from .models import BgpRoute, RouteProtocol, Device

logger = logging.getLogger(__name__)


@dataclass
class FileScanResult:
    """单文件的快速扫描结果（用于目录扫描模式）"""
    filepath: str                # 完整文件路径
    filename: str                # 仅文件名（用于显示）
    is_valid: bool               # 是否通过格式验证
    device_name: str             # 提取到的设备名（如果未找到则使用文件名）
    route_count: int             # 前50行中发现的粗略路由数
    file_size: int               # 文件大小（字节）
    error_message: str = ""      # 如果无效，则填写错误信息


class BgpRouteParser:
    """
    华为BGP路由表解析器

    双解析策略：优先用TextFSM + ntc-templates解析，解析失败回退到内置正则
    支持标准华为格式：Destination/Mask Protocol Pre Cost NextHop Interface
    示例：10.0.0.0/24 BGP 60 0 192.168.1.1 GigabitEthernet0/0/1
    """

    # 预编译正则表达式以提高性能
    # 匹配设备名称：从< >中提取
    DEVICE_NAME_PATTERN = re.compile(r'<([^>]+)>')

    def _parse_with_textfsm(self, lines: List[str]) -> Optional[Tuple[str, List[BgpRoute], Dict[str, str]]]:
        """用TextFSM + ntc-templates解析，失败返回None"""
        device_name = None
        routes: List[BgpRoute] = []
        interface_peer_map: Dict[str, str] = {}
        content = "\n".join(lines)

        # 1. 提取设备名
        for line in lines:
            match = self.DEVICE_NAME_PATTERN.search(line.strip())
            if match:
                device_name = match.group(1).strip()
                break

        if not device_name:
            return None

        # 2. 尝试解析BGP路由表
        try:
            # 加载自定义TextFSM模板
            template_path = Path(__file__).parent / "templates" / "huawei_bgp_routing_table.textfsm"
            if not template_path.exists() and getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                template_path = Path(sys._MEIPASS) / "routesanalysis" / "templates" / "huawei_bgp_routing_table.textfsm"
            
            with open(template_path, encoding='utf8') as f:
                fsm = textfsm.TextFSM(f)
            parsed = fsm.ParseText(content)

            # 模板输出格式：[DESTINATION, PROTOCOL, PREFERENCE, COST, NEXTHOP, INTERFACE]
            for entry in parsed:
                dest = entry[0] if entry[0] else ""
                proto = entry[1]
                # 协议映射
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
                    destination=dest,
                    next_hop=next_hop,
                    interface=interface,
                    pre=pre,
                    cost=cost,
                    protocol=route_proto
                ))

            # 3. 用TextFSM解析接口描述
            try:
                intf_template_path = Path(__file__).parent / "templates" / "huawei_interface_description.textfsm"
                if not intf_template_path.exists() and getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                    intf_template_path = Path(sys._MEIPASS) / "routesanalysis" / "templates" / "huawei_interface_description.textfsm"
                with open(intf_template_path, encoding='utf8') as f:
                    intf_fsm = textfsm.TextFSM(f)
                intf_parsed = intf_fsm.ParseText(content)
                for intf_entry in intf_parsed:
                    intf = intf_entry[0]
                    desc = intf_entry[3].strip() if len(intf_entry) > 3 else ""
                    peer = self._extract_peer_device(desc)
                    if peer:
                        interface_peer_map[intf] = peer
            except Exception:
                pass

            return (device_name, routes, interface_peer_map)

        except Exception:
            return None

    # 匹配路由条目行（华为标准格式）
    # 实际设备格式：Destination/Mask Proto Pre Cost Flags NextHop Interface
    # Flags和NextHop列忽略，只提取 Destination/Mask、Proto、Pre、Cost、Interface
    ROUTE_LINE_PATTERN = re.compile(
        r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\s+'  # Group 1: Destination/Mask
        r'(\w+)\s+'                                          # Group 2: Protocol (BGP/IBGP/EBGP)
        r'(\d+)\s+'                                          # Group 3: Pre
        r'(\d+)\s+'                                          # Group 4: Cost
        r'(?:\S+\s+){1,2}'                                   # Skip: 1-2 fields (NextHop or Flags+NextHop)
        r'(\S+)'                                            # Group 5: Interface
    )

    # 匹配可能的其他格式变体
    ALT_ROUTE_PATTERN = re.compile(
        r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})'
        r'\s+([A-Z]+)\s+(\d+)\s+(\d+)\s+'
        r'(?:\S+\s+){1,2}'
        r'(\S+)'
    )

    # 匹配ECMP续行（省略了Destination/Mask，以空白开头）
    # 实际设备格式：大量前导空白 + Proto Pre Cost Flags NextHop Interface
    ECMP_CONTINUATION_PATTERN = re.compile(
        r'^\s+'                                            # 前导空白（无destination）
        r'(BGP|IBGP|EBGP)\s+'                              # Group 1: Protocol
        r'(\d+)\s+'                                        # Group 2: Pre
        r'(\d+)\s+'                                        # Group 3: Cost
        r'(?:\S+\s+){1,2}'                                 # Skip: Flags + NextHop
        r'(\S+)'                                           # Group 4: Interface
    )

    # 匹配 display interface description 命令
    INTERFACE_DESC_COMMAND = re.compile(r'display\s+interface\s+description', re.IGNORECASE)
    # 接口描述表头
    INTERFACE_DESC_HEADER = re.compile(r'^Interface\s+PHY\s+Protocol\s+Description')
    # 接口描述数据行：Interface(列1) PHY(忽略) Protocol(忽略) Description(剩余)
    INTERFACE_DESC_LINE = re.compile(
        r'^(\S+)\s+\S+\s+\S+\s+(.+)$'
    )

    def __init__(self, encoding: str = 'auto'):
        """
        初始化解析器

        Args:
            encoding: 文件编码，'auto'表示自动检测
        """
        self.encoding = encoding
        self._current_device = None
        self._in_routing_table = False

    def parse_file(self, filepath: str) -> Device:
        """
        解析单个BGP路由表文件

        Args:
            filepath: 文件路径

        Returns:
            Device对象

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件格式错误或解析失败
        """
        filepath = self._normalize_path(filepath)
        logger.info(f"开始解析文件: {filepath}")

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")

        # 检测文件编码
        actual_encoding = self._detect_encoding(filepath) if self.encoding == 'auto' else self.encoding

        # 逐行解析
        routes = []
        device_name = None
        last_destination = None  # 用于ECMP续行
        # 接口描述解析状态
        interface_peer_map: Dict[str, str] = {}
        _in_interface_desc = False
        _looking_for_desc = False

        try:
            with open(filepath, 'r', encoding=actual_encoding, errors='replace') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.rstrip('\n\r')

                    # 尝试提取设备名称（只在文件开头部分尝试）
                    if device_name is None and line_num < 50:  # 只在前50行查找
                        found_name = self._extract_device_name(line)
                        if found_name:
                            device_name = found_name
                            logger.debug(f"发现设备名称: {device_name}")

                    # --- 接口描述段解析 ---
                    if not self._in_routing_table and not _in_interface_desc:
                        # 检测 display interface description 命令
                        if self.INTERFACE_DESC_COMMAND.search(line):
                            _looking_for_desc = True
                            logger.debug(f"第{line_num}行: 检测到 interface description 命令")
                            continue
                        # 检测接口描述表头
                        if _looking_for_desc and self.INTERFACE_DESC_HEADER.match(line.strip()):
                            _in_interface_desc = True
                            _looking_for_desc = False
                            logger.debug(f"第{line_num}行: 检测到接口描述表头")
                            continue

                    # 解析接口描述数据行
                    if _in_interface_desc:
                        desc_match = self.INTERFACE_DESC_LINE.match(line.rstrip())
                        if desc_match:
                            interface = desc_match.group(1)
                            description = desc_match.group(2).strip()
                            peer = self._extract_peer_device(description)
                            if peer:
                                interface_peer_map[interface] = peer
                                logger.debug(f"接口描述: {interface} -> {peer}")
                            continue
                        else:
                            # 接口描述段结束
                            _in_interface_desc = False
                            # 继续处理当前行（可能是路由表头等）
                    # --- 接口描述段解析结束 ---

                    # 检测路由表开始
                    if not self._in_routing_table and self._is_routing_table_start(line):
                        self._in_routing_table = True
                        logger.debug(f"第{line_num}行: 检测到路由表开始")
                        continue

                    # 如果不在路由表中，跳过
                    if not self._in_routing_table:
                        continue

                    # 解析路由行
                    route = self._parse_route_line(line, line_num)
                    if route:
                        routes.append(route)
                        last_destination = route.destination  # 更新last_destination

                        # 显示进度（每10000行）
                        if len(routes) % 10000 == 0:
                            logger.info(f"已解析 {len(routes)} 条路由...")
                    elif last_destination:
                        # 不是标准路由行，尝试ECMP续行（省略destination）
                        ecmp_route = self._parse_ecmp_continuation(line, last_destination, line_num)
                        if ecmp_route:
                            routes.append(ecmp_route)

                    # 检测接口描述段（可能出现在路由表之后）
                    if self.INTERFACE_DESC_COMMAND.search(line):
                        self._in_routing_table = False
                        _looking_for_desc = True
                        logger.debug(f"第{line_num}行: 路由表后检测到接口描述命令")
                        continue

                    # 检测路由表结束（可选）
                    if self._is_routing_table_end(line):
                        self._in_routing_table = False
                        # 不 break，继续处理后续行（可能还有接口描述段等）
                        continue

        except UnicodeDecodeError as e:
            raise ValueError(f"文件编码错误: {filepath} - {e}")

        # 如果没有找到设备名称，使用文件名
        if device_name is None:
            device_name = Path(filepath).stem
            logger.warning(f"未找到设备名称，使用文件名: {device_name}")

        if not routes:
            logger.warning(f"文件 {filepath} 中未找到路由条目")

        if interface_peer_map:
            logger.info(f"解析到 {len(interface_peer_map)} 条接口描述")
        logger.info(f"解析完成: 设备={device_name}, 路由数={len(routes)}")

        # 重置状态
        self._in_routing_table = False

        return Device(name=device_name, filename=filepath, routes=routes,
                      interface_peer_map=interface_peer_map)

    def parse_files_streaming(self, filepaths: List[str]) -> Iterator[Device]:
        """
        流式解析多个文件（逐个解析，节省内存）

        Args:
            filepaths: 文件路径列表

        Yields:
            Device对象
        """
        for filepath in filepaths:
            try:
                device = self.parse_file(filepath)
                yield device
            except Exception as e:
                logger.error(f"解析文件失败: {filepath} - {e}")
                raise

    def parse_lines(self, lines: List[str], device_name: str = "unknown") -> Device:
        """
        直接从文本行解析路由表（用于测试或内存中的文本）
        双解析策略：优先TextFSM + ntc-templates解析，失败回退到内置正则

        Args:
            lines: 文本行列表
            device_name: 设备名称

        Returns:
            Device对象
        """
        extract_device_name = device_name == "unknown"

        # 1. 优先尝试TextFSM解析，未指定自定义设备名才会尝试
        if extract_device_name:
            textfsm_result = self._parse_with_textfsm(lines)
            if textfsm_result is not None:
                parsed_device_name, routes, interface_peer_map = textfsm_result
                device = Device(
                    name=parsed_device_name,
                    filename=self.current_file or "unknown",
                    routes=routes,
                    interface_peer_map=interface_peer_map
                )
                logging.debug("TextFSM解析成功")
                return device

        # 2. TextFSM解析失败，回退到内置正则解析
        logging.debug("TextFSM解析失败，回退到内置正则解析")
        routes = []
        self._in_routing_table = False
        last_destination = None
        # 接口描述解析状态
        interface_peer_map: Dict[str, str] = {}
        _in_interface_desc = False
        _looking_for_desc = False

        for line_num, line in enumerate(lines, 1):
            line = line.rstrip('\n\r')

            # --- 接口描述段解析 ---
            if not self._in_routing_table and not _in_interface_desc:
                if self.INTERFACE_DESC_COMMAND.search(line):
                    _looking_for_desc = True
                    continue
                if _looking_for_desc and self.INTERFACE_DESC_HEADER.match(line.strip()):
                    _in_interface_desc = True
                    _looking_for_desc = False
                    continue

            if _in_interface_desc:
                desc_match = self.INTERFACE_DESC_LINE.match(line.rstrip())
                if desc_match:
                    interface = desc_match.group(1)
                    description = desc_match.group(2).strip()
                    peer = self._extract_peer_device(description)
                    if peer:
                        interface_peer_map[interface] = peer
                    continue
                else:
                    _in_interface_desc = False
            # --- 接口描述段解析结束 ---

            # 检测路由表开始
            if not self._in_routing_table and self._is_routing_table_start(line):
                self._in_routing_table = True
                continue

            # 如果不在路由表中，跳过
            if not self._in_routing_table:
                continue

            # 解析路由行
            route = self._parse_route_line(line, line_num)
            if route:
                routes.append(route)
                last_destination = route.destination  # 更新last_destination
            elif last_destination:
                # 不是标准路由行，尝试ECMP续行（省略destination）
                ecmp_route = self._parse_ecmp_continuation(line, last_destination, line_num)
                if ecmp_route:
                    routes.append(ecmp_route)

            # 检测接口描述段（可能出现在路由表之后）
            if self.INTERFACE_DESC_COMMAND.search(line):
                self._in_routing_table = False
                _looking_for_desc = True
                continue

            # 检测路由表结束（可选）
            if self._is_routing_table_end(line):
                self._in_routing_table = False
                # 不 break，继续处理后续行（可能还有接口描述段等）
                continue

        return Device(name=device_name, filename="memory", routes=routes,
                      interface_peer_map=interface_peer_map)

    def _normalize_path(self, filepath: str) -> str:
        """标准化文件路径（处理Windows长路径等）"""
        path = Path(filepath)

        # Windows长路径处理
        if sys.platform == "win32":
            # 如果路径超过260字符，添加长路径前缀
            if len(str(path)) > 260:
                long_path = "\\\\?\\" + str(path)
                if os.path.exists(long_path):
                    return long_path

        return str(path)

    def _detect_encoding(self, filepath: str) -> str:
        """检测文件编码"""
        try:
            # 读取文件前部分内容进行编码检测
            with open(filepath, 'rb') as f:
                raw_data = f.read(10000)  # 读取前10KB

            result = chardet.detect(raw_data)
            encoding = result['encoding'] or 'utf-8'

            # 常见编码映射
            encoding_map = {
                'GB2312': 'gbk',
                'GBK': 'gbk',
                'UTF-8-SIG': 'utf-8-sig',
            }

            encoding = encoding_map.get(encoding, encoding)
            logger.debug(f"检测到文件编码: {encoding} (置信度: {result['confidence']:.2f})")

            return encoding

        except Exception as e:
            logger.warning(f"编码检测失败，使用utf-8: {e}")
            return 'utf-8'

    def _extract_device_name(self, line: str) -> Optional[str]:
        """从行中提取设备名称（从< >中）"""
        match = self.DEVICE_NAME_PATTERN.search(line)
        if match:
            return match.group(1).strip()
        return None

    def _is_routing_table_start(self, line: str) -> bool:
        """检测列标题行作为路由表开始的标志（所有华为格式的公共特征）"""
        return 'Destination/Mask' in line

    def _is_routing_table_end(self, line: str) -> bool:
        """检测是否为路由表结束行"""
        stripped = line.strip()

        # 空行不是结束标志（路由表标题和数据之间有空行）
        if not stripped:
            return False

        # display interface description 命令不是路由表结束标志
        if self.INTERFACE_DESC_COMMAND.search(stripped):
            return False

        # 如果行以设备提示符结束（如 <HUAWEI> 或 HUAWEI> ）
        if stripped.endswith('>'):
            return True

        # 如果行包含其他路由协议的表头
        if re.search(r'Routing\s+Table.*(OSPF|STATIC|RIP|DIRECT)', stripped, re.IGNORECASE):
            return True

        # 如果行以命令提示符开头（如 [HUAWEI] ）
        if stripped.startswith('[') and stripped.endswith(']'):
            return True

        # 如果行包含"---"分隔线（可能表示另一个表的开始）
        if stripped.startswith('---'):
            return True

        return False

    def _parse_route_line(self, line: str, line_num: int) -> Optional[BgpRoute]:
        """解析单行路由条目"""
        line = line.strip()

        # 跳过空行和注释行
        if not line or line.startswith('#'):
            return None

        # 尝试匹配标准格式
        match = self.ROUTE_LINE_PATTERN.match(line)
        if not match:
            # 尝试匹配替代格式
            match = self.ALT_ROUTE_PATTERN.match(line)

        if not match:
            # 如果行看起来像路由但格式不匹配，记录警告
            if self._looks_like_route_line(line):
                logger.warning(f"第{line_num}行: 路由格式不匹配，跳过: {line[:50]}...")
            return None

        try:
            destination = match.group(1)
            protocol_str = match.group(2).upper()
            pre = int(match.group(3))
            cost = int(match.group(4))
            next_hop = ""        # Flags和NextHop列忽略
            interface = match.group(5)

            # 确定协议类型
            protocol = self._determine_protocol(protocol_str)

            # 创建路由对象
            route = BgpRoute(
                destination=destination,
                next_hop=next_hop,
                interface=interface,
                pre=pre,
                cost=cost,
                protocol=protocol
            )

            return route

        except (ValueError, IndexError) as e:
            logger.warning(f"第{line_num}行: 解析路由失败 - {e}: {line[:50]}...")
            return None

    def _looks_like_route_line(self, line: str) -> bool:
        """判断行是否看起来像路由条目（启发式检测）"""
        # 检查是否包含IP地址和数字（简单启发式）
        ip_pattern = r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}'
        if re.search(ip_pattern, line):
            # 检查是否包含数字（Pre和Cost）
            if re.search(r'\s+\d+\s+\d+\s+', line):
                return True
        return False

    def _parse_ecmp_continuation(self, line: str, last_destination: str, line_num: int) -> Optional[BgpRoute]:
        """解析ECMP续行（省略了Destination/Mask的路由行）

        Args:
            line: 文本行
            last_destination: 前一条路由的destination
            line_num: 行号（用于日志）

        Returns:
            如果匹配则返回BgpRoute对象，否则返回None
        """
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith('#'):
            return None

        match = self.ECMP_CONTINUATION_PATTERN.match(line)
        if not match:
            return None

        try:
            protocol_str = match.group(1).upper()
            pre = int(match.group(2))
            cost = int(match.group(3))
            next_hop = ""        # Flags和NextHop列忽略
            interface = match.group(4)

            protocol = self._determine_protocol(protocol_str)

            return BgpRoute(
                destination=last_destination,
                next_hop=next_hop,
                interface=interface,
                pre=pre,
                cost=cost,
                protocol=protocol
            )
        except (ValueError, IndexError) as e:
            logger.warning(f"第{line_num}行: 解析ECMP续行失败 - {e}: {line_stripped[:50]}...")
            return None

    def _determine_protocol(self, protocol_str: str) -> RouteProtocol:
        """确定路由协议类型"""
        protocol_str = protocol_str.upper()

        if protocol_str == "IBGP":
            return RouteProtocol.IBGP
        elif protocol_str == "EBGP":
            return RouteProtocol.EBGP
        elif protocol_str == "BGP":
            return RouteProtocol.BGP
        else:
            # 如果无法识别，默认使用BGP
            logger.warning(f"未知协议类型: {protocol_str}，使用BGP")
            return RouteProtocol.BGP

    @staticmethod
    def _extract_peer_device(description: str) -> Optional[str]:
        """从接口描述中提取对端设备名

        格式: to_<对端设备名>_<对端接口>
        例如: to_cnnorth2d-dsw-11.134.128.122_Eth-Trunk1
              提取: cnnorth2d-dsw-11.134.128.122

        大小写不敏感（to_ / To_ / TO_）
        """
        desc = description.strip()
        # 匹配 to_ 前缀（大小写不敏感）
        m = re.match(r'^to_(.+)', desc, re.IGNORECASE)
        if not m:
            return None
        rest = m.group(1)
        # 去掉最后一个 _<对端接口> 部分
        last_underscore = rest.rfind('_')
        if last_underscore > 0:
            return rest[:last_underscore]
        return rest

    @staticmethod
    def validate_file_format(filepath: str) -> Tuple[bool, str]:
        """
        验证文件格式是否有效

        Args:
            filepath: 文件路径

        Returns:
            (是否有效, 错误信息)
        """
        try:
            parser = BgpRouteParser()
            # 只解析前100行进行验证
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [next(f, '') for _ in range(100)]

            # 检查是否有设备名称
            device_found = False
            for line in lines:
                if '<' in line and '>' in line:
                    device_found = True
                    break

            # 检查是否有路由表标题（以 Destination/Mask 列标题行为准）
            routing_table_found = any('Destination/Mask' in line for line in lines)

            if not device_found:
                return False, "未找到设备名称（<设备名>格式）"

            if not routing_table_found:
                return False, "未找到路由表标题（Destination/Mask）"

            # 尝试解析几行路由
            parser = BgpRouteParser()
            test_device = parser.parse_lines(lines[:50], "test")
            if len(test_device.routes) == 0:
                return False, "未找到有效的路由条目"

            return True, f"格式验证通过，发现 {len(test_device.routes)} 条路由"

        except Exception as e:
            return False, f"验证失败: {e}"

    @staticmethod
    def scan_file(filepath: str) -> FileScanResult:
        """
        快速扫描单个BGP路由表文件，提取文件信息，无需完整解析。
        仅读取前50行，适合目录扫描场景。

        Args:
            filepath: 文件路径

        Returns:
            FileScanResult 对象
        """
        filename = Path(filepath).name
        try:
            file_size = os.path.getsize(filepath)
        except OSError:
            file_size = 0

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [next(f, '') for _ in range(50)]
        except Exception as e:
            return FileScanResult(
                filepath=filepath, filename=filename, is_valid=False,
                device_name=Path(filepath).stem, route_count=0,
                file_size=file_size, error_message=f"无法读取文件: {e}"
            )

        # 提取设备名称
        device_name = None
        for line in lines:
            if '<' in line and '>' in line:
                match = BgpRouteParser.DEVICE_NAME_PATTERN.search(line)
                if match:
                    device_name = match.group(1).strip()
                    break

        if device_name is None:
            device_name = Path(filepath).stem

        # 检查设备名称和路由表标题
        device_found = any('<' in line and '>' in line for line in lines)
        routing_found = any('Destination/Mask' in line for line in lines)

        if not device_found:
            return FileScanResult(
                filepath=filepath, filename=filename, is_valid=False,
                device_name=device_name, route_count=0,
                file_size=file_size, error_message="未找到设备名称（<设备名>格式）"
            )

        if not routing_found:
            return FileScanResult(
                filepath=filepath, filename=filename, is_valid=False,
                device_name=device_name, route_count=0,
                file_size=file_size, error_message="未找到BGP路由表标题"
            )

        # 统计前50行中粗略的路由条目数（快速扫描，不做完整解析）
        route_count = 0
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if BgpRouteParser.ROUTE_LINE_PATTERN.match(line_stripped):
                route_count += 1

        if route_count == 0:
            return FileScanResult(
                filepath=filepath, filename=filename, is_valid=False,
                device_name=device_name, route_count=0,
                file_size=file_size, error_message="未找到有效的路由条目"
            )

        return FileScanResult(
            filepath=filepath, filename=filename, is_valid=True,
            device_name=device_name, route_count=route_count,
            file_size=file_size
        )


# 快捷函数
def parse_bgp_file(filepath: str, encoding: str = 'auto') -> Device:
    """解析单个BGP路由表文件（快捷函数）"""
    parser = BgpRouteParser(encoding=encoding)
    return parser.parse_file(filepath)


def parse_multiple_bgp_files(filepaths: List[str], encoding: str = 'auto') -> List[Device]:
    """解析多个BGP路由表文件（快捷函数）"""
    parser = BgpRouteParser(encoding=encoding)
    return list(parser.parse_files_streaming(filepaths))