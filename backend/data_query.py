"""Read-only SQL querying over a user's own "Data File" (.xlsx) documents —
backs both the per-document chat path (chat.py, for a single Data File) and
the Copilot's query_data_files tool (copilot_chat.py, for one or more). See
CLAUDE.md's Data Files section for the full design.

The model never gets to run arbitrary SQL against the real database — this
module loads only the specific spreadsheet(s) the user already owns into a
throwaway, in-memory DuckDB connection (nothing here ever touches the real
Postgres data), and run_readonly_sql() below is the only way any SQL text
reaches that connection, gated on a SELECT-only keyword check."""

import io
import re
import uuid
from typing import Any, Dict, List

import duckdb
import pandas as pd
from sqlalchemy.orm import Session

from models import Document
from storage import download_file_from_storage

DEFAULT_ROW_LIMIT = 500

# Case-insensitive, word-boundary match — "SELECT ... created_at" must never
# trip on the word "created" just because "create" is a substring of it.
_FORBIDDEN_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "ATTACH", "COPY", "PRAGMA", "CREATE")
_FORBIDDEN_KEYWORD_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)

# DuckDB identifiers here only ever come from a filename/sheet name we
# control the shape of (never model- or request-supplied text used
# unsanitized), but sanitizing them into valid, unquoted-safe SQL
# identifiers up front means describe_tables()'s output never needs
# quoting either, keeping the schema text the model sees simple.
_INVALID_IDENTIFIER_CHARS = re.compile(r"[^0-9a-zA-Z_]")


def _sanitize_identifier(name: str) -> str:
    sanitized = _INVALID_IDENTIFIER_CHARS.sub("_", name).strip("_") or "table"
    if sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def _filename_stem(filename: str) -> str:
    """Strips any path-like prefix and the file extension — just the "data"
    part of "some/path/data.xlsx"."""
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


class DataQueryError(Exception):
    """Raised for anything the calling tool/endpoint should be able to hand
    back to the model (or the user) as a plain message, rather than a 500 —
    a document that doesn't exist/isn't owned/isn't a Data File, a rejected
    non-SELECT query, or a real DuckDB execution error."""


def load_document_tables(
    document_ids: List[uuid.UUID], user_id: uuid.UUID, db: Session
) -> duckdb.DuckDBPyConnection:
    """One fresh, in-memory DuckDB connection per call — never shared or
    reused across requests/users. Every document is re-verified here (not
    just wherever the id list was assembled) to belong to user_id and be a
    Data File, so this is never the path by which another user's data or a
    non-spreadsheet document's content could end up queryable."""
    con = duckdb.connect(database=":memory:")
    used_table_names: set = set()

    for document_id in document_ids:
        document = db.query(Document).filter(Document.id == document_id).first()
        if document is None or document.user_id != user_id:
            raise DataQueryError(f"Document {document_id} was not found in your registry.")
        if document.category != "Data File":
            raise DataQueryError(
                f"'{document.filename}' isn't a Data File (spreadsheet) document and can't be queried this way."
            )

        file_bytes = download_file_from_storage(document.storage_path)
        try:
            sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
        except Exception as exc:
            raise DataQueryError(f"Could not read '{document.filename}' as an Excel file: {exc}") from exc

        base_name = _sanitize_identifier(_filename_stem(document.filename))
        for sheet_name, frame in sheets.items():
            table_name = _sanitize_identifier(f"{base_name}_{sheet_name}")
            # register() silently REPLACES an existing view of the same name
            # (verified directly against the real duckdb API, not assumed) —
            # a real risk here since two different documents can easily
            # produce the same "<stem>_<sheet>" combination (e.g. two files
            # both literally named "data.xlsx"). Deduplicate rather than
            # silently losing one document's table under the other's name.
            final_name = table_name
            suffix = 2
            while final_name in used_table_names:
                final_name = f"{table_name}_{suffix}"
                suffix += 1
            used_table_names.add(final_name)
            con.register(final_name, frame)

    return con


def describe_tables(con: duckdb.DuckDBPyConnection) -> str:
    """Plain-text schema listing — one line per table, e.g.
    'invoices_Sheet1: name (VARCHAR), amount (DOUBLE)' — this is what the
    model sees before (and while) writing SQL against these tables."""
    tables = con.execute("SHOW TABLES").fetchall()
    if not tables:
        return "(no tables loaded)"

    lines = []
    for (table_name,) in tables:
        columns = con.execute(f'DESCRIBE "{table_name}"').fetchall()
        column_desc = ", ".join(f"{col[0]} ({col[1]})" for col in columns)
        lines.append(f"{table_name}: {column_desc}")
    return "\n".join(lines)


def run_readonly_sql(
    con: duckdb.DuckDBPyConnection, sql: str, row_limit: int = DEFAULT_ROW_LIMIT
) -> List[Dict[str, Any]]:
    """SELECT-only: rejects (raises DataQueryError) if the query contains any
    mutating/DDL/session keyword. A real DuckDB execution error (bad column
    name, syntax error, etc.) is deliberately let through as-is (wrapped in
    DataQueryError with the original message intact) rather than swallowed —
    the calling code hands that message back to the model so it can retry
    with a corrected query."""
    if _FORBIDDEN_KEYWORD_RE.search(sql):
        raise DataQueryError("Only SELECT queries are allowed.")

    statement = sql.strip().rstrip(";")
    if not _LIMIT_RE.search(statement):
        statement = f"{statement} LIMIT {row_limit}"

    try:
        frame = con.execute(statement).fetchdf()
    except Exception as exc:
        raise DataQueryError(str(exc)) from exc

    return frame.to_dict(orient="records")
