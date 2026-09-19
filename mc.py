try:
    import ustruct as struct
except ImportError:
    import struct

try:
    import usocket as socket
except ImportError:
    import socket

import time
import gc

try:
    import network
except ImportError:
    network = None

try:
    import machine
except ImportError:
    machine = None

try:
    ConnectionError
except NameError:
    class ConnectionError(Exception):
        pass


WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
WIFI_TIMEOUT = 30

PROTOCOL_VERSION = 47
GAME_VERSION_STRING = "1.8.9"

MOTD = "A Tiny MicroPython Server"
MAX_PLAYERS = 8

HS_HANDSHAKE = 0x00

ST_STATUS_REQUEST = 0x00
ST_STATUS_RESPONSE = 0x00
ST_PING_REQUEST = 0x01
ST_PING_RESPONSE = 0x01

LG_LOGIN_START = 0x00
LG_LOGIN_SUCCESS = 0x02

PL_KEEPALIVE_CB = 0x00
PL_JOIN_GAME_CB = 0x01
PL_CHAT_CB = 0x02
PL_TIME_UPDATE_CB = 0x03
PL_SPAWN_POSITION_CB = 0x05
PL_PLAYER_POS_LOOK = 0x08
PL_HELD_ITEM_CB = 0x09
PL_CHUNK_DATA_CB = 0x21
PL_BLOCK_CHANGE_CB = 0x23
PL_PLAYER_ABILITIES = 0x39
PL_SERVER_DIFFICULTY = 0x41

PL_KEEPALIVE_SB = 0x00
PL_CHAT_SB = 0x01
PL_PLAYER_SB = 0x03
PL_PLAYER_POS_SB = 0x04
PL_PLAYER_LOOK_SB = 0x05
PL_PLAYER_POS_LOOK_SB = 0x06
PL_PLAYER_DIGGING_SB = 0x07

DIG_STATUS_STARTED = 0
DIG_STATUS_CANCELLED = 1
DIG_STATUS_FINISHED = 2

DEBUG = True

SPAWN_X = 8.5
SPAWN_Y = 64.0
SPAWN_Z = 8.5
GROUND_Y = 63

GRASS_BLOCK_ID = 2
BARRIER_BLOCK_ID = 166

KEEPALIVE_INTERVAL = 10.0
TEMP_INTERVAL = 15.0
RESYNC_INTERVAL = 30.0
SOCKET_TIMEOUT = 1.0
HANDSHAKE_TIMEOUT = 5.0
ACCEPT_TIMEOUT = 1.0

MAX_PACKET_LENGTH = 1 << 17
MAX_STRING_LENGTH = 32767

LED_BLINK_MS = 100
LED_BUSY_GRACE = 0.5

GC_INTERVAL = 20.0

WATCHDOG_MS = 8000


_led_pin = None
_led_timer = None
_led_state = True
_led_busy_until = 0.0
_wdt = None


def _led_tick(timer):
    global _led_state
    if _led_pin is None:
        return
    try:
        busy = time.time() < _led_busy_until
        if busy:
            _led_state = not _led_state
            _led_pin.value(1 if _led_state else 0)
        else:
            if not _led_state:
                _led_state = True
                _led_pin.value(1)
    except Exception:
        pass


def led_init():
    global _led_pin, _led_timer
    if machine is None:
        return
    try:
        _led_pin = machine.Pin("LED", machine.Pin.OUT)
    except Exception:
        try:
            _led_pin = machine.Pin(25, machine.Pin.OUT)
        except Exception:
            _led_pin = None
            return
    try:
        _led_pin.value(1)
    except Exception:
        pass
    try:
        _led_timer = machine.Timer()
        _led_timer.init(period=LED_BLINK_MS, mode=machine.Timer.PERIODIC, callback=_led_tick)
    except Exception:
        _led_timer = None


def led_busy(seconds=LED_BUSY_GRACE):
    global _led_busy_until
    _led_busy_until = time.time() + seconds


def watchdog_init():
    global _wdt
    if machine is None:
        return
    try:
        _wdt = machine.WDT(timeout=WATCHDOG_MS)
    except Exception:
        _wdt = None


def watchdog_feed():
    if _wdt is not None:
        try:
            _wdt.feed()
        except Exception:
            pass


