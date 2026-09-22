# Magic-byte signatures for the 4 file types this app accepts. Confirms a file's
# actual content matches its declared extension instead of trusting the filename
# alone (trivially spoofed — e.g. renaming an arbitrary/malicious file to .pdf
# would otherwise sail through the extension allowlist in main.py).
MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".tif": (b"II*\x00", b"MM\x00*"),  # little- and big-endian TIFF byte order marks
    ".tiff": (b"II*\x00", b"MM\x00*"),
    # .docx is a ZIP archive under the hood — same signature as any ZIP file,
    # so this only rules out non-ZIP content, not "a ZIP that happens not to be
    # a real .docx". Used by POST /tools/docx-to-pdf.
    ".docx": (b"PK\x03\x04",),
}


def matches_declared_type(contents: bytes, extension: str) -> bool:
    signatures = MAGIC_SIGNATURES.get(extension)
    if not signatures:
        return False
    return any(contents.startswith(sig) for sig in signatures)
