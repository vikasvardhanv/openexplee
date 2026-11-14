import asyncio
import json
import os
import re
import shutil
import time
from typing import Any, Dict, Optional
import httpx
from app.config import settings

_CLAUDE_CLI_COOLDOWN_UNTIL = 0.0


def _extract_json_blob(text: str) -> Any:
    """Extracts JSON object or array from raw LLM text with multi-pattern tolerance."""
    cleaned = text.strip()
    if "```" in cleaned:
        m_fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
        if m_fence:
            cleaned = m_fence.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    m_obj = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
    if m_obj:
        try:
            return json.loads(m_obj.group(1))
        except json.JSONDecodeError:
            pass

    # Tolerant extraction for individual JSON objects: [{"a": 1}, {"b": 2}] or multiple lines
    objs = re.findall(r"\{[^{}]+\}", cleaned)
    if objs:
        extracted = []
        for o in objs:
            try:
                extracted.append(json.loads(o))
            except Exception:
                pass
        if extracted:
            return extracted

    # Tolerant extraction for strings / arrays of strings
    arrays = re.findall(r'\[\s*"[^"]+"\s*\]', cleaned)
    if arrays:
        items = []
        for a in arrays:
            try:
                items.extend(json.loads(a))
            except Exception:
                pass
        if items:
            return items

    # Fallback: extract list of quoted lines (e.g. 1. "query one" 2. "query two")
    quoted_lines = re.findall(r'"([^"\n]{3,120})"', cleaned)
    if quoted_lines and len(quoted_lines) >= 2:
        return quoted_lines

    raise ValueError("No valid JSON found in LLM response")


async def _call_claude_cli(prompt: str, timeout: float = 30.0) -> Optional[str]:
    """Runs `claude -p` using the active local Claude Code subscription ($0 API cost)."""
    global _CLAUDE_CLI_COOLDOWN_UNTIL
    if time.time() < _CLAUDE_CLI_COOLDOWN_UNTIL:
        return None

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", prompt, "--output-format", "text", "--safe-mode", "--tools", "",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except Exception:
        return None

    err_text = stderr.decode(errors="ignore") if stderr else ""
    if proc.returncode != 0:
        if any(w in err_text.lower() for w in ("limit", "quota", "session", "exceeded", "hit your")):
            print(f"[LLM:claude_cli] rate/session limit reached, cooling down for 10m: {err_text[:120]}")
            _CLAUDE_CLI_COOLDOWN_UNTIL = time.time() + 600.0
        else:
            print(f"[LLM:claude_cli] exit {proc.returncode}: {err_text[:120]}")
        return None
    return stdout.decode(errors="ignore").strip()


async def _call_openai_compatible(base_url: str, model: str, prompt: str, api_key: Optional[str] = None, json_mode: bool = False, timeout: float = 60.0) -> Optional[str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", headers=headers, json=payload)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        print(f"[LLM:openai_compat] HTTP {resp.status_code}: {resp.text[:200]}")
    return None


async def _call_gemini(prompt: str, api_key: str) -> Optional[str]:
    model = settings.default_llm_model or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code == 200:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return None


async def _call_anthropic(prompt: str, api_key: str) -> Optional[str]:
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-3-5-haiku-latest",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        if resp.status_code == 200:
            return resp.json()["content"][0]["text"]
    return None


async def call_llm_json(prompt: str, provider: Optional[str] = None, api_key: Optional[str] = None) -> Optional[Any]:
    """
    Unified LLM JSON router supporting Claude Code subscription (`claude_cli`),
    Thor GPU Ollama (`thor_ollama`), local Ollama, Gemini, OpenAI, Anthropic, and Groq.
    Automatically falls back across available zero-cost providers if primary fails.
    """
    chosen = provider or settings.default_llm_provider
    attempts = [chosen]
    for fallback in ("claude_cli", "thor_ollama", "ollama"):
        if fallback not in attempts:
            attempts.append(fallback)

    for prov in attempts:
        raw_text: Optional[str] = None
        try:
            if prov == "claude_cli":
                raw_text = await _call_claude_cli(prompt)
            elif prov == "thor_ollama":
                raw_text = await _call_openai_compatible(
                    settings.thor_ollama_url, settings.thor_ollama_model, prompt
                )
            elif prov == "ollama":
                raw_text = await _call_openai_compatible(
                    settings.ollama_base_url, "llama3.2", prompt
                )
            elif prov == "gemini" and (api_key or settings.gemini_api_key):
                raw_text = await _call_gemini(prompt, api_key or settings.gemini_api_key or "")
            elif prov == "openai" and (api_key or settings.openai_api_key):
                raw_text = await _call_openai_compatible(
                    "https://api.openai.com", "gpt-4o-mini", prompt, api_key or settings.openai_api_key, json_mode=True
                )
            elif prov == "groq" and (api_key or settings.groq_api_key):
                raw_text = await _call_openai_compatible(
                    "https://api.groq.com/openai", "llama-3.3-70b-versatile", prompt, api_key or settings.groq_api_key, json_mode=True
                )
            elif prov == "anthropic" and (api_key or settings.anthropic_api_key):
                raw_text = await _call_anthropic(prompt, api_key or settings.anthropic_api_key or "")

            if raw_text:
                return _extract_json_blob(raw_text)
        except Exception as exc:
            print(f"[LLM:{prov}] error: {exc} | raw: {repr(raw_text)[:200] if raw_text else 'None'}")

    return None
