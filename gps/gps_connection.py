import requests
from requests.auth import HTTPDigestAuth
import urllib3
from multiprocessing.connection import Connection
from multiprocess.queues import Queue
from multiprocessing import Pipe
import time


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
DVR_IP = "192.168.1.108"
USERNAME = "admin"
PASSWORD = "l1v3user5"
URL = f"http://{DVR_IP}/cgi-bin/positionManager.cgi?action=getStatus"

def get_gps():
    response = requests.get(URL, auth=HTTPDigestAuth(USERNAME, PASSWORD), verify=False)
    return response.text

def transform_into_coordinates(text : str):
    lines = text.split()
    longitude_text = ""
    latitude_text = ""

    for line in lines:
        if "Longitude" in line:
            numbers = line[18:-1].split(",")
            new_num = int(numbers[0]) - 180
            if new_num < 0: longitude_text = f"{abs(new_num)}° {numbers[1]}' {numbers[2]}\" W"
            else:           longitude_text = f"{abs(new_num)}° {numbers[1]}' {numbers[2]}\" E"
            print("Longitude", longitude_text, line)
        if "Latitude" in line:
            numbers = line[17:-1].split(",")
            new_num = int(numbers[0]) - 90
            if new_num < 0: latitude_text = f"{abs(new_num)}° {numbers[1]}' {numbers[2]}\" S"
            else:           latitude_text = f"{abs(new_num)}° {numbers[1]}' {numbers[2]}\" N"
            print("Latitude", latitude_text, line)
    
    return f"{latitude_text}, {longitude_text} (WRONG)"

def main(connection : Connection, pool : Queue):
    read = False

    while True:
        if read: 
            all_values = get_gps()
            pool.put(("gps_text", transform_into_coordinates(all_values)))
            time.sleep(1)
        
        # Tratar dos callbacks
        if connection.poll():
            event, value = connection.recv()
            match event:
                case "conn_gps": 
                    read = not read;
                    pool.put((event, read))
                


if __name__ == "__main__": 
    send, receive = Pipe()
    q = Queue(5)
    main()
