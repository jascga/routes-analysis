"""routesanalysis CLI 入口"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import List

import click

from . import __version__
from .parser import parse_bgp_file
from .analyzer import MultiGroupAnalyzer, MultiGroupAnalysisResult
from .exporter import export_multi_group_result

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(__version__, prog_name="routesanalysis")
def cli():
    """华为交换机 BGP 路由表分析工具"""


@cli.command("multi-group")
@click.argument("files", nargs=-1, type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--output", type=click.Path(),
              help="Excel 输出路径；多文件时若指向目录，每个输入生成一份报告")
@click.option("-m", "--min-groups", type=click.IntRange(2, 100), default=2, show_default=True,
              help="命中所需的最小平行设备组数")
@click.option("-e", "--encoding", default="auto", show_default=True,
              help="输入文件编码 (auto/utf-8/gbk)")
@click.option("--no-excel", is_flag=True, help="只输出终端摘要，不生成 Excel")
@click.option("-v", "--verbose", is_flag=True, help="显示详细日志")
def multi_group(files: tuple, output: str | None, min_groups: int,
                encoding: str, no_excel: bool, verbose: bool):
    """场景 1：找出负载分担到多组平行设备的路由

    示例:

      \b
      routesanalysis multi-group device.txt -o report.xlsx
      routesanalysis multi-group *.txt -o reports/
      routesanalysis multi-group device.txt --no-excel
    """
    _setup_logging(verbose)

    if not files:
        click.echo("错误：至少需要 1 个输入文件", err=True)
        sys.exit(2)

    analyzer = MultiGroupAnalyzer(min_groups=min_groups)

    results: List[MultiGroupAnalysisResult] = []
    for fp in files:
        click.echo(f"→ 解析 {fp}")
        try:
            device = parse_bgp_file(fp, encoding=encoding)
        except Exception as e:
            click.echo(f"  ✗ 解析失败: {e}", err=True)
            continue

        click.echo(f"  设备名: {device.name} | 路由: {len(device.routes)} | "
                   f"接口描述: {len(device.interface_peer_map)} 条")

        if not device.has_interface_descriptions():
            click.echo("  ⚠️  未发现 'display interface description' 输出，"
                       "对端设备无法识别，结果将不准确。", err=True)

        result = analyzer.analyze(device)
        results.append(result)
        _print_summary(result)

    if no_excel:
        return

    _write_outputs(results, output, files)


def _print_summary(result: MultiGroupAnalysisResult):
    s = result.summary()
    click.echo("  " + "-" * 60)
    click.echo(f"  命中路由数 (≥{s['min_groups']}组): {s['hit_count']} / {s['total_destinations']} 目的网段")
    click.echo(f"  识别平行设备组数: {s['group_count']}")
    if s['unparseable_peer_count']:
        click.echo(f"  ⚠️  不规范对端设备名: {s['unparseable_peer_count']} 个（已按单独成组处理）")

    if result.hits:
        click.echo("  命中示例 (前 5 条):")
        for hit in result.hits[:5]:
            groups = ",".join(hit.group_keys)
            click.echo(f"    {hit.destination:20s}  {hit.group_count}组×{hit.path_count}路径  [{groups}]")
    click.echo()


def _write_outputs(results: List[MultiGroupAnalysisResult],
                   output: str | None, files: tuple):
    if not results:
        return

    # 单文件
    if len(results) == 1:
        out = Path(output) if output else Path(f"{results[0].device.name or 'device'}_multi_group.xlsx")
        if out.exists() and out.is_dir():
            out = out / f"{results[0].device.name or 'device'}_multi_group.xlsx"
        path = export_multi_group_result(results[0], out)
        click.echo(f"✓ Excel 报告已生成: {path}")
        return

    # 多文件：output 必须是目录（或不指定，默认 ./reports/）
    out_dir = Path(output) if output else Path("./reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.exists() and not out_dir.is_dir():
        click.echo(f"错误：多文件模式下 -o 必须指向目录，当前为文件: {out_dir}", err=True)
        sys.exit(2)

    for result in results:
        name = result.device.name or Path(result.device.filename).stem
        path = export_multi_group_result(result, out_dir / f"{name}_multi_group.xlsx")
        click.echo(f"✓ {path}")


@cli.command("inspect")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("-e", "--encoding", default="auto", show_default=True)
def inspect(filepath: str, encoding: str):
    """快速查看文件解析后的设备信息"""
    device = parse_bgp_file(filepath, encoding=encoding)
    click.echo(f"设备名:     {device.name}")
    click.echo(f"源文件:     {device.filename}")
    click.echo(f"路由总数:   {len(device.routes)}")
    click.echo(f"接口描述:   {len(device.interface_peer_map)} 条")
    if device.interface_peer_map:
        click.echo("接口→对端 (前 10):")
        for i, (intf, peer) in enumerate(list(device.interface_peer_map.items())[:10]):
            click.echo(f"  {intf:25s}  →  {peer}")


def main():
    cli()


if __name__ == "__main__":
    main()
