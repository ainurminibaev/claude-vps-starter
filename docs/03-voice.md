# 03. Голос — локальная расшифровка

Голосовые в Telegram приходят как `.oga`-файлы. Мы не гоняем их в облако — распознаём на своём сервере. Причины:

- **Цена** — ноль. Минутная голосовуха в облачном API стоит копейки, но их десятки в день.
- **Приватность** — голос не уходит с твоего сервера.
- **Качество** — для русского GigaAM v3 заметно точнее Whisper `base`/`small` и сразу ставит пунктуацию.

## 1. Схема

```
голосовое → /usr/local/bin/voice-to-text
              │
              ├─ gigaam-asr /detect-language (VoxLingua107, ~0.3–3 с)
              │     русский (ru+be+uk) с уверенностью ≥ 0.60 → GigaAM v3 (gigaam-asr /asr)
              │     всё остальное (tt, en, ar, …)             → Omnilingual 300M (tatar-asr, опционально)
              │
              ├─ gigaam-asr упал → Omnilingual для любого языка (он знает и русский)
              └─ упало всё → ошибка в stderr, код выхода 1
```

Два локальных сервиса, оба — systemd socket activation: порт держит systemd, процесс стартует на первый запрос, отвечает и сам выходит после простоя, отдавая память системе.

| | `gigaam-asr` (обязательный) | `tatar-asr` (опционально) |
|---|---|---|
| Что делает | определение языка + русский | татарский, английский, арабский и ещё сотни языков |
| Модели | VoxLingua107 ECAPA → ONNX; GigaAM v3 e2e CTC → ONNX int8 | Omnilingual ASR CTC 300M → int8 |
| Порт | `127.0.0.1:9003` | `127.0.0.1:9002` |
| Каталог / пользователь | `/opt/gigaam-asr`, `gigaam-asr` | `/opt/tatar-asr`, `tatar-asr` |
| Зависимости в работе | numpy + onnxruntime + sentencepiece, **без torch** | torch + transformers |
| Память | ~400 МБ загружен, пик ~660 МБ; 0 в простое | ~1 ГБ загружен; 0 в простое |
| Холодный старт | ~1–2 с | ~8–15 с |
| Засыпает через | 10 мин | 15 мин |
| Скорость (2 vCPU) | минута речи ≈ 5–10 с | минута речи ≈ 20–35 с |
| Текст | с пунктуацией и заглавными | строчными, без пунктуации |
| Диск | ~0.35 ГБ (+venv) | ~0.4 ГБ (+venv с torch) |

Цифры — с сервера на 2 vCPU без GPU.

## 2. Установка gigaam-asr

```bash
sudo /root/projects/claude-vps-starter/gigaam-asr/install-gigaam-asr.sh
```

`install.sh` запускает его сам, если сервис ещё не стоит (пропустить: `SKIP_STT=1 ./scripts/install.sh`). Скрипт идемпотентный. Что делает:

1. Проверяет место (~4 ГБ на время сборки, остаётся ~0.35 ГБ) и память (~2.5 ГБ RAM+swap на пике сборки).
2. Ставит `ffmpeg`, `curl`, `git`, `python3-venv`, если их нет.
3. Создаёт системного пользователя `gigaam-asr` и `/opt/gigaam-asr/`, кладёт `server.py`.
4. Делает рабочий venv: `numpy`, `onnxruntime==1.31.0`, `sentencepiece`.
5. **Модели** (только если их ещё нет или указан `--force-model`):
   - качает оригинальные веса из первоисточников и **сверяет sha256 каждого файла до загрузки** (суммы зашиты в скрипт, раздел `SOURCES`):
     - GigaAM `v3_e2e_ctc.ckpt` + токенизатор — CDN авторов (`cdn.chatwm.opensmodel.sberdevices.ru/GigaAM`, тот же адрес, что в `gigaam/__init__.py`); пакет `gigaam` дополнительно сверяет свой md5;
     - VoxLingua107 — Hugging Face [`speechbrain/lang-id-voxlingua107-ecapa`](https://huggingface.co/speechbrain/lang-id-voxlingua107-ecapa), зафиксированная ревизия `0253049`;
   - во временном venv (torch 2.11 CPU, speechbrain 1.1.1, onnx, GigaAM на зафиксированном коммите, ~2 ГБ) конвертирует их в ONNX (`export_models.py`), GigaAM — ещё и в int8;
   - сверяет результат: `tokenizer.model` и `lid_labels.txt` должны совпасть побайтно; `lid.onnx` и `gigaam.int8.onnx` сравниваются с эталонной сборкой для информации (на другом железе/библиотеках файл может отличаться байтами, но работать так же — это решает смоук-тест);
   - удаляет временный venv и исходные веса.
6. Ставит `gigaam-asr.socket` + `gigaam-asr.service`, включает сокет.
7. Обновляет `/usr/local/bin/voice-to-text`, пишет `GIGAAM_ENABLED=1` в `/etc/voice-to-text.conf`.
8. Смоук-тест: русский пример от авторов GigaAM (тоже по sha256) → `/detect-language` должен сказать `ru`, `/asr` — вернуть текст.

Готовых моделей с чужих серверов скрипт не берёт. Если поднимаешь несколько своих серверов, можно один раз собрать и скопировать `/opt/gigaam-asr/models/*` на остальные (`scp`), потом прогнать установщик — он увидит модели и пропустит сборку.

Сборку лучше запускать, когда сервер не занят: экспорт GigaAM в ONNX на пике берёт ~2.5 ГБ RAM.

Другие путь/порт/пользователь: `INSTALL_DIR=... PORT=... SVC_USER=... sudo -E ./install-gigaam-asr.sh`. Пересобрать модели: `--force-model`. Удалить: `--uninstall`.

## 3. Татарский и другие языки (опционально)

GigaAM знает только русский. Всё, что не русское, роутер отдаёт в `tatar-asr` — модель [aadel4/omniASR-CTC-300M-v2](https://huggingface.co/aadel4/omniASR-CTC-300M-v2) (Omnilingual ASR 300M, Apache-2.0), сжатую до int8.

```bash
sudo /root/projects/claude-vps-starter/tatar-asr/install-tatar-asr.sh
```

Что делает: ставит `ffmpeg`/`python3-venv`, создаёт пользователя `tatar-asr` и `/opt/tatar-asr/`, venv с torch (CPU) и `transformers>=5.3,<6`, качает модель (~1.3 ГБ), квантует в int8 (несколько минут, ~2.5 ГБ RAM на пике), fp32-копию удаляет, ставит `tatar-asr.socket`/`.service`, пишет `TATAR_ENABLED=1` в `/etc/voice-to-text.conf`, прогоняет тест. Флаги те же: `--force-model`, `--uninstall`.

Без `tatar-asr` роутер отправляет всё в GigaAM без определения языка — для русскоязычного владельца этого достаточно; английская или татарская речь при этом распознается плохо.

В `CLAUDE.md` бота стоит держать строчку: татарский/английский транскрипт приходит строчными и без пунктуации — это нормально.

## 4. Роутер voice-to-text

Файл: `scripts/voice-to-text`, ставится **копией** в `/usr/local/bin/voice-to-text` (это делают `install.sh` и оба установщика).

```bash
voice-to-text <audio-file> [--lang auto|ru|tt|en|ar]
```

- stdout — только текст, stderr — строка о том, куда ушёл файл и сколько это заняло.
- Решения пишутся в syslog: `journalctl -t voice-to-text -n 20`.
- `--lang ru` — сразу GigaAM. `--lang tt|en|ar` — сразу Omnilingual (подсказки языка у него нет — одна многоязычная модель).
- Короткие (<2 с) русские фразы VoxLingua иногда не узнаёт — они уходят в Omnilingual и приходят без пунктуации. Нужна пунктуация — повтори с `--lang ru`.

Настройки — `/etc/voice-to-text.conf` (строки `KEY=value`, файл пишут установщики; переменные окружения с тем же именем важнее файла): `GIGAAM_ENABLED`, `GIGAAM_URL`, `TATAR_ENABLED`, `TATAR_URL`, `LID_TIMEOUT`, `GIGAAM_TIMEOUT`, `TATAR_TIMEOUT`, `VTT_CONF_MIN_RU` (порог русского, по умолчанию 0.60). Без файла: GigaAM на `127.0.0.1:9003` включён, татарский выключен.

**Почему `/usr/local/bin`, а не `/root/.claude/scripts`:** у `/root` права 700. Если на сервере несколько ботов (docs/05), другие пользователи не могут выполнить скрипт из `/root/...`, даже по симлинку. После `git pull` прогони `install.sh` ещё раз, чтобы обновить копию.

Старое имя `scripts/whisper_via_api.sh` оставлено для совместимости со старыми `CLAUDE.md` — это тонкая обёртка `exec /usr/local/bin/voice-to-text "$@"`. С Whisper она больше не связана.

Проверить:

```bash
voice-to-text /path/to/voice.oga
journalctl -t voice-to-text -n 5
systemctl status gigaam-asr.socket tatar-asr.socket   # service в статусе inactive между запросами — норма
journalctl -u gigaam-asr.service -n 50
```

## 5. Куда кладутся голосовые от Telegram

plugin:telegram сохраняет вложения в `/root/.claude/channels/telegram/inbox/*.oga`. Claude получает `attachment_file_id` в событии, вызывает `download_attachment`, получает путь — и пробрасывает его в `voice-to-text` через `Bash`. Это поведение фиксируется в `CLAUDE.md`.

## 6. Протокол ответа на голосовуху

В `CLAUDE.md`:

1. Первое сообщение — транскрипт inline monospace (одинарные бэктики): `` `что ты услышал` ``. Это страхует от ошибок распознавания — ты видишь текст до того, как Claude начал действовать.
2. Дальше — обычный flow (план, прогресс, финал) или сразу ответ, если задача короткая.

Длинные записи (5+ минут) — расшифровка может занять минуту-две: в `CLAUDE.md` предписано запускать её через `Bash run_in_background` и сразу отвечать «расшифровываю».

## 7. Если не работает

```bash
systemctl status gigaam-asr.socket          # должен быть active (listening)
sudo systemctl restart gigaam-asr.socket
journalctl -u gigaam-asr.service -n 50      # ошибки загрузки моделей, OOM
ls -la /opt/gigaam-asr/models/              # lid.onnx, lid_labels.txt, gigaam.int8.onnx, tokenizer.model
```

Модели битые или пропали — `sudo .../gigaam-asr/install-gigaam-asr.sh --force-model`.

## 8. Переезд с Whisper (если ставил шаблон до октября 2026)

Раньше голос распознавал Docker-контейнер `whisper-asr` (`onerahmet/openai-whisper-asr-webservice`, порт 9000). Теперь он не нужен.

1. `git pull`, затем `sudo ./scripts/install.sh` — поставит `gigaam-asr` (сборка моделей ~10–20 мин) и новый роутер. Можно и отдельно: `sudo gigaam-asr/install-gigaam-asr.sh`.
2. Если стоял `tatar-asr` — прогони `sudo tatar-asr/install-tatar-asr.sh`, чтобы он оставил новый роутер.
3. Проверь: `voice-to-text <файл>` и `journalctl -t voice-to-text -n 5` — в логе должно быть `→ gigaam`.
4. Найди, кто ещё ходит в Whisper напрямую: `grep -rn "127.0.0.1:9000\|whisper" /root/CLAUDE.md /home/*/CLAUDE.md /etc/cron* 2>/dev/null; crontab -l | grep -i whisper`. Замени вызовы на `/usr/local/bin/voice-to-text <path>`, обнови раздел «Голосовые сообщения» в `CLAUDE.md` по `templates/CLAUDE.md`.
5. Удали Whisper (освобождает ~8 ГБ диска и ~0.5 ГБ RAM):

```bash
docker rm -f whisper-asr
docker image rm onerahmet/openai-whisper-asr-webservice:latest
rm -rf /root/projects/claude-vps-starter/whisper-asr     # старые cache/ и models/ контейнера
rm -rf /root/.cache/whisper                              # модели старого CLI openai-whisper, если ставил
# у других пользователей-ботов тоже может быть ~/.cache/whisper — проверь, что их скрипты его не используют
```

Если Docker больше ни для чего не нужен — его можно оставить, он ничему не мешает.

## 9. Лицензии моделей

- **GigaAM** (Sber, [salute-developers/GigaAM](https://github.com/salute-developers/GigaAM)) — MIT.
- **VoxLingua107 ECAPA** ([speechbrain/lang-id-voxlingua107-ecapa](https://huggingface.co/speechbrain/lang-id-voxlingua107-ecapa)) — модель Apache-2.0; обучена на датасете VoxLingua107 (CC BY 4.0). Код SpeechBrain — Apache-2.0.
- **Omnilingual ASR 300M** ([aadel4/omniASR-CTC-300M-v2](https://huggingface.co/aadel4/omniASR-CTC-300M-v2)) — Apache-2.0.

Все три допускают коммерческое использование; при распространении сохраняй уведомления об авторстве.
