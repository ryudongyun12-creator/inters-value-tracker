# -*- coding: utf-8 -*-
"""
INTERS 가치투자 트래커: 데이터 수집과 지표 계산

전체 스캔(모든 종목 한꺼번에)
  - 종목 목록·시가총액·주가·섹터: Nasdaq 스크리너 (막히면 SEC 목록 + Yahoo 주가로 대체)
  - 재무 데이터: SEC XBRL "frames" API
    한 번 요청에 모든 상장사의 같은 항목이 오기 때문에, 종목이 수천 개여도 요청은 수백 번이면 끝납니다.
    SEC 데이터는 미국 정부 공개 자료라 동아리 안에서 공유해도 문제가 없습니다.

종목 상세(종목을 눌렀을 때만): detail.py에서 Yahoo Finance로 가져옵니다.

분류와 경고 중 사용자가 바꿀 수 있는 기준(저성장 기준 등)은 classify()가 화면에서 즉시 다시 계산합니다.
"""

import datetime as dt
import io
import os
import re
import threading
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

# ──────────────────────────────────────────────────────────────
# 기준값
# ──────────────────────────────────────────────────────────────
MIN_CAP = 50e6            # 시총 $50M 미만 제외
GDP_BASE = 0.05           # 저성장 기준(명목 GDP 성장률 수준), 화면에서 조정
STALWART_CAP = 10e9       # 대형우량 시총 기준
FAST_MIN = 0.20           # 고성장 기준
HOT_WARN = 0.25           # 과열 주의
HOT_ALERT = 0.50          # 과열 경고

DEFAULT_UA = "INTERS Value Tracker admin@example.com"
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

MCAP_TIERS = [(200e9, "초대형"), (10e9, "대형"), (2e9, "중형"), (300e6, "소형"), (0, "초소형")]
TIER_ORDER = ["초대형", "대형", "중형", "소형", "초소형"]
TIER_RANGE = {"초대형": "$200B 이상", "대형": "$10B~200B", "중형": "$2B~10B",
              "소형": "$300M~2B", "초소형": "$50M~300M"}

CLASS_ORDER = ["고성장", "대형우량", "중간 성장", "저성장", "분류 불가"]

GICS_KO = {
    "Information Technology": "정보기술", "Health Care": "헬스케어", "Financials": "금융",
    "Consumer Discretionary": "경기소비재", "Consumer Staples": "필수소비재", "Industrials": "산업재",
    "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산", "Materials": "소재",
    "Communication Services": "커뮤니케이션서비스", "Unclassified": "미분류",
}
NASDAQ_TO_GICS = {
    "Technology": "Information Technology", "Telecommunications": "Communication Services",
    "Health Care": "Health Care", "Finance": "Financials", "Real Estate": "Real Estate",
    "Consumer Discretionary": "Consumer Discretionary", "Consumer Staples": "Consumer Staples",
    "Industrials": "Industrials", "Basic Materials": "Materials", "Energy": "Energy",
    "Utilities": "Utilities",
}

# 린치가 경기순환주로 든 업종(자동차, 항공, 철강, 화학, 알루미늄·금속, 석유, 제지 등)
CYCLICAL_KEYWORDS = ["auto", "air freight", "airline", "steel", "iron ore", "aluminum", "metal",
                     "mining", "chemical", "oil", "paper", "homebuilding", "building products"]

