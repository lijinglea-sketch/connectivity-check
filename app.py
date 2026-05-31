"""
路网连通性变更检测 — Streamlit 版
启动: streamlit run app.py
"""

import streamlit as st
import streamlit.components.v1 as components
import tempfile
import os

from fetch_task_data import fetch_task_data
from gen_map import run_analysis

st.set_page_config(
    page_title="路网连通性变更检测",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ 路网连通性变更检测")

# ── 侧边栏：输入参数 ──
with st.sidebar:
    st.header("参数配置")

    task_code = st.text_input(
        "Task Code",
        placeholder="输入 task_code，如 ZADE2604031046169943036418",
    )

    cookie = st.text_area(
        "Cookie",
        placeholder="从浏览器 F12 复制完整 Cookie（含 ticket）",
        height=120,
    )

    run_btn = st.button("🚀 拉取数据并分析", type="primary", use_container_width=True)

    st.divider()
    st.caption("使用说明")
    st.markdown("""
    1. 在 roadtask 平台打开目标任务
    2. F12 → Network → 复制任意请求的 Cookie
    3. 粘贴 Cookie + 输入 task_code
    4. 点击按钮，等待分析完成
    """)

# ── 主区域 ──
if run_btn:
    if not task_code.strip():
        st.error("请输入 task_code")
        st.stop()
    if not cookie.strip():
        st.error("请输入 Cookie")
        st.stop()

    with st.spinner("正在拉取 OSM 数据..."):
        try:
            tmp_dir = tempfile.mkdtemp(prefix="conn_check_")
            task_id, draw_path, submit_path = fetch_task_data(
                task_code.strip(), cookie.strip(), output_dir=tmp_dir
            )
        except Exception as e:
            st.error(f"数据拉取失败：{e}")
            st.stop()

    st.success(f"数据拉取完成，task_id={task_id}")

    with st.spinner("正在分析连通性变更..."):
        try:
            html, summary = run_analysis(task_code.strip(), draw_path, submit_path)
        except Exception as e:
            st.error(f"分析失败：{e}")
            st.stop()

    # 展示摘要
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
        # 分组详情
        st.subheader("分组概览")
        for g in summary['group_details']:
            icon = "🔴" if g['bucket'] == '重点' else ("🟡" if g['bucket'] == '普通' else "🟢")
            causal_str = f"根因: {', '.join(g['causal'])}" if g['causal'] else "替换连锁影响"
            st.markdown(f"{icon} **组{g['gidx']}** [{g['bucket']}] 出口:{g['xid']} — {g['entry_count']}个entry — {causal_str}")

    # 渲染交互地图
    st.subheader("交互地图")
    components.html(html, height=800, scrolling=False)

    # 存储到 session 以便不刷新也能看
    st.session_state['last_html'] = html
    st.session_state['last_summary'] = summary

elif 'last_html' in st.session_state:
    # 未点击按钮但有历史结果，展示上次的
    summary = st.session_state['last_summary']

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("变更前 link 数", summary['before_links'])
    col2.metric("变更后 link 数", summary['after_links'])
    col3.metric("自动检测替换对", summary['replacements'])
    col4.metric("核实分组", summary['groups'])

    components.html(st.session_state['last_html'], height=800, scrolling=False)

else:
    st.info("👈 在左侧输入 task_code 和 Cookie，点击按钮开始分析")
