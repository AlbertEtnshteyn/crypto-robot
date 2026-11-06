# -*- coding: utf-8 -*-
"""
Сервер крипторобота (Bybit / Binance, USDT-фьючерсы): тот же робот, что в программе на Windows, но без окна —
для круглосуточной работы на VPS. Управление — с телефона (приложение для Андроида) или из браузера: https://адрес:8444
Работает рядом с роботом Мосбиржи на тех же серверах: своя папка (/opt/cryptorobot), своя служба и свой порт.

Защита: соединение шифруется собственным сертификатом сервера (создаётся при первом запуске и лежит
только в папке программы — в систему ничего не ставится); телефон узнаёт сервер по отпечатку
этого сертификата из кода подключения. Каждый запрос подписан ключом доступа из того же кода.

Запуск:  python3 server.py [--port 8444] [--host адрес-для-кода-подключения]
"""
import argparse
import base64
import calendar
import collections
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import ssl
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import scanner as sc
import autotrader as at
import robotcore as rc
import tgnotify as tg
try:
    import pair as pairmod                       # пара серверов (появляется со второго обновления — старый сервер его не копировал)
except ImportError:
    pairmod = None

HERE = sc.HERE
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SERVER_CFG = os.path.join(HERE, "server.json")
CERT = os.path.join(HERE, "server_cert.pem")
KEY = os.path.join(HERE, "server_key.pem")


def app_version():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")
    try:
        return open(p, encoding="utf-8").read().strip()
    except Exception:
        return "dev"


VERSION = app_version()


def now_msk():
    return sc.msk()                      # настоящее московское время — для журнала и телефона


# ============================================================================ журнал действий
class LogBuf:
    """Последние строки журнала действий (для телефона) + вывод в консоль/systemd."""
    def __init__(self, n=3000):
        self.lines = collections.deque(maxlen=n)
        self.seq = 0
        self.lock = threading.Lock()
        self._part = ""

    def add(self, text):
        with self.lock:
            for ln in str(text).rstrip("\n").split("\n"):
                self.seq += 1
                self.lines.append((self.seq, ln))

    def since(self, n):
        with self.lock:
            return [x for x in self.lines if x[0] > n][-800:], self.seq


LOG = LogBuf()


class Tee:
    def __init__(self, orig):
        self.orig = orig
        self.buf = ""

    def write(self, s):
        try:
            self.orig.write(s)
        except Exception:
            pass
        self.buf += s
        while "\n" in self.buf:
            ln, self.buf = self.buf.split("\n", 1)
            LOG.add(ln)

    def flush(self):
        try:
            self.orig.flush()
        except Exception:
            pass


def log(msg):
    print(f"[{now_msk():%d.%m %H:%M:%S}] {msg}", flush=True)


