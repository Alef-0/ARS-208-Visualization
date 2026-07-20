import numpy as np
import sys
from bitstring import BitArray

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
    
    def fill_701(self, message: list):
        if message[0] > self.max_amount: self.max_amount = message[0]
        id = message[0]
        self.y[id] = message[1]; self.x[id] = message[2]  # Longitude e latitude
        self.dyn[id] = message[3]  # dynprop
    
    def fill_702(self, message: list):
        if message[0] > self.max_amount: self.max_amount = message[0]
        id = message[0]
        self.pdh[id] = message[1]   # PDH
        self.ambg[id] = message[2]  # ambig
        self.inv[id] = message[3]   # invalid_state


from bitstring import BitArray

# ==============================================================================
# 0x200: Create Radar Configuration (TX)
# ==============================================================================
def create_200_radar_configuration(ok_distance, distance, ok_radarpower, radarpower, 
                                   ok_output, output, ok_rcs, rcs,
                                   ok_qual, quality, save_nvm):
    """
    Builds the 8-byte Big-Endian payload for configuring the ARS40X radar (0x200).
    References exact Start bits and Lengths from Tables 2 & 3.
    """
    # Initialize a clean 64-bit block of zeros
    bits = bytearray(8)
    
    # --- BYTE 0: Configuration Update Validity Bits ---
    # bits[0:1]   = [ok_distance]       # RadarCfg_MaxDistance_valid (Start: 0, Len: 1)
    bits[0] += ok_distance
    # bits[1:2]   = [0]                 # RadarCfg_SensorID_valid (Not used here, default 0)
    # bits[2:3]   = [ok_radarpower]     # RadarCfg_RadarPower_valid (Start: 2, Len: 1)
    bits[0] += ok_radarpower << 2
    # bits[3:4]   = [ok_output]         # RadarCfg_OutputType_valid (Start: 3, Len: 1)
    bits[0] += ok_output << 3
    # bits[4:5]   = [ok_qual]           # RadarCfg_SendQuality_valid (Start: 4, Len: 1)
    bits[0] += ok_qual << 4
    # bits[5:6]   = [0]                 # RadarCfg_SendExtInfo_valid (Not used here, default 0)
    # bits[6:7]   = [0]                 # RadarCfg_SortIndex_valid (Not used here, default 0)
    # bits[7:8]   = [save_nvm]          # RadarCfg_StoreInNVM_valid (Start: 7, Len: 1)
    bits[0] += save_nvm << 7
    
    # # --- BYTES 1 - 7: Configuration Parameter Values ---
    # bits[22:32] = distance            # RadarCfg_MaxDistance (Start: 22, Len: 10)
    bits[1] += distance >> 2
    bits[2] += (distance << 6) & 0xFF
    # bits[35:37] = output              # RadarCfg_OutputType (Start: 35, Len: 2)
    bits[4] += (output << 3)
    # bits[37:40] = radarpower          # RadarCfg_RadarPower (Start: 37, Len: 3)
    bits[4] += (radarpower << 5)
    # bits[42:43] = [quality]           # RadarCfg_SendQuality (Start: 42, Len: 1)
    bits[5] += (quality << 2)
    # bits[47:48] = [save_nvm]          # RadarCfg_StoreInNVM (Start: 47, Len: 1)
    bits[5] += (save_nvm << 7)
    # bits[48:49] = [ok_rcs]            # RadarCfg_RCS_Threshold_valid (Start: 48, Len: 1)
    bits[6] += (ok_rcs)
    # bits[49:52] = rcs                 # RadarCfg_RCS_Threshold (Start: 49, Len: 3)
    bits[6] += (rcs << 1)
    
    # Return as integer to match downstream connection framework packing setups
    return int.from_bytes(bits)


# ==============================================================================
# 0x201: Read Radar State (RX)
# ==============================================================================
def read_201_radar_state(package: bytearray):
    """
    Parses the 0x201 cyclical state response payload from the radar.
    References exact configurations and indices from Tables 10 & 11.
    """
    # bits = BitArray(package)
    
    # Extract parameter states using precise documentation bit boundaries
    # max_distance_cfg = bits[22:32].uint   # RadarState_MaxDistanceCfg (Start: 22, Len: 10)
    max_distance_cfg = ((package[1] << 2) + (package[2] >> 6))
    # radar_power_cfg  = bits[39:42].uint   # RadarState_RadarPowerCfg (Start: 39, Len: 3)
    radar_power_cfg = (package[3] << 1) + (package[4] >> 7)
    # output_type_cfg  = bits[42:44].uint   # RadarState_OutputTypeCfg (Start: 42, Len: 2)
    output_type_cfg = (package[5] >> 2) & 0x03
    # send_quality_cfg = bits[44:45].uint   # RadarState_SendQualityCfg (Start: 44, Len: 1)
    send_quality_cfg = (package[5] >> 4) & 0x01
    # rcs_threshold    = bits[58:61].uint   # RadarState_RCS_Threshold (Start: 58, Len: 3)
    rcs_threshold = package[7] >> 2
    
    # Return directly formatted tuple matching thread_201_message expectations
    return max_distance_cfg, radar_power_cfg, output_type_cfg, rcs_threshold, send_quality_cfg, hex(int.from_bytes(package, "big"))

# ==========================================
# 0x701: Read Cluster List - General (RX)
# ==========================================
def read_701_cluster_list(package: bytearray):
    # bits = BitArray(package)
    
    # ID       = bits[0:8].uint
    ID = package[0]
    # dist_lon = bits[19:32].uint
    dist_lon = (package[1] << 5) + (package[2] >> 3)
    # dist_lat = bits[24:34].uint
    dist_lat = ((package[2] & 0x3) << 8) + package[3]
    # DynProp  = bits[48:51].uint
    DynProp = (package[6] & 0x7) 

    new_long = dist_lon * 0.2 - 500.0   # Scale and Offset configuration
    new_lat  = dist_lat * 0.2 - 102.3   
    
    return ID, new_long, new_lat, DynProp


# ==========================================
# 0x702: Read Cluster List - Quality (RX)
# ==========================================
def read_702_quality_info(package: bytearray):
    # bits = BitArray(package)[::-1] 
    
    # # Pull out every index variable requested by your Clusters_messages tracking class
    # ID            = bits[0:8].uint       # Start: 0,  Len: 8
    ID = package[0]
    # pdh0          = bits[24:27].uint     # Start: 24, Len: 3 (Cluster_Pdho)
    pdh0 = package[3] & 0x07
    # ambig_state   = bits[32:35].uint     # Start: 32, Len: 3 (Cluster_AmbigState)
    ambig_state = package[4] & 0x07
    # invalid_state = bits[35:40].uint     # Start: 35, Len: 5 (Cluster_InvalidState)
    invalid_state = package[4] >> 3

    
    # # Return formatted variables perfectly mapped to your fill_702 setup
    return ID, pdh0, ambig_state, invalid_state