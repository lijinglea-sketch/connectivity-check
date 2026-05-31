"""
路网连通性变更 — 可视化 HTML 地图
生成 Leaflet 交互地图：
  ① 变更前全量路网（黑色）
  ② 变更前 link 对路径（绿色）
  ③ 变更后 link 对路径（未变更=棕色，变更=红色）
  ④ 按分组渲染，带开关
"""

import sys
import os
import xml.etree.ElementTree as ET
import networkx as nx
import json
from collections import defaultdict

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 自动替换检测阈值（米）：起终点偏移 < 此值 且几何重合度 > GEO_OVERLAP_THRESH 时认为是同一条路
REPLACE_DIST_THRESH = 20
GEO_OVERLAP_THRESH = 0.7


# ── 解析 ──────────────────────────────────────────────

def parse_all(filepath):
    """解析 XML，返回 (nodes_dict, ways_dict)"""
    tree = ET.parse(filepath)
    root = tree.getroot()

    nodes = {}
    for n in root.findall('node'):
        nid = n.get('id')
        lat, lon = n.get('lat'), n.get('lon')
        if lat and lon:
            nodes[nid] = (float(lat), float(lon))

    ways = {}
    for way in root.findall('way'):
        if way.get('layer') != 'R':
            continue
        wid = way.get('id')
        nds = sorted(way.findall('nd'), key=lambda n: int(n.get('seq')))
        if len(nds) < 2:
            continue
        nd_refs = [nd.get('ref') for nd in nds]
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        ways[wid] = {
            'start': nd_refs[0],
            'end': nd_refs[-1],
            'nd_refs': nd_refs,
            'direction': int(tags.get('Direction', '1')),
            'name': tags.get('Name') or tags.get('EName') or '',
            'task_code': tags.get('task_code', ''),
            'tags': tags,
        }
    return nodes, ways


def find_intersection_merges(ways):
    """找出 link-intersection=yes 且双向的 link，返回节��合并映射。
    这类 link 功能上等价于一个交叉点，两端节点应视为同一节点。
    返回 dict: {node_id: representative_node_id}"""
    # Union-Find
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    intersection_links = set()
    for wid, w in ways.items():
        if w['tags'].get('link-intersection') == 'yes' and w['direction'] == 1:
            union(w['start'], w['end'])
            intersection_links.add(wid)

    # 构建最终映射
    merge_map = {}
    for nid in parent:
        rep = find(nid)
        if rep != nid:
            merge_map[nid] = rep
    return merge_map, intersection_links


def apply_merge(node_id, merge_map):
    """将节点映射到合并后的代表节点"""
    return merge_map.get(node_id, node_id)


def build_graph(ways, merge_map=None):
    G = nx.MultiDiGraph()
    for wid, w in ways.items():
        # 跳过 intersection link 本身（已合并为节点）
        if w['tags'].get('link-intersection') == 'yes' and w['direction'] == 1:
            continue
        s, e, d = w['start'], w['end'], w['direction']
        if merge_map:
            s = apply_merge(s, merge_map)
            e = apply_merge(e, merge_map)
        if s == e:
            continue  # 合并后自环，跳过
        if d == 1:
            G.add_edge(s, e, way_id=wid)
            G.add_edge(e, s, way_id=wid)
        elif d == 2:
            G.add_edge(s, e, way_id=wid)
        elif d == 3:
            G.add_edge(e, s, way_id=wid)
        else:
            G.add_edge(s, e, way_id=wid)
            G.add_edge(e, s, way_id=wid)
    return G


def get_edge_disjoint_paths(G, s, t):
    try:
        return list(nx.edge_disjoint_paths(G, s, t))
    except:
        return []


def path_to_link_ids(G, path):
    links = []
    for i in range(len(path) - 1):
        ed = G.get_edge_data(path[i], path[i + 1])
        if ed:
            links.append(list(ed.values())[0].get('way_id', '?'))
    return links


def normalize_path(ids, replacements=None):
    if not replacements:
        return ids
    return [replacements.get(w, w) for w in ids]


def way_coords(way, nodes):
    """返回 way 的 [[lat, lon], ...] 坐标列表"""
    coords = []
    for ref in way['nd_refs']:
        if ref in nodes:
            coords.append(list(nodes[ref]))
    return coords


def compute_color_hints(before_ids, after_ids):
    """对比 before/after 路径 link ID 列表，返回 after 各位置的颜色提示。
    - 完全相同 → 全 green
    - 从头部连续一致 → green，从尾部连续一致 → green，中间不一致 → red
    """
    if before_ids == after_ids:
        return ['green'] * len(after_ids)
    # 公共前缀
    prefix = 0
    for j in range(min(len(before_ids), len(after_ids))):
        if before_ids[j] == after_ids[j]:
            prefix += 1
        else:
            break
    # 公共后缀（不与前缀重叠）
    suffix = 0
    for j in range(1, min(len(before_ids), len(after_ids)) + 1):
        if before_ids[-j] == after_ids[-j] and (len(after_ids) - j) >= prefix:
            suffix += 1
        else:
            break
    hints = []
    for pos in range(len(after_ids)):
        if pos < prefix or pos >= len(after_ids) - suffix:
            hints.append('green')
        else:
            hints.append('red')
    return hints


import math