# ──────────────────────────────────────────────────────────────
# SEC XBRL 항목 정의
# 같은 내용을 회사마다 다른 태그로 보고하므로 후보를 여러 개 두고, 회사별로 가장 최근 자료가 있는 태그 하나만 씁니다.
# ──────────────────────────────────────────────────────────────
FLOW_Q = {   # 분기 + 연간
    "eps": (["EarningsPerShareDiluted", "EarningsPerShareBasic"], "USD-per-shares"),
    "revenue": (["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                 "RevenueFromContractWithCustomerIncludingAssessedTax"], "USD"),
    "net_income": (["NetIncomeLoss"], "USD"),
    "op_income": (["OperatingIncomeLoss"], "USD"),
    "gross_profit": (["GrossProfit"], "USD"),
    "pretax": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
               "USD"),
}
FLOW_A = {   # 연간만 (현금흐름표 항목은 분기 단독 수치가 공시되지 않음)
    "ocf": (["NetCashProvidedByUsedInOperatingActivities"], "USD"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment"], "USD"),
    "buyback": (["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"], "USD"),
    "dps": (["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"], "USD-per-shares"),
    "shares": (["WeightedAverageNumberOfDilutedSharesOutstanding"], "shares"),
}
INSTANT = {  # 재무상태표 (분기말 시점)
    "cash": (["CashAndCashEquivalentsAtCarryingValue"], "USD"),
    "st_invest": (["ShortTermInvestments", "MarketableSecuritiesCurrent"], "USD"),
    "ltd_total": (["LongTermDebt"], "USD"),
    "ltd_noncurrent": (["LongTermDebtNoncurrent"], "USD"),
    "ltd_current": (["LongTermDebtCurrent"], "USD"),
    "st_borrow": (["ShortTermBorrowings"], "USD"),
    "cp": (["CommercialPaper"], "USD"),
    "equity": (["StockholdersEquity"], "USD"),
    "cur_assets": (["AssetsCurrent"], "USD"),
    "cur_liab": (["LiabilitiesCurrent"], "USD"),
    "inventory": (["InventoryNet"], "USD"),
    "receivables": (["AccountsReceivableNetCurrent"], "USD"),
}

DAY = dt.timedelta(days=1)


def _noop(frac, msg):
    pass


def sec_user_agent():
    return os.environ.get("SEC_USER_AGENT") or DEFAULT_UA


def mcap_tier(m):
    if m is None or pd.isna(m):
        return "초소형"
    for floor, name in MCAP_TIERS:
        if m >= floor:
            return name
    return "초소형"


def clean_name(name):
    name = str(name or "").strip()
    name = re.sub(r"\s+(Class [A-Z]\s+)?(Common Stock|Ordinary Shares|American Depositary Shares|"
                  r"Common Shares|Depositary Shares).*$", "", name, flags=re.I)
    return name.strip()


def norm_ticker(t):
    return str(t or "").strip().upper().replace("/", "-").replace(".", "-")


def _date(s):
    try:
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def _num(x):
    try:
        x = float(x)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def _ratio(a, b):
    a, b = _num(a), _num(b)
    if np.isnan(a) or np.isnan(b) or b == 0:
        return np.nan
    return a / b


def _growth(cur, prev):
    cur, prev = _num(cur), _num(prev)
    if np.isnan(cur) or np.isnan(prev) or prev <= 0:
        return np.nan
    return cur / prev - 1


# ──────────────────────────────────────────────────────────────
# SEC 요청 (초당 10회 제한을 넘지 않도록 간격 유지)
# ──────────────────────────────────────────────────────────────
class SecClient:
    def __init__(self, user_agent, per_second=8):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.interval = 1.0 / per_second
        self.last = 0.0
        self.lock = threading.Lock()

    def get_json(self, url):
        for attempt in range(4):
            with self.lock:
                wait = self.interval - (time.time() - self.last)
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
            try:
                r = self.session.get(url, timeout=90)
            except requests.RequestException:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            if r.status_code in (403, 429, 503):
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        return None


def period_lists(today=None):
    today = today or dt.date.today()
    y = today.year
    annual = [f"CY{yr}" for yr in range(y - 6, y + 1)]
    q = (today.month - 1) // 3 + 1
    quarters = []
    yy, qq = y, q
    for _ in range(13):
        qq -= 1
        if qq == 0:
            yy, qq = yy - 1, 4
        quarters.append(f"CY{yy}Q{qq}")
    quarters.reverse()
    instants = [p + "I" for p in quarters[-6:]]
    return annual, quarters, instants


def fetch_frames(client, ciks, progress=_noop, start=0.05, end=0.80, today=None):
    """모든 항목·기간의 frame을 받아 {key: {cik: {tag: [(start, end, val), ...]}}} 형태로 정리."""
    annual, quarters, instants = period_lists(today)
    jobs = []
    for key, (tags, unit) in FLOW_Q.items():
        for tag in tags:
            for p in annual + quarters:
                jobs.append(("flow", key, tag, unit, p))
    for key, (tags, unit) in FLOW_A.items():
        for tag in tags:
            for p in annual:
                jobs.append(("flow", key, tag, unit, p))
    for key, (tags, unit) in INSTANT.items():
        for tag in tags:
            for p in instants:
                jobs.append(("inst", key, tag, unit, p))

    flows, insts = {}, {}
    for i, (kind, key, tag, unit, period) in enumerate(jobs, start=1):
        url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json"
        js = client.get_json(url)
        if js and js.get("data"):
            target = flows if kind == "flow" else insts
            bucket = target.setdefault(key, {})
            for row in js["data"]:
                cik = int(row["cik"])
                if cik not in ciks:
                    continue
                e = _date(row.get("end"))
                if e is None:
                    continue
                s = _date(row.get("start")) if kind == "flow" else None
                bucket.setdefault(cik, {}).setdefault(tag, []).append((s, e, _num(row.get("val"))))
        if i % 5 == 0 or i == len(jobs):
            progress(start + (end - start) * i / len(jobs), f"SEC 재무 데이터 받는 중 {i}/{len(jobs)}")
    return flows, insts


# ──────────────────────────────────────────────────────────────
# 회사별 시계열 정리
# ──────────────────────────────────────────────────────────────
def _pick_tag(tag_map, order):
    """회사가 보고한 후보 태그 중 가장 최근 자료가 있는 것 하나 (같으면 우선순위 순)."""
    best, best_end = None, None
    for tag in order:
        rows = tag_map.get(tag)
        if not rows:
            continue
        last = max(r[1] for r in rows)
        if best_end is None or last > best_end + dt.timedelta(days=20):
            best, best_end = tag, last
    return tag_map.get(best, []) if best else []


def _split(rows):
    """기간 길이로 연간(약 1년)과 분기(약 3개월)를 나눔."""
    annual, quarter = [], []
    for s, e, v in rows:
        if s is None or np.isnan(v):
            continue
        days = (e - s).days
        if 330 <= days <= 400:
            annual.append((s, e, v))
        elif 60 <= days <= 120:
            quarter.append((s, e, v))
    return _dedupe(annual), _dedupe(quarter)


def _dedupe(rows):
    out = []
    for r in sorted(rows, key=lambda r: r[1]):
        if out and abs((r[1] - out[-1][1]).days) <= 15:
            out[-1] = r
        else:
            out.append(r)
    return out


def build_quarters(annual, quarters):
    """연간 보고서에만 있는 4분기 수치를 '연간 - 1~3분기 합'으로 채워 분기 시계열 완성."""
    qs = [(s, e, v, False) for s, e, v in quarters]
    for a_s, a_e, a_v in annual:
        inside = [q for q in qs if q[0] >= a_s - 20 * DAY and q[1] <= a_e + 20 * DAY]
        has_last = any(abs((q[1] - a_e).days) <= 20 for q in inside)
        if len(inside) == 3 and not has_last:
            s = max(q[1] for q in inside) + DAY
            qs.append((s, a_e, a_v - sum(q[2] for q in inside), True))
    qs.sort(key=lambda q: q[1])
    out = []
    for q in qs:
        if out and abs((q[1] - out[-1][1]).days) <= 15:
            continue
        out.append(q)
    return out


def _find_prior(series, idx, years=1, tol=25):
    target = series[idx][1] - dt.timedelta(days=365 * years)
    for j in range(idx - 1, -1, -1):
        if abs((series[j][1] - target).days) <= tol * years:
            return j
    return None


def yoy_list(qs):
    """분기별 전년 동기 대비 증가율 [(end, growth), ...]"""
    out = []
    for i in range(len(qs)):
        j = _find_prior(qs, i)
        out.append((qs[i][1], _growth(qs[i][2], qs[j][2]) if j is not None else np.nan))
    return out


def _ttm_at(qs, annual, target):
    """target 날짜에 끝나는 1년치: 연간 값이 있으면 그대로, 없으면 연속된 4개 분기 합."""
    for _, e, v in reversed(annual):
        if abs((e - target).days) <= 20:
            return v
    idx = next((i for i in range(len(qs) - 1, -1, -1) if abs((qs[i][1] - target).days) <= 20), None)
    if idx is None or idx < 3:
        return np.nan
    block = qs[idx - 3:idx + 1]
    span = (block[-1][1] - block[0][0]).days
    gaps_ok = all(60 <= (block[k][1] - block[k - 1][1]).days <= 120 for k in range(1, 4))
    return sum(b[2] for b in block) if (gaps_ok and 330 <= span <= 400) else np.nan


def ttm_pair(qs, annual):
    """(최근 1년 합, 기준일, 그 1년 전 1년 합). 분기가 끊겨 있으면 최근 연간 값으로 대체."""
    ends = [a[1] for a in annual[-1:]] + [q[1] for q in qs[-1:]]
    if not ends:
        return np.nan, None, np.nan
    target = max(ends)
    cur = _ttm_at(qs, annual, target)
    if np.isnan(_num(cur)) and annual:
        target, cur = annual[-1][1], annual[-1][2]
    prev = _ttm_at(qs, annual, target - dt.timedelta(days=365))
    return cur, target, prev


def company_flow(flow_map, key, cik, order):
    rows = _pick_tag(flow_map.get(key, {}).get(cik, {}), order)
    annual, quarter = _split(rows)
    return annual, build_quarters(annual, quarter)


def company_instant(inst_map, key, cik, order):
    rows = _pick_tag(inst_map.get(key, {}).get(cik, {}), order)
    return _dedupe([(e, e, v) for _, e, v in rows if not np.isnan(v)])


# ──────────────────────────────────────────────────────────────
# 회사별 지표 계산 (시가총액·주가가 필요한 지표 포함)
# ──────────────────────────────────────────────────────────────
def compute_company(cik, flows, insts, price, mcap):
    m = {}
    series = {}
    for key, (tags, _) in FLOW_Q.items():
        series[key] = company_flow(flows, key, cik, tags)
    for key, (tags, _) in FLOW_A.items():
        series[key] = company_flow(flows, key, cik, tags)

    eps_a, eps_q = series["eps"]
    rev_a, rev_q = series["revenue"]
    if not eps_a and not eps_q and not rev_a and not rev_q:
        return None

    # 실적 (미너비니)
    eps_ttm, eps_ttm_end, eps_ttm_prev = ttm_pair(eps_q, eps_a)
    rev_ttm, _, rev_ttm_prev = ttm_pair(rev_q, rev_a)
    eps_yoy = yoy_list(eps_q)
    rev_yoy = yoy_list(rev_q)

    m["eps_ttm"] = eps_ttm
    m["data_end"] = eps_ttm_end.isoformat() if eps_ttm_end else None
    m["eps_q_growth"] = eps_yoy[-1][1] if eps_yoy else np.nan
    m["eps_q_growth_prev"] = eps_yoy[-2][1] if len(eps_yoy) >= 2 else np.nan
    m["rev_q_growth"] = rev_yoy[-1][1] if rev_yoy else np.nan
    m["rev_q_growth_prev"] = rev_yoy[-2][1] if len(rev_yoy) >= 2 else np.nan
    m["rev_ttm"] = rev_ttm
    m["rev_ttm_growth"] = _growth(rev_ttm, rev_ttm_prev)
    m["eps_ttm_growth"] = _growth(eps_ttm, eps_ttm_prev)

    # 성장 단계: 최근 2개 분기 매출 증가율 평균 vs 그 전 2개 분기
    rg = [g for _, g in rev_yoy if not np.isnan(g)]
    if len(rg) >= 4:
        diff = np.mean(rg[-2:]) - np.mean(rg[-4:-2])
        m["stage"] = "가속" if diff > 0.05 else ("둔화" if diff < -0.05 else "유지")
    else:
        m["stage"] = None

    # 마진 (최근 4개 분기 합 기준) 과 1년 전 대비 변화
    for key in ["gross_profit", "op_income", "net_income", "pretax"]:
        a, q = series[key]
        cur, _, prev = ttm_pair(q, a)
        m[f"{key}_margin"] = _ratio(cur, rev_ttm)
        m[f"{key}_margin_prev"] = _ratio(prev, rev_ttm_prev)
        m[f"{key}_ttm"] = cur
    pre, op = m["pretax_ttm"], m["op_income_ttm"]
    m["nonop_share"] = (pre - op) / pre if (not np.isnan(_num(pre)) and _num(pre) > 0
                                           and not np.isnan(_num(op))) else np.nan

    # 장기 EPS 성장률 (연간 기준 3년, 없으면 2년 연평균)
    m["eps_cagr"], m["cagr_years"] = np.nan, None
    if eps_a:
        a0 = eps_a[-1]
        for yrs in (3, 2):
            j = _find_prior(eps_a, len(eps_a) - 1, years=yrs, tol=40)
            if j is not None:
                if a0[2] > 0 and eps_a[j][2] > 0:
                    m["eps_cagr"] = (a0[2] / eps_a[j][2]) ** (1 / yrs) - 1
                    m["cagr_years"] = yrs
                break
        m["eps_annual_last"] = a0[2]
    else:
        m["eps_annual_last"] = np.nan

    # 현금흐름·주주환원 (최근 회계연도)
    def last_annual(key):
        a, _ = series[key]
        return a[-1] if a else None

    ocf, capex = last_annual("ocf"), last_annual("capex")
    same_year = ocf and capex and abs((capex[1] - ocf[1]).days) <= 20
    m["fcf"] = ocf[2] - capex[2] if same_year else np.nan
    capex_a = series["capex"][0]
    m["capex_growth"] = _growth(capex_a[-1][2], capex_a[-2][2]) if len(capex_a) >= 2 else np.nan
    bb = last_annual("buyback")
    m["buyback"] = bb[2] if bb and eps_a and (eps_a[-1][1] - bb[1]).days <= 400 else 0.0
    sh = series["shares"][0]
    m["shares_change"] = _growth(sh[-1][2], sh[-2][2]) if len(sh) >= 2 else np.nan
    m["shares_latest"] = sh[-1][2] if sh else np.nan

    dps = series["dps"][0]
    recent_div = bool(dps) and (dt.date.today() - dps[-1][1]).days <= 550
    m["dps"] = dps[-1][2] if recent_div else 0.0
    streak = 0
    if recent_div:
        for k in range(len(dps) - 1, 0, -1):
            if dps[k][2] > dps[k - 1][2] and 300 <= (dps[k][1] - dps[k - 1][1]).days <= 430:
                streak += 1
            else:
                break
    m["div_streak"] = streak
    m["payout"] = _ratio(m["dps"], m["eps_annual_last"]) if m["dps"] > 0 and m["eps_annual_last"] > 0 else np.nan

    # 재무상태표 (가장 최근 분기말)
    bs = {}
    for key, (tags, _) in INSTANT.items():
        bs[key] = company_instant(insts, key, cik, tags)
    latest_dates = [v[-1][1] for v in bs.values() if v]
    ref = max(latest_dates) if latest_dates else None

    def bal(key, years_ago=0):
        rows = bs[key]
        if not rows or ref is None:
            return np.nan
        target = ref - dt.timedelta(days=365 * years_ago)
        for _, e, v in reversed(rows):
            if abs((e - target).days) <= 45:
                return v
        return np.nan

    def z(x):
        return 0.0 if np.isnan(x) else x

    cash = bal("cash")
    cash_total = cash + z(bal("st_invest")) if not np.isnan(cash) else np.nan

    def debt_at(years_ago=0):
        ltd = bal("ltd_total", years_ago)
        if np.isnan(ltd):
            parts = [bal("ltd_noncurrent", years_ago), bal("ltd_current", years_ago)]
            ltd = np.nan if all(np.isnan(p) for p in parts) else z(parts[0]) + z(parts[1])
        st = [bal("st_borrow", years_ago), bal("cp", years_ago)]
        if np.isnan(ltd) and all(np.isnan(x) for x in st):
            return np.nan
        return z(ltd) + z(st[0]) + z(st[1])

    debt = debt_at(0)
    short = [bal("ltd_current"), bal("st_borrow"), bal("cp")]
    equity = bal("equity")
    m["balance_date"] = ref.isoformat() if ref else None
    m["debt"] = debt
    m["short_debt_share"] = _ratio(sum(z(s) for s in short), debt) if debt and debt > 0 else np.nan
    m["debt_to_equity"] = _ratio(debt, equity) if equity and equity > 0 else np.nan
    m["debt_share"] = _ratio(debt, debt + equity) if (not np.isnan(_num(debt)) and equity and equity > 0) else np.nan
    m["current_ratio"] = _ratio(bal("cur_assets"), bal("cur_liab"))
    inv = bal("inventory")
    m["quick_ratio"] = _ratio(bal("cur_assets") - z(inv), bal("cur_liab"))
    m["inventory_growth"] = _growth(inv, bal("inventory", 1))
    rec = bal("receivables")
    m["receivables_growth"] = _growth(rec, bal("receivables", 1))
    m["dso"] = rec / (rev_ttm / 365) if (not np.isnan(rec) and rev_ttm and rev_ttm > 0) else np.nan
    m["equity"] = equity
    m["debt_growth"] = _growth(debt, debt_at(1))

    # 주가·시총이 필요한 지표
    m["per"] = price / eps_ttm if (price and eps_ttm and eps_ttm > 0) else np.nan
    m["fcf_yield"] = _ratio(m["fcf"], mcap)
    m["buyback_yield"] = _ratio(m["buyback"], mcap)
    m["div_yield"] = _ratio(m["dps"], price) if m["dps"] else 0.0
    m["pbr"] = _ratio(mcap, equity) if equity and equity > 0 else np.nan
    m["net_cash_ratio"] = _ratio(cash_total - debt, mcap) if not (np.isnan(_num(cash_total)) or np.isnan(_num(debt))) else np.nan

    g = m["eps_cagr"]
    m["peg"] = m["per"] / (g * 100) if (not np.isnan(m["per"]) and not np.isnan(g) and g > 0) else np.nan
    m["lynch_ratio"] = ((g * 100 + m["div_yield"] * 100) / m["per"]
                        if (not np.isnan(m["per"]) and not np.isnan(g)) else np.nan)

    # 상세 화면 차트용 분기 시계열 (최근 8개)
    m["q_eps"] = [(e.isoformat(), float(v), d) for _, e, v, d in eps_q[-8:]]
    m["q_rev"] = [(e.isoformat(), float(v), d) for _, e, v, d in rev_q[-8:]]
    return m


# ──────────────────────────────────────────────────────────────
# 종목 목록
# ──────────────────────────────────────────────────────────────
def fetch_universe_nasdaq(min_cap=MIN_CAP):
    r = requests.get("https://api.nasdaq.com/api/screener/stocks",
                     params={"tableonly": "true", "limit": "25", "offset": "0", "download": "true"},
                     headers=NASDAQ_HEADERS, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["data"]["rows"])
    df["market_cap"] = pd.to_numeric(df["marketCap"].astype(str).str.replace(r"[,$\s]", "", regex=True),
                                     errors="coerce")
    df["price"] = pd.to_numeric(df["lastsale"].astype(str).str.replace(r"[,$\s]", "", regex=True),
                                errors="coerce")
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df = df[~df["symbol"].str.contains(r"[\^\s]", regex=True)]
    df = df[(df["market_cap"] >= min_cap) & (df["price"] > 0)].copy()
    if df.empty:
        raise ValueError("Nasdaq 종목 목록이 비어 있습니다")
    df["ticker"] = df["symbol"].map(norm_ticker)
    df["name"] = df["name"].map(clean_name)
    df["gics"] = df["sector"].fillna("").map(lambda s: NASDAQ_TO_GICS.get(s, "Unclassified"))
    df["industry"] = df["industry"].fillna("")
    return df[["ticker", "name", "market_cap", "price", "gics", "industry"]].drop_duplicates("ticker")


def fetch_sec_tickers(client):
    js = client.get_json("https://www.sec.gov/files/company_tickers_exchange.json")
    if not js:
        raise RuntimeError("SEC 종목 목록을 받지 못했습니다. SEC_USER_AGENT 설정을 확인해 주세요.")
    df = pd.DataFrame(js["data"], columns=js["fields"])
    df["ticker"] = df["ticker"].map(norm_ticker)
    return df


def fetch_prices_fallback(tickers, progress=_noop):
    import yfinance as yf
    prices = {}
    chunk = 200
    batches = [tickers[i:i + chunk] for i in range(0, len(tickers), chunk)]
    for b, batch in enumerate(batches, start=1):
        try:
            data = yf.download(batch, period="5d", interval="1d", auto_adjust=False,
                               group_by="ticker", threads=True, progress=False)
        except Exception:
            data = None
        if data is not None and not data.empty:
            if isinstance(data.columns, pd.MultiIndex):
                avail = set(data.columns.get_level_values(0))
                for t in batch:
                    if t in avail:
                        c = data[t]["Close"].dropna()
                        if len(c):
                            prices[t] = float(c.iloc[-1])
            else:
                c = data["Close"].dropna()
                if len(c):
                    prices[batch[0]] = float(c.iloc[-1])
        progress(0.80 + 0.1 * b / len(batches), f"대체 경로: 주가 받는 중 {b}/{len(batches)}")
        time.sleep(1)
    return prices


# ──────────────────────────────────────────────────────────────
# 분류·경고 (화면에서 기준을 바꾸면 즉시 다시 계산)
# ──────────────────────────────────────────────────────────────
def lynch_class(row, gdp=GDP_BASE):
    g = row.get("eps_cagr")
    eps = row.get("eps_ttm")
    if eps is None or pd.isna(eps) or eps <= 0 or g is None or pd.isna(g):
        return "분류 불가"
    if g <= gdp:
        return "저성장"
    if g >= FAST_MIN:
        return "고성장"
    return "대형우량" if row.get("market_cap", 0) >= STALWART_CAP else "중간 성장"


def warnings_for(row):
    w = []
    cls = row["class"]
    g = row.get("eps_cagr")
    if not pd.isna(g):
        if g >= HOT_ALERT:
            w.append("과열 경고: 이익 성장률 50% 이상 (일시적 인기 업종일 가능성)")
        elif g > HOT_WARN:
            w.append("과열 주의: 이익 성장률 25% 초과")
    if pd.isna(row.get("eps_ttm")) or row.get("eps_ttm", 0) <= 0:
        w.append("적자 기업: 이익이 구체화될 때까지 기다리는 것이 린치의 원칙")
    if row.get("stage") == "둔화":
        w.append("성장 둔화: 최근 매출 증가율이 꺾이는 중")
    if row.get("nonop_share", 0) > 0.25:
        w.append("영업외 이익 비중 높음: 일회성 이익 여부 확인")
    rg = row.get("rev_ttm_growth")
    if not pd.isna(rg):
        if row.get("receivables_growth", np.nan) > rg + 0.10:
            w.append("매출채권이 매출보다 빠르게 증가")
        if (row.get("inventory_growth", np.nan) > rg + 0.10
                and "auto" not in str(row.get("industry", "")).lower()):
            w.append("재고가 매출보다 빠르게 증가")
    if row.get("debt_share", 0) > 0.5:
        w.append("부채 비중 높음: 린치의 보통 수준(자기자본 75 : 부채 25)의 두 배 이상")
    if row.get("short_debt_share", 0) > 0.5:
        w.append("단기 상환 부채 비중 높음: 위기 때 상환 압박 위험")
    if row.get("cyclical") and not pd.isna(row.get("per_vs_industry")) and row["per_vs_industry"] < 0.8:
        w.append("경기순환주의 낮은 PER: 이익 정점일 수 있음")
    if row.get("dps", 0) > 0 and (row.get("cyclical") or row.get("debt_share", 0) > 0.5):
        w.append("배당 신뢰도 주의: 경기순환주이거나 부채가 많은 회사의 배당")

    # 분류별 매도 신호 (7장)
    if cls == "대형우량" and row.get("per_vs_industry", 0) > 1.25:
        w.append("매도 신호(대형우량): 동종 업종보다 PER이 높음")
    if cls == "고성장" and row.get("peg", 0) > 1.5:
        w.append("매도 신호(고성장): PER이 성장률에 비해 높음")
    if cls == "저성장":
        if row.get("div_yield", 0) < 0.02:
            w.append("매도 신호(저성장): 배당수익률이 낮음")
        if row.get("debt_growth", 0) > 0.5:
            w.append("매도 신호(저성장): 부채 급증으로 자사주 매입 여력 감소")
    if row.get("cyclical"):
        if row.get("capex_growth", 0) >= 1.0:
            w.append("매도 신호(경기순환): 설비투자가 두 배 이상 증가")
    return w


def classify(df, gdp=GDP_BASE):
    out = df.copy()
    out["class"] = [lynch_class(r, gdp) for r in out.to_dict("records")]
    out["warnings"] = [warnings_for(r) for r in out.to_dict("records")]
    out["warning_count"] = out["warnings"].map(len)
    return out


def class_label(row):
    label = row["class"]
    if row.get("cyclical"):
        label += " · 경기순환"
    return label


# ──────────────────────────────────────────────────────────────
# 전체 실행
# ──────────────────────────────────────────────────────────────
def run_scan(progress=_noop, today=None):
    started = time.time()
    client = SecClient(sec_user_agent())

    progress(0.01, "종목 목록을 받는 중")
    sec_tickers = fetch_sec_tickers(client)
    cik_map = dict(zip(sec_tickers["ticker"], sec_tickers["cik"]))
    try:
        uni = fetch_universe_nasdaq(MIN_CAP)
        source = "nasdaq"
    except Exception:
        uni = sec_tickers[sec_tickers["exchange"].isin(["Nasdaq", "NYSE", "NYSE American", "NYSE MKT"])].copy()
        uni["price"] = np.nan
        uni["market_cap"] = np.nan
        uni["gics"] = "Unclassified"
        uni["industry"] = ""
        source = "fallback"
    uni["cik"] = uni["ticker"].map(cik_map)
    uni = uni[uni["cik"].notna()].copy()

    flows, insts = fetch_frames(client, set(uni["cik"].astype(int)), progress, today=today)

    if source == "fallback":
        prices = fetch_prices_fallback(uni["ticker"].tolist(), progress)
        uni["price"] = uni["ticker"].map(prices)
        uni = uni[uni["price"] > 0]

    progress(0.90, "지표 계산 중")
    records = []
    for row in uni.itertuples(index=False):
        if pd.isna(row.cik):
            continue
        cik = int(row.cik)
        mcap = row.market_cap
        if pd.isna(mcap):
            # 대체 경로: 주가 × 희석주식수로 시총 추정
            sh = company_flow(flows, "shares", cik, FLOW_A["shares"][0])[0]
            mcap = row.price * sh[-1][2] if sh else np.nan
            if pd.isna(mcap) or mcap < MIN_CAP:
                continue
        try:
            m = compute_company(cik, flows, insts, row.price, mcap)
        except Exception:
            m = None
        if not m:
            continue
        m.update({"ticker": row.ticker, "name": clean_name(row.name), "cik": cik, "price": row.price,
                  "market_cap": mcap, "gics": row.gics, "industry": row.industry})
        records.append(m)

    df = pd.DataFrame(records)
    if df.empty:
        raise RuntimeError("재무 데이터를 계산한 종목이 없습니다. 잠시 후 다시 시도해 주세요.")

    df["tier"] = df["market_cap"].map(mcap_tier)
    df["gics_ko"] = df["gics"].map(lambda g: GICS_KO.get(g, "미분류"))
    df["cyclical"] = df["industry"].str.lower().map(lambda s: any(k in s for k in CYCLICAL_KEYWORDS))

    # 업종 비교: 업종 종목이 5개 미만이면 섹터 기준
    big_industry = (df["industry"] != "") & (df.groupby("industry")["ticker"].transform("count") >= 5)
    df["group"] = df["industry"].where(big_industry, df["gics"])
    pos_per = df["per"].where(df["per"] > 0)
    df["industry_per"] = pos_per.groupby(df["group"]).transform("median")
    df["per_vs_industry"] = df["per"] / df["industry_per"]
    df["pretax_rank"] = df.groupby("group")["pretax_margin"].rank(pct=True)

    now = dt.datetime.now(ZoneInfo("Asia/Seoul"))
    meta = {
        "time_kst": now.strftime("%Y-%m-%d %H:%M"),
        "timestamp": now.timestamp(),
        "source": source,
        "universe": int(len(uni)),
        "evaluated": int(len(df)),
        "minutes": round((time.time() - started) / 60, 1),
    }
    progress(1.0, "스캔 완료")
    return df, meta


# ──────────────────────────────────────────────────────────────
# 표시용
# ──────────────────────────────────────────────────────────────
def sec_link(cik):
    return f"https://www.sec.gov/edgar/browse/?CIK={int(cik)}"


def zacks_link(ticker):
    return f"https://www.zacks.com/stock/quote/{ticker.replace('-', '.')}"


def tradingview_link(ticker):
    return f"https://www.tradingview.com/chart/?symbol={ticker.replace('-', '.')}"


def to_csv_bytes(df):
    cols = {
        "ticker": "티커", "name": "종목명", "class_label": "린치 분류", "tier": "시총 분류",
        "market_cap": "시가총액($)", "gics_ko": "섹터", "industry": "산업", "price": "주가",
        "per": "PER", "peg": "PEG", "lynch_ratio": "린치 비율", "eps_cagr": "EPS 연평균 성장률",
        "eps_q_growth": "EPS 증가율(최근 분기)", "rev_q_growth": "매출 증가율(최근 분기)",
        "fcf_yield": "FCF 수익률", "buyback_yield": "자사주 매입 수익률", "shares_change": "주식 수 변화",
        "div_yield": "배당수익률", "payout": "배당성향", "div_streak": "연속 배당 증가(년)",
        "gross_profit_margin": "매출총이익률", "op_income_margin": "영업이익률",
        "net_income_margin": "순이익률", "pretax_margin": "세전이익률", "pbr": "PBR",
        "net_cash_ratio": "시총 대비 순현금", "debt_to_equity": "부채비율(부채/자본)",
        "current_ratio": "유동비율", "quick_ratio": "당좌비율", "warning_count": "경고 수",
    }
    out = df[[c for c in cols if c in df.columns]].rename(columns=cols)
    return out.to_csv(index=False).encode("utf-8-sig")
