# 🤖 AWS Multiuser Telegram Bot

A powerful, multi-user Telegram bot that allows you to manage your AWS EC2 instances directly from Telegram. Control your cloud infrastructure on the go with an intuitive interface and advanced scheduling capabilities.

## ✨ Features

### 🎯 Core Functionality
- **Start/Stop/Reboot EC2 Instances** - Control your instances with a single tap
- **Real-time Status Monitoring** - Check instance states instantly
- **Multi-User Support** - Each user manages their own AWS credentials securely
- **Encrypted Credentials** - All AWS credentials are encrypted using Fernet encryption
- **Whitelist Access Control** - Only authorized users can access the bot

### ⏰ Advanced Scheduling
- **Auto Start/Stop Scheduling** - Set daily schedules for automatic instance management
- **Per-User Timezone Support** - Each user can set their own timezone (37+ timezones supported)
- **Flexible Time Selection** - Choose from preset times or enter custom times
- **Schedule Management** - View, update, and clear schedules easily
- **Persistent Schedules** - Schedules survive bot restarts

### 🔐 Security Features
- **Encrypted Storage** - AWS credentials encrypted with SHA-256 based keys
- **Whitelist System** - Admin-controlled access via chat ID whitelist
- **Secure Credential Input** - Messages containing credentials are auto-deleted
- **Per-User Isolation** - Users can only access their own instances

### 👨‍💼 Admin Features
- **User Management** - View all registered users
- **Access Control** - Grant/revoke access via `/grant` and `/revoke` commands
- **Whitelist Management** - View all whitelisted users
- **User Deletion** - Remove users and their data

## 📋 Prerequisites

- Python 3.8 or higher
- AWS Account with EC2 access
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Basic knowledge of AWS IAM and EC2

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/aws-ec2-telegram-bot.git
cd aws-ec2-telegram-bot
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure the Bot

Edit `config.py` with your settings:

```python
# Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN = 'YOUR_BOT_TOKEN_HERE'

# Your Telegram Chat ID (get it from @userinfobot)
ADMIN_CHAT_IDS = [YOUR_CHAT_ID]

# Your Telegram Username (for access denied messages)
ADMIN_USERNAME = '@yourusername'

# Whitelisted users (add chat IDs of authorized users)
WHITELISTED_CHAT_IDS = [
    YOUR_CHAT_ID,  # Admin
    # Add more users here
]

# Generate a secure encryption key
# Run: python -c "import secrets; print(secrets.token_hex(32))"
ENCRYPTION_KEY = 'YOUR_ENCRYPTION_KEY_HERE'
```

### 4. Run the Bot

```bash
python bot.py
```

## 📱 User Guide

### First Time Setup

1. **Start the Bot**
   ```
   /start
   ```

2. **Register Your AWS Account**
   ```
   /register
   ```
   - Enter your AWS Access Key ID
   - Enter your AWS Secret Access Key
   - Select your AWS Region
   - Select your Timezone

3. **You're Ready!** Start managing your EC2 instances

### Available Commands

#### 🎮 Instance Control
- `/start` - Show stopped instances and start them
- `/stop` - Show running instances and stop them
- `/reboot` - Reboot running instances
- `/status` - View status of all instances

#### ⏰ Scheduling
- `/schedule` - Create/view/manage auto start/stop schedules

#### 👤 Account Management
- `/myaccount` - View and update your account settings
  - Update AWS Region
  - Update Timezone
  - Update AWS Credentials
  - Delete Account

#### ℹ️ Help
- `/help` - Show all available commands

#### 👨‍💼 Admin Commands (Admin Only)
- `/users` - View and manage all registered users
- `/grant <chat_id>` - Grant access to a new user
- `/revoke <chat_id>` - Revoke access from a user
- `/whitelist` - View all whitelisted users

### Creating a Schedule

1. Send `/schedule`
2. Choose "⏳ Set Schedule"
3. Select an instance (or "All Instances")
4. Set start time (or skip for manual start)
5. Set stop time (or skip for manual stop)
6. Done! Your schedule is active

**Example Schedule:**
- Start at 9:00 AM
- Stop at 6:00 PM
- Timezone: Your selected timezone

## 🔧 AWS Access Key Setup

### Create Access Key for the Bot

1. **Go to AWS IAM Console**
   - Navigate to IAM → Users → Create User

2. **Set Permissions**
   - Attach policy: `AmazonEC2FullAccess`
     ```

3. **Create Access Keys**
   - Go to Security Credentials tab
   - Create Access Key
   - Choose "Third-party service"
   - Save your Access Key ID and Secret Access Key

## 🌍 Supported Timezones

The bot supports 37 timezones across 6 geographic regions:

### United States (6)
- Eastern (New York)
- Central (Chicago)
- Mountain (Denver)
- Pacific (Los Angeles)
- Alaska
- Hawaii

### Asia (8)
- India (Kolkata)
- China (Shanghai)
- Japan (Tokyo)
- Singapore
- Hong Kong
- Dubai
- Bangkok
- Seoul

### Europe (8)
- London (GMT)
- Paris
- Berlin
- Amsterdam
- Rome
- Madrid
- Stockholm
- Moscow

### Australia/Pacific (5)
- Sydney
- Melbourne
- Brisbane
- Auckland
- Fiji

### Americas (6)
- Toronto
- Vancouver
- Mexico City
- São Paulo
- Buenos Aires
- Santiago

### Africa (4)
- Cairo
- Johannesburg
- Lagos
- Nairobi

## 🗂️ Project Structure

```
aws-ec2-telegram-bot/
├── bot.py                 # Main bot application
├── config.py              # Configuration file
├── user_database.py       # Database management
├── users.db              # SQLite database (auto-created)
├── requirements.txt       # Python dependencies
├── README.md             # This file
└── LICENSE               # MIT License
```

## 📊 Database Schema

### Users Table
```sql
CREATE TABLE users (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    aws_access_key TEXT,      -- Encrypted
    aws_secret_key TEXT,      -- Encrypted
    aws_region TEXT,
    timezone TEXT,
    is_active INTEGER,
    created_at TIMESTAMP
);
```

### Schedules Table
```sql
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER,
    instance_id TEXT,
    action TEXT,              -- 'start' or 'stop'
    time TEXT,                -- HH:MM format
    created_at TIMESTAMP
);
```

## 📧 Support

If you encounter any issues or have questions:
 
   Contact the maintainer [@jayvora18](https://telegram.me/jayvora18)

## 🔮 Future Enhancements

- [ ] Support for multiple AWS accounts per user
- [ ] Instance metrics and monitoring
- [ ] Cost tracking and alerts
- [ ] Support for other AWS services (RDS, Lambda, etc.)
- [ ] Web dashboard for management
- [ ] Backup and restore functionality
- [ ] Instance tagging and grouping
- [ ] Notification system for instance state changes

## ⚠️ Disclaimer

This bot provides direct access to your AWS EC2 instances. Use it responsibly and ensure proper security measures are in place. The authors are not responsible for any AWS charges incurred or security issues arising from the use of this bot.

---

**Made with ❤️ for the AWS Community**

**Star ⭐ this repository if you find it helpful!**
