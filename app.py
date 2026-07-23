#!/usr/bin/env python3
"""ROS Bag 时间对齐检查工具 — Gradio Web GUI。

用法:
  pip install -r requirements.txt
  python app.py
  # 浏览器打开 http://<服务器IP>:7860
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

import gradio as gr
import pandas as pd

from presentation import (
    BAG_DETAIL_HEADERS,
    SUMMARY_HEADERS,
    TOPIC_HEADERS,
    empty_bag_detail_df,
    empty_per_bag_detail_html,
    empty_summary_df,
    empty_topic_df,
    stats_to_bag_detail_df,
    stats_to_per_bag_detail_html,
    stats_to_summary_df,
)
from styles import APP_CSS

from core import (
    DEFAULT_ANALYSIS_WORKERS,
    DEFAULT_EXCLUDE_TOPICS,
    DEFAULT_INPUT,
    DEFAULT_OUTPUT,
    DEFAULT_REF_TOPIC,
    DEFAULT_THRESHOLDS_MS,
    MAX_ANALYSIS_WORKERS,
    STATUS_ORDER,
    copy_keepable_bags,
    default_check_topics,
    filter_keepable,
    get_bag_info,
    iter_measure_bags,
    normalize_analysis_workers,
    scan_bags,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bag_inspector")

_analyze_stop = threading.Event()

def _check_topic_update(all_topics: list[str], selected: list[str] | None = None):
    """更新可检查 Topic 勾选：默认全选（不含 Reference 与 /tf_static）。"""
    defaults = default_check_topics(all_topics, reference_topic=DEFAULT_REF_TOPIC)
    if selected is None:
        value = defaults
    else:
        keep = [t for t in selected if t in defaults]
        value = keep if keep else defaults
    return gr.update(choices=defaults, value=value)


# ---------- folder browser ----------

def _normalize_path(path: str) -> str:
    text = (path or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return text


def list_subdirs(path: str) -> list[str]:
    text = _normalize_path(path)
    if not text:
        return []
    p = Path(text)
    if not p.is_dir():
        return []
    try:
        return sorted(d.name for d in p.iterdir() if d.is_dir())
    except OSError:
        return []


def folder_choices(path: str) -> list[str]:
    cur = _normalize_path(path)
    if not cur:
        return []
    p = Path(cur)
    choices: list[str] = [cur]
    parent = str(p.parent)
    if parent != cur:
        choices.append(parent)
    for name in list_subdirs(cur):
        choices.append(str(p / name))
    seen: set[str] = set()
    uniq: list[str] = []
    for c in choices:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def make_folder_update(path: str):
    cur = _normalize_path(path) or (path or "")
    return gr.update(choices=folder_choices(cur), value=cur)


def on_folder_select(selected: str):
    return make_folder_update(selected)


def go_parent_folder(path: str):
    cur = _normalize_path(path)
    if not cur:
        return make_folder_update(path)
    return make_folder_update(str(Path(cur).parent))


def parse_input_folders(value: str) -> list[Path]:
    """Parse one or more input folders from newlines or semicolons."""
    folders: list[Path] = []
    seen: set[str] = set()
    for item in re.split(r"[;\n]+", value or ""):
        text = item.strip()
        if not text:
            continue
        folder = Path(text).expanduser().resolve()
        key = str(folder)
        if key not in seen:
            seen.add(key)
            folders.append(folder)
    return folders


def append_input_folder(value: str, folder: str) -> str:
    """Append the browser's current folder to the multi-folder input."""
    folders = parse_input_folders(value)
    current = _normalize_path(folder)
    paths = [str(path) for path in folders]
    if current and current not in paths:
        paths.append(current)
    return "\n".join(paths)


def collect_bags(input_folders: list[Path]) -> tuple[list[Path], list[tuple[Path, int]]]:
    """Scan all roots and deduplicate bags from overlapping directories."""
    bags_by_path: dict[str, Path] = {}
    counts: list[tuple[Path, int]] = []
    for root in input_folders:
        found = scan_bags(root)
        counts.append((root, len(found)))
        for bag in found:
            resolved = bag.expanduser().resolve()
            bags_by_path.setdefault(str(resolved), resolved)
    return sorted(bags_by_path.values(), key=str), counts


