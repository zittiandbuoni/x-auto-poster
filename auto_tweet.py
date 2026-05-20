#!/usr/bin/env python3
"""
Code to Rich - X自動投稿スクリプト

使い方:
  python3 auto_tweet.py --type engagement   # URLなし共感ツイートを生成して投稿
  python3 auto_tweet.py --type article      # キューから記事ツイートを投稿
  python3 auto_tweet.py --type cost         # 今月のAPIコスト確認
  python3 auto_tweet.py --type preview      # 投稿せず内容だけ確認

cron設定例:
  0  8 * * * venv/bin/python3 auto_tweet.py --type engagement
  0 12 * * 1,3,5 venv/bin/python3 auto_tweet.py --type article
  0 20 * * * venv/bin/python3 auto_tweet.py --type engagement
"""

import tweepy
import anthropic
import requests
import csv
import os
from datetime import datetime
from dotenv import load_dotenv
import argparse

load_dotenv()

API_KEY             = os.getenv('X_API_KEY')
API_KEY_SECRET      = os.getenv('X_API_KEY_SECRET')
ACCESS_TOKEN        = os.getenv('X_ACCESS_TOKEN')
ACCESS_TOKEN_SECRET = os.getenv('X_ACCESS_TOKEN_SECRET')
ANTHROPIC_KEY       = os.getenv('ANTHROPIC_API_KEY')
SERP_API_KEY        = os.getenv('SERP_API_KEY')

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = f'{BASE_DIR}/tweet_queue.csv'
LOG_FILE   = f'{BASE_DIR}/tweet_log.csv'
PERSONA_FILE = f'{BASE_DIR}/persona.md'

COST_WITH_URL = 0.20
COST_NO_URL   = 0.015

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
# 過去ツイート取得（直近10件）
# ============================================================
def load_recent_tweets(n=10):
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r.get('status') == 'success' and not r.get('has_url','').lower() == 'true']
    return [r['text'] for r in rows[-n:]]

# ============================================================
# トレンド取得（SerpAPI）
# ============================================================
def fetch_trends():
    if not SERP_API_KEY:
        return []

    # ブログカテゴリ全体をカバーするクエリ群
    # 毎回全部叩かずランダムに4つ選んでAPIコストを抑える
    import random
    all_queries = [
        # AIツール活用
        'AIツール 仕事 活用 最新',
        '生成AI 業務効率化',
        # 副業・収益化
        '副業 IT エンジニア',
        'ブログ アフィリエイト 収益',
        # Python・開発・自動化
        'Python 自動化 業務',
        'プログラミング 個人開発 副業',
        # ITキャリア・転職
        'ITエンジニア 転職 2026',
        'ITコンサル キャリア',
        # 資産形成・投資
        '投資 副業 資産形成',
        'FIRE サイドFIRE 最新',
        # プログラミング学習
        'プログラミング 独学 学習法',
    ]
    queries = random.sample(all_queries, 4)

    trends = []
    for q in queries:
        try:
            r = requests.get('https://serpapi.com/search', params={
                'q': q, 'api_key': SERP_API_KEY,
                'hl': 'ja', 'gl': 'jp', 'num': 3
            }, timeout=5)
            for result in r.json().get('organic_results', [])[:2]:
                title = result.get('title', '')
                if title:
                    trends.append(title)
        except Exception:
            pass
    return trends[:8]

