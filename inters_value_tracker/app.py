# -*- coding: utf-8 -*-
"""
INTERS 가치투자 트래커 (Streamlit 웹앱)

로컬 실행:  streamlit run app.py
배포:       README.md 참고
"""

import datetime as dt
import os
import pickle
import threading
import time

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="INTERS 가치투자 트래커", page_icon="📊", layout="wide")

try:  # Streamlit Secrets에 넣은 SEC 연락처를 환경 변수로 전달
    os.environ.setdefault("SEC_USER_AGENT", st.secrets["SEC_USER_AGENT"])
except Exception:
    pass

import detail as dm   # noqa: E402
import scanner as sc  # noqa: E402

SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "last_scan.pkl")


# ──────────────────────────────────────────────────────────────
# 모든 부원이 함께 쓰는 스캔 상태 (스캔은 서버에서 계속 돌아감)
# ──────────────────────────────────────────────────────────────
@st.cache_resource
def shared():
    state = {"df": None, "meta": None, "running": False, "progress": 0.0, "msg": "",
             "error": None, "started": None, "lock": threading.Lock()}
    try:
        with open(SAVE_PATH, "rb") as f:
            saved = pickle.load(f)
        state["df"], state["meta"] = saved["df"], saved["meta"]
    except Exception:
        pass
    return state


S = shared()


def _worker():
    def progress(frac, msg):
        S["progress"], S["msg"] = float(min(max(frac, 0.0), 1.0)), msg
    try:
        df, meta = sc.run_scan(progress)
        S["df"], S["meta"], S["error"] = df, meta, None
        try:
            os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
            with open(SAVE_PATH, "wb") as f:
                pickle.dump({"df": df, "meta": meta}, f)
        except Exception:
            pass
    except Exception as e:
        S["error"] = f"{type(e).__name__}: {e}"
    finally:
        S["running"] = False
        S["lock"].release()


def start_scan():
    if S["lock"].acquire(blocking=False):
        S.update(running=True, progress=0.0, msg="준비 중", error=None,
                 started=dt.datetime.now().strftime("%H:%M"))
        threading.Thread(target=_worker, daemon=True).start()


@st.cache_data(show_spinner=False, max_entries=3)
def classified(scan_ts, gdp):
    df = sc.classify(S["df"], gdp)
    df["class_label"] = [sc.class_label(r) for r in df.to_dict("records")]
    return df


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=100)
def get_detail(ticker):
    return dm.fetch_detail(ticker)


# ──────────────────────────────────────────────────────────────
# 표시 도우미
# ──────────────────────────────────────────────────────────────
def isnum(x):
    return x is not None and not (isinstance(x, float) and np.isnan(x)) and not pd.isna(x)


def pct(x, sign=False, digits=1):
    if not isnum(x):
        return "-"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def num(x, digits=2):
    return f"{x:,.{digits}f}" if isnum(x) else "-"


def money(x):
    if not isnum(x):
        return "-"
    a = abs(x)
    s = f"${a / 1e12:.2f}T" if a >= 1e12 else f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M"
    return ("-" if x < 0 else "") + s


# ──────────────────────────────────────────────────────────────
# 사이드바
# ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("분류 기준")
    gdp = st.slider("저성장 기준 (이익 성장률, %)", 3.0, 7.0, 5.0, 0.5,
                    help="이익 성장률이 이 값 이하이면 저성장주로 분류합니다. "
                         "린치는 GNP 성장률 수준을 기준으로 삼았고, 기본값 5%는 실질 성장률에 물가 상승분을 더한 명목 기준입니다.") / 100
    st.caption("대형우량: 기준 초과 20% 미만이면서 시총 $10B 이상\n\n"
               "중간 성장: 기준 초과 20% 미만이면서 시총 $10B 미만\n\n"
               "고성장: 20% 이상 (25% 초과 과열 주의, 50% 이상 과열 경고)")

    st.divider()
    st.header("데이터")
    if S["meta"]:
        m = S["meta"]
        st.write(f"마지막 스캔: **{m['time_kst']}** (한국 시간)")
        st.caption(f"{m['evaluated']:,}개 종목 계산, {m['minutes']}분 소요")
        if (time.time() - m["timestamp"]) / 3600 > 24 * 7:
            st.warning("결과가 일주일 넘게 지났습니다. 다시 스캔하는 것을 권장합니다.")
    else:
        st.write("아직 스캔 결과가 없습니다.")
    ua_problem = sc.sec_user_agent_problem()
    if ua_problem:
        st.warning(f"{ua_problem} Streamlit 앱 설정의 Secrets에 "
                   '`SEC_USER_AGENT = "INTERS Value Tracker your.email@gmail.com"` 형식으로 넣어 주세요.')
    st.button("다시 스캔" if S["df"] is not None else "스캔 시작", type="primary",
              use_container_width=True, disabled=S["running"], on_click=start_scan)
    st.caption("전체 스캔은 5~10분 정도 걸립니다. 재무 데이터는 분기마다 바뀌므로 실적 시즌이 끝날 때마다 한 번씩 스캔하면 충분합니다.")


