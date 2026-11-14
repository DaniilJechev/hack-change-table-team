import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  Bell,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  ExternalLink,
  FileCheck2,
  LogOut,
  Minus,
  Paperclip,
  Settings,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import "./style.css";
declare global {
  interface Window {
    avitocode: {
      call: (operation: string, data?: any) => Promise<any>;
      subscribe: (callback: (state: any) => void) => () => void;
      settings: (callback: () => void) => () => void;
    };
  }
}
const client = window.avitocode;
function App() {
  const [view, setView] = useState<any>({
    settings: { server: "", autostart: false, notifications: true },
    messages: [],
    requests: [],
  });
  const [settings, setSettings] = useState(false),
    [server, setServer] = useState(""),
    [autostart, setAutostart] = useState(false),
    [notifications, setNotifications] = useState(true);
  const [showTelegramHistory, setShowTelegramHistory] = useState(true),
    [mirrorDesktopToTelegram, setMirrorDesktopToTelegram] = useState(true);
  const [draft, setDraft] = useState(
      () => sessionStorage.getItem("draft") || "",
    ),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [device, setDevice] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [demo, setDemo] = useState(false);
  const bottom = useRef<HTMLDivElement>(null),
    lastRead = useRef(""),
    lastMessage = useRef(""),
    lastTarget = useRef(""),
    viewed = useRef(new Set<string>());
  const run = async (fn: () => Promise<any>) => {
    setError("");
    try {
      return await fn();
    } catch (e) {
      if (String((e as Error).message).includes("Подключение изменено")) return;
      setError(
        String((e as Error).message).replace(
          /^Error invoking remote method '[^']+': Error: /,
          "",
        ),
      );
    }
  };
  useEffect(() => {
    client.call("state").then(setView);
    const stop = client.subscribe(setView);
    const stopSettings = client.settings(() => setSettings(true));
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (settings) {
          setSettings(false);
        } else if (device) {
          setDevice(null);
        } else client.call("hide");
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      stop();
      stopSettings();
      document.removeEventListener("keydown", key);
    };
  }, [settings, device]);
  useEffect(() => {
    setServer(view.settings.server);
    setAutostart(view.settings.autostart);
    setNotifications(view.settings.notifications);
  }, [
    view.settings.server,
    view.settings.autostart,
    view.settings.notifications,
  ]);
  useEffect(() => {
    sessionStorage.setItem("draft", draft);
  }, [draft]);
  useEffect(() => {
    setShowTelegramHistory(view.preferences?.show_telegram_history ?? true);
    setMirrorDesktopToTelegram(
      view.preferences?.mirror_desktop_to_telegram ?? true,
    );
  }, [
    view.profile?.user_id,
    view.preferences?.show_telegram_history,
    view.preferences?.mirror_desktop_to_telegram,
  ]);
  useEffect(() => {
    if (!device) return;
    const timer = setInterval(() => {
      if (Date.now() > device.until) {
        setDevice(null);
        setError("Код истёк. Начните вход заново.");
        return;
      }
      run(async () => {
        const r = await client.call("poll", device.device_secret);
        if (!r.pending) setDevice(null);
      });
    }, 5000);
    return () => clearInterval(timer);
  }, [device]);
  useEffect(() => {
    if (view.settings.server && !view.profile)
      run(async () => setDemo((await client.call("info")).demo));
  }, [view.settings.server, view.profile]);
  useEffect(() => {
    const last = view.messages.at(-1);
    if (view.target && view.target !== lastTarget.current) {
      lastTarget.current = view.target;
      document
        .getElementById("message-" + view.target)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (last && last.id !== lastMessage.current)
      bottom.current?.scrollIntoView({ behavior: "smooth" });
    lastMessage.current = last?.id || "";
  }, [view.messages, view.target]);
  useEffect(() => {
    const root = document.querySelector(".messages");
    if (!root) return;
    const visible = () => {
      if (!document.hasFocus() || document.visibilityState !== "visible")
        return;
      const bounds = root.getBoundingClientRect();
      for (const element of root.querySelectorAll<HTMLElement>(
        ".message.assistant",
      )) {
        const rect = element.getBoundingClientRect();
        const id = element.id.slice("message-".length);
        if (
          rect.bottom > bounds.top &&
          rect.top < bounds.bottom &&
          !viewed.current.has(id)
        ) {
          viewed.current.add(id);
          void client.call("read", id).catch(() => viewed.current.delete(id));
        }
      }
    };
    const observer = new IntersectionObserver(visible, {
      root,
      threshold: 0.1,
    });
    root
      .querySelectorAll(".message.assistant")
      .forEach((element) => observer.observe(element));
    window.addEventListener("focus", visible);
    root.addEventListener("scroll", visible);
    visible();
    return () => {
      observer.disconnect();
      window.removeEventListener("focus", visible);
      root.removeEventListener("scroll", visible);
    };
  }, [view.messages]);
  useEffect(() => {
    viewed.current.clear();
    lastTarget.current = "";
    lastMessage.current = "";
  }, [view.profile?.user_id, view.settings.server]);
  useEffect(() => {
    void client.call("chat-visible", !!view.profile && !settings);
  }, [view.profile?.user_id, settings]);
  const send = async (text = draft) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    const r = await run(() =>
      client.call("send", { id: crypto.randomUUID(), text }),
    );
    if (r && text === draft) setDraft("");
    setBusy(false);
  };
  const pending = view.requests.some((r: any) => r.status === "running");
  return (
    <main>
      <header>
        <div className="brand">
          <span className="logo">
            <i />
            <i />
            <i />
            <i />
          </span>
          <div>
            <strong>AvitoCode</strong>
            <small>
              <span
                className={
                  "dot " + (view.connection === "Подключено" ? "online" : "")
                }
              />
              {view.profile ? view.connection : "Ваш ИИ-наставник"}
            </small>
          </div>
        </div>
        <div className="tools">
          <button title="Настройки" onClick={() => setSettings(true)}>
            <Settings size={17} />
          </button>
          <button title="Скрыть в трей" onClick={() => client.call("hide")}>
            <Minus size={18} />
          </button>
        </div>
      </header>
      {settings ? (
        <section className="settings">
          <div className="section-title">
            <h2>Настройки</h2>
            <button onClick={() => setSettings(false)}>
              <X size={18} />
            </button>
          </div>
          <label>
            Адрес сервера
            <input
              placeholder="https://your-endpoint.ngrok.app"
              value={server}
              onChange={(e) => setServer(e.target.value)}
            />
          </label>
          <p className="hint">
            Общий сервер AI-buddy. При смене адреса потребуется новый вход.
          </p>
          <label className="toggle">
            <input
              type="checkbox"
              checked={notifications}
              onChange={(e) => setNotifications(e.target.checked)}
            />
            <Bell size={16} />
            Уведомления об ответах
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={autostart}
              onChange={(e) => setAutostart(e.target.checked)}
            />
            Запускать с Windows в трей
          </label>
          {view.profile && (
            <>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={showTelegramHistory}
                  onChange={(e) => setShowTelegramHistory(e.target.checked)}
                />
                Показывать переписку из Telegram
              </label>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={mirrorDesktopToTelegram}
                  onChange={(e) => setMirrorDesktopToTelegram(e.target.checked)}
                />
                Отправлять ответы desktop в Telegram
              </label>
              <p className="hint">
                Сохраняются в вашем профиле на сервере. Брифы и фоновые
                уведомления приходят независимо от этих настроек.
              </p>
            </>
          )}
          <button
            className="primary"
            onClick={() =>
              run(async () => {
                await client.call("settings", {
                  server,
                  notifications,
                  autostart,
                  preferences: view.profile
                    ? {
                        show_telegram_history: showTelegramHistory,
                        mirror_desktop_to_telegram: mirrorDesktopToTelegram,
                      }
                    : undefined,
                });
                setSettings(false);
                setDevice(null);
                lastRead.current = "";
                setNotice("Настройки сохранены");
              })
            }
          >
            Сохранить
          </button>
          {view.profile && (
            <div className="profile">
              <span>
                {view.profile.display_name}
                <small>
                  {view.profile.team_id} · {view.profile.role}
                </small>
              </span>
              <button onClick={() => run(() => client.call("logout"))}>
                <LogOut size={17} />
                Выйти
              </button>
            </div>
          )}
          <p className="hint">
            Секреты интеграций и LLM хранятся на сервере. Закрытие окна
            оставляет AvitoCode в трее.
          </p>
        </section>
      ) : !view.profile ? (
        <section className="welcome">
          <div className="hero">
            <Sparkles size={28} />
          </div>
          <span className="eyebrow">МЕНЬШЕ РУТИНЫ. БОЛЬШЕ ЯСНОСТИ.</span>
          <h1>
            Рядом, когда
            <br />
            нужна помощь.
          </h1>
          <p>
            Изменения в задачах, доступы, команда и проверка презентации — в
            одном диалоге.
          </p>
          {!view.settings.server ? (
            <button className="primary" onClick={() => setSettings(true)}>
              Настроить подключение
            </button>
          ) : device ? (
            <div className="device">
              <span>В личном чате с ботом отправьте</span>
              <code>/desktop {device.user_code}</code>
              <small>
                Подтвердите вход кнопкой в Telegram. Код действует 5 минут.
              </small>
              <button onClick={() => setDevice(null)}>Отменить</button>
            </div>
          ) : (
            <>
              {!demo && (
                <button
                  className="primary"
                  onClick={() =>
                    run(async () => {
                      const d = await client.call("device");
                      setDevice({
                        ...d,
                        until: Date.now() + d.expires_in * 1000,
                      });
                    })
                  }
                >
                  Войти через Telegram <ExternalLink size={16} />
                </button>
              )}
              <button
                className="secondary"
                onClick={() =>
                  setNotice("Вход через SSO находится в разработке")
                }
              >
                Войти через SSO
              </button>
              {demo && (
                <button
                  className="primary"
                  onClick={() => run(() => client.call("demo"))}
                >
                  Попробовать синтетическое демо
                </button>
              )}
            </>
          )}
          <span className="privacy">
            <ShieldCheck size={14} />
            Один профиль с Telegram-ботом
          </span>
        </section>
      ) : (
        <>
          <nav>
            <button onClick={() => send("/brief")}>
              <CalendarDays size={14} />
              Бриф
            </button>
            <button onClick={() => send("/access")}>
              <ShieldCheck size={14} />
              Доступы
            </button>
            <button onClick={() => send("/ask")}>
              <BookOpen size={14} />
              Команда
            </button>
            <button
              onClick={() =>
                run(() => client.call("upload", crypto.randomUUID()))
              }
            >
              <FileCheck2 size={14} />
              Презентация
            </button>
          </nav>
          {view.profile.demo && (
            <div className="demo">
              Синтетическое демо{" "}
              <button onClick={() => send("/demo next")}>
                Следующий шаг →
              </button>
            </div>
          )}
          <section className="messages">
            <button
              className="history"
              onClick={() =>
                run(() =>
                  client.call(
                    "history",
                    Number.isFinite(view.historyBefore)
                      ? view.historyBefore
                      : 0,
                  ),
                )
              }
            >
              <ChevronDown size={13} />
              Предыдущие сообщения
            </button>
            {!view.messages.length && (
              <div className="empty">
                <Sparkles size={28} />
                <h2>С чего начнём?</h2>
                <p>
                  Спросите о команде, задаче или процессе. Я помогу найти ответ
                  и покажу источники.
                </p>
              </div>
            )}
            {view.messages.map((message: any) => {
              const p = message.payload,
                r = p.response;
              return (
                <article
                  id={"message-" + message.id}
                  key={message.id}
                  className={"message " + p.role}
                >
                  <div className="author">
                    {p.role === "user"
                      ? "Вы"
                      : p.channel === "background"
                        ? "Новое сообщение"
                        : "AvitoCode"}
                    <span>{p.channel === "telegram" ? "Telegram" : ""}</span>
                  </div>
                  <div className="bubble">
                    <Markdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        a: ({ href, children }) => (
                          <a
                            href={href}
                            onClick={(e) => {
                              e.preventDefault();
                              if (href)
                                run(() => client.call("external", href));
                            }}
                          >
                            {children}
                          </a>
                        ),
                        img: () => null,
                      }}
                    >
                      {r.result}
                    </Markdown>
                    {r.folded?.length > 0 && (
                      <details>
                        <summary>Подробнее</summary>
                        {r.folded.map((t: string, i: number) => (
                          <p key={i}>{t}</p>
                        ))}
                      </details>
                    )}
                    {r.links?.length > 0 && (
                      <details className="sources">
                        <summary>Источники · {r.links.length}</summary>
                        {r.links.map((link: string, i: number) => (
                          <button
                            key={link}
                            onClick={() =>
                              run(() => client.call("external", link))
                            }
                          >
                            [{i + 1}] {link}
                            <ExternalLink size={12} />
                          </button>
                        ))}
                      </details>
                    )}
                    {p.attachments?.map((a: any) => (
                      <button
                        className="attachment"
                        key={a.id}
                        onClick={() => run(() => client.call("download", a))}
                      >
                        <Paperclip size={14} />
                        {a.name}
                      </button>
                    ))}
                  </div>
                  {r.actions?.length > 0 && (
                    <div className="actions">
                      {r.actions.map((a: any, i: number) => (
                        <button
                          key={i}
                          onClick={() =>
                            a.url
                              ? run(() => client.call("external", a.url))
                              : run(() =>
                                  client.call("action", {
                                    id: crypto.randomUUID(),
                                    message_id: message.id,
                                    data: a.data,
                                  }),
                                )
                          }
                        >
                          {a.label}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
            {pending && (
              <div className="thinking">
                <span />
                <span />
                <span />
                Думаю над ответом
              </div>
            )}
            {view.requests
              .filter((r: any) => ["failed", "interrupted"].includes(r.status))
              .slice(-2)
              .map((r: any, i: number) => (
                <div key={i} className="request-error">
                  {r.error ||
                    "Операция прервана. Проверьте результат перед повтором."}
                </div>
              ))}
            <div ref={bottom} />
          </section>
          <footer>
            <div className="compose">
              <textarea
                placeholder="Спросите наставника…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <div>
                <button
                  title="Прикрепить файл"
                  onClick={() =>
                    run(() => client.call("upload", crypto.randomUUID()))
                  }
                >
                  <Paperclip size={18} />
                </button>
                <button
                  className="send"
                  title="Отправить"
                  disabled={!draft.trim() || busy}
                  onClick={() => send()}
                >
                  <ArrowUp size={18} />
                </button>
              </div>
            </div>
            <small>Enter — отправить · Shift + Enter — новая строка</small>
          </footer>
        </>
      )}
      {error && (
        <div role="alert" className="toast error">
          <span>{error}</span>
          <button onClick={() => setError("")}>
            <X size={15} />
          </button>
        </div>
      )}
      {notice && (
        <div role="status" className="toast">
          <Check size={16} />
          <span>{notice}</span>
          <button onClick={() => setNotice("")}>
            <X size={15} />
          </button>
        </div>
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
