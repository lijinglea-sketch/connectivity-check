"""
路网连通性变更检测 — Streamlit 版
启动: streamlit run app.py
"""

import streamlit as st
import streamlit.components.v1 as components
import tempfile
import os

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

    input_mode = st.radio(
        "数据来源",
        ["📂 上传 XML 文件", "🌐 API 拉取（仅限内网）"],
        index=0,
    )

    task_code = st.text_input(
        "Task Code",
        placeholder="输入 task_code",
    )

    if input_mode == "📂 上传 XML 文件":
        st.caption("上传变更前后的 OSM XML 文件")
        draw_file = st.file_uploader("变更前 (draw)", type=["xml"], key="draw")
        submit_file = st.file_uploader("变更后 (submit)", type=["xml"], key="submit")
        run_btn = st.button("🚀 开始分析", type="primary", use_container_width=True)

        st.divider()
        st.caption("如何获取 XML 文件？")
        st.markdown("""
        在浏览器中打开以下地址并保存：
        ```
        /trace/history/0.6/trace/history/task_time
        ?taskcode=xxx&taskid=xxx&action=draw
        ```
        将 `action=draw` 和 `action=submit` 分别保存为两个 XML 文件上传。
        """)
    else:
        cookie = st.text_area(
            "Cookie",
            placeholder="从浏览器 F12 复制完整 Cookie（含 ticket）",
            height=120,
        )
        run_btn = st.button("🚀 拉取数据并分析", type="primary", use_container_width=True)

        st.divider()
        st.caption("使用说明")
        st.markdown("""
        1. **仅限公司内网或 VPN 环境下使用**
        2. 在 roadtask 平台打开目标任务
        3. F12 → Network → 复制请求 Cookie
        4. 粘贴 Cookie + 输入 task_code → 分析
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
if run_btn:
    if not task_code.strip():
        st.error("请输入 task_code")
        st.stop()

    tmp_dir = tempfile.mkdtemp(prefix="conn_check_")

    if input_mode == "📂 上传 XML 文件":
        # 文件上传模式
        if not draw_file or not submit_file:
            st.error("请上传变更前 (draw) 和变更后 (submit) 两个 XML 文件")
            st.stop()

        draw_path = os.path.join(tmp_dir, "draw.xml")
        submit_path = os.path.join(tmp_dir, "submit.xml")
        with open(draw_path, "wb") as f:
            f.write(draw_file.getvalue())
        with open(submit_path, "wb") as f:
            f.write(submit_file.getvalue())

    else:
        # API 拉取模式
        if not cookie.strip():
            st.error("请输入 Cookie")
            st.stop()

        with st.spinner("正在拉取 OSM 数据..."):
            try:
                from fetch_task_data import fetch_task_data
                task_id, draw_path, submit_path = fetch_task_data(
                    task_code.strip(), cookie.strip(), output_dir=tmp_dir
                )
                st.success(f"数据拉取完成，task_id={task_id}")
            except Exception as e:
                st.error(f"数据拉取失败：{e}")
                st.stop()

    with st.spinner("正在分析连通性变更..."):
        try:
            html, summary = run_analysis(task_code.strip(), draw_path, submit_path)
        except Exception as e:
            st.error(f"分析失败：{e}")
            st.stop()

    st.session_state['last_html'] = html
    st.session_state['last_summary'] = summary

    show_results(html, summary)

elif 'last_html' in st.session_state:
    show_results(st.session_state['last_html'], st.session_state['last_summary'])

else:
    st.info("👈 在左侧配置参数，上传文件或输入 task_code，点击按钮开始分析")
