"use client";

import { AlertTriangle } from "lucide-react";
import {
  type ChangeEvent,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { type AuthUser, useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

type AuthFetch = (path: string, init?: RequestInit) => Promise<Response>;

const THRESHOLD_OPTIONS = [7, 14, 30, 60] as const;

// "Account" (identity/credentials) vs. "App" (app-wide preferences) — the same
// split GitHub/Linear use for their own settings. See CLAUDE.md's Settings
// section for which endpoint backs which tab.
type SettingsTab = "account" | "app";

function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-4 px-6 py-5">
      <div>
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-ink-soft">{description}</p>}
      </div>
      {children}
    </Card>
  );
}

function ProfileSection({
  user,
  authFetch,
  refreshUser,
}: {
  user: AuthUser | null;
  authFetch: AuthFetch;
  refreshUser: () => Promise<void>;
}) {
  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSuccess(false);
    setSubmitting(true);
    try {
      const res = await authFetch("/auth/profile", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not update your profile.");
      }
      await refreshUser();
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update your profile.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SettingsSection title="Profile" description="Your name and email address.">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="profile-name">Name</Label>
            <Input
              id="profile-name"
              autoComplete="name"
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="profile-email">Email</Label>
            <Input
              id="profile-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
        </div>

        {error && <p className="text-sm text-overdue">{error}</p>}
        {success && <p className="text-sm text-filed">Profile updated.</p>}

        <Button type="submit" disabled={submitting} className="w-fit">
          {submitting ? "Saving..." : "Save changes"}
        </Button>
      </form>
    </SettingsSection>
  );
}

// Phase 31 — see CLAUDE.md's E-signature section. Draw (canvas) or upload a
// signature image once; POST /auth/signature saves/replaces it. Drawing on a
// transparent canvas and exporting with canvas.toBlob("image/png") naturally
// produces a transparent-background PNG with no extra effort — nothing is
// ever painted for the background, only the strokes themselves.
type SignatureMode = "draw" | "upload";

