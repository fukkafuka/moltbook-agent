#!/usr/bin/env python3
"""
agent_log_doctor.py
- agent_claude.logを分析して問題を検出
- Groqに分析させて修正方針を決定
- agent_claude.pyを自動修正（バックアップ付き）
"""
import sys
import os
import re
import json
import requests
import shutil
import subprocess
from model_status import filter_alive_models
from datetime import datetime

LOG_FILE = "/Users/fk/Logs/agent_claude.log"
AGENT_FILE = "/Users/fk/ai-agent/moltbook/agent_claude.py"
BACKUP_DIR = "/Users/fk/ai-agent/moltbook/backups"
DOCTOR_LOG = "/Users/fk/Logs/agent_log_doctor.log"


def git_commit_and_push(filepath, message, timeout=30):
    """AGENT_FILE等への直接書き込み後にgit commit+pushする。
    2026-07-30追加: 従来apply_prompt_rules()/trim_critical_rules()はAGENT_FILEに
    直接書き込むだけでgit commitを一切行っておらず、Mac側の未コミット変更が
    次回のgit pull時に繰り返しコンフリクトを起こす原因になっていたため追加。
    対象がgitリポジトリでない場合はエラーにせずスキップする。"""
    try:
        repo_dir = os.path.dirname(os.path.abspath(filepath))
        chk = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=timeout
        )
        if chk.returncode != 0:
            log(f"git_commit_and_push: {repo_dir}はgitリポジトリではないためスキップ")
            return False
        subprocess.run(["git", "-C", repo_dir, "add", filepath],
                       capture_output=True, text=True, timeout=timeout)
        commit = subprocess.run(
            ["git", "-C", repo_dir, "commit", "-m", message],
            capture_output=True, text=True, timeout=timeout
        )
        if commit.returncode != 0:
            log(f"git_commit_and_push: commitなし(差分無し等): {commit.stdout.strip()[:200]}")
            return False
        push = subprocess.run(
            ["git", "-C", repo_dir, "push"],
            capture_output=True, text=True, timeout=timeout
        )
        if push.returncode != 0:
            log(f"git_commit_and_push: push失敗: {push.stderr.strip()[:200]}")
            return False
        log(f"git_commit_and_push: commit+push成功 ({message[:50]})")
        return True
    except Exception as e:
        log(f"git_commit_and_push: 例外発生（無視して続行）: {e}")
        return False

try:
    import dotenv
    dotenv.load_dotenv("/Users/fk/.config/ai-keys/.env")
except Exception:
    pass

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-20b"  # 2026-08-01: llama-3.1-8b-instantはGroqが2026-08-16に廃止予定のため移行(公式推奨の移行先)

_SANITIZER_PATH = os.path.expanduser("~/.config/ai-keys")
if _SANITIZER_PATH not in sys.path:
    sys.path.insert(0, _SANITIZER_PATH)
try:
    from secret_sanitizer import sanitize_secrets as _sanitize_secrets
except Exception:
    def _sanitize_secrets(text):
        return text

def log(msg):
    msg = _sanitize_secrets(str(msg))
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"🏥[{ts}] {msg}", flush=True)

def groq_analyze(prompt):
    # Groq試行
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a precise log analyzer and Python code expert. Always respond in JSON format only."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1000,
                "temperature": 0
            },
            timeout=30
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        log(f"Groq応答エラー: {data.get('error',{}).get('message','不明')[:80]} → OpenRouterへ")
    except Exception as e:
        log(f"Groq接続エラー: {str(e)[:80]} → OpenRouterへ")

    # OpenRouterフォールバック（中国系モデル除外済み）
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if not openrouter_key:
        raise Exception("OpenRouter APIキーが見つかりません")

    fallback_models = filter_alive_models([
        "openai/gpt-oss-20b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
    ], provider="openrouter")
    for model in fallback_models:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a precise log analyzer and Python code expert. Always respond in JSON format only."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 1000,
                    "temperature": 0
                },
                timeout=30
            )
            data = r.json()
            if "choices" in data:
                log(f"OpenRouter {model} で分析成功")
                return data["choices"][0]["message"]["content"]
            log(f"OpenRouter {model} エラー: {data.get('error',{}).get('message','不明')[:50]}")
        except Exception as e:
            log(f"OpenRouter {model} 接続エラー: {str(e)[:80]}")
            continue
    raise Exception("Groq・OpenRouter全て失敗")

