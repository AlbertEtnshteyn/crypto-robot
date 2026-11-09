#!/usr/bin/env bash
# Установка/обновление сервера крипторобота на VPS (Ubuntu 22.04/24.04, Debian 12).
# Запуск от root:  bash install.sh <публичный-IP-сервера>
# Программа ставится в /opt/cryptorobot и запускается службой cryptorobot (порт 8444).
# Робот Мосбиржи на том же сервере (/opt/moexrobot, порт 8443) не затрагивается.
# Никаких сертификатов в систему не ставится: криптобиржам они не нужны.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/opt/cryptorobot
PORT=8444
IP="${1:-}"
export DEBIAN_FRONTEND=noninteractive

echo "== 1/5 Пакеты системы"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ca-certificates curl tzdata >/dev/null

echo "== 2/5 Пользователь и папка $DST"
id cryptorobot >/dev/null 2>&1 || useradd --system --home "$DST" --shell /usr/sbin/nologin cryptorobot
mkdir -p "$DST"
# код программы обновляется, данные (настройки с ключами бирж, журналы, ключ и сертификат сервера) остаются
for f in server.py scanner.py exchange.py autotrader.py robotcore.py tgnotify.py pair.py remote.py version.txt; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DST/"
done
rm -rf "$DST/web" "$DST/tools"; cp -r "$SRC/web" "$DST/web"; [ -d "$SRC/tools" ] && cp -r "$SRC/tools" "$DST/tools"
# перенос настроек и журналов с компьютера (если переданы)
if [ -d "$SRC/transfer" ]; then
  cp "$SRC/transfer/"* "$DST/" 2>/dev/null || true
  echo "   перенесены настройки и журналы с компьютера"
fi

echo "== 3/5 Python и библиотеки"
[ -x "$DST/venv/bin/python" ] || python3 -m venv "$DST/venv"
"$DST/venv/bin/pip" install -q --upgrade pip
"$DST/venv/bin/pip" install -q "requests>=2.31" certifi cryptography

if [ -z "$IP" ]; then IP="$(curl -s -4 --max-time 8 https://ifconfig.me || hostname -I | awk '{print $1}')"; fi
chown -R cryptorobot:cryptorobot "$DST"
chmod 700 "$DST"
[ -f "$DST/settings.json" ] && chmod 600 "$DST/settings.json"

echo "== 4/5 Служба cryptorobot (адрес $IP, порт $PORT)"
cat > /etc/systemd/system/cryptorobot.service <<UNIT
[Unit]
Description=Крипторобот Bybit/Binance (сервер)
After=network-online.target
Wants=network-online.target

[Service]
User=cryptorobot
WorkingDirectory=$DST
ExecStart=$DST/venv/bin/python -u $DST/server.py --port $PORT --host $IP
Restart=always
RestartSec=10
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable cryptorobot >/dev/null 2>&1
rm -f "$DST/connect_code.txt"
systemctl restart cryptorobot
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow $PORT/tcp >/dev/null; fi

echo "== 5/5 Проверка"
for i in $(seq 1 40); do [ -s "$DST/connect_code.txt" ] && break; sleep 1; done
if [ ! -s "$DST/connect_code.txt" ]; then
  echo "ОШИБКА: сервер не запустился. Последние строки журнала:"; journalctl -u cryptorobot -n 40 --no-pager; exit 1
fi
systemctl is-active --quiet cryptorobot && echo "Служба работает."
echo "== Связь с биржами с этого сервера"
for u in https://api.bybit.com/v5/market/time https://api-demo.bybit.com/v5/market/time https://fapi.binance.com/fapi/v1/time https://demo-fapi.binance.com/fapi/v1/time https://data.binance.vision/; do
  c=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u" || echo 000); echo "   $u → HTTP $c"
done
echo "CONNECT: $(cat $DST/connect_code.txt)"
