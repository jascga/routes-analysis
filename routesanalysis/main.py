"""routesanalysis CLI 入口"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import List

import click

from routesanalysis import __version__
from routesanalysis.parser import parse_bgp_file
from routesanalysis.analyzer import MultiGroupAnalyzer, MultiGroupAnalysisResult
from routesanalysis.export import export_multi_group_result, export_comparison_result
from routesanalysis.comparison import BgpRouteComparator
from routesanalysis.export.comparison import ExcelExporter

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


# ---------------------------------------------------------------------------
# 场景 2：多设备 BGP 路由表比较（从 routescompare 迁移而来）
# ---------------------------------------------------------------------------

_DIFF_TYPE_NAME = {
    "missing_destination": "缺少Destination",
    "missing_interface": "接口/对端缺失",
    "interface_mismatch": "接口不同",
    "pre_cost_diff": "Pre/Cost差异",
}


@cli.command("compare")
@click.argument("files", nargs=-1, type=click.Path(exists=True, dir_okay=False))
@click.option("-b", "--baseline", type=int, default=0, show_default=True,
              help="基准文件索引（从 0 开始）")
@click.option("-o", "--output", type=click.Path(),
              default="bgp_comparison.xlsx", show_default=True,
              help="Excel 输出路径")
@click.option("-e", "--encoding", default="auto", show_default=True,
              help="输入文件编码 (auto/utf-8/gbk)")
@click.option("--no-excel", is_flag=True, help="只输出终端摘要，不生成 Excel")
@click.option("-v", "--verbose", is_flag=True, help="显示详细日志")
def compare(files: tuple, baseline: int, output: str, encoding: str,
            no_excel: bool, verbose: bool):
    """场景 2：比较多台设备的 BGP 路由表，输出差异报告

    示例:

      \b
      routesanalysis compare device1.txt device2.txt
      routesanalysis compare -b 1 file1.txt file2.txt file3.txt -o diff.xlsx
    """
    _setup_logging(verbose)

    if len(files) < 2:
        click.echo("错误：至少需要 2 个文件进行比较", err=True)
        sys.exit(2)

    if baseline < 0 or baseline >= len(files):
        click.echo(f"错误：基准索引 {baseline} 超出范围 (0-{len(files)-1})", err=True)
        sys.exit(2)

    from routesanalysis.parser import parse_bgp_file, BgpRouteParser
    from routesanalysis.comparison.comparator import BgpRouteComparator

    click.echo(f"→ 基准文件: {files[baseline]} (索引 {baseline})")
    click.echo(f"→ 比较文件: {len(files) - 1} 个")
    click.echo()

    # 1) 逐个解析（以支持 encoding 参数）
    parser = BgpRouteParser(encoding=encoding)
    devices = []
    for i, fp in enumerate(files):
        try:
            click.echo(f"  解析 [{i+1}/{len(files)}] {fp}")
            d = parser.parse_file(fp)
            devices.append(d)
        except Exception as e:
            click.echo(f"  ✗ 解析失败: {e}", err=True)
            sys.exit(1)

    # 2) 比较
    comparator = BgpRouteComparator()
    for d in devices:
        comparator.add_device(d)
    comparator.set_baseline(baseline)
    try:
        result = comparator.compare_all()
    except Exception as e:
        click.echo(f"比较失败: {e}", err=True)
        if verbose:
            import traceback
            click.echo(traceback.format_exc(), err=True)
        sys.exit(1)

    stats = result.get_statistics()
    by_type = stats.get("by_type", {})

    click.echo("=" * 60)
    click.echo(f"基准设备: {result.baseline_device.name} ({len(result.baseline_device.routes)} 路由)")
    click.echo(f"比较设备: {', '.join(d.name for d in result.compared_devices)}")
    click.echo(f"差异总数: {len(result.differences)}")
    click.echo()
    if by_type:
        click.echo("差异类型统计:")
        for k, v in sorted(by_type.items()):
            click.echo(f"  {_DIFF_TYPE_NAME.get(k, k)}: {v}")
    click.echo("=" * 60)

    if result.differences:
        click.echo("\n差异示例 (前 5 个):")
        for i, diff in enumerate(result.differences[:5], 1):
            click.echo(f"  {i}. {diff.destination} - {_DIFF_TYPE_NAME.get(diff.difference_type.value, diff.difference_type.value)}")
        if len(result.differences) > 5:
            click.echo(f"  ... 还有 {len(result.differences) - 5} 个差异")
        click.echo()

    if no_excel:
        return

    try:
        exporter = ExcelExporter(write_only=len(result.differences) > 10000)
        exporter.export(result, output)
        click.echo(f"✓ Excel 报告已生成: {output}")
    except Exception as e:
        click.echo(f"导出 Excel 失败: {e}", err=True)
        if verbose:
            import traceback
            click.echo(traceback.format_exc(), err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
