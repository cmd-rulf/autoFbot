# Auto-Forward Telegram Bot

A powerful Telegram bot that clones entire channels **without the "Forwarded from" tag**. Messages appear as original content in the destination channel.

## Features

- 🔄 **Clone entire channels** - Copy all messages from source to destination
- 🏷️ **No forwarded tag** - Messages appear as original (uses `copy_message`)
- 🤖 **Bot or User mode** - Uses bot if admin, otherwise uses user login
- 📱 **Phone login** - Login with phone number + OTP stored in MongoDB
- 🔐 **2FA support** - Handles two-factor authentication
- 💾 **Session persistence** - User sessions stored in MongoDB
- 📊 **Progress tracking** - Real-time progress updates during cloning
- ⚡ **Flood protection** - Automatic handling of Telegram rate limits

## Requirements

- Python 3.9+
- MongoDB database
- Telegram API credentials
- Bot token from @BotFather

## Installation

1. **Clone or download this repository**

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   - Copy `config.env` and fill in your credentials:
   ```env
   API_ID=your_api_id          # From https://my.telegram.org
   API_HASH=your_api_hash      # From https://my.telegram.org
   BOT_TOKEN=your_bot_token    # From @BotFather
   MONGO_URI=mongodb+srv://... # Your MongoDB connection string
   ADMIN_ID=your_user_id       # Your Telegram user ID
   DESTINATION_CHANNEL_ID=-100xxx  # Default destination channel
   ```

4. **Run the bot:**
   ```bash
   python bot.py
   ```

## Getting Credentials

### Telegram API (API_ID & API_HASH)
1. Go to https://my.telegram.org
2. Log in with your phone number
3. Go to "API development tools"
4. Create a new application
5. Copy `api_id` and `api_hash`

### Bot Token
1. Message @BotFather on Telegram
2. Send `/newbot` and follow instructions
3. Copy the token provided

### MongoDB
1. Create a free cluster at https://cloud.mongodb.com
2. Create a database user
3. Get the connection string (replace password)

### Your User ID
- Forward any message to @userinfobot or @RawDataBot

### Channel IDs
- Channels IDs start with `-100`
- Forward a message from the channel to @RawDataBot

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and quick guide |
| `/help` | Detailed help and instructions |
| `/login <phone>` | Login with phone number (e.g., `/login +1234567890`) |
| `/otp <code>` | Enter OTP code received |
| `/2fa <password>` | Enter 2FA password if enabled |
| `/logout` | Logout and remove session |
| `/status` | Check login status |
| `/clone <source> [dest]` | Clone a channel |
| `/cancel` | Cancel ongoing clone task |

## Usage Examples

### Clone with default destination:
```
/clone -100123456789
```

### Clone to specific destination:
```
/clone -100123456789 -100987654321
```

### Login flow:
```
/login +1234567890
/otp 12345
/2fa mypassword  (if 2FA enabled)
```

## How It Works

1. **Permission Check:**
   - Checks if bot is admin in source channel
   - If not, checks for user login session
   - Verifies forwarding is allowed (no protected content)

2. **Cloning Process:**
   - Uses `copy_message` API to copy without forward tag
   - Processes messages from oldest to newest
   - Handles media groups properly
   - Respects Telegram rate limits

3. **Session Management:**
   - Phone login creates a session string
   - Sessions stored encrypted in MongoDB
   - Auto-restore on bot restart

## Limitations

- ❌ Cannot clone channels with protected content (forwarding disabled)
- ❌ Bot must be admin in destination channel
- ⚠️ Large channels take time (1.5s delay per message)
- ⚠️ Some message types may not copy perfectly

## Project Structure

```
auto-forward-bot/
├── bot.py           # Main bot with command handlers
├── database.py      # MongoDB operations
├── forwarder.py     # Message cloning logic
├── user_client.py   # Phone login management
├── config.env       # Configuration file
├── requirements.txt # Python dependencies
└── README.md        # This file
```

## Troubleshooting

### "FloodWait" errors
The bot handles these automatically by waiting. Large channels will take longer.

### "Session expired"
Use `/logout` then `/login` again to create a new session.

### "Cannot access channel"
- For bot: Make sure bot is added as admin
- For user: Login with an account that's a member of the channel

### "Forwarding restricted"
The channel has protected content. This cannot be bypassed.

## Deployment

### Environment Variables

Set these environment variables on your hosting platform:

| Variable | Required | Description |
|----------|----------|-------------|
| `API_ID` | ✅ | Telegram API ID from https://my.telegram.org |
| `API_HASH` | ✅ | Telegram API Hash |
| `BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `MONGO_URI` | ✅ | MongoDB connection string |
| `ADMIN_ID` | ✅ | Your Telegram user ID |
| `PORT` | ❌ | Health check port (default: 8080) |

### Deploy to Koyeb

1. Push your code to GitHub (don't commit `config.env`!)
2. Go to [Koyeb](https://app.koyeb.com)
3. Create new app → GitHub → Select your repo
4. Set **Builder**: Dockerfile
5. Add all environment variables
6. Set **Port**: 8080
7. Deploy!

### Deploy to Railway

1. Push code to GitHub
2. Go to [Railway](https://railway.app)
3. New Project → Deploy from GitHub
4. Add environment variables in Settings
5. Railway auto-detects Dockerfile

### Deploy to Render

1. Push code to GitHub
2. Go to [Render](https://render.com)
3. New → Web Service → Connect repo
4. Set **Environment**: Docker
5. Add environment variables
6. Deploy!

### Deploy to Heroku

```bash
heroku create your-bot-name
heroku config:set API_ID=xxx API_HASH=xxx BOT_TOKEN=xxx MONGO_URI=xxx ADMIN_ID=xxx
git push heroku main
heroku ps:scale worker=1
```

### Deploy with Docker

```bash
# Build the image
docker build -t auto-forward-bot .

# Run with environment variables
docker run -d \
  -e API_ID=your_api_id \
  -e API_HASH=your_api_hash \
  -e BOT_TOKEN=your_bot_token \
  -e MONGO_URI=your_mongo_uri \
  -e ADMIN_ID=your_admin_id \
  -p 8080:8080 \
  auto-forward-bot
```

## License

MIT License - Feel free to modify and use as needed.
