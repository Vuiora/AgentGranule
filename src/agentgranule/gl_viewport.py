"""Native, full-resolution OpenGL viewport; no bitmap rendering fallback."""

from __future__ import annotations

import math
from array import array

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QMatrix4x4, QOpenGLContext, QPainter,
                          QPainterPath, QPen, QPolygonF, QSurfaceFormat, QVector3D)
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .scene3d import Mesh, build_scene, point_in_polygon

# The Qt context resolves these standard OpenGL entry points portably.
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_STENCIL_BUFFER_BIT = 0x4000, 0x0100, 0x0400
GL_DEPTH_TEST, GL_STENCIL_TEST, GL_CULL_FACE, GL_BLEND = 0x0B71, 0x0B90, 0x0B44, 0x0BE2
GL_LESS, GL_LEQUAL, GL_ALWAYS, GL_EQUAL = 0x0201, 0x0203, 0x0207, 0x0202
GL_KEEP, GL_REPLACE = 0x1E00, 0x1E01
GL_TRIANGLES, GL_LINES, GL_FLOAT = 0x0004, 0x0001, 0x1406
GL_VENDOR, GL_RENDERER, GL_VERSION, GL_NO_ERROR = 0x1F00, 0x1F01, 0x1F02, 0
GL_POLYGON_OFFSET_FILL = 0x8037
GL_SAMPLES = 0x80A9

VERTEX_SHADER = """#version 330 core
in vec3 position;
in vec3 normal;
uniform mat4 mvp;
out vec3 worldNormal;
void main() { worldNormal = normal; gl_Position = mvp * vec4(position, 1.0); }
"""
FRAGMENT_SHADER = """#version 330 core
in vec3 worldNormal;
uniform vec3 baseColor;
uniform bool unlit;
out vec4 fragmentColor;
void main() {
    vec3 n = normalize(worldNormal);
    vec3 light = normalize(vec3(-0.35, -0.65, 1.0));
    float shade = unlit ? 1.0 : 0.38 + 0.62 * max(dot(n, light), 0.0);
    fragmentColor = vec4(baseColor * shade, 1.0);
}
"""


def surface_format():
    """Request antialiasing and the depth/stencil buffers used by the renderer."""
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(4)
    return fmt


def configure_default_format():
    """Call before creating QApplication; widget format also covers test apps."""
    QSurfaceFormat.setDefaultFormat(surface_format())