def _bag_choices(bags: list[Path], roots: list[Path]) -> tuple[list[str], dict[str, str]]:
    """Build stable, unique display labels and their absolute bag paths."""
    choices: list[str] = []
    choice_map: dict[str, str] = {}
    for bag in bags:
        if len(roots) == 1:
            try:
                label = str(bag.relative_to(roots[0]))
            except ValueError:
                label = str(bag)
        else:
            label = str(bag)
        choices.append(label)
        choice_map[label] = str(bag)
    return choices, choice_map


# ---------- callbacks ----------

def _bag_topic_detail(bag_path: Path) -> tuple[pd.DataFrame, str, list[str]]:
    """读取单个 bag 的 Topic 表、元信息与 topic 名列表。"""
    info = get_bag_info(bag_path)
    rows = [
        [t.topic, t.msg_type, t.message_count, round(t.frequency_hz, 3)]
        for t in info.topics
    ]
    df = pd.DataFrame(rows, columns=TOPIC_HEADERS)
    meta = (
        f"**{info.name}** · 时长={info.duration_sec:.3f}s · "
        f"Topic 数={len(info.topics)}\n`{info.path}`"
    )
    topics: list[str] = []
    seen: set[str] = set()
    for t in info.topics:
        if t.topic not in seen:
            seen.add(t.topic)
            topics.append(t.topic)
    return df, meta, topics


def do_scan(input_folders_value: str):
    roots = parse_input_folders(input_folders_value)
    if not roots:
        return (
            "请填写至少一个输入目录（每行一个，或使用分号分隔）。",
            gr.update(),
            {},
            "",
            empty_topic_df(),
            gr.update(),
            gr.update(),
        )

    try:
        bags, counts = collect_bags(roots)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        return (
            f"扫描目录失败：{exc}",
            gr.update(),
            {},
            "",
            empty_topic_df(),
            make_folder_update(str(roots[0])),
            gr.update(),
        )

    choices, choice_map = _bag_choices(bags, roots)
    try:
        export_root = Path(os.path.commonpath([str(root) for root in roots]))
    except ValueError:
        export_root = roots[0]

    topics: list[str] = []
    topic_df = empty_topic_df()
    bag_meta = ""
    selected_rel = None
    if bags:
        # 扫描后自动读取首个 bag 的详细 Topic 信息
        selected_rel = choices[0]
        topic_df, bag_meta, topics = _bag_topic_detail(bags[0])

    state = {
        "input_folder": str(export_root),
        "input_folders": [str(root) for root in roots],
        "bag_choice_map": choice_map,
        "bags": [str(p) for p in bags],
        "topics": topics,
        "stats": [],
    }
    exclude_note = ", ".join(sorted(DEFAULT_EXCLUDE_TOPICS)) or "(无)"
    scanned_total = sum(count for _root, count in counts)
    msg = f"已扫描 **{len(roots)}** 个目录，找到 **{len(bags)}** 个不重复的 bag 文件。"
    if scanned_total != len(bags):
        msg += f"（重叠目录合计扫描到 {scanned_total} 个，已自动去重。）"
    msg += "\n\n" + "；".join(f"`{root}`：{count} 个" for root, count in counts)
    if topics:
        check = default_check_topics(topics)
        msg += (
            f" 已自动读取首个 bag 的详细信息（**{len(topics)}** 个 Topic）；"
            f"默认检查 **{len(check)}** 个"
            f"（已排除 Reference 与 {exclude_note}）。"
        )
    elif bags:
        msg += " 首个 bag 未能读出 Topic。"
    else:
        msg += " 未找到 `.bag` 文件。"

    return (
        msg,
        gr.update(choices=choices, value=selected_rel),
        state,
        bag_meta,
        topic_df,
        make_folder_update(str(roots[0])),
        _check_topic_update(topics),
    )


