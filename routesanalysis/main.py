"""routesanalysis CLI 入口"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import List, Tuple

import click

from routesanalysis import __version__
from routesanalysis.parsing import parse_device_file, BgpRouteParser
from routesanalysis.analyzer import MultiGroupAnalyzer, MultiGroupAnalysisResult
from routesanalysis.export import export_multi_group_result, export_comparison_result
from routesanalysis.comparator import BgpRouteComparator
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
            device = parse_device_file(fp, encoding=encoding)
        except Exception as e:
            click.echo(f"  ✗ 解析失败: {e}", err=True)
            continue

        click.echo(f"  设备名: {device.name} | 路由: {len(device.routes)} | "
                   f"接口描述: {len(device.interfaces)} 条")

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
    device = parse_device_file(filepath, encoding=encoding)
    click.echo(f"设备名:     {device.name}")
    click.echo(f"源文件:     {device.filename}")
    click.echo(f"路由总数:   {len(device.routes)}")
    click.echo(f"接口描述:   {len(device.interfaces)} 条")
    if device.interfaces:
        click.echo("接口→对端 (前 10):")
        for intf in device.interfaces[:10]:
            click.echo(f"  {intf.name:25s}  →  {intf.peer_device}")


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


def _format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    else:
        return f"{size_bytes / 1024 / 1024:.1f}MB"


def _scan_and_select_files() -> Tuple[List[str], int]:
    """扫描当前目录，交互式选择文件和基准设备"""
    from routesanalysis.parsing import scan_file, FileScanResult
    from pathlib import Path

    click.echo("\n=== 扫描目录中的 BGP 路由表文件 ===")
    click.echo(f"  扫描目录: {Path.cwd()}")

    extensions = [".txt", ".log", ".cfg"]
    candidates = []
    for ext in extensions:
        for f in Path.cwd().glob(f"*{ext}"):
            if f.is_file():
                candidates.append(str(f))
    candidates.sort()

    if not candidates:
        click.echo(f"当前目录中未找到支持的路由表文件 ({', '.join(extensions)})")
        return [], 0

    results: List[FileScanResult] = []
    for filepath in candidates:
        fname = Path(filepath).name
        click.echo(f"  正在验证: {fname}... ", nl=False)
        result = scan_file(filepath)
        if result.is_valid:
            click.echo(f"有效 (设备: {result.device_name}, 路由数: {result.route_count})")
            results.append(result)
        else:
            click.echo(f"跳过 - {result.error_message}")

    click.echo()

    if not results:
        click.echo("当前目录中未找到有效的 BGP 路由表文件")
        return [], 0

    if len(results) < 2:
        click.echo(f"至少需要 2 个有效文件（仅找到 {len(results)} 个）")
        return [], 0

    # 显示文件列表
    click.echo(f"找到 {len(results)} 个 BGP 路由表文件:")
    header = f"  {'#':<4} {'文件':<30} {'设备':<22} {'路由':<8} {'大小':<8}"
    sep = f"  {'-'*4} {'-'*30} {'-'*22} {'-'*8} {'-'*8}"
    click.echo(header)
    click.echo(sep)
    for i, r in enumerate(results, 1):
        click.echo(f"  {i:<4} {r.filename:<30} {r.device_name:<22} {r.route_count:<8} {_format_file_size(r.file_size):<8}")

    click.echo()

    # 选择文件
    while True:
        use_all = click.confirm("是否使用以上所有文件?", default=True)
        if use_all:
            selected = [r.filepath for r in results]
            break
        indices_str = click.prompt("请输入要比较的文件编号（逗号分隔，例如: 1,3,5）")
        try:
            raw_indices = [x.strip() for x in indices_str.split(",")]
            indices = []
            for x in raw_indices:
                idx = int(x)
                if 1 <= idx <= len(results):
                    indices.append(idx)
            if not indices:
                click.echo("未选择有效编号，请重试")
                continue
            selected = [results[i - 1].filepath for i in indices]
            selected_names = [results[i - 1].filename for i in indices]
            click.echo(f"  已选择: {', '.join(selected_names)}")
            if click.confirm("确认?", default=True):
                break
        except ValueError:
            click.echo("输入格式错误，请使用逗号分隔的数字")

    # 选择基准设备
    selected_info = []
    for fp in selected:
        name = Path(fp).stem
        for r in results:
            if r.filepath == fp:
                name = r.device_name
                break
        selected_info.append((fp, name))

    click.echo("\n  选择基准设备:")
    for i, (_, name) in enumerate(selected_info, 1):
        click.echo(f"    {i}. {name}")

    while True:
        baseline_str = click.prompt(f"请输入基准设备编号 (1-{len(selected_info)}) ", default="1")
        try:
            baseline_idx = int(baseline_str) - 1
            if 0 <= baseline_idx < len(selected_info):
                click.echo(f"基准设备: {selected_info[baseline_idx][1]}")
                break
        except ValueError:
            click.echo("请输入有效数字")

    click.echo()
    return selected, baseline_idx


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
@click.option("--scan", "-s", is_flag=True, help="扫描当前目录并交互式选择比较文件")
@click.option("-v", "--verbose", is_flag=True, help="显示详细日志")
def compare(files: tuple, baseline: int, output: str, encoding: str,
            no_excel: bool, scan: bool, verbose: bool):
    """场景 2：比较多台设备的 BGP 路由表，输出差异报告

    示例:

      \b
      routesanalysis compare device1.txt device2.txt
      routesanalysis compare --scan
      routesanalysis compare -b 1 file1.txt file2.txt file3.txt -o diff.xlsx
    """
    _setup_logging(verbose)

    # --scan 模式
    if scan:
        scanned_files, scan_baseline = _scan_and_select_files()
        if len(scanned_files) < 2:
            sys.exit(2)
        if baseline == 0 and scan_baseline != 0:
            baseline = scan_baseline
        files = tuple(scanned_files)

    if len(files) < 2:
        click.echo("错误：至少需要 2 个文件进行比较", err=True)
        sys.exit(2)

    if baseline < 0 or baseline >= len(files):
        click.echo(f"错误：基准索引 {baseline} 超出范围 (0-{len(files)-1})", err=True)
        sys.exit(2)

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
