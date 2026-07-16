#!/usr/bin/env python3
"""ROS Bag 时间对齐检查工具 — Gradio Web GUI。

用法:
  pip install -r requirements.txt
  python app.py
  # 浏览器打开 http://<服务器IP>:7860
"""

from __future__ import annotations

import logging
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
    sampling_stats_to_summary_df,
)
from styles import APP_CSS

from core import (
    DEFAULT_EXCLUDE_TOPICS,
    DEFAULT_INPUT,
    DEFAULT_OUTPUT,
    DEFAULT_REF_TOPIC,
    DEFAULT_THRESHOLDS_MS,
    STATUS_ORDER,
    copy_keepable_bags,
    default_check_topics,
    filter_keepable,
    get_bag_info,
    iter_measure_bags,
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
    except PermissionError:
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
    """解析换行或分号分隔的输入目录，并保持用户填写顺序。"""
    parts = [part.strip() for part in re.split(r"[;\r\n]+", value or "")]
    roots: list[Path] = []
    seen: set[Path] = set()
    for part in parts:
        if not part:
            continue
        root = Path(part).expanduser().resolve()
        if root not in seen:
            seen.add(root)
            roots.append(root)
    return roots


def add_input_folder(current: str, selected: str) -> str:
    roots = parse_input_folders(current)
    selected_text = _normalize_path(selected)
    if selected_text:
        selected_root = Path(selected_text)
        if selected_root not in roots:
            roots.append(selected_root)
    return "\n".join(str(root) for root in roots)


def _parse_head_skip_frames(value: str | list[str] | list[int]) -> list[int]:
    if isinstance(value, list):
        parts = [str(part).strip() for part in value]
    else:
        parts = [part.strip() for part in re.split(r"[,，;；\s]+", value or "")]
    values: list[int] = []
    for part in parts:
        if not part:
            continue
        number = int(part)
        if number < 0:
            raise ValueError("开头跳过帧数不能为负数")
        if number not in values:
            values.append(number)
    if not values:
        raise ValueError("请至少填写一个开头跳过帧数")
    return values


def _stats_for_head_skip(state: dict, label: str | int | None):
    try:
        head_skip = int(str(label).replace("帧", "").strip())
    except (TypeError, ValueError):
        head_skip = None
    by_skip = (state or {}).get("stats_by_head_skip") or {}
    if head_skip is not None:
        return by_skip.get(head_skip) or by_skip.get(str(head_skip)) or []
    return (state or {}).get("stats") or []


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


def do_scan(input_folders: str):
    if not (input_folders or "").strip():
        return (
            "请至少填写一个输入目录。",
            gr.update(),
            {},
            "",
            empty_topic_df(),
            input_folders,
            gr.update(),
        )

    roots = parse_input_folders(input_folders)
    bags: list[Path] = []
    bag_roots: dict[str, str] = {}
    errors: list[str] = []
    seen_bags: set[Path] = set()
    for root in roots:
        try:
            root_bags = scan_bags(root)
        except (FileNotFoundError, PermissionError) as exc:
            errors.append(str(exc))
            continue
        for bag in root_bags:
            resolved = bag.resolve()
            if resolved in seen_bags:
                continue
            seen_bags.add(resolved)
            bags.append(resolved)
            bag_roots[str(resolved)] = str(root)

    choices: list[str] = []
    bag_choices: dict[str, str] = {}
    for p in bags:
        root = Path(bag_roots[str(p)])
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = p.name
        root_index = roots.index(root) + 1
        label = f"[{root_index}] {root.name or root.anchor} / {rel}"
        choices.append(label)
        bag_choices[label] = str(p)

    topics: list[str] = []
    topic_df = empty_topic_df()
    bag_meta = ""
    selected_rel = None
    if bags:
        # 扫描后自动读取首个 bag 的详细 Topic 信息
        selected_rel = choices[0]
        topic_df, bag_meta, topics = _bag_topic_detail(bags[0])

    state = {
        "input_folders": [str(root) for root in roots],
        "bags": [str(p) for p in bags],
        "bag_roots": bag_roots,
        "bag_choices": bag_choices,
        "topics": topics,
        "stats": [],
        "stats_by_head_skip": {},
    }
    exclude_note = ", ".join(sorted(DEFAULT_EXCLUDE_TOPICS)) or "(无)"
    msg = f"从 **{len(roots)}** 个输入目录找到 **{len(bags)}** 个 bag 文件。"
    if errors:
        msg += " 无法读取：" + "；".join(errors)
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
        "\n".join(str(root) for root in roots),
        _check_topic_update(topics),
    )


