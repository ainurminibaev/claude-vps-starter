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

## 2. Роутер voice-to-text

Файл: `scripts/voice-to-text`, ставится в `/usr/local/bin/voice-to-text` (это делает `install.sh`).

```bash
voice-to-text <audio-file> [--lang auto|ru|tt|en|ar]
```

- stdout — только текст, stderr — одна строка о том, куда ушёл файл и сколько это заняло.
- Решения пишутся в syslog: `journalctl -t voice-to-text -n 20`.
- `--lang ru|en|ar` — сразу в Whisper с этим языком. `--lang tt` — сразу в татарский сервис.
- `--lang auto` (по умолчанию):
  - татарский сервис **не установлен** — сразу Whisper `language=ru`, без определения языка. Задержка как у старого скрипта;
  - татарский сервис **установлен** — см. раздел 8.

Под капотом для Whisper:

```bash
curl -sS --fail-with-body -X POST -F "audio_file=@voice.oga" \
  "http://127.0.0.1:9000/asr?language=ru&output=txt&encode=true"
```

- `language=ru` — подсказка языка, точность чуть выше.
- `output=txt` — plain text, без JSON-обёртки.
- `encode=true` — встроенный ffmpeg декодирует `.oga`/`.ogg`/`.m4a` как есть.

Настройки — в `/etc/voice-to-text.conf` (строки `KEY=value`; переменные окружения с тем же именем важнее файла): `TATAR_ENABLED`, `TATAR_URL`, `WHISPER_URL`, `TATAR_TIMEOUT`, `WHISPER_TIMEOUT`, `VTT_CONF_MIN`. Без файла — Whisper на `127.0.0.1:9000`, татарский выключен.

**Почему `/usr/local/bin`, а не `/root/.claude/scripts`:** у `/root` права 700. Если на сервере несколько ботов (docs/05), другие пользователи не могут выполнить скрипт из `/root/...`, даже по симлинку. Общие скрипты — только в `/usr/local/bin`. Роутер ставится **копией**: после `git pull` прогони `install.sh` ещё раз, чтобы обновить его.

Старое имя `scripts/whisper_via_api.sh` оставлено для совместимости — это тонкая обёртка: `exec /usr/local/bin/voice-to-text "$@"`. Если роутер ещё не установлен, обёртка работает по-старому (Whisper ru). `install.sh` симлинкает её в `/root/.claude/scripts/whisper_via_api.sh`.

Проверить:

```bash
voice-to-text /path/to/voice.oga
```

Должен вывести распознанный текст.

## 3. Куда кладутся голосовые от Telegram

plugin:telegram сохраняет вложения в `/root/.claude/channels/telegram/inbox/*.oga`. Claude получает `attachment_file_id` в событии, вызывает `download_attachment`, получает путь — и пробрасывает его в `voice-to-text` через `Bash`.

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

## 8. Татарский язык (опционально)

Whisper татарский не знает: модель `base` выдаёт на татарской речи мусор, а `/detect-language` называет её азербайджанским, казахским или литовским с низкой уверенностью. На этом и держится маршрутизация: русскую речь Whisper узнаёт уверенно (ru ≥ 0.9), а всё неуверенное — скорее всего татарский.

### Как это работает

```
голосовое → voice-to-text
              │
              ├─ Whisper /detect-language (~3 с)
              │     ru / en / ar и уверенность ≥ 0.80 → Whisper /asr с этим языком
              │     всё остальное                     → татарский сервис :9002
              │
              └─ татарский сервис упал / таймаут → откат на Whisper ru
```

Татарский сервис — `tatar-asr/server.py`: модель [aadel4/omniASR-CTC-300M-v2](https://huggingface.co/aadel4/omniASR-CTC-300M-v2) (Omnilingual ASR 300M, Apache-2.0), сжатая до int8 для CPU. HTTP на `127.0.0.1:9002`: `POST /asr` (поле `audio_file`, любой формат, который ест ffmpeg) → text/plain, `GET /health` → JSON.

Сервис **спит**, когда не нужен. Порт держит systemd (`tatar-asr.socket`); первый запрос будит процесс, тот грузит модель, отвечает и через 15 минут без запросов (`IDLE_TIMEOUT_SEC=900`) сам выходит и отдаёт память.

### Цифры (2 vCPU, без GPU)

| | |
|---|---|
| Скорость | RTF ~0.3–0.6: минута речи за 20–35 с |
| Память | ~1 ГБ, пока модель загружена; 0 в простое |
| Холодный старт | +~8 с к первому запросу после простоя |
| Определение языка | +~3 с к каждому голосовому (и к русским тоже) |
| Диск | ~0.4 ГБ модель + venv; при установке временно ~2 ГБ (+~1.5 ГБ, если ставится torch) |
| Ограничения | `Nice=10`, `CPUWeight=50`, `MemoryMax=2500M` — не душит ботов |

Текст на выходе — строчными, без пунктуации. Длинные записи режутся на куски по ~30 с по паузам. Запросы обрабатываются по одному, остальные ждут в очереди.

### Установка

```bash
sudo /root/projects/claude-vps-starter/tatar-asr/install-tatar-asr.sh
```

Скрипт идемпотентный. Что делает:

1. Проверяет свободное место и память (для квантизации нужно ~2.5 ГБ RAM+swap на пике).
2. Ставит `ffmpeg` и `python3-venv`, если их нет.
3. Создаёт системного пользователя `tatar-asr` и `/opt/tatar-asr/`.
4. Делает venv с `--system-site-packages`; если torch нет — ставит CPU-сборку, плюс `transformers>=5.3,<6`.
5. Качает модель (~1.3 ГБ), квантует в int8 (`quantize.py`, несколько минут), fp32-копию удаляет.
6. Ставит `tatar-asr.socket` + `tatar-asr.service`, включает сокет.
7. Обновляет `/usr/local/bin/voice-to-text` и пишет `TATAR_ENABLED=1` в `/etc/voice-to-text.conf`.
8. Прогоняет тестовый запрос.

Другие путь/порт/пользователь: `INSTALL_DIR=... PORT=... SVC_USER=... sudo -E ./install-tatar-asr.sh`. Пересобрать модель: `--force-model`.

Проверить:

```bash
voice-to-text /path/to/tatar.oga               # авто
voice-to-text /path/to/tatar.oga --lang tt     # сразу в татарский
journalctl -t voice-to-text -n 5               # куда ушёл файл и почему
systemctl status tatar-asr.socket tatar-asr.service
journalctl -u tatar-asr.service -n 50
```

В `CLAUDE.md` бота стоит добавить: татарский транскрипт приходит без пунктуации и строчными — это нормально, а не ошибка.

### Откат

Выключить татарский, оставить файлы:

```bash
sudo sed -i 's/^TATAR_ENABLED=.*/TATAR_ENABLED=0/' /etc/voice-to-text.conf   # роутер снова сразу в Whisper ru
sudo systemctl disable --now tatar-asr.socket tatar-asr.service
```

Удалить полностью (сервис, пользователь, `/opt/tatar-asr`):

```bash
sudo /root/projects/claude-vps-starter/tatar-asr/install-tatar-asr.sh --uninstall
```

Вернуть старый скрипт без роутера: `git checkout <старый коммит> -- scripts/whisper_via_api.sh` — там голый curl в Whisper ru.
