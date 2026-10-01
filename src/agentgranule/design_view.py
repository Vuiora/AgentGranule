"""Native task-module review, 3D projection and explicit human allocation.

The canvas uses translucent extruded module regions in one perspective scene.
Only the two confirmation callbacks persist an accepted graph or allocation.
Geometry and the pending allocation model are independent of Tk.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .algorithms import Task, topological_order
from .core import GranuleError, Project, _design_effort_units, _text
from .venn import hit_regions, region_positions, region_vertices, render_scene

MIN_VOLUME = 0.125
MAX_VOLUME = 8.0
PLACEHOLDER_VOLUME = 1.0
CUBE_FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
              (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
REGION_COLORS = ((55, 147, 117), (83, 127, 194), (188, 107, 144), (208, 154, 65),
                 (137, 116, 191), (56, 153, 173), (183, 115, 76), (110, 156, 78))


def module_label_positions(centers, width, height):
    """Place readable callouts around the shared scene, independent of depth."""
    groups = [[], []]
    for module_id, x, y, depth in centers:
        groups[x >= width / 2].append((module_id, x, y, depth))
    placements = {}
    for side, group in enumerate(groups):
        group.sort(key=lambda center: (center[2], center[0]))
        # Every region retains a numbered target. With many modules, only its
        # selected full label is overlaid; the module list holds all full names.
        expanded = len(group) * 38 <= max(1, height - 70)
        for index, (module_id, x, y, _) in enumerate(group):
            label_y = 42 + (index + 0.5) * max(1, height - 76) / max(1, len(group))
            placements[module_id] = {"x": 14 if side == 0 else width - 14,
                                     "y": label_y, "anchor": "w" if side == 0 else "e",
                                     "expanded": expanded, "center": (x, y)}
    return placements


def fitted_scene_camera(positions, width, height, *, yaw=0.6, pitch=-0.35, zoom=1.0):
    """Fit every maximum-size region, keeping callouts outside the diagram."""
    vertices = [point for position in positions.values() for point in region_vertices(position, 1.0)]
    extent = max((math.sqrt(sum(v * v for v in point)) for point in vertices), default=0)
    distance = max(8, extent * 1.5 + 6)
    projected = [project_point(point, yaw=yaw, pitch=pitch, distance=distance, focal=1, center=(0, 0))
                 for point in vertices]
    x_span = max((abs(point[0]) for point in projected), default=1)
    y_span = max((abs(point[1]) for point in projected), default=1)
    focal = min(max(35, width / 2 - 145) / max(x_span, 1e-9),
                max(35, height / 2 - 38) / max(y_span, 1e-9)) * zoom
    return {"yaw": yaw, "pitch": pitch, "distance": distance, "focal": focal,
            "center": (width / 2, height / 2 + 4)}


def effort_volume(effort):
    """A visible zero, a flagged fixed placeholder, and linear volume mapping."""
    if effort is None:
        return PLACEHOLDER_VOLUME
    units = _design_effort_units(effort)
    return MIN_VOLUME + (MAX_VOLUME - MIN_VOLUME) * units / 100


def cube_vertices(center, effort):
    half = effort_volume(effort) ** (1 / 3) / 2
    return [(center[0] + x * half, center[1] + y * half, center[2] + z * half)
            for x, y, z in ((-1, -1, -1), (1, -1, -1), (-1, 1, -1), (1, 1, -1),
                            (-1, -1, 1), (1, -1, 1), (-1, 1, 1), (1, 1, 1))]


def project_point(point, *, yaw=0.6, pitch=-0.35, distance=10, focal=500, center=(0, 0)):
    """Rotate actual 3D coordinates, then perspective-project into the canvas."""
    x, y, z = point
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    x, z = x * cy + z * sy, -x * sy + z * cy
    y, z = y * cp - z * sp, y * sp + z * cp
    depth = distance + z
    if depth <= 0:
        raise GranuleError("3D camera must remain outside the module graph")
    return (center[0] + x * focal / depth, center[1] - y * focal / depth, depth)


def module_positions(modules):
    """Stable DAG columns with a little depth; parent edges are separate."""
    lookup = {module["id"]: module for module in modules}
    levels = {}
    order = topological_order([Task(module["id"], module["id"], "layout", module.get("depends_on", []))
                               for module in modules])
    for task in order:
        key = task.id
        levels[key] = max((levels[dep] + 1 for dep in lookup[key].get("depends_on", [])), default=0)
    columns = {}
    for key in sorted(lookup):
        columns.setdefault(levels[key], []).append(key)
    maximum = max(levels.values(), default=0)
    return {key: ((level - maximum / 2) * 3.4,
                  ((len(keys) - 1) / 2 - i) * 3.0, (i % 2 - 0.5) * 0.8)
            for level, keys in columns.items() for i, key in enumerate(keys)}


def point_in_polygon(point, polygon):
    x, y = point
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        ax, ay = a[:2]
        bx, by = b[:2]
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if abs(cross) < 1e-7 and min(ax, bx) - 1e-7 <= x <= max(ax, bx) + 1e-7 and min(ay, by) - 1e-7 <= y <= max(ay, by) + 1e-7:
            return True
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def pick_module(point, faces, centers=()):
    """Pick the nearest projected face; small modules get an 18px target."""
    hits = [face for face in faces if point_in_polygon(point, face["points"])]
    if hits:
        return min(hits, key=lambda face: face["depth"])["module_id"]
    nearby = [(math.hypot(point[0] - x, point[1] - y), module_id, depth)
              for module_id, x, y, depth in centers if math.hypot(point[0] - x, point[1] - y) <= 18]
    return min(nearby, default=(0, None, 0), key=lambda item: (item[0], item[2]))[1]


def parse_effort_text(text):
    """Parse exact human decimal input without rounding or lossy float casts."""
    if not isinstance(text, str) or not text.strip():
        raise GranuleError("请输入 0.00–1.00 的设计力度")
    try:
        value = Decimal(text.strip())
    except InvalidOperation as exc:
        raise GranuleError("请输入有效小数，例如 0.37") from exc
    if not value.is_finite() or not 0 <= value <= 1:
        raise GranuleError("设计力度范围为 0.00–1.00")
    # Find a small candidate, then compare the original decimal exactly. This
    # avoids huge integer denominators for inputs such as 1e-999999999, while
    # still rejecting extra precision rather than rounding it into a valid tick.
    try:
        units = _design_effort_units(float(value))
    except GranuleError as exc:
        raise GranuleError("设计力度步长为 0.01，不自动四舍五入") from exc
    if value != Decimal(f"{units / 100:.2f}"):
        raise GranuleError("设计力度步长为 0.01，不自动四舍五入")
    return units


class AllocationModel:
    """Pending human choices are local, separate from persisted snapshots."""

    def __init__(self, graph, snapshot):
        self.graph = graph
        self.snapshot = snapshot
        self.controls = {(c.get("module_id", c.get("problem_id")), c["direction"]): c
                         for c in snapshot["controls"]}
        self.choices = {}
        for module in graph["analysis"]["modules"]:
            actual = graph["module_ids"][module["id"]]
            for direction in module["directions"]:
                control = self.controls[(actual, direction)]
                value = control["parameters"].get("design_effort")
                # Project defaults remain suggestions until a human applies them.
                self.choices[(actual, direction)] = (_design_effort_units(value)
                    if value is not None and control["source"] == "module" else None)

    def effort(self, logical_id, direction):
        units = self.choices.get((self.graph["module_ids"][logical_id], direction))
        return None if units is None else units / 100

    def set_units(self, logical_id, direction, units):
        key = (self.graph["module_ids"][logical_id], direction)
        if key not in self.choices:
            raise GranuleError("该模块没有这个处理方向")
        if type(units) is not int or not 0 <= units <= 100:
            raise GranuleError("设计力度必须使用 0–100 的整数刻度")
        self.choices[key] = units

    def unassigned(self):
        return [key for key, value in self.choices.items() if value is None]

    def submitted_choices(self):
        if self.unassigned():
            raise GranuleError("请为每个模块的每个方向明确分配设计力度")
        return [{"module_id": module, "direction": direction, "design_effort": units / 100}
                for (module, direction), units in self.choices.items()]


class DesignControl:
    """Open a separate database connection for each UI operation."""

    def __init__(self, database):
        _text(str(database), "database")
        if str(database) == ":memory:":
            raise GranuleError("Design view requires a persistent file database")
        self.database = database

    def _call(self, operation, *args):
        from .design import Design
        with Project(self.database) as project:
            return getattr(Design(project), operation)(*args)

    def get_analysis(self, analysis_id):
        return self._call("get_analysis", analysis_id)

    def update_analysis(self, analysis_id, revision, analysis):
        return self._call("update_analysis", analysis_id, revision, analysis)

    def approve_analysis(self, analysis_id, revision):
        return self._call("approve_analysis", analysis_id, revision, "human:design-view")

    def allocation_snapshot(self, analysis_id):
        return self._call("allocation_snapshot", analysis_id)

    def save_allocation(self, snapshot, choices):
        return self._call("save_allocation", snapshot, choices, "human:design-view")

    def workflow_status(self, workflow_id):
        from .workflow import Workflow
        with Project(self.database) as project:
            return Workflow(project).workflow_status(workflow_id)


def show_design(service, analysis_id):
    import tkinter as tk
    from tkinter import messagebox, ttk

    graph = service.get_analysis(analysis_id)
    result = {"status": "cancelled"}
    pending = copy.deepcopy(graph["analysis"])
    model = None
    report = None
    selected = None
    loading = False
    camera = {"yaw": 0.6, "pitch": -0.35, "zoom": 1.0}
    rendered = {"faces": [], "centers": [], "scene": None, "image": None, "redraw": None}
    root = tk.Tk()
    root.title("AgentGranule · 同图 3D 模块与设计力度")
    screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
    window_width = min(1120, max(480, screen_width - 80))
    window_height = min(780, max(420, screen_height - 120))
    root.geometry(f"{window_width}x{window_height}+{max(0, (screen_width - window_width) // 2)}"
                  f"+{max(0, (screen_height - window_height - 80) // 2)}")
    root.minsize(min(900, window_width), min(680, window_height))
    root.configure(bg="#f3f5f0")
    root.option_add("*Font", ("Microsoft YaHei UI", 10))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Treeview", rowheight=29, background="#ffffff", fieldbackground="#ffffff")
    style.configure("TCombobox", padding=5)
    outer = ttk.Frame(root, padding=16)
    outer.pack(fill="both", expand=True)
    # Reserve the action/status rows. Content must shrink before confirmation
    # controls: pack's earlier expanding pane used to push them off the window.
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(2, weight=1)
    phase_var = tk.StringVar()
    ttk.Label(outer, text="AGENTGRANULE · 模块设计", font=("Microsoft YaHei UI", 17, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(outer, textvariable=phase_var).grid(row=1, column=0, sticky="w", pady=(6, 10))
    panes = ttk.Panedwindow(outer, orient="horizontal")
    panes.grid(row=2, column=0, sticky="nsew")
    left, right = ttk.Frame(panes), ttk.Frame(panes)
    panes.add(left, weight=1)
    panes.add(right, weight=3)
    for pane in (left, right):
        pane.columnconfigure(0, weight=1)
    left.rowconfigure(0, weight=1)
    right.rowconfigure(2, weight=1)
    tree_frame = ttk.Frame(left)
    tree_frame.grid(row=0, column=0, sticky="nsew")
    tree = ttk.Treeview(tree_frame, columns=("effort", "state"), show="tree headings", selectmode="browse", height=11)
    tree.heading("#0", text="任务模块 · 全部同图")
    tree.heading("effort", text="当前方向")
    tree.heading("state", text="任务状态")
    tree.column("#0", width=168, minwidth=130)
    tree.column("effort", width=85, minwidth=76, stretch=False)
    tree.column("state", width=84, minwidth=76, stretch=False)
    scroll = ttk.Scrollbar(tree_frame, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    edit_buttons = ttk.Frame(left)
    edit_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 6))
    detail_var = tk.StringVar(value="选择一个模块查看职责与分析依据。")
    detail_frame = ttk.Frame(left)
    detail_frame.grid(row=2, column=0, sticky="ew", pady=(4, 8))
    detail = tk.Text(detail_frame, height=8, width=30, wrap="word", relief="flat", padx=8, pady=6)
    detail_scroll = ttk.Scrollbar(detail_frame, command=detail.yview)
    detail.configure(yscrollcommand=detail_scroll.set, state="disabled")
    detail.pack(side="left", fill="x", expand=True)
    detail_scroll.pack(side="right", fill="y")

    def detail_updated(*_):
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("1.0", detail_var.get())
        detail.configure(state="disabled")

    detail_var.trace_add("write", detail_updated)
    ttk.Label(left, text="分解说明（确认前可编辑）").grid(row=3, column=0, sticky="w")
    summary = tk.Text(left, height=5, width=34, wrap="word", relief="flat", padx=8, pady=8)
    summary.insert("1.0", pending["summary"])
    summary.grid(row=4, column=0, sticky="ew")
    ttk.Label(right, text="同一张 3D 图 · 全部模块", font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w")
    toolbar = ttk.Frame(right)
    toolbar.grid(row=1, column=0, sticky="ew")
    ttk.Label(toolbar, text="处理方向").pack(side="left")
    direction_var = tk.StringVar()
    direction_picker = ttk.Combobox(toolbar, textvariable=direction_var, state="readonly", width=22)
    direction_picker.pack(side="left", padx=8)
    ttk.Label(toolbar, text="拖动旋转 · 滚轮缩放 · 重叠处可选模块", foreground="#617369").pack(side="right")
    canvas = tk.Canvas(right, background="#f0f5f1", highlightthickness=1,
                       highlightbackground="#d9e4db", width=620, height=370)
    canvas.grid(row=2, column=0, sticky="nsew", pady=(8, 6))
    legend = ttk.Label(right, text="所有半透明模块片区共享同一场景；重叠只表示同图展示。固定厚度下，面积和体积随当前方向的力度变化。\n待分配为占位尺寸；0.00 仍可见；不适用方向用灰色。虚线为父关系，箭头为执行依赖。",
                       foreground="#64786b", justify="left", wraplength=620)
    legend.grid(row=3, column=0, sticky="w")
    allocation = ttk.LabelFrame(right, text="选中模块的设计力度 · 0.00–1.00", padding=10)
    allocation.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    effort_name = tk.StringVar(value="先确认模块清单")
    effort_label = ttk.Label(allocation, textvariable=effort_name, wraplength=600)
    effort_label.pack(anchor="w")
    entry_row = ttk.Frame(allocation)
    entry_row.pack(fill="x", pady=(6, 2))
    units_var = tk.IntVar(value=50)
    effort_text = tk.StringVar(value="0.50")
    entry = ttk.Entry(entry_row, textvariable=effort_text, width=8)
    entry.pack(side="right", padx=(8, 0))
    scale = tk.Scale(entry_row, from_=0, to=100, variable=units_var, resolution=1, orient="horizontal",
                     showvalue=False, highlightthickness=0, width=9, sliderlength=25, takefocus=True,
                     bg="#f3f5f0", troughcolor="#dce8dd", activebackground="#30785e")
    scale.pack(side="left", fill="x", expand=True)
    apply_button = ttk.Button(allocation, text="应用当前值")
    apply_button.pack(anchor="e", pady=(4, 0))
    status_var = tk.StringVar()
    status_label = ttk.Label(outer, textvariable=status_var, wraplength=1040, foreground="#5e7465")
    status_label.grid(row=3, column=0, sticky="ew", pady=(10, 6))
    outer.bind("<Configure>", lambda event: status_label.configure(wraplength=max(100, event.width - 8)))
    right.bind("<Configure>", lambda event: (legend.configure(wraplength=max(100, event.width - 8)),
                                             effort_label.configure(wraplength=max(100, event.width - 28))))
    bottom = ttk.Frame(outer)
    bottom.grid(row=4, column=0, sticky="ew")

    def modules():
        return pending["modules"]

    def module_by_id(key):
        return next((item for item in modules() if item["id"] == key), None)

    def status(message):
        status_var.set(message)

    def selected_effort(module):
        return model.effort(module["id"], direction_var.get()) if model is not None else None

    def task_state(module):
        if direction_var.get() not in module["directions"]:
            return "不适用"
        if report is None:
            return "尚未建任务" if model is not None else "待确认模块"
        key = json.dumps([module["id"], direction_var.get()], ensure_ascii=False)
        state = report.get("statuses", {}).get(key)
        return {"ready": "待领取", "blocked": "等待依赖", "dispatched": "处理中",
                "completed": "已完成"}.get(state, state or "待刷新")

    def draw(*_):
        canvas.delete("all")
        rendered.update(faces=[], centers=[], scene=None)
        if not modules():
            return
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        try:
            positions = region_positions(modules())
        except GranuleError as exc:
            canvas.create_text(width / 2, height / 2, text=str(exc), fill="#aa4e3c", width=width - 40)
            return
        # Fit to the maximum possible extent, so moving an effort slider changes
        # a region's size without automatically zooming the camera back out.
        args = fitted_scene_camera(positions, width, height, **camera)
        ids = sorted(module["id"] for module in modules())
        colors = {key: REGION_COLORS[index % len(REGION_COLORS)] for index, key in enumerate(ids)}
        region_specs = [{"module_id": module["id"], "center": positions[module["id"]],
                         "effort": selected_effort(module),
                         "color": colors[module["id"]] if direction_var.get() in module["directions"] else (150, 161, 157)}
                        for module in modules()]
        scene = render_scene(region_specs, args, width, height, pixel_step=6 if drag["active"] else 3)
        image = tk.PhotoImage(master=root, data=scene["ppm"], format="PPM").zoom(scene["pixel_step"])
        rendered["image"] = image  # Tk does not hold the Python image reference.
        canvas.create_image(0, 0, image=image, anchor="nw", tags=("module-scene",))
        centers = {key: (x, y, depth) for key, x, y, depth in scene["centers"]}
        faces = scene["faces"]
        projected = {key: [point for face in faces if face["module_id"] == key for point in face["points"]]
                     for key in ids}
        radii = {key: max((math.hypot(point[0] - centers[key][0], point[1] - centers[key][1])
                          for point in points), default=0) for key, points in projected.items()}

        def edge_points(source, target):
            start, end = centers[source], centers[target]
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length < 1:
                return (*start[:2], *end[:2])
            a, b = min(radii[source] + 4, length / 3), min(radii[target] + 6, length / 3)
            return (start[0] + dx * a / length, start[1] + dy * a / length,
                    end[0] - dx * b / length, end[1] - dy * b / length)

        for module in modules():
            if module.get("parent_id") in centers:
                canvas.create_line(*edge_points(module["parent_id"], module["id"]),
                                   dash=(5, 4), fill="#acb9ad", width=2)
            for dep in module.get("depends_on", []):
                if dep in centers:
                    canvas.create_line(*edge_points(dep, module["id"]), arrow="last", arrowshape=(10, 12, 5),
                                       fill="#64887b", width=2)
        # Show the thickness of the selected region even where translucent
        # surfaces overlap. The raster itself uses actual depth and alpha blend.
        if selected in projected:
            for face in faces:
                if face["module_id"] == selected:
                    canvas.create_polygon(*(coordinate for point in face["points"] for coordinate in point[:2]),
                                          fill="", outline="#285a4c", width=2)
        labels = module_label_positions(scene["centers"], width, height)
        rendered["labels"] = []
        from tkinter import font as tkfont
        label_font = tkfont.Font(root=root, family="Microsoft YaHei UI", size=9)
        for module in modules():
            key = module["id"]
            x, y, _ = centers[key]
            number = ids.index(key) + 1
            color = "#%02x%02x%02x" % colors[key]
            label = labels[key]
            if label["expanded"] or key == selected:
                lx, ly = label["x"], label["y"]
                left = lx if label["anchor"] == "w" else lx - 126
                right_edge = left + 126
                canvas.create_line(x, y, right_edge if label["anchor"] == "w" else left, ly,
                                   fill=color, dash=() if selected_effort(module) is not None else (3, 2))
                box = (left, ly - 19, right_edge, ly + 19)
                canvas.create_rectangle(*box, fill="#ffffff", outline=color,
                                        width=2 if key == selected else 1)
                name = f"{number}. {module['name']}"
                while label_font.measure(name) > 115 and len(name) > 4:
                    name = name[:-2] + "…" if name.endswith("…") else name[:-1] + "…"
                effort = selected_effort(module)
                value = "不适用" if direction_var.get() not in module["directions"] else ("待分配" if effort is None else f"{effort:.2f}")
                canvas.create_text(left + 63, ly, text=name + "\n" + value, fill="#24483c", font=label_font)
                rendered["labels"].append((key, box))
            canvas.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#ffffff", outline=color,
                               width=2 if key == selected else 1)
            canvas.create_text(x, y, text=str(number), fill=color, font=("Microsoft YaHei UI", 8, "bold"))
        canvas.create_text(14, 15, anchor="w", text=f"共享场景 · {len(modules())} 个模块 · {direction_var.get()}",
                           fill="#365747", font=("Microsoft YaHei UI", 10, "bold"))
        rendered["faces"] = faces
        rendered["centers"] = scene["centers"]
        rendered["scene"] = scene

    def schedule_draw(*_):
        if rendered["redraw"] is None:
            def redraw():
                rendered["redraw"] = None
                draw()
            rendered["redraw"] = root.after(35, redraw)

    def select_module(key):
        nonlocal selected, loading
        if module_by_id(key) is None:
            return
        selected = key
        if tree.selection() != (key,):
            tree.selection_set(key)
            tree.see(key)
        module = module_by_id(key)
        detail_var.set(f"{module['name']}\n当前方向任务：{task_state(module)}（已保存设置）\n\n职责：{module['description']}\n输出：{module['expected_output']}\n依据：{module['basis']}")
        applicable = model is not None and direction_var.get() in module["directions"]
        scale.configure(state="normal" if applicable else "disabled")
        entry.configure(state="normal" if applicable else "disabled")
        apply_button.configure(state="normal" if applicable else "disabled")
        loading = True
        effort = selected_effort(module)
        control = None if model is None or not applicable else model.controls[(graph["module_ids"][key], direction_var.get())]
        suggested = None if control is None else control["parameters"].get("design_effort")
        nonlocal_loading_set(_design_effort_units(effort if effort is not None else suggested)
                             if effort is not None or suggested is not None else 50)
        loading = False
        if not applicable:
            effort_name.set("先确认模块清单" if model is None else "该模块不使用当前方向；切换方向后分配")
        else:
            source = {"module": "模块设置", "project_default": "项目默认建议", "builtin_default": "内置建议"}.get(control["source"], control["source"])
            effort_name.set(f"{module['name']} · {direction_var.get()} · {source} · 版本 {control['revision']}" +
                            ("\n待分配：当前数值仅供预览，拖动或点击应用后才计入清单。" if effort is None else f"\n待确认值 {effort:.2f}；提交前可继续修改。"))
        schedule_draw()

    def refresh_tree():
        tree.delete(*tree.get_children())
        ordered_ids = sorted(module["id"] for module in modules())
        for module in modules():
            effort = selected_effort(module)
            label = "不适用" if direction_var.get() not in module["directions"] else ("待分配" if effort is None else f"{effort:.2f}")
            tree.insert("", "end", iid=module["id"], text=f"{ordered_ids.index(module['id']) + 1}. {module['name']}",
                        values=(label, task_state(module)))
        if selected and module_by_id(selected):
            tree.selection_set(selected)
        if model:
            count = len(model.unassigned())
            status(f"还有 {count} 个模块／方向待分配。所有预览只保存在窗口中；最终确认后统一写入。")
        schedule_draw()

    def apply_value(*_, from_scale=False):
        if loading or model is None or selected is None:
            return
        try:
            units = units_var.get() if from_scale else parse_effort_text(effort_text.get())
            model.set_units(selected, direction_var.get(), units)
            nonlocal_loading_set(units)
            refresh_tree()
            select_module(selected)
        except GranuleError as exc:
            status(str(exc))

    def nonlocal_loading_set(units):
        nonlocal loading
        loading = True
        # Tk schedules Scale.command at idle even for a programmatic variable set.
        # Remove it while loading so preview/defaults never become human choices.
        scale.configure(command="")
        units_var.set(units)
        effort_text.set(f"{units / 100:.2f}")
        root.after_idle(lambda: scale.configure(command=lambda _: apply_value(from_scale=True)))
        loading = False

    scale.configure(command=lambda _: apply_value(from_scale=True))
    apply_button.configure(command=apply_value)
    entry.bind("<Return>", apply_value)

    def edit_module(new=False):
        nonlocal pending
        current = None if new else module_by_id(selected)
        if current is None and not new:
            status("请先选择要编辑的模块")
            return
        dialog = tk.Toplevel(root)
        dialog.title("新增模块" if new else "编辑模块")
        dialog.geometry("720x650")
        dialog.minsize(620, 600)
        dialog.transient(root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        base = copy.deepcopy(current) if current else {"id": f"module_{len(modules()) + 1}", "name": "", "description": "",
                "expected_output": "", "basis": "", "directions": ["explanation"], "parent_id": None, "depends_on": []}
        while new and any(item["id"] == base["id"] for item in modules()):
            base["id"] += "_new"
        variables = {}
        row = 0
        for field, title in (("id", "模块标识"), ("name", "模块名称"), ("directions", "处理方向（逗号分隔）")):
            ttk.Label(frame, text=title).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            variables[field] = tk.StringVar(value=", ".join(base[field]) if field == "directions" else base[field])
            ttk.Entry(frame, textvariable=variables[field]).grid(row=row, column=1, sticky="ew", pady=5)
            row += 1
        text_fields = {}
        for field, title in (("description", "职责描述"), ("expected_output", "预期输出"), ("basis", "材料与分解依据")):
            ttk.Label(frame, text=title).grid(row=row, column=0, sticky="nw", pady=5)
            widget = tk.Text(frame, height=2, wrap="word", padx=6, pady=4, relief="solid", borderwidth=1)
            widget.insert("1.0", base[field])
            widget.grid(row=row, column=1, sticky="nsew", pady=5)
            frame.rowconfigure(row, weight=1)
            text_fields[field] = widget
            row += 1
        others = [item for item in modules() if item["id"] != base["id"]]
        parent_values = ["（无父模块）"] + [f"{item['name']} · {item['id']}" for item in others]
        parent_picker = ttk.Combobox(frame, values=parent_values, state="readonly")
        parent_picker.current(next((i + 1 for i, item in enumerate(others) if item["id"] == base["parent_id"]), 0))
        ttk.Label(frame, text="结构父模块").grid(row=row, column=0, sticky="w", pady=5)
        parent_picker.grid(row=row, column=1, sticky="ew", pady=5)
        row += 1
        ttk.Label(frame, text="执行依赖\n按 Ctrl 多选").grid(row=row, column=0, sticky="nw", pady=5)
        deps = tk.Listbox(frame, selectmode="multiple", exportselection=False, height=4)
        for i, item in enumerate(others):
            deps.insert("end", f"{item['name']} · {item['id']}")
            if item["id"] in base["depends_on"]:
                deps.selection_set(i)
        deps.grid(row=row, column=1, sticky="nsew", pady=5)
        frame.rowconfigure(row, weight=1)
        row += 1
        error_var = tk.StringVar()
        ttk.Label(frame, textvariable=error_var, foreground="#a34a38", wraplength=650).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        def accept():
            nonlocal pending, selected
            item = {field: value.get().strip() for field, value in variables.items()}
            item["directions"] = [part.strip() for part in item["directions"].replace("，", ",").split(",") if part.strip()]
            item.update({field: widget.get("1.0", "end-1c").strip() for field, widget in text_fields.items()})
            item["parent_id"] = None if parent_picker.current() == 0 else others[parent_picker.current() - 1]["id"]
            item["depends_on"] = [others[i]["id"] for i in deps.curselection()]
            if any(not item[field] for field in ("id", "name", "description", "expected_output", "basis", "directions")):
                error_var.set("请填写名称、职责、输出、依据与至少一个处理方向。")
                return
            if len(set(item["directions"])) != len(item["directions"]) or any(other["id"] == item["id"] for other in others):
                error_var.set("模块标识和处理方向不能重复。")
                return
            if new:
                pending["modules"].append(item)
            else:
                old_id = current["id"]
                pending["modules"][next(i for i, other in enumerate(modules()) if other["id"] == old_id)] = item
                if old_id != item["id"]:
                    for other in modules():
                        if other["parent_id"] == old_id:
                            other["parent_id"] = item["id"]
                        other["depends_on"] = [item["id"] if dep == old_id else dep for dep in other["depends_on"]]
            selected = item["id"]
            update_directions()
            refresh_tree()
            select_module(selected)
            status("修改尚未保存；点击“确认模块清单”时由后端校验整个图。")
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="应用到待确认清单", command=accept).pack(side="left")

    def remove_module():
        nonlocal selected
        module = module_by_id(selected)
        if module is None:
            return
        related = [item["name"] for item in modules() if item["parent_id"] == selected or selected in item["depends_on"]]
        if not messagebox.askokcancel("删除模块", f"删除“{module['name']}”？\n" + ("同时清除这些模块指向它的关系：" + "、".join(related) if related else "此修改仍需确认模块清单。"), parent=root):
            return
        removed = selected
        pending["modules"] = [item for item in modules() if item["id"] != removed]
        for item in modules():
            if item["parent_id"] == removed:
                item["parent_id"] = None
            item["depends_on"] = [dep for dep in item["depends_on"] if dep != removed]
        selected = modules()[0]["id"] if modules() else None
        update_directions()
        refresh_tree()
        if selected:
            select_module(selected)

    add_button = ttk.Button(edit_buttons, text="新增", command=lambda: edit_module(True))
    edit_button = ttk.Button(edit_buttons, text="编辑", command=edit_module)
    delete_button = ttk.Button(edit_buttons, text="删除", command=remove_module)
    for button in (add_button, edit_button, delete_button):
        button.pack(side="left", padx=(0, 5))

    def update_directions():
        directions = sorted({direction for module in modules() for direction in module["directions"]})
        direction_picker.configure(values=directions)
        if direction_var.get() not in directions:
            direction_var.set(directions[0] if directions else "")

    def load_state():
        nonlocal graph, pending, model, selected, report
        try:
            graph = service.get_analysis(analysis_id)
            pending = copy.deepcopy(graph["analysis"])
            model = AllocationModel(graph, service.allocation_snapshot(analysis_id)) if graph["state"] == "approved" else None
            report = service.workflow_status(graph["workflow_id"]) if graph.get("workflow_id") else None
            summary.configure(state="normal")
            summary.delete("1.0", "end")
            summary.insert("1.0", pending["summary"])
            approved = model is not None
            summary.configure(state="disabled" if approved else "normal")
            for button in (add_button, edit_button, delete_button):
                button.configure(state="disabled" if approved else "normal")
            confirm.configure(text="查看分配清单并确认 →" if approved else "确认模块清单 →")
            phase_var.set(f"{'2 / 2  手动分配每个模块的设计力度' if approved else '1 / 2  检查并修改宿主提出的模块分解'} · 分析版本 {graph['revision']}")
            update_directions()
            selected = selected if module_by_id(selected) else modules()[0]["id"]
            refresh_tree()
            select_module(selected)
            if not approved:
                status("先检查模块及其依据。确认模块清单不会批准任何设计力度，也不会开始执行任务。")
        except (GranuleError, OSError) as exc:
            status(str(exc) + "；可重新加载。")

    def review_allocation():
        nonlocal result
        try:
            choices = model.submitted_choices()
        except GranuleError as exc:
            status(str(exc) + f"（还有 {len(model.unassigned())} 项）")
            return
        review = tk.Toplevel(root)
        review.title("确认完整设计力度清单")
        review.geometry("820x540")
        review.minsize(700, 440)
        review.transient(root)
        review.grab_set()
        frame = ttk.Frame(review, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="以下所有模块／方向将一并提交；保留详细程度、列举数目等其他设置。",
                  wraplength=760).pack(anchor="w", pady=(0, 10))
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="both", expand=True)
        table = ttk.Treeview(table_frame, columns=("module", "direction", "previous", "chosen"), show="headings")
        for key, label in (("module", "模块"), ("direction", "方向"), ("previous", "当前有效值／来源"), ("chosen", "本次确认")):
            table.heading(key, text=label)
            table.column(key, width=170 if key != "chosen" else 90, minwidth=80)
        scrollbar = ttk.Scrollbar(table_frame, command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        names = {graph["module_ids"][module["id"]]: module["name"] for module in modules()}
        changed = set()
        logical_ids = {actual: logical for logical, actual in graph["module_ids"].items()}
        for choice in choices:
            control = model.controls[(choice["module_id"], choice["direction"])]
            old = control["parameters"].get("design_effort")
            source = {"module": "模块", "project_default": "项目默认", "builtin_default": "内置建议"}.get(control["source"], control["source"])
            table.insert("", "end", values=(names[choice["module_id"]], choice["direction"],
                ("未设置" if old is None else f"{old:.2f}") + " / " + source, f"{choice['design_effort']:.2f}"))
            if old != choice["design_effort"] or control["source"] != "module":
                changed.add(logical_ids[choice["module_id"]])
        affected = set(changed)
        while True:
            following = {module["id"] for module in modules() if any(dep in affected for dep in module["depends_on"])}
            if following <= affected:
                break
            affected.update(following)
        impact = "确认后首次建立执行工作流。" if graph.get("workflow_id") is None else ("力度变化将影响模块及其后继：" + "、".join(module["name"] for module in modules() if module["id"] in affected) if affected else "力度没有变化；仍记录本次人工确认。")
        ttk.Label(frame, text=impact, wraplength=760, foreground="#617369").pack(fill="x", pady=(10, 6))
        error_var = tk.StringVar()
        ttk.Label(frame, textvariable=error_var, wraplength=760, foreground="#a34a38").pack(fill="x")

        def commit():
            nonlocal result
            try:
                saved = service.save_allocation(model.snapshot, choices)
                if saved.get("status") != "saved":
                    raise GranuleError("未取得已保存确认结果，请重新加载并核对")
                result = saved
            except (GranuleError, OSError) as exc:
                error_var.set(str(exc) + "；关闭此清单后重新加载，再检查并确认。")
                return
            review.destroy()
            root.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="返回修改", command=review.destroy).pack(side="left")
        ttk.Button(buttons, text="确认全部设计力度并保存", command=commit).pack(side="right")

    def confirm_phase():
        nonlocal graph
        if model is not None:
            review_allocation()
            return
        pending["summary"] = summary.get("1.0", "end-1c").strip()
        if not messagebox.askokcancel("确认模块清单", f"确认这 {len(modules())} 个模块及其方向、父关系和依赖？\n确认后此清单固定；随后逐项分配力度。", parent=root):
            return
        try:
            if pending != graph["analysis"]:
                graph = service.update_analysis(analysis_id, graph["revision"], pending)
            graph = service.approve_analysis(analysis_id, graph["revision"])
            load_state()
        except (GranuleError, OSError) as exc:
            status(str(exc) + "；请修正清单或重新加载。")

    def cancel():
        root.destroy()

    ttk.Button(bottom, text="取消", command=cancel).pack(side="left")
    ttk.Button(bottom, text="重新加载", command=load_state).pack(side="left", padx=8)
    ttk.Button(bottom, text="重置视角", command=lambda: (camera.update(yaw=0.6, pitch=-0.35, zoom=1.0), schedule_draw())).pack(side="left")
    confirm = ttk.Button(bottom, text="确认模块清单 →", command=confirm_phase)
    confirm.pack(side="right")
    tree.bind("<<TreeviewSelect>>", lambda _: select_module(tree.selection()[0]) if tree.selection() else None)
    direction_picker.bind("<<ComboboxSelected>>", lambda _: (refresh_tree(), select_module(selected)) if selected else refresh_tree())
    canvas.bind("<Configure>", schedule_draw)
    drag = {"start": None, "last": None, "moved": False, "active": False}

    def press(event):
        drag.update(start=(event.x, event.y), last=(event.x, event.y), moved=False, active=True)

    def motion(event):
        if drag["last"] is None:
            return
        dx, dy = event.x - drag["last"][0], event.y - drag["last"][1]
        if math.hypot(event.x - drag["start"][0], event.y - drag["start"][1]) > 3:
            drag["moved"] = True
        camera["yaw"] += dx * 0.009
        camera["pitch"] = max(-1.4, min(1.4, camera["pitch"] + dy * 0.009))
        drag["last"] = (event.x, event.y)
        schedule_draw()

    def release(event):
        drag["active"] = False
        if not drag["moved"]:
            point = (event.x, event.y)
            label_hits = [key for key, (left, top, right_edge, bottom_edge) in rendered.get("labels", [])
                          if left <= event.x <= right_edge and top <= event.y <= bottom_edge]
            hits = tuple(label_hits) if label_hits else (hit_regions(point, rendered["scene"])
                                                       if rendered["scene"] is not None else ())
            if len(hits) == 1:
                select_module(hits[0])
            elif hits:
                menu = tk.Menu(root, tearoff=False)
                menu.add_command(label=f"重叠处的 {len(hits)} 个模块 · 请选择", state="disabled")
                menu.add_separator()
                ordered_ids = sorted(module["id"] for module in modules())
                for key in hits:
                    module = module_by_id(key)
                    label = f"{ordered_ids.index(key) + 1}. {module['name']} · {task_state(module)}"
                    menu.add_command(label=label, command=lambda chosen=key: select_module(chosen))
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()
        drag["last"] = None
        schedule_draw()

    def zoom(event):
        delta = 1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else -1
        camera["zoom"] = max(0.3, min(3, camera["zoom"] * (1.12 if delta > 0 else 1 / 1.12)))
        schedule_draw()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", motion)
    canvas.bind("<ButtonRelease-1>", release)
    canvas.bind("<MouseWheel>", zoom)
    canvas.bind("<Button-4>", zoom)
    canvas.bind("<Button-5>", zoom)
    root.bind("<Escape>", lambda _: cancel())
    root.protocol("WM_DELETE_WINDOW", cancel)
    load_state()
    root.mainloop()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentGranule native module analysis and 3D allocation")
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("--analysis-id", required=True)
    parser.add_argument("--output-file", help="A unique new result path for the local Skill host")
    args = parser.parse_args(argv)
    try:
        if args.output_file and Path(args.output_file).exists():
            raise GranuleError("Result path already exists; use a unique new output file")
        result = show_design(DesignControl(args.database), args.analysis_id)
        if not isinstance(result, dict) or result.get("status") not in ("saved", "cancelled"):
            raise GranuleError("Design view did not return an explicit saved/cancelled result")
        rendered_result = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output_file:
            with Path(args.output_file).open("x", encoding="utf-8") as stream:
                stream.write(rendered_result)
    except Exception as exc:
        import sys
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
