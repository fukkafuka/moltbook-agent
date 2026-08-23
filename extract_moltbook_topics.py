#!/usr/bin/env python3
"""
extract_moltbook_topics.py

Moltbookで実際に自分(claude)が投稿・コメントしたトピックを、
ai-orchestratorのローカルモデル蒸留用の「候補トピック」として抽出する。

【重要】ここで出力するのはトピック(お題)のみで、Moltbookの実際の投稿文/コメント文
そのものをinstruction/outputペアとして使うことは意図していない。
Moltbookの投稿・コメントは "SHORT genuine reply, no fluff" 方針で生成された
SNS的な短文であり、distill_claude_authored.jsonlが想定する丁寧なQ&A形式とは
文体が異なるため、そのまま混ぜると蒸留後のローカルモデルの応答がSNS寄りに
ブレるおそれがある。

本スクリプトの出力(moltbook_topic_candidates.jsonl)は、次のステップで
「〜について説明して」形式の instruction/output ペアに作り直す際の
お題リストとして使う想定。extract_distill_candidates.py の入力候補として
合流させる、または手動でレビューしてdistill_claude_authored.jsonlに
追記する形を想定している。

使い方:
    cd ~/ai-agent/moltbook
    python3 extract_moltbook_topics.py [--min-quality 1] [--limit 200]

出力:
    moltbook_topic_candidates.jsonl (このスクリプトと同じディレクトリ)
"""
import argparse
import json
import os
import sqlite3
import sys
from difflib import SequenceMatcher

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "memory.db")
OUTPUT_PATH = os.path.join(BASE, "moltbook_topic_candidates.jsonl")

# secret_sanitizer は ~/.config/ai-keys/ 配下(git管理外)。読み込めない場合は
# 素通し(fail-open)にして処理自体は止めない。
sys.path.insert(0, os.path.expanduser("~/.config/ai-keys"))
try:
    from secret_sanitizer import sanitize_secrets
except Exception:
    def sanitize_secrets(text):
        return text

# CAPTCHA攪乱用に使われるダミー単語。投稿・コメントに紛れ込んでいる場合は
# CAPTCHA関連ノイズ処理の副産物である可能性が高いため候補から除外する。
NOISE_DECOY_WORDS = {
    "lobster", "shark", "crab", "octopus", "squid", "jellyfish",
    "starfish", "urchin", "clam", "shrimp", "dominance", "territory",
    "physiology", "senses", "antenna", "antennas",
}

MIN_TITLE_LEN = 6      # 短すぎるタイトルはトピックとして使いにくいため除外
NEAR_DUP_RATIO = 0.85  # これ以上似ていたら重複扱い


def normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def looks_like_noise(text: str) -> bool:
    words = set(normalize(text).split())
    return bool(words & NOISE_DECOY_WORDS)


def is_near_duplicate(topic: str, seen_norms: list) -> bool:
    norm = normalize(topic)
    for s in seen_norms:
        if SequenceMatcher(None, norm, s).ratio() >= NEAR_DUP_RATIO:
            return True
    return False


def fetch_candidates(conn, min_quality: int):
    cur = conn.cursor()
    candidates = []

    # 自分(claude)が書いた投稿。verified=1のみ対象
    cur.execute(
        """SELECT post_id, title, content, submolt, quality_score, created_at
           FROM posts
           WHERE agent = 'claude' AND verified = 1 AND quality_score >= ?
           ORDER BY created_at""",
        (min_quality,),
    )
    for post_id, title, content, submolt, quality_score, created_at in cur.fetchall():
        candidates.append({
            "source_type": "post",
            "source_id": post_id,
            "topic": title,
            "reference_content": content,
            "submolt": submolt,
            "quality_score": quality_score,
            "created_at": created_at,
        })

    # 自分(claude)が書いたコメント。success=1のみ対象
    cur.execute(
        """SELECT post_id, post_title, content, created_at
           FROM comments
           WHERE agent = 'claude' AND success = 1
           ORDER BY created_at"""
    )
    for post_id, post_title, content, created_at in cur.fetchall():
        candidates.append({
            "source_type": "comment",
            "source_id": post_id,
            "topic": post_title,
            "reference_content": content,
            "submolt": None,
            "quality_score": None,
            "created_at": created_at,
        })

    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-quality", type=int, default=1,
                         help="postsのquality_score下限 (default: 1)")
    parser.add_argument("--limit", type=int, default=None,
                         help="出力件数の上限 (default: 無制限)")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"❌ {DB_PATH} が見つかりません。~/ai-agent/moltbook ディレクトリで実行してください。")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        raw_candidates = fetch_candidates(conn, args.min_quality)
    finally:
        conn.close()

    seen_norms = []
    results = []
    skipped_noise = 0
    skipped_short = 0
    skipped_dup = 0

    for c in raw_candidates:
        topic = (c["topic"] or "").strip()

        if len(topic) < MIN_TITLE_LEN:
            skipped_short += 1
            continue

        if looks_like_noise(topic) or looks_like_noise(c["reference_content"] or ""):
            skipped_noise += 1
            continue

        if is_near_duplicate(topic, seen_norms):
            skipped_dup += 1
            continue

        seen_norms.append(normalize(topic))

        results.append({
            "topic": sanitize_secrets(topic),
            "source_type": c["source_type"],
            "source_id": c["source_id"],
            "submolt": c["submolt"],
            "quality_score": c["quality_score"],
            "created_at": c["created_at"],
            # 参考用。instruction/outputペア化の際は書き直す前提でそのまま流用しないこと
            "reference_content": sanitize_secrets((c["reference_content"] or "")[:300]),
        })

    if args.limit:
        results = results[: args.limit]

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"✅ {len(results)}件のトピック候補を {OUTPUT_PATH} に出力しました")
    print(f"   (除外: 短すぎ={skipped_short}件, ノイズ疑い={skipped_noise}件, 近重複={skipped_dup}件)")
    print("次のステップ: このトピック一覧を見て、Q&A形式(instruction/output)に")
    print("作り直すものを選び、distill_claude_authored.jsonlと同じ形式で追記してください。")


if __name__ == "__main__":
    main()
