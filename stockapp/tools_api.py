"""新世紀類功能的 API。app.py 以 bind() 傳入共用連線後 include_router。"""
from fastapi import APIRouter, HTTPException, Query

from . import formula, indicators, tools

router = APIRouter(prefix="/api")
_ctx = {}


def bind(conn, lock):
    _ctx.update(conn=conn, lock=lock)
    with lock:
        tools.ensure_schema(conn)
        conn.commit()


def _run(fn, *a, **kw):
    with _ctx["lock"]:
        return fn(_ctx["conn"], *a, **kw)


def _floats(s):
    try:
        return [float(x) for x in (s or "").replace("，", ",").split(",") if x.strip()][:20]
    except ValueError:
        raise HTTPException(400, "模擬價格請輸入數字，多天用逗號分隔")


@router.get("/indicators")
def indicator_list():
    return {"indicators": [{"key": k, "title": v[0], "overlay": v[2], "params": v[3]}
                           for k, v in indicators.INDICATORS.items()],
            "formula_help": formula.HELP, "presets": [{"name": n, "expr": e} for n, e in tools.PRESETS]}


@router.get("/chart/{code}")
def chart(code: str, period: str = "D", adjusted: bool = False, ind: str = "MA",
          expr: str = "", sim: str = "", limit: int | None = None):
    names = [x for x in ind.upper().split(",") if x]
    return _run(tools.chart, code, period=period, adjusted=adjusted, sim=_floats(sim),
                names=names, expr=expr.strip() or None, limit=limit)


@router.get("/compare")
def compare(codes: str, limit: int = 240, adjusted: bool = True):
    return _run(tools.compare, [c.strip() for c in codes.split(",") if c.strip()], limit=limit, adjusted=adjusted)


@router.get("/rank")
def rank(kind: str = "gain", date_: str | None = Query(None, alias="date"), top: int = 50,
         market: str | None = None, min_volume: int = 100):
    return _run(tools.rank, kind, until=date_, top=top, market=market or None, min_volume=min_volume)


@router.get("/industries")
def industries(date_: str | None = Query(None, alias="date")):
    return _run(tools.industries, until=date_)


@router.get("/industry")
def industry(name: str, date_: str | None = Query(None, alias="date")):
    return _run(tools.industry_members, name, until=date_)


@router.get("/formula/screen")
def formula_screen(expr: str, date_: str | None = Query(None, alias="date"), market: str | None = None):
    try:
        return _run(tools.formula_screen, expr, until=date_, market=market or None)
    except formula.FormulaError as e:
        raise HTTPException(400, str(e))


@router.get("/calendar")
def calendar(code: str | None = None, days: int = 60):
    return _run(tools.calendar, code=code, days_ahead=days)


@router.get("/financials/{code}")
def financials(code: str):
    return _run(tools.financials, code)


@router.get("/profile/{code}")
def profile(code: str):
    return _run(tools.profile, code)
