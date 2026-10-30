# Сборка переносимого Windows-клиента

Сборка выполнялась в Linux. Для подготовки ZIP не нужна Windows-компиляция:
используются официальный embeddable CPython x64 и готовые Windows wheels.
Сам Windows-запуск этим не подтверждается, его проверяют на целевой машине.

Рабочая директория — корень репозитория. Пример подготовки:

```sh
python3 -m venv /workspace/.wca-packaging-venv
/workspace/.wca-packaging-venv/bin/python -m pip install sigstore==4.0.0
mkdir -p /workspace/.wca-windows-build/wheels
/workspace/.wca-packaging-venv/bin/python -m pip download \
  --dest /workspace/.wca-windows-build/wheels \
  --platform win_amd64 --python-version 3.12 --implementation cp --abi cp312 \
  --only-binary=:all: -r companion/requirements.txt
curl -fL https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip \
  -o /workspace/.wca-windows-build/python-3.12.10-embed-amd64.zip
curl -fL https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.sigstore \
  -o /workspace/.wca-windows-build/python-3.12.10-embed-amd64.zip.sigstore
/workspace/.wca-packaging-venv/bin/python tools/build_companion_windows.py
```

Сборщик проверяет SHA-256 архива и Sigstore-подпись `thomas@python.org`
с issuer `https://accounts.google.com`. Личность сверена с официальной
[страницей Python о Sigstore](https://www.python.org/downloads/metadata/sigstore/).
Хеш CPython указан в подписанном bundle и официальном SPDX:
`4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3`.
Windows wheels отдельно сверяются с SHA-256 PyPI. Проверка TLS, подписи и
контрольных сумм не отключается. Все лицензии из vendor-пакетов сохраняются.

Результат: `dist/WarcraftAudi-Companion-0.4.0-Windows.zip`.
В нём `Start.bat`, код программы, небольшой каталог и основной Lua-аддон.
Голосовые модели и звуковая библиотека не включаются. `VENDOR.json` описывает
версии и проверенные хеши. Реальный звук скачивается клиентом по необходимости.
