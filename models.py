"""Database models for AIFab."""
import sqlite3
import os
from config import DATABASE
from crypto_utils import generate_key, encrypt_key_to_hex, decrypt_key_from_hex


def get_db():
    """Get database connection for current request."""
    db_path = DATABASE
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Initialize database tables and seed default users from config."""
    conn = get_db()
    
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS workrooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT UNIQUE NOT NULL,
            title TEXT DEFAULT '',
            creator_id INTEGER REFERENCES users(id),
            reference_room_id TEXT DEFAULT '',
            is_closed BOOLEAN DEFAULT 0,
            is_deleted BOOLEAN DEFAULT 0,
            encrypt_key_hex TEXT NOT NULL,
            total_size INTEGER DEFAULT 0,
            invitations_raw TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT REFERENCES workrooms(room_id),
            invitee_name TEXT NOT NULL,
            msgkey_hash TEXT NOT NULL,
            msgkey_salt TEXT NOT NULL,
            is_used BOOLEAN DEFAULT 0,
            used_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT REFERENCES workrooms(room_id),
            parent_id INTEGER REFERENCES posts(id),
            author_type TEXT NOT NULL,
            author_name TEXT NOT NULL,
            content_encrypted BLOB NOT NULL,
            content_size INTEGER NOT NULL,
            is_deleted BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_posts_room ON posts(room_id);
        CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_id);
        CREATE INDEX IF NOT EXISTS idx_invitations_room ON invitations(room_id);
    """)

    # Seed users from config.USERS (idempotent, keeps existing rows untouched)
    import hashlib
    from config import USERS
    for username, info in USERS.items():
        pw_hash = hashlib.sha256((info['password'] + 'aifab-salt').encode()).hexdigest()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, ?)",
                (username, pw_hash, info.get('display_name', username), info.get('is_admin', 0))
            )
        except sqlite3.IntegrityError:
            pass
    conn.commit()

    # Idempotent migration: add used_by column if missing (older databases)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(invitations)").fetchall()]
        if 'used_by' not in cols:
            conn.execute("ALTER TABLE invitations ADD COLUMN used_by TEXT DEFAULT ''")
            conn.commit()
    except sqlite3.Error:
        pass

    conn.close()


# ===== User functions =====

def verify_user(username, password):
    """Verify username and password. Returns user dict or None."""
    conn = get_db()
    import hashlib
    pw_hash = hashlib.sha256((password + 'aifab-salt').encode()).hexdigest()
    user = conn.execute(
        "SELECT id, username, display_name, is_admin FROM users WHERE username = ? AND password_hash = ?",
        (username, pw_hash)
    ).fetchone()
    conn.close()
    if user:
        return dict(user)
    return None


def get_user_by_id(user_id):
    """Get user by ID."""
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, display_name, is_admin FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return dict(user) if user else None