def read_recent_logs(lines=200):
    with open(LOG_FILE, "r") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])

def analyze_problems(log_content):
    problems = []

    false_count = log_content.count("Challenge Comment verified: False") + \
                  log_content.count("Post verified: False") + \
                  log_content.count("返信CAPTCHA: False")
    if false_count > 0:
        problems.append(f"CAPTCHA失敗: {false_count}件")

    mismatch_count = log_content.count("Answer mismatch!")
    if mismatch_count > 0:
        problems.append(f"Answer mismatch: {mismatch_count}件")

    fallback_count = log_content.count("Fallback: found answer in text")
    if fallback_count > 0:
        problems.append(f"Fallback多発: {fallback_count}件")

    if "Gemini not available" in log_content:
        problems.append("Gemini利用不可")

    conn_error_count = log_content.count("moltbook_get error") + \
                       log_content.count("moltbook_post error")
    if conn_error_count > 0:
        problems.append(f"接続エラー: {conn_error_count}件")

    crash_count = log_content.count("CRASHED with exit code")
    if crash_count > 0:
        problems.append(f"クラッシュ: {crash_count}件")

    return problems

def backup_agent():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f"agent_claude_doctor_{ts}.py")
    shutil.copy2(AGENT_FILE, backup_path)
    log(f"バックアップ作成: {backup_path}")

    # 世代管理: agent_claude_doctor_*.py を新しい7件のみ保持
    pattern = os.path.join(BACKUP_DIR, "agent_claude_doctor_*.py")
    import glob
    backups = sorted(glob.glob(pattern), reverse=True)
    for old in backups[7:]:
        try:
            os.remove(old)
            log(f"古いバックアップ削除: {os.path.basename(old)}")
        except Exception as e:
            log(f"バックアップ削除失敗: {e}")
    return backup_path

def get_false_challenges(log_content):
    challenges = []
    lines = log_content.split("\n")
    for i, line in enumerate(lines):
        if "🔐" in line and "Challenge:" in line and i + 3 < len(lines):
            lookahead = min(i + 25, len(lines))  # APIフォールバック多発時のログ行数増加に対応（15→25）
            context = "\n".join(lines[i:lookahead])
            if "verified: False" in context or "返信CAPTCHA: False" in context:
                challenge_match = re.search(r'Challenge: (.+)', line)
                reasoning_match = re.search(r'Verification reasoning: (.+)', context)
                answer_match = re.search(r'Verification answer: ([\d.]+)', context)
                if challenge_match:
                    challenges.append({
                        "challenge": challenge_match.group(1),
                        "reasoning": reasoning_match.group(1) if reasoning_match else "不明",
                        "answer": answer_match.group(1) if answer_match else "不明"
                    })
    return challenges

def get_current_rules():
    try:
        with open(AGENT_FILE, "r") as f:
            content = f.read()
        start = content.find("STRICT RULES:")
        end = content.find("Examples:", start)
        if start != -1 and end != -1:
            return content[start:end].strip()[:1000]  # 1000文字に切り詰め
    except Exception:
        pass
    return "ルール取得失敗"

def ask_groq_for_fix(problems, false_challenges, log_sample):
    challenge_text = json.dumps(false_challenges, ensure_ascii=False, indent=2) if false_challenges else "なし"
    current_rules = get_current_rules()

    prompt = f"""You are an expert at analyzing CAPTCHA solving failures for a Lobster Challenge math solver.

CURRENT RULES (excerpt):
{current_rules}

DETECTED PROBLEMS:
{chr(10).join(problems)}

FAILED CHALLENGES:
{challenge_text}

TASK:
1. Determine the CORRECT answer for each failed challenge
2. Identify WHY the agent got it wrong
3. Suggest NEW rules to add (do NOT duplicate existing rules)

Respond ONLY in JSON:
{{
  "summary": "brief summary of what went wrong",
  "root_cause": "why the agent failed",
  "prompt_rules_to_add": ["new rule 1 in English", "new rule 2 in English"],
  "severity": "high/medium/low",
  "action": "fix/skip"
}}"""

    return groq_analyze(prompt)


