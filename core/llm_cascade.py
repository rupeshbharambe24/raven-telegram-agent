"""
Smart LLM cascade: Groq -> Gemini -> local Ollama.

Drop-in replacement for OllamaClient. Tries cloud providers first
for speed, falls back to local Ollama if they fail.
"""

import asyncio
import logging

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

# Provider names for logging / status
GROQ = "Groq"
GEMINI = "Gemini"
OLLAMA = "Ollama"

_TIMEOUT = aiohttp.ClientTimeout(total=30)


class SmartLLM:
    """Cascading LLM client: Groq -> Gemini -> local Ollama."""

    def __init__(self):
        self._last_provider: str = "none"

    # ── public interface (same as OllamaClient) ─────────────────────

    async def ask(
        self,
        prompt: str,
        model: str | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """Send a prompt through the cascade and return the first successful response."""
        providers = [
            (GROQ, self._ask_groq),
            (GEMINI, self._ask_gemini),
            (OLLAMA, self._ask_ollama),
        ]

        for name, fn in providers:
            try:
                logger.info(f"LLM cascade: trying {name}")
                result = await fn(prompt, system_prompt)
                self._last_provider = name
                logger.info(f"LLM cascade: {name} succeeded")
                return result
            except Exception as exc:
                logger.warning(f"LLM cascade: {name} failed — {type(exc).__name__}: {exc}")
                continue

        self._last_provider = "none"
        return "All LLM providers failed. Check logs for details."

    async def ask_code(self, prompt: str) -> str:
        """Code-focused request (uses same cascade)."""
        system = (
            "You are an expert programmer. Provide clean, correct code. "
            "Wrap code in markdown code blocks with the language tag."
        )
        return await self.ask(prompt, system_prompt=system)

    async def list_models(self) -> list | None:
        """List locally-installed Ollama models (for /models command)."""
        url = f"{Config.OLLAMA_URL}/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("models", [])
        except Exception as exc:
            logger.error(f"Error listing Ollama models: {exc}")
            return None

    async def is_alive(self) -> dict:
        """Check connectivity for all three providers. Returns a status dict."""
        groq_ok, gemini_ok, ollama_ok = await asyncio.gather(
            self._ping_groq(),
            self._ping_gemini(),
            self._ping_ollama(),
        )
        return {
            GROQ: groq_ok,
            GEMINI: gemini_ok,
            OLLAMA: ollama_ok,
        }

    def get_status(self) -> str:
        """Return which provider handled the last request."""
        return self._last_provider

    # ── Groq (OpenAI-compatible) ────────────────────────────────────

    async def _ask_groq(self, prompt: str, system_prompt: str | None) -> str:
        if not Config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY not configured")

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": Config.GROQ_MODEL,
            "messages": messages,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=_TIMEOUT) as resp:
                if resp.status == 429:
                    body = await resp.text()
                    raise RuntimeError(f"Groq rate-limited (429): {body[:200]}")
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Groq HTTP {resp.status}: {body[:300]}")
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    async def _ping_groq(self) -> bool:
        if not Config.GROQ_API_KEY:
            return False
        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {Config.GROQ_API_KEY}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    # ── Gemini (REST API) ───────────────────────────────────────────

    async def _ask_gemini(self, prompt: str, system_prompt: str | None) -> str:
        if not Config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{Config.GEMINI_MODEL}:generateContent"
            f"?key={Config.GEMINI_API_KEY}"
        )
        headers = {"Content-Type": "application/json"}

        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
        }
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=_TIMEOUT) as resp:
                if resp.status == 429:
                    text = await resp.text()
                    raise RuntimeError(f"Gemini rate-limited (429): {text[:200]}")
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Gemini HTTP {resp.status}: {text[:300]}")
                data = await resp.json()
                # Navigate Gemini's response structure
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError) as exc:
                    raise RuntimeError(f"Unexpected Gemini response: {str(data)[:300]}") from exc

    async def _ping_gemini(self) -> bool:
        if not Config.GEMINI_API_KEY:
            return False
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models"
            f"?key={Config.GEMINI_API_KEY}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    # ── Ollama (local) ──────────────────────────────────────────────

    async def _ask_ollama(self, prompt: str, system_prompt: str | None) -> str:
        url = f"{Config.OLLAMA_URL}/api/generate"
        payload: dict = {
            "model": Config.DEFAULT_MODEL,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt

        timeout = aiohttp.ClientTimeout(total=120)  # Ollama is local, allow more time
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=timeout) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise RuntimeError(f"Ollama HTTP {resp.status}: {error[:300]}")
                data = await resp.json()
                text = data.get("response", "")
                if not text:
                    raise RuntimeError("Ollama returned empty response")
                return text

    async def _ping_ollama(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.OLLAMA_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False
