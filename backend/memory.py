"""
ContextLayer — Memory Module

Custom SQLite-based knowledge graph for cross-session memory.
Stores facts extracted from conversations and recalls them via keyword search.
"""

import os
import re
import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# ---------------------------------------------------------------------------
# Database path
# ---------------------------------------------------------------------------

_DEFAULT_DB_DIR = os.path.join(os.path.expanduser("~"), ".contextlayer")
DB_PATH = os.path.join(_DEFAULT_DB_DIR, "memory.db")


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    """Return the configured DB path or the default local memory DB."""
    return db_path or DB_PATH

# ---------------------------------------------------------------------------
# Database init
# ---------------------------------------------------------------------------


def init_db(db_path: Optional[str] = None) -> None:
    """Create the memory database and tables if they don't exist."""
    path = _resolve_db_path(db_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            source_llm TEXT DEFAULT 'unknown',
            message_count INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity TEXT NOT NULL,
            relation TEXT NOT NULL,
            target TEXT NOT NULL,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            source_llm TEXT DEFAULT 'unknown',
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fact extraction (rule-based)
# ---------------------------------------------------------------------------

# Simple patterns to extract entity-relation-target triples
_PATTERNS = [
    # "X is Y" / "X are Y"
    re.compile(r"(?:^|\.\s+)([A-Z][A-Za-z0-9_\-\.]+)\s+(?:is|are)\s+(.+?)(?:\.|$)", re.MULTILINE),
    # "X uses Y" / "X using Y"
    re.compile(r"([A-Z][A-Za-z0-9_\-\.]+)\s+(?:uses?|using)\s+([A-Za-z0-9_\-\.]+)", re.MULTILINE),
    # "X built with Y" / "X written in Y"
    re.compile(r"([A-Z][A-Za-z0-9_\-\.]+)\s+(?:built with|written in|made with)\s+([A-Za-z0-9_\-\.]+)", re.MULTILINE),
    # "use X for Y"
    re.compile(r"use\s+([A-Za-z0-9_\-\.]+)\s+for\s+(.+?)(?:\.|$)", re.IGNORECASE | re.MULTILINE),
]


def _extract_facts_rule_based(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Extract entity-relation-target triples from conversation messages."""
    facts = []
    seen = set()

    for msg in messages:
        content = msg.get("content", "")
        for pattern in _PATTERNS:
            for match in pattern.finditer(content):
                groups = match.groups()
                if len(groups) >= 2:
                    entity = groups[0].strip()[:100]
                    target = groups[1].strip()[:200]
                    # Determine relation from the pattern
                    matched_text = match.group(0).lower()
                    if "uses" in matched_text or "using" in matched_text:
                        relation = "uses"
                    elif "built with" in matched_text or "written in" in matched_text or "made with" in matched_text:
                        relation = "built_with"
                    elif "use" in matched_text and "for" in matched_text:
                        relation = "used_for"
                    else:
                        relation = "is"

                    key = (entity.lower(), relation, target.lower())
                    if key not in seen:
                        seen.add(key)
                        facts.append({
                            "entity": entity,
                            "relation": relation,
                            "target": target,
                        })

    # Also extract simple keyword-based facts from user messages
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            # Extract mentions of technologies, tools, etc.
            tech_pattern = re.compile(r"\b(FastAPI|React|Python|JavaScript|TypeScript|Docker|"
                                      r"PostgreSQL|SQLite|Redis|MongoDB|GraphQL|REST|"
                                      r"LLMLingua|Ollama|tiktoken|ChromeExtension|"
                                      r"Node\.js|Express|Flask|Django)\b", re.IGNORECASE)
            for match in tech_pattern.finditer(content):
                entity = match.group(1)
                key = (entity.lower(), "mentioned_in", "conversation")
                if key not in seen:
                    seen.add(key)
                    facts.append({
                        "entity": entity,
                        "relation": "mentioned_in",
                        "target": "conversation",
                    })

    return facts


# ---------------------------------------------------------------------------
# Store session
# ---------------------------------------------------------------------------


def store_session(
    session_id: str,
    messages: List[Dict[str, str]],
    source_llm: str = "unknown",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Store a conversation session and extract facts.

    Returns a dict with ``session_id``, ``facts_stored``, ``message_count``.
    """
    path = _resolve_db_path(db_path)
    now = datetime.now(timezone.utc).isoformat()

    # Extract facts
    facts = _extract_facts_rule_based(messages)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    # Upsert session
    cursor.execute(
        """INSERT OR REPLACE INTO sessions (session_id, timestamp, source_llm, message_count)
           VALUES (?, ?, ?, ?)""",
        (session_id, now, source_llm, len(messages)),
    )

    # Insert facts
    for fact in facts:
        cursor.execute(
            """INSERT INTO facts (entity, relation, target, session_id, timestamp, source_llm)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fact["entity"], fact["relation"], fact["target"], session_id, now, source_llm),
        )

    conn.commit()
    conn.close()

    return {
        "session_id": session_id,
        "facts_stored": len(facts),
        "message_count": len(messages),
    }


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


def recall(
    query: str,
    top_k: int = 5,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Recall facts relevant to *query* via keyword search.

    Returns a dict with ``relevant_context`` (formatted string),
    ``facts`` (list of dicts), ``query``.
    """
    path = _resolve_db_path(db_path)

    if not os.path.exists(path):
        return {"relevant_context": "", "facts": [], "query": query}

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    # Split query into keywords
    keywords = [kw.strip().lower() for kw in query.split() if len(kw.strip()) > 2]

    if not keywords:
        conn.close()
        return {"relevant_context": "", "facts": [], "query": query}

    # Build a WHERE clause matching any keyword in entity or target
    conditions = []
    params = []
    for kw in keywords:
        conditions.append("(LOWER(entity) LIKE ? OR LOWER(target) LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%"])

    where_clause = " OR ".join(conditions)

    cursor.execute(
        f"""SELECT entity, relation, target, session_id, timestamp, source_llm
            FROM facts
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?""",
        params + [top_k],
    )

    rows = cursor.fetchall()
    conn.close()

    facts = [
        {
            "entity": row[0],
            "relation": row[1],
            "target": row[2],
            "session_id": row[3],
            "timestamp": row[4],
            "source_llm": row[5],
        }
        for row in rows
    ]

    # Format context string
    if facts:
        lines = []
        for f in facts:
            lines.append(f"- {f['entity']} {f['relation']} {f['target']} (from session {f['session_id']})")
        relevant_context = "\n".join(lines)
    else:
        relevant_context = ""

    return {
        "relevant_context": relevant_context,
        "facts": facts,
        "query": query,
    }


# ---------------------------------------------------------------------------
# Delete memory
# ---------------------------------------------------------------------------


def delete_memory(db_path: Optional[str] = None) -> Dict[str, str]:
    """Clear all stored memory by dropping and recreating tables."""
    path = _resolve_db_path(db_path)

    if not os.path.exists(path):
        return {"status": "no database found"}

    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS facts")
    cursor.execute("DROP TABLE IF EXISTS sessions")
    conn.commit()
    conn.close()

    # Recreate tables
    init_db(db_path=path)

    return {"status": "memory cleared"}
