# -*- coding: utf-8 -*-
"""Общее для программы на Windows и сервера: настройки, параметры робота, данные для графика."""
import json
import os
from datetime import datetime, timedelta, timezone
_UTC = timezone.utc

import scanner as sc
import autotrader as at

SETTINGS = os.path.join(sc.HERE, "settings.json")
DEFAULTS = {"threshold": 2.0, "entry": "15:00", "capital": 10_000,
            "auto_signals": False, "auto_journal": False, "remember_token": True, "token": "",
            "rescan": False, "autostart": False, "day_on": True, "sound_on": True,
            "spike_on": False, "spike_tfs": [15, 60], "spike_markets": ["top20"], "day_markets": ["top20"], "direction": "both", "leverage": "1",
            "spike_half": 50, "spike_trail": 1.0, "spike_max": 0, "spike_share": 10, "two_takes": True,
            "tg_token": "", "tg_chat": "", "tg_form": True, "tg_trades": True, "tg_imp": False,
            "tg_trend": True, "trend_on": False, "trend_trade": False, "tier_profile": True, "entry_window": "9-19", "exit_late": True, "carry_all": True, "day_carry": 0, "day_mode": "fixed",
            "day_tp": 8, "day_tp_half": 50, "day_trail": 2, "day_act": 0,
            "idx_form": True, "ema10_touch": False, "first_form": True, "trade_5m": False, "ema200": False, "ema200_tfs": ["form"], "idx_day": True, "idx_flip": "off",
            "day_exit": "02:50", "entry_from": "", "day_ref": "main", "trade_weekends": True, "idx_ref": "btc",
            # биржа и счёт: ключи API (key + secret) — по каждой бирже и счёту отдельно; хранятся только в settings.json
            "exchange": "bybit", "mode": "demo",
            "bybit_demo_key": "", "bybit_demo_secret": "", "bybit_real_key": "", "bybit_real_secret": "",
            "binance_demo_key": "", "binance_demo_secret": "", "binance_real_key": "", "binance_real_secret": "",
            # «Движение дня»: время — по Москве (день начинается в 03:00 МСК = 00:00 UTC); суммы — в USDT
            "mid_allday": True, "ex_stop": True, "mid_swing_stop": True, "mid_take_mode": "all", "mid_take_part": 50, "mid_trail": 1.0, "mid_thr": 2.5, "mid_from": "04:00", "mid_to": "02:00", "mid_exit": "02:50", "mid_tf": 60, "mid_tfs": [60], "mid_idx": False, "mid_share": 10, "mid_ref": "0", "mid_multi": True, "mid_carry": "none", "mid_carry_days": 0, "mid_dir": "long", "mid_modes": ["long"], "mid_liq": 1.0, "mid_min_turn": 20, "mid_liq_on": True, "mid_minv_on": False, "mid_vol_on": False, "mid_vol": 5}
SECRET_KEYS = [k for k in DEFAULTS if k.endswith("_key") or k.endswith("_secret")]

CHART_DEPTH = {1: 2, 5: 10, 15: 30, 60: 90, 1440: 400}       # сколько дней истории грузить на график


def load_settings(path=SETTINGS):
    s = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            s.update(json.load(f))
        s.pop("all_hours", None)
    except Exception:
        pass
    sc.configure_exchange(s)
    return s


def save_settings(s, path=SETTINGS, keep_token=None):
    data = dict(s)
    sc.configure_exchange(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, 0o600)                          # в файле ключи API бирж
    except Exception:
        pass
    os.replace(tmp, path)


def window_key(s):
    """Окно входов формации с учётом настройки «время входов от…»: "9-19", "9-19@7" (с 07:00), "9-19@9" (с 09:00), "9-19@10" (с 10:00)."""
    w = s.get("entry_window", "9-19")
    ef = str(s.get("entry_from") or "")
    return f"{w}@{ef}" if ef in ("7", "9", "10") else w


def exit_choice(s):
    """Время выхода формации: True — 23:45, False — ранний выход окна (19:25 / 18:45), или строка "18:45"."""
    v = s.get("exit_late", True)
    return v if isinstance(v, str) and ":" in v else bool(v)


def carry_choice(s):
    """Перенос формации во время выхода: True — все позиции, False — закрыть только в минусе, "none" — закрыть все."""
    v = s.get("carry_all", True)
    return "none" if v == "none" else bool(v)


def spike_cfg(s):
    """Параметры стратегии «всплеск объёма + откат к EMA20» из настроек."""
    return {"on": bool(s.get("spike_on")), "tfs": list(s.get("spike_tfs", [15, 60])),
            "markets": list(s.get("spike_markets", ["top20"])), "day_markets": [m for m in (s.get("day_markets") or []) if m in dict(at.MARKETS)] or ["top20"], "half": int(s.get("spike_half", 50)) / 100,
            "trail_r": float(s.get("spike_trail", 1.0)), "max_pos": int(s.get("spike_max", 0) or 0),
            "share": float(s.get("spike_share", 10)) / 100,
            "leverage": s.get("leverage", "1"),
            "ex_stop": bool(s.get("ex_stop", True)),
            "two_takes": bool(s.get("two_takes", True)),
            "trend_on": bool(s.get("trend_on", True)), "trend_trade": bool(s.get("trend_trade", False)),
            "tier_profile": bool(s.get("tier_profile", True)), "window": window_key(s),
            "entry_from": str(s.get("entry_from") or ""), "day_ref": "market" if s.get("day_ref") == "market" else "main",
            "exit_late": exit_choice(s), "carry_all": carry_choice(s),
            "day_carry": int(s.get("day_carry", 0) or 0), "day_exit": s.get("day_exit", "18:45"),
            "trade_weekends": bool(s.get("trade_weekends", False)),
            "idx_ref": "btc", "day_mode": s.get("day_mode", "fixed"),
            "day_tp": float(s.get("day_tp", 8) or 0) / 100, "day_tp_half": float(s.get("day_tp_half", 50) or 100) / 100,
            "day_trail": float(s.get("day_trail", 2) or 0) / 100, "day_act": float(s.get("day_act", 0) or 0) / 100,
            "index": bool(s.get("idx_form", True)), "ema10_touch": bool(s.get("ema10_touch", False)), "first_form": bool(s.get("first_form", True)), "trade_5m": bool(s.get("trade_5m", False)), "ema200": bool(s.get("ema200", False)), "ema200_tfs": list(s.get("ema200_tfs") or ["form"]), "idx_day": bool(s.get("idx_day", True)),
            "idx_flip": s.get("idx_flip", "off") if s.get("idx_flip") in ("all", "profit") else "off",
            "mid": mid_cfg(s)}


def hhmm(v, default):
    """«10:00» / «10» / «9:30» → минуты от полуночи."""
    try:
        v = str(v).strip().replace(".", ":")
        hh, mm = (v.split(":") + ["0"])[:2]
        m = int(hh) * 60 + int(mm or 0)
        return m if 0 <= m < 24 * 60 else default
    except (ValueError, TypeError):
        return default