# ──────────────────────────────────────────────────────────────
# 본문
# ──────────────────────────────────────────────────────────────
st.title("INTERS 가치투자 트래커")
st.caption("미너비니와 피터 린치의 기준으로 미국 상장 종목(시총 $50M 이상)을 분류하고 점검합니다. "
           "동아리 내부 학습용이며, 데이터와 링크는 개인 공부 목적으로만 사용해 주세요.")

if S["running"]:
    @st.fragment(run_every="3s")
    def progress_panel():
        if S["running"]:
            st.progress(S["progress"], text=f"스캔 중 ({S['started']} 시작): {S['msg']}")
            st.caption("창을 닫아도 스캔은 계속됩니다. 끝나면 이 화면이 자동으로 바뀝니다.")
        else:
            st.rerun()
    progress_panel()

if S["error"]:
    st.error(f"스캔에 실패했습니다. 잠시 후 다시 시도해 주세요.\n\n오류 내용: {S['error']}")

if S["meta"] and S["meta"].get("source") == "fallback":
    st.info("Nasdaq 종목 목록을 받지 못해 SEC 목록으로 스캔했습니다. 이 경우 섹터·산업 정보가 비어 있고, "
            "시가총액은 주가 × 희석주식수로 추정한 값입니다.")

if S["df"] is None:
    if not S["running"]:
        st.info("아직 스캔 결과가 없습니다. 왼쪽의 '스캔 시작'을 눌러 주세요.")
    st.stop()

df = classified(S["meta"]["timestamp"], gdp)

counts = df["class"].value_counts()
cols = st.columns(4)
for col, name in zip(cols, ["고성장", "대형우량", "중간 성장", "저성장"]):
    col.metric(name, f"{int(counts.get(name, 0)):,}개")

# 필터
class_sel = st.pills("린치 분류", sc.CLASS_ORDER + ["경기순환"], selection_mode="multi", key="f_class")
tier_sel = st.pills("시가총액", sc.TIER_ORDER, selection_mode="multi", key="f_tier",
                    format_func=lambda t: f"{t} ({sc.TIER_RANGE[t]})")
sector_opts = sorted(s for s in S["df"]["gics_ko"].unique() if s != "미분류")
if (S["df"]["gics_ko"] == "미분류").any():
    sector_opts.append("미분류")
sector_sel = st.pills("섹터", sector_opts, selection_mode="multi", key="f_sector")

t1, t2, t3, t4 = st.columns(4)
f_peg = t1.toggle("PEG 1 이하", key="f_peg", help="PER이 이익 성장률보다 낮은 종목 (린치: 1 이하면 저평가 가능성)")
f_bb = t2.toggle("자사주 매입 중", key="f_bb", help="최근 회계연도에 자사주를 사들였고 주식 수가 줄어든 종목")
f_profit = t3.toggle("흑자 기업만", key="f_profit")
f_clean = t4.toggle("경고 없는 종목만", key="f_clean")
q = st.text_input("검색", placeholder="티커나 종목명으로 검색", key="q", label_visibility="collapsed")

view = df
if class_sel:
    base = [c for c in class_sel if c != "경기순환"]
    mask = view["class"].isin(base) if base else pd.Series(True, index=view.index)
    if "경기순환" in class_sel:
        mask &= view["cyclical"]
    view = view[mask]