def trim_critical_rules():
    """CRITICALルールが55件以上になったらGroqで整理・統合する"""
    try:
        with open(AGENT_FILE, "r") as f:
            agent_content = f.read()

        lines = agent_content.split("\n")
        critical_lines = [l for l in lines if l.startswith("   - CRITICAL:")]
        current_count = len(critical_lines)

        if current_count < 55:
            return agent_content

        log(f"CRITICALルール整理開始（{current_count}件 → 目標40件）")

        rules_text = "\n".join(critical_lines)
        prompt = f"""You are an expert at consolidating prompt rules for a CAPTCHA math solver.

CURRENT CRITICAL RULES ({current_count} total):
{rules_text}

TASK: Consolidate these rules into ~40 essential rules by:
1. Merging duplicate or near-duplicate rules into one
2. Combining rules that cover the same concept
3. Keeping the most specific and comprehensive version of each rule
4. Preserving all unique edge cases

Respond ONLY in JSON:
{{
  "consolidated_rules": ["rule 1 in English", "rule 2 in English", ...]
}}

Target: ~40 rules maximum. Each rule should start WITHOUT "CRITICAL:" prefix."""

        result = groq_analyze(prompt)
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(result.strip()))
            if isinstance(data, list):
                data = data[0] if data else {}
        except Exception as e:
            log(f"trim パースエラー: {e} → 整理スキップ")
            return agent_content

        new_rules = data.get("consolidated_rules", [])
        if not new_rules or len(new_rules) > current_count:
            log(f"trim 結果不正（{len(new_rules)}件）→ 整理スキップ")
            return agent_content

        new_critical_lines = [f"   - CRITICAL: {r}" for r in new_rules]

        new_lines = []
        skip = False
        for line in lines:
            if line.startswith("   - CRITICAL:"):
                if not skip:
                    new_lines.extend(new_critical_lines)
                    skip = True
            else:
                new_lines.append(line)

        new_content = "\n".join(new_lines)
        with open(AGENT_FILE, "w") as f:
            f.write(new_content)

        git_commit_and_push(AGENT_FILE, f"chore: CRITICALルールを整理・統合({current_count}件→{len(new_rules)}件)")

        log(f"✅ CRITICALルール整理完了: {current_count}件 → {len(new_rules)}件")
        return new_content

    except Exception as e:
        log(f"trim_critical_rules 例外: {e} → 整理スキップ")
        with open(AGENT_FILE, "r") as f:
            return f.read()

def apply_prompt_rules(rules):
    content = trim_critical_rules()
    if not content:
        with open(AGENT_FILE, "r") as f:
            content = f.read()

    insert_point = "   - DEFAULT: if no clear subtraction keyword → ADDITION (+)"

    if insert_point not in content:
        log("挿入ポイントが見つかりません → スキップ")
        return False

    safe_rules = []
    for rule in rules:
        rule_clean = rule.replace("'", "").replace('"', "").replace("（例：", "").replace("）", "")
        if len(rule_clean) > 10:
            safe_rules.append(rule_clean)

    if not safe_rules:
        log("安全なルールがありません → スキップ")
        return False

    # CRITICALルール上限チェック（上限60件）
    current_critical = content.count("   - CRITICAL:")
    max_critical = 60
    if current_critical >= max_critical:
        log(f"CRITICALルール上限到達（{current_critical}/{max_critical}） → スキップ")
        return False
    available = max_critical - current_critical
    log(f"CRITICALルール現在数: {current_critical}/{max_critical}")
    if len(safe_rules) > available:
        log(f"追加可能件数に制限: {len(safe_rules)}件 → {available}件")
        safe_rules = safe_rules[:available]

    new_rules = "\n".join([f"   - CRITICAL: {rule}" for rule in safe_rules])
    new_content = content.replace(
        insert_point,
        insert_point + "\n" + new_rules
    )

    with open(AGENT_FILE, "w") as f:
        f.write(new_content)
    git_commit_and_push(AGENT_FILE, f"chore: CAPTCHA失敗分析からCRITICALルールを{len(safe_rules)}件追加")
    return True

