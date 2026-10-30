export type Role = "viewer" | "customer" | "sales_rep" | "manager";
export const DEMO_TEXT = "We are building a commercial property in Bellevue. I need 500 bags of cement, 200 drywall sheets, and 100 steel studs delivered by Friday. Give me your best price, preferably with a 10% discount.";
export async function api<T = any>(path: string, role: Role, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch("/api" + path, { method: init.method ?? "GET",
    headers: { "Content-Type": "application/json", "X-Role": role, "X-User": role + "-demo" },
    body: init.body ? JSON.stringify(init.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data));
  return data as T;
}
export const money = (s?: string | null) => (s == null ? "—" : "$" + Number(s).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
