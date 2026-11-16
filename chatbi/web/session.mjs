// Same-origin API calls carry the selected, server-authorized data source.
const originalFetch = globalThis.fetch.bind(globalThis);
export const selectedSource = () => sessionStorage.getItem("chatbi-source") || "demo";
globalThis.fetch = (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url, location.origin);
  if (url.origin === location.origin && url.pathname.startsWith("/api/")) {
    const headers = new Headers(init.headers || (typeof input !== "string" ? input.headers : undefined));
    headers.set("X-Data-Source", selectedSource());
    init = { ...init, headers };
  }
  return originalFetch(input, init);
};

export async function requireSession() {
  const response = await fetch("/api/v1/auth/status");
  const status = await response.json();
  if (status.enabled && !status.user) {
    location.replace("/login");
    await new Promise(() => {});
  }
  const scope = `${status.user.user_id}:${selectedSource()}`;
  if (sessionStorage.getItem("chatbi-scope") !== scope) {
    for (const key of ["chatbi-conversation", "chatbi-parent-query", "chatbi-active-job"]) sessionStorage.removeItem(key);
    sessionStorage.setItem("chatbi-scope", scope);
  }
  const sources = await (await fetch("/api/v1/sources")).json();
  if (!sources.items?.some(item => item.id === selectedSource())) {
    sessionStorage.setItem("chatbi-source", sources.items?.[0]?.id || "demo");
  }
  const current = sources.items?.find(item => item.id === selectedSource());
  if (current && current.id !== "demo") {
    for (const node of document.querySelectorAll("[data-table-count]")) node.textContent = Object.keys(current.schema).length;
    for (const node of document.querySelectorAll("[data-field-count]")) node.textContent = Object.values(current.schema).reduce((sum, fields) => sum + fields.length, 0);
    for (const node of document.querySelectorAll("[data-demo-examples]")) node.hidden = true;
  }
  const sidebar = document.querySelector(".sidebar");
  if (sidebar) {
    const label = document.createElement("label");
    label.className = "source-switch";
    label.textContent = "当前数据源";
    const select = document.createElement("select");
    for (const source of sources.items || []) {
      const option = new Option(source.name, source.id);
      option.selected = source.id === selectedSource();
      select.add(option);
    }
    select.addEventListener("change", () => { sessionStorage.removeItem("chatbi-scope"); sessionStorage.setItem("chatbi-source", select.value); location.assign("/"); });
    label.append(select);
    sidebar.querySelector(".nav-list")?.after(label);
    const link = document.createElement("a");
    link.className = "nav-item";
    link.href = "/settings";
    link.textContent = "账号与数据源";
    sidebar.querySelector(".nav-list")?.append(link);
    const feedback = document.createElement("a");
    feedback.className = "nav-item"; feedback.href = "/settings#feedback"; feedback.textContent = "反馈案例库";
    sidebar.querySelector(".nav-list")?.append(feedback);
  }
  return status;
}
