# Copy this file to config.py and fill in your values

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = 'YOUR_BOT_TOKEN_HERE'  # Get from @BotFather

# Admin Chat IDs (can manage all users)
ADMIN_CHAT_IDS = [YOUR_CHAT_ID_HERE]  # Get from @userinfobot

# Access Request Receiver (Chat ID that receives all access requests)
ACCESS_REQUEST_RECEIVER = [YOUR_CHAT_ID_HERE]  # Get from @userinfobot

# Admin Username (for contact in access denied messages)
ADMIN_USERNAME = '@yourusername'  # Your Telegram username

# Encryption Key (CHANGE THIS TO A RANDOM STRING!)
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
ENCRYPTION_KEY = 'YOUR_ENCRYPTION_KEY_HERE'

# Database file (stored in /data volume for persistence)
DATABASE_FILE = 'users.db'

# Timezone for scheduling
TIMEZONE = 'Asia/Kolkata'
