# -*- coding: utf-8 -*-
"""INTERS 가치투자 트래커: 전체 스캔 실행 파일 (GitHub Actions가 매주 실행)"""

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
        if frac - last[0] >= 0.02 or frac >= 1.0:
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
