"""配置加载器"""
from __future__ import annotations

import os
import re
import yaml
from pathlib import Path
from typing import Any, Dict, Optional


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载配置文件，缓存结果避免重复读取"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if config_path:
        path = Path(config_path)
    else:
        path = Path(__file__).parent / "config.yaml"
    if not path.exists():
        _config_cache = {}
        return _config_cache
    with open(path, encoding="utf-8") as f:
        _config_cache = yaml.safe_load(f) or {}
    return _config_cache


# 全局缓存
_config_cache = None


def get_parallel_group_config(config: Optional[Dict] = None) -> Dict:
    """获取平行设备分组配置"""
    if config is None:
        config = load_config()
    return config.get("parallel_group", {})


def get_segment_rules(group_config: Dict) -> list:
    """获取段处理规则列表"""
    return group_config.get("segment_rules", [])


def get_ignore_segments(group_config: Dict) -> list:
    """获取需要忽略的段索引"""
    return group_config.get("ignore_segments", [])


def get_separator(group_config: Dict) -> str:
    """获取分隔符"""
    return group_config.get("separator", "-")
