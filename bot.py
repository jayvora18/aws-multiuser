import asyncio
import json
import boto3
import urllib3
import config
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from concurrent.futures import ThreadPoolExecutor
from user_database import UserDatabase

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.INFO)

# Load configuration
TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
ADMIN_CHAT_IDS = config.ADMIN_CHAT_IDS
ADMIN_USERNAME = config.ADMIN_USERNAME
WHITELISTED_CHAT_IDS = config.WHITELISTED_CHAT_IDS

# Initialize HTTP client
http = urllib3.PoolManager()

# Initialize scheduler
scheduler = AsyncIOScheduler(timezone=pytz.timezone(config.TIMEZONE))

# Thread pool for blocking AWS operations
executor = ThreadPoolExecutor(max_workers=10)

# Initialize user database
user_db = UserDatabase()

# Store button states
button_states = {}
instance_operation_states = {}

# Conversation states for registration
REGISTER_ACCESS_KEY, REGISTER_SECRET_KEY, REGISTER_REGION_AREA, REGISTER_REGION, REGISTER_TIMEZONE_AREA, REGISTER_TIMEZONE = range(6)

# Conversation states for update credentials
UPDATE_CREDS_ACCESS_KEY, UPDATE_CREDS_SECRET_KEY = range(20, 22)

# Conversation states for update region
UPDATE_REGION_AREA, UPDATE_REGION = range(10, 12)

# Conversation states for update timezone
UPDATE_TIMEZONE_AREA, UPDATE_TIMEZONE = range(30, 32)

# Conversation states for scheduling
SCHEDULE_INSTANCE, SCHEDULE_START_TIME, SCHEDULE_STOP_TIME, CUSTOM_START_TIME, CUSTOM_STOP_TIME = range(5, 10)

def format_registration_date(date_str):
    """Format registration date from database format to readable format"""
    from datetime import datetime
    try:
        reg_datetime = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        return reg_datetime.strftime('%Y-%m-%d at %I:%M %p')
    except:
        return date_str

def format_time_12hr(time_str):
    """Convert 24-hour time to 12-hour format with AM/PM"""
    from datetime import datetime
    try:
        time_obj = datetime.strptime(time_str, '%H:%M')
        return time_obj.strftime('%I:%M %p')
    except:
        return time_str

def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    
    encoded_data = json.dumps(payload).encode('utf-8')
    http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})

def edit_message(chat_id, message_id, text, reply_markup=None):
    """Edit an existing message"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {'chat_id': chat_id, 'message_id': message_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    
    encoded_data = json.dumps(payload).encode('utf-8')
    try:
        response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
        logger.info(f"Edit message response: {response.status}")
        return True
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        return False

def delete_message(chat_id, message_id):
    """Delete a message"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
    payload = {'chat_id': chat_id, 'message_id': message_id}
    
    encoded_data = json.dumps(payload).encode('utf-8')
    try:
        http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
        return True
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return False

def get_ec2_client(chat_id):
    """Get EC2 client for user"""
    user = user_db.get_user(chat_id)
    if not user:
        return None
    
    return boto3.client(
        'ec2',
        region_name=user['aws_region'],
        aws_access_key_id=user['aws_access_key'],
        aws_secret_access_key=user['aws_secret_key']
    )

def get_instance_name(ec2_client, instance_id):
    """Get instance name from instance ID"""
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance = response['Reservations'][0]['Instances'][0]
        if 'Tags' in instance:
            for tag in instance['Tags']:
                if tag['Key'] == 'Name':
                    return tag['Value']
        return 'No Name'
    except:
        return 'Unknown'

def check_authorization(chat_id):
    """Check if user is registered"""
    return user_db.user_exists(chat_id)

def is_admin(chat_id):
    """Check if user is admin"""
    return chat_id in ADMIN_CHAT_IDS

def is_whitelisted(chat_id):
    """Check if user is whitelisted"""
    return chat_id in WHITELISTED_CHAT_IDS

def scheduled_start_instance(instance_id, chat_id):
    """Scheduled task to start instance"""
    try:
        ec2 = get_ec2_client(chat_id)
        if not ec2:
            logger.error(f"Cannot get EC2 client for chat_id {chat_id}")
            return
        
        instance_name = get_instance_name(ec2, instance_id)
        
        # Check current instance state
        response = ec2.describe_instances(InstanceIds=[instance_id])
        current_state = response['Reservations'][0]['Instances'][0]['State']['Name']
        
        if current_state == 'running':
            send_message(chat_id, f"⏰ Scheduled Start\n\n🟢 <b>{instance_name}</b> already running")
            logger.info(f"Scheduled start skipped: {instance_id} already running for user {chat_id}")
        elif current_state == 'stopped':
            ec2.start_instances(InstanceIds=[instance_id])
            send_message(chat_id, f"⏰ Scheduled Start\n\n🟢 Starting <b>{instance_name}</b>")
            logger.info(f"Scheduled start: {instance_id} for user {chat_id}")
        else:
            send_message(chat_id, f"⏰ Scheduled Start\n\n⚠️ <b>{instance_name}</b> is {current_state}")
            logger.info(f"Scheduled start skipped: {instance_id} is {current_state} for user {chat_id}")
    except Exception as e:
        logger.error(f"Scheduled start error: {e}")

def scheduled_stop_instance(instance_id, chat_id):
    """Scheduled task to stop instance"""
    try:
        ec2 = get_ec2_client(chat_id)
        if not ec2:
            logger.error(f"Cannot get EC2 client for chat_id {chat_id}")
            return
        
        instance_name = get_instance_name(ec2, instance_id)
        
        # Check current instance state
        response = ec2.describe_instances(InstanceIds=[instance_id])
        current_state = response['Reservations'][0]['Instances'][0]['State']['Name']
        
        if current_state == 'stopped':
            send_message(chat_id, f"⏰ Scheduled Stop\n\n🔴 <b>{instance_name}</b> already stopped")
            logger.info(f"Scheduled stop skipped: {instance_id} already stopped for user {chat_id}")
        elif current_state == 'running':
            ec2.stop_instances(InstanceIds=[instance_id])
            send_message(chat_id, f"⏰ Scheduled Stop\n\n🔴 Stopping <b>{instance_name}</b>")
            logger.info(f"Scheduled stop: {instance_id} for user {chat_id}")
        else:
            send_message(chat_id, f"⏰ Scheduled Stop\n\n⚠️ <b>{instance_name}</b> is {current_state}")
            logger.info(f"Scheduled stop skipped: {instance_id} is {current_state} for user {chat_id}")
    except Exception as e:
        logger.error(f"Scheduled stop error: {e}")

