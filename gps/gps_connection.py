import signal
import time
import webbrowser

import requests
import urllib3
from requests.auth import HTTPDigestAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
DVR_IP = "192.168.1.108"
USERNAME = "admin"
PASSWORD = "l1v3user5"
URL = f"http://{DVR_IP}/cgi-bin/positionManager.cgi?action=getStatus"


def get_gps():
    response = requests.get(URL, auth=HTTPDigestAuth(USERNAME, PASSWORD), verify=False, timeout=3)
    response.raise_for_status()
    return response.text


def dms_to_dd(degrees, minutes, seconds):
    return degrees + minutes / 60.0 + seconds / 3600.0


def dd_to_dms(decimal):
    degrees = int(decimal)
    minutes = int((decimal - degrees) * 60)
    seconds = ((decimal - degrees) * 60 - minutes) * 60
    return degrees, minutes, seconds


def transform_into_coordinates(text):
    longitude_text = latitude_text = ""
    lon_value = lat_value = 0
    for line in text.split():
        if "Latitude" in line:
            lat_value = dms_to_dd(*[float(x) for x in line[17:-1].split(",")]) - 90.0
            degrees, minutes, seconds = dd_to_dms(abs(lat_value))
            latitude_text = f"{degrees}° {minutes}' {seconds:.3f}'' {'S' if lat_value < 0 else 'N'}"
        elif "Longitude" in line:
            lon_value = dms_to_dd(*[float(x) for x in line[18:-1].split(",")]) - 180.0
            degrees, minutes, seconds = dd_to_dms(abs(lon_value))
            longitude_text = f"{degrees}° {minutes}' {seconds:.3f}'' {'W' if lon_value < 0 else 'E'}"
    return f"{latitude_text}, {longitude_text}", f"https://www.google.com/maps/search/?api=1&query={lat_value},{lon_value}"


def main(connection, pool, shutdown_event):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())
    reading = False
    maps_link = "https://www.google.com/maps/search/?api=1&query=0,0"
    next_read = 0.0
    
    while not shutdown_event.is_set():
        while connection.poll():
            event, _ = connection.recv()
            if event == "STOP":
                shutdown_event.set()
            elif event == "conn_gps":
                reading = not reading
                pool.put((event, reading))
            elif event == "gps_maps":
                webbrowser.open(maps_link)
        now = time.monotonic()
        if reading and now >= next_read:
            try:
                text, maps_link = transform_into_coordinates(get_gps())
                pool.put(("gps_text", text))
            except requests.RequestException as exc:
                print(f"GPS request failed: {exc}")
            next_read = now + 1.0
        shutdown_event.wait(0.05)
