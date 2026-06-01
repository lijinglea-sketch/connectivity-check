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


class CookieInvalidError(RuntimeError):
    """Cookie 无效或过期"""
    pass


def get_task_id(task_code, cookie):
    url = (
        f"https://roadtask.map.xiaojukeji.com"
        f"/task/taskmanage/api/v0.1/task/history/list"
        f"?task_code={task_code}"
    )
    headers = _make_headers(cookie)
    try:
        body = _fetch(url, headers)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CookieInvalidError(
                "Cookie 已失效或无权限访问 roadtask。\n"
                "可能原因：\n"
                "1. ticket 已过期，请重新从浏览器复制 Cookie\n"
                "2. 当前网络环境（如外网/VPN）无法访问内网服务\n"
                "3. task_code 不属于当前 Cookie 对应的项目\n\n"
                "建议：请有内网权限的同事帮忙拉取后，使用「分享结果」功能。"
            )
        elif e.code == 404:
            raise RuntimeError(f"task_code={task_code} 不存在或已删除")
        else:
            raise RuntimeError(f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"网络请求失败: {e.reason}\n"
            "可能原因：当前环境无法访问 roadtask.map.xiaojukeji.com（外网限制/VPN）\n"
            "建议：请有内网权限的同事帮忙拉取后，使用「分享结果」功能。"
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(
            "返回数据不是有效的 JSON，可能是登录页或验证码页面。\n"
            "请确认 Cookie 是否包含有效的 ticket 字段。"
        )

    if data.get("status") != 0:
        msg = data.get('msg', '未知错误')
        if '登录' in msg or '权限' in msg or 'ticket' in msg.lower():
            raise CookieInvalidError(
                f"接口返回错误: {msg}\n"
                "Cookie 可能已过期，请重新从浏览器复制完整的 Cookie（含 ticket）。"
            )
        raise RuntimeError(
            f"获取 task_id 失败: {msg}\n"
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
    try:
        return _fetch(url, headers)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CookieInvalidError(
                "拉取 OSM 数据失败：Cookie 已失效或无权限。\n"
                "建议：请有内网权限的同事帮忙拉取后，使用「分享结果」功能。"
            )
        raise RuntimeError(f"拉取 OSM 数据失败: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"网络请求失败: {e.reason}\n"
            "可能原因：当前环境无法访问 roadtask 内网服务。"
        )


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
