# routesanalysis - 华为 BGP 路由表多场景分析工具

针对华为交换机 BGP 路由表的可扩展分析框架。路由表解析逻辑复用自 [`routescompare`](https://github.com/jascga/routescompare)。

## 当前支持场景

### 场景 1：负载分担到多组平行设备分析（multi-group）

**输入**：单台设备的 `display interface description` + `display ip routing-table protocol bgp` 文本输出。

**逻辑**：

1. 用 `display interface description` 提取的"接口 → 对端设备名"映射，把 BGP 路由表里的 `Interface` 替换成对端设备。
2. 设备名按 `aaaa-b...-dddd-管理IP` 格式分组，取最后一个 `-` 之前的字符串作为分组键。
3. 找出"同一 Destination 的多条路径，对端设备归属 ≥ N 组不同的平行设备"的路由。

**分组规则**：

1. 设备名格式 `aaaa-bbbb-cccc-dddd-管理IP`，去掉管理 IP 部分
2. **去掉第三段（设备型号）**，仅保留第 1、2、4 段
3. **例外**：若第二段以 `nc` 开头（如 `nc01_cnt01`）且含 `_cnt`，
   则去掉 `_cnt` 之后的所有字符（保留 `_cnt`），
   使不同 `cntxx` 、同 `ncxx` 的设备归入同组
4. 不规范设备名（不含 `-` 或去 IP 后不足 3 段）：单独成组，Excel 中标黄警告

**示例**：

| 设备名 | 分组键 |
|---|---|
| `BJ-DC-SPINE-01-10.1.1.1` | `BJ-DC-01` |
| `BJ-DC-LEAF-01-10.1.1.2` | `BJ-DC-01` ← 型号不同但同组 |
| `BJ-DC-SPINE-02-10.1.1.3` | `BJ-DC-02` ← 序号不同不同组 |
| `BJ-nc01_cnt01-LEAF-01-10.1.1.1` | `BJ-nc01_cnt-01` |
| `BJ-nc01_cnt02-SPINE-01-10.1.1.2` | `BJ-nc01_cnt-01` ← 同组 |
| `BJ-nc02_cnt01-LEAF-01-10.1.1.3` | `BJ-nc02_cnt-01` ← 不同 nc 不同组 |

**输出**：4 Sheet 的 Excel 报告
- **汇总**：路由数、命中数、平行组数、警告
- **命中明细**：每条命中路由的目的网段、路径、对端、分组
- **所有路由**：全部 Destination 及其对应的分组信息（含未命中的）
- **平行设备组清单**：分组键 → 该组下的成员设备列表

## 安装

```bash
cd routes-analysis
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## CLI 使用

```bash
# 单文件分析
routesanalysis multi-group device.txt -o report.xlsx

# 批量分析（每个文件出一份报告到目录）
routesanalysis multi-group *.txt -o reports/

# 调整最小命中分组数（默认 2）
routesanalysis multi-group device.txt -m 3

# 只看终端摘要
routesanalysis multi-group device.txt --no-excel

# 查看文件解析后的设备信息
routesanalysis inspect device.txt
```

## 输入文件示例

把两条命令的输出合并到一个文件即可（顺序无所谓）：

```
<BJ-CORE-ME-01-10.0.0.1>display interface description
Interface                     PHY     Protocol Description
GigabitEthernet0/0/1          up      up       to_BJ-DC-SPINE-01-10.1.1.1_GE0/0/1
GigabitEthernet0/0/2          up      up       to_BJ-DC-SPINE-01-10.1.1.2_GE0/0/1
...

<BJ-CORE-ME-01-10.0.0.1>display ip routing-table protocol bgp
Destination/Mask    Proto   Pre  Cost        Flags NextHop         Interface
       10.10.1.0/24  EBGP    20   0             RD  192.168.2.1     GigabitEthernet0/0/1
                    EBGP    20   0             RD  192.168.2.4     GigabitEthernet0/0/4
...
```

## 项目结构

```
routes-analysis/
├── routesanalysis/
│   ├── parser.py      # 路由表 + 接口描述解析（复用 routescompare）
│   ├── models.py      # Device / BgpRoute / RouteProtocol（复用 routescompare）
│   ├── analyzer.py    # 平行设备分组 + 多组分担分析
│   ├── exporter.py    # Excel 导出
│   └── main.py        # CLI（click）
├── tests/
│   ├── test_analyzer.py
│   └── fixtures/sample_me_01.txt
├── pyproject.toml
├── requirements.txt
└── README.md
```

## 不规范对端设备名处理

对端设备名不符合 `xxx-yyy` 格式时（例如纯接口名 `Eth-Trunk1` → 没采集到接口描述），按**单独成组**处理（每个不规范名字独立成组），并在 Excel 报告中标黄警告。

## 测试

```bash
pytest -q
```

## Windows 可执行文件打包

### 方式 1：本地打包（推荐个人使用）

需在 **Windows** 上进行（PyInstaller 不支持跨平台编译）。

1. 安装 [Python 3.9+](https://www.python.org/downloads/windows/)，勾选 "Add Python to PATH"
2. clone 仓库到本地
3. 在项目根目录双击运行 `scripts\build.bat`
4. 输出文件：`dist\routesanalysis.exe`（约 10-15MB）

使用方法：

```cmd
routesanalysis.exe multi-group device.txt -o report.xlsx
routesanalysis.exe --help
```

### 方式 2：GitHub Actions 自动打包

push 一个 tag 就会自动打包并发布到 Release：

```bash
git tag v0.1.0
git push origin v0.1.0
```

打包产物会作为 Release asset 发布。同时可手动在 Actions 页面触发 `build-windows` workflow 获取 artifact。

### 方式 3：手动用 spec 文件

```cmd
pip install pyinstaller
pyinstaller --clean scripts\routesanalysis.spec
```

### 跨平台说明

- 不可在 Linux/macOS 打包出可在 Windows 运行的 exe
- Linux/macOS 可用 `scripts/build.sh` 本地测试打包（产物仅限该系统运行）

## 路线图

- [x] 场景 1：负载分担到多组平行设备
- [ ] 场景 2：待定