# ============================================================================ робот
class Robot:
    def __init__(self):
        self.s = rc.load_settings()
        self.robot = None
        self.thread = None
        self.resetting = False
        self.snap = {}
        self.events = collections.deque(maxlen=200)       # для звука/уведомлений на телефоне
        self.ev_seq = 0
        self.lock = threading.Lock()
        self.done = {}
        self.busy = None
        self.index = None
        self._apply()
        threading.Thread(target=self._index_loop, daemon=True, name="index").start()

    def _index_loop(self):
        """BTC от начала дня (03:00 МСК) для телефона — раз в 5 секунд."""
        while True:
            try:
                q = sc.index_quote()
                if q:
                    self.index = dict(q, live=True)
            except Exception:
                pass
            time.sleep(5)

    # --- настройки
    def _apply(self):
        sc.configure_exchange(self.s)

    def save(self, new):
        allowed = set(rc.DEFAULTS) | {"spike_share"}
        for k, v in new.items():
            if k in (rc.SECRET_KEYS + ["tg_token"]) and not str(v or "").strip():
                continue                          # пустое поле ключа — «не менять»
            if k in allowed:
                self.s[k] = str(v).strip() if k in rc.SECRET_KEYS else v
        rc.save_settings(self.s, keep_token=True)
        self._apply()
        log("Настройки сохранены с телефона.")
        self.restart()

    def public_settings(self):
        """Настройки для телефона и программы: ключи API и токен Telegram не отдаются — только «сохранён» и первые/последние символы."""
        d = dict(self.s)
        for k in rc.SECRET_KEYS:
            v = d.pop(k, "") or ""
            d[k + "_set"] = bool(v)
            d[k + "_hint"] = (v[:4] + "…" + v[-4:]) if len(v) > 12 else ""
        tgt = d.get("tg_token", "")
        d["tg_token"] = ""
        d["tg_token_set"] = bool(tgt)
        d["brokers"] = list(at.BROKER_NAMES.items())
        d["markets_all"] = at.MARKETS
        d["mid_modes_all"] = rc.MID_MODES
        d["token_set"] = self.has_token()
        return d

    # --- работа робота
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def has_token(self):
        """Введены ли ключи API выбранной биржи и счёта."""
        ex, mode = self.s.get("exchange", "bybit"), self.s.get("mode", "demo")
        return bool(self.s.get(f"{ex}_{mode}_key") and self.s.get(f"{ex}_{mode}_secret"))

    def start(self, trade):
        if PAIR and PAIR.enabled and not PAIR.active:
            return False                                 # резервный сервер: торгует второй
        with self.lock:
            if self.running():
                if trade and not self.robot.trade:
                    self.robot.set_trade(True)
                    log("Торговля включена.")
                return True
            if trade and not self.has_token():
                log("Для торговли нужны ключи API биржи — введите их в настройках («Биржа и счёт»). Пока робот только наблюдает.")
                trade = False
            self._apply()
            rlog = lambda m: print(f"[робот {now_msk():%H:%M:%S}] {m}", flush=True)
            broker = at.make_broker(self.s, log=rlog)
            self.robot = at.AutoTrader(broker, float(self.s.get("capital") or 10000), log=rlog, rescan=bool(self.s.get("rescan")),
                                       spike=rc.spike_cfg(self.s), day_on=bool(self.s.get("day_on", True)),
                                       direction=self.s.get("direction", "both"), trade=trade,
                                       on_event=self.on_event, on_update=self.on_update)
            robot = self.robot

            def run():
                try:
                    robot.run()
                except sc.CertError as e:
                    rlog(f"Ошибка защищённого соединения: {e}. Робот остановлен, повтор через минуту.")
                except BaseException:
                    rlog("Сбой робота:\n" + traceback.format_exc())
            self.thread = threading.Thread(target=run, daemon=True, name="robot")
            self.thread.start()
            self.s["autostart"] = bool(trade)            # после перезагрузки сервера — в том же режиме
            rc.save_settings(self.s, keep_token=True)
            return True

    def pair_activate(self, peer_trade):
        """Второй сервер замолчал — продолжаем с его позициями и настройками (они уже синхронизированы)."""
        self.s = rc.load_settings()
        self._apply()
        trade = bool(peer_trade) if peer_trade is not None else bool(self.s.get("autostart"))
        self.start(trade)
        try:
            tg.send(self.s.get("tg_token"), self.s.get("tg_chat"),
                    "⚠️ Крипторобот: основной сервер не отвечает — работу продолжил резервный сервер.", lambda m: print(m))
        except Exception:
            pass

    def pair_deactivate(self):
        if self.running():
            self.robot.request_stop(False)               # позиции не закрываем — их ведёт второй сервер

    def stop_trade(self, close):
        if not self.running():
            return
        if close and self.robot.positions:
            self.robot.request_close(None)
        self.robot.set_trade(False)
        self.s["autostart"] = False
        rc.save_settings(self.s, keep_token=True)
        log("Торговля выключена" + (", открытые позиции закрываются по рынку." if close else
                                     "; открытые позиции робот доведёт до стопа/тейка/выхода."))

    def restart(self):
        def job():
            trade = bool(self.robot.trade) if self.running() else bool(self.s.get("autostart"))
            if self.running():
                self.robot.request_stop(False)
                for _ in range(240):
                    if not self.running():
                        break
                    time.sleep(0.5)
            self.start(trade)
        threading.Thread(target=job, daemon=True).start()

    def reset(self):
        """Сброс: новый демо-счёт на сумму капитала, история сделок и баланса — с нуля. Робот перезапускается в том же режиме."""
        if self.resetting:
            return "Сброс уже идёт."
        if self.running() and self.robot.positions:
            return "Сначала закройте открытые позиции, потом сбрасывайте историю."
        self.resetting = True

        def job():
            try:
                was = self.running()
                trade = bool(self.robot.trade) if was else bool(self.s.get("autostart"))
                if was:
                    log("Сброс: останавливаю робота…")
                    self.robot.request_stop(False)
                    for _ in range(240):
                        if not self.running():
                            break
                        time.sleep(0.5)
                self.snap = None
                try:
                    rc.reset_trading(None, float(self.s.get("capital") or 0), log=log)
                finally:
                    if was:
                        self.start(trade)
                tg.send(self.s.get("tg_token"), self.s.get("tg_chat"),
                        "🔄 Крипторобот: история сделок и баланса сброшена.",
                        lambda m: print(m))
            except Exception as e:
                log(f"Сброс не удался: {e}")
            finally:
                self.resetting = False
        threading.Thread(target=job, daemon=True).start()
        return None

    def on_update(self, snap):
        self.snap = snap

    def on_event(self, kind, info):
        with self.lock:
            self.ev_seq += 1
            self.events.append({"id": self.ev_seq, "kind": kind, "time": now_msk().strftime("%H:%M:%S"),
                                "ticker": info.get("ticker"), "text": self._ev_text(kind, info)})
        tok, chat = self.s.get("tg_token"), self.s.get("tg_chat")
        tglog = lambda m: print(m, flush=True)
        try:
            if kind == "formation":
                if self.s.get("tg_trend" if info.get("trend") else "tg_form", True):
                    tg.send(tok, chat, tg.formation_text(info), tglog)
            elif kind == "impulse":
                if self.s.get("tg_imp", False):
                    tg.send(tok, chat, tg.impulse_text(info), tglog)
            elif self.s.get("tg_trades", True):
                tg.send(tok, chat, tg.trade_text(kind, info), tglog)
        except Exception as e:
            print("Telegram:", e, flush=True)

    @staticmethod
    def _ev_text(kind, info):
        try:
            if kind in ("open", "close", "carry"):
                return tg.trade_text(kind, info)
            if kind == "formation":
                return (f"📐 Формация {info.get('ticker', '')} {info.get('tf', '')} {'лонг' if info.get('side', 1) > 0 else 'шорт'}"
                        + (f" — {info['decision']}" if info.get("decision") else ""))
            if kind == "impulse":
                return tg.impulse_text(info)
        except Exception:
            pass
        return f"{kind} {info.get('ticker', '')}"

    # --- присмотр: робот всегда должен работать; журнал «движения дня» в 17:20
    def watchdog(self):
        last_try = 0
        while True:
            try:
                if PAIR and PAIR.enabled and not PAIR.active:
                    time.sleep(20)
                    continue
                if not self.running() and time.time() - last_try > 60:
                    last_try = time.time()
                    self.start(bool(self.s.get("autostart")))
            except Exception:
                traceback.print_exc()
            time.sleep(20)

    def _journal(self, d):
        try:
            sc.journal_day(d, sc.imoex_tickers())
            sc.stats()
        except Exception as e:
            print("Журнал за день:", e, flush=True)

    def _live_positions(self, positions):
        """Текущая цена позиций прямо на запрос телефона (робот сам обновляет их раз в 15 с вместе со стопами)."""
        if not positions:
            return positions
        tks = sorted({p["ticker"] for p in positions})
        key = tuple(tks)
        c = getattr(self, "_lp_cache", None)
        if c and c[0] == key and time.time() - c[1] < 1.0:
            lp = c[2]
        else:
            try:
                lp = _tail_ti().last_prices(tks)
            except Exception:
                lp = {}
            self._lp_cache = (key, time.time(), lp)
        out = []
        for p in positions:
            px = lp.get(p["ticker"])
            if px:
                m = p.get("mult")
                p = dict(p, last=px, pnl_rub=round(p["side"] * (px - p["entry"]) * m, 2) if m else p.get("pnl_rub"))
            out.append(p)
        return out

    def state(self):
        snap = self.snap or {}
        r = self.robot if self.running() else None
        if r and snap.get("positions"):
            snap = dict(snap, positions=self._live_positions(snap["positions"]))
        return {"version": VERSION, "time": now_msk().strftime("%d.%m.%Y %H:%M:%S"),
                "running": bool(r), "trade": bool(r and r.trade), "token_set": self.has_token(),
                "status": snap.get("status") if r else ("ждёт ключи API биржи в настройках" if not self.has_token() else "запускается…"),
                "market": snap.get("market"), "market_open": snap.get("market_open"), "idx_ref": self.s.get("idx_ref", "imoex"),
                "last_scan": snap.get("last_scan"),
                "positions": snap.get("positions", []) if r else [],
                "spikes": list(reversed(snap.get("spikes", []))), "scan": snap.get("scan", [])[:60],
                "threshold": sc.THRESHOLD, "ev_seq": self.ev_seq, "balance": snap.get("balance"),
                "entry": self.s.get("entry", "15:00"), "day_on": bool(self.s.get("day_on", True)), "day_mode": self.s.get("day_mode", "fixed"),
                "pos_summary": rc.positions_summary(snap.get("positions", []) if r else []),
                "index": (LIVE.index_now() if LIVE else None) or self.index,
                "pair": PAIR.info() if PAIR else None, "resetting": self.resetting,
                "broker": f"{self.s.get('exchange', 'bybit')}_{self.s.get('mode', 'demo')}", "currency": "USDT",
                "exchange": at.BROKER_NAMES.get(f"{self.s.get('exchange', 'bybit')}_{self.s.get('mode', 'demo')}", "")}