# ============================================================
# Claude で共感ツイートを生成
# ============================================================
def generate_engagement_tweet():
    persona      = load_persona()
    recent       = load_recent_tweets()
    trends       = fetch_trends()
    weekday      = ['月', '火', '水', '木', '金', '土', '日'][datetime.now().weekday()]

    recent_block = '\n'.join(f'- {t}' for t in recent) if recent else '（まだ投稿なし）'
    trend_block  = '\n'.join(f'- {t}' for t in trends)  if trends else '（取得なし）'

    prompt = f"""以下のペルソナ定義に従って、ペスカとしてXに投稿するツイートを1つ書いてください。

=== ペルソナ定義 ===
{persona}

=== 直近の投稿（同じ書き出し・テーマを繰り返さないための参考） ===
{recent_block}

=== 今日のトレンド（ペスカ視点で触れられそうなら使う） ===
{trend_block}

=== 生成ルール ===
- 今日は{weekday}曜日
- URLなし、140文字以内
- 必ず「いきなり本題から」始める。時間・曜日・場所から入らない
- 構成：具体的な出来事 → 人間らしい反応 → オチ・欲望・疑問
- 語尾は「〜らしい」「〜気がする」「〜なのかな」「〜してみた」など自然なものを状況に合わせて選ぶ（毎回同じにしない）
- `（毎回思ってる）`「〜なんだけどな」などの自己ツッコミを自然に入れる
- 絵文字は3〜4投稿に1回程度。毎回つけない。🫠ばかり使わず状況に合わせて選ぶ
- 抽象的なまとめ・教訓で終わらない
- ペルソナ定義のNGパターンを絶対に使わない
- ツイート本文のみ出力（説明・前置き一切不要）"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    msg = client.messages.create(
        model='claude-opus-4-5',
        max_tokens=300,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return msg.content[0].text.strip()

# ============================================================
# ツイート投稿
# ============================================================
def post_tweet(text, has_url=False, dry_run=False):
    cost = COST_WITH_URL if has_url else COST_NO_URL

    if dry_run:
        print(f'[プレビュー]\n{text}')
        print(f'文字数: {len(text)}  コスト: ${cost:.3f}')
        return True

    try:
        client = get_client()
        res = client.create_tweet(text=text)
        tweet_id = res.data['id']
        log_tweet(tweet_id, text, has_url, cost, 'success')
        print(f'✅ 投稿完了 (ID: {tweet_id})  コスト: ${cost:.3f}')
        return True
    except Exception as e:
        log_tweet(None, text, has_url, 0, f'error: {e}')
        print(f'❌ 投稿エラー: {e}')
        return False

# ============================================================
# ログ記録
# ============================================================
def log_tweet(tweet_id, text, has_url, cost, status):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(['datetime', 'tweet_id', 'text', 'has_url', 'cost_usd', 'status'])
        w.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            tweet_id or '',
            text,
            has_url,
            cost,
            status
        ])

# ============================================================
# キューから記事ツイートを投稿
# ============================================================
def post_from_queue(dry_run=False):
    if not os.path.exists(QUEUE_FILE):
        print('❌ tweet_queue.csv が見つかりません')
        return

    with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    target = next((r for r in rows if r.get('posted', '').lower() != 'true'), None)

    if not target:
        print('📭 キューに未投稿の記事がありません')
        return

    print(f'📤 記事ツイート:\n{target["text"]}\n')

    if post_tweet(target['text'], has_url=True, dry_run=dry_run) and not dry_run:
        target['posted'] = 'true'
        with open(QUEUE_FILE, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)

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

    print(f'【今月のX APIコスト】')
    print(f'  投稿数: {count}件')
    print(f'  合計:   ${total:.3f}（約{int(total * 150)}円）')

# ============================================================
# メイン
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--type', choices=['engagement', 'article', 'cost', 'preview'], default='preview')
    args = parser.parse_args()

    if args.type == 'engagement':
        print('🤖 共感ツイートを生成中...')
        text = generate_engagement_tweet()
        print(f'生成:\n{text}\n')
        post_tweet(text, has_url=False)

    elif args.type == 'article':
        post_from_queue()

    elif args.type == 'cost':
        show_cost()

    elif args.type == 'preview':
        print('🤖 共感ツイートをプレビュー生成中...')
        text = generate_engagement_tweet()
        post_tweet(text, has_url=False, dry_run=True)
