from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

MAX_AUDIO = 16 * 1024**2


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Сервер попытался перенаправить авторизованный запрос")


def fetch(url: str, *, token: str = "", payload: dict | None = None, limit: int = MAX_AUDIO, timeout: int = 30) -> tuple[bytes, str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Некорректный адрес сервера")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Для внешнего сервера требуется HTTPS")
    headers = {"User-Agent": "WarcraftAudi/0.4.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers)
    opener = urllib.request.build_opener(NoRedirect()) if token else urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as response:
        if int(response.headers.get("Content-Length", 0)) > limit:
            raise ValueError("Ответ сервера превышает допустимый размер")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Ответ сервера превышает допустимый размер")
        return data, response.headers.get_content_type()


def fetch_json(url, **kwargs):
    data, _ = fetch(url, limit=256 * 1024, **kwargs)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Некорректный ответ сервера")
    return value
