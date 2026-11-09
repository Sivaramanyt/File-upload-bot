import asyncio
import hashlib
import html
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message as BotMessage
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import Settings
from database import JobStore
from peestream import PeeStreamClient
from pixhost import PixhostClient

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("file-upload-bot")


def media_details(message):
    video = getattr(message, "video", None)
    document = getattr(message, "document", None)
    media_file = getattr(message, "file", None)

    if video:
        name = (
            getattr(video, "file_name", None)
            or getattr(media_file, "name", None)
            or "video.mp4"
        )
        size = (
            getattr(video, "file_size", None)
            or getattr(video, "size", None)
            or getattr(media_file, "size", None)
            or 0
        )
        return name, int(size)

    if document:
        mime = (
            getattr(document, "mime_type", None)
            or getattr(media_file, "mime_type", None)
            or ""
        ).lower()
        if mime.startswith("video/"):
            name = (
                getattr(document, "file_name", None)
                or getattr(media_file, "name", None)
                or "video"
            )
            size = (
                getattr(document, "file_size", None)
                or getattr(document, "size", None)
                or getattr(media_file, "size", None)
                or 0
            )
            return name, int(size)
    return None


async def main():
    cfg = Settings.load()
    store = JobStore(cfg.database_path)
    await store.init()

    bot = Bot(token=cfg.bot_token)
    dp = Dispatcher()
    telethon = TelegramClient(
        StringSession(cfg.telethon_session), cfg.api_id, cfg.api_hash
    )
    peestream = PeeStreamClient(
        cfg.peestream_api_base,
        cfg.peestream_api_key,
        cfg.peestream_folder_id,
        cfg.peestream_link_template,
    )
    pixhost = PixhostClient(
        api_base=os.getenv("PIXHOST_API_BASE", "https://api.pixhost.cc"),
        content_type=1,
        thumb_size=int(os.getenv("PIXHOST_THUMB_SIZE", "800")),
    )
    saved_interval = await store.get_setting(
        "publish_interval_minutes", cfg.publish_interval_minutes
    )
    state = {
        "paused": False,
        "started_at": int(time.time()),
        "publish_interval_minutes": max(1, saved_interval),
    }

    async def enqueue(chat_id, message_id, filename, file_size):
        if chat_id != cfg.storage_channel_id:
            return
        if file_size > cfg.max_video_mb * 1024 * 1024:
            log.warning("Skipping %s: exceeds MAX_VIDEO_MB", filename)
            await notify_admin(
                f"Skipped <b>{html.escape(filename)}</b>: "
                f"file is larger than {cfg.max_video_mb} MB."
            )
            return
        added = await store.add(chat_id, message_id, filename, file_size)
        if added:
            log.info("Queued Telegram message %s from %s", message_id, chat_id)

    async def notify_admin(text):
        try:
            await bot.send_message(cfg.admin_user_id, text, parse_mode="HTML")
        except Exception:
            log.exception("Could not send admin notification")

    @dp.channel_post()
    async def on_channel_post(message: BotMessage):
        details = media_details(message)
        if details:
            await enqueue(message.chat.id, message.message_id, details[0], details[1])

    def admin_only(message: BotMessage) -> bool:
        return bool(message.from_user and message.from_user.id == cfg.admin_user_id)

    @dp.message(Command("start"))
    async def cmd_start(message: BotMessage):
        if not admin_only(message):
            return
        await message.answer(
            "File Upload Bot is running. Use /help to see admin commands."
        )

    @dp.message(Command("status"))
    async def cmd_status(message: BotMessage):
        if not admin_only(message):
            return
        counts = await store.counts()
        lines = [f"{html.escape(k)}: {v}" for k, v in sorted(counts.items())]
        next_slot = await store.get_next_publish_at()
        wait_seconds = max(0, next_slot - int(time.time()))
        wait_text = (
            f"{(wait_seconds + 59) // 60} minute(s)"
            if wait_seconds else "Ready for next video"
        )
        await message.answer(
            "<b>Publisher status</b>\n"
            f"Paused: {state['paused']}\n"
            f"Publishing interval: {state['publish_interval_minutes']} minutes\n"
            f"Next publish slot: {html.escape(wait_text)}\n"
            + ("\n".join(lines) if lines else "No jobs yet."),
            parse_mode="HTML",
        )

    @dp.message(Command("queue"))
    async def cmd_queue(message: BotMessage):
        if not admin_only(message):
            return
        jobs = await store.list_queue(10)
        if not jobs:
            await message.answer("📭 Queue is empty.")
            return
        lines = ["<b>Upcoming queue (first 10)</b>"]
        for job in jobs:
            name = html.escape(job["filename"][:70])
            status = "⏳ Processing" if job["status"] == "processing" else "🕒 Pending"
            lines.append(f"#{job['id']} · {status}\n{name}")
        await message.answer("\n\n".join(lines), parse_mode="HTML")

    @dp.message(Command("history"))
    async def cmd_history(message: BotMessage):
        if not admin_only(message):
            return
        jobs = await store.list_history(10)
        if not jobs:
            await message.answer("No publishing history yet.")
            return
        lines = ["<b>Recent history (last 10)</b>"]
        labels = {
            "published": "✅ Published",
            "failed": "❌ Failed",
            "duplicate": "♻️ Duplicate skipped",
            "skipped": "⏭ Skipped by admin",
        }
        for job in jobs:
            name = html.escape(job["filename"][:65])
            line = f"#{job['id']} · {labels.get(job['status'], job['status'])}\n{name}"
            if job["status"] == "duplicate" and job.get("duplicate_of"):
                line += f"\nDuplicate of job #{job['duplicate_of']}"
            lines.append(line)
        await message.answer("\n\n".join(lines), parse_mode="HTML")

    @dp.message(Command("skip"))
    async def cmd_skip(message: BotMessage):
        if not admin_only(message):
            return
        job_id = await store.skip_next_pending()
        if job_id is None:
            await message.answer("No pending job to skip. Processing jobs are not affected.")
        else:
            await message.answer(
                f"⏭ Skipped pending job #{job_id}. The currently processing job was not changed."
            )

    @dp.message(Command("setinterval"))
    async def cmd_setinterval(message: BotMessage):
        if not admin_only(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer("Usage: /setinterval 30\nAllowed range: 1–1440 minutes.")
            return
        try:
            minutes = int(parts[1].strip())
        except ValueError:
            await message.answer("Please enter a whole number. Example: /setinterval 30")
            return
        if not 1 <= minutes <= 1440:
            await message.answer("Interval must be between 1 and 1440 minutes.")
            return
        await store.set_setting("publish_interval_minutes", minutes)
        state["publish_interval_minutes"] = minutes
        await message.answer(
            f"✅ Publishing interval saved: {minutes} minute(s). "
            "It applies after the next successful publication."
        )

    @dp.message(Command("stats"))
    async def cmd_stats(message: BotMessage):
        if not admin_only(message):
            return
        counts = await store.counts()
        total = sum(counts.values())
        await message.answer(
            "<b>Upload analytics</b>\n"
            f"Total jobs: {total}\n"
            f"Published: {counts.get('published', 0)}\n"
            f"Pending: {counts.get('pending', 0)}\n"
            f"Processing: {counts.get('processing', 0)}\n"
            f"Failed: {counts.get('failed', 0)}\n"
            f"Duplicates skipped: {counts.get('duplicate', 0)}\n"
            f"Skipped by admin: {counts.get('skipped', 0)}",
            parse_mode="HTML",
        )

    @dp.message(Command("pause"))
    async def cmd_pause(message: BotMessage):
        if admin_only(message):
            state["paused"] = True
            await message.answer("⏸ Upload queue paused.")

    @dp.message(Command("resume"))
    async def cmd_resume(message: BotMessage):
        if admin_only(message):
            state["paused"] = False
            await message.answer("▶️ Upload queue resumed.")

    @dp.message(Command("retry"))
    async def cmd_retry(message: BotMessage):
        if not admin_only(message):
            return
        count = await store.retry_failed()
        await message.answer(f"Re-queued {count} failed job(s).")

    @dp.message(Command("help"))
    async def cmd_help(message: BotMessage):
        if admin_only(message):
            await message.answer(
                "<b>Admin commands</b>\n"
                "/status - publisher and queue status\n"
                "/queue - first 10 pending/processing jobs\n"
                "/history - last 10 completed jobs\n"
                "/stats - upload analytics\n"
                "/skip - skip the oldest pending job\n"
                "/setinterval 30 - change publish interval\n"
                "/pause - pause uploads\n"
                "/resume - resume uploads\n"
                "/retry - retry failed jobs",
                parse_mode="HTML",
            )

    @telethon.on(events.NewMessage(chats=cfg.storage_channel_id))
    async def on_telethon_message(event):
        msg = event.message
        details = media_details(msg)
        if details:
            await enqueue(cfg.storage_channel_id, msg.id, details[0], details[1])

    async def process_job(job):
        job_id = job["id"]
        title = Path(job["filename"]).stem[:180] or "Video"

        video_url = job.get("video_url")
        filecode = job.get("peestream_filecode")
        thumbnail_url = job.get("thumbnail_url")

        if not video_url or not thumbnail_url:
            msg = await telethon.get_messages(
                cfg.storage_channel_id, ids=job["message_id"]
            )
            if not msg:
                raise RuntimeError("Original Telegram message is no longer available")

            suffix = Path(job["filename"]).suffix or ".video"
            fd, temp_path = tempfile.mkstemp(prefix="upload-", suffix=suffix)
            os.close(fd)
            thumb_path = temp_path + ".jpg"
            try:
                downloaded = await telethon.download_media(msg, file=temp_path)
                if not downloaded or not os.path.exists(temp_path):
                    raise RuntimeError("Telethon could not download the Telegram video")
                actual_size = os.path.getsize(temp_path)
                if actual_size > cfg.max_video_mb * 1024 * 1024:
                    raise RuntimeError("Downloaded video exceeds MAX_VIDEO_MB")

                if not video_url:
                    digest = hashlib.sha256()
                    with open(temp_path, "rb") as video_file:
                        for chunk in iter(lambda: video_file.read(1024 * 1024), b""):
                            digest.update(chunk)
                    duplicate_of = await store.register_content_hash(
                        job_id, digest.hexdigest()
                    )
                    if duplicate_of:
                        await notify_admin(
                            f"♻️ Duplicate skipped: <b>{html.escape(title)}</b> "
                            f"(job #{job_id}); already published as job #{duplicate_of}."
                        )
                        log.info(
                            "Skipped duplicate job %s; original published job is %s",
                            job_id, duplicate_of
                        )
                        return

                    filecode, video_url = await peestream.upload(temp_path, title)
                    await store.update(
                        job_id, peestream_filecode=filecode,
                        video_url=video_url, error=None,
                    )

                if not thumbnail_url:
                    proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-ss", "00:00:03", "-i", temp_path,
                        "-frames:v", "1", "-vf", "scale=w='min(1280,iw)':h=-2",
                        "-q:v", "2", "-y", thumb_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate()
                    if proc.returncode != 0 or not os.path.exists(thumb_path):
                        proc = await asyncio.create_subprocess_exec(
                            "ffmpeg", "-hide_banner", "-loglevel", "error",
                            "-i", temp_path, "-frames:v", "1",
                            "-vf", "scale=w='min(1280,iw)':h=-2", "-q:v", "2", "-y", thumb_path,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        _, stderr = await proc.communicate()
                    if proc.returncode != 0 or not os.path.exists(thumb_path):
                        raise RuntimeError(
                            "FFmpeg thumbnail extraction failed: "
                            + (stderr.decode(errors="replace")[-500:])
                        )
                    show_url, thumbnail_url = await pixhost.upload(thumb_path)
                    await store.update(
                        job_id, thumbnail_url=thumbnail_url, error=None
                    )
                    log.info("Pixhost preview uploaded: %s", show_url)
            finally:
                for path in (temp_path, thumb_path):
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass

        safe_title = html.escape(title)
        post = f"<b>{safe_title}</b>"
        watch_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="▶️ WATCH VIDEO", url=video_url)
            ]]
        )
        if thumbnail_url:
            await bot.send_photo(
                cfg.target_channel_id,
                photo=thumbnail_url,
                caption=post,
                parse_mode="HTML",
                reply_markup=watch_keyboard,
            )
        else:
            await bot.send_message(
                cfg.target_channel_id,
                post,
                parse_mode="HTML",
                reply_markup=watch_keyboard,
                disable_web_page_preview=False,
            )
        await store.update(job_id, status="published", error=None)
        next_slot = int(time.time()) + state["publish_interval_minutes"] * 60
        await store.set_next_publish_at(next_slot)
        log.info(
            "Next queued video can publish in %s minute(s)",
            state["publish_interval_minutes"],
        )
        await notify_admin(
            f"✅ Published: <b>{safe_title}</b>\n"
            f"<a href=\"{safe_url}\">Open video link</a>"
        )
        log.info("Published job %s (%s)", job_id, filecode)

    async def worker():
        while True:
            try:
                if state["paused"]:
                    await asyncio.sleep(cfg.worker_poll_seconds)
                    continue
                job = await store.next_job()
                if not job:
                    await asyncio.sleep(cfg.worker_poll_seconds)
                    continue
                try:
                    await process_job(job)
                except Exception as exc:
                    attempts = int(job["attempts"]) + 1
                    error = str(exc)[:1500]
                    if attempts >= cfg.max_upload_attempts:
                        await store.update(
                            job["id"], status="failed", attempts=attempts, error=error
                        )
                        await notify_admin(
                            f"❌ Job #{job['id']} failed after {attempts} attempts.\n"
                            f"<code>{html.escape(error[:900])}</code>\n"
                            "Use /retry after fixing the issue."
                        )
                    else:
                        delay = min(60 * (2 ** (attempts - 1)), 1800)
                        await store.update(
                            job["id"],
                            status="pending",
                            attempts=attempts,
                            error=error,
                            next_attempt_at=int(time.time()) + delay,
                        )
                        log.exception(
                            "Job %s failed; retrying in %s seconds",
                            job["id"], delay
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Worker loop error")
                await asyncio.sleep(10)

    async def health(request):
        return web.json_response({
            "status": "ok",
            "service": "telegram-peestream-publisher",
            "paused": state["paused"],
            "uptime_seconds": int(time.time()) - state["started_at"],
        })

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.port)
    await site.start()

    await telethon.connect()
    if not await telethon.is_user_authorized():
        raise RuntimeError(
            "TELETHON_SESSION is not authorized. Generate a StringSession locally "
            "with generate_session.py and set it as a Koyeb secret."
        )

    await bot.delete_webhook(drop_pending_updates=False)

    worker_task = asyncio.create_task(worker(), name="upload-worker")
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=["message", "channel_post"]),
        name="telegram-bot-polling",
    )
    log.info("Service started; monitoring storage channel %s", cfg.storage_channel_id)

    try:
        await telethon.run_until_disconnected()
    finally:
        worker_task.cancel()
        polling_task.cancel()
        await asyncio.gather(worker_task, polling_task, return_exceptions=True)
        await runner.cleanup()
        await telethon.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
