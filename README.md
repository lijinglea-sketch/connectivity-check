# 路网连通性变更检测工具

对比道路数据生产任务变更前后的 OSM XML 快照，自动检测路网拓扑变更对进出口连通性的影响，并生成交互式地图可视化。

---

## 快速开始

### macOS

1. 下载 `connectivity-check-macOS.zip` 并解压
2. 双击 `启动路网检测.command`
3. 首次启动会自动安装依赖（约 1-2 分钟），随后浏览器自动打开

### Windows

1. 下载 `connectivity-check-Windows.zip` 并解压
2. 双击 `启动路网检测.bat`
3. 首次启动会自动安装依赖（约 1-2 分钟），随后浏览器自动打开

**系统要求**：Python 3.9+（macOS 通常自带，Windows 需手动安装）

---

## 使用方式

### 方式一：API 拉取（推荐，需内网权限）

1. 在左侧选择 **"API 拉取"**
2. 输入 **Task Code**（如 `ZBJL26041610221715858631665`）
3. 从浏览器 F12 → Application → Cookies 复制完整 **Cookie**（含 `ticket` 字段）
4. 点击 **"拉取数据并分析"**

### 方式二：上传 XML 文件

1. 在左侧选择 **"上传 XML 文件"**
2. 分别上传变更前（draw）和变更后（submit）的 OSM XML
3. 点击 **"开始分析"**

**获取 XML 文件**：
- 打开 roadtask 平台，进入目标任务
- F12 → Network，找到 `task_time` 请求
- 分别保存 `action=draw` 和 `action=submit` 的响应为 XML 文件

### 分享给外网同学

分析完成后，点击结果页中的 **"📤 分享给外网同学"** → 下载离线 HTML → 发送给同事。对方双击 HTML 即可在浏览器中查看完整交互地图，无需任何网络/API。

---

## 分析结果说明

| 优先级 | 含义 | 操作 |
|--------|------|------|
| 🔴 重点 | 路径拓扑发生实质变化 | 需人工核实 |
| 🟡 普通 | 有变更但影响较小 | 视情况核实 |
| 🟢 透出 | 未受影响的进出口对 | 无需处理 |

地图图例：
- **绿色**：变更前路径
- **深红色**：根因 link（导致连通性变化的源头）
- **粉色**：普通变更 link
- **紫色**：未变更 link / 进出口 link

---

## 常见问题

**Q: 提示 "Cookie 已失效或无权限访问 roadtask"**

A: 你的网络环境无法访问内网服务，或 ticket 已过期。请：
1. 确认已连接公司 VPN（如需）
2. 重新从浏览器复制最新的 Cookie
3. 或请有内网权限的同事帮忙拉取后分享 HTML

**Q: 提示 "Name or service not known"**

A: 当前环境无法解析 `roadtask.map.xiaojukeji.com`。如果你在 Streamlit Cloud 等外网环境访问，这是预期行为——请使用本地部署版本。

**Q: 如何更新到最新版本？**

A: 重新下载最新 Release 的 zip 包解压即可。

---

## 技术栈

- [Streamlit](https://streamlit.io/) — Web UI 框架
- [Leaflet](https://leafletjs.com/) — 地图可视化
- [NetworkX](https://networkx.org/) — 图算法（连通性分析）
- 高德地图 — 底图 tiles

---

## 项目结构

```
.
├── app.py              # Streamlit 主应用
├── fetch_task_data.py  # roadtask API 拉取
├── gen_map.py          # 连通性分析 + HTML 地图生成
├── requirements.txt    # Python 依赖
├── 启动路网检测.command   # macOS 启动脚本
└── 启动路网检测.bat       # Windows 启动脚本
```

---

## 开发者

本地调试：

```bash
pip install -r requirements.txt
streamlit run app.py
```
