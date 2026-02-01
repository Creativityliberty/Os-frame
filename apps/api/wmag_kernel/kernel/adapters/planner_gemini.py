from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from kernel.ports.planner import LLMPlanner

# Google GenAI SDK (Gemini Developer API / Vertex AI)
# Docs: https://ai.google.dev/gemini-api/docs/quickstart
from google import genai


class GeminiPlanner(LLMPlanner):
    """LLMPlanner backed by Gemini via Google GenAI SDK.

    Usage accounting:
      - After each call, `self.last_usage` is populated with whatever the SDK exposes.
      - If token usage is unavailable, we provide an estimate based on char length.
    """

    last_usage: Dict[str, Any] = {}

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
        self.client = genai.Client()

    def _extract_usage(self, res: Any, prompt_text: str, response_text: str) -> Dict[str, Any]:
        usage: Dict[str, Any] = {}
        um = getattr(res, "usage_metadata", None)
        if um is not None:
            for k in [
                "prompt_token_count",
                "candidates_token_count",
                "total_token_count",
                "cached_content_token_count",
            ]:
                v = getattr(um, k, None)
                if v is not None:
                    usage[k] = int(v)
        usage["prompt_chars"] = len(prompt_text or "")
        usage["response_chars"] = len(response_text or "")
        est_tokens = int((usage["prompt_chars"] + usage["response_chars"]) / 4) if (usage["prompt_chars"] + usage["response_chars"]) else 0
        usage["estimated_total_tokens"] = est_tokens
        return usage

    async def select_nodes(
        self,
        user_message: str,
        trees: List[Dict[str, Any]],
        policy_snippets: List[Dict[str, Any]],
    ) -> List[str]:
        prompt_text = (
            "You are a context router. "
            "Pick up to 8 node paths that are most relevant to answer the user. "
            "Return ONLY a JSON array of strings (node paths).\n\n"
            f"USER_MESSAGE:\n{user_message}\n\n"
            f"TREES (json):\n{json.dumps(trees, ensure_ascii=False)}\n\n"
            f"POLICY_SNIPPETS (json):\n{json.dumps(policy_snippets, ensure_ascii=False)}\n"
        )
        prompt = {"role": "user", "parts": [{"text": prompt_text}]}

        res = self.client.models.generate_content(
            model=self.model,
            contents=[prompt],
            config={"response_mime_type": "application/json"},
        )
        txt = getattr(res, "text", None) or str(res)
        self.last_usage = self._extract_usage(res, prompt_text, txt)

        try:
            arr = json.loads(txt)
            if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                return arr[:8]
        except Exception:
            pass
        return ["SUPPORT/KB/Refunds", "SUPPORT/PLAYBOOKS/RefundFlow"]

    async def build_plan(self, context_pack: Dict[str, Any]) -> Dict[str, Any]:
        action_space = context_pack.get("action_space", [])
        policies = context_pack.get("policies", [])
        user_message = context_pack.get("task", {}).get("user_message", "")

        system = (
            "You are an orchestration planner. "
            "You must output a WMAG Plan as STRICT JSON (no markdown, no prose). "
            "Use ONLY action_id values provided in ACTION_SPACE. "
            "Every side-effect action (email/send, ticket/create, etc.) MUST include args.idempotency_key. "
            "Keep steps minimal and deterministic."
        )

        prompt_text = (
            f"{system}\n\n"
            "OUTPUT SHAPE (high level):\n"
            "{\n"
            '  "type": "plan",\n'
            '  "goal": "...",\n'
            '  "controls": {"requires_approval": bool, "max_tool_calls": int, "allowed_tools": [..]},\n'
            '  "steps": [ {"step_id":"s1","action_id":"...","args":{...},"depends_on":[..]?}, ... ]\n'
            "}\n\n"
            f"USER_MESSAGE:\n{user_message}\n\n"
            f"CONTEXT_PACK (json):\n{json.dumps(context_pack, ensure_ascii=False)}\n\n"
            f"POLICIES (json):\n{json.dumps(policies, ensure_ascii=False)}\n\n"
            f"ACTION_SPACE (json):\n{json.dumps(action_space, ensure_ascii=False)}\n"
        )
        user = {"role": "user", "parts": [{"text": prompt_text}]}

        res = self.client.models.generate_content(
            model=self.model,
            contents=[user],
            config={"response_mime_type": "application/json"},
        )
        txt = getattr(res, "text", None) or str(res)
        self.last_usage = self._extract_usage(res, prompt_text, txt)

        try:
            plan = json.loads(txt)
            if isinstance(plan, dict) and plan.get("type") == "plan":
                return plan
        except Exception:
            pass

        return {
            "type": "plan",
            "goal": "Fallback: draft a safe response",
            "controls": {
                "requires_approval": False,
                "max_tool_calls": 3,
                "allowed_tools": ["internal.llm.draft_reply"],
            },
            "steps": [
                {
                    "step_id": "s1",
                    "action_id": "act_draft_reply_v1",
                    "args": {
                        "language": "fr-FR",
                        "tone": "support_pro",
                        "facts": {},
                        "policy_snippets": [],
                    },
                }
            ],
        }