def mid_cfg(s):
    """Настройки «Движения дня»: покупка на середине EMA10/EMA20."""
    tfs = [int(x) for x in (s.get("mid_tfs") or [s.get("mid_tf", 60) or 60]) if int(x) in (5, 15, 60, 1440)] or [60]
    u = lambda v, d: (hhmm(v, d) - sc.SHIFT) % 1440        # время из настроек — по Москве, робот считает в UTC
    return {"thr": float(s.get("mid_thr", 2.5) or 2.5) / 100,
            "from": 0 if s.get("mid_allday", True) else u(s.get("mid_from", "04:00"), 240),
            "to": 1440 if s.get("mid_allday", True) else (u(s.get("mid_to", "02:00"), 120) or 1440),
            "allday": bool(s.get("mid_allday", True)),
            "swing_stop": bool(s.get("mid_swing_stop", True)),     # повторный вход — стоп за лоем предыдущего движения
            # тейк на хае дня: «all» — закрыть всё; «half» — закрыть часть, стоп в безубыток, остаток ведёт трейлинг-стоп
            "half": (min(0.95, max(0.05, float(s.get("mid_take_part", 50) or 50) / 100))
                     if s.get("mid_take_mode") == "half" else 0.0),
            "trail": max(0.001, float(s.get("mid_trail", 1.0) or 1.0) / 100),
            "exit": u(s.get("mid_exit", "02:50"), 170), "tfs": sorted(set(tfs), reverse=True),
            "idx": False, "share": float(s.get("mid_share", 10) or 10) / 100,
            "ref": 0, "multi": bool(s.get("mid_multi", True)),
            "carry": s.get("mid_carry") if s.get("mid_carry") in ("all", "profit") else "none",
            "carry_days": int(s.get("mid_carry_days", 0) or 0),
            "modes": mid_modes(s),
            "liq": float(s.get("mid_liq", 1.0) or 0) / 100 if s.get("mid_liq_on", True) else 0.0,       # доля дневного оборота
            "min_turn": float(s.get("mid_min_turn", 0) or 0) * 1e6 if s.get("mid_minv_on", False) else 0.0,  # мин. оборот, USDT в день
            "min_vol": float(s.get("mid_vol", 0) or 0) * 1e6 if s.get("mid_vol_on", False) else 0.0}      # мин. объём сегодня, USDT


MID_MODES = [("idx_long", "BTC растёт — лонг"), ("idx_short", "BTC падает — шорт"),
             ("both", "в обе стороны при любом BTC"), ("long", "только лонг при любом BTC"),
             ("short", "только шорт при любом BTC")]


def mid_modes(s):
    """Режимы торговли (можно несколько): лонг разрешён при long/both или idx_long и растущем BTC; шорт — зеркально."""
    v = s.get("mid_modes")
    if isinstance(v, list):
        out = [x for x in v if x in dict(MID_MODES)]
    else:                                             # старая настройка mid_dir
        d = s.get("mid_dir", "long")
        out = ["idx_long", "idx_short"] if d == "index" else [d] if d in ("long", "short", "both") else ["long"]
    return out or ["long"]


mid_allowed = at.mid_allowed
mid_sides = at.mid_sides


def mid_modes_text(modes):
    return ", ".join(n for k, n in MID_MODES if k in modes)


def chart_sp(s):
    sp = dict(at.SPIKE_DEFAULTS)
    sp.update(two_takes=bool(s.get("two_takes", True)), half=int(s.get("spike_half", 50)) / 100,
              trail_r=float(s.get("spike_trail", 1.0)))
    return sp


def chart_data(token, sandbox, ticker, tf, s, frm=None, to=None):
    """Свечи монеты для графика (время — UTC без пояса; экран сдвигает на московское). Возвращает (свечи, [], None).
    frm / to (секунды UTC) — участок истории, например вокруг сделки из проверки на истории."""
    ti = sc.TInvest()
    now = datetime.now(sc.MSK)
    if frm:
        from datetime import timezone as _tz
        a = datetime.fromtimestamp(int(frm), _tz.utc)
        b = min(datetime.fromtimestamp(int(to), _tz.utc), now) if to else now
        return ti.candles_range(ticker, a, b + timedelta(minutes=1), tf), [], None
    frm = (now - timedelta(days=CHART_DEPTH[tf])).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = ti.candles_range(ticker, frm, now + timedelta(minutes=1), tf)
    return rows, [], None


def bt_window(t, tf):
    """Сделка из проверки на истории → (вход, выход) по Москве без пояса и участок графика (frm, to) в секундах UTC.
    В сделке время UTC: date «ГГГГ-ММ-ДД», time «ЧЧ:ММ», exit_time «ДД.ММ ЧЧ:ММ»."""
    import calendar as _cal
    e = datetime.strptime(t["date"] + " " + t["time"], "%Y-%m-%d %H:%M")
    try:
        x = datetime.strptime(f"{t['exit_time']}.{e.year}", "%d.%m %H:%M.%Y")
        if x < e - timedelta(days=1):                       # выход уже в следующем году
            x = x.replace(year=e.year + 1)
    except (ValueError, KeyError, TypeError):
        x = e
    m = tf if tf < 1440 else 1440
    a = e - timedelta(minutes=m * 260)                      # 200 свечей на EMA200/SMA200 и запас слева
    b = x + timedelta(minutes=m * (80 if tf < 1440 else 20))
    sh = timedelta(minutes=sc.SHIFT)
    return e + sh, x + sh, _cal.timegm(a.timetuple()), _cal.timegm(b.timetuple())


def ema(vals, span):
    k, e, out = 2 / (span + 1), None, []
    for v in vals:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


# ============================================================================ результаты торговли за период
PERIODS = [("day", "День"), ("week", "Неделя"), ("month", "Месяц"), ("quarter", "Квартал"), ("year", "Год"), ("all", "Всё время")]


def _read(path):
    import csv
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


STRAT_NAMES = {"всплеск": "Всплеск + EMA20", "15:00": "Движение дня", "spike": "Всплеск + EMA20", "15": "Движение дня"}


def real_trades(ticker):
    """Настоящие сделки робота по бумаге (закрытые) — для отметок входа и выхода на графике."""
    out = []
    for path, strat in ((sc.SPIKE_LOG, "всплеск"), (sc.SANDBOX_LOG, "15:00"), (sc.MANUAL_LOG, "вручную")):
        for r in _read(path):
            if r.get("ticker") != ticker:
                continue
            out.append({"strat": strat, "side": 1 if r.get("side") == "лонг" else -1, "entry": _num(r.get("entry")),
                        "exit": _num(r.get("exit")), "reason": r.get("reason", ""), "result_pct": _num(r.get("result_pct")),
                        "exit_time": r.get("time", ""), "entry_time": r.get("entry_time", "")})
    return out[-200:]


