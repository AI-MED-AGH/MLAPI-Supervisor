import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { z } from "zod";
import { ApiError, onUnauthorized, request } from "./api/client";

type Status = "checking" | "in" | "out";
type AuthValue = { status: Status; login: (key: string) => Promise<void>; logout: () => Promise<void> };

const AuthContext = createContext<AuthValue | null>(null);
const me = z.object({ authenticated: z.boolean() });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const qc = useQueryClient();

  const expire = useCallback(() => {
    setStatus("out");
    qc.clear();
  }, [qc]);

  useEffect(() => {
    onUnauthorized(expire);
    request("GET", "/v1/auth/me", me, { skipUnauthorizedHandler: true })
      .then(() => setStatus("in"))
      .catch(() => setStatus("out"));
    return () => onUnauthorized(null);
  }, [expire]);

  const login = useCallback(async (key: string) => {
    await request("POST", "/v1/auth/login", null, { body: { key }, skipUnauthorizedHandler: true });
    setStatus("in");
  }, []);

  const logout = useCallback(async () => {
    try {
      await request("POST", "/v1/auth/logout", null, { skipUnauthorizedHandler: true });
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
    }
    expire();
  }, [expire]);

  return <AuthContext.Provider value={{ status, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
