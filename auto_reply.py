#!/usr/bin/env python3
"""
Code to Rich - リプライ自動返信スクリプト

自分の投稿に届いた新しいリプライ（メンション）を取得し、
ペルソナに沿った返信をClaudeで生成して自動投稿する。

使い方:
  python3 auto_reply.py            # 新しいリプライに自動返信
  python3 auto_reply.py --preview  # 投稿せず内容だけ確認（since_idは更新しない）
  python3 auto_reply.py --cost     # 今月のコスト確認

cron設定例:
  */30 * * * * venv/bin/python3 auto_reply.py
"""

import tweepy
import anthropic
import csv
import os
import json
from datetime import datetime
from dotenv import load_dotenv
import argparse

load_dotenv()

API_KEY             = os.getenv('X_API_KEY')
API_KEY_SECRET      = os.getenv('X_API_KEY_SECRET')
ACCESS_TOKEN        = os.getenv('X_ACCESS_TOKEN')
ACCESS_TOKEN_SECRET = os.getenv('X_ACCESS_TOKEN_SECRET')
ANTHROPIC_KEY       = os.getenv('ANTHROPIC_API_KEY')

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
LOG_FILE     = f'{BASE_DIR}/reply_log.csv'
STATE_FILE   = f'{BASE_DIR}/reply_state.json'
PERSONA_FILE = f'{BASE_DIR}/persona.md'

COST_READ = 0.005   # リプライ1件の読み取り（pay-per-use）
COST_POST = 0.015   # 返信投稿（URLなし投稿と同コスト）

MAX_REPLIES_PER_RUN = 5  # 1回の実行で処理する上限件数（暴走・コスト防止）

# ============================================================
# Twitter クライアント
# ============================================================
def get_client():
    return tweepy.Client(
        consumer_key=API_KEY,
        consumer_secret=API_KEY_SECRET,
        access_token=ACCESS_TOKEN,
        access_token_secret=ACCESS_TOKEN_SECRET
    )

# ============================================================
# ペルソナ読み込み
# ============================================================
def load_persona():
    with open(PERSONA_FILE, 'r', encoding='utf-8') as f:
        return f.read()

# ============================================================
# 状態（自分のuser_id・処理済みmention since_id）の読み書き
# ============================================================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def get_my_user(client, state):
    if 'user_id' in state and 'username' in state:
        return state['user_id'], state['username']
    me = client.get_me()
    state['user_id']  = me.data.id
    state['username'] = me.data.username
    save_state(state)
    return state['user_id'], state['username']

# ============================================================
# Claude で返信を生成
# ============================================================
def generate_reply(mention_text, mention_author):
    persona = load_persona()

    prompt = f"""以下のペルソナ定義に従って、ペスカとしてXに届いたリプライへの返信を書いてください。

=== ペルソナ定義 ===
{persona}

=== 届いたリプライ ===
@{mention_author}: {mention_text}

=== 生成ルール ===
- 返信本文のみ80文字以内
- 「コメントありがとうございます」のような定型のお礼文で始めない
- 相手の発言の内容に具体的に反応する（テンプレ感を出さない）
- ペルソナの口調（〜らしい、〜気がする、〜してみた等）を保つ
- 絵文字は使っても1個まで。無理に入れない
- 返信本文のみ出力（説明・前置き・@ユーザー名は不要、APIが自動付与する）"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    msg = client.messages.create(
        model='claude-opus-4-5',
        max_tokens=200,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return msg.content[0].text.strip()

# ============================================================
# ログ記録
# ============================================================
def log_reply(mention_id, mention_author, mention_text, reply_id, reply_text, cost, status):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(['datetime', 'mention_id', 'mention_author', 'mention_text', 'reply_id', 'reply_text', 'cost_usd', 'status'])
        w.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            mention_id,
            mention_author,
            mention_text,
            reply_id or '',
            reply_text,
            cost,
            status
        ])

# ============================================================
# メイン処理：新着リプライへの自動返信
# ============================================================
def run(preview=False):
    client = get_client()
    state  = load_state()
    user_id, username = get_my_user(client, state)

    kwargs = {
        'id': user_id,
        'max_results': 20,
        'tweet_fields': ['author_id', 'created_at', 'text'],
        'expansions': ['author_id'],
        'user_fields': ['username'],
    }
    since_id = state.get('since_id')
    if since_id:
        kwargs['since_id'] = since_id

    res = client.get_users_mentions(**kwargs)

    if not res.data:
        print('📭 新しいリプライはありません')
        return

    users = {u.id: u.username for u in (res.includes.get('users') or [])}
    mentions = list(reversed(res.data))  # 古い順に処理

    last_processed_id = None

    for i, m in enumerate(mentions):
        if i >= MAX_REPLIES_PER_RUN:
            break

        last_processed_id = m.id

        if m.author_id == user_id:
            continue  # 自分自身の投稿は除外

        author = users.get(m.author_id, 'unknown')
        print(f'💬 @{author}: {m.text}')

        reply_text = generate_reply(m.text, author)
        cost = COST_READ + COST_POST
        print(f'↩️  返信案: {reply_text}')

        if preview:
            print('[プレビューのため投稿はスキップ]\n')
            continue

        try:
            r = client.create_tweet(text=reply_text, in_reply_to_tweet_id=m.id)
            reply_id = r.data['id']
            log_reply(m.id, author, m.text, reply_id, reply_text, cost, 'success')
            print(f'✅ 返信完了 (ID: {reply_id})  コスト: ${cost:.3f}\n')
        except Exception as e:
            log_reply(m.id, author, m.text, None, reply_text, 0, f'error: {e}')
            print(f'❌ 返信エラー: {e}\n')

    if not preview and last_processed_id:
        state['since_id'] = last_processed_id
        save_state(state)

# ============================================================
# コスト集計
# ============================================================
def show_cost():
    if not os.path.exists(LOG_FILE):
        print('ログファイルがまだありません')
        return

    this_month = datetime.now().strftime('%Y-%m')
    total, count = 0.0, 0

    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['datetime'].startswith(this_month) and row['status'] == 'success':
                total += float(row['cost_usd'])
                count += 1

    print('【今月のリプライ自動返信コスト】')
    print(f'  返信数: {count}件')
    print(f'  合計:   ${total:.3f}（約{int(total * 150)}円）')

# ============================================================
# メイン
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true', help='投稿せず内容だけ確認')
    parser.add_argument('--cost', action='store_true', help='今月のコスト確認')
    args = parser.parse_args()

    if args.cost:
        show_cost()
    else:
        run(preview=args.preview)
