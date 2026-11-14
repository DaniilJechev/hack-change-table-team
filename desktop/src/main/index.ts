import {
  app,
  BrowserWindow,
  Tray,
  Menu,
  nativeImage,
  ipcMain,
  safeStorage,
  Notification,
  screen,
  shell,
  dialog,
} from "electron";
import { readFile, writeFile, mkdir, rename } from "node:fs/promises";
import { join } from "node:path";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
if (process.argv.includes("--smoke"))
  app.setPath("userData", mkdtempSync(join(tmpdir(), "avitocode-smoke-")));
import { panelBounds, serverURL, shouldNotify, safeExternal } from "./policy";

const APP_ID = "team.aibuddy.avitocode";
type Settings = {
  server: string;
  autostart: boolean;
  notifications: boolean;
  width?: number;
  height?: number;
  encryptedToken?: string;
};
let settings: Settings = { server: "", autostart: false, notifications: true };
let window: BrowserWindow,
  tray: Tray,
  token = "",
  profile: any = null,
  connection = "Не подключено",
  unread = 0;
let chatVisible = false;
let controller: AbortController | undefined,
  quitting = false,
  generation = 0,
  cursor = 0,
  target = "";
const messages = new Map<string, any>(),
  requests = new Map<string, any>();
const notices = new Map<string, Notification>();
const config = () => join(app.getPath("userData"), "settings.json");
let writing = Promise.resolve();
function persist() {
  writing = writing
    .catch(() => {})
    .then(async () => {
      const path = config();
      await mkdir(app.getPath("userData"), { recursive: true });
      await writeFile(path + ".tmp", JSON.stringify(settings), { mode: 0o600 });
      await rename(path + ".tmp", path);
    });
  return writing;
}
function state() {
  return {
    settings: {
      server: settings.server,
      autostart: settings.autostart,
      notifications: settings.notifications,
    },
    profile,
    connection,
    unread,
    target,
    messages: [...messages.values()].sort((a, b) => a.seq - b.seq),
    requests: [...requests.values()],
  };
}
function publish() {
  if (window && !window.isDestroyed())
    window.webContents.send("view-state", state());
  tray?.setToolTip(`AvitoCode${unread ? ` · ${unread} новых` : ""}`);
}
function show(message = "") {
  target = message;
  const work = screen.getDisplayNearestPoint(
    screen.getCursorScreenPoint(),
  ).workArea;
  window.setMinimumSize(Math.min(360, work.width), Math.min(480, work.height));
  window.setBounds(
    panelBounds(
      screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea,
      settings,
    ),
  );
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  publish();
}
function hide() {
  window.hide();
}
async function api(
  path: string,
  method = "GET",
  body?: unknown,
  headers: Record<string, string> = {},
  signal?: AbortSignal,
) {
  if (!settings.server) throw new Error("Укажите адрес сервера в настройках");
  const requestGeneration = generation;
  const response = await fetch(settings.server + "/client/v1" + path, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "ngrok-skip-browser-warning": "1",
      ...(body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...headers,
    },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
    signal: signal || AbortSignal.timeout(30000),
    redirect: "error",
  });
  if (requestGeneration !== generation) throw new Error("Подключение изменено");
  if (response.status === 401) {
    token = "";
    profile = null;
    messages.clear();
    requests.clear();
    unread = 0;
    notices.forEach((notice) => notice.close());
    notices.clear();
    delete settings.encryptedToken;
    void persist();
    controller?.abort();
    publish();
    throw new Error("Сессия истекла. Войдите снова");
  }
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(
      error?.error?.message || `Ошибка сервера (${response.status})`,
    );
  }
  return response;
}
async function json(path: string, method = "GET", body?: unknown) {
  return (await api(path, method, body)).json();
}
async function acceptToken(value: string) {
  if (typeof value !== "string" || value.length !== 43)
    throw new Error("Некорректная сессия");
  token = value;
  settings.encryptedToken = (
    await safeStorage.encryptStringAsync(token)
  ).toString("base64");
  await persist();
  await reconnect();
}
async function connect() {
  const own = ++generation;
  controller?.abort();
  cursor = 0;
  messages.clear();
  requests.clear();
  unread = 0;
  if (!token || !settings.server) {
    publish();
    return;
  }
  profile = await json("/me");
  const history = await json("/chat/messages");
  if (own !== generation) return;
  history.items.forEach((m: any) => messages.set(m.id, m));
  const pending = await json("/chat/requests");
  pending.items.reverse().forEach((r: any) => requests.set(r.id, r.payload));
  connection = "Подключено";
  publish();
  void events(own);
}
async function reconnect() {
  try {
    await connect();
  } catch {
    connection = "Не удалось подключиться · повтор через 5 секунд";
    publish();
    const own = generation;
    setTimeout(() => {
      if (token && own === generation && !quitting) void reconnect();
    }, 5000);
  }
}
async function events(own: number) {
  let delay = 1000;
  while (!quitting && token && own === generation) {
    controller = new AbortController();
    let bootstrap = true;
    try {
      const response = await api(
        "/events?after=" + cursor,
        "GET",
        undefined,
        {},
        controller.signal,
      );
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      connection = "Подключено";
      publish();
      delay = 1000;
      while (own === generation) {
        const result = await reader.read();
        if (result.done) break;
        buffer += decoder.decode(result.value, { stream: true });
        let end: number;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          if (block.startsWith(":")) {
            bootstrap = false;
            continue;
          }
          const data = block
            .split("\n")
            .find((line) => line.startsWith("data: "));
          if (!data) continue;
          const event = JSON.parse(data.slice(6));
          if (event.seq <= cursor) continue;
          cursor = event.seq;
          if (own !== generation) return;
          const id = event.payload.id,
            record = event.payload.record;
          if (event.kind === "message") {
            const prior = messages.get(id);
            messages.set(id, {
              id,
              seq: prior?.seq || event.payload.seq || event.seq,
              payload: record,
              created_at: prior?.created_at || new Date().toISOString(),
            });
            if (
              !prior &&
              record.role !== "user" &&
              !(window.isVisible() && window.isFocused() && chatVisible)
            ) {
              unread++;
              if (
                settings.notifications &&
                shouldNotify(false, bootstrap, record.role)
              ) {
                const notice = new Notification({
                  title:
                    record.channel === "background"
                      ? "AvitoCode · новое сообщение"
                      : "AvitoCode · ответ",
                  body: String(record.response.result)
                    .replace(/[#*_`]/g, "")
                    .slice(0, 160),
                });
                notice.on("click", () => show(id));
                notice.on("close", () => notices.delete(id));
                notices.set(id, notice);
                notice.show();
              }
            }
          } else if (event.kind === "request") {
            requests.delete(id);
            requests.set(id, record);
          }
          publish();
        }
      }
    } catch {
      if (!token || own !== generation || quitting) return;
    }
    if (own !== generation) return;
    connection = "Нет связи · переподключение";
    publish();
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(delay * 2, 30000);
  }
}
function setupIPC() {
  ipcMain.handle("client", async (event, operation: string, data: any) => {
    if (
      event.sender !== window.webContents ||
      event.senderFrame !== window.webContents.mainFrame
    )
      throw new Error("Недопустимый источник");
    switch (operation) {
      case "chat-visible":
        chatVisible = !!data;
        return;
      case "state":
        return state();
      case "hide":
        hide();
        return;
      case "settings": {
        const server = serverURL(String(data.server), !app.isPackaged);
        if (server !== settings.server) {
          generation++;
          controller?.abort();
          token = "";
          profile = null;
          cursor = 0;
          messages.clear();
          requests.clear();
          delete settings.encryptedToken;
        }
        settings.server = server;
        settings.notifications = !!data.notifications;
        settings.autostart = !!data.autostart;
        if (process.platform === "win32")
          app.setLoginItemSettings({
            openAtLogin: settings.autostart,
            path: app.getPath("exe"),
            args: app.isPackaged ? [] : [app.getAppPath()],
          });
        await persist();
        publish();
        return;
      }
      case "info":
        return json("/info");
      case "device":
        return json("/auth/device", "POST", {});
      case "poll": {
        const result = await json("/auth/token", "POST", {
          device_secret: data,
        });
        if (result.token) await acceptToken(result.token);
        return { pending: result.pending || false };
      }
      case "demo": {
        const result = await json("/auth/demo", "POST", {});
        await acceptToken(result.token);
        return;
      }
      case "logout":
        try {
          await json("/session", "DELETE");
        } finally {
          generation++;
          controller?.abort();
          token = "";
          profile = null;
          messages.clear();
          requests.clear();
          delete settings.encryptedToken;
          await persist();
          publish();
        }
        return;
      case "send":
        return json("/chat/requests", "POST", { id: data.id, text: data.text });
      case "action":
        return json("/chat/actions", "POST", data);
      case "read":
        if (window.isVisible() && window.isFocused()) {
          await json("/chat/read", "PUT", { message_id: data });
          unread = 0;
          publish();
        }
        return;
      case "history": {
        const result = await json(
          "/chat/messages?before=" + encodeURIComponent(data),
        );
        result.items.forEach((m: any) => messages.set(m.id, m));
        publish();
        return result.items.length;
      }
      case "upload": {
        const file = await dialog.showOpenDialog(window, {
          properties: ["openFile"],
          filters: [
            {
              name: "Презентация или карточка команды",
              extensions: ["pdf", "pptx", "json", "yaml", "yml"],
            },
          ],
        });
        if (file.canceled) return;
        const { stat } = await import("node:fs/promises");
        if ((await stat(file.filePaths[0])).size > 10 * 1024 * 1024)
          throw new Error("Файл больше 10 МиБ");
        const { basename } = await import("node:path");
        const form = new FormData();
        form.append(
          "file",
          new Blob([await readFile(file.filePaths[0])]),
          basename(file.filePaths[0]),
        );
        return (
          await api("/chat/files", "POST", form, { "Idempotency-Key": data })
        ).json();
      }
      case "download": {
        if (!/^[a-f0-9]{32}$/.test(data.id))
          throw new Error("Некорректный файл");
        const save = await dialog.showSaveDialog(window, {
          defaultPath: String(data.name).replace(/[/\\]/g, "_"),
        });
        if (save.canceled || !save.filePath) return;
        const result = await api("/files/" + data.id);
        await writeFile(save.filePath, Buffer.from(await result.arrayBuffer()));
        return;
      }
      case "external":
        if (safeExternal(data)) return shell.openExternal(data);
        throw new Error("Ссылка недоступна");
      case "focus":
        unread = 0;
        target = "";
        publish();
        return;
      default:
        throw new Error("Неизвестная операция");
    }
  });
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => {
    if (window) show();
  });
  app.on("before-quit", () => {
    quitting = true;
    controller?.abort();
  });
  app.on("window-all-closed", () => {});
  app
    .whenReady()
    .then(async () => {
      app.setAppUserModelId(APP_ID);
      Menu.setApplicationMenu(null);
      try {
        settings = {
          ...settings,
          ...JSON.parse(await readFile(config(), "utf8")),
        };
        if (settings.server)
          settings.server = serverURL(settings.server, !app.isPackaged);
        if (settings.encryptedToken)
          token = (
            await safeStorage.decryptStringAsync(
              Buffer.from(settings.encryptedToken, "base64"),
            )
          ).result;
      } catch {
        settings = { server: "", autostart: false, notifications: true };
        token = "";
      }
      window = new BrowserWindow({
        ...panelBounds(screen.getPrimaryDisplay().workArea, settings),
        show: false,
        alwaysOnTop: true,
        resizable: true,
        minWidth: 360,
        minHeight: 480,
        backgroundColor: "#f5f6fa",
        title: "AvitoCode",
        webPreferences: {
          preload: join(__dirname, "../preload/index.js"),
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
        },
      });
      window.on("close", (event) => {
        if (!quitting) {
          event.preventDefault();
          hide();
        }
      });
      window.on("minimize", () => hide());
      window.on("resize", () => {
        const [width, height] = window.getSize();
        settings.width = width;
        settings.height = height;
        void persist();
      });
      window.webContents.on("will-navigate", (event) => event.preventDefault());
      window.webContents.setWindowOpenHandler(({ url }) => {
        if (safeExternal(url)) void shell.openExternal(url);
        return { action: "deny" };
      });
      screen.on("display-removed", () =>
        window.setBounds(
          panelBounds(screen.getPrimaryDisplay().workArea, settings),
        ),
      );
      screen.on("display-metrics-changed", () =>
        window.setBounds(
          panelBounds(
            screen.getDisplayMatching(window.getBounds()).workArea,
            settings,
          ),
        ),
      );
      const icon = nativeImage.createFromPath(
        app.isPackaged
          ? join(process.resourcesPath, "icon.ico")
          : join(__dirname, "../../resources/icon.png"),
      );
      tray = new Tray(icon);
      tray.on("click", () => (window.isVisible() ? hide() : show()));
      tray.setContextMenu(
        Menu.buildFromTemplate([
          { label: "Открыть чат", click: () => show() },
          {
            label: "Настроить подключение",
            click: () => {
              show();
              window.webContents.send("open-settings");
            },
          },
          { type: "separator" },
          { label: "Выйти", click: () => app.quit() },
        ]),
      );
      setupIPC();
      if (process.env.ELECTRON_RENDERER_URL && !app.isPackaged)
        await window.loadURL(process.env.ELECTRON_RENDERER_URL);
      else await window.loadFile(join(__dirname, "../renderer/index.html"));
      if (process.argv.includes("--smoke")) {
        if (window.isVisible() || !window.isAlwaysOnTop() || tray.isDestroyed())
          throw new Error("Invalid hidden tray lifecycle");
        process.stdout.write("smoke: hidden tray window ready\n");
        window.showInactive();
        if (
          !(await window.webContents.executeJavaScript(
            "typeof window.avitocode?.call === 'function' && typeof window.require === 'undefined'",
          ))
        )
          throw new Error("Preload isolation failed");
        process.stdout.write("smoke: isolated preload ready\n");
        const apiTest = process.argv.find((arg) =>
          arg.startsWith("--smoke-api="),
        );
        if (apiTest) {
          settings.server = serverURL(
            apiTest.slice("--smoke-api=".length),
            true,
          );
          process.stdout.write("smoke: connecting synthetic API\n");
          const info = await json("/info");
          if (!info.demo) throw new Error("Smoke requires synthetic demo");
          settings.notifications = false;
          token = (await json("/auth/demo", "POST", {})).token;
          await connect();
          process.stdout.write("smoke: synthetic session connected\n");
          await json("/chat/requests", "POST", {
            id: "smoke-brief",
            text: "/brief",
          });
          for (
            let attempt = 0;
            attempt < 40 && !messages.has("smoke-brief:answer");
            attempt++
          )
            await new Promise((resolve) => setTimeout(resolve, 250));
          if (!messages.has("smoke-brief:answer"))
            throw new Error("Desktop did not receive shared agent response");
          await new Promise((resolve) => setTimeout(resolve, 500));
          if (
            !(await window.webContents.executeJavaScript(
              "document.body.textContent.includes('Главное сегодня')",
            ))
          )
            throw new Error("React did not render brief");
        }
        window.showInactive();
        window.setSize(
          Math.min(
            window.getSize()[0] + 20,
            screen.getPrimaryDisplay().workArea.width,
          ),
          window.getSize()[1],
        );
        if (process.env.AVITOCODE_SMOKE_SCREENSHOT) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          await writeFile(
            process.env.AVITOCODE_SMOKE_SCREENSHOT,
            (await window.webContents.capturePage()).toPNG(),
          );
        }
        window.close();
        if (window.isDestroyed() || window.isVisible())
          throw new Error("Close must hide, not destroy");
        process.stdout.write("smoke: passed\n");
        app.quit();
        return;
      }
      if (token) void reconnect();
      else publish();
    })
    .catch(() => app.exit(1));
}
