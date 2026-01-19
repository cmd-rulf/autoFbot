"""
MongoDB Database Operations for storing user sessions and channel configurations.
"""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, mongo_uri: str):
        self.client = AsyncIOMotorClient(mongo_uri)
        self.db = self.client["auto_forward_bot"]
        self.users = self.db["users"]
        self.channels = self.db["channels"]
        self.forward_tasks = self.db["forward_tasks"]
        self.user_settings = self.db["user_settings"]
    
    async def init_indexes(self):
        """Create database indexes for better performance."""
        await self.users.create_index("user_id", unique=True)
        await self.channels.create_index("channel_id", unique=True)
        await self.forward_tasks.create_index([("source_channel", 1), ("user_id", 1)])
        await self.user_settings.create_index("user_id", unique=True)
        logger.info("Database indexes created successfully")
    
    # ==================== User Settings (Destination Channel) ====================
    
    async def set_destination(self, user_id: int, destination_channel_id: int) -> bool:
        """Set user's default destination channel."""
        try:
            await self.user_settings.update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id, "destination_channel_id": destination_channel_id}},
                upsert=True
            )
            logger.info(f"Destination set for user {user_id}: {destination_channel_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting destination: {e}")
            return False
    
    async def get_destination(self, user_id: int) -> int:
        """Get user's default destination channel. Returns 0 if not set."""
        try:
            settings = await self.user_settings.find_one({"user_id": user_id})
            if settings:
                return settings.get("destination_channel_id", 0)
            return 0
        except Exception as e:
            logger.error(f"Error getting destination: {e}")
            return 0
    
    async def clear_destination(self, user_id: int) -> bool:
        """Clear user's default destination channel."""
        try:
            await self.user_settings.delete_one({"user_id": user_id})
            return True
        except Exception as e:
            logger.error(f"Error clearing destination: {e}")
            return False
    
    # ==================== User Session Management ====================
    
    async def save_user_session(self, user_id: int, phone_number: str, session_string: str) -> bool:
        """Save user session to database."""
        try:
            await self.users.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "user_id": user_id,
                        "phone_number": phone_number,
                        "session_string": session_string,
                        "is_active": True
                    }
                },
                upsert=True
            )
            logger.info(f"User session saved for user_id: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving user session: {e}")
            return False
    
    async def get_user_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user session from database."""
        try:
            user = await self.users.find_one({"user_id": user_id, "is_active": True})
            return user
        except Exception as e:
            logger.error(f"Error getting user session: {e}")
            return None
    
    async def delete_user_session(self, user_id: int) -> bool:
        """Delete/deactivate user session."""
        try:
            await self.users.update_one(
                {"user_id": user_id},
                {"$set": {"is_active": False}}
            )
            logger.info(f"User session deleted for user_id: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting user session: {e}")
            return False
    
    async def get_all_active_users(self) -> List[Dict[str, Any]]:
        """Get all active user sessions."""
        try:
            users = await self.users.find({"is_active": True}).to_list(None)
            return users
        except Exception as e:
            logger.error(f"Error getting active users: {e}")
            return []
    
    # ==================== Channel Configuration ====================
    
    async def add_channel_config(
        self,
        source_channel_id: int,
        destination_channel_id: int,
        user_id: int,
        use_bot: bool = True,
        last_message_id: int = 0
    ) -> bool:
        """Add or update channel forwarding configuration."""
        try:
            await self.channels.update_one(
                {"source_channel_id": source_channel_id, "user_id": user_id},
                {
                    "$set": {
                        "source_channel_id": source_channel_id,
                        "destination_channel_id": destination_channel_id,
                        "user_id": user_id,
                        "use_bot": use_bot,
                        "last_message_id": last_message_id,
                        "is_active": True
                    }
                },
                upsert=True
            )
            logger.info(f"Channel config added: {source_channel_id} -> {destination_channel_id}")
            return True
        except Exception as e:
            logger.error(f"Error adding channel config: {e}")
            return False
    
    async def get_channel_config(self, source_channel_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Get channel configuration."""
        try:
            config = await self.channels.find_one({
                "source_channel_id": source_channel_id,
                "user_id": user_id,
                "is_active": True
            })
            return config
        except Exception as e:
            logger.error(f"Error getting channel config: {e}")
            return None
    
    async def get_all_channel_configs(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all active channel configurations for a user."""
        try:
            configs = await self.channels.find({
                "user_id": user_id,
                "is_active": True
            }).to_list(None)
            return configs
        except Exception as e:
            logger.error(f"Error getting channel configs: {e}")
            return []
    
    async def update_last_message_id(self, source_channel_id: int, user_id: int, message_id: int) -> bool:
        """Update the last forwarded message ID."""
        try:
            await self.channels.update_one(
                {"source_channel_id": source_channel_id, "user_id": user_id},
                {"$set": {"last_message_id": message_id}}
            )
            return True
        except Exception as e:
            logger.error(f"Error updating last message ID: {e}")
            return False
    
    async def remove_channel_config(self, source_channel_id: int, user_id: int) -> bool:
        """Remove channel configuration."""
        try:
            await self.channels.update_one(
                {"source_channel_id": source_channel_id, "user_id": user_id},
                {"$set": {"is_active": False}}
            )
            logger.info(f"Channel config removed: {source_channel_id}")
            return True
        except Exception as e:
            logger.error(f"Error removing channel config: {e}")
            return False
    
    # ==================== Forward Task Tracking ====================
    
    async def save_forward_task(
        self,
        user_id: int,
        source_channel: int,
        destination_channel: int,
        total_messages: int,
        forwarded_count: int = 0,
        status: str = "pending"
    ) -> Optional[str]:
        """Save a forward task for tracking progress."""
        try:
            result = await self.forward_tasks.insert_one({
                "user_id": user_id,
                "source_channel": source_channel,
                "destination_channel": destination_channel,
                "total_messages": total_messages,
                "forwarded_count": forwarded_count,
                "status": status  # pending, in_progress, completed, failed
            })
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error saving forward task: {e}")
            return None
    
    async def update_forward_task(self, task_id: str, forwarded_count: int, status: str):
        """Update forward task progress."""
        from bson import ObjectId
        try:
            await self.forward_tasks.update_one(
                {"_id": ObjectId(task_id)},
                {"$set": {"forwarded_count": forwarded_count, "status": status}}
            )
        except Exception as e:
            logger.error(f"Error updating forward task: {e}")
    
    async def close(self):
        """Close database connection."""
        self.client.close()
        logger.info("Database connection closed")
