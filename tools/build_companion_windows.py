"""Build a portable Windows client using signed CPython and verified PyPI wheels.

Run in an environment containing sigstore==4.0.0. No compilation or developer
certificate is invented; the bundle contains the official CPython executables.
"""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILE = "python-3.12.10-embed-amd64.zip"
PYTHON_SHA = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
README = """Warcraft Audi 0.5.0 — Windows 10/11 x64

Обновление / первая установка:
1. Полностью распакуйте архив в обычную папку на компьютере.
2. При закрытой игре скопируйте AddOns/WarcraftAudi в Interface/AddOns
   вашего WoW Circle 10.2.7, заменив основной аддон.
3. Откройте WarcraftAudi.exe, запустите игру и откройте квест у NPC.

Браузер не нужен. Подключение начинается автоматически. Выделять области,
нажимать «Запустить» или вводить /wca companion не требуется. Окно программы
можно свернуть. Закрытие окна завершает программу и останавливает озвучку.
Программа может быть запущена раньше или позже игры.

Используйте оконный режим без рамки. Небольшой экранный сигнал появляется
автоматически при диалоге квеста и исчезает после его закрытия. Не закрывайте
его другими окнами: это канал передачи текста из обычного WoW-аддона.
После перемещения игры, смены монитора или масштаба программа ищет его сама.

Без сервера доступны опубликованные записи Акунды и «Природного лекарства»
от Киро. Новые неизвестные квесты требуют работающего сервера генерации;
публичный сервер пока не развёрнут. Настройки сервера, громкости и кэша
открываются кнопкой «Настройки», для готовых записей менять их не нужно.
Голоса Акунды и Киро утверждены пользователем и закреплены за этими NPC.

Start.bat оставлен для диагностики. Установка Python и права администратора
не требуются. WarcraftAudi.exe — небольшой launcher без цифровой подписи;
официальный CPython и Windows wheels проверены при сборке. Это не подпись
Microsoft на нашем приложении.

Кэш: 300 МБ, автоматически очищается. Настройки, журнал ошибок app.log и кэш:
%LOCALAPPDATA%/WarcraftAudi. Изображения обрабатываются локально и не сохраняются.
Сервер получает только текст и сведения о NPC. Ключ сервера хранится в памяти.

Проверки протокола, Lua, очереди и декодирования не заменяют проверку
настоящего WoW Circle и физического звука на компьютере игрока.
Исходники: https://github.com/Natjiz/mode-warcraft-audi
"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(archive: Path, target: Path):
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            relative = PurePosixPath(info.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in info.filename:
                raise ValueError("Unsafe vendor archive path")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Vendor archive contains a symlink")
        source.extractall(target)


def build(vendor: Path, output: Path, zig: Path):
    python_zip = vendor / PYTHON_FILE
    if sha(python_zip) != PYTHON_SHA:
        raise ValueError("Official CPython checksum does not match its signed bundle")
    subprocess.run([sys.executable, "-m", "sigstore", "verify", "identity", "--bundle", str(python_zip) + ".sigstore",
                    "--cert-identity", "thomas@python.org", "--cert-oidc-issuer", "https://accounts.google.com", str(python_zip)], check=True)
    wheels = list((vendor / "wheels").glob("*.whl"))
    if len(wheels) != 5:
        raise ValueError("Expected the five Windows client wheels and their dependencies")
    verified = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            metadata = Parser().parsestr(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))).decode())
        name, version = metadata["Name"], metadata["Version"]
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=20) as response:
            details = json.load(response)
        published = next(item for item in details["urls"] if item["filename"] == wheel.name)
        actual = sha(wheel)
        if actual != published["digests"]["sha256"]:
            raise ValueError(f"Wheel checksum mismatch: {wheel.name}")
        verified.append({"name": name, "version": version, "file": wheel.name, "sha256": actual})
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        root = Path(directory) / "WarcraftAudiCompanion"
        runtime = root / "runtime"
        runtime.mkdir(parents=True)
        extract(python_zip, runtime)
        (runtime / "python312._pth").write_text("python312.zip\n.\n..\nLib/site-packages\nimport site\n", encoding="utf-8")
        for wheel in wheels:
            extract(wheel, runtime / "Lib/site-packages")
        for source in (ROOT / "companion").rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts:
                target = root / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        addon = root / "AddOns/WarcraftAudi"
        addon.mkdir(parents=True)
        for source in (ROOT / "addon/WarcraftAudi").iterdir():
            if source.suffix in {".lua", ".toc"}:
                shutil.copyfile(source, addon / source.name)
        subprocess.run([str(zig), 'cc', '-target', 'x86_64-windows-gnu', '-nostdlib', '-ffreestanding',
                        '-isystem', str(zig.resolve().parent / 'lib/libc/include/any-windows-any'),
                        '-Wl,--entry,start', '-lkernel32', '-luser32',
                        '-Wl,--subsystem,windows', '-O2', '-s', str(ROOT / 'tools/windows_launcher.c'),
                        '-o', str(root / 'WarcraftAudi.exe')], check=True)
        (root / "Start.bat").write_bytes(b'@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\nset PYTHONUTF8=1\r\n"%~dp0runtime\\python.exe" -m companion\r\nif errorlevel 1 pause\r\n')
        (root / "README-RU.txt").write_text(README, encoding="utf-8-sig")
        (root / "VENDOR.json").write_text(json.dumps({"python": {"version": "3.12.10", "sha256": PYTHON_SHA,
            "source": "https://www.python.org/ftp/python/3.12.10/" + PYTHON_FILE,
            "signature_verified": "Sigstore: thomas@python.org via https://accounts.google.com"}, "wheels": verified, "launcher": {"source_sha256": sha(ROOT / "tools/windows_launcher.c"), "sha256": sha(root / "WarcraftAudi.exe"), "compiler": "Zig cc targeting x86_64-windows-gnu", "signed": False}}, indent=2), encoding="utf-8")
        archive_path = Path(directory) / "bundle.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(root.rglob("*")):
                if source.is_file():
                    archive.write(source, str(source.relative_to(root.parent)))
        archive_path.replace(output)
    print(f"Portable Windows client built: {output}; {output.stat().st_size / 1024**2:.1f} MiB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", type=Path, default=Path("/workspace/.wca-windows-build"))
    parser.add_argument("--output", type=Path, default=ROOT / "dist/WarcraftAudi-0.5.0-Windows.zip")
    parser.add_argument("--zig", type=Path, required=True)
    arguments = parser.parse_args()
    build(arguments.vendor, arguments.output, arguments.zig)
