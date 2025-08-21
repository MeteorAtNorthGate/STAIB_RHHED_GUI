import sys
import time
import math
from ctypes import windll, c_int, c_char, c_float

from PySide6.QtWidgets import (
	QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
	QLabel, QSlider, QMessageBox, QPushButton, QTextEdit
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QPainter, QColor, QBrush, QFont, QTextCursor

# 尝试导入用户定义的配置文件
try:
	import config
except ImportError:
	# 创建一个虚拟的 config 模块作为后备
	class DummyConfig:
		ENERGY_IDLE = 5.0
		FILAMENT_IDLE = 4.0
		ENERGY_WORK = 6.0
		FILAMENT_WORK = 7.0
		GRID_CAL = 1.9
		FOCUS_CAL = 3.95
		X_CAL = -2.14
		Y_CAL = -0.9
		ENERGY_RAMP = 0.03
		FILAMENT_RAMP = 0.1
	config = DummyConfig()


# =============================================================================
# 0. Custom Stream for Logging
#    - 将 stdout 重定向到 GUI 文本框
# =============================================================================
class Stream(QObject):
	"""自定义流对象，用于将 print 输出重定向到 QTextEdit。"""
	newText = Signal(str)

	def write(self, text):
		self.newText.emit(str(text))

	def flush(self):
		pass # 在这个应用中是必需的，但可以留空


# =============================================================================
# 1. Hardware Controller Class
#    - 管理与 USB3000.dll 的所有交互
# =============================================================================
class HardwareController:
	"""处理 DLL 加载和向硬件发送命令。"""

	def __init__(self):
		self.dll = None
		self.device_open = False
		self.dummy_mode = False

		# --- 引脚定义 ---
		self.DevIndex = c_int(0)
		self.GRID = c_char(1)
		self.FOCUS = c_char(2)
		self.BEAM_BLANKING = c_char(3)
		self.FILAMENT = c_char(4)
		self.ENERGY = c_char(5)
		self.DEFLECTION_X = c_char(6)
		self.DEFLECTION_Y = c_char(7)
		self.BEAM_ROCKING = c_char(8)
		self.COMPUTER_CONTROL = c_char(11)

		try:
			self.dll = windll.LoadLibrary(r'.\lib\x64\USB3000.dll')
			print("成功加载 USB3000.dll")
		except OSError as e:
			print(f"无法加载 USB3000.dll: {e}")
			print("--- 将以虚拟模式运行 ---")
			self.dummy_mode = True

	def open_device(self) -> bool:
		"""打开 USB 设备。如果打开失败则进入虚拟模式。"""
		if self.dummy_mode:
			self.device_open = True
			return True
		if self.dll:
			if self.dll.USB3OpenDevice(self.DevIndex) == 0:
				print("USB3 设备已成功打开。")
				self.device_open = True
				return True
			else:
				print("错误: 无法打开 USB3 设备。")
				self.device_open = False
				self.dummy_mode = True
				return False
		return False

	def close_device(self):
		"""关闭 USB 设备。"""
		if self.device_open and not self.dummy_mode and self.dll:
			self.dll.USB3CloseDevice(self.DevIndex)
			print("USB3 设备已关闭。")
		self.device_open = False

	def set_voltage(self, channel: c_char, voltage: float):
		"""立即设置特定通道的电压。"""
		if self.dummy_mode:
			print(f"dummy_mode: 设置通道 {ord(channel.value)} 为 {voltage:.2f} V")
			return
		if self.device_open and self.dll:
			self.dll.SetUSB3AoImmediately(self.DevIndex, channel, c_float(voltage))
			#print(f"设置通道 {ord(channel.value)} 为 {voltage:.2f} V")


# =============================================================================
# 2. Ramping Manager Class
#    - 处理平滑的电压过渡逻辑
# =============================================================================
class RampingManager(QObject):
	"""管理所有适用通道的电压ramp。"""

	def __init__(self, controller: HardwareController, parent=None):
		super().__init__(parent)
		self.controller = controller
		self._targets = {}
		self._currents = {}
		self._ramps = {}

		self._timer = QTimer(self)
		self._timer.setInterval(50)  # 每秒更新20次以实现平滑ramp
		self._timer.timeout.connect(self._update_all_voltages)
		self._last_update_time = 0

	def start(self):
		self._last_update_time = time.time()
		self._timer.start()

	def set_initial_state(self, channel: c_char, voltage: float, ramp_rate: float):
		"""在启动时立即设置通道的初始电压。"""
		self._targets[channel.value] = voltage
		self._currents[channel.value] = voltage
		self._ramps[channel.value] = ramp_rate
		self.controller.set_voltage(channel, voltage)

	def set_target(self, channel: c_char, voltage: float, ramp_rate: float):
		"""为通道设置一个新的目标电压以进行ramp。"""
		if channel.value not in self._currents:
			self._currents[channel.value] = 0.0
		self._targets[channel.value] = voltage
		self._ramps[channel.value] = ramp_rate

	def _update_all_voltages(self):
		"""由 QTimer 调用以更新所有电压。"""
		now = time.time()
		delta_t = now - self._last_update_time
		self._last_update_time = now
		if delta_t <= 0: return

		for ch_val, target_v in self._targets.items():
			current_v = self._currents.get(ch_val, 0.0)
			if abs(current_v - target_v) < 0.001: continue

			ramp_rate = self._ramps.get(ch_val, 1.0)
			max_change = ramp_rate * delta_t

			new_v = min(current_v + max_change, target_v) if target_v > current_v else max(current_v - max_change,
																						   target_v)

			self._currents[ch_val] = new_v
			self.controller.set_voltage(c_char(ch_val), new_v)


# =============================================================================
# 3. Custom Widget Classes
#    - 用于滑块和开关的 UI 组件
# =============================================================================
class ToggleSwitch(QWidget):
	"""自定义拨动开关控件。"""
	stateChanged = Signal(int)

	def __init__(self, parent=None, width=60):
		super().__init__(parent)
		self.setFixedSize(width, 30)
		self._checked = False
		self.setCursor(Qt.CursorShape.PointingHandCursor)

	def isChecked(self):
		return self._checked

	def setChecked(self, checked):
		if self._checked != checked:
			self._checked = checked
			self.stateChanged.emit(5 if self._checked else 0)
			self.update()

	def mousePressEvent(self, event):
		self.setChecked(not self._checked)
		super().mousePressEvent(event)

	def paintEvent(self, event):
		p = QPainter(self)
		p.setRenderHint(QPainter.RenderHint.Antialiasing)
		bg = QColor("#4CAF50") if self._checked else QColor("#BDBDBD")
		handle_color = QColor("#FFFFFF")
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(QBrush(bg))
		p.drawRoundedRect(self.rect(), 15, 15)
		pos = self.width() - 26 if self._checked else 4
		p.setBrush(QBrush(handle_color))
		p.drawEllipse(pos, 4, 22, 22)


class ToggleControl(QWidget):
	"""标签和拨动开关的组合控件。"""
	stateChanged = Signal(int)

	def __init__(self, name: str, parent=None):
		super().__init__(parent)
		layout = QVBoxLayout(self)
		layout.setContentsMargins(0, 5, 0, 5)
		layout.setSpacing(5)
		self.label = QLabel(name)
		self.label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
		self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self.switch = ToggleSwitch()
		self.switch.stateChanged.connect(self.stateChanged)
		sw_layout = QHBoxLayout()
		sw_layout.addStretch()
		sw_layout.addWidget(self.switch)
		sw_layout.addStretch()
		layout.addWidget(self.label)
		layout.addLayout(sw_layout)


class VoltageSlider(QWidget):
	"""包含标签、滑块和数值显示的组合控件。"""
	voltageChanged = Signal(float)

	def __init__(self, name: str, min_v: float, max_v: float, parent=None):
		super().__init__(parent)
		self.min_v, self.max_v = min_v, max_v
		self.slider_min, self.slider_max = 0, 1000
		layout = QGridLayout(self)
		layout.setContentsMargins(0, 5, 0, 5)
		self.name_label = QLabel(name)
		self.name_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
		self.slider = QSlider(Qt.Orientation.Horizontal)
		self.slider.setRange(self.slider_min, self.slider_max)
		self.value_label = QLabel("0.00 V")
		self.value_label.setMinimumWidth(60)
		self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
		layout.addWidget(self.name_label, 0, 0)
		layout.addWidget(self.slider, 1, 0)
		layout.addWidget(self.value_label, 1, 1)
		self.slider.valueChanged.connect(self._on_slider_change)

	def set_voltage(self, voltage: float):
		"""通过代码设置滑块的电压值并更新UI。"""
		self.slider.blockSignals(True)
		pos = self.slider_min + ((voltage - self.min_v) / (self.max_v - self.min_v)) * (
				self.slider_max - self.slider_min)
		self.slider.setValue(int(pos))
		self.value_label.setText(f"{voltage:.2f} V")
		self.slider.blockSignals(False)
		self.voltageChanged.emit(voltage)

	def _on_slider_change(self, value: int):
		"""当用户手动拖动滑块时调用。"""
		voltage = self.min_v + (value / self.slider_max) * (self.max_v - self.min_v)
		self.value_label.setText(f"{voltage:.2f} V")
		self.voltageChanged.emit(voltage)


# =============================================================================
# 4. Main Application Window
#    - 组装所有控件并将信号连接到控制器
# =============================================================================
class MainWindow(QMainWindow):
	def __init__(self):
		super().__init__()
		self.setWindowTitle("Staib Instruments Control Panel")
		self.setMinimumSize(900, 550)
		self._is_shutting_down = False

		self.controller = HardwareController()
		self.ramping_manager = RampingManager(self.controller, self)

		central_widget = QWidget()
		self.setCentralWidget(central_widget)
		main_layout = QVBoxLayout(central_widget)

		# --- 创建顶部布局 (开关和预设按钮) ---
		top_layout = QGridLayout()
		top_layout.setContentsMargins(10, 10, 10, 10)
		top_layout.setSpacing(10)

		self.comp_ctrl = ToggleControl("COMPUTER CONTROL")
		self.comp_ctrl.stateChanged.connect(self.safe_toggle_computer_control)
		top_layout.addWidget(self.comp_ctrl, 0, 0)

		preset_button_layout = QHBoxLayout()
		self.idle_button = QPushButton("Idle")
		self.work_button = QPushButton("Work")
		self.idle_button.setStyleSheet("padding: 8px; font-weight: bold;")
		self.work_button.setStyleSheet("padding: 8px; font-weight: bold;")
		preset_button_layout.addWidget(self.idle_button)
		preset_button_layout.addWidget(self.work_button)
		top_layout.addLayout(preset_button_layout, 0, 1, Qt.AlignmentFlag.AlignCenter)

		self.beam_blank = ToggleControl("BEAM BLANKING")
		self.beam_blank.stateChanged.connect(
			lambda v: self.controller.set_voltage(self.controller.BEAM_BLANKING, float(v)))
		top_layout.addWidget(self.beam_blank, 0, 2)

		top_layout.setColumnStretch(0, 1)
		top_layout.setColumnStretch(1, 1)
		top_layout.setColumnStretch(2, 1)
		main_layout.addLayout(top_layout)

		# --- 创建滑块布局 ---
		sliders_layout = QGridLayout()
		sliders_layout.setSpacing(20)
		sliders_layout.setContentsMargins(10, 10, 10, 10)

		self.energy_slider = VoltageSlider("ENERGY", 0.0, 10.0)
		self.filament_slider = VoltageSlider("FILAMENT", 0.0, 10.0)
		self.grid_slider = VoltageSlider("GRID", 0.0, 10.0)
		self.focus_slider = VoltageSlider("FOCUS", 0.0, 10.0)
		self.def_x_slider = VoltageSlider("DEFLECTION X", -10.0, 10.0)
		self.def_y_slider = VoltageSlider("DEFLECTION Y", -10.0, 10.0)
		self.beam_rock_slider = VoltageSlider("BEAM ROCKING", -10.0, 10.0)

		sliders_layout.addWidget(self.energy_slider, 0, 0)
		sliders_layout.addWidget(self.filament_slider, 0, 1)
		sliders_layout.addWidget(self.grid_slider, 1, 0)
		sliders_layout.addWidget(self.focus_slider, 1, 1)
		sliders_layout.addWidget(self.def_x_slider, 2, 0)
		sliders_layout.addWidget(self.def_y_slider, 2, 1)
		sliders_layout.addWidget(self.beam_rock_slider, 3, 0, 1, 2)
		main_layout.addLayout(sliders_layout)
		main_layout.addStretch()

		# --- NEW: 创建输出面板 ---
		self.output_panel = QTextEdit()
		self.output_panel.setReadOnly(True)
		self.output_panel.setFont(QFont("Courier", 9))
		self.output_panel.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ccc;")
		self.output_panel.setFixedHeight(150)
		main_layout.addWidget(self.output_panel)

		# --- 连接信号 ---
		self.idle_button.clicked.connect(self.set_idle_state)
		self.work_button.clicked.connect(self.set_work_state)
		self.energy_slider.voltageChanged.connect(
			lambda v: self.ramping_manager.set_target(self.controller.ENERGY, v, config.ENERGY_RAMP))
		self.filament_slider.voltageChanged.connect(
			lambda v: self.ramping_manager.set_target(self.controller.FILAMENT, v, config.FILAMENT_RAMP))
		self.grid_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.GRID, v))
		self.focus_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.FOCUS, v))
		self.def_x_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.DEFLECTION_X, v))
		self.def_y_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.DEFLECTION_Y, v))
		self.beam_rock_slider.voltageChanged.connect(
			lambda v: self.controller.set_voltage(self.controller.BEAM_ROCKING, v))

		# --- 初始化 ---
		self.setup_logging()
		self.open_device_and_show_status()
		self.initialize_states()
		self.ramping_manager.start()

	def setup_logging(self):
		"""NEW: 重定向 stdout 到输出面板。"""
		self._stream = Stream()
		self._stream.newText.connect(self.on_new_text)
		sys.stdout = self._stream
		# 存储原始 stdout 以便在退出时恢复
		self._original_stdout = sys.__stdout__

	def on_new_text(self, text: str):
		"""NEW: 将文本附加到输出面板。"""
		self.output_panel.moveCursor(QTextCursor.MoveOperation.End)
		self.output_panel.insertPlainText(text)

	def initialize_states(self):
		"""在启动时立即设置所有通道的初始值。"""
		print("正在初始化通道状态...")
		self.energy_slider.set_voltage(config.ENERGY_IDLE)
		self.filament_slider.set_voltage(config.FILAMENT_IDLE)
		self.grid_slider.set_voltage(config.GRID_CAL)
		self.focus_slider.set_voltage(config.FOCUS_CAL)
		self.def_x_slider.set_voltage(config.X_CAL)
		self.def_y_slider.set_voltage(config.Y_CAL)
		self.beam_rock_slider.set_voltage(0.0)

		self.ramping_manager.set_initial_state(self.controller.ENERGY, config.ENERGY_IDLE, config.ENERGY_RAMP)
		self.ramping_manager.set_initial_state(self.controller.FILAMENT, config.FILAMENT_IDLE, config.FILAMENT_RAMP)
		print("初始化完成。")

	def set_idle_state(self):
		"""将 ENERGY 和 FILAMENT ramp到 IDLE 状态。"""
		print("设置状态为: Idle")
		self.energy_slider.set_voltage(config.ENERGY_IDLE)
		self.filament_slider.set_voltage(config.FILAMENT_IDLE)

	def set_work_state(self):
		"""将 ENERGY 和 FILAMENT ramp到 WORK 状态。"""
		print("设置状态为: Work")
		self.energy_slider.set_voltage(config.ENERGY_WORK)
		self.filament_slider.set_voltage(config.FILAMENT_WORK)

	def open_device_and_show_status(self):
		"""尝试打开设备并显示结果。"""
		if not self.controller.open_device():
			QMessageBox.warning(self, "未能打开设备",
								"未能连接到采集卡\n请检查USB线是否插好\n以及驱动和dll库的设置是否正确")

	def safe_toggle_computer_control(self, voltage_value: float):
		"""安全地切换 COMPUTER_CONTROL 的状态。"""
		current_e = self.ramping_manager._currents.get(self.controller.ENERGY.value, 0.0)
		current_f = self.ramping_manager._currents.get(self.controller.FILAMENT.value, 0.0)

		is_idle = math.isclose(current_e, config.ENERGY_IDLE) and math.isclose(current_f, config.FILAMENT_IDLE)

		if is_idle:
			print("系统已处于 Idle 状态，立即切换 Computer Control。")
			self.controller.set_voltage(self.controller.COMPUTER_CONTROL, voltage_value)
		else:
			print("警告: 系统不处于 Idle 状态。将首先ramp到 Idle...")
			self.set_idle_state()

			time_e = abs(current_e - config.ENERGY_IDLE) / config.ENERGY_RAMP
			time_f = abs(current_f - config.FILAMENT_IDLE) / config.FILAMENT_RAMP
			delay_ms = int(max(time_e, time_f) * 1000) + 100

			print(f"预计ramp时间: {delay_ms / 1000:.1f} 秒。将在之后切换 Computer Control。")
			QTimer.singleShot(delay_ms,
							  lambda: self.controller.set_voltage(self.controller.COMPUTER_CONTROL, voltage_value))

	def _perform_final_shutdown(self):
		"""执行最后的关闭步骤。"""
		print("ramp完成。关闭 Computer Control 并退出。")
		self.comp_ctrl.switch.blockSignals(True)
		self.comp_ctrl.switch.setChecked(False)
		self.comp_ctrl.switch.blockSignals(False)

		self.controller.set_voltage(self.controller.COMPUTER_CONTROL, 0.0)
		self.controller.close_device()

		self._is_shutting_down = True
		self.close()

	def closeEvent(self, event):
		"""在退出时确保硬件先回到 Idle 状态并关闭。"""
		# 恢复 stdout
		sys.stdout = self._original_stdout

		if self._is_shutting_down:
			print("正在关闭应用程序...")
			event.accept()
			return

		if not self.comp_ctrl.switch.isChecked():
			print("Computer Control 已关闭。")
			self.controller.close_device()
			event.accept()
			return

		print("关闭请求：Computer Control 处于开启状态。")
		event.ignore()

		current_e = self.ramping_manager._currents.get(self.controller.ENERGY.value, 0.0)
		current_f = self.ramping_manager._currents.get(self.controller.FILAMENT.value, 0.0)
		is_idle = math.isclose(current_e, config.ENERGY_IDLE) and math.isclose(current_f, config.FILAMENT_IDLE)

		delay_ms = 0
		if not is_idle:
			print("系统不处于 idle 状态，ramp到idle...")
			self.set_idle_state()
			time_e = abs(current_e - config.ENERGY_IDLE) / config.ENERGY_RAMP
			time_f = abs(current_f - config.FILAMENT_IDLE) / config.FILAMENT_RAMP
			delay_ms = int(max(time_e, time_f) * 1000) + 100
		else:
			print("系统已处于 Idle 状态。")

		QTimer.singleShot(delay_ms, self._perform_final_shutdown)


# =============================================================================
# 5. Application Entry Point
# =============================================================================
if __name__ == "__main__":
	app = QApplication(sys.argv)
	window = MainWindow()
	window.show()
	sys.exit(app.exec())
