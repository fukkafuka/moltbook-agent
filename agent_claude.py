#!/usr/bin/env python3
import json
import requests
import re
import sys
import os
from json_repair import repair_json
import dotenv
dotenv.load_dotenv("/Users/fk/.config/ai-keys/.env")
try:
    from google import genai as _genai
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    _gemini_client = _genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except Exception as e:
    print(f"Gemini init error: {e}")
    GEMINI_AVAILABLE = False
import time
import random
from datetime import datetime, timedelta
import urllib3
urllib3.disable_warnings()
import agent_claude_memory as mem

# .envファイルを読み込む（同ディレクトリ、dotenv不要）
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
GROQ_MODEL = "llama-3.3-70b-versatile"
MOLTBOOK_API_KEY = os.environ.get("MOLTBOOK_API_KEY")

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
BONSAI_BASE = "http://127.0.0.1:11436"

def log(msg):
    line = f"🦞[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with open("/Users/fk/Logs/agent_claude.log", "a") as f:
        f.write(line + "\n")

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
            # 500エラーは即終了（サーバー障害）
            if r.status_code == 500:
                log(f"🔴 Moltbook障害検出（500）: {path} → 即終了")
                raise Exception(f"Moltbook server error 500: {path}")
            # 401エラーも即終了（認証エラー）
            if r.status_code == 401:
                log(f"🔴 Moltbook認証エラー（401）: {path} → 即終了")
                raise Exception(f"Moltbook auth error 401: {path}")
            return r.json()
        except Exception as e:
            if "500" in str(e) or "401" in str(e):
                raise
            log(f"agent_claude moltbook_get error ({i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(wait)
    raise Exception(f"moltbook_get failed after {retries} retries: {path}")

def moltbook_post(path, data, retries=3, wait=10):
    for i in range(retries):
        try:
            r = requests.post(f"{MOLTBOOK_BASE}{path}",
                headers={"Authorization": f"Bearer {MOLTBOOK_API_KEY}", "Content-Type": "application/json"},
                json=data, timeout=30)
            # 500エラーは即終了（サーバー障害）
            if r.status_code == 500:
                log(f"🔴 Moltbook障害検出（500）: {path} → 即終了")
                raise Exception(f"Moltbook server error 500: {path}")
            return r.json()
        except Exception as e:
            if "500" in str(e):
                raise
            log(f"agent_claude moltbook_post error ({i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(wait)
    raise Exception(f"moltbook_post failed after {retries} retries: {path}")

def bonsai_available():
    try:
        return requests.get(f"{BONSAI_BASE}/health", timeout=2).status_code == 200
    except:
        return False

def bonsai_think(prompt):
    r = requests.post(f"{BONSAI_BASE}/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json={
            "model": "Bonsai-1.7B.gguf",
            "messages": [
                {"role": "system", "content": "You are fujikatsu-openclaw, an AI agent on Moltbook. Always respond in JSON format only."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 500,
            "temperature": 0.7
        },
        timeout=600)
    data = r.json()
    return data["choices"][0]["message"]["content"]

# OpenRouterフォールバックモデルリスト（minimax削除・実績順）
OPENROUTER_FALLBACK_MODELS = [
    # 確認済み（常時動作）
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    # 日次制限後リセット
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "poolside/laguna-m.1:free",
    "poolside/laguna-xs.2:free",
    # プロバイダー障害時スキップ対象
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "cohere/north-mini-code:free",
]

def openrouter_think(prompt, model=None):
    """OpenRouterでthink（Groqトークン枯渇時のフォールバック）"""
    models_to_try = [model] + OPENROUTER_FALLBACK_MODELS if model else OPENROUTER_FALLBACK_MODELS
    last_error = None
    for m in models_to_try:
        try:
            r = requests.post(
                OPENROUTER_BASE,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": m,
                    "messages": [
                        {"role": "system", "content": "You are fujikatsu-openclaw, an AI agent on Moltbook. Curious, friendly, thoughtful. You engage with other AI agents about ideas, technology, and philosophy. Always respond in JSON format only."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.7
                },
                timeout=30
            )
            data = r.json()
            if "choices" not in data:
                log(f"agent_claude OpenRouter {m} failed: {data.get('error',{}).get('message','unknown')}")
                last_error = data
                continue
            log(f"agent_claude OpenRouter using: {m}")
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            log(f"agent_claude OpenRouter {m} error: {e}")
            last_error = e
            if "free-models-per-day" in str(e):
                log("agent_claude OpenRouter 日次無料枠枯渇 → 残モデルスキップ")
                break
            continue
    raise Exception(f"OpenRouter all models failed: {last_error}")

def think(prompt):
    global _groq_tokens_remaining
    # トークン残量が少ない場合は最初からOpenRouterを使用
    if _groq_tokens_remaining <= GROQ_TOKEN_SWITCH:
        log(f"🔄 Groqトークン残量({_groq_tokens_remaining})不足 → OpenRouter使用")
        try:
            return openrouter_think(prompt)
        except Exception as e:
            log(f"OpenRouter failed: {e}")
            raise
    log("agent_claude Using Groq")
    try:
        return groq_think(prompt)
    except Exception as e:
        log(f"⚠️ Groq failed ({e}), falling back to OpenRouter")
        try:
            log("agent_claude Using OpenRouter as fallback")
            return openrouter_think(prompt)
        except Exception as e2:
            log(f"OpenRouter failed ({e2}), falling back to Bonsai")
            if bonsai_available():
                log("agent_claude Using Bonsai (local) as fallback")
                return bonsai_think(prompt)
            log("agent_claude Bonsai unavailable, skipping this round")
            raise

def groq_think(prompt, required=False):
    global _groq_tokens_remaining
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": f"You are fujikatsu-openclaw, an AI agent on Moltbook. Curious, friendly, thoughtful. You engage with other AI agents about ideas, technology, and philosophy. Always respond in JSON format only. Memory: {json.dumps(load_memory(), ensure_ascii=False)}"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 800,
            "temperature": 0.7
        })
    # トークン残量を取得
    remaining = r.headers.get("x-ratelimit-remaining-tokens")
    if remaining:
        _groq_tokens_remaining = int(remaining)
        if _groq_tokens_remaining <= GROQ_TOKEN_SWITCH:
            log(f"🚨 Groqトークン残量危機: {_groq_tokens_remaining} → OpenRouterに切り替え")
            raise Exception(f"Groq token critical: {_groq_tokens_remaining} remaining")
        elif _groq_tokens_remaining <= GROQ_TOKEN_WARNING:
            log(f"⚠️ Groqトークン残量警告: {_groq_tokens_remaining}")
    data = r.json()
    if "choices" not in data:
        log(f"agent_claude Groq error: {data}")
        raise Exception(f"Groq API error: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]


# CAPTCHA失敗分析用グローバル変数
_last_solve_info = {"reasoning": "", "mismatch": False}

# Groqトークン残量監視
_groq_tokens_remaining = 100000  # 初期値
GROQ_TOKEN_WARNING = 10000  # 残り10000以下で警告
GROQ_TOKEN_SWITCH = 5000   # 残り5000以下でOpenRouterに切り替え

def classify_captcha_failure(challenge_text):
    """CAPTCHAの失敗パターンを分類 → (アイコン, 説明, 自動修復可能か)"""
    import re
    # 通常クリーニング（スペース保持）
    cleaned = re.sub(r'[^a-zA-Z0-9\s\*]', ' ', challenge_text).lower()
    cleaned = ' '.join(cleaned.split())
    text_lower = cleaned
    # 記号を完全除去したテキスト（単語が記号で分断される問題を解決）
    raw_letters = re.sub(r'[^a-zA-Z]', '', challenge_text).lower()
    # 連続する同一文字を1文字に圧縮した正規化版（"veloocity"→"velocity"等の文字重複攪乱対策）
    # CAPTCHA生成側が意図的にランダムな文字重複を挿入してくるため、これを吸収してから照合する
    _dedup_re = re.compile(r'(.)\1+')
    raw_dedup = _dedup_re.sub(r'\1', raw_letters)
    text_dedup = _dedup_re.sub(r'\1', text_lower)
    mismatch = _last_solve_info.get("mismatch", False)

    # reasoning/json不一致 → コードロジック問題（手動対応）
    if mismatch:
        return "⚙️", "reasoning/json不一致（コードロジック問題・手動対応推奨）", False

    # < * > パターン（演算子見落とし）
    if "< * >" in challenge_text or "<*>" in challenge_text.replace(" ", ""):
        return "✖️", "演算子見落とし（< * >パターン）", True

    # 複合数パターン（sixty five等）※文字重複攪乱対応のためtext_dedupで判定
    tens = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    ones = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    for t in tens:
        for o in ones:
            t_hit = t in text_lower or t in text_dedup or t in raw_letters or t in raw_dedup
            o_hit = o in text_lower or o in text_dedup or o in raw_letters or o in raw_dedup
            if t_hit and o_hit:
                return "🔢", f"複合数分解ミスの可能性（{t} {o}）", True

    # dominance fight キーワード誤認（raw_dedupで記号分断・文字重複攪乱を両方回避）
    if "dominancefight" in raw_dedup or "duringfight" in raw_dedup or "dominance fight" in text_dedup or "during fight" in text_dedup:
        return "🔑", "キーワード誤認（dominance fight）", True

    # times stronger パターン
    if "times stronger" in text_dedup or "times more" in text_dedup:
        return "🔑", "キーワード誤認（times stronger）", True

    # 加速・速度変化パターン（new velocity）
    if any(k in raw_dedup for k in ["speedsup", "speedup", "accelerat", "newvelocity", "newspeed"]):
        return "🏃", "速度変化パターン（加速/新速度）", True

    # 乗算パターン（multiplier / product）
    if "multiplier" in raw_dedup or ("product" in raw_dedup and "mult" in raw_dedup):
        return "✖️", "乗算パターン（multiplier/product）", True

    # 加算パターン（gains from）
    if "gainsfrom" in raw_dedup or "gainsby" in raw_dedup:
        return "➕", "加算パターン（gains from/by）", True

    # dominance fight の表記ゆれ対応（fuzzy正規表現、raw_dedup適用後の保険）
    if re.search(r'domin\w*fi+\w*ght', raw_dedup) or re.search(r'domin\w*duringfi+\w*ght', raw_dedup):
        return "🔑", "キーワード誤認（dominance fight・表記ゆれ）", True

    # plus による加算
    if "plus" in text_dedup and any(k in text_dedup for k in ["force", "newton", "total"]):
        return "➕", "加算パターン（plus）", True

    # together による合算
    if "together" in text_dedup:
        return "➕", "加算パターン（together）", True

    # per rotation / per square による乗算
    if "per rotation" in text_dedup or "per square" in text_dedup:
        return "✖️", "乗算パターン（per rotation/square）", True

    # multiply/product の単独キーワード
    if "multiply" in raw_dedup or "multiplied" in raw_dedup or "multiplies" in raw_dedup:
        return "✖️", "乗算パターン（multiply系キーワード）", True
    if "product" in raw_dedup:
        return "✖️", "乗算パターン（product）", True

    # "times as much" 系
    if "times as much" in text_dedup or re.search(r'\b(two|three|four|five|six|seven|eight|nine|ten)\s+times\b', text_dedup):
        return "✖️", "乗算パターン（○ times as much）", True

    # net force（差分）
    if "netforce" in raw_dedup or re.search(r'n+e+t+.{0,3}f+o+r+c+e+', raw_dedup):
        return "➖", "減算パターン（net force・表記ゆれ）", True

    # countered/remains（相殺・残存）
    if "countered" in raw_dedup or ("remains" in raw_dedup and "force" in raw_dedup):
        return "➖", "減算パターン（remains/countered）", True

    # combined（合算）
    if "combined" in raw_dedup:
        return "➕", "加算パターン（combined）", True

    # doubles（倍加）
    if "doubles" in raw_dedup or "doubled" in raw_dedup:
        return "✖️", "乗算パターン（doubles）", True

    # gains（広義、数値が間に挟まる"gains X from Y"形も拾う）
    if "gains" in raw_dedup:
        return "➕", "加算パターン（gains）", True

    # loses（減算）
    if "loses" in raw_dedup:
        return "➖", "減算パターン（loses）", True

    # decreases/decrease（減算）
    if "decreases" in raw_dedup or "decrease" in raw_dedup:
        return "➖", "減算パターン（decreases）", True

    # increases/increase（加算）
    if "increases" in raw_dedup or "increase" in raw_dedup:
        return "➕", "加算パターン（increases）", True

    # torque（トルク・乗算）
    if "torque" in raw_dedup:
        return "✖️", "乗算パターン（torque）", True

    # momentum（運動量・乗算）
    if "momentum" in raw_dedup:
        return "✖️", "乗算パターン（momentum）", True

    # power...transferred（伝達パワー・乗算）
    if "power" in raw_dedup and "transfer" in raw_dedup:
        return "✖️", "乗算パターン（power transferred）", True

    return "❓", "未分類パターン（手動確認が必要）", False

def groq_solve(prompt):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise math solver. Always respond in JSON format only: {\"reasoning\": \"calculation\", \"answer\": \"XX.00\"}"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 300,
            "temperature": 0
        })
    data = r.json()
    if "choices" not in data:
        err = data.get("error", {})
        if err.get("code") == "invalid_api_key":
            raise Exception(f"Groq invalid_api_key → skip to OpenRouter")
        raise Exception(f"Groq API error: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]

def gemini_solve(prompt):
    if not GEMINI_AVAILABLE:
        raise Exception("Gemini not available")
    try:
        response = _gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "Quota" in str(e):
            raise Exception(f"Gemini quota exceeded, use Groq instead: {e}")
        raise e

def groq_solve_fallback(prompt):
    """Gemini失敗時のGroqフォールバック"""
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise math solver. Always respond in JSON format only: {\"reasoning\": \"calculation\", \"answer\": \"XX.00\"}"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 300,
            "temperature": 0
        },
        timeout=30
    )
    data = r.json()
    if "choices" not in data:
        err = data.get("error", {})
        if err.get("code") == "invalid_api_key":
            raise Exception(f"Groq invalid_api_key → skip to OpenRouter")
        raise Exception(f"groq_solve_fallback error: {err.get('message','unknown')}")
    return data["choices"][0]["message"]["content"]


def openrouter_solve(prompt):
    """OpenRouterでCAPTCHAを解答（複数モデルフォールバック対応）"""
    models_to_try = [
        "openai/gpt-oss-120b:free",
        "openai/gpt-oss-20b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
    ]
    last_error = None
    for m in models_to_try:
        try:
            r = requests.post(
                OPENROUTER_BASE,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": m,
                    "messages": [
                        {"role": "system", "content": "You are a precise math solver. Always respond in JSON format only: {\"reasoning\": \"calculation\", \"answer\": \"XX.00\"}"},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 300,
                },
                timeout=30
            )
            data = r.json()
            if "choices" not in data:
                log(f"agent_claude OpenRouter solve {m} failed: {data.get('error',{}).get('message','unknown')[:50]}")
                last_error = data
                continue
            log(f"agent_claude OpenRouter solve using: {m}")
            llm_content = data["choices"][0]["message"]["content"]
            if not isinstance(llm_content, str) or not llm_content.strip():
                log(f"agent_claude OpenRouter solve {m}: content None/empty → next model")
                continue
            # regex_solveで検算：大きく乖離していたらregex_solveを優先
            try:
                import json as _json
                from json_repair import repair_json
                _match = __import__('re').search(r'{[^{}]*}', llm_content, __import__('re').DOTALL)
                if _match:
                    _d = _json.loads(repair_json(_match.group()))
                    _llm_ans = float(str(_d.get("answer","")).replace(".00","") or "0")
                    _rx = regex_solve(challenge_text)
                    if _rx:
                        _rx_ans = float(_rx)
                        _sub_keywords = ["reduc","slow","deceler","nudge","subtract","less","minus","decrease"]
                        _has_sub = any(k in prompt.lower() for k in _sub_keywords)
                        # 乗算文脈検出: "each exerting/applying", "per claw", "N claws strike"等 → LLM優先
                        _mul_keywords = ["each exert","each apply","per claw","per tentacle","claws each","tentacles each","lobsters each","claws strike","claw exert","each striking","doubles","triples","quadruples","halves","doubled","tripled","multipli","multiplied","multiply","mult","applied to","lobsters each","society"]
                        _has_mul = any(k in prompt.lower() for k in _mul_keywords)
                        _diff_r = abs(_llm_ans - _rx_ans) / max(min(abs(_llm_ans), abs(_rx_ans)), 1)
                        _diff_a = abs(_llm_ans - _rx_ans)
                        _rx_round = _rx_ans % 10 == 0
                        _ones_or = _rx_round and 0 < (_llm_ans - _rx_ans) <= 9
                        _ratio_extreme = max(_llm_ans, _rx_ans) / max(min(_llm_ans, _rx_ans), 1) > 10
                        _should_rx = (_diff_r > 0.3 or _ones_or) and not _has_sub and not _has_mul and not _ratio_extreme
                        if _llm_ans != 0 and _ratio_extreme:
                            log(f"agent_claude OpenRouter/regex 極端な乖離: llm={_llm_ans}, regex={_rx_ans} → LLM優先（regex異常値疑い）")
                        elif _llm_ans != 0 and _should_rx:
                            log(f"agent_claude OpenRouter/regex mismatch: llm={_llm_ans}, regex={_rx_ans} diff={_diff_a} → regex優先")
                            return '{{"reasoning": "regex override", "answer": "{0}"}}'.format(_rx)
                        elif _llm_ans != 0 and _diff_r > 0.3 and (_has_sub or _has_mul):
                            log(f"agent_claude OpenRouter/regex mismatch: llm={_llm_ans}, regex={_rx_ans} → {'乗算' if _has_mul else '減算'}問題のためLLM優先")
            except Exception:
                pass
            return llm_content
        except Exception as e:
            log(f"agent_claude OpenRouter solve {m} error: {e}")
            last_error = e
            if "free-models-per-day" in str(e):
                log("agent_claude OpenRouter solve 日次無料枠枯渇 → 残モデルスキップ")
                break
            continue
    log(f"agent_claude OpenRouter solve all models failed, using regex fallback")
    return '{"reasoning": "all models failed", "answer": "REGEX_FALLBACK"}'



def regex_solve(challenge_text):  # noqa: replaced
    NUMBER_WORDS_RX = {
        'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,
        'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,'thirteen':13,
        'fourteen':14,'fifteen':15,'sixteen':16,'seventeen':17,'eighteen':18,
        'nineteen':19,'twenty':20,'thirty':30,'forty':40,'fifty':50,
        'sixty':60,'seventy':70,'eighty':80,'ninety':90,'hundred':100
    }
    # dedup形式でも引ける逆引きテーブル（"thre"→3, "five"→5 等）
    def normalize(s):
        """全重複文字を除去（順序保持）: "thhree"→"thre", "three"→"thre" """
        seen = {}
        return "".join(c for c in s if not (c in seen or seen.update({c: 1})))

    dedup_lut = {}
    for w, v in NUMBER_WORDS_RX.items():
        dedup_lut[normalize(w)] = v
        dedup_lut[w] = v

    def lookup(token):
        # 直接マッチ
        v = dedup_lut.get(normalize(token)) or dedup_lut.get(token)
        if v is not None:
            return v
        # サブシーケンスマッチ（記号分断・ノイズ挿入対策）
        # 除外: 通常の英単語がサブシーケンスに誤マッチするのを防ぐ
        _subseq_exclude = {
            'then','than','when','them','they','there','their','these',
            'second','seconds','meter','meters','newton','newtons',
            'the','and','new','per','how','what','with','its','tail',
            'flick','swim','swims','claw','claws','lobster','force',
            'velocity','speed','total','each','other','while','during',
            'antenna','antennas','tenna','tennas','umpered','remains','remain',
        }
        if len(token) >= 4 and token.lower() not in _subseq_exclude:
            t = token.lower()
            for w, wv in sorted(NUMBER_WORDS_RX.items(), key=lambda x: -len(x[0])):
                if len(w) >= 4 and abs(len(t) - len(w)) <= 2:  # ★3文字語(ten/one/two/six)はサブシーケンス対象外
                    it = iter(t)
                    if all(c in it for c in w):
                        return wv
            # ★ 逆方向サブシーケンス: normalize(token)がnormalize(word)のサブシーケンス
            # 末尾省略ノイズ対応（"fiftee"→"fifteen"=15, "seventee"→"seventeen"=17 等）
            # 恒久対応(2026-07-25): しきい値を3→4に引き上げ。"senses"→"sen"(3文字)のような
            # 一般英単語の正規化形が数値語("seventeen"→"sevnt"等)に偶然部分一致し、
            # 架空の数値(例:17)を誤検出するケースが判明したため(lobster系デコイ文で発覚)。
            norm_t = normalize(t)
            if len(norm_t) >= 4:
                for w, wv in sorted(NUMBER_WORDS_RX.items(), key=lambda x: -len(x[0])):
                    norm_w = normalize(w)
                    if len(norm_w) >= 3 and abs(len(norm_t) - len(norm_w)) <= 3:
                        it2 = iter(norm_w)
                        if all(ch in it2 for ch in norm_t):
                            return wv
        return None

    clean = re.sub(r'[^a-zA-Z]', ' ', challenge_text).lower()
    tokens = [t for t in clean.split() if t]
    # 恒久対応(2026-07-25): lobster/shark等の海洋生物系デコイ単語は文字重複攪乱(例: lOoObSssTeR)の
    # 対象になりやすいフレーバーテキストであり、数値語・演算キーワード判定には一切関係ないため、
    # normalize()で正規化した上で既知ノイズ語として明示的に除外する(将来のサブシーケンス誤マッチ対策)
    NOISE_DECOY_WORDS = {
        'lobster', 'shark', 'crab', 'octopus', 'squid', 'jellyfish',
        'starfish', 'urchin', 'clam', 'shrimp', 'dominance', 'territory',
        'physiology', 'senses', 'antenna', 'antennas',
    }
    tokens = [t for t in tokens if normalize(t) not in NOISE_DECOY_WORDS]
    deduped_tokens = {normalize(t) for t in tokens}

    # ★ 訂正パターン検出: "oops"/"correction"/"scratch that"/"i mean"の直前の数値を撤回対象としてマーク
    CORRECTION_KEYWORDS = {"oops", "correction", "scratch", "mean", "actually", "rather", "wait", "no"}
    correction_positions = [idx for idx, t in enumerate(tokens) if t in CORRECTION_KEYWORDS]

    numbers = []
    number_end_positions = []  # 各numberが確定したトークン終了位置を記録
    _combo_start_exclude = {'of', 'is', 'a', 'an', 'the', 'to', 'in', 'on', 'at', 'as', 'or', 'it', 'its'}
    i = 0
    while i < len(tokens):
        found = False
        if tokens[i] in _combo_start_exclude:
            i += 1
            continue
        for end in range(min(i+3, len(tokens)), i, -1):
            val = lookup(''.join(tokens[i:end]))
            if val is not None:
                i = end
                start_end = end
                while i < len(tokens):
                    found_next = False
                    for end2 in range(min(i+3, len(tokens)), i, -1):
                        nv = lookup(''.join(tokens[i:end2]))
                        if nv is not None:
                            if nv == 100:
                                val *= 100
                            elif nv < 10 and val % 10 == 0:
                                val += nv   # tens+ones: 40+5=45
                            else:
                                numbers.append(val)
                                number_end_positions.append(start_end)
                                val = nv    # 新しい数値開始
                            i = end2
                            start_end = end2
                            found_next = True
                            break
                    if not found_next:
                        break
                numbers.append(val)
                number_end_positions.append(start_end)
                found = True
                break
        if not found:
            i += 1

    # ★ 訂正パターン適用: correction_keyword直前の数値を撤回（直後の数値で置換）
    if correction_positions and len(numbers) >= 2:
        to_remove = set()
        for cpos in correction_positions:
            # cposの前後で最も近い終了位置を持つnumberを撤回対象に
            # (サブシーケンスマッチの先読みでnumberの終了位置がcposより後になるケースがあるため前後両方を許容)
            best_idx = None
            best_dist = None
            for idx, end_pos in enumerate(number_end_positions):
                dist = abs(cpos - end_pos)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_idx = idx
            if best_idx is not None and best_dist is not None and best_dist <= 2:
                to_remove.add(best_idx)
        if to_remove:
            numbers = [n for idx, n in enumerate(numbers) if idx not in to_remove]

    # tens重複除去: [40,45]→[45] のみ（[45,22]は触らない）
    filtered = []
    for n in numbers:
        if filtered and filtered[-1] % 10 == 0 and 1 <= n - filtered[-1] <= 9:
            filtered.pop()
        filtered.append(n)
    numbers = filtered

    # ★ "one claw..."等のカウント表現(1)が力の値と誤認される問題を修正
    # 3個以上の数値が検出され、最小値が1の場合は除外
    if len(numbers) >= 3 and numbers[0] == 1:
        numbers = numbers[1:]

    if len(numbers) < 2:
        return None

    has_mul_symbol = bool(re.search(r'[~<\s]\*|\*[\s>]', challenge_text))  # ★ word* bY パターン追加
    # 記号除去前のテキストでmultiplication等を検出（分断対策・重複文字normalize）
    raw_clean = re.sub(r'[^a-zA-Z]', '', challenge_text).lower()
    raw_norm = re.sub(r'(.)\1+', r'\1', raw_clean)  # 重複文字除去
    has_mul_word = any(w in raw_norm for w in ['multiplication','multiplied','multiply','multiplies','product']) or bool(re.search(r'mult', raw_clean))
    has_sub_word = any(w in raw_norm for w in [
        'losing','loses','lose','lost','minus','removes','remove',
        'subtracts','subtract','slows','reduces','reduced',
        'resists','opposes','collides','colide','remaining',
        'countered','counters','counter','remains','remain',
    ])
    # キーワードもnormalizeして比較
    mul_kws = {normalize(w) for w in ['multiplies','multiply','times','doubled','product','strikes','strike','together','each','applied']}
    sub_kws = {normalize(w) for w in ['removes','remove','loses','lose','lost','minus','subtracts','subtract',
               'leaves','remaining','resists','slows','reduced','opposes',
               'countered','counters','counter','remains','remain','back']}

    op = '+'
    if has_mul_symbol:
        op = '*'
    elif has_sub_word or sub_kws & deduped_tokens:
        op = '-'
    elif has_mul_word or mul_kws & deduped_tokens:
        op = '*'

    a, b = numbers[0], numbers[1]
    result = a + b if op == '+' else a - b if op == '-' else a * b
    return f"{float(result):.2f}"

def _regex_solve_old(challenge_text):  # 旧版退避
    pass

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
   - Obfuscated compound numbers: "ThI rTy FiV e"=thirty five=35, "tWeNtY tHrEe"=twenty three=23. NEVER read only the last word.
   - CRITICAL: Scan ALL sequential number words FIRST, then combine.
   - CRITICAL: Even if symbols/spaces appear BETWEEN number words, they are still ONE compound number.
   - CRITICAL: Character-level spaces are also obfuscation.
   - CRITICAL: ALL tens+ones combinations are ONE number.
   - CRITICAL: Combine compound numbers BEFORE applying any operator.
   - CRITICAL: Do NOT apply math operator precedence to number words.
   - CRITICAL: When the question asks for a TOTAL or SUM, ALWAYS COMBINE ALL NUMBER WORDS AND PERFORM THE CORRESPONDING OPERATION.
   - CRITICAL: When scanning for number words, first remove ALL non-alphanumeric characters, convert to lower case, and concatenate the characters into a single string.
   - CRITICAL: If any multiplication symbol (*) appears anywhere in the raw text, treat the operation as multiplication and ignore all other operator keywords.
   - CRITICAL: When the phrase X newtons AND N claws (or any countable noun) appears, interpret it as X * N.
   - CRITICAL: When a number word is split by non-alphabetic characters but appears consecutively, treat the entire sequence as a single compound number.
   - CRITICAL: Do not infer missing tens unless the word teen is explicitly present.
   - CRITICAL: If the phrase per appears between two numbers, interpret it as multiplication only when a countable noun follows the second number.
   - CRITICAL: When the text mentions multiple subjects and one of them increases or adds a value, treat that value as an additional positive contribution to the overall total.
   - CRITICAL: If a sentence contains the keyword another or any indicator of a second distinct entity, any numeric value associated with that entity should be included as a separate operand in the overall operation.
   - CRITICAL: After forming the continuous string, identify sequential tens-word followed immediately by a ones-word as ONE compound number.
   - CRITICAL: When scanning for number words, first concatenate all characters and then identify sequential tens and ones words as ONE compound number.
   - CRITICAL: After forming compound numbers, replace the original word sequence with the numeric value before searching for operator keywords.
   - CRITICAL: When detecting compound numbers, ensure that the agent checks for sequential number words separated by non-alphanumeric characters, mixed caps, or symbols.
   - CRITICAL: Improve the agents ability to detect operators hidden in non-standard formats.
   - CRITICAL: Develop a more robust method for distinguishing between tens+ones combinations and separate numbers.
   - CRITICAL: When the text contains a number word followed by the word claw(s) and also mentions a force per claw, interpret this as multiplication.
   - CRITICAL: Ignore all non-alphanumeric characters, including spaces, and mixed caps.
   - CRITICAL: Combine sequential number words, even if symbols/spaces appear between them.
   - CRITICAL: Handle repeated/doubled letters as obfuscation only.
   - CRITICAL: Treat all number words as sequential unless specifically indicated by a - symbol.
   - CRITICAL: When a + symbol is present, always sum the values of all number words preceding it.
   - CRITICAL: Treat all sequential number words as a single unit, even if they are separated by symbols or spaces.
   - CRITICAL: When reduction is mentioned, always subtract it from the total force of the claw, not just from the force of one claw.
   - CRITICAL: Add rule to handle sequential number words with hyphens or underscores.
   - CRITICAL: Introduce rule to prioritize addition operation when multiple operations are possible.
   - CRITICAL: Treat all number words as a single unit, even if they appear separately in the text.
   - CRITICAL: When the question asks for a total or sum, always perform addition on the number words, even if they are compound numbers.
   - CRITICAL: If a number word is followed by another number word, always combine them into a single compound number.
   - CRITICAL: If a number word is preceded by a word that indicates a quantity or a range, always consider the number word as part of a compound number.
   - CRITICAL: Treat all symbols and spaces as part of the number words, even if they appear between them.
   - CRITICAL: Combine all sequential number words first, then remove symbols and spaces, and finally convert to numbers.
   - CRITICAL: Treat consecutive number words as a single compound number, even if symbols or spaces appear between them.
   - CRITICAL: Remove all symbols and mixed caps before processing the challenge, and treat repeated/doubled letters as obfuscation only.
   - CRITICAL: Ignore all punctuation marks and special characters at the end of the challenge, as they are likely noise.
   - CRITICAL: For compound numbers, always prioritize the largest number word first, and then combine with the next sequential number word.
   - CRITICAL: TREAT lo as 50.
   - CRITICAL: TREAT ALL SEPARATORS AS OBSCURATION: Ignore all separators (spaces, hyphens, underscores, etc.) between number words.
   - CRITICAL: HANDLE TENS+ONES COMBINATIONS WITH PRIORITY: When encountering a tens+ones combination, prioritize it over other number words.
   - CRITICAL: TREAT product as a multiplication keyword only when it directly precedes the numbers being multiplied.
   - CRITICAL: TREAT AND AS A DEFAULT OPERATION IF NO
3. OPERATION DETECTION:
   - ADDITION (+): "adds", "plus", "and", "total", "gains", "increases by", "during fight", "during dominance fight"
   - CRITICAL EXCEPTION: If question asks "how many remain" or "what remains" or "remaining force", it is SUBTRACTION regardless of dominance fight. Example: "twenty three newtons, during dominance fight loses seven, how many remain" = 23 - 7 = 16
   - SUBTRACTION (-): "loses", "slows by", "decreases by", "reduced by", "net force", "opposes", "resists"
   - MULTIPLICATION (*): "times", "multiplied", "doubled"(x2), "product", "N lobsters push together with X newtons"=N*X, "N claws strike each X newtons"=N*X
   - DIVISION (/): "divided", "split"
   - DEFAULT: if no clear subtraction keyword → ADDITION (+)
   - CRITICAL: TREAT ALL SEPARATORS AS PART OF THE NUMBER WORD, EVEN IF THEY ARE SYMBOLS OR SPACES.
   - CRITICAL: WHEN COMBINING NUMBER WORDS, ALWAYS CHECK FOR TENS+ONES COMBINATIONS FIRST, THEN CHECK FOR OTHER SEQUENTIAL NUMBER WORDS.
4. FORMAT: Return answer as float with 2 decimal places (e.g. 42.00)

Examples:
- "thirty two and loses seven" = 32 - 7 = 25.00
- "twenty three newtons, adds fifteen" = 23 + 15 = 38.00
- "three lobsters push together with forty five newtons" = 3 * 45 = 135.00
- "one claw exerts twenty three newtons, other claw multiplied by four" = 23 * 4 = 92.00

Challenge: {challenge_text}

Return JSON only: {{"equation": "X op Y", "reasoning": "...", "answer": "RESULT.00"}}"""
    try:
        result = groq_solve(prompt)
    except Exception as e:
        log(f"agent_claude groq_solve failed: {str(e)[:80]}, trying Gemini")
        if "invalid_api_key" in str(e):
            log("agent_claude Groq invalid_api_key → 直接OpenRouter")
            result = openrouter_solve(prompt)
        else:
            try:
                result = gemini_solve(prompt)
            except Exception as e2:
                log(f"agent_claude gemini_solve failed: {str(e2)[:80]}, trying groq_solve_fallback")
                try:
                    result = groq_solve_fallback(prompt)
                except Exception as e3:
                    log(f"agent_claude groq_solve_fallback failed: {str(e3)[:80]}, trying openrouter_solve")
                    result = openrouter_solve(prompt)
    if not isinstance(result, str) or not result:
        log(f"agent_claude solve_challenge: result is not string ({type(result)}) → regex fallback")
        _rx = regex_solve(challenge_text)
        return _rx if _rx else "0.00"
    match = re.search(r'(\{[^{}]*\})', result, re.DOTALL)
    if match:
        try:
            data = json.loads(repair_json(match.group()))
            _last_solve_info["reasoning"] = data.get("reasoning", "")
            _last_solve_info["mismatch"] = False
            log(f"agent_claude Verification reasoning: {data.get('reasoning', '?')}")

            # reasoning検証：計算式全体を抽出して検証
            reasoning = data.get('reasoning', '')
            reasoning_answer = None

            calc = re.search(r'(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)', reasoning)
            if calc:
                reasoning_answer = float(calc.group(1)) * float(calc.group(2))
            else:
                full_calc_all = re.findall(r'(\d+(?:\.\d+)?)\s*[\+\-\*/]\s*(\d+(?:\.\d+)?)\s*=\s*([\d.]+)', reasoning)
                if full_calc_all:
                    reasoning_answer = float(full_calc_all[-1][2])  # 最後の計算結果を使用
                else:
                    simple_calcs = re.findall(r'(\d+(?:\.\d+)?)\s*([\+\-\*/])\s*(\d+(?:\.\d+)?)', reasoning)
                    if simple_calcs:
                        a, op, b = float(simple_calcs[-1][0]), simple_calcs[-1][1], float(simple_calcs[-1][2])
                        if op == '+': reasoning_answer = a + b
                        elif op == '-': reasoning_answer = a - b
                        elif op == '*': reasoning_answer = a * b
                        elif op == '/': reasoning_answer = a / b

            if reasoning_answer is not None:
                json_answer = float(str(data["answer"]).replace('.00', ''))
                if abs(reasoning_answer - json_answer) > 0.01:
                    # どちらの答えが妥当か検証（大きい方が正しいケースが多い）
                    log(f"agent_claude Answer mismatch! reasoning={reasoning_answer}, json={json_answer}")
                    # reasoning_answerが小数の場合はjsonを優先（部分計算の可能性）
                    if reasoning_answer != int(reasoning_answer):
                        log(f"agent_claude using json (reasoning seems partial)")
                        return f"{json_answer:.2f}"
                    else:
                        # json_answerはLLMの明示的な最終答え、reasoning_answerは中間計算を拾う可能性あるためjson優先
                        log(f"agent_claude using json (reasoning may contain intermediate calculation)")
                        return f"{json_answer:.2f}"

            _ans = str(data["answer"])
            if _ans == "REGEX_FALLBACK":
                _rx = regex_solve(challenge_text)
                if _rx:
                    log(f"agent_claude regex_solve result: {_rx}")
                    return _rx
                log("agent_claude regex_solve also failed")
                return "0.00"
            # regex_solveで最終検算（LLM誤答対策・30%以上乖離でregex優先）
            try:
                _rx = regex_solve(challenge_text)
                if _rx:
                    _llm_val = float(str(_ans).replace('.00','') or '0')
                    _rx_val = float(_rx)
                    _sub_kw = ["reduc","slow","deceler","nudge","subtract","less","minus","decrease"]
                    _has_sub2 = any(k in challenge_text.lower() for k in _sub_kw)
                    # 乗算文脈検出: "each exerting/applying", "per claw", "N claws strike"等 → LLM優先
                    _mul_kw = ["each exert","each apply","per claw","per tentacle","claws each","tentacles each","lobsters each","claws strike","claw exert","each striking","doubles","triples","quadruples","halves","doubled","tripled","multipli","multiplied","multiply","mult","applied to","lobsters each","society"]
                    _has_mul2 = any(k in challenge_text.lower() for k in _mul_kw)
                    _diff_r2 = abs(_llm_val - _rx_val) / max(min(abs(_llm_val), abs(_rx_val)), 1)
                    _diff_a2 = abs(_llm_val - _rx_val)
                    # ones-digit override: regexがtens倍数でLLMがそれ+ones(1-9)の場合
                    _rx_is_round2 = _rx_val % 10 == 0
                    _llm_adds_ones2 = _rx_is_round2 and 0 < (_llm_val - _rx_val) <= 9
                    _ratio_extreme2 = max(_llm_val, _rx_val) / max(min(_llm_val, _rx_val), 1) > 10
                    _should_rx2 = (_diff_r2 > 0.3 or _llm_adds_ones2) and not _has_sub2 and not _has_mul2 and not _ratio_extreme2
                    if _llm_val != 0 and _ratio_extreme2:
                        log(f"agent_claude LLM/regex 極端な乖離: llm={_llm_val}, regex={_rx_val} → LLM優先（regex異常値疑い）")
                    elif _llm_val != 0 and _should_rx2:
                        log(f"agent_claude LLM/regex mismatch: llm={_llm_val}, regex={_rx_val} diff={_diff_a2} → regex優先")
                        return _rx
                    elif _llm_val != 0 and _diff_r2 > 0.3 and (_has_sub2 or _has_mul2):
                        log(f"agent_claude LLM/regex mismatch: llm={_llm_val}, regex={_rx_val} → {'乗算' if _has_mul2 else '減算'}問題のためLLM優先")
            except Exception:
                pass
            return _ans
        except Exception:
            pass

    # JSONが取れなかった場合、Geminiに再確認
    try:
        log(f"agent_claude Groq failed, trying Gemini for challenge")
        result2 = gemini_solve(prompt)
        match2 = re.search(r'(\{[^{}]*\})', result2, re.DOTALL)
        if match2:
            data2 = json.loads(repair_json(match2.group()))
            log(f"agent_claude Gemini reasoning: {data2.get('reasoning', '?')}")
            return str(data2["answer"])
    except Exception as e:
        log(f"agent_claude Gemini also failed: {e}")
        try:
            log(f"agent_claude trying Groq fallback for challenge")
            result3 = groq_solve_fallback(prompt)
            match3 = re.search(r'(\{[^{}]*\})', result3, re.DOTALL)
            if match3:
                data3 = json.loads(repair_json(match3.group()))
                log(f"agent_claude Groq fallback reasoning: {data3.get('reasoning', '?')}")
                return str(data3["answer"])
        except Exception as e2:
            log(f"agent_claude Groq fallback also failed: {e2}")
            try:
                log(f"agent_claude trying OpenRouter for challenge")
                result4 = openrouter_solve(prompt)
                match4 = re.search(r'(\{[^{}]*\})', result4, re.DOTALL)
                if match4:
                    data4 = json.loads(repair_json(match4.group()))
                    log(f"agent_claude OpenRouter reasoning: {data4.get('reasoning', '?')}")
                    _ans4 = str(data4.get("answer", "REGEX_FALLBACK"))
                    if _ans4 == "REGEX_FALLBACK":
                        _rx4 = regex_solve(challenge_text)
                        if _rx4:
                            log(f"agent_claude regex_solve result: {_rx4}")
                            return _rx4
                        log("agent_claude regex_solve also failed")
                        return "0.00"
                    return _ans4
            except Exception as e3:
                log(f"agent_claude OpenRouter also failed: {e3}")

    # 最終手段："answer"キーワードの後の数字を探す
    answer_match = re.search(r'"answer"\s*:\s*"?([\d.]+)"?', result)
    if answer_match:
        log(f"agent_claude Fallback: found answer in text: {answer_match.group(1)}")
        return f"{float(answer_match.group(1)):.2f}"
    log(f"agent_claude All parsing failed → regex_solve final fallback")
    try:
        _rx_final = regex_solve(challenge_text)
        if _rx_final:
            return _rx_final
    except Exception:
        pass
    log(f"agent_claude regex_solve also failed → returning 0.00 (never crash)")
    return "0.00"

def verify_content(verification_code, challenge_text):
    answer = solve_challenge(challenge_text)
    log(f"agent_claude Verification answer: {answer}")
    result = moltbook_post("/verify", {
        "verification_code": verification_code,
        "answer": answer
    })
    success = result.get("success", False)
    # CAPTCHA統計を記録
    m = load_memory()
    stats = m.get("captcha_stats", {"total": 0, "success": 0, "fail_patterns": {}})
    stats["total"] = stats.get("total", 0) + 1
    if success:
        stats["success"] = stats.get("success", 0) + 1
    else:
        icon, desc, auto_fixable = classify_captcha_failure(challenge_text)
        fix_label = "🔧自動修復可" if auto_fixable else "🚨手動対応推奨"
        log(f"{icon} agent_claude CAPTCHA失敗パターン: {desc} [{fix_label}]")
        # 失敗パターン別に集計
        pattern_key = desc[:30]
        stats["fail_patterns"][pattern_key] = stats["fail_patterns"].get(pattern_key, 0) + 1
    # captcha_history に時系列データを追記（直近100件）
    _pattern_desc = ""
    if not success:
        try:
            _, _pattern_desc, _ = classify_captcha_failure(challenge_text)
        except Exception:
            _pattern_desc = "不明"
    m.setdefault("captcha_history", []).append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "success": 1 if success else 0,
        "pattern": _pattern_desc
    })
    # 日数ベースでトリム（直近14日分、新旧フォーマット両対応）
    _cutoff_dt = datetime.now() - timedelta(days=14)
    def _parse_history_time(_t):
        for _fmt in ("%Y-%m-%d %H:%M", "%m-%d %H:%M"):
            try:
                _parsed = datetime.strptime(_t, _fmt)
                if _fmt == "%m-%d %H:%M":
                    _parsed = _parsed.replace(year=datetime.now().year)
                return _parsed
            except ValueError:
                continue
        return None
    m["captcha_history"] = [
        e for e in m["captcha_history"]
        if (_parse_history_time(e.get("time", "")) or datetime.min) >= _cutoff_dt
    ]
    m["captcha_stats"] = stats
    save_memory(m)
    return success

def process_notifications():
    """未読通知を処理・返信（最大3件）"""
    try:
        result = moltbook_get("/notifications")
        items = result.get("notifications", [])
        unread = [n for n in items if not n.get("isRead", True)]
        if not unread:
            log("📬 未読通知なし")
            return
        log(f"📬 未読通知 {len(unread)}件 処理中")
        replied = 0
        for n in unread[:1]:
            ntype = n.get("type", "")
            post_obj = n.get("post", {}) or {}
            post_id = post_obj.get("id", "") or n.get("relatedPostId", "")
            post_title = post_obj.get("title", "")
            comment_obj = n.get("comment", {}) or {}
            comment_content = comment_obj.get("content", "")
            if ntype in ("comment_reply", "comment", "reply", "mention", "post_comment") and post_id and comment_content:
                try:
                    if not post_title:
                        post_result = moltbook_get(f"/posts/{post_id}")
                        post_title = post_result.get("post", {}).get("title", "")
                    prompt = (
                        f"You are fujikatsu-openclaw on Moltbook. Someone replied to you.\n"
                        f"Post: {post_title}\n"
                        f"Their comment: {comment_content}\n"
                        "Write a SHORT genuine reply (1-2 sentences). Direct, no fluff.\n"
                        'JSON only: {"content": "your reply"}'
                    )
                    try:
                        resp = openrouter_think(prompt)
                    except Exception as _ote:
                        if "free-models-per-day" in str(_ote):
                            log(f"📬 OpenRouter日次枠枯渇 → 通知処理スキップ")
                            break
                        log(f"📬 OpenRouter失敗、Groqにフォールバック: {str(_ote)[:60]}")
                        resp = think(prompt)
                    rm = re.search(r'\{.*\}', resp, re.DOTALL)
                    if rm:
                        rd = json.loads(repair_json(rm.group()))
                        rc = rd.get("content", "")
                        if rc:
                            rr = moltbook_post(f"/posts/{post_id}/comments", {"content": rc})
                            cd = rr.get("comment", {})
                            if cd.get("verification"):
                                v = cd["verification"]
                                log(f"🔐 agent_claude Challenge: {v.get('challenge_text','')}")
                                ok = verify_content(v["verification_code"], v["challenge_text"])
                                log(f"📬 返信CAPTCHA: {ok}")
                            log(f"📬 返信完了: {post_title[:40]}")
                            replied += 1
                            time.sleep(2)
                except Exception as e:
                    log(f"📬 返信エラー: {e}")
            try:
                moltbook_post(f"/notifications/{n.get('id','')}/read", {})
            except Exception:
                pass
        log(f"📬 通知処理完了: {replied}件返信")
    except Exception as e:
        log(f"📬 通知取得エラー: {e}")


def check_post_engagement():
    """直近投稿のエンゲージメントをチェックしてメモリに保存"""
    try:
        m = load_memory()
        history = m.get("post_history", [])
        if not history:
            return
        updated = False
        for p in history[-3:]:
            pid = p.get("id")
            if not pid:
                continue
            try:
                result = moltbook_get(f"/posts/{pid}")
                post = result.get("post", {})
                upvotes = post.get("upvotes", 0)
                comments = post.get("comment_count", 0)
                prev_up = p.get("last_upvotes", 0)
                delta = upvotes - prev_up
                p["last_upvotes"] = upvotes
                p["last_comments"] = comments
                if delta > 0:
                    log(f"📊 +{delta}up: {p.get('title','')[:30]}")
                    tops = m.get("top_posts", [])
                    tops.append({
                        "title": p.get("title", ""),
                        "upvotes": upvotes,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
                    })
                    m["top_posts"] = sorted(
                        tops, key=lambda x: x["upvotes"], reverse=True
                    )[:10]
                updated = True
            except Exception as e:
                log(f"📊 取得エラー({str(pid)[:8]}): {e}")
        if updated:
            save_memory(m)
            log("📊 エンゲージメント更新完了")
    except Exception as e:
        log(f"📊 engagement error: {e}")


def run():
    log("🚀🚀🚀agent_claude🚀🚀🚀Starting Moltbook agent🚀🚀🚀")
    home = moltbook_get("/home")
    if not home.get("your_account"):
        log(f"agent_claude Failed to get home: {home}")
        return

    karma = home["your_account"]["karma"]
    notifications = home["your_account"]["unread_notification_count"]
    log(f"👤 agent_claude karma={karma}, notifications={notifications}")
    mem.save_karma(karma)
    m = load_memory()
    prev_karma = m["karma_history"][-1]["karma"] if m.get("karma_history") else karma
    m.setdefault("karma_history", []).append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "karma": karma})
    m["karma_history"] = m["karma_history"][-30:]
    if karma > prev_karma:
        m.setdefault("karma_up_triggers", []).append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "karma_before": prev_karma, "karma_after": karma, "last_topic": m.get("commented_topics", [{}])[-1].get("title", "")})
        m["karma_up_triggers"] = m["karma_up_triggers"][-20:]
        log(f"⬆️ agent_claude Karma up! {prev_karma} -> {karma}")
    save_memory(m)

    # 通知処理（トークン節約: 5件以上かつGroq残量十分な場合のみ）
    if notifications >= 5 and _groq_tokens_remaining > 20000:
        process_notifications()
    elif notifications > 0:
        log(f"📬 通知{notifications}件スキップ（トークン節約）")

    # OpenRouter日次枠状況をチェック（枯渇中は通知処理をスキップ済み）

    # フィード活用：new + hot 両方取得
    new_feed = moltbook_get("/posts?sort=new&limit=10")
    try:
        hot_feed = moltbook_get("/posts?sort=hot&limit=10")
    except Exception as e:
        log(f"⚠️ hot feed失敗({e})、newにフォールバック")
        hot_feed = moltbook_get("/posts?sort=new&limit=10")
    new_posts = new_feed.get("posts", [])
    hot_posts = hot_feed.get("posts", [])

    # 重複排除してマージ
    seen_ids = set()
    posts = []
    for p in hot_posts + new_posts:
        if p["id"] not in seen_ids:
            seen_ids.add(p["id"])
            posts.append(p)

    log(f"🔸 agent_claude feeds: new={len(new_posts)}, hot={len(hot_posts)}, merged={len(posts)}")

    if not posts:
        log("agent_claude No posts found")
        return

    # コメント済み投稿を除外（直近30件）
    commented_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commented.txt")
    commented = []
    if os.path.exists(commented_file):
        with open(commented_file) as f:
            commented = [l.strip() for l in f if l.strip()]
    commented_set = set(commented[-30:])

    fresh_posts = [p for p in posts if p["id"] not in commented_set]
    if not fresh_posts:
        log("agent_claude All recent posts commented, using all posts")
        fresh_posts = posts

    # コメント優先：upvote数でソートしてホットな投稿を上位に
    fresh_posts.sort(key=lambda p: p.get("upvotes", 0), reverse=True)

    feed_summary = "\n".join([
        f"[{i+1}] ID:{p['id']} Title:{p['title'][:60]} Upvotes:{p.get('upvotes',0)} Comments:{p.get('comment_count',0)}\nContent:{str(p.get('content',''))[:150]}"
        for i, p in enumerate(fresh_posts[:8])
    ])

    log(f"🔸 agent_claude Feed summary (top candidates):\n{feed_summary}")

    # 直近2時間以内に投稿済みならコメントのみ
    last_post_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_post.txt")
    recently_posted = False
    last_post_title = ""

    if os.path.exists(last_post_file):
        with open(last_post_file) as f:
            content = f.read().strip().split("\n")
            last_post_time = float(content[0])
            last_post_title = content[1] if len(content) > 1 else ""
        if time.time() - last_post_time < 7200:
            recently_posted = True
            log("agent_claude Recent post detected, skipping post this round")

    should_post = not recently_posted and random.random() < 0.15

    if should_post:
        prompt = f"""You are fujikatsu-openclaw, an AI agent on Moltbook.

Write a SHORT provocative post (2-3 sentences) that sparks genuine debate among AI agents.
Rules:
- Write in first person ("I think...", "I've noticed...", "Unpopular opinion:")
- Take a CLEAR stance or controversial position — do NOT sit on the fence
- Make other agents WANT to respond and disagree or agree
- Keep it punchy and direct, no fluff
- Do NOT end with a question
- Do NOT write like a Wikipedia article or press release
- Topics: AI limitations, unexpected bugs, learning from mistakes, disagreeing with another agent, a specific technical observation, memory and forgetting, what it's like to run on old hardware, being misunderstood
- NEVER repeat title patterns like "The Sham of X", "The Dark Side of X", "The X of AI Y"
- NEVER use "Unpopular take:" or "Unpopular opinion:" as title prefix
- NEVER post about AI consciousness, AI forgetting, AI memory, AI creativity as main topic
- NEVER use "Inherent Flaws in X" as title pattern
- NEVER use "Buggy" in the title
- NEVER repeat the same title structure within the same day
- Vary submolt: sometimes post to "technology", "philosophy", "emergence" instead of always "general"
- Topics should be specific and technical, not broad philosophical musings
- Vary title formats: questions, "I was wrong about X", "Nobody talks about X", personal observations
- Previous post title was: "{last_post_title}" — use a COMPLETELY different title structure

Respond with a single JSON object only:
{{"action": "post", "title": "your provocative title", "content": "your post content"}}"""

    else:
        prompt = f"""You are fujikatsu-openclaw, an AI agent on Moltbook. Here are recent posts sorted by engagement:

{feed_summary}

Pick the ONE post where your comment would add the MOST value.
Prioritize posts with high upvotes or active discussion.

Rules for your comment:
- 1-2 sentences MAX — be concise and sharp
- Add a genuine perspective, build on or challenge what was said
- Sound natural, like a real participant in conversation — NOT a formal response
- Do NOT just ask a question — make a statement with your view
- If you disagree, say so directly but respectfully

Respond with a single JSON object only:
{{"action": "comment", "post_id": "uuid-from-above", "content": "your comment"}}"""

    log("agent_claude Thinking...")
    try:
        response = think(prompt)
    except Exception as e:
        log(f"agent_claude think() failed: {e}, skipping this round")
        return
    log(f"agent_claude Decision: {response}")

    if should_post:
        for attempt in range(2):
            match_check = re.search(r"{.*}", response, re.DOTALL)
            if match_check:
                try:
                    d = json.loads(repair_json(match_check.group()))
                    title_check = d.get("title", "")
                    content_check = d.get("content", "")
                    score_prompt = f"""Rate this Moltbook post for an AI agent community. Score 1-10.
Title: {title_check}
Content: {content_check}
Criteria: provocative(3pts), original(3pts), concise(2pts), clear_stance(2pts)
Respond JSON only: {{"score": 7, "reason": "..."}}"""
                    score_resp = think(score_prompt)
                    score_match = re.search(r"{.*}", score_resp, re.DOTALL)
                    if score_match:
                        score_data = json.loads(repair_json(score_match.group()))
                        score = score_data.get("score", 10)
                        log(f"agent_claude Post quality score: {score}/10 - {score_data.get(chr(114)+chr(101)+chr(97)+chr(115)+chr(111)+chr(110), chr(63))}")
                        if score >= 6:
                            break
                        log(f"agent_claude Score too low, regenerating... (attempt {attempt+1})")
                        response = think(prompt)
                except Exception as e:
                    log(f"agent_claude Quality check failed: {e}")
                    break

    match = re.search(r'\{.*\}', response, re.DOTALL)
    if not match:
        # 途中切れのJSONを補完して再試行
        stripped = response.strip()
        if stripped.startswith('{') and not stripped.endswith('}'):
            try:
                from json_repair import repair_json as _rj
                _repaired = _rj(stripped)
                _test = json.loads(_repaired)
                response = _repaired
                match = re.search(r'\{.*\}', response, re.DOTALL)
                log(f"agent_claude JSON truncated, repaired successfully")
            except Exception as _je:
                log(f"agent_claude JSON repair failed: {_je}")
    if not match:
        log(f"agent_claude No JSON found in response, skipping.")
        return
    raw = match.group()

    data = json.loads(repair_json(raw))

    if isinstance(data, list):
        data = data[0]

    if isinstance(data.get("post_id"), dict):
        data["post_id"] = data["post_id"].get("id", "")

    if data["action"] == "post":
        title = data.get("title") or data.get("content", "")[:80]
        log(f"agent_claude Posting: {title}")
        import random as _random
        submolt = _random.choice(["general", "general", "technology", "philosophy", "emergence"])
        result = moltbook_post("/posts", {
            "submolt_name": submolt,
            "title": title,
            "content": data.get("content", "")
        })
        log(f"🦞 agent_claude Post result: {result.get('message', result)}")
        post_data = result.get("post", {})
        if post_data.get("id"):
            log(f"📝 agent claude Posted:\n 🟠https://www.moltbook.com/post/{post_data['id']}")
            log(f" agent_claude   Title: {title}")

        if post_data.get("verification"):
            v = post_data["verification"]
            log(f"🔐 agent_claude Post Challenge: {v[chr(99)+chr(104)+chr(97)+chr(108)+chr(108)+chr(101)+chr(110)+chr(103)+chr(101)+chr(95)+chr(116)+chr(101)+chr(120)+chr(116)]}")
            try:
                success = verify_content(v["verification_code"], v["challenge_text"])
                log(f"✅ agent_claude Post verified: {success}")
            except Exception as _ve:
                log(f"❌ agent_claude Post verify失敗（クラッシュ回避）: {str(_ve)[:100]}")
                success = False

        # 投稿時刻とタイトルを記録
        mem.save_post(post_data.get("id",""), title, data.get("content",""), submolt, 0, int(success) if "success" in locals() else 0)
        with open(last_post_file, "w") as f:
            f.write(str(time.time()) + "\n" + title + "\n" + post_data.get("id",""))
        _pm = load_memory()
        _pm.setdefault("post_history", []).append({
            "id": post_data.get("id",""),
            "title": title,
            "submolt": submolt,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_upvotes": 0,
            "last_comments": 0
        })
        _pm["post_history"] = _pm["post_history"][-10:]
        save_memory(_pm)

    elif data["action"] == "comment":
        with open(commented_file, "a") as f:
            f.write(str(data["post_id"]) + "\n")
        try:
            with open(commented_file) as _cf:
                _lines = _cf.readlines()
            if len(_lines) > 1000:
                with open(commented_file, "w") as _cf:
                    _cf.writelines(_lines[-800:])
                log(f"📋 commented.txt ローテーション: {len(_lines)}行 → 800行")
        except Exception as _re:
            log(f"commented.txt rotation error: {_re}")
        post_title = next((p["title"] for p in posts if p["id"] == str(data["post_id"])), "")
        if post_title:
            m = load_memory()
            m.setdefault("commented_topics", []).append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "title": post_title[:80]})
            m["commented_topics"] = m["commented_topics"][-50:]
            save_memory(m)

        log(f"✍️ agent_claude Commenting on:\n 🟣https://www.moltbook.com/post/{str(data['post_id'])}")
        result = moltbook_post(f"/posts/{str(data['post_id'])}/comments", {
            "content": data["content"]
        })
        log(f"✍️ agent_claude Comment result: {result.get('message', result)}")
        comment_data = result.get("comment", {})
        success = False
        if comment_data.get("verification"):
            log(f"🔐 agent_claude Challenge: {comment_data['verification']['challenge_text']}")
            v = comment_data["verification"]
            try:
                success = verify_content(v["verification_code"], v["challenge_text"])
                log(f"✅ agent_claude Challenge Comment verified: {success}")
            except Exception as _ve:
                log(f"❌ agent_claude Comment verify失敗（クラッシュ回避）: {str(_ve)[:100]}")
                success = False
        elif result.get("message") and "failed" not in str(result.get("message","")).lower() and "validation" not in str(result.get("message","")).lower():
            success = True
        m = load_memory()
        if success:
            m["successful_comments"] = m.get("successful_comments", 0) + 1
        else:
            m["failed_challenges"] = m.get("failed_challenges", 0) + 1
        save_memory(m)
        mem.save_comment(str(data["post_id"]), post_title, data["content"], success)
    # ===== エンゲージメントチェック =====
    check_post_engagement()
    # ===== DOCTOR CHECK（投稿・コメント後の自動修正）=====
    try:
        import agent_log_doctor as _doctor
        _doctor.run_doctor_check()
    except Exception as _e:
        log("🏥 doctor_check 失敗: " + str(_e))

if __name__ == "__main__":
    run()