def write_varint(value):
    out = bytearray()
    value &= 0xFFFFFFFF
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def read_varint(sock_reader):
    num = 0
    shift = 0
    while True:
        b = sock_reader.read_exact(1)[0]
        num |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift > 35:
            raise ValueError("VarInt too big")
    if num & (1 << 31):
        num -= 1 << 32
    return num


def write_string(s):
    data = s.encode("utf-8")
    return write_varint(len(data)) + data


def write_ushort(v):
    return struct.pack(">H", v)


def write_bool(b):
    return b"\x01" if b else b"\x00"


def write_double(v):
    return struct.pack(">d", v)


def write_float(v):
    return struct.pack(">f", v)


def write_long(v):
    return struct.pack(">q", v)


def write_int(v):
    return struct.pack(">i", v)


def write_byte(v):
    return struct.pack(">b", v)


def write_ubyte(v):
    return struct.pack(">B", v)


def json_escape(s):
    out = []
    for ch in s:
        if ch == '"' or ch == "\\":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 0x20:
            continue
        else:
            out.append(ch)
    return "".join(out)


class SockReader:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(1024, n - len(self.buf)))
            if not chunk:
                raise ConnectionError("peer closed connection")
            self.buf += chunk
        data = self.buf[:n]
        self.buf = self.buf[n:]
        return data


class _BytesReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read_exact(self, n):
        d = self.data[self.pos:self.pos + n]
        self.pos += n
        return d


def read_packet(reader):
    length = read_varint(reader)
    if length < 0 or length > MAX_PACKET_LENGTH:
        raise ValueError("packet length out of bounds: %d" % length)
    payload = reader.read_exact(length)
    p = _BytesReader(payload)
    pkt_id = read_varint(p)
    rest = p.data[p.pos:]
    return pkt_id, rest


def read_string_from(data, pos):
    length, pos = read_varint_from(data, pos)
    if length < 0 or length > MAX_STRING_LENGTH or pos + length > len(data):
        raise ValueError("invalid string length: %d" % length)
    s = data[pos:pos + length].decode("utf-8")
    return s, pos + length


def read_double_from(data, pos):
    if pos + 8 > len(data):
        raise ValueError("truncated double")
    v = struct.unpack(">d", data[pos:pos + 8])[0]
    return v, pos + 8


def read_long_from(data, pos):
    if pos + 8 > len(data):
        raise ValueError("truncated long")
    v = struct.unpack(">q", data[pos:pos + 8])[0]
    return v, pos + 8


def read_varint_from(data, pos):
    num = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        b = data[pos]
        pos += 1
        num |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift > 35:
            raise ValueError("VarInt too big")
    if num & (1 << 31):
        num -= 1 << 32
    return num, pos


def make_packet(packet_id, payload=b""):
    body = write_varint(packet_id) + payload
    return write_varint(len(body)) + body


def send_packet(sock, packet_id, payload=b""):
    led_busy()
    sock.sendall(make_packet(packet_id, payload))


SECTION_BLOCK_BYTES = 4096 * 2
SECTION_LIGHT_BYTES = 2048
SECTION_SKY_BYTES = 2048


def _pack_block(block_id, metadata=0):
    v = ((block_id & 0xFFF) << 4) | (metadata & 0xF)
    return struct.pack("<H", v)


def _blank_section_blocks():
    return bytearray(SECTION_BLOCK_BYTES)


def build_grass_section_blocks():
    blocks = _blank_section_blocks()
    grass = _pack_block(GRASS_BLOCK_ID, 0)
    start = 15 * 256 * 2
    end = start + 256 * 2
    blocks[start:end] = grass * 256
    return bytes(blocks)


def build_barrier_section_blocks(full_height=False):
    blocks = _blank_section_blocks()
    barrier = _pack_block(BARRIER_BLOCK_ID, 0)
    y_range = range(16) if full_height else (0, 1)
    for y in y_range:
        for z in range(16):
            for x in range(16):
                if x == 0 or x == 15 or z == 0 or z == 15:
                    idx = y * 256 + z * 16 + x
                    off = idx * 2
                    blocks[off:off + 2] = barrier
    return bytes(blocks)


_FULL_LIT_BLOCK_LIGHT = bytes(SECTION_LIGHT_BYTES)
_FULL_LIT_SKY_LIGHT = b"\xff" * SECTION_SKY_BYTES

