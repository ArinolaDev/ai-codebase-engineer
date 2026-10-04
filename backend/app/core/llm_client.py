"""
A small abstraction over "ask an LLM to generate text" so the rest of the
app doesn't care whether we're hitting a local Ollama model or a paid API.
Right now we only implement Ollama (free, local). When there's budget for
Anthropic, add an AnthropicClient here with the same .generate() interface
and swap it in via config — nothing else in the app needs to change.
"""

import httpx

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:1b"


class LLMError(Exception):
    pass


def generate(prompt: str, model: str = DEFAULT_MODEL, timeout: float = 60.0) -> str:
    """Send a prompt to the local Ollama server and return the generated text."""
    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.ConnectError:
        raise LLMError(
            "Could not connect to Ollama. Is it running? Try `ollama serve` "
            "or check that the Ollama app is open."
        )
    except httpx.HTTPStatusError as e:
        raise LLMError(f"Ollama returned an error: {e.response.text}")

    data = response.json()
    return data.get("response", "").strip()