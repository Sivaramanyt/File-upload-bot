import os
import time
import aiosqlite


class JobStore:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    peestream_filecode TEXT,
                    video_url TEXT,\n                    thumbnail_url TEXT,
                    error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(chat_id, message_id)
                )
            """)
            columns = await (await db.execute("PRAGMA table_info(jobs)")).fetchall()
            if "thumbnail_url" not in {row[1] for row in columns}:
                await db.execute("ALTER TABLE jobs ADD COLUMN thumbnail_url TEXT")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_state (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, next_attempt_at)")
            # A process may have been interrupted during a transfer. Re-queue it safely.
            await db.execute(
                "UPDATE jobs SET status='pending', updated_at=? WHERE status='processing'",
                (int(time.time()),),
            )
            await db.commit()

    async def add(self, chat_id: int, message_id: int, filename: str, file_size: int):
        now = int(time.time())
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT OR IGNORE INTO jobs
                   (chat_id,message_id,filename,file_size,status,created_at,updated_at)
                   VALUES (?,?,?,?, 'pending',?,?)""",
                (chat_id, message_id, filename, file_size, now, now),
            )
            await db.commit()
            return cur.rowcount == 1

    async def get_next_publish_at(self):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT value FROM scheduler_state WHERE key='next_publish_at'"
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def set_next_publish_at(self, timestamp: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO scheduler_state(key, value)
                   VALUES ('next_publish_at', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (int(timestamp),),
            )
            await db.commit()

    async def next_job(self):
        now = int(time.time())
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT value FROM scheduler_state WHERE key='next_publish_at'"
            )
            schedule = await cur.fetchone()
            if schedule and int(schedule[0]) > now:
                return None
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM jobs
                   WHERE status='pending' AND next_attempt_at<=?
                   ORDER BY id ASC LIMIT 1""",
                (now,),
            )
            row = await cur.fetchone()
            if row:
                await db.execute(
                    "UPDATE jobs SET status='processing',updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
                await db.commit()
                return dict(row)
            return None

    async def update(self, job_id: int, **fields):
        allowed = {
            "status", "attempts", "peestream_filecode", "video_url", "thumbnail_url",
            "error", "next_attempt_at"
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        values["updated_at"] = int(time.time())
        sql = ", ".join(f"{key}=?" for key in values)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE jobs SET {sql} WHERE id=?",
                (*values.values(), job_id),
            )
            await db.commit()

    async def counts(self):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
            return {row[0]: row[1] for row in await cur.fetchall()}

    async def retry_failed(self):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """UPDATE jobs SET status='pending', attempts=0, error=NULL,
                   next_attempt_at=0, updated_at=? WHERE status='failed'""",
                (int(time.time()),),
            )
            await db.commit()
            return cur.rowcount