def do_show_topics(selected_rel: str, state: dict, check_topics: list[str] | None):
    if not state or not selected_rel:
        return (
            empty_topic_df(),
            "扫描后会自动展示首个 bag；也可在此切换查看其它 bag。",
            gr.update(),
        )

    mapped_path = (state.get("bag_choice_map") or {}).get(selected_rel)
    root = Path(state["input_folder"])
    bag_path = Path(mapped_path) if mapped_path else root / selected_rel
    if not bag_path.exists():
        for p in state.get("bags", []):
            if Path(p).name == Path(selected_rel).name or str(p).endswith(selected_rel):
                bag_path = Path(p)
                break
    if not bag_path.exists():
        return (
            empty_topic_df(),
            f"未找到 bag：{selected_rel}",
            gr.update(),
        )

    df, meta, topics = _bag_topic_detail(bag_path)
    state["topics"] = topics

    return (
        df,
        meta,
        _check_topic_update(topics, check_topics),
    )


def do_analyze(
    check_topics: list[str] | None,
    state: dict,
    detail_threshold: str,
    failed_only: bool = False,
    worker_count: int = DEFAULT_ANALYSIS_WORKERS,
):
    _analyze_stop.clear()
    targets = [t for t in (check_topics or []) if t and t != DEFAULT_REF_TOPIC]
    empty_sum = empty_summary_df()
    empty_bag = empty_bag_detail_df()
    empty_md = empty_per_bag_detail_html()
    try:
        tau = _parse_threshold(detail_threshold)
    except ValueError:
        tau = float(DEFAULT_THRESHOLDS_MS[0])

    if not state or not state.get("bags"):
        tip = "请先扫描 bag。"
        yield 0, tip, empty_sum, empty_bag, empty_md, state or {}
        return
    if not targets:
        tip = "请至少勾选一个要检查的 Topic（不含固定 Reference）。"
        yield 0, tip, empty_sum, empty_bag, empty_md, state
        return

    bags = [Path(p) for p in state["bags"]]

    for progress, status, stats in iter_measure_bags(
        bags,
        reference_topic=DEFAULT_REF_TOPIC,
        target_topics=targets,
        should_stop=_analyze_stop.is_set,
        max_workers=worker_count,
    ):
        state = {**state, "stats": stats, "check_topics": targets}
        table = stats_to_summary_df(stats) if stats else empty_sum
        bag_df = (
            stats_to_bag_detail_df(stats, threshold_ms=tau) if stats else empty_bag
        )
        detail_html = (
            stats_to_per_bag_detail_html(
                stats,
                check_topics=targets,
                threshold_ms=tau,
                failed_only=failed_only,
            )
            if stats
            else empty_md
        )
        yield progress, status, table, bag_df, detail_html, state
        if _analyze_stop.is_set():
            return


def refresh_detail_views(
    detail_threshold: str,
    state: dict,
    failed_only: bool = False,
):
    """切换详情判定阈值时，刷新 bag 汇总判定 + 每 bag Topic 表。"""
    empty_bag = empty_bag_detail_df()
    empty_md = empty_per_bag_detail_html()
    if not state or not state.get("stats"):
        return empty_bag, empty_md
    try:
        tau = _parse_threshold(detail_threshold)
    except ValueError:
        return empty_bag, f"阈值无效：{detail_threshold}"
    check = state.get("check_topics") or []
    bag_df = stats_to_bag_detail_df(state["stats"], threshold_ms=tau)
    detail_html = stats_to_per_bag_detail_html(
        state["stats"],
        check_topics=check,
        threshold_ms=tau,
        failed_only=failed_only,
    )
    return bag_df, detail_html

def do_stop_analyze():
    _analyze_stop.set()
    return "正在停止分析；等待中的任务将取消，当前 bag 完成后 worker 退出。"


def _threshold_choices() -> list[str]:
    return [f"{int(t)} ms" for t in DEFAULT_THRESHOLDS_MS]


def _parse_threshold(label: str) -> float:
    text = (label or "").strip().lower().replace("ms", "").strip()
    return float(text)


