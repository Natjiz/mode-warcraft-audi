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
README = """Warcraft Audi Companion 0.4.0 — первая проверочная сборка Windows 10/11 x64

1. Полностью распакуйте этот архив в обычную папку на компьютере.
   Запускать Start.bat прямо внутри ZIP нельзя.
2. При закрытой игре скопируйте папку AddOns/WarcraftAudi в Interface/AddOns
   вашего клиента, заменив основной аддон WarcraftAudi.
3. Запустите Start.bat. Установка Python и права администратора не нужны.
   Откроется окно управления в браузере. Окно программы можно свернуть.
4. Нажмите «Проверить голос» — будет скачана утверждённая проба Акунды.
5. Войдите в WoW 10.2.7 и введите /wca companion.
6. В программе нажмите «Выделить индикатор на экране» и за 3 секунды
   переключитесь в WoW. Подождите 3 секунды, вернитесь в программу, обведите
   прямоугольник аддона с тремя цветными углами и сохраните область.
7. Нажмите «Запустить», вернитесь в игру и откройте квест.

Используйте оконный режим без рамки. Не закрывайте индикатор другими окнами.
После перемещения индикатора или смены разрешения выделите его снова.
Когда индикатор пропадает, воспроизведение прекращается примерно через 2 секунды.
Для возврата к обычному аддону повторите /wca companion.

Первоначально доступны опубликованные записи Акунды, а не все квесты.
Чтобы генерировать новые реплики, нужен отдельный работающий сервер генерации:
его HTTPS-адрес вводится в настройках. Публичный GPU-сервер в комплект не входит
и сейчас не развёрнут. Эта сборка проверена в Linux на уровне протокола,
интерфейса, HTTP и аудиодекодирования; запуск в Windows и связь с настоящим
WoW Circle необходимо проверить на вашем компьютере.

Аудиокэш: 300 МБ по умолчанию, автоматически очищается. Настройки и кэш лежат
в %LOCALAPPDATA%/WarcraftAudi. Изображение экрана обрабатывается локально.
Серверу передаются только текст задания и сведения о NPC. Ключ сервера хранится
только до закрытия программы. Для выхода нажмите «Завершить программу».

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


def build(vendor: Path, output: Path):
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
        (root / "Start.bat").write_bytes(b'@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\nset PYTHONUTF8=1\r\n"%~dp0runtime\\python.exe" -m companion\r\nif errorlevel 1 pause\r\n')
        (root / "README-RU.txt").write_text(README, encoding="utf-8-sig")
        (root / "VENDOR.json").write_text(json.dumps({"python": {"version": "3.12.10", "sha256": PYTHON_SHA,
            "source": "https://www.python.org/ftp/python/3.12.10/" + PYTHON_FILE,
            "signature_verified": "Sigstore: thomas@python.org via https://accounts.google.com"}, "wheels": verified}, indent=2), encoding="utf-8")
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
    parser.add_argument("--output", type=Path, default=ROOT / "dist/WarcraftAudi-Companion-0.4.0-Windows.zip")
    arguments = parser.parse_args()
    build(arguments.vendor, arguments.output)
