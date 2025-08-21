import ctypes                #数据转C类型
from ctypes import *

dll = windll.LoadLibrary(r'.\lib\x64\USB3000.dll')   #调用动态链接库   x64动态链接库就在工程文件下

#如需了解更改采集卡函数功能，请查看函数手册查找函数参数功能！！！！！！！！！！！！！

##########################################################################################################
# 将数据转换c数据类型
# 索引 一张卡默认是0
DevIndex = ctypes.c_int(0)
# 设置模拟输出通道
GRID = ctypes.c_char(1)
FOCUS = ctypes.c_char(2)
BEAM_BLANKING = ctypes.c_char(3)
FILAMENT = ctypes.c_char(4)
ENERGY = ctypes.c_char(5)
DEFLECTION_X = ctypes.c_char(6)
DEFLECTION_Y = ctypes.c_char(7)
BEAM_ROCKING = ctypes.c_char(8)
#n.c.8
#n.c.9
COMPUTER_CONTROL = ctypes.c_char(11)
#n.c.11
#SIGNAL_GROUND = ctypes.c_char(12)
#CASE_GROUND = ctypes.c_char(13)
#n.c.14~21
#CASE_GROUND = ctypes.c_char(22)
#n.c.23
#n.c.24

############################################################################################################
if __name__ == "__main__":
	#打开采集卡
	_ = dll.USB3OpenDevice(DevIndex)
	print(_)

	# 设置对应通道电压值
	GRID_v = ctypes.c_float(1)

	#设置模拟输出对应通道对应电压值
	temp = dll.SetUSB3AoImmediately(DevIndex,GRID,GRID_v)
	print(temp)

	#关闭采集卡
	dll.USB3CloseDevice(DevIndex)