if tier_sel:
    view = view[view["tier"].isin(tier_sel)]
if sector_sel:
    view = view[view["gics_ko"].isin(sector_sel)]
if f_peg:
    view = view[view["peg"] <= 1]
if f_bb:
    view = view[(view["buyback_yield"] > 0) & (view["shares_change"] < 0)]
if f_profit:
    view = view[view["eps_ttm"] > 0]
if f_clean:
    view = view[view["warning_count"] == 0]
if q.strip():
    k = q.strip().lower()
    view = view[view["ticker"].str.lower().str.contains(k, regex=False)
                | view["name"].str.lower().str.contains(k, regex=False)]
view = view.sort_values("market_cap", ascending=False).reset_index(drop=True)

st.caption(f"{len(view):,}개 종목. 표에서 행을 선택하면 아래에 상세 정보가 열립니다. 열 제목을 누르면 정렬됩니다.")

table = pd.DataFrame({
    "티커": view["ticker"],
    "종목명": view["name"],
    "린치 분류": view["class_label"],
    "시총": view["tier"],
    "섹터": view["gics_ko"],
    "PER": view["per"],
    "PEG": view["peg"],
    "EPS 증가율": view["eps_q_growth"] * 100,
    "매출 증가율": view["rev_q_growth"] * 100,
    "FCF 수익률": view["fcf_yield"] * 100,
    "자사주 매입": view["buyback_yield"] * 100,
    "경고": view["warning_count"],
})
event = st.dataframe(
    table, hide_index=True, use_container_width=True, height=460,
    on_select="rerun", selection_mode="single-row", key="main_table",
    column_config={
        "종목명": st.column_config.TextColumn(width="medium"),
        "PER": st.column_config.NumberColumn(format="%.1f"),
        "PEG": st.column_config.NumberColumn(format="%.2f", help="PER ÷ EPS 연평균 성장률(%). 1 이하면 성장 대비 싸다는 뜻"),
        "EPS 증가율": st.column_config.NumberColumn(format="%.1f%%", help="최근 분기 EPS, 전년 동기 대비"),
        "매출 증가율": st.column_config.NumberColumn(format="%.1f%%", help="최근 분기 매출, 전년 동기 대비"),
        "FCF 수익률": st.column_config.NumberColumn(format="%.1f%%", help="잉여현금흐름 ÷ 시가총액. 린치: 10% 양호, 20% 우수"),
        "자사주 매입": st.column_config.NumberColumn(format="%.1f%%", help="최근 회계연도 자사주 매입액 ÷ 시가총액"),
        "경고": st.column_config.NumberColumn(format="%d", help="상세 화면의 체크리스트 탭에서 내용을 볼 수 있습니다"),
    },
)

st.download_button("표에 보이는 종목 CSV 받기", data=sc.to_csv_bytes(view),
                   file_name=f"value_tracker_{S['meta']['time_kst'][:10]}.csv", mime="text/csv")


# ──────────────────────────────────────────────────────────────
# 종목 상세
# ──────────────────────────────────────────────────────────────
rows = event.selection.rows if event and hasattr(event, "selection") else []
options = df.sort_values("market_cap", ascending=False)["ticker"].tolist()
names = dict(zip(df["ticker"], df["name"]))
picked = st.selectbox("종목 바로 보기", options, index=None, placeholder="티커를 입력하거나 고르세요",
                      format_func=lambda t: f"{t}  {names.get(t, '')}")
ticker = view.loc[rows[0], "ticker"] if rows else picked
if not ticker:
    st.stop()

row = df[df["ticker"] == ticker].iloc[0].to_dict()

st.divider()
st.subheader(f"{row['ticker']}  {row['name']}")
st.write(f"**{row['class_label']}** | {row['tier']} ({money(row['market_cap'])}) | {row['gics_ko']} | "
         f"{row['industry'] or '산업 정보 없음'} | 주가 ${num(row['price'])}")
