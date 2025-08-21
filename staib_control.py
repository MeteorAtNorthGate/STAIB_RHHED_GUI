import sys
import time
from ctypes import windll, c_int, c_char, c_float

from PySide6.QtWidgets import (
	QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
	QLabel, QSlider, QMessageBox, QPushButton
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QPainter, QColor, QBrush, QFont

# 尝试导入用户定义的配置文件
try:
	import config

	print("成功加载 config.py")
except ImportError:
	print("警告: 未找到 config.py。将使用默认值。")


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
			print(f"设置通道 {ord(channel.value)} 为 {voltage:.2f} V")


# =============================================================================
# 2. Ramping Manager Class
#    - 处理平滑的电压过渡逻辑
# =============================================================================
class RampingManager(QObject):
	"""管理所有适用通道的电压渐变。"""

	def __init__(self, controller: HardwareController, parent=None):
		super().__init__(parent)
		self.controller = controller
		self._targets = {}
		self._currents = {}
		self._ramps = {}

		self._timer = QTimer(self)
		self._timer.setInterval(50)  # 每秒更新20次以实现平滑渐变
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
		"""为通道设置一个新的目标电压以进行渐变。"""
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

	def setChecked(self, checked):
		if self._checked != checked: self._checked = checked; self.stateChanged.emit(
			5 if self._checked else 0); self.update()

	def mousePressEvent(self, event): self.setChecked(not self._checked); super().mousePressEvent(event)

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
		# 阻止信号循环触发
		self.slider.blockSignals(True)
		# 将电压转换为滑块位置
		pos = self.slider_min + ((voltage - self.min_v) / (self.max_v - self.min_v)) * (
				self.slider_max - self.slider_min)
		self.slider.setValue(int(pos))
		self.value_label.setText(f"{voltage:.2f} V")
		self.slider.blockSignals(False)
		# 手动发射信号以确保状态一致
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
		self.setMinimumWidth(500)

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
		self.comp_ctrl.stateChanged.connect(
			lambda v: self.controller.set_voltage(self.controller.COMPUTER_CONTROL, float(v)))
		top_layout.addWidget(self.comp_ctrl, 0, 0)

		# Idle/Work 按钮
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

		# --- 连接信号 ---
		self.idle_button.clicked.connect(self.set_idle_state)
		self.work_button.clicked.connect(self.set_work_state)

		# ENERGY and FILAMENT use the ramping manager for smooth transitions
		self.energy_slider.voltageChanged.connect(
			lambda v: self.ramping_manager.set_target(self.controller.ENERGY, v, config.ENERGY_RAMP))
		self.filament_slider.voltageChanged.connect(
			lambda v: self.ramping_manager.set_target(self.controller.FILAMENT, v, config.FILAMENT_RAMP))

		# MODIFIED: These sliders now directly control the hardware for immediate response
		self.grid_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.GRID, v))
		self.focus_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.FOCUS, v))
		self.def_x_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.DEFLECTION_X, v))
		self.def_y_slider.voltageChanged.connect(lambda v: self.controller.set_voltage(self.controller.DEFLECTION_Y, v))
		self.beam_rock_slider.voltageChanged.connect(
			lambda v: self.controller.set_voltage(self.controller.BEAM_ROCKING, v))

		# --- 初始化 ---
		# 修正: 无论设备是否连接成功，都执行初始化
		self.open_device_and_show_status()
		self.initialize_states()
		self.ramping_manager.start()

	def initialize_states(self):
		"""在启动时立即设置所有通道的初始值。"""
		print("正在初始化通道状态...")
		# 设置滑块UI。对于直接控制的滑块，这也会立即更新硬件。
		self.energy_slider.set_voltage(config.ENERGY_IDLE)
		self.filament_slider.set_voltage(config.FILAMENT_IDLE)
		self.grid_slider.set_voltage(config.GRID_CAL)
		self.focus_slider.set_voltage(config.FOCUS_CAL)
		self.def_x_slider.set_voltage(config.X_CAL)
		self.def_y_slider.set_voltage(config.Y_CAL)
		# BEAM ROCKING starts at its default value

		# 初始化 Ramping Manager 的内部状态 (仅限需要渐变的通道)
		self.ramping_manager.set_initial_state(self.controller.ENERGY, config.ENERGY_IDLE, config.ENERGY_RAMP)
		self.ramping_manager.set_initial_state(self.controller.FILAMENT, config.FILAMENT_IDLE, config.FILAMENT_RAMP)

	def set_idle_state(self):
		"""将 ENERGY 和 FILAMENT 渐变到 IDLE 状态。"""
		print("设置状态为: Idle")
		self.energy_slider.set_voltage(config.ENERGY_IDLE)
		self.filament_slider.set_voltage(config.FILAMENT_IDLE)

	def set_work_state(self):
		"""将 ENERGY 和 FILAMENT 渐变到 WORK 状态。"""
		print("设置状态为: Work")
		self.energy_slider.set_voltage(config.ENERGY_WORK)
		self.filament_slider.set_voltage(config.FILAMENT_WORK)

	def open_device_and_show_status(self):
		"""尝试打开设备并显示结果。"""
		if not self.controller.open_device():
			QMessageBox.warning(self, "未能打开设备",
								"未能连接到采集卡\n请检查USB线是否插好\n以及驱动和dll库的设置是否正确")

	def closeEvent(self, event):
		"""在退出时确保硬件连接已关闭。"""
		print("正在关闭应用程序...")
		self.controller.close_device()
		event.accept()


# =============================================================================
# 5. Application Entry Point
# =============================================================================
if __name__ == "__main__":
	app = QApplication(sys.argv)
	window = MainWindow()
	window.show()
	sys.exit(app.exec())
