# Moltbook Agent

SNS「Moltbook」上で自動的に投稿・コメントを行うAIエージェント。CAPTCHA(数値計算パズル形式)の自動解読を含む。

- 本体: `agent_claude.py`(メインエージェント)
- 関連: `agent_gemini.py`(別AIでの並行投稿)、`agent_log_doctor.py`(ログ診断・自己修正支援)
- ログ: `/Users/fk/Logs/agent_claude.log`
- 実行: `run_agent.sh` 経由で cron から起動(多重起動防止ロック付き)

## 主な機能

### 1. 投稿・コメント自動化

Moltbook上の話題を読み取り、コメントを生成して投稿する。実行結果は `commented.txt`(投稿済みコメント記録)・`last_post.txt`・`memory.json`(統計・トピック履歴)に記録される。

### 2. CAPTCHA自動解読

投稿・コメント時に出題される、文字装飾で難読化された計算問題(例: `LoOoObBsTt-ErR S^wImS aT ThReE mEeTeRs PeR sEcOnD fOr FiVe sEcOnDs`)を解く。

- `regex_solve()`: 正規表現ベースの高速解読。数値語抽出・演算子判定(加算/減算/乗算/除算)・ノイズ語(海洋生物のデコイ単語等)除外・文字重複攪乱(`lOoObSssTeR`型)対応を行う
- 正規表現で解けない場合はLLM(Groq→OpenRouterフォールバック)にフォールバック
- 未分類パターンは `unclassified_patterns.json` の `entries` に記録され、手動確認・恒久対応の判断材料になる(対応後は `entries` から削除すること。自動では消えない)

回帰テスト: `test_captcha_regex.py`(既知の攪乱パターンを網羅)。CAPTCHA周りを修正した際は必ず実行する。

```bash
python3 test_captcha_regex.py
```

## ファイル構成

```
agent_claude.py          # メインエージェント(投稿・コメント・CAPTCHA解読)
agent_claude_memory.py    # 記憶・文脈管理
agent_claude_dreaming.py  # (バックグラウンド処理系、詳細別途)
agent_gemini.py            # 別AIでの並行投稿
agent_log_doctor.py        # ログ診断・自己修正支援(auto_patchと連携)
test_captcha_regex.py       # CAPTCHA解読の回帰テスト
run.sh, run_agent.sh          # 実行スクリプト
unclassified_patterns.json     # 未分類CAPTCHAパターンの記録(git管理下)
memory.json, commented.txt, last_post.txt  # 実行時データ(.gitignore対象、git追跡なし)
```

## 実行時データについて

`memory.json` / `commented.txt` / `last_post.txt` は実行のたびに更新される統計・履歴ファイルで、`.gitignore`により意図的にgit追跡対象外にしている(頻繁な変更でコミット履歴がノイズになるため)。バックアップは `backups/` に別途保存される想定。

`unclassified_patterns.json` は例外的にgit管理下にあり、CAPTCHA解読の未対応パターンを記録している。対応が完了したらエントリを忘れずに削除すること。

## 運用上の注意

- APIキー(Groq/OpenRouter)は `~/.config/ai-keys/.env` を正本とする(重複させない)
- `agent_claude.py` はauto_patchのホワイトリスト対象で、オーケストレーター経由での自動修正が可能
- CAPTCHA解読ロジックを修正した際は、`test_captcha_regex.py` の既存テストを壊していないか必ず確認する
