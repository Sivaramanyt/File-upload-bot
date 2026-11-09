# Setup and deployment

## 1. Telegram setup
1. Create a bot with @BotFather and copy its token.
2. Add the bot as an administrator to the **storage channel** so Bot API channel-post updates are delivered.
3. Add the same bot as an administrator to the **destination channel** with permission to post messages.
4. The Telegram account used for Telethon must be a member of the storage channel.
5. Get the numeric channel IDs and your own numeric Telegram user ID. Channel IDs commonly look like -1001234567890.

## 2. Generate a Telethon StringSession
1. On a trusted local device, clone this repository and install dependencies: `pip install -r requirements.txt`
2. Set TELEGRAM_API_ID and TELEGRAM_API_HASH in a local, untracked .env file.
3. Run `python generate_session.py`, complete Telegram login and any 2-step verification.
4. Copy the printed StringSession directly into Koyeb's secret environment variable TELETHON_SESSION.
5. Never share this session string. It gives access to your Telegram account.

## 3. PeeStream setup
1. Copy the API key from PeeStream Settings.
2. Set PEESTREAM_API_KEY as a Koyeb secret.
3. The app requests GET /api/upload/server?key=... and POSTs multipart fields file, fld_id, and new_title to the returned upload server.
4. It then requests /api/file/info?key=...&file_code=... and looks for a returned public link.
5. The app deliberately does not invent a watch URL. If PeeStream's response does not contain a public link, check the API response and set PEESTREAM_LINK_TEMPLATE only after verifying the correct official link format. The template must contain {filecode}.

## 3A. Adult thumbnail hosting with Pixhost
1. This project uses the official Pixhost API v2: `POST https://api.pixhost.cc/images`. API v2 documentation says no API key is required.
2. The app extracts one frame from each video with FFmpeg, then uploads it to Pixhost.
3. Adult previews are explicitly classified with `content_type=1` (NSFW). The API returns `show_url` and `th_url`; the bot sends the returned thumbnail URL as a Telegram photo with the PeeStream watch link in the caption.
4. Pixhost's documented maximum image size is 10 MB. The generated JPEG preview is resized to a maximum width of 960 px and the returned Pixhost thumbnail uses the configured maximum size (default 420).
5. Set optional `PIXHOST_API_BASE=https://api.pixhost.cc` and `PIXHOST_THUMB_SIZE=420`. No Pixhost API key is needed.
6. Pixhost's Terms allow lawful adult content only when every depicted person is an adult and consented to both creation and distribution. Do not upload minors, non-consensual intimate imagery, leaks, or content you lack rights to distribute. Public image URLs are not private.
7. Pixhost does not promise permanent availability. Review its terms and current upload limits before relying on it for a commercial workflow.

## 4. Required environment variables
Copy .env.example values into Koyeb environment variables. Required:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_API_ID
- TELEGRAM_API_HASH
- TELETHON_SESSION
- STORAGE_CHANNEL_ID
- TARGET_CHANNEL_ID
- ADMIN_USER_ID
- PEESTREAM_API_KEY

Optional:
- PEESTREAM_API_BASE (default https://peestream.com)
- PEESTREAM_FOLDER_ID (default 0)
- PEESTREAM_LINK_TEMPLATE (leave blank until verified)
- DATABASE_PATH (default ./data/jobs.sqlite3)
- MAX_VIDEO_MB (default 500)
- PORT (Koyeb normally injects this)
- WORKER_POLL_SECONDS (default 5)
- MAX_UPLOAD_ATTEMPTS (default 5)
- PUBLISH_INTERVAL_MINUTES (default 30; minimum 1; initial/default value)
- PIXHOST_API_BASE (default https://api.pixhost.cc)
- PIXHOST_THUMB_SIZE (default 420)

## 5. Deploy to Koyeb
1. Create a Web Service from this GitHub repository.
2. Select Dockerfile build.
3. Set the environment variables above as secrets where appropriate.
4. Expose the service on the configured PORT (default 8000) and use /health as the health-check path.
5. Keep in mind that a Koyeb Free Instance may scale to zero and has no persistent volume. It is not a reliable always-on Telegram listener. The SQLite database is local to the instance and can be lost on replacement/redeploy. Use an always-on instance plus persistent storage or an external database for production.
6. After deployment, open the service URL /health. A JSON status response should appear.

## 5A. Scheduled publishing queue
- Videos are detected and queued immediately, but only one worker processes them at a time.
- The first queued video can publish immediately. After each successful publication, the next video is held until the configured interval has elapsed.
- Set `PUBLISH_INTERVAL_MINUTES=30` for one publication every 30 minutes. The next slot is stored in SQLite so it survives ordinary process restarts when the database storage itself persists.
- The interval is measured from the previous successful publication, not from when a video was uploaded to the storage channel.
- The admin can change the interval with `/setinterval 30`; this value is saved in SQLite and overrides the environment default after first initialization.
- On Koyeb Free instances without persistent storage, local SQLite data and the saved schedule may be lost when the instance is replaced or scaled down. Use persistent storage or an external database if preserving the queue is essential.

## 6. Bot commands
Send these commands to your bot from the ADMIN_USER_ID account:
- `/start`, `/help`
- `/status`, `/queue`, `/history`, `/stats`
- `/pause`, `/resume`, `/skip`, `/retry`
- `/setinterval 30` (allowed range 1–1440 minutes)

Duplicate protection calculates SHA-256 after downloading a new queued video and before sending it to PeeStream. Exact byte-for-byte matches of previously published videos are skipped. The duplicate is still downloaded once for hashing. Historical published jobs that predate this feature have no saved fingerprints, so they are not automatically recognized as duplicates. Queue listing and history commands show up to 10 jobs each.

## 7. First test
1. Wait until the current bulk queue has finished before deploying a worker update; a deployment restarts the service.
2. After deployment, test with a small video you own or are authorized to distribute.
3. Confirm it appears in the storage channel.
4. Check bot `/status`, `/queue`, and Koyeb logs.
5. Confirm the file appears in PeeStream, a Pixhost NSFW thumbnail is created, and the destination channel receives the thumbnail with the PeeStream watch link in its caption.
6. Upload the exact same test file again and verify it is marked duplicate without a second PeeStream upload. Then test `/history`, `/stats`, `/skip` on a disposable pending job, and `/setinterval`.

## Notes
- The Bot API monitor is a second detector only. Standard Bot API getFile downloads are limited to 20 MB; large media is downloaded through Telethon.
- Both Bot API and Telethon may detect the same channel post. The database unique key (chat_id, message_id) deduplicates them.
- This first version processes one video at a time and stores job state in SQLite.
- The Koyeb Free tier is for testing only; uninterrupted processing needs an always-on worker and durable storage.
- Upload only content you own or have permission to distribute. For adult images, every depicted person must be an adult and must have consented to creation and distribution. Respect Pixhost, Telegram and PeeStream terms and applicable law.
