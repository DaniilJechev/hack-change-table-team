import { contextBridge, ipcRenderer } from "electron";
contextBridge.exposeInMainWorld("avitocode", {
  call: (operation: string, data?: unknown) =>
    ipcRenderer.invoke("client", operation, data),
  subscribe: (callback: (state: unknown) => void) => {
    const handler = (_: unknown, state: unknown) => callback(state);
    ipcRenderer.on("view-state", handler);
    return () => ipcRenderer.removeListener("view-state", handler);
  },
  settings: (callback: () => void) => {
    ipcRenderer.on("open-settings", callback);
    return () => ipcRenderer.removeListener("open-settings", callback);
  },
});
