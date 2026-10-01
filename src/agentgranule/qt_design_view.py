"""Native Qt review and allocation controls around the OpenGL module scene.

Only explicit graph confirmation and the final full-allocation confirmation
call the persistence service. Camera changes and slider previews stay local.
"""

from __future__ import annotations

import copy
import json

from PySide6.QtCore import QEvent, Qt, QSignalBlocker
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget, QAbstractItemView, QHeaderView,
)

from .core import GranuleError, _design_effort_units
from .design_view import AllocationModel, parse_effort_text
from .gl_viewport import ModuleViewport, configure_default_format
from .proportions import normalized_shares


def _button(text, name, callback):
    widget = QPushButton(text)
    widget.setObjectName(name)
    # Enter in the decimal field applies that field only; it must not also
    # trigger QDialog's automatic default confirmation button.
    widget.setAutoDefault(False)
    widget.clicked.connect(callback)
    return widget


class ModuleEditor(QDialog):
    """Edit one proposed module; accepting does not persist or approve it."""

    def __init__(self, modules, current=None, parent=None):
        super().__init__(parent)
        self.setObjectName("moduleEditor")
        self.setWindowTitle("新增模块" if current is None else "编辑模块")
        self.resize(720, 620)
        self.setMinimumSize(580, 460)
        self.current = current
        self.item = None
        self.others = [m for m in modules if current is None or m["id"] != current["id"]]
        base = copy.deepcopy(current) if current is not None else {
            "id": f"module_{len(modules) + 1}", "name": "", "description": "",
            "expected_output": "", "basis": "", "directions": ["explanation"],
            "parent_id": None, "depends_on": [],
        }
        while current is None and any(m["id"] == base["id"] for m in modules):
            base["id"] += "_new"
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        self.fields = {}
        for key, label in (("id", "模块标识"), ("name", "模块名称"),
                           ("directions", "处理方向（逗号分隔）")):
            value = ", ".join(base[key]) if key == "directions" else base[key]
            field = QLineEdit(value)
            field.setObjectName(f"module_{key}")
            self.fields[key] = field
            form.addRow(label, field)
        self.text_fields = {}
        for key, label in (("description", "职责描述"), ("expected_output", "预期输出"),
                           ("basis", "材料与分解依据")):
            field = QTextEdit()
            field.setObjectName(f"module_{key}")
            field.setPlainText(base[key])
            field.setMinimumHeight(70)
            field.setMaximumHeight(110)
            self.text_fields[key] = field
            form.addRow(label, field)
        self.parent_picker = QComboBox()
        self.parent_picker.setObjectName("module_parent")
        self.parent_picker.addItem("（无父模块）", None)
        for module in self.others:
            self.parent_picker.addItem(f"{module['name']} · {module['id']}", module["id"])
        index = self.parent_picker.findData(base["parent_id"])
        self.parent_picker.setCurrentIndex(max(0, index))
        form.addRow("结构父模块", self.parent_picker)
        self.dependencies = QListWidget()
        self.dependencies.setObjectName("module_dependencies")
        self.dependencies.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.dependencies.setMinimumHeight(80)
        self.dependencies.setMaximumHeight(140)
        for module in self.others:
            item = QListWidgetItem(f"{module['name']} · {module['id']}")
            item.setData(Qt.ItemDataRole.UserRole, module["id"])
            self.dependencies.addItem(item)
            item.setSelected(module["id"] in base["depends_on"])
        form.addRow("执行依赖（可多选）", self.dependencies)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.error = QLabel()
        self.error.setObjectName("moduleEditorError")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #a34a38;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        apply = buttons.addButton("应用到待确认清单", QDialogButtonBox.ButtonRole.AcceptRole)
        apply.setObjectName("applyModuleEdit")
        apply.clicked.connect(self.validate_and_accept)
        layout.addWidget(buttons)

    def validate_and_accept(self):
        item = {key: widget.text().strip() for key, widget in self.fields.items()}
        item["directions"] = [part.strip() for part in item["directions"].replace("，", ",").split(",")
                              if part.strip()]
        item.update({key: widget.toPlainText().strip() for key, widget in self.text_fields.items()})
        item["parent_id"] = self.parent_picker.currentData()
        item["depends_on"] = [entry.data(Qt.ItemDataRole.UserRole)
                              for entry in self.dependencies.selectedItems()]
        if any(not item[key] for key in ("id", "name", "description", "expected_output", "basis", "directions")):
            self.error.setText("请填写名称、职责、输出、依据与至少一个处理方向。")
            return
        if len(set(item["directions"])) != len(item["directions"]) or any(
                module["id"] == item["id"] for module in self.others):
            self.error.setText("模块标识和处理方向不能重复。")
            return
        self.item = item
        self.accept()


