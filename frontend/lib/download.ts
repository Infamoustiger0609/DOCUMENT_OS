// Triggers a real browser file download (Save-to-disk), as opposed to
// opening a signed URL in a new tab (what "View original PDF" does) — a
// signed URL alone has no notion of the document's real filename, so a
// browser opening it directly falls back to guessing a name from the URL's
// own path, which is an opaque internal Storage key, not anything
// human-readable. Fetching the bytes ourselves and saving them via an
// explicit `download` attribute sidesteps that entirely.

type AuthFetch = (path: string, init?: RequestInit) => Promise<Response>;

export async function downloadSignedFile(
  authFetch: AuthFetch,
  downloadUrlPath: string,
  filename: string
): Promise<void> {
  const urlRes = await authFetch(downloadUrlPath);
  if (!urlRes.ok) {
    const data = await urlRes.json().catch(() => null);
    throw new Error(data?.detail ?? "Could not get a download link for this file.");
  }
  const { url }: { url: string } = await urlRes.json();

  const fileRes = await fetch(url);
  if (!fileRes.ok) {
    throw new Error("Could not download the file.");
  }
  const blob = await fileRes.blob();

  const objectUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
