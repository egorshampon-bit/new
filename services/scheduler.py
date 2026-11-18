import re
import json
import asyncio
import logging
from datetime import datetime, timedelta
import zoneinfo
from typing import List, Optional, Tuple

from aiogram import Bot
from aiogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
)
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

from config import TIMEZONE, POLL_INTERVAL_SECONDS
from database import (
    get_due_posts,
    claim_post_for_publishing,
    mark_post_published,
    mark_post_failed,
)

logger = logging.getLogger(__name__)


def get_tz() -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(TIMEZONE)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


def parse_time_slots(text: str) -> List[str]:
    """
    Parses strings like '15,18', '14:00, 21:00', '9 12 15 18' into ['09:00', '12:00', ...]
    """
    # Replace separators with spaces
    normalized = re.sub(r"[,;|\n]+", " ", text).strip()
    tokens = normalized.split()
    results = set()

    for token in tokens:
        # Match HH:MM or HH
        match = re.match(r"^(\d{1,2})(?::(\d{1,2}))?$", token)
        if match:
            h = int(match.group(1))
            m = int(match.group(2)) if match.group(2) is not None else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                results.add(f"{h:02d}:{m:02d}")

    return sorted(list(results))


def calculate_next_slot(
    schedule_slots: List[str],
    last_post_iso: Optional[str] = None,
    occupied_slots: Optional[set] = None,
    tz_name: Optional[str] = None
) -> Tuple[datetime, str]:
    """
    Calculates the next available slot for a channel based on its schedule.
    Guarantees no collisions with occupied_slots (never schedules 2 posts to the same slot).
    Returns (slot_datetime_in_tz, formatted_display_string).
    """
    tz = zoneinfo.ZoneInfo(tz_name or TIMEZONE)
    now = datetime.now(tz)

    parsed_slots: List[Tuple[int, int]] = []
    for s in schedule_slots:
        try:
            parts = s.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            parsed_slots.append((h, m))
        except (ValueError, IndexError):
            continue

    parsed_slots.sort()

    # Normalize occupied slots into "YYYY-MM-DD HH:MM" strings in target tz
    occupied_normalized = set()
    if occupied_slots:
        for item in occupied_slots:
            if isinstance(item, str):
                try:
                    dt = datetime.fromisoformat(item)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=tz)
                    else:
                        dt = dt.astimezone(tz)
                    occupied_normalized.add(dt.strftime("%Y-%m-%d %H:%M"))
                except Exception:
                    pass
            elif isinstance(item, datetime):
                dt = item if item.tzinfo else item.replace(tzinfo=tz)
                occupied_normalized.add(dt.astimezone(tz).strftime("%Y-%m-%d %H:%M"))

    # If no schedule is configured, post immediately (now + 5 seconds)
    if not parsed_slots:
        immediate_dt = now + timedelta(seconds=5)
        return immediate_dt, immediate_dt.strftime("%d.%m.%Y %H:%M")

    start_from = now
    if last_post_iso:
        try:
            last_dt = datetime.fromisoformat(last_post_iso)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=tz)
            else:
                last_dt = last_dt.astimezone(tz)
            if last_dt > now:
                start_from = last_dt
        except Exception as e:
            logger.warning(f"Could not parse last post ISO {last_post_iso}: {e}")

    # Earliest acceptable time is strictly after start_from
    min_future = max(now + timedelta(minutes=1), start_from + timedelta(seconds=1))

    current_date = min_future.date()
    # Find next matching slot within 365 days
    for day_offset in range(365):
        test_date = current_date + timedelta(days=day_offset)
        for h, m in parsed_slots:
            candidate = datetime(test_date.year, test_date.month, test_date.day, h, m, 0, tzinfo=tz)
            cand_key = candidate.strftime("%Y-%m-%d %H:%M")
            if candidate >= min_future and cand_key not in occupied_normalized:
                return candidate, candidate.strftime("%d.%m.%Y %H:%M")

    # Fallback
    fallback = now + timedelta(hours=1)
    return fallback, fallback.strftime("%d.%m.%Y %H:%M")


