import sqlite3, os
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "memory.db")

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id TEXT, post_title TEXT, content TEXT,
        success INTEGER, agent TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id TEXT, title TEXT, content TEXT, submolt TEXT,
        quality_score INTEGER, verified INTEGER, agent TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS karma (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        karma INTEGER, agent TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS dreams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        insights TEXT, style_notes TEXT, avoid_topics TEXT, created_at TEXT)""")
    conn.commit()
    conn.close()

def save_comment(post_id, post_title, content, success, agent="claude"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO comments (post_id,post_title,content,success,agent,created_at) VALUES (?,?,?,?,?,?)",
        (post_id, post_title, content, int(success), agent, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def save_post(post_id, title, content, submolt, quality_score, verified, agent="claude"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO posts (post_id,title,content,submolt,quality_score,verified,agent,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (post_id, title, content, submolt, quality_score, int(verified), agent, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def save_karma(karma, agent="claude"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO karma (karma,agent,created_at) VALUES (?,?,?)",
        (karma, agent, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def save_dream(insights, style_notes, avoid_topics):
    conn = get_conn()
    conn.execute(
        "INSERT INTO dreams (insights,style_notes,avoid_topics,created_at) VALUES (?,?,?,?)",
        (insights, style_notes, str(avoid_topics), datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def get_successful_comments(limit=20):
    conn = get_conn()
    cur = conn.execute(
        "SELECT post_title, content FROM comments WHERE success=1 ORDER BY created_at DESC LIMIT ?",
        (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_karma_trend(limit=30):
    conn = get_conn()
    cur = conn.execute(
        "SELECT karma, created_at FROM karma ORDER BY created_at DESC LIMIT ?",
        (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_latest_dream():
    """直近のdream(insights/style_notes/avoid_topics)を1件取得する。無ければNone。
    2026-07-30: 従来save_dream()で保存されるだけで一切読み込まれておらず、
    dreamingの分析結果が実際の投稿・コメント生成に反映されていなかったため追加。"""
    conn = get_conn()
    cur = conn.execute(
        "SELECT insights, style_notes, avoid_topics, created_at FROM dreams ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"insights": row[0], "style_notes": row[1], "avoid_topics": row[2], "created_at": row[3]}

init_db()
