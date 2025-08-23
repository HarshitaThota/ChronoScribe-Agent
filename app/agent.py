"""
ChronoScribeAgent — a minimal, framework-free Agent:
- Policy/persona     -> SYSTEM PROMPT
- Planning           -> prompt builder + presets
- Tool use           -> function-calling loop (wiki summary, timeline anchors)
- Post-processing    -> output normalization & probability renorm

Back-compat:
- `generate_simulation` and `generate_simulation_simple` still exist and
  delegate to a module-level singleton `agent` so you don't need to touch main.py.
"""

import os
import re
import json
import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from openai import OpenAI
import requests

from app.schemas import SimulationRequest, SimpleSimulationRequest


class ChronoScribeAgent:
    """Lightweight agent that plans, calls tools, and returns structured JSON."""

    def __init__(
        self,
        model: Optional[str] = None,
        tools_enabled: Optional[bool] = None,
        current_year: Optional[int] = None,
        client: Optional[OpenAI] = None,
        name: str = "ChronoScribe Agent",
    ):
        self.name = name
        self.client = client or OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if tools_enabled is None:
            tools_enabled = os.getenv("TOOLS_ENABLED", "1").lower() not in ("0", "false", "no")
        self.tools_enabled = tools_enabled
        if current_year is None:
            current_year = int(os.getenv("CURRENT_YEAR", datetime.datetime.utcnow().year))
        self.current_year = current_year

    # ---------- Policy / System Prompt ----------
    def _system_prompt(self) -> str:
        return f"""
You are {self.name} — a what-if simulation agent.
Current year is {self.current_year}.
You may call tools to improve realism: use make_timeline_anchors to set consistent time labels,
and wiki_summary to ground assumptions. Respond ONLY with a single json object. No prose, no
code fences, no markdown.

The json MUST match this shape exactly:
{{
  "premise": "...",
  "assumptions": ["..."],
  "time_horizon": "...",
  "scenarios": [
    {{
      "name": "Baseline",
      "probability": 0.5,
      "summary": "...",
      "timeline": [
        {{"year_or_period": "T+1y", "event": "...", "rationale": "..."}},
        {{"year_or_period": "T+5y", "event": "...", "rationale": "..."}}
      ],
      "second_order_effects": ["..."]
    }},
    {{
      "name": "Best Case",
      "probability": 0.25,
      "summary": "...",
      "timeline": [],
      "second_order_effects": []
    }},
    {{
      "name": "Worst Case",
      "probability": 0.25,
      "summary": "...",
      "timeline": [],
      "second_order_effects": []
    }}
  ],
  "tradeoffs": ["..."],
  "red_team": ["Key uncertainties or failure modes..."],
  "speculative_realism_score": 0.0,
  "style": "brief|cinematic|bullet|paper",
  "disclaimer": "Short reminder that this is speculative."
}}

Guidelines:
- Prefer calling tools early to get anchors and a brief background.
- Keep it concise and information-dense.
- Ensure scenario probabilities sum to ~1.0.
- Use realistic causal chains; avoid impossibilities.
- Output must be valid json and ONLY a json object.
"""

    # ---------- Tools (environment actions) ----------
    @staticmethod
    def make_timeline_anchors(start_year: int, horizon: str, intervals: Optional[List[int]] = None) -> Dict[str, Any]:
        """Compute T+Ny labels and absolute years from a horizon like '5y', '25y', '50y'."""
        m = re.match(r"^\s*(\d+)\s*y\s*$", horizon.lower())
        total = int(m.group(1)) if m else 50
        if not intervals:
            if total <= 10:
                intervals = [1, 3, 5, 10]
            elif total <= 25:
                intervals = [1, 5, 10, 25]
            else:
                intervals = [1, 5, 10, 25, 50]
        anchors = []
        for n in intervals:
            if n <= total:
                anchors.append({"label": f"T+{n}y", "year": start_year + n})
        return {"start_year": start_year, "horizon_years": total, "anchors": anchors}

    @staticmethod
    def wiki_summary(topic: str, sentences: int = 3) -> Dict[str, Any]:
        """Fetch a short neutral background summary from Wikipedia REST API (best-effort)."""
        try:
            t = topic.strip().strip("?")
            t = re.sub(r"^what if\s+", "", t, flags=re.I)
            t = t.replace(" ", "_")
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}"
            r = requests.get(url, timeout=5)
            if r.status_code != 200:
                return {"topic": topic, "ok": False, "summary": "", "source": url, "status": r.status_code}
            data = r.json()
            text = data.get("extract", "")
            parts = re.split(r"(?<=[.!?])\s+", text)
            short = " ".join(parts[: max(1, sentences)])
            return {"topic": topic, "ok": True, "summary": short, "source": url}
        except Exception as e:
            return {"topic": topic, "ok": False, "summary": "", "error": str(e)}

    def _openai_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "make_timeline_anchors",
                    "description": "Compute timeline anchors (T+Ny) and absolute years for the given horizon.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_year": {"type": "integer"},
                            "horizon": {"type": "string", "description": "e.g., '5y', '25y', '50y'"},
                            "intervals": {"type": "array", "items": {"type": "integer"}},
                        },
                        "required": ["start_year", "horizon"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "wiki_summary",
                    "description": "Fetch a short neutral background summary from Wikipedia given a topic.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "sentences": {"type": "integer", "default": 3},
                        },
                        "required": ["topic"],
                    },
                },
            },
        ]

    # ---------- Planning ----------
    def _build_user_prompt(self, req: SimulationRequest) -> str:
        parts = [
            f"Premise: {req.what_if}",
            f"Scope: {req.scope}",
            f"Time Horizon: {req.time_horizon}",
            f"Style: {req.style}",
            f"Current year: {self.current_year}",
        ]
        if req.constraints:
            parts.append("Constraints:\n- " + "\n- ".join(req.constraints))
        parts.append("Return only a json object as specified above.")
        return "\n".join(parts)

    # ---------- Tool execution & model loop ----------
    def _run_tool_call(self, name: str, arguments_json: str) -> str:
        try:
            args = json.loads(arguments_json or "{}")
        except Exception:
            args = {}

        if name == "make_timeline_anchors":
            res = self.make_timeline_anchors(**args)
            return json.dumps(res)
        if name == "wiki_summary":
            res = self.wiki_summary(**args)
            return json.dumps(res)
        return json.dumps({"error": f"Unknown tool {name}"})

    def _call_openai_json(self, prompt: str, temperature: float) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": prompt},
        ]
        tools = self._openai_tools() if self.tools_enabled else None

        # small bounded tool loop
        for _ in range(4):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto" if tools else "none",
                )
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"LLM error: {e}")

            msg = resp.choices[0].message

            # If the assistant requested tools, we must append that assistant message
            # (with its tool_calls) BEFORE we append any tool results.
            if getattr(msg, "tool_calls", None):
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Now run each tool and append its result
                for tc in msg.tool_calls:
                    tool_out = self._run_tool_call(tc.function.name, tc.function.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": tool_out,
                    })
                continue  # let the model read tool outputs next round

            # No tool calls -> should be final JSON
            try:
                return json.loads(msg.content or "")
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"LLM JSON parse error: {e}")

        raise HTTPException(status_code=502, detail="LLM/tool loop did not produce final JSON")

        # ---------- Public API ----------
    def simulate(self, req: SimulationRequest) -> Dict[str, Any]:
            data = self._call_openai_json(self._build_user_prompt(req), temperature=req.temperature)

            # normalization / guards
            data.setdefault("premise", req.what_if)
            data.setdefault("time_horizon", req.time_horizon)
            data.setdefault("style", req.style or "brief")
            data.setdefault("disclaimer", "Speculative scenario generation; not factual prediction.")
            for key in ["assumptions", "scenarios", "tradeoffs", "red_team"]:
                data.setdefault(key, [])

            if isinstance(data.get("scenarios"), list):
                probs = [s.get("probability", 0) for s in data["scenarios"] if isinstance(s, dict)]
                total = sum(p for p in probs if isinstance(p, (int, float)))
                if total and abs(total - 1.0) > 0.05:
                    data["scenarios"] = [
                        {**s, "probability": (s.get("probability", 0) / total) if total else 0}
                        for s in data["scenarios"]
                    ]
            return data

        # ----- Simple input path -----
    _HORIZON = {"short": "5y", "medium": "25y", "long": "50y"}

    @staticmethod
    def _preset_config(preset: str) -> Dict[str, Any]:
            if preset == "cinematic":
                return {"style": "cinematic", "temperature": 0.9}
            if preset == "academic":
                return {"style": "paper", "temperature": 0.4}
            if preset == "risk":
                return {"style": "bullet", "temperature": 0.6, "constraints": ["call out major risks explicitly"]}
            if preset == "optimistic":
                return {"style": "brief", "temperature": 0.8}
            if preset == "pessimistic":
                return {"style": "brief", "temperature": 0.6}
            return {"style": "brief", "temperature": 0.7}

    def simulate_simple(self, s: SimpleSimulationRequest) -> Dict[str, Any]:
            cfg = self._preset_config(s.preset)
            req = SimulationRequest(
                what_if=s.what_if,
                time_horizon=self._HORIZON[s.horizon],
                scope=s.focus,
                style=cfg["style"],
                constraints=cfg.get("constraints"),
                temperature=cfg["temperature"],
            )
            return self.simulate(req)


agent = ChronoScribeAgent()

def generate_simulation(req: SimulationRequest) -> Dict[str, Any]:
    """Backward-compatible wrapper for existing main.py code."""
    return agent.simulate(req)

def generate_simulation_simple(s: SimpleSimulationRequest) -> Dict[str, Any]:
    """Backward-compatible wrapper for existing main.py code."""
    return agent.simulate_simple(s)
