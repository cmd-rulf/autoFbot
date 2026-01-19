"""
Message Forwarder - Handles cloning messages without the forwarded tag.
Uses copy_message to avoid the "Forwarded from" label.
"""

import asyncio
import re
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, ChatForwardsRestricted, RPCError
from pyrogram.enums import ChatType, ChatMemberStatus
from typing import Optional, Tuple, Callable, List, Union
import logging

logger = logging.getLogger(__name__)


def parse_telegram_link(link: str) -> Tuple[Optional[Union[int, str]], Optional[int]]:
    """
    Parse a Telegram message link to extract channel ID/username and message ID.
    
    Supports formats:
    - https://t.me/channelname/123
    - https://t.me/c/1234567890/123
    - t.me/channelname/123
    - @channelname/123
    - -100123456789/123
    
    Returns: (channel_id_or_username, message_id) or (None, None) if invalid
    """
    link = link.strip()
    
    # Pattern for t.me/c/CHANNEL_ID/MESSAGE_ID (private channels)
    private_match = re.match(r'(?:https?://)?t\.me/c/(\d+)/(\d+)', link)
    if private_match:
        channel_id = int(f"-100{private_match.group(1)}")
        message_id = int(private_match.group(2))
        return channel_id, message_id
    
    # Pattern for t.me/USERNAME/MESSAGE_ID (public channels)
    public_match = re.match(r'(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,30}[a-zA-Z0-9])/(\d+)', link)
    if public_match:
        username = public_match.group(1)
        message_id = int(public_match.group(2))
        return username, message_id
    
    # Pattern for @username/MESSAGE_ID
    at_match = re.match(r'@([a-zA-Z][a-zA-Z0-9_]{3,30}[a-zA-Z0-9])/(\d+)', link)
    if at_match:
        username = at_match.group(1)
        message_id = int(at_match.group(2))
        return username, message_id
    
    # Pattern for CHANNEL_ID/MESSAGE_ID (direct IDs)
    id_match = re.match(r'(-?\d+)/(\d+)', link)
    if id_match:
        channel_id = int(id_match.group(1))
        message_id = int(id_match.group(2))
        return channel_id, message_id
    
    # Try parsing as just a number (message ID only)
    try:
        return None, int(link)
    except ValueError:
        pass
    
    return None, None


