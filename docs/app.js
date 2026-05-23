/* ============================================
   動画検索 PWA フロントエンドスクリプト
   GitHub REST API の workflow_dispatch を叩いて
   既存の Actions ワークフローをアドホック実行する。
   PAT は localStorage に保存（このページを開けるのは本人のみの前提）。
   ============================================ */

const LS_KEYS = {
  owner: 'vn_owner',
  repo: 'vn_repo',
  pat: 'vn_pat',
  workflow: 'vn_workflow',
  history: 'vn_history',
};

const HISTORY_LIMIT = 12;

// ===== 要素参照 =====
const form = document.getElementById('searchForm');
const queryInput = document.getElementById('query');
const submitBtn = document.getElementById('submitBtn');
const statusEl = document.getElementById('status');
const historySection = document.getElementById('historySection');
const historyList = document.getElementById('historyList');
const clearHistoryBtn = document.getElementById('clearHistoryBtn');

const settingsBtn = document.getElementById('settingsBtn');
const settingsDialog = document.getElementById('settingsDialog');
const settingsForm = document.getElementById('settingsForm');
const ownerInput = document.getElementById('ownerInput');
const repoInput = document.getElementById('repoInput');
const patInput = document.getElementById('patInput');
const workflowInput = document.getElementById('workflowInput');
const cancelSettingsBtn = document.getElementById('cancelSettingsBtn');

// ===== 設定 =====
function loadSettings() {
  return {
    owner: localStorage.getItem(LS_KEYS.owner) || '',
    repo: localStorage.getItem(LS_KEYS.repo) || '',
    pat: localStorage.getItem(LS_KEYS.pat) || '',
    workflow: localStorage.getItem(LS_KEYS.workflow) || 'run.yml',
  };
}

function saveSettings(s) {
  localStorage.setItem(LS_KEYS.owner, s.owner);
  localStorage.setItem(LS_KEYS.repo, s.repo);
  localStorage.setItem(LS_KEYS.pat, s.pat);
  localStorage.setItem(LS_KEYS.workflow, s.workflow);
}

function openSettings() {
  const s = loadSettings();
  ownerInput.value = s.owner;
  repoInput.value = s.repo;
  patInput.value = s.pat;
  workflowInput.value = s.workflow || 'run.yml';
  settingsDialog.showModal();
}

settingsBtn.addEventListener('click', openSettings);

cancelSettingsBtn.addEventListener('click', () => {
  settingsDialog.close();
});

settingsForm.addEventListener('submit', (e) => {
  e.preventDefault();
  saveSettings({
    owner: ownerInput.value.trim(),
    repo: repoInput.value.trim(),
    pat: patInput.value.trim(),
    workflow: workflowInput.value.trim() || 'run.yml',
  });
  settingsDialog.close();
  showStatus('success', '設定を保存しました');
});

// ===== 履歴 =====
function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEYS.history) || '[]');
  } catch {
    return [];
  }
}

function saveHistory(list) {
  localStorage.setItem(LS_KEYS.history, JSON.stringify(list));
}

function pushHistory(query) {
  let list = loadHistory();
  list = list.filter((q) => q !== query);
  list.unshift(query);
  if (list.length > HISTORY_LIMIT) list = list.slice(0, HISTORY_LIMIT);
  saveHistory(list);
  renderHistory();
}

function deleteHistoryItem(query) {
  const list = loadHistory().filter((q) => q !== query);
  saveHistory(list);
  renderHistory();
}

function renderHistory() {
  const list = loadHistory();
  historyList.innerHTML = '';
  if (list.length === 0) {
    historySection.hidden = true;
    return;
  }
  historySection.hidden = false;
  for (const q of list) {
    const li = document.createElement('li');

    const useBtn = document.createElement('button');
    useBtn.type = 'button';
    useBtn.className = 'history-item';
    useBtn.textContent = q;
    useBtn.addEventListener('click', () => {
      queryInput.value = q;
      queryInput.focus();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'history-delete';
    delBtn.setAttribute('aria-label', '削除');
    delBtn.textContent = '×';
    delBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteHistoryItem(q);
    });

    li.appendChild(useBtn);
    li.appendChild(delBtn);
    historyList.appendChild(li);
  }
}

clearHistoryBtn.addEventListener('click', () => {
  if (confirm('履歴をすべて削除しますか？')) {
    saveHistory([]);
    renderHistory();
  }
});

// ===== ステータス表示 =====
function showStatus(type, mainText, subText) {
  statusEl.hidden = false;
  statusEl.className = `status ${type}`;
  statusEl.innerHTML = '';
  const main = document.createElement('div');
  main.textContent = mainText;
  statusEl.appendChild(main);
  if (subText) {
    const small = document.createElement('div');
    small.className = 'small';
    small.textContent = subText;
    statusEl.appendChild(small);
  }
}

function hideStatus() {
  statusEl.hidden = true;
}

// ===== GitHub API 呼び出し =====
async function dispatchWorkflow(query) {
  const s = loadSettings();
  if (!s.owner || !s.repo || !s.pat) {
    throw new Error('GitHubユーザー名・リポジトリ名・Personal Access Token を設定画面で登録してください。');
  }
  const url = `https://api.github.com/repos/${encodeURIComponent(s.owner)}/${encodeURIComponent(s.repo)}/actions/workflows/${encodeURIComponent(s.workflow)}/dispatches`;
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Accept': 'application/vnd.github+json',
      'Authorization': `Bearer ${s.pat}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      ref: 'main',
      inputs: { query },
    }),
  });

  if (res.status === 204) return;

  // エラーハンドリング
  let detail = '';
  try {
    const body = await res.json();
    detail = body.message || JSON.stringify(body);
  } catch {
    detail = await res.text();
  }
  if (res.status === 401 || res.status === 403) {
    throw new Error(`認証エラー (${res.status}): PATが正しいか・有効期限切れでないか確認してください。\n${detail}`);
  }
  if (res.status === 404) {
    throw new Error(`リポジトリ／ワークフローが見つかりません (404)。\nowner=${s.owner} repo=${s.repo} workflow=${s.workflow}\n${detail}`);
  }
  if (res.status === 422) {
    throw new Error(`入力エラー (422): ${detail}`);
  }
  throw new Error(`API エラー (${res.status}): ${detail}`);
}

// ===== フォーム送信 =====
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;

  submitBtn.disabled = true;
  showStatus('loading', '送信中…');

  try {
    await dispatchWorkflow(query);
    pushHistory(query);
    showStatus(
      'success',
      '✓ 検索リクエストを送信しました',
      'GitHub Actions が起動中です。2〜3分後にGmailで結果が届きます。'
    );
  } catch (err) {
    showStatus('error', '✕ 送信に失敗しました', err.message);
  } finally {
    submitBtn.disabled = false;
  }
});

// ===== 初期化 =====
renderHistory();

// 初回起動：設定が無ければ設定ダイアログを開く
{
  const s = loadSettings();
  if (!s.owner || !s.repo || !s.pat) {
    setTimeout(openSettings, 200);
  }
}

// PWA: Service Worker 登録（オフライン表示・ホーム画面アイコン化に必要）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('service-worker.js').catch((err) => {
      console.warn('SW register failed:', err);
    });
  });
}
