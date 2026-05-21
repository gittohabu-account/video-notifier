# 動画サイト新着通知システム

動画まとめサイトを定期巡回して、新着動画をGmailで通知する自動化システム。

## 全体像

```
┌─────────────────────┐
│  GitHub Actions     │  ← 毎日 JST 16:17 と 22:23 に自動起動
│  (Ubuntu runner)    │     (2回スケジュール：片方が失敗しても保険あり)
└──────────┬──────────┘
           │
           │ python video_notifier.py
           ▼
┌─────────────────────────────────────────┐
│  video_notifier.py                      │
│  1. 検索ページ取得（指定キーワード）         │
│  2. 5分未満動画フィルタ                   │
│  3. URL/サムネで既知判定                  │
│  4. 詳細ページから直リン抽出               │
│  5. 直リン先からog:image取得              │
│  6. サムネをCID埋め込みでGmail送信         │
└──────────┬──────────────────────────────┘
           │
           │ 結果を保存
           ▼
┌─────────────────────────────────────────┐
│  seen_urls.json / seen_thumbs.json      │
│  (リポジトリにコミットして次回引き継ぎ)      │
└─────────────────────────────────────────┘
           │
           │ 新着あればメール送信
           ▼
       Gmail受信箱
```

## ファイル構成

| ファイル | 役割 |
|---|---|
| `video_notifier.py` | メインスクリプト。全ロジックがここに |
| `.github/workflows/run.yml` | GitHub Actionsの定期実行設定（cron） |
| `seen_urls.json` | 既に通知した動画URLの履歴 |
| `seen_thumbs.json` | 既に通知したサムネURLの履歴（重複防止） |
| `.gitignore` | Git管理対象外ファイルの指定 |
| `README.md` | このドキュメント |

## 設定変更したいとき

### キーワードを変える
`video_notifier.py` の `SEARCH_QUERIES` を編集：

```python
SEARCH_QUERIES = [
    "着衣 ずらして",        # 通常検索（AND）
    "アニメ -予告",          # 「予告」を除外
    "ドラマ OR 映画",        # OR検索
]
```

### 実行頻度を変える
`.github/workflows/run.yml` の cron を編集：

```yaml
- cron: '17 7 * * *'     # 毎日 JST 16:17（デフォルト・メイン）
- cron: '23 13 * * *'    # 毎日 JST 22:23（デフォルト・保険）
- cron: '37 22 * * *'    # 毎日 JST 07:37（朝に変更したい場合）
- cron: '*/30 * * * *'   # 30分おき（高頻度）
```

注意点：
- GitHub ActionsのcronはUTC基準。**JST = UTC + 9時間** で逆算
  - JST 16:00 → UTC 07:00
  - JST 09:00 → UTC 00:00
- **「分=00」のキリ番時刻は数時間〜半日遅延することがある**ため、
  半端な分（例：17分、23分）を指定するのが鉄則
- 重要な通知は **複数の時間帯にスケジュール**するのが推奨
  （片方が失敗しても、seen_urls の重複防止により多重通知にはならない）

### 動画長フィルタを変える
`MIN_DURATION_MINUTES = 5` を編集（0で無効化）。

### 巡回ページ数を変える
`MAX_PAGES = 3` を編集（多くすると過去まで遡る、初回大量通知に注意）。

## GitHub Secrets（認証情報）

リポジトリの Settings → Secrets and variables → Actions で以下を設定：

| Secret名 | 値 |
|---|---|
| `GMAIL_ADDRESS` | 送信元Gmailアドレス |
| `GMAIL_APP_PASSWORD` | Gmailアプリパスワード（16桁） |
| `MAIL_TO` | 通知先メアド（自分宛なら同じ） |

## ローカルで手動実行する場合

```powershell
# 環境変数セット
$env:GMAIL_ADDRESS = "your@gmail.com"
$env:GMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"
$env:MAIL_TO = "your@gmail.com"

# 実行
python video_notifier.py
```

ライブラリ：`pip install requests beautifulsoup4`

## デバッグ（DRY_RUNモード）

メール送信せずに動作確認したいとき：

```python
DRY_RUN = True
DRY_RUN_MAX_ITEMS = 10
```

→ `dryrun_output.json` に取得結果が書き出されるだけ。

## トラブルシューティング

### 「メールが届かない」
- GitHubの Actions タブで実行ログを確認
- Secretsが正しく登録されているか
- Gmailアプリパスワードが有効か（取り消されてないか）

### 「サムネが出ない」
- 直リン先サイトに `og:image` / `screenshot1` などのメタタグが無いサイト構造の可能性
- `parse_destination_thumb()` のセレクタリスト追加で対応可

### 「同じ動画が何度も通知される」
- `seen_urls.json` が正しくコミットされていない可能性
- GitHub Actions の権限（contents: write）を確認
- リポジトリの最新コミット履歴に "Update seen state" が並んでいればOK

## 注意点

- GitHub Actionsの cron は混雑時に遅延することがある（数分〜十数分）
- 無料枠は月2000分。本構成（30分間隔・1実行20秒程度）なら**月240分前後**で十分余裕あり
- アンテナサイト側CDN（e2.eroimg.net）はホットリンク防止されているため、直リン先のog:imageを優先使用している

---

メールにこのREADMEへのリンクが貼られている場合、毎月の動作確認や設定変更時にここを参照してください。
