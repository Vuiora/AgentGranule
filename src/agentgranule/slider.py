"""Compact native granularity dialog, usable by the workflow Skill."""

import argparse
import json
from pathlib import Path

from .core import GranuleError, Project, _design_effort_units, _parameters, _text
from .workflow import Workflow

LEVELS = ("brief", "standard", "detailed")
LABELS = ("简要", "标准", "详细")
DESCRIPTIONS = ("聚焦核心结论，减少展开细节。", "覆盖主要信息，保持适度解释。", "充分说明依据与细节，深入展开。")


class PopupControl:
    """Persistence service independent of Tk, sharing the existing project DB."""

    def __init__(self, database):
        _text(str(database), "database")
        if str(database) == ":memory:":
            raise GranuleError("Popup requires a persistent file database")
        self.database = database

    def modules(self):
        with Project(self.database) as project:
            return [dict(row) for row in project._db.execute(
                "SELECT p.id, p.description, s.title FROM problems p JOIN sessions s ON s.id=p.session_id "
                "ORDER BY s.rowid, p.rowid")]

    def directions(self):
        with Project(self.database) as project:
            return sorted({"classification", "enumeration", "advantages", "disadvantages", "explanation"} |
                          {row[0] for row in project._db.execute(
                              "SELECT direction FROM module_controls UNION SELECT direction FROM granularity_defaults")})

    def load(self, module_id, direction):
        with Project(self.database) as project:
            return project.get_granularity(module_id, direction)

    def save(self, snapshot, level, *, parameter="detail_level"):
        if parameter not in ("detail_level", "design_effort"):
            raise GranuleError("Unknown slider parameter")
        if parameter == "detail_level" and level not in LEVELS:
            raise GranuleError("Choose brief, standard or detailed")
        if parameter == "design_effort":
            level = _design_effort_units(level) / 100
        with Project(self.database) as project:
            with project._db:
                project._db.execute("BEGIN IMMEDIATE")
                current = project.get_granularity(snapshot["problem_id"], snapshot["direction"])
                if current != snapshot:
                    raise GranuleError("设置已在其他入口更新，请重新加载后选择")
                parameters = _parameters({**current["parameters"], parameter: level})
                control = project._set_granularity_locked(snapshot["problem_id"], snapshot["direction"],
                                                          parameters, "human:popup")
                session = project._problem(snapshot["problem_id"])["session_id"]
                project._event(session, "conversation.message", {"role": "user", "content":
                    "粒度弹窗确认：" + json.dumps({"module_id": snapshot["problem_id"],
                    "direction": snapshot["direction"], parameter: level}, ensure_ascii=False)})
            engine = Workflow(project)
            reports = [engine.workflow_status(row[0]) for row in project._db.execute(
                "SELECT id FROM workflows WHERE session_id=?", (session,)).fetchall()]
            return {"status": "saved", "control": control, "workflows": reports}


