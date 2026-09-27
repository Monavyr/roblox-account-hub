"use strict";

const panelToken = document.querySelector('meta[name="panel-token"]').content;

const elements = {
  cookieForm: document.getElementById("cookie-form"),
  cookieInput: document.getElementById("cookie-input"),
  fileInput: document.getElementById("file-input"),
  dropZone: document.getElementById("drop-zone"),
  scanButton: document.getElementById("scan-button"),
  clearButton: document.getElementById("clear-button"),
  refreshCatalogButton: document.getElementById("refresh-catalog-button"),
  accountsList: document.getElementById("accounts-list"),
  catalogStatus: document.getElementById("catalog-status"),
  accountsStatus: document.getElementById("accounts-status"),
  scanStatus: document.getElementById("scan-status"),
  issuesPanel: document.getElementById("issues-panel"),
  issuesToggle: document.getElementById("issues-toggle"),
  issuesCount: document.getElementById("issues-count"),
  issuesList: document.getElementById("issues-list"),
  toastRegion: document.getElementById("toast-region"),
  themeToggle: document.getElementById("theme-toggle"),
};

const viewState = {
  accounts: [],
  catalog: {
    target_count: 0,
    loaded_count: 0,
    error_count: 0,
    loading: false,
  },
  settings: {},
  issues: [],
  busy: false,
  presenceRefreshInFlight: false,
  detailRefreshIds: new Set(),
};

const THEME_STORAGE_KEY = "roblox-continue-panel-theme";
const numberFormatter = new Intl.NumberFormat("ru-RU");
const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

function applyTheme(theme, persist = false) {
  const normalized = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = normalized;
  const isDark = normalized === "dark";
  elements.themeToggle.querySelector(".theme-icon").textContent = isDark ? "☀" : "☾";
  elements.themeToggle.querySelector(".theme-label").textContent = isDark ? "Светлая" : "Тёмная";
  elements.themeToggle.setAttribute(
    "aria-label",
    isDark ? "Включить светлую тему" : "Включить тёмную тему",
  );
  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, normalized);
    } catch (error) {
      // В приватном режиме браузер может запрещать localStorage.
    }
  }
}

function initializeTheme() {
  let savedTheme = null;
  try {
    savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  } catch (error) {
    savedTheme = null;
  }
  applyTheme(savedTheme === "light" ? "light" : "dark");
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if ((options.method || "GET").toUpperCase() !== "GET") {
    headers.set("X-Panel-Token", panelToken);
  }

  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch (error) {
    throw new Error("Локальный сервер недоступен.");
  }

  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    if (!response.ok) {
      throw new Error(`Ошибка локального сервера: HTTP ${response.status}`);
    }
  }

  if (!response.ok) {
    throw new Error(payload.error || `Ошибка HTTP ${response.status}`);
  }
  return payload;
}

