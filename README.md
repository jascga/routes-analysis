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

1. 设备名格式 `aaaa-b...-dddd-管理IP`，取最后一个 `-` 之前的字符串作为分组键
2. **例外**：若第二段以 `nc` 开头（如 `nc01_cnt01`）且含 `_cnt`，
   则去掉 `_cnt` 之后的所有字符（保留 `_cnt`），
   使不同 `cntxx` 、同 `ncxx` 的设备归入同组
3. 不规范设备名（不含 `-`）：单独成组，Excel 报告中标黄警告

**示例**：

| 设备名 | 分组键 |
|---|---|
| `BJ-DC-SPINE-01-10.1.1.1` | `BJ-DC-SPINE-01` |
| `BJ-DC-SPINE-01-10.1.1.2` | `BJ-DC-SPINE-01` |
| `BJ-nc01_cnt01-LEAF-01-10.1.1.1` | `BJ-nc01_cnt-LEAF-01` |
| `BJ-nc01_cnt02-LEAF-01-10.1.1.2` | `BJ-nc01_cnt-LEAF-01` ← 与上行同组 |
| `BJ-nc02_cnt01-LEAF-01-10.1.1.3` | `BJ-nc02_cnt-LEAF-01` ← 不同 nc 不同组 |
| `BJ-abc_cnt01-LEAF-01-10.1.1.1` | `BJ-abc_cnt01-LEAF-01` ← 第二段不以 nc 开头，不变 |

**输出**：3 Sheet 的 Excel 报告
- **汇总**：路由数、命中数、平行组数、警告
- **命中明细**：每条命中路由的目的网段、路径、对端、分组
- **平行设备组清单**：分组键 → 该组下的成员设备列表（核对分组是否正确）

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

## 路线图

- [x] 场景 1：负载分担到多组平行设备
- [ ] 场景 2：待定