_CACHED_GRASS_BLOCKS = None
_CACHED_BARRIER_BLOCKS = None
_CACHED_BARRIER_FULL_BLOCKS = None
_CACHED_CHUNK_PAYLOAD = None
_CHUNK_READY = False


def _get_cached_section_blocks():
    global _CACHED_GRASS_BLOCKS, _CACHED_BARRIER_BLOCKS, _CACHED_BARRIER_FULL_BLOCKS
    if _CACHED_GRASS_BLOCKS is None:
        _CACHED_GRASS_BLOCKS = build_grass_section_blocks()
    if _CACHED_BARRIER_BLOCKS is None:
        _CACHED_BARRIER_BLOCKS = build_barrier_section_blocks(full_height=False)
    if _CACHED_BARRIER_FULL_BLOCKS is None:
        _CACHED_BARRIER_FULL_BLOCKS = build_barrier_section_blocks(full_height=True)
    return _CACHED_GRASS_BLOCKS, _CACHED_BARRIER_BLOCKS, _CACHED_BARRIER_FULL_BLOCKS


GRASS_SECTION_INDEX = GROUND_Y // 16
BARRIER_WALL_SECTIONS_ABOVE = 2
BARRIER_SECTION_INDEX = GRASS_SECTION_INDEX + 1
BARRIER_TOP_SECTION_INDEX = GRASS_SECTION_INDEX + BARRIER_WALL_SECTIONS_ABOVE


def build_chunk_data_payload(cx, cz):
    global _CACHED_CHUNK_PAYLOAD
    if cx == 0 and cz == 0 and _CACHED_CHUNK_PAYLOAD is not None:
        return _CACHED_CHUNK_PAYLOAD

    grass_blocks, barrier_blocks, barrier_full_blocks = _get_cached_section_blocks()

    section_block_list = [grass_blocks]
    section_mask = 1 << GRASS_SECTION_INDEX

    for i in range(BARRIER_SECTION_INDEX, BARRIER_TOP_SECTION_INDEX + 1):
        if i < 0 or i > 15:
            continue
        section_mask |= (1 << i)
        section_block_list.append(barrier_full_blocks if i < BARRIER_TOP_SECTION_INDEX else barrier_blocks)

    num_sections = len(section_block_list)

    all_blocks = b"".join(section_block_list)
    all_block_light = _FULL_LIT_BLOCK_LIGHT * num_sections
    all_sky_light = _FULL_LIT_SKY_LIGHT * num_sections
    biomes = b"\x01" * 256

    section_data = all_blocks + all_block_light + all_sky_light + biomes

    expected_len = (SECTION_BLOCK_BYTES + SECTION_LIGHT_BYTES + SECTION_SKY_BYTES) * num_sections + 256
    if len(section_data) != expected_len:
        raise ValueError(
            "chunk payload size mismatch: got %d, expected %d" % (len(section_data), expected_len)
        )

    payload = bytearray()
    payload += write_int(cx)
    payload += write_int(cz)
    payload += write_bool(True)
    payload += write_ushort(section_mask)
    payload += write_varint(len(section_data))
    payload += section_data

    result = bytes(payload)
    if cx == 0 and cz == 0:
        _CACHED_CHUNK_PAYLOAD = result
    return result


def block_id_at(x, y, z):
    if y == GROUND_Y:
        return GRASS_BLOCK_ID, 0

    barrier_top_y = (BARRIER_TOP_SECTION_INDEX + 1) * 16 - 1
    if GROUND_Y < y <= barrier_top_y:
        if x == 0 or x == 15 or z == 0 or z == 15:
            return BARRIER_BLOCK_ID, 0

    return 0, 0


def restore_world_block(sock, x, y, z):
    block_id, metadata = block_id_at(x, y, z)
    send_block_change(sock, x, y, z, block_id, metadata)
    if block_id == BARRIER_BLOCK_ID:
        log("Restored barrier block at", (x, y, z), "- barriers cannot be broken")


