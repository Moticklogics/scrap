"""Optional local AI classifier for ambiguous layouts (experimental).

The classifier only *chooses* among DOM candidates that were already found and validated by the
extraction engine. It never generates or edits post text: the text written to the output always comes
from the selected DOM element. It talks to a local Ollama server (http://127.0.0.1:11434 by default);
no data leaves the machine.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request

log = logging.getLogger("forum_extractor.classifier")

PROMPT = """You are helping a forum scraper decide which DOM element holds the ORIGINAL POST (the first post
that started the discussion thread), as opposed to replies, quotes, signatures or navigation.
Answer ONLY with JSON: {{"index": <candidate number>, "reason": "<short explanation>"}}.
If none of the candidates is the original post, answer {{"index": -1, "reason": "..."}}.

Page title: {title}

Candidates:
{candidates}
"""


class OllamaClassifier:
    def __init__(self, model: str = "llama3.1", endpoint: str = "http://127.0.0.1:11434", timeout: float = 60):
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.title = ""

    def describe(self, candidates) -> str:
        lines = []
        for i, c in enumerate(candidates):
            others = len(c.others)
            preview = re.sub(r"\s+", " ", c.text)[:400]
            lines.append(
                f"[{i}] method={c.method} selector={c.container_selector!r} position=first-of-{others + 1} "
                f"posts warnings={c.warnings} text_preview={preview!r}")
        return "\n".join(lines)

    def choose(self, candidates) -> tuple[int, str] | None:
        prompt = PROMPT.format(title=self.title, candidates=self.describe(candidates))
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False, "format": "json",
                           "options": {"temperature": 0}}).encode()
        req = urllib.request.Request(self.endpoint + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        answer = json.loads(data.get("response", "{}"))
        idx = int(answer.get("index", -1))
        reason = str(answer.get("reason", ""))[:500]
        if idx < 0 or idx >= len(candidates):
            log.info("AI classifier declined: %s", reason)
            return None
        return idx, reason


def make_classifier(kind: str, model: str, endpoint: str):
    if kind == "ollama":
        return OllamaClassifier(model=model, endpoint=endpoint)
    return None
