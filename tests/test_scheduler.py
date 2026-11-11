import asyncio
import os
import shutil
from datetime import datetime
import zoneinfo

from services.scheduler import parse_time_slots, calculate_next_slot
from database.models import (
    init_db,
    add_or_update_channel,
    get_channel_by_id,
    update_channel_schedule,
    add_post,
    get_due_posts,
    cancel_session_posts,
    get_pending_posts_count,
    get_last_scheduled_post_time,
)


def test_parsing():
    print("Testing parse_time_slots...")
    assert parse_time_slots("15,18") == ["15:00", "18:00"], f"Failed: {parse_time_slots('15,18')}"
    assert parse_time_slots("14:00, 21:00") == ["14:00", "21:00"]
    assert parse_time_slots("9 12 15 18") == ["09:00", "12:00", "15:00", "18:00"]
    assert parse_time_slots("14:30") == ["14:30"]
    print("✓ parse_time_slots tests passed!")


def test_consecutive_slots():
    print("\nTesting consecutive slot allocation (10 media files at 15:00 & 18:00)...")
    slots = ["15:00", "18:00"]
    tz = zoneinfo.ZoneInfo("Europe/Moscow")

    # Simulate batch of 10 media files
    last_iso = None
    results = []
    for i in range(1, 11):
        slot_dt, formatted_str = calculate_next_slot(slots, last_iso, tz_name="Europe/Moscow")
        results.append((i, slot_dt, formatted_str))
        last_iso = slot_dt.isoformat()
        print(f"File #{i:2d} -> {formatted_str}")

    # Check that each consecutive slot is strictly greater than the previous
    for idx in range(len(results) - 1):
        curr_dt = results[idx][1]
        next_dt = results[idx + 1][1]
        assert next_dt > curr_dt, f"Slot {idx+1} not greater than {idx}"
        # Check hour is either 15 or 18
        assert next_dt.hour in (15, 18), f"Unexpected hour: {next_dt.hour}"

    print("✓ Consecutive slot allocation tests passed!")


async def test_database_lifecycle():
    print("\nTesting SQLite database operations...")
    import config
    config.DB_PATH = "tg_autoposter/data/test_bot.sqlite"
    if os.path.exists("tg_autoposter/data/test_bot.sqlite"):
        os.remove("tg_autoposter/data/test_bot.sqlite")

    await init_db()

    # 1. Add channel
    ch_id = await add_or_update_channel(
        user_id=12345,
        tg_channel_id=-1009999999,
        title="Тестовый канал",
        username="test_channel"
    )
    assert ch_id > 0
    print(f"Added channel #{ch_id}")

    # 2. Update schedule
    await update_channel_schedule(ch_id, ["15:00", "18:00"])
    ch = await get_channel_by_id(ch_id)
    assert '["15:00", "18:00"]' in ch["schedule_slots"]

    # 3. Add 4 posts under session A
    session_a = "session_test_a"
    last_iso = None
    for i in range(1, 5):
        slot_dt, _ = calculate_next_slot(["15:00", "18:00"], last_iso)
        last_iso = slot_dt.isoformat()
        await add_post(
            channel_id=ch_id,
            user_id=12345,
            session_id=session_a,
            msg_type="photo",
            publish_at=last_iso,
            file_id=f"file_id_{i}",
            caption="Тестовая подпись 💰"
        )

    count = await get_pending_posts_count(ch_id)
    assert count == 4, f"Expected 4, got {count}"
    print(f"Successfully staged 4 posts. Pending count = {count}")

    # 4. Cancel session A
    cancelled = await cancel_session_posts(session_a)
    assert cancelled == 4
    count_after_cancel = await get_pending_posts_count(ch_id)
    assert count_after_cancel == 0
    print(f"Successfully cancelled session posts. Remaining = {count_after_cancel}")

    # 5. Add 2 permanent posts under session B
    session_b = "session_test_b"
    t1, _ = calculate_next_slot(["15:00", "18:00"], None)
    t2, _ = calculate_next_slot(["15:00", "18:00"], t1.isoformat())
    await add_post(ch_id, 12345, session_b, "photo", t1.isoformat(), "fid_1", "Post 1")
    await add_post(ch_id, 12345, session_b, "video", t2.isoformat(), "fid_2", "Post 2")

    last_time = await get_last_scheduled_post_time(ch_id)
    assert last_time == t2.isoformat()
    print(f"Verified last post time in queue: {last_time}")

    # Cleanup test db
    if os.path.exists("tg_autoposter/data/test_bot.sqlite"):
        os.remove("tg_autoposter/data/test_bot.sqlite")
    print("✓ Database tests passed!")


if __name__ == "__main__":
    test_parsing()
    test_consecutive_slots()
    asyncio.run(test_database_lifecycle())
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")