def verify_world_ready():
    global _CHUNK_READY
    if _CHUNK_READY:
        return True
    try:
        payload = build_chunk_data_payload(0, 0)
    except Exception as e:
        log("World verification failed while building chunk:", e)
        return False

    if len(payload) < 9:
        log("World verification failed: chunk payload too short")
        return False

    try:
        mask = struct.unpack(">H", payload[9:11])[0]
    except Exception as e:
        log("World verification failed while reading mask:", e)
        return False

    if mask & (1 << GRASS_SECTION_INDEX) == 0:
        log("World verification failed: ground section missing from mask")
        return False

    if len(payload) > MAX_PACKET_LENGTH:
        log("World verification failed: chunk payload too large (%d bytes)" % len(payload))
        return False

    _CHUNK_READY = True
    log("World verified OK: chunk payload %d bytes, section mask %s" % (len(payload), bin(mask)))
    return True


def log(*args):
    if DEBUG:
        print(*args)


def handle_status(sock, reader):
    led_busy()
    pkt_id, payload = read_packet(reader)
    if pkt_id == ST_STATUS_REQUEST:
        body = (
            '{"version":{"name":"' + GAME_VERSION_STRING + '","protocol":' +
            str(PROTOCOL_VERSION) + '},"players":{"max":' + str(MAX_PLAYERS) +
            ',"online":0},"description":{"text":"' + json_escape(MOTD) + '"}}'
        )
        send_packet(sock, ST_STATUS_RESPONSE, write_string(body))

    pkt_id, payload = read_packet(reader)
    if pkt_id == ST_PING_REQUEST:
        send_packet(sock, ST_PING_RESPONSE, payload)


def _offline_uuid(username):
    name_bytes = ("OfflinePlayer:" + username).encode("utf-8")
    try:
        import hashlib
        h = hashlib.md5(name_bytes).digest()
    except ImportError:
        h = bytearray(16)
        acc = 0x811C9DC5
        for byte in name_bytes:
            acc = ((acc ^ byte) * 0x01000193) & 0xFFFFFFFF
            h[acc % 16] ^= byte
        h = bytes(h)

    b = bytearray(h)
    b[6] = (b[6] & 0x0F) | 0x30
    b[8] = (b[8] & 0x3F) | 0x80

    hx = b.hex()
    return "%s-%s-%s-%s-%s" % (hx[0:8], hx[8:12], hx[12:16], hx[16:20], hx[20:32])


def _valid_username(username):
    if not username or len(username) > 16:
        return False
    for ch in username:
        o = ord(ch)
        is_digit = 0x30 <= o <= 0x39
        is_upper = 0x41 <= o <= 0x5A
        is_lower = 0x61 <= o <= 0x7A
        is_underscore = ch == "_"
        if not (is_digit or is_upper or is_lower or is_underscore):
            return False
    return True


def handle_login(sock, reader):
    led_busy()
    pkt_id, payload = read_packet(reader)
    if pkt_id != LG_LOGIN_START:
        raise ValueError("expected Login Start, got 0x%02x" % pkt_id)

    username, _pos = read_string_from(payload, 0)
    if not _valid_username(username):
        raise ValueError("rejected invalid username: %r" % username)
    log("Login Start from", username)

    uuid_str = _offline_uuid(username)
    resp = write_string(uuid_str) + write_string(username)
    send_packet(sock, LG_LOGIN_SUCCESS, resp)

    return username, uuid_str


PLAYER_ENTITY_ID = 1


def send_join_game(sock):
    payload = bytearray()
    payload += write_int(PLAYER_ENTITY_ID)
    payload += write_ubyte(1)
    payload += write_byte(0)
    payload += write_ubyte(0)
    payload += write_ubyte(MAX_PLAYERS)
    payload += write_string("flat")
    payload += write_bool(False)
    send_packet(sock, PL_JOIN_GAME_CB, bytes(payload))


def send_server_difficulty(sock):
    send_packet(sock, PL_SERVER_DIFFICULTY, write_ubyte(0))


def decode_position_long(packed):
    x = (packed >> 38) & 0x3FFFFFF
    z = (packed >> 12) & 0x3FFFFFF
    y = packed & 0xFFF
    if x >= (1 << 25):
        x -= (1 << 26)
    if z >= (1 << 25):
        z -= (1 << 26)
    if y >= (1 << 11):
        y -= (1 << 12)
    return x, y, z


def encode_position_long(x, y, z):
    return ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)


def send_block_change(sock, x, y, z, block_id, metadata=0):
    packed = encode_position_long(x, y, z)
    block_state = ((block_id & 0xFFF) << 4) | (metadata & 0xF)
    payload = write_long(packed) + write_varint(block_state)
    send_packet(sock, PL_BLOCK_CHANGE_CB, payload)


