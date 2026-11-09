#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общие функции крипторобота: часы, журналы, данные биржи, живые цены.

Время робота — UTC: торговый день начинается в 00:00 UTC = 03:00 МСК, рост и падение монеты считаются от цены в это время.
Константа MSK оставлена с прежним именем (её используют все модули), но теперь это UTC — часы торгового дня.
На экране и в журналах время показывается по Москве (UTC+3), а время из настроек («покупки с / до», «выход»)
вписывается по Москве и переводится в UTC (см. robotcore.mid_cfg).

Робот Мосбиржи этот файл не использует — это отдельная программа.
"""
import csv
import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Установите библиотеку: pip install requests")

import exchange as exm

MSK = timezone.utc                       # часы торгового дня робота (UTC), имя оставлено для совместимости
REAL_MSK = timezone(timedelta(hours=3))  # настоящее московское время — для экрана и журналов
SHIFT = 180                              # МСК = UTC + 3 ч, в минутах

ENTRY_MIN = 12 * 60
EXIT_MIN = 23 * 60 + 50       # выход по умолчанию — 23:50 UTC (02:50 МСК), до начала следующего дня
OPEN_MIN = 0                  # начало торгового дня — 00:00 UTC
THRESHOLD = 0.03
STOP = 0.03
COST = 0.0012                 # 0,12% на сделку туда-обратно: комиссия тейкера ~0,05% × 2 + проскальзывание
LATE_OK_MIN = 5
INDEX = "BTCUSDT"             # «индекс» крипторынка — биткоин от начала дня

FALLBACK_TICKERS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT",
                    "SUIUSDT", "LTCUSDT", "TRXUSDT", "DOTUSDT", "BCHUSDT", "NEARUSDT", "APTUSDT", "UNIUSDT", "ARBUSDT",
                    "OPUSDT", "AAVEUSDT"]
BLUE_CHIPS = FALLBACK_TICKERS[:10]

HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
JOURNAL = os.path.join(HERE, "forward_journal.csv")
DAYS_LOG = os.path.join(HERE, "forward_days.csv")
SANDBOX_LOG = os.path.join(HERE, "sandbox_trades.csv")     # сделки робота («Движение дня»)
SPIKE_LOG = os.path.join(HERE, "sandbox_spikes.csv")
MANUAL_LOG = os.path.join(HERE, "manual_trades.csv")       # сделки, открытые вручную
STOP_EVENT = threading.Event()
BACKTEST = {}
DAY_KINDS = {"trading": "торговый день (круглосуточно)", "weekend_session": "выходной (крипта торгуется)",
             "holiday": "—", "weekend": "—"}


def msk(dt=None):
    """Время по Москве для экрана: из UTC-времени робота (с поясом или без)."""
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(REAL_MSK)


def msk_hm(m):
    """Минуты UTC от полуночи → «ЧЧ:ММ» по Москве."""
    m = (int(m) + SHIFT) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


class CertError(RuntimeError):
    """Оставлено для совместимости: криптобиржам российские сертификаты не нужны, эта ошибка не возникает."""
    def __init__(self, host="", port=443):
        super().__init__(f"Ошибка сертификата {host}")
        self.host, self.port = host, port


def purge_ru_certs():
    return []


# ============================================================================ HTTP с запасным путём через второй сервер
RELAY = None          # fn(method, url, kwargs) -> Response | None — ставит пара серверов (pair.py)


class _Session(requests.Session):
    """Если запрос не прошёл (нет связи, таймаут, биржа закрыта для страны сервера) — повторить через второй сервер."""
    def request(self, method, url, **kw):
        try:
            r = super().request(method, url, **kw)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if RELAY is None or isinstance(e, requests.exceptions.SSLError):
                raise
            r2 = _relay(method, url, kw)
            if r2 is None:
                raise
            return r2
        if r.status_code in (403, 451) and RELAY is not None:
            r2 = _relay(method, url, kw)
            if r2 is not None and r2.status_code not in (403, 451):
                return r2
        return r

    def direct(self, method, url, **kw):
        return super().request(method, url, **kw)


def _relay(method, url, kw):
    try:
        return RELAY(method, url, kw)
    except Exception:
        return None


_SESSION = None


def http():
    global _SESSION
    if _SESSION is None:
        _SESSION = _Session()
        _SESSION.headers["User-Agent"] = "crypto-robot/1.0"
    return _SESSION


def reset_http():
    global _SESSION
    _SESSION = None


exm.http = http                  # биржи ходят через ту же сессию (с запасным путём)


# ============================================================================ биржа по настройкам
_EX = {"ex": "bybit", "mode": "demo", "key": "", "secret": ""}
_PUB = {}


def configure_exchange(s):
    """Запомнить выбранную биржу и счёт из настроек (robotcore.load_settings вызывает при каждой загрузке)."""
    ex = s.get("exchange", "bybit") if s.get("exchange") in exm.NAMES else "bybit"
    mode = "real" if s.get("mode") == "real" else "demo"
    _EX.update(ex=ex, mode=mode, key=s.get(f"{ex}_{mode}_key", ""), secret=s.get(f"{ex}_{mode}_secret", ""))


def exchange_name():
    return exm.NAMES[_EX["ex"]]


def EX(ex=None):
    """Публичные данные выбранной биржи (одна копия на биржу — кэш контрактов и тикеров общий)."""
    ex = ex or _EX["ex"]
    if ex not in _PUB:
        _PUB[ex] = exm.Exchange(ex, "demo")
    return _PUB[ex]


def trade_client():
    """Клиент для торговли по выбранной бирже и счёту (с ключами)."""
    return exm.Exchange(_EX["ex"], _EX["mode"], _EX["key"], _EX["secret"])


def tbank_url(ticker, kind="share"):
    """Страница монеты на бирже (имя функции — от робота Мосбиржи)."""
    t = (ticker or "BTCUSDT").upper()
    if _EX["ex"] == "binance":
        return f"https://www.binance.com/ru/futures/{t}"
    return f"https://www.bybit.com/trade/usdt/{t}"


TBANK_TERMINAL = "https://www.bybit.com/trade/usdt/BTCUSDT"


class TInvest:
    """Совместимость с кодом робота Мосбиржи: тот же набор методов, но данные — с выбранной криптобиржи.
    Аргументы (токен, площадка) не нужны и игнорируются."""

    def __init__(self, token=None, env=None, ex=None):
        self.x = EX(ex)

    @staticmethod
    def q(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    def share(self, ticker):
        return self.x.share(ticker)

    def all_shares(self):
        return self.x.top_symbols()

    def candles(self, ticker, day, frm=None, to=None, tf=1):
        frm = frm or datetime(day.year, day.month, day.day, tzinfo=MSK)
        to = to or min(datetime.now(MSK), frm.replace(hour=0, minute=0) + timedelta(days=1))
        return self.x.candles(ticker, frm, to, tf)

    def candles_range(self, ticker, frm, to, tf):
        return self.x.candles(ticker, frm, to, tf)

    def last_prices(self, tickers):
        return self.x.last_prices(tickers)

    def index_last(self, code=INDEX, only_uid=False):
        p = self.x.last_prices([INDEX]).get(INDEX)
        return {"value": p, "time": msk().strftime("%H:%M:%S")} if p else None

    def future_terms(self, ticker):
        return self.share(ticker)["lot"], None


class TradingCalendar:
    """Крипта торгуется круглосуточно и без выходных: каждый день — торговый."""
    def __init__(self, ti=None):
        pass

    def kind(self, d):
        return "trading"

    def day(self, d):
        return {"kind": "trading", "start": "00:00", "end": "23:59"}

    def next_trading(self, d, weekends=False):
        return d + timedelta(days=1)

    def listing(self, n=14):
        return []


# ============================================================================ «индекс»: биткоин от начала дня
_IDX = {"t": 0, "q": None}


def day_open(sym, day=None):
    """Цена монеты в начале торгового дня (открытие первой 5-минутки после 00:00 UTC)."""
    now = datetime.now(MSK)
    d = day or now.date()
    a = datetime(d.year, d.month, d.day, tzinfo=MSK)
    rows = EX().candles(sym, a, min(now, a + timedelta(minutes=30)), 5)
    return rows[0]["o"] if rows else None


def index_quote(code=INDEX):
    """BTC сейчас и изменение от начала дня (00:00 UTC = 03:00 МСК). Кэш 20 с."""
    if time.time() - _IDX["t"] < 20 and _IDX["q"]:
        return _IDX["q"]
    try:
        last = EX().last_prices([INDEX]).get(INDEX)
        key = datetime.now(MSK).date()
        if _IDX.get("day") != key or not _IDX.get("open"):
            _IDX["open"], _IDX["day"] = day_open(INDEX), key
        o = _IDX["open"]
        if not last or not o:
            return _IDX["q"]
        q = {"code": "BTC", "value": last, "chg": round(last - o, 2), "pct": round((last / o - 1) * 100, 2),
             "time": msk().strftime("%H:%M")}
        _IDX.update(t=time.time(), q=q)
        return q
    except Exception:
        return _IDX["q"]


def index_open(code=INDEX, main=True):
    try:
        return day_open(INDEX)
    except Exception:
        return None


def imoex_tickers():
    """«Список индекса» робота Мосбиржи; для крипты — 20 самых торгуемых монет (по ним и BTC считается рынок)."""
    try:
        return EX().top_symbols(20) or list(FALLBACK_TICKERS)
    except Exception:
        return list(FALLBACK_TICKERS)


def index_members(code, fallback=None, min_n=10):
    return list(fallback or FALLBACK_TICKERS)


def iss_get(path, params, retries=4):
    raise RuntimeError("данные Мосбиржи в крипторороботе не используются")


# ============================================================================ журналы
def configure(threshold_pct=2.0, entry="15:00"):
    global THRESHOLD
    THRESHOLD = threshold_pct / 100


def minute_of(ts):
    return ts.hour * 60 + ts.minute


def append_rows(path, cols, rows):
    new = not os.path.exists(path)
    if not new:                                   # в журнал добавились колонки — переписать старый файл с новой шапкой
        with open(path, encoding="utf-8") as f:
            head = f.readline().strip().split(",")
        if head != cols and set(head) <= set(cols):
            with open(path, encoding="utf-8") as f:
                old_rows = list(csv.DictReader(f))
            tmp = path + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in old_rows:
                    w.writerow({k: r.get(k, "") for k in cols})
            os.replace(tmp, path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def journal_day(day, tickers, force=False):
    """Форвард-журнал стратегии «15:00» робота Мосбиржи — в крипторороботе не ведётся."""
    return None


def stats():
    return None


def compute_day(candles, threshold=None, stop=STOP, cost=COST, with_result=True, entry_min=None):
    return [], []


# ============================================================================ живые цены
class LiveFeed:
    """
    Последние цены в реальном времени — общий для сервера и программы на компьютере. Опрос тикеров биржи раз
    в секунду (один запрос сразу по всем монетам). Следит за BTC («индекс»), открытыми позициями робота и монетами,
    которые кто-то смотрит (watch). Каждая цена идёт роботу (robot.on_price — стоп/тейк) и слушателям (listeners).
    get_ti — не используется (оставлено для совместимости).
    """
    POLL = 1.0

    def __init__(self, get_ti=None, get_robot=lambda: None, has_token=lambda: True, always=False):
        self.get_robot, self.always = get_robot, always
        self.px = {}
        self.seq = 0
        self.cond = threading.Condition()
        self.want = {}
        self.clients = 0
        self.listeners = []
        self.mode = "опрос 1 с"
        threading.Thread(target=self._loop, daemon=True, name="live").start()

    def watch(self, tickers, ttl=20):
        t = time.time() + ttl
        for tk in tickers:
            if tk:
                self.want[tk] = max(self.want.get(tk, 0), t)

    def _robot(self):
        try:
            return self.get_robot()
        except Exception:
            return None

    def _targets(self):
        now = time.time()
        tks = {INDEX} | {k for k, t in self.want.items() if t > now}
        r = self._robot()
        if r is not None:
            tks |= {p["ticker"] for p in r.positions}
        return tks

    def _active(self):
        r = self._robot()
        return self.always or self.clients > 0 or bool(r is not None and r.positions)

    def _put(self, tk, price):
        if not price:
            return
        r = self._robot()
        if r is not None and hasattr(r, "on_price"):
            r.on_price(tk, price)
        if self.px.get(tk) == price:
            return
        with self.cond:
            self.px[tk] = price
            self.seq += 1
            self.cond.notify_all()
        for fn in list(self.listeners):
            try:
                fn(tk, price)
            except Exception:
                pass

    def index_now(self):
        q = index_quote()
        v = self.px.get(INDEX)
        if not q or not v:
            return q
        o = q["value"] - q["chg"]
        return {"code": "BTC", "value": v, "chg": round(v - o, 2), "pct": round((v / o - 1) * 100, 2), "live": True}

    def _loop(self):
        while True:
            try:
                if not self._active():
                    time.sleep(0.3)
                    continue
                tks = self._targets()
                lp = EX().last_prices(list(tks))
                for tk, px in lp.items():
                    self._put(tk, px)
                time.sleep(self.POLL)
            except Exception:
                time.sleep(3)

    def snapshot(self, tks):
        return {tk: self.px[tk] for tk in tks if tk in self.px}
