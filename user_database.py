import sqlite3
import json
from cryptography.fernet import Fernet
import base64
import hashlib
import config

class UserDatabase:
    """Manage user credentials with encryption"""
    
    def __init__(self):
        self.db_file = config.DATABASE_FILE
        self.cipher = self._get_cipher()
        self._init_database()
    
    def _get_cipher(self):
        """Get encryption cipher"""
        # Create a key from the config encryption key
        key = hashlib.sha256(config.ENCRYPTION_KEY.encode()).digest()
        key_base64 = base64.urlsafe_b64encode(key)
        return Fernet(key_base64)
    
    def _init_database(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                aws_access_key TEXT,
                aws_secret_key TEXT,
                aws_region TEXT DEFAULT 'ap-south-1',
                timezone TEXT DEFAULT 'Asia/Kolkata',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add timezone column if it doesn't exist (for existing databases)
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT "Asia/Kolkata"')
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Schedules table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                instance_id TEXT,
                action TEXT,
                time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def _encrypt(self, data):
        """Encrypt sensitive data"""
        return self.cipher.encrypt(data.encode()).decode()
    
    def _decrypt(self, data):
        """Decrypt sensitive data"""
        return self.cipher.decrypt(data.encode()).decode()
    
    def add_user(self, chat_id, username, aws_access_key, aws_secret_key, aws_region='ap-south-1', timezone='Asia/Kolkata'):
        """Add or update user credentials"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Encrypt credentials
        encrypted_access_key = self._encrypt(aws_access_key)
        encrypted_secret_key = self._encrypt(aws_secret_key)
        
        # Check if user exists to preserve timezone if not provided
        cursor.execute('SELECT timezone FROM users WHERE chat_id = ?', (chat_id,))
        existing = cursor.fetchone()
        if existing and timezone == 'Asia/Kolkata':
            timezone = existing[0]  # Preserve existing timezone
        
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (chat_id, username, aws_access_key, aws_secret_key, aws_region, timezone, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', (chat_id, username, encrypted_access_key, encrypted_secret_key, aws_region, timezone))
        
        conn.commit()
        conn.close()
    
    def get_user(self, chat_id):
        """Get user credentials"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT chat_id, username, aws_access_key, aws_secret_key, aws_region, timezone, is_active, created_at
            FROM users WHERE chat_id = ?
        ''', (chat_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'chat_id': row[0],
                'username': row[1],
                'aws_access_key': self._decrypt(row[2]),
                'aws_secret_key': self._decrypt(row[3]),
                'aws_region': row[4],
                'timezone': row[5] if row[5] else 'Asia/Kolkata',
                'is_active': row[6],
                'created_at': row[7]
            }
        return None
    
    def get_all_users(self):
        """Get all users (for admin)"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT chat_id, username, aws_region, timezone, is_active, created_at
            FROM users
        ''')
        
        users = []
        for row in cursor.fetchall():
            users.append({
                'chat_id': row[0],
                'username': row[1],
                'aws_region': row[2],
                'timezone': row[3] if row[3] else 'Asia/Kolkata',
                'is_active': row[4],
                'created_at': row[5]
            })
        
        conn.close()
        return users
    
    def delete_user(self, chat_id):
        """Delete user and their schedules"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Delete schedules first
        cursor.execute('DELETE FROM schedules WHERE chat_id = ?', (chat_id,))
        
        # Delete user
        cursor.execute('DELETE FROM users WHERE chat_id = ?', (chat_id,))
        
        conn.commit()
        conn.close()
    
    def deactivate_user(self, chat_id):
        """Deactivate user (soft delete)"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE users SET is_active = 0 WHERE chat_id = ?', (chat_id,))
        
        conn.commit()
        conn.close()
    
    def activate_user(self, chat_id):
        """Activate user"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE users SET is_active = 1 WHERE chat_id = ?', (chat_id,))
        
        conn.commit()
        conn.close()
    
    def user_exists(self, chat_id):
        """Check if user exists"""
        user = self.get_user(chat_id)
        return user is not None and user['is_active'] == 1
    
    def update_user_region(self, chat_id, aws_region):
        """Update user's AWS region"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE users SET aws_region = ? WHERE chat_id = ?', (aws_region, chat_id))
        
        conn.commit()
        conn.close()
    
    def update_user_timezone(self, chat_id, timezone):
        """Update user's timezone"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE users SET timezone = ? WHERE chat_id = ?', (timezone, chat_id))
        
        conn.commit()
        conn.close()
    
    # Schedule methods
    def add_schedule(self, chat_id, instance_id, action, time):
        """Add schedule for user"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Remove existing schedules for this instance and action
        cursor.execute('''
            DELETE FROM schedules 
            WHERE chat_id = ? AND instance_id = ? AND action = ?
        ''', (chat_id, instance_id, action))
        
        # Add new schedule
        cursor.execute('''
            INSERT INTO schedules (chat_id, instance_id, action, time)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, instance_id, action, time))
        
        conn.commit()
        conn.close()
    
    def get_schedules(self, chat_id, instance_id=None):
        """Get all schedules for user, optionally filtered by instance_id"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        if instance_id:
            cursor.execute('''
                SELECT id, instance_id, action, time
                FROM schedules WHERE chat_id = ? AND instance_id = ?
            ''', (chat_id, instance_id))
        else:
            cursor.execute('''
                SELECT id, instance_id, action, time
                FROM schedules WHERE chat_id = ?
            ''', (chat_id,))
        
        schedules = []
        for row in cursor.fetchall():
            schedules.append({
                'id': row[0],
                'instance_id': row[1],
                'action': row[2],
                'time': row[3]
            })
        
        conn.close()
        return schedules
    
    def delete_schedule(self, chat_id, instance_id, action):
        """Delete a specific schedule by chat_id, instance_id, and action"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM schedules 
            WHERE chat_id = ? AND instance_id = ? AND action = ?
        ''', (chat_id, instance_id, action))
        
        conn.commit()
        conn.close()
    
    def delete_schedule_by_id(self, schedule_id):
        """Delete a schedule by ID"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,))
        
        conn.commit()
        conn.close()
    
    def delete_schedules_for_instance(self, chat_id, instance_id):
        """Delete all schedules for an instance"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM schedules 
            WHERE chat_id = ? AND instance_id = ?
        ''', (chat_id, instance_id))
        
        conn.commit()
        conn.close()
    
    def get_all_schedules(self):
        """Get all schedules (for scheduler)"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT s.id, s.chat_id, s.instance_id, s.action, s.time, u.aws_region, u.timezone
            FROM schedules s
            JOIN users u ON s.chat_id = u.chat_id
            WHERE u.is_active = 1
        ''')
        
        schedules = []
        for row in cursor.fetchall():
            schedules.append({
                'id': row[0],
                'chat_id': row[1],
                'instance_id': row[2],
                'action': row[3],
                'time': row[4],
                'aws_region': row[5],
                'timezone': row[6] if row[6] else 'Asia/Kolkata'
            })
        
        conn.close()
        return schedules
