"""新世紀類功能用到的額外免費資料：除權息、公司產業別、季報財務。

跟 sources.py 一樣抓取與解析分開，解析只吃 JSON，測試用樣本即可。
"""
import re
from datetime import date

from .sources import TPEX_OPENAPI, _field_index, _get, _pick_like, inum, is_common_stock, num, parse_roc_compact, ymd

TWSE_OPENAPI = "https://openapi.twse.com.tw/v1/"


def parse_roc_text(s):
    """'115年10月15日'、'115/10/15'、'1151015' -> date。"""
    m = re.search(r"(\d{2,3})\D+(\d{1,2})\D+(\d{1,2})", str(s or ""))
    if m:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    return parse_roc_compact(s)


# ---------- 除權息結果（還原 K 線用） ----------

def fetch_twse_exrights(start: date, end: date):
    return _get("https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
                {"startDate": ymd(start), "endDate": ymd(end), "response": "json"})


def parse_twse_exrights(js):
    """回傳 [{date, code, prev_close, ref_price, value, kind}]。"""
    if not js or js.get("stat") != "OK":
        return []
    f = js.get("fields") or []
    i_date = _field_index(f, "資料日期")
    i_code = _field_index(f, "股票代號")
    i_prev = _field_index(f, "除權息前收盤價")
    i_ref = _field_index(f, "除權息參考價")
    i_val = _field_index(f, "權值+息值", "權值+息值 ")
    i_kind = _field_index(f, "權/息")
    out = []
    for row in js.get("data") or []:
        code = str(row[i_code]).strip()
        d = parse_roc_text(row[i_date])
        prev, refp = num(row[i_prev]), num(row[i_ref])
        if not is_common_stock(code) or not d or not prev or not refp:
            continue
        out.append({"date": d.isoformat(), "code": code, "prev_close": prev, "ref_price": refp,
                    "value": num(row[i_val]) if i_val is not None else None,
                    "kind": str(row[i_kind]).strip() if i_kind is not None else ""})
    return out


# ---------- 除權息預告（行事曆） ----------

def fetch_twse_exright_plan():
    return _get("https://www.twse.com.tw/rwd/zh/exRight/TWT48U", {"response": "json"})


def fetch_twse_exright_plan_openapi():
    return _get(TWSE_OPENAPI + "exchangeReport/TWT48U_ALL")


def _field_like(fields, *needles):
    """欄位名稱包含關鍵字即可（證交所欄位常帶單位或空白，例如「現金股利(元/股)」）。"""
    for i, f in enumerate(fields):
        f2 = re.sub(r"\s|<[^>]+>", "", f)
        if any(n in f2 for n in needles):
            return i
    return None


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else None


def parse_twse_exright_plan(js):
    """除權除息預告表。現金股利空白表示公司尚未公告金額，保留為 None 而不是 0。"""
    if not js or js.get("stat") != "OK":
        return []
    f = js.get("fields") or []
    i_date = _field_like(f, "除權除息日期", "日期")
    i_code = _field_like(f, "股票代號", "代號")
    i_name = _field_like(f, "名稱")
    i_kind = _field_index(f, "除權息")
    i_kind = i_kind if i_kind is not None else _field_like(f, "權/息", "權息")
    i_stock = _field_like(f, "無償配股")
    i_cap = _field_like(f, "現金增資配股")
    i_sub = _field_like(f, "認購價")
    i_cash = _field_like(f, "現金股利")
    out = []
    for row in js.get("data") or []:
        code = str(_cell(row, i_code) or "").strip()
        d = parse_roc_text(_cell(row, i_date))
        if not d or not code:
            continue
        out.append({"date": d.isoformat(), "code": code, "name": str(_cell(row, i_name) or "").strip(),
                    "kind": re.sub(r"<[^>]+>", "", str(_cell(row, i_kind) or "")).strip(),
                    "stock_ratio": num(_cell(row, i_stock)), "cash_div": num(_cell(row, i_cash)),
                    "cash_capital_ratio": num(_cell(row, i_cap)), "subscription_price": num(_cell(row, i_sub)),
                    "market": "tse"})
    return out


def parse_twse_exright_plan_openapi(rows):
    """OpenAPI 版預告表（英文欄位），用來補 rwd 版空白的金額。"""
    out = []
    for r in rows or []:
        code = str(r.get("Code") or "").strip()
        d = parse_roc_text(r.get("Date"))
        if not code or not d:
            continue
        out.append({"date": d.isoformat(), "code": code, "name": str(r.get("Name") or "").strip(),
                    "kind": str(_pick_like(r, "exdividend") or r.get("Exdividend") or "").strip(),
                    "stock_ratio": num(_pick_like(r, "stockdividend")),
                    "cash_div": num(_pick_like(r, "cashdividend")),
                    "cash_capital_ratio": num(_pick_like(r, "cashcapital")),
                    "subscription_price": num(_pick_like(r, "subscriptionprice")), "market": "tse"})
    return out


