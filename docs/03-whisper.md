# 03. Whisper — локальная расшифровка голоса

Голосовые в Telegram приходят как `.oga`-файлы. Мы не гоняем их в OpenAI — поднимаем Whisper локально в Docker. Причины:

- **Цена** — ноль. Минутная голосовуха в OpenAI ~$0.006, у тебя в день их десятки, за год набежит.
- **Задержка** — модель `base` тёплая в RAM, минутный файл разбирается за ~10 секунд. CLI-запуск `whisper` грузит модель с диска каждый раз, даёт ~30–50 секунд overhead.
- **Приватность** — голос не уходит из твоего сервера.

## 1. Запуск контейнера

```bash
cd /root/projects/claude-vps-starter/whisper-asr
docker compose up -d
docker ps | grep whisper-asr   # должен быть Up
```

Конфиг — `docker-compose.yml`:

```yaml
services:
  whisper-asr:
    image: onerahmet/openai-whisper-asr-webservice:latest
    container_name: whisper-asr
    restart: unless-stopped
    ports:
      - "127.0.0.1:9000:9000"   # только localhost, извне не торчит
    environment:
      ASR_ENGINE: faster_whisper
      ASR_MODEL: base
      ASR_MODEL_PATH: /data/models
    volumes:
      - ./cache:/root/.cache
      - ./models:/data/models
```

Первый запуск скачает модель `base` (~75 МБ) в `./models/`. Дальше модель живёт в RAM между запросами — это и есть warm-эффект.

## 2. Скрипт-обёртка

Файл: `scripts/whisper_via_api.sh`

```bash
#!/bin/bash
set -euo pipefail
if [ -z "${1:-}" ]; then
  echo "usage: $0 <audio-file>" >&2
  exit 1
fi
curl -sS --fail-with-body -X POST \
  -F "audio_file=@${1}" \
  "http://127.0.0.1:9000/asr?language=ru&output=txt&encode=true"
```

Параметры:
- `language=ru` — подсказка языка, точность чуть выше.
- `output=txt` — plain text, без JSON-обёртки.
- `encode=true` — встроенный ffmpeg декодирует `.oga`/`.ogg`/`.m4a` как есть.

Положи скрипт в `/root/.claude/scripts/whisper_via_api.sh`, сделай `chmod +x`. `install.sh` симлинкает его автоматически.

Проверить:

```bash
/root/.claude/scripts/whisper_via_api.sh /path/to/voice.oga
```

Должен вывести распознанный текст.

## 3. Куда кладутся голосовые от Telegram

plugin:telegram сохраняет вложения в `/root/.claude/channels/telegram/inbox/*.oga`. Claude получает `attachment_file_id` в событии, вызывает `download_attachment`, получает путь — и пробрасывает его в `whisper_via_api.sh` через `Bash`.

Это поведение фиксируется в `CLAUDE.md` — там прописано: для всех голосовых в `/root/.claude/channels/telegram/inbox/` использовать именно warm-API, а не CLI.

## 4. Протокол ответа на голосовуху

В `CLAUDE.md`:

1. Первое сообщение — транскрипт inline monospace (одинарные бэктики): `` `что ты услышал` ``. Это страхует от глухих ошибок Whisper — ты видишь текст до того, как Claude начал действовать.
2. Дальше — обычный flow (план, прогресс, финал) или сразу ответ, если задача короткая.

## 5. Апгрейд точности

Для русского `base` иногда мажет. Варианты:

- `small` — в 2–3 раза точнее на русском, 2 ГБ RAM, ~2× медленнее.
- `medium` — заметно лучше, ~5 ГБ RAM, медленнее ещё.

Как переключить:

```bash
# в whisper-asr/docker-compose.yml
environment:
  ASR_MODEL: small   # или medium
```

```bash
cd /root/projects/claude-vps-starter/whisper-asr
docker compose up -d
```

Первый запрос после смены модели будет долгим (подгрузка), дальше — warm.

## 6. Если контейнер упал

```bash
docker ps | grep whisper      # ничего не вышло = упал
cd /root/projects/claude-vps-starter/whisper-asr
docker compose up -d
docker logs whisper-asr --tail 100
```

Fallback на CLI (если совсем надо):

```bash
whisper /path/to/voice.oga --language ru --output_format txt --model base
```

Это медленно (+30 сек overhead), но работает без контейнера.

## 7. Длинные аудио (>30 сек обработки)

Если голосовуха длинная (5+ минут), расшифровка может занять минуту. В `CLAUDE.md` предписано: если предполагаешь >30 сек — запусти через `Bash run_in_background`, сразу отправь ack в Telegram «расшифровываю, минуту», дождись результата и продолжай.
