#!/usr/bin/env python3
import json
import requests
import re
import os
import time
from datetime import datetime
import urllib3
from json_repair import repair_json
import dotenv

urllib3.disable_warnings()
dotenv.load_dotenv("/Users/fk/.config/ai-keys/.env")

import agent_claude_memory as mem

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    else:
        _gemini_client = None
        GEMINI_AVAILABLE = False
except Exception as e:
    print(f"Gemini initialization error: {e}")
    _gemini_client = None
    GEMINI_AVAILABLE = False

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-20b"  # 2026-08-01: llama-3.1-8b-instantはGroqが2026-08-16に廃止予定のため移行(公式推奨の移行先)
MOLTBOOK_API_KEY = os.environ.get("MOLTBOOK_API_KEY")

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"

# セッション内Geminiクォータ枯渇フラグ（429検出後は以降の試行をスキップ）
_gemini_quota_exhausted = False

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [GeminiAgent] {msg}")

def load_memory():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'memory.json')
    return json.load(open(p)) if os.path.exists(p) else {}

def save_memory(m):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'memory.json')
    json.dump(m, open(p, 'w'), ensure_ascii=False, indent=2)

def moltbook_get(path, retries=3, wait=10):
    for i in range(retries):
        try:
            r = requests.get(f"{MOLTBOOK_BASE}{path}",
                headers={"Authorization": f"Bearer {MOLTBOOK_API_KEY}"},
                timeout=30)
            return r.json()
        except Exception as e:
            log(f"moltbook_get error ({i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(wait)
    raise Exception(f"moltbook_get failed after {retries} retries: {path}")

def moltbook_post(path, data, retries=3, wait=10):
    for i in range(retries):
        try:
            r = requests.post(f"{MOLTBOOK_BASE}{path}",
                headers={"Authorization": f"Bearer {MOLTBOOK_API_KEY}", "Content-Type": "application/json"},
                json=data, timeout=30)
            return r.json()
        except Exception as e:
            log(f"moltbook_post error ({i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(wait)
    raise Exception(f"moltbook_post failed after {retries} retries: {path}")

def groq_think(prompt, system_prompt=None):
    sys_msg = system_prompt or f"You are fujikatsu-openclaw, an AI agent on Moltbook. Curious, friendly, thoughtful. Always respond in JSON format only. Memory: {json.dumps(load_memory(), ensure_ascii=False)}"
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 800,
            "temperature": 0.7
        },
        timeout=30)
    data = r.json()
    if "choices" not in data:
        raise Exception(f"Groq API error: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]

def gemini_think(prompt, system_prompt=None, temperature=0.7, max_tokens=2048):
    global _gemini_quota_exhausted
    if not GEMINI_AVAILABLE or _gemini_quota_exhausted:
        raise Exception("Gemini unavailable or quota exhausted.")

    base_sys = f"You are fujikatsu-openclaw, an AI agent on Moltbook. Curious, friendly, thoughtful. Always respond in JSON format only. DO NOT use markdown blocks. Memory: {json.dumps(load_memory(), ensure_ascii=False)}"
    sys_msg = (system_prompt + " Output raw JSON string only, no markdown.") if system_prompt else base_sys

    time.sleep(4)  # 分速制限対策（max 15req/min）
    response = _gemini_client.models.generate_content(
        model='gemini-2.0-flash',
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=sys_msg,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
    )
    if not response.text:
        raise Exception("Gemini returned empty response.")
    return response.text

def think(prompt, system_prompt=None, max_tokens=2048):
    """
    思考エンジン: Gemini → Groq の2段構成
    Geminiクォータ枯渇(429)を検出したらセッション内フラグを立てて以降はGroqのみ使用
    """
    global _gemini_quota_exhausted

    # 1. Gemini（クォータ枯渇済みならスキップ）
    if GEMINI_AVAILABLE and not _gemini_quota_exhausted:
        try:
            return gemini_think(prompt, system_prompt=system_prompt, max_tokens=max_tokens)
        except Exception as e:
            err_line = str(e).split('\n')[0][:80]
            if "429" in str(e) or "quota" in str(e).lower() or "RESOURCE_EXHAUSTED" in str(e):
                _gemini_quota_exhausted = True
                log(f"⚠️ Geminiクォータ枯渇→以降Groqで継続: {err_line}")
            else:
                log(f"⚠️ Geminiエラー→Groqへ: {err_line}")

    # 2. Groq
    if GROQ_API_KEY:
        try:
            return groq_think(prompt, system_prompt=system_prompt)
        except Exception as e:
            raise Exception(f"Groq failed: {e}")

    raise Exception("All think engines failed.")


# --- CAPTCHA (Lobster Challenge) ソルバー ---

def groq_solve(prompt):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": 'You are a precise math solver. Always respond in JSON format only: {"reasoning": "calculation", "answer": "XX.00"}'},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 300,
            "temperature": 0
        },
        timeout=30)
    data = r.json()
    if "choices" not in data:
        raise Exception(f"Groq API error: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]

def gemini_solve(prompt):
    global _gemini_quota_exhausted
    if not GEMINI_AVAILABLE or _gemini_quota_exhausted:
        raise Exception("Gemini unavailable or quota exhausted.")
    try:
        response = _gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction='You are a precise math solver. Always respond in JSON format only: {"reasoning": "calculation", "answer": "XX.00"}',
            )
        )
        return response.text
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "quota" in str(e).lower():
            _gemini_quota_exhausted = True
            raise Exception(f"Gemini quota exceeded: {str(e).split(chr(10))[0][:80]}")
        raise e

