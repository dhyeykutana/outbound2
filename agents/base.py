"""Base class for all Calyxr agents."""

import json
import logging
import re
import anthropic

log = logging.getLogger("calyxr")


class BaseAgent:
    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model  = model

    def _call(self, system: str, user: str, max_tokens: int = 600) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}]
        )
        return response.content[0].text.strip()

    def _parse_json(self, raw: str) -> dict | None:
        """Strip code fences, extract first JSON object, and parse. Returns None on failure."""
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        # Find the first { ... } block using brace matching
        start = raw.find("{")
        if start != -1:
            raw = raw[start:]
            depth, end = 0, -1
            in_str, escape = False, False
            for i, ch in enumerate(raw):
                if escape:
                    escape = False; continue
                if ch == "\\" and in_str:
                    escape = True; continue
                if ch == '"':
                    in_str = not in_str; continue
                if not in_str:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i; break
            if end != -1:
                raw = raw[:end + 1]

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _call_json(self, system: str, user: str, max_tokens: int = 600) -> dict:
        """Call API expecting a JSON response. Retries once on parse failure."""
        system_with_json = (
            system
            + "\n\nIMPORTANT: Respond ONLY with a single valid JSON object. "
            "No markdown code fences, no backticks, no extra text before or after the JSON."
        )

        raw = ""
        for attempt in range(2):
            raw = self._call(system_with_json, user, max_tokens)
            result = self._parse_json(raw)
            if result is not None:
                return result
            log.warning(f"[BASE] JSON parse attempt {attempt + 1}/2 failed. Raw (first 200): {raw[:200]}")

        # Both attempts failed — return raw so callers can detect and handle
        log.error(f"[BASE] Both JSON parse attempts failed — returning raw fallback. Raw: {raw[:500]}")
        return {"raw": raw}