function setBusy(value, message = "") {
  viewState.busy = value;
  elements.scanButton.disabled = value;
  elements.clearButton.disabled = value;
  elements.refreshCatalogButton.disabled = value;
  elements.cookieInput.disabled = value;
  elements.fileInput.disabled = value;
  elements.cookieForm.querySelector("button").disabled = value;
  if (message) {
    elements.scanStatus.textContent = message;
  }
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast ${type === "error" ? "error" : ""}`;
  toast.textContent = message;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4600);
}

function initials(username) {
  const cleaned = String(username || "?").replace(/[^a-zA-Z0-9а-яА-ЯёЁ]/g, "");
  return (cleaned.slice(0, 2) || "?").toUpperCase();
}

function makeButton(label, className = "button button-secondary") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  return button;
}

function replaceAccount(updatedAccount) {
  const index = viewState.accounts.findIndex(
    (account) => account.account_id === updatedAccount.account_id,
  );
  if (index >= 0) {
    viewState.accounts[index] = updatedAccount;
  }
}

function findAccount(accountId) {
  return viewState.accounts.find((account) => account.account_id === accountId) || null;
}

function makeStatus(account) {
  const badge = document.createElement("span");
  badge.className = "status-badge";

  if (account.status === "scanning") {
    badge.classList.add("scanning");
    badge.textContent = "Проверяется";
  } else if (account.status === "error") {
    badge.classList.add("error");
    badge.textContent = "Ошибка";
  } else if (account.status === "done") {
    badge.classList.add("success");
    badge.textContent = `${account.match_count} совпад.`;
  } else {
    badge.textContent = "Готов";
  }
  return badge;
}

function makePresence(account) {
  const badge = document.createElement("span");
  badge.className = "presence-badge";

  if (account.presence === "in_game") {
    badge.classList.add("in-game");
    badge.textContent = "В режиме";
  } else if (account.presence === "online") {
    badge.classList.add("online");
    badge.textContent = "Онлайн";
  } else if (account.presence === "offline") {
    badge.classList.add("offline");
    badge.textContent = "Офлайн";
  } else {
    badge.classList.add("unknown");
    badge.textContent = "Статус неизвестен";
  }

  if (account.presence_last_location) {
    badge.title = account.presence_last_location;
  }
  return badge;
}

function formatRobux(value, status) {
  if (value === null || value === undefined) {
    return status === "loading" ? "…" : "—";
  }
  return numberFormatter.format(value);
}

function makeFinanceMetric(label, value, status, hint) {
  const metric = document.createElement("div");
  metric.className = "finance-metric";
  if (status === "loading") metric.classList.add("is-loading");

  const metricLabel = document.createElement("span");
  metricLabel.className = "finance-label";
  metricLabel.textContent = label;
  if (hint) metricLabel.title = hint;

  const metricValue = document.createElement("strong");
  metricValue.className = "finance-value";
  metricValue.textContent = formatRobux(value, status);

  const unit = document.createElement("span");
  unit.className = "finance-unit";
  unit.textContent = "R$";

  const valueRow = document.createElement("div");
  valueRow.className = "finance-value-row";
  valueRow.append(metricValue, unit);
  metric.append(metricLabel, valueRow);
  return metric;
}

function makeFinancePanel(account) {
  const wrapper = document.createElement("section");
  wrapper.className = "finance-panel";
  wrapper.setAttribute("aria-label", `Robux аккаунта ${account.username}`);

  const heading = document.createElement("div");
  heading.className = "mini-heading";
  const title = document.createElement("span");
  title.textContent = "Robux";
  const refresh = makeButton("↻ Обновить", "mini-action");
  refresh.dataset.action = "refresh-details";
  refresh.dataset.accountId = account.account_id;
  refresh.disabled = viewState.detailRefreshIds.has(account.account_id);
  heading.append(title, refresh);

  const grid = document.createElement("div");
  grid.className = "finance-grid";
  grid.append(
    makeFinanceMetric("Баланс", account.robux_balance, account.finance_status),
    makeFinanceMetric(
      "Pending",
      account.pending_robux,
      account.finance_status,
      "Неконвертированные / ожидающие зачисления Robux",
    ),
    makeFinanceMetric(
      "Lifetime donate",
      account.lifetime_spent,
      account.finance_status,
      "Сумма исходящих Robux за всю доступную историю покупок",
    ),
    makeFinanceMetric(
      "Year donate",
      account.year_spent,
      account.finance_status,
      "Исходящие Robux за последние 365 дней по сводке Roblox",
    ),
  );
  wrapper.append(heading, grid);

  if (account.finance_status === "error") {
    const error = document.createElement("p");
    error.className = "inline-error";
    error.textContent = account.finance_error || "Не удалось загрузить финансы.";
    wrapper.append(error);
  }
  return wrapper;
}

function makeGameCard(account, game) {
  const card = document.createElement("article");
  card.className = "game-card";

  const title = document.createElement("div");
  title.className = "game-title";
  title.textContent = game.name;

  const id = document.createElement("div");
  id.className = "game-id";
  id.textContent = `PlaceId ${game.place_id} · UniverseId ${game.universe_id}`;

  const actions = document.createElement("div");
  actions.className = "game-actions";

  const launch = makeButton("▶ Запустить", "button button-primary");
  launch.dataset.action = "launch";
  launch.dataset.accountId = account.account_id;
  launch.dataset.placeId = String(game.place_id);
  launch.setAttribute("aria-label", `Запустить ${game.name} от аккаунта ${account.username}`);

  const page = document.createElement("a");
  page.className = "page-link";
  page.href = game.url;
  page.target = "_blank";
  page.rel = "noopener noreferrer";
  page.textContent = "Страница ↗";

  actions.append(launch, page);
  card.append(title, id, actions);
  return card;
}

function makeContinuePanel(account) {
  const panel = document.createElement("section");
  panel.className = "tool-panel continue-panel";

  const heading = document.createElement("div");
  heading.className = "tool-heading";
  const titleWrap = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "tool-eyebrow";
  eyebrow.textContent = "CONTINUE";
  const title = document.createElement("h3");
  title.textContent = "Игры из config.json";
  titleWrap.append(eyebrow, title);
  heading.append(titleWrap, makeStatus(account));
  panel.append(heading);

  const content = document.createElement("div");
  content.className = "continue-content";
  if (account.status === "error") {
    const error = document.createElement("div");
    error.className = "account-error";
    error.textContent = account.error || "Проверка аккаунта завершилась ошибкой.";
    content.append(error);
  } else if (account.status === "scanning") {
    const loading = document.createElement("div");
    loading.className = "no-matches";
    loading.textContent = "Получаем раздел Continue…";
    content.append(loading);
  } else if (account.status === "done" && account.matches.length === 0) {
    const empty = document.createElement("div");
    empty.className = "no-matches";
    empty.textContent = "Совпадений нет";
    content.append(empty);
  } else if (account.matches.length > 0) {
    const grid = document.createElement("div");
    grid.className = "games-grid";
    for (const game of account.matches) {
      grid.append(makeGameCard(account, game));
    }
    content.append(grid);
  } else {
    const waiting = document.createElement("div");
    waiting.className = "no-matches";
    waiting.textContent = "Нажмите «Проверить все»";
    content.append(waiting);
  }
  panel.append(content);
  return panel;
}

function formatRequestDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : dateFormatter.format(date);
}

function makeFriendRequestRow(account, request) {
  const row = document.createElement("div");
  row.className = "social-row";

  const identity = document.createElement("div");
  identity.className = "social-identity";
  const avatar = document.createElement("span");
  avatar.className = "mini-avatar";
  avatar.textContent = initials(request.username);
  const names = document.createElement("div");
  const username = document.createElement("strong");
  username.textContent = `@${request.username}`;
  const secondary = document.createElement("span");
  const date = formatRequestDate(request.created_at);
  secondary.textContent = date
    ? `${request.display_name} · ${date}`
    : request.display_name;
  names.append(username, secondary);
  identity.append(avatar, names);

  const actions = document.createElement("div");
  actions.className = "social-actions";
  const accept = makeButton("Принять", "button button-success button-compact");
  accept.dataset.action = "accept-friend";
  accept.dataset.accountId = account.account_id;
  accept.dataset.userId = String(request.user_id);
  const decline = makeButton("×", "icon-action danger");
  decline.dataset.action = "decline-friend";
  decline.dataset.accountId = account.account_id;
  decline.dataset.userId = String(request.user_id);
  decline.setAttribute("aria-label", `Отклонить заявку от ${request.username}`);
  actions.append(accept, decline);
  row.append(identity, actions);
  return row;
}

function makeFriendsPanel(account) {
  const panel = document.createElement("section");
  panel.className = "tool-panel social-panel";

  const heading = document.createElement("div");
  heading.className = "tool-heading";
  const titleWrap = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "tool-eyebrow";
  eyebrow.textContent = "ДРУЗЬЯ";
  const title = document.createElement("h3");
  title.textContent = "Заявки и добавление";
  titleWrap.append(eyebrow, title);
  const count = document.createElement("span");
  count.className = "count-badge";
  count.textContent = String((account.friend_requests || []).length);
  heading.append(titleWrap, count);

  const form = document.createElement("form");
  form.className = "quick-form";
  form.dataset.formAction = "send-friend";
  form.dataset.accountId = account.account_id;
  const input = document.createElement("input");
  input.type = "text";
  input.name = "username";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.maxLength = 21;
  input.placeholder = "Username без @";
  input.setAttribute("aria-label", `Username для заявки от ${account.username}`);
  const submit = makeButton("Добавить", "button button-secondary button-compact");
  submit.type = "submit";
  form.append(input, submit);

  const listTitle = document.createElement("div");
  listTitle.className = "subheading";
  listTitle.textContent = "Последние входящие";

  const list = document.createElement("div");
  list.className = "social-list request-list";
  if (account.friend_requests_status === "loading") {
    const state = document.createElement("div");
    state.className = "panel-state";
    state.textContent = "Загружаем заявки…";
    list.append(state);
  } else if (account.friend_requests_status === "error") {
    const error = document.createElement("div");
    error.className = "panel-state error";
    error.textContent = account.friend_requests_error || "Не удалось загрузить заявки.";
    list.append(error);
  } else if (!account.friend_requests || account.friend_requests.length === 0) {
    const empty = document.createElement("div");
    empty.className = "panel-state";
    empty.textContent = "Новых входящих заявок нет";
    list.append(empty);
  } else {
    for (const request of account.friend_requests) {
      list.append(makeFriendRequestRow(account, request));
    }
  }

  panel.append(heading, form, listTitle, list);
  return panel;
}

function makeInGameFriendRow(account, friend) {
  const row = document.createElement("div");
  row.className = "social-row";

  const identity = document.createElement("div");
  identity.className = "social-identity";
  const dot = document.createElement("span");
  dot.className = "friend-game-dot";
  const names = document.createElement("div");
  const username = document.createElement("strong");
  username.textContent = `@${friend.username}`;
  const location = document.createElement("span");
  location.textContent = friend.last_location || "В игре";
  names.append(username, location);
  identity.append(dot, names);

  const join = makeButton("Войти", "button button-primary button-compact");
  join.dataset.action = "join-friend";
  join.dataset.accountId = account.account_id;
  join.dataset.userId = String(friend.user_id);
  join.setAttribute("aria-label", `Подключиться к ${friend.username} от ${account.username}`);
  row.append(identity, join);
  return row;
}

function makeLaunchPanel(account) {
  const panel = document.createElement("section");
  panel.className = "tool-panel launch-panel";

  const heading = document.createElement("div");
  heading.className = "tool-heading";
  const titleWrap = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "tool-eyebrow";
  eyebrow.textContent = "БЫСТРЫЙ ЗАПУСК";
  const title = document.createElement("h3");
  title.textContent = "VIP и друзья в игре";
  titleWrap.append(eyebrow, title);
  const inGameCount = document.createElement("span");
  inGameCount.className = "count-badge success-count";
  inGameCount.textContent = String((account.in_game_friends || []).length);
  heading.append(titleWrap, inGameCount);

  const vipForm = document.createElement("form");
  vipForm.className = "quick-form vip-form";
  vipForm.dataset.formAction = "join-vip";
  vipForm.dataset.accountId = account.account_id;
  const vipInput = document.createElement("input");
  vipInput.type = "url";
  vipInput.name = "link";
  vipInput.autocomplete = "off";
  vipInput.spellcheck = false;
  vipInput.placeholder = "roblox.com/share?code=...&type=Server";
  vipInput.setAttribute("aria-label", `VIP-ссылка для ${account.username}`);
  const vipSubmit = makeButton("Подключиться", "button button-secondary button-compact");
  vipSubmit.type = "submit";
  vipForm.append(vipInput, vipSubmit);

  const help = document.createElement("p");
  help.className = "panel-help";
  help.textContent = "Поддерживается новый формат /share?code=...&type=Server и старые ссылки privateServerLinkCode/accessCode.";

  const listTitle = document.createElement("div");
  listTitle.className = "subheading";
  listTitle.textContent = "Друзья сейчас в игре";

  const list = document.createElement("div");
  list.className = "social-list friend-list";
  if (account.friends_status === "loading") {
    const state = document.createElement("div");
    state.className = "panel-state";
    state.textContent = "Проверяем друзей…";
    list.append(state);
  } else if (account.friends_status === "error") {
    const error = document.createElement("div");
    error.className = "panel-state error";
    error.textContent = account.friends_error || "Не удалось загрузить друзей.";
    list.append(error);
  } else if (!account.in_game_friends || account.in_game_friends.length === 0) {
    const empty = document.createElement("div");
    empty.className = "panel-state";
    empty.textContent = "Друзья в игре не найдены";
    list.append(empty);
  } else {
    for (const friend of account.in_game_friends) {
      list.append(makeInGameFriendRow(account, friend));
    }
  }

  panel.append(heading, vipForm, help, listTitle, list);
  return panel;
}

function makeAccountRow(account) {
  const row = document.createElement("article");
  row.className = "account-row";

  const accountCell = document.createElement("div");
  accountCell.className = "account-cell";

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-account";
  remove.dataset.action = "remove";
  remove.dataset.accountId = account.account_id;
  remove.setAttribute("aria-label", `Удалить аккаунт ${account.username}`);
  remove.textContent = "×";

  const identity = document.createElement("div");
  identity.className = "account-identity";
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = initials(account.username);
  const names = document.createElement("div");
  names.className = "account-names";
  const usernameRow = document.createElement("div");
  usernameRow.className = "username-row";
  const username = document.createElement("div");
  username.className = "username";
  username.textContent = `@${account.username}`;
  usernameRow.append(username, makePresence(account));
  const display = document.createElement("div");
  display.className = "display-name";
  display.textContent = `${account.display_name} · ID ${account.user_id}`;
  names.append(usernameRow, display);
  identity.append(avatar, names);

  const accountActions = document.createElement("div");
  accountActions.className = "account-quick-actions";
  const copyCookie = makeButton("Скопировать cookie", "mini-action cookie-copy-action");
  copyCookie.type = "button";
  copyCookie.dataset.action = "copy-cookie";
  copyCookie.dataset.accountId = account.account_id;
  copyCookie.setAttribute("aria-label", `Скопировать cookie аккаунта ${account.username}`);
  copyCookie.title = "Cookie даёт доступ к аккаунту. Не передавайте её другим.";
  accountActions.append(copyCookie);

  accountCell.append(remove, identity, accountActions, makeFinancePanel(account));

  const content = document.createElement("div");
  content.className = "account-games";
  content.append(makeContinuePanel(account));
  const tools = document.createElement("div");
  tools.className = "account-tools-grid";
  tools.append(makeFriendsPanel(account), makeLaunchPanel(account));
  content.append(tools);

  row.append(accountCell, content);
  return row;
}

function renderAccounts() {
  elements.accountsList.replaceChildren();
  if (viewState.accounts.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const icon = document.createElement("div");
    icon.className = "empty-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "◎";
    const title = document.createElement("h3");
    title.textContent = "Здесь появятся аккаунты";
    const copy = document.createElement("p");
    copy.textContent = "Добавьте одну cookie или перетащите .txt с несколькими значениями.";
    empty.append(icon, title, copy);
    elements.accountsList.append(empty);
    return;
  }

  for (const account of viewState.accounts) {
    elements.accountsList.append(makeAccountRow(account));
  }
}

function renderWorkflow() {
  const catalog = viewState.catalog;
  if (catalog.loading) {
    elements.catalogStatus.textContent = "Загрузка…";
  } else if (catalog.error_count > 0) {
    elements.catalogStatus.textContent = `${catalog.loaded_count}/${catalog.target_count}, ошибок: ${catalog.error_count}`;
  } else {
    elements.catalogStatus.textContent = `${catalog.loaded_count}/${catalog.target_count} загружено`;
  }

  const count = viewState.accounts.length;
  elements.accountsStatus.textContent = count === 0 ? "Пока не добавлены" : `${count} добавлено`;

  if (!viewState.busy) {
    const scanned = viewState.accounts.filter((account) => account.status === "done").length;
    const failed = viewState.accounts.filter((account) => account.status === "error").length;
    if (scanned || failed) {
      elements.scanStatus.textContent = `Готово: ${scanned}, ошибок: ${failed}`;
    } else {
      elements.scanStatus.textContent = "Ожидает запуска";
    }
  }
}

function renderIssues() {
  const issues = viewState.issues;
  elements.issuesPanel.hidden = issues.length === 0;
  elements.issuesCount.textContent = String(issues.length);
  elements.issuesList.replaceChildren();
  for (const issue of issues) {
    const item = document.createElement("div");
    item.textContent = `Cookie #${issue.item}: ${issue.error}`;
    elements.issuesList.append(item);
  }
}