def _haversine(c1, c2):
    """两点间距离（米），输入 (lat, lon)"""
    lat1, lon1 = math.radians(c1[0]), math.radians(c1[1])
    lat2, lon2 = math.radians(c2[0]), math.radians(c2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _polyline_length(coords):
    """折线总长度（米）"""
    total = 0
    for i in range(len(coords) - 1):
        total += _haversine(coords[i], coords[i+1])
    return total


def _polyline_overlap(coords_a, coords_b):
    """估算两条折线的几何重合度（0~1）。
    用 A 上采样点到 B 的最近距离来评估。"""
    if not coords_a or not coords_b:
        return 0
    # 简化：在较短折线上按固定间隔取样，计算到另一条的最近点距离
    sample = coords_a if len(coords_a) <= len(coords_b) else coords_b
    target = coords_b if sample is coords_a else coords_a
    close_count = 0
    for pt in sample:
        min_d = min(_haversine(pt, tp) for tp in target)
        if min_d < REPLACE_DIST_THRESH:
            close_count += 1
    return close_count / len(sample) if sample else 0


def auto_detect_replacements(deleted, added, b_ways, a_ways, b_nodes, a_nodes,
                              merge_map=None):
    """自动检测 deleted↔added 中的替换对（删除重绘场景）。
    merge_map: 节点合并映射（蝴蝶型场景：交叉点内link两端合并后，
               新旧link端点对齐，可被识别为替换对）。
    返回 dict: {old_id: new_id}
    """
    if merge_map is None:
        merge_map = {}
    replacements = {}
    used_added = set()
    for did in sorted(deleted):
        dw = b_ways[did]
        # 应用 merge_map 获取合并后端点坐标
        d_start_node = apply_merge(dw['start'], merge_map)
        d_end_node = apply_merge(dw['end'], merge_map)
        d_start = b_nodes.get(d_start_node) or a_nodes.get(d_start_node)
        d_end = b_nodes.get(d_end_node) or a_nodes.get(d_end_node)
        if not d_start or not d_end:
            continue
        d_coords = [b_nodes[ref] for ref in dw['nd_refs'] if ref in b_nodes]

        best_aid, best_score = None, -1
        for aid in sorted(added):
            if aid in used_added:
                continue
            aw = a_ways[aid]
            # 应用 merge_map 获取合并后端点坐标
            a_start_node = apply_merge(aw['start'], merge_map)
            a_end_node = apply_merge(aw['end'], merge_map)
            a_start = a_nodes.get(a_start_node) or b_nodes.get(a_start_node)
            a_end = a_nodes.get(a_end_node) or b_nodes.get(a_end_node)
            if not a_start or not a_end:
                continue

            # 起终点距离（正向或反向）
            dist_fwd = max(_haversine(d_start, a_start), _haversine(d_end, a_end))
            dist_rev = max(_haversine(d_start, a_end), _haversine(d_end, a_start))
            endpoint_dist = min(dist_fwd, dist_rev)
            if endpoint_dist > REPLACE_DIST_THRESH:
                continue

            # 几何重合度
            a_coords = [a_nodes[ref] for ref in aw['nd_refs'] if ref in a_nodes]
            overlap = _polyline_overlap(d_coords, a_coords)
            if overlap < GEO_OVERLAP_THRESH:
                continue

            score = overlap - endpoint_dist / 1000  # 综合评分
            if score > best_score:
                best_score, best_aid = score, aid

        if best_aid is not None:
            replacements[did] = best_aid
            used_added.add(best_aid)
    return replacements


def _resample_polyline(coords, step=5):
    """沿折线每 step 米均匀重采样，返回采样点列表 [(lat, lon), ...]"""
    if len(coords) < 2:
        return list(coords)
    samples = [coords[0]]
    carry = 0  # 上个线段剩余距离
    for i in range(len(coords) - 1):
        p1, p2 = coords[i], coords[i + 1]
        seg_len = _haversine(p1, p2)
        if seg_len < 1e-9:
            continue
        # 在这个线段上从 carry 处开始按 step 采样
        d = step - carry
        while d <= seg_len:
            ratio = d / seg_len
            lat = p1[0] + (p2[0] - p1[0]) * ratio
            lon = p1[1] + (p2[1] - p1[1]) * ratio
            samples.append((lat, lon))
            d += step
        carry = seg_len - (d - step)
    # 确保末端点
    if _haversine(samples[-1], coords[-1]) > 1:
        samples.append(coords[-1])
    return samples


def _point_to_segment_distance(pt, seg_start, seg_end):
    """点到线段的最短距离（米），基于投影。
    先将经纬度近似转为局部米制坐标做投影，再算距离。"""
    # 以 seg_start 为原点，转局部坐标（米）
    cos_lat = math.cos(math.radians(pt[0]))
    M_PER_DEG_LAT = 111320
    M_PER_DEG_LON = 111320 * cos_lat

    px = (pt[1] - seg_start[1]) * M_PER_DEG_LON
    py = (pt[0] - seg_start[0]) * M_PER_DEG_LAT
    ax, ay = 0, 0
    bx = (seg_end[1] - seg_start[1]) * M_PER_DEG_LON
    by = (seg_end[0] - seg_start[0]) * M_PER_DEG_LAT

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        return math.sqrt(px * px + py * py)

    t = max(0, min(1, (px * dx + py * dy) / seg_len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)


def _point_to_polyline_distance(pt, polyline):
    """点到折线的最短垂直距离（米）"""
    min_d = float('inf')
    for i in range(len(polyline) - 1):
        d = _point_to_segment_distance(pt, polyline[i], polyline[i + 1])
        if d < min_d:
            min_d = d
    return min_d


def _polyline_deviation(coords_a, coords_b, step=5):
    """计算两条折线的偏差（米）：重采样后每个采样点到对方折线的垂直距离。
    返回 (mean_dev, p90_dev, max_dev)。
    双向计算取较大值，确保不遗漏任何方向的偏差。"""
    if not coords_a or not coords_b or len(coords_a) < 2 or len(coords_b) < 2:
        return 9999, 9999, 9999

    def _one_direction(src_coords, tgt_coords):
        samples = _resample_polyline(src_coords, step)
        dists = [_point_to_polyline_distance(pt, tgt_coords) for pt in samples]
        dists.sort()
        n = len(dists)
        mean_d = sum(dists) / n
        p90_d = dists[int(n * 0.9)] if n >= 2 else dists[-1]
        max_d = dists[-1]
        return mean_d, p90_d, max_d

    m1, p1, x1 = _one_direction(coords_a, coords_b)
    m2, p2, x2 = _one_direction(coords_b, coords_a)
    return max(m1, m2), max(p1, p2), max(x1, x2)


def compute_path_similarity(lost_ids, gain_ids, b_ways, a_ways, b_nodes, a_nodes):
    """评估 lost/gain 路径对的相似度。
    返回 (mean_dev_m, p90_dev_m, attr_consistent, is_similar)。
    - mean_dev_m: 重采样后平均垂直偏差（米）
    - p90_dev_m: 90分位垂直偏差（米）
    - attr_consistent: 属性是否完全一致（bool）
    - is_similar: 综合判定是否为高匹配替换（无需核实）
    """
    if not lost_ids or not gain_ids:
        return 9999, 9999, False, False

    # 几何：重采样 + 垂直距离
    lost_coords = []
    for w in lost_ids:
        if w in b_ways:
            lost_coords.extend([b_nodes[ref] for ref in b_ways[w]['nd_refs'] if ref in b_nodes])
    gain_coords = []
    for w in gain_ids:
        if w in a_ways:
            gain_coords.extend([a_nodes[ref] for ref in a_ways[w]['nd_refs'] if ref in a_nodes])

    mean_dev, p90_dev, max_dev = _polyline_deviation(lost_coords, gain_coords, step=5)

    # 道路属性一致性：FuncClass 和 kindclass 集合必须完全相同
    lost_funcs = sorted(set(b_ways[w]['tags'].get('FuncClass', '?') for w in lost_ids if w in b_ways))
    gain_funcs = sorted(set(a_ways[w]['tags'].get('FuncClass', '?') for w in gain_ids if w in a_ways))
    lost_kinds = sorted(set(b_ways[w]['tags'].get('kindclass', '?') for w in lost_ids if w in b_ways))
    gain_kinds = sorted(set(a_ways[w]['tags'].get('kindclass', '?') for w in gain_ids if w in a_ways))

    attr_consistent = (lost_funcs == gain_funcs) and (lost_kinds == gain_kinds)

    # 综合判定：平均偏差 < 5m 且 P90 < 10m 且属性一致 → 高匹配
    is_similar = mean_dev < 5 and p90_dev < 10 and attr_consistent

    return mean_dev, p90_dev, attr_consistent, is_similar


def _union_find_groups(items, get_changed_set):
    """用 Union-Find 按 changed_in_paths 交集做连通分量聚类。
    items: list of dicts, get_changed_set: item -> set of changed link ids
    返回: list of list of items (每组一个 list)
    """
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 如果两个 entry 共享任何变更 link，则合并
    sets = [get_changed_set(item) for item in items]
    for i in range(n):
        for j in range(i + 1, n):
            if sets[i] & sets[j]:
                union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(items[i])
    return list(groups.values())


# ── 主逻辑 ──────────────────────────────────────────────

def run_analysis(task_code, before_file, after_file):
    """运行连通性分析，返回 (html_str, summary_dict)"""
    TASK_CODE = task_code
    BEFORE_FILE = before_file
    AFTER_FILE = after_file

    b_nodes, b_ways = parse_all(BEFORE_FILE)
    a_nodes, a_ways = parse_all(AFTER_FILE)

    # 识别 intersection link 并合并节点（双向交叉点内link等价于单点）
    b_merge_map, b_intersection_links = find_intersection_merges(b_ways)
    a_merge_map, a_intersection_links = find_intersection_merges(a_ways)
    # 合并两侧的映射（确保同一节点在 before/after 中一致）
    all_merge_map = {**b_merge_map, **a_merge_map}
    all_intersection_links = b_intersection_links | a_intersection_links
    if all_intersection_links:
        pass  # intersection links count included in summary

    G_before = build_graph(b_ways, all_merge_map)
    G_after = build_graph(a_ways, all_merge_map)

    before_ids = set(b_ways.keys())
    after_ids = set(a_ways.keys())
    # 蝴蝶型场景：交叉点内link不算真正的"新增/删除"，它们等价于节点
    deleted = (before_ids - after_ids) - all_intersection_links
    added = (after_ids - before_ids) - all_intersection_links
    modified = {wid for wid in before_ids & after_ids
                if a_ways[wid]['task_code'] == TASK_CODE}
    changed_ids = deleted | added | modified
    # 识别"节点重整型 modified"：起终点偏移 < 阈值且方向相同，不应作为根因
    trivial_modified = set()
    for wid in modified:
        bw, aw = b_ways[wid], a_ways[wid]
        if bw['direction'] != aw['direction']:
            continue
        bs = b_nodes.get(bw['start']); ae = a_nodes.get(aw['start'])
        be = b_nodes.get(bw['end']); af = a_nodes.get(aw['end'])
        if not (bs and ae and be and af):
            continue
        if _haversine(bs, ae) <= REPLACE_DIST_THRESH and _haversine(be, af) <= REPLACE_DIST_THRESH:
            trivial_modified.add(wid)

    # 自动检测替换对（删除重绘）
    LINK_REPLACEMENTS = auto_detect_replacements(deleted, added, b_ways, a_ways, b_nodes, a_nodes,
                                                 merge_map=all_merge_map)
    replacement_ids = set(LINK_REPLACEMENTS.keys()) | set(LINK_REPLACEMENTS.values())

    affected_nodes = set()
    for wid in changed_ids:
        for ways in [b_ways, a_ways]:
            if wid in ways:
                affected_nodes.add(apply_merge(ways[wid]['start'], all_merge_map))
                affected_nodes.add(apply_merge(ways[wid]['end'], all_merge_map))

    entry_links = {}
    exit_links = {}
    for wid, w in a_ways.items():
        if wid in changed_ids or w['tags'].get('link-intersection') == 'yes':
            continue
        s, e, d = w['start'], w['end'], w['direction']
        # 用合并后的节点检测是否与变更区域相邻
        sm, em = apply_merge(s, all_merge_map), apply_merge(e, all_merge_map)
        if d == 1:
            if em in affected_nodes: entry_links[wid] = w
            if sm in affected_nodes: exit_links[wid] = w
        elif d == 2:
            if em in affected_nodes: entry_links[wid] = w
            if sm in affected_nodes: exit_links[wid] = w
        elif d == 3:
            if sm in affected_nodes: entry_links[wid] = w
            if em in affected_nodes: exit_links[wid] = w

    # ── 检测连通性变化 ────────────────────
    all_pairs = []
    for eid, entry in entry_links.items():
        for xid, exitt in exit_links.items():
            if eid == xid:
                continue
            en = entry['end'] if entry['direction'] != 3 else entry['start']
            xn = exitt['start'] if exitt['direction'] != 3 else exitt['end']
            # 应用 intersection link 节点合并
            en = apply_merge(en, all_merge_map)
            xn = apply_merge(xn, all_merge_map)
            if en not in G_before or xn not in G_before:
                continue
            if en not in G_after or xn not in G_after:
                continue
            all_pairs.append((eid, entry, xid, exitt, en, xn))

    summary_changes = []
    for eid, entry, xid, exitt, en, xn in all_pairs:
        pb = get_edge_disjoint_paths(G_before, en, xn)
        pa = get_edge_disjoint_paths(G_after, en, xn)
        braw = [path_to_link_ids(G_before, p) for p in pb]
        araw = [path_to_link_ids(G_after, p) for p in pa]
        bnorm = [frozenset(normalize_path(ids, LINK_REPLACEMENTS)) for ids in braw]
        anorm = [frozenset(normalize_path(ids, LINK_REPLACEMENTS)) for ids in araw]
        changed = (len(bnorm) != len(anorm)) or any(b not in anorm for b in bnorm)
        if not changed:
            continue

        def extract_causal(lls, src):
            c = set()
            for path in lls:
                for w in path:
                    if w in src:
                        c.add(w)
            return frozenset(c)

        lost = [ids for ids, ns in zip(braw, bnorm) if ns not in anorm]
        gain = [ids for ids, ns in zip(araw, anorm) if ns not in bnorm]
        lost_indices = [i for i, ns in enumerate(bnorm) if ns not in anorm]
        gain_indices = [i for i, ns in enumerate(anorm) if ns not in bnorm]

        # 按 link 相似度配对 lost ↔ gain
        repl_pairs = []
        _remaining = list(gain_indices)
        for li in lost_indices:
            b_set = set(braw[li])
            best_gi, best_sim = None, -1
            for gi in _remaining:
                a_set = set(araw[gi])
                union = b_set | a_set
                sim = len(b_set & a_set) / len(union) if union else 0
                if sim > best_sim:
                    best_sim, best_gi = sim, gi
            if best_gi is not None:
                repl_pairs.append((li, best_gi))
                _remaining.remove(best_gi)

        causal = extract_causal(lost, deleted | modified) | extract_causal(gain, added | modified)
        causal -= replacement_ids
        causal -= trivial_modified  # 排除节点重整型 modified
        # 路径中涉及的变更 link：分两层（用于根因分层回退）
        #   changed_in_paths: 排除替换对+trivial_modified（强根因候���）
        #   changed_in_paths_full: 仅排除替换对（弱根因候选，含连带修改）
        changed_in_paths = set()
        changed_in_paths_full = set()
        for path in lost + gain:
            for w in path:
                if w in changed_ids and w not in replacement_ids:
                    changed_in_paths_full.add(w)
                    if w not in trivial_modified:
                        changed_in_paths.add(w)

        if not pb and pa:
            tag = "新通"; priority = "重点"
        elif pb and not pa:
            tag = "断通"; priority = "重点"
        elif len(pb) > len(pa):
            tag = f"通路减少({len(pb)}→{len(pa)})"; priority = "重点"
        elif len(pb) < len(pa):
            tag = f"通路增加({len(pb)}→{len(pa)})"; priority = "重点"
        else:
            tag = f"路径替换(共{len(pb)}条)"; priority = "普通"

        # 对路径替换和通路增加类，评估 lost/gain 相似度
        # 高匹配（几何+属性）→ "透出"；属性不一致或几何偏差大 → "重点"
        priority_reason = ""
        priority_confidence = 0
        if lost and gain and repl_pairs:
            all_similar = True
            has_mismatch = False
            reasons = []
            max_mean_dev = 0
            max_p90_dev = 0
            for li, gi in repl_pairs:
                mean_dev, p90_dev, attr_ok, sim = compute_path_similarity(
                    braw[li], araw[gi], b_ways, a_ways, b_nodes, a_nodes)
                max_mean_dev = max(max_mean_dev, mean_dev)
                max_p90_dev = max(max_p90_dev, p90_dev)
                if not sim:
                    all_similar = False
                if not attr_ok:
                    has_mismatch = True
                    # 具体说明哪些属性不一致
                    l_fc = sorted(set(b_ways[w]['tags'].get('FuncClass', '?') for w in braw[li] if w in b_ways))
                    g_fc = sorted(set(a_ways[w]['tags'].get('FuncClass', '?') for w in araw[gi] if w in a_ways))
                    l_kc = sorted(set(b_ways[w]['tags'].get('kindclass', '?') for w in braw[li] if w in b_ways))
                    g_kc = sorted(set(a_ways[w]['tags'].get('kindclass', '?') for w in araw[gi] if w in a_ways))
                    if l_fc != g_fc:
                        reasons.append(f"FuncClass变化({'/'.join(l_fc)}→{'/'.join(g_fc)})")
                    if l_kc != g_kc:
                        reasons.append(f"kindclass变化({'/'.join(l_kc)}→{'/'.join(g_kc)})")
                if p90_dev > 10:
                    has_mismatch = True
                    reasons.append(f"几何偏差(均值{mean_dev:.1f}m/P90={p90_dev:.1f}m)")
            if all_similar and priority != "重点":
                priority = "透出"
                priority_reason = f"几何高度吻合(均值偏差{max_mean_dev:.1f}m)且道路属性一致"
                priority_confidence = min(95, int(92 - max_mean_dev * 2))
            elif has_mismatch:
                priority = "重点"
                priority_reason = "；".join(reasons)
                # 置信度：属性不一致比几何偏差更确定
                if any('Class变化' in r for r in reasons):
                    priority_confidence = 90
                else:
                    priority_confidence = 75
        elif priority == "重点" and tag in ("新通", "断通"):
            priority_reason = "新增/断开通路"
            priority_confidence = 95
        elif priority == "重点" and "增加" in tag:
            priority_reason = "通路数量增加"
            priority_confidence = 90
        elif priority == "重点" and "减少" in tag:
            priority_reason = "通路数量减少"
            priority_confidence = 90
        elif priority == "普通" and not priority_reason:
            if "替换" in tag:
                priority_reason = "路径替换，相似度中等"
                priority_confidence = 60

        summary_changes.append({
            'eid': eid, 'xid': xid,
            'entry': entry, 'exitt': exitt,
            'tag': tag, 'priority': priority,
            'priority_reason': priority_reason,
            'priority_confidence': priority_confidence,
            'before_link_ids': braw,
            'after_link_ids': araw,
            'causal': causal,
            'changed_in_paths': changed_in_paths,
            'changed_in_paths_full': changed_in_paths_full,
            'lost_indices': lost_indices,
            'gain_indices': gain_indices,
            'replacement_pairs': repl_pairs,
        })

    # ── 分组（按出口 xid 分组，组级根因 = changed_in_paths 交集）──
    cause_groups = defaultdict(list)
    for item in summary_changes:
        cause_groups[item['xid']].append(item)
    # 每组确定优先级和根因
    sorted_groups_raw = []
    for xid, group_items in cause_groups.items():
        # 组级优先级：重点 > 普通 > 透出（取组内最高优先级）
        priorities = [it['priority'] for it in group_items]
        if '重点' in priorities:
            bucket = '重点'
        elif '普通' in priorities:
            bucket = '普通'
        else:
            bucket = '透出'
        # 组级根因：分层回退（仅使用主动变更 link，不含节点重整型 modified）
        # 1) 严格交集
        group_causal_set = set(group_items[0]['changed_in_paths'])
        for it in group_items[1:]:
            group_causal_set &= it['changed_in_paths']
        # 2) 多数票（出现在 >50% entry 中）
        if not group_causal_set and len(group_items) > 1:
            from collections import Counter
            link_freq = Counter()
            for it in group_items:
                for lid in it['changed_in_paths']:
                    link_freq[lid] += 1
            threshold = len(group_items) / 2
            group_causal_set = {lid for lid, cnt in link_freq.items() if cnt > threshold}
        # 若仍为空 → 该组变化由替换连锁导致，无独立根因
        sorted_groups_raw.append((bucket, xid, group_causal_set, group_items))
    # 排序：重点在前，组内 entry 数多的在前
    _prio_order = {'重点': 0, '普通': 1, '透出': 2}
    sorted_groups_raw.sort(key=lambda x: (_prio_order.get(x[0], 9), -len(x[3]), x[1]))
    # 转换为兼容原有结构
    sorted_groups = [((bucket, xid), items) for bucket, xid, _, items in sorted_groups_raw]
    # 保存组级根因供后续使用
    group_causal_sets = {i: sorted_groups_raw[i][2] for i in range(len(sorted_groups_raw))}

    # ── 构建 JSON 数据 ────────────────────

    # 1) 变更前全量路网
    before_network = []
    before_node_set = set()
    for wid, w in b_ways.items():
        coords = way_coords(w, b_nodes)
        if len(coords) >= 2:
            before_network.append({
                'id': wid,
                'coords': coords,
                'name': w['name'],
            })
            before_node_set.add(w['start'])
            before_node_set.add(w['end'])

    before_node_coords = []
    for nid in before_node_set:
        if nid in b_nodes:
            before_node_coords.append({'id': nid, 'coord': list(b_nodes[nid])})

    # 2) 变更后全量路网
    after_network = []
    after_node_set = set()
    for wid, w in a_ways.items():
        coords = way_coords(w, a_nodes)
        if len(coords) >= 2:
            is_changed = wid in changed_ids
            after_network.append({
                'id': wid,
                'coords': coords,
                'name': w['name'],
                'changed': is_changed,
            })
            after_node_set.add(w['start'])
            after_node_set.add(w['end'])

    after_node_data = []
    for nid in after_node_set:
        if nid in a_nodes:
            after_node_data.append({
                'id': nid,
                'coord': list(a_nodes[nid]),
                'changed': nid in affected_nodes,
            })

    # 3) 每组的路径数据
    groups_data = []
    for gidx, ((bucket, exit_id), items) in enumerate(sorted_groups, 1):
        if bucket == '重点':
            priority_label = '🔴 重点核实'
        elif bucket == '普通':
            priority_label = '🟡 需核实'
        else:
            priority_label = '🟢 仅透出'
        exit_name = items[0]['exitt'].get('name', '')

        # 组级根因 = changed_in_paths 交集（已在分组阶段计算）
        group_causal = sorted(group_causal_sets.get(gidx - 1, set()))

        group_entries = []
        for item in items:
            entry_name = item['entry'].get('name', '')

            # before 路径坐标 + link ids
            before_paths = []
            for path_links_ids in item['before_link_ids']:
                full_ids = [item['eid']] + path_links_ids + [item['xid']]
                path_coords_all = []
                for lid in full_ids:
                    w = b_ways.get(lid)
                    if w:
                        c = way_coords(w, b_nodes)
                        if c:
                            path_coords_all.append({
                                'link_id': lid,
                                'coords': c,
                                'changed': lid in changed_ids,
                                'direction': w['direction'],
                                'is_entry_exit': lid == item['eid'] or lid == item['xid'],
                            })
                before_paths.append(path_coords_all)

            # 构建 before/after 完整 ID 列表，用于逐 link 对比
            before_full_ids_list = [[item['eid']] + pl + [item['xid']]
                                    for pl in item['before_link_ids']]
            after_full_ids_list = [[item['eid']] + pl + [item['xid']]
                                   for pl in item['after_link_ids']]

            unchanged_after_indices = set()
            for i in range(len(after_full_ids_list)):
                if i < len(before_full_ids_list) and before_full_ids_list[i] == after_full_ids_list[i]:
                    unchanged_after_indices.add(i)

            # after 路径坐标 + color_hint（列表用）+ 原始标记（地图用）
            after_paths = []
            for ai, path_links_ids in enumerate(item['after_link_ids']):
                full_ids = after_full_ids_list[ai]
                is_unchanged = ai in unchanged_after_indices
                # 计算逐 link 颜色（供侧边栏列表）
                if is_unchanged:
                    hints = ['green'] * len(full_ids)
                elif ai < len(before_full_ids_list):
                    hints = compute_color_hints(before_full_ids_list[ai], full_ids)
                else:
                    hints = ['red'] * len(full_ids)

                path_coords_all = []
                for pos, lid in enumerate(full_ids):
                    w = a_ways.get(lid)
                    if w:
                        c = way_coords(w, a_nodes)
                        if c:
                            path_coords_all.append({
                                'link_id': lid,
                                'coords': c,
                                'changed': lid in changed_ids,
                                'is_causal': lid in group_causal,
                                'direction': w['direction'],
                                'is_entry_exit': lid == item['eid'] or lid == item['xid'],
                                'is_unchanged_pair': is_unchanged,
                                'color_hint': hints[pos] if pos < len(hints) else 'red',
                            })
                after_paths.append(path_coords_all)

            # 断通/新通场景：after_paths 为空时，补充渲染 entry/exit link（如果它们仍存在）
            if not after_paths:
                stub_path = []
                for lid in [item['eid'], item['xid']]:
                    w = a_ways.get(lid)
                    if w:
                        c = way_coords(w, a_nodes)
                        if c:
                            stub_path.append({
                                'link_id': lid,
                                'coords': c,
                                'changed': lid in changed_ids,
                                'is_causal': lid in group_causal,
                                'direction': w['direction'],
                                'is_entry_exit': True,
                                'is_unchanged_pair': False,
                                'color_hint': 'green',
                            })
                if stub_path:
                    after_paths.append(stub_path)

            # 路径涉及的节点（带坐标 + 是否变更）
            path_nodes_before = set()
            for pl in item['before_link_ids']:
                for lid in [item['eid']] + pl + [item['xid']]:
                    if lid in b_ways:
                        path_nodes_before.add(b_ways[lid]['start'])
                        path_nodes_before.add(b_ways[lid]['end'])

            path_nodes_after = set()
            for pl in item['after_link_ids']:
                for lid in [item['eid']] + pl + [item['xid']]:
                    if lid in a_ways:
                        path_nodes_after.add(a_ways[lid]['start'])
                        path_nodes_after.add(a_ways[lid]['end'])
            # 断通场景补充 entry/exit 节点
            for lid in [item['eid'], item['xid']]:
                if lid in a_ways:
                    path_nodes_after.add(a_ways[lid]['start'])
                    path_nodes_after.add(a_ways[lid]['end'])

            entry_exit_nodes = set()
            for wid in [item['eid'], item['xid']]:
                if wid in a_ways:
                    entry_exit_nodes.add(a_ways[wid]['start'])
                    entry_exit_nodes.add(a_ways[wid]['end'])
                if wid in b_ways:
                    entry_exit_nodes.add(b_ways[wid]['start'])
                    entry_exit_nodes.add(b_ways[wid]['end'])

            before_pn = [{'id': nid, 'coord': list(b_nodes[nid]),
                          'changed': nid in affected_nodes,
                          'is_entry_exit': nid in entry_exit_nodes}
                         for nid in path_nodes_before if nid in b_nodes]
            after_pn = [{'id': nid, 'coord': list(a_nodes[nid]),
                         'changed': nid in affected_nodes,
                         'is_entry_exit': nid in entry_exit_nodes}
                        for nid in path_nodes_after if nid in a_nodes]

            # 路径替换对数据 — 逐对计算 prefix/suffix color_hint
            is_repl = item['tag'].startswith('路径替换')
            pair_data = []
            kept_count = 0
            if is_repl:
                for pi, (li, gi) in enumerate(item.get('replacement_pairs', [])):
                    bp = before_paths[li] if li < len(before_paths) else []
                    b_pair_ids = before_full_ids_list[li] if li < len(before_full_ids_list) else []
                    a_pair_ids = after_full_ids_list[gi] if gi < len(after_full_ids_list) else []
                    pair_hints = compute_color_hints(b_pair_ids, a_pair_ids)

                    # 用 pair 专属 color_hint 重建 after path 段
                    ap = []
                    for pos, lid in enumerate(a_pair_ids):
                        w = a_ways.get(lid)
                        if w:
                            c = way_coords(w, a_nodes)
                            if c:
                                ap.append({
                                    'link_id': lid,
                                    'coords': c,
                                    'changed': lid in changed_ids,
                                    'direction': w['direction'],
                                    'color_hint': pair_hints[pos] if pos < len(pair_hints) else 'red',
                                })
                    # 该对涉及的节点
                    pnb = set()
                    for seg in bp:
                        lid = seg['link_id']
                        if lid in b_ways:
                            pnb.add(b_ways[lid]['start']); pnb.add(b_ways[lid]['end'])
                    pna = set()
                    for seg in ap:
                        lid = seg['link_id']
                        if lid in a_ways:
                            pna.add(a_ways[lid]['start']); pna.add(a_ways[lid]['end'])
                    pair_data.append({
                        'pair_idx': pi,
                        'before_path': bp,
                        'after_path': ap,
                        'before_link_ids': b_pair_ids,
                        'after_link_ids': a_pair_ids,
                        'before_nodes': [{'id': nid, 'coord': list(b_nodes[nid]),
                                          'changed': nid in affected_nodes}
                                         for nid in pnb if nid in b_nodes],
                        'after_nodes': [{'id': nid, 'coord': list(a_nodes[nid]),
                                         'changed': nid in affected_nodes}
                                        for nid in pna if nid in a_nodes],
                    })
                kept_count = len(before_paths) - len(item.get('lost_indices', []))

            # 构建完整 way_id 路径（含 entry/exit），用于弹窗详情
            before_full_ids = []
            for path_links_ids in item['before_link_ids']:
                before_full_ids.append([item['eid']] + path_links_ids + [item['xid']])
            after_full_ids = []
            for path_links_ids in item['after_link_ids']:
                after_full_ids.append([item['eid']] + path_links_ids + [item['xid']])

            group_entries.append({
                'eid': item['eid'],
                'xid': item['xid'],
                'entry_name': entry_name,
                'tag': item['tag'],
                'entry_priority': item['priority'],
                'priority_reason': item.get('priority_reason', ''),
                'priority_confidence': item.get('priority_confidence', 0),
                'before_paths': before_paths,
                'after_paths': after_paths,
                'before_nodes': before_pn,
                'after_nodes': after_pn,
                'is_replacement': is_repl,
                'pairs': pair_data,
                'kept_count': kept_count,
                'before_full_ids': before_full_ids,
                'after_full_ids': after_full_ids,
                'causal': sorted(item.get('causal', [])) if item.get('causal') else [],
                'unchanged_after_indices': sorted(unchanged_after_indices),
            })

        groups_data.append({
            'gidx': gidx,
            'priority': priority_label,
            'bucket': bucket,
            'exit_id': exit_id,
            'exit_name': exit_name,
            'count': len(items),
            'causal_links': group_causal,
            'entries': group_entries,
        })

    # ── 计算地图中心和范围 ────────────────
    all_lats = [c[0] for c in b_nodes.values()] + [c[0] for c in a_nodes.values()]
    all_lons = [c[1] for c in b_nodes.values()] + [c[1] for c in a_nodes.values()]
    center = [(min(all_lats) + max(all_lats)) / 2, (min(all_lons) + max(all_lons)) / 2]
    bounds = [[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]]

    # ── 构建全部变更清单（供前端"查看全部变更"按钮） ──
    changes_list = []
    shown_ids = set()
    for old_id, new_id in LINK_REPLACEMENTS.items():
        changes_list.append({'type': '替换', 'before': old_id, 'after': new_id})
        shown_ids.update([old_id, new_id])
    for wid in sorted(deleted - shown_ids):
        changes_list.append({'type': '删除', 'before': wid, 'after': None})
        shown_ids.add(wid)
    for wid in sorted(added - shown_ids):
        changes_list.append({'type': '新增', 'before': None, 'after': wid})
        shown_ids.add(wid)
    for wid in sorted(modified - all_intersection_links):
        changes_list.append({'type': '修改', 'before': wid, 'after': wid})
        shown_ids.add(wid)
    for wid in sorted(all_intersection_links & (before_ids | after_ids)):
        is_new = wid not in before_ids
        is_del = wid not in after_ids
        is_mod = wid in modified
        suffix = '新增' if is_new else ('删除' if is_del else ('修改' if is_mod else ''))
        label = f"交叉点内link{'·' + suffix if suffix else ''}"
        changes_list.append({'type': label,
                             'before': wid if wid in before_ids else None,
                             'after': wid if wid in after_ids else None})

    map_data = {
        'center': center,
        'bounds': bounds,
        'before_network': before_network,
        'before_nodes': before_node_coords,
        'after_network': after_network,
        'after_nodes': after_node_data,
        'groups': groups_data,
        'task_code': TASK_CODE,
        'changes': changes_list,
    }

    # ── 生成 HTML ────────────────────────
    html = generate_html(map_data)

    # 汇总信息
    summary = {
        'before_links': len(before_network),
        'before_nodes': len(before_node_coords),
        'after_links': len(after_network),
        'replacements': len(LINK_REPLACEMENTS),
        'replacement_pairs': {old_id: new_id for old_id, new_id in LINK_REPLACEMENTS.items()},
        'groups': len(groups_data),
        'group_details': [],
        'intersection_links': len(all_intersection_links),
    }
    for gidx, ((bucket, xid), items) in enumerate(sorted_groups, 1):
        causal = sorted(group_causal_sets.get(gidx - 1, set()))
        summary['group_details'].append({
            'gidx': gidx, 'bucket': bucket, 'xid': xid,
            'entry_count': len(items), 'causal': causal,
        })

    return html, summary


def generate_html(data):
    data_json = json.dumps(data, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>路网连通性变更可视化 - {data["task_code"]}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-polylinedecorator@1.6.0/dist/leaflet.polylineDecorator.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif; background: #f5f5f5; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }}
.header {{ background: #1a1a2e; color: white; padding: 12px 20px; flex-shrink: 0; }}
.header h1 {{ font-size: 16px; font-weight: 500; }}
.header .sub {{ font-size: 12px; color: #aaa; margin-top: 2px; }}
.main {{ display: flex; flex: 1; overflow: hidden; }}
.sidebar {{ width: 340px; background: white; overflow-y: auto; border-right: 1px solid #ddd;
            flex-shrink: 0; font-size: 13px; }}
.sidebar-section {{ border-bottom: 1px solid #eee; }}
.sidebar-section .sec-title {{
    padding: 10px 14px; font-weight: 600; font-size: 13px;
    cursor: pointer; display: flex; justify-content: space-between; align-items: center;
    user-select: none;
}}
.sidebar-section .sec-title:hover {{ background: #f9f9f9; }}
.sidebar-section .sec-body {{ padding: 0 14px 10px; }}
.group-header {{
    padding: 8px 14px; font-weight: 600; font-size: 12px;
    cursor: pointer; display: flex; justify-content: space-between; align-items: center;
    border-top: 1px solid #eee;
}}
.group-header.critical {{ background: #fff5f5; color: #c62828; }}
.group-header.normal {{ background: #fffde7; color: #f57c00; }}
.group-header.info {{ background: #f1f8e9; color: #558b2f; }}
.group-body {{ padding: 4px 14px 8px; }}
.entry-item {{
    padding: 5px 8px; margin: 3px 0; border-radius: 4px;
    cursor: pointer; font-size: 12px; border-left: 3px solid #ddd;
}}
.entry-item:hover {{ background: #e3f2fd; }}
.entry-item.active {{ background: #bbdefb; border-left-color: #1976d2; }}
.entry-tag {{ font-weight: 600; }}
.entry-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px; }}
.priority-reason {{ font-size: 11px; color: #555; margin: 2px 0 4px 0; padding: 2px 6px; background: #f5f5f5; border-radius: 3px; line-height: 1.4; }}
.priority-reason .confidence {{ color: #888; font-size: 10px; }}
.group-mode-bar {{ display: flex; align-items: center; gap: 6px; padding: 8px 10px; background: #e8eaf6; border-bottom: 1px solid #c5cae9; font-size: 13px; position: sticky; top: 0; z-index: 10; }}
.mode-btn {{ padding: 3px 10px; border: 1px solid #7986cb; border-radius: 4px; background: #fff; cursor: pointer; font-size: 12px; }}
.mode-btn.active {{ background: #3f51b5; color: #fff; border-color: #3f51b5; }}
.review-summary {{ padding: 10px; background: #fff3e0; border-bottom: 1px solid #ffe0b2; font-size: 13px; }}
.no-critical-msg {{ padding: 20px; text-align: center; color: #2e7d32; font-size: 14px; background: #e8f5e9; border-bottom: 1px solid #c8e6c9; }}
.changes-toggle-bar {{ padding: 8px 10px; background: #f5f5f5; border-bottom: 1px solid #e0e0e0; }}
.changes-toggle-btn {{ width: 100%; padding: 6px 12px; border: 1px solid #bdbdbd; border-radius: 4px; background: #fff; cursor: pointer; font-size: 12px; color: #424242; }}
.changes-toggle-btn:hover {{ background: #eeeeee; }}
.changes-toggle-btn.active {{ background: #e3f2fd; border-color: #1976d2; color: #1976d2; }}
.changes-panel {{ padding: 6px 10px; max-height: 400px; overflow-y: auto; }}
.change-item {{ padding: 4px 8px; margin: 2px 0; border-radius: 3px; font-size: 11px; display: flex; align-items: center; gap: 6px; cursor: pointer; border-left: 3px solid #ddd; }}
.change-item:hover {{ background: #e3f2fd; }}
.change-item .change-type {{ font-weight: 600; min-width: 56px; font-size: 10px; padding: 1px 4px; border-radius: 2px; text-align: center; }}
.change-type.ct-replace {{ background: #fff3e0; color: #e65100; }}
.change-type.ct-delete {{ background: #ffebee; color: #c62828; }}
.change-type.ct-add {{ background: #e8f5e9; color: #2e7d32; }}
.change-type.ct-modify {{ background: #e3f2fd; color: #1565c0; }}
.change-type.ct-intersection {{ background: #f3e5f5; color: #7b1fa2; }}
.change-link-ids {{ font-family: monospace; font-size: 11px; }}
.locate-btn {{ padding: 1px 6px; margin-left: 6px; border: 1px solid #999; border-radius: 3px; background: #fff; cursor: pointer; font-size: 11px; vertical-align: middle; }}
.detail-btn {{
    font-size: 11px; padding: 2px 8px; border: 1px solid #1976d2;
    background: #e3f2fd; color: #1976d2; border-radius: 3px; cursor: pointer;
}}
.detail-btn:hover {{ background: #1976d2; color: white; }}
.pair-list {{ margin: 2px 0 4px 12px; }}
.pair-item {{
    padding: 4px 8px; margin: 2px 0; border-radius: 4px;
    cursor: pointer; font-size: 11px; border-left: 3px solid #e0e0e0;
    background: #fafafa;
}}
.pair-item:hover {{ background: #e8eaf6; }}
.pair-item.active {{ background: #c5cae9; border-left-color: #3f51b5; }}
.pair-title {{ font-weight: 600; color: #333; margin-bottom: 2px; }}
.pair-before {{ color: #2e7d32; }}
.pair-after {{ color: #d32f2f; }}
.kept-info {{ font-size: 11px; color: #999; padding: 2px 8px; margin-left: 12px; }}
.path-breakdown {{ margin: 4px 0 6px 12px; border: 1px solid #e8eaf6; border-radius: 4px; overflow: hidden; }}
.pb-section {{ padding: 4px 8px; }}
.pb-section + .pb-section {{ border-top: 1px solid #e8eaf6; }}
.pb-title {{ font-size: 10px; font-weight: 600; color: #666; margin-bottom: 3px; text-transform: uppercase; }}
.pb-row {{ font-size: 11px; padding: 2px 0; line-height: 1.4; cursor: pointer; }}
.pb-row:hover {{ background: #f5f5f5; }}
.pb-before {{ color: #2e7d32; }}
.pb-after {{ color: #d32f2f; }}
.pb-kept {{ color: #666; font-style: italic; }}
.switch-row {{
    display: flex; align-items: center; gap: 8px; padding: 4px 0;
}}
.switch-row label {{ font-size: 12px; color: #555; cursor: pointer; }}
.toggle {{ position: relative; width: 36px; height: 20px; }}
.toggle input {{ display: none; }}
.toggle .slider {{
    position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: #ccc; border-radius: 10px; cursor: pointer; transition: .2s;
}}
.toggle .slider:before {{
    content: ""; position: absolute; width: 16px; height: 16px;
    left: 2px; bottom: 2px; background: white; border-radius: 50%; transition: .2s;
}}
.toggle input:checked + .slider {{ background: #43a047; }}
.toggle input:checked + .slider:before {{ transform: translateX(16px); }}
.legend {{ padding: 10px 14px; font-size: 11px; }}
.legend-item {{ display: flex; align-items: center; gap: 6px; margin: 3px 0; }}
.legend-line {{ width: 24px; height: 3px; border-radius: 2px; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; border: 2px solid; }}
#map {{ flex: 1; position: relative; }}
.map-controls {{ position: absolute; top: 10px; right: 10px; z-index: 1000; background: white; border-radius: 6px; box-shadow: 0 2px 10px rgba(0,0,0,0.15); max-width: 260px; font-size: 11px; }}
.map-controls .mc-section {{ border-bottom: 1px solid #eee; padding: 10px 14px; }}
.map-controls .mc-section:last-child {{ border-bottom: none; }}
.map-controls .mc-title {{ font-weight: 600; color: #333; margin-bottom: 6px; font-size: 12px; }}
.map-controls .switch-row {{ display: flex; align-items: center; gap: 8px; margin: 5px 0; }}
.map-controls .switch-row label {{ cursor: pointer; }}
.modal-overlay {{
    display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5); z-index: 9999; justify-content: center; align-items: center;
}}
.modal-overlay.active {{ display: flex; }}
.modal-box {{
    background: white; border-radius: 8px; width: 680px; max-height: 80vh;
    display: flex; flex-direction: column; box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}}
.modal-header {{
    padding: 14px 20px; border-bottom: 1px solid #eee;
    display: flex; justify-content: space-between; align-items: center;
}}
.modal-header h3 {{ font-size: 15px; font-weight: 600; }}
.modal-close {{
    font-size: 20px; color: #999; cursor: pointer; background: none; border: none;
}}
.modal-close:hover {{ color: #333; }}
.modal-body {{
    padding: 16px 20px; overflow-y: auto; font-size: 13px;
}}
.modal-section {{ margin-bottom: 14px; }}
.modal-section-title {{ font-weight: 600; color: #333; margin-bottom: 6px; font-size: 12px; }}
.modal-path {{ padding: 6px 10px; margin: 3px 0; background: #f5f5f5; border-radius: 4px; font-family: monospace; font-size: 12px; }}
.modal-path.before {{ border-left: 3px solid #2e7d32; }}
.modal-path.after {{ border-left: 3px solid #d32f2f; }}
.modal-causal {{ display: inline-block; padding: 2px 8px; margin: 2px 4px 2px 0;
    background: #ffebee; color: #c62828; border-radius: 3px; font-size: 12px; }}

/* 判读相关样式 */
.review-bar {{ display: flex; gap: 6px; margin-top: 4px; align-items: center; }}
.review-btn {{ font-size: 11px; padding: 2px 8px; border-radius: 3px; border: 1px solid #ddd; cursor: pointer; background: #fff; }}
.review-btn:hover {{ background: #f5f5f5; }}
.review-btn.correct {{ border-color: #43a047; color: #43a047; }}
.review-btn.correct.active {{ background: #43a047; color: white; }}
.review-btn.wrong {{ border-color: #e53935; color: #e53935; }}
.review-btn.wrong.active {{ background: #e53935; color: white; }}
.review-note {{ font-size: 11px; color: #666; margin-left: 4px; font-style: italic; }}

/* 历史记录面板 */
.history-panel {{ position: fixed; right: 0; top: 0; bottom: 0; width: 320px; background: white; box-shadow: -2px 0 10px rgba(0,0,0,0.15); z-index: 10000; transform: translateX(100%); transition: transform 0.25s; display: flex; flex-direction: column; }}
.history-panel.active {{ transform: translateX(0); }}
.history-header {{ padding: 14px 16px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }}
.history-header h3 {{ font-size: 14px; font-weight: 600; }}
.history-close {{ font-size: 18px; color: #999; cursor: pointer; background: none; border: none; }}
.history-body {{ flex: 1; overflow-y: auto; padding: 10px 14px; }}
.history-item {{ padding: 10px 12px; margin: 6px 0; border-radius: 6px; border: 1px solid #eee; cursor: pointer; font-size: 12px; }}
.history-item:hover {{ background: #f9f9f9; border-color: #1976d2; }}
.history-item .h-title {{ font-weight: 600; color: #333; margin-bottom: 3px; display: flex; justify-content: space-between; }}
.history-item .h-meta {{ color: #999; font-size: 11px; }}
.history-item .h-note {{ color: #666; font-size: 11px; margin-top: 4px; font-style: italic; }}
.history-item .h-actions {{ display: flex; gap: 6px; margin-top: 6px; }}
.history-item .h-actions button {{ font-size: 11px; padding: 2px 8px; border: 1px solid #ddd; border-radius: 3px; background: white; cursor: pointer; }}
.history-item .h-actions button:hover {{ background: #f5f5f5; }}
.history-item .h-actions .btn-del {{ color: #e53935; border-color: #ffcdd2; }}
.history-item .h-actions .btn-del:hover {{ background: #ffebee; }}
.history-empty {{ text-align: center; color: #999; padding: 40px 20px; font-size: 13px; }}
.history-toolbar {{ padding: 10px 14px; border-top: 1px solid #eee; display: flex; gap: 8px; }}
.history-toolbar button {{ flex: 1; padding: 6px 0; font-size: 12px; border: 1px solid #1976d2; border-radius: 4px; background: #1976d2; color: white; cursor: pointer; }}
.history-toolbar button:hover {{ background: #1565c0; }}
.history-toolbar button.secondary {{ background: white; color: #1976d2; }}
.history-toolbar button.secondary:hover {{ background: #e3f2fd; }}
.history-toggle {{ position: fixed; right: 16px; bottom: 16px; z-index: 9998; padding: 10px 16px; background: #1976d2; color: white; border: none; border-radius: 20px; font-size: 12px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }}
.history-toggle:hover {{ background: #1565c0; }}

/* 根因标签 */
.causal-tag {{ display: inline-block; padding: 2px 6px; margin: 2px 4px 2px 0; background: #ffebee; color: #c62828; border-radius: 3px; font-size: 11px; font-weight: 600; }}
.group-causal-bar {{ padding: 6px 14px; font-size: 11px; color: #c62828; background: #ffebee; border-bottom: 1px solid #ffcdd2; }}

/* 判读弹窗 */
.review-modal-body {{ padding: 20px; }}
.review-modal-body .r-link-id {{ font-weight: 600; font-size: 14px; margin-bottom: 12px; color: #333; }}
.review-modal-body .r-options {{ display: flex; gap: 12px; margin-bottom: 14px; }}
.review-modal-body .r-option {{ flex: 1; padding: 10px; text-align: center; border: 2px solid #ddd; border-radius: 6px; cursor: pointer; font-size: 13px; }}
.review-modal-body .r-option:hover {{ border-color: #1976d2; }}
.review-modal-body .r-option.selected.correct {{ border-color: #43a047; background: #e8f5e9; }}
.review-modal-body .r-option.selected.wrong {{ border-color: #e53935; background: #ffebee; }}
.review-modal-body textarea {{ width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 12px; resize: vertical; min-height: 60px; }}
.review-modal-footer {{ padding: 12px 20px; border-top: 1px solid #eee; display: flex; justify-content: flex-end; gap: 8px; }}
.review-modal-footer button {{ padding: 6px 16px; font-size: 12px; border-radius: 4px; cursor: pointer; }}
.review-modal-footer .btn-primary {{ background: #1976d2; color: white; border: none; }}
.review-modal-footer .btn-secondary {{ background: white; color: #555; border: 1px solid #ddd; }}
</style>
</head>
<body>
<div class="header">
  <h1>路网连通性变更可视化</h1>
  <div class="sub">任务: {data["task_code"]}</div>
</div>

<div class="main">
  <div class="sidebar" id="sidebar"></div>
  <div id="map"><div class="map-controls" id="mapControls"></div></div>
</div>

<!-- 路径详情弹窗 -->
<div class="modal-overlay" id="pathModal" onclick="closePathModal(event)">
  <div class="modal-box" onclick="event.stopPropagation()">
    <div class="modal-header">
      <h3 id="modalTitle">路径详情</h3>
      <button class="modal-close" onclick="closePathModal()">&times;</button>
    </div>
    <div class="modal-body" id="modalBody"></div>
  </div>
</div>

<!-- 判读弹窗 -->
<div class="modal-overlay" id="reviewModal" onclick="closeReviewModal(event)">
  <div class="modal-box" onclick="event.stopPropagation()" style="width:420px;">
    <div class="modal-header">
      <h3>Link 判读</h3>
      <button class="modal-close" onclick="closeReviewModal()">&times;</button>
    </div>
    <div class="review-modal-body">
      <div class="r-link-id" id="reviewLinkId"></div>
      <div class="r-options">
        <div class="r-option correct" id="optCorrect" onclick="selectReview('correct')">✅ 正确</div>
        <div class="r-option wrong" id="optWrong" onclick="selectReview('wrong')">❌ 错误</div>
      </div>
      <textarea id="reviewNote" placeholder="备注（可选）：为什么判读为对/错？"></textarea>
    </div>
    <div class="review-modal-footer">
      <button class="btn-secondary" onclick="closeReviewModal()">取消</button>
      <button class="btn-primary" onclick="saveReview()">保存判读</button>
    </div>
  </div>
</div>

<!-- 历史记录面板 -->
<div class="history-panel" id="historyPanel">
  <div class="history-header">
    <h3>📋 检测历史</h3>
    <button class="history-close" onclick="toggleHistory()">&times;</button>
  </div>
  <div class="history-body" id="historyBody">
    <div class="history-empty">暂无历史记录</div>
  </div>
  <div class="history-toolbar">
    <button class="secondary" onclick="exportHistory()">📥 导出 CSV</button>
    <button onclick="loadHistoryFromServer()">🔄 同步服务端</button>
  </div>
</div>
<button class="history-toggle" onclick="toggleHistory()">📋 历史</button>

<script>
const DATA = {data_json};

// ── 地图初始化（高德底图，数据已是 GCJ02 坐标）──
const map = L.map('map', {{ zoomControl: true }});
L.tileLayer('https://webrd0{{s}}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={{x}}&y={{y}}&z={{z}}', {{
    maxNativeZoom: 18, maxZoom: 22, subdomains: '1234', attribution: '&copy; 高德地图'
}}).addTo(map);
map.fitBounds(DATA.bounds, {{ padding: [20, 20] }});

// ── 图层组 ──
const layers = {{
    beforeNetwork: L.layerGroup(),
    beforeNodes: L.layerGroup(),
    afterNetwork: L.layerGroup(),
    afterNodes: L.layerGroup(),
}};

// 每条进出口对的独立图层：entryLayers["gidx-idx"] = {{ before, after, nodes_before, nodes_after }}
const entryLayers = {{}};

// link 快速查找表
const linkLookup = {{}};
function buildLinkLookup() {{
    DATA.after_network.forEach(w => {{ linkLookup[w.id] = w; }});
    DATA.before_network.forEach(w => {{ if (!linkLookup[w.id]) linkLookup[w.id] = w; }});
}}
buildLinkLookup();

// ── 绘制变更前全量路网 ──
DATA.before_network.forEach(w => {{
    L.polyline(w.coords, {{
        color: '#333', weight: 2, opacity: 0.5,
    }}).bindTooltip(w.id + (w.name ? ' ' + w.name : ''), {{ sticky: true, className: 'link-tip' }})
      .addTo(layers.beforeNetwork);
}});
DATA.before_nodes.forEach(n => {{
    L.circleMarker(n.coord, {{
        radius: 3, color: '#333', fillColor: '#333', fillOpacity: 0.6, weight: 1,
    }}).bindTooltip(n.id, {{ sticky: true }}).addTo(layers.beforeNodes);
}});
layers.beforeNetwork.addTo(map);
layers.beforeNodes.addTo(map);

// ── 绘制变更后全量路网（默认不显示）──
DATA.after_network.forEach(w => {{
    const clr = w.changed ? '#c62828' : '#1565c0';
    const wt = w.changed ? 3 : 2;
    const op = w.changed ? 0.7 : 0.5;
    L.polyline(w.coords, {{
        color: clr, weight: wt, opacity: op,
    }}).bindTooltip(w.id + (w.name ? ' ' + w.name : '') + (w.changed ? ' [变更]' : ''), {{ sticky: true, className: 'link-tip' }})
      .addTo(layers.afterNetwork);
}});
DATA.after_nodes.forEach(n => {{
    const clr = n.changed ? '#c62828' : '#1565c0';
    L.circleMarker(n.coord, {{
        radius: 3, color: clr, fillColor: clr, fillOpacity: 0.6, weight: 1,
    }}).bindTooltip(n.id + (n.changed ? ' [变更]' : ''), {{ sticky: true }}).addTo(layers.afterNodes);
}});
// 默认不显示

// ── 为每条进出口对创建独立图层 ──
DATA.groups.forEach(g => {{
    g.entries.forEach((entry, idx) => {{
        const key = g.gidx + '-' + idx;
        const el = {{
            before_paths: [],
            after_paths: [],
            nodes_before: L.layerGroup(),
            nodes_after: L.layerGroup(),
        }};
        entryLayers[key] = el;

        // before 路径（每条路径独立图层，绿色 + 方向箭头）
        entry.before_paths.forEach(path => {{
            const pathLayer = L.layerGroup();
            path.forEach(seg => {{
                const coords = seg.coords;
                const reversed = [...coords].reverse();
                const dirLabel = seg.direction === 1 ? '↔' : seg.direction === 2 ? '→' : '←';
                const pl = L.polyline(coords, {{
                    color: '#2e7d32', weight: 4, opacity: 0.8,
                }}).bindTooltip('变更前: ' + seg.link_id + ' ' + dirLabel, {{ sticky: true }})
                  .addTo(pathLayer);
                if (seg.direction !== 3) {{
                    L.polylineDecorator(pl, {{
                        patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                            pixelSize: 7, polygon: false, pathOptions: {{ color: '#2e7d32', weight: 2, opacity: 0.9 }}
                        }}) }}]
                    }}).addTo(pathLayer);
                }}
                if (seg.direction === 3 || seg.direction === 1) {{
                    const plRev = L.polyline(reversed, {{ opacity: 0 }});
                    L.polylineDecorator(plRev, {{
                        patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                            pixelSize: 7, polygon: false, pathOptions: {{ color: '#2e7d32', weight: 2, opacity: 0.9 }}
                        }}) }}]
                    }}).addTo(pathLayer);
                }}
            }});
            el.before_paths.push(pathLayer);
        }});
        entry.before_nodes.forEach(n => {{
            L.circleMarker(n.coord, {{
                radius: 5, color: '#2e7d32', fillColor: '#2e7d32', fillOpacity: 0.7, weight: 2,
            }}).bindTooltip(n.id, {{ sticky: true }}).addTo(el.nodes_before);
        }});

        // after 路径（原始配色：根因=深红, 变更=粉色, 无变化对=绿, 其他=紫 + 方向箭头）
        entry.after_paths.forEach(path => {{
            const pathLayer = L.layerGroup();
            path.forEach(seg => {{
                const clr = seg.is_causal ? '#c62828' : (seg.changed ? '#ff1493' : (seg.is_unchanged_pair ? '#2e7d32' : '#8e24aa'));
                const wt = seg.is_causal ? 5 : (seg.changed ? 4 : (seg.is_entry_exit ? 5 : 4));
                const coords = seg.coords;
                const reversed = [...coords].reverse();
                const dirLabel = seg.direction === 1 ? '↔' : seg.direction === 2 ? '→' : '←';
                const tip = '变更后: ' + seg.link_id + ' ' + dirLabel + (seg.is_causal ? ' [根因]' : '') + (seg.changed ? ' [变更]' : '');
                const pl = L.polyline(coords, {{
                    color: clr, weight: wt, opacity: 0.85,
                }}).bindTooltip(tip, {{ sticky: true }})
                  .on('click', () => openReviewModal(seg.link_id))
                  .addTo(pathLayer);
                if (seg.direction !== 3) {{
                    L.polylineDecorator(pl, {{
                        patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                            pixelSize: 7, polygon: false, pathOptions: {{ color: clr, weight: 2, opacity: 0.9 }}
                        }}) }}]
                    }}).addTo(pathLayer);
                }}
                if (seg.direction === 3 || seg.direction === 1) {{
                    const plRev = L.polyline(reversed, {{ opacity: 0 }});
                    L.polylineDecorator(plRev, {{
                        patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                            pixelSize: 7, polygon: false, pathOptions: {{ color: clr, weight: 2, opacity: 0.9 }}
                        }}) }}]
                    }}).addTo(pathLayer);
                }}
            }});
            el.after_paths.push(pathLayer);
        }});
        entry.after_nodes.forEach(n => {{
            const clr = n.changed ? '#d32f2f' : (n.is_entry_exit ? '#8e24aa' : '#8e24aa');
            L.circleMarker(n.coord, {{
                radius: n.is_entry_exit ? 7 : 5, color: clr, fillColor: clr, fillOpacity: 0.7, weight: 2,
            }}).bindTooltip(n.id + (n.changed ? ' [变更]' : '') + (n.is_entry_exit ? ' [起终]' : ''), {{ sticky: true }})
              .addTo(el.nodes_after);
        }});

        // ── 替换对独立图层 ──
        if (entry.is_replacement && entry.pairs) {{
            el.pairs = {{}};
            entry.pairs.forEach(pair => {{
                const pKey = 'p' + pair.pair_idx;
                const pl = {{
                    before: L.layerGroup(),
                    after: L.layerGroup(),
                    nodes_before: L.layerGroup(),
                    nodes_after: L.layerGroup(),
                }};
                // 替换前路径（绿色）
                pair.before_path.forEach(seg => {{
                    const coords = seg.coords;
                    const reversed = [...coords].reverse();
                    const dirLabel = seg.direction === 1 ? '↔' : seg.direction === 2 ? '→' : '←';
                    const line = L.polyline(coords, {{
                        color: '#2e7d32', weight: 4, opacity: 0.8,
                    }}).bindTooltip('替换前: ' + seg.link_id + ' ' + dirLabel, {{ sticky: true }})
                      .addTo(pl.before);
                    if (seg.direction !== 3) {{
                        L.polylineDecorator(line, {{
                            patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                                pixelSize: 7, polygon: false, pathOptions: {{ color: '#2e7d32', weight: 2, opacity: 0.9 }}
                            }}) }}]
                        }}).addTo(pl.before);
                    }}
                    if (seg.direction === 3 || seg.direction === 1) {{
                        const rev = L.polyline(reversed, {{ opacity: 0 }});
                        L.polylineDecorator(rev, {{
                            patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                                pixelSize: 7, polygon: false, pathOptions: {{ color: '#2e7d32', weight: 2, opacity: 0.9 }}
                            }}) }}]
                        }}).addTo(pl.before);
                    }}
                }});
                // 替换后路径（原始配色）
                pair.after_path.forEach(seg => {{
                    const coords = seg.coords;
                    const reversed = [...coords].reverse();
                    const dirLabel = seg.direction === 1 ? '↔' : seg.direction === 2 ? '→' : '←';
                    const pClr = seg.changed ? '#ff1493' : '#8e24aa';
                    const pWt = seg.changed ? 5 : 4;
                    const line = L.polyline(coords, {{
                        color: pClr, weight: pWt, opacity: 0.85,
                    }}).bindTooltip('替换后: ' + seg.link_id + ' ' + dirLabel + (seg.changed ? ' [变更]' : ''), {{ sticky: true }})
                      .on('click', () => openReviewModal(seg.link_id))
                      .addTo(pl.after);
                    if (seg.direction !== 3) {{
                        L.polylineDecorator(line, {{
                            patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                                pixelSize: 7, polygon: false, pathOptions: {{ color: pClr, weight: 2, opacity: 0.9 }}
                            }}) }}]
                        }}).addTo(pl.after);
                    }}
                    if (seg.direction === 3 || seg.direction === 1) {{
                        const rev = L.polyline(reversed, {{ opacity: 0 }});
                        L.polylineDecorator(rev, {{
                            patterns: [{{ offset: '30%', repeat: '35%', symbol: L.Symbol.arrowHead({{
                                pixelSize: 7, polygon: false, pathOptions: {{ color: pClr, weight: 2, opacity: 0.9 }}
                            }}) }}]
                        }}).addTo(pl.after);
                    }}
                }});
                // 节点：before 节点全绿，after 节点按变更状态上色，分别放入独立图层
                pair.before_nodes.forEach(n => {{
                    L.circleMarker(n.coord, {{
                        radius: 5, color: '#2e7d32', fillColor: '#2e7d32', fillOpacity: 0.7, weight: 2,
                    }}).bindTooltip(n.id, {{ sticky: true }}).addTo(pl.nodes_before);
                }});
                pair.after_nodes.forEach(n => {{
                    const clr = n.changed ? '#d32f2f' : '#8e24aa';
                    L.circleMarker(n.coord, {{
                        radius: 5, color: clr, fillColor: clr, fillOpacity: 0.7, weight: 2,
                    }}).bindTooltip(n.id + (n.changed ? ' [变更]' : ''), {{ sticky: true }}).addTo(pl.nodes_after);
                }});
                el.pairs[pKey] = pl;
            }});
        }}
    }});
}});

// ── 侧边栏 ──
const sidebar = document.getElementById('sidebar');
let activeEntry = null;

// 全局开关
let showBefore = true, showAfter = true, showBaseNetwork = true, showAfterNetwork = false;

function buildMapControls() {{
    let html = '';
    // 图例
    html += `<div class="mc-section">
      <div class="mc-title">图例</div>
      <div class="legend-item"><div class="legend-line" style="background:#333"></div> 变更前路网</div>
      <div class="legend-item"><div class="legend-dot" style="border-color:#333;background:#333"></div> 变更前节点</div>
      <div class="legend-item"><div class="legend-line" style="background:#2e7d32"></div> 变更前 link 对路径</div>
      <div class="legend-item"><div class="legend-dot" style="border-color:#2e7d32;background:#2e7d32"></div> 变更前路径节点</div>
      <div class="legend-item"><div class="legend-line" style="background:#8e24aa"></div> 变更后未变更 link</div>
      <div class="legend-item"><div class="legend-line" style="background:#8e24aa;height:4px"></div> 变更后起终 link（进出口）</div>
      <div class="legend-item"><div class="legend-line" style="background:#c62828;height:4px"></div> 根因 link</div>
      <div class="legend-item"><div class="legend-line" style="background:#ff1493;height:4px"></div> 普通变更 link</div>
      <div class="legend-item"><div class="legend-dot" style="border-color:#d32f2f;background:#d32f2f"></div> 变更后变更节点</div>
      <div class="legend-item"><div class="legend-dot" style="border-color:#8e24aa;background:#8e24aa"></div> 起终节点</div>
      <div class="legend-item"><span style="font-size:14px">▶</span> 通行方向箭头 (↔双向 →正向 ←反向)</div>
    </div>`;
    // 图层开关
    html += `<div class="mc-section">
      <div class="mc-title">图层控制</div>
      <div class="switch-row">
        <label class="toggle"><input type="checkbox" id="sw-base" ${{showBaseNetwork ? 'checked' : ''}}
          onchange="toggleBaseNetwork(this.checked)"><span class="slider"></span></label>
        <label for="sw-base">变更前全量路网</label>
      </div>
      <div class="switch-row">
        <label class="toggle"><input type="checkbox" id="sw-after-net" ${{showAfterNetwork ? 'checked' : ''}}
          onchange="toggleAfterNetwork(this.checked)"><span class="slider"></span></label>
        <label for="sw-after-net">变更后全量路网</label>
      </div>
      <div class="switch-row">
        <label class="toggle"><input type="checkbox" id="sw-before" ${{showBefore ? 'checked' : ''}}
          onchange="toggleGlobalBefore(this.checked)"><span class="slider"></span></label>
        <label for="sw-before">变更前 link 对 (F)</label>
      </div>
      <div class="switch-row">
        <label class="toggle"><input type="checkbox" id="sw-after" ${{showAfter ? 'checked' : ''}}
          onchange="toggleGlobalAfter(this.checked)"><span class="slider"></span></label>
        <label for="sw-after">变更后 link 对 (G)</label>
      </div>
    </div>`;
    document.getElementById('mapControls').innerHTML = html;
}}

let groupByMode = 'exit';  // 'exit' | 'entry' | 'review'
let showChangesPanel = false;
let showDetectionDetails = false;

function toggleGroupMode() {{
    groupByMode = groupByMode === 'exit' ? 'entry' : 'exit';
    buildSidebar();
}}

function hasCriticalItems() {{
    return DATA.groups.some(g => g.entries.some(e => e.entry_priority === '重点'));
}}

function buildSidebar() {{
    let html = '';

    // 分组维度切换按钮
    html += `<div class="group-mode-bar">
      <span>分组维度：</span>
      <button class="mode-btn ${{groupByMode === 'exit' ? 'active' : ''}}" onclick="groupByMode='exit';buildSidebar()">按出口</button>
      <button class="mode-btn ${{groupByMode === 'entry' ? 'active' : ''}}" onclick="groupByMode='entry';buildSidebar()">按入口</button>
      <button class="mode-btn ${{groupByMode === 'review' ? 'active' : ''}}" onclick="groupByMode='review';buildSidebar()">核实模式</button>
    </div>`;

    const noCritical = !hasCriticalItems();

    if (noCritical) {{
        html += `<div class="no-critical-msg">🎉 无重点核实变更</div>`;
    }}

    // 查看全部变更按钮
    if (DATA.changes && DATA.changes.length > 0) {{
        html += `<div class="changes-toggle-bar">
          <button class="changes-toggle-btn ${{showChangesPanel ? 'active' : ''}}"
            onclick="showChangesPanel=!showChangesPanel;buildSidebar()">
            ${{showChangesPanel ? '📋 收起全部变更' : '📋 查看全部变更'}} (${{DATA.changes.length}}项)
          </button>
        </div>`;
        if (showChangesPanel) {{
            html += buildChangesPanel();
        }}
    }}

    // 无重点核实时，检测详情默认收起，点击可展开
    if (noCritical && DATA.groups.length > 0) {{
        html += `<div class="changes-toggle-bar">
          <button class="changes-toggle-btn ${{showDetectionDetails ? 'active' : ''}}"
            onclick="showDetectionDetails=!showDetectionDetails;buildSidebar()">
            ${{showDetectionDetails ? '🔍 收起检测详情' : '🔍 查看检测详情'}} (${{DATA.groups.length}}组)
          </button>
        </div>`;
        if (!showDetectionDetails) {{
            sidebar.innerHTML = html;
            return;
        }}
    }}

    if (groupByMode === 'review') {{
        buildSidebarByReview(html);
        return;
    }}
    if (groupByMode === 'entry') {{
        buildSidebarByEntry(html);
        return;
    }}

    // 按出口分组（原有逻辑）
    DATA.groups.forEach(g => {{
        const cls = g.bucket === '重点' ? 'critical' : (g.bucket === '透出' ? 'info' : 'normal');
        const needReview = g.entries.filter(e => e.entry_priority === '重点').length;
        const reviewInfo = needReview > 0 ? `${{needReview}}个需核实/` : '';
        html += `<div class="sidebar-section">
          <div class="group-header ${{cls}}" onclick="toggleGroupBody('gb-${{g.gidx}}')">
            <span>组${{g.gidx}} ${{g.priority}} 出口:${{g.exit_id}}</span>
            <span>${{reviewInfo}}${{g.count}}个</span>
          </div>`;
        if (g.causal_links && g.causal_links.length > 0) {{
            const causalTags = g.causal_links.map(wid => `<span class="causal-tag" onclick="focusLink('${{wid}}')" style="cursor:pointer">${{wid}}</span>`).join('');
            html += `<div class="group-causal-bar">🔴 根因: ${{causalTags}}</div>\n`;
        }} else {{
            html += `<div class="group-causal-bar" style="color:#777">⚙️ 替换连锁影响（无独立根因）</div>\n`;
        }}
        html += `<div class="group-body" id="gb-${{g.gidx}}" style="display:none">`;

        const prioOrd = {{'重点': 0, '普通': 1, '透出': 2}};
        const sortedEntries = g.entries.map((entry, idx) => ({{entry, idx}}))
            .sort((a, b) => (prioOrd[a.entry.entry_priority] ?? 9) - (prioOrd[b.entry.entry_priority] ?? 9));
        sortedEntries.forEach(({{entry, idx}}) => {{
            html += `<div class="entry-item" id="ei-${{g.gidx}}-${{idx}}"
              onclick="focusEntry(${{g.gidx}}, ${{idx}})">
              <div class="entry-header">
                <div class="entry-tag">${{entry.entry_priority === '透出' ? '🟢' : (entry.entry_priority === '重点' ? '🔴' : '🟡')}} ${{entry.tag}}</div>
                <button class="detail-btn" onclick="event.stopPropagation();showPathDetail(${{g.gidx}},${{idx}})">路径详情</button>
              </div>
              ${{entry.priority_reason ? `<div class="priority-reason">${{entry.entry_priority === '重点' ? '⚠️ 核实原因' : '✅ 无需核实原因'}}：${{entry.priority_reason}} <span class="confidence">[置信度${{entry.priority_confidence}}%]</span></div>` : ''}}
              <div>进口: ${{entry.eid}}</div>
            </div>`;

            // 多路径拆分展示（替换类走替换对子项，不重复展示）
            const multiPath = !entry.is_replacement && (entry.before_paths.length > 1 || entry.after_paths.length > 1);
            if (multiPath) {{
                html += `<div class="path-breakdown">`;
                const canPair = entry.before_full_ids && entry.after_full_ids && entry.before_full_ids.length === entry.after_full_ids.length;
                if (canPair) {{
                    // 按索引配对展示
                    for (let pi = 0; pi < entry.before_full_ids.length; pi++) {{
                        const bIds = entry.before_full_ids[pi];
                        const aIds = entry.after_full_ids[pi];
                        const isSame = JSON.stringify(bIds) === JSON.stringify(aIds);
                        if (isSame) {{
                            // 无变化路径：全绿
                            const greenIds = bIds.map(id => `<span style="color:#2e7d32">${{id}}</span>`).join(' → ');
                            html += `<div class="pb-row pb-kept" onclick="event.stopPropagation();focusPathPair(${{g.gidx}},${{idx}},${{pi}})">路径${{pi+1}}: ${{greenIds}} (无变化)</div>`;
                        }} else {{
                            // 变化路径：before 全绿，after 按 color_hint 上色
                            const bHtml = bIds.map(id => `<span style="color:#2e7d32">${{id}}</span>`).join(' → ');
                            const hintMap = {{}};
                            if (entry.after_paths[pi]) entry.after_paths[pi].forEach(seg => {{ hintMap[seg.link_id] = seg.color_hint; }});
                            const aHtml = aIds.map(id => {{
                                const clr = (hintMap[id] === 'green') ? '#2e7d32' : '#d32f2f';
                                return `<span style="color:${{clr}}">${{id}}</span>`;
                            }}).join(' → ');
                            html += `<div class="pb-section">`;
                            html += `<div class="pb-title">路径${{pi+1}}</div>`;
                            html += `<div class="pb-row pb-before" onclick="event.stopPropagation();focusPath(${{g.gidx}},${{idx}},'before',${{pi}})">前: ${{bHtml}}</div>`;
                            html += `<div class="pb-row pb-after" onclick="event.stopPropagation();focusPath(${{g.gidx}},${{idx}},'after',${{pi}})">后: ${{aHtml}}</div>`;
                            html += `</div>`;
                        }}
                    }}
                }} else {{
                    if (entry.before_full_ids && entry.before_full_ids.length > 0) {{
                        html += `<div class="pb-section"><div class="pb-title">变更前路径 (${{entry.before_full_ids.length}}条)</div>`;
                        entry.before_full_ids.forEach((ids, pi) => {{
                            const bHtml = ids.map(id => `<span style="color:#2e7d32">${{id}}</span>`).join(' → ');
                            html += `<div class="pb-row pb-before" onclick="event.stopPropagation();focusPath(${{g.gidx}},${{idx}},'before',${{pi}})">路径${{pi+1}}: ${{bHtml}}</div>`;
                        }});
                        html += `</div>`;
                    }}
                    if (entry.after_full_ids && entry.after_full_ids.length > 0) {{
                        html += `<div class="pb-section"><div class="pb-title">变更后路径 (${{entry.after_full_ids.length}}条)</div>`;
                        entry.after_full_ids.forEach((ids, pi) => {{
                            const hintMap = {{}};
                            if (entry.after_paths[pi]) entry.after_paths[pi].forEach(seg => {{ hintMap[seg.link_id] = seg.color_hint; }});
                            const aHtml = ids.map(id => {{
                                const clr = (hintMap[id] === 'green') ? '#2e7d32' : '#d32f2f';
                                return `<span style="color:${{clr}}">${{id}}</span>`;
                            }}).join(' → ');
                            html += `<div class="pb-row pb-after" onclick="event.stopPropagation();focusPath(${{g.gidx}},${{idx}},'after',${{pi}})">路径${{pi+1}}: ${{aHtml}}</div>`;
                        }});
                        html += `</div>`;
                    }}
                }}
                html += `</div>`;
            }}

            // 替换对子项（用 color_hint 逐 link 上色）
            if (entry.is_replacement && entry.pairs && entry.pairs.length > 0) {{
                html += `<div class="pair-list">`;
                entry.pairs.forEach((pair, pi) => {{
                    // before 全绿
                    const bHtml = pair.before_link_ids.map(id => `<span style="color:#2e7d32">${{id}}</span>`).join(' → ');
                    // after 按 color_hint 上色
                    const hintMap = {{}};
                    if (pair.after_path) pair.after_path.forEach(seg => {{ hintMap[seg.link_id] = seg.color_hint; }});
                    const aHtml = pair.after_link_ids.map(id => {{
                        const clr = (hintMap[id] === 'green') ? '#2e7d32' : '#d32f2f';
                        return `<span style="color:${{clr}}">${{id}}</span>`;
                    }}).join(' → ');
                    html += `<div class="pair-item" id="pi-${{g.gidx}}-${{idx}}-${{pi}}"
                      onclick="event.stopPropagation(); focusPair(${{g.gidx}}, ${{idx}}, ${{pi}})">
                      <div class="pair-title">替换对${{pi + 1}}</div>
                      <div class="pair-before">前: ${{bHtml}}</div>
                      <div class="pair-after">后: ${{aHtml}}</div>
                    </div>`;
                }});
                if (entry.kept_count > 0) {{
                    html += `<div class="kept-info">未变化路径: ${{entry.kept_count}}条</div>`;
                }}
                html += `</div>`;
            }}
        }});

        html += `</div></div>`;
    }});

    sidebar.innerHTML = html;
}}

function buildChangesPanel() {{
    const typeClass = {{
        '替换': 'ct-replace', '删除': 'ct-delete', '新增': 'ct-add', '修改': 'ct-modify',
    }};
    const wid = id => id ? 'w' + id : '';
    let html = '<div class="changes-panel">';
    DATA.changes.forEach(c => {{
        const cls = typeClass[c.type] || 'ct-intersection';
        let linkHtml = '';
        if (c.type === '替换') {{
            linkHtml = `<span class="change-link-ids">
              <span style="cursor:pointer;color:#c62828" onclick="event.stopPropagation();focusLink('${{c.before}}')">${{wid(c.before)}}</span>
              → <span style="cursor:pointer;color:#2e7d32" onclick="event.stopPropagation();focusLink('${{c.after}}')">${{wid(c.after)}}</span></span>`;
        }} else if (c.before && !c.after) {{
            linkHtml = `<span class="change-link-ids" style="cursor:pointer;color:#c62828"
              onclick="event.stopPropagation();focusLink('${{c.before}}')">${{wid(c.before)}}</span>`;
        }} else if (!c.before && c.after) {{
            linkHtml = `<span class="change-link-ids" style="cursor:pointer;color:#2e7d32"
              onclick="event.stopPropagation();focusLink('${{c.after}}')">${{wid(c.after)}}</span>`;
        }} else {{
            linkHtml = `<span class="change-link-ids" style="cursor:pointer"
              onclick="event.stopPropagation();focusLink('${{c.after || c.before}}')">${{wid(c.after || c.before)}}</span>`;
        }}
        html += `<div class="change-item">
          <span class="change-type ${{cls}}">${{c.type}}</span>
          ${{linkHtml}}
        </div>`;
    }});
    html += '</div>';
    return html;
}}

function buildSidebarByEntry(prefixHtml) {{
    // 按入口聚合：从所有 groups 中提取 entries，按 eid 分组
    const byEntry = {{}};
    DATA.groups.forEach(g => {{
        g.entries.forEach((entry, idx) => {{
            const key = entry.eid;
            if (!byEntry[key]) {{
                byEntry[key] = {{ eid: key, entry_name: entry.entry_name, items: [] }};
            }}
            byEntry[key].items.push({{ gidx: g.gidx, idx, entry, group: g }});
        }});
    }});

    // 按组级优先级排序
    const prioOrder = {{ '重点': 0, '普通': 1, '透出': 2 }};
    const entryGroups = Object.values(byEntry).sort((a, b) => {{
        const ap = Math.min(...a.items.map(i => prioOrder[i.entry.entry_priority] ?? 9));
        const bp = Math.min(...b.items.map(i => prioOrder[i.entry.entry_priority] ?? 9));
        return ap - bp || b.items.length - a.items.length;
    }});

    let html = prefixHtml;
    entryGroups.forEach((eg, egIdx) => {{
        // 入口级优先级 = 组内最高
        const priorities = eg.items.map(i => i.entry.entry_priority);
        const bucket = priorities.includes('重点') ? '重点' : (priorities.includes('普通') ? '普通' : '透出');
        const cls = bucket === '重点' ? 'critical' : (bucket === '透出' ? 'info' : 'normal');
        const prioLabel = bucket === '重点' ? '🔴 重点核实' : (bucket === '普通' ? '🟡 需核实' : '🟢 仅透出');

        const needReview = eg.items.filter(i => i.entry.entry_priority === '重点').length;
        const reviewInfo = needReview > 0 ? `${{needReview}}个需核实/` : '';
        html += `<div class="sidebar-section">
          <div class="group-header ${{cls}}" onclick="toggleGroupBody('eg-${{egIdx}}')">
            <span>入口:${{eg.eid}}</span>
            <span>${{prioLabel}} ${{reviewInfo}}${{eg.items.length}}个出口</span>
          </div>`;
        if (eg.entry_name) {{
            html += `<div class="group-causal-bar" style="color:#555">📍 ${{eg.entry_name}}</div>`;
        }}
        html += `<div class="group-body" id="eg-${{egIdx}}" style="display:none">`;

        const prioOrd = {{'重点': 0, '普通': 1, '透出': 2}};
        eg.items.sort((a, b) => (prioOrd[a.entry.entry_priority] ?? 9) - (prioOrd[b.entry.entry_priority] ?? 9));
        eg.items.forEach(item => {{
            const entry = item.entry;
            const g = item.group;
            html += `<div class="entry-item" id="ei-${{item.gidx}}-${{item.idx}}"
              onclick="focusEntry(${{item.gidx}}, ${{item.idx}})">
              <div class="entry-header">
                <div class="entry-tag">${{entry.entry_priority === '透出' ? '🟢' : (entry.entry_priority === '重点' ? '🔴' : '🟡')}} ${{entry.tag}}</div>
                <button class="detail-btn" onclick="event.stopPropagation();showPathDetail(${{item.gidx}},${{item.idx}})">路径详情</button>
              </div>
              ${{entry.priority_reason ? `<div class="priority-reason">${{entry.entry_priority === '重点' ? '⚠️ 核实原因' : '✅ 无需核实原因'}}：${{entry.priority_reason}} <span class="confidence">[置信度${{entry.priority_confidence}}%]</span></div>` : ''}}
              <div>出口: ${{entry.xid}} (${{g.priority}})</div>
            </div>`;

            // 替换对子项
            if (entry.is_replacement && entry.pairs && entry.pairs.length > 0) {{
                html += `<div class="pair-list">`;
                entry.pairs.forEach((pair, pi) => {{
                    const bHtml = pair.before_link_ids.map(id => `<span style="color:#2e7d32">${{id}}</span>`).join(' → ');
                    const hintMap = {{}};
                    if (pair.after_path) pair.after_path.forEach(seg => {{ hintMap[seg.link_id] = seg.color_hint; }});
                    const aHtml = pair.after_link_ids.map(id => {{
                        const clr = (hintMap[id] === 'green') ? '#2e7d32' : '#d32f2f';
                        return `<span style="color:${{clr}}">${{id}}</span>`;
                    }}).join(' → ');
                    html += `<div class="pair-item" id="pi-${{item.gidx}}-${{item.idx}}-${{pi}}"
                      onclick="event.stopPropagation(); focusPair(${{item.gidx}}, ${{item.idx}}, ${{pi}})">
                      <div class="pair-title">替换对${{pi + 1}}</div>
                      <div class="pair-before">前: ${{bHtml}}</div>
                      <div class="pair-after">后: ${{aHtml}}</div>
                    </div>`;
                }});
                if (entry.kept_count > 0) {{
                    html += `<div class="kept-info">未变化路径: ${{entry.kept_count}}条</div>`;
                }}
                html += `</div>`;
            }}
        }});

        html += `</div></div>`;
    }});

    sidebar.innerHTML = html;
}}

function buildSidebarByReview(prefixHtml) {{
    // 核实模式：只展示重点条目，按根因 link 聚合
    // 1) 收集所有重点条目
    const criticalItems = [];
    DATA.groups.forEach(g => {{
        g.entries.forEach((entry, idx) => {{
            if (entry.entry_priority === '重点') {{
                criticalItems.push({{ gidx: g.gidx, idx, entry, group: g }});
            }}
        }});
    }});

    if (criticalItems.length === 0) {{
        sidebar.innerHTML = prefixHtml;
        return;
    }}

    // 2) 按根因聚合
    // 有组级根因 → 按根因 link 分组（一个条目可能对应多个根因 link，放入每个根因组）
    // 按根因 link 分组：优先用组级根因，其次用 entry 自身的 causal links
    const byCause = {{}};
    criticalItems.forEach(item => {{
        const groupCausal = item.group.causal_links || [];
        const entryCausal = item.entry.causal || [];
        const effectiveCausal = groupCausal.length > 0 ? groupCausal : entryCausal;
        if (effectiveCausal.length > 0) {{
            effectiveCausal.forEach(cid => {{
                const key = 'link:' + cid;
                if (!byCause[key]) byCause[key] = {{ type: 'link', cause_id: cid, label: '根因link: ' + cid, items: [] }};
                byCause[key].items.push(item);
            }});
        }} else {{
            const reason = item.entry.priority_reason || '未知原因';
            const key = 'reason:' + reason;
            if (!byCause[key]) byCause[key] = {{ type: 'reason', cause_id: null, label: reason, items: [] }};
            byCause[key].items.push(item);
        }}
    }});

    // 3) 排序：根因 link 组在前（按影响条目数降序），原因组在后
    const causeGroups = Object.values(byCause).sort((a, b) => {{
        if (a.type !== b.type) return a.type === 'link' ? -1 : 1;
        return b.items.length - a.items.length;
    }});

    let html = prefixHtml;

    // 统计摘要
    const totalCritical = criticalItems.length;
    const withCause = causeGroups.filter(g => g.type === 'link').reduce((s, g) => s + g.items.length, 0);
    html += `<div class="review-summary">
      <div>🔴 共 <b>${{totalCritical}}</b> 个需核实条目</div>
      <div style="font-size:11px;color:#666;margin-top:2px">${{causeGroups.filter(g=>g.type==='link').length}} 个根因link · ${{causeGroups.filter(g=>g.type==='reason').length}} 个其他原因</div>
    </div>`;

    causeGroups.forEach((cg, cgIdx) => {{
        const icon = cg.type === 'link' ? '🔗' : '⚠️';
        html += `<div class="sidebar-section">
          <div class="group-header critical" onclick="toggleGroupBody('rg-${{cgIdx}}')">
            <span>${{icon}} ${{cg.label}}${{cg.type === 'link' ? ` <button class="locate-btn" onclick="event.stopPropagation();focusLink('${{cg.cause_id}}')">📍</button>` : ''}}</span>
            <span>${{cg.items.length}}个link对</span>
          </div>`;
        html += `<div class="group-body" id="rg-${{cgIdx}}" style="display:none">`;

        cg.items.forEach(item => {{
            const entry = item.entry;
            html += `<div class="entry-item" id="ei-${{item.gidx}}-${{item.idx}}"
              onclick="focusEntry(${{item.gidx}}, ${{item.idx}})">
              <div class="entry-header">
                <div class="entry-tag">🔴 ${{entry.tag}}</div>
                <button class="detail-btn" onclick="event.stopPropagation();showPathDetail(${{item.gidx}},${{item.idx}})">路径详情</button>
              </div>
              ${{entry.priority_reason ? `<div class="priority-reason">⚠️ ${{entry.priority_reason}} <span class="confidence">[置信度${{entry.priority_confidence}}%]</span></div>` : ''}}
              <div>进口: ${{entry.eid}} → 出口: ${{entry.xid}}</div>
            </div>`;

            // 替换对子项
            if (entry.is_replacement && entry.pairs && entry.pairs.length > 0) {{
                html += `<div class="pair-list">`;
                entry.pairs.forEach((pair, pi) => {{
                    const bHtml = pair.before_link_ids.map(id => `<span style="color:#2e7d32">${{id}}</span>`).join(' → ');
                    const hintMap = {{}};
                    if (pair.after_path) pair.after_path.forEach(seg => {{ hintMap[seg.link_id] = seg.color_hint; }});
                    const aHtml = pair.after_link_ids.map(id => {{
                        const clr = (hintMap[id] === 'green') ? '#2e7d32' : '#d32f2f';
                        return `<span style="color:${{clr}}">${{id}}</span>`;
                    }}).join(' → ');
                    html += `<div class="pair-item" id="pi-${{item.gidx}}-${{item.idx}}-${{pi}}"
                      onclick="event.stopPropagation(); focusPair(${{item.gidx}}, ${{item.idx}}, ${{pi}})">
                      <div class="pair-title">替换对${{pi + 1}}</div>
                      <div class="pair-before">前: ${{bHtml}}</div>
                      <div class="pair-after">后: ${{aHtml}}</div>
                    </div>`;
                }});
                html += `</div>`;
            }}
        }});

        html += `</div></div>`;
    }});

    sidebar.innerHTML = html;
}}

// ── 路径详情弹窗 ──
function showPathDetail(gidx, idx) {{
    const group = DATA.groups.find(g => g.gidx === gidx);
    if (!group) return;
    const entry = group.entries[idx];
    if (!entry) return;

    document.getElementById('modalTitle').innerText =
        `路径详情 — 组${{gidx}} 进口:${{entry.eid}} 出口:${{entry.xid}}`;

    let bodyHtml = '';

    // 变更前路径
    if (entry.before_full_ids && entry.before_full_ids.length > 0) {{
        bodyHtml += '<div class="modal-section"><div class="modal-section-title">变更前路径</div>';
        entry.before_full_ids.forEach((ids, pi) => {{
            bodyHtml += `<div class="modal-path before">路径${{pi+1}}: ${{ids.join(' → ')}}</div>`;
        }});
        bodyHtml += '</div>';
    }}

    // 变更后路径
    if (entry.after_full_ids && entry.after_full_ids.length > 0) {{
        bodyHtml += '<div class="modal-section"><div class="modal-section-title">变更后路径</div>';
        entry.after_full_ids.forEach((ids, pi) => {{
            bodyHtml += `<div class="modal-path after">路径${{pi+1}}: ${{ids.join(' → ')}}</div>`;
        }});
        bodyHtml += '</div>';
    }}

    // 关键影响 links
    if (entry.causal && entry.causal.length > 0) {{
        bodyHtml += '<div class="modal-section"><div class="modal-section-title">关键影响 links（根因）</div>';
        entry.causal.forEach(wid => {{
            bodyHtml += `<span class="modal-causal">${{wid}}</span>`;
        }});
        bodyHtml += '</div>';
    }} else {{
        bodyHtml += '<div class="modal-section"><div class="modal-section-title">关键影响 links</div><span style="color:#999;font-size:12px;">未能定位到明确根因</span></div>';
    }}

    // 替换对详情
    if (entry.is_replacement && entry.pairs && entry.pairs.length > 0) {{
        bodyHtml += '<div class="modal-section"><div class="modal-section-title">替换对详情</div>';
        entry.pairs.forEach((pair, pi) => {{
            bodyHtml += `<div style="margin:6px 0;padding:8px;background:#f9f9f9;border-radius:4px;">`;
            bodyHtml += `<div style="font-weight:bold;font-size:12px;margin-bottom:4px;">替换对${{pi+1}}</div>`;
            bodyHtml += `<div style="color:#2e7d32;font-size:12px;">替换前: ${{pair.before_link_ids.join(' → ')}}</div>`;
            bodyHtml += `<div style="color:#d32f2f;font-size:12px;">替换后: ${{pair.after_link_ids.join(' → ')}}</div>`;
            bodyHtml += `</div>`;
        }});
        if (entry.kept_count > 0) {{
            bodyHtml += `<div style="font-size:12px;color:#666;margin-top:4px;">未变化路径: ${{entry.kept_count}}条</div>`;
        }}
        bodyHtml += '</div>';
    }}

    document.getElementById('modalBody').innerHTML = bodyHtml;
    document.getElementById('pathModal').classList.add('active');
}}

function closePathModal(event) {{
    if (!event || event.target.id === 'pathModal') {{
        document.getElementById('pathModal').classList.remove('active');
    }}
}}


// ── 判读系统 ──
const STORAGE_KEY = 'connectivity_review_' + DATA.task_code;
const HISTORY_KEY = 'connectivity_history';
let reviewData = {{}};
let currentReviewLink = null;

function loadReviewData() {{
    try {{
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) reviewData = JSON.parse(raw);
    }} catch(e) {{ reviewData = {{}}; }}
}}

function saveReviewData() {{
    localStorage.setItem(STORAGE_KEY, JSON.stringify(reviewData));
}}

function openReviewModal(linkId) {{
    currentReviewLink = linkId;
    document.getElementById('reviewLinkId').innerText = 'Link: ' + linkId;
    const existing = reviewData[linkId] || {{}};
    document.getElementById('reviewNote').value = existing.note || '';
    selectReview(existing.result || null);
    document.getElementById('reviewModal').classList.add('active');
}}

function closeReviewModal(event) {{
    if (!event || event.target.id === 'reviewModal' || event === undefined) {{
        document.getElementById('reviewModal').classList.remove('active');
        currentReviewLink = null;
    }}
}}

function selectReview(result) {{
    const optC = document.getElementById('optCorrect');
    const optW = document.getElementById('optWrong');
    optC.classList.remove('selected');
    optW.classList.remove('selected');
    if (result === 'correct') optC.classList.add('selected');
    if (result === 'wrong') optW.classList.add('selected');
}}

function saveReview() {{
    if (!currentReviewLink) return;
    const note = document.getElementById('reviewNote').value.trim();
    const optC = document.getElementById('optCorrect');
    const optW = document.getElementById('optWrong');
    let result = null;
    if (optC.classList.contains('selected')) result = 'correct';
    if (optW.classList.contains('selected')) result = 'wrong';
    reviewData[currentReviewLink] = {{ result, note, time: new Date().toISOString() }};
    saveReviewData();
    closeReviewModal();
    updateReviewIndicators();
}}

function updateReviewIndicators() {{
    // 在侧边栏更新判读状态指示
    document.querySelectorAll('.entry-item').forEach(el => {{
        const key = el.id;
        if (!key) return;
        const parts = key.split('-');
        if (parts.length < 3) return;
        const gidx = parseInt(parts[1]);
        const idx = parseInt(parts[2]);
        const group = DATA.groups.find(g => g.gidx === gidx);
        if (!group) return;
        const entry = group.entries[idx];
        const linkIds = new Set();
        entry.after_paths.forEach(p => p.forEach(seg => {{ if (seg.changed) linkIds.add(seg.link_id); }}));
        let correct = 0, wrong = 0;
        linkIds.forEach(lid => {{
            const r = reviewData[lid];
            if (r && r.result === 'correct') correct++;
            if (r && r.result === 'wrong') wrong++;
        }});
        let badge = '';
        if (correct > 0 || wrong > 0) {{
            badge = ` <span style="font-size:10px;color:#43a047;">${{correct}}✅</span> <span style="font-size:10px;color:#e53935;">${{wrong}}❌</span>`;
        }}
        const tagEl = el.querySelector('.entry-tag');
        if (tagEl && !tagEl.dataset.reviewBadge) {{
            tagEl.dataset.reviewBadge = '1';
        }}
        if (tagEl) {{
            const baseText = entry.tag;
            tagEl.innerHTML = baseText + badge;
        }}
    }});
}}

// ── 历史记录系统 ──
function getHistory() {{
    try {{
        const raw = localStorage.getItem(HISTORY_KEY);
        return raw ? JSON.parse(raw) : [];
    }} catch(e) {{ return []; }}
}}

function saveHistory(list) {{
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
}}

function recordCurrentTask() {{
    const list = getHistory();
    const existingIdx = list.findIndex(h => h.task_code === DATA.task_code);
    const summary = {{
        task_code: DATA.task_code,
        time: new Date().toISOString(),
        group_count: DATA.groups.length,
        entry_count: DATA.groups.reduce((s, g) => s + g.entries.length, 0),
        review_count: Object.keys(reviewData).length,
        causal_count: DATA.groups.reduce((s, g) => s + (g.causal_links ? g.causal_links.length : 0), 0),
    }};
    if (existingIdx >= 0) {{
        list[existingIdx] = summary;
    }} else {{
        list.push(summary);
    }}
    saveHistory(list);
    renderHistory();
}}

function toggleHistory() {{
    const panel = document.getElementById('historyPanel');
    panel.classList.toggle('active');
    if (panel.classList.contains('active')) renderHistory();
}}

function renderHistory() {{
    const list = getHistory();
    const body = document.getElementById('historyBody');
    if (list.length === 0) {{
        body.innerHTML = '<div class="history-empty">暂无历史记录</div>';
        return;
    }}
    // 按时间倒序
    const sorted = [...list].sort((a, b) => new Date(b.time) - new Date(a.time));
    let html = '';
    sorted.forEach(h => {{
        const dateStr = new Date(h.time).toLocaleString('zh-CN');
        html += `<div class="history-item">
            <div class="h-title"><span>${{h.task_code}}</span><span style="color:#999;font-size:11px;">${{dateStr}}</span></div>
            <div class="h-meta">${{h.group_count}}组 · ${{h.entry_count}}个进出口对 · 已判读${{h.review_count}}条 · 根因${{h.causal_count}}个</div>
            <div class="h-actions">
                <button onclick="location.href='?task=' + encodeURIComponent('${{h.task_code}}')">打开</button>
                <button class="btn-del" onclick="deleteHistory('${{h.task_code}}')">删除</button>
            </div>
        </div>`;
    }});
    body.innerHTML = html;
}}

function deleteHistory(taskCode) {{
    if (!confirm('确定删除这条历史记录？')) return;
    let list = getHistory();
    list = list.filter(h => h.task_code !== taskCode);
    saveHistory(list);
    renderHistory();
}}

function exportHistory() {{
    const list = getHistory();
    if (list.length === 0) {{ alert('暂无历史记录可导出'); return; }}
    const rows = [];
    rows.push(['task_code', '检测时间', '分组数', '进出口对数', '已判读数', '根因数']);
    list.forEach(h => {{
        rows.push([h.task_code, h.time, h.group_count, h.entry_count, h.review_count, h.causal_count]);
    }});
    // 导出当前 task 的 link 判读详情
    const allReviewKeys = Object.keys(localStorage).filter(k => k.startsWith('connectivity_review_'));
    rows.push([]);
    rows.push(['task_code', 'link_id', '判读结果', '备注', '判读时间']);
    allReviewKeys.forEach(key => {{
        const tc = key.replace('connectivity_review_', '');
        try {{
            const data = JSON.parse(localStorage.getItem(key) || '{{}}');
            Object.entries(data).forEach(([lid, r]) => {{
                rows.push([tc, lid, r.result === 'correct' ? '正确' : (r.result === 'wrong' ? '错误' : ''), r.note || '', r.time || '']);
            }});
        }} catch(e) {{}}
    }});
    const csv = rows.map(r => r.map(c => `"${{String(c).replace(/"/g, '""')}}"`).join(',')).join('\\n');
    const blob = new Blob(['\\uFEFF' + csv], {{ type: 'text/csv;charset=utf-8;' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `connectivity_history_${{new Date().toISOString().slice(0,10)}}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}}

async function loadHistoryFromServer() {{
    try {{
        const resp = await fetch('/api/history');
        const serverList = await resp.json();
        if (serverList && serverList.length > 0) {{
            saveHistory(serverList);
            renderHistory();
            alert('已从服务端同步 ' + serverList.length + ' 条历史记录');
        }} else {{
            alert('服务端暂无历史记录');
        }}
    }} catch(e) {{
        alert('同步失败: ' + e.message);
    }}
}}

async function pushHistoryToServer() {{
    try {{
        const list = getHistory();
        const resp = await fetch('/api/history', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(list)
        }});
        const result = await resp.json();
        if (result.success) {{
            alert('已推送 ' + list.length + ' 条历史记录到服务端');
        }} else {{
            alert('推送失败: ' + (result.error || '未知错误'));
        }}
    }} catch(e) {{
        alert('推送失败: ' + e.message);
    }}
}}

// ── 初始化 ──
loadReviewData();
recordCurrentTask();
updateReviewIndicators();

// ── 配置与重跑 ──
// ── 交互函数 ──

function toggleGroupBody(id) {{
    const el = document.getElementById(id);
    el.style.display = el.style.display === 'none' ? 'block' : 'none';
}}

function toggleBaseNetwork(on) {{
    showBaseNetwork = on;
    if (on) {{
        layers.beforeNetwork.addTo(map);
        layers.beforeNodes.addTo(map);
    }} else {{
        map.removeLayer(layers.beforeNetwork);
        map.removeLayer(layers.beforeNodes);
    }}
}}

function toggleAfterNetwork(on) {{
    showAfterNetwork = on;
    if (on) {{
        layers.afterNetwork.addTo(map);
        layers.afterNodes.addTo(map);
    }} else {{
        map.removeLayer(layers.afterNetwork);
        map.removeLayer(layers.afterNodes);
    }}
}}

let activeKey = null;       // 当前激活的 entry key
let activePairIdx = null;   // 当前激活的替换对索引（null = entry 模式）
let activePulse = null;     // focusLink 高亮图层

function clearActiveLayers() {{
    if (activeKey && entryLayers[activeKey]) {{
        const prev = entryLayers[activeKey];
        // 清 entry 级图层
        prev.before_paths.forEach(pl => map.removeLayer(pl));
        prev.after_paths.forEach(pl => map.removeLayer(pl));
        map.removeLayer(prev.nodes_before);
        map.removeLayer(prev.nodes_after);
        // 清所有 pair 级图层
        if (prev.pairs) {{
            Object.values(prev.pairs).forEach(p => {{
                map.removeLayer(p.before);
                map.removeLayer(p.after);
                map.removeLayer(p.nodes_before);
                map.removeLayer(p.nodes_after);
            }});
        }}
    }}
    activeKey = null;
    activePairIdx = null;
    // 清理 focusLink 高亮
    if (activePulse) {{
        map.removeLayer(activePulse);
        activePulse = null;
    }}
    map.closePopup();
}}

function toggleGlobalBefore(on) {{
    showBefore = on;
    if (!activeKey || !entryLayers[activeKey]) return;
    const el = entryLayers[activeKey];
    if (activePairIdx !== null && el.pairs) {{
        // pair 模式：before 路径 + before 节点独立控制
        const p = el.pairs['p' + activePairIdx];
        if (p) {{
            if (on) {{ p.before.addTo(map); p.nodes_before.addTo(map); }}
            else {{ map.removeLayer(p.before); map.removeLayer(p.nodes_before); }}
        }}
    }} else {{
        // entry 模式
        el.before_paths.forEach(pl => {{ if (on) pl.addTo(map); else map.removeLayer(pl); }});
        if (on) el.nodes_before.addTo(map); else map.removeLayer(el.nodes_before);
    }}
    const sw = document.getElementById('sw-before');
    if (sw) sw.checked = on;
}}

function toggleGlobalAfter(on) {{
    showAfter = on;
    if (!activeKey || !entryLayers[activeKey]) return;
    const el = entryLayers[activeKey];
    if (activePairIdx !== null && el.pairs) {{
        const p = el.pairs['p' + activePairIdx];
        if (p) {{
            if (on) {{ p.after.addTo(map); p.nodes_after.addTo(map); }}
            else {{ map.removeLayer(p.after); map.removeLayer(p.nodes_after); }}
        }}
    }} else {{
        el.after_paths.forEach(pl => {{ if (on) pl.addTo(map); else map.removeLayer(pl); }});
        if (on) el.nodes_after.addTo(map); else map.removeLayer(el.nodes_after);
    }}
    const sw = document.getElementById('sw-after');
    if (sw) sw.checked = on;
}}

function focusLink(linkId) {{
    const link = linkLookup[linkId];
    if (!link) {{ alert('未找到 link: ' + linkId); return; }}
    if (link.coords && link.coords.length > 1) {{
        const bounds = L.latLngBounds(link.coords);
        map.panTo(bounds.getCenter());
    }}
    // 清理上次高亮
    if (activePulse) {{ map.removeLayer(activePulse); activePulse = null; }}
    // 用粗 polyline 沿整条 link geometry 高亮，并闪烁 3 次
    const hlLayer = L.layerGroup();
    const hl = L.polyline(link.coords, {{
        color: '#ff0000', weight: 10, opacity: 0.7, lineCap: 'round'
    }}).addTo(hlLayer);
    hlLayer.addTo(map);
    activePulse = hlLayer;
    // 闪烁效果
    let blinks = 0;
    const blinkInterval = setInterval(() => {{
        blinks++;
        if (blinks >= 6) {{
            clearInterval(blinkInterval);
            hl.setStyle({{ opacity: 0.5, weight: 8 }});
            return;
        }}
        hl.setStyle({{ opacity: blinks % 2 === 0 ? 0.7 : 0 }});
    }}, 250);
}}

// ── 键盘快捷键 ──
document.addEventListener('keydown', (e) => {{
    // 排除输入框
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'f' || e.key === 'F') {{
        showBefore = !showBefore;
        toggleGlobalBefore(showBefore);
    }}
    if (e.key === 'g' || e.key === 'G') {{
        showAfter = !showAfter;
        toggleGlobalAfter(showAfter);
    }}
}});

function focusPath(gidx, entryIdx, pathType, pathIdx) {{
    const group = DATA.groups.find(g => g.gidx === gidx);
    if (!group) return;
    const entry = group.entries[entryIdx];
    if (!entry) return;

    clearActiveLayers();

    const key = gidx + '-' + entryIdx;
    activeKey = key;
    activePairIdx = null;
    const el = entryLayers[key];
    if (!el) return;

    // 只显示指定的一条路径（不显示节点，避免其他路径的离散节点干扰）
    if (pathType === 'before' && el.before_paths[pathIdx]) {{
        el.before_paths[pathIdx].addTo(map);
    }} else if (pathType === 'after' && el.after_paths[pathIdx]) {{
        el.after_paths[pathIdx].addTo(map);
    }}

    // 缩放
    let coords = [];
    if (pathType === 'before' && entry.before_paths[pathIdx]) {{
        entry.before_paths[pathIdx].forEach(seg => coords.push(seg.coords));
    }} else if (pathType === 'after' && entry.after_paths[pathIdx]) {{
        entry.after_paths[pathIdx].forEach(seg => coords.push(seg.coords));
    }}
    if (coords.length > 0) {{
        const flat = coords.flat();
        const bounds = L.latLngBounds(flat);
        map.panTo(bounds.getCenter());
    }}
}}

// 配对路径聚焦（同时展示变更前和变更后的同索引路径，用于无变化路径对）
function focusPathPair(gidx, entryIdx, pathIdx) {{
    const group = DATA.groups.find(g => g.gidx === gidx);
    if (!group) return;
    const entry = group.entries[entryIdx];
    if (!entry) return;

    clearActiveLayers();

    const key = gidx + '-' + entryIdx;
    activeKey = key;
    activePairIdx = null;
    const el = entryLayers[key];
    if (!el) return;

    if (el.before_paths[pathIdx]) el.before_paths[pathIdx].addTo(map);
    if (el.after_paths[pathIdx]) el.after_paths[pathIdx].addTo(map);

    let coords = [];
    if (entry.before_paths[pathIdx]) {{
        entry.before_paths[pathIdx].forEach(seg => coords.push(seg.coords));
    }}
    if (entry.after_paths[pathIdx]) {{
        entry.after_paths[pathIdx].forEach(seg => coords.push(seg.coords));
    }}
    if (coords.length > 0) {{
        const flat = coords.flat();
        const bounds = L.latLngBounds(flat);
        map.panTo(bounds.getCenter());
    }}
}}

function focusEntry(gidx, entryIdx) {{
    clearActiveLayers();

    const key = gidx + '-' + entryIdx;
    activeKey = key;
    activePairIdx = null;  // entry 模式
    const el = entryLayers[key];
    if (el) {{
        if (showBefore) {{ el.before_paths.forEach(pl => pl.addTo(map)); el.nodes_before.addTo(map); }}
        if (showAfter) {{ el.after_paths.forEach(pl => pl.addTo(map)); el.nodes_after.addTo(map); }}
    }}

    // 高亮 sidebar
    document.querySelectorAll('.entry-item').forEach(e => e.classList.remove('active'));
    document.querySelectorAll('.pair-item').forEach(e => e.classList.remove('active'));
    const target = document.getElementById(`ei-${{gidx}}-${{entryIdx}}`);
    if (target) target.classList.add('active');

    // 平移到当前 entry 中心（保持比例尺不变）
    const group = DATA.groups.find(g => g.gidx === gidx);
    if (group) {{
        const entry = group.entries[entryIdx];
        const allCoords = [];
        entry.before_nodes.forEach(n => allCoords.push(n.coord));
        entry.after_nodes.forEach(n => allCoords.push(n.coord));
        if (allCoords.length > 0) {{
            const bounds = L.latLngBounds(allCoords);
            map.panTo(bounds.getCenter());
        }}
    }}
}}

// 聚焦替换对（仅展示该对的变更前+变更后+节点）
function focusPair(gidx, entryIdx, pairIdx) {{
    const group = DATA.groups.find(g => g.gidx === gidx);
    if (!group) return;
    const entry = group.entries[entryIdx];
    if (!entry) return;

    clearActiveLayers();

    const key = gidx + '-' + entryIdx;
    activeKey = key;
    activePairIdx = pairIdx;  // pair 模式
    const el = entryLayers[key];
    if (!el || !el.pairs) return;

    const pKey = 'p' + pairIdx;
    const pair = el.pairs[pKey];
    if (!pair) return;

    if (showBefore) {{ pair.before.addTo(map); pair.nodes_before.addTo(map); }}
    if (showAfter) {{ pair.after.addTo(map); pair.nodes_after.addTo(map); }}

    // 高亮 sidebar
    document.querySelectorAll('.entry-item').forEach(e => e.classList.remove('active'));
    document.querySelectorAll('.pair-item').forEach(e => e.classList.remove('active'));
    const target = document.getElementById(`pi-${{gidx}}-${{entryIdx}}-${{pairIdx}}`);
    if (target) target.classList.add('active');

    // 平移到该替换对中心（保持比例尺不变）
    const pairData = entry.pairs[pairIdx];
    if (pairData) {{
        const allCoords = [];
        pairData.before_path.forEach(seg => {{ if (seg.coords) allCoords.push(...seg.coords); }});
        pairData.after_path.forEach(seg => {{ if (seg.coords) allCoords.push(...seg.coords); }});
        if (allCoords.length > 0) {{
            const bounds = L.latLngBounds(allCoords);
            map.panTo(bounds.getCenter());
        }}
    }}
}}

buildMapControls();
buildSidebar();
</script>
</body>
</html>'''


