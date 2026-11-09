# -*- coding: utf-8 -*-
"""
Криптобиржи для робота: бессрочные USDT-фьючерсы (контракты) Bybit и Binance, демо-счёт и реальный счёт.

Цены и свечи всегда берутся с основной (публичной) площадки биржи — на демо-счетах Bybit и Binance котировки те же.
Заявки, баланс и позиции — с выбранного счёта (демо или реальный) по ключу API (key + secret).

Время свечей — UTC без пояса: торговый день робота начинается в 00:00 UTC (03:00 МСК).
Объём свечи "v" — в «лотах»: лот = шаг количества монеты на бирже (например, 0.001 BTC). Так робот считает
размер позиции целым числом лотов, как в роботе Мосбиржи; в заявку уходит лоты × шаг.

Ключи API хранятся только в settings.json рядом с программой (на сервере — в папке программы), в репозиторий не попадают.
"""
import hashlib
import hmac
import secrets
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import requests

UTC = timezone.utc

NAMES = {"bybit": "Bybit", "binance": "Binance"}
MODES = {"demo": "демо-счёт", "real": "реальный счёт"}

HOSTS = {
    ("bybit", "public"): ["https://api.bybit.com", "https://api.bytick.com"],
    ("bybit", "real"): ["https://api.bybit.com", "https://api.bytick.com"],
    ("bybit", "demo"): ["https://api-demo.bybit.com"],
    ("binance", "public"): ["https://fapi.binance.com"],
    ("binance", "real"): ["https://fapi.binance.com"],
    ("binance", "demo"): ["https://demo-fapi.binance.com", "https://testnet.binancefuture.com"],
}

TF_BYBIT = {1: "1", 5: "5", 15: "15", 60: "60", 1440: "D"}
TF_BINANCE = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 1440: "1d"}

_session = threading.local()

# Общий на процесс «светофор» по лимитам биржи (лимиты считаются на IP, а клиентов у робота несколько):
# Binance — вес запросов за минуту (2400), заголовок X-MBX-USED-WEIGHT-1M; Bybit — 600 запросов за 5 с.
# При 418/429 биржа просит паузу — тогда все запросы к ней ждут, чтобы не получить бан IP.
_GATE = {"binance": {"pause_until": 0.0, "used": 0, "used_t": 0.0}, "bybit": {"pause_until": 0.0, "used": 0, "used_t": 0.0}}
_GATE_LOCK = threading.Lock()
BINANCE_SOFT_WEIGHT = 1800          # выше этого веса за минуту — притормаживаем до следующей минуты