def strat_summary(rows):
    """Сводка по закрытым сделкам: число, в плюсе, итог USDT (чистый: после комиссии и фандинга), средний %."""
    res = [x for x in (_num(t.get("result_pct")) for t in rows) if x is not None]
    pnl = [x for x in (_num(t.get("pnl_rub")) for t in rows) if x is not None]
    return {"n": len(res), "wins": sum(1 for x in res if x > 0), "pnl_rub": round(sum(pnl), 2) if pnl else None,
            "avg_pct": round(sum(res) / len(res), 3) if res else None, "sum_pct": round(sum(res), 3) if res else None}


def positions_summary(positions):
    """Открытые позиции по стратегиям: число и текущий результат (₽ и %), плюс общий итог."""
    out, tot = {}, {"n": 0, "pnl_rub": 0.0, "known": 0}
    for p in positions:
        k = "всплеск" if p.get("strat") == "spike" else "вручную" if p.get("strat") == "manual" else "15:00"
        d = out.setdefault(k, {"n": 0, "pnl_rub": 0.0, "known": 0, "pct": []})
        d["n"] += 1
        tot["n"] += 1
        last = p.get("last")
        if last and p.get("entry"):
            d["pct"].append(p["side"] * (last / p["entry"] - 1) * 100)
        if p.get("pnl_rub") is not None:
            d["pnl_rub"] += p["pnl_rub"]; d["known"] += 1
            tot["pnl_rub"] += p["pnl_rub"]; tot["known"] += 1
    for d in out.values():
        d["avg_pct"] = round(sum(d["pct"]) / len(d["pct"]), 3) if d["pct"] else None
        d.pop("pct")
    return {"by_strat": out, "total": tot}


def period_start(kind, now):
    d = now.date()
    if kind == "day":
        s = d
    elif kind == "week":
        s = d - timedelta(days=d.weekday())
    elif kind == "month":
        s = d.replace(day=1)
    elif kind == "quarter":
        s = d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1)
    elif kind == "year":
        s = d.replace(month=1, day=1)
    else:
        s = None
    return s