def send_spawn_position(sock):
    x = int(SPAWN_X)
    y = int(SPAWN_Y)
    z = int(SPAWN_Z)
    packed = ((x & 0x3FFFFFF) << 38) | ((y & 0xFFF) << 26) | (z & 0x3FFFFFF)
    send_packet(sock, PL_SPAWN_POSITION_CB, write_long(packed))


ABILITY_FLAG_INVULNERABLE = 0x01
ABILITY_FLAG_FLYING = 0x02
ABILITY_FLAG_ALLOW_FLYING = 0x04
ABILITY_FLAG_CREATIVE = 0x08


def send_player_abilities(sock):
    flags = (ABILITY_FLAG_INVULNERABLE |
             ABILITY_FLAG_ALLOW_FLYING | ABILITY_FLAG_CREATIVE)
    payload = bytearray()
    payload += write_byte(flags)
    payload += write_float(0.05)
    payload += write_float(0.1)
    send_packet(sock, PL_PLAYER_ABILITIES, bytes(payload))


def send_held_item_change(sock):
    send_packet(sock, PL_HELD_ITEM_CB, write_byte(0))


def send_time_update(sock):
    payload = write_long(0) + write_long(6000)
    send_packet(sock, PL_TIME_UPDATE_CB, payload)


def send_player_position_and_look(sock, x=SPAWN_X, y=SPAWN_Y, z=SPAWN_Z, yaw=0.0, pitch=0.0):
    payload = bytearray()
    payload += write_double(x)
    payload += write_double(y)
    payload += write_double(z)
    payload += write_float(yaw)
    payload += write_float(pitch)
    payload += write_byte(0)
    send_packet(sock, PL_PLAYER_POS_LOOK, bytes(payload))


def send_spawn_chunk(sock):
    payload = build_chunk_data_payload(0, 0)
    send_packet(sock, PL_CHUNK_DATA_CB, payload)
    log("Sent 1 chunk (0,0) with grass layer + barrier walls")


def send_keepalive(sock, tick):
    send_packet(sock, PL_KEEPALIVE_CB, write_varint(tick))


def send_chat(sock, message):
    body = '{"text":"' + json_escape(message) + '","color":"yellow"}'
    send_packet(sock, PL_CHAT_CB, write_string(body) + write_byte(0))


_cached_adc = None


def read_cpu_temperature():
    global _cached_adc
    if machine is None:
        return None
    try:
        if _cached_adc is None:
            _cached_adc = machine.ADC(4)
        raw = _cached_adc.read_u16()
        voltage = raw * 3.3 / 65535
        return 27.0 - (voltage - 0.706) / 0.001721
    except Exception:
        return None


