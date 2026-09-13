"""Durable, bounded visitor chat history. No session credentials are stored."""
from pathlib import Path
import sqlite3
import time


class ChatHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, visitor TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL, at REAL NOT NULL)')

    def append(self, visitor, name, text, at=None):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO messages(visitor,name,text,at) VALUES(?,?,?,?)',
                       (visitor, name, text, time.time() if at is None else at))
            db.execute('DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 100)')

    def messages(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT id,visitor,name,text,at FROM messages ORDER BY id')]
