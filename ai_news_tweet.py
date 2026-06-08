#!/usr/bin/env python3
"""
Code to Rich - AIニュース自動ツイートスクリプト

主要AIベンダーのニュースを検知し、ペスカ視点のツイートを生成する。
デフォルトはキュー追加、--post で即時X投稿。

使い方:
  python3 ai_news_tweet.py                    # 新着チェック → ツイート生成 → キュー追加
  python3 ai_news_tweet.py --post --no-image  # 新着を即時X投稿（テキストのみ）
  python3 ai_news_tweet.py --preview --force  # 既読無視で内容確認のみ（投稿・seen登録なし）

cron設定例（日中のみ30分ごと・即時投稿）:
  */30 7-23 * * * /home/code2rich/miniconda3/bin/python3 /home/code2rich/x-auto/ai_news_tweet.py --post --no-image >> /home/code2rich/x-auto/ai_news.log 2>&1
"""

import os
import csv
import json
import argparse
import re
import time
from datetime import datetime
from dotenv import load_dotenv
import requests
import anthropic
import tweepy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'), override=True)

ANTHROPIC_KEY         = os.getenv('ANTHROPIC_API_KEY')
X_API_KEY             = os.getenv('X_API_KEY')
X_API_KEY_SECRET      = os.getenv('X_API_KEY_SECRET')
X_ACCESS_TOKEN        = os.getenv('X_ACCESS_TOKEN')
X_ACCESS_TOKEN_SECRET = os.getenv('X_ACCESS_TOKEN_SECRET')

QUEUE_FILE   = os.path.join(BASE_DIR, 'tweet_queue.csv')
SEEN_FILE    = os.path.join(BASE_DIR, 'ai_news_seen.json')  # 既読管理
PERSONA_FILE = os.path.join(BASE_DIR, 'persona.md')

# ニュース生成モデル（短文生成のためsonnetで十分・コスト抑制）
MODEL = 'claude-sonnet-4-6'

# 1回の実行で生成・投稿する新着の上限（連投・コスト暴走防止）
# 上限を超えた新着はseen登録せず次回実行に持ち越す（取りこぼさない）
MAX_ITEMS_PER_RUN = 1

# ============================================================
# 監視対象 RSSフィード
# ============================================================
RSS_FEEDS = [
    {
        'name': 'Anthropic',
        'type': 'scrape',
        'url': 'https://www.anthropic.com/news',
    },
    {
        'name': 'OpenAI',
        'type': 'rss',
        'url': 'https://openai.com/blog/rss.xml',
    },
    {
        'name': 'Google AI',
        'type': 'rss',
        'url': 'https://blog.google/technology/ai/rss/',
        'keywords': ['ai', 'model', 'gemini', 'llm', 'agent', 'deep mind',
                     'machine learning', 'generative', 'neural', 'io 20'],
    },
    {
        'name': 'GitHub',
        'type': 'rss',
        'url': 'https://github.blog/feed/',
        'keywords': ['copilot', 'ai', 'model', 'coding', 'agent'],
    },
    {
        'name': 'HuggingFace',
        'type': 'rss',
        'url': 'https://huggingface.co/blog/feed.xml',
    },
    {
        'name': 'TechCrunch AI',
        'type': 'rss',
        'url': 'https://techcrunch.com/category/artificial-intelligence/feed/',
        'keywords': ['ai', 'model', 'llm', 'openai', 'anthropic', 'google',
                     'agent', 'chatgpt', 'gpt', 'claude', 'gemini', 'startup',
                     'funding', 'launch', 'release', 'raises'],
    },
]

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

# ============================================================
# 既読管理
# ============================================================
def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, 'r') as f:
        return set(json.load(f))

def save_seen(seen: set):
    with open(SEEN_FILE, 'w') as f:
        json.dump(list(seen), f, indent=2)

# ============================================================
# RSS取得
# ============================================================
def fetch_rss(feed: dict) -> list:
    """RSS/Atomフィードから最新記事を取得"""
    try:
        xml = requests.get(feed['url'], headers=HEADERS, timeout=10).content.decode('utf-8', errors='ignore')

        items = []
        # <item> or <entry> を抽出
        blocks = re.findall(r'<(?:item|entry)>(.*?)</(?:item|entry)>', xml, re.DOTALL)
        for b in blocks[:5]:  # 最新5件
            title = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', b, re.DOTALL)
            link  = re.search(r'<(?:link|id)>([^<]+)</(?:link|id)>|<link[^>]+href=["\']([^"\']+)["\']', b)
            desc  = re.search(r'<(?:description|summary)[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</(?:description|summary)>', b, re.DOTALL)
            title = title.group(1).strip() if title else ''
            url   = (link.group(1) or link.group(2)).strip() if link else ''
            description = re.sub(r'<[^>]+>', '', desc.group(1)).strip() if desc else ''

            if not title or not url:
                continue

            # キーワードフィルタ（feedに 'keywords' があるときのみ）
            if 'keywords' in feed:
                if not any(k in title.lower() or k in url.lower() for k in feed['keywords']):
                    continue

            items.append({'title': title, 'url': url, 'source': feed['name'], 'description': description[:300]})

        return items

    except Exception as e:
        print(f'  RSS取得エラー [{feed["name"]}]: {e}')
        return []

