# Setup and deployment

## 1. Telegram setup
1. Create a bot with @BotFather and copy its token.
2. Add the bot as an administrator to the **storage channel** so Bot API channel-post updates are delivered.
3. Add the same bot as an administrator to the **destination channel** with permission to post messages.
4. The Telegram account used for Telethon must be a member of the storage channel.
5. Get the numeric channel IDs and your own numeric Telegram user ID. Channel IDs commonly look like -1001234567890.

## 2. Generate a Telethon StringSession
1. Create Telegram API credentials at https://my.telegram.org.
2. On a trusted local device, clone this repository and install dependencies:
   `pip install -r requirements.txt`
3. Set TELEGRAM_API_ID and TELEGRAM_API_HASH in a local, untracked .env file.
4. Run `python generate_session.py`, complete Telegram login and any 2-step verification.
5. Copy the printed StringSession directly into Koyeb's secret environment variable TELETHON_SESSION.
6. Never share this session string. It gives access to your Telegram account.

## 3. PeeStream setup
1. Copy the API key from PeeStream Settings.
2. Set PEESTREAM_API_KEY as a Koyeb secret.
3. The app requests GET /api/upload/server?key=... and POSTs multipart fields file, fld_id, and new_title to the returned upload server.
4. It then requests /api/file/info?key=...&file_code=... and looks for a returned public link.
5. The app deliberately does not invent a watch URL. If PeeStream's response does not contain a public link, check the API response and set PEESTREAM_LINK_TEMPLATE only after verifying the correct official link format. The template must contain {filecode}.

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

## 5. Deploy to Koyeb
1. Create a Web Service from this GitHub repository.
2. Select Dockerfile build.
3. Set the environment variables above as secrets where appropriate.
4. Expose the service on the configured PORT (default 8000) and use /health as the health-check path.
5. Keep in mind that a Koyeb Free Instance may scale to zero and has no persistent volume. It is not a reliable always-on Telegram listener. The SQLite database is local to the instance and can be lost on replacement/redeploy. Use an always-on instance plus persistent storage or an external database for production.
6. After deployment, open the service URL /health. A JSON status response should appear.

## 6. Bot commands
Send these commands to your bot from the ADMIN_USER_ID account:
- /start
- /status
- /pause
- /resume
- /retry

## 7. First test
1. Start with one small video you own or are authorized to distribute.
2. Confirm it appears in the storage channel.
3. Check bot /status and Koyeb logs.
4. Confirm the file appears in PeeStream and that a real public watch link is published to the destination channel.
5. Only after this succeeds, test larger files and a larger daily queue.

## Notes
- The Bot API monitor is a second detector only. Standard Bot API getFile downloads are limited to 20 MB; large media is downloaded through Telethon.
- Both Bot API and Telethon may detect the same channel post. The database unique key (chat_id, message_id) deduplicates them.
- This first version processes one video at a time and stores job state in SQLite.
- The Koyeb Free tier is for testing only; uninterrupted 20+ videos/day processing needs an always-on worker and durable storage.
- Upload only content you own or have permission to distribute. Respect Telegram and PeeStream terms and applicable law.
