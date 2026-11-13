import os
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
import aiosqlite

import config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_db():
    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = await aiosqlite.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA busy_timeout=30000;")
    try:
        yield conn
    finally:
        await conn.close()


async def init_db():
    async with get_db() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tg_channel_id INTEGER NOT NULL,
                channel_title TEXT NOT NULL,
                channel_username TEXT,
                schedule_slots TEXT NOT NULL DEFAULT '[]',
                caption TEXT,
                rename_docs TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, tg_channel_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                session_id TEXT,
                msg_type TEXT NOT NULL,
                file_id TEXT,
                caption TEXT,
                text TEXT,
                album_data TEXT,
                publish_at TIMESTAMP NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                published_message_id INTEGER,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP,
                FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                active_channel_id INTEGER
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.commit()
    logger.info("Database initialized successfully.")


# Channels management
async def add_or_update_channel(user_id: int, tg_channel_id: int, title: str, username: Optional[str]) -> int:
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            INSERT INTO channels (user_id, tg_channel_id, channel_title, channel_username)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, tg_channel_id) DO UPDATE SET
                channel_title = excluded.channel_title,
                channel_username = excluded.channel_username
            RETURNING id;
            """,
            (user_id, tg_channel_id, title, username)
        )
        row = await cursor.fetchone()
        channel_id = row[0]
        # Set as active channel on the same connection
        await conn.execute(
            """
            INSERT INTO user_states (user_id, active_channel_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET active_channel_id = excluded.active_channel_id;
            """,
            (user_id, channel_id)
        )
        await conn.commit()
        return channel_id


async def get_user_channels(user_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM channels WHERE user_id = ? ORDER BY id ASC;",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_channel_by_id(channel_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM channels WHERE id = ?;",
            (channel_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_active_channel(user_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT c.* FROM channels c
            JOIN user_states u ON u.active_channel_id = c.id
            WHERE u.user_id = ?;
            """,
            (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        # Fallback to first channel
        cursor = await conn.execute(
            "SELECT * FROM channels WHERE user_id = ? ORDER BY id ASC LIMIT 1;",
            (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            ch = dict(row)
            await conn.execute(
                """
                INSERT INTO user_states (user_id, active_channel_id)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET active_channel_id = excluded.active_channel_id;
                """,
                (user_id, ch["id"])
            )
            await conn.commit()
            return ch
        return None


async def set_active_channel(user_id: int, channel_id: int):
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO user_states (user_id, active_channel_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET active_channel_id = excluded.active_channel_id;
            """,
            (user_id, channel_id)
        )
        await conn.commit()


async def update_channel_schedule(channel_id: int, slots: List[str]):
    async with get_db() as conn:
        await conn.execute(
            "UPDATE channels SET schedule_slots = ? WHERE id = ?;",
            (json.dumps(slots), channel_id)
        )
        await conn.commit()


async def update_channel_caption(channel_id: int, caption: Optional[str]):
    async with get_db() as conn:
        await conn.execute(
            "UPDATE channels SET caption = ? WHERE id = ?;",
            (caption, channel_id)
        )
        await conn.commit()


async def delete_channel(channel_id: int, user_id: int):
    async with get_db() as conn:
        await conn.execute("DELETE FROM posts WHERE channel_id = ?;", (channel_id,))
        await conn.execute("DELETE FROM channels WHERE id = ? AND user_id = ?;", (channel_id, user_id))
        await conn.execute("UPDATE user_states SET active_channel_id = NULL WHERE active_channel_id = ?;", (channel_id,))
        await conn.commit()


# Posts management
async def add_post(
    channel_id: int,
    user_id: int,
    session_id: str,
    msg_type: str,
    publish_at: str,
    file_id: Optional[str] = None,
    caption: Optional[str] = None,
    text: Optional[str] = None,
    album_data: Optional[str] = None
) -> int:
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            INSERT INTO posts (
                channel_id, user_id, session_id, msg_type, file_id, caption, text, album_data, publish_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            RETURNING id;
            """,
            (channel_id, user_id, session_id, msg_type, file_id, caption, text, album_data, publish_at)
        )
        row = await cursor.fetchone()
        await conn.commit()
        return row[0]


async def get_last_scheduled_post_time(channel_id: int) -> Optional[str]:
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT publish_at FROM posts
            WHERE channel_id = ? AND status = 'pending'
            ORDER BY datetime(publish_at) DESC
            LIMIT 1;
            """,
            (channel_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_pending_posts_count(channel_id: int) -> int:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM posts WHERE channel_id = ? AND status = 'pending';",
            (channel_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def cancel_session_posts(session_id: str) -> int:
    async with get_db() as conn:
        cursor = await conn.execute(
            "DELETE FROM posts WHERE session_id = ? AND status = 'pending';",
            (session_id,)
        )
        await conn.commit()
        return cursor.rowcount


async def get_due_posts(current_iso: str) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT p.*, c.tg_channel_id, c.channel_title
            FROM posts p
            JOIN channels c ON p.channel_id = c.id
            WHERE p.status = 'pending' AND datetime(p.publish_at) <= datetime(?)
            ORDER BY datetime(p.publish_at) ASC;
            """,
            (current_iso,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def mark_post_published(post_id: int, message_id: Optional[int] = None):
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE posts
            SET status = 'published',
                published_message_id = ?,
                published_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (message_id, post_id)
        )
        await conn.commit()


async def mark_post_failed(post_id: int, error_msg: str):
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE posts
            SET status = 'error',
                error_message = ?
            WHERE id = ?;
            """,
            (error_msg, post_id)
        )
        await conn.commit()


async def get_channel_queue_preview(channel_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT id, msg_type, caption, text, publish_at, status
            FROM posts
            WHERE channel_id = ? AND status = 'pending'
            ORDER BY datetime(publish_at) ASC
            LIMIT ?;
            """,
            (channel_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def clear_channel_queue(channel_id: int) -> int:
    async with get_db() as conn:
        cursor = await conn.execute(
            "DELETE FROM posts WHERE channel_id = ? AND status = 'pending';",
            (channel_id,)
        )
        await conn.commit()
        return cursor.rowcount


# User and Admin management
async def add_or_update_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None
):
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, last_active_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                is_blocked = 0,
                last_active_at = CURRENT_TIMESTAMP;
            """,
            (user_id, username, first_name, last_name)
        )
        await conn.commit()


async def set_user_admin(user_id: int, is_admin: int = 1):
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, is_admin)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_admin = excluded.is_admin;
            """,
            (user_id, is_admin)
        )
        await conn.commit()


async def is_user_admin(user_id: int) -> bool:
    if config.ADMIN_IDS and user_id in config.ADMIN_IDS:
        return True
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT is_admin FROM users WHERE user_id = ?;",
            (user_id,)
        )
        row = await cursor.fetchone()
        return bool(row and row[0] == 1)


async def get_admin_stats() -> Dict[str, Any]:
    async with get_db() as conn:
        # Total users
        cursor = await conn.execute("SELECT COUNT(*) FROM users;")
        total_users = (await cursor.fetchone())[0]

        # New users today
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE date(created_at) = date('now');"
        )
        new_today = (await cursor.fetchone())[0]

        # New users this week (last 7 days)
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE datetime(created_at) >= datetime('now', '-7 days');"
        )
        new_week = (await cursor.fetchone())[0]

        # Active in last 24h
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE datetime(last_active_at) >= datetime('now', '-1 day');"
        )
        active_24h = (await cursor.fetchone())[0]

        # Channels count
        cursor = await conn.execute("SELECT COUNT(*) FROM channels;")
        total_channels = (await cursor.fetchone())[0]

        # Posts published
        cursor = await conn.execute("SELECT COUNT(*) FROM posts WHERE status = 'published';")
        posts_published = (await cursor.fetchone())[0]

        # Posts pending
        cursor = await conn.execute("SELECT COUNT(*) FROM posts WHERE status = 'pending';")
        posts_pending = (await cursor.fetchone())[0]

        return {
            "total_users": total_users,
            "new_today": new_today,
            "new_week": new_week,
            "active_24h": active_24h,
            "total_channels": total_channels,
            "posts_published": posts_published,
            "posts_pending": posts_pending,
        }


async def get_all_broadcast_users() -> List[int]:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT user_id FROM users WHERE is_blocked = 0;"
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def mark_user_blocked(user_id: int):
    async with get_db() as conn:
        await conn.execute(
            "UPDATE users SET is_blocked = 1 WHERE user_id = ?;",
            (user_id,)
        )
        await conn.commit()


async def get_recent_users(limit: int = 15) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT user_id, username, first_name, is_admin, created_at, last_active_at
            FROM users
            ORDER BY datetime(last_active_at) DESC
            LIMIT ?;
            """,
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

