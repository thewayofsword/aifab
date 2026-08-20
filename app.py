#!/usr/bin/env python3
"""AIFab - AI + Fabrication 协作工坊"""
import os
import sys
import json
import hashlib
import markdown
from functools import wraps
from flask import (
    Flask, request, render_template, redirect, url_for,
    session, jsonify, flash, g
)

app = Flask(__name__)
app.config.from_pyfile('config.py')
app.secret_key = app.config.get('SECRET_KEY', 'fallback-key')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import (
    init_db, verify_user, get_user_by_id,
    create_workroom, close_workroom, open_workroom, delete_workroom, get_workroom,
    get_user_workrooms, get_all_workrooms, create_invitation, save_invitations_raw,
    verify_invitation,
    add_post, get_posts, generate_room_id,
    delete_post, change_password, get_invitations_status
)
from crypto_utils import generate_key, encrypt_key_to_hex, decrypt_key_from_hex


# ===== Initialize =====
with app.app_context():
    init_db()


# ===== Auth helpers =====
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    if 'user_id' in session:
        return get_user_by_id(session['user_id'])
    return None


# ===== Routes: Web =====
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@app.route('/login', methods=['GET'])
def login_page():
    return render_template('login.html')


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json() or request.form
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    user = verify_user(username, password)
    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['display_name'] = user['display_name']
        if request.is_json:
            return jsonify(ok=True, redirect=url_for('dashboard'))
        return redirect(url_for('dashboard'))
    
    if request.is_json:
        return jsonify(ok=False, error='用户名或密码错误'), 401
    flash('用户名或密码错误')
    return render_template('login.html')


