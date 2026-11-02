# Сборка Windows 0.5.0

В комплект входят официальный CPython 3.12.10 embed x64, пять Windows wheels,
Python-код, Lua-аддон и собственный маленький Win32 launcher. Его исходник —
`tools/windows_launcher.c`; он использует только KERNEL32/USER32 и не требует
отдельного C runtime. Launcher не подписан; подпись CPython не является
подписью всего приложения.

Подготовьте vendor как в предыдущей версии: скачайте Python embed и .sigstore
с https://www.python.org/ftp/python/3.12.10/, Windows wheels через `pip download
--platform win_amd64 --python-version 3.12 --implementation cp --abi cp312
--only-binary=:all: -r companion/requirements.txt`. Нужен `sigstore==4.0.0`.

Также нужен Zig 0.14.1: скачайте с https://ziglang.org/download/ и проверьте SHA-256
по официальному https://ziglang.org/download/index.json. В облачной машине:

```sh
XDG_CACHE_HOME=/workspace/.wca-packaging-cache \
XDG_DATA_HOME=/workspace/.wca-packaging-data \
ZIG_GLOBAL_CACHE_DIR=/workspace/.wca-toolchain/cache \
/workspace/.wca-packaging-venv/bin/python tools/build_companion_windows.py \
  --zig /workspace/.wca-toolchain/zig-x86_64-linux-0.14.1/zig \
  --output downloads/WarcraftAudi-0.5.0-Windows.zip
```

Сборщик проверяет подписанный SHA CPython и Sigstore identity `thomas@python.org`
с issuer `https://accounts.google.com`, затем сверяет wheels с SHA-256 PyPI.
TLS, подписи и контрольные суммы не отключаются. Все vendor-лицензии сохраняются.
`VENDOR.json` включает хеши launcher и его исходника.

Результат — переносимый ZIP: `WarcraftAudi.exe`, диагностический `Start.bat`,
`runtime`, `companion`, `AddOns/WarcraftAudi`, инструкция. Озвучки скачиваются
при необходимости, моделей в комплекте нет. Настоящее окно и захват Windows
проверяются отдельным workflow, работа с настоящим WoW требует проверки в игре.
