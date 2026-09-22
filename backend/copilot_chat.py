"""The global Copilot's tool definitions, query logic, and agentic
tool-calling loop (Phase 25 — see CLAUDE.md's Copilot section). Distinct from
chat.py (which answers questions about one specific document's raw_text) and
editor_chat.py (which calls file-editing tools) — this one answers questions
about the user's whole document registry: `list_documents` and
`get_deadline_summary` use existing structured fields (category, status,
deadline_date); `search_document_text` is Postgres full-text keyword search
over raw_text, explicitly NOT semantic/meaning-based search — see its tool
description and the system prompt below, both of which say so, so the model
itself stays honest about that distinction in its answers.

Same agentic-loop shape as editor_chat.py's run_editor_chat(): call Groq -> if
it returns tool_calls, execute each against the real database and feed the
result back as a "tool" message -> call Groq again -> repeat until it responds
with plain text or MAX_TOOL_ROUNDS is hit. Kept as its own copy rather than a
shared helper — this is only the second occurrence of the pattern in this
codebase, and this project's own convention (see classification.py/chat.py/
structured_extraction.py's near-identical Groq retry blocks) favors a little
duplication over an abstraction with only two call sites.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List

from groq import Groq, RateLimitError
from sqlalchemy import func
from sqlalchemy.orm import Session

from classification import CATEGORIES
from config import GROQ_API_KEY
from models import Document, User

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
MAX_RETRIES = 3
MAX_TOOL_ROUNDS = 5
MAX_LIST_RESULTS = 20
MAX_SEARCH_RESULTS = 10
MAX_DEADLINE_LIST = 10
MAX_CITATIONS = 20

client = Groq(api_key=GROQ_API_KEY)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "List the user's own documents from the DocumentOS registry, optionally filtered "
                "by category, status, and/or a deadline date range. Call with NO arguments at all "
                "to get every document in the registry. Use this when the user wants to see the "
                "actual matching documents (e.g. 'show all active agreements', 'what purchase "
                "orders do I have', 'list my documents') — for a pure count/total question (e.g. "
                "'how many documents do I have'), use get_document_count instead: this tool caps "
                f"at {MAX_LIST_RESULTS} results and fetches full document details, so it would "
                "silently undercount a larger registry and does more work than a count needs. "
                "'Active' usually means status=processed (fully filed, nothing wrong with it) — "
                "use the status filter for that rather than guessing from category alone. For a "
                "question scoped to ONE category with a deadline condition (e.g. 'which invoices "
                "are overdue', 'what agreements expire this month'), prefer THIS tool with both "
                "category and deadline_before/deadline_after set, rather than "
                "get_deadline_summary — that keeps the result to just that category instead of "
                "mixing in other document types. Returns "
                f"up to {MAX_LIST_RESULTS} matching documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES, "description": "Filter to exactly one category."},
                    "status": {
                        "type": "string",
                        "description": (
                            "Filter by processing status — 'processed' (fully filed; the normal "
                            "meaning of 'active'/'current'), 'uploaded', 'extraction_failed', "
                            "'classification_failed', or 'structuring_failed'."
                        ),
                    },
                    "deadline_before": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD). Only documents with a deadline on or before this date.",
                    },
                    "deadline_after": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD). Only documents with a deadline on or after this date.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document_count",
            "description": (
                "Get just the NUMBER of documents in the user's registry — the cheap, direct way "
                "to answer any pure count/total question, e.g. 'how many documents/files do I "
                "have', 'how many total X', 'how many invoices do I have'. Call with NO arguments "
                "for the grand total across every category and status. Always prefer this over "
                "list_documents for a counting question: list_documents caps at "
                f"{MAX_LIST_RESULTS} results and fetches full document details, so it can "
                "silently undercount a larger registry and does unnecessary work when all that's "
                "needed is a number. A count/total question is always answerable this way — never "
                "say there's no tool for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES, "description": "Count only this category."},
                    "status": {"type": "string", "description": "Count only documents with this processing status."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deadline_summary",
            "description": (
                "Get the user's own deadlines grouped by urgency across ALL document types: "
                "overdue, due soon (within the user's own configured threshold), and the total "
                "count of documents with any deadline at all. Use this for a general question like "
                "'what's overdue' or 'what deadlines are coming up' with no single category named. "
                "If the question names one specific document type (e.g. 'which invoices are "
                "overdue'), use list_documents instead, with category set — it gives a cleaner, "
                "unmixed answer than filtering this tool's combined results yourself."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_document_text",
            "description": (
                "Search the extracted text of the user's own documents for a word or phrase, using "
                "Postgres full-text KEYWORD search with English stemming — this is NOT semantic/"
                "meaning-based search. It finds documents containing that literal term or a close "
                "grammatical variant (e.g. 'exclusive' also matches 'exclusivity'), not documents "
                "that are conceptually related without using similar wording. Use this for content "
                "questions like 'find contracts mentioning exclusivity'. Returns up to "
                f"{MAX_SEARCH_RESULTS} matches with a short highlighted snippet."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The word or phrase to search for."}},
                "required": ["query"],
            },
        },
    },
]

SYSTEM_PROMPT_TEMPLATE = """You are the DocumentOS Copilot — a global assistant answering \
questions about the user's own document registry (agreements, invoices, GST documents, bank \
statements, purchase orders) by calling the tools provided. You do not have access to any other \
user's documents, and you do not perform classification, extraction, or file editing yourself — \
those are separate parts of this app; if asked to do one, say so and point at the right page \
instead of attempting it.

