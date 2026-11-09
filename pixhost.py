import aiohttp
import json
from pathlib import Path


class PixhostClient:
    """Pixhost API v2 client. NSFW content_type=1 is intentional for adult previews."""

    def __init__(self, api_base="https://api.pixhost.cc", content_type=1, thumb_size=800):
        self.api_base = api_base.rstrip("/")
        self.content_type = str(content_type)
        self.thumb_size = str(thumb_size)

    async def upload(self, image_path: str):
        path = Path(image_path)
        mime = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
            ".gif": "image/gif", ".avif": "image/avif",
        }.get(path.suffix.lower(), "application/octet-stream")

        timeout = aiohttp.ClientTimeout(total=90, sock_connect=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            form = aiohttp.FormData()
            with path.open("rb") as image:
                form.add_field("img", image, filename=path.name, content_type=mime)
                form.add_field("content_type", self.content_type)
                form.add_field("max_th_size", self.thumb_size)
                form.add_field("optimize_for_web", "1")
                async with session.post(
                    f"{self.api_base}/images",
                    data=form,
                    headers={"Accept": "application/json"},
                ) as response:
                    body = await response.text()
                    if response.status >= 400:
                        raise RuntimeError(
                            f"Pixhost upload HTTP {response.status}: {body[:500]}"
                        )
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(f"Pixhost returned invalid JSON: {body[:300]}") from exc

        show_url = data.get("show_url")
        thumb_url = data.get("th_url")
        if not show_url or not thumb_url:
            raise RuntimeError(f"Pixhost response missing show_url/th_url: {data}")
        return show_url, thumb_url
