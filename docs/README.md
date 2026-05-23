# 動画検索 PWA（アドホック検索アプリ）

スマホのホーム画面アイコンをタップ → キーワード入力 → 数分後に Gmail で結果通知。

## セットアップ手順（初回1回だけ）

### 1. GitHub Pages を有効化

1. GitHub のリポジトリ画面 → **Settings** タブ
2. 左メニュー **Pages**
3. **Source**: `Deploy from a branch`
4. **Branch**: `main` / `/docs` を選択 → **Save**
5. 数分後、ページ上部に `Your site is live at https://<ユーザー名>.github.io/<リポジトリ名>/` と表示されればOK

### 2. Personal Access Token（PAT）を発行

GitHub Actions を外部から発火するための認証トークン。

1. https://github.com/settings/tokens?type=beta を開く（**fine-grained** 推奨）
2. **Generate new token**
3. 設定：
   - **Token name**: `video-notifier-pwa` など
   - **Expiration**: 任意（最大1年）
   - **Repository access**: `Only select repositories` → 該当リポジトリを選択
   - **Repository permissions** → **Actions**: `Read and write`
4. **Generate token** → 表示された `github_pat_xxxxx...` を**この場でコピー**（1度しか表示されない）

### 3. スマホで PWA を開いて初期設定

1. iPhone / Android のブラウザで `https://<ユーザー名>.github.io/<リポジトリ名>/` を開く
2. 初回起動時に設定ダイアログが自動で開く
3. 入力：
   - **GitHubユーザー名**: 例 `gittohabu-account`
   - **リポジトリ名**: 例 `video-notifier`
   - **Personal Access Token**: 手順2でコピーした値を貼り付け
   - **ワークフロー名**: `run.yml`（変えなくてOK）
4. **保存**

### 4. ホーム画面に追加

**iPhone (Safari)**
1. 画面下の共有ボタン（↑のアイコン）
2. **「ホーム画面に追加」**
3. 名前は「動画検索」のまま **追加**

**Android (Chrome)**
1. 右上の **⋮** メニュー
2. **「ホーム画面に追加」** または **「アプリをインストール」**
3. **追加**

これでホーム画面に青いアイコンが出ます。タップすると全画面アプリとして起動。

## 使い方

1. ホーム画面のアイコンをタップ
2. キーワードを入力（例：`+着衣 パンチラ OR ずらして`）
3. **検索する** ボタン
4. 「送信しました」表示 → 2〜3分後に Gmail に新着通知メールが届く
5. 過去のキーワードは履歴から再実行可能

## クエリ書式（既存と同じ）

| 書式 | 意味 |
|---|---|
| `AAA BBB` | AAA と BBB の両方を含む（AND） |
| `AAA OR BBB` | どちらか含む |
| `+AAA BBB OR CCC` | AAA は必須、BBB か CCC のどちらか |
| `AAA -BBB` | AAA を含み、BBB は含まない |

## 仕様メモ

- アドホック検索は `seen_urls.json` / `seen_thumbs.json` を**更新しない**ので、定期実行の通知に影響しません
- メール件名は `[動画新着][アドホック] 新着 N 件` で区別されます
- PAT・履歴はスマホの localStorage に保存（外部に送信されません）
- GitHub API への通信は HTTPS で直接行います（中継サーバーなし）

## トラブルシューティング

### 「送信に失敗しました（401 / 403）」
- PAT の期限切れ、または権限不足
- → 手順2をやり直し、設定画面で新しいPATを貼り付け

### 「送信に失敗しました（404）」
- ユーザー名 / リポジトリ名 / ワークフロー名のどれかが間違ってる
- → 設定画面で確認

### 「成功表示が出たのにメールが来ない」
- GitHub のリポジトリ → Actions タブで実行履歴を確認
- 赤×が出てたらログを開いてエラー内容を見る
- Gmail Secrets（`GMAIL_ADDRESS` 等）が正しく設定されているか

### 「ホーム画面アイコンが反映されない」
- Safari は一度ページを閉じてから追加し直すと反映されることが多い
- service-worker が登録されているか確認（DevTools → Application）

## 構成ファイル

```
docs/
├── index.html          ページ本体
├── style.css           スタイル（モバイル最適化・ダークモード対応）
├── app.js              ロジック（GitHub API 呼び出し・履歴・設定）
├── manifest.json       PWA マニフェスト
├── service-worker.js   PWA ホーム画面追加に必要
├── icon.svg            アイコン（SVG・Android向け）
├── icon-192.png        アイコン（iOS向け）
├── icon-512.png        アイコン（マニフェスト・スプラッシュ画面用）
└── README.md           このファイル
```