ROBOT = None
PAIR = None


# ============================================================================ живые цены (поток для телефона и робота)
LIVE = None


# ============================================================================ данные для вкладок
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def closed_trades():
    rows = [dict(r, strat="15:00") for r in read_csv(sc.SANDBOX_LOG)[-500:]] + \
           [dict(r, strat="всплеск") for r in read_csv(sc.SPIKE_LOG)[-500:]] + \
           [dict(r, strat="вручную") for r in read_csv(sc.MANUAL_LOG)[-500:]]
    rows.sort(key=lambda r: r.get("date", ""))
    out = list(reversed(rows))
    res = [float(r.get("result_pct") or 0) for r in out]
    return {"rows": out[:400], "n": len(res), "wins": sum(1 for x in res if x > 0), "sum": sum(res),
            "by_strat": {k: rc.strat_summary([r for r in out if r["strat"] == k]) for k in ("всплеск", "15:00", "вручную")},
            "total": rc.strat_summary(out)}


_CAL = {"cal": None}


def trade_calendar():
    """Крипта торгуется круглосуточно — календаря нет."""
    return {"days": [], "today": None, "trade_weekends": True, "note": "Криптобиржи работают круглосуточно, без выходных."}


def journal():
    rows = read_csv(sc.JOURNAL)
    days = read_csv(sc.DAYS_LOG)
    eq, peak, dd = 1.0, 1.0, 0.0
    for d in sorted(days, key=lambda x: x["date"]):
        eq *= 1 + float(d["day_net_pct"]) / 100
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    nets = [float(r["net_pct"]) for r in rows]
    return {"rows": list(reversed(rows[-300:])), "days": len(days), "trades": len(nets),
            "wins": sum(1 for x in nets if x > 0), "avg": (sum(nets) / len(nets)) if nets else 0,
            "total": (eq - 1) * 100, "dd": dd * 100,
            "period": f"{days[0]['date']} — {days[-1]['date']}" if days else ""}