# ============================================================
# Anthropicスクレイピング
# ============================================================
def fetch_anthropic() -> list:
    """Anthropicのニュースページをスクレイピング"""
    try:
        html = requests.get('https://www.anthropic.com/news', headers=HEADERS, timeout=10).content.decode('utf-8', errors='ignore')

        paths = re.findall(r'href="(/news/[^"#?]+)"', html)
        unique_paths = list(dict.fromkeys(paths))[:5]

        items = []
        for path in unique_paths:
            url = f'https://www.anthropic.com{path}'
            # 個別ページからタイトルを取得
            try:
                page = requests.get(url, headers=HEADERS, timeout=8).content.decode('utf-8', errors='ignore')
                title_m = re.search(r'<title[^>]*>([^<]+)</title>', page)
                desc_m  = (re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page)
                           or re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', page))
                title = title_m.group(1).replace(' \\ Anthropic', '').strip() if title_m else path.split('/')[-1]
                description = desc_m.group(1).strip() if desc_m else ''
                items.append({'title': title, 'url': url, 'source': 'Anthropic', 'description': description[:300]})
            except Exception:
                items.append({'title': path.split('/')[-1], 'url': url, 'source': 'Anthropic', 'description': ''})

        return items

    except Exception as e:
        print(f'  Anthropicスクレイピングエラー: {e}')
        return []

# ============================================================
# ペルソナ読み込み
# ============================================================
def load_persona():
    with open(PERSONA_FILE, 'r', encoding='utf-8') as f:
        return f.read()

