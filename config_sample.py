# Copy this file to config.py and fill in your values

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = 'YOUR_BOT_TOKEN_HERE'  # Get from @BotFather

# Admin Chat IDs (can manage all users)
ADMIN_CHAT_IDS = [YOUR_CHAT_ID_HERE]  # Get from @userinfobot

# Encryption Key (CHANGE THIS TO A RANDOM STRING!)
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
ENCRYPTION_KEY = 'YOUR_ENCRYPTION_KEY_HERE'

# Database file (stored in /data volume for persistence)
DATABASE_FILE = 'users.db'

# Timezone for scheduling
TIMEZONE = 'Asia/Kolkata'



# Admin Username (for contact in access denied messages)
ADMIN_USERNAME = '@yourusername'  # Your Telegram username

# Whitelisted Chat IDs (users allowed to register and use the bot)
WHITELISTED_CHAT_IDS = [
    # Add more chat IDs below:
    # 123456789,
    # 987654321,
]