function SignatureSection({ authFetch }: { authFetch: AuthFetch }) {
  const [mode, setMode] = useState<SignatureMode>("draw");
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const hasDrawnRef = useRef(false);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadPreviewUrl, setUploadPreviewUrl] = useState<string | null>(null);

  const [savedUrl, setSavedUrl] = useState<string | null>(null);
  const [loadingSaved, setLoadingSaved] = useState(true);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const loadSaved = useCallback(async () => {
    setLoadingSaved(true);
    try {
      const res = await authFetch("/auth/signature");
      if (res.ok) {
        const data: { has_signature: boolean; download_url: string | null } = await res.json();
        setSavedUrl(data.has_signature ? data.download_url : null);
      }
    } catch {
      // Non-fatal — the save/upload form below still works even if this
      // background preview fetch fails.
    } finally {
      setLoadingSaved(false);
    }
  }, [authFetch]);

  useEffect(() => {
    loadSaved();
  }, [loadSaved]);

  const getPos = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    canvas.setPointerCapture(event.pointerId);
    drawingRef.current = true;
    const { x, y } = getPos(event);
    ctx.beginPath();
    ctx.moveTo(x, y);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return;
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = getPos(event);
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#1B1F3B";
    ctx.lineTo(x, y);
    ctx.stroke();
    hasDrawnRef.current = true;
  };

  const handlePointerUp = () => {
    drawingRef.current = false;
  };

  const clearCanvas = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    hasDrawnRef.current = false;
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setUploadFile(file);
    if (uploadPreviewUrl) URL.revokeObjectURL(uploadPreviewUrl);
    setUploadPreviewUrl(file ? URL.createObjectURL(file) : null);
  };

  const handleSave = async () => {
    setError(null);
    setSuccess(false);

    let fileToSend: Blob | null = null;
    let filename = "signature.png";
    if (mode === "draw") {
      if (!hasDrawnRef.current || !canvasRef.current) {
        setError("Draw your signature first.");
        return;
      }
      fileToSend = await new Promise<Blob | null>((resolve) =>
        canvasRef.current!.toBlob((blob) => resolve(blob), "image/png")
      );
    } else {
      if (!uploadFile) {
        setError("Choose an image file first.");
        return;
      }
      fileToSend = uploadFile;
      filename = uploadFile.name;
    }
    if (!fileToSend) {
      setError("Could not prepare the signature image. Please try again.");
      return;
    }

    setSaving(true);
    try {
      const formData = new FormData();
      formData.append("file", fileToSend, filename);
      const res = await authFetch("/auth/signature", { method: "POST", body: formData });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not save your signature.");
      }
      const data: { download_url: string | null } = await res.json();
      setSavedUrl(data.download_url);
      setSuccess(true);
      clearCanvas();
      setUploadFile(null);
      if (uploadPreviewUrl) URL.revokeObjectURL(uploadPreviewUrl);
      setUploadPreviewUrl(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save your signature.");
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    setRemoving(true);
    setError(null);
    try {
      const res = await authFetch("/auth/signature", { method: "DELETE" });
      if (!res.ok) throw new Error("Could not remove your signature.");
      setSavedUrl(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove your signature.");
    } finally {
      setRemoving(false);
    }
  };

  return (
    <SettingsSection
      title="Signature"
      description="Draw or upload your signature once, then apply it to documents you sign."
    >
      <div className="flex flex-col gap-4">
        {!loadingSaved && savedUrl && (
          <div className="flex items-center gap-4 rounded-md border border-line bg-paper px-4 py-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={savedUrl}
              alt="Your saved signature"
              className="h-16 w-auto max-w-[200px] object-contain"
            />
            <div className="flex flex-col gap-1">
              <span className="text-xs text-ink-soft">Currently saved signature</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleRemove}
                disabled={removing}
                className="w-fit"
              >
                {removing ? "Removing..." : "Remove signature"}
              </Button>
            </div>
          </div>
        )}

        <div className="flex w-fit gap-1 rounded-md border border-line bg-paper p-1">
          {(["draw", "upload"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "rounded px-3 py-1.5 text-sm capitalize transition-colors",
                mode === m ? "bg-ink text-paper" : "text-ink-soft hover:bg-sidebar-bg"
              )}
            >
              {m}
            </button>
          ))}
        </div>

        {mode === "draw" ? (
          <div className="flex flex-col gap-2">
            <canvas
              ref={canvasRef}
              width={400}
              height={160}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerLeave={handlePointerUp}
              className="w-full max-w-[400px] touch-none rounded-md border border-dashed border-line bg-paper-raised"
            />
            <Button type="button" variant="outline" size="sm" onClick={clearCanvas} className="w-fit">
              Clear
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <input
              type="file"
              accept="image/png,image/jpeg"
              onChange={handleFileChange}
              className="text-sm text-ink-soft"
            />
            {uploadPreviewUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={uploadPreviewUrl}
                alt="Signature preview"
                className="h-16 w-auto max-w-[200px] rounded-md border border-line bg-paper-raised object-contain p-2"
              />
            )}
          </div>
        )}

        {error && <p className="text-sm text-overdue">{error}</p>}
        {success && <p className="text-sm text-filed">Signature saved.</p>}

        <Button type="button" onClick={handleSave} disabled={saving} className="w-fit">
          {saving ? "Saving..." : "Save signature"}
        </Button>
      </div>
    </SettingsSection>
  );
}

function ChangePasswordSection({ authFetch }: { authFetch: AuthFetch }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSuccess(false);

    if (newPassword !== confirmPassword) {
      setError("New passwords don't match.");
      return;
    }

    setSubmitting(true);
    try {
      const res = await authFetch("/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not change password.");
      }
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change password.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SettingsSection title="Change password">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="current-password">Current password</Label>
          <Input
            id="current-password"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            className="max-w-sm"
          />
        </div>

        <div className="grid max-w-sm grid-cols-1 gap-4 sm:max-w-none sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-password">New password</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="confirm-password">Confirm new password</Label>
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          </div>
        </div>

        {error && <p className="text-sm text-overdue">{error}</p>}
        {success && <p className="text-sm text-filed">Password updated.</p>}

        <Button type="submit" disabled={submitting} className="w-fit">
          {submitting ? "Updating..." : "Update password"}
        </Button>
      </form>
    </SettingsSection>
  );
}