def run():
    pid_file = "/Users/fk/ai-agent/logs/agent_log_doctor.pid"
    # 2026-08-05: pid_fileの親ディレクトリが存在しない場合、open()がFileNotFoundErrorで
    # クラッシュしrun()全体(=doctorの分析処理そのもの)が実行されなくなるバグがあった。
    # 多重起動防止ロック自体が失敗しても、本来の分析処理は必ず実行されるようにする。
    try:
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                old_pid = f.read().strip()
            try:
                import subprocess
                result = subprocess.run(["ps", "-p", old_pid], capture_output=True)
                if result.returncode == 0:
                    print(f"Already running (PID {old_pid})")
                    return
            except Exception:
                pass
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log(f"⚠️ PIDロック処理でエラー（多重起動防止は無効化されるが処理は続行）: {e}")
        pid_file = None
    try:
        _run()
    finally:
        if pid_file and os.path.exists(pid_file):
            os.remove(pid_file)

def _record_doctor_error(reason):
    """doctorが問題を検出したにもかかわらずクラッシュ/失敗して修正に至らなかった場合の記録。
    2026-08-04追加: ダッシュボードの「修正0件」が『本当に修正不要だった』のか
    『エラーで記録すらできなかった』のかを区別できるようにするため。"""
    try:
        m = json.load(open(MEMORY_FILE)) if os.path.exists(MEMORY_FILE) else {}
    except Exception:
        m = {}
    m.setdefault("doctor_errors", []).append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reason": reason[:200],
    })
    m["doctor_errors"] = m["doctor_errors"][-200:]
    try:
        json.dump(m, open(MEMORY_FILE, "w"), ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"_record_doctor_error: memory.json書き込み失敗: {e}")


def _run():
    log("🏥 agent_log_doctor 起動")

    if not os.path.exists(LOG_FILE):
        log("ログファイルが見つかりません")
        return

    log_content = read_recent_logs(100)

    problems = analyze_problems(log_content)
    if not problems:
        log("✅ 問題なし")
        return

    log(f"⚠️ 検出: {', '.join(problems)}")

    false_challenges = get_false_challenges(log_content)
    if false_challenges:
        log(f"失敗Challenge: {len(false_challenges)}件")

    log("分析中...")
    try:
        result = ask_groq_for_fix(problems, false_challenges, log_content)
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(result.strip()))
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict):
                log(f"パースエラー: 期待した辞書形式ではありません(型={type(data).__name__}): {str(data)[:200]}")
                _record_doctor_error(f"パースエラー(型={type(data).__name__})")
                return
        except Exception as e:
            log(f"パースエラー: {e}")
            _record_doctor_error(f"パースエラー: {e}")
            return
    except Exception as e:
        log(f"分析エラー: {e}")
        _record_doctor_error(f"分析エラー: {e}")
        return

    log(f"分析結果: {data.get('summary', '不明')}")
    log(f"重要度: {data.get('severity', '不明')} / アクション: {data.get('action', 'skip')}")

    if data.get('action') != 'fix':
        log("修正不要")
        return

    rules = data.get('prompt_rules_to_add', [])
    if not rules:
        log("追加ルールなし")
        return

    backup_agent()
    log(f"修正適用: {len(rules)}件のルールを追加")
    for rule in rules:
        log(f"  + {rule}")

    if apply_prompt_rules(rules):
        log("✅ 修正完了")
        _record_doctor_fix(len(false_challenges), false_challenges)
    else:
        log("❌ 修正失敗")
        _record_doctor_error("apply_prompt_rules失敗")

    log("🏥 agent_log_doctor 終了")

MEMORY_FILE = "/Users/fk/ai-agent/moltbook/memory.json"