l1, l2, l3, _ = st.columns([1, 1, 1, 3])
l1.link_button("잭스에서 보기", sc.zacks_link(row["ticker"]), use_container_width=True)
l2.link_button("SEC 공시 보기", sc.sec_link(row["cik"]), use_container_width=True)
l3.link_button("트레이딩뷰 차트", sc.tradingview_link(row["ticker"]), use_container_width=True)
st.caption(f"재무 데이터 기준일: {row.get('data_end') or '-'} (SEC 공시)")

with st.spinner("애널리스트·내부자 데이터를 불러오는 중"):
    det = get_detail(row["ticker"])

tab1, tab2, tab3, tab4, tab5 = st.tabs(["실적", "애널리스트", "가치평가·재무", "주주환원·수급", "체크리스트"])

with tab1:
    c = st.columns(4)
    c[0].metric("EPS 증가율 (최근 분기)", pct(row["eps_q_growth"]),
                delta=None if not isnum(row["eps_q_growth_prev"]) else f"직전 분기 {pct(row['eps_q_growth_prev'])}",
                delta_color="off")
    c[1].metric("매출 증가율 (최근 분기)", pct(row["rev_q_growth"]),
                delta=None if not isnum(row["rev_q_growth_prev"]) else f"직전 분기 {pct(row['rev_q_growth_prev'])}",
                delta_color="off")
    c[2].metric(f"EPS 연평균 성장률 ({row.get('cagr_years') or '-'}년)", pct(row["eps_cagr"]))
    c[3].metric("성장 단계", row.get("stage") or "-", help="최근 2개 분기 매출 증가율이 그 전 2개 분기보다 5%p 이상 높으면 가속, 낮으면 둔화")

    g1, g2 = st.columns(2)
    for col, key, title, scale, unit in [(g1, "q_eps", "분기 EPS ($)", 1, ""), (g2, "q_rev", "분기 매출 ($M)", 1e6, "")]:
        data = row.get(key) or []
        if data:
            chart = pd.DataFrame({"분기말": [d[0] for d in data], title: [d[1] / scale for d in data]}).set_index("분기말")
            col.caption(title)
            col.bar_chart(chart, height=220)
    if any(d[2] for d in (row.get("q_eps") or [])):
        st.caption("4분기 값은 연간 실적에서 1~3분기 합을 빼서 계산했습니다.")

    margins = pd.DataFrame([
        {"항목": label, "최근 1년": pct(row[f"{k}_margin"]), "1년 전": pct(row[f"{k}_margin_prev"]),
         "변화": (f"{(row[f'{k}_margin'] - row[f'{k}_margin_prev']) * 100:+.1f}%p"
                if isnum(row[f"{k}_margin"]) and isnum(row[f"{k}_margin_prev"]) else "-")}
        for k, label in [("gross_profit", "매출총이익률"), ("op_income", "영업이익률"),
                         ("net_income", "순이익률"), ("pretax", "세전이익률")]
    ])
    st.markdown("**마진 추이**")
    st.dataframe(margins, hide_index=True, use_container_width=True)
    st.write(f"영업외 이익 비중: **{pct(row['nonop_share'])}** "
             "(세전이익 중 영업이익이 아닌 부분. 높으면 일회성 이익인지 확인)")

    st.markdown("**어닝 서프라이즈**")
    if det.get("earnings") is not None and not det["earnings"].empty:
        st.dataframe(det["earnings"], hide_index=True, use_container_width=True,
                     column_config={"서프라이즈(%)": st.column_config.NumberColumn(format="%.1f%%")})
    else:
        st.caption("서프라이즈 데이터를 불러오지 못했습니다.")
    st.caption("매출 서프라이즈와 '가이던스 하향 후 서프라이즈' 여부는 무료 데이터로 확인할 수 없어 체크리스트의 직접 확인 항목에 넣었습니다.")

