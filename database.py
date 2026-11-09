import os
import time
import aiosqlite

try:
    import asyncpg
except ImportError:
    asyncpg = None


class JobStore:
    def __init__(self, path: str, database_url: str = ""):
        self.path = path
        self.database_url = (database_url or "").strip()
        self.is_postgres = bool(self.database_url)
        self.pool = None

    async def init(self):
        if self.is_postgres:
            if asyncpg is None:
                raise RuntimeError("DATABASE_URL is set but asyncpg is not installed")
            dsn = self.database_url.replace("postgres://", "postgresql://", 1)
            self.pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5,
                command_timeout=60, server_settings={"application_name": "file-upload-bot"})
            async with self.pool.acquire() as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id BIGSERIAL PRIMARY KEY, chat_id BIGINT NOT NULL,
                        message_id BIGINT NOT NULL, filename TEXT NOT NULL,
                        file_size BIGINT NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0, peestream_filecode TEXT,
                        video_url TEXT, thumbnail_url TEXT, error TEXT,
                        created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
                        next_attempt_at BIGINT NOT NULL DEFAULT 0, content_sha256 TEXT,
                        duplicate_of BIGINT, UNIQUE(chat_id, message_id))
                """)
                await db.execute("CREATE TABLE IF NOT EXISTS scheduler_state (key TEXT PRIMARY KEY, value BIGINT NOT NULL)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status,next_attempt_at)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_content_sha256 ON jobs(content_sha256)")
                await db.execute("UPDATE jobs SET status='pending',updated_at=$1 WHERE status='processing'", int(time.time()))
            await self._import_sqlite_if_present()
            return

        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL, filename TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, peestream_filecode TEXT,
                    video_url TEXT, thumbnail_url TEXT, error TEXT, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL, next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(chat_id,message_id))
            """)
            cols = await (await db.execute("PRAGMA table_info(jobs)")).fetchall()
            existing = {row[1] for row in cols}
            for name, definition in (("thumbnail_url","TEXT"),("content_sha256","TEXT"),("duplicate_of","INTEGER")):
                if name not in existing:
                    await db.execute("ALTER TABLE jobs ADD COLUMN %s %s" % (name, definition))
            await db.execute("CREATE TABLE IF NOT EXISTS scheduler_state (key TEXT PRIMARY KEY,value INTEGER NOT NULL)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status,next_attempt_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_content_sha256 ON jobs(content_sha256)")
            await db.execute("UPDATE jobs SET status='pending',updated_at=? WHERE status='processing'", (int(time.time()),))
            await db.commit()

    async def _import_sqlite_if_present(self):
        if not self.path or not os.path.isfile(self.path):
            return
        async with aiosqlite.connect(self.path) as local:
            local.row_factory = aiosqlite.Row
            cols = await (await local.execute("PRAGMA table_info(jobs)")).fetchall()
            if not cols:
                return
            rows = await (await local.execute("SELECT * FROM jobs ORDER BY id")).fetchall()
            states = await (await local.execute("SELECT key,value FROM scheduler_state")).fetchall()
        async with self.pool.acquire() as db:
            async with db.transaction():
                for row in rows:
                    x = dict(row)
                    await db.execute("""
                        INSERT INTO jobs(id,chat_id,message_id,filename,file_size,status,attempts,
                            peestream_filecode,video_url,thumbnail_url,error,created_at,updated_at,
                            next_attempt_at,content_sha256,duplicate_of)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                        ON CONFLICT(chat_id,message_id) DO NOTHING
                    """, x["id"],x["chat_id"],x["message_id"],x["filename"],x.get("file_size",0),
                        "pending" if x.get("status")=="processing" else x.get("status","pending"),
                        x.get("attempts",0),x.get("peestream_filecode"),x.get("video_url"),
                        x.get("thumbnail_url"),x.get("error"),x.get("created_at",int(time.time())),
                        x.get("updated_at",int(time.time())),x.get("next_attempt_at",0),
                        x.get("content_sha256"),x.get("duplicate_of"))
                for row in states:
                    await db.execute("""INSERT INTO scheduler_state(key,value) VALUES($1,$2)
                        ON CONFLICT(key) DO NOTHING""", row["key"],row["value"])
                await db.execute("""SELECT setval(pg_get_serial_sequence('jobs','id'),
                    GREATEST(COALESCE((SELECT MAX(id) FROM jobs),1),1), EXISTS(SELECT 1 FROM jobs))""")

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def add(self, chat_id, message_id, filename, file_size):
        now = int(time.time())
        if self.is_postgres:
            async with self.pool.acquire() as db:
                result = await db.execute("""INSERT INTO jobs(chat_id,message_id,filename,file_size,status,created_at,updated_at)
                    VALUES($1,$2,$3,$4,'pending',$5,$5) ON CONFLICT(chat_id,message_id) DO NOTHING""",
                    chat_id,message_id,filename,file_size,now)
                return result.endswith("1")
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""INSERT OR IGNORE INTO jobs(chat_id,message_id,filename,file_size,status,created_at,updated_at)
                VALUES(?,?,?,?, 'pending',?,?)""",(chat_id,message_id,filename,file_size,now,now))
            await db.commit()
            return cur.rowcount == 1

    async def register_content_hash(self, job_id, digest):
        now = int(time.time())
        if self.is_postgres:
            async with self.pool.acquire() as db:
                async with db.transaction():
                    old = await db.fetchrow("""SELECT id FROM jobs WHERE content_sha256=$1 AND status='published'
                        AND id<>$2 ORDER BY id LIMIT 1""",digest,job_id)
                    if old:
                        await db.execute("""UPDATE jobs SET content_sha256=$1,duplicate_of=$2,status='duplicate',
                            error=NULL,updated_at=$3 WHERE id=$4""",digest,old["id"],now,job_id)
                        return int(old["id"])
                    await db.execute("UPDATE jobs SET content_sha256=$1,updated_at=$2 WHERE id=$3",digest,now,job_id)
                    return None
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""SELECT id FROM jobs WHERE content_sha256=? AND status='published'
                AND id<>? ORDER BY id LIMIT 1""",(digest,job_id))
            old = await cur.fetchone()
            if old:
                await db.execute("""UPDATE jobs SET content_sha256=?,duplicate_of=?,status='duplicate',
                    error=NULL,updated_at=? WHERE id=?""",(digest,old[0],now,job_id))
                await db.commit()
                return int(old[0])
            await db.execute("UPDATE jobs SET content_sha256=?,updated_at=? WHERE id=?",(digest,now,job_id))
            await db.commit()
            return None

    async def get_setting(self, key, default=0):
        if self.is_postgres:
            async with self.pool.acquire() as db:
                value = await db.fetchval("SELECT value FROM scheduler_state WHERE key=$1",key)
                return int(value) if value is not None else int(default)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT value FROM scheduler_state WHERE key=?",(key,))
            row = await cur.fetchone()
            return int(row[0]) if row else int(default)

    async def set_setting(self, key, value):
        if self.is_postgres:
            async with self.pool.acquire() as db:
                await db.execute("""INSERT INTO scheduler_state(key,value) VALUES($1,$2)
                    ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""",key,int(value))
            return
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""INSERT INTO scheduler_state(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",(key,int(value)))
            await db.commit()

    async def get_next_publish_at(self):
        return await self.get_setting("next_publish_at",0)

    async def set_next_publish_at(self, timestamp):
        await self.set_setting("next_publish_at",timestamp)

    async def next_job(self):
        now = int(time.time())
        if self.is_postgres:
            async with self.pool.acquire() as db:
                async with db.transaction():
                    due = await db.fetchval("SELECT value FROM scheduler_state WHERE key='next_publish_at'")
                    if due and int(due)>now:
                        return None
                    row = await db.fetchrow("""SELECT * FROM jobs WHERE status='pending' AND next_attempt_at<=$1
                        ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""",now)
                    if row:
                        await db.execute("UPDATE jobs SET status='processing',updated_at=$1 WHERE id=$2",now,row["id"])
                        return dict(row)
                    return None
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT value FROM scheduler_state WHERE key='next_publish_at'")
            due = await cur.fetchone()
            if due and int(due[0])>now:
                return None
            db.row_factory = aiosqlite.Row
            cur = await db.execute("""SELECT * FROM jobs WHERE status='pending' AND next_attempt_at<=?
                ORDER BY id LIMIT 1""",(now,))
            row = await cur.fetchone()
            if row:
                await db.execute("UPDATE jobs SET status='processing',updated_at=? WHERE id=?",(now,row["id"]))
                await db.commit()
                return dict(row)
            return None

    async def update(self, job_id, **fields):
        allowed = {"status","attempts","peestream_filecode","video_url","thumbnail_url","error","next_attempt_at"}
        values = {k:v for k,v in fields.items() if k in allowed}
        values["updated_at"] = int(time.time())
        if self.is_postgres:
            keys = list(values)
            assignments = ", ".join("%s=$%s" % (key,i+1) for i,key in enumerate(keys))
            async with self.pool.acquire() as db:
                await db.execute("UPDATE jobs SET %s WHERE id=$%s" % (assignments,len(keys)+1),
                    *[values[k] for k in keys],job_id)
            return
        assignments = ", ".join("%s=?" % k for k in values)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE jobs SET %s WHERE id=?" % assignments,(*values.values(),job_id))
            await db.commit()

    async def counts(self):
        if self.is_postgres:
            async with self.pool.acquire() as db:
                rows = await db.fetch("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status")
                return {r["status"]:r["count"] for r in rows}
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status")
            return {r[0]:r[1] for r in await cur.fetchall()}

    async def list_queue(self, limit=10):
        query = """SELECT id,filename,status,attempts,next_attempt_at FROM jobs
            WHERE status IN ('pending','processing') ORDER BY id LIMIT """
        if self.is_postgres:
            async with self.pool.acquire() as db:
                rows = await db.fetch(query+"$1",limit)
                return [dict(r) for r in rows]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query+"?",(limit,))
            return [dict(r) for r in await cur.fetchall()]

    async def list_history(self, limit=10):
        query = """SELECT id,filename,status,updated_at,duplicate_of,error FROM jobs
            WHERE status IN ('published','failed','duplicate','skipped') ORDER BY updated_at DESC LIMIT """
        if self.is_postgres:
            async with self.pool.acquire() as db:
                rows = await db.fetch(query+"$1",limit)
                return [dict(r) for r in rows]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query+"?",(limit,))
            return [dict(r) for r in await cur.fetchall()]

    async def skip_next_pending(self):
        now = int(time.time())
        if self.is_postgres:
            async with self.pool.acquire() as db:
                async with db.transaction():
                    row = await db.fetchrow("""SELECT id FROM jobs WHERE status='pending'
                        ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""")
                    if not row:
                        return None
                    await db.execute("UPDATE jobs SET status='skipped',error='Skipped by admin',updated_at=$1 WHERE id=$2",
                        now,row["id"])
                    return int(row["id"])
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT id FROM jobs WHERE status='pending' ORDER BY id LIMIT 1")
            row = await cur.fetchone()
            if not row:
                return None
            await db.execute("UPDATE jobs SET status='skipped',error='Skipped by admin',updated_at=? WHERE id=?",
                (now,row[0]))
            await db.commit()
            return int(row[0])

    async def retry_failed(self):
        now = int(time.time())
        query = """UPDATE jobs SET status='pending',attempts=0,error=NULL,next_attempt_at=0,updated_at=
            %s WHERE status='failed'"""
        if self.is_postgres:
            async with self.pool.acquire() as db:
                result = await db.execute(query % "$1",now)
                return int(result.rsplit(" ",1)[-1])
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(query % "?",(now,))
            await db.commit()
            return cur.rowcount
