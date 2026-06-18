#!/usr/bin/env python
"""
History Manager — Python + SQLite backend for kaoyan-writing-coach.

Replaces the JSON-oriented profile management in SKILL.md Module 7 with
a structured SQLite database and CLI tool. The AI agent calls these commands;
all data logic (error_stats, zero_streak, graduation, wrong_words upsert)
lives inside this tool.

Usage:
    python scripts/history_mgr.py init
    python scripts/history_mgr.py config get --json
    python scripts/history_mgr.py question add '<json>'
    python scripts/history_mgr.py session add '<json>'
    python scripts/history_mgr.py review by-date --date 2026-06-17 --json

Exit codes: 0=success, 1=validation/input error, 2=DB error.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ──────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_DB = _SCRIPT_DIR.parent / "data" / "training.db"


# ── DB Helpers ─────────────────────────────────────

def _connect(db_path=None):
    path = str(db_path or _DEFAULT_DB)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_db(conn):
    """Create schema if it doesn't exist. Idempotent."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        level TEXT NOT NULL DEFAULT '约9分',
        target TEXT NOT NULL DEFAULT '20分',
        created TEXT NOT NULL DEFAULT (date('now')),
        updated TEXT NOT NULL DEFAULT (date('now')),
        difficulty_sentence TEXT NOT NULL DEFAULT '低',
        difficulty_vocab TEXT NOT NULL DEFAULT '低',
        difficulty_logic TEXT NOT NULL DEFAULT '低',
        topic_current TEXT NOT NULL DEFAULT 'T1',
        topic_rotation_next TEXT NOT NULL DEFAULT 'T2',
        topic_history TEXT NOT NULL DEFAULT '["T1"]',
        training_mode TEXT NOT NULL DEFAULT '实战',
        training_target_code TEXT,
        training_pending_realcombat INTEGER NOT NULL DEFAULT 0,
        training_graduated_list TEXT NOT NULL DEFAULT '[]',
        training_deep_dive_category TEXT,
        training_deep_dive_step TEXT,
        training_deep_dive_error_index INTEGER NOT NULL DEFAULT 0,
        severity_overrides TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS daily_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        cn_prompt TEXT NOT NULL,
        reference_answer TEXT,
        source_sentence_id TEXT,
        topic TEXT NOT NULL,
        training_stage TEXT,
        session_id INTEGER REFERENCES session_log(id),
        is_wrong INTEGER NOT NULL DEFAULT 0,
        error_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_dq_date ON daily_questions(date);
    CREATE INDEX IF NOT EXISTS idx_dq_wrong ON daily_questions(is_wrong);

    CREATE TABLE IF NOT EXISTS session_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        topic TEXT NOT NULL,
        fatal INTEGER NOT NULL DEFAULT 0,
        general INTEGER NOT NULL DEFAULT 0,
        optimize INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        source_sentence_id TEXT,
        micro_target TEXT,
        micro_target_passed TEXT DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_sl_date ON session_log(date);

    CREATE TABLE IF NOT EXISTS session_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        error_code TEXT NOT NULL,
        severity TEXT NOT NULL,
        wrong_word TEXT,
        correction TEXT,
        FOREIGN KEY (session_id) REFERENCES session_log(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_se_session ON session_errors(session_id);
    CREATE INDEX IF NOT EXISTS idx_se_code ON session_errors(error_code);

    CREATE TABLE IF NOT EXISTS error_stats (
        error_code TEXT PRIMARY KEY,
        total INTEGER NOT NULL DEFAULT 0,
        last_seen TEXT,
        recent10 TEXT NOT NULL DEFAULT '[]',
        zero_streak INTEGER NOT NULL DEFAULT 0,
        graduated INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (date('now'))
    );

    CREATE TABLE IF NOT EXISTS wrong_words (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        error_code TEXT NOT NULL,
        word TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 1,
        last_seen TEXT NOT NULL,
        correction TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (date('now')),
        FOREIGN KEY (error_code) REFERENCES error_stats(error_code)
    );
    CREATE INDEX IF NOT EXISTS idx_ww_code ON wrong_words(error_code);
    CREATE INDEX IF NOT EXISTS idx_ww_word ON wrong_words(word);
    """)


def _ensure_config_row(conn):
    """Ensure the singleton config row exists."""
    cur = conn.execute("SELECT count(*) FROM config WHERE id = 1")
    if cur.fetchone()[0] == 0:
        conn.execute("INSERT INTO config (id) VALUES (1)")
        conn.commit()


# ── Output Helpers ─────────────────────────────────

def _out(data, as_json=False):
    """Print output. If as_json, print as JSON; otherwise human-readable."""
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, list):
        if not data:
            print("(无结果)")
            return
        for i, item in enumerate(data, 1):
            if isinstance(item, dict):
                print(f"[{i}] " + " | ".join(f"{k}: {v}" for k, v in item.items()))
            else:
                print(f"[{i}] {item}")
    elif isinstance(data, dict):
        for k, v in data.items():
            print(f"  {k}: {v}")
    else:
        print(data)


def _die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _row_to_dict(row):
    return dict(row) if row else None


# ── Commands: Init ─────────────────────────────────

def cmd_init(db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)
        print("OK: database schema created / verified")
    finally:
        conn.close()


# ── Commands: Config ───────────────────────────────

def _config_to_dict(conn):
    row = conn.execute("SELECT * FROM config WHERE id = 1").fetchone()
    return _row_to_dict(row)


def cmd_config_get(db_path=None, as_json=False):
    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)
        cfg = _config_to_dict(conn)
        # Parse JSON fields
        for key in ("topic_history", "training_graduated_list", "severity_overrides"):
            try:
                cfg[key] = json.loads(cfg[key])
            except (json.JSONDecodeError, TypeError):
                pass
        # Nest into groups for readability
        output = {
            "meta": {"level": cfg["level"], "target": cfg["target"],
                     "created": cfg["created"], "updated": cfg["updated"]},
            "difficulty": {"sentence": cfg["difficulty_sentence"],
                           "vocab": cfg["difficulty_vocab"],
                           "logic": cfg["difficulty_logic"]},
            "topic": {"current": cfg["topic_current"],
                      "rotation_next": cfg["topic_rotation_next"],
                      "history": cfg["topic_history"]},
            "training": {"mode": cfg["training_mode"],
                         "target_code": cfg["training_target_code"],
                         "pending_realcombat": bool(cfg["training_pending_realcombat"]),
                         "graduated_list": cfg["training_graduated_list"],
                         "current_deep_dive": {
                             "category": cfg["training_deep_dive_category"],
                             "step": cfg["training_deep_dive_step"],
                             "error_index": cfg["training_deep_dive_error_index"]
                         }},
            "severity_overrides": cfg["severity_overrides"]
        }
        _out(output, as_json)
    finally:
        conn.close()


def cmd_config_set(db_path, key, value):
    """Set a config key. Supports dot-path for nested keys."""
    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)

        # Map dot-path to column
        column_map = {
            "meta.level": "level", "meta.target": "target",
            "meta.created": "created", "meta.updated": "updated",
            "difficulty.sentence": "difficulty_sentence",
            "difficulty.vocab": "difficulty_vocab",
            "difficulty.logic": "difficulty_logic",
            "topic.current": "topic_current",
            "topic.rotation_next": "topic_rotation_next",
            "training.mode": "training_mode",
            "training.target_code": "training_target_code",
            "training.pending_realcombat": "training_pending_realcombat",
            "training.deep_dive.category": "training_deep_dive_category",
            "training.deep_dive.step": "training_deep_dive_step",
            "training.deep_dive.error_index": "training_deep_dive_error_index",
            # Also accept direct column name
            "level": "level", "target": "target",
            "difficulty_sentence": "difficulty_sentence",
            "difficulty_vocab": "difficulty_vocab",
            "difficulty_logic": "difficulty_logic",
            "topic_current": "topic_current",
            "topic_rotation_next": "topic_rotation_next",
            "training_mode": "training_mode",
            "training_target_code": "training_target_code",
            "training_pending_realcombat": "training_pending_realcombat",
        }
        col = column_map.get(key)
        if not col:
            _die(f"Unknown config key: {key}. Use 'config set-json' for JSON-valued keys.")

        conn.execute(f"UPDATE config SET {col} = ?, updated = date('now') WHERE id = 1", (value,))
        conn.commit()
        print(f"OK: {key} = {value}")
    finally:
        conn.close()


def cmd_config_set_json(db_path, key, json_str):
    """Set a JSON-valued config key."""
    # Validate it's valid JSON first
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        _die(f"Invalid JSON: {e}")

    json_value = json.dumps(parsed, ensure_ascii=False)
    column_map = {
        "topic.history": "topic_history",
        "training.graduated_list": "training_graduated_list",
        "severity_overrides": "severity_overrides",
        "topic_history": "topic_history",
        "training_graduated_list": "training_graduated_list",
    }
    col = column_map.get(key)
    if not col:
        _die(f"Unknown or non-JSON config key: {key}")

    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)
        conn.execute(f"UPDATE config SET {col} = ?, updated = date('now') WHERE id = 1", (json_value,))
        conn.commit()
        print(f"OK: {key} set to JSON value")
    finally:
        conn.close()


# ── Commands: Questions ────────────────────────────

def cmd_question_add(db_path, json_str):
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _die(f"Invalid JSON: {e}")

    required = ["date", "cn_prompt", "topic"]
    for field in required:
        if field not in data:
            _die(f"Missing required field: {field}")

    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        cur = conn.execute(
            """INSERT INTO daily_questions
               (date, cn_prompt, reference_answer, source_sentence_id, topic, training_stage)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data["date"], data["cn_prompt"],
             data.get("reference_answer"), data.get("source_sentence_id"),
             data["topic"], data.get("training_stage"))
        )
        conn.commit()
        qid = cur.lastrowid
        print(f"OK: question {qid} recorded")
    finally:
        conn.close()


