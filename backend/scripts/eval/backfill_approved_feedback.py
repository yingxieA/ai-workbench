# -*- coding: utf-8 -*-
"""B：approved 反馈回填 dataset.json
从 /api/feedbacks/export?review_status=approved 拉取真实用户问题（已成对：question+answer+route），
映射为评测用例追加到 dataset.json，实现"在线反馈 → 回归用例"闭环。
关键词维度留空（真实问题不强制关键词），主指标 = 路由正确性（route_ok）。
"""

import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
DATASET = r"D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\dataset.json"


def _req(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


# 1) 登录拿 token
tok = json.loads(_req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"}))["token"]

# 2) 拉取 approved 反馈（JSONL）
raw = _req("GET", "/api/feedbacks/export?review_status=approved", token=tok)
rows = [json.loads(line) for line in raw.strip().splitlines() if line.strip()]
print(f"approved 反馈: {len(rows)} 条")


# 3) route -> category 映射（无上下文字段，按路由归入最贴近的类别）
def to_category(route: str) -> str:
    m = {
        "rag": "rag_factual",
        "tool": "tool_calling",
        "agent_loop": "agent_loop",
        "direct": "chitchat",
    }
    return m.get(route, "chitchat")


# 4) 追加（按 question 去重）
cases = json.load(open(DATASET, encoding="utf-8"))
seen_questions = {c["query"].strip() for c in cases}
added, skipped = 0, 0
idx = 0
for r in rows:
    q = (r.get("question") or "").strip()
    if not q or q in seen_questions:
        skipped += 1
        continue
    route = r.get("route") or ""
    case = {
        "id": f"fb_{idx:03d}",
        "category": to_category(route),
        "query": q,
        "expected_route": route or "direct",
        "expected_keywords": [],
        "ground_truth_contexts": [],
        "notes": f"真实用户反馈回填(approved): feedback={r.get('feedback')}, reason={r.get('reason', '')[:60]}, model={r.get('model') or ''}",
    }
    cases.append(case)
    seen_questions.add(q)
    added += 1
    idx += 1

json.dump(cases, open(DATASET, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"回填完成: 新增 {added} 条（去重跳过 {skipped} 条），dataset.json 现有 {len(cases)} 条")