def results(balance_now=None):
    """Баланс и результаты за день / неделю / месяц / квартал / год / всё время.
    Изменение баланса — по стоимости портфеля (пополнения песочницы вычитаются);
    сделки — по журналам закрытых сделок обеих стратегий."""
    now = datetime.now(sc.MSK)
    bal = []
    for r in _read(at.bal_log()):
        v = _num(r.get("value"))
        if v is None:
            continue
        try:
            t = datetime.strptime(r["time"], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        bal.append((t, v, _num(r.get("deposit")) or 0.0))
    bal.sort(key=lambda x: x[0])
    cur = balance_now if balance_now else (bal[-1][1] if bal else None)
    trades = [dict(r, strat="всплеск") for r in _read(sc.SPIKE_LOG)] + [dict(r, strat="15:00") for r in _read(sc.SANDBOX_LOG)] + \
        [dict(r, strat="вручную") for r in _read(sc.MANUAL_LOG)]
    out = []
    for kind, name in PERIODS:
        start = period_start(kind, now)
        row = {"kind": kind, "name": name, "from": start.isoformat() if start else (bal[0][0].date().isoformat() if bal else "")}
        if bal and cur:
            st_dt = datetime.combine(start, datetime.min.time()) if start else bal[0][0]
            before = [b for b in bal if b[0] < st_dt]
            if before:
                base = before[-1][1]
            else:
                first = next((b for b in bal if b[0] >= st_dt), None)
                base = (first[1] - first[2]) if first and first[2] else (first[1] if first else None)
            dep = sum(b[2] for b in bal if b[0] >= st_dt and b[2])
            if base is not None and base + dep > 0:
                ch = cur - base - dep
                row.update(start_balance=round(base if base > 0 else base + dep, 2), change=round(ch, 2), change_pct=round(ch / (base + dep) * 100, 3), deposits=dep)
        tr = [t for t in trades if (not start) or t.get("date", "") >= start.isoformat()]
        res = [_num(t.get("result_pct")) for t in tr]
        res = [x for x in res if x is not None]
        pnl = [_num(t.get("pnl_rub")) for t in tr]
        pnl = [x for x in pnl if x is not None]
        fees = sum(_num(t.get("fee")) or 0 for t in tr)
        fund = sum(_num(t.get("funding")) or 0 for t in tr)
        row.update(trades=len(res), wins=sum(1 for x in res if x > 0), avg_pct=round(sum(res) / len(res), 3) if res else None,
                   pnl_rub=round(sum(pnl), 2) if pnl else None, pnl_known=len(pnl), fees=round(fees, 2), funding=round(fund, 2),
                   by_strat={s: strat_summary([t for t in tr if t["strat"] == s]) for s in ("всплеск", "15:00", "вручную")})
        out.append(row)
    # баланс по дням (последнее значение дня) — для графика
    daily = {}
    for t, v, _ in bal:
        daily[t.date().isoformat()] = v
    if cur:
        daily[now.date().isoformat()] = cur
    return {"balance": cur, "time": now.strftime("%d.%m.%Y %H:%M"), "periods": out,
            "series": [{"date": d, "value": round(v, 2)} for d, v in sorted(daily.items())]}


# ============================================================================ вкладка «Рынки»
MARKET_TYPES = [("future", "USDT-фьючерсы")]


def avg_turnover(days=10):
    return {}


def market_overview(tier_map=None, live_prices=None, known_opens=None):
    """Все USDT-фьючерсы выбранной биржи: цена, изменение от начала дня (03:00 МСК) и за 24 часа, оборот за 24 часа."""
    rows, errors = [], []
    x = sc.EX()
    try:
        tk = x.tickers(max_age=10)
        ins = x.instruments()
        top20 = set(x.top_symbols(20))
        top100 = set(x.top_symbols(100))
        opens = _day_opens(x, list(top100), known_opens)
        for sym, t in tk.items():
            if sym not in ins:
                continue
            o = opens.get(sym)
            rows.append({"type": "future", "ticker": sym, "name": ins[sym].get("base") or sym, "price": t["last"],
                         "prev": o, "pct": (t["last"] / o - 1) * 100 if o and t["last"] else None, "pct24": t["pct24"],
                         "value": t["turnover"], "volume": 0, "avg_value": t["turnover"],
                         "time": sc.msk().strftime("%H:%M"), "live": True,
                         "tier": "index" if sym in top20 else ("2" if sym in top100 else "3"), "blue": sym in top20})
    except Exception as e:
        errors.append(f"{sc.exchange_name()}: {e}")
    sh = [r for r in rows if r.get("pct") is not None]
    summary = {"up": sum(1 for r in sh if r["pct"] > 0), "down": sum(1 for r in sh if r["pct"] < 0),
               "flat": sum(1 for r in sh if r["pct"] == 0), "value": sum(r["value"] for r in rows), "live": True}
    return {"time": sc.msk().strftime("%d.%m.%Y %H:%M:%S"), "rows": rows, "summary": summary,
            "types": MARKET_TYPES, "errors": errors, "currency": "USDT"}


_OPENS = {"day": None, "data": {}}


def _day_opens(x, syms, known=None):
    """Цена в начале дня (00:00 UTC) по монетам, кэш на день. Что уже знает робот — берётся у него, остальное
    качается сразу всё, в несколько потоков. Монета, по которой запрос не прошёл, будет запрошена снова в следующий раз."""
    day = datetime.now(sc.MSK).date()
    if _OPENS["day"] != day:
        _OPENS.update(day=day, data={})
    d = _OPENS["data"]
    for k, v in (known or {}).items():
        if v and k not in d:
            d[k] = v
    a = datetime(day.year, day.month, day.day, tzinfo=sc.MSK)
    todo = [s_ for s_ in syms if s_ not in d]
    got = at._parallel(todo, lambda s_: x.candles(s_, a, a + timedelta(minutes=10), 5))
    for s_, r in got.items():
        if not isinstance(r, Exception):
            d[s_] = r[0]["o"] if r else None
    return d


def _form_short(why):
    w = str(why or "")
    if w.startswith("ВХОД"):
        return "вход"
    if w.startswith("ТРЕНД"):
        return "тренд"
    if w.startswith("жду"):
        return "жду отката"
    if w.startswith("коснулся"):
        return "касание EMA20"
    if "ПОДТВЕРЖД" in w:
        return "подтверждена"
    if "против" in w:
        return "против индекса"
    if w.startswith("отмен") or w.startswith("время вышло"):
        return "отменена"
    return w.split(" — ")[0][:24] or "формация"


def attach_formations(data, spikes):
    """Отметить в «Рынках» бумаги, где сегодня есть всплеск/формация EMA20: ТФ, направление, статус (последний по ТФ)."""
    by = {}
    for row in spikes or []:
        tm, tk, tfn, _v, _m, body, why = row[:7]
        by.setdefault(tk, {})[tfn] = {"tf": tfn, "side": 1 if body > 0 else -1, "status": _form_short(why), "time": tm,
                                      "full": str(why)}
    out = dict(data)
    out["rows"] = [dict(r, forms=list(by[r["ticker"]].values())) if r.get("ticker") in by else r for r in data.get("rows", [])]
    out["forms_n"] = len(by)
    return out


# ============================================================================ сброс: новый демо-счёт и чистая история торговли
def reset_trading(token, capital, log=print, broker="real"):
    """Очистить историю сделок и баланса робота (копия — в папку reset_ГГГГММДД_ЧЧММСС). Счёт на бирже не меняется:
    пополнить демо-счёт можно на сайте биржи. Робот перед этим должен быть остановлен."""
    import glob
    import shutil
    try:
        with open(at.STATE_FILE, encoding="utf-8") as f:
            if json.load(f).get("positions"):
                raise RuntimeError("у робота есть открытые позиции — сначала закройте их")
    except (OSError, ValueError):
        pass
    log("Счёт на бирже не меняется, очищаю историю сделок и баланса робота.")
    capital = float(capital or 0)
    now = datetime.now(sc.MSK)
    arch = os.path.join(sc.HERE, "reset_" + now.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(arch, exist_ok=True)
    files = glob.glob(os.path.join(sc.HERE, "sandbox_trades*.csv")) + [sc.SPIKE_LOG, sc.MANUAL_LOG, at.bal_log(), at.STATE_FILE]
    for p in files:
        if os.path.exists(p):
            shutil.copy2(p, arch)
    # состояние робота: позиции и сделки — с нуля, найденные сегодня всплески/формации — оставить
    try:
        with open(at.STATE_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}
    st = at.AutoTrader._fresh(now.date().isoformat())
    if old.get("date") == st["date"]:
        for k in ("found_spikes", "found_seen", "found_waits", "entered", "done"):   # вход «Движения дня» сегодня не повторять
            if k in old:
                st[k] = old[k]
    with open(at.STATE_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(at.STATE_FILE + ".tmp", at.STATE_FILE)
    for p in files[:-1]:
        if os.path.exists(p):
            os.remove(p)
    try:
        v = sc.trade_client().equity()
        sc.append_rows(at.bal_log(), at.BAL_COLS, [{"time": now.strftime("%Y-%m-%d %H:%M"), "value": round(v, 2), "deposit": round(v, 2)}])
        capital = v
    except Exception as e:
        log(f"Баланс счёта не получен ({e}) — запишется при запуске робота.")
    log(f"История сделок и баланса очищена (копия: {os.path.basename(arch)}). Начинаем с {capital:,.2f} USDT.".replace(",", " "))
    return {"capital": capital, "archive": os.path.basename(arch)}


# ====================================================================== проверка «Движения дня» на истории
import csv as _csv
import threading as _th
import time
from datetime import date as _date

BT_DIR = os.path.join(sc.HERE, "history_bt")          # кэш 5-мин свечей для проверки на истории
BT_COST = 0.0012                                       # запасное значение, если тариф не получен (0,055% × 2 + проскальзывание)
BT_SLIP = 0.0002                                       # проскальзывание рыночных заявок туда-обратно (комиссия — отдельно, по тарифу)


def _bt_read(path):
    out = []
    with open(path, encoding="utf-8") as f:
        r = _csv.reader(f)
        next(r, None)
        for row in r:
            try:
                out.append((datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S"), float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                            float(row[5]) if len(row) > 5 and row[5] else 0.0))
            except (ValueError, IndexError):
                pass
    return out


def _bt_write(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["begin", "open", "high", "low", "close", "value"])
        for r in rows:
            w.writerow([r[0].strftime("%Y-%m-%d %H:%M:%S"), r[1], r[2], r[3], r[4], round(r[5], 2) if len(r) > 5 else 0])
    os.replace(tmp, path)


VISION = "https://data.binance.vision/data/futures/um"


def _vision_zip(url):
    """Архив свечей Binance (data.binance.vision): строки (t UTC, o, h, l, c, оборот USDT). None — архива нет."""
    import io
    import zipfile
    r = sc.http().get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    out = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for name in z.namelist():
            for line in z.read(name).decode("utf-8", "replace").splitlines():
                p_ = line.split(",")
                if len(p_) < 8 or not p_[0].isdigit():
                    continue                              # строка-заголовок
                t = datetime.utcfromtimestamp(int(p_[0]) / 1000)
                out.append((t, float(p_[1]), float(p_[2]), float(p_[3]), float(p_[4]), float(p_[7])))
    return out


def bt_bars(ti, token, ticker, since, log=print, prog=None):
    """5-мин свечи монеты (UTC) с даты since: кэш на диске + архивы Binance по месяцам и дням (data.binance.vision);
    если архива нет (монеты нет на Binance или сайт не отвечает) — свечи API выбранной биржи."""
    os.makedirs(BT_DIR, exist_ok=True)
    path = os.path.join(BT_DIR, f"{ticker}.csv")
    today = datetime.now(sc.MSK).date()
    old = _bt_read(path) if os.path.exists(path) else []
    if old and old[0][0].date() <= since + timedelta(days=2) and old[-1][0].date() >= today - timedelta(days=1):
        return [r for r in old if r[0].date() >= since]
    prog = prog or (lambda txt: None)
    have = {r[0]: r for r in old}
    rows, d = [], _date(since.year, since.month, 1)
    vision_ok = True
    while d <= today and vision_ok:
        nxt = _date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        if nxt <= today - timedelta(days=1):              # прошлые месяцы — месячным архивом
            if sum(1 for k in have if d <= k.date() < nxt) < (nxt - d).days * 280:
                prog(f"архив {d:%m.%Y}")
                try:
                    got = _vision_zip(f"{VISION}/monthly/klines/{ticker}/5m/{ticker}-5m-{d:%Y-%m}.zip")
                except Exception as e:
                    log(f"  {ticker}: архив Binance не ответил ({e}) — беру свечи через API биржи")
                    vision_ok = False
                    break
                for r in got or []:
                    have[r[0]] = r
        else:                                             # текущий месяц — дневными архивами
            dd = d
            while dd < today:
                if sum(1 for k in have if k.date() == dd) < 280:
                    prog(f"архив {dd:%d.%m.%Y}")
                    try:
                        got = _vision_zip(f"{VISION}/daily/klines/{ticker}/5m/{ticker}-5m-{dd:%Y-%m-%d}.zip")
                    except Exception:
                        got = None
                    for r in got or []:
                        have[r[0]] = r
                dd += timedelta(days=1)
        d = nxt
    rows = [have[k] for k in sorted(have)]
    # чего нет в архивах (монеты нет на Binance, сегодняшний день) — свечами API, не дольше 4 минут на монету
    start = max((rows[-1][0] + timedelta(minutes=5)) if rows else datetime(since.year, since.month, since.day), 
                datetime(since.year, since.month, since.day))
    t_end = time.time() + 240
    a_ = start.replace(tzinfo=sc.MSK)
    now = datetime.now(sc.MSK)
    while a_ < now:
        if time.time() > t_end:
            log(f"  {ticker}: история через API докачана не до конца (дольше 4 минут)")
            break
        prog(f"свечи API с {a_:%d.%m.%Y}")
        b_ = min(a_ + timedelta(days=3), now)
        try:
            for r in ti.candles_range(ticker, a_, b_, 5):
                rows.append((r["t"], r["o"], r["h"], r["l"], r["c"], r.get("q") or 0.0))
        except Exception as e:
            if not rows:
                raise
            log(f"  {ticker}: свечи API: {e}")
            break
        a_ = b_
    seen, res = set(), []
    for r in sorted(rows):
        if r[0] not in seen:
            seen.add(r[0]); res.append(r)
    if res:
        _bt_write(path, res)
    return [r for r in res if r[0].date() >= since]


def bt_btc(rows):
    """BTC от начала дня (00:00 UTC) на конец каждой 5-мин свечи: {время конца свечи → изменение}. Для режимов «по BTC»."""
    out, day, op = {}, None, None
    for t, o, h, l, c, *_ in rows:
        if t.date() != day:
            day, op = t.date(), o
        out[t + timedelta(minutes=5)] = c / op - 1
    return out


def _bt_mids(rows, tf):
    """Середина (EMA10+EMA20)/2 по последней закрытой свече ТФ — для каждой 5-мин свечи."""
    a10, a20 = 2 / 11, 2 / 21
    e10 = e20 = None
    out = [None] * len(rows)
    cur_key, cur_close, mid = None, None, None
    for i, (t, o, h, l, c, *_) in enumerate(rows):
        m = t.hour * 60 + t.minute
        key = t.date() if tf >= 1440 else (t.date(), m // tf)
        if key != cur_key:
            if cur_close is not None:                 # предыдущая свеча ТФ закрылась
                e10 = cur_close if e10 is None else e10 + a10 * (cur_close - e10)
                e20 = cur_close if e20 is None else e20 + a20 * (cur_close - e20)
                mid = (e10 + e20) / 2
            cur_key = key
        cur_close = c
        out[i] = mid
    return out


def _bt_trend(rows, tf):
    """EMA200 и SMA200 по закрытым свечам ТФ — для каждой 5-мин свечи (как у живого робота). None — мало истории."""
    a = 2 / 201
    out = [None] * len(rows)
    closes, e = [], None
    cur_key, cur_close, val = None, None, None
    for i, (t, o, h, l, c, *_) in enumerate(rows):
        m = t.hour * 60 + t.minute
        key = t.date() if tf >= 1440 else (t.date(), m // tf)
        if key != cur_key:
            if cur_close is not None:                 # предыдущая свеча ТФ закрылась
                closes.append(cur_close)
                e = cur_close if e is None else e + a * (cur_close - e)
                val = (e, sum(closes[-200:]) / 200) if len(closes) >= 200 else None
            cur_key = key
        cur_close = c
        out[i] = val
    return out


def bt_run(data, m, imoex=None):
    """Проверка «Движения дня» по 5-мин свечам. data: {тикер: [(t,o,h,l,c), …]}. Возвращает список сделок.
    Лонг: бумага выросла на порог → покупка на середине EMA10/EMA20, стоп за лоем дня, тейк на хае.
    Шорт (зеркально): бумага упала на порог → шорт на середине, стоп за хаем дня, тейк на лое."""
    trades = []
    ex = m["exit"]
    can_l, can_s = mid_sides(m.get("modes", ["long"]))
    idx_need = any(x in m.get("modes", []) for x in ("idx_long", "idx_short"))
    for tk, rows in data.items():
        if len(rows) < 100:
            continue
        mids = {tf: _bt_mids(rows, tf) for tf in m["tfs"]}
        trend = {tf: _bt_trend(rows, tf) for tf in m["tfs"]}
        days, start = [], 0
        didx = [0] * len(rows)
        for i in range(1, len(rows) + 1):
            if i == len(rows) or rows[i][0].date() != rows[start][0].date():
                for j in range(start, i):
                    didx[j] = len(days)
                days.append((start, i)); start = i
        dval = [sum(rows[j][5] if len(rows[j]) > 5 else 0 for j in range(a_, z_)) for a_, z_ in days]
        busy = -1                                     # строка, до которой по бумаге открыта (перенесённая) позиция
        for di, (a, z) in enumerate(days):
            prev = [v for v in dval[max(0, di - 10):di] if v > 0]
            liq = sum(prev) / len(prev) if prev else 0.0          # средний дневной оборот за 10 прошлых дней, USDT
            if m.get("min_turn") and liq and liq < m["min_turn"]:
                continue
            tm = [r[0].hour * 60 + r[0].minute for r in rows[a:z]]
            ii = [k for k, x in enumerate(tm) if x >= m["ref"]]
            if not ii or tm[ii[0]] > m["ref"] + 5:
                continue
            op = rows[a + ii[0]][1]
            day_from = a                                  # день — с 00:00 UTC (03:00 МСК)
            prev_in = None                                # строка прошлого входа по монете сегодня
            q, sd = None, 0
            for r in range(a + ii[0], z):
                x = tm[r - a]
                if x < m["from"] - 5:
                    continue
                if x >= m["to"]:
                    break
                mv = rows[r][4] / op - 1
                if mv >= m["thr"] and can_l:
                    q, sd = r, 1
                    break
                if mv <= -m["thr"] and can_s:
                    q, sd = r, -1
                    break
            if q is None:
                continue
            arm = {tf: (mids[tf][q] is not None and (rows[q][4] - mids[tf][q]) * sd > 0) for tf in m["tfs"]}
            r = q + 1
            while r < z and tm[r - a] < m["to"]:
                t, o, h, l, c = rows[r][:5]
                hit = None
                for tf in m["tfs"]:
                    md = mids[tf][r]
                    if md is None:
                        continue
                    if arm[tf] and hit is None and ((l <= md) if sd > 0 else (h >= md)):
                        hit = (tf, md)
                if r <= busy:
                    r += 1
                    continue
                if hit is None or not mid_allowed(m.get("modes", ["long"]), sd, (imoex.get(t) if imoex is not None else None)):
                    for tf in m["tfs"]:
                        md = mids[tf][r]
                        if md is not None and (c - md) * sd > 0:
                            arm[tf] = True
                    r += 1
                    continue
                tf, md = hit
                px = (o if o <= md else md) if sd > 0 else (o if o >= md else md)
                for x in m["tfs"]:
                    arm[x] = False
                if r <= day_from:
                    r += 1; continue
                if m.get("min_vol") and sum(x[5] if len(x) > 5 else 0 for x in rows[day_from:r]) < m["min_vol"]:
                    r += 1; continue                        # объём торгов сегодня к моменту входа меньше заданного
                lo_d = min(x[3] for x in rows[day_from:r]); hi_d = max(x[2] for x in rows[day_from:r])
                if sd > 0:
                    stop, take = lo_d * (1 - at.MID_STOP_BUF), hi_d
                else:
                    stop, take = hi_d * (1 + at.MID_STOP_BUF), lo_d
                if m.get("multi") and m.get("swing_stop") and prev_in is not None and prev_in < r:
                    sw = min(x[3] for x in rows[prev_in:r]) if sd > 0 else max(x[2] for x in rows[prev_in:r])
                    if (px - sw) * sd > 0 and abs(px / sw - 1) >= at.MID_MIN_STOP:   # повторный вход — стоп за лоем предыдущего движения
                        stop = sw * (1 - sd * at.MID_STOP_BUF)
                if not ((px - stop) * sd > 0 and (take - px) * sd > 0):
                    r += 1; continue
                tr_ = trend[tf][r]                         # всегда: лонг выше EMA200 и SMA200, шорт ниже обеих
                if tr_ is None or (px - tr_[0]) * sd <= 0 or (px - tr_[1]) * sd <= 0:
                    r += 1; continue
                ex_day = di if tm[r - a] < ex else di + 1          # вошли после времени выхода — выход на следующий день
                ret = lambda p_: sd * (p_ / px - 1)
                hit_stop = lambda k_: (rows[k_][3] <= stop) if sd > 0 else (rows[k_][2] >= stop)
                hit_take = lambda k_: (rows[k_][2] >= take) if sd > 0 else (rows[k_][3] <= take)
                res, why, xr = None, "", r
                half, trl = m.get("half", 0.0), m.get("trail", 0.01)
                took, part_res, best, took_k = False, 0.0, px, None
                if hit_stop(r):
                    res, why = ret(stop), "стоп"
                else:
                    k = r + 1
                    while k < len(rows):
                        tk_t = rows[k][0]
                        kd = didx[k]
                        if kd > ex_day or (kd == ex_day and tk_t.hour * 60 + tk_t.minute >= ex):
                            last_c = rows[k - 1][4]
                            held = ex_day - (di if tm[r - a] < ex else di + 1)       # сколько раз уже переносили
                            if m.get("carry", "none") != "none" and (m["carry"] == "all" or took or ret(last_c) > 0) \
                                    and (not m.get("carry_days") or held < m["carry_days"]) and k < len(rows) - 1:
                                ex_day += 1                                        # перенос на следующий день
                            else:
                                res, why, xr = ret(last_c), "время" if held == 0 else f"время (+{held} дн.)", k - 1
                                if took:
                                    res, why = part_res + (1 - half) * res, f"тейк {half * 100:.0f}% + " + why
                                break
                        if hit_stop(k):
                            fill = min(rows[k][1], stop) if sd > 0 else max(rows[k][1], stop)
                            res, why, xr = ret(fill), "стоп", k
                            if took:
                                res = part_res + (1 - half) * res
                                why = f"тейк {half * 100:.0f}% + " + ("б/у" if abs(stop - px) < 1e-12 else "трейлинг")
                            break
                        if not took and hit_take(k):
                            fill = max(rows[k][1], take) if sd > 0 else min(rows[k][1], take)
                            if not half:
                                res, why, xr = ret(fill), "тейк", k
                                break
                            took, part_res, stop, best, took_k = True, half * ret(fill), px, fill, k   # часть закрыта, стоп в безубыток
                        if took:                                   # трейлинг-стоп от лучшей цены (после проверки стопа на этой свече)
                            best = max(best, rows[k][2]) if sd > 0 else min(best, rows[k][3])
                            ts = best * (1 - sd * trl)
                            if (ts - stop) * sd > 0:
                                stop = ts
                        k += 1
                    if res is None:
                        res, why, xr = ret(rows[-1][4]), "конец данных", len(rows) - 1
                        if took:
                            res = part_res + (1 - half) * res
                # фандинг: на каждой выплате между входом и выходом лонг платит ставку (шорт получает), если ставка > 0;
                # после частичного тейка — только с оставшейся доли позиции
                fund = 0.0
                fl = (m.get("fund") or {}).get(tk)
                if fl:
                    t_in = int(rows[r][0].replace(tzinfo=_UTC).timestamp() * 1000)
                    t_out = int(rows[xr][0].replace(tzinfo=_UTC).timestamp() * 1000) + 300_000
                    t_tk = int(rows[took_k][0].replace(tzinfo=_UTC).timestamp() * 1000) if took_k is not None else None
                    for ft, rate in fl:
                        if t_in < ft <= t_out:
                            frac = (1 - half) if (t_tk is not None and ft > t_tk) else 1.0
                            fund -= sd * rate * frac
                cost = m.get("cost", BT_COST)
                xp = px * (1 + sd * res)                         # средняя цена выхода (с учётом частичного тейка)
                prev_in = r
                busy = xr
                trades.append({"date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M"), "ticker": tk, "tf": tf, "side": sd,
                               "liq": round(liq), "cap": round(liq * m["liq"]) if m.get("liq") and liq else None,
                               "entry": round(px, 6), "stop": round(stop, 6), "take": round(take, 6), "exit": round(xp, 6),
                               "exit_time": rows[xr][0].strftime("%d.%m %H:%M"), "reason": why,
                               "gross": round(res * 100, 3), "fee_pct": round(cost * 100, 4), "fund_pct": round(fund * 100, 4),
                               "pct": round((res - cost + fund) * 100, 3)})
                if not m["multi"] or why not in ("стоп", "тейк") or xr >= z:
                    break
                r = xr + 1
    trades.sort(key=lambda x: (x["date"], x["time"]))
    return trades


def bt_leverage(trades, setting):
    """Плечо каждой сделки на истории — как у робота: из настроек или максимальное плечо монеты на бирже («auto»),
    но не больше, чем позволяет стоп (ход до стопа — не больше половины хода до ликвидации)."""
    v = str(setting or "1")
    if v == "1":
        return
    mx = {}
    for t in trades:
        tk = t["ticker"]
        if tk not in mx:
            try:
                mx[tk] = max(1.0, sc.EX().max_leverage(tk))
            except Exception:
                mx[tk] = 1.0 if v == "auto" else None
        try:
            lev = mx[tk] if v == "auto" else (min(float(v), mx[tk]) if mx[tk] else float(v))
        except ValueError:
            lev = 1.0
        dist = abs(t["entry"] / t["stop"] - 1) if t.get("stop") else 0
        if dist > 0:
            lev = min(lev, max(1.0, int(0.5 / dist)))
        t["lev"] = lev


def bt_summary(trades, share, capital=1_000_000):
    """Итоги проверки. Каждая сделка — доля share от счёта на начало дня входа (как у робота), счёт растёт со сделками."""
    if not trades:
        return {"n": 0, "capital": capital, "final": capital, "profit_rub": 0, "profit_pct": 0}
    pcts = [t["pct"] for t in trades]
    win = [x for x in pcts if x > 0]
    los = [x for x in pcts if x <= 0]
    by_day = {}
    for t in trades:
        by_day.setdefault(t["date"], []).append(t)
    eq, peak, dd, dd_rub, curve = float(capital), float(capital), 0.0, 0.0, []
    fees_rub, fund_rub = 0.0, 0.0
    months = {}
    for d in sorted(by_day):
        start = eq
        mo = months.setdefault(d[:7], {"m": d[:7], "n": 0, "sum": 0.0, "start": start, "rub": 0.0})
        for t in by_day[d]:
            stake = start * share * t.get("lev", 1)
            if t.get("cap"):
                stake = min(stake, t["cap"])                       # не больше доли дневного оборота бумаги
            t["stake"] = round(stake)
            t["rub"] = round(stake * t["pct"] / 100, 2)
            fees_rub += stake * (t.get("fee_pct") or 0) / 100
            fund_rub += stake * (t.get("fund_pct") or 0) / 100
            eq += t["rub"]
            mo["n"] += 1
            mo["sum"] += t["pct"]
            mo["rub"] += t["rub"]
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
        dd_rub = min(dd_rub, eq - peak)
        curve.append([d, round((eq / capital - 1) * 100, 3), round(eq, 2)])
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {"n": len(trades), "win": round(len(win) / len(trades) * 100, 1), "avg": round(sum(pcts) / len(pcts), 3),
            "sum": round(sum(pcts), 2), "total": round((eq / capital - 1) * 100, 2), "dd": round(dd * 100, 2), "dd_rub": round(dd_rub, 2),
            "capital": capital, "final": round(eq, 2), "profit_rub": round(eq - capital, 2), "profit_pct": round((eq / capital - 1) * 100, 2),
            "pf": round(sum(win) / -sum(los), 2) if los and sum(los) < 0 else None,
            "avg_win": round(sum(win) / len(win), 3) if win else None, "avg_loss": round(sum(los) / len(los), 3) if los else None,
            "reasons": reasons, "curve": curve, "fees": round(fees_rub, 2), "funding": round(fund_rub, 2),
            "months": [{"m": v["m"], "n": v["n"], "sum": round(v["sum"], 2), "rub": round(v["rub"], 2),
                        "eq": round(v["rub"] / v["start"] * 100, 2)} for _, v in sorted(months.items())]}


class Backtest:
    """Фоновая проверка на истории: одна за раз; состояние читают экран программы и телефон.
    На сервере (Linux) считается в отдельном процессе с низким приоритетом — робот и связь с телефоном не тормозят."""
    def __init__(self):
        self._state = {"running": False, "progress": "", "pct": 0, "result": None, "error": None}
        self._stop = _th.Event()
        self._proc = None
        self._sp = os.path.join(BT_DIR, "_state.json")

    @property
    def state(self):
        p = self._proc
        if p is not None:
            try:
                with open(self._sp, encoding="utf-8") as f:
                    st = json.load(f)
            except Exception:
                st = None
            if p.poll() is None:
                if st:
                    st["running"] = True
                    self._state = st
            else:
                self._proc = None
                if st:
                    self._state = st
                self._state["running"] = False
                if not st or st.get("progress") not in ("готово", "ошибка"):
                    self._state.setdefault("error", None)
                    self._state["error"] = self._state.get("error") or "проверка прервалась"
        return self._state

    @state.setter
    def state(self, v):
        self._state = v

    def start(self, token, sandbox, s, months, tickers=None, log=print, capital=None):
        if self.state["running"]:
            return False
        self._stop.clear()
        self.state = {"running": True, "progress": "начинаю…", "pct": 0, "result": None, "error": None,
                      "started": datetime.now(sc.MSK).strftime("%d.%m %H:%M")}
        cap = float(capital or s.get("capital") or 10_000)
        import sys as _sys
        if os.name != "nt" and not getattr(_sys, "frozen", False):
            return self._start_proc(token, sandbox, dict(s), int(months), tickers, log, cap)
        _th.Thread(target=self._run, args=(token, sandbox, dict(s), int(months), tickers, log, cap), daemon=True).start()
        return True

    def _start_proc(self, token, sandbox, s, months, tickers, log, cap):
        import subprocess, sys as _sys
        os.makedirs(BT_DIR, exist_ok=True)
        job = os.path.join(BT_DIR, "_job.json")
        with open(job, "w", encoding="utf-8") as f:
            json.dump({"token": token, "sandbox": sandbox, "s": s, "months": months, "tickers": tickers, "capital": cap,
                       "state": self._sp}, f, ensure_ascii=False)
        try:
            os.chmod(job, 0o600)
        except Exception:
            pass
        with open(self._sp, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False)
        self._proc = subprocess.Popen([_sys.executable, "-c", f"import robotcore as rc; rc._bt_child({job!r})"], cwd=sc.HERE,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                      preexec_fn=_bt_limits)
        pr = self._proc

        def pump():
            for line in pr.stdout:
                line = line.rstrip()
                if line:
                    log(line)
        _th.Thread(target=pump, daemon=True).start()
        return True

    def stop(self):
        self._stop.set()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()

    def reset(self):
        """Сброс: остановить, если идёт, и убрать результат (скачанная история остаётся — следующий прогон быстрый)."""
        self.stop()
        self._proc = None
        self._state = {"running": False, "progress": "", "pct": 0, "result": None, "error": None}
        try:
            os.remove(self._sp)
        except Exception:
            pass

    def _run(self, token, sandbox, s, months, tickers, log, capital=10_000):
        try:
            sc.configure_exchange(s)
            ti = sc.TInvest()
            m = mid_cfg(s)
            dm = list(s.get("day_markets") or ["top20"])
            if not tickers:
                self.state["progress"] = "список монет…"
                n = max([at.MARKET_N.get(x) or 10 ** 6 for x in dm] or [20])
                tickers = sc.EX().top_symbols(None if n >= 10 ** 6 else n) or list(sc.FALLBACK_TICKERS)
            since = (datetime.now(sc.MSK) - timedelta(days=int(months * 30.5))).date()
            btc = None
            if any(x in m["modes"] for x in ("idx_long", "idx_short")):
                self.state["progress"] = "BTC…"
                try:
                    btc = bt_btc(bt_bars(ti, token, sc.INDEX, since, log))
                except Exception as e:
                    log(f"BTC для режима «по BTC» не получен ({e}) — фильтр в проверке не учитывается")
            # комиссия — по тарифу счёта (ключ из настроек), иначе базовый тариф биржи; платим тейкер на входе и выходе
            ex_ = s.get("exchange", "bybit")
            acc = f"{ex_}_{s.get('mode', 'demo')}"
            try:
                import exchange as exm
                xt = exm.Exchange(ex_, s.get("mode", "demo"), s.get(acc + "_key", ""), s.get(acc + "_secret", ""))
                taker = xt.fee_rate("BTCUSDT")[0]
                fee_src = getattr(xt, "fee_src", "тариф")
            except Exception as e:
                taker, fee_src = 0.00055, f"базовый тариф ({e})"
            m["cost"] = 2 * taker + BT_SLIP
            m["fund"] = {}
            fund_fail = 0
            t0_ms = int(datetime(since.year, since.month, since.day, tzinfo=sc.MSK).timestamp() * 1000)
            # по одной монете: скачать → посчитать → забыть свечи (память сервера не растёт с числом монет)
            trades, fails, done_n = [], [], 0
            for n, tk in enumerate(tickers, 1):
                if self._stop.is_set():
                    raise RuntimeError("остановлено")
                self.state.update(progress=f"свечи {tk} ({n}/{len(tickers)})", pct=int(n / len(tickers) * 98))
                pr = lambda txt, tk=tk, n=n: self.state.update(progress=f"свечи {tk} ({n}/{len(tickers)}): {txt}")
                try:
                    rows = bt_bars(ti, token, tk, since, log, pr)
                except Exception as e:
                    fails.append(tk)
                    log(f"  {tk}: история не получена ({e})")
                    continue
                if rows:
                    done_n += 1
                    try:                                  # ставки фандинга монеты за период (открытые данные биржи)
                        m["fund"] = {tk: sc.EX().funding_history(tk, t0_ms, int(time.time() * 1000))}
                    except Exception as e:
                        fund_fail += 1
                        m["fund"] = {}
                        log(f"  {tk}: история фандинга не получена ({e}) — в этой монете фандинг не учтён")
                    trades += bt_run({tk: rows}, m, btc)
                del rows
            trades.sort(key=lambda x: (x["date"], x["time"]))
            for t in trades:                              # время входа — по Москве
                try:
                    tt = datetime.strptime(t["date"] + " " + t["time"], "%Y-%m-%d %H:%M")
                    t["time_msk"] = sc.msk(tt).strftime("%d.%m %H:%M")
                except Exception:
                    pass
            bt_leverage(trades, s.get("leverage", "1"))
            sm = bt_summary(trades, m["share"], capital)
            tfn = {5: "5м", 15: "15м", 60: "1ч", 1440: "1д"}
            self.state["result"] = {
                "summary": sm, "trades": trades[-400:], "tickers": done_n, "fails": fails, "currency": "USDT",
                "period": f"{since:%d.%m.%Y} — {datetime.now(sc.MSK):%d.%m.%Y}",
                "settings": (f"движение {m['thr'] * 100:g}% от начала дня (03:00 МСК), EMA {', '.join(tfn[x] for x in m['tfs'])}, "
                             f"входы {at.hm(m['from'])}–{at.hm(m['to'])} МСК, выход {at.hm(m['exit'])} МСК, "
                             + "режим: " + mid_modes_text(m["modes"]) + ", "
                             f"{'несколько входов' if m['multi'] else 'один вход'} в день"
                             + {"none": ", во время выхода всё закрыть", "all": ", перенос всех позиций", "profit": ", перенос позиций в плюсе"}[m["carry"]]
                             + (f" (до {m['carry_days']} дн.)" if m["carry"] != "none" and m["carry_days"] else "")
                             + f", {m['share'] * 100:g}% счёта на сделку"
                             + (f" × плечо {s.get('leverage')}" if str(s.get("leverage", "1")) not in ("1", "auto") else "")
                             + (f", но не больше {m['liq'] * 100:g}% дневного оборота монеты" if m["liq"] else "")
                             + (f", оборот не меньше {m['min_turn'] / 1e6:g} млн USDT/день" if m["min_turn"] else "")
                             + (f", объём сегодня не меньше {m['min_vol'] / 1e6:g} млн USDT" if m.get("min_vol") else "") + "; "
                             f"монеты: {', '.join(dict(at.MARKETS).get(x, x) for x in dm)}; комиссия {taker * 100:.4g}% × 2 ({fee_src}) "
                             f"+ проскальзывание {BT_SLIP * 100:g}% = {m['cost'] * 100:.3g}% на сделку; фандинг — по фактическим ставкам {sc.exchange_name()}"
                             + (f" (не получен по {fund_fail} монетам)" if fund_fail else "")),
                "done": sc.msk().strftime("%d.%m %H:%M")}
            self.state.update(progress="готово", pct=100)
        except Exception as e:
            self.state.update(error=str(e), progress="ошибка")
        finally:
            self.state["running"] = False


BACKTEST = Backtest()


def _bt_limits():
    """Для процесса проверки: низкий приоритет и не больше 700 МБ памяти — сервер робота не должен задыхаться."""
    os.nice(15)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (700 * 2 ** 20, 700 * 2 ** 20))
    except Exception:
        pass


def _bt_child(job_path):
    """Отдельный процесс проверки: читает задание, считает, раз в секунду пишет состояние в файл."""
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    try:
        os.remove(job_path)                          # там токен — сразу удалить
    except Exception:
        pass
    bt = Backtest()
    bt._state = {"running": True, "progress": "начинаю…", "pct": 0, "result": None, "error": None,
                 "started": datetime.now(sc.MSK).strftime("%d.%m %H:%M")}
    done = _th.Event()

    def writer():
        while True:
            try:
                tmp = job["state"] + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(bt._state, f, ensure_ascii=False)
                os.replace(tmp, job["state"])
            except Exception:
                pass
            if done.is_set():
                return
            done.wait(1)
    w = _th.Thread(target=writer, daemon=True)
    w.start()
    bt._run(job["token"], job["sandbox"], job["s"], job["months"], job.get("tickers"), lambda m: print(m, flush=True), job["capital"])
    done.set()
    w.join(5)
