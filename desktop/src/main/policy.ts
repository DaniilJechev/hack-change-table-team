export type Area = { x: number; y: number; width: number; height: number };
export function panelBounds(
  area: Area,
  saved?: { width?: number; height?: number },
): Area {
  const width = Math.min(
    area.width,
    Math.max(
      Math.min(360, area.width),
      Math.round(saved?.width || area.width / 3),
    ),
  );
  const height = Math.min(
    area.height,
    Math.max(
      Math.min(480, area.height),
      Math.round(saved?.height || area.height),
    ),
  );
  return { x: area.x + area.width - width, y: area.y, width, height };
}
export function serverURL(raw: string, development = false): string {
  const u = new URL(raw);
  if (u.username || u.password || u.search || u.hash || u.pathname !== "/")
    throw new Error("Введите корневой адрес сервера без пути и пароля");
  if (
    u.protocol !== "https:" &&
    !(
      development &&
      u.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname)
    )
  )
    throw new Error("Требуется HTTPS-адрес сервера");
  return u.origin;
}
export function shouldNotify(
  focused: boolean,
  bootstrap: boolean,
  role: string,
) {
  return !focused && !bootstrap && role !== "user";
}
export function safeExternal(raw: string): boolean {
  try {
    return ["https:", "http:"].includes(new URL(raw).protocol);
  } catch {
    return false;
  }
}
