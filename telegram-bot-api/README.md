# Локальный сервер Telegram Bot API — файлы до 2 ГБ

Облачный Bot API (`api.telegram.org`) отдаёт боту файлы только до **20 МБ** и принимает
до **50 МБ**. Официальный сервер [telegram-bot-api](https://github.com/tdlib/telegram-bot-api),
поднятый у себя в режиме `--local`, снимает оба лимита до **2 ГБ**. Один сервер обслуживает
всех ботов VPS: каждый бот просто ходит на `http://127.0.0.1:8081` вместо облака.

## Устройство

| Что | Где |
|---|---|
| Контейнеры | `telegram-bot-api` (aiogram/telegram-bot-api) + `telegram-bot-api-web` (nginx), `restart: unless-stopped` |
| Адрес для ботов | `http://127.0.0.1:8081` — снаружи VPS не виден |
| Данные | `/var/lib/telegram-bot-api/<token>/` — состояние бота (`td.binlog`) и файлы (`documents/`, `videos/`, …) |
| Ключи | `.env` рядом с compose: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` с https://my.telegram.org (любое приложение владельца VPS) |
| Чистка | `scripts/telegram-bot-api-cleanup.sh` → `/usr/local/bin/`, крон `25 4 * * *`: удаляет файлы старше 2 дней (плагин сразу копирует их в inbox бота). Лог `/var/log/telegram-bot-api-cleanup.log` |

Зачем nginx: в режиме `--local` сервер в `getFile` отдаёт абсолютный путь на своём диске
и сам файлы по HTTP не раздаёт. nginx раздаёт их по ссылке `/file/bot<token>/<папка>/<имя>` —
как облако: доступ только у того, кто знает токен. Служебные `td.binlog` не отдаются.
Читать файлы прямо с диска ботам нельзя: папки принадлежат пользователю контейнера, а имена
папок — это токены ботов.

## Что меняется у бота

1. **Патч плагина** (`plugin-patches/telegram/apply_patch.py`, блок `local-bot-api`): адрес
   API берётся из `TELEGRAM_API_ROOT`, ссылка на файл строится из локального пути, файл
   пишется на диск потоком. Без переменной плагин работает с облаком, как штатный.
2. **`~/.claude/channels/telegram/.env`**: `TELEGRAM_API_ROOT=http://127.0.0.1:8081`.
   Эту же переменную читают `hooks/stop-autoreply.py` и `scripts/bigfile-retention.py`.
3. **Всё, что шлёт запросы от имени этого бота** (кроны, свои скрипты), — тоже через
   `$TELEGRAM_API_ROOT`. После `logOut` запрос в облако снова «логинит» бота там, и
   локальный сервер перестаёт получать его сообщения.

## Установка

```bash
sudo mkdir -p /opt/telegram-bot-api /var/lib/telegram-bot-api
sudo cp docker-compose.yml nginx.conf /opt/telegram-bot-api/
sudo cp .env.example /opt/telegram-bot-api/.env && sudo chmod 600 /opt/telegram-bot-api/.env   # вписать ключи
sudo chown 101:101 /var/lib/telegram-bot-api && sudo chmod 750 /var/lib/telegram-bot-api
cd /opt/telegram-bot-api && sudo docker compose up -d
sudo cp ../scripts/telegram-bot-api-cleanup.sh /usr/local/bin/
( sudo crontab -l; echo '25 4 * * * /usr/local/bin/telegram-bot-api-cleanup.sh >/dev/null 2>&1' ) | sudo crontab -
```

## Перевести бота на локальный сервер

```bash
U=<linux-user>; E=/home/$U/.claude/channels/telegram/.env
TOK=$(grep '^TELEGRAM_BOT_TOKEN=' $E | cut -d= -f2- | tr -d "'\"")
echo 'TELEGRAM_API_ROOT=http://127.0.0.1:8081' | sudo tee -a $E
curl -s https://api.telegram.org/bot$TOK/logOut          # {"ok":true,"result":true}
sudo /usr/local/bin/bot-restart.sh $U 120                   # сразу после logOut: облако не пустит бота
                                                            # обратно 10 мин, ждать тишину дольше нельзя
curl -s http://127.0.0.1:8081/bot$TOK/getMe                 # проверка
```

Патч ставится сам: watchdog перед стартом сессии вызывает `tg-plugin-sync.sh --patch`.

## Вернуть бота в облако

Убрать `TELEGRAM_API_ROOT` из `.env`, вызвать `http://127.0.0.1:8081/bot$TOK/logOut`,
перезапустить бота. Telegram пускает обратно в облако не раньше чем через 10 минут после
`logOut` из облака.

## Диагностика

```bash
docker ps | grep telegram-bot-api
docker exec telegram-bot-api wget -qO- http://127.0.0.1:8082/   # статистика: боты, запросы
docker logs --tail 30 telegram-bot-api
```

Сервер упал — молчат все переведённые боты; watchdog увидит это как «бот не отвечает».
Поднять: `cd /opt/telegram-bot-api && docker compose up -d`.