def do_show_topics(selected_rel: str, state: dict, check_topics: list[str] | None):
    if not state or not selected_rel:
        return (
            empty_topic_df(),
            "扫描后会自动展示首个 bag；也可在此切换查看其它 bag。",
            gr.update(),
        )

    bag_path = Path((state.get("bag_choices") or {}).get(selected_rel, selected_rel))
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
    head_skip_frames_value: list[str] | list[int],
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
        yield 0, tip, empty_sum, empty_bag, empty_md, state or {}, gr.update(), gr.update()
        return
    if not targets:
        tip = "请至少勾选一个要检查的 Topic（不含固定 Reference）。"
        yield 0, tip, empty_sum, empty_bag, empty_md, state, gr.update(), gr.update()
        return
    try:
        head_skip_values = _parse_head_skip_frames(head_skip_frames_value)
    except ValueError as exc:
        tip = f"跳帧参数无效：{exc}"
        yield 0, tip, empty_sum, empty_bag, empty_md, state, gr.update(), gr.update()
        return

    bags = [Path(p) for p in state["bags"]]
    stats_by_head_skip: dict[int, list] = {}
    detail_head_skip = head_skip_values[0]
    choices = [str(value) for value in head_skip_values]

    for config_index, head_skip in enumerate(head_skip_values):
        for local_progress, status, stats in iter_measure_bags(
            bags,
            reference_topic=DEFAULT_REF_TOPIC,
            target_topics=targets,
            head_skip_frames=head_skip,
            should_stop=_analyze_stop.is_set,
        ):
            stats_by_head_skip[head_skip] = stats
            progress = round(
                (config_index + local_progress / 100.0)
                / len(head_skip_values)
                * 100,
                1,
            )
            detail_stats = stats_by_head_skip.get(detail_head_skip, [])
            state = {
                **state,
                "stats": detail_stats,
                "stats_by_head_skip": stats_by_head_skip,
                "head_skip_values": head_skip_values,
                "check_topics": targets,
            }
            table = sampling_stats_to_summary_df(stats_by_head_skip)
            bag_df = (
                stats_to_bag_detail_df(detail_stats, threshold_ms=tau)
                if detail_stats
                else empty_bag
            )
            detail_html = (
                stats_to_per_bag_detail_html(
                    detail_stats,
                    check_topics=targets,
                    threshold_ms=tau,
                )
                if detail_stats
                else empty_md
            )
            prefixed_status = f"跳过 {head_skip} 帧 · {status}"
            dropdown_update = gr.update(choices=choices, value=str(detail_head_skip))
            yield (
                progress,
                prefixed_status,
                table,
                bag_df,
                detail_html,
                state,
                dropdown_update,
                dropdown_update,
            )
            if _analyze_stop.is_set():
                tip = "分析已停止。"
                yield (
                    progress,
                    tip,
                    table,
                    bag_df,
                    detail_html,
                    state,
                    dropdown_update,
                    dropdown_update,
                )
                return


def refresh_detail_views(detail_threshold: str, detail_head_skip: str, state: dict):
    """切换详情判定阈值时，刷新 bag 汇总判定 + 每 bag Topic 表。"""
    empty_bag = empty_bag_detail_df()
    empty_md = empty_per_bag_detail_html()
    stats = _stats_for_head_skip(state, detail_head_skip)
    if not stats:
        return empty_bag, empty_md
    try:
        tau = _parse_threshold(detail_threshold)
    except ValueError:
        return empty_bag, f"阈值无效：{detail_threshold}"
    check = state.get("check_topics") or []
    bag_df = stats_to_bag_detail_df(stats, threshold_ms=tau)
    detail_html = stats_to_per_bag_detail_html(
        stats,
        check_topics=check,
        threshold_ms=tau,
    )
    return bag_df, detail_html

