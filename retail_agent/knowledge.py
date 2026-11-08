import json
import re
from pathlib import Path


def retrieve(path: Path, question: str, allowed: list[int], k=2):
    """Small, curated lexical retriever. Production uses ACL-filtered hybrid search."""
    documents = json.loads(path.read_text(encoding="utf-8"))
    terms = set(re.findall(r"[a-z]+", question.lower()))
    scored = []
    for doc in documents:
        if doc.get("status") != "approved" or doc.get("schema_version") != "scoped-item-v1":
            continue
        scope = doc.get("product_ids", [])
        if scope and not set(scope).issubset(allowed):
            continue
        score = len(terms & set(re.findall(r"[a-z]+", doc["question"].lower())))
        if score:
            scored.append((score, doc))
    return [doc for _, doc in sorted(scored, key=lambda x: (-x[0], x[1]["id"]))[:k]]
