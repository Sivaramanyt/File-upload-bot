import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int
    api_hash: str
    telethon_session: str
    storage_channel_id: int
    target_channel_id: int
    admin_user_id: int
    peestream_api_key: str
    peestream_api_base: str
    peestream_folder_id: str
    peestream_link_template: str
    database_path: str
    max_video_mb: int
    port: int
    worker_poll_seconds: int
    max_upload_attempts: int

    @classmethod
    def load(cls):
        return cls(
            bot_token=_required("TELEGRAM_BOT_TOKEN"),
            api_id=int(_required("TELEGRAM_API_ID")),
            api_hash=_required("TELEGRAM_API_HASH"),
            telethon_session=_required("TELETHON_SESSION"),
            storage_channel_id=int(_required("STORAGE_CHANNEL_ID")),
            target_channel_id=int(_required("TARGET_CHANNEL_ID")),
            admin_user_id=int(_required("ADMIN_USER_ID")),
            peestream_api_key=_required("PEESTREAM_API_KEY"),
            peestream_api_base=os.getenv("PEESTREAM_API_BASE", "https://peestream.com").rstrip("/"),
            peestream_folder_id=os.getenv("PEESTREAM_FOLDER_ID", "0"),
            peestream_link_template=os.getenv("PEESTREAM_LINK_TEMPLATE", "").strip(),
            database_path=os.getenv("DATABASE_PATH", "./data/jobs.sqlite3"),
            max_video_mb=int(os.getenv("MAX_VIDEO_MB", "500")),
            port=int(os.getenv("PORT", "8000")),
            worker_poll_seconds=int(os.getenv("WORKER_POLL_SECONDS", "5")),
            max_upload_attempts=int(os.getenv("MAX_UPLOAD_ATTEMPTS", "5")),
        )
