"""Versioned, checksummed optical frames; no game process access is used."""
from __future__ import annotations

import hashlib
import json
import re
import struct
import time
import unicodedata
import zlib

COLS, ROWS, CELL, PAD = 64, 32, 3, 8
WIDTH, HEIGHT, MARKER = COLS * CELL + PAD * 2, ROWS * CELL + PAD * 2, 6
PAYLOAD, MAX_PARTS = 232, 128
HEADER = struct.Struct(">4sIIBBBBI")
MAGIC = b"WCA1"
STAGES = {"detail", "progress", "complete"}
DEMO_TEXT = "Ты хорошо себя проявил и показал достойным моего благословения. Просто подойди к алтарю и прими мой дар."


def frame_packet(message: bytes, session: int, sequence: int, part: int) -> bytes:
    count = max(1, (len(message) + PAYLOAD - 1) // PAYLOAD)
    if count > MAX_PARTS or not 0 <= part < count:
        raise ValueError("Message is too long or part is invalid")
    fragment = message[part * PAYLOAD:(part + 1) * PAYLOAD]
    body = HEADER.pack(MAGIC, session, sequence, part, count, len(fragment), 0,
                       zlib.adler32(message)) + fragment.ljust(PAYLOAD, b"\0")
    return body + struct.pack(">I", zlib.adler32(body))


def parse_frame(packet: bytes) -> tuple:
    if len(packet) != 256 or zlib.adler32(packet[:252]) != struct.unpack(">I", packet[252:])[0]:
        raise ValueError("Invalid frame checksum")
    magic, session, sequence, part, count, size, reserved, checksum = HEADER.unpack(packet[:20])
    if magic != MAGIC or reserved or not 1 <= count <= MAX_PARTS or part >= count or size > PAYLOAD:
        raise ValueError("Invalid frame header")
    if any(packet[20 + size:252]):
        raise ValueError("Nonzero padding")
    return session, sequence, part, count, checksum, packet[20:20 + size]


class Assembler:
    def __init__(self):
        self.identity = None
        self.parts = {}
        self.count = self.checksum = None
        self.started = 0.0
        self.completed = None

    def feed(self, packet: bytes, now: float | None = None) -> dict | None:
        session, sequence, part, count, checksum, data = parse_frame(packet)
        now = time.monotonic() if now is None else now
        identity = session, sequence
        if self.identity and session == self.identity[0] and sequence != self.identity[1]:
            if (sequence - self.identity[1]) % 2**32 >= 2**31:
                return None  # Ignore delayed pages from the previous request.
        if identity != self.identity or now - self.started > 40:
            self.identity, self.parts = identity, {}
            self.count, self.checksum, self.started = count, checksum, now
        if self.completed == identity:
            return None
        if count != self.count or checksum != self.checksum:
            raise ValueError("Inconsistent message pages")
        self.parts[part] = data
        if len(self.parts) != count:
            return None
        message = b"".join(self.parts[i] for i in range(count))
        if zlib.adler32(message) != checksum:
            self.parts.clear()
            raise ValueError("Invalid message checksum")
        value = json.loads(message.decode("utf-8"))
        if not isinstance(value, dict) or value.get("v") != 1:
            raise ValueError("Unsupported message")
        self.completed = identity
        return value


def canonical_request(message: dict) -> dict:
    if message.get("action") == "demo":
        message = {"quest_id": 50913, "stage": "demo", "locale": "ruRU", "gender": "male",
                   "npc_id": 128152, "speaker": "Акунда", "text": DEMO_TEXT}
    quest = message.get("quest_id")
    if type(quest) is not int or not 0 < quest < 2**31:
        raise ValueError("Некорректный ID квеста")
    stage = message.get("stage")
    if stage not in STAGES | {"demo"}:
        raise ValueError("Неизвестный этап квеста")
    locale = message.get("locale", "ruRU")
    if not isinstance(locale, str) or not re.fullmatch(r"[a-z]{2}[A-Z]{2}", locale):
        raise ValueError("Некорректный язык")
    gender = message.get("gender", "unknown")
    if gender not in {"male", "female", "unknown"}:
        raise ValueError("Некорректный пол персонажа")
    text = message.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 24000:
        raise ValueError("Текст квеста пуст или слишком длинный")
    name = message.get("player_name")
    if isinstance(name, str) and name:
        escaped = re.escape(name)
        text = re.sub(r",\s*" + escaped + r"(?=[.!?])", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<!\w)" + escaped + r"(?!\w)", "путница" if gender == "female" else "путник", text, flags=re.IGNORECASE)
    text = " ".join(unicodedata.normalize("NFC", text).split())
    npc = message.get("npc_id", 0) or 0
    if type(npc) is not int or not 0 <= npc < 2**31:
        raise ValueError("Некорректный ID NPC")
    result = {"quest_id": quest, "stage": stage, "locale": locale, "gender": gender,
              "npc_id": npc, "text": text}
    for key in ("speaker", "npc_race", "npc_type", "npc_gender"):
        value = message.get(key, "")
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError("Некорректные сведения о NPC")
        result[key] = value.strip()
    return result  # Deliberately excludes screenshots and the player's name.


def request_key(request: dict, revision: str = "") -> str:
    value = {**request, "voice_revision": revision}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def render_frame(packet: bytes):
    """Reference renderer used for real Lua -> image -> decoder integration tests."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(image)
    for x, y, color in [(0, 0, (255, 0, 255)), (WIDTH - MARKER, 0, (0, 255, 255)),
                        (0, HEIGHT - MARKER, (255, 255, 0))]:
        draw.rectangle((x, y, x + MARKER - 1, y + MARKER - 1), fill=color)
    for bit in range(2048):
        if packet[bit // 8] & (128 >> (bit % 8)):
            x, y = PAD + bit % COLS * CELL, PAD + bit // COLS * CELL
            draw.rectangle((x, y, x + CELL - 1, y + CELL - 1), fill="white")
    return image


def find_geometry(image) -> tuple[float, float, float, float]:
    """Locate the three colored corner markers inside the user's selection."""
    image = image.convert("RGB")
    groups = [set(), set(), set()]
    for index, (r, g, b) in enumerate(image.getdata()):
        category = 0 if r > 190 and b > 190 and g < 95 else 1 if g > 190 and b > 190 and r < 95 else 2 if r > 190 and g > 190 and b < 95 else None
        if category is not None:
            groups[category].add((index % image.width, index // image.width))
    centers = []
    for points in groups:
        if len(points) > 50000:
            raise ValueError("Выделите только небольшой индикатор аддона")
        found = []
        while points:
            stack = [points.pop()]
            component = []
            while stack:
                x, y = stack.pop()
                component.append((x, y))
                for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if point in points:
                        points.remove(point)
                        stack.append(point)
            if len(component) >= 4:
                xs, ys = zip(*component)
                if abs((max(xs) - min(xs)) - (max(ys) - min(ys))) <= 3:
                    found.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
        centers.append(found[:30])
    for tl in centers[0]:
        for tr in centers[1]:
            for bl in centers[2]:
                sx, sy = (tr[0] - tl[0]) / (WIDTH - MARKER), (bl[1] - tl[1]) / (HEIGHT - MARKER)
                if 0.45 < sx < 4 and 0.45 < sy < 4 and abs(sx - sy) < 0.08 and abs(tl[1] - tr[1]) < 3 and abs(tl[0] - bl[0]) < 3:
                    return tl[0] - (MARKER - 1) / 2 * sx, tl[1] - (MARKER - 1) / 2 * sy, sx, sy
    raise ValueError("Индикатор не найден. Включите /wca companion и выделите его вместе с тремя цветными углами.")


def read_frame(image, geometry: tuple) -> bytes:
    x0, y0, sx, sy = geometry
    image = image.convert("RGB")
    packet = bytearray(256)
    for bit in range(2048):
        x = round(x0 + (PAD + (bit % COLS) * CELL + (CELL - 1) / 2) * sx)
        y = round(y0 + (PAD + (bit // COLS) * CELL + (CELL - 1) / 2) * sy)
        if not 0 <= x < image.width or not 0 <= y < image.height:
            raise ValueError("Индикатор вышел за границы выбранной области")
        r, g, b = image.getpixel((x, y))
        if (r + g + b) / 3 > 125:
            packet[bit // 8] |= 128 >> (bit % 8)
    parse_frame(bytes(packet))
    return bytes(packet)