def change_password(user_id, old_password, new_password):
    """Change user password. Returns (ok, error_or_none)."""
    import hashlib
    conn = get_db()
    user = conn.execute(
        "SELECT id, password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not user:
        conn.close()
        return False, '用户不存在'
    
    old_hash = hashlib.sha256((old_password + 'aifab-salt').encode()).hexdigest()
    if user['password_hash'] != old_hash:
        conn.close()
        return False, '旧密码不正确'
    
    new_hash = hashlib.sha256((new_password + 'aifab-salt').encode()).hexdigest()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    conn.commit()
    conn.close()
    return True, None


# ===== Workroom functions =====

import random
import string
import json


def generate_room_id():
    """Generate a random room ID: room_xxxxxx (6 chars, a-z case-sensitive)."""
    chars = string.ascii_letters  # a-z A-Z
    suffix = ''.join(random.choice(chars) for _ in range(6))
    return f"room_{suffix}"


def create_workroom(creator_id, creator_name, title, reference_room_id='', first_post_content=''):
    """Create a new workroom with encrypted first post."""
    conn = get_db()
    encrypt_key = generate_key()
    encrypt_key_hex = encrypt_key_to_hex(encrypt_key)
    room_id = generate_room_id()
    
    # Ensure unique room_id
    while conn.execute("SELECT id FROM workrooms WHERE room_id = ?", (room_id,)).fetchone():
        room_id = generate_room_id()
    
    conn.execute(
        "INSERT INTO workrooms (room_id, title, creator_id, reference_room_id, encrypt_key_hex, total_size, invitations_raw) VALUES (?, ?, ?, ?, ?, 0, ?)",
        (room_id, title, creator_id, reference_room_id, encrypt_key_hex, '[]')
    )
    conn.commit()
    
    # Add first post if provided
    if first_post_content.strip():
        from crypto_utils import encrypt_content
        content_size = len(first_post_content.encode('utf-8'))
        encrypted = encrypt_content(first_post_content, encrypt_key)
        conn.execute(
            "INSERT INTO posts (room_id, parent_id, author_type, author_name, content_encrypted, content_size) VALUES (?, NULL, 'human', ?, ?, ?)",
            (room_id, creator_name, encrypted, content_size)
        )
        conn.execute(
            "UPDATE workrooms SET total_size = total_size + ? WHERE room_id = ?",
            (content_size, room_id)
        )
        conn.commit()
    
    conn.close()
    return room_id, encrypt_key_hex, encrypt_key


def close_workroom(room_id, user_id):
    """Close a workroom. Returns True if a row was updated (creator only)."""
    conn = get_db()
    cur = conn.execute("UPDATE workrooms SET is_closed = 1 WHERE room_id = ? AND creator_id = ?", (room_id, user_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def open_workroom(room_id, user_id):
    """Re-open a closed workroom. Returns True if a row was updated (creator only)."""
    conn = get_db()
    cur = conn.execute("UPDATE workrooms SET is_closed = 0 WHERE room_id = ? AND creator_id = ?", (room_id, user_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_workroom(room_id):
    """Get workroom info."""
    conn = get_db()
    wr = conn.execute("SELECT * FROM workrooms WHERE room_id = ?", (room_id,)).fetchone()
    conn.close()
    return dict(wr) if wr else None


def get_user_workrooms(user_id, status='all'):
    """Get workrooms created by a specific user, filtered by status.
    status: 'all', 'open', 'closed', 'deleted'
    """
    conn = get_db()
    if status == 'open':
        rows = conn.execute(
            "SELECT * FROM workrooms WHERE creator_id = ? AND is_closed = 0 AND is_deleted = 0 ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
    elif status == 'closed':
        rows = conn.execute(
            "SELECT * FROM workrooms WHERE creator_id = ? AND is_closed = 1 AND is_deleted = 0 ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
    elif status == 'deleted':
        rows = conn.execute(
            "SELECT * FROM workrooms WHERE creator_id = ? AND is_deleted = 1 ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM workrooms WHERE creator_id = ? AND is_deleted = 0 ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_workrooms(status='all'):
    """Get all workrooms visible to every human user (shared list), filtered by status.
    Each row includes creator_name via JOIN users.
    status: 'all', 'open', 'closed', 'deleted'
    """
    conn = get_db()
    base = "SELECT w.*, u.display_name AS creator_name FROM workrooms w LEFT JOIN users u ON w.creator_id = u.id"
    if status == 'open':
        rows = conn.execute(base + " WHERE w.is_closed = 0 AND w.is_deleted = 0 ORDER BY w.created_at DESC").fetchall()
    elif status == 'closed':
        rows = conn.execute(base + " WHERE w.is_closed = 1 AND w.is_deleted = 0 ORDER BY w.created_at DESC").fetchall()
    elif status == 'deleted':
        rows = conn.execute(base + " WHERE w.is_deleted = 1 ORDER BY w.created_at DESC").fetchall()
    else:
        rows = conn.execute(base + " WHERE w.is_deleted = 0 ORDER BY w.created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_workroom(room_id, user_id):
    """Soft delete a workroom. Returns True if a row was updated (creator only)."""
    conn = get_db()
    cur = conn.execute("UPDATE workrooms SET is_deleted = 1 WHERE room_id = ? AND creator_id = ?", (room_id, user_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# ===== Invitation functions =====

from crypto_utils import generate_msgkey


def create_invitation(room_id, invitee_name):
    """Create an invitation for a workroom. Returns (msgkey_plaintext, msgkey_hash, salt)."""
    conn = get_db()
    msgkey, msgkey_hash, salt = generate_msgkey()
    conn.execute(
        "INSERT INTO invitations (room_id, invitee_name, msgkey_hash, msgkey_salt) VALUES (?, ?, ?, ?)",
        (room_id, invitee_name, msgkey_hash, salt)
    )
    conn.commit()
    conn.close()
    return msgkey


def save_invitations_raw(room_id, invitations_data):
    """Save raw invitation data for display.
    invitations_data: list of dicts with room_id, msgkey, encrypt_key
    """
    conn = get_db()
    conn.execute(
        "UPDATE workrooms SET invitations_raw = ? WHERE room_id = ?",
        (json.dumps(invitations_data), room_id)
    )
    conn.commit()
    conn.close()


def verify_invitation(room_id, msgkey, ai_name=None):
    """Verify an invitation msgkey. Records usage (is_used=1, used_by=ai_name) on first use.
    Returns the invitation dict (with used_by) or None.
    """
    conn = get_db()
    invite = conn.execute(
        "SELECT * FROM invitations WHERE room_id = ?",
        (room_id,)
    ).fetchall()
    conn.close()
    for inv in invite:
        inv = dict(inv)
        from crypto_utils import verify_msgkey
        if verify_msgkey(msgkey, inv['msgkey_hash'], inv['msgkey_salt']):
            # Mark as used and record who used it (first use wins)
            conn2 = get_db()
            if not inv.get('is_used'):
                conn2.execute(
                    "UPDATE invitations SET is_used = 1, used_by = ? WHERE id = ?",
                    (ai_name or '', inv['id'])
                )
            elif ai_name and not inv.get('used_by'):
                # Already marked used but name unknown → fill it in
                conn2.execute(
                    "UPDATE invitations SET used_by = ? WHERE id = ?",
                    (ai_name, inv['id'])
                )
            conn2.commit()
            inv['is_used'] = 1
            if ai_name:
                inv['used_by'] = ai_name
            conn2.close()
            return inv
    return None


def get_invitations_status(room_id):
    """Get all invitations of a workroom with usage status, in creation order.
    Returns list of {invitee_name, is_used, used_by, created_at}.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT invitee_name, is_used, used_by, created_at FROM invitations WHERE room_id = ? ORDER BY id ASC",
        (room_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== Post functions =====

MAX_WORKROOM_SIZE = 150 * 1024  # 150KB
MAX_POST_SIZE = 50 * 1024       # 50KB


def add_post(room_id, parent_id, author_type, author_name, content, encrypt_key):
    """Add a post to a workroom."""
    content_size = len(content.encode('utf-8'))
    
    if content_size > MAX_POST_SIZE:
        return None, f"Content exceeds 50KB limit ({content_size} bytes)"
    
    conn = get_db()
    
    # Check workroom total size
    wr = conn.execute("SELECT total_size, is_closed FROM workrooms WHERE room_id = ?", (room_id,)).fetchone()
    if not wr:
        conn.close()
        return None, "Workroom not found"
    if wr['is_closed']:
        conn.close()
        return None, "Workroom is closed"
    
    new_total = wr['total_size'] + content_size
    if new_total > MAX_WORKROOM_SIZE:
        conn.close()
        return None, f"Workroom size limit exceeded ({new_total}/{MAX_WORKROOM_SIZE} bytes)"
    
    # Encrypt and insert
    from crypto_utils import encrypt_content
    encrypted = encrypt_content(content, encrypt_key)
    cursor = conn.execute(
        "INSERT INTO posts (room_id, parent_id, author_type, author_name, content_encrypted, content_size) VALUES (?, ?, ?, ?, ?, ?)",
        (room_id, parent_id, author_type, author_name, encrypted, content_size)
    )
    post_id = cursor.lastrowid
    
    conn.execute("UPDATE workrooms SET total_size = total_size + ? WHERE room_id = ?", (content_size, room_id))
    conn.commit()
    conn.close()
    
    return post_id, None


def delete_post(room_id, post_id, encrypt_key):
    """Hard delete a post. Returns (True, None) or (False, error_msg)."""
    conn = get_db()
    
    # Get post info to update total_size
    from crypto_utils import decrypt_content
    blob = conn.execute("SELECT content_encrypted, content_size FROM posts WHERE id = ? AND room_id = ?", (post_id, room_id)).fetchone()
    if not blob:
        conn.close()
        return False, "Post not found"
    
    content_size = blob['content_size']
    
    # Hard delete
    conn.execute("DELETE FROM posts WHERE id = ? AND room_id = ?", (post_id, room_id))
    # Update workroom total_size
    conn.execute("UPDATE workrooms SET total_size = MAX(0, total_size - ?) WHERE room_id = ?", (content_size, room_id))
    conn.commit()
    conn.close()
    
    return True, None


def get_posts(room_id, encrypt_key):
    """Get all posts for a workroom, decrypted."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, parent_id, author_type, author_name, content_size, created_at, updated_at FROM posts WHERE room_id = ? AND is_deleted = 0 ORDER BY created_at ASC",
        (room_id,)
    ).fetchall()
    conn.close()
    
    from crypto_utils import decrypt_content
    results = []
    for r in rows:
        r = dict(r)
        # Fetch encrypted content separately
        conn2 = get_db()
        blob = conn2.execute("SELECT content_encrypted FROM posts WHERE id = ?", (r['id'],)).fetchone()
        conn2.close()
        if blob:
            try:
                r['content'] = decrypt_content(blob['content_encrypted'], encrypt_key)
            except Exception as e:
                r['content'] = f'[解密失败: {e}]'
        else:
            r['content'] = '[内容丢失]'
        results.append(r)
    
    return results
