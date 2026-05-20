#!/usr/bin/env python3
"""
Code to Rich - 月次レポート生成スクリプト
使い方: python3 monthly_report.py
"""

import requests
import re
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

WP_URL    = os.getenv('WP_URL')
WP_USER   = os.getenv('WP_USER')
WP_PASS   = os.getenv('WP_APP_PASSWORD')
LOG_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monthly_log.md')

# ============================================================
# WordPress から記事数を自動取得
# ============================================================
def get_wp_stats():
    try:
        r = requests.get(
            f'{WP_URL}/wp-json/wp/v2/posts',
            params={'per_page': 1, 'status': 'publish'},
            auth=(WP_USER, WP_PASS)
        )
        total = int(r.headers.get('X-WP-Total', 0))
        return total
    except Exception as e:
        print(f'WordPress取得エラー: {e}')
        return None

# ============================================================
# 最新月のログを monthly_log.md から取得
# ============================================================
def get_latest_log():
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 最新月のセクションを抽出（テンプレートブロック以降の最初の ## 20XX年X月）
    pattern = r'(## \d{4}年\d+月.*?)(?=\n## \d{4}年|\Z)'
    matches = re.findall(pattern, content, re.DOTALL)

    if not matches:
        print('月次ログが見つかりませんでした。monthly_log.md を確認してください。')
        return None

    return matches[-1].strip()  # 最新月

# ============================================================
# ログからフィールドを抽出
# ============================================================
def parse_log(log_text):
    def extract_section(title):
        pattern = rf'### {title}\n(.*?)(?=\n### |\Z)'
        m = re.search(pattern, log_text, re.DOTALL)
        return m.group(1).strip() if m else ''

    month_m = re.search(r'## (\d{4}年\d+月)', log_text)
    month   = month_m.group(1) if month_m else '不明'

    revenue_text = extract_section('収益')
    metrics_text = extract_section('サイト指標')
    done_text    = extract_section('今月やったこと')
    good_text    = extract_section('うまくいったこと')
    bad_text     = extract_section('うまくいかなかったこと')
    next_text    = extract_section('来月の作戦')

    # 合計収益を抽出
    total_m = re.search(r'合計：([\d,]+)円', revenue_text)
    total   = total_m.group(1) if total_m else '0'

    # 目標不足を抽出
    gap_m = re.search(r'目標まで：([\d,]+)円不足', revenue_text)
    gap   = gap_m.group(1) if gap_m else '50,000'

    return {
        'month':        month,
        'revenue_text': revenue_text,
        'metrics_text': metrics_text,
        'done_text':    done_text,
        'good_text':    good_text,
        'bad_text':     bad_text,
        'next_text':    next_text,
        'total':        total,
        'gap':          gap,
    }

# ============================================================
# ツイート生成
# ============================================================
def clean_metrics(metrics_text, wp_total):
    """記事数行をWP自動取得値に置き換える"""
    lines = [l for l in metrics_text.splitlines() if not l.strip().startswith('- 記事数')]
    if wp_total:
        lines.insert(0, f'- 記事数：{wp_total}本')
    return '\n'.join(lines)

def clean_revenue(revenue_text):
    """目標不足行を除去（ツイート内で重複しないよう）"""
    lines = [l for l in revenue_text.splitlines() if '目標まで' not in l]
    return '\n'.join(lines)

def generate_thread(d, wp_total):
    """スレッド形式で3ツイートを生成"""
    metrics = clean_metrics(d['metrics_text'], wp_total)

    pv_line  = next((l.lstrip('- ') for l in metrics.splitlines() if '月間PV'    in l), '')
    imp_line = next((l.lstrip('- ') for l in metrics.splitlines() if '表示回数'  in l), '')
    clk_line = next((l.lstrip('- ') for l in metrics.splitlines() if 'クリック数' in l), '')
    art_line = next((l.lstrip('- ') for l in metrics.splitlines() if '記事数'    in l), '')

    done_lines = [l.lstrip('- ').strip() for l in d['done_text'].splitlines() if l.strip().startswith('-')]
    done_items = '\n'.join(f'・{l}' for l in done_lines[:5])

    next_lines = [l.lstrip('- ').strip() for l in d['next_text'].splitlines() if l.strip().startswith('-')]
    next_items = '\n'.join(f'・{l}' for l in next_lines[:3])

    tweet1 = f"""副業収入レポート {d['month']}

収益：{d['total']}円
{art_line}
{pv_line}
{imp_line}
{clk_line}

目標の月5万まで：{d['gap']}円不足"""

    tweet2 = f"""今月やったこと：
{done_items}"""

    tweet3 = f"""来月の作戦：
{next_items}

詳細はブログに書きました👇
{WP_URL}"""

    return [tweet1.strip(), tweet2.strip(), tweet3.strip()]

# ============================================================
# ブログ記事（まとめ）生成
# ============================================================
def generate_blog(d, wp_total):
    metrics = clean_metrics(d['metrics_text'], wp_total)
    blog = f"""# {d['month']} 副業収入レポート｜Code to Rich

## 今月の収益

{d['revenue_text']}

## サイト指標

{metrics}

## 今月やったこと

{d['done_text']}

## うまくいったこと

{d['good_text']}

## うまくいかなかったこと

{d['bad_text']}

## 来月の作戦

{d['next_text']}

---

副業収入は毎月公開していきます。
目標は月5万円。引き続きよろしくお願いします。
"""
    return blog.strip()

# ============================================================
# メイン
# ============================================================
def main():
    print('=' * 60)
    print('Code to Rich - 月次レポート生成')
    print('=' * 60)

    print('\n📊 WordPress記事数を取得中...')
    wp_total = get_wp_stats()
    if wp_total:
        print(f'  → 公開記事数: {wp_total}本')

    print('\n📄 最新月のログを読み込み中...')
    log_text = get_latest_log()
    if not log_text:
        return

    d = parse_log(log_text)
    print(f'  → 対象月: {d["month"]}')

    thread = generate_thread(d, wp_total)
    blog   = generate_blog(d, wp_total)

    print('\n' + '=' * 60)
    print('【Xスレッド（3ツイート）】')
    print('=' * 60)
    for i, t in enumerate(thread, 1):
        print(f'\n--- ツイート{i} ({len(t)}文字) ---')
        print(t)

    print('\n' + '=' * 60)
    print('【ブログ記事用（マークダウン）】')
    print('=' * 60)
    print(blog)

    # ファイルに保存
    out_dir  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    os.makedirs(out_dir, exist_ok=True)
    month_slug = d['month'].replace('年', '-').replace('月', '')
    tweet_path = f'{out_dir}/{month_slug}-tweet.txt'
    blog_path  = f'{out_dir}/{month_slug}-blog.md'

    with open(tweet_path, 'w', encoding='utf-8') as f:
        f.write('\n\n---\n\n'.join(thread))
    with open(blog_path, 'w', encoding='utf-8') as f:
        f.write(blog)

    print(f'\n✅ 保存完了')
    print(f'  ツイート: {tweet_path}')
    print(f'  ブログ:   {blog_path}')

if __name__ == '__main__':
    main()
