"use client";

import { File } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth-context";

export default function LoginPage() {
  const router = useRouter();
  const { user, loading, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!loading && user) {
      router.replace("/");
    }
  }, [loading, user, router]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen">
      <div className="flex w-full items-center justify-center px-10 lg:w-[560px] lg:shrink-0">
        <div className="w-full max-w-[360px]">
          <div className="mb-10 flex items-center gap-2">
            <File className="h-5 w-5 text-ink" strokeWidth={1.75} />
            <span className="font-serif text-[18px] font-medium text-ink">DocumentOS</span>
          </div>

          <h1 className="font-serif text-2xl font-semibold text-ink">Welcome back</h1>
          <p className="mt-1 text-sm text-ink-soft">Sign in to your document registry</p>

          <form className="mt-8 flex flex-col gap-5" onSubmit={handleSubmit}>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="email">Work email</Label>
              <Input
                id="email"
                type="email"
                placeholder="you@company.com"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">Password</Label>
                <a href="#" className="text-xs text-ink-soft hover:text-ink">
                  Forgot password?
                </a>
              </div>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>

            {error && <p className="text-sm text-overdue">{error}</p>}

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Logging in..." : "Log in"}
            </Button>
          </form>

          <div className="mt-8 border-t border-line pt-4">
            <p className="text-xs text-ink-soft">
              New to DocumentOS?{" "}
              <Link href="/register" className="text-ink underline hover:no-underline">
                Create an account
              </Link>
              .
            </p>
          </div>
        </div>
      </div>

      <div
        className="relative hidden flex-1 overflow-hidden lg:block"
        style={{
          backgroundImage: "linear-gradient(135deg, #171B33 0%, #24493F 55%, #3F6659 100%)",
        }}
      >
        <div className="pointer-events-none absolute right-16 top-24 h-64 w-52 rotate-[-6deg] rounded-xl bg-white/[0.06]" />
        <div className="pointer-events-none absolute right-10 top-28 h-64 w-52 rotate-[4deg] rounded-xl bg-white/[0.08]" />
        <div className="pointer-events-none absolute right-14 top-32 flex h-64 w-52 flex-col gap-2.5 rounded-xl bg-white/[0.1] p-4">
          <div className="h-2 w-3/4 rounded-full bg-white/40" />
          <div className="h-2 w-full rounded-full bg-white/25" />
          <div className="h-2 w-5/6 rounded-full bg-white/20" />
          <div className="h-2 w-2/3 rounded-full bg-white/15" />
          <div className="mt-auto flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-due-soon" />
            <div className="h-2 w-1/3 rounded-full bg-white/20" />
          </div>
        </div>

        <div className="relative z-10 flex h-full max-w-lg flex-col justify-center gap-6 px-16 py-16">
          <span className="text-xs uppercase tracking-wide text-paper/60">
            For document-heavy teams
          </span>
          <h2 className="font-serif text-4xl font-semibold leading-tight text-paper/95">
            Never miss a renewal, a due date, or a filing again.
          </h2>
          <p className="text-sm leading-relaxed text-paper/70">
            DocumentOS reads every contract, invoice, and filing the moment it lands, and tells
            you exactly what needs attention before it becomes a problem.
          </p>
          <p className="text-xs text-paper/50">Built for finance, legal, and operations teams.</p>
        </div>
      </div>
    </div>
  );
}
