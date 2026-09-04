# GPS integration

`gps_connection.py` polls the DVR position endpoint once per second while GPS
monitoring is enabled. It converts the DVR's latitude/longitude representation
to signed coordinates, formats degrees/minutes/seconds for the GUI, and keeps a
Google Maps URL for the most recent successful position.

The endpoint address and digest-authentication credentials are currently fixed
in the module. Network failures are reported to the terminal and polling
continues on the next interval.
