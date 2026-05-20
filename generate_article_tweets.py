#!/usr/bin/env python3
"""
記事ツイートを一括生成してtweet_queue.csvを更新するスクリプト
"""

import csv
import os
import time
import requests
import anthropic
from dotenv import load_dotenv

load_dotenv()

WP_URL        = os.getenv('WP_URL')
WP_USER       = os.getenv('WP_USER')
WP_PASS       = os.getenv('WP_APP_PASSWORD')
ANTHROPIC_KEY = os.getenv('ANTHROPIC_API_KEY')

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE   = f'{BASE_DIR}/tweet_queue.csv'
PERSONA_FILE = f'{BASE_DIR}/persona.md'

# ============================================================
# ペルソナ読み込み
# ============================================================
def load_persona():
    with open(PERSONA_FILE, 'r', encoding='utf-8') as f:
        return f.read()

# ============================================================
# WordPress から記事本文を取得
# ============================================================
def fetch_article(url):
    try:
        # URLからスラッグを取得
        slug = url.rstrip('/').split('/')[-1]
        r = requests.get(
            f'{WP_URL}/wp-json/wp/v2/posts',
            params={'slug': slug, 'context': 'edit'},
            auth=(WP_USER, WP_PASS),
            timeout=10
        )
        posts = r.json()
        if not posts:
            return None, None
        post = posts[0]
        # HTMLタグを除去してプレーンテキストに
        import re
        content = re.sub(r'<[^>]+>', '', post['content']['raw'])
        content = re.sub(r'\n{3,}', '\n\n', content).strip()
        return post['title']['rendered'], content[:3000]  # 最初の3000文字
    except Exception as e:
        print(f'  取得エラー: {e}')
        return None, None

# ============================================================
# Claude でツイート生成
# ============================================================
USED_PATTERNS = []  # 生成済みパターンを記録して重複を防ぐ

def generate_article_tweet(title, content, url, category, persona):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    patterns_used = '\n'.join(f'- {p}' for p in USED_PATTERNS[-10:]) or 'なし'

    prompt = f"""以下のペルソナ定義に従い、ペスカとしてブログ記事を紹介するXのツイートを1つ書いてください。

=== ペルソナ定義 ===
{persona}

=== 紹介する記事 ===
タイトル：{title}
カテゴリ：{category}
本文（抜粋）：
{content}

=== URL ===
{url}

=== 最近使ったパターンの書き出し（絶対に被らせない） ===
{patterns_used}

=== 構成の選び方 ===
ペルソナ定義にある「記事紹介ツイートの構成バリエーション」（A〜E）から、
最近使っていないパターンを1つ選んで書くこと。
どのパターンを使うか選んだ上で書き始めること。

=== 鉄則 ===
- 「〜みた」で文を終わらせるのは1ツイート中1回まで
- 「きっかけ→発見1→発見2」の3段構成を使わない
- 「〜が面白かった」「〜が意外だった」で締めない
- 事実の説明だけで終わりそうなとき、自然であればペスカの率直な一言反応を入れる（毎回入れなくてよい）
- ハッシュタグ不要
- URLは最後に1行空けて置く
- テキスト部分は120文字以内
- ツイート本文とURLのみ出力（パターン名や説明は不要）"""

    msg = client.messages.create(
        model='claude-opus-4-5',
        max_tokens=400,
        messages=[{'role': 'user', 'content': prompt}]
    )
    tweet = msg.content[0].text.strip()

    # URLが含まれていない場合は追加
    if url not in tweet:
        tweet = tweet.rstrip() + f'\n\n{url}'

    # 使ったパターンの書き出しを記録
    first_line = tweet.split('\n')[0][:30]
    USED_PATTERNS.append(first_line)

    return tweet

# ============================================================
# メイン
# ============================================================
def main():
    print('=' * 60)
    print('記事ツイート一括生成')
    print('=' * 60)

    persona = load_persona()

    with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    targets = [r for r in rows if r.get('posted', '').lower() != 'true']
    print(f'対象: {len(targets)}件\n')

    for i, row in enumerate(rows):
        if row.get('posted', '').lower() == 'true':
            continue

        title    = row['title']
        url      = row['url']
        category = row['category']

        print(f'[{i+1}/{len(targets)}] {title[:40]}...')
        print(f'  記事取得中...')

        _, content = fetch_article(url)
        if not content:
            print('  → スキップ（取得失敗）')
            continue

        print(f'  ツイート生成中...')
        tweet = generate_article_tweet(title, content, url, category, persona)
        row['text'] = tweet

        print(f'  生成完了:')
        print(f'  {tweet[:80]}...')
        print()

        # レート制限対策
        time.sleep(1.5)

    # CSV更新
    with open(QUEUE_FILE, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    print('=' * 60)
    print(f'✅ tweet_queue.csv を更新しました')

if __name__ == '__main__':
    main()
