import json
import re
import time
import zlib
from dataclasses import dataclass

import httpx

from ..config import Settings, settings as default_settings
from ..i18n import N_, translate
from .injection import detect_injection, normalize


@dataclass
class GradingRequest:
    instructions: str
    criteria: list[dict]
    student_text: str
    model_answer: str = ""
    language: str = "lv"
    temperature: float | None = None


class LLMError(RuntimeError):
    def __init__(self, message: str, **params):
        super().__init__(message.format(**params) if params else message)
        self.message = message
        self.params = params

    def localized(self, language: str) -> str:
        return translate(self.message, language, **self.params)


class LLMBadRequest(LLMError):
    pass


RETRY_STATUSES = {429, 500, 502, 503, 504, 529}


class Provider:
    name = "base"
    label = "API"

    def __init__(self, cfg: Settings, sleep=time.sleep):
        self.cfg = cfg
        self._sleep = sleep

    @property
    def model_label(self) -> str:
        return self.name

    def generate(self, system: str, user: str, request: GradingRequest) -> str:
        raise NotImplementedError

    def _temperature(self, request: GradingRequest) -> float:
        return self.cfg.llm_temperature if request.temperature is None else request.temperature

    def _wait_seconds(self, response: httpx.Response, attempt: int) -> float:
        wait = None
        header = response.headers.get("retry-after")
        if header:
            try:
                wait = float(header)
            except ValueError:
                wait = None
        if wait is None:
            wait = 5.0 * (2 ** attempt)
        return min(max(wait, 1.0), self.cfg.llm_max_wait)

    def _post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        attempts = max(self.cfg.llm_max_retries, 0) + 1
        for attempt in range(attempts):
            try:
                r = httpx.post(url, json=payload, headers=headers, timeout=self.cfg.llm_timeout)
            except httpx.ConnectError as e:
                raise LLMError("Cannot connect to {label} ({url}). Is the service running and is the internet available?",
                               label=self.label, url=url) from e
            except httpx.TimeoutException as e:
                raise LLMError("{label} did not respond within {seconds} s.",
                               label=self.label, seconds=f"{self.cfg.llm_timeout:g}") from e
            except httpx.HTTPError as e:
                raise LLMError("{label} error: {error}", label=self.label, error=e) from e

            if r.status_code in RETRY_STATUSES and attempt < attempts - 1:
                self._sleep(self._wait_seconds(r, attempt))
                continue
            if r.status_code in (401, 403):
                raise LLMError("{label}: the API key is invalid or inactive; check the .env file.", label=self.label)
            if r.status_code == 429:
                raise LLMError("{label}: the free usage limit was exceeded. Wait and try again (or grade fewer submissions at once).",
                               label=self.label)
            if r.status_code == 400:
                raise LLMBadRequest("{label} rejected the request: {detail}", label=self.label, detail=r.text[:300])
            if r.status_code >= 400:
                raise LLMError("{label} error {status}: {detail}", label=self.label, status=r.status_code, detail=r.text[:300])
            try:
                return r.json()
            except ValueError as e:
                raise LLMError("{label} returned an unreadable response.", label=self.label) from e
        raise LLMError("{label}: no response received.", label=self.label)  # pragma: no cover


_STOPWORDS = {
    "un", "vai", "kā", "ka", "kas", "tas", "tā", "to", "ir", "uz", "no", "par", "ar", "lai", "kāpēc",
    "kur", "kad", "arī", "bet", "jo", "pie", "pēc", "līdz", "starp", "ko", "kurš", "kura", "kuru",
    "norāda", "paskaidro", "nosauc", "apraksta", "skolēns", "skolēna", "atbilde", "atbildē", "darbs",
    "darbā", "piemēram", "kāda", "kādas", "kādi", "tiek", "var", "nav", "ļoti", "savā", "savu", "šo",
    "tajā", "the", "and", "for", "with",
}


def _stem(word: str) -> str:
    if len(word) > 5:
        word = word[:-2]
    elif len(word) > 3:
        word = word[:-1]
    return word[:6]


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", normalize(text).lower())