def show_popup(service, module_id=None, direction="explanation", *, parameter="detail_level"):
    import tkinter as tk
    from tkinter import ttk

    if parameter not in ("detail_level", "design_effort"):
        raise GranuleError("Unknown slider parameter")
    effort_mode = parameter == "design_effort"
    modules = service.modules()
    if not modules:
        raise GranuleError("没有可设置的模块，请先通过 Skill 或 CLI 创建任务模块")
    if module_id is not None and not any(m["id"] == module_id for m in modules):
        raise GranuleError("Unknown module_id")
    result = {"status": "cancelled"}
    snapshot = None
    root = tk.Tk()
    root.title("AgentGranule · 粒度")
    root.configure(bg="#f3f5f0")
    root.resizable(False, False)
    width, height = 460, 438
    root.geometry(f"{width}x{height}+{max(0,(root.winfo_screenwidth()-width)//2)}+{max(0,(root.winfo_screenheight()-height)//2)}")
    root.option_add("*Font", ("Microsoft YaHei UI", 10))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TCombobox", padding=6, fieldbackground="#ffffff", foreground="#243d34")
    outer = tk.Frame(root, bg="#f3f5f0", padx=26, pady=20)
    outer.pack(fill="both", expand=True)
    tk.Label(outer, text="●  AGENTGRANULE", fg="#30785e", bg="#f3f5f0", font=("Segoe UI", 9, "bold")).pack(anchor="w")
    tk.Label(outer, text="为这个模块分配设计力度" if effort_mode else "这次，想展开到什么程度？", fg="#193b2c", bg="#f3f5f0",
             font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", pady=(8, 14))
    module_names = [m["title"] + " / " + m["description"] for m in modules]
    selected = next((i for i, m in enumerate(modules) if m["id"] == module_id), 0)
    picker = ttk.Combobox(outer, values=module_names, state="readonly")
    picker.current(selected)
    picker.pack(fill="x")
    direction_var = tk.StringVar(value=direction)
    direction_picker = ttk.Combobox(outer, textvariable=direction_var, values=service.directions())
    direction_picker.pack(fill="x", pady=(8, 12))
    card = tk.Frame(outer, bg="white", padx=18, pady=14, highlightbackground="#dce5dc", highlightthickness=1)
    card.pack(fill="x")
    # Integer ticks avoid binary float accumulation while dragging in 0.01 steps.
    value = tk.IntVar(value=50 if effort_mode else 1)
    title = tk.Label(card, text="0.50" if effort_mode else "标准", bg="white", fg="#236f50", font=("Microsoft YaHei UI", 15, "bold"))
    title.pack(anchor="w")
    explanation = tk.Label(card, text="设计力度 · 0.00–1.00 · 每步 0.01" if effort_mode else DESCRIPTIONS[1], bg="white", fg="#617369", anchor="w")
    explanation.pack(fill="x", pady=(3, 8))
    scale = tk.Scale(card, variable=value, from_=0, to=100 if effort_mode else 2, orient="horizontal", resolution=1,
                     showvalue=False, bg="white", troughcolor="#dce8dd", activebackground="#2e8561",
                     sliderrelief="flat", sliderlength=24, highlightthickness=0, bd=0, width=8,
                     takefocus=True)
    scale.pack(fill="x")
    ticks = tk.Frame(card, bg="white")
    ticks.pack(fill="x", pady=(2, 0))
    for i, label in enumerate(("0.00", "0.50", "1.00") if effort_mode else LABELS):
        tk.Label(ticks, text=label, bg="white", fg="#76837a").grid(row=0, column=i, sticky=("w", "", "e")[i])
        ticks.columnconfigure(i, weight=1)
    status = tk.Label(outer, text="", bg="#f3f5f0", fg="#64796b", wraplength=402, justify="left", anchor="w")
    status.pack(fill="x", pady=(12, 8))
    buttons = tk.Frame(outer, bg="#f3f5f0")
    buttons.pack(fill="x", side="bottom")

    def cancel():
        root.destroy()

    def update_preview(*_):
        if effort_mode:
            title.configure(text=f"{value.get() / 100:.2f}")
        else:
            title.configure(text=LABELS[value.get()])
            explanation.configure(text=DESCRIPTIONS[value.get()])

    def load(*_):
        nonlocal snapshot
        try:
            loaded = service.load(modules[picker.current()]["id"], direction_var.get())
            level = loaded["parameters"].get(parameter)
            units = (_design_effort_units(level) if level is not None else 50) if effort_mode else (LEVELS.index(level) if level in LEVELS else 1)
            snapshot = loaded
            value.set(units)
            source = {"module": "模块设置", "project_default": "项目默认", "builtin_default": "内置建议"}[snapshot["source"]]
            status.configure(text=f"{source} · 版本 {snapshot['revision']}" +
                ((" · 未分配设计力度，0.50 为预览，确认后生效" if level is None else " · 拖动预览，确认后生效") if effort_mode else
                 (f" · 原值 {level or '未指定'}，确认后改为所选程度" if level not in LEVELS else " · 拖动预览，确认后生效")), fg="#64796b")
            confirm.configure(state="normal")
        except GranuleError as exc:
            snapshot = None
            status.configure(text=str(exc), fg="#aa4e3c")
            confirm.configure(state="disabled")

    def save():
        nonlocal result
        if snapshot is None:
            return
        try:
            result = service.save(snapshot, value.get() / 100 if effort_mode else LEVELS[value.get()], parameter=parameter)
        except (GranuleError, OSError) as exc:
            status.configure(text=str(exc), fg="#aa4e3c")
            return
        root.destroy()

    tk.Button(buttons, text="取消", command=cancel, bd=0, bg="#e6ece5", fg="#466351", padx=15, pady=8).pack(side="left")
    tk.Button(buttons, text="重新加载", command=load, bd=0, bg="#f3f5f0", fg="#466351", padx=8, pady=8).pack(side="left", padx=6)
    confirm = tk.Button(buttons, text="确认粒度  →", command=save, bd=0, bg="#267455", activebackground="#1d5e43",
                         fg="white", activeforeground="white", padx=18, pady=8)
    confirm.pack(side="right")
    value.trace_add("write", update_preview)
    picker.bind("<<ComboboxSelected>>", load)
    direction_picker.bind("<<ComboboxSelected>>", load)
    def direction_blur(*_):
        if snapshot is None or snapshot["direction"] != direction_var.get():
            load()
        else:
            confirm.configure(state="normal")

    direction_picker.bind("<FocusOut>", direction_blur)
    direction_var.trace_add("write", lambda *_: confirm.configure(state="disabled"))
    root.bind("<Escape>", lambda _: cancel())
    root.protocol("WM_DELETE_WINDOW", cancel)
    load()
    scale.focus_set()
    root.mainloop()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentGranule native granularity popup")
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("--module-id")
    parser.add_argument("--direction", default="explanation")
    parser.add_argument("--parameter", choices=("detail_level", "design_effort"), default="detail_level",
                        help="Slider field; design_effort uses 0.00–1.00 in 0.01 steps")
    parser.add_argument("--output-file", help="Write saved/cancelled JSON for a Skill host")
    args = parser.parse_args(argv)
    try:
        result = show_popup(PopupControl(args.database), args.module_id, args.direction, parameter=args.parameter)
        if args.output_file:
            Path(args.output_file).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        import sys
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
