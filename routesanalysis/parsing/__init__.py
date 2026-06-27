"""解析层 - 统一入口"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Iterator

from ..models import Device
from .core import FileScanResult, read_file_lines, extract_device_name, \
    detect_encoding
from .textfsm_engine import TextfsmParser

logger = logging.getLogger(__name__)

# 单例
_textfsm_parser = TextfsmParser()


def parse_device_file(filepath: str, encoding: str = 'auto') -> Device:
    """
    解析 BGP 路由表文件
    单解析策略：TextFSM（不再 fallback 到正则引擎）
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

    # TextFSM 解析
    routes = _textfsm_parser.parse_bgp_routes(content)
    interfaces = _textfsm_parser.parse_interface_descriptions(content)

    if routes is None:
        raise ValueError(
            f"TextFSM 解析失败，无法解析文件: {filepath}"
        )

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


def parse_device_files(filepaths: List[str], encoding: str = 'auto') -> List[Device]:
    """解析多个 BGP 路由表文件"""
    return [parse_device_file(fp, encoding) for fp in filepaths]


def validate_file_format(filepath: str) -> bool:
    """验证文件格式是否有效——以 TextFSM 能否解析为标志"""
    try:
        lines = read_file_lines(filepath, 'auto')
        content = "\n".join(lines)
        routes = _textfsm_parser.parse_bgp_routes(content[:4096])
        return routes is not None
    except Exception:
        return False


def scan_file(filepath: str) -> FileScanResult:
    """扫描单个文件，提取设备名和路由数量"""
    try:
        lines = read_file_lines(filepath, 'auto')
        content = "\n".join(lines)
        device_name = extract_device_name(lines[0]) if lines else None
        if not device_name:
            for line in lines:
                name = extract_device_name(line)
                if name:
                    device_name = name
                    break
        routes = _textfsm_parser.parse_bgp_routes(content)
        route_count = len(routes) if routes else 0
        file_size = Path(filepath).stat().st_size

        if routes is None:
            return FileScanResult(
                filepath=str(Path(filepath).resolve()),
                filename=Path(filepath).name,
                is_valid=False,
                error_message="TextFSM 解析失败",
                device_name=str(device_name) if device_name else "",
                route_count=0,
                file_size=file_size,
            )

        return FileScanResult(
            filepath=str(Path(filepath).resolve()),
            filename=Path(filepath).name,
            is_valid=True,
            error_message="",
            device_name=str(device_name) if device_name else "",
            route_count=route_count,
            file_size=file_size,
        )
    except Exception as e:
        fpath = Path(filepath)
        return FileScanResult(
            filepath=str(fpath.resolve()),
            filename=fpath.name,
            is_valid=False,
            error_message=str(e),
            device_name="",
            route_count=0,
            file_size=fpath.stat().st_size,
        )


class BgpRouteParser:
    """兼容旧 API 的包装类，内部使用 TextFSM 单解析策略"""

    def __init__(self, encoding: str = 'auto'):
        self.encoding = encoding

    def parse_file(self, filepath: str) -> Device:
        return parse_device_file(filepath, encoding=self.encoding)

    def parse_lines(self, lines: List[str], device_name: str = "unknown") -> Device:
        content = "\n".join(lines)
        routes = _textfsm_parser.parse_bgp_routes(content)
        interfaces = _textfsm_parser.parse_interface_descriptions(content)
        if routes is None:
            raise ValueError("TextFSM 解析失败，无法解析输入内容")
        return Device(
            name=device_name,
            filename="",
            routes=routes,
            interfaces=interfaces,
        )

    def parse_files_streaming(self, filepaths: List[str]) -> Iterator[Device]:
        for fp in filepaths:
            yield self.parse_file(fp)


__all__ = [
    "parse_device_file",
    "parse_device_files",
    "validate_file_format",
    "scan_file",
    "FileScanResult",
    "BgpRouteParser",
]
