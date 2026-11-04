# -*- coding: utf-8 -*-
"""
INTERS 가치투자 트래커: 전체 스캔 실행 파일

GitHub Actions가 매주 이 파일을 실행해 data/scan.json.gz를 만들고 저장소에 올립니다.
내 컴퓨터에서 직접 돌릴 수도 있습니다.

    set SEC_USER_AGENT=INTERS Value Tracker your.email@gmail.com   (맥: export SEC_USER_AGENT="...")
    python run_scan.py
"""

import gzip
import json
import os
import sys
import time

import scanner as sc

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scan.json.gz")


def main():
    problem = sc.sec_user_agent_problem()
    if problem:
        print(f"주의: {problem} 기본 연락처로 진행합니다.", flush=True)

    last = [0.0]

    def progress(frac, msg):
        if frac - last[0] >= 0.02 or frac >= 1.0:   # 로그가 너무 길어지지 않게 2%마다 출력
            print(f"[{frac * 100:5.1f}%] {msg}", flush=True)
            last[0] = frac

    started = time.time()
    df, meta = sc.run_scan(progress)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows = df.to_json(orient="records", force_ascii=False, double_precision=10)
    payload = '{"meta": ' + json.dumps(meta, ensure_ascii=False) + ', "rows": ' + rows + "}"
    tmp = OUT + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, OUT)

    size = os.path.getsize(OUT) / 1e6
    print(f"완료: {meta['evaluated']:,}개 종목, {size:.1f}MB, {(time.time() - started) / 60:.1f}분 소요 "
          f"(종목 목록 출처: {meta['source']})", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"스캔 실패: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