with tab2:
    score = det.get("estimate_score")
    st.metric("추정치 상향 점수", "-" if score is None else f"{score:+d}점",
              help="최근 30일 동안 EPS 추정치가 오르면 +, 내리면 -. 미너비니가 더 중요하게 본 연간 추정치는 2점")
    if det.get("estimates") is not None:
        st.dataframe(det["estimates"], hide_index=True, use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(format="%+.1f%%")
                                    for c in ["30일 전 대비(%)", "60일 전 대비(%)", "90일 전 대비(%)"]})
    else:
        st.caption("추정치 데이터를 불러오지 못했습니다.")

    tg = det.get("targets") or {}
    if tg.get("mean"):
        a, b, c3, d4 = st.columns(4)
        a.metric("목표주가 평균", f"${num(tg.get('mean'))}",
                 delta=f"현재가 대비 {pct(tg['mean'] / row['price'] - 1, sign=True)}" if row["price"] else None)
        b.metric("최고", f"${num(tg.get('high'))}")
        c3.metric("최저", f"${num(tg.get('low'))}")
        d4.metric("애널리스트 수", f"{det.get('analysts') or '-'}명")

    st.markdown("**증권사별 투자의견·목표주가 변경 (최근 12개월)**")
    rt = det.get("ratings")
    if rt is not None and not rt.empty:
        st.dataframe(rt, hide_index=True, use_container_width=True, height=min(36 * (len(rt) + 1) + 2, 420),
                     column_config={"이전 목표가($)": st.column_config.NumberColumn(format="%.2f"),
                                    "현재 목표가($)": st.column_config.NumberColumn(format="%.2f")})
    else:
        st.caption("최근 12개월 변경 내역이 없거나 불러오지 못했습니다.")
    st.caption(f"애널리스트 데이터: Yahoo Finance ({det.get('fetched')} 조회)")

with tab3:
    val = pd.DataFrame([
        ["PER", num(row["per"], 1), f"업종 중앙값 {num(row['industry_per'], 1)}"],
        ["PEG", num(row["peg"]), "PER ÷ 이익 성장률. 0.5 이하 매우 긍정적, 2 이상 매우 부정적"],
        ["린치 비율", num(row["lynch_ratio"]), "(성장률 + 배당수익률) ÷ PER. 1 미만 나쁨, 1.5 괜찮음, 2 이상 좋음"],
        ["FCF 수익률", pct(row["fcf_yield"]), "10% 양호, 20% 우수"],
        ["PBR", num(row["pbr"]), "참고용. 장부가치는 실제 가치와 다를 수 있음"],
        ["시총 대비 순현금", pct(row["net_cash_ratio"]), "플러스면 현금이 부채보다 많음. 클수록 주가의 바닥 역할"],
        ["부채비율 (부채 ÷ 자본)", num(row["debt_to_equity"]), "린치의 보통 수준: 자본 75 : 부채 25 (약 0.33)"],
        ["단기 상환 부채 비중", pct(row["short_debt_share"]), "높을수록 위기 때 상환 압박"],
        ["유동비율", num(row["current_ratio"]), "유동자산 ÷ 유동부채"],
        ["당좌비율", num(row["quick_ratio"]), "재고를 뺀 유동자산 ÷ 유동부채"],
        ["매출채권 증가율", pct(row["receivables_growth"]), f"매출 증가율 {pct(row['rev_ttm_growth'])}보다 높으면 주의"],
        ["매출채권회전일수", num(row["dso"], 0) + "일" if isnum(row["dso"]) else "-", "매출을 현금으로 받기까지 걸리는 일수"],
        ["재고 증가율", pct(row["inventory_growth"]), f"매출 증가율 {pct(row['rev_ttm_growth'])}보다 높으면 적신호"],
        ["세전이익률", pct(row["pretax_margin"]),
         f"업종 내 상위 {max(1, 100 - row['pretax_rank'] * 100):.0f}%" if isnum(row.get("pretax_rank")) else "업종 비교 불가"],
    ], columns=["지표", "값", "참고"])
    st.dataframe(val, hide_index=True, use_container_width=True, height=36 * 15 + 2)
    st.caption(f"재무상태표 기준일: {row.get('balance_date') or '-'}")

