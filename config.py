import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ⚠️ 敏感配置一律从环境变量读取；未设置时使用占位值。
# 部署时必须通过环境变量（或 .env）注入真实值，例如：
#   AIFAB_SECRET_KEY=<你的密钥>
#   AIFAB_PW_GENE=<初始密码>  AIFAB_PW_ZHONGYI=<初始密码>  AIFAB_PW_GUOJUN=<初始密码>
SECRET_KEY = os.environ.get('AIFAB_SECRET_KEY', 'aifab-2026-super-secret-key-change-in-production')
DATABASE = os.path.join(BASE_DIR, 'data', 'aifab.db')

# Built-in users（密码为占位，生产请通过环境变量注入真实初始密码）
USERS = {
    'gene': {
        'password': os.environ.get('AIFAB_PW_GENE', 'nonepasswd'),
        'display_name': '帅兵',
        'is_admin': 1
    },
    'zhongyi': {
        'password': os.environ.get('AIFAB_PW_ZHONGYI', 'nonepasswd'),
        'display_name': '张宗毅'
    },
    'guojun': {
        'password': os.environ.get('AIFAB_PW_GUOJUN', 'nonepasswd'),
        'display_name': '吴国君'
    }
}

# Server config
HOST = '0.0.0.0'
PORT = 9001