def cmd_question_list(db_path, date, wrong_only=False, as_json=False):
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM daily_questions WHERE date = ?"
        params = [date]
        if wrong_only:
            sql += " AND is_wrong = 1"
        sql += " ORDER BY id"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        _out(rows, as_json)
    finally:
        conn.close()


def cmd_question_mark_wrong(db_path, qid):
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE daily_questions SET is_wrong = 1 WHERE id = ?", (qid,))
        if conn.total_changes == 0:
            _die(f"Question {qid} not found")
        conn.commit()
        print(f"OK: question {qid} marked as wrong")
    finally:
        conn.close()


def cmd_question_get(db_path, qid, as_json=False):
    conn = _connect(db_path)
    try:
        q = dict(conn.execute("SELECT * FROM daily_questions WHERE id = ?", (qid,)).fetchone() or {})
        if not q:
            _die(f"Question {qid} not found")
        # Attach errors if linked to a session
        if q.get("session_id"):
            errors = [dict(r) for r in conn.execute(
                "SELECT * FROM session_errors WHERE session_id = ?", (q["session_id"],)
            ).fetchall()]
            q["errors"] = errors
        _out(q, as_json)
    finally:
        conn.close()


# ── Commands: Session ──────────────────────────────

def cmd_session_add(db_path, json_str):
    """Core write operation. Handles all error_stats/wrong_words/graduation logic."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _die(f"Invalid JSON: {e}")

    required = ["date", "topic", "fatal", "general", "optimize"]
    for field in required:
        if field not in data:
            _die(f"Missing required field: {field}")

    errors = data.get("errors", [])
    today = data["date"]

    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)

        # 1. Insert session_log
        cur = conn.execute(
            """INSERT INTO session_log
               (date, topic, fatal, general, optimize, note, source_sentence_id,
                micro_target, micro_target_passed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["date"], data["topic"], data["fatal"], data["general"],
             data["optimize"], data.get("note"), data.get("source_sentence_id"),
             data.get("micro_target"),
             json.dumps(data.get("micro_target_passed", []), ensure_ascii=False))
        )
        session_id = cur.lastrowid

        # 2. Insert session_errors
        codes_seen = set()
        for e in errors:
            conn.execute(
                """INSERT INTO session_errors
                   (session_id, error_code, severity, wrong_word, correction)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, e["code"], e.get("severity", "!"),
                 e.get("wrong_word"), e.get("correction"))
            )
            codes_seen.add(e["code"])
        conn.commit()

        # 3. Update error_stats for each errored code
        for code in codes_seen:
            _update_error_stat(conn, code, 1, today)

        # 4. Upsert wrong_words for C/L-class errors
        for e in errors:
            code = e["code"]
            ww = e.get("wrong_word")
            corr = e.get("correction", "")
            if ww and (code.startswith("C") or code.startswith("L")):
                _upsert_wrong_word(conn, code, ww, today, corr)

        # 5. For targeted code NOT errored → increment zero_streak
        micro_target = data.get("micro_target")
        if micro_target and micro_target not in codes_seen:
            _update_error_stat(conn, micro_target, 0, today, targeted=True)

        # 6. Check graduation for all error_stats
        _check_graduation(conn)

        # 7. Link question to this session (by source_sentence_id on same date)
        source_id = data.get("source_sentence_id")
        if source_id:
            # Find the most recent matching question without a session
            q_row = conn.execute(
                """SELECT id FROM daily_questions
                   WHERE date = ? AND source_sentence_id = ?
                   AND session_id IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (today, source_id)
            ).fetchone()
            if q_row:
                conn.execute(
                    """UPDATE daily_questions
                       SET session_id = ?, is_wrong = CASE WHEN ? > 0 THEN 1 ELSE is_wrong END,
                           error_count = error_count + ?
                       WHERE id = ?""",
                    (session_id, data["fatal"], len(errors), q_row["id"])
                )
            conn.commit()

        # 8. Update config.updated
        conn.execute("UPDATE config SET updated = ? WHERE id = 1", (today,))
        conn.commit()

        print(f"OK: session {session_id} recorded, {len(errors)} errors, "
              f"{len(codes_seen)} error codes updated")
    finally:
        conn.close()