def do_stop_analyze():
    _analyze_stop.set()
    return "正在停止分析…"


def _threshold_choices() -> list[str]:
    return [f"{int(t)} ms" for t in DEFAULT_THRESHOLDS_MS]


def _parse_threshold(label: str) -> float:
    text = (label or "").strip().lower().replace("ms", "").strip()
    return float(text)


def preview_filter(
    threshold_label: str,
    head_skip_label: str,
    output_folder: str,
    state: dict,
):
    """根据所选阈值预览可保留数量，并确认输出目录。"""
    out = _normalize_path(output_folder) or (output_folder or "").strip()
    stats = _stats_for_head_skip(state, head_skip_label)
    if not stats:
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

    keep = filter_keepable(stats, tau)
    total = len(stats)
    n_topics = len(state.get("check_topics") or [])
    md = (
        f"**当前将导出到:** `{out or '(未设置)'}`\n\n"
        f"**开头跳过:** {head_skip_label} 帧 · **所选阈值:** {tau:g} ms"
        + (f" · 检查 {n_topics} 个 Topic\n\n" if n_topics else "\n\n")
        + f"**可保留:** {len(keep)} / {total} 个 bag 将被复制到上述输出目录（保留相对路径）。\n\n"
        "可保留条件：所选 Topic 均存在，且相对 Reference 的 max_delay 均 ≤ 阈值。"
    )
    return md, len(keep)