function render() {
  renderAccounts();
  renderWorkflow();
  renderIssues();
}

async function loadState() {
  const payload = await api("/api/state");
  viewState.accounts = payload.accounts || [];
  viewState.catalog = payload.catalog || viewState.catalog;
  viewState.settings = payload.settings || {};
  render();
}

async function refreshPresence({ silent = true } = {}) {
  if (viewState.presenceRefreshInFlight || viewState.accounts.length === 0) return;
  viewState.presenceRefreshInFlight = true;
  try {
    const payload = await api("/api/presence/refresh", { method: "POST" });
    viewState.accounts = payload.accounts || viewState.accounts;
    renderAccounts();
  } catch (error) {
    if (!silent) showToast(error.message, "error");
  } finally {
    viewState.presenceRefreshInFlight = false;
  }
}

async function refreshAccountDetails(accountId, { silent = false } = {}) {
  if (viewState.detailRefreshIds.has(accountId)) return;
  const account = findAccount(accountId);
  if (!account) return;
  viewState.detailRefreshIds.add(accountId);
  account.finance_status = "loading";
  account.friend_requests_status = "loading";
  account.friends_status = "loading";
  renderAccounts();
  try {
    const payload = await api(
      `/api/accounts/${encodeURIComponent(accountId)}/details/refresh`,
      { method: "POST" },
    );
    if (payload.account) replaceAccount(payload.account);
    if (!silent) showToast(`Данные @${payload.account?.username || account.username} обновлены.`);
  } catch (error) {
    if (!silent) showToast(error.message, "error");
    await loadState();
  } finally {
    viewState.detailRefreshIds.delete(accountId);
    renderAccounts();
  }
}

