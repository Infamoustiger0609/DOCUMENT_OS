"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// localStorage (not an httpOnly cookie) — deliberate for this POC: an httpOnly
// cookie would need the backend to set it and the frontend to send credentials
// cross-origin (different ports locally, different domains on Render), which
// means CORS with allow_credentials + an explicit origin allowlist instead of
// today's allow_origins=["*"]. Storing the token and attaching it manually via
// an Authorization header avoids all of that. Trade-off: vulnerable to XSS
// reading localStorage, unlike an httpOnly cookie. Revisit if this ships past POC.
const TOKEN_STORAGE_KEY = "documentos_token";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: string;
  created_at: string;
  due_soon_threshold_days: number;
  // Phase 31 — see CLAUDE.md's E-signature section. Whether this user has
  // saved a signature yet; never the raw storage path itself.
  has_signature: boolean;
}

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  logout: () => void;
  authFetch: (path: string, init?: RequestInit) => Promise<Response>;
  // Re-fetches /auth/me and updates local `user` state — for after a Settings-page
  // change (e.g. due_soon_threshold_days) so every consumer of `user` stays in sync
  // without a full page reload.
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function readStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // ignore — e.g. private-browsing storage restrictions
  }
}

async function fetchCurrentUser(token: string): Promise<AuthUser> {
  const res = await fetch(`${API_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Invalid session");
  return res.json();
}

async function parseErrorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    return data?.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const stored = readStoredToken();
    if (!stored) {
      setLoading(false);
      return;
    }
    fetchCurrentUser(stored)
      .then((me) => {
        setToken(stored);
        setUser(me);
      })
      .catch(() => {
        writeStoredToken(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      throw new Error(await parseErrorDetail(res, "Login failed."));
    }
    const data: { access_token: string } = await res.json();
    const me = await fetchCurrentUser(data.access_token);
    writeStoredToken(data.access_token);
    setToken(data.access_token);
    setUser(me);
  }, []);

  const register = useCallback(async (email: string, password: string, name: string) => {
    const res = await fetch(`${API_URL}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name }),
    });
    if (!res.ok) {
      throw new Error(await parseErrorDetail(res, "Registration failed."));
    }
    const data: { access_token: string } = await res.json();
    const me = await fetchCurrentUser(data.access_token);
    writeStoredToken(data.access_token);
    setToken(data.access_token);
    setUser(me);
  }, []);

  const logout = useCallback(() => {
    writeStoredToken(null);
    setToken(null);
    setUser(null);
    router.push("/login");
  }, [router]);

  const authFetch = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const headers = new Headers(init.headers);
      if (token) headers.set("Authorization", `Bearer ${token}`);
      const res = await fetch(`${API_URL}${path}`, { ...init, headers });
      if (res.status === 401) {
        writeStoredToken(null);
        setToken(null);
        setUser(null);
        router.push("/login");
      }
      return res;
    },
    [token, router]
  );

  const refreshUser = useCallback(async () => {
    if (!token) return;
    try {
      setUser(await fetchCurrentUser(token));
    } catch {
      // ignore — keep the existing user state rather than surprise-logging-out
      // over a transient network hiccup; authFetch's 401 handler covers a truly
      // invalid session elsewhere.
    }
  }, [token]);

  return (
    <AuthContext.Provider
      value={{ user, token, loading, login, register, logout, authFetch, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