class MessageForwarder:
    def __init__(self, db):
        self.db = db
        self.delay_between_messages = 1.5  # Delay to avoid FloodWait
    
    async def resolve_channel(self, client: Client, channel_ref: Union[int, str]) -> Tuple[Optional[int], str]:
        """
        Resolve a channel reference (ID or username) to a channel ID.
        
        Returns: (channel_id, error_message)
        """
        try:
            chat = await client.get_chat(channel_ref)
            return chat.id, ""
        except Exception as e:
            return None, f"❌ Could not resolve channel: {str(e)}"
    
    async def check_channel_access(
        self,
        client: Client,
        channel_id: int
    ) -> Tuple[bool, str]:
        """
        Check if the client can access the channel.
        
        Returns: (can_access, error_message)
        """
        try:
            chat = await client.get_chat(channel_id)
            
            # Check if forwarding is allowed
            if chat.has_protected_content:
                return False, "❌ This channel has protected content. Forwarding is not allowed."
            
            return True, ""
                
        except RPCError as e:
            logger.error(f"Error checking channel access: {e}")
            return False, f"❌ Cannot access channel: {str(e)}"
    
    async def check_post_permission(
        self,
        client: Client,
        channel_id: int
    ) -> Tuple[bool, str]:
        """
        Check if the client can post to the channel.
        
        Returns: (can_post, error_message)
        """
        try:
            chat = await client.get_chat(channel_id)
            
            try:
                member = await client.get_chat_member(channel_id, "me")
                if member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]:
                    if member.status == ChatMemberStatus.OWNER:
                        return True, ""
                    if member.privileges and member.privileges.can_post_messages:
                        return True, ""
                    return False, "❌ No permission to post in this channel."
                return False, "❌ Not an admin in this channel."
            except Exception:
                return False, "❌ Not a member of this channel."
                
        except RPCError as e:
            logger.error(f"Error checking post permission: {e}")
            return False, f"❌ Error accessing channel: {str(e)}"
    
    async def copy_message(
        self,
        client: Client,
        source_chat_id: int,
        destination_chat_id: int,
        message_id: int
    ) -> Tuple[bool, Optional[int], str]:
        """
        Copy a single message without the forwarded tag.
        
        Returns: (success, new_message_id, error_message)
        """
        try:
            # Use copy_message to avoid "Forwarded from" tag
            copied = await client.copy_message(
                chat_id=destination_chat_id,
                from_chat_id=source_chat_id,
                message_id=message_id
            )
            return True, copied.id, ""
            
        except ChatForwardsRestricted:
            return False, None, "forwarding_restricted"
        except FloodWait as e:
            logger.warning(f"FloodWait: sleeping for {e.value} seconds")
            await asyncio.sleep(e.value)
            # Retry after waiting
            return await self.copy_message(client, source_chat_id, destination_chat_id, message_id)
        except RPCError as e:
            error_msg = str(e)
            logger.error(f"Error copying message {message_id}: {error_msg}")
            return False, None, error_msg
    
    async def clone_channel(
        self,
        client: Client,
        source_channel_id: int,
        destination_channel_id: int,
        user_id: int,
        progress_callback: Optional[Callable] = None,
        start_from_message_id: int = 0,
        limit: Optional[int] = None
    ) -> dict:
        """
        Clone all messages from source channel to destination channel.
        Messages are copied without the "Forwarded from" tag.
        Uses the same client for source and destination.
        
        Args:
            client: Pyrogram client (must have access to both channels)
            source_channel_id: Source channel ID
            destination_channel_id: Destination channel ID  
            user_id: User ID who initiated the clone
            progress_callback: Optional callback for progress updates
            start_from_message_id: Start from this message ID (for resuming)
            limit: Optional limit on number of messages to clone
            
        Returns: Statistics dictionary
        """
        stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": []
        }
        
        try:
            # Get all messages from the channel
            messages: List[Message] = []
            
            async for message in client.get_chat_history(
                source_channel_id,
                limit=limit
            ):
                if start_from_message_id and message.id <= start_from_message_id:
                    continue
                messages.append(message)
            
            # Reverse to process oldest first
            messages.reverse()
            stats["total"] = len(messages)
            
            if progress_callback:
                await progress_callback(f"📊 Found {stats['total']} messages to clone...")
            
            # Process messages
            for i, message in enumerate(messages):
                # Skip empty messages or service messages
                if message.empty or message.service:
                    stats["skipped"] += 1
                    continue
                
                success, new_id, error = await self.copy_message(
                    client,
                    source_channel_id,
                    destination_channel_id,
                    message.id
                )
                
                if success:
                    stats["success"] += 1
                    # Update last message ID in database
                    await self.db.update_last_message_id(
                        source_channel_id, 
                        user_id, 
                        message.id
                    )
                elif error == "forwarding_restricted":
                    stats["failed"] += 1
                    return {
                        **stats,
                        "error": "❌ Channel has forwarding restricted. Cannot clone.",
                        "aborted": True
                    }
                else:
                    stats["failed"] += 1
                    if len(stats["errors"]) < 5:
                        stats["errors"].append(f"Message {message.id}: {error}")
                
                # Progress update every 10 messages
                if progress_callback and (i + 1) % 10 == 0:
                    await progress_callback(
                        f"📤 Progress: {i + 1}/{stats['total']} "
                        f"(✅ {stats['success']} | ❌ {stats['failed']} | ⏭️ {stats['skipped']})"
                    )
                
                # Delay to avoid FloodWait
                await asyncio.sleep(self.delay_between_messages)
            
            return stats
            
        except ChatForwardsRestricted:
            return {
                **stats,
                "error": "❌ This channel has restricted forwarding/copying. Cannot clone.",
                "aborted": True
            }
        except Exception as e:
            logger.error(f"Error cloning channel: {e}")
            return {
                **stats,
                "error": f"❌ Error: {str(e)}",
                "aborted": True
            }
    
    async def clone_range(
        self,
        client: Client,
        source_channel_id: int,
        destination_channel_id: int,
        start_message_id: int,
        end_message_id: int,
        user_id: int,
        progress_callback: Optional[Callable] = None
    ) -> dict:
        """
        Clone messages from a specific range (start_id to end_id inclusive).
        Messages are copied without the "Forwarded from" tag.
        
        Args:
            client: Pyrogram client (must have access to both channels)
            source_channel_id: Source channel ID
            destination_channel_id: Destination channel ID
            start_message_id: First message ID to clone
            end_message_id: Last message ID to clone
            user_id: User ID who initiated the clone
            progress_callback: Optional callback for progress updates
            
        Returns: Statistics dictionary
        """
        stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
            "start_id": start_message_id,
            "end_id": end_message_id
        }
        
        # Ensure start <= end
        if start_message_id > end_message_id:
            start_message_id, end_message_id = end_message_id, start_message_id
        
        try:
            # Get messages in the range
            messages: List[Message] = []
            
            if progress_callback:
                await progress_callback(f"🔍 Fetching messages from {start_message_id} to {end_message_id}...")
            
            # Fetch messages - we need to iterate through history
            async for message in client.get_chat_history(
                source_channel_id,
                offset_id=end_message_id + 1,
                limit=end_message_id - start_message_id + 100
            ):
                if message.id < start_message_id:
                    break
                if message.id <= end_message_id:
                    messages.append(message)
            
            # Reverse to process oldest first
            messages.reverse()
            stats["total"] = len(messages)
            
            if stats["total"] == 0:
                return {
                    **stats,
                    "error": "❌ No messages found in the specified range.",
                    "aborted": True
                }
            
            if progress_callback:
                await progress_callback(f"📊 Found {stats['total']} messages to clone (IDs {start_message_id} - {end_message_id})...")
            
            # Process messages
            for i, message in enumerate(messages):
                # Skip empty messages or service messages
                if message.empty or message.service:
                    stats["skipped"] += 1
                    continue
                
                success, new_id, error = await self.copy_message(
                    client,
                    source_channel_id,
                    destination_channel_id,
                    message.id
                )
                
                if success:
                    stats["success"] += 1
                elif error == "forwarding_restricted":
                    stats["failed"] += 1
                    return {
                        **stats,
                        "error": "❌ Channel has forwarding restricted. Cannot clone.",
                        "aborted": True
                    }
                else:
                    stats["failed"] += 1
                    if len(stats["errors"]) < 5:
                        stats["errors"].append(f"Message {message.id}: {error}")
                
                # Progress update every 10 messages
                if progress_callback and (i + 1) % 10 == 0:
                    await progress_callback(
                        f"📤 Progress: {i + 1}/{stats['total']} "
                        f"(✅ {stats['success']} | ❌ {stats['failed']} | ⏭️ {stats['skipped']})"
                    )
                
                # Delay to avoid FloodWait
                await asyncio.sleep(self.delay_between_messages)
            
            return stats
            
        except ChatForwardsRestricted:
            return {
                **stats,
                "error": "❌ This channel has restricted forwarding/copying. Cannot clone.",
                "aborted": True
            }
        except Exception as e:
            logger.error(f"Error cloning range: {e}")
            return {
                **stats,
                "error": f"❌ Error: {str(e)}",
                "aborted": True
            }
