// JSON API wrapper. POST requests carry the custom header required by the server (T12).

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const init = { method, headers: {} };
  if (method !== "GET") {
    init.headers["X-FotoDuenner"] = "1";
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Keine Verbindung zum Programm. Bitte FotoDünner neu starten.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.detail || `Fehler ${res.status}`);
  return data;
}

export const api = {
  version: () => request("GET", "/api/version"),
  recent: () => request("GET", "/api/recent"),
  pickFolder: () => request("POST", "/api/pick-folder"),
  open: (path, recursive) => request("POST", "/api/album/open", { path, recursive }),
  analyze: (id, skipOffline = false) =>
    request("POST", `/api/album/${id}/analyze`, { skip_offline: skipOffline }),
  cancel: (id) => request("POST", `/api/album/${id}/cancel`),
  view: (id) => request("GET", `/api/album/${id}/view`),
  info: (id, photoId) => request("GET", `/api/album/${id}/photo/${photoId}/info`),
  events: (id) => new EventSource(`/api/album/${id}/events`),
  thumbUrl: (id, photo, size = "s") =>
    `/api/album/${id}/photo/${photo.id}/thumb?size=${size}&v=${photo.v ?? 0}`,
};
