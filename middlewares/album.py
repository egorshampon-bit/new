import asyncio
from typing import Any, Awaitable, Callable, Dict, List
from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    """
    Groups messages belonging to the same media group (album) into a single handler call.
    Places `album: List[Message]` into data.
    """
    def __init__(self, latency: float = 0.6):
        super().__init__()
        self.latency = latency
        self.albums: Dict[str, List[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        media_group_id = event.media_group_id

        # Not part of an album - proceed normally
        if not media_group_id:
            return await handler(event, data)

        if media_group_id in self.albums:
            # Group is already being collected, append message and return None
            self.albums[media_group_id].append(event)
            return None

        # First message of this album
        self.albums[media_group_id] = [event]
        await asyncio.sleep(self.latency)

        # Retrieve all collected messages for this album
        messages = self.albums.pop(media_group_id, [])
        if not messages:
            return None

        # Sort messages by message_id so order is preserved
        messages.sort(key=lambda m: m.message_id)
        data["album"] = messages

        # Call handler with the first message as event, and album in data
        return await handler(messages[0], data)
