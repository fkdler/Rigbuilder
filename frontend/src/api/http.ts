import axios from "axios";

import { invalidateSession, readToken } from "./token";

const parsedTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS || 30_000);

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
  timeout: Number.isFinite(parsedTimeout) ? parsedTimeout : 30_000,
  headers: {
    "Content-Type": "application/json",
  },
});

/**
 * Every request carries the account token.
 *
 * Done here rather than at each call site so a new API module cannot forget it and
 * quietly read somebody else's data with a missing credential.
 */
http.interceptors.request.use((config) => {
  const token = readToken();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/**
 * A 401 means the server no longer accepts this token: expired, revoked by a
 * password change, or the account was disabled. Dropping it here is what closes the
 * session -- without it the page would keep retrying with a credential that can
 * never work, and every panel would show an unexplained failure.
 */
http.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error?.config?.url ?? "";
    // A 401 from the credential endpoints means "that username/password pair is
    // wrong", not "your session died" -- invalidating there would sign a user out
    // for mistyping a password in the account switcher.
    const isCredentialCheck = url.startsWith("/api/auth/login") || url.startsWith("/api/auth/register");
    const sentToken = error?.config?.headers?.Authorization;
    if (axios.isAxiosError(error) && error.response?.status === 401 && !isCredentialCheck
        && sentToken === `Bearer ${readToken()}`) {
      invalidateSession();
    }
    return Promise.reject(error);
  },
);
