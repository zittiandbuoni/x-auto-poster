# x-auto-poster

X (Twitter) の自動投稿パイプライン。WordPressブログの記事紹介ツイートと、Claude AIによるエンゲージメント投稿を自動生成・スケジュール配信する。

## 機能

- **記事ツイート自動生成** (`generate_article_tweets.py`)
  - WordPress REST APIから記事本文を取得
  - Claude AIでペルソナに沿った紹介ツイートを一括生成
  - パターンが重複しないよう生成履歴を参照

- **エンゲージメントツイート自動投稿** (`auto_tweet.py`)
  - SerpAPIでトレンドを取得してClaudeに渡す
  - 過去の投稿履歴を読み込み、口調・テーマの一貫性を維持
  - URLなし投稿($0.015/件)とURL付き投稿($0.20/件)を使い分け

- **月次レポート生成** (`monthly_report.py`)
  - `monthly_log.md`に記入した数字からスレッドツイート（3件）とブログ記事を自動生成
  - X の280文字制限に対応するため「数字サマリー→やったこと→来月の作戦」の3ツイート構成
  - WordPress REST APIから記事数を自動取得して埋め込む

## セットアップ

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .envに各APIキーを設定
```

## 使い方

```bash
# 記事ツイートを一括生成してtweet_queue.csvに保存
python3 generate_article_tweets.py

# エンゲージメントツイートをプレビュー（投稿なし）
python3 auto_tweet.py --type preview

# エンゲージメントツイートを投稿
python3 auto_tweet.py --type engagement

# キューから記事ツイートを1件投稿
python3 auto_tweet.py --type article

# 今月のAPIコスト確認
python3 auto_tweet.py --type cost

# 月次レポート生成（スレッド3ツイート＋ブログ記事を出力）
# 事前に monthly_log.md に当月の数字を記入しておく
python3 monthly_report.py
```

## cron設定例

```
# 毎朝8時：エンゲージメントツイート
0 8 * * * cd /path/to/x-auto-poster && venv/bin/python3 auto_tweet.py --type engagement

# 月水金12時：記事ツイート
0 12 * * 1,3,5 cd /path/to/x-auto-poster && venv/bin/python3 auto_tweet.py --type article

# 毎晩20時：エンゲージメントツイート
0 20 * * * cd /path/to/x-auto-poster && venv/bin/python3 auto_tweet.py --type engagement
```

## ペルソナ設定

`persona.md`にアカウントのキャラクター・口調・NGパターン・ツイート構成バリエーションを定義する。Claude APIの生成プロンプトに毎回読み込まれる。

## コスト目安

| 項目 | 単価 | 月間目安 |
|-----|------|---------|
| X API URLあり投稿 | $0.20/件 | $2〜3 |
| X API URLなし投稿 | $0.015/件 | $0.90 |
| Claude生成 | ~$0.034/件 | $2 |
| **合計** | | **~$5/月** |