async function refreshMissingDetails() {
  const ids = viewState.accounts
    .filter(
      (account) =>
        account.finance_status === "idle" ||
        account.friend_requests_status === "idle" ||
        account.friends_status === "idle",
    )
    .map((account) => account.account_id);
  for (let index = 0; index < ids.length; index += 2) {
    await Promise.all(
      ids.slice(index, index + 2).map((accountId) =>
        refreshAccountDetails(accountId, { silent: true }),
      ),
    );
  }
}

async function importText(text) {
  setBusy(true, "Проверяем cookies…");
  try {
    const payload = await api("/api/accounts/import", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    viewState.accounts = payload.accounts || [];
    viewState.issues = payload.errors || [];
    const parts = [];
    if (payload.added) parts.push(`добавлено: ${payload.added}`);
    if (payload.updated) parts.push(`обновлено: ${payload.updated}`);
    if (payload.duplicates) parts.push(`дубликатов: ${payload.duplicates}`);
    if (payload.skipped_limit) parts.push(`сверх лимита: ${payload.skipped_limit}`);
    showToast(parts.length ? parts.join(" · ") : "Новых аккаунтов нет.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
    render();
  }
  await refreshMissingDetails();
}

async function readFiles(files) {
  if (viewState.busy) return;
  const list = Array.from(files || []);
  if (list.length === 0) return;
  const totalSize = list.reduce((sum, file) => sum + file.size, 0);
  if (totalSize > 2 * 1024 * 1024) {
    showToast("Общий размер файлов превышает 2 МБ.", "error");
    return;
  }
  try {
    const texts = await Promise.all(list.map((file) => file.text()));
    await importText(texts.join("\n"));
  } finally {
    elements.fileInput.value = "";
  }
}

async function openLaunch(payload, successMessage) {
  if (payload.open_url) {
    showToast(successMessage);
    window.location.assign(payload.open_url);
  } else {
    showToast(successMessage);
  }
}

async function withButtonBusy(button, label, action) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    await action();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

elements.cookieForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = elements.cookieInput.value;
  elements.cookieInput.value = "";
  if (!text.trim()) {
    showToast("Вставьте значение .ROBLOSECURITY.", "error");
    return;
  }
  await importText(text);
});

