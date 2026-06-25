"""解析层公共工具"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import chardet


@dataclass
class FileScanResult:
    """单文件的快速扫描结果（用于目录扫描模式）"""
    filepath: str
    filename: str
    is_valid: bool
    device_name: str
    route_count: int
    file_size: int
    error_message: str = ""


# 预编译正则
DEVICE_NAME_PATTERN = re.compile(r'<([^>]+)>')


def detect_encoding(filepath: str, default: str = 'auto') -> str:
    """检测文件编码，返回 utf-8 或 gbk"""
    if default and default.lower() != 'auto':
        return default
    try:
        with open(filepath, 'rb') as f:
            raw = f.read(8192)
        result = chardet.detect(raw)
        encoding = result.get('encoding', 'utf-8') or 'utf-8'
        encoding = encoding.lower().replace('gb2312', 'gbk').replace('gb18030', 'gbk')
        return encoding
    except Exception:
        return 'utf-8'


def extract_device_name(line: str) -> Optional[str]:
    """从一行中提取设备名（< > 内的内容）"""
    match = DEVICE_NAME_PATTERN.search(line.strip())
    return match.group(1).strip() if match else None


def normalize_path(filepath: str) -> str:
    """规范化文件路径"""
    return str(Path(filepath).resolve())


def read_file_lines(filepath: str, encoding: str = 'auto') -> List[str]:
    """读取文件并返回行列表"""
    enc = detect_encoding(filepath, encoding)
    with open(filepath, 'r', encoding=enc) as f:
        return f.readlines()
