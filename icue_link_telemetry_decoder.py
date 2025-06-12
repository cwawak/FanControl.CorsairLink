# iCUE LINK Telemetry Decoder (Protocol-Driven)
#
# This script is a direct Python implementation of the protocol used by the
# working FanControl.CorsairLink driver. It uses an active, transactional
# polling method to query the device for each sensor group.

import hid
import time
import struct

# --- DEVICE CONFIGURATION ---
VENDOR_ID = 0x1B1C
PRODUCT_ID = 0x0C3F
# Per the protocol, HID packets are 512 bytes plus a report ID byte.
# python-hid automatically prepends the report ID when sending so we
# allocate 512 bytes for reads and writes here.
PACKET_SIZE = 512

# --- PROTOCOL CONSTANTS (from FanControl.CorsairLink) ---
CMD_HEADER = bytes([0x00, 0x00, 0x01])
CMD_ENTER_SOFTWARE_MODE = bytes([0x01, 0x03, 0x00, 0x02])
CMD_OPEN_ENDPOINT = bytes([0x0d, 0x01])
CMD_CLOSE_ENDPOINT = bytes([0x05, 0x01, 0x01])
CMD_READ = bytes([0x08, 0x01])

ENDPOINT_SPEEDS = bytes([0x17])
ENDPOINT_TEMPS = bytes([0x21])

DATA_TYPE_SPEEDS = bytes([0x25, 0x00])
DATA_TYPE_TEMPS = bytes([0x10, 0x00])

# --- RESPONSE PARSING CONSTANTS ---
ERROR_CODE_INDEX = 4
DATA_TYPE_START_INDEX = 5
SENSOR_COUNT_INDEX = 6
SENSOR_DATA_START_INDEX = 7
SENSOR_BLOCK_SIZE = 3
TEMP_SCALING_FACTOR = 10.0

READ_TIMEOUT_SEC = 0.5


def create_command_packet(command: bytes, data: bytes = bytes()) -> bytes:
    """Wraps a command and data in the required header."""
    packet = bytearray(PACKET_SIZE)
    full = CMD_HEADER + command + data
    packet[: len(full)] = full
    return bytes(packet)


def read_packet(device: hid.Device) -> bytes | None:
    """Reads a single packet from the device."""
    data = device.read(PACKET_SIZE)
    return bytes(data) if data else None


def send_command(device: hid.Device, command: bytes, data: bytes = bytes(), wait_for_type: bytes | None = None) -> bytes | None:
    """Send a command and optionally wait for a response with a specific data type."""
    packet = create_command_packet(command, data)
    device.write(packet)

    response = read_packet(device)
    if not response:
        return None
    if response[ERROR_CODE_INDEX] != 0:
        return None

    if wait_for_type is None:
        return response

    if response[DATA_TYPE_START_INDEX:DATA_TYPE_START_INDEX + 2] == wait_for_type:
        return response

    start = time.monotonic()
    while time.monotonic() - start < READ_TIMEOUT_SEC:
        resp = read_packet(device)
        if resp and resp[ERROR_CODE_INDEX] == 0 and resp[DATA_TYPE_START_INDEX:DATA_TYPE_START_INDEX + 2] == wait_for_type:
            return resp
    return None


def parse_sensors(packet: bytes | None, is_temp: bool = False) -> list:
    """Parses speed or temperature packets."""
    if not packet or len(packet) <= SENSOR_DATA_START_INDEX:
        return []

    sensors = []
    count = packet[SENSOR_COUNT_INDEX]
    data = packet[SENSOR_DATA_START_INDEX:]
    for i in range(count):
        off = i * SENSOR_BLOCK_SIZE
        if off + 2 >= len(data):
            break
        status = data[off]
        if status == 0:
            raw = struct.unpack_from('<h', data, off + 1)[0]
            sensors.append(raw / TEMP_SCALING_FACTOR if is_temp else raw)
        else:
            sensors.append(None)
    return sensors


def main() -> None:
    device = None
    try:
        info = hid.enumerate(VENDOR_ID, PRODUCT_ID)
        if not info:
            print("Device not found.")
            return
        path = info[0]['path']
        print(f"Opening device at {path.decode('utf-8')}")
        device = hid.device()
        device.open_path(path)
        device.set_nonblocking(1)

        print("Entering software mode...")
        send_command(device, CMD_ENTER_SOFTWARE_MODE)

        print("\n--- Live iCUE LINK Telemetry (Ctrl+C to exit) ---")
        while True:
            send_command(device, CMD_CLOSE_ENDPOINT, ENDPOINT_TEMPS)
            send_command(device, CMD_OPEN_ENDPOINT, ENDPOINT_TEMPS)
            t_pkt = send_command(device, CMD_READ, wait_for_type=DATA_TYPE_TEMPS)
            send_command(device, CMD_CLOSE_ENDPOINT, ENDPOINT_TEMPS)
            temps = parse_sensors(t_pkt, is_temp=True)

            send_command(device, CMD_CLOSE_ENDPOINT, ENDPOINT_SPEEDS)
            send_command(device, CMD_OPEN_ENDPOINT, ENDPOINT_SPEEDS)
            s_pkt = send_command(device, CMD_READ, wait_for_type=DATA_TYPE_SPEEDS)
            send_command(device, CMD_CLOSE_ENDPOINT, ENDPOINT_SPEEDS)
            speeds = parse_sensors(s_pkt)

            liquid = f"{temps[0]:.1f}°C" if temps and temps[0] is not None else "N/A"
            pump = str(speeds[0]) if speeds and speeds[0] is not None else "N/A"
            fans = [str(s) if s is not None else "N/A" for s in speeds[1:]] if speeds else []
            print(f"Liquid: {liquid} | Pump: {pump} RPM | Fans: {', '.join(fans)} RPM")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if device:
            send_command(device, bytes([0x01, 0x03, 0x00, 0x01]))
            device.close()
            print("Device connection closed.")


if __name__ == "__main__":
    main()
