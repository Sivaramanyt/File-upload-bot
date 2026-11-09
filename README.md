# Telegram → PeeStream Auto Publisher

Automatically monitors a Telegram storage channel, uploads eligible videos to PeeStream, and publishes the returned watch link to a destination Telegram channel.

## Current scope
- Telegram Bot API channel-post detection (bot must be an administrator in the storage channel).
- Telethon user-session monitoring and media download (the authorized user account must have access to the storage channel).
- PeeStream API upload-server discovery and multipart upload.
- Publishes only when PeeStream returns a usable public link. It never guesses a watch URL.
- SQLite job deduplication by Telegram message and SHA-256 video content, retry handling, queue management, and upload analytics.
- HTTP health endpoint for Koyeb.

## Admin commands
- `/status` — publisher state, interval, and job counts.
- `/queue` — first 10 pending or processing jobs.
- `/history` — last 10 published, failed, duplicate, or skipped jobs.
- `/stats` — total and status-wise upload counts.
- `/skip` — skip the oldest pending job (does not interrupt a processing upload).
- `/setinterval 15` — persist a publishing interval in minutes (1–1440).
- `/pause`, `/resume`, `/retry`, `/help`.

## Duplicate protection
The existing Telegram message ID constraint still prevents Bot API and Telethon from queueing the same channel post twice. Before uploading a new job to PeeStream, the worker now calculates its SHA-256 fingerprint and compares it with previously published jobs. An exact byte-for-byte duplicate is marked `duplicate` and skipped. This check requires downloading the Telegram file first, but prevents a second PeeStream upload. Existing historical jobs without fingerprints are not retroactively scanned.

## Important deployment notes
- Koyeb Free is suitable only for a prototype. It can scale to zero and has no persistent volume; continuous monitoring and SQLite/session persistence are not guaranteed.
- Use an always-on service and persistent storage (or move the job store to a managed database) for reliable production.
- Only upload and distribute videos you own or are authorized to share, and follow PeeStream and Telegram rules.
- Never commit API keys, bot tokens, Telethon session strings, or .env.

See DEPLOYMENT.md for setup.