def do_export(
    threshold_label: str,
    head_skip_label: str,
    output_folder: str,
    state: dict,
):
    out_raw = (output_folder or "").strip()
    if not out_raw:
        tip = "请先设置输出目录。"
        yield 0, tip, tip, "**输出目录:** `(未设置)`"
        return
    stats = _stats_for_head_skip(state, head_skip_label)
    if not stats:
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

    out = str(Path(out_raw).expanduser().resolve())
    Path(out).mkdir(parents=True, exist_ok=True)
    roots = [Path(root) for root in state.get("input_folders", [])]
    confirm = (
        f"**输出目录:** `{out}`\n\n"
        f"**开头跳过:** {head_skip_label} 帧 · **所选阈值:** {tau:g} ms"
        " — 开始复制可保留 bag…"
    )

    log_lines: list[str] = []
    for progress, line, status, _copied, _failed in copy_keepable_bags(
        stats,
        threshold_ms=tau,
        input_roots=roots,
        output_folder=out,
    ):
        log_lines.append(line)
        yield (
            progress,
            "\n".join(log_lines[-200:]),
            status,
            confirm if progress < 100 else (
                f"**输出目录:** `{out}`\n\n**开头跳过:** {head_skip_label} 帧"
                f" · **所选阈值:** {tau:g} ms\n\n{status}"
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
                '选择输入目录并扫描 Bag；扫描后可切换 Bag 查看 Topic 元信息。'
                '</div>'
            )
            input_folders = gr.Textbox(
                label="输入目录（可多选）",
                info="每行填写一个目录；也可以用下方目录浏览器逐个添加",
                value=in_path,
                lines=3,
                interactive=True,
            )
            folder_browser = gr.Dropdown(
                label="目录浏览器",
                info="选择或输入目录后，点击“添加到输入目录”",
                choices=folder_choices(in_path),
                value=in_path,
                allow_custom_value=True,
                filterable=True,
                interactive=True,
            )
            with gr.Row(elem_classes=["action-row"]):
                input_refresh_btn = gr.Button("刷新目录", size="sm")
                input_up_btn = gr.Button("返回上级", size="sm")
                input_add_btn = gr.Button("添加到输入目录", size="sm")
                input_clear_btn = gr.Button("清空目录", size="sm")
                scan_btn = gr.Button("扫描 Bag", variant="primary")
            scan_msg = gr.Markdown(
                "选择输入目录后点击 **扫描 Bag**。",
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
                head_skip_frames = gr.CheckboxGroup(
                    label="开头跳过帧数",
                    info="选择需要生成报表的固定跳帧配置",
                    choices=["0", "15", "30", "45", "60"],
                    value=["0", "15", "30", "45", "60"],
                    scale=1,
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
                detail_head_skip = gr.Dropdown(
                    label="详情跳帧数",
                    info="分析后切换对应的 Bag 详情",
                    choices=["0"],
                    value="0",
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
                '每个开头跳帧配置分别生成多阈值统计；合格率以全部 Bag 为分母；'
                '“较前档新增”表示同一跳帧配置下，阈值放宽后新增的可保留 Bag 数。'
                '</div>'
            )
            summary_table = gr.Dataframe(
                label=f"跳帧数 × 阈值总览（{thr_label} ms）",
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
                '按所选阈值筛选所有检查 Topic 均合格的 Bag，并保留相对路径复制。'
                '</div>'
            )
            with gr.Row():
                export_head_skip = gr.Dropdown(
                    label="导出跳帧数",
                    choices=["0"],
                    value="0",
                    interactive=True,
                    scale=1,
                )
                export_threshold = gr.Dropdown(
                    label="导出阈值",
                    choices=thr_choices,
                    value=thr_choices[0],
                    interactive=True,
                    scale=1,
                )
                export_output = gr.Textbox(
                    label="输出目录",
                    info="目录不存在时自动创建",
                    value=out_path,
                    placeholder="/mnt/data/kuavo/tmp/your_export_dir",
                    interactive=True,
                    scale=3,
                )
            with gr.Row(elem_classes=["action-row"]):
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
        folder_browser.select(
            fn=on_folder_select,
            inputs=[folder_browser],
            outputs=[folder_browser],
        )
        input_refresh_btn.click(
            fn=on_folder_select,
            inputs=[folder_browser],
            outputs=[folder_browser],
        )
        input_up_btn.click(
            fn=go_parent_folder,
            inputs=[folder_browser],
            outputs=[folder_browser],
        )
        input_add_btn.click(
            fn=add_input_folder,
            inputs=[input_folders, folder_browser],
            outputs=[input_folders],
        )
        input_clear_btn.click(fn=lambda: "", outputs=[input_folders])

        def _sync_output_confirm(out_path_val, thr, head_skip, st):
            md, _ = preview_filter(thr, head_skip, out_path_val, st or {})
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

        export_output.change(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_head_skip, state],
            outputs=[export_confirm],
        )
        export_output.submit(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_head_skip, state],
            outputs=[export_confirm],
        )
        export_threshold.change(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_head_skip, state],
            outputs=[export_confirm],
        )
        export_head_skip.change(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_head_skip, state],
            outputs=[export_confirm],
        )
        preview_btn.click(
            fn=lambda thr, skip, out, st: preview_filter(thr, skip, out, st or {})[0],
            inputs=[export_threshold, export_head_skip, export_output, state],
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
                input_folders,
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
            inputs=[check_topics, state, detail_threshold, head_skip_frames],
            outputs=[
                analyze_progress,
                analyze_status,
                summary_table,
                bag_detail_table,
                per_bag_detail_html,
                state,
                detail_head_skip,
                export_head_skip,
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
            inputs=[detail_threshold, detail_head_skip, state],
            outputs=[bag_detail_table, per_bag_detail_html],
        )
        detail_head_skip.change(
            fn=refresh_detail_views,
            inputs=[detail_threshold, detail_head_skip, state],
            outputs=[bag_detail_table, per_bag_detail_html],
        )

        analyze_event.then(
            fn=_sync_output_confirm,
            inputs=[export_output, export_threshold, export_head_skip, state],
            outputs=[export_confirm],
        )

        export_btn.click(
            fn=do_export,
            inputs=[export_threshold, export_head_skip, export_output, state],
            outputs=[export_progress, export_log, export_status, export_confirm],
        )

    return demo


def main() -> None:
    demo = build_ui()
    demo.queue().launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        css=APP_CSS,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()
