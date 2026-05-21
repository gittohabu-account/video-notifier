# -*- coding: utf-8 -*-
"""
動画一覧まとめサイト 新着通知ツール

検索キーワードに合致する新着動画を定期チェックし、Gmailで通知します。
"""

import json
import os
import re
import smtplib
import sys
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from html import escape as html_escape
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup


# ==========================================================================
# 設定（ここを編集してください）
# ==========================================================================

# 対象サイトのベースURL（末尾スラッシュなし）
BASE_URL = "https://movie.eroterest.net/"

# 検索URLのテンプレート。{word} にキーワードが入り、{page} にページ番号が入ります。
# サイトによってはページ番号のパラメータ名が page= ではなく p= や &paged= の場合あり
SEARCH_URL_TEMPLATE = "https://movie.eroterest.net/?word={word}&c=&page={page}"

# 検索キーワード（リスト形式。各文字列に対し個別に検索を行う）
# 1キーワード内のフォーマット:
#   "AAA BBB"      → AAA と BBB を両方含むタイトル（AND検索、ローカルでフィルタ）
#   "AAA -BBB"     → AAA を含むが BBB を含まないタイトル（除外）
#   "AAA OR BBB"   → AAA か BBB のいずれかを含むタイトル（OR検索）
# サイト側の検索クエリには先頭の単語のみ渡し、複雑な条件はローカルで再フィルタします
SEARCH_QUERIES = [
    "+着衣 ずらしハメ or ずらしてハメ or ずらして挿入 or ずらし挿入",
    "+着衣 パンツずらして or パンツずらし or パンティずらして or パンティずらし",
    "+着衣 ずらしてバック or ずらしバック or tバックずらし or tバックずらして",
    "+着衣 ずらし生ハメ or ずらして生ハメ or ずらして生挿入 or ずらし生挿入",
]

# 1キーワードあたりにチェックする最大ページ数
MAX_PAGES = 3

# 動画長フィルタ：指定した分数未満の動画を通知対象から外す（0で無効）
MIN_DURATION_MINUTES = 5
# 動画長が表示されていない動画も弾くか（True=弾く / False=通す）
SKIP_UNKNOWN_DURATION = False

# サムネイル画像のURLでも重複チェックする（同じ動画の再投稿を防ぐ）
DEDUP_BY_THUMBNAIL = True

# 詳細ページを開いて本物の動画ホストURLに直リンする（新着のみ・1件ごとに+1リクエスト）
RESOLVE_DIRECT_LINKS = True
# 詳細ページ取得時のsleep秒数（連続アクセスでサイトに負荷をかけないため）
SLEEP_BETWEEN_DETAIL_REQUESTS = 1.5

# DRY_RUN モード: メールを送らず、結果を dryrun_output.json に出すだけ。
# テスト・デバッグ用。Trueの間は seen_urls / seen_thumbs も更新しない。
DRY_RUN = False
# DRY_RUN時に処理する最大件数（0で無制限）。少なくするとテスト高速化
DRY_RUN_MAX_ITEMS = 0

# ページ間アクセス時のsleep秒数（サイト負荷軽減のため必ず入れる）
SLEEP_BETWEEN_REQUESTS = 2.0

# HTTPリクエストのタイムアウト（秒）
REQUEST_TIMEOUT = 15

# User-Agent（ブラウザを装う。BOT判定回避のため）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 既知URLを保存するJSONファイルパス（このスクリプトと同じディレクトリ）
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_urls.json")
SEEN_THUMBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_thumbs.json")
DRY_RUN_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dryrun_output.json")

# Gmail設定（環境変数から読み込み。GitHub Secrets経由でセットされる想定）
# ローカル実行時は事前に PowerShell で:
#   $env:GMAIL_ADDRESS = "your@gmail.com"
#   $env:GMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"
#   $env:MAIL_TO = "your@gmail.com"
def _clean_env(name: str, default: str = "") -> str:
    """環境変数を読みつつBOM・前後空白を除去（PowerShellパイプ経由のBOM混入対策）"""
    v = os.environ.get(name, default) or ""
    return v.replace("﻿", "").strip()

