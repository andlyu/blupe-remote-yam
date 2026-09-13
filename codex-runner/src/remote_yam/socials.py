"""Optional participant handles, retained privately for sharing run clips."""
import re
import sqlite3
import time
from pathlib import Path


def social_handles(payload):
    result = {}
    for key, label in (('x_handle', 'X'), ('instagram_handle', 'Instagram')):
        value = payload.get(key, '')
        if not isinstance(value, str):
            raise ValueError(f'Enter a valid {label} handle or leave it blank')
        value = value.strip().removeprefix('@')
        if value and not re.fullmatch(r'[A-Za-z0-9_.]{1,32}', value):
            raise ValueError(f'Enter a {label} handle using letters, numbers, underscores or periods (up to 32 characters)')
        result[key] = value
    email = payload.get('email', '')
    if not isinstance(email, str):
        raise ValueError('Enter a valid email address or leave it blank')
    email = email.strip()
    if email and (len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or any(ord(c) < 32 for c in email)):
        raise ValueError('Enter a valid email address or leave it blank')
    result['email'] = email
    return result


def save_handles(database, session_id, runner_name, handles):
    if not any(handles.values()):
        return
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create privately before SQLite opens the file.
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS run_socials (session_id TEXT PRIMARY KEY, created REAL, runner_name TEXT, x_handle TEXT, instagram_handle TEXT)')
        if 'email' not in {row[1] for row in db.execute('PRAGMA table_info(run_socials)')}:
            db.execute('ALTER TABLE run_socials ADD COLUMN email TEXT')
        db.execute('INSERT INTO run_socials (session_id, created, runner_name, x_handle, instagram_handle, email) VALUES (?, ?, ?, ?, ?, ?)',
                   (session_id, time.time(), runner_name, handles.get('x_handle', ''), handles.get('instagram_handle', ''), handles.get('email', '')))