_MARKET = {"t": 0, "data": None}
_MARKET_LOCK = threading.Lock()


def market():
    """Вкладка «Рынки»: все USDT-фьючерсы выбранной биржи; кэш 20 секунд."""
    with _MARKET_LOCK:
        if _MARKET["data"] and time.time() - _MARKET["t"] < 20:
            return _MARKET["data"]
        data = rc.market_overview()
        _MARKET.update(t=time.time(), data=data)
        return data


def _ts(t):
    """Время свечи (UTC) → секунды «как по Москве»: график подписывает время без сдвига, поэтому прибавляем 3 часа."""
    return calendar.timegm((t.replace(tzinfo=None) + timedelta(minutes=sc.SHIFT)).timetuple())


_TAIL_TI = {}


def _tail_ti():
    return sc.TInvest()


_LAST = {}


def last_price(ticker):
    """Последняя цена сделки (раз в секунду для живого графика); кэш 0,5 с — несколько окон не множат запросы."""
    now = time.time()
    c = _LAST.get(ticker)
    if c and now - c[0] < 0.5:
        return c[1]
    px = _tail_ti().last_prices([ticker]).get(ticker)
    out = {"ticker": ticker, "price": px, "t": _ts(datetime.now(sc.MSK))}
    _LAST[ticker] = (now, out)
    return out


def chart_tail(ticker, tf):
    """Последние 3–4 свечи — для быстрого обновления открытого графика (раз в несколько секунд)."""
    ti = _tail_ti()
    now = datetime.now(sc.MSK)
    back = timedelta(days=4) if tf >= 1440 else timedelta(minutes=tf * 4)
    rows = ti.candles_range(ticker, now - back, now + timedelta(minutes=1), tf)
    bars = [{"t": _ts(r_["t"]), "o": r_["o"], "h": r_["h"], "l": r_["l"], "c": r_["c"], "v": r_.get("v", 0)} for r_ in rows]
    poss = [p for p in (ROBOT.robot.positions if ROBOT.running() else []) if p["ticker"] == ticker]
    return {"ticker": ticker, "tf": tf, "bars": bars, "positions": poss}


