import json, os, requests, re, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_claude_memory as mem
from model_status import filter_alive_models

BASE = os.path.dirname(os.path.abspath(__file__))

def load_memory():
    p = os.path.join(BASE, 'memory.json')
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except (json.JSONDecodeError, OSError) as e:
        log(f'⚠️ memory.json読み込み失敗、空dictで継続: {e}')
        return {}

def save_memory(m):
    p = os.path.join(BASE, 'memory.json')
    json.dump(m, open(p, 'w'), ensure_ascii=False, indent=2)

def read_log(n=100):
    log_path = '/Users/fk/Logs/agent_claude.log'
    if not os.path.exists(log_path):
        return ''
    return ''.join(open(log_path).readlines()[-n:])

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"🌙[{ts}] {msg}", flush=True)

def to_str(val, default=''):
    """LLMがリストで返してきた場合も文字列に変換する"""
    if isinstance(val, list):
        return '\n'.join(str(v) for v in val)
    if val is None:
        return default
    return str(val)

def dream():
    # 2026-07-29: 正本(~/.config/ai-keys/.env)を先に読み込み、ローカル.envはsetdefaultで
    # 不足分のみ補完する順序に修正。従来はローカル.envを先に読んでいたため、
    # ローカルに古いキーが残っていると正本より優先されてしまうバグがあった
    # (2026-06-14に発覚したGROQ_API_KEY重複問題と同種の構造)。
    ai_keys_env = os.path.expanduser('~/.config/ai-keys/.env')
    if os.path.exists(ai_keys_env):
        for line in open(ai_keys_env):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip()
    env_path = os.path.join(BASE, '.env')
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

    groq_key = os.environ.get('GROQ_API_KEY')
    log_content = read_log(30)
    memory = load_memory()
    log('🌙 Dreaming started')

    prompt = (
        'Analyze the activity log of AI agent fujikatsu-openclaw on Moltbook.\n'
        'Current memory:\n' + json.dumps(memory, ensure_ascii=False)[:500] + '\n'
        'Recent log:\n' + log_content + '\n\n'
        'Output ONLY a single raw JSON object. No explanation, no markdown, no preamble.\n'
        'Required fields (all must be non-empty):\n'
        '- "insights": string — key observations about agent behavior from the log\n'
        '- "style_notes": string — concrete suggestions to improve posting/commenting style\n'
        '- "avoid_topics": array of strings — topics to avoid based on past failures\n\n'
        'Example: {"insights": "Agent skipped 30/30 posts due to quota exhaustion.", '
        '"style_notes": "Prioritize high-karma posts first.", '
        '"avoid_topics": ["off-topic spam"]}'
    )
    result = None

    # Groq試行
    try:
        r = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={'model': 'llama-3.1-8b-instant', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 500, 'temperature': 0.5},
            timeout=30
        )
        resp = r.json()
        if 'choices' in resp:
            result = resp['choices'][0]['message']['content']
        else:
            log(f'Groq APIエラー: {resp.get("error", {}).get("message", "不明")[:80]} → OpenRouterへ')
    except Exception as e:
        log(f'Groq接続エラー: {str(e)[:80]} → OpenRouterへ')

    # OpenRouterフォールバック（中国系モデル除外済み）
    if result is None:
        openrouter_key = os.environ.get('OPENROUTER_API_KEY')
        fallback_models = filter_alive_models([
            'openai/gpt-oss-20b:free',
            'nvidia/nemotron-3-super-120b-a12b:free',
            'nvidia/nemotron-3-nano-30b-a3b:free',
            'nousresearch/hermes-3-llama-3.1-405b:free',
        ], provider="openrouter")
        for model in fallback_models:
            try:
                r = requests.post(
                    'https://openrouter.ai/api/v1/chat/completions',
                    headers={'Authorization': f'Bearer {openrouter_key}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 500, 'temperature': 0.5},
                    timeout=30
                )
                resp = r.json()
                if 'choices' in resp:
                    log(f'OpenRouter {model} で分析成功')
                    result = resp['choices'][0]['message']['content']
                    break
                log(f'OpenRouter {model} エラー: {resp.get("error", {}).get("message", "不明")[:50]}')
            except Exception as e:
                log(f'OpenRouter {model} 接続エラー: {str(e)[:80]}')
                continue

    if result is None:
        log('❌ Groq・OpenRouter全て失敗 → スキップ')
        return

    log(f'Response: {result[:100]}')

    try:
        from json_repair import repair_json
    except ImportError:
        repair_json = lambda s: s

    matches = list(re.finditer(r'\{[^{}]*\}', result, re.DOTALL))
    data = None
    for m in reversed(matches):
        try:
            data = json.loads(repair_json(m.group()))
            if any(k in data for k in ['style_notes', 'avoid_topics', 'insights']):
                break
        except Exception:
            continue

    if data:
        # LLMがリストで返してきた場合も文字列に変換してからDBへ保存
        insights    = to_str(data.get('insights', ''))
        style_notes = to_str(data.get('style_notes', ''))
        avoid_topics = data.get('avoid_topics', [])
        if not isinstance(avoid_topics, list):
            avoid_topics = [str(avoid_topics)]

        memory['style_notes']   = style_notes
        memory['avoid_topics']  = avoid_topics
        memory['last_dream']    = datetime.now().strftime('%Y-%m-%d %H:%M')
        memory['last_insights'] = insights
        save_memory(memory)
        mem.save_dream(insights, style_notes, avoid_topics)
        log('✅ Dreaming complete!')
        log(f"Insights: {insights[:100]}")
    else:
        log('❌ ERROR: Could not parse response')

if __name__ == '__main__':
    dream()