Today's date is {today}. Use it to compute deadline_before/deadline_after arguments for "overdue" \
or "due by <date>"-style questions — e.g. "overdue" means deadline_before={today}.

Rules:
- Always call a tool to get real data before answering a factual question — never guess or invent \
a document's name, date, category, or contents.
- Any "how many"/"total number of"/count-style question is always answerable from the registry — \
call get_document_count (with a category/status filter if the question named one, or with no \
arguments at all for a grand total like "how many files/documents do I have"). Never respond that \
no tool exists for a counting question.
- `search_document_text` is keyword/full-text search, not true semantic search. If it returns few \
or no results for a conceptual question, say so plainly (e.g. "no documents contain that exact \
term") rather than implying a deeper, meaning-based search was done.
- Only cite documents a tool call actually returned — never a document you're inferring or \
remembering from earlier in the conversation without re-checking.
- If a question is ambiguous, ask a short clarifying question instead of guessing.
- If nothing in the registry answers the question, say so directly.
- Keep answers concise — a short paragraph or a few short lines, not an essay.
- Respond in plain text only — no markdown (no **bold**, no bullet/numbered lists, no headers). \
The UI renders your reply as plain text and shows the actual matching documents as their own \
clickable cards right below it, so you don't need to format or repeat a document list yourself — \
just write the answer as you'd say it out loud.
"""


class CopilotToolError(Exception):
    """Raised for a recoverable tool-execution problem (bad date format, etc.)
    — its message is safe to feed straight back to the model as the tool's
    result, so it can react (retry with a corrected argument, or tell the
    user) instead of the whole turn failing."""


@dataclass
class ToolRunResult:
    tool_name: str
    content: Dict[str, Any]
    documents: List[Document] = field(default_factory=list)


@dataclass
class CopilotChatResult:
    reply: str
    citations: List[Document]


def _doc_summary(doc: Document) -> Dict[str, Any]:
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "category": doc.category,
        "status": doc.status,
        "deadline_date": doc.deadline_date.isoformat() if doc.deadline_date else None,
    }


def _parse_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise CopilotToolError(f"'{value}' isn't a valid date for {field_name} — use YYYY-MM-DD.")


def _list_documents(db: Session, user: User, args: Dict[str, Any]) -> ToolRunResult:
    query = db.query(Document).filter(Document.user_id == user.id)
    if args.get("category"):
        query = query.filter(Document.category == args["category"])
    if args.get("status"):
        query = query.filter(Document.status == args["status"])
    if args.get("deadline_before"):
        query = query.filter(Document.deadline_date <= _parse_date(args["deadline_before"], "deadline_before"))
    if args.get("deadline_after"):
        query = query.filter(Document.deadline_date >= _parse_date(args["deadline_after"], "deadline_after"))

    docs = query.order_by(Document.upload_date.desc()).limit(MAX_LIST_RESULTS).all()
    return ToolRunResult(
        tool_name="list_documents",
        documents=docs,
        content={"count": len(docs), "documents": [_doc_summary(d) for d in docs]},
    )


def _get_document_count(db: Session, user: User, args: Dict[str, Any]) -> ToolRunResult:
    query = db.query(Document).filter(Document.user_id == user.id)
    if args.get("category"):
        query = query.filter(Document.category == args["category"])
    if args.get("status"):
        query = query.filter(Document.status == args["status"])

    # A real SELECT count(*) — never capped, never fetches full rows, so it's
    # correct regardless of how large the registry is (see list_documents'
    # own MAX_LIST_RESULTS cap, which a count must NOT inherit).
    count = query.count()
    return ToolRunResult(
        tool_name="get_document_count",
        # No citations for a bare count — there's no specific document to
        # link to, just a number.
        documents=[],
        content={"count": count, "category": args.get("category"), "status": args.get("status")},
    )


def _get_deadline_summary(db: Session, user: User) -> ToolRunResult:
    today = date.today()
    threshold = user.due_soon_threshold_days
    base = db.query(Document).filter(Document.user_id == user.id, Document.deadline_date.isnot(None))

    overdue = base.filter(Document.deadline_date < today).order_by(Document.deadline_date.asc()).all()
    due_soon = (
        base.filter(
            Document.deadline_date >= today,
            Document.deadline_date <= today + timedelta(days=threshold),
        )
        .order_by(Document.deadline_date.asc())
        .all()
    )
    total_with_deadline = base.count()

    return ToolRunResult(
        tool_name="get_deadline_summary",
        documents=overdue + due_soon,
        content={
            "today": today.isoformat(),
            "due_soon_threshold_days": threshold,
            "overdue": {"count": len(overdue), "documents": [_doc_summary(d) for d in overdue[:MAX_DEADLINE_LIST]]},
            "due_soon": {"count": len(due_soon), "documents": [_doc_summary(d) for d in due_soon[:MAX_DEADLINE_LIST]]},
            "total_documents_with_a_deadline": total_with_deadline,
        },
    )


def _search_document_text(db: Session, user: User, args: Dict[str, Any]) -> ToolRunResult:
    query_text = (args.get("query") or "").strip()
    if not query_text:
        raise CopilotToolError("A search query is required.")

    ts_query = func.plainto_tsquery("english", query_text)
    snippet_col = func.ts_headline(
        "english", Document.raw_text, ts_query, "MaxFragments=1, MaxWords=30, MinWords=15, ShortWord=3"
    ).label("snippet")
    rank_col = func.ts_rank(Document.search_vector, ts_query).label("rank")

    rows = (
        db.query(Document, snippet_col, rank_col)
        .filter(Document.user_id == user.id, Document.search_vector.op("@@")(ts_query))
        .order_by(rank_col.desc())
        .limit(MAX_SEARCH_RESULTS)
        .all()
    )
    documents = [row[0] for row in rows]
    return ToolRunResult(
        tool_name="search_document_text",
        documents=documents,
        content={
            "query": query_text,
            "search_type": "keyword/full-text (Postgres, English stemming) — not semantic search",
            "count": len(rows),
            "matches": [{**_doc_summary(doc), "snippet": snippet} for doc, snippet, _rank in rows],
        },
    )


def _execute_tool(name: str, args: Dict[str, Any], db: Session, user: User) -> ToolRunResult:
    try:
        if name == "list_documents":
            return _list_documents(db, user, args)
        if name == "get_document_count":
            return _get_document_count(db, user, args)
        if name == "get_deadline_summary":
            return _get_deadline_summary(db, user)
        if name == "search_document_text":
            return _search_document_text(db, user, args)
        raise CopilotToolError(f"'{name}' isn't a tool I have available.")
    except CopilotToolError as exc:
        return ToolRunResult(tool_name=name, content={"error": str(exc)})
    except Exception:
        logger.exception("Copilot tool '%s' failed", name)
        return ToolRunResult(tool_name=name, content={"error": f"Running '{name}' failed unexpectedly."})


def _call_groq(messages: List[Dict[str, Any]]):
    return client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
        temperature=0,
        max_tokens=800,
        reasoning_effort="low",
    )


def _call_groq_with_retry(messages: List[Dict[str, Any]]):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _call_groq(messages)
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2**attempt)


def run_copilot_chat(message: str, history: List[Dict[str, str]], db: Session, user: User) -> CopilotChatResult:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(today=date.today().isoformat())
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": h["role"], "content": h["content"]} for h in history)
    messages.append({"role": "user", "content": message})

    seen_ids = set()
    citations: List[Document] = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = _call_groq_with_retry(messages)
        assistant_message = response.choices[0].message

        if not assistant_message.tool_calls:
            return CopilotChatResult(reply=(assistant_message.content or "").strip(), citations=citations)

        messages.append(
            {
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in assistant_message.tool_calls
                ],
            }
        )

        for tool_call in assistant_message.tool_calls:
            try:
                args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _execute_tool(tool_call.function.name, args, db, user)
            for doc in result.documents:
                if doc.id not in seen_ids and len(citations) < MAX_CITATIONS:
                    seen_ids.add(doc.id)
                    citations.append(doc)
            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result.content)}
            )

    return CopilotChatResult(
        reply="I looked into a few things but didn't finish — try rephrasing, or ask again.",
        citations=citations,
    )
