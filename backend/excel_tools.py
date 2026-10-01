"""Excel (.xlsx) support: reading sheet metadata for the main upload pipeline
(see processing.py's "Data File" branch) and merging multiple workbooks for
POST /tools/merge-excel (see CLAUDE.md's Document tools section)."""

import io
import re
from typing import List, Literal, Optional

import openpyxl
import pandas as pd

# Characters Excel itself refuses in a sheet name, plus the 31-character limit
# — openpyxl raises InvalidSheetNameException if either is violated, so a
# filename like "Q1 Report: Draft/Final.xlsx" has to be sanitized before it
# can become a sheet name, not just truncated.
_INVALID_SHEET_NAME_CHARS = re.compile(r"[:\\/?*\[\]]")
MAX_SHEET_NAME_LENGTH = 31


def _sanitize_sheet_name(name: str, used: set) -> str:
    base = _INVALID_SHEET_NAME_CHARS.sub("_", name).strip() or "Sheet"
    base = base[:MAX_SHEET_NAME_LENGTH]
    candidate = base
    suffix = 1
    while candidate in used:
        suffix += 1
        marker = f"_{suffix}"
        candidate = f"{base[: MAX_SHEET_NAME_LENGTH - len(marker)]}{marker}"
    used.add(candidate)
    return candidate


def extract_excel_metadata(file_bytes: bytes) -> dict:
    """Reads sheet names, the header row (as column names), and row count
    (excluding the header) for every sheet in the workbook — this IS a "Data
    File" document's whole extracted_json (see processing.py); the real
    row-level data is queried on demand later (see data_query.py), never
    flattened into JSON here. Raises a plain ValueError on anything unreadable
    (corrupt file, not really an .xlsx despite the extension, etc.) — caught
    by processing.py the same way a PDF extraction failure is, landing on
    status="extraction_failed"."""
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        try:
            sheets = []
            for sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
                rows_iter = sheet.iter_rows(values_only=True)
                header_row = next(rows_iter, ())
                columns = [str(value) if value is not None else "" for value in header_row]
                row_count = sum(1 for _ in rows_iter)
                sheets.append({"name": sheet_name, "columns": columns, "row_count": row_count})
            return {"sheets": sheets}
        finally:
            workbook.close()
    except Exception as exc:
        raise ValueError(f"Could not read this Excel file: {exc}") from exc


def merge_excel(
    file_byte_list: List[bytes],
    mode: Literal["sheets", "concat"],
    filenames: Optional[List[str]] = None,
) -> bytes:
    """mode="sheets": each source file becomes its own sheet in a brand-new
    workbook, named after its own source filename (sanitized/truncated/
    deduplicated to Excel's rules — see _sanitize_sheet_name above). Only the
    active/first sheet of each source file is copied in — this tool merges
    *files* into sheets, not every sheet of every file.
    mode="concat": every source file's first/active sheet is read into a
    pandas DataFrame and outer-joined by column name (pandas.concat(axis=0,
    join="outer")) into a single combined sheet — a column present in one
    file but not another becomes blank for the rows that don't have it,
    rather than dropping the column or erroring.

    `filenames` is optional only so this function's own two required
    positional params (file_byte_list, mode) match this phase's spec exactly;
    the real POST /tools/merge-excel endpoint always has each upload's real
    filename in scope and passes it through. A caller that omits it falls
    back to a generic "File N" name per sheet in "sheets" mode (irrelevant in
    "concat" mode, which never names anything after a source file)."""
    if mode == "sheets":
        output = openpyxl.Workbook()
        output.remove(output.active)  # drop the default blank sheet
        used_names: set = set()
        for index, data in enumerate(file_byte_list):
            source = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
            try:
                source_sheet = source.active
                raw_name = (
                    filenames[index].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                    if filenames and index < len(filenames)
                    else f"File {index + 1}"
                )
                # Drop the .xlsx extension from the sheet name — "Q1_Report" reads
                # better than "Q1_Report.xlsx" as a tab label.
                raw_name = re.sub(r"\.xlsx$", "", raw_name, flags=re.IGNORECASE)
                new_sheet = output.create_sheet(title=_sanitize_sheet_name(raw_name, used_names))
                for row in source_sheet.iter_rows(values_only=True):
                    new_sheet.append(row)
            finally:
                source.close()

        buffer = io.BytesIO()
        output.save(buffer)
        return buffer.getvalue()

    # mode == "concat"
    frames = [pd.read_excel(io.BytesIO(data), sheet_name=0) for data in file_byte_list]
    combined = pd.concat(frames, axis=0, join="outer", ignore_index=True)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        combined.to_excel(writer, index=False, sheet_name="Combined")
    return buffer.getvalue()
