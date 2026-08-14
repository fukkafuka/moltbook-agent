#!/usr/bin/env python3
"""
CAPTCHA regex_solve() 回帰テスト
実際にログで検出された攪乱パターンの実例を保存し、修正のたびに壊れていないか確認する。
実行: python3 test_captcha_regex.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_claude as ac

# (テキスト, 期待される答え, パターンの説明)
CASES = [
    (
        "A] LoBsTeR lOoobssstEr S^wImS/ iN TeRrItOrY tErR ItOrY, sEnSeS \\aNtEnNaS aNd- phYySoIlOgY um, "
        "iT sHaRkS {do/miNaNcE} aNd- rEaLly^ aCcElErAtEs; iTs VeLaWcItEe Is tWeN ty- ThReE cEmMeNtS pEr- "
        "SeCoNd ~ aNd It GeTs + sEvEn pUsHes- oF fOrCe, WhAt Is ThE/ nEw- VeLoOcItY? >",
        "30.00",
        "lobster単語内文字重複型攪乱 + senses誤爆(2026-07-24発見, 07-25修正)",
    ),
    (
        "LoBsTeR] DoMiNaNcE- FiGhT ]ClAw^ FoRcE oF TwEnTy /FiVe ~NeU tOnS + SiX tEeN} NeOotOns, "
        "Um WhAt- Is ThE {ToTaL< FoRcE?",
        "41.00",
        "lobster単語内文字重複型攪乱(2026-07-24発見)",
    ),
    (
        "A] LoOoObBsTt-ErR S^wImS[ aT/ ThReE mEeTeRs PeR sEcOnD fOr] FiVe sEcOnDs, Um/ "
        "HoW MaNy MeTeRs TrAvEls? < >",
        "15.00",
        "lobster単語内部ハイフン分断型攪乱(2026-07-28発見)",
    ),
    (
        "ThIs] LoO b-StEr~ SwImS^ aT/ fOoUr\\ MeTeR sPeR{ SeCoNd| A nD- sWaM sFoR< tHrEe> SeCoNdS, "
        "HoW/ faR iS~ tHe LoO bStErrr?",
        "12.00",
        "per/forへの1文字ノイズ混入(sper/sfor)型攪乱(2026-08-15発見)",
    ),
    (
        "A] lOoObBsT-eR ClAw^ ExErTs[ tHiR tY fIfE~ nEeWwToNs/ aNd] tHe^ OtHeR| ClAw ExErTs{ tWeLvE } "
        "nEeWwToNs, Um] wHaTs~ ToTaL| FoRcE?",
        "47.00",
        "'five'のV→F置換難読化('fife'表記)(2026-08-15発見)",
    ),
    (
        "A] lOoO bS-tEr SwImS^ iN/ cOoL wAtErS, ClAw FoRcE iS tHiRtY fIfE] nEu-ToNs~ AnD{ aNtEnNaA "
        "tOuCh MeAsUrEs+ aN oXtRa tWeLvE\\ nEuToNs, WhAt Is] tHe ToTaL^ fOrCe?",
        "47.00",
        "'five'のV→F置換難読化('fife'表記)・文脈違い(2026-08-15発見)",
    ),
]

def main():
    failed = 0
    for text, expected, desc in CASES:
        try:
            result = ac.regex_solve(text)
        except Exception as e:
            result = f"ERROR: {e}"
        ok = result == expected
        status = "OK  " if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{status}] {desc}")
        print(f"       expected={expected} got={result}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
