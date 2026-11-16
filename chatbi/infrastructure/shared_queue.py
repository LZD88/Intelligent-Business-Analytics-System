"""Transactional, leased queue shared by processes on a single host.

SQLite must remain on a local filesystem; this backend does not support NFS.
Lease loss fences all subsequent history writes and never replays external calls.
"""
from contextvars import ContextVar
from queue import Empty, Full
from threading import Event, Lock, Thread, local
import time
from uuid import uuid4

from chatbi.application.progress import QueryStopped

LEASE = ContextVar("query_lease", default=None)


def fence(db, query_id):
    claim = LEASE.get()
    if claim and claim[0] == query_id:
        row = db.execute("SELECT token,expires FROM shared_jobs WHERE id=?", (query_id,)).fetchone()
        if not row or row[0] != claim[1] or row[1] <= time.time():
            raise QueryStopped("lease_lost")


class SharedEvent:
    def __init__(self, history, query_id):
        self.history, self.query_id = history, query_id
        self.local_stop = Event()

    def is_set(self):
        if self.local_stop.is_set():
            return True
        with self.history.connect() as db:
            fence(db, self.query_id)
            row = db.execute("SELECT cancelled FROM shared_jobs WHERE id=?", (self.query_id,)).fetchone()
        return bool(row and row[0])

    def set(self):
        with self.history.connect() as db:
            db.execute("UPDATE shared_jobs SET cancelled=1 WHERE id=?", (self.query_id,))


class SharedQueue:
    def __init__(self, history, capacity, timeout, lease_seconds):
        self.history, self.capacity, self.timeout, self.lease_seconds = history, capacity, timeout, lease_seconds
        self.local = local()
        self.claims = {}
        self.lock, self.stop = Lock(), Event()
        with history.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS shared_jobs(id TEXT PRIMARY KEY,question TEXT,
                deadline REAL NOT NULL,token TEXT,expires REAL,cancelled INTEGER NOT NULL DEFAULT 0)""")
        self.heartbeat = Thread(target=self._renew, daemon=True, name="chatbi-lease-heartbeat")
        self.heartbeat.start()

    def full(self):
        with self.history.connect() as db:
            return db.execute("SELECT COUNT(*) FROM shared_jobs").fetchone()[0] >= self.capacity

    def put_nowait(self, item):
        query_id, question, _ = item
        with self.history.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM shared_jobs").fetchone()[0] >= self.capacity:
                raise Full()
            db.execute("INSERT INTO shared_jobs(id,question,deadline) VALUES(?,?,?)", (query_id,question,time.time()+self.timeout))

    def get(self, timeout=.2):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            expired = []
            with self.history.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                expired = [r[0] for r in db.execute("SELECT id FROM shared_jobs WHERE token IS NOT NULL AND expires<=?", (time.time(),))]
                for query_id in expired:
                    # Atomic fencing precedes interruption. No worker may commit after expiry.
                    db.execute("DELETE FROM shared_jobs WHERE id=?", (query_id,))
                    checkpoint = db.execute("SELECT 1 FROM analysis_checkpoints WHERE query_id=?", (query_id,)).fetchone()
                    db.execute("UPDATE queries SET status=? WHERE id=?", ("suspended" if checkpoint else "interrupted",query_id))
                row = db.execute("SELECT id,question,deadline FROM shared_jobs WHERE token IS NULL ORDER BY deadline LIMIT 1").fetchone()
                if row:
                    token = uuid4().hex
                    db.execute("UPDATE shared_jobs SET token=?,expires=? WHERE id=? AND token IS NULL", (token,time.time()+self.lease_seconds,row[0]))
            for query_id in expired:
                self.history.event(query_id,"lease_expired",{"message":"Worker 租约失效，任务已中断；未自动重放模型或数据库调用。"})
            if row:
                query_id,question,deadline=row
                with self.lock:
                    self.claims[query_id]=token
                self.local.claim=(query_id,token)
                self.local.context=LEASE.set(self.local.claim)
                return query_id,question,time.monotonic()+max(0,deadline-time.time())
            self.stop.wait(.03)
        raise Empty()

    def task_done(self):
        query_id,token=self.local.claim
        with self.history.connect() as db:
            db.execute("DELETE FROM shared_jobs WHERE id=? AND token=?",(query_id,token))
        with self.lock:
            self.claims.pop(query_id,None)
        LEASE.reset(self.local.context)

    def _renew(self):
        while not self.stop.wait(self.lease_seconds/3):
            with self.lock:
                claims=list(self.claims.items())
            with self.history.connect() as db:
                for query_id,token in claims:
                    db.execute("UPDATE shared_jobs SET expires=? WHERE id=? AND token=? AND expires>?",
                               (time.time()+self.lease_seconds,query_id,token,time.time()))

    def close(self):
        self.stop.set()
        self.heartbeat.join(timeout=2)
