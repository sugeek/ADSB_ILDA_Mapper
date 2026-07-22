from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
import threading
import time

def handler(address, *args):
    print(f"OSC Command Received: {address} with args {args}")

def run_server(port):
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(handler)
    server = BlockingOSCUDPServer(("127.0.0.1", port), dispatcher)
    print(f"Server listening on port {port}...")
    server.serve_forever()

if __name__ == "__main__":
    ports = [7000, 8000, 9000]
    for port in ports:
        thread = threading.Thread(target=run_server, args=(port,), daemon=True)
        thread.start()
    
    print("All simulators running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down.")