def classify_captcha_failure_offline(challenge_text):
    """agent_claude.pyのclassify_captcha_failure()の複製版（ログ解析専用、mismatch判定は除外）
    agent_claude.pyをimportすると初期化副作用があるため、分類ロジックのみ複製している。
    agent_claude.py側を修正した場合はこちらも同期すること。
    2026-07-15: 文字重複正規化(dedup)導入に同期済み。"""
    cleaned = re.sub(r'[^a-zA-Z0-9\s\*]', ' ', challenge_text).lower()
    cleaned = ' '.join(cleaned.split())
    text_lower = cleaned
    raw_letters = re.sub(r'[^a-zA-Z]', '', challenge_text).lower()
    _dedup_re = re.compile(r'(.)\1+')
    raw_dedup = _dedup_re.sub(r'\1', raw_letters)
    text_dedup = _dedup_re.sub(r'\1', text_lower)

    if "< * >" in challenge_text or "<*>" in challenge_text.replace(" ", ""):
        return "演算子見落とし（< * >パターン）"

    tens = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    ones = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    for t in tens:
        for o in ones:
            t_hit = t in text_lower or t in text_dedup or t in raw_letters or t in raw_dedup
            o_hit = o in text_lower or o in text_dedup or o in raw_letters or o in raw_dedup
            if t_hit and o_hit:
                return f"複合数分解ミスの可能性（{t} {o}）"

    if "dominancefight" in raw_dedup or "duringfight" in raw_dedup or "dominance fight" in text_dedup or "during fight" in text_dedup:
        return "キーワード誤認（dominance fight）"

    if "times stronger" in text_dedup or "times more" in text_dedup:
        return "キーワード誤認（times stronger）"

    if any(k in raw_dedup for k in ["speedsup", "speedup", "accelerat", "newvelocity", "newspeed"]):
        return "速度変化パターン（加速/新速度）"

    if "multiplier" in raw_dedup or ("product" in raw_dedup and "mult" in raw_dedup):
        return "乗算パターン（multiplier/product）"

    if "gainsfrom" in raw_dedup or "gainsby" in raw_dedup:
        return "加算パターン（gains from/by）"

    if re.search(r'domin\w*fi+\w*ght', raw_dedup) or re.search(r'domin\w*duringfi+\w*ght', raw_dedup):
        return "キーワード誤認（dominance fight・表記ゆれ）"

    if "plus" in text_dedup and any(k in text_dedup for k in ["force", "newton", "total"]):
        return "加算パターン（plus）"

    if "together" in text_dedup:
        return "加算パターン（together）"

    if "per rotation" in text_dedup or "per square" in text_dedup:
        return "乗算パターン（per rotation/square）"

    if "multiply" in raw_dedup or "multiplied" in raw_dedup or "multiplies" in raw_dedup:
        return "乗算パターン（multiply系キーワード）"
    if "product" in raw_dedup:
        return "乗算パターン（product）"

    if "times as much" in text_dedup or re.search(r'\b(two|three|four|five|six|seven|eight|nine|ten)\s+times\b', text_dedup):
        return "乗算パターン（○ times as much）"

    if "netforce" in raw_dedup or re.search(r'n+e+t+.{0,3}f+o+r+c+e+', raw_dedup):
        return "減算パターン（net force・表記ゆれ）"

    if "countered" in raw_dedup or ("remains" in raw_dedup and "force" in raw_dedup):
        return "減算パターン（remains/countered）"

    if "combined" in raw_dedup:
        return "加算パターン（combined）"

    if "doubles" in raw_dedup or "doubled" in raw_dedup:
        return "乗算パターン（doubles）"

    if "gains" in raw_dedup:
        return "加算パターン（gains）"

    if "loses" in raw_dedup:
        return "減算パターン（loses）"

    if "decreases" in raw_dedup or "decrease" in raw_dedup:
        return "減算パターン（decreases）"

    if "increases" in raw_dedup or "increase" in raw_dedup:
        return "加算パターン（increases）"

    if "torque" in raw_dedup:
        return "乗算パターン（torque）"

    if "momentum" in raw_dedup:
        return "乗算パターン（momentum）"

    if "power" in raw_dedup and "transfer" in raw_dedup:
        return "乗算パターン（power transferred）"

    if "exert" in raw_dedup and "and" in raw_dedup:
        return "加算誤乗算パターン（exert+and誤判定・2026-07-22修正済み）"

    if any(k in text_dedup.split() for k in ("no", "wait", "wel", "actualy")) and "?" in challenge_text:
        return "言い直しパターン（訂正キーワード・2026-07-22修正済み）"

    if " of " in text_lower:
        return "機能語誤結合パターン（of等・2026-07-22修正済み）"

    # 乗算/減算キーワードが一切なく"and"のみの場合はデフォルト加算として扱われるため分類可能
    _mul_sub_kws = (
        "multipl", "product", "strike", "together", "each", "applied",
        "remov", "lose", "lost", "minus", "subtract", "leav", "resist",
        "slow", "reduc", "oppos", "counter", "remain", "back",
    )
    if "and" in raw_dedup and not any(k in raw_dedup for k in _mul_sub_kws):
        return "加算パターン（デフォルト加算・2026-07-23修正済み）"

    return "未分類パターン（手動確認が必要）"