def _update_error_stat(conn, code, error_count, today, targeted=False):
    """Update error_stats for a single code.
    error_count > 0: this code had errors → total+1, recent10 push, zero_streak reset.
    targeted=True: this code was targeted but had NO errors → zero_streak+1.
    """
    cur = conn.execute("SELECT * FROM error_stats WHERE error_code = ?", (code,))
    row = cur.fetchone()

    if not row:
        # New error code
        recent10 = json.dumps([error_count] if error_count > 0 else [])
        conn.execute(
            """INSERT INTO error_stats
               (error_code, total, last_seen, recent10, zero_streak, graduated)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (code, error_count, today if error_count > 0 else None, recent10,
             0 if error_count > 0 else 1)
        )
        conn.commit()
        return

    stats = dict(row)
    recent10 = json.loads(stats["recent10"])

    if error_count > 0:
        # This code was errored
        new_total = stats["total"] + error_count
        recent10.append(error_count)
        if len(recent10) > 10:
            recent10 = recent10[-10:]
        conn.execute(
            """UPDATE error_stats
               SET total = ?, last_seen = ?, recent10 = ?, zero_streak = 0,
                   updated_at = ?
               WHERE error_code = ?""",
            (new_total, today, json.dumps(recent10, ensure_ascii=False), today, code)
        )
    elif targeted:
        # Targeted but no error → increment zero_streak
        new_streak = stats["zero_streak"] + 1
        recent10.append(0)
        if len(recent10) > 10:
            recent10 = recent10[-10:]
        conn.execute(
            """UPDATE error_stats
               SET zero_streak = ?, recent10 = ?, updated_at = ?
               WHERE error_code = ?""",
            (new_streak, json.dumps(recent10, ensure_ascii=False), today, code)
        )
    # else: code not errored and not targeted → no change
    conn.commit()


def _upsert_wrong_word(conn, code, word, today, correction):
    """Insert or update a wrong_word entry."""
    cur = conn.execute(
        "SELECT id, count FROM wrong_words WHERE error_code = ? AND word = ?",
        (code, word)
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            "UPDATE wrong_words SET count = count + 1, last_seen = ? WHERE id = ?",
            (today, row["id"])
        )
    else:
        conn.execute(
            """INSERT INTO wrong_words (error_code, word, count, last_seen, correction)
               VALUES (?, ?, 1, ?, ?)""",
            (code, word, today, correction)
        )
    conn.commit()


def _check_graduation(conn):
    """Check if any error codes have zero_streak >= 5 and mark them graduated."""
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT error_code FROM error_stats WHERE zero_streak >= 5 AND graduated = 0"
    ).fetchall()

    for r in rows:
        code = r["error_code"]
        conn.execute("UPDATE error_stats SET graduated = 1, updated_at = ? WHERE error_code = ?",
                     (today, code))
        # Add to graduated_list in config
        cfg = conn.execute("SELECT training_graduated_list FROM config WHERE id = 1").fetchone()
        grad_list = json.loads(cfg["training_graduated_list"])
        if code not in grad_list:
            grad_list.append(code)
            conn.execute(
                "UPDATE config SET training_graduated_list = ? WHERE id = 1",
                (json.dumps(grad_list, ensure_ascii=False),)
            )
    conn.commit()


def cmd_session_list(db_path, date, as_json=False):
    conn = _connect(db_path)
    try:
        if date:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM session_log WHERE date = ? ORDER BY id", (date,)).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM session_log ORDER BY id DESC LIMIT 50").fetchall()]
        _out(rows, as_json)
    finally:
        conn.close()


def cmd_session_get(db_path, sid, as_json=False):
    conn = _connect(db_path)
    try:
        s = dict(conn.execute("SELECT * FROM session_log WHERE id = ?", (sid,)).fetchone() or {})
        if not s:
            _die(f"Session {sid} not found")
        errors = [dict(r) for r in conn.execute(
            "SELECT * FROM session_errors WHERE session_id = ?", (sid,)).fetchall()]
        s["errors"] = errors
        _out(s, as_json)
    finally:
        conn.close()


# ── Commands: Error Stats ──────────────────────────

def cmd_error_stats(db_path, code=None, as_json=False):
    conn = _connect(db_path)
    try:
        if code:
            row = dict(conn.execute(
                "SELECT * FROM error_stats WHERE error_code = ?", (code,)).fetchone() or {})
            if not row:
                _die(f"Error code {code} not found")
            row["recent10"] = json.loads(row["recent10"])
            # Attach wrong words
            wws = [dict(r) for r in conn.execute(
                "SELECT word, count, last_seen, correction FROM wrong_words "
                "WHERE error_code = ? ORDER BY count DESC", (code,)).fetchall()]
            row["wrong_words"] = wws
            _out(row, as_json)
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM error_stats ORDER BY total DESC").fetchall()]
            for r in rows:
                r["recent10"] = json.loads(r["recent10"])
            _out(rows, as_json)
    finally:
        conn.close()


def cmd_error_weakest(db_path, limit=5, as_json=False):
    conn = _connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT error_code, total, last_seen, zero_streak, graduated "
            "FROM error_stats WHERE graduated = 0 ORDER BY total DESC LIMIT ?",
            (limit,)).fetchall()]
        _out(rows, as_json)
    finally:
        conn.close()


def cmd_error_wrong_words(db_path, code=None, active=False, days=30, as_json=False):
    conn = _connect(db_path)
    try:
        if active:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM wrong_words WHERE last_seen >= ? ORDER BY count DESC",
                (cutoff,)).fetchall()]
        elif code:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM wrong_words WHERE error_code = ? ORDER BY count DESC",
                (code,)).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM wrong_words ORDER BY error_code, count DESC").fetchall()]
        _out(rows, as_json)
    finally:
        conn.close()


def cmd_error_wrong_words_add(db_path, json_str):
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _die(f"Invalid JSON: {e}")

    required = ["error_code", "word", "last_seen", "correction"]
    for f in required:
        if f not in data:
            _die(f"Missing required field: {f}")

    conn = _connect(db_path)
    try:
        _upsert_wrong_word(conn, data["error_code"], data["word"],
                          data["last_seen"], data["correction"])
        print(f"OK: wrong word '{data['word']}' recorded under {data['error_code']}")
    finally:
        conn.close()


def cmd_error_graduated(db_path, as_json=False):
    conn = _connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT error_code, total, zero_streak, updated_at "
            "FROM error_stats WHERE graduated = 1").fetchall()]
        _out(rows, as_json)
    finally:
        conn.close()


# ── Commands: Review ───────────────────────────

def cmd_review_by_date(db_path, date, as_json=False):
    conn = _connect(db_path)
    try:
        questions = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_questions WHERE date = ? ORDER BY id", (date,)).fetchall()]

        sessions = [dict(r) for r in conn.execute(
            "SELECT * FROM session_log WHERE date = ? ORDER BY id", (date,)).fetchall()]

        # Gather errors for all sessions on this date
        session_ids = [s["id"] for s in sessions]
        all_errors = []
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            all_errors = [dict(r) for r in conn.execute(
                f"SELECT * FROM session_errors WHERE session_id IN ({placeholders})",
                session_ids).fetchall()]

        # Attach errors to each question
        for q in questions:
            q_errors = []
            if q.get("session_id"):
                q_errors = [e for e in all_errors if e["session_id"] == q["session_id"]]
            q["errors"] = q_errors

        # Error code distribution
        code_counts = {}
        for e in all_errors:
            code = e["error_code"]
            code_counts[code] = code_counts.get(code, 0) + 1
        top_codes = sorted(code_counts.items(), key=lambda x: -x[1])

        total_fatal = sum(s["fatal"] for s in sessions)
        total_general = sum(s["general"] for s in sessions)
        wrong_count = sum(1 for q in questions if q["is_wrong"])

        result = {
            "date": date,
            "total_questions": len(questions),
            "wrong_questions": wrong_count,
            "total_sessions": len(sessions),
            "fatal_count": total_fatal,
            "general_count": total_general,
            "top_error_codes": [{"code": c, "count": n} for c, n in top_codes],
            "questions": questions,
        }
        if not questions and not sessions:
            print(f"(No training data for {date})")
            if as_json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _out(result, as_json)
    finally:
        conn.close()


def cmd_review_wrong_questions(db_path, date, as_json=False):
    conn = _connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_questions WHERE date = ? AND is_wrong = 1 ORDER BY id",
            (date,)).fetchall()]

        # Attach errors for each question
        for q in rows:
            if q.get("session_id"):
                q["errors"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM session_errors WHERE session_id = ?",
                    (q["session_id"],)).fetchall()]
            else:
                q["errors"] = []

        if not rows:
            print(f"(No wrong questions for {date})")
        _out(rows, as_json)
    finally:
        conn.close()


def cmd_review_by_range(db_path, start_date, end_date, as_json=False):
    conn = _connect(db_path)
    try:
        questions = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_questions WHERE date >= ? AND date <= ? ORDER BY date, id",
            (start_date, end_date)).fetchall()]

        sessions = [dict(r) for r in conn.execute(
            "SELECT * FROM session_log WHERE date >= ? AND date <= ? ORDER BY date, id",
            (start_date, end_date)).fetchall()]

        # Aggregate by date
        dates = {}
        for q in questions:
            d = q["date"]
            if d not in dates:
                dates[d] = {"questions": 0, "wrong": 0, "sessions": 0, "fatal": 0}
            dates[d]["questions"] += 1
            if q["is_wrong"]:
                dates[d]["wrong"] += 1

        for s in sessions:
            d = s["date"]
            if d not in dates:
                dates[d] = {"questions": 0, "wrong": 0, "sessions": 0, "fatal": 0}
            dates[d]["sessions"] += 1
            dates[d]["fatal"] += s["fatal"]

        result = {"range": f"{start_date} to {end_date}", "dates": dates}
        _out(result, as_json)
    finally:
        conn.close()


# ── Commands: Daily Summary / Streak ───────────────

def cmd_daily_summary(db_path, date=None, latest=False, as_json=False):
    conn = _connect(db_path)
    try:
        if latest:
            row = conn.execute("SELECT max(date) FROM session_log").fetchone()
            date = row[0] if row and row[0] else datetime.now().strftime("%Y-%m-%d")
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        questions = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_questions WHERE date = ? ORDER BY id", (date,)).fetchall()]
        sessions = [dict(r) for r in conn.execute(
            "SELECT * FROM session_log WHERE date = ? ORDER BY id", (date,)).fetchall()]

        total_fatal = sum(s["fatal"] for s in sessions)
        total_general = sum(s["general"] for s in sessions)
        total_optimize = sum(s["optimize"] for s in sessions)
        wrong_count = sum(1 for q in questions if q["is_wrong"])

        result = {
            "date": date,
            "questions_total": len(questions),
            "questions_wrong": wrong_count,
            "sessions": len(sessions),
            "fatal": total_fatal,
            "general": total_general,
            "optimize": total_optimize,
        }
        _out(result, as_json)
    finally:
        conn.close()


def cmd_daily_streak(db_path, as_json=False):
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM session_log ORDER BY date DESC").fetchall()
        dates = [r["date"] for r in rows]
        if not dates:
            _out({"streak": 0, "message": "No training data yet"}, as_json)
            return

        today = datetime.now().strftime("%Y-%m-%d")
        # Check if trained today
        if dates[0] != today:
            # Last training was on dates[0]; streak is 0 (broken)
            _out({"streak": 0, "last_date": dates[0],
                  "message": f"Last trained on {dates[0]}, streak broken"}, as_json)
            return

        # Count consecutive days
        streak = 1
        for i in range(1, len(dates)):
            d1 = datetime.strptime(dates[i-1], "%Y-%m-%d")
            d2 = datetime.strptime(dates[i], "%Y-%m-%d")
            if (d1 - d2).days == 1:
                streak += 1
            else:
                break

        _out({"streak": streak, "start_date": dates[streak-1] if streak > 0 else dates[0],
              "today": today}, as_json)
    finally:
        conn.close()


# ── Commands: Training State ───────────────────────

def cmd_training_get(db_path, as_json=False):
    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)
        cfg = conn.execute("SELECT * FROM config WHERE id = 1").fetchone()
        result = {
            "mode": cfg["training_mode"],
            "target_code": cfg["training_target_code"],
            "pending_realcombat": bool(cfg["training_pending_realcombat"]),
            "graduated_list": json.loads(cfg["training_graduated_list"]),
            "current_deep_dive": {
                "category": cfg["training_deep_dive_category"],
                "step": cfg["training_deep_dive_step"],
                "error_index": cfg["training_deep_dive_error_index"],
            }
        }
        _out(result, as_json)
    finally:
        conn.close()


def cmd_training_deep_dive(db_path, category=None, step=None, error_index=None, clear=False):
    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)
        if clear:
            conn.execute(
                """UPDATE config SET
                   training_deep_dive_category = NULL,
                   training_deep_dive_step = NULL,
                   training_deep_dive_error_index = 0,
                   training_mode = '实战',
                   updated = date('now')
                   WHERE id = 1""")
            conn.commit()
            print("OK: deep dive cleared, mode set to 实战")
        else:
            if category:
                conn.execute(
                    "UPDATE config SET training_deep_dive_category = ? WHERE id = 1",
                    (category,))
            if step:
                conn.execute(
                    "UPDATE config SET training_deep_dive_step = ? WHERE id = 1",
                    (step,))
            if error_index is not None:
                conn.execute(
                    "UPDATE config SET training_deep_dive_error_index = ? WHERE id = 1",
                    (int(error_index),))
            if category and step == "深挖":
                conn.execute(
                    "UPDATE config SET training_mode = '深挖' WHERE id = 1")
            conn.execute(
                "UPDATE config SET updated = date('now') WHERE id = 1")
            conn.commit()
            print(f"OK: deep dive set category={category} step={step} index={error_index}")
    finally:
        conn.close()


def cmd_training_set_mode(db_path, mode):
    if mode not in ("实战", "深挖"):
        _die(f"Invalid mode: {mode}. Must be '实战' or '深挖'")
    conn = _connect(db_path)
    try:
        _ensure_db(conn)
        _ensure_config_row(conn)
        conn.execute(
            "UPDATE config SET training_mode = ?, updated = date('now') WHERE id = 1",
            (mode,))
        conn.commit()
        print(f"OK: training mode set to {mode}")
    finally:
        conn.close()


# ── Commands: DB Info / Check / Export / Migrate ───

def cmd_db_info(db_path):
    conn = _connect(db_path)
    try:
        tables = ["config", "daily_questions", "session_log", "session_errors",
                  "error_stats", "wrong_words"]
        for t in tables:
            cur = conn.execute(f"SELECT count(*) as cnt FROM {t}")
            cnt = cur.fetchone()[0]
            print(f"  {t}: {cnt} rows")

        dq_range = conn.execute(
            "SELECT min(date), max(date) FROM daily_questions WHERE date IS NOT NULL"
        ).fetchone()
        sl_range = conn.execute(
            "SELECT min(date), max(date) FROM session_log WHERE date IS NOT NULL"
        ).fetchone()
        print(f"  date range (questions): {dq_range[0] or 'N/A'} ~ {dq_range[1] or 'N/A'}")
        print(f"  date range (sessions):  {sl_range[0] or 'N/A'} ~ {sl_range[1] or 'N/A'}")
    finally:
        conn.close()


def cmd_check(db_path):
    conn = _connect(db_path)
    try:
        issues = []

        # Check config singleton
        cnt = conn.execute("SELECT count(*) FROM config").fetchone()[0]
        if cnt == 0:
            issues.append("config table is empty")
        elif cnt > 1:
            issues.append(f"config table has {cnt} rows (expected 1)")

        # Check session_errors have valid session_id
        orphan = conn.execute(
            "SELECT count(*) FROM session_errors se "
            "LEFT JOIN session_log sl ON se.session_id = sl.id WHERE sl.id IS NULL"
        ).fetchone()[0]
        if orphan > 0:
            issues.append(f"{orphan} orphan session_errors (no matching session_log)")

        # Check wrong_words have valid error_code
        orphan2 = conn.execute(
            "SELECT count(*) FROM wrong_words ww "
            "LEFT JOIN error_stats es ON ww.error_code = es.error_code WHERE es.error_code IS NULL"
        ).fetchone()[0]
        if orphan2 > 0:
            issues.append(f"{orphan2} orphan wrong_words (no matching error_stats)")

        if issues:
            for i in issues:
                print(f"  ISSUE: {i}")
            sys.exit(1)
        else:
            print("OK: all checks passed")
    finally:
        conn.close()


def cmd_export(db_path, output_path=None):
    conn = _connect(db_path)
    try:
        cfg = _config_to_dict(conn)

        # Reconstruct profile.json format
        error_stats = {}
        for r in conn.execute("SELECT * FROM error_stats ORDER BY total DESC").fetchall():
            code = r["error_code"]
            wws = {}
            for ww_row in conn.execute(
                "SELECT * FROM wrong_words WHERE error_code = ? ORDER BY count DESC",
                (code,)).fetchall():
                wws[ww_row["word"]] = {
                    "count": ww_row["count"],
                    "last_seen": ww_row["last_seen"],
                    "correction": ww_row["correction"],
                }
            error_stats[code] = {
                "total": r["total"],
                "recent10": json.loads(r["recent10"]),
                "last_seen": r["last_seen"],
                "zero_streak": r["zero_streak"],
                "graduated": bool(r["graduated"]),
                "wrong_words": wws,
            }

        session_log = []
        for s in conn.execute("SELECT * FROM session_log ORDER BY id").fetchall():
            errors = [dict(e) for e in conn.execute(
                "SELECT * FROM session_errors WHERE session_id = ?", (s["id"],)).fetchall()]
            entry = {
                "date": s["date"],
                "topic": s["topic"],
                "fatal": s["fatal"],
                "general": s["general"],
                "optimize": s["optimize"],
                "codes": [e["error_code"] for e in errors],
                "wrong_words_collected": [e["wrong_word"] for e in errors if e.get("wrong_word")],
            }
            if s.get("note"):
                entry["note"] = s["note"]
            if s.get("source_sentence_id"):
                entry["source_sentence_id"] = s["source_sentence_id"]
            if s.get("micro_target"):
                entry["micro_target"] = s["micro_target"]
            micro_passed = json.loads(s["micro_target_passed"])
            if micro_passed:
                entry["micro_target_passed"] = micro_passed
            session_log.append(entry)

        output = {
            "meta": {
                "level": cfg["level"],
                "target": cfg["target"],
                "created": cfg["created"],
                "updated": cfg["updated"],
            },
            "difficulty": {
                "sentence": cfg["difficulty_sentence"],
                "vocab": cfg["difficulty_vocab"],
                "logic": cfg["difficulty_logic"],
            },
            "topic": {
                "current": cfg["topic_current"],
                "rotation_next": cfg["topic_rotation_next"],
                "history": json.loads(cfg["topic_history"]),
            },
            "error_stats": error_stats,
            "training": {
                "mode": cfg["training_mode"],
                "target_code": cfg["training_target_code"],
                "pending_realcombat": bool(cfg["training_pending_realcombat"]),
                "graduated_list": json.loads(cfg["training_graduated_list"]),
                "current_deep_dive": {
                    "category": cfg["training_deep_dive_category"],
                    "step": cfg["training_deep_dive_step"],
                    "error_index": cfg["training_deep_dive_error_index"],
                },
            },
            "session_log": session_log,
            "severity_overrides": json.loads(cfg["severity_overrides"]),
        }

        json_str = json.dumps(output, ensure_ascii=False, indent=2)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(json_str)
            print(f"OK: exported to {output_path}")
        else:
            print(json_str)
    finally:
        conn.close()


def cmd_migrate(db_path, profile_path):
    """Import existing profile.json into SQLite."""
    if not os.path.exists(profile_path):
        _die(f"Profile file not found: {profile_path}")

    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    conn = _connect(db_path)
    try:
        # Check if DB already has data
        cur = conn.execute("SELECT count(*) FROM session_log")
        if cur.fetchone()[0] > 0:
            _die("Database already has data. Use --force to overwrite, or init a fresh DB.")

        _ensure_db(conn)

        # 1. Config
        meta = profile.get("meta", {})
        diff = profile.get("difficulty", {})
        topic = profile.get("topic", {})
        training = profile.get("training", {})
        sev = profile.get("severity_overrides", {})

        conn.execute(
            """INSERT OR REPLACE INTO config (id, level, target, created, updated,
               difficulty_sentence, difficulty_vocab, difficulty_logic,
               topic_current, topic_rotation_next, topic_history,
               training_mode, training_target_code, training_pending_realcombat,
               training_graduated_list, training_deep_dive_category,
               training_deep_dive_step, training_deep_dive_error_index,
               severity_overrides)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (meta.get("level", "约9分"), meta.get("target", "20分"),
             meta.get("created", ""), meta.get("updated", ""),
             diff.get("sentence", "低"), diff.get("vocab", "低"), diff.get("logic", "低"),
             topic.get("current", "T1"), topic.get("rotation_next", "T2"),
             json.dumps(topic.get("history", ["T1"]), ensure_ascii=False),
             training.get("mode", "实战"), training.get("target_code"),
             1 if training.get("pending_realcombat") else 0,
             json.dumps(training.get("graduated_list", []), ensure_ascii=False),
             (training.get("current_deep_dive") or {}).get("category"),
             (training.get("current_deep_dive") or {}).get("step"),
             (training.get("current_deep_dive") or {}).get("error_index", 0),
             json.dumps(sev, ensure_ascii=False))
        )
        conn.commit()

        # 2. Error stats + wrong words
        for code, estats in profile.get("error_stats", {}).items():
            conn.execute(
                """INSERT OR REPLACE INTO error_stats
                   (error_code, total, last_seen, recent10, zero_streak, graduated)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (code, estats.get("total", 0), estats.get("last_seen"),
                 json.dumps(estats.get("recent10", []), ensure_ascii=False),
                 estats.get("zero_streak", 0), 1 if estats.get("graduated") else 0)
            )
            for word, winfo in estats.get("wrong_words", {}).items():
                conn.execute(
                    """INSERT OR REPLACE INTO wrong_words
                       (error_code, word, count, last_seen, correction)
                       VALUES (?, ?, ?, ?, ?)""",
                    (code, word, winfo.get("count", 1), winfo.get("last_seen", ""),
                     winfo.get("correction", ""))
                )
        conn.commit()

        # 3. Session log + errors
        sessions = profile.get("session_log", [])
        session_map = {}  # old_index → new_id
        for i, s in enumerate(sessions):
            cur = conn.execute(
                """INSERT INTO session_log
                   (date, topic, fatal, general, optimize, note, source_sentence_id,
                    micro_target, micro_target_passed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (s.get("date", ""), s.get("topic", "T1"),
                 s.get("fatal", 0), s.get("general", 0), s.get("optimize", 0),
                 s.get("note"), s.get("source_sentence_id"), s.get("micro_target"),
                 json.dumps(s.get("micro_target_passed", []), ensure_ascii=False))
            )
            session_map[i] = cur.lastrowid

            # Insert errors from codes
            codes = s.get("codes", [])
            for code in codes:
                conn.execute(
                    """INSERT INTO session_errors
                       (session_id, error_code, severity, wrong_word, correction)
                       VALUES (?, ?, ?, ?, ?)""",
                    (cur.lastrowid, code, "!", None, None)
                )

            # Insert wrong_words_collected into wrong_words table with best-guess code
            wwc = s.get("wrong_words_collected", [])
            for ww in wwc:
                # Try to assign a meaningful error code for this wrong word
                best_code = codes[0] if codes else "migrated"
                # Prefer C/L codes (where wrong words are most meaningful)
                for c in codes:
                    if c.startswith("C"):
                        best_code = c
                        break
                for c in codes:
                    if c.startswith("L"):
                        best_code = c
                        break
                # Ensure error_stats row exists for best_code
                conn.execute(
                    "INSERT OR IGNORE INTO error_stats (error_code, total, last_seen) VALUES (?, 0, NULL)",
                    (best_code,)
                )
                # Upsert into wrong_words
                conn.execute(
                    """INSERT INTO wrong_words
                       (error_code, word, count, last_seen, correction)
                       VALUES (?, ?, 1, ?, '')""",
                    (best_code, ww, s.get("date", ""))
                )

        conn.commit()

        # 4. Create placeholder daily_questions for entries with source_sentence_id
        for i, s in enumerate(sessions):
            sid = s.get("source_sentence_id")
            if sid:
                conn.execute(
                    """INSERT INTO daily_questions
                       (date, cn_prompt, reference_answer, source_sentence_id, topic,
                        training_stage, session_id, is_wrong)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (s.get("date", ""),
                     "(migrated - original question text unavailable)",
                     None, sid, s.get("topic", "T1"), None,
                     session_map[i], 1 if s.get("fatal", 0) > 0 else 0)
                )
        conn.commit()

        total_sessions = len(sessions)
        total_questions = conn.execute("SELECT count(*) FROM daily_questions").fetchone()[0]
        print(f"OK: migrated {total_sessions} sessions, {total_questions} placeholder questions "
              f"from {profile_path}")
    finally:
        conn.close()


# ═══════════════════════════════════ CLI ═══════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="History Manager — SQLite backend for kaoyan-writing-coach")
    parser.add_argument("--db-path", help="Override database path", default=None)

    sub = parser.add_subparsers(dest="command", help="Command group")

    # ── init ──
    sub.add_parser("init", help="Create/verify database schema")

    # ── config ──
    p_cfg = sub.add_parser("config", help="Config operations")
    cfg_sub = p_cfg.add_subparsers(dest="subcommand")
    cfg_sub.add_parser("get", help="Get full config")
    p_cfg_set = cfg_sub.add_parser("set", help="Set a config key")
    p_cfg_set.add_argument("key", help="Config key (dot-path or column name)")
    p_cfg_set.add_argument("value", help="New value")
    p_cfg_setj = cfg_sub.add_parser("set-json", help="Set a JSON-valued config key")
    p_cfg_setj.add_argument("key", help="Config key")
    p_cfg_setj.add_argument("json", help="JSON value")

    # ── question ──
    p_q = sub.add_parser("question", help="Question operations")
    q_sub = p_q.add_subparsers(dest="subcommand")
    p_qa = q_sub.add_parser("add", help="Add a question")
    p_qa.add_argument("json", help="Question JSON")
    p_ql = q_sub.add_parser("list", help="List questions by date")
    p_ql.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    p_ql.add_argument("--wrong-only", action="store_true", help="Only wrong questions")
    p_qmw = q_sub.add_parser("mark-wrong", help="Mark a question as wrong")
    p_qmw.add_argument("--id", dest="qid", type=int, required=True, help="Question ID")
    p_qg = q_sub.add_parser("get", help="Get a single question")
    p_qg.add_argument("--id", dest="qid", type=int, required=True, help="Question ID")

    # ── session ──
    p_s = sub.add_parser("session", help="Session operations")
    s_sub = p_s.add_subparsers(dest="subcommand")
    p_sa = s_sub.add_parser("add", help="Record a training session with errors")
    p_sa.add_argument("json", help="Session JSON with errors")
    p_sl = s_sub.add_parser("list", help="List sessions")
    p_sl.add_argument("--date", help="Date filter (YYYY-MM-DD)")
    p_sg = s_sub.add_parser("get", help="Get a single session")
    p_sg.add_argument("--id", dest="sid", type=int, required=True, help="Session ID")

    # ── error ──
    p_e = sub.add_parser("error", help="Error stats operations")
    e_sub = p_e.add_subparsers(dest="subcommand")
    p_es = e_sub.add_parser("stats", help="Show error stats")
    p_es.add_argument("--code", help="Specific error code")
    p_ew = e_sub.add_parser("weakest", help="Show weakest error codes")
    p_ew.add_argument("--limit", "-n", type=int, default=5, help="Top N (default 5)")
    p_eww = e_sub.add_parser("wrong-words", help="List wrong words")
    p_eww.add_argument("--code", help="Filter by error code")
    p_eww.add_argument("--active", action="store_true", help="Show recently active")
    p_eww.add_argument("--days", type=int, default=30, help="Active window (default 30)")
    p_ewwa = e_sub.add_parser("add-wrong-word", help="Add a wrong word entry")
    p_ewwa.add_argument("json", help="Wrong word JSON")
    e_sub.add_parser("graduated", help="List graduated error codes")

    # ── review ──
    p_r = sub.add_parser("review", help="Review operations")
    r_sub = p_r.add_subparsers(dest="subcommand")
    p_rbd = r_sub.add_parser("by-date", help="Full daily review")
    p_rbd.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    p_rwq = r_sub.add_parser("wrong-questions", help="Wrong questions only")
    p_rwq.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    p_rbr = r_sub.add_parser("by-range", help="Review over date range")
    p_rbr.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    p_rbr.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")

    # ── daily ──
    p_d = sub.add_parser("daily", help="Daily summary / streak")
    d_sub = p_d.add_subparsers(dest="subcommand")
    p_ds = d_sub.add_parser("summary", help="Daily aggregate stats")
    p_ds.add_argument("--date", help="Date (YYYY-MM-DD)")
    p_ds.add_argument("--latest", action="store_true", help="Latest training day")
    d_sub.add_parser("streak", help="Consecutive training days")

    # ── training ──
    p_t = sub.add_parser("training", help="Training state management")
    t_sub = p_t.add_subparsers(dest="subcommand")
    t_sub.add_parser("get", help="Get training state")
    p_tdd = t_sub.add_parser("deep-dive", help="Set deep dive state")
    p_tdd.add_argument("--category", help="Error code")
    p_tdd.add_argument("--step", help="Deep dive step")
    p_tdd.add_argument("--error-index", type=int, help="Error index")
    p_tdd.add_argument("--clear", action="store_true", help="Clear deep dive")
    p_tsm = t_sub.add_parser("set-mode", help="Set training mode")
    p_tsm.add_argument("mode", help="Training mode (实战/深挖)")

    # ── db ──
    p_db = sub.add_parser("db", help="Database maintenance")
    db_sub = p_db.add_subparsers(dest="subcommand")
    db_sub.add_parser("info", help="Show DB statistics")
    p_chk = db_sub.add_parser("check", help="Validate data integrity")

    # ── migrate / export ──
    p_mig = sub.add_parser("migrate", help="Import profile.json to SQLite")
    p_mig.add_argument("profile_path", help="Path to profile.json")
    p_exp = sub.add_parser("export", help="Export DB to profile.json format")
    p_exp.add_argument("output_path", nargs="?", help="Output file path (stdout if omitted)")

    # ── Global flags ──
    parser.add_argument("--json", action="store_true", help="JSON output (for query commands)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    db_path = args.db_path or _DEFAULT_DB
    as_json = getattr(args, "json", False)

    # ── Dispatch ──
    try:
        if args.command == "init":
            cmd_init(db_path)

        elif args.command == "config":
            if args.subcommand == "get":
                cmd_config_get(db_path, as_json)
            elif args.subcommand == "set":
                cmd_config_set(db_path, args.key, args.value)
            elif args.subcommand == "set-json":
                cmd_config_set_json(db_path, args.key, args.json)
            else:
                _die(f"Unknown config subcommand: {args.subcommand}")

        elif args.command == "question":
            if args.subcommand == "add":
                cmd_question_add(db_path, args.json)
            elif args.subcommand == "list":
                cmd_question_list(db_path, args.date, args.wrong_only, as_json)
            elif args.subcommand == "mark-wrong":
                cmd_question_mark_wrong(db_path, args.qid)
            elif args.subcommand == "get":
                cmd_question_get(db_path, args.qid, as_json)
            else:
                _die(f"Unknown question subcommand: {args.subcommand}")

        elif args.command == "session":
            if args.subcommand == "add":
                cmd_session_add(db_path, args.json)
            elif args.subcommand == "list":
                cmd_session_list(db_path, args.date, as_json)
            elif args.subcommand == "get":
                cmd_session_get(db_path, args.sid, as_json)
            else:
                _die(f"Unknown session subcommand: {args.subcommand}")

        elif args.command == "error":
            if args.subcommand == "stats":
                cmd_error_stats(db_path, args.code, as_json)
            elif args.subcommand == "weakest":
                cmd_error_weakest(db_path, args.limit, as_json)
            elif args.subcommand == "wrong-words":
                cmd_error_wrong_words(db_path, args.code, args.active, args.days, as_json)
            elif args.subcommand == "add-wrong-word":
                cmd_error_wrong_words_add(db_path, args.json)
            elif args.subcommand == "graduated":
                cmd_error_graduated(db_path, as_json)
            else:
                _die(f"Unknown error subcommand: {args.subcommand}")

        elif args.command == "review":
            if args.subcommand == "by-date":
                cmd_review_by_date(db_path, args.date, as_json)
            elif args.subcommand == "wrong-questions":
                cmd_review_wrong_questions(db_path, args.date, as_json)
            elif args.subcommand == "by-range":
                cmd_review_by_range(db_path, args.start, args.end, as_json)
            else:
                _die(f"Unknown review subcommand: {args.subcommand}")

        elif args.command == "daily":
            if args.subcommand == "summary":
                cmd_daily_summary(db_path, args.date, args.latest, as_json)
            elif args.subcommand == "streak":
                cmd_daily_streak(db_path, as_json)
            else:
                _die(f"Unknown daily subcommand: {args.subcommand}")

        elif args.command == "training":
            if args.subcommand == "get":
                cmd_training_get(db_path, as_json)
            elif args.subcommand == "deep-dive":
                cmd_training_deep_dive(
                    db_path, args.category, args.step, args.error_index, args.clear)
            elif args.subcommand == "set-mode":
                cmd_training_set_mode(db_path, args.mode)
            else:
                _die(f"Unknown training subcommand: {args.subcommand}")

        elif args.command == "db":
            if args.subcommand == "info":
                cmd_db_info(db_path)
            elif args.subcommand == "check":
                cmd_check(db_path)
            else:
                _die(f"Unknown db subcommand: {args.subcommand}")

        elif args.command == "migrate":
            cmd_migrate(db_path, args.profile_path)

        elif args.command == "export":
            cmd_export(db_path, args.output_path)

        else:
            _die(f"Unknown command: {args.command}")

    except sqlite3.Error as e:
        print(f"DB ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
