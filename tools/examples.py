"""Примеры входов и выходов робота на истории («Движение дня» с фильтром тренда) — картинки в out/.
Запуск: python tools/examples.py [дней]. Нужен доступ к data.binance.vision."""
import os, sys, json, math
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import robotcore as rc, scanner as sc

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
OUT = "out"; os.makedirs(OUT, exist_ok=True)
s = dict(rc.DEFAULTS); s.update(exchange="binance", mid_modes=["both"])
sc.configure_exchange(s)
ti = sc.TInvest()
since = (datetime.utcnow() - timedelta(days=DAYS + 12)).date()     # +12 дней — история под EMA200 на 1 часе
start = (datetime.utcnow() - timedelta(days=DAYS)).date()
TK = list(sc.FALLBACK_TICKERS)
data = {}
for tk in TK:
    try:
        rows = rc.bt_bars(ti, None, tk, since, print)
    except Exception as e:
        print(tk, "нет истории", e); continue
    if rows:
        data[tk] = rows
TFS = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "5,15,60").split(",")]
TRS = [float(x) / 100 for x in (sys.argv[3] if len(sys.argv) > 3 else "0.5,1,1.5,2,3,5").split(",")]
VARS = [(tf, tr) for tf in TFS for tr in TRS]
SMN = {"24h": "стоп за лоем 24 ч", "trend": "стоп за лоем с начала тренда", "1h": "стоп за лоем последнего часа"}
res = {}
for tf, mode in VARS:
    s["mid_tfs"] = [tf]
    m = rc.mid_cfg(s); m.update(cost=2 * 0.0005 + rc.BT_SLIP, fund={}, stop_mode="swing", no_time_exit=True, trail_mode=True, act=0.01, trail=mode)
    tr = []
    for tk, rows in data.items():
        tr += [t for t in rc.bt_run({tk: rows}, m, None) if t["date"] >= str(start)]
    pos = sum(t["pct"] for t in tr if t["pct"] > 0); neg = -sum(t["pct"] for t in tr if t["pct"] < 0)
    res[(tf, mode)] = tr
    import statistics as _st
    tr.sort(key=lambda t: (t["date"], t["time"]))
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in tr:
        eq += t["pct"]; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    stp = _st.median([abs(t["entry"] / t.get("stop0", t["stop"]) - 1) * 100 for t in tr]) if tr else 0
    months = {}
    for t in tr:
        months[t["date"][:7]] = months.get(t["date"][:7], 0) + t["pct"]
    print(f"ВАРИАНТ ТФ {tf} мин, стоп под лоем предыдущего отката, +1% → б/у, трейлинг {mode * 100:g}%: сделок {len(tr)}, стоп обычно {stp:.2f}%, "
          f"по месяцам {', '.join(f'{k} {v:+.0f}%' for k, v in sorted(months.items()))}, месяцев в плюсе {sum(1 for v in months.values() if v > 0)} из {len(months)}, "
          f"худшая просадка {mdd:.0f}%, "
          f"в плюсе {sum(1 for t in tr if t['pct'] > 0) / max(1, len(tr)) * 100:.0f}%, средняя {sum(t['pct'] for t in tr) / max(1, len(tr)):+.3f}%, "
          f"сумма {sum(t['pct'] for t in tr):+.1f}%, профит-фактор {pos / neg if neg else 0:.2f}", flush=True)
for k in res:
    res[k].sort(key=lambda t: (t["date"], t["time"]))
for tf_ in TFS:
    b_ = max((k for k in res if k[0] == tf_), key=lambda k: sum(t["pct"] for t in res[k]))
    print(f"ЛУЧШИЙ ТРЕЙЛИНГ ТФ {tf_} мин: {b_[1] * 100:g}% — сумма {sum(t['pct'] for t in res[b_]):+.1f}%", flush=True)
best = max(res, key=lambda k: sum(t["pct"] for t in res[k]))
kept, cut = res[best], []
print("ЛУЧШИЙ", best)

def resample(rows, tf):
    out, cur = [], None
    for t, o, h, l, c, *rest in rows:
        v = rest[0] if rest else 0
        k = t.replace(minute=t.minute - t.minute % tf) if tf < 60 else t.replace(minute=0)
        if cur and cur[0] == k:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
        else:
            cur = [k, o, h, l, c, v]; out.append(cur)
    return out