def handle_play(sock, reader, username):
    led_busy(2.0)

    if not verify_world_ready():
        log("Refusing to spawn", username, "- world/chunk failed verification")
        try:
            send_chat(sock, "[Server] World not ready, please reconnect shortly")
        except Exception:
            pass
        return

    try:
        send_join_game(sock)
        send_server_difficulty(sock)
        send_spawn_position(sock)
        send_player_abilities(sock)
        send_held_item_change(sock)
        send_time_update(sock)
        send_spawn_chunk(sock)
        send_player_position_and_look(sock)
    except Exception as e:
        log("Error during initial play setup:", e)
        return

    log(username, "reached Play state; spawned on a single grass layer")

    try:
        sock.settimeout(SOCKET_TIMEOUT)
    except Exception:
        pass

    last_keepalive = time.time()
    last_temp_send = time.time()
    last_gc = time.time()
    last_resync = time.time()
    tick = 0

    while True:
        watchdog_feed()

        pkt_id = None
        payload = b""
        try:
            pkt_id, payload = read_packet(reader)
        except ConnectionError:
            log("Client closed connection")
            return
        except OSError:
            pkt_id = None
        except Exception as e:
            log("Malformed packet from", username, "-", e, "- dropping connection")
            return

        now = time.time()

        if now - last_keepalive >= KEEPALIVE_INTERVAL:
            tick += 1
            try:
                send_keepalive(sock, tick)
                last_keepalive = now
            except Exception as e:
                log("Keepalive send failed:", e)
                return

        if now - last_temp_send >= TEMP_INTERVAL:
            temp = read_cpu_temperature()
            if temp is not None:
                try:
                    send_chat(sock, "[Server] CPU temperature: %.1f C" % temp)
                except Exception as e:
                    log("Chat send failed:", e)
                    return
            last_temp_send = now

        if now - last_resync >= RESYNC_INTERVAL:
            try:
                send_spawn_position(sock)
            except Exception as e:
                log("Resync send failed:", e)
                return
            last_resync = now

        if now - last_gc >= GC_INTERVAL:
            gc.collect()
            last_gc = now

        if pkt_id is None:
            continue

        led_busy()

        if pkt_id == PL_KEEPALIVE_SB:
            continue
        elif pkt_id in (PL_PLAYER_POS_SB, PL_PLAYER_POS_LOOK_SB,
                        PL_PLAYER_LOOK_SB, PL_PLAYER_SB):
            continue
        elif pkt_id == PL_PLAYER_DIGGING_SB:
            try:
                pos = 0
                status = payload[pos]
                pos += 1
                packed = struct.unpack(">q", payload[pos:pos + 8])[0]
                pos += 8
                bx, by, bz = decode_position_long(packed)
            except Exception as e:
                log("Malformed digging packet from", username, "-", e)
                continue

            if status in (DIG_STATUS_STARTED, DIG_STATUS_FINISHED):
                try:
                    restore_world_block(sock, bx, by, bz)
                except Exception as e:
                    log("Failed to restore block at", (bx, by, bz), "-", e)
                    return
            continue
        elif pkt_id == PL_CHAT_SB:
            continue
        else:
            continue


