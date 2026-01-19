"""
User Client Management for handling user login with phone number.
This is used when the bot is not an admin in the source channel.
"""

import asyncio
from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    FloodWait,
    BadRequest
)
from typing import Optional, Callable, Dict
import logging

logger = logging.getLogger(__name__)


class UserClientManager:
    def __init__(self, api_id: int, api_hash: str, db):
        self.api_id = api_id
        self.api_hash = api_hash
        self.db = db
        self.active_clients: Dict[int, Client] = {}
        self.pending_logins: Dict[int, dict] = {}  # Store pending login states
    
    async def start_client_from_session(self, user_id: int, session_string: str) -> Optional[Client]:
        """Start a client from an existing session string."""
        try:
            client = Client(
                name=f"user_{user_id}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                session_string=session_string,
                in_memory=True
            )
            await client.start()
            self.active_clients[user_id] = client
            logger.info(f"User client started for user_id: {user_id}")
            return client
        except Exception as e:
            logger.error(f"Error starting client from session: {e}")
            return None
    
    async def initiate_login(self, user_id: int, phone_number: str) -> dict:
        """
        Initiate phone login process.
        Returns status and next step instructions.
        """
        try:
            client = Client(
                name=f"user_{user_id}_temp",
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True
            )
            await client.connect()
            
            sent_code = await client.send_code(phone_number)
            
            # Store pending login state
            self.pending_logins[user_id] = {
                "client": client,
                "phone_number": phone_number,
                "phone_code_hash": sent_code.phone_code_hash,
                "step": "otp"
            }
            
            return {
                "success": True,
                "step": "otp",
                "message": f"OTP sent to {phone_number}. Please enter the OTP code."
            }
            
        except FloodWait as e:
            return {
                "success": False,
                "error": f"Too many attempts. Please wait {e.value} seconds."
            }
        except Exception as e:
            logger.error(f"Error initiating login: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def verify_otp(self, user_id: int, otp_code: str) -> dict:
        """Verify the OTP code."""
        if user_id not in self.pending_logins:
            return {
                "success": False,
                "error": "No pending login found. Please start with /login first."
            }
        
        login_data = self.pending_logins[user_id]
        client = login_data["client"]
        phone_number = login_data["phone_number"]
        phone_code_hash = login_data["phone_code_hash"]
        
        try:
            await client.sign_in(
                phone_number=phone_number,
                phone_code_hash=phone_code_hash,
                phone_code=otp_code
            )
            
            # Login successful, export session string
            session_string = await client.export_session_string()
            
            # Save to database
            await self.db.save_user_session(user_id, phone_number, session_string)
            
            # Store active client
            self.active_clients[user_id] = client
            
            # Clear pending login
            del self.pending_logins[user_id]
            
            return {
                "success": True,
                "message": "Login successful! You can now use the bot to forward from channels where you have access."
            }
            
        except SessionPasswordNeeded:
            # 2FA is enabled
            self.pending_logins[user_id]["step"] = "2fa"
            return {
                "success": True,
                "step": "2fa",
                "message": "Two-factor authentication is enabled. Please enter your 2FA password."
            }
        except PhoneCodeInvalid:
            return {
                "success": False,
                "error": "Invalid OTP code. Please try again."
            }
        except PhoneCodeExpired:
            del self.pending_logins[user_id]
            return {
                "success": False,
                "error": "OTP code expired. Please start login again with /login."
            }
        except Exception as e:
            logger.error(f"Error verifying OTP: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def verify_2fa(self, user_id: int, password: str) -> dict:
        """Verify 2FA password."""
        if user_id not in self.pending_logins:
            return {
                "success": False,
                "error": "No pending login found."
            }
        
        login_data = self.pending_logins[user_id]
        if login_data.get("step") != "2fa":
            return {
                "success": False,
                "error": "2FA not required at this step."
            }
        
        client = login_data["client"]
        phone_number = login_data["phone_number"]
        
        try:
            await client.check_password(password)
            
            # Login successful, export session string
            session_string = await client.export_session_string()
            
            # Save to database
            await self.db.save_user_session(user_id, phone_number, session_string)
            
            # Store active client
            self.active_clients[user_id] = client
            
            # Clear pending login
            del self.pending_logins[user_id]
            
            return {
                "success": True,
                "message": "Login successful! You can now use the bot to forward from channels."
            }
            
        except BadRequest as e:
            return {
                "success": False,
                "error": "Invalid 2FA password. Please try again."
            }
        except Exception as e:
            logger.error(f"Error verifying 2FA: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_client(self, user_id: int) -> Optional[Client]:
        """Get an active client for a user, or try to restore from database."""
        # Check if already active
        if user_id in self.active_clients:
            client = self.active_clients[user_id]
            if client.is_connected:
                return client
        
        # Try to restore from database
        user_data = await self.db.get_user_session(user_id)
        if user_data and user_data.get("session_string"):
            client = await self.start_client_from_session(
                user_id, 
                user_data["session_string"]
            )
            return client
        
        return None
    
    async def logout(self, user_id: int) -> dict:
        """Logout and remove user session."""
        try:
            # Stop active client if exists
            if user_id in self.active_clients:
                client = self.active_clients[user_id]
                await client.stop()
                del self.active_clients[user_id]
            
            # Remove from database
            await self.db.delete_user_session(user_id)
            
            return {
                "success": True,
                "message": "Logged out successfully."
            }
        except Exception as e:
            logger.error(f"Error logging out: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def cancel_login(self, user_id: int):
        """Cancel pending login."""
        if user_id in self.pending_logins:
            client = self.pending_logins[user_id]["client"]
            await client.disconnect()
            del self.pending_logins[user_id]
    
    async def stop_all_clients(self):
        """Stop all active user clients."""
        for user_id, client in list(self.active_clients.items()):
            try:
                await client.stop()
            except Exception as e:
                logger.error(f"Error stopping client for {user_id}: {e}")
        self.active_clients.clear()
        
        # Also cleanup pending logins
        for user_id, data in list(self.pending_logins.items()):
            try:
                await data["client"].disconnect()
            except:
                pass
        self.pending_logins.clear()
