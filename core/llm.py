import asyncio
import logging

import aiohttp

from config import Config

logger = logging.getLogger(__name__)


class OllamaClient:
    """Async client for the local Ollama API."""

    async def ask(self, prompt: str, model: str | None = None, system_prompt: str | None = None) -> str:
        model = model or Config.DEFAULT_MODEL
        url = f"{Config.OLLAMA_URL}/api/generate"
        payload = {"model": model, "prompt": prompt, "stream": False}
        if system_prompt:
            payload["system"] = system_prompt

        try:
            async with aiohttp.ClientSession() as session:
                timeout = aiohttp.ClientTimeout(total=120)
                async with session.post(url, json=payload, timeout=timeout) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return f"Ollama error ({resp.status}): {error[:300]}"
                    data = await resp.json()
                    return data.get("response", "No response from model.")
        except aiohttp.ClientConnectorError:
            return f"Cannot connect to Ollama at {Config.OLLAMA_URL}. Is it running?"
        except asyncio.TimeoutError:
            return "Ollama request timed out (120s)."
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return f"LLM error: {e}"

    async def ask_code(self, prompt: str) -> str:
        return await self.ask(prompt, model=Config.CODE_MODEL)

    async def list_models(self) -> list | None:
        url = f"{Config.OLLAMA_URL}/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("models", [])
        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return None

    async def is_alive(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.OLLAMA_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False
