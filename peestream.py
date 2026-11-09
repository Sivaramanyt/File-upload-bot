import aiohttp
import json
import os
from urllib.parse import urlparse


def _walk_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key.lower(), item
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def _as_json_or_text(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"raw": text}


def _find_value(data, names):
    for key, value in _walk_values(data):
        if key in names and isinstance(value, str) and value.strip():
            return value.strip()
    return None


class PeeStreamClient:
    def __init__(self, base_url, api_key, folder_id, link_template=""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.folder_id = str(folder_id)
        self.link_template = link_template

    async def _get_json(self, session, endpoint, params):
        async with session.get(
            f"{self.base_url}{endpoint}", params=params,
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            body = await response.text()
            data = _as_json_or_text(body)
            if response.status >= 400:
                raise RuntimeError(f"PeeStream {endpoint} HTTP {response.status}: {body[:500]}")
            return data

    async def upload(self, file_path, title):
        # PeeStream's dashboard documentation specifies /api/upload/server?key=...
        async with aiohttp.ClientSession() as session:
            server_data = await self._get_json(
                session, "/api/upload/server", {"key": self.api_key}
            )
            upload_url = _find_value(
                server_data, {"url", "server", "upload_url", "uploadserver", "result"}
            )
            if not upload_url and isinstance(server_data, dict):
                raw = server_data.get("raw", "").strip()
                if raw.startswith("http"):
                    upload_url = raw
            if not upload_url or urlparse(upload_url).scheme not in {"http", "https"}:
                raise RuntimeError(
                    "PeeStream did not return a valid upload-server URL. "
                    f"Response: {str(server_data)[:500]}"
                )

            filename = os.path.basename(file_path)
            form = aiohttp.FormData()
            form.add_field("fld_id", self.folder_id)
            form.add_field("new_title", title)
            with open(file_path, "rb") as handle:
                form.add_field(
                    "file", handle, filename=filename,
                    content_type="application/octet-stream"
                )
                async with session.post(
                    upload_url, data=form,
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=60)
                ) as response:
                    body = await response.text()
                    upload_data = _as_json_or_text(body)
                    if response.status >= 400:
                        raise RuntimeError(
                            f"PeeStream upload HTTP {response.status}: {body[:700]}"
                        )

            filecode = _find_value(
                upload_data, {"filecode", "file_code", "filecode_id"}
            )
            if not filecode:
                raise RuntimeError(
                    "Upload response did not contain filecode. "
                    f"Response: {str(upload_data)[:700]}"
                )

            # Prefer a real public link returned by upload or file-info API.
            watch_url = _find_value(upload_data, {"link", "watch_url", "url"})
            if not watch_url:
                info_data = await self._get_json(
                    session, "/api/file/info",
                    {"key": self.api_key, "file_code": filecode}
                )
                watch_url = _find_value(
                    info_data, {"link", "watch_url", "url"}
                )

            if not watch_url and self.link_template:
                watch_url = self.link_template.format(filecode=filecode)

            if not watch_url or urlparse(watch_url).scheme not in {"http", "https"}:
                raise RuntimeError(
                    "Video uploaded, but PeeStream did not return a verified public "
                    "watch link. Filecode saved for manual link-template verification: "
                    + filecode
                )
            return filecode, watch_url