def _gate_wait(ex):
    g = _GATE[ex]
    while True:
        with _GATE_LOCK:
            now = time.time()
            wait = g["pause_until"] - now
            if wait <= 0 and ex == "binance" and g["used"] >= BINANCE_SOFT_WEIGHT and int(g["used_t"] // 60) == int(now // 60):
                wait = 60 - now % 60 + 0.5          # вес минуты почти исчерпан — ждём новую минуту
        if wait <= 0:
            return
        time.sleep(min(wait, 5))


def _gate_note(ex, r):
    g = _GATE[ex]
    with _GATE_LOCK:
        if ex == "binance":
            w = r.headers.get("X-MBX-USED-WEIGHT-1M") or r.headers.get("x-mbx-used-weight-1m")
            if w:
                try:
                    g["used"], g["used_t"] = int(w), time.time()
                except ValueError:
                    pass
        if r.status_code in (418, 429):
            try:
                ra = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                ra = 0
            g["pause_until"] = max(g["pause_until"], time.time() + max(ra, 10 if r.status_code == 429 else 60))


def http():
    s = getattr(_session, "s", None)
    if s is None:
        s = _session.s = requests.Session()
        s.headers["User-Agent"] = "crypto-robot/1.0"
    return s


class ExchangeError(RuntimeError):
    pass


class ExchangeUnknown(ExchangeError):
    """Запрос на изменение (заявка) ушёл, но ответа нет: исполнена ли — надо проверить по номеру заявки."""


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# Базовый тариф USDT-фьючерсов (тейкер, мейкер), если тариф счёта не получен. Рыночные заявки робота — тейкер.
DEFAULT_FEES = {"bybit": (0.00055, 0.0002), "binance": (0.0005, 0.0002)}

HINTS = {
    10001: "", -4061: " — включите на бирже режим позиций «односторонний» (One-Way), робот работает только в нём",
    10003: " — ключ API неверный или для другого счёта (демо/реальный)", 10004: " — неверный secret ключа API",
    10005: " — у ключа нет прав на торговлю контрактами", 10010: " — IP сервера не разрешён в настройках ключа",
    110007: " — не хватает свободных USDT на счёте", -2019: " — не хватает свободных USDT (маржи) на счёте",
    -2014: " — ключ API неверный", -2015: " — ключ API неверный, нет прав или IP сервера не разрешён",
    110017: " — позиции уже нет на бирже", -2022: " — позиции уже нет на бирже",
}


def _hint(code):
    try:
        return HINTS.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def _ms(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _naive_utc(ms):
    return datetime.fromtimestamp(int(ms) / 1000, UTC).replace(tzinfo=None)


def fmt_qty(qty, step):
    """Количество по шагу биржи строкой без экспоненты (0.001, 12, 0.5)."""
    d = (Decimal(str(qty)) / Decimal(str(step))).to_integral_value(ROUND_DOWN) * Decimal(str(step))
    s = format(d.normalize(), "f")
    return s


class Exchange:
    """Один класс на обе биржи. ex = "bybit" | "binance", mode = "demo" | "real"."""

    def __init__(self, ex="bybit", mode="demo", key="", secret=""):
        if ex not in NAMES:
            raise ValueError(f"неизвестная биржа {ex}")
        self.ex, self.mode = ex, ("real" if mode == "real" else "demo")
        self.key, self.secret = (key or "").strip(), (secret or "").strip()
        self.pub_hosts = list(HOSTS[(ex, "public")])
        self.priv_hosts = list(HOSTS[(ex, self.mode)])
        self._ins = {}            # symbol -> условия контракта
        self._ins_t = 0
        self._tick = {}           # symbol -> тикер за 24 часа
        self._tick_t = 0
        self._px, self._px_t = {}, 0  # Binance: последние цены всех монет
        self._stops = {}              # Binance: symbol -> номер страховочной стоп-заявки
        self._fees = {}               # symbol -> (тейкер, мейкер, время) — тариф счёта
        self.last_fill = {}           # symbol -> {"px", "qty", "fee"} последней заявки (комиссия — фактическая, USDT)
        self._dt = 0              # поправка часов для подписи, мс
        self._lev = {}            # symbol -> установленное плечо
        self._lock = threading.Lock()

    @property
    def title(self):
        return f"{NAMES[self.ex]} — {MODES[self.mode]}"

    # ------------------------------------------------------------------ HTTP
    def _get_pub(self, path, params=None):
        last = None
        for i, base in enumerate(self.pub_hosts):
            for attempt in range(3):
                _gate_wait(self.ex)
                try:
                    r = http().get(base + path, params=params or {}, timeout=(5, 15))
                except requests.RequestException as e:
                    last = ExchangeError(f"нет связи с {NAMES[self.ex]} ({base}): {e}")
                    break
                _gate_note(self.ex, r)
                if r.status_code in (418, 429):
                    last = ExchangeError(f"{NAMES[self.ex]}: слишком много запросов (HTTP {r.status_code}) — пауза по требованию биржи")
                    continue                                   # _gate_wait выдержит паузу, которую попросила биржа
                if r.status_code >= 500:
                    last = ExchangeError(f"{NAMES[self.ex]}: HTTP {r.status_code}")
                    time.sleep(1 + attempt * 2)
                    continue
                if r.status_code in (403, 451):
                    last = ExchangeError(f"{NAMES[self.ex]} не отвечает с этого адреса (HTTP {r.status_code}: доступ из страны сервера закрыт)")
                    break
                js = self._check(r)
                if i:                             # запасной адрес сработал — пусть он будет первым
                    self.pub_hosts.insert(0, self.pub_hosts.pop(i))
                return js
        raise last or ExchangeError("нет связи с биржей")

    def _check(self, r):
        try:
            js = r.json()
        except ValueError:
            raise ExchangeError(f"{NAMES[self.ex]}: HTTP {r.status_code} {r.text[:200]}")
        if self.ex == "bybit":
            if js.get("retCode", 0) != 0:
                raise ExchangeError(f"Bybit: {js.get('retMsg')} (код {js.get('retCode')})" + _hint(js.get("retCode")))
            return js.get("result", {})
        if isinstance(js, dict) and "code" in js and js.get("code", 0) not in (0, 200) and "msg" in js:
            raise ExchangeError(f"Binance: {js.get('msg')} (код {js.get('code')})" + _hint(js.get("code")))
        if r.status_code >= 400:
            raise ExchangeError(f"Binance: HTTP {r.status_code} {r.text[:200]}")
        return js

    def _sync_time(self, base):
        try:
            t0 = time.time()
            if self.ex == "bybit":
                js = http().get(base + "/v5/market/time", timeout=10).json()
                srv = int(js.get("time") or int(js["result"]["timeSecond"]) * 1000)
            else:
                srv = int(http().get(base + "/fapi/v1/time", timeout=10).json()["serverTime"])
            self._dt = srv - int((t0 + time.time()) / 2 * 1000)
        except Exception:
            pass

    def _priv(self, method, path, params=None):
        if not (self.key and self.secret):
            raise ExchangeError(f"не введены ключи API {self.title} — «Настройки» → «Биржа и счёт»")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last = None
        for i, base in enumerate(self.priv_hosts):
            if not self._dt:
                self._sync_time(base)
            for attempt in range(2):
                _gate_wait(self.ex)
                ts = str(int(time.time() * 1000) + self._dt)
                try:
                    if self.ex == "bybit":
                        recv = "10000"
                        if method == "GET":
                            q = urlencode(sorted(params.items()))
                            sign = hmac.new(self.secret.encode(), (ts + self.key + recv + q).encode(), hashlib.sha256).hexdigest()
                            h = {"X-BAPI-API-KEY": self.key, "X-BAPI-SIGN": sign, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv}
                            r = http().get(base + path + ("?" + q if q else ""), headers=h, timeout=20)
                        else:
                            body = json.dumps(params, separators=(",", ":"))
                            sign = hmac.new(self.secret.encode(), (ts + self.key + recv + body).encode(), hashlib.sha256).hexdigest()
                            h = {"X-BAPI-API-KEY": self.key, "X-BAPI-SIGN": sign, "X-BAPI-TIMESTAMP": ts,
                                 "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
                            r = http().post(base + path, data=body, headers=h, timeout=20)
                    else:
                        p = dict(params, timestamp=ts, recvWindow=10000)
                        q = urlencode(p)
                        sig = hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
                        h = {"X-MBX-APIKEY": self.key}
                        r = http().request(method, base + path + "?" + q + "&signature=" + sig, headers=h, timeout=20)
                except requests.RequestException as e:
                    if method != "GET":                  # заявка могла дойти — не повторяем вслепую, решает вызывающий
                        raise ExchangeUnknown(f"нет ответа от {self.title} ({base}): {e}")
                    last = ExchangeError(f"нет связи с {self.title} ({base}): {e}")
                    break
                _gate_note(self.ex, r)
                if r.status_code in (418, 429) or r.status_code >= 500:
                    if method != "GET" and r.status_code >= 500:
                        raise ExchangeUnknown(f"{self.title}: HTTP {r.status_code} — неизвестно, исполнена ли заявка")
                    last = ExchangeError(f"{self.title}: HTTP {r.status_code}")
                    if r.status_code >= 500:
                        time.sleep(2)
                    continue
                try:
                    js = self._check(r)
                except ExchangeError as e:
                    txt = str(e)
                    if ("10002" in txt or "-1021" in txt) and attempt == 0:      # часы разошлись — пересчитать поправку
                        self._sync_time(base)
                        continue
                    if r.status_code == 404 and i + 1 < len(self.priv_hosts):
                        last = e
                        break
                    raise
                if i:
                    self.priv_hosts.insert(0, self.priv_hosts.pop(i))
                return js
        raise last or ExchangeError("нет связи с биржей")

    # ------------------------------------------------------------------ контракты и тикеры
    def instruments(self, force=False):
        """Все бессрочные USDT-контракты, которые сейчас торгуются: symbol -> условия."""
        if self._ins and not force and time.time() - self._ins_t < 6 * 3600:
            return self._ins
        out = {}
        if self.ex == "bybit":
            cursor = ""
            for _ in range(10):
                js = self._get_pub("/v5/market/instruments-info", {"category": "linear", "limit": 1000, "cursor": cursor})
                for it in js.get("list", []):
                    if it.get("quoteCoin") != "USDT" or it.get("status") != "Trading" or it.get("contractType") != "LinearPerpetual":
                        continue
                    lf, pf, lv = it.get("lotSizeFilter", {}), it.get("priceFilter", {}), it.get("leverageFilter", {})
                    out[it["symbol"]] = {"lot": _f(lf.get("qtyStep"), 1), "min_qty": _f(lf.get("minOrderQty")),
                                         "max_mkt": _f(lf.get("maxMktOrderQty")) or None,
                                         "min_notional": _f(lf.get("minNotionalValue"), 5), "tick": _f(pf.get("tickSize")),
                                         "max_lev": _f(lv.get("maxLeverage"), 10), "base": it.get("baseCoin", ""),
                                         "short": True, "kind": "future"}
                cursor = js.get("nextPageCursor") or ""
                if not cursor:
                    break
        else:
            js = self._get_pub("/fapi/v1/exchangeInfo")
            for it in js.get("symbols", []):
                if it.get("quoteAsset") != "USDT" or it.get("status") != "TRADING" or it.get("contractType") != "PERPETUAL":
                    continue
                flt = {f["filterType"]: f for f in it.get("filters", [])}
                lot, mkt = flt.get("LOT_SIZE", {}), flt.get("MARKET_LOT_SIZE", {})
                out[it["symbol"]] = {"lot": _f(mkt.get("stepSize") or lot.get("stepSize"), 1),
                                     "min_qty": max(_f(lot.get("minQty")), _f(mkt.get("minQty"))),
                                     "max_mkt": _f(mkt.get("maxQty")) or None,
                                     "min_notional": _f(flt.get("MIN_NOTIONAL", {}).get("notional"), 5),
                                     "tick": _f(flt.get("PRICE_FILTER", {}).get("tickSize")), "max_lev": 20,
                                     "base": it.get("baseAsset", ""), "short": True, "kind": "future"}
        if out:
            self._ins, self._ins_t = out, time.time()
        return self._ins

    def share(self, sym):
        ins = self.instruments()
        if sym not in ins:
            ins = self.instruments(force=True)
        if sym not in ins:
            raise ExchangeError(f"{sym}: такого USDT-контракта на {NAMES[self.ex]} нет")
        return ins[sym]

    def tickers(self, max_age=20):
        """symbol -> {"last", "turnover" (USDT за 24 ч), "pct24", "open24"} по всем USDT-контрактам."""
        if self._tick and time.time() - self._tick_t < max_age:
            return self._tick
        out = {}
        if self.ex == "bybit":
            js = self._get_pub("/v5/market/tickers", {"category": "linear"})
            for t in js.get("list", []):
                s = t.get("symbol", "")
                if not s.endswith("USDT"):
                    continue
                out[s] = {"last": _f(t.get("lastPrice")), "turnover": _f(t.get("turnover24h")),
                          "pct24": _f(t.get("price24hPcnt")) * 100, "open24": _f(t.get("prevPrice24h"))}
        else:
            js = self._get_pub("/fapi/v1/ticker/24hr")
            for t in js:
                s = t.get("symbol", "")
                if not s.endswith("USDT"):
                    continue
                out[s] = {"last": _f(t.get("lastPrice")), "turnover": _f(t.get("quoteVolume")),
                          "pct24": _f(t.get("priceChangePercent")), "open24": _f(t.get("openPrice"))}
        if out:
            self._tick, self._tick_t = out, time.time()
        return self._tick

    def top_symbols(self, n=None):
        """Монеты по обороту за сутки, от большего к меньшему (n=None — все)."""
        ins = self.instruments()
        tk = self.tickers(max_age=300)
        syms = sorted((s for s in ins if s in tk), key=lambda s: -tk[s]["turnover"])
        return syms[:n] if n else syms

    def last_prices(self, syms):
        """Последние цены. Bybit — тикеры (один запрос на все монеты); Binance — /ticker/price (вес 2 вместо 40)."""
        if self.ex == "bybit":
            tk = self.tickers(max_age=2)
            return {s: tk[s]["last"] for s in syms if s in tk and tk[s]["last"]}
        with self._lock:
            if not self._px or time.time() - self._px_t >= 1.5:
                js = self._get_pub("/fapi/v1/ticker/price")
                px = {x.get("symbol"): _f(x.get("price")) for x in (js if isinstance(js, list) else [js])}
                if px:
                    self._px, self._px_t = px, time.time()
        return {s: self._px[s] for s in syms if self._px.get(s)}

    # ------------------------------------------------------------------ свечи
    def candles(self, sym, frm, to, tf=5):
        """Свечи tf минут за frm..to (datetime с поясом или UTC без пояса). t — начало свечи, UTC без пояса;
        v — объём в лотах (монеты / шаг количества); q — оборот в USDT."""
        step = self.share(sym)["lot"] or 1
        a, b = _ms(frm), _ms(to)
        span = tf * 60_000
        out = {}
        lim = 1000 if self.ex == "bybit" else 1500
        while a < b:
            end = min(b, a + span * lim - 1)
            n = min(lim, (end - a) // span + 2)          # ровно столько свечей, сколько нужно: у Binance вес запроса растёт с limit
            if self.ex == "bybit":
                js = self._get_pub("/v5/market/kline", {"category": "linear", "symbol": sym, "interval": TF_BYBIT[tf],
                                                        "start": a, "end": end, "limit": n})
                rows = [(int(x[0]), x[1], x[2], x[3], x[4], x[5], x[6]) for x in js.get("list", [])]
            else:
                js = self._get_pub("/fapi/v1/klines", {"symbol": sym, "interval": TF_BINANCE[tf], "startTime": a,
                                                       "endTime": end, "limit": n})
                rows = [(int(x[0]), x[1], x[2], x[3], x[4], x[5], x[7]) for x in js]
            for t, o, h, l, c, v, q in rows:
                if a <= t <= end:
                    out[t] = {"t": _naive_utc(t), "o": _f(o), "h": _f(h), "l": _f(l), "c": _f(c),
                              "v": _f(v) / step, "q": _f(q)}
            a = end + 1
        return [out[k] for k in sorted(out)]

    # ------------------------------------------------------------------ счёт
    def equity(self):
        """Стоимость счёта в USDT с учётом открытых позиций (нереализованный результат)."""
        if self.ex == "bybit":
            js = self._priv("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            acc = (js.get("list") or [{}])[0]
            for c in acc.get("coin", []):
                if c.get("coin") == "USDT":
                    return _f(c.get("equity"))
            return _f(acc.get("totalEquity"))
        js = self._priv("GET", "/fapi/v2/account")
        return _f(js.get("totalMarginBalance")) or _f(js.get("totalWalletBalance"))

    def balance_info(self):
        """Для экрана: стоимость, свободно, нереализованный результат (USDT)."""
        if self.ex == "bybit":
            js = self._priv("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            acc = (js.get("list") or [{}])[0]
            return {"equity": _f(acc.get("totalEquity")), "free": _f(acc.get("totalAvailableBalance")),
                    "upnl": _f(acc.get("totalPerpUPL"))}
        js = self._priv("GET", "/fapi/v2/account")
        return {"equity": _f(js.get("totalMarginBalance")), "free": _f(js.get("availableBalance")),
                "upnl": _f(js.get("totalUnrealizedProfit"))}

    def positions(self):
        """symbol -> количество монет (минус — шорт) по открытым позициям на бирже."""
        out = {}
        if self.ex == "bybit":
            cursor = ""
            for _ in range(10):
                js = self._priv("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": 200, "cursor": cursor or None})
                for p in js.get("list", []):
                    q = _f(p.get("size"))
                    if q:
                        out[p["symbol"]] = q if p.get("side") == "Buy" else -q
                cursor = js.get("nextPageCursor")
                if not cursor:
                    break
        else:
            for p in self._priv("GET", "/fapi/v2/positionRisk"):
                q = _f(p.get("positionAmt"))
                if q:
                    out[p["symbol"]] = q
        return out

    def max_leverage(self, sym):
        """Максимальное плечо монеты на бирже (маржинальные условия). Bybit — из списка контрактов (открытые данные);
        Binance — из таблицы плеч счёта (/fapi/v1/leverageBracket, нужен ключ), без ключа — ×20."""
        if self.ex == "bybit":
            return float(self.share(sym).get("max_lev") or 10)
        br = getattr(self, "_brackets", None)
        if (br is None or time.time() - getattr(self, "_brackets_t", 0) > 6 * 3600) and self.key and self.secret:
            try:
                js = self._priv("GET", "/fapi/v1/leverageBracket", {})
                br = {x["symbol"]: max(b_.get("initialLeverage", 1) for b_ in x.get("brackets", [{}]))
                      for x in (js if isinstance(js, list) else [js])}
                self._brackets, self._brackets_t = br, time.time()
            except Exception:
                br = br or {}
                self._brackets, self._brackets_t = br, time.time() - 6 * 3600 + 600   # не получилось — повтор через 10 мин
        return float((br or {}).get(sym) or 20)

    def set_leverage(self, sym, lev):
        lev = max(1, min(int(-(-float(lev) // 1)), int(self.max_leverage(sym))))
        if self._lev.get(sym) == lev:
            return lev
        try:
            if self.ex == "bybit":
                self._priv("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": sym,
                                                                 "buyLeverage": str(lev), "sellLeverage": str(lev)})
            else:
                self._priv("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": lev})
        except ExchangeError as e:
            if "110043" not in str(e):        # Bybit: плечо уже такое
                raise
        self._lev[sym] = lev
        return lev

    def fee_rate(self, sym="BTCUSDT"):
        """Комиссия по тарифу счёта (доли): (тейкер, мейкер). Берётся у биржи по ключу API — с учётом уровня (VIP)
        и скидок счёта; кэш 6 часов. Без ключа — базовый тариф биржи (DEFAULT_FEES), это отмечается в отчётах."""
        c = self._fees.get(sym)
        if c and time.time() - c[2] < 6 * 3600:
            return c[0], c[1]
        tk, mk = DEFAULT_FEES[self.ex]
        if self.key and self.secret:
            try:
                if self.ex == "bybit":
                    lst = self._priv("GET", "/v5/account/fee-rate", {"category": "linear", "symbol": sym}).get("list") or []
                    if lst:
                        tk, mk = _f(lst[0].get("takerFeeRate"), tk), _f(lst[0].get("makerFeeRate"), mk)
                else:
                    js = self._priv("GET", "/fapi/v1/commissionRate", {"symbol": sym})
                    tk, mk = _f(js.get("takerCommissionRate"), tk), _f(js.get("makerCommissionRate"), mk)
                self._fees[sym] = (tk, mk, time.time())
                self.fee_src = "тариф счёта"
            except Exception:
                self.fee_src = "базовый тариф биржи (тариф счёта не получен)"
                self._fees[sym] = (tk, mk, time.time() - 6 * 3600 + 600)    # повтор запроса тарифа — через 10 минут
        else:
            self.fee_src = "базовый тариф биржи (ключ не введён)"
        return tk, mk

    def funding(self, sym, since_ms, until_ms=None):
        """Фандинг по монете на счёте с момента since_ms (фактические начисления биржи), USDT:
        плюс — получили, минус — заплатили. Bybit — журнал операций (SETTLEMENT), Binance — /income FUNDING_FEE."""
        until_ms = until_ms or int(time.time() * 1000) + 60_000
        total = 0.0
        if self.ex == "bybit":
            a = int(since_ms)
            while a < until_ms:                        # журнал Bybit отдаёт не больше 7 дней за запрос
                b = min(until_ms, a + 7 * 86400_000 - 1)
                cursor = None
                for _ in range(20):
                    js = self._priv("GET", "/v5/account/transaction-log", {"accountType": "UNIFIED", "category": "linear",
                                                                         "type": "SETTLEMENT", "startTime": a, "endTime": b,
                                                                         "limit": 50, "cursor": cursor})
                    for x in js.get("list", []):
                        if x.get("symbol") == sym:
                            total -= _f(x.get("funding"))          # у Bybit плюс — списано, минус — получено
                    cursor = js.get("nextPageCursor")
                    if not cursor:
                        break
                a = b + 1
            return total
        js = self._priv("GET", "/fapi/v1/income", {"symbol": sym, "incomeType": "FUNDING_FEE", "startTime": int(since_ms),
                                                  "endTime": int(until_ms), "limit": 1000})
        for x in js or []:
            total += _f(x.get("income"))                          # у Binance плюс — получено, минус — заплачено
        return total

    def funding_history(self, sym, start_ms, end_ms):
        """Ставки фандинга за период (открытые данные): [(время мс, ставка)]. Ставка > 0 — лонг платит шорту."""
        out = []
        if self.ex == "bybit":                         # Bybit отдаёт от конца периода назад — листаем к началу
            e = int(end_ms)
            for _ in range(100):
                js = self._get_pub("/v5/market/funding/history", {"category": "linear", "symbol": sym, "startTime": int(start_ms),
                                                                  "endTime": e, "limit": 200})
                rows = [(int(x["fundingRateTimestamp"]), _f(x.get("fundingRate"))) for x in js.get("list", [])]
                rows = [r for r in rows if int(start_ms) <= r[0] <= e]
                out += rows
                if len(rows) < 200:
                    break
                e = min(r[0] for r in rows) - 1
            return sorted(set(out))
        a = int(start_ms)
        for _ in range(100):
            js = self._get_pub("/fapi/v1/fundingRate", {"symbol": sym, "startTime": a, "endTime": int(end_ms), "limit": 1000})
            rows = sorted((int(x["fundingTime"]), _f(x.get("fundingRate"))) for x in (js or []))
            rows = [r for r in rows if r[0] >= a]
            out += rows
            if len(rows) < 1000:
                break
            a = rows[-1][0] + 1
        return out

    def _binance_fill_fee(self, sym, order_id):
        """Фактическая комиссия исполненной заявки Binance (USDT) — из сделок счёта по номеру заявки."""
        try:
            trs = self._priv("GET", "/fapi/v1/userTrades", {"symbol": sym, "orderId": order_id, "limit": 50})
            fee = 0.0
            for t in trs or []:
                c = _f(t.get("commission"))
                a = t.get("commissionAsset") or "USDT"
                fee += c if a in ("USDT", "USDC") else c * _f(t.get("price"))
            return fee if trs else None
        except ExchangeError:
            return None

    def order(self, sym, qty, side, reduce=False):
        """Рыночная заявка: side > 0 — купить, < 0 — продать. reduce=True — только уменьшение позиции (закрытие).
        У каждой заявки свой номер (clientId): если ответ потерялся, робот спрашивает биржу по этому номеру,
        а не отправляет заявку второй раз. Возвращает среднюю цену исполнения (или None)."""
        ins = self.share(sym)
        qs = fmt_qty(qty, ins["lot"])
        if float(qs) <= 0:
            raise ExchangeError(f"{sym}: количество {qty} меньше шага {ins['lot']}")
        cid = f"cr{int(time.time() * 1000)}{secrets.token_hex(3)}"
        if self.ex == "bybit":
            try:
                self._priv("POST", "/v5/order/create", {"category": "linear", "symbol": sym,
                                                        "side": "Buy" if side > 0 else "Sell", "orderType": "Market",
                                                        "qty": qs, "reduceOnly": bool(reduce), "positionIdx": 0,
                                                        "orderLinkId": cid})
            except ExchangeUnknown:
                pass                                    # ответ потерялся — узнаем по номеру ниже
            for k in range(12):
                time.sleep(0.3 if k < 6 else 1.0)
                try:
                    o = None
                    for path in ("/v5/order/realtime", "/v5/order/history"):
                        lst = (self._priv("GET", path, {"category": "linear", "symbol": sym, "orderLinkId": cid}).get("list") or [])
                        if lst:
                            o = lst[0]
                            break
                except ExchangeError:
                    continue
                if not o:
                    continue
                if o.get("orderStatus") in ("Rejected", "Cancelled", "Deactivated") and not _f(o.get("cumExecQty")):
                    raise ExchangeError(f"Bybit отклонил заявку: {o.get('rejectReason') or o.get('orderStatus')}")
                px = _f(o.get("avgPrice"))
                if px and o.get("orderStatus") in ("Filled", "PartiallyFilledCanceled"):
                    q_ = _f(o.get("cumExecQty")) or float(qs)
                    fee = o.get("cumExecFee")
                    self.last_fill[sym] = {"px": px, "qty": q_, "fee": abs(_f(fee)) if fee not in (None, "") else
                                           px * q_ * self.fee_rate(sym)[0]}
                    return px
            raise ExchangeError("Bybit: заявка не найдена — похоже, не дошла до биржи")
        p = {"symbol": sym, "side": "BUY" if side > 0 else "SELL", "type": "MARKET", "quantity": qs,
             "newOrderRespType": "RESULT", "newClientOrderId": cid}
        if reduce:
            p["reduceOnly"] = "true"
        try:
            js = self._priv("POST", "/fapi/v1/order", p)
        except ExchangeUnknown:
            js = None
            for _ in range(6):
                time.sleep(1.0)
                try:
                    js = self._priv("GET", "/fapi/v1/order", {"symbol": sym, "origClientOrderId": cid})
                    break
                except ExchangeError as e:
                    if "-2013" in str(e):               # такой заявки нет — до биржи не дошла
                        raise ExchangeError("Binance: заявка не дошла до биржи")
            if js is None:
                raise ExchangeError("Binance: нет ответа — состояние заявки неизвестно, проверю позицию")
        if js.get("status") in ("REJECTED", "EXPIRED") and not _f(js.get("executedQty")):
            raise ExchangeError(f"Binance отклонил заявку: {js.get('status')}")
        px = _f(js.get("avgPrice")) or None
        q_ = _f(js.get("executedQty")) or float(qs)
        fee = self._binance_fill_fee(sym, js.get("orderId")) if js.get("orderId") else None
        if fee is None and px:
            fee = px * q_ * self.fee_rate(sym)[0]          # фактическая не получена — по тарифу счёта (тейкер)
        self.last_fill[sym] = {"px": px, "qty": q_, "fee": fee}
        return px

    def set_stop(self, sym, side, qty, stop):
        """Страховочный стоп на бирже на всю позицию (срабатывает, даже если робот или сервер недоступен).
        Bybit — стоп-лосс позиции (/v5/position/trading-stop); Binance — STOP_MARKET closePosition (старый отменяется).
        side — сторона ПОЗИЦИИ (+1 лонг, −1 шорт). stop=None — снять стоп."""
        tick = self.share(sym).get("tick") or 0
        def rnd(x):
            if not tick:
                return f"{x:.8g}"
            d = (Decimal(str(x)) / Decimal(str(tick))).to_integral_value(ROUND_DOWN if side > 0 else "ROUND_UP") * Decimal(str(tick))
            return format(d.normalize(), "f")
        if self.ex == "bybit":
            try:
                self._priv("POST", "/v5/position/trading-stop", {"category": "linear", "symbol": sym, "positionIdx": 0,
                                                                 "tpslMode": "Full", "stopLoss": rnd(stop) if stop else "0",
                                                                 "slTriggerBy": "LastPrice"})
            except ExchangeError as e:
                if "34040" not in str(e):           # «не изменилось» — не ошибка
                    raise
            return
        old = self._stops.pop(sym, None)
        if old:
            try:
                self._priv("DELETE", "/fapi/v1/order", {"symbol": sym, "orderId": old})
            except ExchangeError:
                pass
        if stop:
            js = self._priv("POST", "/fapi/v1/order", {"symbol": sym, "side": "SELL" if side > 0 else "BUY", "type": "STOP_MARKET",
                                                       "stopPrice": rnd(stop), "closePosition": "true", "workingType": "CONTRACT_PRICE",
                                                       "newClientOrderId": f"crs{int(time.time() * 1000)}{secrets.token_hex(2)}"})
            self._stops[sym] = js.get("orderId")

    # ------------------------------------------------------------------ проверка связи
    def check(self):
        """Проверка: публичные данные и (если есть ключи) счёт. Возвращает список строк для журнала."""
        out = []
        t0 = time.time()
        try:
            n = len(self.instruments(force=True))
            tk = self.tickers(max_age=0)
            btc = tk.get("BTCUSDT", {}).get("last")
            btc_s = f"{btc:,.1f}".replace(",", " ") if btc else "—"
            out.append(f"✔ {NAMES[self.ex]}: данные отвечают ({self.pub_hosts[0]}, {int((time.time() - t0) * 1000)} мс) — "
                       f"{n} USDT-контрактов, BTC {btc_s} USDT")
        except Exception as e:
            out.append(f"✘ {NAMES[self.ex]}: данные не получены — {e}")
        if self.key and self.secret:
            try:
                b = self.balance_info()
                eq, fr = (f"{b['equity']:,.2f}".replace(",", " "), f"{b['free']:,.2f}".replace(",", " "))
                out.append(f"✔ {self.title}: ключ подходит, на счёте {eq} USDT, свободно {fr}")
                if self.ex == "binance":
                    try:
                        if self._priv("GET", "/fapi/v1/positionSide/dual").get("dualSidePosition"):
                            out.append(f"✘ {self.title}: включён режим хеджирования — переключите на «односторонний» (One-Way), иначе заявки не пройдут")
                    except ExchangeError:
                        pass
                tkr, mkr = self.fee_rate("BTCUSDT")
                out.append(f"  комиссия ({getattr(self, 'fee_src', 'тариф')}): тейкер {tkr * 100:.4g}%, мейкер {mkr * 100:.4g}% — "
                           "робот входит и выходит рыночными заявками, платит тейкер")
                t1 = time.time()
                self._priv("GET", "/v5/account/wallet-balance" if self.ex == "bybit" else "/fapi/v2/balance",
                           {"accountType": "UNIFIED"} if self.ex == "bybit" else {})
                out.append(f"  скорость ответа счёта: {int((time.time() - t1) * 1000)} мс")
            except Exception as e:
                out.append(f"✘ {self.title}: ключ не подошёл или нет прав на торговлю фьючерсами — {e}")
        else:
            out.append(f"• {self.title}: ключи API не введены — торговля пока невозможна")
        return out


def check_all(settings=None):
    """Проверка связи с обеими биржами (публичные данные) и ключами из настроек."""
    s = settings or {}
    lines = []
    for ex in ("bybit", "binance"):
        mode = s.get("mode", "demo") if s.get("exchange", "bybit") == ex else "demo"
        key, sec = (s.get(f"{ex}_{mode}_key", ""), s.get(f"{ex}_{mode}_secret", ""))
        lines += Exchange(ex, mode, key, sec).check()
    return lines