def _keywords(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for w in _words(text):
        if len(w) >= 4 and w not in _STOPWORDS:
            result.setdefault(_stem(w), w)
    return result


class MockProvider(Provider):
    name = "mock"

    @property
    def model_label(self) -> str:
        return N_("mock (keyword matching, no AI)")

    def generate(self, system: str, user: str, request: GradingRequest) -> str:
        if self.cfg.mock_delay > 0:
            self._sleep(self.cfg.mock_delay)
        text_stems = {_stem(w) for w in _words(request.student_text)}
        word_count = len(_words(request.student_text))
        length_factor = min(1.0, word_count / 80)

        lang = request.language
        out = []
        for c in request.criteria:
            keywords = _keywords(c.get("expected_answer") or c.get("description") or c["name"])
            found = [w for s, w in keywords.items() if s in text_stems]
            missing = [w for s, w in keywords.items() if s not in text_stems]
            coverage = len(found) / len(keywords) if keywords else 0.5
            ratio = 0.75 * coverage + 0.25 * length_factor
            points = round(ratio * c["max_points"] * 2) / 2
            if request.temperature:
                noise = zlib.crc32(f"{request.student_text}|{c['id']}".encode()) % 8
                if noise < 2:
                    points = min(max(points + (1 if noise else -1), 0), c["max_points"])
            justification = translate("Keywords found: {found}. Missing: {missing}.", lang,
                                      found=", ".join(found[:8]) or translate("none", lang),
                                      missing=", ".join(missing[:8]) or translate("nothing", lang))
            mistakes = ([translate("Missing: {word}", lang, word=w) for w in missing[:3]]
                        if points < c["max_points"] else [])
            improvement = (translate("Add to your answer: {words}.", lang, words=", ".join(missing[:3]))
                           if missing else "")
            out.append({"id": c["id"], "points": points, "justification": justification,
                        "mistakes": mistakes, "improvement": improvement})

        summary = translate("[MOCK mode: not a real AI grade, keyword matching only.] The work has {n} words.",
                            lang, n=word_count)
        return json.dumps({
            "criteria": out,
            "summary": summary,
            "injection_suspected": bool(detect_injection(request.student_text, lang)),
        }, ensure_ascii=False)


class OllamaProvider(Provider):
    name = "ollama"
    label = "Ollama"

    @property
    def model_label(self) -> str:
        return f"ollama:{self.cfg.ollama_model}"

    def generate(self, system: str, user: str, request: GradingRequest) -> str:
        payload = {
            "model": self.cfg.ollama_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": self._temperature(request)},
        }
        if self.cfg.llm_json_mode:
            payload["format"] = "json"
        data = self._post_json(self.cfg.ollama_url.rstrip("/") + "/api/chat", payload)
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMError("{label} returned an unexpected response.", label=self.label) from e


class AnthropicProvider(Provider):
    name = "anthropic"
    label = "Anthropic API"

    @property
    def model_label(self) -> str:
        return f"anthropic:{self.cfg.anthropic_model}"

    def generate(self, system: str, user: str, request: GradingRequest) -> str:
        if not self.cfg.anthropic_api_key:
            raise LLMError("{key} is not set (in the .env file).", key="ANTHROPIC_API_KEY")
        data = self._post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.cfg.anthropic_model,
                "max_tokens": 4000,
                "temperature": self._temperature(request),
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            headers={"x-api-key": self.cfg.anthropic_api_key, "anthropic-version": "2023-06-01"},
        )
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as e:
            raise LLMError("{label} returned an unexpected response.", label=self.label) from e


class OpenAIProvider(Provider):
    name = "openai"
    label = "OpenAI API"
    key_env = "OPENAI_API_KEY"

    def _connection(self) -> tuple[str, str, str]:
        return self.cfg.openai_base_url, self.cfg.openai_api_key, self.cfg.openai_model

    @property
    def model_label(self) -> str:
        return f"{self.name}:{self._connection()[2]}"

    def generate(self, system: str, user: str, request: GradingRequest) -> str:
        base_url, api_key, model = self._connection()
        if not api_key:
            raise LLMError("{key} is not set (in the .env file).", key=self.key_env)
        payload = {
            "model": model,
            "temperature": self._temperature(request),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.cfg.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post_json(base_url.rstrip("/") + "/chat/completions", payload,
                               headers={"Authorization": f"Bearer {api_key}"})
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("{label} returned an unexpected response.", label=self.label) from e


class GroqProvider(OpenAIProvider):
    name = "groq"
    label = "Groq API"
    key_env = "GROQ_API_KEY"

    def _connection(self) -> tuple[str, str, str]:
        return self.cfg.groq_base_url, self.cfg.groq_api_key, self.cfg.groq_model


PROVIDERS = {
    "mock": MockProvider,
    "ollama": OllamaProvider,
    "groq": GroqProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def get_provider(cfg: Settings | None = None) -> Provider:
    cfg = cfg or default_settings
    cls = PROVIDERS.get(cfg.llm_provider)
    if cls is None:
        raise LLMError("Unknown LLM_PROVIDER '{name}'. Options: {options}",
                       name=cfg.llm_provider, options=", ".join(PROVIDERS))
    return cls(cfg)