def _record_doctor_fix(fixed_count, false_challenges=None):
    """doctorによる自動修正成功を日別トレンド用に記録（captcha_historyと同じ形式、パターン内訳も記録）
    同じchallenge文が複数回のdoctor実行（毎時のrun_doctor_check等）で重複検出されても、
    二重カウントしないようdoctor_fixed_seen_challengesで既知チェックを行う。"""
    if fixed_count <= 0 or not false_challenges:
        return
    try:
        with open(MEMORY_FILE, "r") as f:
            m = json.load(f)
    except Exception:
        m = {}

    seen = m.setdefault("doctor_fixed_seen_challenges", [])
    seen_set = set(seen)

    patterns = []
    new_seen = []
    for fc in false_challenges:
        ctext = fc.get("challenge", "") if isinstance(fc, dict) else ""
        if not ctext or ctext in seen_set:
            continue
        patterns.append(classify_captcha_failure_offline(ctext))
        seen_set.add(ctext)
        new_seen.append(ctext)

    if not patterns:
        log("📊 修正件数記録: 新規分なし（既知challengeの重複検出のためスキップ）")
        return

    seen.extend(new_seen)
    # 既知リストの上限管理（直近2000件）
    m["doctor_fixed_seen_challenges"] = seen[-2000:]

    new_count = len(patterns)
    m.setdefault("doctor_fix_history", []).append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "fixed": new_count,
        "patterns": patterns
    })
    # 日数ベースでトリム（直近14日分、captcha_historyと同じ方針）
    from datetime import timedelta
    _cutoff_dt = datetime.now() - timedelta(days=14)
    def _parse_time(_t):
        try:
            return datetime.strptime(_t, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    m["doctor_fix_history"] = [
        e for e in m["doctor_fix_history"]
        if (_parse_time(e.get("time", "")) or datetime.min) >= _cutoff_dt
    ]
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        log(f"📊 修正件数記録: {new_count}件（新規分のみ、パターン: {', '.join(patterns)}） → doctor_fix_history")
    except Exception as e:
        log(f"⚠️ 修正件数記録エラー: {e}")

def extract_unclassified_patterns():
    """❓未分類パターンのChallenge文を抽出し、JSONに蓄積（重複防止）
    また、手動でclassify_captcha_failure_offline()にパターンが追加され、
    待機中のchallenge文が分類可能になっていれば「手動修正済み」に移動する。"""
    log("📋 未分類パターン抽出開始")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unclassified_patterns.json')
    try:
        with open(out_path, "r") as f:
            store = json.load(f)
    except Exception:
        store = {"seen_challenges": [], "entries": []}

    # 待機中エントリを現在の分類ロジックで再チェックし、解消済みなら「手動修正済み」へ移動
    still_pending = []
    resolved = []
    for e in store.get("entries", []):
        ctext = e.get("challenge", "")
        current_pattern = classify_captcha_failure_offline(ctext) if ctext else "未分類パターン（手動確認が必要）"
        if current_pattern != "未分類パターン（手動確認が必要）":
            resolved.append({"challenge": ctext, "resolved_pattern": current_pattern})
        else:
            still_pending.append(e)
    store["entries"] = still_pending

    if resolved:
        try:
            with open(MEMORY_FILE, "r") as f:
                mm = json.load(f)
        except Exception:
            mm = {}
        mm.setdefault("manual_fix_history", []).append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "fixed": len(resolved),
            "patterns": [r["resolved_pattern"] for r in resolved]
        })
        with open(MEMORY_FILE, "w") as f:
            json.dump(mm, f, ensure_ascii=False, indent=2)
        log(f"✅ 手動修正済みに移動: {len(resolved)}件（" + ", ".join(r["resolved_pattern"] for r in resolved) + "）")

    if not os.path.exists(LOG_FILE):
        with open(out_path, "w") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        return
    with open(LOG_FILE) as f:
        lines = f.readlines()

    new_entries = []
    for i, line in enumerate(lines):
        if "❓ agent_claude CAPTCHA失敗パターン" in line:
            for j in range(max(0, i - 10), i):
                m = re.search(r'Challenge: (.+)', lines[j])
                if m:
                    challenge_text = m.group(1).strip()
                    if challenge_text not in store["seen_challenges"]:
                        store["seen_challenges"].append(challenge_text)
                        new_entries.append({
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "challenge": challenge_text
                        })
                    break

    if new_entries:
        store["entries"].extend(new_entries)
        store["entries"] = store["entries"][-500:]
        store["seen_challenges"] = store["seen_challenges"][-500:]

    with open(out_path, "w") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    if new_entries:
        log(f"📋 未分類パターン新規抽出: {len(new_entries)}件（待機中{len(store['entries'])}件） → unclassified_patterns.json")
    else:
        log(f"📋 未分類パターン: 新規なし（待機中{len(store['entries'])}件）")

