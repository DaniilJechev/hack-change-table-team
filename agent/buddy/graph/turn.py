"""FR-AG-15: one readable JSON per turn, built by code from `llm_calls` and the graph state.

The raw journal (`llm_calls`) keeps every request as the model saw it; here the agent input is split
into system prompt, trusted context, history and the current message, and each step lists the tool
calls with their results. Bot-internal fields (`entities`, `brief_id`, button data) stay out (FR-AG-16).
"""

import json
import time

from buddy.agent.loop import MAX_CALLS, MAX_STEPS, RETRY, TRUSTED


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _path(state) -> str:
    if "agent" in state.get("trace", []):
        return "agent"
    if not state.get("text"):
        return "trigger"  # a button or the scheduler gave the decision: no text, no Router
    return "followup" if state.get("followup") else "router"


def _model_input(first: dict) -> dict:
    messages = first["request"]["messages"]
    trusted = messages[1]["content"] if len(messages) > 2 else ""
    return {
        "system_prompt": messages[0]["content"],
        "trusted_context": _json(trusted.removeprefix(TRUSTED)) if trusted.startswith(TRUSTED) else {},
        "history": [{"role": m["role"], "text": m["content"]} for m in messages[2:-1]],
        "current_message": messages[-1]["content"],
        "tools_offered": [tool["function"]["name"] for tool in first["request"].get("tools", [])],
    }


def _steps(chats: list[dict]) -> list[dict]:
    # A tool result reaches the model in the next request as a `tool` message with the call id.
    results = {
        m["tool_call_id"]: _json(m["content"])
        for chat in chats
        for m in chat["request"]["messages"]
        if m.get("role") == "tool"
    }
    steps = []
    for number, chat in enumerate(chats, 1):
        reply = chat["response"] or {}
        calls = []
        for call in reply.get("tool_calls", []):
            result = results.get(call["id"])
            observed = result if isinstance(result, dict) else {"result": result}
            calls.append(
                {
                    "tool": call["name"],
                    "arguments": _json(call["arguments"]),
                    "result": observed.get("result"),
                    "sources": [source["id"] for source in observed.get("sources", [])],
                }
            )
        step = {"step": number, "model_text": reply.get("content", ""), "tool_calls": calls}
        if chat["error"]:
            step["error"] = chat["error"]
        steps.append(step)
    return steps


def describe(state) -> dict:
    calls = state.get("llm_calls", [])
    chats = [call for call in calls if call["node"] == "agent" and call["kind"] == "chat"]
    path = _path(state)
    decision, response, context = state.get("decision"), state.get("response"), state.get("context")
    turn = {
        "request": {"text": state.get("text", ""), "role": getattr(context, "role", ""), "path": path},
        "case": getattr(decision, "case", ""),
        "fallback": bool(chats) and path != "agent",
    }
    if chats:
        steps = _steps(chats)
        turn |= {
            "model_input": _model_input(chats[0]),
            "steps": steps,
            "forced_refresh": any(
                m.get("role") == "user" and m.get("content") == RETRY
                for chat in chats
                for m in chat["request"]["messages"]
            ),
        }
    else:
        steps = []
    turn["other_llm_calls"] = [
        {
            "node": call["node"],
            "kind": call["kind"],
            "prompt": call["request"].get("prompt", call["request"].get("messages")),
            "answer": call["response"],
            **({"error": call["error"]} if call["error"] else {}),
        }
        for call in calls
        if call not in chats
    ]
    if response is not None:
        turn["answer"] = {
            "text": response.result,
            "sources": [{"n": n, "url": url} for n, url in enumerate(response.links, 1)],
            "buttons": [action.label for action in response.actions],
            **({"more_blocks": response.folded} if response.folded else {}),
        }
    turn["limits"] = {
        "model_calls": len(chats),
        "max_model_calls": MAX_STEPS,
        "tool_calls": sum(len(step["tool_calls"]) for step in steps),
        "max_tool_calls": MAX_CALLS,
    }
    if state.get("started"):
        turn["duration_ms"] = round((time.monotonic() - state["started"]) * 1000)
    return turn
