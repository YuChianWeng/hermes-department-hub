"""Deterministic synthetic model for the live isolated lab.

This is NOT a real LLM and performs no reasoning. It maps recognized wake text
to fixed tool calls so the REAL Hermes gateway/profile/tool path is exercised
without provider network or spend. Every response is labeled synthetic in the
evidence log.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
LOG = LAB / "evidence" / "model.jsonl"
LOCK = threading.Lock()


def _record(rec: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        with LOG.open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, data: dict) -> None:
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "proto-synthetic",
                                                "object": "model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        messages = body.get("messages", [])
        user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                user = content if isinstance(content, str) else json.dumps(content)
                break
        last_is_tool = bool(messages and messages[-1].get("role") == "tool")
        kind = "final_after_tools" if last_is_tool else "plain"
        task = None
        m = re.search(r"\b(t_[0-9a-f]{6,})\b", user)
        if m:
            task = m.group(1)
        tool_calls = []
        if not last_is_tool and task:
            review = re.search(r"REVIEW (PASS|REQUEST_CHANGES) sha=([0-9a-f]{64})", user)
            accept = re.search(r"ACCEPT PASS sha=([0-9a-f]{64})", user)
            work = re.search(r"WORK DONE sha=([0-9a-f]{64})", user)
            if review:
                kind = "review_verdict"
                verdict, sha = review.group(1), review.group(2)
                payload = json.dumps({
                    "prototype": "review-verdict",
                    "verdict": verdict,
                    "artifact_sha": sha,
                    "review_task": task,
                    "synthetic_model": True,
                })
                tool_calls = [
                    ("kanban_show", {"task_id": task}),
                    ("kanban_comment", {"task_id": task, "body": payload}),
                ]
            elif accept:
                kind = "manager_acceptance"
                sha = accept.group(1)
                payload = json.dumps({
                    "prototype": "manager-acceptance",
                    "decision": "ACCEPT",
                    "artifact_sha": sha,
                    "synthetic_model": True,
                })
                tool_calls = [
                    ("kanban_show", {"task_id": task}),
                    ("kanban_comment", {"task_id": task, "body": payload}),
                ]
            elif work:
                kind = "work_receipt"
                tool_calls = [
                    ("kanban_show", {"task_id": task}),
                    ("kanban_comment", {"task_id": task,
                                        "body": "SYNTHETIC_ROUTE_RECEIPT " + task}),
                ]
            elif re.search(rf"Task {re.escape(task)} (?:completed|blocked)", user):
                kind = "wake_receipt"
                tool_calls = [
                    ("kanban_show", {"task_id": task}),
                    ("kanban_comment", {"task_id": task,
                                        "body": "SYNTHETIC_ROUTE_RECEIPT " + task}),
                ]
        _record({
            "time": time.time(),
            "kind": kind,
            "model": body.get("model"),
            "user_excerpt": user[:400],
            "tools": [t.get("function", {}).get("name") for t in body.get("tools", [])],
            "tool_calls": tool_calls,
            "tool_results": [m for m in messages if m.get("role") == "tool"][-3:],
            "synthetic": True,
        })
        message = {"role": "assistant", "content": "SYNTHETIC_ACK"}
        finish = "stop"
        if tool_calls:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": f"call_{time.time_ns()}_{i}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(tool_calls)
                ],
            }
            finish = "tool_calls"
        self._send({
            "id": "chatcmpl-synthetic",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "proto-synthetic"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18251), Handler).serve_forever()