class ModuleViewport(QOpenGLWidget):
    module_selected = Signal(str)
    fatal_error = Signal(str)
    initialized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFormat(surface_format())
        self._requested_samples = self.format().samples()
        self.setMinimumSize(180, 150)
        self.setFocusPolicy(Qt.StrongFocus)
        self.gl_ready = False
        self.gl_error = None
        self._scene = build_scene([], {}, "")
        self._selected = None
        self._camera = {"yaw": -0.90, "pitch": 0.75, "roll": 0.0, "zoom": 1.0}
        self._projected = {}
        self._zero_targets = {}
        self._press = None
        self._dragged = False
        self._frame_count = 0
        self._viewport_size = (0, 0)
        self._framebuffer_samples = None
        self._gl_info = {}
        self._program = self._buffer = self._vao = self._functions = None
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self._check_initialization)

    @property
    def scene(self):
        """Pure geometry metadata for independent tests and native host labels."""
        return self._scene

    def set_modules(self, modules, efforts, direction, selected=None):
        self._scene = build_scene(modules, efforts, direction)
        self._selected = selected
        self.update()

    def select_module(self, module_id):
        self._selected = module_id
        self.update()

    def reset_camera(self):
        self._camera.update(yaw=-0.90, pitch=0.75, roll=0.0, zoom=1.0)
        self.update()

    def diagnostics(self):
        context = self.context()
        fmt = context.format() if context is not None else self.format()
        return {"backend": "native-opengl", "gl_ready": self.gl_ready,
                "gl_error": self.gl_error, "frame_count": self._frame_count,
                "logical_size": [self.width(), self.height()],
                "framebuffer_size": list(self._viewport_size), "device_pixel_ratio": self.devicePixelRatioF(),
                "samples": self._framebuffer_samples, "requested_samples": self._requested_samples,
                "context_samples": max(0, fmt.samples()), "depth_bits": fmt.depthBufferSize(),
                "stencil_bits": fmt.stencilBufferSize(),
                "context_version": [fmt.majorVersion(), fmt.minorVersion()],
                "camera": dict(self._camera), "world_up": "+Z",
                "floor_clip": "per-module-stencil", **self._gl_info}

    def _fail(self, error):
        if self.gl_error is None:
            self.gl_error = str(error)
            self.gl_ready = False
            QTimer.singleShot(0, lambda: self.fatal_error.emit(self.gl_error))

    def initializeGL(self):
        try:
            context = QOpenGLContext.currentContext()
            if context is None or not context.isValid():
                raise RuntimeError("无法创建原生 OpenGL 上下文")
            fmt = context.format()
            if (fmt.majorVersion(), fmt.minorVersion()) < (3, 0):
                raise RuntimeError("原生 3D 显示需要 OpenGL 3.0 或更高版本")
            if fmt.depthBufferSize() < 16 or fmt.stencilBufferSize() < 1:
                raise RuntimeError("原生 3D 上下文缺少深度或模板缓冲区")
            self._functions = context.functions()
            self._functions.initializeOpenGLFunctions()
            program = QOpenGLShaderProgram(self)
            # Qt's bundled Windows software driver exposes desktop GL 3.0.
            # GLSL 130 supports this identical mesh/depth/stencil pipeline;
            # only shader syntax version changes, never rendering resolution.
            shader_version = "330 core" if (fmt.majorVersion(), fmt.minorVersion()) >= (3, 3) else "130"
            for kind, source in ((QOpenGLShader.Vertex, VERTEX_SHADER),
                                 (QOpenGLShader.Fragment, FRAGMENT_SHADER)):
                source = source.replace("330 core", shader_version, 1)
                if not program.addShaderFromSourceCode(kind, source):
                    raise RuntimeError("3D 着色器编译失败：" + program.log())
            if not program.link():
                raise RuntimeError("3D 着色器链接失败：" + program.log())
            self._program = program
            self._vao = QOpenGLVertexArrayObject(self)
            self._buffer = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            if not self._vao.create() or not self._buffer.create():
                raise RuntimeError("无法创建 3D 网格缓冲区")
            self._vao.bind()
            self._buffer.bind()
            self._buffer.setUsagePattern(QOpenGLBuffer.DynamicDraw)
            for name, offset in (("position", 0), ("normal", 12)):
                program.enableAttributeArray(name)
                program.setAttributeBuffer(name, GL_FLOAT, offset, 3, 24)
            self._buffer.release()
            self._vao.release()
            def gl_text(which):
                value = self._functions.glGetString(which)
                return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
            self._gl_info = {"vendor": gl_text(GL_VENDOR), "renderer": gl_text(GL_RENDERER),
                             "version": gl_text(GL_VERSION), "shader_linked": program.isLinked()}
            self.gl_ready = True
            context.aboutToBeDestroyed.connect(self._cleanup)
            QTimer.singleShot(0, self.initialized.emit)
        except Exception as exc:
            self._fail(exc)

    def _cleanup(self):
        if self._buffer is not None:
            self.makeCurrent()
            self._buffer.destroy()
            self._vao.destroy()
            self._program.removeAllShaders()
            self.doneCurrent()
            self._buffer = self._vao = self._program = None
            self.gl_ready = False

    def closeEvent(self, event):
        # Release while the widget/context are still alive, rather than during
        # QApplication teardown when Python callback ownership is ambiguous.
        self._cleanup()
        self._startup_timer.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._startup_timer.start(500)

    def _check_initialization(self):
        if not self.gl_ready and self.gl_error is None:
            self._fail("无法创建原生 OpenGL 3D 显示上下文；本轮未确认任何设置")

    def _matrix(self):
        """Fit only fixed board bounds; changing shares never changes the camera."""
        yaw, pitch = self._camera["yaw"], self._camera["pitch"]
        eye = QVector3D(25 * math.cos(pitch) * math.cos(yaw),
                        25 * math.cos(pitch) * math.sin(yaw), 25 * math.sin(pitch))
        view = QMatrix4x4()
        view.lookAt(eye, QVector3D(0, 0, 0), QVector3D(0, 0, 1))
        if self._camera["roll"]:
            roll = QMatrix4x4()
            roll.rotate(math.degrees(self._camera["roll"]), 0, 0, 1)
            view = roll * view
        w, h = self._scene["width"] / 2, self._scene["height"] / 2
        floor = [view.map(QVector3D(x, y, z)) for x in (-w, w) for y in (-h, h)
                 for z in (-0.35, 0.0)]
        extent_x = max(abs(p.x()) for p in floor)
        extent_y = max(abs(p.y()) for p in floor)
        aspect = max(1, self.width()) / max(1, self.height())
        half_y = max(extent_y, extent_x / aspect) * 1.14 / self._camera["zoom"]
        projection = QMatrix4x4()
        projection.ortho(-half_y * aspect, half_y * aspect, -half_y, half_y, 0.1, 100.0)
        return projection * view

    def _project(self, point, matrix):
        result = matrix.map(QVector3D(*point))
        return ((result.x() + 1) * self.width() / 2, (1 - result.y()) * self.height() / 2)

    def _draw(self, mesh, matrix, color, *, unlit=False, lines=False):
        points = tuple((*p, 0.0, 0.0, 1.0) for p in mesh.edges) if lines else mesh.vertices
        if not points:
            return
        self._program.bind()
        self._vao.bind()
        self._buffer.bind()
        payload = array("f", (v for point in points for v in point)).tobytes()
        self._buffer.allocate(payload, len(payload))
        self._program.setUniformValue("mvp", matrix)
        self._program.setUniformValue("baseColor", QVector3D(*(v / 255 for v in color)))
        # PySide's overloaded setUniformValue(bool) can select glUniform1f;
        # explicit integer upload is required for a GLSL bool uniform.
        self._program.setUniformValue1i("unlit", int(unlit))
        self._functions.glDrawArrays(GL_LINES if lines else GL_TRIANGLES, 0, len(points))
        self._buffer.release()
        self._vao.release()
        self._program.release()

    def paintGL(self):
        if not self.gl_ready or self.gl_error is not None:
            return
        try:
            gl = self._functions
            ratio = self.devicePixelRatioF()
            # QSize multiplication uses Qt's qRound, matching its physical
            # FBO allocation even at half pixels (Python round uses ties-even).
            physical = self.size() * ratio
            self._viewport_size = (max(1, physical.width()), max(1, physical.height()))
            gl.glViewport(0, 0, *self._viewport_size)
            # QOpenGLWidget's own FBO can have MSAA even when the native
            # window/context format itself reports zero samples.
            self._framebuffer_samples = int(gl.glGetIntegerv(GL_SAMPLES))
            gl.glColorMask(True, True, True, True)
            gl.glDepthMask(True)
            gl.glStencilMask(0xFF)
            gl.glClearColor(0.945, 0.965, 0.955, 1.0)
            gl.glClearDepthf(1.0)
            gl.glClearStencil(0)
            gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT)
            gl.glDisable(GL_BLEND)
            gl.glDisable(GL_CULL_FACE)
            gl.glDisable(GL_STENCIL_TEST)
            gl.glEnable(GL_DEPTH_TEST)
            gl.glDepthFunc(GL_LEQUAL)
            matrix = self._matrix()
            self._draw(self._scene["board"], matrix, (168, 180, 175))
            # The coloured floor coincides with the board top geometrically;
            # depth bias prevents driver-dependent coplanar z-fighting.
            gl.glEnable(GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(-1.0, -1.0)
            self._projected = {}
            for key, tile in self._scene["tiles"].items():
                self._projected[key] = [self._project(p, matrix) for p in tile["floor_corners"]]
                if not self._scene["allocation"]["shares"][key]:
                    continue
                floor_color = tuple(round(v * 0.32 + 245 * 0.68) for v in tile["color"])
                self._draw(tile["floor"], matrix, floor_color, unlit=True)
            gl.glDisable(GL_POLYGON_OFFSET_FILL)
            for key, tile in self._scene["tiles"].items():
                if not tile["mesh"].vertices:
                    continue
                # A new 1-bit mask per module has no 255-module ID ceiling.
                # Depth/color remain intact while creating its floor aperture.
                gl.glEnable(GL_STENCIL_TEST)
                gl.glStencilMask(0xFF)
                gl.glClear(GL_STENCIL_BUFFER_BIT)
                gl.glColorMask(False, False, False, False)
                gl.glDepthMask(False)
                gl.glDisable(GL_DEPTH_TEST)
                gl.glStencilFunc(GL_ALWAYS, 1, 0xFF)
                gl.glStencilOp(GL_KEEP, GL_KEEP, GL_REPLACE)
                self._draw(tile["floor"], matrix, (0, 0, 0), unlit=True)
                gl.glColorMask(True, True, True, True)
                gl.glDepthMask(True)
                gl.glEnable(GL_DEPTH_TEST)
                gl.glStencilMask(0x00)
                gl.glStencilFunc(GL_EQUAL, 1, 0xFF)
                gl.glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP)
                self._draw(tile["mesh"], matrix, tile["color"])
                self._draw(tile["mesh"], matrix, (30, 78, 64) if key == self._selected else (52, 83, 74),
                           unlit=True, lines=True)
            gl.glStencilMask(0xFF)
            gl.glDisable(GL_STENCIL_TEST)
            gl.glDisable(GL_DEPTH_TEST)
            error = gl.glGetError()
            if error != GL_NO_ERROR:
                raise RuntimeError(f"原生 3D 渲染出错：OpenGL 0x{error:04x}")
            self._paint_labels(matrix)
            self._frame_count += 1
        except Exception as exc:
            self._fail(exc)

    def _paint_labels(self, matrix):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        allocation = self._scene["allocation"]
        for index, (key, tile) in enumerate(self._scene["tiles"].items()):
            polygon = QPolygonF([QPointF(*p) for p in self._projected[key]])
            if not allocation["shares"][key]:
                continue
            path = QPainterPath()
            path.addPolygon(polygon)
            painter.save()
            painter.setClipPath(path)
            painter.setPen(QPen(QColor("#245a4c") if key == self._selected else QColor("#ffffff"),
                                2 if key == self._selected else 1))
            painter.drawPolygon(polygon)
            center = self._project(tile["center"], matrix)
            label = f"{index + 1}. {tile['module'].get('name', key)}\n{allocation['percent_units'][key] / 100:.2f}%"
            box = painter.fontMetrics().boundingRect(QRectF(-150, -50, 300, 100).toRect(),
                                                     int(Qt.AlignCenter), label)
            corners = ((center[0] - box.width() / 2 - 2, center[1] - box.height() / 2 - 2),
                       (center[0] + box.width() / 2 + 2, center[1] + box.height() / 2 + 2))
            if all(point_in_polygon((x, y), self._projected[key])
                   for x in (corners[0][0], corners[1][0]) for y in (corners[0][1], corners[1][1])):
                painter.setPen(QColor("#183f34"))
                painter.drawText(QRectF(center[0] - box.width() / 2, center[1] - box.height() / 2,
                                        box.width(), box.height()), int(Qt.AlignCenter), label)
            painter.restore()
        zeros = [key for key in self._scene["tiles"] if not allocation["shares"][key]]
        self._zero_targets = {}
        for index, key in enumerate(zeros):
            center = QPointF((index + 0.5) * self.width() / len(zeros), self.height() - 15)
            painter.setPen(QPen(QColor("#245a4c"), 2 if key == self._selected else 1))
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(center, 10, 10)
            painter.drawText(QRectF(center.x() - 10, center.y() - 10, 20, 20), int(Qt.AlignCenter),
                             str(list(self._scene["tiles"]).index(key) + 1))
            self._zero_targets[key] = (center.x(), center.y())
        # Existing, explicit relationships are overlaid without inventing links.
        for key, tile in self._scene["tiles"].items():
            module = tile["module"]
            for source, dashed in ([(module.get("parent_id"), True)] +
                                   [(dep, False) for dep in module.get("depends_on", [])]):
                if source not in self._scene["tiles"]:
                    continue
                start = QPointF(*self._project(self._scene["tiles"][source]["center"], matrix))
                end = QPointF(*self._project(tile["center"], matrix))
                # A relationship may cross other allocations geometrically;
                # display its ends only inside the two participating cells.
                path = QPainterPath()
                for related in (source, key):
                    path.addPolygon(QPolygonF([QPointF(*p) for p in self._projected[related]]))
                painter.save()
                painter.setClipPath(path)
                painter.setPen(QPen(QColor("#486e60"), 1, Qt.DashLine if dashed else Qt.SolidLine))
                painter.drawLine(start, end)
                if not dashed:
                    angle = math.atan2(end.y() - start.y(), end.x() - start.x())
                    for turn in (-0.45, 0.45):
                        arrow = QPointF(end.x() - 7 * math.cos(angle + turn),
                                        end.y() - 7 * math.sin(angle + turn))
                        painter.drawLine(end, arrow)
                painter.restore()
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = (event.position().x(), event.position().y(), dict(self._camera))
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press is None or not event.buttons() & Qt.LeftButton:
            return
        x, y, original = self._press
        dx, dy = event.position().x() - x, event.position().y() - y
        if abs(dx) + abs(dy) > 3:
            self._dragged = True
        self._camera["yaw"] = original["yaw"] - dx * 0.008
        self._camera["pitch"] = max(-1.45, min(1.45, original["pitch"] + dy * 0.008))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragged:
            return
        point = (event.position().x(), event.position().y())
        for key, center in self._zero_targets.items():
            if math.hypot(point[0] - center[0], point[1] - center[1]) <= 12:
                self.select_module(key)
                self.module_selected.emit(key)
                return
        for key, polygon in self._projected.items():
            if self._scene["allocation"]["shares"][key] and point_in_polygon(point, polygon):
                self.select_module(key)
                self.module_selected.emit(key)
                return

    def wheelEvent(self, event):
        self._camera["zoom"] = max(0.4, min(3.0, self._camera["zoom"] *
                                           1.1 ** (event.angleDelta().y() / 120)))
        self.update()
        event.accept()
