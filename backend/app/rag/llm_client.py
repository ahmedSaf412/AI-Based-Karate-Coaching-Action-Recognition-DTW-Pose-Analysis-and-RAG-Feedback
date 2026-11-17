import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from backend/ directory
# Look for .env in backend/ (app layout) and in the project root (karate-paper/.env); first hit wins.
_HERE = Path(__file__).resolve()
for _p in [*list(_HERE.parents)[2:4], Path.cwd()]:
    if (_p / '.env').is_file():
        load_dotenv(_p / '.env', override=True)
        break

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Model used for coaching/chat generation. Groq retires model IDs over time;
# a retired ID silently degrades every call to the offline template fallback.
# Default is a currently supported production chat model (checked 2026-11-16).
# NOTE: Groq shut down llama-3.3-70b-versatile and llama-3.1-8b-instant on 2026-08-16 (free/developer keys);
# its recommended replacements are openai/gpt-oss-120b and openai/gpt-oss-20b. Do NOT switch back to them.
# Override in .env without a code change if Groq renames again, e.g.:
#   GROQ_MODEL=openai/gpt-oss-20b
_DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
# openai/gpt-oss-20b and -120b are Groq *production* models; qwen/qwen3.6-27b is a *preview* model (last resort only).
# NOTE: the earlier id "qwen/qwen3.8-27b" does not exist on Groq, so that fallback could never succeed.
_FALLBACK_GROQ_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.6-27b")

# Retired IDs that must never be used even if an old .env still pins them.
_DECOMMISSIONED_MODELS = {
    "llama-3.1-70b-versatile",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "gemma2-9b-it",
    "mixtral-8x7b-32768",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3-32b",
}


def _resolve_groq_model() -> str:
    """Pick the Groq chat model: env GROQ_MODEL unless it names a retired ID."""
    m = (os.environ.get("GROQ_MODEL") or "").strip()
    if m and m not in _DECOMMISSIONED_MODELS:
        return m
    return _DEFAULT_GROQ_MODEL


GROQ_MODEL = _resolve_groq_model()


def _candidate_models() -> list[str]:
    """Primary GROQ_MODEL plus live fallbacks, skipping retired IDs."""
    seen = set()
    out = []
    for m in (GROQ_MODEL, _DEFAULT_GROQ_MODEL, *_FALLBACK_GROQ_MODELS):
        if m and m not in _DECOMMISSIONED_MODELS and m not in seen:
            seen.add(m)
            out.append(m)
    return out

# Lazy-initialize client so startup never crashes even if key is missing
_groq_client = None

def _get_client():
    """Return a Groq client, re-reading the environment on every cache miss.

    Re-check (instead of only trusting the import-time GROQ_API_KEY) so callers
    that load .env *after* this module was imported — e.g. the validation
    notebook — still get live LLM calls rather than the offline fallback.
    Also re-resolves GROQ_MODEL so an old cached module with a retired model ID
    (e.g. llama-3.1-70b-versatile) does not degrade every call.
    """
    global _groq_client, GROQ_API_KEY, GROQ_MODEL
    # Always re-resolve the model name in case the module was imported before
    # the env was loaded, or the model was retired after the module was cached.
    GROQ_MODEL = _resolve_groq_model()
    if _groq_client is None:
        key = GROQ_API_KEY or os.environ.get("GROQ_API_KEY", "")
        if key:
            GROQ_API_KEY = key
            try:
                from groq import Groq
                _groq_client = Groq(api_key=key)
            except Exception as e:
                print(f"[llm_client] Groq client init failed: {e}")
    return _groq_client



# ── Single place that talks to Groq ───────────────────────────────────────────
# Why this exists: the default model (openai/gpt-oss-20b) is a *reasoning* model.
# Its hidden reasoning tokens count against the token limit, so with the old
# max_tokens=600 the visible answer could come back empty or cut off, which then
# looked like "the LLM is not working" and silently dropped to the template.
# We therefore (a) ask for low reasoning effort and no reasoning text, (b) allow a
# larger budget, (c) treat an empty answer as a failure and try the next model.
# extra_body is used so this works on any installed `groq` SDK version.
def _chat(client, model_id: str, prompt: str, system: str) -> str:
    extra = {}
    if model_id.startswith("openai/gpt-oss"):
        # gpt-oss accepts low|medium|high; qwen3.x uses different values, so we send nothing for it
        extra["reasoning_effort"] = "low"
        extra["include_reasoning"] = False
    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.6,
        max_completion_tokens=1500,
        extra_body=extra or None,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("empty completion (reasoning consumed the token budget?)")
    return text

# ── Fallback formatter (used when Groq is unavailable) ────────────────────────
def _fallback_coaching(move_name: str, error_context: list) -> str:
    """
    Returns plain-text coaching from the DTW error list without calling Groq.
    This always works — it's just less naturally phrased than the LLM version.
    """
    if not error_context:
        return "Looking good! Keep practising this technique."

    lines = [f"📊 Form analysis for **{move_name.replace('_', ' ').title()}**:\n"]
    for err in error_context[:3]:
        joint = err["joint"].replace("_", " ").title()
        diff  = err["mean_error"]
        uval  = err["user_val"]
        rval  = err["ref_val"]
        direction = "too extended" if uval > rval else "needs more extension"
        lines.append(f"• **{joint}**: {direction} (you: {uval:.0f}° | master: {rval:.0f}°, diff {diff:.0f}°)")

    lines.append(
        "\n💡 Focus on matching the master's posture in those joints. "
        "Try slowing down the movement and checking each position in a mirror."
    )
    return "\n".join(lines)


