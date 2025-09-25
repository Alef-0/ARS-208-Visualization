import numpy as np
import sys

ba = lambda x: bytearray(x)

class Clusters_messages():
	def __init__(self):
		self.max_amount = 0
		self.x = {}; self.y = {}
		self.dyn = {}
		self.pdh = {}; self.ambg = {}; self.inv = {}
	
	def clear(self):
		self.max_amount = 0
		self.x.clear(); self.y.clear()
		self.dyn.clear()
		self.pdh.clear(); self.ambg.clear(); self.inv.clear()
	
	def fill_701(self, message : list):
		if message[0] < self.max_amount: print("Something is wrong with the amount")
		if message[0] > self.max_amount: self.max_amount = message[0]
		id = message[0]
		self.y[id] = message[1]; self.x[id] = message[2] # Longitude e latitude
		self.dyn[id] = message[3] # dynprop
	
	def fill_702(self, message :list):
		if message[0] > self.max_amount: self.max_amount = message[0]
		id = message[0]
		self.pdh[id] = message[1] # PDH
		self.ambg[id] = message[2] # ambig
		self.inv[id] = message[3]

def create_200_radar_configuration(ok_distance, distance, ok_radarpower, radarpower, 
								    ok_output, output, ok_rcs, rcs,
									ok_qual, quality
								   ):
	raw = 0x0
	
	# Distance
	raw += ok_distance << 56 
	raw += distance << 46 # Treshold de Max Distance de (196 - 1200) / 2
	# RadarPower
	raw += ok_radarpower << 58 
	raw += radarpower << 29 # Radar Power [STANDARD, -3, -6, -9]
	# Output
	raw += ok_output << 59
	raw += output << 27 # [None, Object, Cluster, DONOTUSE]
	# RCS
	raw += ok_rcs << 8
	raw += rcs << 9
	# External
	raw += ok_qual << 60
	raw += quality << 18

	return raw

def read_201_radar_state(package : bytearray):
	raw = int.from_bytes(package, "big")

	# Pegar variaveis
	# Linhas 7 -> 6 (+ 1)
	raw >>= 2 # Limpar parte
	RCS_Treshold = 		raw & 0x07;	raw >>= 3 + 12
	# Linhas 5
	CtrlRelayCfg = 		raw & 0x1; 	raw >>= 1
	OutputTypeCfg = 	raw & 0x3; 	raw >>= 2
	SendQualityCfg = 	raw & 0x1;  raw >>= 1
	SendExtInfoCfg = 	raw & 0x1;	raw >>= 1
	MotionRxState = 	raw & 0x3; 	raw >>= 2
	# Linhas 4 e 3
	SensorID = 			raw & 0x7 ; raw >>= 3 + 1
	SortIndex = 		raw & 0x7 ;	raw >>= 3
	RadarPowerCfg = 	raw & 0x7 ; raw >>= 3 + 7
	# Linhas 2 e 1
	VoltageError = 		raw & 0x1 ;	raw >>= 1
	TemporaryError = 	raw & 0x1 ;	raw >>= 1
	TemperatureError=	raw & 0x1 ;	raw >>= 1
	Interference = 		raw & 0x1 ; raw >>= 1
	PersistentError= 	raw & 0x1 ; raw >>= 1
	MaxDistanceCfg = 	raw & 0x3FF;raw >>= 10 + 6
	# Linhas 0
	NVMReadStatus =		raw & 0x01;	raw >>= 1
	NVMWriteStatus=		raw & 0x01;	raw >>= 1
	
	return MaxDistanceCfg, RadarPowerCfg, OutputTypeCfg, RCS_Treshold, SendQualityCfg, hex(int.from_bytes(package, "big"))

def read_701_cluster_list(package : bytearray):
	raw = int.from_bytes(package, "big")

	rcs = raw & 0xFF; 			raw = raw >> 8
	DynProp = raw & 0x07;		raw = raw >> 3 + 2
	vrel_lat = raw & 0x1FF; 	raw = raw >> 9
	vrel_lon = raw & 0x3FF; 	raw = raw >> 10;
	dist_lat = raw & 0x3FF; 	raw = raw >> 10 + 1;
	dist_lon = raw & 0x1FFF;	raw = raw >> 13
	ID = raw & 0xFF;

	# Conversão de coisas
	new_long = dist_lon * 0.2 + (-500) # de -500 -> 1338.2
	new_lat = dist_lat * 0.2 + (-102.3) # +- 102
	new_vlong = vrel_lon * 0.25 + (-128) # +- 128
	return ID, new_long, new_lat, DynProp

def read_702_quality_info(package : bytearray):
	raw = int.from_bytes(package, "big")
	# print(hex(raw))
	raw = raw >> 24 # Começa no 4° byte

	ambig_state = raw 	& 0x7; 	raw = raw >> 3
	invalid_state = raw & 0x1F;	raw = raw >> 5
	PDH0 = raw & 0x7; raw = raw >> 3
	raw = raw >> 21 # Pula para o id
	ID = raw
	# print(hex(raw), "later")
	

	return ID, PDH0, ambig_state, invalid_state