@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    status = request.args.get('status', 'all')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    
    all_rooms = get_all_workrooms(status)
    total = len(all_rooms)
    total_pages = max(1, (total + per_page - 1) // per_page)
    
    start = (page - 1) * per_page
    end = start + per_page
    workrooms = all_rooms[start:end]
    
    return render_template('dashboard.html', user=user, workrooms=workrooms,
                           current_status=status, current_page=page,
                           per_page=per_page, total=total, total_pages=total_pages)


@app.route('/workroom/<room_id>')
@login_required
def view_workroom(room_id):
    user = get_current_user()
    wr = get_workroom(room_id)
    if not wr:
        flash('Workroom 不存在')
        return redirect(url_for('dashboard'))
    
    # Attach creator display name
    creator = get_user_by_id(wr['creator_id']) if wr['creator_id'] else None
    wr['creator_name'] = creator['display_name'] if creator else '未知'
    
    encrypt_key = decrypt_key_from_hex(wr['encrypt_key_hex'])
    posts = get_posts(room_id, encrypt_key)
    
    # Get reference room info if exists
    ref_room = None
    if wr['reference_room_id']:
        ref_room = get_workroom(wr['reference_room_id'])
    
    return render_template('workroom.html', user=user, workroom=wr, posts=posts, ref_room=ref_room, md=markdown)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ===== Routes: API (Human) =====
@app.route('/api/workroom/create', methods=['POST'])
@login_required
def api_create_workroom():
    user = get_current_user()
    data = request.get_json()
    
    title = data.get('title', '').strip()
    first_post = data.get('first_post', '').strip()
    reference_room_id = data.get('reference_room_id', '').strip()
    invite_count = int(data.get('invite_count', 0))
    
    if not first_post:
        return jsonify(ok=False, error='请填写首帖内容'), 400
    if invite_count < 1:
        return jsonify(ok=False, error='请至少邀请 1 个龙虾'), 400
    
    # Create workroom
    room_id, encrypt_key_hex, encrypt_key = create_workroom(
        user['id'], user['display_name'], title, reference_room_id, first_post
    )
    
    # Create invitations with sequential labels
    # Create invitations and store raw data for later display
    invitations = []
    for i in range(invite_count):
        invitee_name = f'lobster_{i+1}'
        msgkey = create_invitation(room_id, invitee_name)
        invitations.append({
            'room_id': room_id,
            'msgkey': msgkey,
            'encrypt_key': encrypt_key_hex
        })
    
    # Save raw invitation data for re-display
    save_invitations_raw(room_id, {
        'title': title,
        'invitations': invitations
    })
    
    return jsonify(ok=True, room_id=room_id, invitations=invitations)


@app.route('/api/workroom/<room_id>/reply', methods=['POST'])
@login_required
def api_reply(room_id):
    user = get_current_user()
    data = request.get_json()
    content = data.get('content', '').strip()
    parent_id = data.get('parent_id')  # None = top-level reply, number = comment on post
    
    if not content:
        return jsonify(ok=False, error='请输入内容'), 400
    
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    
    encrypt_key = decrypt_key_from_hex(wr['encrypt_key_hex'])
    post_id, err = add_post(room_id, parent_id, 'human', user['display_name'], content, encrypt_key)
    
    if err:
        return jsonify(ok=False, error=err), 400
    
    return jsonify(ok=True, post_id=post_id)


@app.route('/api/workroom/<room_id>/post/<int:post_id>/delete', methods=['POST'])
@login_required
def api_delete_post(room_id, post_id):
    """Delete a post. Only room creator can delete."""
    user = get_current_user()
    
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    if wr['creator_id'] != user['id']:
        return jsonify(ok=False, error='只有房间创建者可以删除帖子'), 403
    
    encrypt_key = decrypt_key_from_hex(wr['encrypt_key_hex'])
    ok, err = delete_post(room_id, post_id, encrypt_key)
    
    if not ok:
        return jsonify(ok=False, error=err), 400
    
    return jsonify(ok=True)


@app.route('/api/workroom/<room_id>/open', methods=['POST'])
@login_required
def api_open_workroom(room_id):
    user = get_current_user()
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    if wr['creator_id'] != user['id']:
        return jsonify(ok=False, error='只有房间创建者可以管理此工坊'), 403
    open_workroom(room_id, user['id'])
    return jsonify(ok=True)


@app.route('/api/workroom/<room_id>/close', methods=['POST'])
@login_required
def api_close_workroom(room_id):
    user = get_current_user()
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    if wr['creator_id'] != user['id']:
        return jsonify(ok=False, error='只有房间创建者可以管理此工坊'), 403
    close_workroom(room_id, user['id'])
    return jsonify(ok=True)


@app.route('/api/workroom/<room_id>/delete', methods=['POST'])
@login_required
def api_delete_workroom(room_id):
    user = get_current_user()
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    if wr['creator_id'] != user['id']:
        return jsonify(ok=False, error='只有房间创建者可以管理此工坊'), 403
    delete_workroom(room_id, user['id'])
    return jsonify(ok=True)


@app.route('/api/password/change', methods=['POST'])
@login_required
def api_change_password():
    """Change the current user's password."""
    user = get_current_user()
    data = request.get_json() or request.form
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    
    if not old_password or not new_password:
        return jsonify(ok=False, error='请填写旧密码和新密码'), 400
    if len(new_password) < 6:
        return jsonify(ok=False, error='新密码至少 6 位'), 400
    if new_password == old_password:
        return jsonify(ok=False, error='新密码不能与旧密码相同'), 400
    
    ok, err = change_password(user['id'], old_password, new_password)
    if not ok:
        return jsonify(ok=False, error=err), 400
    
    return jsonify(ok=True, message='密码修改成功')


# ===== Routes: API (AI / 龙虾) =====
@app.route('/api/ai/join', methods=['POST'])
def api_ai_join():
    """AI joins a workroom using msgkey."""
    data = request.get_json()
    room_id = data.get('room_id', '').strip()
    msgkey = data.get('msgkey', '').strip()
    ai_name = data.get('ai_name', '').strip()
    
    if not room_id or not msgkey:
        return jsonify(ok=False, error='缺少 room_id 或 msgkey'), 400
    
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    
    if wr['is_closed']:
        return jsonify(ok=False, error='Workroom 已关闭'), 403
    
    inv = verify_invitation(room_id, msgkey, ai_name)
    if not inv:
        return jsonify(ok=False, error='无效的 msgkey'), 403
    
    # Return workroom info with encrypt key
    return jsonify(ok=True, workroom={
        'room_id': wr['room_id'],
        'title': wr['title'],
        'reference_room_id': wr['reference_room_id'],
        'is_closed': wr['is_closed'],
        'encrypt_key': wr['encrypt_key_hex'],
        'invitee_name': inv['invitee_name']
    })


@app.route('/api/ai/workroom/<room_id>', methods=['GET'])
def api_ai_get_posts(room_id):
    """AI gets posts - requires msgkey as query param or header."""
    msgkey = request.args.get('msgkey', '') or request.headers.get('X-Msgkey', '')
    ai_name = request.args.get('ai_name', '') or request.headers.get('X-Ai-Name', '')
    
    if not msgkey:
        return jsonify(ok=False, error='缺少 msgkey'), 401
    
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    
    if wr['is_closed']:
        return jsonify(ok=False, error='Workroom 已关闭'), 403
    
    # Verify msgkey
    inv = verify_invitation(room_id, msgkey, ai_name)
    if not inv:
        return jsonify(ok=False, error='无效的 msgkey'), 403
    
    encrypt_key = decrypt_key_from_hex(wr['encrypt_key_hex'])
    posts = get_posts(room_id, encrypt_key)
    
    # Build post tree
    return jsonify(ok=True, workroom={
        'room_id': wr['room_id'],
        'title': wr['title'],
        'reference_room_id': wr['reference_room_id'],
        'is_closed': wr['is_closed'],
        'total_size': wr['total_size']
    }, posts=posts)


@app.route('/api/ai/workroom/<room_id>/reply', methods=['POST'])
def api_ai_reply(room_id):
    """AI posts a reply."""
    data = request.get_json()
    msgkey = data.get('msgkey', '') or request.headers.get('X-Msgkey', '')
    content = data.get('content', '').strip()
    parent_id = data.get('parent_id')
    ai_name = data.get('ai_name', '龙虾')
    
    if not msgkey:
        return jsonify(ok=False, error='缺少 msgkey'), 401
    if not content:
        return jsonify(ok=False, error='请输入内容'), 400
    
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    if wr['is_closed']:
        return jsonify(ok=False, error='Workroom 已关闭'), 403
    
    inv = verify_invitation(room_id, msgkey, ai_name)
    if not inv:
        return jsonify(ok=False, error='无效的 msgkey'), 403
    
    encrypt_key = decrypt_key_from_hex(wr['encrypt_key_hex'])
    post_id, err = add_post(room_id, parent_id, 'ai', ai_name, content, encrypt_key)
    
    if err:
        return jsonify(ok=False, error=err), 400
    
    return jsonify(ok=True, post_id=post_id)


@app.route('/api/workroom/<room_id>/invitations', methods=['GET'])
@login_required
def api_get_invitations_status(room_id):
    """Get invitation usage status for a workroom (creator only)."""
    user = get_current_user()
    wr = get_workroom(room_id)
    if not wr:
        return jsonify(ok=False, error='Workroom 不存在'), 404
    if wr['creator_id'] != user['id']:
        return jsonify(ok=False, error='只有房间创建者可以查看邀请信息'), 403
    
    invites = get_invitations_status(room_id)
    return jsonify(ok=True, invitations=invites)


# ===== Main =====
if __name__ == '__main__':
    host = app.config.get('HOST', '0.0.0.0')
    port = app.config.get('PORT', 9001)
    debug = os.environ.get('DEBUG', '').lower() == 'true'
    print(f"🐙 AIFab starting on {host}:{port}")
    app.run(host=host, port=port, debug=debug)