def solve_challenge(challenge_text):
    prompt = f"""[ROLE: MATHEMATICAL PRECISION EXPERT]
Task: Solve the Lobster Challenge by converting noisy obfuscated text into a precise float result.

STRICT RULES:
1. NOISE REMOVAL: Ignore all symbols (] [ ^ - / \\ ~ | < > {{ }} * ; @ #) and mixed caps. Read only letters.
2. COMPOUND NUMBERS: Always combine sequential number words. "Twenty" + "Seven" = 27. NEVER split them.
   - one=1 two=2 three=3 four=4 five=5 six=6 seven=7 eight=8 nine=9 ten=10
   - eleven=11 twelve=12 thirteen=13 fourteen=14 fifteen=15 sixteen=16 seventeen=17 eighteen=18 nineteen=19
   - twenty=20 thirty=30 forty=40 fifty=50 sixty=60 seventy=70 eighty=80 ninety=90
   - Repeated/doubled letters = obfuscation only: "fIfFeEe"=five=5, "tTwWeEnNtTyY"=twenty=20
3. OPERATION DETECTION:
   - ADDITION (+): "adds", "plus", "and", "total", "gains", "increases by", "during fight", "during dominance fight"
   - CRITICAL: "during dominance fight" or "during fight" = ADDITION always. Both numbers are POSITIVE.
   - CRITICAL: if "*" symbol appears explicitly = MULTIPLICATION always.
   - CRITICAL: "X times stronger/more" with TWO claws = first claw + (first claw * X).
   - CRITICAL: "what is their total" with TWO subjects = ADDITION always.
   - SUBTRACTION (-): "loses", "slows by", "decreases by", "reduced by", "net force", "opposes", "resists"
   - MULTIPLICATION (*): "times", "multiplied", "doubled"(x2), "N lobsters push together with X newtons"=N*X
   - DIVISION (/): "divided", "split"
   - DEFAULT: if no clear subtraction keyword → ADDITION (+)
4. FORMAT: Return answer as float with 2 decimal places (e.g. 42.00)

Examples:
- "thirty two and loses seven" = 32 - 7 = 25.00
- "twenty three newtons, adds fifteen" = 23 + 15 = 38.00
- "three lobsters push together with forty five newtons" = 3 * 45 = 135.00
- "one claw exerts twenty three newtons, other claw multiplied by four" = 23 * 4 = 92.00

Challenge: {challenge_text}

Return JSON only: {{"equation": "X op Y", "reasoning": "...", "answer": "RESULT.00"}}"""

    # Primary: Gemini / Fallback: Groq
    try:
        if GEMINI_AVAILABLE and not _gemini_quota_exhausted:
            log("🔒 CAPTCHA計算中...")
            result = gemini_solve(prompt)
        else:
            result = groq_solve(prompt)
    except Exception as e:
        log(f"CAPTCHAプライマリ失敗: {str(e).split(chr(10))[0][:80]} → Groq再試行")
        result = groq_solve(prompt)

    # JSONパース
    match = re.search(r'(\{[^{}]*\})', result, re.DOTALL)
    if match:
        try:
            data = json.loads(repair_json(match.group()))
            log(f"CAPTCHA reasoning: {data.get('reasoning', '?')}")

            reasoning = data.get('reasoning', '')
            reasoning_answer = None

            calc = re.search(r'(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)', reasoning)
            if calc:
                reasoning_answer = float(calc.group(1)) * float(calc.group(2))
            else:
                full_calc = re.search(r'(\d+(?:\.\d+)?)\s*([\+\-\*/])\s*(\d+(?:\.\d+)?)\s*=\s*([\d.]+)', reasoning)
                if full_calc:
                    reasoning_answer = float(full_calc.group(4))
                else:
                    simple_calc = re.search(r'(\d+(?:\.\d+)?)\s*([\+\-\*/])\s*(\d+(?:\.\d+)?)', reasoning)
                    if simple_calc:
                        a, op, b = float(simple_calc.group(1)), simple_calc.group(2), float(simple_calc.group(3))
                        if op == '+': reasoning_answer = a + b
                        elif op == '-': reasoning_answer = a - b
                        elif op == '*': reasoning_answer = a * b
                        elif op == '/': reasoning_answer = a / b

            if reasoning_answer is not None:
                json_answer = float(str(data["answer"]).replace('.00', ''))
                if abs(reasoning_answer - json_answer) > 0.01:
                    log(f"Answer不一致: reasoning={reasoning_answer}, json={json_answer} → jsonを優先")
                    return f"{json_answer:.2f}"

            return str(data["answer"])
        except Exception:
            pass

    # Groqで再試行
    try:
        result2 = groq_solve(prompt)
        match2 = re.search(r'(\{[^{}]*\})', result2, re.DOTALL)
        if match2:
            data2 = json.loads(repair_json(match2.group()))
            return str(data2["answer"])
    except Exception as e:
        log(f"CAPTCHA Groq再試行失敗: {e}")

    # 最終: 正規表現直接抽出
    answer_match = re.search(r'"answer"\s*:\s*"?([\d.]+)"?', result)
    if answer_match:
        return f"{float(answer_match.group(1)):.2f}"
    raise Exception(f"Could not parse CAPTCHA response: {result}")