with tab4:
    ins = det.get("insiders") or {}
    sh = pd.DataFrame([
        ["자사주 매입액 (최근 회계연도)", money(row["buyback"])],
        ["자사주 매입 수익률", pct(row["buyback_yield"])],
        ["희석주식 수 변화 (전년 대비)", pct(row["shares_change"], sign=True)],
        ["배당수익률", pct(row["div_yield"])],
        ["배당성향", pct(row["payout"])],
        ["연속 배당 증가", f"{row['div_streak']}년" + (" (최근 6년 자료 기준)" if row["div_streak"] else "")],
        ["내부자 매수 (최근 6개월)", f"{ins.get('buyers', 0)}명, {ins.get('buy_count', 0)}건" if ins else "-"],
        ["경영진 지분율", pct(det.get("insider_pct"))],
        ["기관 보유 비율", pct(det.get("inst_pct"))],
        ["애널리스트 수", f"{det.get('analysts')}명" if det.get("analysts") else "-"],
    ], columns=["항목", "값"])
    st.dataframe(sh, hide_index=True, use_container_width=True)
    st.caption("린치: 여러 명이 동시에 사들이는 내부자 매수는 강한 신호지만, 내부자 매도는 대개 별 의미가 없습니다.")
    if ins and ins.get("table") is not None and not ins["table"].empty:
        st.markdown("**내부자 매수 내역**")
        st.dataframe(ins["table"], hide_index=True, use_container_width=True)


def checklist(r, d):
    """(항목, 판단, 근거). 판단: 충족 / 주의 / 보통 / 직접 확인"""
    out = []

    def add(item, ok, basis):
        out.append({"항목": item, "판단": ok, "근거": basis})

    inst, analysts = d.get("inst_pct"), d.get("analysts")
    ins = d.get("insiders") or {}
    pvi = r.get("per_vs_industry")
    add("PER이 동종 업종보다 낮은가", "-" if not isnum(pvi) else ("충족" if pvi <= 1 else "주의"),
        f"업종 중앙값의 {num(pvi)}배")
    add("기관 보유 비율이 낮은가", "-" if not isnum(inst) else ("충족" if inst < 0.3 else "주의" if inst >= 0.6 else "보통"),
        pct(inst))
    add("내부자 매수 또는 자사주 매입이 있는가",
        "충족" if (ins.get("buyers", 0) > 0 or r["buyback_yield"] > 0) else "보통",
        f"내부자 매수 {ins.get('buyers', 0)}명, 자사주 매입 수익률 {pct(r['buyback_yield'])}")
    add("이익이 꾸준한가", "충족" if (isnum(r["eps_cagr"]) and r["eps_cagr"] > 0 and r["eps_ttm"] > 0) else "주의",
        f"EPS 연평균 성장률 {pct(r['eps_cagr'])}")
    ds = r.get("debt_share")
    add("재무구조가 건실한가", "-" if not isnum(ds) else ("충족" if ds <= 0.25 else "보통" if ds <= 0.5 else "주의"),
        f"부채 비중 {pct(ds)} (보통 25%)")
    add("순현금이 있는가 (주가 바닥 역할)", "-" if not isnum(r["net_cash_ratio"]) else
        ("충족" if r["net_cash_ratio"] > 0 else "보통"), f"시총 대비 {pct(r['net_cash_ratio'])}")

    cls = r["class"]
    if cls == "저성장":
        add("배당을 꾸준히 늘려 왔는가", "충족" if r["div_streak"] >= 3 else "주의", f"연속 증가 {r['div_streak']}년")
        add("배당성향이 낮아 여유가 있는가", "-" if not isnum(r["payout"]) else
            ("충족" if r["payout"] < 0.6 else "주의" if r["payout"] > 0.8 else "보통"), pct(r["payout"]))
    if cls == "대형우량":
        add("장기 성장률을 최근에도 유지하는가",
            "-" if not isnum(r["eps_ttm_growth"]) else ("충족" if r["eps_ttm_growth"] >= r["eps_cagr"] - 0.05 else "주의"),
            f"최근 1년 {pct(r['eps_ttm_growth'])} vs 장기 {pct(r['eps_cagr'])}")
        add("이익을 갉아먹는 사업다악화가 없는가", "직접 확인", "최근 인수합병, 무관한 사업 진출")
        add("과거 불황기에 어떻게 버텼는가", "직접 확인", "2020년, 2022년 실적과 주가")
    if cls in ("고성장", "중간 성장"):
        g = r["eps_cagr"]
        add("이익 성장률이 20~25% 범위인가", "-" if not isnum(g) else
            ("충족" if 0.2 <= g <= 0.25 else "주의" if g > 0.25 else "보통"), pct(g))
        add("PER이 성장률 근처인가 (PEG)", "-" if not isnum(r["peg"]) else
            ("충족" if r["peg"] <= 1 else "보통" if r["peg"] <= 1.5 else "주의"), f"PEG {num(r['peg'])}")
        add("확장 속도가 빨라지고 있는가", {"가속": "충족", "유지": "보통", "둔화": "주의"}.get(r.get("stage"), "-"),
            f"성장 단계: {r.get('stage') or '-'}")
        add("기관·애널리스트가 아직 적은가", "-" if not (isnum(inst) and analysts) else
            ("충족" if inst < 0.3 and analysts <= 5 else "주의" if inst >= 0.6 and analysts >= 20 else "보통"),
            f"기관 {pct(inst)}, 애널리스트 {analysts or '-'}명")
        add("돈을 벌어 줄 제품이 회사의 주력인가", "직접 확인", "제품별 매출 비중 (10-K 사업부문 정보)")
        add("다른 지역에서도 성공을 반복했고 성장 여지가 남았는가", "직접 확인", "매장·지역 확장 현황")
    if r.get("cyclical"):
        add("재고가 매출보다 빠르게 쌓이지 않는가", "-" if not isnum(r["inventory_growth"]) else
            ("주의" if r["inventory_growth"] > (r["rev_ttm_growth"] if isnum(r["rev_ttm_growth"]) else 0) + 0.1 else "충족"),
            f"재고 {pct(r['inventory_growth'])} vs 매출 {pct(r['rev_ttm_growth'])}")
        add("업황 사이클의 어디쯤인가, 신규 진입자는 없는가", "직접 확인", "원자재 가격, 업계 설비 증설 뉴스")

    add("특정 고객 의존도가 25~30%를 넘지 않는가", "직접 확인", "10-K의 주요 고객(Concentration) 항목")
    add("시장점유율이 유지되고 있는가", "직접 확인", "업계 자료")
    add("가이던스를 낮춘 뒤 나온 서프라이즈가 아닌가", "직접 확인", "직전 실적 발표 전후 뉴스")
    return pd.DataFrame(out)


