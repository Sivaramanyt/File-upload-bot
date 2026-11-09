# Telegram → PeeStream Auto Publisher

Automatically monitors a Telegram storage channel, uploads eligible videos to PeeStream, and publishes the returned watch link to a destination Telegram channel.

## Current scope
- Telegram Bot API channel-post detection (bot must be an administrator in the storage channel).
- Telethon user-session monitoring and media download (the authorized user account must have access to the storage channel).
- PeeStream API upload-server discovery and multipart upload.
- Publishes only when PeeStream returns a usable public link. It never guesses a watch URL.
- SQLite job deduplication, retry handling, and admin commands.
- HTTP health endpoint for Koyeb.

## Important deployment notes
- Koyeb Free is suitable only for a prototype. It can scale to zero and has no persistent volume; continuous monitoring and SQLite/session persistence are not guaranteed.
- Use an always-on service and persistent storage (or move the job store to a managed database) for reliable production.
- Only upload and distribute videos you own or are authorized to share, and follow PeeStream and Telegram rules.
- Never commit API keys, bot tokens, Telethon session strings, or .env.

See DEPLOYMENT.md for setup.
