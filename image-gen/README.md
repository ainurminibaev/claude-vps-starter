# image-gen — генерация картинок для всех ботов

Общий сервис, как `gigaam-asr/` для голосовых: боты зовут одну команду, а модель
и учётка живут в одном месте.

Картинки рисует **Codex CLI** (`@openai/codex`) встроенным инструментом
`image_generation` — на **подписке ChatGPT** (Plus/Pro/Business), а не на
API-кредитах. Одна картинка — 1–6 минут.

## Устройство

```
бот ──> /usr/local/bin/image-gen ──unix-сокет──> imagegen.service ──> codex exec ──> OpenAI
        (клиент, у всех ботов)   /run/imagegen/   (user imagegen,     (codex-image)
                                 api.sock          токен ChatGPT)
```

| Файл | Куда ставится | Что это |
|---|---|---|
| `image-gen` | `/usr/local/bin/image-gen` | Клиент. `image-gen "<prompt>" -o out.png [--ref img]... [--size WxH]` |
| `imagegen-server.py` | `/usr/local/lib/imagegen/` | HTTP-сервер на unix-сокете, очередь по одной генерации, журнал |
| `codex-image` | `/usr/local/lib/imagegen/` | Вызов `codex exec` с фиксированной инструкцией «сгенерируй и сохрани out.png» |
| `imagegen.service` | `/etc/systemd/system/` | systemd-юнит с песочницей |

Почему не открытый порт, как у распознавания речи: там локальная модель, запрос ничего
не стоит. Здесь каждый запрос тратит чужую подписку, а внутри работает ИИ-агент,
поэтому:

- **токен** (`~/.codex/auth.json`) есть только у системного пользователя `imagegen`;
- **доступ** — членство в группе `imagegen-users` (сокет `0660`), кто вызвал —
  по `SO_PEERCRED`, пишется в `/var/log/imagegen/requests.log`;
- **очередь** — генерации строго по одной;
- **песочница** systemd: `ProtectHome`, `ProtectSystem=strict`, свой `/tmp` —
  агент не видит файлы ботов. Внутренняя песочница Codex (bubblewrap) внутри
  systemd не стартует, поэтому `CODEX_SANDBOX=danger-full-access`.

## Установка

```bash
groupadd --system imagegen-users
useradd --system --home-dir /var/lib/imagegen --create-home --shell /usr/sbin/nologin imagegen
npm i -g @openai/codex
install -d -m 755 /usr/local/lib/imagegen
install -m 755 imagegen-server.py codex-image /usr/local/lib/imagegen/
install -m 755 image-gen /usr/local/bin/image-gen
install -m 644 imagegen.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now imagegen

# вход в ChatGPT — владелец подписки вводит код у себя в браузере (15 минут)
sudo -u imagegen -H env HOME=/var/lib/imagegen codex login --device-auth
sudo -u imagegen -H env HOME=/var/lib/imagegen codex login status

# доступ боту (+ перезапуск его сессии, иначе процесс не увидит группу)
usermod -aG imagegen-users <user>
# и в его settings.json: "Bash(/usr/local/bin/image-gen:*)"
```

Конфиг Codex для приватности (`/var/lib/imagegen/.codex/config.toml`):

```toml
[history]
persistence = "none"

[features]
memories = false
```

## Важно

- Токен один: **не копировать** `auth.json` в другие места — OpenAI меняет
  refresh-токен при обновлении, вторая копия ломается. Только переносить.
- Подписка ChatGPT персональная. Общий доступ многих ботов к одной подписке
  формально — передача аккаунта; лимиты кончаются быстро. Для массовой генерации
  честнее API (`gpt-image`, оплата за картинку).
- Коды клиента: `0` ок · `2` нет доступа/аргументы · `3` сервис не залогинен ·
  `4` лимит подписки · `5` таймаут · `1` прочее.