class AllocationReview(QDialog):
    """Review every module/direction and explicitly save the full snapshot."""

    def __init__(self, owner, choices):
        super().__init__(owner)
        self.owner = owner
        self.choices = copy.deepcopy(choices)
        self.setObjectName("allocationReview")
        self.setWindowTitle("确认完整设计力度清单")
        self.resize(840, 540)
        self.setMinimumSize(640, 420)
        layout = QVBoxLayout(self)
        intro = QLabel("以下所有模块／方向将一并提交；保留详细程度、列举数目等其他设置。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        table = QTableWidget(len(choices), 4)
        table.setObjectName("allocationReviewTable")
        table.setHorizontalHeaderLabels(("模块", "方向", "当前有效值／来源", "本次确认"))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        names = {owner.graph["module_ids"][module["id"]]: module["name"] for module in owner.modules}
        logical_ids = {actual: logical for logical, actual in owner.graph["module_ids"].items()}
        changed = set()
        for row, choice in enumerate(choices):
            control = owner.model.controls[(choice["module_id"], choice["direction"])]
            old = control["parameters"].get("design_effort")
            source = {"module": "模块", "project_default": "项目默认", "builtin_default": "内置建议"}.get(
                control["source"], control["source"])
            values = (names[choice["module_id"]], choice["direction"],
                      ("未设置" if old is None else f"{old:.2f}") + " / " + source,
                      f"{choice['design_effort']:.2f}")
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
            if old != choice["design_effort"] or control["source"] != "module":
                changed.add(logical_ids[choice["module_id"]])
        layout.addWidget(table, 1)
        affected = set(changed)
        while True:
            following = {m["id"] for m in owner.modules if any(dep in affected for dep in m["depends_on"])}
            if following <= affected:
                break
            affected.update(following)
        impact = ("确认后首次建立执行工作流。" if owner.graph.get("workflow_id") is None else
                  "力度变化将影响模块及其后继：" + "、".join(m["name"] for m in owner.modules if m["id"] in affected)
                  if affected else "力度没有变化；仍记录本次人工确认。")
        impact_label = QLabel(impact)
        impact_label.setWordWrap(True)
        layout.addWidget(impact_label)
        self.error = QLabel()
        self.error.setObjectName("allocationReviewError")
        self.error.setStyleSheet("color: #a34a38;")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addWidget(_button("返回修改", "returnToAllocation", self.reject))
        buttons.addStretch(1)
        self.save_button = _button("确认全部设计力度并保存", "saveAllAllocation", self.commit)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

    def commit(self):
        if not self.owner.graphics_ready():
            self.error.setText("OpenGL 场景未就绪，不能确认保存。")
            return
        try:
            saved = self.owner.service.save_allocation(self.owner.model.snapshot, self.choices)
            if not isinstance(saved, dict) or saved.get("status") != "saved":
                raise GranuleError("未取得已保存确认结果，请重新加载并核对")
        except (GranuleError, OSError) as exc:
            self.error.setText(str(exc) + "；返回修改后重新加载，再检查并确认。")
            return
        self.owner.saved_result = saved
        self.accept()
        self.owner.accept()


class DesignWindow(QDialog):
    """Complete native review workflow, with an independently rendered viewport."""

    def __init__(self, service, analysis_id, parent=None):
        super().__init__(parent)
        self.service, self.analysis_id = service, analysis_id
        self.graph = None
        self.pending = None
        self.model = None
        self.report = None
        self.selected = None
        self.saved_result = None
        self.fatal_error = None
        self.review = None
        self._loading = False
        self._state_loaded = False
        self.setObjectName("designWindow")
        self.setWindowTitle("AgentGranule · 原生 OpenGL 3D 模块与100%占比")
        self.setFont(QFont("Microsoft YaHei UI", 10))
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        max_width, max_height = ((available.width() - 60, available.height() - 80)
                                 if available is not None else (1120, 780))
        self.resize(min(1120, max(640, max_width)), min(780, max(480, max_height)))
        self.setMinimumSize(min(820, max(640, max_width)), min(600, max(480, max_height)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("AGENTGRANULE · 模块设计")
        title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Weight.Bold))
        layout.addWidget(title)
        self.phase = QLabel()
        self.phase.setObjectName("designPhase")
        self.phase.setWordWrap(True)
        layout.addWidget(self.phase)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(245)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.tree = QTreeWidget()
        self.tree.setObjectName("moduleTree")
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(("任务模块 · 全部同图", "力度", "图中占比", "任务状态"))
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(140)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 58), (2, 74), (3, 76)):
            self.tree.setColumnWidth(column, width)
        self.tree.currentItemChanged.connect(self._tree_selected)
        left_layout.addWidget(self.tree, 3)
        editor_buttons = QHBoxLayout()
        self.add_button = _button("新增", "addModule", lambda: self.edit_module(True))
        self.edit_button = _button("编辑", "editModule", self.edit_module)
        self.delete_button = _button("删除", "deleteModule", self.remove_module)
        for button in (self.add_button, self.edit_button, self.delete_button):
            editor_buttons.addWidget(button)
        left_layout.addLayout(editor_buttons)
        self.details = QTextEdit()
        self.details.setObjectName("moduleDetails")
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(105)
        self.details.setMaximumHeight(175)
        left_layout.addWidget(self.details, 1)
        left_layout.addWidget(QLabel("分解说明（确认前可编辑）"))
        self.summary = QTextEdit()
        self.summary.setObjectName("analysisSummary")
        self.summary.setMinimumHeight(70)
        self.summary.setMaximumHeight(125)
        left_layout.addWidget(self.summary, 1)
        left_scroll.setWidget(left)
        self.splitter.addWidget(left_scroll)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("处理方向"))
        self.direction = QComboBox()
        self.direction.setObjectName("designDirection")
        self.direction.currentTextChanged.connect(self._direction_changed)
        toolbar.addWidget(self.direction)
        toolbar.addStretch(1)
        hint = QLabel("拖动旋转 · 滚轮缩放 · 点击模块")
        hint.setWordWrap(True)
        toolbar.addWidget(hint)
        right_layout.addLayout(toolbar)
        self.viewport = ModuleViewport(right)
        self.viewport.setObjectName("moduleViewport")
        self.viewport.setMinimumSize(260, 160)
        self.viewport.module_selected.connect(self.select_module)
        self.viewport.fatal_error.connect(self._graphics_failed)
        self.viewport.initialized.connect(self._graphics_initialized)
        right_layout.addWidget(self.viewport, 1)
        self.legend = QLabel()
        self.legend.setObjectName("designLegend")
        self.legend.setWordWrap(True)
        right_layout.addWidget(self.legend)
        allocation = QGroupBox("选中模块的设计力度权重 · 0.00–1.00")
        allocation_layout = QVBoxLayout(allocation)
        self.effort_name = QLabel("先确认模块清单")
        self.effort_name.setWordWrap(True)
        allocation_layout.addWidget(self.effort_name)
        effort_row = QHBoxLayout()
        effort_row.addWidget(QLabel("0.00"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("designEffortSlider")
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(5)
        self.slider.setValue(50)
        self.slider.valueChanged.connect(self._slider_changed)
        effort_row.addWidget(self.slider, 1)
        effort_row.addWidget(QLabel("1.00"))
        self.entry = QLineEdit("0.50")
        self.entry.setObjectName("designEffortEntry")
        self.entry.setMaximumWidth(90)
        self.entry.returnPressed.connect(self.apply_value)
        effort_row.addWidget(self.entry)
        allocation_layout.addLayout(effort_row)
        self.apply_button = _button("应用当前值", "applyDesignEffort", self.apply_value)
        allocation_layout.addWidget(self.apply_button, 0, Qt.AlignmentFlag.AlignRight)
        right_layout.addWidget(allocation)
        self.splitter.addWidget(right)
        self.splitter.setSizes([350, 750])
        self.status = QLabel()
        self.status.setObjectName("designStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addWidget(_button("取消", "cancelDesign", self.reject))
        self.reload_button = _button("重新加载", "reloadDesign", self.load_state)
        bottom.addWidget(self.reload_button)
        bottom.addWidget(_button("重置视角", "resetDesignCamera", self.viewport.reset_camera))
        bottom.addStretch(1)
        self.confirm = _button("确认模块清单 →", "confirmDesignPhase", self.confirm_phase)
        self.confirm.setEnabled(False)
        bottom.addWidget(self.confirm)
        layout.addLayout(bottom)
        self.load_state(initial=True)

    @property
    def modules(self):
        return self.pending["modules"] if self.pending is not None else []

    def module(self, key):
        return next((m for m in self.modules if m["id"] == key), None)

    def graphics_ready(self):
        return self._state_loaded and self.fatal_error is None and self.viewport.gl_ready

    def _graphics_initialized(self):
        if getattr(self.viewport, "gl_error", None):
            self._graphics_failed(self.viewport.gl_error)
        else:
            self.confirm.setEnabled(self.graph is not None and self.graphics_ready())

    def _graphics_failed(self, message):
        self.fatal_error = str(message)
        self.confirm.setEnabled(False)
        self.slider.setEnabled(False)
        self.entry.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.status.setText("原生 OpenGL 3D 场景失败：" + self.fatal_error + "；此次窗口不能批准或保存。")
        if self.review is not None:
            self.review.reject()

    def effort(self, module):
        return self.model.effort(module["id"], self.direction.currentText()) if self.model is not None else None

    def allocation(self):
        direction = self.direction.currentText()
        return normalized_shares({m["id"]: self.effort(m) for m in self.modules},
                                 applicable={m["id"] for m in self.modules if direction in m["directions"]})

    def task_state(self, module):
        direction = self.direction.currentText()
        if direction not in module["directions"]:
            return "不适用"
        if self.report is None:
            return "尚未建任务" if self.model is not None else "待确认模块"
        key = json.dumps([module["id"], direction], ensure_ascii=False)
        state = self.report.get("statuses", {}).get(key)
        return {"ready": "待领取", "blocked": "等待依赖", "dispatched": "处理中", "completed": "已完成"}.get(
            state, state or "待刷新")

    def update_directions(self):
        previous = self.direction.currentText()
        with QSignalBlocker(self.direction):
            self.direction.clear()
            self.direction.addItems(sorted({d for m in self.modules for d in m["directions"]}))
            index = self.direction.findText(previous)
            self.direction.setCurrentIndex(index if index >= 0 else 0)

    def refresh(self):
        allocation = self.allocation()
        ordered = sorted(m["id"] for m in self.modules)
        with QSignalBlocker(self.tree):
            self.tree.clear()
            for module in self.modules:
                key = module["id"]
                effort = self.effort(module)
                label = ("不适用" if self.direction.currentText() not in module["directions"] else
                         "待分配" if effort is None else f"{effort:.2f}")
                item = QTreeWidgetItem((f"{ordered.index(key) + 1}. {module['name']}", label,
                                       f"{allocation['percent_units'][key] / 100:.2f}%", self.task_state(module)))
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                self.tree.addTopLevelItem(item)
                if key == self.selected:
                    self.tree.setCurrentItem(item)
        self.viewport.set_modules(self.modules, {m["id"]: self.effort(m) for m in self.modules},
                                  self.direction.currentText(), self.selected)
        total = sum(allocation["percent_units"].values()) / 100
        preview = ("全部力度为 0，临时等分显示；原值保持 0。" if allocation["zero_total"] else
                   "待分配模块按 0.50 临时预览；应用后才计入力度。" if allocation["provisional"] else
                   "无适用模块，总占比 0%。" if not allocation["applicable"] else
                   "0 占比保留独立选择标记；虚线为父关系，箭头为依赖。")
        self.legend.setText(f"同图 {len(self.modules)} 个模块 · 合计 {total:.2f}%\n"
                            "力度是权重；占比提高时底面面积与真实3D高度同步增大。\n" + preview)
        if self.model is not None and self.fatal_error is None:
            self.status.setText(f"还有 {len(self.model.unassigned())} 个模块／方向待分配。预览只保存在窗口中；最终确认后统一写入。")

    def _tree_selected(self, item, _previous):
        if item is not None:
            self.select_module(item.data(0, Qt.ItemDataRole.UserRole))

    def select_module(self, key):
        module = self.module(key)
        if module is None:
            return
        self.selected = key
        with QSignalBlocker(self.tree):
            for index in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(index)
                if item.data(0, Qt.ItemDataRole.UserRole) == key:
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                    break
        self.details.setPlainText(f"{module['name']}\n当前方向任务：{self.task_state(module)}（已保存设置）\n\n"
                                  f"职责：{module['description']}\n输出：{module['expected_output']}\n依据：{module['basis']}")
        applicable = self.model is not None and self.direction.currentText() in module["directions"]
        for widget in (self.slider, self.entry, self.apply_button):
            widget.setEnabled(applicable and self.fatal_error is None)
        effort = self.effort(module)
        control = (self.model.controls[(self.graph["module_ids"][key], self.direction.currentText())]
                   if applicable else None)
        suggested = control["parameters"].get("design_effort") if control is not None else None
        units = _design_effort_units(effort if effort is not None else suggested) if (
            effort is not None or suggested is not None) else 50
        self._set_units(units)
        if not applicable:
            self.effort_name.setText("先确认模块清单" if self.model is None else "该模块不使用当前方向；切换方向后分配")
        else:
            source = {"module": "模块设置", "project_default": "项目默认建议", "builtin_default": "内置建议"}.get(
                control["source"], control["source"])
            share = self.allocation()["percent_units"][key] / 100
            self.effort_name.setText(f"{module['name']} · {self.direction.currentText()} · 图中 {share:.2f}% · {source}\n" +
                                     ("当前滑块仅为建议，应用后计入。" if effort is None else
                                      f"力度权重 {effort:.2f}；提交前可继续修改。"))
        self.viewport.select_module(key)

    def _set_units(self, units):
        with QSignalBlocker(self.slider):
            self.slider.setValue(units)
        self.entry.setText(f"{units / 100:.2f}")

    def _slider_changed(self, units):
        if self._loading or self.model is None or self.selected is None or self.fatal_error is not None:
            return
        self.apply_value(from_slider=True)

    def apply_value(self, _checked=False, *, from_slider=False):
        if self._loading or self.model is None or self.selected is None or self.fatal_error is not None:
            return
        try:
            units = self.slider.value() if from_slider else parse_effort_text(self.entry.text())
            self.model.set_units(self.selected, self.direction.currentText(), units)
            self._set_units(units)
            self.refresh()
            self.select_module(self.selected)
        except GranuleError as exc:
            self.status.setText(str(exc))

    def _direction_changed(self, _direction):
        if self._loading or self.pending is None:
            return
        self.refresh()
        self.select_module(self.selected)

    def load_state(self, _checked=False, *, initial=False):
        self._loading = True
        self._state_loaded = False
        try:
            graph = self.service.get_analysis(self.analysis_id)
            model = (AllocationModel(graph, self.service.allocation_snapshot(self.analysis_id))
                     if graph["state"] == "approved" else None)
            report = self.service.workflow_status(graph["workflow_id"]) if graph.get("workflow_id") else None
            self.graph, self.pending, self.model, self.report = graph, copy.deepcopy(graph["analysis"]), model, report
            self._state_loaded = True
            self.summary.setPlainText(self.pending["summary"])
            self.summary.setReadOnly(model is not None)
            for widget in (self.add_button, self.edit_button, self.delete_button):
                widget.setEnabled(model is None)
            self.confirm.setText("查看分配清单并确认 →" if model is not None else "确认模块清单 →")
            self.confirm.setEnabled(self.graphics_ready())
            self.phase.setText(("2 / 2  手动分配每个模块的设计力度" if model is not None else
                                "1 / 2  检查并修改宿主提出的模块分解") + f" · 分析版本 {graph['revision']}")
            self.update_directions()
            self.selected = self.selected if self.module(self.selected) is not None else (
                self.modules[0]["id"] if self.modules else None)
            self.refresh()
            self.select_module(self.selected)
            if model is None and self.fatal_error is None:
                self.status.setText("先检查模块及其依据。确认模块清单不会批准任何设计力度，也不会开始执行任务。")
        except (GranuleError, OSError) as exc:
            self.confirm.setEnabled(False)
            self.status.setText(str(exc) + "；可重新加载。")
            if initial:
                self.deleteLater()
                raise
        finally:
            self._loading = False

    def edit_module(self, new=False):
        if self.model is not None:
            return
        current = None if new else self.module(self.selected)
        if current is None and not new:
            self.status.setText("请先选择要编辑的模块")
            return
        dialog = ModuleEditor(self.modules, current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item = dialog.item
        if new:
            self.modules.append(item)
        else:
            old_id = current["id"]
            self.modules[next(i for i, module in enumerate(self.modules) if module["id"] == old_id)] = item
            if old_id != item["id"]:
                for module in self.modules:
                    if module["parent_id"] == old_id:
                        module["parent_id"] = item["id"]
                    module["depends_on"] = [item["id"] if dep == old_id else dep for dep in module["depends_on"]]
        self.selected = item["id"]
        self.update_directions()
        self.refresh()
        self.select_module(self.selected)
        self.status.setText("修改尚未保存；确认模块清单时由后端校验整个图。")

    def remove_module(self):
        if self.model is not None:
            return
        module = self.module(self.selected)
        if module is None:
            return
        related = [m["name"] for m in self.modules if m["parent_id"] == self.selected or self.selected in m["depends_on"]]
        question = f"删除“{module['name']}”？\n" + ("同时清除这些模块指向它的关系：" + "、".join(related)
                                                   if related else "此修改仍需确认模块清单。")
        if QMessageBox.question(self, "删除模块", question, QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        removed = self.selected
        self.pending["modules"] = [m for m in self.modules if m["id"] != removed]
        for module in self.modules:
            if module["parent_id"] == removed:
                module["parent_id"] = None
            module["depends_on"] = [dep for dep in module["depends_on"] if dep != removed]
        self.selected = self.modules[0]["id"] if self.modules else None
        self.update_directions()
        self.refresh()
        self.select_module(self.selected)

    def confirm_phase(self):
        if not self.graphics_ready():
            self.status.setText("原生 OpenGL 场景尚未就绪，不能确认。")
            return
        if self.model is not None:
            try:
                choices = self.model.submitted_choices()
            except GranuleError as exc:
                self.status.setText(str(exc) + f"（还有 {len(self.model.unassigned())} 项）")
                return
            self.review = AllocationReview(self, choices)
            try:
                self.review.exec()
            finally:
                self.review.deleteLater()
                self.review = None
            return
        self.pending["summary"] = self.summary.toPlainText().strip()
        question = f"确认这 {len(self.modules)} 个模块及其方向、父关系和依赖？\n确认后此清单固定；随后逐项分配力度。"
        if QMessageBox.question(self, "确认模块清单", question,
                                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        # A GL failure can be delivered while the modal confirmation is open.
        if not self.graphics_ready():
            return
        try:
            if self.pending != self.graph["analysis"]:
                self.graph = self.service.update_analysis(self.analysis_id, self.graph["revision"], self.pending)
            self.graph = self.service.approve_analysis(self.analysis_id, self.graph["revision"])
            self.load_state()
        except (GranuleError, OSError) as exc:
            self.status.setText(str(exc) + "；请修正清单或重新加载。")


def show_design_qt(service, analysis_id):
    """Block for genuine native human input and return explicit save/cancel.

    A failed graphics context is an error, including when that window closes;
    it is never treated as a human cancellation or approval.
    """
    app = QApplication.instance()
    if app is None:
        configure_default_format()
        app = QApplication([])
    if not isinstance(app, QApplication):
        raise GranuleError("The native design window requires a QApplication")
    window = None
    try:
        window = DesignWindow(service, analysis_id)
        window.exec()
        error = window.fatal_error or getattr(window.viewport, "gl_error", None)
        if error:
            raise GranuleError("原生 OpenGL 3D 场景失败：" + str(error))
        return window.saved_result if window.saved_result is not None else {"status": "cancelled"}
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
        # The synchronous dialog loop has stopped. Process its deferred
        # destruction while this QApplication and its OpenGL driver are still
        # alive; leaving GL widgets pending until interpreter shutdown can
        # crash native Windows drivers before the host writes its result.
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