# ── Coaching feedback ──────────────────────────────────────────────────────────
def generate_coaching_feedback(move_name: str, error_context: list, retrieved_chunks: list) -> str:
    client = _get_client()

    if not client:
        print("[llm_client] Groq key missing or client failed — using fallback coaching.")
        return _fallback_coaching(move_name, error_context)

    error_summary = "\n".join([
        f"- {e['joint']}: {e['query']} (user: {round(e['user_val'])}°, target: {e['target_range']}°)"
        for e in error_context
    ])
    knowledge_summary = "\n\n".join([
        f"Source {i+1}:\n{chunk['text']}"
        for i, chunk in enumerate(retrieved_chunks)
    ])

    prompt = f"""You are an encouraging and expert AI Karate Sensei.

The student just attempted a '{move_name}'. Here are the specific errors detected by our computer vision system:
{error_summary}

Here is coaching knowledge retrieved from the master database regarding these issues:
{knowledge_summary}

Your task:
Write a friendly, encouraging, and actionable response strictly based on the coaching knowledge provided.
- Limit to 2-3 short paragraphs.
- Do NOT use generic template praise. Directly address the joint errors shown in the data.
- If the student's angle is higher than the target, tell them to decrease it (or vice versa), using natural language.
- Give them 1-2 specific actions to fix their errors based on the data.
- Never mention "computer vision", "angles", or "degrees" — translate the math into physical feelings.
"""

    import time
    last_err = None
    for model_id in _candidate_models():
        try:
            t0 = time.time()
            text = _chat(client, model_id, prompt, "You are a helpful and expert AI Karate coach.")
            latency = (time.time() - t0) * 1000
            print(f"[Metrics] RAG LLM Generation took {latency:.2f} ms ({model_id})")
            return text
        except Exception as e:
            last_err = e
            print(f"[llm_client] Groq API error on {model_id}: {type(e).__name__}: {e}")

    print(f"[llm_client] All Groq models failed (last: {last_err}); using fallback coaching.")
    return _fallback_coaching(move_name, error_context)



# ── RAG-only chat fallback ─────────────────────────────────────────────────────
def _rag_only_chat_fallback(chat_message: str, retrieved_chunks: list) -> str:
    """Format retrieved coaching chunks as plain text when Groq is unavailable."""
    if not retrieved_chunks:
        return (
            "💡 I don't have specific knowledge about that topic yet.\n"
            "Try asking about mae geri, gyaku zuki, or gedan barai technique."
        )
    lines = ["💡 Here's what I know about your question:\n"]
    for i, chunk in enumerate(retrieved_chunks[:3], 1):
        lines.append(f"{i}. {chunk['text'].strip()}")
    lines.append(
        "\n_(AI coaching brain is currently offline — showing knowledge base directly)_"
    )
    return "\n\n".join(lines)


# ── Chat response ──────────────────────────────────────────────────────────────
def generate_chat_response(chat_message: str, move_name: str, retrieved_chunks: list) -> str:
    client = _get_client()

    if not client:
        print("[llm_client] Groq unavailable — using RAG-only chat fallback.")
        return _rag_only_chat_fallback(chat_message, retrieved_chunks)

    knowledge_summary = "\n\n".join([
        f"Source {i+1}:\n{chunk['text']}"
        for i, chunk in enumerate(retrieved_chunks)
    ])

    prompt = f"""You are an encouraging and expert AI Karate Sensei answering a student's question.

Context (Move): {move_name}
Student asks: "{chat_message}"

Here is coaching knowledge retrieved from the master database:
{knowledge_summary}

Answer the student's question concisely using the provided knowledge. Be friendly and instructional.
"""

    last_err = None
    for model_id in _candidate_models():
        try:
            return _chat(client, model_id, prompt, "You are a helpful and expert AI Karate coach.")
        except Exception as e:
            last_err = e
            print(f"[llm_client] Groq chat error on {model_id}: {type(e).__name__}: {e}")

    print(f"[llm_client] All Groq chat models failed (last: {last_err})")
    return _rag_only_chat_fallback(chat_message, retrieved_chunks)


# ── Self-test:  python -m app.rag.llm_client   (run from backend/) ────────────
# Prints the exact reason a live call fails instead of silently falling back.
if __name__ == "__main__":
    import sys
    print(f"llm_client file : {__file__}")
    print(f"GROQ_API_KEY set: {bool(os.environ.get('GROQ_API_KEY'))}")
    print(f"candidate models: {_candidate_models()}")
    try:
        import groq
        print(f"groq SDK version: {getattr(groq, '__version__', '?')}")
    except Exception as e:
        print(f"groq SDK import failed: {e}")
        sys.exit(1)
    c = _get_client()
    if c is None:
        print("-> no client (missing key or SDK init failed)"); sys.exit(1)
    ok = False
    for m in _candidate_models():
        try:
            print(f"[{m}] ->", _chat(c, m, "Reply with the single word: ready", "You are a test."))
            ok = True
            break
        except Exception as e:
            print(f"[{m}] FAILED: {type(e).__name__}: {e}")
    sys.exit(0 if ok else 2)