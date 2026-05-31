"""
路网连通性变更检测 — Streamlit 版
启动: streamlit run app.py
"""

import streamlit as st
import streamlit.components.v1 as components
import tempfile
import os
import xml.etree.ElementTree as ET

from gen_map import run_analysis


def extract_task_code_from_xml(filepath):
    """从 XML 中提取 task_code（取第一个 way 的 task_code tag）"""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        for way in root.findall('way'):
            for tag in way.findall('tag'):
                if tag.get('k') == 'task_code' and tag.get('v'):
                    return tag.get('v')
    except Exception:
        pass
    return None


st.set_page_config(
    page_title="路网连通性变更检测",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ 路网连通性变更检测")

# ── 侧边栏：输入参数 ──
with st.sidebar:
    st.header("参数配置")

    input_mode = st.radio(
        "数据来源",
        ["📂 上传 XML 文件", "🌐 API 拉取（仅限本地部署）"],
        index=0,
    )

    if input_mode == "📂 上传 XML 文件":
        st.caption("上传变更前后的 OSM XML 文件")
        draw_file = st.file_uploader("变更前 (draw)", type=["xml"], key="draw")
        submit_file = st.file_uploader("变更后 (submit)", type=["xml"], key="submit")
        run_btn = st.button("🚀 开始分析", type="primary", use_container_width=True)

        st.divider()
        st.caption("如何获取 XML 文件？")
        st.markdown("""
        1. 打开 roadtask 平台，进入目标任务
        2. F12 → Network，找到 `task_time` 请求
        3. 分别保存 `action=draw` 和 `action=submit` 的响应为 XML 文件
        """)
    else:
        task_code = st.text_input(
            "Task Code",
            placeholder="输入 task_code",
        )
        cookie = st.text_area(
            "Cookie",
            placeholder="从浏览器 F12 复制完整 Cookie（含 ticket）",
            height=120,
        )
        run_btn = st.button("🚀 拉取数据并分析", type="primary", use_container_width=True)

        st.divider()
        st.caption("⚠️ 注意")
        st.markdown("""
        API 拉取仅在**本地部署**时可用（`streamlit run app.py`）。

        Cloud 版请使用「上传 XML 文件」模式。
        """)


def show_results(html, summary):
    """展示分析结果"""
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("变更前 link 数", summary['before_links'])
    col2.metric("变更后 link 数", summary['after_links'])
    col3.metric("自动检测替换对", summary['replacements'])
    col4.metric("核实分组", summary['groups'])

    if summary['intersection_links'] > 0:
        st.info(f"识别到 {summary['intersection_links']} 条交叉点内 link（已合并为节点，不影响通行性）")

    if summary['groups'] == 0:
        st.success("🎉 未检测到连通性变更，无需核实！")
    else:
        st.subheader("分组概览")
        for g in summary['group_details']:
            icon = "🔴" if g['bucket'] == '重点' else ("🟡" if g['bucket'] == '普通' else "🟢")
            causal_str = f"根因: {', '.join(g['causal'])}" if g['causal'] else "替换连锁影响"
            st.markdown(f"{icon} **组{g['gidx']}** [{g['bucket']}] 出口:{g['xid']} — {g['entry_count']}个entry — {causal_str}")

    st.subheader("交互地图")
    components.html(html, height=800, scrolling=False)


# ── 主区域 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_XML_DIR = os.path.join(_SCRIPT_DIR, "xml_data")

if run_btn:
    os.makedirs(_XML_DIR, exist_ok=True)

    if input_mode == "📂 上传 XML 文件":
        # 文件上传模式
        if not draw_file or not submit_file:
            st.error("请上传变更前 (draw) 和变更后 (submit) 两个 XML 文件")
            st.stop()

        # 先写到临时位置提取 task_code，再用规范文件名落盘
        tmp_dir = tempfile.mkdtemp(prefix="conn_check_")
        tmp_draw = os.path.join(tmp_dir, "draw.xml")
        tmp_submit = os.path.join(tmp_dir, "submit.xml")
        with open(tmp_draw, "wb") as f:
            f.write(draw_file.getvalue())
        with open(tmp_submit, "wb") as f:
            f.write(submit_file.getvalue())

        # 从 XML 中自动提取 task_code
        tc = extract_task_code_from_xml(tmp_submit) or extract_task_code_from_xml(tmp_draw) or "UNKNOWN"

        # 用规范文件名保存到本地
        draw_path = os.path.join(_XML_DIR, f"taskcode={tc}&action=draw.xml")
        submit_path = os.path.join(_XML_DIR, f"taskcode={tc}&action=submit.xml")
        with open(draw_path, "wb") as f:
            f.write(draw_file.getvalue())
        with open(submit_path, "wb") as f:
            f.write(submit_file.getvalue())
        st.caption(f"📁 XML 已保存到 `{_XML_DIR}`")

    else:
        # API 拉取模式
        if not task_code.strip():
            st.error("请输入 task_code")
            st.stop()
        if not cookie.strip():
            st.error("请输入 Cookie")
            st.stop()

        tc = task_code.strip()
        with st.spinner("正在拉取 OSM 数据..."):
            try:
                from fetch_task_data import fetch_task_data
                task_id, draw_path, submit_path = fetch_task_data(
                    tc, cookie.strip(), output_dir=_XML_DIR
                )
                st.success(f"数据拉取完成，task_id={task_id}")
                st.caption(f"📁 XML 已保存到 `{_XML_DIR}`")
            except Exception as e:
                st.error(f"数据拉取失败：{e}")
                st.stop()

    with st.spinner("正在分析连通性变更..."):
        try:
            html, summary = run_analysis(tc, draw_path, submit_path)
        except Exception as e:
            st.error(f"分析失败：{e}")
            st.stop()

    st.session_state['last_html'] = html
    st.session_state['last_summary'] = summary

    show_results(html, summary)

elif 'last_html' in st.session_state:
    show_results(st.session_state['last_html'], st.session_state['last_summary'])

else:
    st.info("👈 在左侧上传变更前后的 XML 文件，点击按钮开始分析")