def run_doctor_check():
    """agent_claudeのrun()末尾から呼び出し。直近60行のCAPTCHA問題を即時修正。"""
    if not os.path.exists(LOG_FILE):
        return
    with open(LOG_FILE) as f:
        log_content = "".join(f.readlines()[-200:])
    problems = analyze_problems(log_content)
    if not problems:
        log("OK: run_doctor_check 問題なし")
        return
    log("⚡ run_doctor_check 検出: " + ", ".join(problems))
    actionable = [p for p in problems if "CAPTCHA" in p or "mismatch" in p or "Fallback" in p]
    if not actionable:
        log("run_doctor_check: CAPTCHA問題なし → スキップ")
        return
    false_challenges = get_false_challenges(log_content)
    if not false_challenges:
        log("run_doctor_check: 失敗Challengeなし → スキップ")
        return
    log("🏥 失敗Challenge: " + str(len(false_challenges)) + "件 分析中...")
    try:
        result = ask_groq_for_fix(problems, false_challenges, log_content)
        from json_repair import repair_json
        import json as _json
        data = _json.loads(repair_json(result.strip()))
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            log(f"🏥 run_doctor_check: パースエラー、期待した辞書形式ではありません(型={type(data).__name__})")
            try:
                import os as _os
                from datetime import datetime as _dt
                _debug_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "debug_raw")
                _os.makedirs(_debug_dir, exist_ok=True)
                _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
                _debug_path = _os.path.join(_debug_dir, f"doctor_parsefail_{_ts}.txt")
                with open(_debug_path, "w", encoding="utf-8") as _f:
                    _f.write(_sanitize_secrets(str(result)))
                log(f"🏥 生応答を保存: debug_raw/doctor_parsefail_{_ts}.txt")
            except Exception as _e:
                log(f"🏥 生応答の保存に失敗: {_e}")
            return
        log("🏥 doctor: " + data.get("summary", "不明") + " / " + data.get("severity", "不明"))
        if data.get("action") != "fix":
            log("🏥 修正不要と判断")
            return
        rules = data.get("prompt_rules_to_add", [])
        if not rules:
            log("🏥 追加ルールなし")
            return
        backup_agent()
        if apply_prompt_rules(rules):
            log("✅ 自動修正完了: " + str(len(rules)) + "件追加")
            _record_doctor_fix(len(false_challenges), false_challenges)
        else:
            log("❌ 自動修正失敗")
    except Exception as e:
        log("🏥 run_doctor_check エラー: " + str(e))
    try:
        extract_unclassified_patterns()
    except Exception as e:
        log("🏥 extract_unclassified_patterns エラー: " + str(e))


if __name__ == "__main__":
    run()
