import time

from groq import Groq, RateLimitError

from config import GROQ_API_KEY

MODEL = "openai/gpt-oss-120b"
MAX_CHARS = 12000
MAX_RETRIES = 3

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions about a specific document. Answer "
    "using only the document text provided below. If the answer isn't in the document, "
    "say so clearly instead of guessing. Be concise."
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
