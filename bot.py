"""
Auto-Forward Telegram Bot
- Clones entire channels without the "Forwarded from" tag
- Uses bot account if admin, otherwise uses user login via phone number
- Stores user sessions in MongoDB
"""

import asyncio
import logging
import os
import sys
from aiohttp import web
from dotenv import load_dotenv

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import RPCError

from database import Database
from user_client import UserClientManager
from forwarder import MessageForwarder, parse_telegram_link

# Load environment variables
load_dotenv("config.env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_URI = os.getenv("MONGO_URI", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.getenv("PORT", 8080))

# Validate configuration
if not all([API_ID, API_HASH, BOT_TOKEN, MONGO_URI]):
    logger.error("Missing required environment variables!")
    sys.exit(1)


# ==================== Health Check Server ====================

async def health_handler(request):
    """Health check endpoint for hosting platforms."""
    return web.Response(text="OK", status=200)


async def start_health_server():
    """Start a simple HTTP server for health checks."""
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health check server started on port {PORT}")
    return runner

# Initialize components
db = Database(MONGO_URI)
user_manager = UserClientManager(API_ID, API_HASH, db)
forwarder = MessageForwarder(db)

# Store active cloning tasks
active_tasks = {}


# ==================== Helper Functions ====================

def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return user_id == ADMIN_ID


async def get_working_client(bot: Client, user_id: int) -> tuple:
    """
    Get a working client for the user.
    Returns: (client, is_bot, error_message)
    """
    # First, try to get user's logged-in client
    user_client = await user_manager.get_client(user_id)
    if user_client:
        return user_client, False, None
    
    # If no user client, use the bot
    return bot, True, None


# ==================== Command Handlers ====================

async def start_command(client: Client, message: Message):
    """Handle /start command."""
    logger.info(f"/start from {message.from_user.id}")
    welcome_text = """
🤖 **Auto-Forward Bot**

Welcome! I can help you clone Telegram channels without the "Forwarded from" tag.

**Quick Start:**
1️⃣ `/login +1234567890` - Login with your phone
2️⃣ `/setdest -100xxxxx` - Set destination channel  
3️⃣ `/clone -100xxxxx` or `/crange <link> <link>` - Clone!

**Commands:**
• `/login <phone>` - Login with your phone number
• `/setdest <channel_id>` - Set destination channel
• `/getdest` - Show current destination
• `/clone <source_id>` - Clone entire channel
• `/crange <start_link> <end_link>` - Clone range
• `/status` - Check login status
• `/logout` - Logout
• `/cancel` - Cancel ongoing task
• `/help` - Detailed help

**Note:** Your logged-in account is used for everything.
    """
    await message.reply_text(welcome_text)


async def help_command(client: Client, message: Message):
    """Handle /help command."""
    logger.info(f"/help from {message.from_user.id}")
    help_text = """
📚 **Detailed Help**

**Step 1: Login**
`/login +1234567890`
Login with your Telegram phone number. Your account is used for cloning.

**Step 2: Set Destination**
`/setdest -100123456789`
Set where cloned messages go. You must be admin there.

**Step 3: Clone**
`/clone -100123456789` - Clone entire channel
`/crange <start_link> <end_link>` - Clone specific range

**Clone Range Examples:**
• `/crange https://t.me/channel/100 https://t.me/channel/200`
• `/crange https://t.me/c/1234567890/100 https://t.me/c/1234567890/200`

**Other Commands:**
• `/getdest` - Show current destination
• `/status` - Check login status
• `/logout` - Remove login session
• `/cancel` - Stop ongoing clone

**Get Channel ID:**
Forward any message from a channel to @RawDataBot

**How It Works:**
• Your logged-in account reads from source and writes to destination
• Messages are copied (not forwarded) - no "Forwarded from" tag
• Speed: ~1.5 seconds per message to avoid rate limits
    """
    await message.reply_text(help_text)


async def setdest_command(client: Client, message: Message):
    """Handle /setdest command - Set destination channel."""
    user_id = message.from_user.id
    logger.info(f"/setdest from {user_id}")
    
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text(
            "**Set Destination Channel**\n\n"
            "Usage: `/setdest -100123456789`\n\n"
            "You must be an admin in the destination channel with posting permission.\n\n"
            "**Get channel ID:** Forward any message from the channel to @RawDataBot"
        )
        return
    
    # Check if user is logged in
    user_client = await user_manager.get_client(user_id)
    if not user_client:
        await message.reply_text(
            "❌ **Please login first!**\n\n"
            "Use `/login +1234567890` to login with your phone number."
        )
        return
    
    try:
        dest_id = int(args[1])
    except ValueError:
        await message.reply_text("❌ Invalid channel ID. Must be a number starting with -100")
        return
    
    # Verify user can post there
    can_post, error = await forwarder.check_post_permission(user_client, dest_id)
    
    if not can_post:
        await message.reply_text(
            f"❌ Cannot use this channel as destination.\n\n"
            f"{error}\n\n"
            "Make sure you are an admin with post permission."
        )
        return
    
    # Get channel title for confirmation
    try:
        chat = await user_client.get_chat(dest_id)
        channel_title = chat.title
    except:
        channel_title = str(dest_id)
    
    # Save to database
    success = await db.set_destination(user_id, dest_id)
    
    if success:
        await message.reply_text(
            f"✅ **Destination Set!**\n\n"
            f"📥 Channel: {channel_title}\n"
            f"🆔 ID: `{dest_id}`\n\n"
            "All your cloned messages will be sent here."
        )
    else:
        await message.reply_text("❌ Failed to save destination. Please try again.")


async def getdest_command(client: Client, message: Message):
    """Handle /getdest command - Show current destination."""
    user_id = message.from_user.id
    logger.info(f"/getdest from {user_id}")
    
    dest_id = await db.get_destination(user_id)
    
    if dest_id:
        try:
            chat = await client.get_chat(dest_id)
            await message.reply_text(
                f"📥 **Current Destination**\n\n"
                f"Channel: {chat.title}\n"
                f"ID: `{dest_id}`\n\n"
                "Use `/setdest <new_id>` to change."
            )
        except:
            await message.reply_text(
                f"📥 **Current Destination**\n\n"
                f"ID: `{dest_id}`\n"
                f"⚠️ Could not get channel info (bot may have been removed)\n\n"
                "Use `/setdest <new_id>` to change."
            )
    else:
        await message.reply_text(
            "❌ **No destination set**\n\n"
            "Use `/setdest -100123456789` to set your destination channel."
        )


async def login_command(client: Client, message: Message):
    """Handle /login command."""
    user_id = message.from_user.id
    logger.info(f"/login from {user_id}")
    
    # Check if already logged in
    existing = await user_manager.get_client(user_id)
    if existing:
        await message.reply_text("✅ You're already logged in! Use `/logout` to logout first.")
        return
    
    # Parse phone number
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text(
            "📱 **Login with Phone Number**\n\n"
            "Usage: `/login +1234567890`\n\n"
            "Include the country code (e.g., +1 for US, +91 for India)"
        )
        return
    
    phone_number = args[1].strip()
    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number
    
    await message.reply_text(f"📲 Sending OTP to {phone_number}...")
    
    result = await user_manager.initiate_login(user_id, phone_number)
    
    if result["success"]:
        await message.reply_text(
            f"✅ {result['message']}\n\n"
            "Please reply with the OTP code you received.\n"
            "Format: `/otp 12345`"
        )
    else:
        await message.reply_text(f"❌ {result.get('error', 'Unknown error')}")


async def otp_command(client: Client, message: Message):
    """Handle /otp command for OTP verification."""
    user_id = message.from_user.id
    logger.info(f"/otp from {user_id}")
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Usage: `/otp 12345`")
        return
    
    otp_code = args[1].strip().replace(" ", "")
    
    result = await user_manager.verify_otp(user_id, otp_code)
    
    if result["success"]:
        if result.get("step") == "2fa":
            await message.reply_text(
                f"🔐 {result['message']}\n\n"
                "Reply with: `/2fa your_password`"
            )
        else:
            await message.reply_text(f"✅ {result['message']}")
    else:
        await message.reply_text(f"❌ {result.get('error', 'Unknown error')}")


async def twofa_command(client: Client, message: Message):
    """Handle /2fa command for 2FA verification."""
    user_id = message.from_user.id
    logger.info(f"/2fa from {user_id}")
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Usage: `/2fa your_password`")
        return
    
    password = args[1].strip()
    
    # Delete the message containing password for security
    try:
        await message.delete()
    except:
        pass
    
    result = await user_manager.verify_2fa(user_id, password)
    
    if result["success"]:
        await client.send_message(user_id, f"✅ {result['message']}")
    else:
        await client.send_message(user_id, f"❌ {result.get('error', 'Unknown error')}")


async def logout_command(client: Client, message: Message):
    """Handle /logout command."""
    user_id = message.from_user.id
    logger.info(f"/logout from {user_id}")
    
    result = await user_manager.logout(user_id)
    
    if result["success"]:
        await message.reply_text(f"✅ {result['message']}")
    else:
        await message.reply_text(f"❌ {result.get('error', 'Unknown error')}")


async def status_command(client: Client, message: Message):
    """Handle /status command."""
    user_id = message.from_user.id
    logger.info(f"/status from {user_id}")
    
    user_client = await user_manager.get_client(user_id)
    
    if user_client:
        try:
            me = await user_client.get_me()
            await message.reply_text(
                f"✅ **Logged in as:**\n"
                f"• Name: {me.first_name} {me.last_name or ''}\n"
                f"• Username: @{me.username or 'N/A'}\n"
                f"• User ID: `{me.id}`"
            )
        except Exception as e:
            await message.reply_text(f"⚠️ Session exists but error getting info: {e}")
    else:
        await message.reply_text(
            "❌ **Not logged in**\n\n"
            "Use `/login <phone>` to login with your phone number.\n"
            "The bot can still work if it's an admin in the source channel."
        )


async def clone_command(client: Client, message: Message):
    """Handle /clone command."""
    user_id = message.from_user.id
    logger.info(f"/clone from {user_id}")
    
    # Check if there's already an active task
    if user_id in active_tasks:
        await message.reply_text(
            "⚠️ You already have an active cloning task.\n"
            "Use `/cancel` to stop it first."
        )
        return
    
    # Check if user is logged in
    user_client = await user_manager.get_client(user_id)
    if not user_client:
        await message.reply_text(
            "❌ **Please login first!**\n\n"
            "Use `/login +1234567890` to login with your phone number.\n"
            "Your account will be used for cloning."
        )
        return
    
    # Parse arguments
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text(
            "**Clone Channel**\n\n"
            "Usage: `/clone <source_id>`\n\n"
            "Example: `/clone -100123456789`\n\n"
            "**First set destination:** `/setdest <channel_id>`"
        )
        return
    
    try:
        source_channel_id = int(args[1])
        destination_channel_id = await db.get_destination(user_id)
        
        if not destination_channel_id:
            await message.reply_text(
                "❌ **No destination set!**\n\n"
                "First set your destination channel:\n"
                "`/setdest -100123456789`"
            )
            return
            
    except ValueError:
        await message.reply_text("❌ Invalid channel ID. Channel IDs should be numbers.")
        return
    
    status_msg = await message.reply_text("🔍 Checking access...")
    
    # Check source channel access
    can_access, error = await forwarder.check_channel_access(user_client, source_channel_id)
    if not can_access:
        await status_msg.edit_text(error)
        return
    
    # Check destination channel access
    can_post, error = await forwarder.check_post_permission(user_client, destination_channel_id)
    if not can_post:
        await status_msg.edit_text(
            f"❌ **Cannot post to destination!**\n\n"
            f"{error}\n\n"
            "Make sure you are an admin in the destination channel."
        )
        return
    
    # Get channel info
    try:
        source_chat = await user_client.get_chat(source_channel_id)
        dest_chat = await user_client.get_chat(destination_channel_id)
    except Exception as e:
        await status_msg.edit_text(f"❌ Error getting channel info: {e}")
        return
    
    # Start cloning
    await status_msg.edit_text(
        f"🚀 **Starting Clone**\n\n"
        f"📤 Source: {source_chat.title}\n"
        f"📥 Destination: {dest_chat.title}\n"
        f"👤 Using: Your account\n\n"
        f"⏳ This may take a while..."
    )
    
    # Mark task as active
    active_tasks[user_id] = True
    
    async def progress_callback(text: str):
        """Update progress message."""
        if user_id not in active_tasks:
            return
        try:
            await status_msg.edit_text(text)
        except:
            pass
    
    # Perform cloning using user's client for everything
    stats = await forwarder.clone_channel(
        client=user_client,
        source_channel_id=source_channel_id,
        destination_channel_id=destination_channel_id,
        user_id=user_id,
        progress_callback=progress_callback
    )
    
    # Remove from active tasks
    active_tasks.pop(user_id, None)
    
    # Send final report
    if stats.get("aborted"):
        await status_msg.edit_text(
            f"❌ **Clone Aborted**\n\n"
            f"{stats.get('error', 'Unknown error')}\n\n"
            f"**Partial Stats:**\n"
            f"• Total found: {stats['total']}\n"
            f"• Cloned: {stats['success']}\n"
            f"• Failed: {stats['failed']}"
        )
    else:
        await status_msg.edit_text(
            f"✅ **Clone Complete!**\n\n"
            f"📤 Source: {source_chat.title}\n"
            f"📥 Destination: {dest_chat.title}\n\n"
            f"**Stats:**\n"
            f"• Total: {stats['total']}\n"
            f"• ✅ Cloned: {stats['success']}\n"
            f"• ❌ Failed: {stats['failed']}\n"
            f"• ⏭️ Skipped: {stats['skipped']}"
        )


async def crange_command(client: Client, message: Message):
    """Handle /crange command - Clone a range of messages using post links."""
    user_id = message.from_user.id
    logger.info(f"/crange from {user_id}")
    
    # Check if there's already an active task
    if user_id in active_tasks:
        await message.reply_text(
            "⚠️ You already have an active cloning task.\n"
            "Use `/cancel` to stop it first."
        )
        return
    
    # Check if user is logged in
    user_client = await user_manager.get_client(user_id)
    if not user_client:
        await message.reply_text(
            "❌ **Please login first!**\n\n"
            "Use `/login +1234567890` to login with your phone number.\n"
            "Your account will be used for cloning."
        )
        return
    
    # Parse arguments
    args = message.text.split()
    if len(args) < 3:
        await message.reply_text(
            "**Clone Range**\n\n"
            "Usage: `/crange <start_link> <end_link>`\n\n"
            "**Examples:**\n"
            "• `/crange https://t.me/channel/100 https://t.me/channel/500`\n"
            "• `/crange https://t.me/c/1234567890/100 https://t.me/c/1234567890/500`\n\n"
            "**First set destination:** `/setdest <channel_id>`"
        )
        return
    
    start_link = args[1]
    end_link = args[2]
    
    # Get destination from database
    destination_channel_id = await db.get_destination(user_id)
    
    if not destination_channel_id:
        await message.reply_text(
            "❌ **No destination set!**\n\n"
            "First set your destination channel:\n"
            "`/setdest -100123456789`"
        )
        return
    
    # Parse start link
    start_channel, start_message_id = parse_telegram_link(start_link)
    if start_channel is None or start_message_id is None:
        await message.reply_text(
            f"❌ Invalid start link: `{start_link}`\n\n"
            "Please use a valid Telegram message link."
        )
        return
    
    # Parse end link
    end_channel, end_message_id = parse_telegram_link(end_link)
    if end_channel is None or end_message_id is None:
        await message.reply_text(
            f"❌ Invalid end link: `{end_link}`\n\n"
            "Please use a valid Telegram message link."
        )
        return
    
    status_msg = await message.reply_text("🔍 Resolving channels...")
    
    # Resolve start channel to get ID
    if isinstance(start_channel, str):
        resolved_id, error = await forwarder.resolve_channel(user_client, start_channel)
        if error:
            await status_msg.edit_text(error)
            return
        start_channel = resolved_id
    
    # Resolve end channel to get ID
    if isinstance(end_channel, str):
        resolved_id, error = await forwarder.resolve_channel(user_client, end_channel)
        if error:
            await status_msg.edit_text(error)
            return
        end_channel = resolved_id
    
    # Both links should be from the same channel
    if start_channel != end_channel:
        await status_msg.edit_text(
            "❌ Start and end links must be from the **same channel**.\n\n"
            f"Start channel: `{start_channel}`\n"
            f"End channel: `{end_channel}`"
        )
        return
    
    source_channel_id = start_channel
    
    # Check source channel access
    can_access, error = await forwarder.check_channel_access(user_client, source_channel_id)
    if not can_access:
        await status_msg.edit_text(error)
        return
    
    # Check destination channel access
    can_post, error = await forwarder.check_post_permission(user_client, destination_channel_id)
    if not can_post:
        await status_msg.edit_text(
            f"❌ **Cannot post to destination!**\n\n"
            f"{error}\n\n"
            "Make sure you are an admin in the destination channel."
        )
        return
    
    # Get channel info
    try:
        source_chat = await user_client.get_chat(source_channel_id)
        dest_chat = await user_client.get_chat(destination_channel_id)
    except Exception as e:
        await status_msg.edit_text(f"❌ Error getting channel info: {e}")
        return
    
    # Ensure correct order
    if start_message_id > end_message_id:
        start_message_id, end_message_id = end_message_id, start_message_id
    
    # Start cloning
    await status_msg.edit_text(
        f"🚀 **Starting Range Clone**\n\n"
        f"📤 Source: {source_chat.title}\n"
        f"📥 Destination: {dest_chat.title}\n"
        f"📍 Range: {start_message_id} → {end_message_id}\n"
        f"👤 Using: Your account\n\n"
        f"⏳ This may take a while..."
    )
    
    # Mark task as active
    active_tasks[user_id] = True
    
    async def progress_callback(text: str):
        """Update progress message."""
        if user_id not in active_tasks:
            return
        try:
            await status_msg.edit_text(text)
        except:
            pass
    
    # Perform range cloning using user's client for everything
    stats = await forwarder.clone_range(
        client=user_client,
        source_channel_id=source_channel_id,
        destination_channel_id=destination_channel_id,
        start_message_id=start_message_id,
        end_message_id=end_message_id,
        user_id=user_id,
        progress_callback=progress_callback
    )
    
    # Remove from active tasks
    active_tasks.pop(user_id, None)
    
    # Send final report
    if stats.get("aborted"):
        await status_msg.edit_text(
            f"❌ **Clone Aborted**\n\n"
            f"{stats.get('error', 'Unknown error')}\n\n"
            f"**Partial Stats:**\n"
            f"• Range: {start_message_id} → {end_message_id}\n"
            f"• Total found: {stats['total']}\n"
            f"• Cloned: {stats['success']}\n"
            f"• Failed: {stats['failed']}"
        )
    else:
        await status_msg.edit_text(
            f"✅ **Range Clone Complete!**\n\n"
            f"📤 Source: {source_chat.title}\n"
            f"📥 Destination: {dest_chat.title}\n"
            f"📍 Range: {start_message_id} → {end_message_id}\n\n"
            f"**Stats:**\n"
            f"• Total: {stats['total']}\n"
            f"• ✅ Cloned: {stats['success']}\n"
            f"• ❌ Failed: {stats['failed']}\n"
            f"• ⏭️ Skipped: {stats['skipped']}"
        )


async def cancel_command(client: Client, message: Message):
    """Handle /cancel command."""
    user_id = message.from_user.id
    logger.info(f"/cancel from {user_id}")
    
    if user_id in active_tasks:
        del active_tasks[user_id]
        await message.reply_text("✅ Cloning task cancelled.")
    else:
        await message.reply_text("❌ No active cloning task to cancel.")


async def catch_all(client: Client, message: Message):
    """Catch all messages for debugging."""
    logger.info(f"Message from {message.from_user.id}: {message.text}")


# ==================== Main Entry Point ====================

async def main():
    """Main function to run the bot."""
    logger.info("Starting Auto-Forward Bot...")
    
    # Start health check server for hosting platforms
    health_runner = await start_health_server()
    
    # Initialize database
    await db.init_indexes()
    logger.info("Database initialized")
    
    # Create bot client with in_memory to avoid session file issues
    bot = Client(
        name="auto_forward_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )
    
    # Add handlers BEFORE starting
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            start_command,
            filters.command("start") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            help_command,
            filters.command("help") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            setdest_command,
            filters.command("setdest") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            getdest_command,
            filters.command("getdest") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            login_command,
            filters.command("login") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            otp_command,
            filters.command("otp") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            twofa_command,
            filters.command("2fa") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            logout_command,
            filters.command("logout") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            status_command,
            filters.command("status") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            clone_command,
            filters.command("clone") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            crange_command,
            filters.command("crange") & filters.private
        )
    )
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            cancel_command,
            filters.command("cancel") & filters.private
        )
    )
    # Debug catch-all handler (lowest priority)
    bot.add_handler(
        __import__('pyrogram.handlers', fromlist=['MessageHandler']).MessageHandler(
            catch_all,
            filters.private
        ),
        group=99
    )
    
    logger.info("Handlers registered")
    
    # Start the bot
    await bot.start()
    logger.info("Bot started successfully!")
    
    # Send startup notification to admin
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                "🤖 **Auto-Forward Bot Started!**\n\n"
                "I'm ready to clone channels.\n"
                "Use `/help` to see available commands."
            )
        except Exception as e:
            logger.warning(f"Could not send startup notification: {e}")
    
    # Keep the bot running
    logger.info("Bot is running. Press Ctrl+C to stop.")
    
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        # Cleanup
        logger.info("Shutting down...")
        await user_manager.stop_all_clients()
        await bot.stop()
        await db.close()
        await health_runner.cleanup()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
