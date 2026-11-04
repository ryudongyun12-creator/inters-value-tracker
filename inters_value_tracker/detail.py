# -*- coding: utf-8 -*-
"""
INTERS 가치투자 트래커: 종목 상세 데이터 (종목을 눌렀을 때만 Yahoo Finance에서 가져옴)

- 어닝 서프라이즈 (최근 발표)
- 애널리스트 EPS 추정치 변화와 상향·하향 수
- 컨센서스 목표주가
- 증권사별 투자의견·목표주가 변경 내역 (최근 12개월)
- 내부자 매수 (최근 6개월), 경영진·기관 보유 비율, 애널리스트 수

Yahoo 응답 형식은 수시로 바뀌므로 모든 항목을 실패해도 화면이 멈추지 않게 처리합니다.
"""

import datetime as dt

import numpy as np
import pandas as pd

PERIOD_KO = {"0q": "이번 분기", "+1q": "다음 분기", "0y": "올해", "+1y": "내년"}
ACTION_KO = {"up": "상향", "down": "하향", "init": "신규", "main": "유지", "reit": "재확인"}
PT_ACTION_KO = {"raises": "목표가 상향", "lowers": "목표가 하향", "maintains": "목표가 유지",
                "announces": "목표가 신규", "sets": "목표가 신규"}


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _col(df, *names):
    """대소문자·공백 차이를 무시하고 열 이름 찾기."""
    if df is None:
        return None
    lookup = {str(c).lower().replace(" ", ""): c for c in df.columns}
    for n in names:
        c = lookup.get(n.lower().replace(" ", ""))
        if c is not None:
            return c
    return None


def _naive(idx):
    idx = pd.to_datetime(idx, errors="coerce")
    try:
        return idx.tz_localize(None)
    except (TypeError, AttributeError):
        try:
            return idx.tz_convert(None)
        except Exception:
            return idx


def earnings_table(t):
    df = t.get_earnings_dates(limit=12)
    if df is None or df.empty:
        return None
    est, rep, sur = _col(df, "EPS Estimate"), _col(df, "Reported EPS"), _col(df, "Surprise(%)")
    if rep is None:
        return None
    df = df[df[rep].notna()].copy()
    df.index = _naive(df.index)
    df = df.sort_index(ascending=False).head(8)
    return pd.DataFrame({
        "발표일": [d.strftime("%Y-%m-%d") for d in df.index],
        "예상 EPS": df[est].to_numpy() if est else np.nan,
        "실제 EPS": df[rep].to_numpy(),
        "서프라이즈(%)": df[sur].to_numpy() if sur else np.nan,
    })


def estimate_table(t):
    trend = t.eps_trend
    if trend is None or trend.empty:
        return None, None
    cur = _col(trend, "current")
    rows, score = [], 0
    for period, label in PERIOD_KO.items():
        if period not in trend.index or cur is None:
            continue
        r = trend.loc[period]
        now = r[cur]
        row = {"기간": label, "현재 추정 EPS": now}
        for days in (30, 60, 90):
            c = _col(trend, f"{days}daysAgo")
            past = r[c] if c is not None else np.nan
            row[f"{days}일 전 대비(%)"] = (now / past - 1) * 100 if (past and past > 0 and pd.notna(now)) else np.nan
        c30 = _col(trend, "30daysAgo")
        if c30 is not None and pd.notna(now) and pd.notna(r[c30]):
            weight = 2 if period.endswith("y") else 1   # 미너비니: 연간 추정치 상향이 더 중요
            score += weight * (1 if now > r[c30] else (-1 if now < r[c30] else 0))
        rows.append(row)

    rev = _safe(lambda: t.eps_revisions)
    if rev is not None and not rev.empty:
        up, down = _col(rev, "upLast30days"), _col(rev, "downLast30days")
        for row, period in zip(rows, [p for p in PERIOD_KO if p in trend.index]):
            if period in rev.index:
                row["30일 상향 수"] = rev.loc[period, up] if up else np.nan
                row["30일 하향 수"] = rev.loc[period, down] if down else np.nan
    return pd.DataFrame(rows), score