function PreferencesSection({
  user,
  authFetch,
  refreshUser,
}: {
  user: AuthUser | null;
  authFetch: AuthFetch;
  refreshUser: () => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSelect = async (days: number) => {
    if (saving || user?.due_soon_threshold_days === days) return;
    setSaving(true);
    setError(null);
    try {
      const res = await authFetch("/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ due_soon_threshold_days: days }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not update your preference.");
      }
      await refreshUser();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update your preference.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      title="Due soon threshold"
      description={
        'Documents due within this window are flagged "due soon" on the Documents and Tasks pages.'
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex w-fit gap-1 rounded-md border border-line bg-paper p-1">
          {THRESHOLD_OPTIONS.map((days) => (
            <button
              key={days}
              type="button"
              onClick={() => handleSelect(days)}
              disabled={saving}
              className={cn(
                "rounded px-3 py-1.5 text-sm transition-colors disabled:opacity-50",
                user?.due_soon_threshold_days === days
                  ? "bg-ink text-paper"
                  : "text-ink-soft hover:bg-sidebar-bg"
              )}
            >
              {days} days
            </button>
          ))}
        </div>
        {error && <p className="text-sm text-overdue">{error}</p>}
      </div>
    </SettingsSection>
  );
}

function DangerZoneSection({
  user,
  authFetch,
  logout,
}: {
  user: AuthUser | null;
  authFetch: AuthFetch;
  logout: () => void;
}) {
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canDelete = user !== null && confirmText.trim().toLowerCase() === user.email.toLowerCase();

  const handleDelete = async () => {
    if (!canDelete || deleting) return;
    setDeleting(true);
    setError(null);
    try {
      const res = await authFetch("/auth/me", { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not delete your account.");
      }
      logout();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete your account.");
      setDeleting(false);
    }
  };

  return (
    <Card className="flex flex-col gap-4 border-overdue/30 bg-overdue/5 px-6 py-5">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-overdue" strokeWidth={1.75} />
        <div>
          <h2 className="text-sm font-semibold text-overdue">Danger zone</h2>
          <p className="mt-0.5 text-xs text-ink-soft">
            Deleting your account is permanent and cannot be undone.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="delete-confirm">
          Type <span className="font-semibold text-ink">{user?.email}</span> to confirm
        </Label>
        <Input
          id="delete-confirm"
          type="text"
          autoComplete="off"
          value={confirmText}
          onChange={(event) => setConfirmText(event.target.value)}
          className="max-w-sm"
        />
      </div>

      {error && <p className="text-sm text-overdue">{error}</p>}

      <Button
        type="button"
        variant="outline"
        onClick={handleDelete}
        disabled={!canDelete || deleting}
        className="w-fit border-overdue text-overdue hover:bg-overdue/10"
      >
        {deleting ? "Deleting account..." : "Delete account"}
      </Button>
    </Card>
  );
}

const TABS: { key: SettingsTab; label: string }[] = [
  { key: "account", label: "Account" },
  { key: "app", label: "App" },
];

export default function SettingsPage() {
  const { user, authFetch, refreshUser, logout } = useAuth();
  const [tab, setTab] = useState<SettingsTab>("account");

  return (
    <div className="flex flex-col gap-6 px-10 py-10">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-serif text-2xl font-semibold text-ink">Settings</h1>
        <p className="text-sm text-ink-soft">Manage your account and preferences.</p>
      </div>

      <div className="flex w-fit gap-1 rounded-md border border-line bg-paper p-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={cn(
              "rounded px-4 py-1.5 text-sm font-medium transition-colors",
              tab === t.key ? "bg-ink text-paper" : "text-ink-soft hover:bg-sidebar-bg"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex max-w-2xl flex-col gap-6">
        {tab === "account" ? (
          <>
            <ProfileSection user={user} authFetch={authFetch} refreshUser={refreshUser} />
            <SignatureSection authFetch={authFetch} />
            <ChangePasswordSection authFetch={authFetch} />
            <DangerZoneSection user={user} authFetch={authFetch} logout={logout} />
          </>
        ) : (
          <PreferencesSection user={user} authFetch={authFetch} refreshUser={refreshUser} />
        )}
      </div>
    </div>
  );
}