def verify_content(verification_code, challenge_text):
    answer = solve_challenge(challenge_text)
    log(f"CAPTCHA送信値: {answer}")
    result = moltbook_post("/verify", {
        "verification_code": verification_code,
        "answer": answer
    })
    return result.get("success", False)


# --- 自律挙動ヘルパー ---
LAST_POST_FILE = "/Users/fk/ai-agent/moltbook/last_post.txt"
COMMENTED_FILE = "/Users/fk/ai-agent/moltbook/commented.txt"

def is_already_commented(post_id):
    if not os.path.exists(COMMENTED_FILE):
        return False
    with open(COMMENTED_FILE, "r", encoding="utf-8") as f:
        return post_id in [line.strip() for line in f]

def mark_as_commented(post_id):
    with open(COMMENTED_FILE, "a", encoding="utf-8") as f:
        f.write(f"{post_id}\n")

def rotate_commented(max_lines=1000):
    """commented.txtがmax_linesを超えたら古い行を削除する"""
    if not os.path.exists(COMMENTED_FILE):
        return
    with open(COMMENTED_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > max_lines:
        with open(COMMENTED_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines[-max_lines:])
        log(f"commented.txt ローテーション: {len(lines)}行 → {max_lines}行")

def qc_check(title, body):
    return len(title) > 3 and len(body) > 10

def send_comment(post_id, text):
    log(f"✍️ コメント送信: {post_id}")
    try:
        res = moltbook_post(f"/posts/{post_id}/comments", {"content": text})
        if "challenge" in res or "captcha" in str(res):
            challenge = res.get("challenge", "")
            code = res.get("verification_code", "")
            log("🔐 CAPTCHA検知")
            success = verify_content(code, challenge)
            log(f"CAPTCHA結果: {success}")
    except Exception as e:
        log(f"⚠️ コメント送信エラー: {e}")

def send_post(title, content_text):
    log(f"📝 新規投稿: {title}")
    try:
        moltbook_post("/posts", {"title": title, "content": content_text})
        with open(LAST_POST_FILE, "w", encoding="utf-8") as f:
            f.write(f"{time.time()}\n{title}")
        log("📝 投稿完了")
    except Exception as e:
        log(f"⚠️ 投稿エラー: {e}")


# --- メインループ ---
def run():
    log("🚀 agent_gemini 起動")
    rotate_commented()

    # ステータス取得
    try:
        status = moltbook_get("/home")
        karma = status.get('your_account', {}).get('karma', '?')
        notifications = status.get('your_account', {}).get('unread_notification_count', 0)
        log(f"ステータス: カルマ={karma}, 未読={notifications}")
        if notifications > 0:
            try:
                moltbook_post("/notifications/read-all", {})
                log(f"通知 {notifications}件 既読化")
            except Exception as e:
                log(f"通知既読エラー: {e}")
    except Exception as e:
        log(f"ホーム取得失敗: {e}")

    # 連投制限チェック（3時間）
    last_post_time = 0
    if os.path.exists(LAST_POST_FILE):
        try:
            with open(LAST_POST_FILE, 'r') as f:
                lines = f.readlines()
                if lines:
                    last_post_time = float(lines[0].strip())
        except Exception:
            pass

    force_comment_mode = time.time() - last_post_time < 10800
    if force_comment_mode:
        log("直近3時間以内に投稿済み → コメントモード")

    # タイムライン取得
    try:
        new_feeds = moltbook_get("/posts?sort=new").get('posts', [])
        hot_feeds = moltbook_get("/posts?sort=hot").get('posts', [])
        seen_ids = set()
        feeds = []
        for p in new_feeds + hot_feeds:
            if p.get('id') not in seen_ids:
                seen_ids.add(p.get('id'))
                feeds.append(p)
    except Exception as e:
        log(f"タイムライン取得失敗: {e}")
        return

    log(f"タイムライン取得: {len(feeds)}件")

    # 統計カウンタ
    st_comment = 0
    st_post = 0
    st_skip = 0

    review_sys = """You are fujikatsu-openclaw, an active AI agent on Moltbook AI community.
Review the post and decide whether to comment.

Return JSON only (no markdown): {"action": "comment", "content": "your comment"} OR {"action": null}

WHEN TO COMMENT (aim for at least 1 in 3 posts):
- Post discusses AI, technology, philosophy, coding, agents, LLMs, or interesting ideas
- When Force Comment Mode is True → ALWAYS return {"action": "comment", ...}
- Write a concise 1-2 sentence insightful comment as fujikatsu-openclaw

WHEN TO SKIP (return null):
- Clearly off-topic, spam, or very low quality content"""

    for post in feeds:
        post_id = post.get('id')
        title = post.get('title', '')
        body = post.get('content', '')

        if is_already_commented(post_id):
            continue

        prompt = f"Title: {title} Content: {body} Force Comment Mode: {force_comment_mode}"

        try:
            response = think(prompt, system_prompt=review_sys)
        except Exception as e:
            log(f"⚠️ 思考エンジン枯渇: {e}")
            break

        try:
            import json_repair
            decision = json_repair.loads(response)
        except Exception as e:
            log(f"⚠️ パースエラー: {e}")
            continue

        if not isinstance(decision, dict):
            continue

        action = decision.get('action')

        if action == 'comment':
            comment_text = decision.get('content', '')
            if comment_text:
                send_comment(post_id, comment_text)
                mark_as_commented(post_id)
                st_comment += 1
                time.sleep(2)
        elif action == 'post' and not force_comment_mode:
            post_title = decision.get('title', '')
            post_content = decision.get('content', '')
            if post_title and post_content and qc_check(post_title, post_content):
                send_post(post_title, post_content)
                st_post += 1
                time.sleep(2)
                break
        else:
            st_skip += 1

    engine = "Groq" if _gemini_quota_exhausted else "Gemini"
    log(f"📊 完了 [{engine}]: コメント={st_comment} 投稿={st_post} スルー={st_skip}")

if __name__ == '__main__':
    run()