def merge_exright_plan(primary, extra):
    """以 (日期, 代號) 對齊，rwd 版空白的欄位用 OpenAPI 版補上；rwd 沒有的列也加入。"""
    idx = {(r["date"], r["code"]): r for r in primary}
    for r in extra:
        p = idx.get((r["date"], r["code"]))
        if p is None:
            primary.append(r)
            idx[(r["date"], r["code"])] = r
            continue
        for k, v in r.items():
            if p.get(k) in (None, "") and v not in (None, ""):
                p[k] = v
    return primary


# ---------- 公司基本資料（產業別） ----------

INDUSTRY = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
    "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "13": "電子工業",
    "14": "建材營造", "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨", "19": "綜合",
    "20": "其他", "21": "化學工業", "22": "生技醫療", "23": "油電燃氣", "24": "半導體業", "25": "電腦及週邊設備",
    "26": "光電業", "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務", "31": "其他電子",
    "32": "文化創意", "33": "農業科技", "34": "電子商務", "35": "綠能環保", "36": "數位雲端", "37": "運動休閒",
    "38": "居家生活", "80": "管理股票", "91": "存託憑證",
}


def fetch_company_info():
    """上市、上櫃公司基本資料，個別失敗不影響另一個。"""
    rows = []
    for url, market in [(TWSE_OPENAPI + "opendata/t187ap03_L", "tse"), (TPEX_OPENAPI + "mopsfin_t187ap03_O", "otc")]:
        try:
            rows += [dict(r, _market=market) for r in _get(url)]
        except RuntimeError as e:
            print("[公司資料]", e)
    return rows


def parse_company_info(rows):
    out = []
    for r in rows or []:
        code = str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()
        if not is_common_stock(code):
            continue
        ind = str(r.get("產業別") or r.get("SecuritiesIndustryCode") or "").strip()
        ind = INDUSTRY.get(ind.zfill(2) if ind.isdigit() else ind, ind or "其他")
        out.append({"code": code, "name": str(r.get("公司簡稱") or r.get("CompanyAbbreviation") or "").strip(),
                    "industry": ind, "market": r.get("_market")})
    return out


# ---------- 季報：綜合損益表、資產負債表（OpenAPI 只給最新一季，需每季收集累積） ----------

FIN_KINDS = ["ci", "basi", "bd", "fh", "ins", "mim"]  # 一般業、銀行、證券、金控、保險、異業


def fetch_financials():
    income, balance = [], []
    for kind in FIN_KINDS:
        for base in [TWSE_OPENAPI + "opendata/t187ap06_L_", TPEX_OPENAPI + "mopsfin_t187ap06_O_"]:
            try:
                income += _get(base + kind)
            except RuntimeError as e:
                print("[損益表]", e)
        for base in [TWSE_OPENAPI + "opendata/t187ap07_L_", TPEX_OPENAPI + "mopsfin_t187ap07_O_"]:
            try:
                balance += _get(base + kind)
            except RuntimeError as e:
                print("[資產負債表]", e)
    return income, balance


def _find(r, *needles, exclude=()):
    for k, v in r.items():
        if all(n in k for n in needles) and not any(x in k for x in exclude):
            return num(v)
    return None


def _first(*vals):
    return next((v for v in vals if v is not None), None)


def parse_financials(income_rows, balance_rows):
    """合併成 {(code, year, quarter): {...}}，金額單位為千元（同公開資訊觀測站）。"""
    out = {}

    def key(r):
        code = str(r.get("公司代號") or "").strip()
        y, q = inum(r.get("年度")), inum(r.get("季別"))
        if not is_common_stock(code) or not y or not q:
            return None
        return code, y + 1911 if y < 1911 else y, q

    for r in income_rows or []:
        k = key(r)
        if not k:
            continue
        out.setdefault(k, {}).update({
            "revenue": _first(_find(r, "營業收入"), _find(r, "收益", exclude=("其他", "營業外"))),
            "gross_profit": _find(r, "營業毛利", exclude=("已實現", "未實現")),
            "op_income": _find(r, "營業利益"),
            "pretax_income": _find(r, "稅前"),
            "net_income": _first(_find(r, "本期淨利", exclude=("歸屬", "非控制")), _find(r, "本期稅後淨利")),
            "eps": _find(r, "基本每股盈餘"),
        })
    for r in balance_rows or []:
        k = key(r)
        if not k:
            continue
        out.setdefault(k, {}).update({
            "assets": _first(_find(r, "資產總"), _find(r, "資產合計")),
            "liabilities": _first(_find(r, "負債總"), _find(r, "負債合計")),
            "equity": _first(_find(r, "權益總", exclude=("歸屬", "非控制")), _find(r, "權益合計")),
            "bvps": _find(r, "每股參考淨值"),
        })
    return [{"code": c, "year": y, "quarter": q, **v} for (c, y, q), v in out.items()]
