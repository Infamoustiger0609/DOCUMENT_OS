"""Natural-language layer over the Phase 21 document-editing tools (Phase 23
— see CLAUDE.md's Editor assistant section). POST /editor/chat (editor_router.py)
sends the user's message to Groq with these tools defined as function-calling
schemas; Groq decides whether to answer directly (a clarifying question, or a
plain "I can't do that"), or to call one or more tools. Tool calls are executed
here against the SAME transform functions tools_router.py's direct endpoints
use, and results are saved via the same tools_common.save_tool_output — so a
file produced by the assistant is a completely ordinary ToolFile row,
downloadable via GET /tools/{id}/download-url exactly like a manually-run tool.

Chaining (e.g. "merge these, then compress the result under 10MB") is a
standard agentic tool-calling loop: call Groq -> execute the tool it asked
for -> feed the result back as a "tool" message -> call Groq again -> repeat
until it responds with plain text instead of another tool call, or
MAX_TOOL_ROUNDS is hit.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from groq import Groq, RateLimitError
from sqlalchemy.orm import Session

from config import GROQ_API_KEY
from image_tools import convert_image, resize_image
from models import ToolFile, User
from ocr_tools import ocr_pdf
from office_tools import docx_to_pdf
from pdf_tools import compress_pdf, merge_pdfs, pdf_to_docx, split_pdf
from storage import download_file_from_storage
from tools_common import save_tool_output

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
MAX_RETRIES = 3
# One user message can legitimately need several chained tool calls (see the
# module docstring's example) — capped so a confused model can't loop forever
# against a real Groq quota/this request's own timeout.
MAX_TOOL_ROUNDS = 5

client = Groq(api_key=GROQ_API_KEY)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
IMAGE_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
IMAGE_MIME_BY_TARGET = {"jpg": "image/jpeg", "png": "image/png", "tiff": "image/tiff"}

# Ordered largest/highest-fidelity -> smallest/most-aggressive. Used by
# compress_pdf's target_size_kb handling below.
_QUALITY_LADDER = ["high", "medium", "low"]

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "merge_pdf",
            "description": "Merge two or more PDF files into a single PDF, in the given order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Filenames of the PDFs to merge, in the order they should appear in "
                            "the output. Must be exact filenames from the Available files list."
                        ),
                    }
                },
                "required": ["files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "split_pdf",
            "description": "Split one PDF into multiple PDFs — one page per file, or by page range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "The PDF filename to split."},
                    "ranges": {
                        "type": "string",
                        "description": (
                            'Optional, e.g. "1-3,5,7-9". Omit entirely to split into one PDF per '
                            "page — that is a normal default, not something to ask about."
                        ),
                    },
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compress_pdf",
            "description": (
                "Shrink a PDF's file size by recompressing its embedded images. Use `quality` for "
                "a general request; use `target_size_kb` if the user gave an actual size goal (e.g. "
                "'under 10MB' -> target_size_kb: 10240) — the tool will try progressively more "
                "aggressive quality levels to get under that size, best-effort."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "The PDF filename to compress."},
                    "quality": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Compression level. Defaults to medium if not given.",
                    },
                    "target_size_kb": {
                        "type": "number",
                        "description": "Optional target file size in KB, if the user specified one.",
                    },
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pdf_to_docx",
            "description": "Convert a PDF file to an editable Word (.docx) document.",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string", "description": "The PDF filename to convert."}},
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_to_pdf",
            "description": "Convert a Word (.docx) document to a PDF file.",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string", "description": "The .docx filename to convert."}},
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_image",
            "description": "Convert an image file (JPG, PNG, or TIFF) to a different image format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "The image filename to convert."},
                    "target_format": {
                        "type": "string",
                        "enum": ["jpg", "png", "tiff"],
                        "description": (
                            "The format to convert to. Required — ask the user if they didn't say "
                            "which format they want."
                        ),
                    },
                },
                "required": ["file", "target_format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resize_image",
            "description": (
                "Resize an image by pixel dimensions and/or a target file size. At least one of "
                "width, height, or target_size_kb is required — ask the user for one if none of "
                "these were given."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "The image filename to resize."},
                    "width": {"type": "number", "description": "Target width in pixels."},
                    "height": {"type": "number", "description": "Target height in pixels."},
                    "target_size_kb": {"type": "number", "description": "Target file size in KB."},
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ocr_pdf",
            "description": (
                "Make a scanned/image-only PDF searchable by adding an invisible OCR text layer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "The PDF filename to OCR."},
                    "force_ocr": {
                        "type": "boolean",
                        "description": "Re-OCR every page even if some already have text. Defaults to false.",
                    },
                },
                "required": ["file"],
            },
        },
    },
]

SYSTEM_PROMPT_TEMPLATE = """You are the DocumentOS Editor assistant. You help users merge, \
split, compress, convert (PDF<->DOCX, and between JPG/PNG/TIFF), resize, and OCR files by \
calling the tools provided — that is the entire scope of what you do. You do not answer \
general-knowledge questions, hold unrelated conversations, or perform document classification \
or data extraction (that is a different, separate part of this app). If a request has nothing \
to do with editing/converting a file, say so plainly and briefly redirect to what you can help \
with instead of answering it.