async def publish_single_post(bot: Bot, post: dict):
    post_id = post["id"]
    tg_channel_id = post["tg_channel_id"]
    msg_type = post["msg_type"]
    file_id = post["file_id"]
    caption = post["caption"]
    text = post["text"]
    album_data_raw = post.get("album_data")

    logger.info(f"Publishing post #{post_id} ({msg_type}) to channel {tg_channel_id}...")

    try:
        sent_msg_id = None
        if msg_type == "photo":
            res = await bot.send_photo(
                chat_id=tg_channel_id,
                photo=file_id,
                caption=caption,
                parse_mode="HTML"
            )
            sent_msg_id = res.message_id
        elif msg_type == "video":
            res = await bot.send_video(
                chat_id=tg_channel_id,
                video=file_id,
                caption=caption,
                parse_mode="HTML"
            )
            sent_msg_id = res.message_id
        elif msg_type == "document":
            res = await bot.send_document(
                chat_id=tg_channel_id,
                document=file_id,
                caption=caption,
                parse_mode="HTML"
            )
            sent_msg_id = res.message_id
        elif msg_type == "animation":
            res = await bot.send_animation(
                chat_id=tg_channel_id,
                animation=file_id,
                caption=caption,
                parse_mode="HTML"
            )
            sent_msg_id = res.message_id
        elif msg_type == "audio":
            res = await bot.send_audio(
                chat_id=tg_channel_id,
                audio=file_id,
                caption=caption,
                parse_mode="HTML"
            )
            sent_msg_id = res.message_id
        elif msg_type == "text":
            res = await bot.send_message(
                chat_id=tg_channel_id,
                text=text or caption or "",
                parse_mode="HTML"
            )
            sent_msg_id = res.message_id
        elif msg_type == "album" and album_data_raw:
            items = json.loads(album_data_raw)
            media_list = []
            for idx, itm in enumerate(items):
                itm_type = itm.get("type", "photo")
                itm_fid = itm.get("file_id")
                # Apply caption only to first item in album
                itm_cap = caption if idx == 0 else None
                if itm_type == "photo":
                    media_list.append(InputMediaPhoto(media=itm_fid, caption=itm_cap, parse_mode="HTML"))
                elif itm_type == "video":
                    media_list.append(InputMediaVideo(media=itm_fid, caption=itm_cap, parse_mode="HTML"))
                elif itm_type == "document":
                    media_list.append(InputMediaDocument(media=itm_fid, caption=itm_cap, parse_mode="HTML"))
                elif itm_type == "audio":
                    media_list.append(InputMediaAudio(media=itm_fid, caption=itm_cap, parse_mode="HTML"))
            if media_list:
                res_list = await bot.send_media_group(chat_id=tg_channel_id, media=media_list)
                sent_msg_id = res_list[0].message_id if res_list else None

        await mark_post_published(post_id, sent_msg_id)
        logger.info(f"✓ Post #{post_id} published successfully (msg_id: {sent_msg_id}).")

    except TelegramRetryAfter as e:
        logger.warning(f"Telegram flood wait for post #{post_id}: retry after {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
    except TelegramAPIError as e:
        logger.error(f"Telegram API Error publishing post #{post_id}: {e}")
        await mark_post_failed(post_id, str(e))
    except Exception as e:
        logger.exception(f"Unexpected error publishing post #{post_id}: {e}")
        await mark_post_failed(post_id, str(e))


async def run_scheduler_worker(bot: Bot):
    """
    Background worker loop that scans SQLite for due posts and publishes them.
    """
    tz = get_tz()
    logger.info("Starting background autoposter scheduler worker...")

    while True:
        try:
            now_local = datetime.now(tz)
            now_iso = now_local.isoformat()
            due_posts = await get_due_posts(now_iso)

            if due_posts:
                logger.info(f"Found {len(due_posts)} due posts ready for publication.")
                for post in due_posts:
                    # Atomically claim post to guarantee it is published exactly once
                    claimed = await claim_post_for_publishing(post["id"])
                    if not claimed:
                        logger.info(f"Post #{post['id']} already claimed or published. Skipping.")
                        continue
                    await publish_single_post(bot, post)
                    # Gentle throttle between posts
                    await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            logger.info("Scheduler worker cancelled.")
            break
        except Exception as e:
            logger.exception(f"Error in scheduler worker cycle: {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
