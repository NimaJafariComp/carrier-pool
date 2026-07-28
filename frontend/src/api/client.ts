import type { components } from "./generated";

export type Tenant = components["schemas"]["TenantResponse"];
export type Load = components["schemas"]["LoadResponse"];
export type Decision = components["schemas"]["DecisionResponse"];

export class ApiError extends Error {
  constructor(readonly status: number) {
    super(status === 422 ? "Insufficient decision evidence." : "Unable to load data.");
  }
}

async function request<T>(path: string, tenantId?: string): Promise<T> {
  const response = await fetch(path, {
    headers: tenantId ? { "X-Tenant-ID": tenantId } : undefined,
  });
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return (await response.json()) as T;
}

export function getTenants(): Promise<Tenant[]> {
  return request<Tenant[]>("/api/v1/tenants");
}

export function getActiveLoads(tenantId: string): Promise<Load[]> {
  return request<Load[]>("/api/v1/loads?status=ACTIVE", tenantId);
}

export function getDecision(tenantId: string, loadId: string): Promise<Decision> {
  return request<Decision>(`/api/v1/loads/${loadId}/decision`, tenantId);
}
