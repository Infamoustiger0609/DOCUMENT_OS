"""DOCX -> PDF via LibreOffice headless. Chosen over docx2pdf because docx2pdf
shells out to MS Word via COM automation and only works on Windows with Word
installed — a non-starter on Render's Linux container. LibreOffice's
`--headless --convert-to pdf` works cross-platform and needs no Word license.
See CLAUDE.md's Document tools section."""

import subprocess
import tempfile
from pathlib import Path

from config import LIBREOFFICE_CMD


def docx_to_pdf(data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "input.docx"
        input_path.write_bytes(data)

        # -env:UserInstallation isolates each conversion in its own LibreOffice
        # profile directory. Without it, concurrent `soffice --headless` calls
        # sharing the default profile can collide on its lock file and one
        # invocation fails outright — a real risk here since this endpoint is
        # only rate-limited, not serialized.
        profile_uri = (tmp_path / "lo_profile").as_uri()

        result = subprocess.run(
            [
                LIBREOFFICE_CMD,
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_path),
                str(input_path),
            ],
            capture_output=True,
            timeout=120,
        )

        output_path = tmp_path / "input.pdf"
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"LibreOffice conversion failed (exit {result.returncode}): "
                f"{result.stderr.decode(errors='replace')[:500]}"
            )
        return output_path.read_bytes()