with tab5:
    warns = row.get("warnings") or []
    if warns:
        st.markdown("**경고**")
        for w in warns:
            st.warning(w)
    else:
        st.success("자동으로 잡힌 경고가 없습니다.")
    st.markdown(f"**{row['class']} 체크리스트**" + (" (경기순환 포함)" if row.get("cyclical") else ""))
    st.dataframe(checklist(row, det), hide_index=True, use_container_width=True,
                 column_config={"항목": st.column_config.TextColumn(width="large")})


with st.expander("지표 설명과 데이터 출처"):
    st.markdown(
        "- **린치 분류**: 최근 회계연도 EPS의 3년(없으면 2년) 연평균 성장률로 나눕니다. 적자이거나 성장률을 계산할 수 없으면 분류 불가입니다.\n"
        "- **경기순환**: 린치가 예로 든 자동차, 항공, 철강, 화학, 금속, 석유, 제지, 주택건설 업종에 붙는 보조 태그입니다.\n"
        "- **EPS·매출 증가율**: 최근 분기의 전년 동기 대비 증가율입니다. 4분기는 연간에서 1~3분기를 빼서 계산합니다.\n"
        "- **PER**: 주가 ÷ 최근 4개 분기 EPS 합입니다.\n"
        "- **재무 데이터**: SEC XBRL 공시. 미국 회계기준(US GAAP)으로 공시하지 않는 해외 기업(20-F 제출 기업 등)은 재무 지표가 비어 있을 수 있습니다.\n"
        "- **종목 목록·시가총액·주가·섹터**: Nasdaq 스크리너. 섹터는 GICS 11개 섹터로 매핑한 값입니다.\n"
        "- **애널리스트·내부자 데이터**: Yahoo Finance (종목을 열 때 조회).\n"
        "- 잭스 랭크는 약관상 재배포가 금지되어 있어 '잭스에서 보기' 링크로 제공합니다."
    )