GMAIL_ADDRESS = _clean_env("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = _clean_env("GMAIL_APP_PASSWORD")
MAIL_TO = _clean_env("MAIL_TO", GMAIL_ADDRESS)
MAIL_SUBJECT_PREFIX = "[動画新着]"
# メール末尾に貼るシステム概要ページURL（READMEや管理ページ）
# 環境変数で上書き可能。空文字なら表示しない。
SYSTEM_README_URL = os.environ.get(
    "SYSTEM_README_URL",
    "https://github.com/gittohabu-account/video-notifier/blob/main/README.md",
)

# 1回の通知メールに掲載する最大件数（多すぎる場合の保護）
MAX_ITEMS_PER_MAIL = 50


# ==========================================================================
# スクレイピング
# ==========================================================================

def build_session() -> requests.Session:
    """共通ヘッダ付きのrequests.Sessionを生成"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    })
    return s


def fetch_html(session: requests.Session, url: str, retries: int = 2) -> str | None:
    """
    指定URLのHTMLを取得。失敗時はNone。
    GitHub Actions のIPは時々403になるので、軽くリトライする。
    """
    backoff = 3.0  # 秒（リトライ間隔）
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            if resp.encoding and resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding
            return resp.text
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    print(f"[WARN] fetch failed (after {retries + 1} tries): {url} : {last_err}",
          file=sys.stderr)
    return None


def parse_items(html: str) -> list[dict]:
    """
    検索結果HTMLから {"title": ..., "url": ...} のリストを返す。

    動画エロタレスト (movie.eroterest.net) 用セレクタ:
      <div class="itemWrapper">                       … 1動画のかたまり
        <div class="item [goodSiteItem|itemDead]">
          <div class="itemHead">
            <div class="itemTitle">
              <a href="/page/XXXXXX/" title="タイトル">タイトル</a>
          <div class="itemBody">
            <div class="itemImage">
              <img src="//e2.eroimg.net/.../..jpeg" ...>  … サムネイル
              <span class="movieTime">49分</span>           … 動画長
            <div class="itemFoot">
              <div class="itemTime">6年前</div>             … 投稿時期

      ・「itemDead」クラスは動画削除済み → 通知不要なので除外
      ・<a>の title="..." 属性を採用（テキストだと「優良サイト」バッジ文字が混入するため）
      ・サムネは <picture><source webp> もあるが、メール互換のため <img src> (jpeg) を使用
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for wrapper in soup.select("div.itemWrapper"):
        # 動画削除済みはスキップ
        if wrapper.select_one("div.itemDead"):
            continue

        a = wrapper.select_one("div.itemTitle a[href]")
        if not a:
            continue

        title = (a.get("title") or a.get_text(strip=True)).strip()
        href = (a.get("href") or "").strip()
        if not title or not href:
            continue

        if title.startswith("[動画削除済み]"):
            continue

        url = urljoin(BASE_URL, href)

        # サムネイル画像（プロトコル相対URL `//...` は urljoin で https:// に）
        img = wrapper.select_one("div.itemImage img")
        thumb_url = urljoin(BASE_URL, (img.get("src") or "").strip()) if img else ""

        # 投稿時期（例: "6年前", "3時間前"）
        time_el = wrapper.select_one("div.itemTime")
        posted = time_el.get_text(strip=True) if time_el else ""

        # 動画長（例: "49分"）
        dur_el = wrapper.select_one("span.movieTime")
        duration = dur_el.get_text(strip=True) if dur_el else ""

        # 配信元ホスト名（例: "ShareVideos", "TXXX", "PornHub"）
        host_el = wrapper.select_one("span.proName")
        host = host_el.get_text(strip=True) if host_el else ""

        # カテゴリタグ（itemTag内の<a>テキスト。例: 企画/人気女優/朝から など）
        tags = [
            el.get_text(strip=True)
            for el in wrapper.select("div.itemTag a")
            if el.get_text(strip=True)
        ]

        items.append({
            "title": title,
            "url": url,
            "thumb": thumb_url,
            "posted": posted,
            "duration": duration,
            "host": host,
            "tags": tags,
        })

    return _dedup(items)


def _dedup(items: list[dict]) -> list[dict]:
    """URLをキーに重複除去（順序保持）"""
    seen = set()
    out = []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        out.append(it)
    return out


def fetch_destination_info(
    session: requests.Session, dest_url: str, expected_host: str = "",
) -> tuple[str | None, str | None]:
    """
    直リン先（動画紹介サイト）のページから2種類の情報を取得：
      (og_image_url, video_host_link)

    expected_host: eroterest の proName 値（例 "ドーガ", "TokyoMotion", "TXXX"）。
                   指定すると、そのホストの既知ドメインを優先的に探す。
    1回のHTTPリクエストで両方の抽出を行う。
    """
    try:
        r = session.get(dest_url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        if r.encoding and r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
    except (requests.RequestException, OSError):
        return None, None

    return (
        _extract_og_image(soup, dest_url),
        _extract_video_host_link(soup, dest_url, expected_host),
    )


# eroterest の proName 表示 → 実際の動画ホストドメイン
# このマップにあるホスト名の場合、紹介サイト内でそのドメインのリンクを
# 最優先で探す（汎用ヒューリスティックより精度が高い）
_HOST_DOMAIN_MAP = {
    "ドーガ": "do-ga.eroterest.net",     # eroterest自前プレーヤー（重要：通常は除外対象なので特例扱い）
    "TokyoMotion": "tokyomotion.net",
    "TXXX": "txxx.com",
    "VJAV": "vjav.com",
    "ShareVideos": "sharevideos.com",
    "PornHub": "pornhub.com",
    "hclips": "hclips.com",
    "HClips": "hclips.com",
    "MGStage": "mgstage.com",
    "Senzuri": "senzuri.tube",
    "センズリ": "senzuri.tube",
    "HDZog": "hdzog.com",
    "xHamster": "xhamster.com",
    "RedTube": "redtube.com",
    "Xvideos": "xvideos.com",
    "SpankBang": "spankbang.com",
    "Eporner": "eporner.com",
    "JavTube": "javtube.com",
    "JavHub": "javhub.net",
    "EroVideo": "ero-video.net",
}


# 動画ホストリンク抽出時にスキップするドメイン（広告・アンテナ・SNS・関連系）
# 末尾一致でマッチ判定するので、"x.com" は "*.x.com" にしか当たらず "txxx.com" は通る
_VIDEO_HOST_SKIP_DOMAINS = (
    "eroterest.net",
    "fanza.co.jp", "fanza.com", "dmm.com", "dmm.co.jp", "al.fanza.co.jp",
    "happymail.jp", "match.com",
    "shinobi.jp",
    "google.com", "google.co.jp", "googleadservices.com", "googletagmanager.com",
    "twitter.com", "x.com", "facebook.com", "instagram.com", "line.me",
    "youtube.com", "youtu.be",
    "amazon.co.jp", "amazon.com", "rakuten.co.jp",
    "doubleclick.net", "i-mobile.co.jp", "popin.cc", "outbrain.com",
    "accesstrade.net", "popads.net",
    "okbp.xyz", "okclix.com",
    "fetish-portal.click", "portal-fetish.com",
    "kanzae.net",
)

# 画像CDN（リンク先が動画ページではないので除外）
_IMAGE_CDN_PREFIXES = ("image.", "img.", "static.", "cdn.", "thumb.", "thumbs.", "i.", "pic.")


def _extract_video_host_link(soup, base_url: str, expected_host: str = "") -> str | None:
    """
    紹介サイトのHTMLから動画共有サイトへのリンクを抽出。

    戦略:
      Phase 0: expected_host が既知マップにあれば、そのドメインを最優先で探す
      Phase 1: 画像を囲む外部リンクの最初のもの（広告系・画像CDNは除外）
      Phase 2: CTA文言を含む外部リンク（"視聴", "クリック", "動画を見る" 等）
    """
    from urllib.parse import urlparse
    own_host = urlparse(base_url).netloc.lower()
    target_domain = _HOST_DOMAIN_MAP.get(expected_host, "") if expected_host else ""

    # Phase 0: ホスト名が既知ならそのドメインを「厳密に」探す
    # 見つかった → 返す。見つからない → None（汎用フォールバックには進まない）
    # 理由: ホストタグがTXXXなのに紹介サイトにTXXXリンクが無い場合、
    #       その紹介サイトは「だましサイト」の可能性が高い。間違ったリンクを返すより無の方が安全。
    if target_domain:
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href.startswith(("http://", "https://")):
                continue
            host = urlparse(href).netloc.lower()
            if host == target_domain or host.endswith("." + target_domain):
                # 画像CDNサブドメインは除外（例: image.mgstage.com はNG、www.mgstage.comはOK）
                if any(host.startswith(p) for p in _IMAGE_CDN_PREFIXES):
                    continue
                return href
        # ホスト指定があったのに該当ドメインが見つからない → だまし疑い → None
        return None

    def _accept(href: str) -> bool:
        if not href or not href.startswith(("http://", "https://")):
            return False
        host = urlparse(href).netloc.lower()
        if not host:
            return False
        # 自ドメインや関連サブドメインは除外
        if host == own_host or host.endswith("." + own_host) or own_host.endswith("." + host):
            return False
        # 既知のスキップ対象（末尾一致）
        for skip in _VIDEO_HOST_SKIP_DOMAINS:
            if host == skip or host.endswith("." + skip):
                return False
        # 画像CDN
        if any(host.startswith(p) for p in _IMAGE_CDN_PREFIXES):
            return False
        return True

    # Phase 1: 画像を含む外部リンク
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not _accept(href):
            continue
        if a.find("img"):
            return href

    # Phase 2: CTAテキストリンク
    cta_patterns = (
        "視聴", "再生", "動画を見る", "動画はこちら", "ここをクリック",
        "クリック", "本編", "観る", "見る", "watch", "play",
    )
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not _accept(href):
            continue
        text = (a.get_text() or "").strip()
        if any(p in text for p in cta_patterns):
            return href

    return None


def _extract_og_image(soup, base_url: str) -> str | None:
    """ページのメインサムネ画像URLを抽出（og:image系を中心に複数戦略）"""
    # 優先順:
    #   1) og:image系（SNS用、ほぼ確実にメイン画像）
    #   2) twitter:image系
    #   3) <link rel="image_src">
    #   4) <video poster="..."> 動画プレーヤーのプレビュー画像
    #   5) WordPress系まとめサイトでよくあるクラスの <img>
    #   6) ページ内で最大サイズの <img>（最終フォールバック）
    for selector, attr in [
        ('meta[property="og:image:secure_url"]', "content"),
        ('meta[property="og:image"]', "content"),
        ('meta[property="og:image:url"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('meta[name="twitter:image:src"]', "content"),
        ('link[rel="image_src"]', "href"),
        ('video[poster]', "poster"),
        # 動画まとめWPテンプレでよく使われるクラス
        ('img.screenshot1', "src"),
        ('img.screenshot', "src"),
        ('img.wp-post-image', "src"),
        ('img.attachment-post-thumbnail', "src"),
        ('article img.size-full', "src"),
        ('figure img', "src"),
    ]:
        el = soup.select_one(selector)
        if not el:
            continue
        val = (el.get(attr) or "").strip()
        if val:
            return urljoin(base_url, val)

    # 最終フォールバック: <img> の中で width*height が最大のものを選ぶ
    # （ロゴやアイコンを避けるため width >= 200 のみ対象）
    # widthが "100%" のような文字列の場合は数値変換失敗 → スキップ
    best_img = None
    best_area = 0
    for img in soup.select("img[src]"):
        src = (img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        try:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
        except (ValueError, TypeError):
            continue
        if w < 200:
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best_img = src
    if best_img:
        return urljoin(base_url, best_img)

    return None


def resolve_direct_url(session: requests.Session, page_url: str) -> str | None:
    """
    動画詳細ページ（/page/XXXX/）から本物の配信ホストURLを取り出す。
    成功時: 直リンURL文字列、失敗時: None

    詳細ページ構造:
      <div class="gotoBlog"><a href="https://本物のホスト/..." target="_blank">のページへ行く</a></div>
      （フォールバック: <div class="pageDetail"> 内のサムネリンク）
    """
    html = fetch_html(session, page_url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # 「のページへ行く」ボタンが第一候補
    a = soup.select_one("div.gotoBlog a[href]")
    # フォールバック: 詳細ページのサムネを囲む外部リンク
    if not a:
        a = soup.select_one("div.pageDetail div.itemBody a[href][target='_blank']")
    if not a:
        return None

    href = (a.get("href") or "").strip()
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    # アンテナ自身を指している場合は外部リンクではない
    if "movie.eroterest.net" in href:
        return None
    return href


def parse_duration_minutes(text: str) -> int | None:
    """
    "49分" "1時間20分" "30秒" "1時間" などを分単位の整数に変換。
    数値が読めなかったら None を返す（動画長不明の扱い）。
    """
    if not text:
        return None
    # 全角数字を半角に
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    h = re.search(r"(\d+)\s*時間", text)
    m = re.search(r"(\d+)\s*分", text)
    s = re.search(r"(\d+)\s*秒", text)
    if not (h or m or s):
        return None
    total_sec = 0
    if h:
        total_sec += int(h.group(1)) * 3600
    if m:
        total_sec += int(m.group(1)) * 60
    if s:
        total_sec += int(s.group(1))
    return total_sec // 60


# ==========================================================================
# キーワードクエリの解釈
# ==========================================================================

def parse_query(query: str) -> tuple[str, callable, bool]:
    """
    ユーザクエリを「サイトに渡す検索語」「ローカル側フィルタ関数」「OR検索フラグ」に分解。

    動画エロタレストはサイト自身がAND/-除外/OR構文をサポートしている。
    OR検索はURL上では wordChkOr=1 パラメータで表現される（ブラウザフォーム準拠）。

    対応構文:
      "AAA BBB"               → AND（全部必須）
      "AAA -BBB"              → AAA含み、BBB除外
      "AAA OR BBB"            → AAA か BBB のどちらか含む
      "+AAA BBB OR CCC"       → AAA必須 AND (BBB OR CCC) ← 新構文
      "+AAA +BBB CCC OR DDD"  → AAA・BBB必須 AND (CCC OR DDD)

    "+"で始まる単語は必須扱い。OR検索時もこれらは必ず含まれる。
    """
    tokens = query.strip().split()
    if not tokens:
        return "", (lambda _it: True), False

    is_or = any(t.upper() == "OR" for t in tokens)
    required_terms: list[str] = []   # 必ず含む（タイトル+タグで検査）
    excluded_terms: list[str] = []   # 含まない
    or_terms: list[str] = []         # OR検索時の候補語

    for t in tokens:
        if t.upper() == "OR":
            continue
        if t.startswith("+") and len(t) > 1:
            required_terms.append(t[1:])
        elif t.startswith("-") and len(t) > 1:
            excluded_terms.append(t[1:])
        else:
            # OR検索時は OR候補、AND検索時は必須語として扱う
            if is_or:
                or_terms.append(t)
            else:
                required_terms.append(t)

    if is_or:
        # OR検索: OR候補のみサイトに送る（wordChkOr=1付）
        # 必須語(+)はサイトでは表現できないため、ローカルフィルタで縛る
        site_query = " ".join(or_terms + [f"-{ng}" for ng in excluded_terms])
    else:
        # AND検索: 必須語＋除外をそのままサイトに送る
        site_query = " ".join(required_terms + [f"-{ng}" for ng in excluded_terms])

    def _filter(item: dict) -> bool:
        """item dict（title, tagsを含む）に対する必須・除外チェック"""
        searchable = item.get("title") or ""
        if item.get("tags"):
            searchable += " " + " ".join(item["tags"])
        for r in required_terms:
            if r not in searchable:
                return False
        for ng in excluded_terms:
            if ng in searchable:
                return False
        return True

    return site_query, _filter, is_or


# ==========================================================================
# 既知URLストア
# ==========================================================================

def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        if isinstance(data, dict) and "urls" in data:
            return set(data["urls"])
        return set()
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] seen file load failed: {e}", file=sys.stderr)
        return set()


def save_seen(urls: set[str]) -> None:
    _save_json_set(urls, SEEN_FILE, "seen")


def load_seen_thumbs() -> set[str]:
    if not os.path.exists(SEEN_THUMBS_FILE):
        return set()
    try:
        with open(SEEN_THUMBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] seen-thumbs file load failed: {e}", file=sys.stderr)
        return set()


def save_seen_thumbs(thumbs: set[str]) -> None:
    _save_json_set(thumbs, SEEN_THUMBS_FILE, "seen-thumbs")


def _save_json_set(values: set[str], path: str, label: str) -> None:
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(sorted(values), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError as e:
        print(f"[ERROR] {label} file save failed: {e}", file=sys.stderr)


# ==========================================================================
# Gmail通知
# ==========================================================================

def send_mail(session: requests.Session, new_items: list[dict]) -> None:
    """
    新着アイテムをGmailで通知。
    サムネ画像は CID 埋め込み方式（multipart/related）で添付し、
    Gmailの外部画像ブロック・ホットリンク防止の両方を回避する。
    """
    if not new_items:
        return

    items = new_items[:MAX_ITEMS_PER_MAIL]
    truncated = len(new_items) - len(items)
    subject = f"{MAIL_SUBJECT_PREFIX} 新着 {len(new_items)} 件"

    # サムネをダウンロードしCID参照に変換
    # 優先順位: 直リン先のog:image → eroterestのサムネ（フォールバック）
    base_headers = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }
    thumb_cids: dict[str, str] = {}
    thumb_payloads: list[tuple[str, bytes, str]] = []  # (cid, bytes, subtype)

    def _try_fetch(url: str, referers: list[str]) -> tuple[bytes, str] | None:
        last_err = None
        for ref in referers:
            try:
                hdr = dict(base_headers, Referer=ref)
                r = session.get(url, timeout=REQUEST_TIMEOUT, headers=hdr)
                r.raise_for_status()
                return r.content, r.headers.get("Content-Type", "").lower()
            except requests.RequestException as e:
                last_err = e
        if last_err:
            print(f"[WARN] thumb fetch failed: {url} : {last_err}", file=sys.stderr)
        return None

    for it in items:
        # 候補リスト: (URL, Referer候補)
        # 注: eroterest CDN (e2.eroimg.net) は403を返すので試さない。
        #     直リン先のog:image系のみを候補とする。
        candidates: list[tuple[str, list[str]]] = []
        if it.get("dest_thumb"):
            candidates.append((it["dest_thumb"], [it.get("direct_url") or it["dest_thumb"]]))

        for url, refs in candidates:
            if url in thumb_cids:
                # 既にダウンロード済みなら再利用
                break
            result = _try_fetch(url, refs)
            if result:
                content, content_type = result
                subtype = (
                    "png" if "png" in content_type
                    else "gif" if "gif" in content_type
                    else "webp" if "webp" in content_type
                    else "jpeg"
                )
                cid = f"thumb{len(thumb_cids)}"
                thumb_cids[url] = cid
                thumb_payloads.append((cid, content, subtype))
                # この item についてはダウンロード成功 → 次へ
                break
    print(f"[INFO] embedded {len(thumb_payloads)} thumbnails")

    # --- プレーンテキスト版（HTML非対応クライアント向けフォールバック）---
    text_lines = [f"新着 {len(new_items)} 件を検出しました。\n"]
    for i, it in enumerate(items, 1):
        text_lines.append(f"{i}. {it['title']}")
        meta = [v for v in (it.get("duration"), it.get("posted"), it.get("host")) if v]
        if meta:
            text_lines.append(f"   ({' / '.join(meta)})")
        if it.get("tags"):
            text_lines.append(f"   tags: {' / '.join(it['tags'])}")
        # リンク優先度: 動画共有サイト > 紹介サイト > アンテナ
        if it.get("video_host_url"):
            text_lines.append(f"   ▶ 動画ホスト直行: {it['video_host_url']}")
        if it.get("direct_url"):
            label = "紹介サイト経由" if it.get("video_host_url") else "▶ 紹介サイト"
            text_lines.append(f"   {label}: {it['direct_url']}")
        text_lines.append(f"   アンテナ: {it['url']}")
        if it.get("query"):
            text_lines.append(f"   query: {it['query']}")
        text_lines.append("")
    if truncated > 0:
        text_lines.append(f"...他 {truncated} 件は省略しました。")
    if SYSTEM_README_URL:
        text_lines.append("")
        text_lines.append("-" * 40)
        text_lines.append(f"このメールの仕組み・設定変更: {SYSTEM_README_URL}")
    text_body = "\n".join(text_lines)

    # --- HTML版（サムネ画像入り、最深リンク優先、モバイル最適化）---
    # リンク優先順位:
    #   video_host_url（動画共有サイト直行） > direct_url（紹介サイト） > eroterest
    blocks = []
    for i, it in enumerate(items, 1):
        primary_url = it.get("video_host_url") or it.get("direct_url") or it["url"]
        has_host = bool(it.get("video_host_url"))
        has_direct = bool(it.get("direct_url"))

        thumb_html = ""
        # 表示優先: 直リン先のog:image → eroterestサムネ
        img_src = ""
        for cand in (it.get("dest_thumb"), it.get("thumb")):
            if not cand:
                continue
            cid = thumb_cids.get(cand)
            if cid:
                img_src = f"cid:{cid}"
                break
        # CID埋め込みが全滅した場合のフォールバック（外部URL直リン）
        if not img_src:
            img_src = it.get("dest_thumb") or it.get("thumb") or ""
        if img_src:
            # スマホでは画面いっぱい、PCでは480pxを上限に
            thumb_html = (
                f'<a href="{html_escape(primary_url, quote=True)}" '
                f'style="display:block;margin:10px 0;">'
                f'<img src="{html_escape(img_src, quote=True)}" alt="" '
                f'style="width:100%;max-width:480px;height:auto;'
                f'display:block;border:0;border-radius:6px;"></a>'
            )

        # メタ情報: 動画長 / 投稿時期 / 配信元ホスト
        meta_parts = [html_escape(v) for v in (it.get("duration"), it.get("posted"), it.get("host")) if v]
        meta_html = (
            f'<div style="color:#666;font-size:13px;margin-top:4px;'
            f'line-height:1.5;">{" / ".join(meta_parts)}</div>'
        ) if meta_parts else ""

        # カテゴリタグ（最大10個。エロタレストの itemTag）
        tags_html = ""
        if it.get("tags"):
            shown_tags = it["tags"][:10]
            tags_html = (
                f'<div style="color:#888;font-size:12px;margin-top:4px;'
                f'line-height:1.6;">🏷 {"・".join(html_escape(t) for t in shown_tags)}</div>'
            )

        # サブリンク：動画ホストがprimaryなら紹介サイトとアンテナを併記
        #             紹介サイトがprimaryならアンテナのみ併記
        sublink_parts = []
        if has_host and has_direct:
            sublink_parts.append(
                f'<a href="{html_escape(it["direct_url"], quote=True)}" '
                f'style="color:#666;font-size:13px;text-decoration:underline;'
                f'display:inline-block;padding:6px 8px 6px 0;">紹介サイトへ</a>'
            )
        if has_host or has_direct:
            sublink_parts.append(
                f'<a href="{html_escape(it["url"], quote=True)}" '
                f'style="color:#888;font-size:13px;text-decoration:underline;'
                f'display:inline-block;padding:6px 8px 6px 0;">アンテナへ</a>'
            )
        sublink_html = (
            f'<div style="margin-top:8px;">{"".join(sublink_parts)}</div>'
            if sublink_parts else ""
        )

        # バッジ表示：video_host > direct > なし
        if has_host:
            badge_html = (
                '<span style="display:inline-block;background:#ea4335;color:#fff;'
                'font-size:11px;padding:2px 7px;border-radius:3px;margin-left:6px;'
                'vertical-align:middle;font-weight:normal;">動画直</span>'
            )
        elif has_direct:
            badge_html = (
                '<span style="display:inline-block;background:#34a853;color:#fff;'
                'font-size:11px;padding:2px 7px;border-radius:3px;margin-left:6px;'
                'vertical-align:middle;font-weight:normal;">直リン</span>'
            )
        else:
            badge_html = ""

        query_html = (
            f'<div style="color:#aaa;font-size:11px;margin-top:4px;">'
            f'query: {html_escape(it["query"])}</div>'
        ) if it.get("query") else ""

        # タイトル: スマホで読みやすい16px、line-height広め、タップ領域確保
        blocks.append(
            f'<div style="margin:0 0 28px 0;padding-bottom:18px;'
            f'border-bottom:1px solid #eee;">'
            f'<div style="font-size:16px;font-weight:bold;line-height:1.45;'
            f'word-break:break-word;">'
            f'<a href="{html_escape(primary_url, quote=True)}" '
            f'style="color:#1a73e8;text-decoration:none;'
            f'display:inline-block;padding:4px 0;">'
            f'{i}. {html_escape(it["title"])}</a>{badge_html}</div>'
            f'{meta_html}{tags_html}{thumb_html}{sublink_html}{query_html}</div>'
        )
    trunc_html = (
        f'<p style="color:#999;font-size:14px;">'
        f'...他 {truncated} 件は省略しました。</p>'
        if truncated > 0 else ""
    )
    # メール末尾のシステム情報フッター
    footer_html = (
        f'<div style="margin-top:32px;padding-top:16px;border-top:2px solid #ddd;'
        f'color:#888;font-size:12px;line-height:1.6;">'
        f'<p style="margin:0;">'
        f'このメールは GitHub Actions で動く自動通知システムから送信されました。<br>'
        f'仕組みの確認・設定変更・トラブル対応はこちら →<br>'
        f'<a href="{html_escape(SYSTEM_README_URL, quote=True)}" '
        f'style="color:#1a73e8;word-break:break-all;">'
        f'{html_escape(SYSTEM_README_URL)}</a>'
        f'</p></div>'
    ) if SYSTEM_README_URL else ""
    # viewport meta でモバイル幅を端末に合わせる
    # 左右に少しpaddingを入れて画面端ぴったりにならないように
    html_body = (
        '<!doctype html><html lang="ja"><head>'
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>新着通知</title>'
        '</head>'
        '<body style="margin:0;padding:12px;font-family:'
        '-apple-system,BlinkMacSystemFont,\'Segoe UI\',\'Hiragino Sans\','
        '\'Yu Gothic\',sans-serif;font-size:15px;color:#222;line-height:1.5;'
        'background:#fff;">'
        '<div style="max-width:600px;margin:0 auto;">'
        f'<p style="font-size:15px;margin:0 0 16px 0;">'
        f'新着 <b>{len(new_items)}</b> 件を検出しました。</p>'
        f'{"".join(blocks)}{trunc_html}{footer_html}'
        '</div></body></html>'
    )

    # 構造:
    #   multipart/related  ← 全体
    #     multipart/alternative  ← テキスト/HTML切替
    #       text/plain
    #       text/html  (cid:thumb0, cid:thumb1, ... を参照)
    #     image/jpeg  Content-ID: <thumb0>
    #     image/jpeg  Content-ID: <thumb1>
    #     ...
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = MAIL_TO
    msg["Date"] = formatdate(localtime=True)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    for cid, data, subtype in thumb_payloads:
        img_part = MIMEImage(data, _subtype=subtype)
        img_part.add_header("Content-ID", f"<{cid}>")
        img_part.add_header("Content-Disposition", "inline", filename=f"{cid}.{subtype}")
        msg.attach(img_part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.sendmail(GMAIL_ADDRESS, [a.strip() for a in MAIL_TO.split(",")], msg.as_string())
        print(f"[INFO] mail sent: {len(new_items)} items")
    except (smtplib.SMTPException, OSError) as e:
        print(f"[ERROR] mail send failed: {e}", file=sys.stderr)


# ==========================================================================
# メイン
# ==========================================================================

def crawl_one_query(session: requests.Session, query: str) -> list[dict]:
    """1キーワード分のクロール。マッチした全アイテムを返す（新旧問わず）"""
    fetch_word, title_filter, is_or = parse_query(query)
    if not fetch_word:
        return []

    matched: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = SEARCH_URL_TEMPLATE.format(word=quote_plus(fetch_word), page=page)
        # OR検索時はサイトのフォーム挙動に合わせ wordChkOr=1 を付与
        if is_or:
            url += "&wordChkOr=1"
        print(f"[INFO] GET {url}")
        html = fetch_html(session, url)
        if not html:
            break

        items = parse_items(html)
        if not items:
            # 0件ページに到達したら以降は見ない
            print(f"[INFO] no items on page {page}, stop paging.")
            break

        # ローカルフィルタ（必須語/除外語をtitle+tagsで検査）
        for it in items:
            if title_filter(it):
                it = dict(it)
                it["query"] = query
                matched.append(it)

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    return matched


def main() -> int:
    if not SEARCH_QUERIES:
        print("[ERROR] SEARCH_QUERIES is empty.", file=sys.stderr)
        return 1
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(
            "[ERROR] GMAIL_ADDRESS / GMAIL_APP_PASSWORD environment variables "
            "are required. Set them via GitHub Secrets or local env vars.",
            file=sys.stderr,
        )
        return 1

    session = build_session()
    if DRY_RUN:
        # DRY_RUN: 既知ファイルを読まず、全件を「新着」扱いにして調査できるように
        seen: set[str] = set()
        seen_thumbs: set[str] = set()
        print("[INFO] DRY_RUN mode: ignoring seen state, mail will NOT be sent")
    else:
        seen = load_seen()
        seen_thumbs = load_seen_thumbs() if DEDUP_BY_THUMBNAIL else set()
        print(f"[INFO] loaded {len(seen)} known URLs, {len(seen_thumbs)} known thumbs")

    all_matched: list[dict] = []
    for q in SEARCH_QUERIES:
        try:
            all_matched.extend(crawl_one_query(session, q))
        except Exception as e:
            # 1キーワードの失敗で全体を止めない
            print(f"[ERROR] query failed: {q} : {e}", file=sys.stderr)

    # URL重複除去（複数クエリで同じ動画にヒットする可能性）
    all_matched = _dedup(all_matched)

    # 動画長フィルタ
    filtered: list[dict] = []
    skipped_short = 0
    skipped_unknown = 0
    for it in all_matched:
        mins = parse_duration_minutes(it.get("duration", ""))
        if mins is None:
            if SKIP_UNKNOWN_DURATION:
                skipped_unknown += 1
                continue
        elif MIN_DURATION_MINUTES > 0 and mins < MIN_DURATION_MINUTES:
            skipped_short += 1
            continue
        filtered.append(it)
    if skipped_short or skipped_unknown:
        print(f"[INFO] duration filter: skipped {skipped_short} short, "
              f"{skipped_unknown} unknown")

    # 新着判定: URL未知 AND（サムネ重複除外が有効ならサムネも未知）
    new_items = []
    for it in filtered:
        if it["url"] in seen:
            continue
        if DEDUP_BY_THUMBNAIL and it.get("thumb") and it["thumb"] in seen_thumbs:
            continue
        new_items.append(it)
    print(f"[INFO] matched={len(all_matched)}, after-filter={len(filtered)}, "
          f"new={len(new_items)}")

    # DRY_RUN時の件数制限
    if DRY_RUN and DRY_RUN_MAX_ITEMS > 0:
        new_items = new_items[:DRY_RUN_MAX_ITEMS]
        print(f"[INFO] DRY_RUN: limiting to {len(new_items)} items")

    # 新着のみ詳細ページを開いて直リンURLを解決（重い処理なので新着限定）
    # 直リン先からは og:image（サムネ）と video_host_url（動画共有サイトへのリンク）を
    # 1回のHTTPで両方取得する。
    if RESOLVE_DIRECT_LINKS and new_items:
        print(f"[INFO] resolving direct URLs for {len(new_items)} new items...")
        resolved = 0
        dest_thumbs = 0
        video_hosts = 0
        for it in new_items:
            try:
                direct = resolve_direct_url(session, it["url"])
            except Exception as e:
                print(f"[WARN] resolve failed: {it['url']} : {e}", file=sys.stderr)
                direct = None
            time.sleep(SLEEP_BETWEEN_DETAIL_REQUESTS)

            if direct:
                it["direct_url"] = direct
                resolved += 1
                # 直リン先ページから2情報を1回で取得
                # ホスト名（"TXXX" 等）を渡して、その動画ホストドメインを優先的に探す
                try:
                    dt, vh = fetch_destination_info(
                        session, direct, expected_host=it.get("host", "")
                    )
                except Exception as e:
                    print(f"[WARN] dest info failed: {direct} : {e}", file=sys.stderr)
                    dt, vh = None, None
                if dt:
                    it["dest_thumb"] = dt
                    dest_thumbs += 1
                if vh:
                    it["video_host_url"] = vh
                    video_hosts += 1
                time.sleep(SLEEP_BETWEEN_DETAIL_REQUESTS)
        print(f"[INFO] resolved {resolved}/{len(new_items)} direct URLs, "
              f"got {dest_thumbs} thumbnails, {video_hosts} video-host links")

    if DRY_RUN:
        # 結果をJSONに書き出すだけ。メール送信・既知ストア更新はスキップ
        try:
            with open(DRY_RUN_OUTPUT, "w", encoding="utf-8") as f:
                json.dump(new_items, f, ensure_ascii=False, indent=2)
            print(f"[INFO] DRY_RUN: wrote {len(new_items)} items to {DRY_RUN_OUTPUT}")
        except OSError as e:
            print(f"[ERROR] DRY_RUN output write failed: {e}", file=sys.stderr)
        return 0

    if new_items:
        send_mail(session, new_items)

    # 既知ストア更新（フィルタ後のものを登録 = 次回以降「既知」扱い）
    seen.update(it["url"] for it in filtered)
    save_seen(seen)
    if DEDUP_BY_THUMBNAIL:
        seen_thumbs.update(it["thumb"] for it in filtered if it.get("thumb"))
        save_seen_thumbs(seen_thumbs)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ==========================================================================
# README / セットアップ
# ==========================================================================
#
# 1) 必要パッケージのインストール
# ---------------------------------------------------------------
#   pip install requests beautifulsoup4
#
#
# 2) Gmail アプリパスワードの取得方法
# ---------------------------------------------------------------
#   通常のGoogleアカウントパスワードではSMTP送信できません。
#   「アプリパスワード（16桁）」を発行してください。
#
#   手順:
#     a. Googleアカウント → セキュリティ
#        https://myaccount.google.com/security
#     b. 「2段階認証プロセス」を有効化（必須）
#     c. 「アプリパスワード」ページを開く
#        https://myaccount.google.com/apppasswords
#     d. アプリ名（例: "video_notifier"）を入力 → 生成
#     e. 表示された16桁の文字列（例: "abcd efgh ijkl mnop"）を
#        本スクリプト先頭の GMAIL_APP_PASSWORD にコピー
#        （スペース込みでも可）
#
#   注意: アプリパスワードは1度しか表示されません。控えておくこと。
#
#
# 3) 動作確認
# ---------------------------------------------------------------
#   まずは BASE_URL / SEARCH_URL_TEMPLATE を実サイトに合わせ、
#   parse_items() のセレクタを調整してから:
#
#     python video_notifier.py
#
#   初回は seen_urls.json が無いので、マッチした全件が「新着」扱いで
#   メールされます。テスト時は MAX_PAGES=1 / SEARCH_QUERIES を1件に
#   絞ることを推奨。
#
#
# 4) 定期実行
# ---------------------------------------------------------------
#   [Linux/macOS cron] crontab -e で以下のような行を追加:
#
#     # 30分おきに実行（標準出力/エラーをログに）
#     */30 * * * * /usr/bin/python3 /path/to/video_notifier.py >> /path/to/video_notifier.log 2>&1
#
#     # 平日9〜22時の毎時0分に実行
#     0 9-22 * * 1-5 /usr/bin/python3 /path/to/video_notifier.py >> /path/to/video_notifier.log 2>&1
#
#   [Windows タスクスケジューラ]
#     1. タスクスケジューラ → 「基本タスクの作成」
#     2. トリガー: 毎日 / 30分ごとに繰り返す
#     3. 操作:
#          プログラム: python.exe (フルパス例: C:\Python312\python.exe)
#          引数:       "C:\Users\moriy\Documents\claude code\動画サイト検索\video_notifier.py"
#          開始場所:   C:\Users\moriy\Documents\claude code\動画サイト検索
#
#   [PowerShell ワンライナーで登録する例]
#     $action  = New-ScheduledTaskAction `
#         -Execute "python.exe" `
#         -Argument '"C:\Users\moriy\Documents\claude code\動画サイト検索\video_notifier.py"'
#     $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
#         -RepetitionInterval (New-TimeSpan -Minutes 30)
#     Register-ScheduledTask -TaskName "VideoNotifier" `
#         -Action $action -Trigger $trigger -Description "動画サイト新着通知"