# ==================== REGISTRATION COMMANDS ====================

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /register command"""
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name
    
    # Check if user is whitelisted
    if not is_whitelisted(chat_id):
        text = "⛔ <b>Access Denied</b>\n\n"
        text += "You are not authorized to use this bot.\n\n"
        text += f"Your Chat ID: <code>{chat_id}</code>\n\n"
        text += f"Contact the administrator to request access {ADMIN_USERNAME}"
        send_message(chat_id, text)
        return ConversationHandler.END
    
    # Check if already registered
    if user_db.user_exists(chat_id):
        send_message(chat_id, "✅ You are already registered\n\nUse /myaccount to view your details")
        return ConversationHandler.END
        
    text = "<b>🔐 AWS Account Registration</b>\n\n"
    text += "Welcome! Let's set up your AWS account.\n\n"
    text += "Please send your <b>AWS Access Key ID</b>\n\n"
    text += "Example: <code>AKIAIOSFODNN7EXAMPLE</code>\n\n"
    text += "⚠️ Your credentials will be encrypted and stored securely."
    
    keyboard = [
        [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
    ]
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        context.user_data['registration_message_id'] = msg_id
    
    context.user_data['username'] = username
    return REGISTER_ACCESS_KEY

async def register_access_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle AWS Access Key input"""
    chat_id = update.effective_chat.id
    
    # Handle callback query (cancel button)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == 'reg_cancel':
            edit_message(chat_id, context.user_data.get('registration_message_id'), "❌ Registration cancelled")
            context.user_data.clear()
            return ConversationHandler.END
        
        return REGISTER_ACCESS_KEY
    
    access_key = update.message.text.strip()
    
    if access_key == '/cancel':
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, "❌ Registration cancelled")
        else:
            send_message(chat_id, "❌ Registration cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Basic validation
    if not access_key.startswith('AKIA') or len(access_key) != 20:
        text = "<b>🔐 AWS Account Registration</b>\n\n"
        text += "❌ Invalid Access Key format\n\n"
        text += "Please send your <b>AWS Access Key ID</b>\n\n"
        text += "Example: <code>AKIAIOSFODNN7EXAMPLE</code>\n\n"
        text += "⚠️ Must start with AKIA and be 20 characters"
        
        keyboard = [
            [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
        ]
        
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return REGISTER_ACCESS_KEY
    
    context.user_data['aws_access_key'] = access_key
    
    # Delete the message containing access key for security
    try:
        await update.message.delete()
    except:
        pass
    
    text = "<b>🔐 AWS Account Registration</b>\n\n"
    text += "✅ Access Key received\n\n"
    text += "Now send your <b>AWS Secret Access Key</b>\n\n"
    text += "Example: <code>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</code>\n\n"
    text += "⚠️ Your credentials will be encrypted and stored securely."
    
    keyboard = [
        [{'text': '🔙 Back', 'callback_data': 'reg_back_access'}],
        [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
    ]
    
    msg_id = context.user_data.get('registration_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return REGISTER_SECRET_KEY

async def register_secret_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle AWS Secret Key input"""
    chat_id = update.effective_chat.id
    
    # Handle callback query (back button)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == 'reg_cancel':
            edit_message(chat_id, context.user_data.get('registration_message_id'), "❌ Registration cancelled")
            context.user_data.clear()
            return ConversationHandler.END
        
        if query.data == 'reg_back_access':
            # Go back to access key step
            context.user_data.pop('aws_access_key', None)
            
            text = "<b>🔐 AWS Account Registration</b>\n\n"
            text += "Welcome! Let's set up your AWS account.\n\n"
            text += "Please send your <b>AWS Access Key ID</b>\n\n"
            text += "Example: <code>AKIAIOSFODNN7EXAMPLE</code>\n\n"
            text += "⚠️ Your credentials will be encrypted and stored securely."
            
            keyboard = [
                [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
            ]
            
            msg_id = context.user_data.get('registration_message_id')
            if msg_id:
                edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
            
            return REGISTER_ACCESS_KEY
        
        return REGISTER_SECRET_KEY
    
    secret_key = update.message.text.strip()
    
    if secret_key == '/cancel':
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, "❌ Registration cancelled")
        else:
            send_message(chat_id, "❌ Registration cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Basic validation
    if len(secret_key) != 40:
        text = "<b>🔐 AWS Account Registration</b>\n\n"
        text += "❌ Invalid Secret Key format\n\n"
        text += "Now send your <b>AWS Secret Access Key</b>\n\n"
        text += "Example: <code>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</code>\n\n"
        text += "⚠️ Must be exactly 40 characters"
        
        keyboard = [
            [{'text': '🔙 Back', 'callback_data': 'reg_back_access'}],
            [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
        ]
        
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return REGISTER_SECRET_KEY
    
    context.user_data['aws_secret_key'] = secret_key
    
    # Delete the message containing secret key for security
    try:
        await update.message.delete()
    except:
        pass
    
    text = "<b>🔐 AWS Account Registration</b>\n\n"
    text += "✅ Secret Key received\n\n"
    text += "Select your <b>Geographic Region</b>:"
    
    keyboard = [
        [{'text': '🇺🇸 United States', 'callback_data': 'regarea_us'}],
        [{'text': '🇮🇳 Asia Pacific', 'callback_data': 'regarea_ap'}],
        [{'text': '🇨🇦 Canada', 'callback_data': 'regarea_ca'}],
        [{'text': '🇪🇺 Europe', 'callback_data': 'regarea_eu'}],
        [{'text': '🇦🇺 South America', 'callback_data': 'regarea_sa'}],
        [{'text': '🔙 Back', 'callback_data': 'reg_back_secret'}],
        [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
    ]
    
    msg_id = context.user_data.get('registration_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return REGISTER_REGION_AREA

async def register_region_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle geographic region area selection"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    # Handle back button
    if query.data == 'reg_back_secret':
        context.user_data.pop('aws_secret_key', None)
        
        text = "<b>🔐 AWS Account Registration</b>\n\n"
        text += "✅ Access Key received\n\n"
        text += "Now send your <b>AWS Secret Access Key</b>\n\n"
        text += "Example: <code>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</code>"
        
        keyboard = [
            [{'text': '🔙 Back', 'callback_data': 'reg_back_access'}],
            [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
        ]
        
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return REGISTER_SECRET_KEY
    
    if query.data == 'reg_cancel':
        edit_message(chat_id, context.user_data.get('registration_message_id'), "❌ Registration cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Store selected area
    area = query.data.replace('regarea_', '')
    context.user_data['selected_area'] = area
    
    # Show regions based on selected area
    text = "<b>🔐 AWS Account Registration</b>\n\n"
    text += "✅ Secret Key received\n\n"
    
    keyboard = []
    
    if area == 'us':
        text += "Select <b>United States</b> Region:"
        keyboard = [
            [{'text': 'N. Virginia (us-east-1)', 'callback_data': 'region_us-east-1'}],
            [{'text': 'Ohio (us-east-2)', 'callback_data': 'region_us-east-2'}],
            [{'text': 'N. California (us-west-1)', 'callback_data': 'region_us-west-1'}],
            [{'text': 'Oregon (us-west-2)', 'callback_data': 'region_us-west-2'}]
        ]
    elif area == 'ap':
        text += "Select <b>Asia Pacific</b> Region:"
        keyboard = [
            [{'text': 'Mumbai (ap-south-1)', 'callback_data': 'region_ap-south-1'}],
            [{'text': 'Osaka (ap-northeast-3)', 'callback_data': 'region_ap-northeast-3'}],
            [{'text': 'Seoul (ap-northeast-2)', 'callback_data': 'region_ap-northeast-2'}],
            [{'text': 'Singapore (ap-southeast-1)', 'callback_data': 'region_ap-southeast-1'}],
            [{'text': 'Sydney (ap-southeast-2)', 'callback_data': 'region_ap-southeast-2'}],
            [{'text': 'Tokyo (ap-northeast-1)', 'callback_data': 'region_ap-northeast-1'}]
        ]
    elif area == 'ca':
        text += "Select <b>Canada</b> Region:"
        keyboard = [
            [{'text': 'Central (ca-central-1)', 'callback_data': 'region_ca-central-1'}]
        ]
    elif area == 'eu':
        text += "Select <b>Europe</b> Region:"
        keyboard = [
            [{'text': 'Frankfurt (eu-central-1)', 'callback_data': 'region_eu-central-1'}],
            [{'text': 'Ireland (eu-west-1)', 'callback_data': 'region_eu-west-1'}],
            [{'text': 'London (eu-west-2)', 'callback_data': 'region_eu-west-2'}],
            [{'text': 'Paris (eu-west-3)', 'callback_data': 'region_eu-west-3'}],
            [{'text': 'Stockholm (eu-north-1)', 'callback_data': 'region_eu-north-1'}]
        ]
    elif area == 'sa':
        text += "Select <b>South America</b> Region:"
        keyboard = [
            [{'text': 'São Paulo (sa-east-1)', 'callback_data': 'region_sa-east-1'}]
        ]
    
    keyboard.append([{'text': '🔙 Back to Regions', 'callback_data': 'reg_back_area'}])
    keyboard.append([{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}])
    
    msg_id = context.user_data.get('registration_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return REGISTER_REGION

async def register_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle region selection"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    # Handle back to area selection
    if query.data == 'reg_back_area':
        text = "<b>🔐 AWS Account Registration</b>\n\n"
        text += "✅ Secret Key received\n\n"
        text += "Select your <b>Geographic Region</b>:"
        
        keyboard = [
            [{'text': '🇺🇸 United States', 'callback_data': 'regarea_us'}],
            [{'text': '🇮🇳 Asia Pacific', 'callback_data': 'regarea_ap'}],
            [{'text': '🇨🇦 Canada', 'callback_data': 'regarea_ca'}],
            [{'text': '🇪🇺 Europe', 'callback_data': 'regarea_eu'}],
            [{'text': '🇦🇺 South America', 'callback_data': 'regarea_sa'}],
            [{'text': '🔙 Back', 'callback_data': 'reg_back_secret'}],
            [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
        ]
        
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return REGISTER_REGION_AREA
    
    if query.data == 'reg_cancel':
        edit_message(chat_id, context.user_data.get('registration_message_id'), "❌ Registration cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    region = query.data.replace('region_', '')
    context.user_data['aws_region'] = region
    
    # Show timezone selection
    text = "<b>🔐 AWS Account Registration</b>\n\n"
    text += f"✅ Region: {region}\n\n"
    text += "Select your <b>Timezone</b>:"
    
    keyboard = [
        [{'text': '🇺🇸 United States', 'callback_data': 'regtzarea_us'}],
        [{'text': '🇮🇳 Asia', 'callback_data': 'regtzarea_asia'}],
        [{'text': '🇪🇺 Europe', 'callback_data': 'regtzarea_europe'}],
        [{'text': '🇦🇺 Australia/Pacific', 'callback_data': 'regtzarea_pacific'}],
        [{'text': '🌎 Americas', 'callback_data': 'regtzarea_americas'}],
        [{'text': '🌍 Africa', 'callback_data': 'regtzarea_africa'}],
        [{'text': '🔙 Back to Region', 'callback_data': 'reg_back_to_region'}],
        [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
    ]
    
    msg_id = context.user_data.get('registration_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return REGISTER_TIMEZONE_AREA

async def register_timezone_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone area selection"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    if query.data == 'reg_back_to_region':
        # Go back to region selection
        area = context.user_data.get('selected_area', 'us')
        
        text = "<b>🔐 AWS Account Registration</b>\n\n"
        text += "✅ Secret Key received\n\n"
        
        keyboard = []
        
        if area == 'us':
            text += "Select <b>United States</b> Region:"
            keyboard = [
                [{'text': 'N. Virginia (us-east-1)', 'callback_data': 'region_us-east-1'}],
                [{'text': 'Ohio (us-east-2)', 'callback_data': 'region_us-east-2'}],
                [{'text': 'N. California (us-west-1)', 'callback_data': 'region_us-west-1'}],
                [{'text': 'Oregon (us-west-2)', 'callback_data': 'region_us-west-2'}]
            ]
        elif area == 'ap':
            text += "Select <b>Asia Pacific</b> Region:"
            keyboard = [
                [{'text': 'Mumbai (ap-south-1)', 'callback_data': 'region_ap-south-1'}],
                [{'text': 'Osaka (ap-northeast-3)', 'callback_data': 'region_ap-northeast-3'}],
                [{'text': 'Seoul (ap-northeast-2)', 'callback_data': 'region_ap-northeast-2'}],
                [{'text': 'Singapore (ap-southeast-1)', 'callback_data': 'region_ap-southeast-1'}],
                [{'text': 'Sydney (ap-southeast-2)', 'callback_data': 'region_ap-southeast-2'}],
                [{'text': 'Tokyo (ap-northeast-1)', 'callback_data': 'region_ap-northeast-1'}]
            ]
        elif area == 'ca':
            text += "Select <b>Canada</b> Region:"
            keyboard = [
                [{'text': 'Central (ca-central-1)', 'callback_data': 'region_ca-central-1'}]
            ]
        elif area == 'eu':
            text += "Select <b>Europe</b> Region:"
            keyboard = [
                [{'text': 'Frankfurt (eu-central-1)', 'callback_data': 'region_eu-central-1'}],
                [{'text': 'Ireland (eu-west-1)', 'callback_data': 'region_eu-west-1'}],
                [{'text': 'London (eu-west-2)', 'callback_data': 'region_eu-west-2'}],
                [{'text': 'Paris (eu-west-3)', 'callback_data': 'region_eu-west-3'}],
                [{'text': 'Stockholm (eu-north-1)', 'callback_data': 'region_eu-north-1'}]
            ]
        elif area == 'sa':
            text += "Select <b>South America</b> Region:"
            keyboard = [
                [{'text': 'São Paulo (sa-east-1)', 'callback_data': 'region_sa-east-1'}]
            ]
        
        keyboard.append([{'text': '🔙 Back to Regions', 'callback_data': 'reg_back_area'}])
        keyboard.append([{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}])
        
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return REGISTER_REGION
    
    if query.data == 'reg_cancel':
        edit_message(chat_id, context.user_data.get('registration_message_id'), "❌ Registration cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    tz_area = query.data.replace('regtzarea_', '')
    context.user_data['selected_tz_area'] = tz_area
    
    text = "<b>🔐 AWS Account Registration</b>\n\n"
    text += f"✅ Region: {context.user_data.get('aws_region')}\n\n"
    text += "Select your <b>Timezone</b>:"
    
    keyboard = []
    
    if tz_area == 'us':
        keyboard = [
            [{'text': 'Eastern (New York)', 'callback_data': 'regtz_America/New_York'}],
            [{'text': 'Central (Chicago)', 'callback_data': 'regtz_America/Chicago'}],
            [{'text': 'Mountain (Denver)', 'callback_data': 'regtz_America/Denver'}],
            [{'text': 'Pacific (Los Angeles)', 'callback_data': 'regtz_America/Los_Angeles'}],
            [{'text': 'Alaska', 'callback_data': 'regtz_America/Anchorage'}],
            [{'text': 'Hawaii', 'callback_data': 'regtz_Pacific/Honolulu'}]
        ]
    elif tz_area == 'asia':
        keyboard = [
            [{'text': 'India (Kolkata)', 'callback_data': 'regtz_Asia/Kolkata'}],
            [{'text': 'China (Shanghai)', 'callback_data': 'regtz_Asia/Shanghai'}],
            [{'text': 'Japan (Tokyo)', 'callback_data': 'regtz_Asia/Tokyo'}],
            [{'text': 'Singapore', 'callback_data': 'regtz_Asia/Singapore'}],
            [{'text': 'Hong Kong', 'callback_data': 'regtz_Asia/Hong_Kong'}],
            [{'text': 'Dubai', 'callback_data': 'regtz_Asia/Dubai'}],
            [{'text': 'Bangkok', 'callback_data': 'regtz_Asia/Bangkok'}],
            [{'text': 'Seoul', 'callback_data': 'regtz_Asia/Seoul'}]
        ]
    elif tz_area == 'europe':
        keyboard = [
            [{'text': 'London (GMT)', 'callback_data': 'regtz_Europe/London'}],
            [{'text': 'Paris', 'callback_data': 'regtz_Europe/Paris'}],
            [{'text': 'Berlin', 'callback_data': 'regtz_Europe/Berlin'}],
            [{'text': 'Amsterdam', 'callback_data': 'regtz_Europe/Amsterdam'}],
            [{'text': 'Rome', 'callback_data': 'regtz_Europe/Rome'}],
            [{'text': 'Madrid', 'callback_data': 'regtz_Europe/Madrid'}],
            [{'text': 'Stockholm', 'callback_data': 'regtz_Europe/Stockholm'}],
            [{'text': 'Moscow', 'callback_data': 'regtz_Europe/Moscow'}]
        ]
    elif tz_area == 'pacific':
        keyboard = [
            [{'text': 'Sydney', 'callback_data': 'regtz_Australia/Sydney'}],
            [{'text': 'Melbourne', 'callback_data': 'regtz_Australia/Melbourne'}],
            [{'text': 'Brisbane', 'callback_data': 'regtz_Australia/Brisbane'}],
            [{'text': 'Auckland', 'callback_data': 'regtz_Pacific/Auckland'}],
            [{'text': 'Fiji', 'callback_data': 'regtz_Pacific/Fiji'}]
        ]
    elif tz_area == 'americas':
        keyboard = [
            [{'text': 'Toronto', 'callback_data': 'regtz_America/Toronto'}],
            [{'text': 'Vancouver', 'callback_data': 'regtz_America/Vancouver'}],
            [{'text': 'Mexico City', 'callback_data': 'regtz_America/Mexico_City'}],
            [{'text': 'São Paulo', 'callback_data': 'regtz_America/Sao_Paulo'}],
            [{'text': 'Buenos Aires', 'callback_data': 'regtz_America/Argentina/Buenos_Aires'}],
            [{'text': 'Santiago', 'callback_data': 'regtz_America/Santiago'}]
        ]
    elif tz_area == 'africa':
        keyboard = [
            [{'text': 'Cairo', 'callback_data': 'regtz_Africa/Cairo'}],
            [{'text': 'Johannesburg', 'callback_data': 'regtz_Africa/Johannesburg'}],
            [{'text': 'Lagos', 'callback_data': 'regtz_Africa/Lagos'}],
            [{'text': 'Nairobi', 'callback_data': 'regtz_Africa/Nairobi'}]
        ]
    
    keyboard.append([{'text': '🔙 Back to Timezone Areas', 'callback_data': 'reg_back_tz_area'}])
    keyboard.append([{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}])
    
    msg_id = context.user_data.get('registration_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return REGISTER_TIMEZONE

async def register_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone selection and complete registration"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    if query.data == 'reg_back_tz_area':
        # Go back to timezone area selection
        text = "<b>🔐 AWS Account Registration</b>\n\n"
        text += f"✅ Region: {context.user_data.get('aws_region')}\n\n"
        text += "Select your <b>Timezone</b>:"
        
        keyboard = [
            [{'text': '🇺🇸 United States', 'callback_data': 'regtzarea_us'}],
            [{'text': '🇮🇳 Asia', 'callback_data': 'regtzarea_asia'}],
            [{'text': '🇪🇺 Europe', 'callback_data': 'regtzarea_europe'}],
            [{'text': '🇦🇺 Australia/Pacific', 'callback_data': 'regtzarea_pacific'}],
            [{'text': '🌎 Americas', 'callback_data': 'regtzarea_americas'}],
            [{'text': '🌍 Africa', 'callback_data': 'regtzarea_africa'}],
            [{'text': '🔙 Back to Region', 'callback_data': 'reg_back_to_region'}],
            [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
        ]
        
        msg_id = context.user_data.get('registration_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return REGISTER_TIMEZONE_AREA
    
    if query.data == 'reg_cancel':
        edit_message(chat_id, context.user_data.get('registration_message_id'), "❌ Registration cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    timezone = query.data.replace('regtz_', '')
    
    # Get stored data
    username = context.user_data.get('username')
    access_key = context.user_data.get('aws_access_key')
    secret_key = context.user_data.get('aws_secret_key')
    region = context.user_data.get('aws_region')
    msg_id = context.user_data.get('registration_message_id')
    
    # Show verifying message
    text = "<b>🔐 AWS Account Registration</b>\n\n"
    text += "⏳ Verifying credentials...\n\n"
    text += f"Region: {region}\n"
    text += f"Timezone: {timezone}"
    
    if msg_id:
        edit_message(chat_id, msg_id, text)
    
    # Test credentials
    try:
        test_ec2 = boto3.client(
            'ec2',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )
        # Try to describe instances to verify credentials
        test_ec2.describe_instances(MaxResults=5)
        
        # Save to database with timezone
        user_db.add_user(chat_id, username, access_key, secret_key, region, timezone)
        
        # Get the saved user to get registration date
        saved_user = user_db.get_user(chat_id)
        
        text = "<b>✅ Registration Successfully!</b>\n\n"
        text += f"👤 {username}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {region}\n"
        text += f"   Timezone: {timezone}\n"
        text += f"   Access Key: {access_key[:8]}...{access_key[-4:]}\n"
        text += f"   Registered: {format_registration_date(saved_user['created_at'])}\n\n"
        text += "Your AWS credentials have been encrypted and saved securely.\n\n"
        text += "Use /help to see available commands"
        
        if msg_id:
            edit_message(chat_id, msg_id, text)
        else:
            send_message(chat_id, text)
        
        # Clear sensitive data
        context.user_data.clear()
        
        return ConversationHandler.END
        
    except Exception as e:
        text = f"<b>❌ Credential Verification Failed</b>\n\n"
        text += f"Error: {str(e)}\n\n"
        text += "Please check your credentials and try again."
        
        keyboard = [
            [{'text': '🔙 Back to Timezone', 'callback_data': 'reg_back_tz_area'}],
            [{'text': '❌ Cancel', 'callback_data': 'reg_cancel'}]
        ]
        
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        else:
            send_message(chat_id, text, {'inline_keyboard': keyboard})
        
        return REGISTER_TIMEZONE

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel registration"""
    chat_id = update.effective_chat.id
    send_message(chat_id, "❌ Registration cancelled")
    context.user_data.clear()
    return ConversationHandler.END

# ==================== USER ACCOUNT COMMANDS ====================

async def myaccount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /myaccount command"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        send_message(chat_id, "⛔ You are not registered\n\nUse /register to get started")
        return
    
    user = user_db.get_user(chat_id)
    
    text = "<b>👤 Your Account</b>\n\n"
    text += f"✅ {user['username']}\n"
    text += f"   Chat ID: <code>{chat_id}</code>\n"
    text += f"   Region: {user['aws_region']}\n"
    text += f"   Timezone: {user['timezone']}\n"
    text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
    text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
    
    keyboard = [
        [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
        [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
        [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
        [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
    ]
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        context.user_data['myaccount_message_id'] = msg_id

async def update_creds_access_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle AWS Access Key input for update"""
    chat_id = update.effective_chat.id
    
    # Handle callback query
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == 'creds_cancel' or query.data == 'back_to_account_from_creds':
            user = user_db.get_user(chat_id)
            
            text = "<b>👤 Your Account</b>\n\n"
            text += f"✅ {user['username']}\n"
            text += f"   Chat ID: <code>{chat_id}</code>\n"
            text += f"   Region: {user['aws_region']}\n"
            text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
            text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
            
            keyboard = [
                [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
                [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
                [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
                [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
            ]
            
            msg_id = context.user_data.get('update_creds_message_id', query.message.message_id)
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
            context.user_data.clear()
            return ConversationHandler.END
        
        return UPDATE_CREDS_ACCESS_KEY
    
    access_key = update.message.text.strip()
    
    # Basic validation
    if not access_key.startswith('AKIA') or len(access_key) != 20:
        text = "<b>🔐 Update AWS Credentials</b>\n\n"
        text += "❌ Invalid Access Key format\n\n"
        text += "Please send your new <b>AWS Access Key ID</b>\n\n"
        text += "Example: <code>AKIAIOSFODNN7EXAMPLE</code>\n\n"
        text += "⚠️ Must start with AKIA and be 20 characters"
        
        keyboard = [
            [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account_from_creds'}],
            [{'text': '❌ Cancel', 'callback_data': 'creds_cancel'}]
        ]
        
        msg_id = context.user_data.get('update_creds_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return UPDATE_CREDS_ACCESS_KEY
    
    context.user_data['new_aws_access_key'] = access_key
    
    # Delete the message containing access key for security
    try:
        await update.message.delete()
    except:
        pass
    
    text = "<b>🔐 Update AWS Credentials</b>\n\n"
    text += "✅ Access Key received\n\n"
    text += "Now send your new <b>AWS Secret Access Key</b>\n\n"
    text += "Example: <code>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</code>\n\n"
    text += "⚠️ Your credentials will be encrypted and stored securely."
    
    keyboard = [
        [{'text': '🔙 Back to Credentials', 'callback_data': 'back_to_creds_access'}],
        [{'text': '❌ Cancel', 'callback_data': 'creds_cancel'}]
    ]
    
    msg_id = context.user_data.get('update_creds_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return UPDATE_CREDS_SECRET_KEY

async def update_creds_secret_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle AWS Secret Key input for update"""
    chat_id = update.effective_chat.id
    
    # Handle callback query
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == 'back_to_creds_access':
            # Go back to access key step
            context.user_data.pop('new_aws_access_key', None)
            
            text = "<b>🔐 Update AWS Credentials</b>\n\n"
            text += "Please send your new <b>AWS Access Key ID</b>\n\n"
            text += "Example: <code>AKIAIOSFODNN7EXAMPLE</code>\n\n"
            text += "⚠️ Your credentials will be encrypted and stored securely."
            
            keyboard = [
                [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account_from_creds'}],
                [{'text': '❌ Cancel', 'callback_data': 'creds_cancel'}]
            ]
            
            msg_id = context.user_data.get('update_creds_message_id', query.message.message_id)
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
            return UPDATE_CREDS_ACCESS_KEY
        
        if query.data == 'creds_cancel' or query.data == 'back_to_account_from_creds':
            user = user_db.get_user(chat_id)
            
            text = "<b>👤 Your Account</b>\n\n"
            text += f"✅ {user['username']}\n"
            text += f"   Chat ID: <code>{chat_id}</code>\n"
            text += f"   Region: {user['aws_region']}\n"
            text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
            text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
            
            keyboard = [
                [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
                [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
                [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
                [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
            ]
            
            msg_id = context.user_data.get('update_creds_message_id', query.message.message_id)
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
            context.user_data.clear()
            return ConversationHandler.END
        
        return UPDATE_CREDS_SECRET_KEY
    
    secret_key = update.message.text.strip()
    
    # Basic validation
    if len(secret_key) != 40:
        text = "<b>🔐 Update AWS Credentials</b>\n\n"
        text += "❌ Invalid Secret Key format\n\n"
        text += "Now send your new <b>AWS Secret Access Key</b>\n\n"
        text += "Example: <code>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</code>\n\n"
        text += "⚠️ Must be exactly 40 characters"
        
        keyboard = [
            [{'text': '🔙 Back to Credentials', 'callback_data': 'back_to_creds_access'}],
            [{'text': '❌ Cancel', 'callback_data': 'creds_cancel'}]
        ]
        
        msg_id = context.user_data.get('update_creds_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return UPDATE_CREDS_SECRET_KEY
    
    # Delete the message containing secret key for security
    try:
        await update.message.delete()
    except:
        pass
    
    # Get stored data
    username = context.user_data.get('username')
    aws_region = context.user_data.get('aws_region')
    access_key = context.user_data.get('new_aws_access_key')
    msg_id = context.user_data.get('update_creds_message_id')
    
    # Show verifying message
    text = "<b>🔐 Update AWS Credentials</b>\n\n"
    text += "⏳ Verifying credentials...\n\n"
    text += f"Region: {aws_region}"
    
    if msg_id:
        edit_message(chat_id, msg_id, text)
    
    # Test credentials
    try:
        test_ec2 = boto3.client(
            'ec2',
            region_name=aws_region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )
        # Try to describe instances to verify credentials
        test_ec2.describe_instances(MaxResults=5)
        
        # Update credentials in database
        user_db.add_user(chat_id, username, access_key, secret_key, aws_region)
        
        # Get updated user data
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        keyboard = [
            [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
            [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
            [{'text': '✅ Credentials Updated', 'callback_data': 'disabled'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        else:
            send_message(chat_id, text, {'inline_keyboard': keyboard})
        
        # Clear sensitive data
        context.user_data.clear()
        
        return ConversationHandler.END
        
    except Exception as e:
        text = f"<b>❌ Credential Verification Failed</b>\n\n"
        text += f"Error: {str(e)}\n\n"
        text += "Please check your credentials and try again."
        
        keyboard = [
            [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account_from_creds'}],
            [{'text': '❌ Cancel', 'callback_data': 'creds_cancel'}]
        ]
        
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        else:
            send_message(chat_id, text, {'inline_keyboard': keyboard})
        
        return UPDATE_CREDS_SECRET_KEY

async def update_region_area_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle geographic region area selection for update"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    if query.data == 'upd_cancel' or query.data == 'back_to_account':
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        keyboard = [
            [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
            [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
            [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        msg_id = context.user_data.get('update_region_message_id', query.message.message_id)
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        context.user_data.clear()
        return ConversationHandler.END
    
    # Store selected area
    area = query.data.replace('updarea_', '')
    context.user_data['selected_area'] = area
    
    # Show regions based on selected area
    text = "<b>🌍 Update AWS Region</b>\n\n"
    
    keyboard = []
    
    if area == 'us':
        text += "Select <b>United States</b> Region:"
        keyboard = [
            [{'text': 'N. Virginia (us-east-1)', 'callback_data': 'updreg_us-east-1'}],
            [{'text': 'Ohio (us-east-2)', 'callback_data': 'updreg_us-east-2'}],
            [{'text': 'N. California (us-west-1)', 'callback_data': 'updreg_us-west-1'}],
            [{'text': 'Oregon (us-west-2)', 'callback_data': 'updreg_us-west-2'}]
        ]
    elif area == 'ap':
        text += "Select <b>Asia Pacific</b> Region:"
        keyboard = [
            [{'text': 'Mumbai (ap-south-1)', 'callback_data': 'updreg_ap-south-1'}],
            [{'text': 'Osaka (ap-northeast-3)', 'callback_data': 'updreg_ap-northeast-3'}],
            [{'text': 'Seoul (ap-northeast-2)', 'callback_data': 'updreg_ap-northeast-2'}],
            [{'text': 'Singapore (ap-southeast-1)', 'callback_data': 'updreg_ap-southeast-1'}],
            [{'text': 'Sydney (ap-southeast-2)', 'callback_data': 'updreg_ap-southeast-2'}],
            [{'text': 'Tokyo (ap-northeast-1)', 'callback_data': 'updreg_ap-northeast-1'}]
        ]
    elif area == 'ca':
        text += "Select <b>Canada</b> Region:"
        keyboard = [
            [{'text': 'Central (ca-central-1)', 'callback_data': 'updreg_ca-central-1'}]
        ]
    elif area == 'eu':
        text += "Select <b>Europe</b> Region:"
        keyboard = [
            [{'text': 'Frankfurt (eu-central-1)', 'callback_data': 'updreg_eu-central-1'}],
            [{'text': 'Ireland (eu-west-1)', 'callback_data': 'updreg_eu-west-1'}],
            [{'text': 'London (eu-west-2)', 'callback_data': 'updreg_eu-west-2'}],
            [{'text': 'Paris (eu-west-3)', 'callback_data': 'updreg_eu-west-3'}],
            [{'text': 'Stockholm (eu-north-1)', 'callback_data': 'updreg_eu-north-1'}]
        ]
    elif area == 'sa':
        text += "Select <b>South America</b> Region:"
        keyboard = [
            [{'text': 'São Paulo (sa-east-1)', 'callback_data': 'updreg_sa-east-1'}]
        ]
    
    keyboard.append([{'text': '🔙 Back to Regions', 'callback_data': 'upd_back_area'}])
    keyboard.append([{'text': '❌ Cancel', 'callback_data': 'upd_cancel'}])
    
    msg_id = context.user_data.get('update_region_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return UPDATE_REGION

async def update_region_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle region selection for update"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    logger.info(f"update_region_handler called with data: {query.data}")
    
    # Handle back to area selection
    if query.data == 'upd_back_area':
        text = "<b>🌍 Update AWS Region</b>\n\n"
        text += "Select your <b>Geographic Region</b>:"
        
        keyboard = [
            [{'text': '🇺🇸 United States', 'callback_data': 'updarea_us'}],
            [{'text': '🇮🇳 Asia Pacific', 'callback_data': 'updarea_ap'}],
            [{'text': '🇨🇦 Canada', 'callback_data': 'updarea_ca'}],
            [{'text': '🇪🇺 Europe', 'callback_data': 'updarea_eu'}],
            [{'text': '🇦🇺 South America', 'callback_data': 'updarea_sa'}],
            [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account'}],
            [{'text': '❌ Cancel', 'callback_data': 'upd_cancel'}]
        ]
        
        msg_id = context.user_data.get('update_region_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return UPDATE_REGION_AREA
    
    if query.data == 'upd_cancel' or query.data == 'back_to_account':
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        keyboard = [
            [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
            [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
            [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        msg_id = context.user_data.get('update_region_message_id', query.message.message_id)
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        context.user_data.clear()
        return ConversationHandler.END
    
    region = query.data.replace('updreg_', '')
    
    # Get user data
    user = user_db.get_user(chat_id)
    msg_id = context.user_data.get('update_region_message_id')
    
    # Show verifying message
    text = "<b>🌍 Update AWS Region</b>\n\n"
    text += "⏳ Verifying region...\n\n"
    text += f"New Region: {region}"
    
    if msg_id:
        edit_message(chat_id, msg_id, text)
    
    # Test credentials with new region
    try:
        test_ec2 = boto3.client(
            'ec2',
            region_name=region,
            aws_access_key_id=user['aws_access_key'],
            aws_secret_access_key=user['aws_secret_key']
        )
        # Try to describe instances to verify
        test_ec2.describe_instances(MaxResults=5)
        
        # Update region in database
        user_db.update_user_region(chat_id, region)
        
        # Get updated user data
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        keyboard = [
            [{'text': '✅ Region Updated', 'callback_data': 'disabled'}],
            [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
            [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        else:
            send_message(chat_id, text, {'inline_keyboard': keyboard})
        
        context.user_data.clear()
        return ConversationHandler.END
        
    except Exception as e:
        text = f"<b>❌ Region Verification Failed</b>\n\n"
        text += f"Error: {str(e)}\n\n"
        text += "Please try again or contact support."
        
        keyboard = [
            [{'text': '🔙 Back to Regions', 'callback_data': 'upd_back_area'}],
            [{'text': '❌ Cancel', 'callback_data': 'upd_cancel'}]
        ]
        
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        else:
            send_message(chat_id, text, {'inline_keyboard': keyboard})
        
        return UPDATE_REGION

# ==================== UPDATE TIMEZONE HANDLERS ====================

async def update_timezone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start update timezone flow from button"""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    
    text = "<b>⏰ Update Timezone</b>\n\n"
    text += "Select your <b>Timezone Area</b>:"
    
    keyboard = [
        [{'text': '🇺🇸 United States', 'callback_data': 'updtzarea_us'}],
        [{'text': '🇮🇳 Asia', 'callback_data': 'updtzarea_asia'}],
        [{'text': '🇪🇺 Europe', 'callback_data': 'updtzarea_europe'}],
        [{'text': '🇦🇺 Australia/Pacific', 'callback_data': 'updtzarea_pacific'}],
        [{'text': '🌎 Americas', 'callback_data': 'updtzarea_americas'}],
        [{'text': '🌍 Africa', 'callback_data': 'updtzarea_africa'}],
        [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account_from_tz'}],
        [{'text': '❌ Cancel', 'callback_data': 'updtz_cancel'}]
    ]
    
    msg_id = query.message.message_id
    context.user_data['update_timezone_message_id'] = msg_id
    edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return UPDATE_TIMEZONE_AREA

async def update_timezone_area_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone area selection for update"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    if query.data == 'updtz_cancel' or query.data == 'back_to_account_from_tz':
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Timezone: {user['timezone']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        keyboard = [
            [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
            [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
            [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        msg_id = context.user_data.get('update_timezone_message_id', query.message.message_id)
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        context.user_data.clear()
        return ConversationHandler.END
    
    tz_area = query.data.replace('updtzarea_', '')
    context.user_data['selected_tz_area'] = tz_area
    
    text = "<b>⏰ Update Timezone</b>\n\n"
    text += "Select your <b>Timezone</b>:"
    
    keyboard = []
    
    if tz_area == 'us':
        keyboard = [
            [{'text': 'Eastern (New York)', 'callback_data': 'updtz_America/New_York'}],
            [{'text': 'Central (Chicago)', 'callback_data': 'updtz_America/Chicago'}],
            [{'text': 'Mountain (Denver)', 'callback_data': 'updtz_America/Denver'}],
            [{'text': 'Pacific (Los Angeles)', 'callback_data': 'updtz_America/Los_Angeles'}],
            [{'text': 'Alaska', 'callback_data': 'updtz_America/Anchorage'}],
            [{'text': 'Hawaii', 'callback_data': 'updtz_Pacific/Honolulu'}]
        ]
    elif tz_area == 'asia':
        keyboard = [
            [{'text': 'India (Kolkata)', 'callback_data': 'updtz_Asia/Kolkata'}],
            [{'text': 'China (Shanghai)', 'callback_data': 'updtz_Asia/Shanghai'}],
            [{'text': 'Japan (Tokyo)', 'callback_data': 'updtz_Asia/Tokyo'}],
            [{'text': 'Singapore', 'callback_data': 'updtz_Asia/Singapore'}],
            [{'text': 'Hong Kong', 'callback_data': 'updtz_Asia/Hong_Kong'}],
            [{'text': 'Dubai', 'callback_data': 'updtz_Asia/Dubai'}],
            [{'text': 'Bangkok', 'callback_data': 'updtz_Asia/Bangkok'}],
            [{'text': 'Seoul', 'callback_data': 'updtz_Asia/Seoul'}]
        ]
    elif tz_area == 'europe':
        keyboard = [
            [{'text': 'London (GMT)', 'callback_data': 'updtz_Europe/London'}],
            [{'text': 'Paris', 'callback_data': 'updtz_Europe/Paris'}],
            [{'text': 'Berlin', 'callback_data': 'updtz_Europe/Berlin'}],
            [{'text': 'Amsterdam', 'callback_data': 'updtz_Europe/Amsterdam'}],
            [{'text': 'Rome', 'callback_data': 'updtz_Europe/Rome'}],
            [{'text': 'Madrid', 'callback_data': 'updtz_Europe/Madrid'}],
            [{'text': 'Stockholm', 'callback_data': 'updtz_Europe/Stockholm'}],
            [{'text': 'Moscow', 'callback_data': 'updtz_Europe/Moscow'}]
        ]
    elif tz_area == 'pacific':
        keyboard = [
            [{'text': 'Sydney', 'callback_data': 'updtz_Australia/Sydney'}],
            [{'text': 'Melbourne', 'callback_data': 'updtz_Australia/Melbourne'}],
            [{'text': 'Brisbane', 'callback_data': 'updtz_Australia/Brisbane'}],
            [{'text': 'Auckland', 'callback_data': 'updtz_Pacific/Auckland'}],
            [{'text': 'Fiji', 'callback_data': 'updtz_Pacific/Fiji'}]
        ]
    elif tz_area == 'americas':
        keyboard = [
            [{'text': 'Toronto', 'callback_data': 'updtz_America/Toronto'}],
            [{'text': 'Vancouver', 'callback_data': 'updtz_America/Vancouver'}],
            [{'text': 'Mexico City', 'callback_data': 'updtz_America/Mexico_City'}],
            [{'text': 'São Paulo', 'callback_data': 'updtz_America/Sao_Paulo'}],
            [{'text': 'Buenos Aires', 'callback_data': 'updtz_America/Argentina/Buenos_Aires'}],
            [{'text': 'Santiago', 'callback_data': 'updtz_America/Santiago'}]
        ]
    elif tz_area == 'africa':
        keyboard = [
            [{'text': 'Cairo', 'callback_data': 'updtz_Africa/Cairo'}],
            [{'text': 'Johannesburg', 'callback_data': 'updtz_Africa/Johannesburg'}],
            [{'text': 'Lagos', 'callback_data': 'updtz_Africa/Lagos'}],
            [{'text': 'Nairobi', 'callback_data': 'updtz_Africa/Nairobi'}]
        ]
    
    keyboard.append([{'text': '🔙 Back to Timezone Areas', 'callback_data': 'updtz_back_area'}])
    keyboard.append([{'text': '❌ Cancel', 'callback_data': 'updtz_cancel'}])
    
    msg_id = context.user_data.get('update_timezone_message_id')
    if msg_id:
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return UPDATE_TIMEZONE

async def update_timezone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone selection for update"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    
    if query.data == 'updtz_back_area':
        text = "<b>⏰ Update Timezone</b>\n\n"
        text += "Select your <b>Timezone Area</b>:"
        
        keyboard = [
            [{'text': '🇺🇸 United States', 'callback_data': 'updtzarea_us'}],
            [{'text': '🇮🇳 Asia', 'callback_data': 'updtzarea_asia'}],
            [{'text': '🇪🇺 Europe', 'callback_data': 'updtzarea_europe'}],
            [{'text': '🇦🇺 Australia/Pacific', 'callback_data': 'updtzarea_pacific'}],
            [{'text': '🌎 Americas', 'callback_data': 'updtzarea_americas'}],
            [{'text': '🌍 Africa', 'callback_data': 'updtzarea_africa'}],
            [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account_from_tz'}],
            [{'text': '❌ Cancel', 'callback_data': 'updtz_cancel'}]
        ]
        
        msg_id = context.user_data.get('update_timezone_message_id')
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return UPDATE_TIMEZONE_AREA
    
    if query.data == 'updtz_cancel' or query.data == 'back_to_account_from_tz':
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Timezone: {user['timezone']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        keyboard = [
            [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
            [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
            [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        msg_id = context.user_data.get('update_timezone_message_id', query.message.message_id)
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        context.user_data.clear()
        return ConversationHandler.END
    
    new_timezone = query.data.replace('updtz_', '')
    
    # Get user data
    user = user_db.get_user(chat_id)
    old_timezone = user['timezone']
    msg_id = context.user_data.get('update_timezone_message_id')
    
    # Show updating message
    text = "<b>⏰ Update Timezone</b>\n\n"
    text += "⏳ Updating timezone and rescheduling jobs...\n\n"
    text += f"Old: {old_timezone}\n"
    text += f"New: {new_timezone}"
    
    if msg_id:
        edit_message(chat_id, msg_id, text)
    
    try:
        # Validate timezone
        tz = pytz.timezone(new_timezone)
        
        # Get all user's schedules
        schedules = user_db.get_schedules(chat_id)
        
        # Remove all old scheduled jobs
        for sched in schedules:
            try:
                hour, minute = map(int, sched['time'].split(':'))
                job_id = f"{sched['action']}_{sched['instance_id']}_{chat_id}_{hour}_{minute}"
                scheduler.remove_job(job_id)
                logger.info(f"Removed old job: {job_id}")
            except Exception as e:
                logger.warning(f"Could not remove job: {e}")
        
        # Update timezone in database
        user_db.update_user_timezone(chat_id, new_timezone)
        
        # Re-add all scheduled jobs with new timezone
        for sched in schedules:
            try:
                hour, minute = map(int, sched['time'].split(':'))
                job_id = f"{sched['action']}_{sched['instance_id']}_{chat_id}_{hour}_{minute}"
                
                if sched['action'] == 'start':
                    job = scheduler.add_job(
                        scheduled_start_instance,
                        CronTrigger(hour=hour, minute=minute, timezone=tz),
                        args=[sched['instance_id'], chat_id],
                        id=job_id,
                        replace_existing=True
                    )
                elif sched['action'] == 'stop':
                    job = scheduler.add_job(
                        scheduled_stop_instance,
                        CronTrigger(hour=hour, minute=minute, timezone=tz),
                        args=[sched['instance_id'], chat_id],
                        id=job_id,
                        replace_existing=True
                    )
                logger.info(f"Re-added job with new timezone: {job_id} - Next run: {job.next_run_time}")
            except Exception as e:
                logger.error(f"Error re-adding job: {e}")
        
        # Get updated user data
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Timezone: {user['timezone']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        if schedules:
            text += f"✅ {len(schedules)} schedule(s) updated to new timezone"
        
        keyboard = [
            [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
            [{'text': '✅ Timezone Updated', 'callback_data': 'disabled'}],
            [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        else:
            send_message(chat_id, text, {'inline_keyboard': keyboard})
        
        context.user_data.clear()
        return ConversationHandler.END
        
    except Exception as e:
        text = f"<b>❌ Timezone Update Failed</b>\n\n"
        text += f"Error: {str(e)}\n\n"
        text += "Please try again or contact support."
        
        keyboard = [
            [{'text': '🔙 Back to Timezone Areas', 'callback_data': 'updtz_back_area'}],
            [{'text': '❌ Cancel', 'callback_data': 'updtz_cancel'}]
        ]
        
        if msg_id:
            edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        else:
            send_message(chat_id, text, {'inline_keyboard': keyboard})
        
        return UPDATE_TIMEZONE

# ==================== INSTANCE CONTROL COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        # Check if whitelisted
        if not is_whitelisted(chat_id):
            text = "⛔ <b>Access Denied</b>\n\n"
            text += "You are not authorized to use this bot.\n\n"
            text += f"Your Chat ID: <code>{chat_id}</code>\n\n"
            text += f"Contact the administrator to request access {ADMIN_USERNAME}"
            send_message(chat_id, text)
            return
        
        # Welcome message for new users
        text = "<b>🤖 Welcome to Multi-User EC2 Control Bot!</b>\n\n"
        text += "This bot allows you to control your AWS EC2 instances directly from Telegram.\n\n"
        text += "<b>To get started:</b>\n"
        text += "1️⃣ Send /register\n"
        text += "2️⃣ Enter your AWS Access Key\n"
        text += "3️⃣ Enter your AWS Secret Key\n"
        text += "4️⃣ Select your AWS Region\n\n"
        text += "Your credentials will be encrypted and stored securely.\n\n"
        text += "Ready? Send /register to begin!"
        
        keyboard = [
            [{'text': '📖 How to Create AWS Access Keys?', 'callback_data': 'show_aws_guide'}]
        ]
        
        send_message(chat_id, text, {'inline_keyboard': keyboard})
        return
    
    # For registered users, show stopped instances
    ec2 = get_ec2_client(chat_id)
    if not ec2:
        send_message(chat_id, "❌ Error getting AWS credentials")
        return
    
    try:
        response = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['stopped']}])
        instances = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                name = get_instance_name(ec2, instance_id)
                instances.append({'id': instance_id, 'name': name})
        
        if not instances:
            send_message(chat_id, "No stopped instances found")
            return
        
        keyboard = []
        text = "<b>⏸️ Stopped Instances:</b>\n\n"
        for inst in instances:
            text += f"• {inst['name']} ({inst['id']})\n\n"
            keyboard.append([{'text': f"▶️ Start {inst['name']}", 'callback_data': f"start_{inst['id']}"}])
        
        send_message(chat_id, text, {'inline_keyboard': keyboard})
    except Exception as e:
        send_message(chat_id, f"❌ Error: {e}")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        send_message(chat_id, "⛔ You are not registered\n\nUse /register to get started")
        return
    
    ec2 = get_ec2_client(chat_id)
    if not ec2:
        send_message(chat_id, "❌ Error getting AWS credentials")
        return
    
    try:
        response = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
        instances = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                name = get_instance_name(ec2, instance_id)
                instances.append({'id': instance_id, 'name': name})
        
        if not instances:
            send_message(chat_id, "No running instances")
            return
        
        keyboard = []
        text = "<b>🟢 Running Instances:</b>\n\n"
        for inst in instances:
            text += f"• {inst['name']} ({inst['id']})\n\n"
            keyboard.append([{'text': f"⏹️ Stop {inst['name']}", 'callback_data': f"stop_{inst['id']}"}])
        
        send_message(chat_id, text, {'inline_keyboard': keyboard})
    except Exception as e:
        send_message(chat_id, f"❌ Error: {e}")

async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reboot command"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        send_message(chat_id, "⛔ You are not registered\n\nUse /register to get started")
        return
    
    ec2 = get_ec2_client(chat_id)
    if not ec2:
        send_message(chat_id, "❌ Error getting AWS credentials")
        return
    
    try:
        response = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
        instances = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                name = get_instance_name(ec2, instance_id)
                instances.append({'id': instance_id, 'name': name})
        
        if not instances:
            send_message(chat_id, "No running instances")
            return
        
        keyboard = []
        text = "<b>🔄 Reboot Instances:</b>\n\n"
        for inst in instances:
            text += f"• {inst['name']} ({inst['id']})\n\n"
            keyboard.append([{'text': f"🔄 Reboot {inst['name']}", 'callback_data': f"reboot_{inst['id']}"}])
        
        send_message(chat_id, text, {'inline_keyboard': keyboard})
    except Exception as e:
        send_message(chat_id, f"❌ Error: {e}")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        send_message(chat_id, "⛔ You are not registered\n\nUse /register to get started")
        return
    
    ec2 = get_ec2_client(chat_id)
    if not ec2:
        send_message(chat_id, "❌ Error getting AWS credentials")
        return
    
    try:
        response = ec2.describe_instances()
        instances = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                state = instance['State']['Name']
                name = get_instance_name(ec2, instance_id)
                
                if instance_id in instance_operation_states:
                    state = instance_operation_states[instance_id]
                
                instances.append({'id': instance_id, 'name': name, 'state': state})
        
        if not instances:
            send_message(chat_id, "No instances found")
            return
        
        text = "<b>🖥️ All Instances Status:</b>\n\n"
        for inst in instances:
            if inst['state'] in ['starting', 'stopping', 'rebooting']:
                emoji = "🟡"
            elif inst['state'] == 'running':
                emoji = "🟢"
            elif inst['state'] == 'stopped':
                emoji = "🔴"
            else:
                emoji = "🟡"
            
            text += f"{emoji} {inst['name']}\n"
            text += f"   ID: {inst['id']}\n"
            text += f"   Status: {inst['state']}\n\n"
        
        send_message(chat_id, text)
    except Exception as e:
        send_message(chat_id, f"❌ Error: {e}")

# ==================== HELP COMMAND ====================

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule command - Show options: Set, Clear, Status"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        send_message(chat_id, "⛔ You are not registered\n\nUse /register to get started")
        return
    
    context.user_data.clear()
    
    keyboard = [
        [{'text': '⏳ Set Schedule', 'callback_data': 'schedopt_set'}],
        [{'text': '📋 Schedule Status', 'callback_data': 'schedopt_status'}],
        [{'text': '❌ Cancel', 'callback_data': 'sched_cancel'}]
    ]
    
    text = "<b>⏰ Schedule Auto Start/Stop</b>\n\nChoose an option:"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        context.user_data['message_ids'] = [msg_id]
        context.user_data['first_schedule_message_id'] = msg_id
    
    return SCHEDULE_INSTANCE

async def schedule_instance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle schedule option selection"""
    query = update.callback_query
    chat_id = query.message.chat.id
    
    if not check_authorization(chat_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    await query.answer()
    
    # CRITICAL: Track message IDs
    if 'message_ids' not in context.user_data:
        context.user_data['message_ids'] = []
    if query.message.message_id not in context.user_data['message_ids']:
        context.user_data['message_ids'].append(query.message.message_id)
    
    # Handle cancel
    if query.data == 'sched_cancel':
        first_msg_id = context.user_data.get('first_schedule_message_id')
        if not first_msg_id:
            first_msg_id = query.message.message_id
        
        # Delete status message if it exists and is different from first message
        status_msg_id = context.user_data.get('schedule_status_message_id')
        if status_msg_id and status_msg_id != first_msg_id:
            try:
                delete_message(chat_id, status_msg_id)
            except:
                pass
        
        if 'current_instance_messages' in context.user_data:
            for msg_id in context.user_data['current_instance_messages']:
                try:
                    delete_message(chat_id, msg_id)
                except:
                    pass
        
        if 'instance_selection_message_id' in context.user_data:
            try:
                delete_message(chat_id, context.user_data['instance_selection_message_id'])
            except:
                pass
        
        keyboard = [
            [{'text': '⏳ Set Schedule', 'callback_data': 'schedopt_set'}],
            [{'text': '📋 Schedule Status', 'callback_data': 'schedopt_status'}],
            [{'text': '✅ Cancelled', 'callback_data': 'already_cancelled'}]
        ]
        text = "<b>⏰ Schedule Auto Start/Stop</b>\n\nChoose an option:"
        edit_message(chat_id, first_msg_id, text, {'inline_keyboard': keyboard})
        context.user_data.clear()
        return ConversationHandler.END
    
    # CRITICAL: Validate callback data to prevent processing wrong buttons
    valid_prefixes = ['schedopt_', 'schedins_', 'remschedins_', 'remsched_', 'sched_cancel', 'sched_back_main', 'sched_back_status', 'already_cancelled']
    if not any(query.data.startswith(prefix) or query.data == prefix for prefix in valid_prefixes):
        await query.answer("⚠️ Please use the current menu", show_alert=True)
        return SCHEDULE_INSTANCE
    
    # Ignore already_cancelled button
    if query.data == 'already_cancelled':
        await query.answer()
        return SCHEDULE_INSTANCE
    
    # Handle back to main schedule menu
    if query.data == 'sched_back_main':
        first_msg_id = context.user_data.get('first_schedule_message_id', query.message.message_id)
        
        keyboard = [
            [{'text': '⏳ Set Schedule', 'callback_data': 'schedopt_set'}],
            [{'text': '📋 Schedule Status', 'callback_data': 'schedopt_status'}],
            [{'text': '❌ Cancel', 'callback_data': 'sched_cancel'}]
        ]
        text = "<b>⏰ Schedule Auto Start/Stop</b>\n\nChoose an option:"
        edit_message(chat_id, first_msg_id, text, {'inline_keyboard': keyboard})
        return SCHEDULE_INSTANCE
    
    # Handle back to schedule status
    if query.data == 'sched_back_status':
        schedules = user_db.get_schedules(chat_id)
        
        if not schedules:
            text = "📅 No active schedules\n\nUse /schedule to create"
            keyboard = []
        else:
            text = "<b>📅 Active Schedules:</b>\n\n"
            
            instance_schedules = {}
            for sched in schedules:
                iid = sched['instance_id']
                if iid not in instance_schedules:
                    instance_schedules[iid] = {'start': None, 'stop': None}
                instance_schedules[iid][sched['action']] = sched['time']
            
            ec2 = get_ec2_client(chat_id)
            for iid, times in instance_schedules.items():
                instance_name = get_instance_name(ec2, iid)
                text += f"• <b>{instance_name}</b>\n"
                if times['start']:
                    text += f"     ▶️ Start: {format_time_12hr(times['start'])}\n"
                if times['stop']:
                    text += f"     ⏹️ Stop: {format_time_12hr(times['stop'])}\n"
                text += "\n"
            
            text += f"<b>Total Schedules:</b> {len(instance_schedules)}"
            
            keyboard = [
                [{'text': '🗑️ Clear Schedule', 'callback_data': 'schedopt_clear_show'}]
            ]
        
        status_msg_id = context.user_data.get('schedule_status_message_id', query.message.message_id)
        edit_message(chat_id, status_msg_id, text, {'inline_keyboard': keyboard})
        return SCHEDULE_INSTANCE
    
    # Handle option selection
    if query.data == 'schedopt_set':
        return await show_instance_selection(chat_id, context)
    elif query.data == 'schedopt_status':
        await query.answer()
        return await show_schedule_status(chat_id, context, query.message.message_id)
    elif query.data == 'schedopt_clear' or query.data == 'schedopt_clear_show':
        # Show clear schedule button
        schedules = user_db.get_schedules(chat_id)
        if not schedules:
            await query.answer("📅 No schedules to clear", show_alert=True)
            return SCHEDULE_INSTANCE
        
        await query.answer()
        
        # Get the message ID to edit
        msg_id = context.user_data.get('schedule_status_message_id', query.message.message_id)
        
        # Show clear schedule options by editing the message
        text = "<b>🗑️ Clear Schedules:</b>\n\n"
        
        instance_schedules = {}
        for sched in schedules:
            iid = sched['instance_id']
            if iid not in instance_schedules:
                instance_schedules[iid] = {'start': None, 'stop': None}
            instance_schedules[iid][sched['action']] = sched['time']
        
        ec2 = get_ec2_client(chat_id)
        for iid, times in instance_schedules.items():
            instance_name = get_instance_name(ec2, iid)
            text += f"• <b>{instance_name}</b>\n"
            if times['start']:
                text += f"     ▶️ Start: {format_time_12hr(times['start'])}\n"
            if times['stop']:
                text += f"     ⏹️ Stop: {format_time_12hr(times['stop'])}\n"
            text += "\n"
        
        text += f"<b>Total Schedules:</b> {len(instance_schedules)}"
        
        keyboard = []
        for iid in instance_schedules.keys():
            instance_name = get_instance_name(ec2, iid)
            keyboard.append([{'text': f"🗑️ {instance_name}", 'callback_data': f"remschedins_{iid}"}])
        
        keyboard.append([{'text': '🗑️ Clear All', 'callback_data': 'remsched_all'}])
        keyboard.append([{'text': '❌ Cancel', 'callback_data': 'sched_back_status'}])
        
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        
        return SCHEDULE_INSTANCE
    
    # Handle instance selection (from Set Schedule flow)
    if query.data.startswith('schedins_'):
        instance_id = query.data.replace('schedins_', '')
        
        # CRITICAL: Validate instance_id format
        if not instance_id or (instance_id != 'all' and not instance_id.startswith('i-')):
            await query.answer("❌ Invalid instance selection", show_alert=True)
            return SCHEDULE_INSTANCE
        
        # Delete previous instance flow messages if user is changing selection
        if 'current_instance_messages' in context.user_data:
            instance_selection_msg_id = context.user_data.get('instance_selection_message_id')
            for msg_id in context.user_data['current_instance_messages']:
                if msg_id != instance_selection_msg_id:
                    try:
                        delete_message(chat_id, msg_id)
                    except:
                        pass
        
        # Reset current instance messages tracking
        context.user_data['current_instance_messages'] = []
        context.user_data['schedule_instance'] = instance_id
        return await show_start_time_selection(chat_id, context, instance_id)
    
    # Handle clear schedule for specific instance
    if query.data.startswith('remschedins_'):
        instance_id = query.data.replace('remschedins_', '')
        logger.info(f"Clear schedule for instance: {instance_id}")
        
        removed_count = 0
        schedules_to_remove = user_db.get_schedules(chat_id, instance_id)
        logger.info(f"Found {len(schedules_to_remove)} schedules for instance {instance_id}")
        
        for sched in schedules_to_remove:
            try:
                hour, minute = map(int, sched['time'].split(':'))
                job_id = f"{sched['action']}_{instance_id}_{chat_id}_{hour}_{minute}"
                logger.info(f"Attempting to remove job: {job_id}")
                
                # Try to remove from scheduler
                try:
                    scheduler.remove_job(job_id)
                    logger.info(f"Removed job from scheduler: {job_id}")
                except Exception as e:
                    logger.warning(f"Job not found in scheduler: {job_id} - {e}")
                
                # Remove from database
                user_db.delete_schedule(chat_id, instance_id, sched['action'])
                removed_count += 1
                logger.info(f"Removed schedule from database: {job_id}")
            except Exception as e:
                logger.error(f"Error removing schedule: {e}")
        
        if removed_count > 0:
            ec2 = get_ec2_client(chat_id)
            instance_name = get_instance_name(ec2, instance_id)
            logger.info(f"Successfully removed {removed_count} schedules for {instance_name}")
            
            # Rebuild the message text with remaining schedules
            remaining_schedules = user_db.get_schedules(chat_id)
            
            if remaining_schedules:
                text = "<b>🗑️ Clear Schedule:</b>\n\n"
                
                instance_schedules = {}
                for sched in remaining_schedules:
                    iid = sched['instance_id']
                    if iid not in instance_schedules:
                        instance_schedules[iid] = {'start': None, 'stop': None}
                    instance_schedules[iid][sched['action']] = sched['time']
                
                for iid, times in instance_schedules.items():
                    inst_name = get_instance_name(ec2, iid)
                    text += f"• <b>{inst_name}</b>\n"
                    if times['start']:
                        text += f"     ▶️ Start: {format_time_12hr(times['start'])}\n"
                    if times['stop']:
                        text += f"     ⏹️ Stop: {format_time_12hr(times['stop'])}\n"
                    text += "\n"
                
                text += "Select instance to clear:"
            else:
                # All schedules cleared
                text = "<b>🗑️ All Schedules Cleared</b>"
            
            # Update keyboard
            if query.message.reply_markup and query.message.reply_markup.inline_keyboard:
                keyboard = []
                for row in query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        if button.callback_data == f"remschedins_{instance_id}":
                            new_row.append({'text': f"✅ {instance_name} Cleared", 'callback_data': 'disabled'})
                        else:
                            new_row.append({'text': button.text, 'callback_data': button.callback_data})
                    keyboard.append(new_row)
                
                edit_message(chat_id, query.message.message_id, text, {'inline_keyboard': keyboard})
        else:
            logger.warning(f"No schedules found to remove for instance {instance_id}")
            await query.answer("⚠️ No schedules found", show_alert=True)
        
        return SCHEDULE_INSTANCE
    
    # Handle clear all schedules
    if query.data == 'remsched_all':
        logger.info(f"Clear all schedules for user {chat_id}")
        
        removed_count = 0
        all_schedules = user_db.get_schedules(chat_id)
        logger.info(f"Found {len(all_schedules)} total schedules for user {chat_id}")
        
        for sched in all_schedules:
            try:
                hour, minute = map(int, sched['time'].split(':'))
                job_id = f"{sched['action']}_{sched['instance_id']}_{chat_id}_{hour}_{minute}"
                logger.info(f"Attempting to remove job: {job_id}")
                
                # Try to remove from scheduler
                try:
                    scheduler.remove_job(job_id)
                    logger.info(f"Removed job from scheduler: {job_id}")
                except Exception as e:
                    logger.warning(f"Job not found in scheduler: {job_id} - {e}")
                
                # Remove from database
                user_db.delete_schedule(chat_id, sched['instance_id'], sched['action'])
                removed_count += 1
                logger.info(f"Removed schedule from database: {job_id}")
            except Exception as e:
                logger.error(f"Error removing schedule: {e}")
        
        if removed_count > 0:
            logger.info(f"Successfully removed {removed_count} schedules")
            
            # Show all schedules cleared message
            text = "<b>🗑️ All Schedules Cleared</b>"
            
            if query.message.reply_markup and query.message.reply_markup.inline_keyboard:
                keyboard = []
                for row in query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        if button.callback_data == 'remsched_all':
                            new_row.append({'text': '✅ All Cleared', 'callback_data': 'disabled'})
                        elif button.callback_data.startswith('remschedins_'):
                            instance_id = button.callback_data.replace('remschedins_', '')
                            ec2 = get_ec2_client(chat_id)
                            instance_name = get_instance_name(ec2, instance_id)
                            new_row.append({'text': f"✅ {instance_name} Cleared", 'callback_data': 'disabled'})
                        else:
                            new_row.append({'text': button.text, 'callback_data': button.callback_data})
                    keyboard.append(new_row)
                
                edit_message(chat_id, query.message.message_id, text, {'inline_keyboard': keyboard})
        else:
            logger.warning(f"No schedules found to remove for user {chat_id}")
            await query.answer("⚠️ No schedules found", show_alert=True)
        
        return SCHEDULE_INSTANCE
    
    return ConversationHandler.END

async def show_instance_selection(chat_id, context):
    """Show instance selection for setting schedule"""
    ec2 = get_ec2_client(chat_id)
    if not ec2:
        send_message(chat_id, "❌ Error getting AWS credentials")
        return ConversationHandler.END
    
    response = ec2.describe_instances()
    instances = []
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            name = get_instance_name(ec2, instance_id)
            instances.append({'id': instance_id, 'name': name})
    
    if not instances:
        send_message(chat_id, "No instances found")
        return ConversationHandler.END
    
    context.user_data['current_instance_messages'] = []
    
    keyboard = []
    text = "<b>⏰ Set Schedule</b>\n\nSelect Instance:"
    
    for inst in instances:
        keyboard.append([{'text': inst['name'], 'callback_data': f"schedins_{inst['id']}"}])
    
    keyboard.append([{'text': '🌐 All Instances', 'callback_data': 'schedins_all'}])
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response_http = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response_http.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        context.user_data['instance_selection_message_id'] = msg_id
    
    return SCHEDULE_INSTANCE

async def show_clear_schedule(chat_id, context, message_id=None):
    """Show clear schedule options"""
    schedules = user_db.get_schedules(chat_id)
    if not schedules:
        text = "📅 No schedules to clear\n\nUse /schedule to create"
        send_message(chat_id, text)
        return SCHEDULE_INSTANCE
    
    keyboard = []
    
    instance_schedules = {}
    for sched in schedules:
        iid = sched['instance_id']
        if iid not in instance_schedules:
            instance_schedules[iid] = {'start': None, 'stop': None}
        instance_schedules[iid][sched['action']] = sched['time']
    
    ec2 = get_ec2_client(chat_id)
    for iid in instance_schedules.keys():
        instance_name = get_instance_name(ec2, iid)
        keyboard.append([{'text': f"🗑️ {instance_name}", 'callback_data': f"remschedins_{iid}"}])
    
    keyboard.append([{'text': '🗑️ Clear All', 'callback_data': 'remsched_all'}])
    keyboard.append([{'text': '❌ Cancel', 'callback_data': 'sched_back_main'}])
    
    text = "<b>🗑️ Clear Schedule</b>\n\nSelect instance to clear:"
    
    send_message(chat_id, text, {'inline_keyboard': keyboard})
    
    return SCHEDULE_INSTANCE

async def show_schedule_status(chat_id, context, message_id=None):
    """Show schedule status"""
    schedules = user_db.get_schedules(chat_id)
    if not schedules:
        text = "📅 No active schedules\n\nUse /schedule to create"
        send_message(chat_id, text)
    else:
        text = "<b>📅 Active Schedules:</b>\n\n"
        
        instance_schedules = {}
        for sched in schedules:
            iid = sched['instance_id']
            if iid not in instance_schedules:
                instance_schedules[iid] = {'start': None, 'stop': None}
            instance_schedules[iid][sched['action']] = sched['time']
        
        ec2 = get_ec2_client(chat_id)
        for iid, times in instance_schedules.items():
            instance_name = get_instance_name(ec2, iid)
            text += f"• <b>{instance_name}</b>\n"
            if times['start']:
                text += f"     ▶️ Start: {format_time_12hr(times['start'])}\n"
            if times['stop']:
                text += f"     ⏹️ Stop: {format_time_12hr(times['stop'])}\n"
            text += "\n"
        
        text += f"<b>Total Schedules:</b> {len(instance_schedules)}"
        
        keyboard = [
            [{'text': '🗑️ Clear Schedule', 'callback_data': 'schedopt_clear_show'}]
        ]
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
        encoded_data = json.dumps(payload).encode('utf-8')
        response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
        response_data = json.loads(response.data.decode('utf-8'))
        
        if response_data.get('ok'):
            msg_id = response_data['result']['message_id']
            context.user_data['schedule_status_message_id'] = msg_id
    
    return SCHEDULE_INSTANCE

async def show_start_time_selection(chat_id, context, instance_id):
    """Show start time selection"""
    ec2 = get_ec2_client(chat_id)
    instance_text = "All Instances" if instance_id == 'all' else get_instance_name(ec2, instance_id)
    
    keyboard = []
    text = f"<b>⏰ Schedule: {instance_text}</b>\n\nSet Start Time:"
    
    times = [
        [('12:00 AM', '00:00'), ('12:00 PM', '12:00')],
        [('01:00 AM', '01:00'), ('01:00 PM', '13:00')],
        [('02:00 AM', '02:00'), ('02:00 PM', '14:00')],
        [('03:00 AM', '03:00'), ('03:00 PM', '15:00')],
        [('04:00 AM', '04:00'), ('04:00 PM', '16:00')],
        [('05:00 AM', '05:00'), ('05:00 PM', '17:00')],
        [('06:00 AM', '06:00'), ('06:00 PM', '18:00')],
        [('07:00 AM', '07:00'), ('07:00 PM', '19:00')],
        [('08:00 AM', '08:00'), ('08:00 PM', '20:00')],
        [('09:00 AM', '09:00'), ('09:00 PM', '21:00')],
        [('10:00 AM', '10:00'), ('10:00 PM', '22:00')],
        [('11:00 AM', '11:00'), ('11:00 PM', '23:00')],
    ]
    
    for row in times:
        button_row = []
        for display, value in row:
            button_row.append({'text': display, 'callback_data': f"starttime_{value}"})
        keyboard.append(button_row)
    
    keyboard.append([{'text': '⏭️ Skip (No Auto Start)', 'callback_data': 'starttime_skip'}])
    keyboard.append([{'text': '⌨️ Custom Time', 'callback_data': 'starttime_custom'}])
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        if 'current_instance_messages' not in context.user_data:
            context.user_data['current_instance_messages'] = []
        context.user_data['current_instance_messages'].append(msg_id)
    
    return SCHEDULE_START_TIME

async def schedule_start_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle start time selection"""
    query = update.callback_query
    chat_id = query.message.chat.id
    
    if not check_authorization(chat_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    await query.answer()
    
    # Track message IDs
    if 'message_ids' not in context.user_data:
        context.user_data['message_ids'] = []
    if query.message.message_id not in context.user_data['message_ids']:
        context.user_data['message_ids'].append(query.message.message_id)
    
    first_msg_id = context.user_data.get('first_schedule_message_id')
    if first_msg_id and query.message.message_id != first_msg_id:
        if 'current_instance_messages' not in context.user_data:
            context.user_data['current_instance_messages'] = []
        if query.message.message_id not in context.user_data['current_instance_messages']:
            context.user_data['current_instance_messages'].append(query.message.message_id)
    
    # Handle cancel
    if query.data == 'sched_cancel':
        first_msg_id = context.user_data.get('first_schedule_message_id')
        if 'current_instance_messages' in context.user_data:
            for msg_id in context.user_data['current_instance_messages']:
                try:
                    delete_message(chat_id, msg_id)
                except:
                    pass
        if 'instance_selection_message_id' in context.user_data:
            try:
                delete_message(chat_id, context.user_data['instance_selection_message_id'])
            except:
                pass
        if first_msg_id:
            keyboard = [
                [{'text': '⏳ Set Schedule', 'callback_data': 'schedopt_set'}],
                [{'text': '📋 Schedule Status', 'callback_data': 'schedopt_status'}],
                [{'text': '✅ Cancelled', 'callback_data': 'already_cancelled'}]
            ]
            text = "<b>⏰ Schedule Auto Start/Stop</b>\n\nChoose an option:"
            edit_message(chat_id, first_msg_id, text, {'inline_keyboard': keyboard})
        context.user_data.clear()
        return ConversationHandler.END
    
    # CRITICAL: If user clicks on instance button (redirect to instance selection)
    if query.data.startswith('schedins_'):
        if 'current_instance_messages' in context.user_data:
            instance_selection_msg_id = context.user_data.get('instance_selection_message_id')
            for msg_id in context.user_data['current_instance_messages']:
                if msg_id != instance_selection_msg_id:
                    try:
                        delete_message(chat_id, msg_id)
                    except:
                        pass
            context.user_data['current_instance_messages'] = []
        
        instance_id = query.data.replace('schedins_', '')
        
        # Validate instance_id
        if not instance_id or (instance_id != 'all' and not instance_id.startswith('i-')):
            await query.answer("❌ Invalid instance selection", show_alert=True)
            return SCHEDULE_START_TIME
        
        context.user_data['schedule_instance'] = instance_id
        return await show_start_time_selection(chat_id, context, instance_id)
    
    # CRITICAL: If user clicks on option buttons (shouldn't happen but handle it)
    if query.data.startswith('schedopt_'):
        await query.answer("⚠️ Please use the current menu", show_alert=True)
        return SCHEDULE_START_TIME
    
    # CRITICAL: If user clicks on clear schedule buttons
    if query.data.startswith('remschedins_') or query.data == 'remsched_all':
        await query.answer("⚠️ Please use the current menu", show_alert=True)
        return SCHEDULE_START_TIME
    
    # Handle custom time input
    if query.data == 'starttime_custom':
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "Enter start time\n\nExamples:\n\n• <code>09:00 AM</code>\n\n• <code>6:30 PM</code>\n\n• <code>9:00</code>\n\n• <code>18:30</code>",
            'parse_mode': 'HTML'
        }
        encoded_data = json.dumps(payload).encode('utf-8')
        response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
        response_data = json.loads(response.data.decode('utf-8'))
        
        if response_data.get('ok'):
            msg_id = response_data['result']['message_id']
            if 'current_instance_messages' not in context.user_data:
                context.user_data['current_instance_messages'] = []
            context.user_data['current_instance_messages'].append(msg_id)
        
        return CUSTOM_START_TIME
    
    # Handle skip start time
    if query.data == 'starttime_skip':
        context.user_data['start_time'] = None
        
        # CRITICAL: Validate instance is set
        if 'schedule_instance' not in context.user_data:
            await query.answer("⚠️ Session expired, please start again", show_alert=True)
            return ConversationHandler.END
        
        instance_id = context.user_data['schedule_instance']
        ec2 = get_ec2_client(chat_id)
        instance_text = "All Instances" if instance_id == 'all' else get_instance_name(ec2, instance_id)
        
        keyboard = []
        text = f"<b>⏰ Schedule: {instance_text}</b>\n\nStart Time: <b>Manual</b>\nSet Stop Time:"
        
        times = [
            [('12:00 AM', '00:00'), ('12:00 PM', '12:00')],
            [('01:00 AM', '01:00'), ('01:00 PM', '13:00')],
            [('02:00 AM', '02:00'), ('02:00 PM', '14:00')],
            [('03:00 AM', '03:00'), ('03:00 PM', '15:00')],
            [('04:00 AM', '04:00'), ('04:00 PM', '16:00')],
            [('05:00 AM', '05:00'), ('05:00 PM', '17:00')],
            [('06:00 AM', '06:00'), ('06:00 PM', '18:00')],
            [('07:00 AM', '07:00'), ('07:00 PM', '19:00')],
            [('08:00 AM', '08:00'), ('08:00 PM', '20:00')],
            [('09:00 AM', '09:00'), ('09:00 PM', '21:00')],
            [('10:00 AM', '10:00'), ('10:00 PM', '22:00')],
            [('11:00 AM', '11:00'), ('11:00 PM', '23:00')],
        ]
        
        for row in times:
            button_row = []
            for display, value in row:
                button_row.append({'text': display, 'callback_data': f"stoptime_{value}"})
            keyboard.append(button_row)
        
        keyboard.append([{'text': '⏭️ Skip (No Auto Stop)', 'callback_data': 'stoptime_skip'}])
        keyboard.append([{'text': '⌨️ Custom Time', 'callback_data': 'stoptime_custom'}])
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
        encoded_data = json.dumps(payload).encode('utf-8')
        response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
        response_data = json.loads(response.data.decode('utf-8'))
        
        if response_data.get('ok'):
            msg_id = response_data['result']['message_id']
            if 'current_instance_messages' not in context.user_data:
                context.user_data['current_instance_messages'] = []
            context.user_data['current_instance_messages'].append(msg_id)
        
        return SCHEDULE_STOP_TIME
    
    # CRITICAL: Validate that this is actually a start time callback
    if not query.data.startswith('starttime_'):
        await query.answer("⚠️ Invalid selection", show_alert=True)
        return SCHEDULE_START_TIME
    
    # CRITICAL: Validate instance is set
    if 'schedule_instance' not in context.user_data:
        await query.answer("⚠️ Session expired, please start again", show_alert=True)
        return ConversationHandler.END
    
    start_time = query.data.replace('starttime_', '')
    
    # Validate time format
    try:
        hour, minute = map(int, start_time.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except:
        await query.answer("❌ Invalid time format", show_alert=True)
        return SCHEDULE_START_TIME
    
    context.user_data['start_time'] = start_time
    
    instance_id = context.user_data['schedule_instance']
    ec2 = get_ec2_client(chat_id)
    instance_text = "All Instances" if instance_id == 'all' else get_instance_name(ec2, instance_id)
    
    keyboard = []
    text = f"<b>⏰ Schedule: {instance_text}</b>\n\nStart Time: <b>{start_time}</b>\nSet Stop Time:"
    
    times = [
        [('12:00 AM', '00:00'), ('12:00 PM', '12:00')],
        [('01:00 AM', '01:00'), ('01:00 PM', '13:00')],
        [('02:00 AM', '02:00'), ('02:00 PM', '14:00')],
        [('03:00 AM', '03:00'), ('03:00 PM', '15:00')],
        [('04:00 AM', '04:00'), ('04:00 PM', '16:00')],
        [('05:00 AM', '05:00'), ('05:00 PM', '17:00')],
        [('06:00 AM', '06:00'), ('06:00 PM', '18:00')],
        [('07:00 AM', '07:00'), ('07:00 PM', '19:00')],
        [('08:00 AM', '08:00'), ('08:00 PM', '20:00')],
        [('09:00 AM', '09:00'), ('09:00 PM', '21:00')],
        [('10:00 AM', '10:00'), ('10:00 PM', '22:00')],
        [('11:00 AM', '11:00'), ('11:00 PM', '23:00')],
    ]
    
    for row in times:
        button_row = []
        for display, value in row:
            button_row.append({'text': display, 'callback_data': f"stoptime_{value}"})
        keyboard.append(button_row)
    
    keyboard.append([{'text': '⏭️ Skip (No Auto Stop)', 'callback_data': 'stoptime_skip'}])
    keyboard.append([{'text': '⌨️ Custom Time', 'callback_data': 'stoptime_custom'}])
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        if 'current_instance_messages' not in context.user_data:
            context.user_data['current_instance_messages'] = []
        context.user_data['current_instance_messages'].append(msg_id)
    
    return SCHEDULE_STOP_TIME

async def schedule_stop_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle stop time selection"""
    query = update.callback_query
    chat_id = query.message.chat.id
    
    if not check_authorization(chat_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    await query.answer()
    
    # Track message IDs
    if 'message_ids' not in context.user_data:
        context.user_data['message_ids'] = []
    if query.message.message_id not in context.user_data['message_ids']:
        context.user_data['message_ids'].append(query.message.message_id)
    
    first_msg_id = context.user_data.get('first_schedule_message_id')
    if first_msg_id and query.message.message_id != first_msg_id:
        if 'current_instance_messages' not in context.user_data:
            context.user_data['current_instance_messages'] = []
        if query.message.message_id not in context.user_data['current_instance_messages']:
            context.user_data['current_instance_messages'].append(query.message.message_id)
    
    # Handle cancel
    if query.data == 'sched_cancel':
        first_msg_id = context.user_data.get('first_schedule_message_id')
        if 'current_instance_messages' in context.user_data:
            for msg_id in context.user_data['current_instance_messages']:
                try:
                    delete_message(chat_id, msg_id)
                except:
                    pass
        if 'instance_selection_message_id' in context.user_data:
            try:
                delete_message(chat_id, context.user_data['instance_selection_message_id'])
            except:
                pass
        if first_msg_id:
            keyboard = [
                [{'text': '⏳ Set Schedule', 'callback_data': 'schedopt_set'}],
                [{'text': '📋 Schedule Status', 'callback_data': 'schedopt_status'}],
                [{'text': '✅ Cancelled', 'callback_data': 'already_cancelled'}]
            ]
            text = "<b>⏰ Schedule Auto Start/Stop</b>\n\nChoose an option:"
            edit_message(chat_id, first_msg_id, text, {'inline_keyboard': keyboard})
        context.user_data.clear()
        return ConversationHandler.END
    
    # CRITICAL: If user clicks on instance button (redirect)
    if query.data.startswith('schedins_'):
        if 'current_instance_messages' in context.user_data:
            instance_selection_msg_id = context.user_data.get('instance_selection_message_id')
            for msg_id in context.user_data['current_instance_messages']:
                if msg_id != instance_selection_msg_id:
                    try:
                        delete_message(chat_id, msg_id)
                    except:
                        pass
            context.user_data['current_instance_messages'] = []
        
        instance_id = query.data.replace('schedins_', '')
        
        # Validate instance_id
        if not instance_id or (instance_id != 'all' and not instance_id.startswith('i-')):
            await query.answer("❌ Invalid instance selection", show_alert=True)
            return SCHEDULE_STOP_TIME
        
        context.user_data['schedule_instance'] = instance_id
        return await show_start_time_selection(chat_id, context, instance_id)
    
    # CRITICAL: If user clicks on start time button (going back)
    if query.data.startswith('starttime_'):
        # Validate instance is set
        if 'schedule_instance' not in context.user_data:
            await query.answer("⚠️ Session expired, please start again", show_alert=True)
            return ConversationHandler.END
        
        start_time = query.data.replace('starttime_', '')
        
        # Validate time format
        try:
            hour, minute = map(int, start_time.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except:
            await query.answer("❌ Invalid time format", show_alert=True)
            return SCHEDULE_STOP_TIME
        
        context.user_data['start_time'] = start_time
        instance_id = context.user_data.get('schedule_instance')
        
        ec2 = get_ec2_client(chat_id)
        instance_text = "All Instances" if instance_id == 'all' else get_instance_name(ec2, instance_id)
        
        keyboard = []
        text = f"<b>⏰ Schedule: {instance_text}</b>\n\nStart Time: <b>{start_time}</b>\nSet Stop Time:"
        
        times = [
            [('12:00 AM', '00:00'), ('12:00 PM', '12:00')],
            [('01:00 AM', '01:00'), ('01:00 PM', '13:00')],
            [('02:00 AM', '02:00'), ('02:00 PM', '14:00')],
            [('03:00 AM', '03:00'), ('03:00 PM', '15:00')],
            [('04:00 AM', '04:00'), ('04:00 PM', '16:00')],
            [('05:00 AM', '05:00'), ('05:00 PM', '17:00')],
            [('06:00 AM', '06:00'), ('06:00 PM', '18:00')],
            [('07:00 AM', '07:00'), ('07:00 PM', '19:00')],
            [('08:00 AM', '08:00'), ('08:00 PM', '20:00')],
            [('09:00 AM', '09:00'), ('09:00 PM', '21:00')],
            [('10:00 AM', '10:00'), ('10:00 PM', '22:00')],
            [('11:00 AM', '11:00'), ('11:00 PM', '23:00')],
        ]
        
        for row in times:
            button_row = []
            for display, value in row:
                button_row.append({'text': display, 'callback_data': f"stoptime_{value}"})
            keyboard.append(button_row)
        
        keyboard.append([{'text': '⏭️ Skip (No Auto Stop)', 'callback_data': 'stoptime_skip'}])
        keyboard.append([{'text': '⌨️ Custom Time', 'callback_data': 'stoptime_custom'}])
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
        encoded_data = json.dumps(payload).encode('utf-8')
        response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
        response_data = json.loads(response.data.decode('utf-8'))
        
        if response_data.get('ok'):
            msg_id = response_data['result']['message_id']
            if 'current_instance_messages' not in context.user_data:
                context.user_data['current_instance_messages'] = []
            context.user_data['current_instance_messages'].append(msg_id)
        
        return SCHEDULE_STOP_TIME
    
    # CRITICAL: If user clicks on option buttons
    if query.data.startswith('schedopt_'):
        await query.answer("⚠️ Please use the current menu", show_alert=True)
        return SCHEDULE_STOP_TIME
    
    # CRITICAL: If user clicks on clear schedule buttons
    if query.data.startswith('remschedins_') or query.data == 'remsched_all':
        await query.answer("⚠️ Please use the current menu", show_alert=True)
        return SCHEDULE_STOP_TIME
    
    # Handle custom time input
    if query.data == 'stoptime_custom':
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "Enter stop time\n\nExamples:\n\n• <code>09:00 AM</code>\n\n• <code>6:30 PM</code>\n\n• <code>9:00</code>\n\n• <code>18:30</code>",
            'parse_mode': 'HTML'
        }
        encoded_data = json.dumps(payload).encode('utf-8')
        response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
        response_data = json.loads(response.data.decode('utf-8'))
        
        if response_data.get('ok'):
            msg_id = response_data['result']['message_id']
            if 'current_instance_messages' not in context.user_data:
                context.user_data['current_instance_messages'] = []
            context.user_data['current_instance_messages'].append(msg_id)
        
        return CUSTOM_STOP_TIME
    
    # CRITICAL: Validate that this is actually a stop time callback
    if query.data != 'stoptime_skip' and not query.data.startswith('stoptime_'):
        await query.answer("⚠️ Invalid selection", show_alert=True)
        return SCHEDULE_STOP_TIME
    
    # CRITICAL: Validate required data exists
    if 'schedule_instance' not in context.user_data or 'start_time' not in context.user_data:
        await query.answer("⚠️ Session expired, please start again", show_alert=True)
        return ConversationHandler.END
    
    stop_time = None if query.data == 'stoptime_skip' else query.data.replace('stoptime_', '')
    
    # Validate stop time format if provided
    if stop_time:
        try:
            hour, minute = map(int, stop_time.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except:
            await query.answer("❌ Invalid time format", show_alert=True)
            return SCHEDULE_STOP_TIME
    
    return await create_schedule_final(chat_id, context, stop_time)

async def custom_start_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom start time input"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        return ConversationHandler.END
    
    if 'schedule_instance' not in context.user_data:
        send_message(chat_id, "❌ Session expired. Please start again with /schedule")
        return ConversationHandler.END
    
    time_input = update.message.text.strip()
    
    if time_input.lower() == 'cancel':
        return ConversationHandler.END
    
    try:
        time_input_upper = time_input.upper()
        if 'AM' in time_input_upper or 'PM' in time_input_upper:
            time_input_clean = time_input_upper.replace(' ', '')
            if 'AM' in time_input_clean:
                time_part = time_input_clean.replace('AM', '')
                hour, minute = map(int, time_part.split(':'))
                if hour == 12:
                    hour = 0
            else:
                time_part = time_input_clean.replace('PM', '')
                hour, minute = map(int, time_part.split(':'))
                if hour != 12:
                    hour += 12
        else:
            hour, minute = map(int, time_input.split(':'))
        
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        start_time = f"{hour:02d}:{minute:02d}"
    except:
        send_message(chat_id, "❌ Invalid format\n\nUse:\n• <code>09:00 AM</code>\n• <code>6:30 PM</code>\n• <code>18:30</code>")
        return CUSTOM_START_TIME
    
    context.user_data['start_time'] = start_time
    instance_id = context.user_data['schedule_instance']
    ec2 = get_ec2_client(chat_id)
    instance_text = "All Instances" if instance_id == 'all' else get_instance_name(ec2, instance_id)
    
    keyboard = []
    text = f"<b>⏰ Schedule: {instance_text}</b>\n\nStart Time: <b>{start_time}</b>\nSet Stop Time:"
    
    times = [
        [('12:00 AM', '00:00'), ('12:00 PM', '12:00')],
        [('01:00 AM', '01:00'), ('01:00 PM', '13:00')],
        [('02:00 AM', '02:00'), ('02:00 PM', '14:00')],
        [('03:00 AM', '03:00'), ('03:00 PM', '15:00')],
        [('04:00 AM', '04:00'), ('04:00 PM', '16:00')],
        [('05:00 AM', '05:00'), ('05:00 PM', '17:00')],
        [('06:00 AM', '06:00'), ('06:00 PM', '18:00')],
        [('07:00 AM', '07:00'), ('07:00 PM', '19:00')],
        [('08:00 AM', '08:00'), ('08:00 PM', '20:00')],
        [('09:00 AM', '09:00'), ('09:00 PM', '21:00')],
        [('10:00 AM', '10:00'), ('10:00 PM', '22:00')],
        [('11:00 AM', '11:00'), ('11:00 PM', '23:00')],
    ]
    
    for row in times:
        button_row = []
        for display, value in row:
            button_row.append({'text': display, 'callback_data': f"stoptime_{value}"})
        keyboard.append(button_row)
    
    keyboard.append([{'text': '⏭️ Skip (No Auto Stop)', 'callback_data': 'stoptime_skip'}])
    keyboard.append([{'text': '⌨️ Custom Time', 'callback_data': 'stoptime_custom'}])
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        if 'current_instance_messages' not in context.user_data:
            context.user_data['current_instance_messages'] = []
        context.user_data['current_instance_messages'].append(msg_id)
    
    return SCHEDULE_STOP_TIME

async def custom_stop_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom stop time input"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        return ConversationHandler.END
    
    if 'schedule_instance' not in context.user_data or 'start_time' not in context.user_data:
        send_message(chat_id, "❌ Session expired. Please start again with /schedule")
        return ConversationHandler.END
    
    time_input = update.message.text.strip()
    
    if time_input.lower() == 'cancel':
        return ConversationHandler.END
    
    try:
        time_input_upper = time_input.upper()
        if 'AM' in time_input_upper or 'PM' in time_input_upper:
            time_input_clean = time_input_upper.replace(' ', '')
            if 'AM' in time_input_clean:
                time_part = time_input_clean.replace('AM', '')
                hour, minute = map(int, time_part.split(':'))
                if hour == 12:
                    hour = 0
            else:
                time_part = time_input_clean.replace('PM', '')
                hour, minute = map(int, time_part.split(':'))
                if hour != 12:
                    hour += 12
        else:
            hour, minute = map(int, time_input.split(':'))
        
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        stop_time = f"{hour:02d}:{minute:02d}"
    except:
        send_message(chat_id, "❌ Invalid format\n\nUse:\n• <code>09:00 PM</code>\n• <code>11:30 PM</code>\n• <code>22:00</code>")
        return CUSTOM_STOP_TIME
    
    return await create_schedule_final(chat_id, context, stop_time)

async def create_schedule_final(chat_id, context, stop_time):
    """Create the final schedule"""
    # CRITICAL: Validate all required data exists
    if 'schedule_instance' not in context.user_data:
        send_message(chat_id, "❌ Error: Session data missing. Please start again with /schedule")
        return ConversationHandler.END
    
    instance_id = context.user_data['schedule_instance']
    start_time = context.user_data.get('start_time')
    
    # At least one time (start or stop) must be set
    if not start_time and not stop_time:
        send_message(chat_id, "❌ Error: You must set at least start time or stop time")
        return ConversationHandler.END
    
    # CRITICAL: Validate time formats
    if start_time:
        try:
            start_hour, start_minute = map(int, start_time.split(':'))
            if not (0 <= start_hour <= 23 and 0 <= start_minute <= 59):
                raise ValueError
        except:
            send_message(chat_id, "❌ Error: Invalid start time format. Please start again with /schedule")
            return ConversationHandler.END
    else:
        start_hour = start_minute = None
    
    if stop_time:
        try:
            stop_hour, stop_minute = map(int, stop_time.split(':'))
            if not (0 <= stop_hour <= 23 and 0 <= stop_minute <= 59):
                raise ValueError
        except:
            send_message(chat_id, "❌ Error: Invalid stop time format. Please start again with /schedule")
            return ConversationHandler.END
    
    ec2 = get_ec2_client(chat_id)
    if not ec2:
        send_message(chat_id, "❌ Error getting AWS credentials")
        return ConversationHandler.END
    
    # Get user's timezone
    user = user_db.get_user(chat_id)
    user_timezone = user['timezone']
    tz = pytz.timezone(user_timezone)
    
    # Log current time in user's timezone
    from datetime import datetime
    current_time = datetime.now(tz)
    logger.info(f"Current time in {user_timezone}: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if instance_id == 'all':
        response = ec2.describe_instances()
        instance_ids = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_ids.append(instance['InstanceId'])
        
        for iid in instance_ids:
            # Remove old jobs from scheduler
            try:
                old_start_jobs = [job.id for job in scheduler.get_jobs() if job.id.startswith(f"start_{iid}_{chat_id}_")]
                for job_id in old_start_jobs:
                    scheduler.remove_job(job_id)
                    logger.info(f"Removed old start job: {job_id}")
            except Exception as e:
                logger.error(f"Error removing old start jobs: {e}")
            
            try:
                old_stop_jobs = [job.id for job in scheduler.get_jobs() if job.id.startswith(f"stop_{iid}_{chat_id}_")]
                for job_id in old_stop_jobs:
                    scheduler.remove_job(job_id)
                    logger.info(f"Removed old stop job: {job_id}")
            except Exception as e:
                logger.error(f"Error removing old stop jobs: {e}")
            
            user_db.delete_schedule(chat_id, iid, 'start')
            user_db.delete_schedule(chat_id, iid, 'stop')
            
            if start_time:
                hour, minute = map(int, start_time.split(':'))
                job_id = f"start_{iid}_{chat_id}_{hour}_{minute}"
                job = scheduler.add_job(scheduled_start_instance, CronTrigger(hour=hour, minute=minute, timezone=tz), args=[iid, chat_id], id=job_id, replace_existing=True)
                user_db.add_schedule(chat_id, iid, 'start', start_time)
                logger.info(f"Added start schedule: {job_id} at {hour:02d}:{minute:02d}")
                logger.info(f"Next run time: {job.next_run_time}")
            
            if stop_time:
                hour, minute = map(int, stop_time.split(':'))
                job_id = f"stop_{iid}_{chat_id}_{hour}_{minute}"
                job = scheduler.add_job(scheduled_stop_instance, CronTrigger(hour=hour, minute=minute, timezone=tz), args=[iid, chat_id], id=job_id, replace_existing=True)
                user_db.add_schedule(chat_id, iid, 'stop', stop_time)
                logger.info(f"Added stop schedule: {job_id} at {hour:02d}:{minute:02d}")
                logger.info(f"Next run time: {job.next_run_time}")
        
        instance_text = f"All Instances ({len(instance_ids)})"
    else:
        # CRITICAL: Validate instance_id format
        if not instance_id.startswith('i-'):
            send_message(chat_id, "❌ Error: Invalid instance ID format. Please start again with /schedule")
            return ConversationHandler.END
        
        # Remove old jobs from scheduler
        try:
            old_start_jobs = [job.id for job in scheduler.get_jobs() if job.id.startswith(f"start_{instance_id}_{chat_id}_")]
            for job_id in old_start_jobs:
                scheduler.remove_job(job_id)
                logger.info(f"Removed old start job: {job_id}")
        except Exception as e:
            logger.error(f"Error removing old start jobs: {e}")
        
        try:
            old_stop_jobs = [job.id for job in scheduler.get_jobs() if job.id.startswith(f"stop_{instance_id}_{chat_id}_")]
            for job_id in old_stop_jobs:
                scheduler.remove_job(job_id)
                logger.info(f"Removed old stop job: {job_id}")
        except Exception as e:
            logger.error(f"Error removing old stop jobs: {e}")
        
        user_db.delete_schedule(chat_id, instance_id, 'start')
        user_db.delete_schedule(chat_id, instance_id, 'stop')
        
        if start_time:
            hour, minute = map(int, start_time.split(':'))
            job_id = f"start_{instance_id}_{chat_id}_{hour}_{minute}"
            job = scheduler.add_job(scheduled_start_instance, CronTrigger(hour=hour, minute=minute, timezone=tz), args=[instance_id, chat_id], id=job_id, replace_existing=True)
            user_db.add_schedule(chat_id, instance_id, 'start', start_time)
            logger.info(f"Added start schedule: {job_id} at {hour:02d}:{minute:02d}")
            logger.info(f"Next run time: {job.next_run_time}")
        
        if stop_time:
            hour, minute = map(int, stop_time.split(':'))
            job_id = f"stop_{instance_id}_{chat_id}_{hour}_{minute}"
            job = scheduler.add_job(scheduled_stop_instance, CronTrigger(hour=hour, minute=minute, timezone=tz), args=[instance_id, chat_id], id=job_id, replace_existing=True)
            user_db.add_schedule(chat_id, instance_id, 'stop', stop_time)
            logger.info(f"Added stop schedule: {job_id} at {hour:02d}:{minute:02d}")
            logger.info(f"Next run time: {job.next_run_time}")
        
        instance_text = get_instance_name(ec2, instance_id)
    
    text = f"✅ <b>Schedule Created</b>\n\n"
    text += f"<b>{instance_text}</b>\n"
    if start_time:
        text += f"   ▶️ Start: <b>{format_time_12hr(start_time)}</b>\n"
    else:
        text += f"   ▶️ Start: <b>Manual</b>\n"
    if stop_time:
        text += f"   ⏹️ Stop: <b>{format_time_12hr(stop_time)}</b>\n"
    else:
        text += f"   ⏹️ Stop: <b>Manual</b>\n"
    text += f"\n🌍 Timezone: <b>{user_timezone}</b>"
    
    send_message(chat_id, text)
    
    # Delete ALL intermediate messages (everything except the first /schedule menu message)
    if 'message_ids' in context.user_data and len(context.user_data['message_ids']) > 0:
        for i in range(1, len(context.user_data['message_ids'])):
            try:
                delete_message(chat_id, context.user_data['message_ids'][i])
            except:
                pass
    
    # Delete instance selection message
    if 'instance_selection_message_id' in context.user_data:
        try:
            delete_message(chat_id, context.user_data['instance_selection_message_id'])
        except:
            pass
    
    # Delete current instance messages
    if 'current_instance_messages' in context.user_data:
        for msg_id in context.user_data['current_instance_messages']:
            try:
                delete_message(chat_id, msg_id)
            except:
                pass
    
    # Edit only the FIRST message (initial /schedule message) to show "Cancelled" button
    first_msg_id = context.user_data.get('first_schedule_message_id')
    if first_msg_id:
        cancelled_keyboard = [
            [{'text': '⏳ Set Schedule', 'callback_data': 'schedopt_set'}],
            [{'text': '📋 Schedule Status', 'callback_data': 'schedopt_status'}],
            [{'text': '✅ Cancelled', 'callback_data': 'already_cancelled'}]
        ]
        try:
            edit_message(chat_id, first_msg_id, "<b>⏰ Schedule Auto Start/Stop</b>\n\nChoose an option:", {'inline_keyboard': cancelled_keyboard})
        except:
            pass
    
    # CRITICAL: Clear conversation data after successful completion
    context.user_data.clear()
    
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    chat_id = update.effective_chat.id
    
    if not check_authorization(chat_id):
        text = "<b>🤖 Welcome to EC2 Control Bot!</b>\n\n"
        text += "To get started, register your AWS account:\n\n"
        text += "🔐 /register - Register your AWS account\n\n"
        text += "After registration, you can control your EC2 instances!"
        send_message(chat_id, text)
        return
    
    text = "<b>🤖 Available Commands:</b>\n\n"
    text += "<b>Instance Control:</b>\n"
    text += "▶️ /start - Show stopped instances\n"
    text += "⏹️ /stop - Show running instances\n"
    text += "🔄 /reboot - Reboot running instances\n"
    text += "📊 /status - Show all instances\n\n"
    text += "<b>Scheduling:</b>\n"
    text += "⏰ /schedule - Schedule auto start/stop\n\n"
    text += "<b>Account:</b>\n"
    text += "👤 /myaccount - Manage your account\n\n"
    text += "❓ /help - Show this message"
    
    if is_admin(chat_id):
        text += "\n\n<b>Admin Commands:</b>\n"
        text += "👥 /users - Manage all users\n"
        text += "✅ /grant - Grant access to user\n"
        text += "❌ /revoke - Revoke user access\n"
        text += "📋 /whitelist - Show whitelisted users"
    
    send_message(chat_id, text)

# ==================== ADMIN COMMANDS ====================

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /users command (admin only)"""
    chat_id = update.effective_chat.id
    
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ This command is only for administrators")
        return
    
    users = user_db.get_all_users()
    
    if not users:
        send_message(chat_id, "👥 No users registered yet")
        return
    
    text = "<b>👥 Registered Users:</b>\n\n"
    
    for user in users:
        status_emoji = "👤" if user['is_active'] else "❌"
        text += f"{status_emoji} <b>{user['username']}</b>\n"
        text += f"   Chat ID: <code>{user['chat_id']}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
    
    text += f"<b>Total Users:</b> {len(users)}"
    
    keyboard = [
        [{'text': '🗑️ Delete Users', 'callback_data': 'show_delete_users'}]
    ]
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
    encoded_data = json.dumps(payload).encode('utf-8')
    response = http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    response_data = json.loads(response.data.decode('utf-8'))
    
    if response_data.get('ok'):
        msg_id = response_data['result']['message_id']
        context.user_data['users_message_id'] = msg_id

async def grant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /grant command (admin only) - Grant access to a user"""
    chat_id = update.effective_chat.id
    
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ This command is only for administrators")
        return
    
    # Check if chat_id provided
    if not context.args or len(context.args) == 0:
        text = "<b>✅ Grant Access</b>\n\n"
        text += "Usage: <code>/grant CHAT_ID</code>\n\n"
        text += "Example: <code>/grant 123456789</code>\n\n"
        text += "To get someone's chat ID, ask them to send /start to this bot."
        send_message(chat_id, text)
        return
    
    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        send_message(chat_id, "❌ Invalid chat ID. Must be a number.")
        return
    
    # Check if already whitelisted
    if target_chat_id in WHITELISTED_CHAT_IDS:
        send_message(chat_id, f"✅ Chat ID <code>{target_chat_id}</code> is already whitelisted")
        return
    
    # Add to whitelist in config file
    try:
        with open('config.py', 'r', encoding='utf-8') as f:
            config_content = f.read()
        
        # Find the WHITELISTED_CHAT_IDS list and add the new ID
        import re
        pattern = r'(WHITELISTED_CHAT_IDS = \[)([^\]]*)(\])'
        match = re.search(pattern, config_content, re.DOTALL)
        
        if match:
            current_ids = match.group(2)
            new_id_line = f"\n    {target_chat_id},  # Added by admin"
            new_content = config_content.replace(
                f'WHITELISTED_CHAT_IDS = [{current_ids}]',
                f'WHITELISTED_CHAT_IDS = [{current_ids}{new_id_line}\n]'
            )
            
            with open('config.py', 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Reload config
            import importlib
            importlib.reload(config)
            WHITELISTED_CHAT_IDS.append(target_chat_id)
            
            text = f"✅ <b>Access Granted</b>\n\n"
            text += f"Chat ID: <code>{target_chat_id}</code>\n\n"
            text += "User can now register and use the bot."
            send_message(chat_id, text)
        else:
            send_message(chat_id, "❌ Error: Could not update config file")
    except Exception as e:
        send_message(chat_id, f"❌ Error: {str(e)}")

async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /revoke command (admin only) - Revoke access from a user"""
    chat_id = update.effective_chat.id
    
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ This command is only for administrators")
        return
    
    # Check if chat_id provided
    if not context.args or len(context.args) == 0:
        text = "<b>❌ Revoke Access</b>\n\n"
        text += "Usage: <code>/revoke CHAT_ID</code>\n\n"
        text += "Example: <code>/revoke 123456789</code>"
        send_message(chat_id, text)
        return
    
    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        send_message(chat_id, "❌ Invalid chat ID. Must be a number.")
        return
    
    # Don't allow revoking admin
    if target_chat_id in ADMIN_CHAT_IDS:
        send_message(chat_id, "❌ Cannot revoke access from admin")
        return
    
    # Check if whitelisted
    if target_chat_id not in WHITELISTED_CHAT_IDS:
        send_message(chat_id, f"⚠️ Chat ID <code>{target_chat_id}</code> is not whitelisted")
        return
    
    # Remove from whitelist in config file
    try:
        with open('config.py', 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        new_lines = []
        for line in lines:
            if str(target_chat_id) not in line or 'WHITELISTED_CHAT_IDS' in line:
                new_lines.append(line)
        
        with open('config.py', 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        
        # Reload config
        import importlib
        importlib.reload(config)
        if target_chat_id in WHITELISTED_CHAT_IDS:
            WHITELISTED_CHAT_IDS.remove(target_chat_id)
        
        text = f"❌ <b>Access Revoked</b>\n\n"
        text += f"Chat ID: <code>{target_chat_id}</code>\n\n"
        text += "User can no longer use the bot."
        send_message(chat_id, text)
    except Exception as e:
        send_message(chat_id, f"❌ Error: {str(e)}")

async def whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /whitelist command (admin only) - Show all whitelisted users"""
    chat_id = update.effective_chat.id
    
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ This command is only for administrators")
        return
    
    text = "<b>✅ Whitelisted Users:</b>\n\n"
    
    for wl_chat_id in WHITELISTED_CHAT_IDS:
        user = user_db.get_user(wl_chat_id)
        if user:
            text += f"👤 <b>{user['username']}</b>\n"
            text += f"   Chat ID: <code>{wl_chat_id}</code>\n"
            text += f"   Status: Registered\n\n"
        else:
            text += f"⚪ Chat ID: <code>{wl_chat_id}</code>\n"
            text += f"   Status: Not Registered\n\n"
    
    text += f"<b>Total Whitelisted:</b> {len(WHITELISTED_CHAT_IDS)}"
    
    send_message(chat_id, text)

# ==================== CALLBACK HANDLER ====================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    chat_id = query.message.chat.id
    
    await query.answer()
    
    # Handle AWS guide button
    if query.data == 'show_aws_guide':
        text = "<b>📖 How to Create AWS Access Keys</b>\n\n"
        text += "<b>Step 1: Sign in to AWS Console</b>\n"
        text += "• Go to <a href='https://console.aws.amazon.com'>AWS Console</a>\n"
        text += "• Sign in with your AWS account\n\n"
        
        text += "<b>Step 2: Open IAM Dashboard</b>\n"
        text += "• Search for 'IAM' in the search bar\n"
        text += "• Click on 'IAM' service\n\n"
        
        text += "<b>Step 3: Create IAM User (if needed)</b>\n"
        text += "• Click 'IAM Users' in left sidebar\n"
        text += "• Click 'Create user'\n"
        text += "• Enter username (e.g. 'telegram-bot')\n"
        text += "• Click 'Next'\n\n"
        
        text += "<b>Step 4: Set Permissions</b>\n"
        text += "• Select 'Attach policies directly'\n"
        text += "• Search and select: <code>AmazonEC2FullAccess</code>\n"
        text += "• Click 'Next' then 'Create user'\n\n"
        
        text += "<b>Step 5: Create Access Key</b>\n"
        text += "• Click on the user you created (e.g. 'telegram-bot')\n"
        text += "• Go to 'Security credentials' tab\n"
        text += "• Scroll to 'Access keys' section\n"
        text += "• Click 'Create access key'\n"
        text += "• Select 'Third-party service'\n"
        text += "• Check confirmation box\n"
        text += "• Click 'Next' then 'Create access key'\n\n"
        
        text += "<b>Step 6: Save Your Keys</b>\n"
        text += "• <b>Access Key ID</b>: Starts with AKIA...\n"
        text += "• <b>Secret Access Key</b>: Long random string\n"
        text += "⚠️ Save these keys securely!\n"
        text += "⚠️ Secret key is shown only once!\n\n"
        
        text += "<b>✅ You're Ready!</b>\n"
        text += "Now use /register to add your keys to the bot."
        
        keyboard = [
            [{'text': '🔙 Back to Start', 'callback_data': 'back_to_start'}]
        ]
        
        send_message(chat_id, text, {'inline_keyboard': keyboard})
        return
    
    # Handle back to start button
    if query.data == 'back_to_start':
        text = "<b>🤖 Welcome to Multi-User EC2 Control Bot!</b>\n\n"
        text += "This bot allows you to control your AWS EC2 instances directly from Telegram.\n\n"
        text += "<b>To get started:</b>\n"
        text += "1️⃣ Send /register\n"
        text += "2️⃣ Enter your AWS Access Key\n"
        text += "3️⃣ Enter your AWS Secret Key\n"
        text += "4️⃣ Select your AWS Region\n\n"
        text += "Your credentials will be encrypted and stored securely.\n\n"
        text += "Ready? Send /register to begin!"
        
        keyboard = [
            [{'text': '📖 How to Create AWS Access Keys?', 'callback_data': 'show_aws_guide'}]
        ]
        
        send_message(chat_id, text, {'inline_keyboard': keyboard})
        return
    
    # Handle show delete users button
    if query.data == 'show_delete_users':
        if not is_admin(chat_id):
            await query.answer("⛔ Unauthorized", show_alert=True)
            return
        
        users = user_db.get_all_users()
        
        if not users:
            await query.answer("No users to delete", show_alert=True)
            return
        
        text = "<b>🗑️ Delete Users:</b>\n\n"
        
        for user in users:
            status_emoji = "👤" if user['is_active'] else "❌"
            text += f"{status_emoji} <b>{user['username']}</b>\n"
            text += f"   Chat ID: <code>{user['chat_id']}</code>\n"
            text += f"   Region: {user['aws_region']}\n"
            text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        text += f"<b>Total Users:</b> {len(users)}"
        
        keyboard = []
        for user in users:
            keyboard.append([{'text': f"🗑️ Delete {user['username']}", 'callback_data': f"deluser_{user['chat_id']}"}])
        keyboard.append([{'text': '❌ Cancel', 'callback_data': 'back_to_users'}])
        
        msg_id = context.user_data.get('users_message_id', query.message.message_id)
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        return
    
    # Handle back to users button
    if query.data == 'back_to_users':
        if not is_admin(chat_id):
            await query.answer("⛔ Unauthorized", show_alert=True)
            return
        
        users = user_db.get_all_users()
        
        text = "<b>👥 Registered Users:</b>\n\n"
        
        for user in users:
            status_emoji = "👤" if user['is_active'] else "❌"
            text += f"{status_emoji} <b>{user['username']}</b>\n"
            text += f"   Chat ID: <code>{user['chat_id']}</code>\n"
            text += f"   Region: {user['aws_region']}\n"
            text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        text += f"<b>Total Users:</b> {len(users)}"
        
        keyboard = [
            [{'text': '🗑️ Delete Users', 'callback_data': 'show_delete_users'}]
        ]
        
        msg_id = context.user_data.get('users_message_id', query.message.message_id)
        edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
        return
    
    # Handle delete user (admin only)
    if query.data.startswith('deluser_'):
        if not is_admin(chat_id):
            await query.answer("⛔ Unauthorized", show_alert=True)
            return
        
        if query.data == 'deluser_cancel':
            send_message(chat_id, "❌ Cancelled")
            return
        
        user_chat_id = int(query.data.replace('deluser_', ''))
        
        # Don't allow deleting yourself
        if user_chat_id == chat_id:
            await query.answer("❌ You cannot delete your own account", show_alert=True)
            return
        
        # Get user info before deleting
        user = user_db.get_user(user_chat_id)
        if user:
            username = user['username']
            user_db.delete_user(user_chat_id)
            
            # Update the button to show deleted status
            if query.message.reply_markup and query.message.reply_markup.inline_keyboard:
                keyboard = []
                for row in query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        if button.callback_data == f"deluser_{user_chat_id}":
                            new_row.append({'text': f"✅ {username} Deleted", 'callback_data': 'disabled'})
                        else:
                            new_row.append({'text': button.text, 'callback_data': button.callback_data})
                    keyboard.append(new_row)
                
                # Update the text to remove the deleted user
                users = user_db.get_all_users()
                text = "<b>👥 Registered Users:</b>\n\n"
                
                for u in users:
                    status_emoji = "👤" if u['is_active'] else "❌"
                    text += f"{status_emoji} <b>{u['username']}</b>\n"
                    text += f"   Chat ID: <code>{u['chat_id']}</code>\n"
                    text += f"   Region: {u['aws_region']}\n"
                    text += f"   Registered: {format_registration_date(u['created_at'])}\n\n"
                
                text += f"<b>Total Users:</b> {len(users)}"
                
                edit_message(chat_id, query.message.message_id, text, {'inline_keyboard': keyboard})
            
            # Notify the deleted user
            send_message(user_chat_id, "⛔ Your account has been deleted by an administrator")
        else:
            await query.answer("❌ User not found", show_alert=True)
        return
    
    # Check authorization for other buttons
    if not check_authorization(chat_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    
    # Handle update credentials button - removed, handled by conversation handler
    # if query.data == 'update_creds':
    
    # Handle update region button - removed, handled by conversation handler
    # if query.data == 'update_region_start':
    
    # Handle disabled buttons
    if query.data == 'disabled':
        await query.answer()
        return
    
    # Handle delete account button
    if query.data == 'delete_account':
        keyboard = [
            [{'text': '✅ Yes, Delete My Account', 'callback_data': 'confirm_delete'}],
            [{'text': '❌ Cancel', 'callback_data': 'cancel_delete'}]
        ]
        text = "⚠️ <b>Delete Account</b>\n\n"
        text += "Are you sure you want to delete your account?\n\n"
        text += "This will remove:\n"
        text += "• Your AWS credentials\n"
        text += "• All your schedules\n"
        text += "• All your data\n\n"
        text += "This action cannot be undone!"
        
        edit_message(chat_id, query.message.message_id, text, {'inline_keyboard': keyboard})
        return
    
    # Handle delete confirmation
    if query.data == 'confirm_delete':
        user_db.delete_user(chat_id)
        text = "✅ Your account has been deleted successfully"
        edit_message(chat_id, query.message.message_id, text)
        return
    
    if query.data == 'cancel_delete':
        user = user_db.get_user(chat_id)
        
        text = "<b>👤 Your Account</b>\n\n"
        text += f"✅ {user['username']}\n"
        text += f"   Chat ID: <code>{chat_id}</code>\n"
        text += f"   Region: {user['aws_region']}\n"
        text += f"   Access Key: {user['aws_access_key'][:8]}...{user['aws_access_key'][-4:]}\n"
        text += f"   Registered: {format_registration_date(user['created_at'])}\n\n"
        
        keyboard = [
            [{'text': '🌍 Update Region', 'callback_data': 'update_region_start'}],
            [{'text': '⏰ Update Timezone', 'callback_data': 'update_timezone_start'}],
            [{'text': '🔄 Update Credentials', 'callback_data': 'update_creds'}],
            [{'text': '🗑️ Delete Your Account', 'callback_data': 'delete_account'}]
        ]
        
        edit_message(chat_id, query.message.message_id, text, {'inline_keyboard': keyboard})
        return
    
    # Handle instance actions
    if query.data.startswith(('start_', 'stop_', 'reboot_')):
        action, instance_id = query.data.split('_', 1)
        
        ec2 = get_ec2_client(chat_id)
        if not ec2:
            send_message(chat_id, "❌ Error getting AWS credentials")
            return
        
        instance_name = get_instance_name(ec2, instance_id)
        
        # Get original keyboard and store it
        if query.message.reply_markup and query.message.reply_markup.inline_keyboard:
            state_key = f"{chat_id}_{query.message.message_id}"
            if state_key not in button_states:
                keyboard = []
                for row in query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        btn_dict = {'text': button.text, 'callback_data': button.callback_data}
                        if '_' in button.callback_data:
                            try:
                                _, btn_instance_id = button.callback_data.split('_', 1)
                                btn_dict['instance_id'] = btn_instance_id
                            except:
                                pass
                        new_row.append(btn_dict)
                    keyboard.append(new_row)
                button_states[state_key] = keyboard
        
        # Start async task
        asyncio.create_task(process_instance_action(
            chat_id,
            query.message.message_id,
            action,
            instance_id,
            instance_name
        ))

async def update_button_status(chat_id, message_id, instance_id, new_text):
    """Update a specific button in the keyboard by instance_id"""
    try:
        state_key = f"{chat_id}_{message_id}"
        
        if state_key not in button_states:
            return
            
        keyboard = button_states[state_key]
        
        for row in keyboard:
            for button in row:
                callback_data = button.get('callback_data', '')
                if instance_id in callback_data or button.get('instance_id') == instance_id:
                    button['text'] = new_text
                    button['callback_data'] = 'disabled'
                    button['instance_id'] = instance_id
                    break
        
        edit_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
        payload = {'chat_id': chat_id, 'message_id': message_id, 'reply_markup': json.dumps({'inline_keyboard': keyboard})}
        encoded_data = json.dumps(payload).encode('utf-8')
        http.request('POST', edit_url, body=encoded_data, headers={'Content-Type': 'application/json'})
        
        button_states[state_key] = keyboard
        
    except Exception as e:
        logger.error(f"Error updating button: {e}")

async def process_instance_action(chat_id, message_id, action, instance_id, instance_name):
    """Process instance start/stop/reboot action asynchronously"""
    try:
        ec2 = get_ec2_client(chat_id)
        if not ec2:
            logger.error(f"Cannot get EC2 client for chat_id {chat_id}")
            return
        
        loop = asyncio.get_event_loop()
        
        if action == 'start':
            instance_operation_states[instance_id] = 'starting'
            await update_button_status(chat_id, message_id, instance_id, f"🟡 Starting {instance_name}...")
            
            await loop.run_in_executor(executor, lambda: ec2.start_instances(InstanceIds=[instance_id]))
            
            waiter = ec2.get_waiter('instance_running')
            await loop.run_in_executor(executor, lambda: waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={'Delay': 3, 'MaxAttempts': 40}
            ))
            
            await update_button_status(chat_id, message_id, instance_id, f"🟢 Started {instance_name}")
            if instance_id in instance_operation_states:
                del instance_operation_states[instance_id]
            
        elif action == 'stop':
            instance_operation_states[instance_id] = 'stopping'
            await update_button_status(chat_id, message_id, instance_id, f"🟡 Stopping {instance_name}...")
            
            await loop.run_in_executor(executor, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
            
            waiter = ec2.get_waiter('instance_stopped')
            await loop.run_in_executor(executor, lambda: waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={'Delay': 3, 'MaxAttempts': 40}
            ))
            
            await update_button_status(chat_id, message_id, instance_id, f"🔴 Stopped {instance_name}")
            if instance_id in instance_operation_states:
                del instance_operation_states[instance_id]
            
        elif action == 'reboot':
            instance_operation_states[instance_id] = 'rebooting'
            await update_button_status(chat_id, message_id, instance_id, f"🟡 Rebooting {instance_name}...")
            
            await loop.run_in_executor(executor, lambda: ec2.reboot_instances(InstanceIds=[instance_id]))
            
            await asyncio.sleep(15)
            
            max_attempts = 80
            for attempt in range(max_attempts):
                try:
                    status_response = await loop.run_in_executor(
                        executor,
                        lambda: ec2.describe_instance_status(
                            InstanceIds=[instance_id],
                            IncludeAllInstances=True
                        )
                    )
                    
                    if status_response['InstanceStatuses']:
                        instance_status = status_response['InstanceStatuses'][0]
                        instance_state = instance_status['InstanceState']['Name']
                        system_status = instance_status.get('SystemStatus', {}).get('Status', 'initializing')
                        instance_check = instance_status.get('InstanceStatus', {}).get('Status', 'initializing')
                        
                        logger.info(f"Reboot check {attempt+1}: {instance_id} - State: {instance_state}, System: {system_status}, Instance: {instance_check}")
                        
                        if instance_state == 'running' and system_status == 'ok' and instance_check == 'ok':
                            logger.info(f"Instance {instance_id} fully rebooted")
                            break
                    
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"Error checking instance status: {e}")
                    await asyncio.sleep(5)
            
            await update_button_status(chat_id, message_id, instance_id, f"🟢 Rebooted {instance_name}")
            if instance_id in instance_operation_states:
                del instance_operation_states[instance_id]
            
    except Exception as e:
        logger.error(f"Error processing {action} for {instance_id}: {e}")
        await update_button_status(chat_id, message_id, instance_id, f"❌ Error {instance_name}")
        if instance_id in instance_operation_states:
            del instance_operation_states[instance_id]

def main():
    """Start the bot"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured!")
        return
    
    logger.info("Starting Multi-User EC2 Bot...")
    logger.info(f"Timezone: {config.TIMEZONE}")
    logger.info(f"Admin IDs: {ADMIN_CHAT_IDS}")
    
    # Load and restore schedules
    all_schedules = user_db.get_all_schedules()
    logger.info(f"Loading {len(all_schedules)} schedules from database")
    
    scheduler.start()
    logger.info("Scheduler started")
    
    # Restore schedules to scheduler
    for sched in all_schedules:
        try:
            chat_id = sched['chat_id']
            instance_id = sched['instance_id']
            action = sched['action']
            time_str = sched['time']
            user_timezone = sched['timezone']
            
            # Use user's timezone for their schedules
            tz = pytz.timezone(user_timezone)
            
            hour, minute = map(int, time_str.split(':'))
            job_id = f"{action}_{instance_id}_{chat_id}_{hour}_{minute}"
            
            if action == 'start':
                job = scheduler.add_job(
                    scheduled_start_instance,
                    CronTrigger(hour=hour, minute=minute, timezone=tz),
                    args=[instance_id, chat_id],
                    id=job_id,
                    replace_existing=True
                )
                logger.info(f"Restored schedule: {job_id} (TZ: {user_timezone}) - Next run: {job.next_run_time}")
            elif action == 'stop':
                job = scheduler.add_job(
                    scheduled_stop_instance,
                    CronTrigger(hour=hour, minute=minute, timezone=tz),
                    args=[instance_id, chat_id],
                    id=job_id,
                    replace_existing=True
                )
                logger.info(f"Restored schedule: {job_id} (TZ: {user_timezone}) - Next run: {job.next_run_time}")
        except Exception as e:
            logger.error(f"Error restoring schedule: {e}")
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Registration conversation handler
    register_conv = ConversationHandler(
        entry_points=[
            CommandHandler('register', register_command)
        ],
        states={
            REGISTER_ACCESS_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_access_key),
                CallbackQueryHandler(register_access_key, pattern='^reg_')
            ],
            REGISTER_SECRET_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_secret_key),
                CallbackQueryHandler(register_secret_key, pattern='^reg_')
            ],
            REGISTER_REGION_AREA: [
                CallbackQueryHandler(register_region_area, pattern='^(regarea_|reg_)')
            ],
            REGISTER_REGION: [
                CallbackQueryHandler(register_region, pattern='^(region_|reg_)')
            ],
            REGISTER_TIMEZONE_AREA: [
                CallbackQueryHandler(register_timezone_area, pattern='^(regtzarea_|reg_)')
            ],
            REGISTER_TIMEZONE: [
                CallbackQueryHandler(register_timezone, pattern='^(regtz_|reg_)')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_registration)],
        allow_reentry=True
    )

    # Update credentials conversation handler
    update_creds_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: update_creds_start(u, c), pattern='^update_creds$')
        ],
        states={
            UPDATE_CREDS_ACCESS_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, update_creds_access_key_handler),
                CallbackQueryHandler(update_creds_access_key_handler, pattern='^(creds_cancel|back_to_account_from_creds)$')
            ],
            UPDATE_CREDS_SECRET_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, update_creds_secret_key_handler),
                CallbackQueryHandler(update_creds_secret_key_handler, pattern='^(creds_cancel|back_to_account_from_creds|back_to_creds_access)$')
            ]
        },
        fallbacks=[],
        allow_reentry=True
    )

    # Update region conversation handler
    update_region_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: update_region_start(u, c), pattern='^update_region_start$')
        ],
        states={
            UPDATE_REGION_AREA: [
                CallbackQueryHandler(update_region_area_handler)
            ],
            UPDATE_REGION: [
                CallbackQueryHandler(update_region_handler)
            ]
        },
        fallbacks=[],
        allow_reentry=True,
        per_message=True
    )

    # Update timezone conversation handler
    update_timezone_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: update_timezone_start(u, c), pattern='^update_timezone_start$')
        ],
        states={
            UPDATE_TIMEZONE_AREA: [
                CallbackQueryHandler(update_timezone_area_handler)
            ],
            UPDATE_TIMEZONE: [
                CallbackQueryHandler(update_timezone_handler)
            ]
        },
        fallbacks=[],
        allow_reentry=True,
        per_message=True
    )

    # Schedule conversation handler
    schedule_conv = ConversationHandler(
        entry_points=[CommandHandler('schedule', schedule_command)],
        states={
            SCHEDULE_INSTANCE: [
                CallbackQueryHandler(schedule_instance_handler, pattern='^(schedopt_|schedins_|remschedins_|remsched_|sched_cancel|sched_back_status|sched_back_main|already_cancelled)')
            ],
            SCHEDULE_START_TIME: [
                CallbackQueryHandler(schedule_start_time_handler, pattern='^(starttime_|schedins_|schedopt_|sched_cancel)')
            ],
            SCHEDULE_STOP_TIME: [
                CallbackQueryHandler(schedule_stop_time_handler, pattern='^(stoptime_|starttime_|schedins_|schedopt_|sched_cancel)')
            ],
            CUSTOM_START_TIME: [
                CallbackQueryHandler(schedule_start_time_handler, pattern='^schedins_'),
                CallbackQueryHandler(schedule_start_time_handler, pattern='^sched_cancel$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_start_time_handler)
            ],
            CUSTOM_STOP_TIME: [
                CallbackQueryHandler(schedule_stop_time_handler, pattern='^schedins_'),
                CallbackQueryHandler(schedule_stop_time_handler, pattern='^starttime_'),
                CallbackQueryHandler(schedule_stop_time_handler, pattern='^sched_cancel$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_stop_time_handler)
            ]
        },
        fallbacks=[],
        allow_reentry=True,
        per_message=False
    )
    
    # Add handlers
    application.add_handler(register_conv)
    application.add_handler(update_creds_conv)
    application.add_handler(update_region_conv)
    application.add_handler(update_timezone_conv)
    application.add_handler(schedule_conv)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("reboot", reboot_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myaccount", myaccount_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("grant", grant_command))
    application.add_handler(CommandHandler("revoke", revoke_command))
    application.add_handler(CommandHandler("whitelist", whitelist_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

async def update_creds_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start update credentials flow from button"""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    
    text = "<b>🔐 Update AWS Credentials</b>\n\n"
    text += "Please send your new <b>AWS Access Key ID</b>\n\n"
    text += "Example: <code>AKIAIOSFODNN7EXAMPLE</code>\n\n"
    text += "⚠️ Your credentials will be encrypted and stored securely."
    
    keyboard = [
        [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account_from_creds'}],
        [{'text': '❌ Cancel', 'callback_data': 'creds_cancel'}]
    ]
    
    msg_id = query.message.message_id
    context.user_data['update_creds_message_id'] = msg_id
    user = user_db.get_user(chat_id)
    context.user_data['username'] = user['username']
    context.user_data['aws_region'] = user['aws_region']
    edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return UPDATE_CREDS_ACCESS_KEY

async def update_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start update region flow from button"""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    
    text = "<b>🌍 Update AWS Region</b>\n\n"
    text += "Select your <b>Geographic Region</b>:"
    
    keyboard = [
        [{'text': '🇺🇸 United States', 'callback_data': 'updarea_us'}],
        [{'text': '🇮🇳 Asia Pacific', 'callback_data': 'updarea_ap'}],
        [{'text': '🇨🇦 Canada', 'callback_data': 'updarea_ca'}],
        [{'text': '🇪🇺 Europe', 'callback_data': 'updarea_eu'}],
        [{'text': '🇦🇺 South America', 'callback_data': 'updarea_sa'}],
        [{'text': '🔙 Back to Account', 'callback_data': 'back_to_account'}],
        [{'text': '❌ Cancel', 'callback_data': 'upd_cancel'}]
    ]
    
    msg_id = query.message.message_id
    context.user_data['update_region_message_id'] = msg_id
    edit_message(chat_id, msg_id, text, {'inline_keyboard': keyboard})
    
    return UPDATE_REGION_AREA

if __name__ == '__main__':
    main()