def preview_filter(
    threshold_label: str,
    output_folder: str,
    state: dict,
    worker_count: int = DEFAULT_ANALYSIS_WORKERS,
):
    """根据所选阈值预览可保留数量，并确认输出目录。"""
    out = _normalize_path(output_folder) or (output_folder or "").strip()
    if not state or not state.get("stats"):
        return (
            f"**输出目录:** `{out or '(未设置)'}`\n\n请先完成分析。",
            0,
        )
    try:
        tau = _parse_threshold(threshold_label)
    except ValueError:
        return (
            f"**输出目录:** `{out or '(未设置)'}`\n\n阈值无效：{threshold_label}",
            0,
        )

    keep = filter_keepable(state["stats"], tau)
    total = len(state["stats"])
    n_topics = len(state.get("check_topics") or [])
    workers = normalize_analysis_workers(worker_count)
    name_counts: dict[str, int] = {}
    for stat in keep:
        name = Path(stat.bag_path).name.casefold()
        name_counts[name] = name_counts.get(name, 0) + 1
    conflict_bags = sum(count for count in name_counts.values() if count > 1)
    existing = sum(
        1 for stat in keep if out and (Path(out) / Path(stat.bag_path).name).exists()
    )
    md = (
        f"**当前将导出到:** `{out or '(未设置)'}`\n\n"
        f"**所选阈值:** {tau:g} ms · **导出 Worker:** {workers}"
        + (f" · 检查 {n_topics} 个 Topic\n\n" if n_topics else "\n\n")
        + f"**可保留:** {len(keep)} / {total} 个 bag。所有 episode 将直接复制到上述目录，"
        "不保留源目录层级。\n\n"
        + f"**目标中已存在:** {existing} 个，将跳过且不覆盖。"
        + (
            f"\n\n**同名冲突:** {conflict_bags} 个 bag，需先处理重名后才能导出。"
            if conflict_bags
            else ""
        )
        + "\n\n可保留条件：所选 Topic 均存在，且相对 Reference 的 max_delay 均 ≤ 阈值。"
    )
    return md, len(keep)


def do_export(
    threshold_label: str,
    output_folder: str,
    state: dict,
    worker_count: int = DEFAULT_ANALYSIS_WORKERS,
):
    out_raw = (output_folder or "").strip()
    if not out_raw:
        tip = "请先设置输出目录。"
        yield 0, tip, tip, "**输出目录:** `(未设置)`"
        return
    if not state or not state.get("stats"):
        tip = "请先完成分析。"
        out = _normalize_path(out_raw) or out_raw
        yield 0, tip, tip, f"**输出目录:** `{out}`"
        return

    try:
        tau = _parse_threshold(threshold_label)
    except ValueError:
        tip = f"阈值无效：{threshold_label}"
        yield 0, tip, tip, f"**输出目录:** `{out_raw}`"
        return

    try:
        output_path = Path(out_raw).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        if not output_path.is_dir():
            raise NotADirectoryError(f"不是目录: {output_path}")
    except OSError as exc:
        tip = f"无法使用输出目录 `{out_raw}`: {exc}"
        yield 0, tip, tip, tip
        return
    out = str(output_path)
    root = Path(state["input_folder"])
    confirm = (
        f"**输出目录:** `{out}`\n\n"
        f"**所选阈值:** {tau:g} ms · **导出 Worker:** "
        f"{normalize_analysis_workers(worker_count)} — 开始复制可保留 bag…"
    )

    log_lines: list[str] = []
    for progress, line, status, _copied, _failed in copy_keepable_bags(
        state["stats"],
        threshold_ms=tau,
        input_root=root,
        output_folder=out,
        max_workers=worker_count,
        flat_output=True,
    ):
        log_lines.append(line)
        yield (
            progress,
            "\n".join(log_lines[-200:]),
            status,
            confirm if progress < 100 else (
                f"**输出目录:** `{out}`\n\n**所选阈值:** {tau:g} ms\n\n{status}"
            ),
        )


# ---------- UI ----------