Available files in this session:
{files_block}

Rules:
- Only reference files from the list above, using their exact filenames.
- If a request is ambiguous, or a tool needs information the user didn't give (e.g. what size or \
dimensions to resize to, what format to convert an image to, which files to merge and in what \
order), ask one short clarifying question instead of guessing or inventing a value.
- If a request implies multiple steps (e.g. "merge these and then compress the result"), call \
one tool at a time — after seeing each result, decide whether another tool call is still needed \
to finish the request.
- If nothing in the request matches an available tool, or the file it needs isn't in the list \
above, say so plainly rather than attempting it anyway.
- Once everything requested is done, reply with one short, plain-language sentence summarizing \
what happened — don't repeat raw filenames or ids verbatim, the user can already see the result.
"""


class ChatToolError(Exception):
    """Raised for a recoverable tool-execution problem (file not found, missing
    required argument the model should have asked about, etc.) — its message
    is safe to feed straight back to the model as the tool's result, so it can
    react appropriately instead of the whole turn failing."""


@dataclass
class ChatFileContext:
    """Resolves a filename the model references to actual bytes, checking (in
    order) files produced earlier in this same turn's tool-calling loop, files
    uploaded with this request, and finally files referenced from an earlier
    turn in the conversation (context_refs — fetched from Storage on demand,
    no need for the frontend to re-upload something already produced)."""

    current_user: User
    db: Session
    uploaded: Dict[str, bytes]
    context_refs: Dict[str, ToolFile] = field(default_factory=dict)
    produced: Dict[str, bytes] = field(default_factory=dict)

    def resolve(self, filename: str) -> bytes:
        if filename in self.produced:
            return self.produced[filename]
        if filename in self.uploaded:
            return self.uploaded[filename]
        if filename in self.context_refs:
            tool_file = self.context_refs[filename]
            try:
                return download_file_from_storage(tool_file.storage_path)
            except Exception as exc:
                raise ChatToolError(
                    f"Could not retrieve '{filename}' — it may have expired."
                ) from exc
        raise ChatToolError(f"'{filename}' isn't one of the available files.")

    def known_filenames(self) -> List[str]:
        return sorted(set(self.uploaded) | set(self.context_refs))


@dataclass
class ToolRunResult:
    tool_name: str
    arguments: Dict[str, Any]
    tool_files: List[ToolFile] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class EditorChatResult:
    reply: str
    tool_runs: List[ToolRunResult]


def _compress_to_target(data: bytes, quality: str, target_size_kb: Optional[float]) -> bytes:
    if not target_size_kb:
        return compress_pdf(data, quality)

    start = _QUALITY_LADDER.index(quality) if quality in _QUALITY_LADDER else _QUALITY_LADDER.index("medium")
    target_bytes = target_size_kb * 1024
    result = data
    for step in _QUALITY_LADDER[start:]:
        result = compress_pdf(data, step)
        if len(result) <= target_bytes:
            break
    return result


def _execute_tool(name: str, args: Dict[str, Any], ctx: ChatFileContext) -> ToolRunResult:
    try:
        if name == "merge_pdf":
            filenames = args.get("files") or []
            if len(filenames) < 2:
                raise ChatToolError("Merging needs at least 2 files.")
            data_list = [ctx.resolve(f) for f in filenames]
            merged = merge_pdfs(data_list)
            tool_file = save_tool_output(
                ctx.db, ctx.current_user, "merge-pdf", ", ".join(filenames), "merged.pdf", merged, PDF_MIME
            )
            ctx.produced[tool_file.output_filename] = merged
            return ToolRunResult(name, args, [tool_file])

        if name == "split_pdf":
            filename = args["file"]
            data = ctx.resolve(filename)
            parts = split_pdf(data, args.get("ranges") or None)
            tool_files = []
            for part_name, part_bytes in parts:
                tf = save_tool_output(ctx.db, ctx.current_user, "split-pdf", filename, part_name, part_bytes, PDF_MIME)
                ctx.produced[part_name] = part_bytes
                tool_files.append(tf)
            return ToolRunResult(name, args, tool_files)

        if name == "compress_pdf":
            filename = args["file"]
            data = ctx.resolve(filename)
            compressed = _compress_to_target(data, args.get("quality") or "medium", args.get("target_size_kb"))
            output_name = f"compressed_{Path(filename).stem}.pdf"
            tf = save_tool_output(ctx.db, ctx.current_user, "compress-pdf", filename, output_name, compressed, PDF_MIME)
            ctx.produced[output_name] = compressed
            return ToolRunResult(name, args, [tf])

        if name == "pdf_to_docx":
            filename = args["file"]
            data = ctx.resolve(filename)
            converted = pdf_to_docx(data)
            output_name = f"{Path(filename).stem}.docx"
            tf = save_tool_output(ctx.db, ctx.current_user, "pdf-to-docx", filename, output_name, converted, DOCX_MIME)
            ctx.produced[output_name] = converted
            return ToolRunResult(name, args, [tf])

        if name == "docx_to_pdf":
            filename = args["file"]
            data = ctx.resolve(filename)
            converted = docx_to_pdf(data)
            output_name = f"{Path(filename).stem}.pdf"
            tf = save_tool_output(ctx.db, ctx.current_user, "docx-to-pdf", filename, output_name, converted, PDF_MIME)
            ctx.produced[output_name] = converted
            return ToolRunResult(name, args, [tf])

        if name == "convert_image":
            filename = args["file"]
            data = ctx.resolve(filename)
            target_format = args["target_format"]
            converted = convert_image(data, target_format)
            output_name = f"{Path(filename).stem}.{target_format}"
            tf = save_tool_output(
                ctx.db, ctx.current_user, "convert-image", filename, output_name, converted,
                IMAGE_MIME_BY_TARGET[target_format],
            )
            ctx.produced[output_name] = converted
            return ToolRunResult(name, args, [tf])

        if name == "resize_image":
            filename = args["file"]
            width, height, target_kb = args.get("width"), args.get("height"), args.get("target_size_kb")
            if not (width or height or target_kb):
                raise ChatToolError("Resizing needs at least a width, height, or target size.")
            data = ctx.resolve(filename)
            extension = Path(filename).suffix.lower()
            resized = resize_image(data, extension, width, height, target_kb)
            output_name = f"resized_{filename}"
            mime = IMAGE_MIME_BY_EXT.get(extension, "application/octet-stream")
            tf = save_tool_output(ctx.db, ctx.current_user, "resize-image", filename, output_name, resized, mime)
            ctx.produced[output_name] = resized
            return ToolRunResult(name, args, [tf])

        if name == "ocr_pdf":
            filename = args["file"]
            data = ctx.resolve(filename)
            searchable = ocr_pdf(data, force_ocr=bool(args.get("force_ocr", False)))
            output_name = f"searchable_{filename}"
            tf = save_tool_output(ctx.db, ctx.current_user, "ocr-pdf", filename, output_name, searchable, PDF_MIME)
            ctx.produced[output_name] = searchable
            return ToolRunResult(name, args, [tf])

        raise ChatToolError(f"'{name}' isn't a tool I have available.")

    except ChatToolError as exc:
        return ToolRunResult(name, args, [], error=str(exc))
    except Exception:
        logger.exception("Editor chat tool '%s' failed", name)
        return ToolRunResult(name, args, [], error=f"Running '{name}' failed unexpectedly.")


def _tool_result_message_content(result: ToolRunResult) -> str:
    if result.error:
        return json.dumps({"error": result.error})
    return json.dumps(
        {
            "files": [
                {"filename": tf.output_filename, "size_bytes": tf.size_bytes}
                for tf in result.tool_files
            ]
        }
    )


def _build_files_block(ctx: ChatFileContext) -> str:
    names = ctx.known_filenames()
    if not names:
        return "(no files are available yet — ask the user to upload one)"
    return "\n".join(f"- {name}" for name in names)


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


def run_editor_chat(message: str, history: List[Dict[str, str]], ctx: ChatFileContext) -> EditorChatResult:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(files_block=_build_files_block(ctx))
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": h["role"], "content": h["content"]} for h in history)
    messages.append({"role": "user", "content": message})

    tool_runs: List[ToolRunResult] = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = _call_groq_with_retry(messages)
        assistant_message = response.choices[0].message

        if not assistant_message.tool_calls:
            return EditorChatResult(reply=(assistant_message.content or "").strip(), tool_runs=tool_runs)

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
            result = _execute_tool(tool_call.function.name, args, ctx)
            tool_runs.append(result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": _tool_result_message_content(result),
                }
            )

    return EditorChatResult(
        reply="I ran several steps but didn't finish — here's what completed so far.",
        tool_runs=tool_runs,
    )
