import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = 'aifab-2026-super-secret-key-change-in-production'
DATABASE = os.path.join(BASE_DIR, 'data', 'aifab.db')

# Built-in users
USERS = {
    'gene': {
        'password': 'goodgood',
        'display_name': '帅兵',
        'is_admin': 1
    },
    'zhongyi': {
        'password': '123456',
        'display_name': '张宗毅'
    },
    'guojun': {
        'password': '123456',
        'display_name': '吴国君'
    }
}

# Server config
HOST = '0.0.0.0'
PORT = 9001