def handle_client(conn, addr):
    led_busy(2.0)
    log("Connection from", addr)
    try:
        conn.settimeout(HANDSHAKE_TIMEOUT)
    except Exception:
        pass
    reader = SockReader(conn)
    try:
        pkt_id, payload = read_packet(reader)
        if pkt_id != HS_HANDSHAKE:
            log("Unexpected first packet 0x%02x, dropping" % pkt_id)
            return

        pos = 0
        _proto_ver, pos = read_varint_from(payload, pos)
        _server_addr, pos = read_string_from(payload, pos)
        pos += 2
        next_state, pos = read_varint_from(payload, pos)

        if next_state == 1:
            try:
                conn.settimeout(HANDSHAKE_TIMEOUT)
            except Exception:
                pass
            handle_status(conn, reader)
            return
        elif next_state == 2:
            try:
                conn.settimeout(HANDSHAKE_TIMEOUT)
            except Exception:
                pass
            username, _uuid = handle_login(conn, reader)
            handle_play(conn, reader, username)
        else:
            return

    except ConnectionError:
        log("Client disconnected", addr)
    except OSError as e:
        log("Socket timeout/error with", addr, "-", e)
    except Exception as e:
        log("Error handling", addr, "-", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        gc.collect()


def _is_valid_ip(ip):
    if not ip:
        return False
    if ip == "0.0.0.0":
        return False
    if ip == "255.255.255.255":
        return False
    return True


def _wifi_teardown(wlan):
    try:
        wlan.disconnect()
    except Exception:
        pass
    try:
        wlan.active(False)
    except Exception:
        pass


def connect_wifi(ssid=WIFI_SSID, password=WIFI_PASSWORD, timeout=WIFI_TIMEOUT):
    if network is None:
        print("ERROR: No network module available on this board/port.")
        return None

    if not ssid or ssid == "YOUR_WIFI_SSID":
        print("ERROR: WIFI_SSID is not configured. Edit the top of the script.")
        return None

    try:
        wlan = network.WLAN(network.STA_IF)
    except Exception as e:
        print("ERROR: Could not initialise WLAN:", e)
        return None

    try:
        try:
            wlan.active(False)
            time.sleep(0.5)
        except Exception:
            pass
        wlan.active(True)

        if not wlan.isconnected():
            print("Connecting to WiFi:", ssid)
            try:
                wlan.connect(ssid, password)
            except Exception as e:
                print("ERROR: wlan.connect failed:", e)
                _wifi_teardown(wlan)
                return None

            t0 = time.time()
            while not wlan.isconnected():
                if time.time() - t0 > timeout:
                    print()
                    print("ERROR: WiFi association timed out after %ds." % timeout)
                    print("       Check SSID/password and that 2.4 GHz is enabled.")
                    _wifi_teardown(wlan)
                    return None
                time.sleep(0.5)
                print(".", end="")
            print()

        t0 = time.time()
        ip = wlan.ifconfig()[0]
        while not _is_valid_ip(ip) and time.time() - t0 < 10:
            time.sleep(0.5)
            ip = wlan.ifconfig()[0]

        if not _is_valid_ip(ip):
            print("ERROR: WiFi reports connected but no IP was assigned (got %r)." % ip)
            _wifi_teardown(wlan)
            return None

    except Exception as e:
        print("ERROR: Unexpected WiFi error:", e)
        _wifi_teardown(wlan)
        return None

    print("WiFi up. IP:", ip)
    return ip


def _reconnect_wifi_if_needed(ssid, password):
    if network is None:
        return True
    try:
        wlan = network.WLAN(network.STA_IF)
        if wlan.isconnected():
            return True
    except Exception:
        return True
    log("WiFi link lost, attempting reconnect...")
    ip = connect_wifi(ssid, password)
    return ip is not None


def run(host="0.0.0.0", port=25565, ssid=None, password=None):
    led_init()
    watchdog_init()
    led_busy(WIFI_TIMEOUT)

    print("Verifying world data before startup...")
    if not verify_world_ready():
        print("FATAL: world/chunk data failed verification, refusing to start server.")
        led_busy(0.0)
        time.sleep(0.2)
        if _led_pin is not None:
            try:
                _led_pin.value(0)
            except Exception:
                pass
        return
    print("World verified OK.")

    active_ssid = ssid if ssid is not None else WIFI_SSID
    active_password = password if password is not None else WIFI_PASSWORD

    ip = connect_wifi(active_ssid, active_password)

    if ip is None:
        print("WiFi not available -- not starting the Minecraft server.")
        led_busy(0.0)
        time.sleep(0.2)
        if _led_pin is not None:
            try:
                _led_pin.value(0)
            except Exception:
                pass
        return

    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(4)
        s.settimeout(ACCEPT_TIMEOUT)
    except Exception as e:
        print("ERROR: Could not bind to %s:%d -" % (host, port), e)
        led_busy(0.0)
        time.sleep(0.2)
        if _led_pin is not None:
            try:
                _led_pin.value(0)
            except Exception:
                pass
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        return

    led_busy(0.0)
    time.sleep(0.2)

    print("Tiny MC server listening on %s:%d" % (ip, port))
    print("In Minecraft 1.8.9, add: %s:%d" % (ip, port))
    print("Protocol", PROTOCOL_VERSION, "(1.8.9) -- offline mode only")

    last_wifi_check = time.time()

    try:
        while True:
            watchdog_feed()

            now = time.time()
            if now - last_wifi_check >= 5.0:
                if not _reconnect_wifi_if_needed(active_ssid, active_password):
                    time.sleep(1)
                    continue
                last_wifi_check = now

            try:
                conn, addr = s.accept()
            except OSError:
                continue
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log("accept() failed:", e)
                time.sleep(1)
                continue

            try:
                handle_client(conn, addr)
            except KeyboardInterrupt:
                try:
                    conn.close()
                except Exception:
                    pass
                raise
            except Exception as e:
                log("Unhandled error in handle_client:", e)
            gc.collect()
    finally:
        shutdown(s)


def shutdown(server_socket=None):
    print("Shutting down...")

    if server_socket is not None:
        try:
            server_socket.close()
        except Exception:
            pass

    if _led_timer is not None:
        try:
            _led_timer.deinit()
        except Exception:
            pass

    if _led_pin is not None:
        try:
            _led_pin.value(0)
        except Exception:
            pass

    if network is not None:
        try:
            wlan = network.WLAN(network.STA_IF)
            wlan.disconnect()
            wlan.active(False)
        except Exception:
            pass

    print("Shutdown complete.")


def main():
    while True:
        try:
            run()
        except KeyboardInterrupt:
            print("KeyboardInterrupt received, shutting down safely...")
            shutdown()
            return
        except Exception as e:
            print("FATAL top-level error, restarting in 5s:", e)
            time.sleep(5)
            continue
        print("Server loop exited, restarting in 5s...")
        time.sleep(5)


if __name__ == "__main__":
    main()
