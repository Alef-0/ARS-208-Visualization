import re
import signal
from multiprocessing import Event, Pipe, Process, Queue
from queue import Empty

import FreeSimpleGUI as sg

from camera.camera_gstreamer import gstreamer_main
from connection.connection_main import create_connection_communication
from gps.gps_connection import main as gps_main
from menu_configurations import Configurations


def check_popup():
    layout = [
