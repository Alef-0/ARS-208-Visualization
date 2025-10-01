import socket

def tcp_ping(host: str, port: int, timeout: int = 2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

print(tcp_ping("google.com", 80))   # True if reachable via HTTP
