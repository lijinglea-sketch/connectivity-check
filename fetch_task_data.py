"""
根据 task_code + cookie 自动拉取 roadtask 地图数据。
Streamlit 版：cookie 作为参数传入，不硬编码。
"""

import json
import os
import tempfile
import urllib.request
import urllib.error
import gzip


def _make_headers(cookie):
    return {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie,
        "Referer": "https://roadtask.map.xiaojukeji.com/trace/taskhistory/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }


def _fetch(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8")


def get_task_id(task_code, cookie):
    url = (
        f"https://roadtask.map.xiaojukeji.com"
        f"/task/taskmanage/api/v0.1/task/history/list"
        f"?task_code={task_code}"
    )
    headers = _make_headers(cookie)
    body = _fetch(url, headers)
    data = json.loads(body)
    if data.get("status") != 0:
        raise RuntimeError(
            f"获取 task_id 失败: {data.get('msg', '未知错误')}\n"
            f"task_code: {task_code}\n"
            f"可能原因: 1) Cookie/ticket 过期  "
            f"2) task_code 不属于当前 Cookie 的项目  "
            f"3) 网络问题"
        )
    result = data["result_data"]
    if not result:
        raise RuntimeError(f"task_code={task_code} 没有历史记录")
    return result[0]["task_id"]


def get_task_osm(task_code, task_id, action, cookie):
    url = (
        f"https://roadtask.map.xiaojukeji.com"
        f"/trace/history/0.6/trace/history/task_time"
        f"?taskcode={task_code}&taskid={task_id}&action={action}"
    )
    headers = _make_headers(cookie)
    return _fetch(url, headers)


def fetch_task_data(task_code, cookie, output_dir=None):
    """拉取 draw + submit OSM 数据，返回 (task_id, draw_path, submit_path)。
    output_dir 为 None 时使用临时目录。"""
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="connectivity_")

    task_id = get_task_id(task_code, cookie)

    os.makedirs(output_dir, exist_ok=True)
    paths = {}

    for action in ["draw", "submit"]:
        osm_xml = get_task_osm(task_code, task_id, action, cookie)
        filename = f"taskcode={task_code}&taskid={task_id}&action={action}.xml"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(osm_xml)
        paths[action] = filepath

    return task_id, paths["draw"], paths["submit"]
