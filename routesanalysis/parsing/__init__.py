"""解析层 - 统一入口"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Iterator

from ..models import Device
from .core import FileScanResult, read_file_lines, extract_device_name
from .textfsm_engine import TextfsmParser
from .regex_engine import RegexParser

logger = logging.getLogger(__name__)

# 单例
_textfsm_parser = TextfsmParser()
_regex_parser = RegexParser()


def parse_bgp_file(filepath: str, encoding: str = 'auto') -> Device:
    """
    解析 BGP 路由表文件
    双解析策略：TextFSM 优先 → 正则 fallback
    """
    logger.info(f"开始解析文件: {filepath}")

    lines = read_file_lines(filepath, encoding)
    content = "\n".join(lines)
    device_name = extract_device_name(lines[0]) if lines else None
    if not device_name:
        for line in lines:
            name = extract_device_name(line)
            if name:
                device_name = name
                break

    # 1. TextFSM 优先
    routes = _textfsm_parser.parse_bgp_routes(content)
    interfaces = _textfsm_parser.parse_interface_descriptions(content)

    if routes is not None:
        logger.debug("TextFSM 解析成功")
        device = Device(
            name=device_name or Path(filepath).stem,
            filename=str(Path(filepath).resolve()),
            routes=routes,
            interfaces=interfaces,
        )
        logger.info(f"解析完成: 设备={device.name}, 路由数={len(device.routes)}")
        if interfaces:
            logger.info(f"解析到 {len(interfaces)} 条接口描述")
        return device

    # 2. TextFSM 失败，回退到正则
    logger.debug("TextFSM 解析失败，回退到正则解析")
    device = _regex_parser.parse_file(filepath, encoding)
    logger.info(f"解析完成: 设备={device.name}, 路由数={len(device.routes)}")
    if device.interfaces:
        logger.info(f"解析到 {len(device.interfaces)} 条接口描述")
    return device


def parse_multiple_bgp_files(filepaths: List[str], encoding: str = 'auto') -> List[Device]:
    """解析多个 BGP 路由表文件"""
    return [parse_bgp_file(fp, encoding) for fp in filepaths]


def validate_file_format(filepath: str) -> bool:
    """验证文件格式是否有效"""
    return RegexParser.validate_file_format(filepath)[0]


def scan_file(filepath: str) -> FileScanResult:
    """扫描单个文件"""
    return RegexParser.scan_file(filepath)


class BgpRouteParser:
    """兼容旧 API 的包装类，内部使用双解析策略"""

    def __init__(self, encoding: str = 'auto'):
        self.encoding = encoding

    def parse_file(self, filepath: str) -> Device:
        return parse_bgp_file(filepath, encoding=self.encoding)

    def parse_lines(self, lines: List[str], device_name: str = "unknown") -> Device:
        content = "\n".join(lines)
        routes = _textfsm_parser.parse_bgp_routes(content)
        interfaces = _textfsm_parser.parse_interface_descriptions(content)
        if routes is not None:
            return Device(
                name=device_name,
                filename="",
                routes=routes,
                interfaces=interfaces,
            )
        return _regex_parser.parse_lines(lines, device_name)

    def parse_files_streaming(self, filepaths: List[str]) -> Iterator[Device]:
        for fp in filepaths:
            yield self.parse_file(fp)


__all__ = [
    "parse_bgp_file",
    "parse_multiple_bgp_files",
    "validate_file_format",
    "scan_file",
    "FileScanResult",
    "BgpRouteParser",
]