def ema(v, n):
    a, e, o = 2 / (n + 1), None, []
    for x in v:
        e = x if e is None else e + a * (x - e); o.append(e)
    return o

BG, TX, UP, DN = "#131722", "#d1d4dc", "#26a69a", "#ef5350"
def draw(t, kind, n):
    tf = t["tf"]; B = resample(data[t["ticker"]], tf)
    cl = [b[4] for b in B]
    e10, e20, e200 = ema(cl, 10), ema(cl, 20), ema(cl, 200)
    s200 = [sum(cl[i - 199:i + 1]) / 200 if i >= 199 else None for i in range(len(cl))]
    te = datetime.strptime(t["date"] + " " + t["time"], "%Y-%m-%d %H:%M")
    x = datetime.strptime(f"{t['exit_time']}.{te.year}", "%d.%m %H:%M.%Y")
    ie = max(i for i, b in enumerate(B) if b[0] <= te)
    ix = max(i for i, b in enumerate(B) if b[0] <= x)
    a, z = max(0, ie - 70), min(len(B), ix + 25)
    fig, ax = plt.subplots(figsize=(12, 5.6), facecolor=BG); ax.set_facecolor(BG)
    for sp_ in ax.spines.values(): sp_.set_color("#2a2e39")
    ax.tick_params(colors="#787b86", labelsize=8)
    for i in range(a, z):
        _, o, h, l, c, _v = B[i]; col = UP if c >= o else DN
        ax.plot([i, i], [l, h], color=col, lw=.8)
        ax.add_patch(Rectangle((i - .35, min(o, c)), .7, max(abs(c - o), (h - l) * .02 or 1e-12), color=col))
    xs = list(range(a, z))
    av = ax.twinx(); av.set_ylim(0, max(b[5] for b in B[a:z]) * 4 or 1); av.axis("off")
    av.bar(xs, [B[i][5] for i in xs], width=.7, color=[(UP if B[i][4] >= B[i][1] else DN) for i in xs], alpha=.45)
    ax.plot(xs, e10[a:z], color="#e8e8e8", lw=1.1, label="EMA 10")
    ax.plot(xs, e20[a:z], color="#2962ff", lw=1.4, label="EMA 20")
    ax.plot(xs, e200[a:z], color="#ef5350", lw=1.4, label="EMA 200")
    ax.plot(xs, [v if v else math.nan for v in s200[a:z]], color="#b388ff", lw=1.4, label="SMA 200")
    sd = t["side"]; ok = t["pct"] > 0
    rej = kind != "вход"
    ax.scatter([ie], [t["entry"]], marker="^" if sd > 0 else "v", s=220, color="none" if rej else ("#00e676" if sd > 0 else "#ff5252"),
               edgecolor="#f5c542" if rej else "white", linewidth=2, zorder=5)
    msk = lambda d: (d + timedelta(hours=3)).strftime("%d.%m %H:%M")
    ax.annotate(f"{'сигнал ' if rej else 'ВХОД '}{'лонг' if sd > 0 else 'шорт'}{' — НЕ ВОШЁЛ' if rej else ''}\n{t['entry']:.6g} · {msk(te)}", (ie, t["entry"]), (12, -38 if sd > 0 else 26),
                textcoords="offset points", color="white", fontsize=9, fontweight="bold", arrowprops=dict(arrowstyle="-", color="#888"))
    if kind == "вход":
        s0 = t.get("stop0", t["stop"])
        ax.hlines([s0], ie, ix, colors=DN, linestyles="--", lw=1.2); ax.text(ie - 1, s0, f"стоп на входе {s0:.6g}", color=DN, fontsize=8, ha="right", va="center")
        if abs(t["take"] / t["entry"] - 1) < 0.5:
            ax.hlines([t["take"]], ie, ix, colors=UP, linestyles="--", lw=1.2); ax.text(ie - 1, t["take"], f"тейк {t['take']:.6g}", color=UP, fontsize=8, ha="right", va="center")
        ax.scatter([ix], [t["exit"]], marker="o", s=140, color=UP if ok else DN, edgecolor="white", zorder=5)
        ax.annotate(f"ВЫХОД: {t['reason']} {t['pct']:+.2f}%\n{t['exit']:.6g} · {msk(x)}", (ix, t["exit"]), (12, 22 if sd > 0 else -40),
                    textcoords="offset points", color=UP if ok else DN, fontsize=9, fontweight="bold", arrowprops=dict(arrowstyle="-", color="#888"))
    trend = (f"EMA10 {'>' if e10[ie-1] > e20[ie-1] else '<'} EMA20 · цена {'выше' if t['entry'] > e200[ie-1] else 'ниже'} EMA200 · "
             f"{'выше' if s200[ie-1] and t['entry'] > s200[ie-1] else 'ниже'} SMA200")
    tfn = {5: "5 мин", 15: "15 мин", 60: "1 час"}[tf]
    if kind == "вход":
        title = f"{n}. {t['ticker']} · {tfn} · {'ЛОНГ' if sd > 0 else 'ШОРТ'} · итог {t['pct']:+.2f}% ({t['reason']}) · стоп под лоем отката, +1% → б/у, трейлинг"
        col = UP if ok else DN
    else:
        title = f"{n}. {t['ticker']} · {tfn} · {'ЛОНГ' if sd > 0 else 'ШОРТ'} НЕ ОТКРЫТ — против тренда"
        col = "#f5c542"
        ax.text(.5, .04, f"без фильтра было бы: {t['reason']} {t['pct']:+.2f}%", transform=ax.transAxes, ha="center", color="#9598a1", fontsize=9)
    ax.set_title(title + "\nна входе: " + trend, color=col, fontsize=11, loc="left")
    step = max(1, (z - a) // 8)
    ax.set_xticks(list(range(a, z, step))); ax.set_xticklabels([msk(B[i][0])[0:11] for i in range(a, z, step)], rotation=0)
    ax.set_xlim(a - 4, z + 2)
    lg = ax.legend(loc="upper left", fontsize=8, facecolor=BG, edgecolor="#2a2e39")
    for tx_ in lg.get_texts(): tx_.set_color(TX)
    fig.text(.99, .01, "время МСК · история Binance USDT-фьючерсы, 5-мин свечи", color="#5d606b", ha="right", fontsize=7)
    plt.tight_layout(); fn = f"{OUT}/{n:02d}-{t['ticker']}-{kind}.png"; plt.savefig(fn, dpi=100, facecolor=BG); plt.close(fig)
    return fn

# выбор: разные монеты, лонги и шорты, плюс и минус
def pick(lst, k):
    lst = sorted(lst, key=lambda t: (t["reason"] not in ("открыта", "конец данных"), t["date"], t["time"]), reverse=True)
    out, seen = [], set()
    for want in ((1, True), (-1, True), (1, False), (-1, False)) * 6:
        for t in lst:
            if len(out) >= k: break
            if t["ticker"] in seen or t in out: continue
            if t["side"] == want[0] and (t["pct"] > 0) == want[1]:
                out.append(t); seen.add(t["ticker"]); break
    for t in lst:
        if len(out) >= k: break
        if t not in out: out.append(t)
    return out
sel = pick(kept, 14); rej = []
files = [draw(t, "вход", i + 1) for i, t in enumerate(sel)] + [draw(t, "отсечён", len(sel) + i + 1) for i, t in enumerate(rej)]
w = sum(1 for t in kept if t["pct"] > 0)
summ = {"best": list(best), "variants": {f"{a}|{b}": {"n": len(v), "win": sum(1 for t in v if t["pct"] > 0), "avg": round(sum(t["pct"] for t in v) / max(1, len(v)), 3), "sum": round(sum(t["pct"] for t in v), 1)} for (a, b), v in res.items()}, "days": DAYS, "coins": len(data), "trades": len(kept), "win": w, "avg": round(sum(t["pct"] for t in kept) / max(1, len(kept)), 3),
        "cut": len(cut), "cut_avg": round(sum(t["pct"] for t in cut) / max(1, len(cut)), 3),
        "cut_win": sum(1 for t in cut if t["pct"] > 0), "files": files}
json.dump(summ, open(f"{OUT}/summary.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summ, ensure_ascii=False))