def build_ui() -> gr.Blocks:
    in_path = str(DEFAULT_INPUT)
    out_path = str(DEFAULT_OUTPUT)
    thr_choices = _threshold_choices()
    exclude_note = ", ".join(sorted(DEFAULT_EXCLUDE_TOPICS))

    with gr.Blocks(
        title="ROS Bag 时间对齐检查工具",
        elem_classes=["app-shell"],
    ) as demo:
        gr.HTML(
            f"""
            <section class="app-hero">
              <div class="app-hero__eyebrow">ROS BAG QUALITY WORKBENCH</div>
              <h1>时间对齐检查工具</h1>
              <p>
                以固定 Reference 为主轴，批量检查所选 Topic 的时间偏差，
                快速定位缺失、无消息与超阈值问题，并按阈值导出合格 Bag。
              </p>
              <div class="app-hero__badges">
                <span class="app-hero__badge">Reference · {DEFAULT_REF_TOPIC}</span>
                <span class="app-hero__badge">多 Topic 联合判定</span>
                <span class="app-hero__badge">多阈值对比</span>
                <span class="app-hero__badge">结果筛选与导出</span>
              </div>
            </section>
            """
        )

        state = gr.State({})

        with gr.Accordion(
            "数据源与 Bag 浏览",
            open=True,
            elem_classes=["app-section", "step-01"],
        ):
            gr.HTML(
                '<div class="section-lead">'
                '可输入多个目录并一次扫描全部 Bag；每行一个目录，也支持用分号分隔。'
                '</div>'
            )
            input_folders = gr.Textbox(
                label="输入目录（可多个）",
                info="每行输入一个目录，也支持用分号分隔；扫描时自动合并并去重 Bag",
                value=in_path,
                lines=4,
                max_lines=10,
                interactive=True,
            )
            input_folder_browser = gr.Dropdown(
                label="目录浏览器",
                info="选择或输入一个目录，再点击“添加当前目录”放入上方输入框",
                choices=folder_choices(in_path),
                value=in_path,
                allow_custom_value=True,
                filterable=True,
                interactive=True,
            )
            with gr.Row(elem_classes=["action-row"]):
                input_add_btn = gr.Button("添加当前目录", size="sm")
                input_refresh_btn = gr.Button("刷新目录", size="sm")
                input_up_btn = gr.Button("返回上级", size="sm")
                scan_btn = gr.Button("扫描 Bag", variant="primary")
            scan_msg = gr.Markdown(
                "输入一个或多个目录后点击 **扫描 Bag**。",
                elem_classes=["status-note"],
            )

            with gr.Row(equal_height=True):
                with gr.Column(scale=1, min_width=320):
                    bag_list = gr.Dropdown(
                        label="Bag 列表",
                        info="扫描后自动展示首个 Bag，可切换查看其它文件",
                        choices=[],
                        interactive=True,
                        filterable=True,
                    )
                    bag_meta = gr.Markdown("", elem_classes=["bag-meta"])
                with gr.Column(scale=2, min_width=520):
                    topic_table = gr.Dataframe(
                        label="Topic 信息",
                        headers=TOPIC_HEADERS,
                        interactive=False,
                        wrap=True,
                        elem_classes=["data-panel"],
                    )

        with gr.Accordion(
            "时间对齐分析",
            open=True,
            elem_classes=["app-section", "step-02"],
        ):
            gr.HTML(
                '<div class="section-lead">'
                '选择需要联合检查的 Topics；任一 Topic 缺失、无消息或超过阈值，'
                '该 Bag 即判定为不合格。'
                '</div>'
            )
            with gr.Row():
                gr.Textbox(
                    label="Reference Topic",
                    value=DEFAULT_REF_TOPIC,
                    interactive=False,
                    scale=2,
                )
                detail_threshold = gr.Dropdown(
                    label="详情判定阈值",
                    info="联动下方 Bag 判定和 Topic 状态",
                    choices=thr_choices,
                    value=next(
                        (c for c in thr_choices if c.startswith("40")),
                        thr_choices[0],
                    ),
                    interactive=True,
                    scale=1,
                )
                worker_count = gr.Slider(
                    minimum=1,
                    maximum=MAX_ANALYSIS_WORKERS,
                    value=DEFAULT_ANALYSIS_WORKERS,
                    step=1,
                    label="并行 Worker 数",
                    info="默认 8；共享存储繁忙时可降低，最高 16",
                    interactive=True,
                    scale=1,
                )
            check_topics = gr.CheckboxGroup(
                label=f"检查对齐的 Topics（不含 Reference 与 {exclude_note}）",
                choices=[],
                value=[],
                interactive=True,
            )
            with gr.Row(elem_classes=["action-row"]):
                select_all_btn = gr.Button("全选默认 Topics", size="sm")
                clear_topics_btn = gr.Button("清空选择", size="sm")
                analyze_btn = gr.Button("开始分析", variant="primary")
                stop_btn = gr.Button("停止分析", variant="stop")
            with gr.Row():
                analyze_progress = gr.Slider(
                    0, 100, value=0, label="分析进度", interactive=False, scale=2
                )
                analyze_status = gr.Textbox(
                    label="当前状态", interactive=False, scale=3
                )
            thr_label = "/".join(f"{int(t)}" for t in DEFAULT_THRESHOLDS_MS)
            gr.HTML(
                '<div class="summary-guide">'
                '合格率以全部 Bag 为分母；“较前档新增”表示阈值放宽后新增的可保留 Bag 数。'
                '</div>'
            )
            summary_table = gr.Dataframe(
                label=f"多阈值总览（{thr_label} ms）",
                headers=SUMMARY_HEADERS,
                interactive=False,
                wrap=True,
                elem_classes=["data-panel"],
            )

        with gr.Accordion(
            "Bag 结果详情",
            open=True,
            elem_classes=["app-section", "step-03"],
        ):
            gr.HTML(
                '<div class="section-lead">'
                '不合格 Bag 优先展示；汇总表与卡片详情均可独立滚动查看。'
                '</div>'
            )
            with gr.Column(elem_classes=["bag-summary-scroll"]):
                bag_detail_table = gr.Dataframe(
                    label="每个 Bag 是否合格",
                    headers=BAG_DETAIL_HEADERS,
                    interactive=False,
                    wrap=True,
                    elem_classes=["data-panel"],
                )
            gr.HTML(
                '<div class="workflow-hint">'
                '<span>异常 Topic 置顶</span>'
                '<span>状态颜色区分</span>'
                '<span>每个 Bag 独立卡片</span>'
                '</div>'
            )
            failed_only = gr.Checkbox(
                label="只看不合格 Bag",
                value=False,
                info="勾选后仅显示当前判定阈值下不合格的 Bag 独立卡片",
                elem_classes=["bag-failed-filter"],
            )
            with gr.Column(elem_classes=["bag-detail-scroll"]):
                per_bag_detail_html = gr.HTML(
                    empty_per_bag_detail_html(),
                    elem_classes=["bag-detail-panel"],
                )

        with gr.Accordion(
            "筛选并导出",
            open=True,
            elem_classes=["app-section", "step-04"],
        ):
            gr.HTML(
                '<div class="section-lead">'
                '按所选阈值筛选合格 Bag，并将所有 episode 直接复制到同一个输出目录。'
                '</div>'
            )
            with gr.Row():
                export_threshold = gr.Dropdown(
                    label="导出阈值",
                    choices=thr_choices,
                    value=thr_choices[0],
                    interactive=True,
                    scale=1,
                )
                export_workers = gr.Slider(
                    minimum=1,
                    maximum=MAX_ANALYSIS_WORKERS,
                    value=DEFAULT_ANALYSIS_WORKERS,
                    step=1,
                    label="导出 Worker 数",
                    info="默认 8；共享存储带宽充足时可尝试 16",
                    interactive=True,
                    scale=1,
                )
                export_output = gr.Dropdown(
                    label="输出目录",
                    info="可选择目录，也可直接输入绝对路径；不存在时自动创建",
                    choices=folder_choices(out_path),
                    value=out_path,
                    allow_custom_value=True,
                    filterable=True,
                    interactive=True,
                    scale=3,
                )
            with gr.Row(elem_classes=["action-row"]):
                export_refresh_btn = gr.Button("刷新输出目录", size="sm")
                export_up_btn = gr.Button("输出目录返回上级", size="sm")
                preview_btn = gr.Button("预览可导出数量", size="sm")
                export_btn = gr.Button("导出合格 Bag", variant="primary")
            with gr.Column(elem_classes=["export-summary"]):
                export_confirm = gr.Markdown(
                    f"**当前输出目录:** `{out_path}`\n\n"
                    "完成分析后可预览数量，再执行导出。"
                )
            export_progress = gr.Slider(
                0, 100, value=0, label="导出进度", interactive=False
            )
            export_status = gr.Textbox(label="导出状态", interactive=False)
            export_log = gr.Textbox(
                label="导出日志", lines=7, max_lines=14, interactive=False
            )

        # folder events
        input_folder_browser.select(
            fn=on_folder_select,
            inputs=[input_folder_browser],
            outputs=[input_folder_browser],
        )
        input_add_btn.click(
            fn=append_input_folder,
            inputs=[input_folders, input_folder_browser],
            outputs=[input_folders],
        )
        input_refresh_btn.click(
            fn=on_folder_select,
            inputs=[input_folder_browser],
            outputs=[input_folder_browser],
        )
        input_up_btn.click(
            fn=go_parent_folder,
            inputs=[input_folder_browser],
            outputs=[input_folder_browser],
        )

        def _sync_output_confirm(out_path_val, thr, workers, st):
            md, _ = preview_filter(thr, out_path_val, st or {}, workers)
            return md

        def _select_all_topics(st: dict):
            topics = (st or {}).get("topics") or []
            return _check_topic_update(topics)

        def _clear_topics(st: dict):
            topics = (st or {}).get("topics") or []
            defaults = default_check_topics(topics, reference_topic=DEFAULT_REF_TOPIC)
            return gr.update(choices=defaults, value=[])

        select_all_btn.click(
            fn=_select_all_topics,
            inputs=[state],
            outputs=[check_topics],
        )
        clear_topics_btn.click(
            fn=_clear_topics,
            inputs=[state],
            outputs=[check_topics],
        )

        export_output.select(
            fn=on_folder_select,
            inputs=[export_output],
            outputs=[export_output],
        )
        export_refresh_btn.click(
            fn=on_folder_select,
            inputs=[export_output],
            outputs=[export_output],
        )
        export_up_btn.click(
            fn=go_parent_folder,
            inputs=[export_output],
            outputs=[export_output],
        )
        export_output.change(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_workers, state],
            outputs=[export_confirm],
        )
        export_threshold.change(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_workers, state],
            outputs=[export_confirm],
        )
        export_workers.change(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_workers, state],
            outputs=[export_confirm],
        )
        preview_btn.click(
            fn=lambda thr, out, workers, st: preview_filter(
                thr, out, st or {}, workers
            )[0],
            inputs=[export_threshold, export_output, export_workers, state],
            outputs=[export_confirm],
        )

        scan_btn.click(
            fn=do_scan,
            inputs=[input_folders],
            outputs=[
                scan_msg,
                bag_list,
                state,
                bag_meta,
                topic_table,
                input_folder_browser,
                check_topics,
            ],
        )

        bag_list.change(
            fn=do_show_topics,
            inputs=[bag_list, state, check_topics],
            outputs=[topic_table, bag_meta, check_topics],
        )

        analyze_event = analyze_btn.click(
            fn=do_analyze,
            inputs=[check_topics, state, detail_threshold, failed_only, worker_count],
            outputs=[
                analyze_progress,
                analyze_status,
                summary_table,
                bag_detail_table,
                per_bag_detail_html,
                state,
            ],
        )
        stop_btn.click(
            fn=do_stop_analyze,
            inputs=None,
            outputs=[analyze_status],
            cancels=[analyze_event],
        )

        detail_threshold.change(
            fn=refresh_detail_views,
            inputs=[detail_threshold, state, failed_only],
            outputs=[bag_detail_table, per_bag_detail_html],
        )
        failed_only.change(
            fn=refresh_detail_views,
            inputs=[detail_threshold, state, failed_only],
            outputs=[bag_detail_table, per_bag_detail_html],
        )

        analyze_event.then(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_workers, state],
            outputs=[export_confirm],
        )

        export_btn.click(
            fn=do_export,
            inputs=[export_threshold, export_output, state, export_workers],
            outputs=[export_progress, export_log, export_status, export_confirm],
        )

    return demo


def main() -> None:
    demo = build_ui()
    demo.queue().launch(
        server_name="0.0.0.0",
        server_port=7888,
        share=False,
        show_error=True,
        css=APP_CSS,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()