def chart(ticker, tf):
    s = ROBOT.s
    rows, trades, tier = rc.chart_data(None, True, ticker, tf, s)
    closes = [r["c"] for r in rows]
    e10, e20, e200 = rc.ema(closes, 10), rc.ema(closes, 20), rc.ema(closes, 200)
    s200 = [sum(closes[i - 199:i + 1]) / 200 if i >= 199 else None for i in range(len(closes))]
    bars = [{"t": _ts(r["t"]), "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r.get("v", 0)} for r in rows]
    tr_out = []
    for t in trades:
        tr_out.append({"side": t["side"], "entry": t["entry"], "stop": t["stop"], "t1": t.get("t1"), "t2": t.get("t2"),
                       "entry_t": bars[t["entry_i"]]["t"], "result": t["result"], "open": t.get("open", False),
                       "impulse": t["impulse_t"].strftime("%d.%m %H:%M") if t.get("impulse_t") else "",
                       "touch": t["touch_t"].strftime("%d.%m %H:%M") if t.get("touch_t") else "",
                       "exits": [{"t": bars[i]["t"], "px": px, "frac": fr, "why": why} for i, px, fr, why in t["exits"]]})
    poss = [p for p in (ROBOT.robot.positions if ROBOT.running() else []) if p["ticker"] == ticker]
    return {"ticker": ticker, "tf": tf, "bars": bars, "ema10": e10, "ema20": e20, "ema200": e200, "sma200": s200, "trades": tr_out, "tier": tier,
            "days": rc.CHART_DEPTH[tf], "positions": poss, "real": rc.real_trades(ticker),
            "tbank": sc.tbank_url(ticker, (ROBOT.robot._terms_cache.get(ticker, ("share",))[0] if ROBOT.robot else "share"))}


# ============================================================================ обновление программы с телефона/компьютера
CODE_FILES = {"server.py", "scanner.py", "exchange.py", "autotrader.py", "robotcore.py", "tgnotify.py", "pair.py", "remote.py", "version.txt"}
CODE_DIRS = ("web/", "tools/")


def apply_update(data):
    """Новая версия программы сервера (тот же архив, что ставит установщик): заменить код и перезапуститься.
    Настройки, журналы, позиции, ключ и сертификат сервера не трогаются."""
    import shutil
    import tempfile
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    if "server.py" not in names or "autotrader.py" not in names:
        raise ValueError("это не архив программы сервера")
    code_dir = os.path.dirname(os.path.abspath(__file__))
    tmp = tempfile.mkdtemp(prefix="upd_", dir=code_dir)
    try:
        for n in names:
            if n.endswith("/") or ".." in n or n.startswith("/"):
                continue
            if n in CODE_FILES or n.startswith(CODE_DIRS):
                dst = os.path.join(tmp, n)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(z.read(n))
        for py in ("server.py", "scanner.py", "exchange.py", "autotrader.py", "robotcore.py"):    # архив целый и код читается
            compile(open(os.path.join(tmp, py), encoding="utf-8").read(), py, "exec")
        for d in CODE_DIRS:
            if os.path.isdir(os.path.join(tmp, d)):
                shutil.rmtree(os.path.join(code_dir, d), ignore_errors=True)
                shutil.copytree(os.path.join(tmp, d), os.path.join(code_dir, d))
        for f in CODE_FILES:
            if os.path.exists(os.path.join(tmp, f)):
                os.replace(os.path.join(tmp, f), os.path.join(code_dir, f))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ver = ""
    try:
        ver = z.read("version.txt").decode("utf-8").strip()
    except Exception:
        pass
    log(f"Программа сервера обновлена до версии {ver}. Перезапускаюсь (позиции и настройки сохраняются)…")

    def restart():
        time.sleep(2)                                        # сначала ответ телефону/компьютеру
        try:
            if ROBOT and ROBOT.running():
                ROBOT.robot.request_stop(False)
                time.sleep(3)
        finally:
            os._exit(3)                                      # служба systemd поднимет сервер уже с новым кодом
    threading.Thread(target=restart, daemon=True).start()
    return ver


# ============================================================================ сертификат и ключ доступа
def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "crypto-robot")])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(k, hashes.SHA256()))
    with open(KEY, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(KEY, 0o600)
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def cert_fp():
    der = ssl.PEM_cert_to_DER_cert(open(CERT).read())
    return hashlib.sha256(der).hexdigest()


def load_cfg():
    try:
        cfg = json.load(open(SERVER_CFG, encoding="utf-8"))
    except Exception:
        cfg = {}
    if not cfg.get("key"):
        cfg["key"] = secrets.token_urlsafe(24)
        with open(SERVER_CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        os.chmod(SERVER_CFG, 0o600)
    return cfg


def connect_code(host, port, key):
    return f"cryptorobot://{host}:{port}/{key}/{cert_fp()}"


# ============================================================================ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "CryptoRobot"
    key = ""

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        got = self.headers.get("X-Key", "")
        if got and hmac.compare_digest(got, self.key):
            return True
        time.sleep(1.0)                                      # замедляем подбор ключа
        self._send(401, {"error": "Нужен код подключения"})
        return False

    def _body(self):
        if getattr(self, "_raw_body", None) is not None:
            return json.loads(self._raw_body or b"{}")
        n = int(self.headers.get("Content-Length") or 0)
        if n > 12_000_000:
            raise ValueError("слишком большой запрос")
        return json.loads(self.rfile.read(n) or b"{}")

    LOCAL_API = {"/api/ping", "/api/pair_sync", "/api/relay", "/api/pair_set", "/api/pair_info", "/api/update", "/api/live"}

    def _proxy(self, method, raw=None):
        """Резервный сервер пересылает запросы телефона/программы активному серверу."""
        c = PAIR.client
        try:
            r = c.s.request(method, c.base + self.path, data=raw, timeout=60,
                            headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
            self._send(r.status_code, r.content, r.headers.get("Content-Type", "application/json"))
            return True
        except Exception:
            return False                                  # второй не отвечает — отвечаем сами

    def _live(self, q):
        """Живые цены одной длинной строкой событий (по строке JSON на изменение). Держим до 2 минут — телефон переподключится."""
        tks = [t for t in (q.get("tk") or "").split(",") if t][:20]
        LIVE.watch(tks, ttl=30)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        LIVE.clients += 1
        try:
            end, seq, last = time.time() + 120, -1, None
            while time.time() < end:
                with LIVE.cond:
                    if LIVE.seq == seq:
                        LIVE.cond.wait(10)
                    seq = LIVE.seq
                LIVE.watch(tks, ttl=30)
                r = ROBOT.robot if ROBOT.running() else None
                want = set(tks) | {sc.INDEX} | ({p["ticker"] for p in r.positions} if r else set())
                p = LIVE.snapshot(want)
                if p == last:
                    self.wfile.write(b"{}\n")                       # поддержка соединения
                else:
                    out = {"p": p, "t": int(time.time() * 1000), "mode": LIVE.mode}
                    ix = LIVE.index_now()
                    if ix:
                        out["idx"] = ix
                    self.wfile.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
                    last = p
                self.wfile.flush()
        except Exception:
            pass
        finally:
            LIVE.clients -= 1

    def _passive(self, path):
        return PAIR and PAIR.enabled and not PAIR.active and PAIR.peer_ok and path not in self.LOCAL_API

    def do_GET(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._static(u.path)
        if not self._auth():
            return
        if self._passive(u.path) and self._proxy("GET"):
            return
        if u.path == "/api/ping":
            r = ROBOT.robot if ROBOT.running() else None
            return self._send(200, {"active": bool(PAIR.active) if PAIR else True, "trade": bool(r and r.trade),
                                    "version": VERSION, "time": now_msk().strftime("%H:%M:%S")})
        if u.path == "/api/pair_sync" and pairmod:
            return self._send(200, pairmod.sync_payload())
        if u.path == "/api/pair_info":
            return self._send(200, PAIR.info() if PAIR else {"enabled": False})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/live":
                return self._live(q)
            if u.path == "/api/state":
                st = ROBOT.state()
                since = int(q.get("ev", 0))
                st["events"] = [e for e in ROBOT.events if e["id"] > since]
                lines, seq = LOG.since(int(q.get("log", 0)))
                st["log"] = [ln for _, ln in lines]
                st["log_seq"] = seq
                return self._send(200, st)
            if u.path == "/api/trades":
                return self._send(200, closed_trades())
            if u.path == "/api/results":
                return self._send(200, rc.results((ROBOT.snap or {}).get("balance")))
            if u.path == "/api/calendar":
                return self._send(200, trade_calendar())
            if u.path == "/api/backtest":
                return self._send(200, rc.BACKTEST.state)
            if u.path == "/api/journal":
                return self._send(200, journal())
            if u.path == "/api/market":
                return self._send(200, rc.attach_formations(market(), (ROBOT.snap or {}).get("spikes", [])))
            if u.path == "/api/settings":
                return self._send(200, ROBOT.public_settings())
            if u.path == "/api/last":
                return self._send(200, last_price(q.get("ticker", "BTCUSDT").upper().strip()))
            if u.path == "/api/chart_tail":
                tf = int(q.get("tf", 15))
                if tf not in rc.CHART_DEPTH:
                    raise ValueError("таймфрейм")
                return self._send(200, chart_tail(q.get("ticker", "BTCUSDT").upper().strip(), tf))
            if u.path == "/api/chart":
                tf = int(q.get("tf", 15))
                if tf not in rc.CHART_DEPTH:
                    raise ValueError("таймфрейм")
                return self._send(200, chart(q.get("ticker", "BTCUSDT").upper().strip(), tf))
            if u.path == "/api/check":
                return self._send(200, {"lines": __import__("exchange").check_all(ROBOT.s)})
            return self._send(404, {"error": "нет такого"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._auth():
            return
        if self._passive(u.path):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if self._proxy("POST", raw):
                return
            self._raw_body = raw
        try:
            b = self._body()
            if u.path in ("/api/relay", "/api/pair_set") and not pairmod:
                return self._send(404, {"error": "на сервере нет модуля пары — обновите сервер ещё раз"})
            if u.path == "/api/relay":
                return self._send(200, pairmod.do_relay(b))
            if u.path == "/api/pair_set":
                pairmod.save_pair(b.get("peer", ""), b.get("role", "primary"))
                log(f"Пара серверов настроена: этот сервер — {'основной' if b.get('role') == 'primary' else 'резервный'}. Перезапускаюсь.")
                threading.Thread(target=lambda: (time.sleep(2), os._exit(3)), daemon=True).start()
                return self._send(200, {"ok": True})
            if u.path == "/api/start":
                ok = ROBOT.start(True)
                return self._send(200, {"ok": ok, "msg": "" if ok else "Сначала введите ключи API биржи в настройках."})
            if u.path == "/api/reset":
                if b.get("confirm") != "СБРОС":
                    return self._send(400, {"error": "нужно подтверждение"})
                err = ROBOT.reset()
                return self._send(200, {"ok": not err, "msg": err or "Сброс начат: история сделок и баланса очищается."})
            if u.path == "/api/stop":
                ROBOT.stop_trade(bool(b.get("close")))
                return self._send(200, {"ok": True})
            if u.path == "/api/open":
                if not ROBOT.running():
                    return self._send(200, {"ok": False, "msg": "Робот не запущен."})
                tk = str(b.get("ticker") or "").upper().strip()
                if not tk:
                    return self._send(200, {"ok": False, "msg": "Укажите тикер."})
                f = lambda k: float(str(b.get(k) or 0).replace(",", ".")) / 100
                ROBOT.robot.request_open(tk, 1 if b.get("side", "long") == "long" else -1, f("share") or None, f("stop") or None,
                                         f("take"), f("trail"), b.get("exit_at") or "", bool(b.get("carry", True)),
                                         bool(b.get("by_form", True)))
                log(f"Запрошено открытие вручную: {tk} {'лонг' if b.get('side', 'long') == 'long' else 'шорт'}.")
                return self._send(200, {"ok": True, "msg": f"Открытие {tk} запрошено — результат в журнале действий."})
            if u.path == "/api/levels":
                if not ROBOT.running():
                    return self._send(200, {"ok": False, "msg": "Робот не запущен."})
                g = lambda k: float(str(b[k]).replace(",", ".")) if b.get(k) not in (None, "") else None
                ok = ROBOT.robot.request_levels(b.get("ticker"), g("stop"), g("take"), g("take2"))
                return self._send(200, {"ok": ok, "msg": "Изменено." if ok else "Позиция не найдена."})
            if u.path == "/api/carry":
                if not ROBOT.running():
                    return self._send(200, {"ok": False, "msg": "Робот не запущен."})
                ok = ROBOT.robot.request_carry(b.get("ticker"), b.get("mode") or "")
                return self._send(200, {"ok": ok, "msg": "" if ok else "Позиция не найдена."})
            if u.path == "/api/close":
                if not ROBOT.running():
                    return self._send(200, {"ok": False, "msg": "Робот не запущен."})
                ROBOT.robot.request_close(b.get("ticker") or None)
                log(f"С телефона запрошено закрытие: {b.get('ticker') or 'все позиции'}.")
                return self._send(200, {"ok": True})
            if u.path == "/api/backtest":
                if b.get("stop"):
                    rc.BACKTEST.stop()
                    return self._send(200, {"ok": True})
                if b.get("reset"):
                    rc.BACKTEST.reset()
                    return self._send(200, {"ok": True})
                s_ = dict(ROBOT.s, **{k: v for k, v in (b.get("settings") or {}).items() if str(k).startswith("mid_") or k == "day_markets"})
                s_.update({k: v for k, v in (b.get("settings") or {}).items() if k == "leverage"})
                ok = rc.BACKTEST.start(None, True, s_, int(b.get("months") or 12), log=log,
                                       capital=float(b.get("capital") or 0) or None)
                return self._send(200, {"ok": ok, "msg": "" if ok else "Проверка уже идёт."})
            if u.path == "/api/settings":
                new = dict(b)
                for k in rc.SECRET_KEYS + ["tg_token"]:
                    if not str(new.get(k) or "").strip():
                        new.pop(k, None)                     # пустое поле — ключ не менять
                    elif b.get(k + "_clear"):
                        new[k] = ""
                    else:
                        new[k] = str(new[k]).strip()
                for k in rc.SECRET_KEYS:
                    if b.get(k + "_clear"):
                        new[k] = ""
                acc_new = (new.get("exchange", ROBOT.s.get("exchange")), new.get("mode", ROBOT.s.get("mode")))
                if acc_new != (ROBOT.s.get("exchange"), ROBOT.s.get("mode")) and ROBOT.running() and ROBOT.robot.positions:
                    return self._send(200, {"ok": False, "msg": "Сначала закройте открытые позиции — потом меняйте биржу или счёт."})
                ROBOT.save(new)
                return self._send(200, {"ok": True})
            if u.path == "/api/tg_test":
                tg.send_now(ROBOT.s.get("tg_token"), ROBOT.s.get("tg_chat"),
                            "✅ Сервер крипторобота: уведомления работают.")
                return self._send(200, {"ok": True})
            if u.path == "/api/tg_find":
                tok = b.get("tg_token") or ROBOT.s.get("tg_token")
                cid, name = tg.find_chat_id(tok)
                return self._send(200, {"ok": bool(cid), "chat": cid, "name": name})
            if u.path == "/api/update":
                if PAIR and PAIR.enabled and PAIR.peer_ok and not b.get("from_peer"):
                    try:                                   # и второй сервер — той же версией
                        PAIR.client.post("/api/update", dict(b, from_peer=True))
                    except Exception as e:
                        log(f"Второй сервер не обновился: {e}")
                ver = apply_update(base64.b64decode(b.get("zip", "")))
                return self._send(200, {"ok": True, "version": ver})
            if u.path == "/api/journal_fill":
                d0 = date.fromisoformat(b.get("from"))
                threading.Thread(target=self._fill, args=(d0,), daemon=True).start()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "нет такого"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    @staticmethod
    def _fill(d0):
        n = now_msk()
        d1 = n.date() if (n.hour, n.minute) >= (17, 15) else n.date() - timedelta(days=1)
        print(f"=== Дозаполнение журнала с {d0} ===", flush=True)
        tick = sc.imoex_tickers()
        d = d0
        while d <= d1:
            sc.journal_day(d, tick)
            d += timedelta(days=1)
        sc.stats()

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype, {"Content-Security-Policy":
                                              "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"})


def main():
    global ROBOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("CRYPTO_PORT", 8444)))
    ap.add_argument("--host", default=os.environ.get("CRYPTO_HOST", ""), help="адрес сервера для кода подключения")
    ap.add_argument("--print-code", action="store_true", help="только показать код подключения")
    a = ap.parse_args()
    sys.stdout = Tee(sys.stdout)
    ensure_cert()
    cfg = load_cfg()
    host = a.host or cfg.get("host") or "IP-СЕРВЕРА"
    if a.host and cfg.get("host") != a.host:
        cfg["host"] = a.host
        json.dump(cfg, open(SERVER_CFG, "w", encoding="utf-8"))
    code = connect_code(host, a.port, cfg["key"])
    with open(os.path.join(HERE, "connect_code.txt"), "w", encoding="utf-8") as f:
        f.write(code + "\n")
    if a.print_code:
        print(code)
        return
    Handler.key = cfg["key"]
    global PAIR
    ROBOT = Robot()
    PAIR = pairmod.Pair(log, ROBOT.pair_activate, ROBOT.pair_deactivate) if pairmod else None
    if PAIR and PAIR.enabled:
        sc.RELAY = PAIR.relay
        log(f"Пара серверов: этот сервер — {'основной' if PAIR.role == 'primary' else 'резервный'}, второй — {PAIR.client.host}.")
        threading.Thread(target=PAIR.loop, daemon=True, name="pair").start()
    log(f"Сервер крипторобота, версия {VERSION}. Папка: {HERE}. Порт {a.port}.")
    try:
        for ln in __import__("exchange").check_all(ROBOT.s):
            log("Связь с биржами: " + ln)
    except Exception as e:
        log(f"Проверка связи с биржами: {e}")
    log(f"Код подключения для телефона сохранён в {os.path.join(HERE, 'connect_code.txt')}")
    threading.Thread(target=ROBOT.watchdog, daemon=True, name="watchdog").start()
    if PAIR and PAIR.enabled:
        pass                                              # кто торгует — решит пара после проверки второго сервера
    else:
        if not ROBOT.has_token():
            log("Ключи API биржи ещё не введены — робот только наблюдает. Ключи — в приложении на телефоне или в программе → Настройки.")
        ROBOT.start(bool(ROBOT.s.get("autostart")) and ROBOT.has_token())
    global LIVE
    LIVE = sc.LiveFeed(get_ti=_tail_ti, get_robot=lambda: ROBOT.robot if ROBOT and ROBOT.running() else None,
                       has_token=lambda: True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(CERT, KEY)
    httpd = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    httpd.daemon_threads = True
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