def ratings_table(t, days=365):
    df = t.upgrades_downgrades
    if df is None or df.empty:
        return None
    df = df.copy()
    df.index = _naive(df.index)
    df = df[df.index >= pd.Timestamp.today() - pd.Timedelta(days=days)].sort_index(ascending=False)
    if df.empty:
        return pd.DataFrame()
    firm, to_g, from_g, act = (_col(df, "Firm"), _col(df, "ToGrade"), _col(df, "FromGrade"), _col(df, "Action"))
    pta, cur_pt, prior_pt = (_col(df, "priceTargetAction"), _col(df, "currentPriceTarget"),
                             _col(df, "priorPriceTarget"))
    out = pd.DataFrame({
        "날짜": [d.strftime("%Y-%m-%d") for d in df.index],
        "증권사": df[firm].to_numpy() if firm else "",
        "의견 변경": df[act].map(lambda a: ACTION_KO.get(str(a).lower(), a)).to_numpy() if act else "",
        "이전 의견": df[from_g].to_numpy() if from_g else "",
        "현재 의견": df[to_g].to_numpy() if to_g else "",
    })
    if cur_pt is not None:
        out["목표가 변경"] = (df[pta].map(lambda a: PT_ACTION_KO.get(str(a).lower(), a)).to_numpy()
                         if pta else "")
        out["이전 목표가($)"] = pd.to_numeric(df[prior_pt], errors="coerce").replace(0, np.nan).to_numpy() if prior_pt else np.nan
        out["현재 목표가($)"] = pd.to_numeric(df[cur_pt], errors="coerce").replace(0, np.nan).to_numpy()
    return out


def insider_summary(t, days=180):
    df = t.insider_transactions
    if df is None or df.empty:
        return None
    date_c = _col(df, "Start Date", "Date")
    text_c, trans_c = _col(df, "Text"), _col(df, "Transaction")
    who_c, pos_c = _col(df, "Insider"), _col(df, "Position")
    sh_c, val_c = _col(df, "Shares"), _col(df, "Value")
    d = df.copy()
    d["_date"] = pd.to_datetime(d[date_c], errors="coerce") if date_c else pd.NaT
    d = d[d["_date"] >= pd.Timestamp.today() - pd.Timedelta(days=days)]
    desc = (d[text_c].astype(str) if text_c else "") + " " + (d[trans_c].astype(str) if trans_c else "")
    buys = d[desc.str.contains("purchase|buy", case=False, regex=True)]
    table = pd.DataFrame({
        "날짜": buys["_date"].dt.strftime("%Y-%m-%d").to_numpy(),
        "내부자": buys[who_c].to_numpy() if who_c else "",
        "직책": buys[pos_c].to_numpy() if pos_c else "",
        "주식 수": buys[sh_c].to_numpy() if sh_c else np.nan,
        "금액($)": buys[val_c].to_numpy() if val_c else np.nan,
    })
    return {
        "buyers": int(buys[who_c].nunique()) if who_c and len(buys) else 0,
        "buy_count": int(len(buys)),
        "table": table,
    }


def fetch_detail(ticker):
    import yfinance as yf

    t = _safe(lambda: yf.Ticker(ticker))
    if t is None:
        return {"fetched": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    info = _safe(lambda: t.get_info()) or {}
    est = _safe(lambda: estimate_table(t)) or (None, None)
    targets = _safe(lambda: t.analyst_price_targets) or {}
    return {
        "fetched": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "earnings": _safe(lambda: earnings_table(t)),
        "estimates": est[0],
        "estimate_score": est[1],
        "targets": targets if isinstance(targets, dict) else {},
        "ratings": _safe(lambda: ratings_table(t)),
        "insiders": _safe(lambda: insider_summary(t)),
        "inst_pct": info.get("heldPercentInstitutions"),
        "insider_pct": info.get("heldPercentInsiders"),
        "analysts": info.get("numberOfAnalystOpinions"),
        "recommendation": info.get("recommendationKey"),
    }