# ============================================================
# Claudeでツイート生成
# ============================================================
def generate_news_tweet(title: str, url: str, source: str, description: str = '') -> str:
    persona = load_persona()
    desc_block = f'概要: {description}' if description else '概要: （取得なし。タイトルの情報のみで判断する）'

    prompt = f"""以下のペルソナ定義に従って、このAIニュースに対するペスカとしてのXへの投稿を1つ書いてください。

=== ペルソナ定義 ===
{persona}

=== AIニュース ===
ソース: {source}
タイトル: {title}
{desc_block}
URL: {url}

=== 生成ルール ===
- テキスト部分は**117文字以内**（URLはX上で23文字換算のため、合計140文字に収まる）
- URLは末尾に1行空けて置く
- ツイート本文のみ出力（説明・前置き一切不要）

=== 絶対にやってはいけないパターン（以下のような文章はNG）===

**「らしい」の多用NG**
❌「〜するらしい。〜が気になる」→ 全ツイートに「らしい」が入るのは避ける。使う場合は1ツイートに1回まで

**毎回同じ締めNG**
❌「週末に試してみる」「週末に触ってみる」→ 毎回この締めにしない
❌「気になる」「〜のかな」「〜なのか」→ 疑問で終わるパターンを連続させない

**「個人開発」の多用NG**
❌「個人開発に使えそう」「個人開発でも〜」→ 本当に関係あるときだけ使う。毎回入れない

**自虐NG**
❌「副業0円の自分には〜」「1年目の自分が〜」「自分みたいなレベルでは〜」→ 自分を下げすぎない
❌「自分がシコシコ書いてる〜とのスケール差がすごい」→ こういう卑下表現はしない

**お決まり構成NG**
❌「〜らしい。〜が気になる。個人開発で試してみる」→ この3段構成を毎回繰り返さない

**記憶頼みのバージョン比較NG（最重要）**
❌「Claude 3シリーズが出てからそんなに経ってない気がするけど」「前のバージョンからまだ間もないのに」
→ 過去のバージョン名・リリース時期・世代を自分の記憶や推測で持ち出さない（事実と食い違って支離滅裂になるリスクが高い）。新機能や差分について語ってよいのは、上の「概要」に書かれている内容が根拠にできる場合のみ。それ以外は今回のニュース単体への観察で完結させる

=== 良い例（参考）===
✅「KPMGが27万人規模でClaude導入。コンサル業界、会社の判断待たずに現場が勝手に使い始めるパターンの方が早そう」
✅「OpenAIのモデルが離散幾何学の予想を反証した。AIが証明を出す時代、検証するのが人間って構図、だいぶ変わってきた」
✅「GitHub Copilot、3年連続でリーダー評価。補完ツールとして完全に前提になってきてる。入れてない現場ほど評価されやすい時代かも」

=== ポイント ===
- ニュースに対して**鋭い観察や気づき**を短く言い切る
- 「自分ごとへの落とし込み」は必須ではない。ニュースへの解釈だけで完結してもよい
- 締めは「〜だ」「〜かも」「〜かな」「〜だったりして」など、状況によって変える
- 毎回同じ語尾・構成にならないように意識する"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=300,
                messages=[{'role': 'user', 'content': prompt}]
            )
            return msg.content[0].text.strip()
        except Exception as e:
            if attempt < 2:
                print(f'  API一時エラー、リトライ中... ({attempt+1}/3): {e}')
                time.sleep(3)
            else:
                raise

# ============================================================
# インフォグラフィック生成（generate_infographic.py がある場合のみ）
# ============================================================
def generate_infographic_for_news(title: str, url: str, source: str):
    """インフォグラフィック画像を生成してファイルパスを返す。失敗時はNone"""
    try:
        from generate_infographic import generate_infographic
        return generate_infographic(title, url, source)
    except Exception as e:
        print(f'  ⚠️ インフォグラフィック生成スキップ（テキストのみで続行）: {e}')
        return None

# ============================================================
# Xに（任意で画像付きで）即時投稿
# ============================================================
def post_to_x(text: str, image_path=None) -> bool:
    """ツイートを投稿。image_pathがあれば画像付きで投稿"""
    try:
        media_ids = None
        if image_path and os.path.exists(image_path):
            auth = tweepy.OAuth1UserHandler(
                X_API_KEY, X_API_KEY_SECRET,
                X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
            )
            api_v1 = tweepy.API(auth)
            media = api_v1.media_upload(filename=image_path)
            media_ids = [media.media_id]
            print(f'  📎 メディアアップロード完了 (id={media.media_id})')

        client = tweepy.Client(
            consumer_key=X_API_KEY,
            consumer_secret=X_API_KEY_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_TOKEN_SECRET
        )
        kwargs = {'text': text}
        if media_ids:
            kwargs['media_ids'] = media_ids

        res = client.create_tweet(**kwargs)
        tweet_id = res.data['id']
        print(f'  ✅ X投稿完了 (ID: {tweet_id})')
        return True

    except Exception as e:
        print(f'  ❌ X投稿エラー: {e}')
        return False

# ============================================================
# キューに追加
# ============================================================
def add_to_queue(text: str, title: str, url: str, image_path: str = ''):
    file_exists = os.path.exists(QUEUE_FILE)
    rows = []
    fieldnames = ['text', 'title', 'url', 'image_path', 'category', 'posted']

    if file_exists:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or fieldnames
            rows = list(reader)

    rows.append({
        'text': text,
        'title': title,
        'url': url,
        'image_path': image_path,
        'category': 'AIニュース',
        'posted': 'false'
    })

    with open(QUEUE_FILE, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview',   action='store_true', help='投稿・キュー追加・seen登録をせず確認のみ')
    parser.add_argument('--force',     action='store_true', help='既読スキップ（テスト用・seen登録しない）')
    parser.add_argument('--post',      action='store_true', help='キューに追加せず即時X投稿')
    parser.add_argument('--no-image',  action='store_true', help='インフォグラフィック生成をスキップ')
    args = parser.parse_args()

    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] AIニュースチェック開始')

    seen = set() if args.force else load_seen()
    new_seen = set()
    handled = 0

    for feed in RSS_FEEDS:
        if handled >= MAX_ITEMS_PER_RUN:
            break
        print(f'  チェック中: {feed["name"]}')

        items = fetch_anthropic() if feed['type'] == 'scrape' else fetch_rss(feed)

        for item in items:
            if handled >= MAX_ITEMS_PER_RUN:
                break

            uid = item['url']
            if uid in seen:
                continue

            print(f'\n  🆕 新着: [{item["source"]}] {item["title"][:60]}')
            print(f'     URL: {item["url"]}')

            # ツイート生成（失敗時はseen登録せず次回再試行）
            try:
                tweet = generate_news_tweet(item['title'], item['url'], item['source'], item.get('description', ''))
            except Exception as e:
                print(f'  ⚠️ ツイート生成エラー（スキップ・次回再試行）: {e}')
                continue

            print(f'  生成ツイート ({len(tweet)}文字):\n  {tweet}')

            # インフォグラフィック（preview/no-image時はスキップ）
            image_path = None
            if not args.no_image and not args.preview:
                image_path = generate_infographic_for_news(item['title'], item['url'], item['source'])

            if args.preview:
                print('  → [PREVIEW] 投稿・キュー追加・seen登録スキップ')
                handled += 1  # プレビューでも上限はカウント（コスト抑制）

            elif args.post:
                if post_to_x(tweet, image_path):
                    new_seen.add(uid)
                    handled += 1
                else:
                    print('  → 投稿失敗のためseen登録せず、次回再試行')

            else:
                add_to_queue(tweet, item['title'], item['url'], image_path or '')
                print('  → キューに追加しました')
                new_seen.add(uid)
                handled += 1

    # preview/force ではseenを永続化しない
    if not args.force and not args.preview:
        save_seen(seen | new_seen)

    if handled == 0:
        print('  新着なし')
    print(f'完了: {handled}件処理（1回の上限={MAX_ITEMS_PER_RUN}）')

if __name__ == '__main__':
    main()
