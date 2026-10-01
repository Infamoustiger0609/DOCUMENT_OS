import json
import re
import time

from groq import Groq, RateLimitError
from sqlalchemy.orm import Session

from config import GROQ_API_KEY
from data_query import DataQueryError, describe_tables, load_document_tables, run_readonly_sql
from models import Document

MODEL = "openai/gpt-oss-120b"
MAX_CHARS = 12000
MAX_RETRIES = 3

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions about a specific document. Answer "
    "using only the document text provided below. If the answer isn't in the document, "
    "say so clearly instead of guessing. Be concise."
)

# --- "Data File" (.xlsx) path — real SQL over the spreadsheet's own rows,
# never a guess from the schema alone. See CLAUDE.md's Data Files section.

SQL_SYSTEM_PROMPT = (
    "You are a data analyst. You are given the schema of one or more DuckDB tables, "
    "derived from an uploaded spreadsheet, and a question about that data. Write a "
    "single read-only SQL SELECT query (DuckDB dialect) that answers the question. "
    "Return ONLY the raw SQL query — no explanation, no markdown code fences."
)

SQL_RETRY_SYSTEM_PROMPT = (
    "You are a data analyst. Your previous SQL query failed to execute. Given the same "
    "table schema, the original question, your previous query, and the error it raised, "
    "write a corrected read-only SQL SELECT query (DuckDB dialect). Return ONLY the raw "
    "SQL query — no explanation, no markdown code fences."
)

DATA_ANSWER_SYSTEM_PROMPT = (
    "You are a helpful assistant. You are given a question, the SQL query that was run "
    "against the user's spreadsheet data, and that query's real results. Answer the "
    "question in plain, concise language using ONLY these results — never invent or "
    "estimate a number that isn't in them. If the results are empty, say so plainly."
)


def _build_messages(document_text: str, question: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Document text:\n---\n{document_text}\n---\n\nQuestion: {question}",
        },
    ]


def _call_groq(messages: list[dict]) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0,
        max_tokens=600,
        reasoning_effort="low",
    )
    return response.choices[0].message.content or ""


def _call_groq_with_retry(messages: list[dict]) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _call_groq(messages)
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2**attempt)


def answer_question(raw_text: str, question: str) -> tuple[str, bool]:
    text = raw_text or ""
    truncated = len(text) > MAX_CHARS
    messages = _build_messages(text[:MAX_CHARS], question)
    answer = _call_groq_with_retry(messages)
    return answer.strip(), truncated


def _strip_sql_fences(text: str) -> str:
    """Models asked for "raw SQL, no code fences" sometimes wrap it in
    ```sql ... ``` anyway — strip that defensively rather than letting a
    fenced response fail as invalid SQL."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = re.sub(r"^sql\s*\n", "", text, flags=re.IGNORECASE | re.MULTILINE)
    return text.strip()


def _build_sql_messages(schema: str, question: str) -> list[dict]:
    return [
        {"role": "system", "content": SQL_SYSTEM_PROMPT},
        {"role": "user", "content": f"Tables:\n{schema}\n\nQuestion: {question}"},
    ]


def _build_sql_retry_messages(schema: str, question: str, prior_sql: str, error: str) -> list[dict]:
    return [
        {"role": "system", "content": SQL_RETRY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Tables:\n{schema}\n\nQuestion: {question}\n\n"
                f"Your previous query:\n{prior_sql}\n\nError it raised:\n{error}"
            ),
        },
    ]


def _build_data_answer_messages(question: str, sql: str, rows: list[dict]) -> list[dict]:
    return [
        {"role": "system", "content": DATA_ANSWER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\nSQL that was run:\n{sql}\n\n"
                f"Results ({len(rows)} row(s)):\n{json.dumps(rows, default=str)}"
            ),
        },
    ]


def answer_question_over_data(document: Document, question: str, db: Session) -> tuple[str, bool]:
    """The "Data File" (.xlsx) counterpart to answer_question() above — see
    CLAUDE.md's Data Files section. Loads just this one document's sheets
    into a throwaway DuckDB connection, has the model write a SQL query
    against the real schema, runs it, and has the model turn the real
    results into a plain-language answer. One self-correction retry if the
    first query fails to execute; a second failure propagates as a real
    error (same as any other Groq-call failure in this app — the caller
    turns it into a 502). `truncated` is always False here — there's no
    document-text truncation concept in this path."""
    con = load_document_tables([document.id], document.user_id, db)
    schema = describe_tables(con)

    sql = _strip_sql_fences(_call_groq_with_retry(_build_sql_messages(schema, question)))
    try:
        rows = run_readonly_sql(con, sql)
    except DataQueryError as exc:
        sql = _strip_sql_fences(
            _call_groq_with_retry(_build_sql_retry_messages(schema, question, sql, str(exc)))
        )
        rows = run_readonly_sql(con, sql)  # a second failure is allowed to propagate

    answer = _call_groq_with_retry(_build_data_answer_messages(question, sql, rows))
    return answer.strip(), False