elements.fileInput.addEventListener("change", () => readFiles(elements.fileInput.files));

for (const eventName of ["dragenter", "dragover"]) {
  elements.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropZone.classList.add("is-dragging");
  });
}

for (const eventName of ["dragleave", "drop"]) {
  elements.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropZone.classList.remove("is-dragging");
  });
}

elements.dropZone.addEventListener("drop", (event) => readFiles(event.dataTransfer.files));
elements.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    elements.fileInput.click();
  }
});

elements.scanButton.addEventListener("click", async () => {
  setBusy(true, "Проверяем Continue…");
  for (const account of viewState.accounts) {
    account.status = "scanning";
    account.error = null;
  }
  render();
  try {
    const payload = await api("/api/scan", { method: "POST" });
    viewState.accounts = payload.accounts || [];
    showToast("Проверка завершена.");
  } catch (error) {
    showToast(error.message, "error");
    await loadState();
  } finally {
    setBusy(false);
    render();
  }
});

elements.refreshCatalogButton.addEventListener("click", async () => {
  setBusy(true, "Обновляем английские названия…");
  try {
    const payload = await api("/api/catalog/refresh", { method: "POST" });
    viewState.catalog = payload.catalog || viewState.catalog;
    showToast("Список названий обновлён.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
    render();
  }
});

elements.clearButton.addEventListener("click", async () => {
  if (viewState.accounts.length === 0) return;
  if (!window.confirm("Удалить все cookies и результаты из памяти?")) return;
  setBusy(true, "Очищаем…");
  try {
    await api("/api/accounts/clear", { method: "POST" });
    viewState.accounts = [];
    viewState.issues = [];
    viewState.detailRefreshIds.clear();
    showToast("Аккаунты удалены из памяти.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
    render();
  }
});

elements.accountsList.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-form-action]");
  if (!form) return;
  event.preventDefault();
  const accountId = form.dataset.accountId;
  const submit = form.querySelector('button[type="submit"]');
  if (!accountId || !submit) return;

  if (form.dataset.formAction === "send-friend") {
    const input = form.elements.username;
    const username = input.value.trim();
    if (!username) {
      showToast("Введите username пользователя.", "error");
      input.focus();
      return;
    }
    await withButtonBusy(submit, "Отправляем…", async () => {
      try {
        const payload = await api(
          `/api/accounts/${encodeURIComponent(accountId)}/friends/request`,
          {
            method: "POST",
            body: JSON.stringify({ username }),
          },
        );
        input.value = "";
        showToast(`Заявка отправлена @${payload.target.username}.`);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    return;
  }

  if (form.dataset.formAction === "join-vip") {
    const input = form.elements.link;
    const link = input.value.trim();
    if (!link) {
      showToast("Вставьте ссылку VIP-сервера.", "error");
      input.focus();
      return;
    }
    await withButtonBusy(submit, "Получаем ticket…", async () => {
      try {
        const payload = await api(
          `/api/accounts/${encodeURIComponent(accountId)}/join-vip`,
          {
            method: "POST",
            body: JSON.stringify({ link }),
          },
        );
        await openLaunch(payload, `Открываем VIP-сервер от @${payload.username}…`);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }
});

async function copyTextToClipboard(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) throw new Error("Браузер не разрешил скопировать cookie.");
}

elements.accountsList.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || viewState.busy) return;
  const accountId = button.dataset.accountId;

  if (button.dataset.action === "remove") {
    button.disabled = true;
    try {
      await api(`/api/accounts/${encodeURIComponent(accountId)}`, { method: "DELETE" });
      viewState.accounts = viewState.accounts.filter(
        (account) => account.account_id !== accountId,
      );
      viewState.detailRefreshIds.delete(accountId);
      showToast("Аккаунт удалён из памяти.");
      render();
    } catch (error) {
      button.disabled = false;
      showToast(error.message, "error");
    }
    return;
  }

  if (button.dataset.action === "refresh-details") {
    await refreshAccountDetails(accountId);
    return;
  }

  if (button.dataset.action === "copy-cookie") {
    const account = findAccount(accountId);
    if (!account) return;
    const confirmed = window.confirm(
      `Cookie @${account.username} даёт доступ к аккаунту. Скопировать её в буфер обмена?`,
    );
    if (!confirmed) return;
    await withButtonBusy(button, "Копируем…", async () => {
      try {
        const payload = await api(
          `/api/accounts/${encodeURIComponent(accountId)}/cookie`,
          { method: "POST" },
        );
        await copyTextToClipboard(payload.cookie);
        showToast(`Cookie @${payload.username} скопирована. Не передавайте её другим.`);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    return;
  }

  if (button.dataset.action === "launch") {
    await withButtonBusy(button, "Получаем ticket…", async () => {
      try {
        const payload = await api("/api/launch", {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId,
            place_id: Number(button.dataset.placeId),
          }),
        });
        await openLaunch(payload, `Открываем Roblox от @${payload.username}…`);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    return;
  }

  if (button.dataset.action === "accept-friend" || button.dataset.action === "decline-friend") {
    const accept = button.dataset.action === "accept-friend";
    await withButtonBusy(button, accept ? "Принимаем…" : "…", async () => {
      try {
        const payload = await api(
          `/api/accounts/${encodeURIComponent(accountId)}/friend-requests/${encodeURIComponent(button.dataset.userId)}/${accept ? "accept" : "decline"}`,
          { method: "POST" },
        );
        if (payload.account) replaceAccount(payload.account);
        showToast(accept ? "Заявка принята." : "Заявка отклонена.");
        renderAccounts();
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    return;
  }

  if (button.dataset.action === "join-friend") {
    await withButtonBusy(button, "Ticket…", async () => {
      try {
        const payload = await api(
          `/api/accounts/${encodeURIComponent(accountId)}/join-friend`,
          {
            method: "POST",
            body: JSON.stringify({ user_id: Number(button.dataset.userId) }),
          },
        );
        await openLaunch(
          payload,
          `Подключаем @${payload.username} к @${payload.friend_username}…`,
        );
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }
});

elements.issuesToggle.addEventListener("click", () => {
  const expanded = elements.issuesToggle.getAttribute("aria-expanded") === "true";
  elements.issuesToggle.setAttribute("aria-expanded", String(!expanded));
  elements.issuesList.hidden = expanded;
});

elements.themeToggle.addEventListener("click", () => {
  const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(nextTheme, true);
});

initializeTheme();
loadState()
  .then(async () => {
    await refreshPresence();
    await refreshMissingDetails();
  })
  .catch((error) => {
    showToast(error.message, "error");
  });

window.setInterval(() => {
  if (!document.hidden && !viewState.busy) {
    refreshPresence();
  }
}, 30_000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !viewState.busy) {
    refreshPresence();
  }
});
