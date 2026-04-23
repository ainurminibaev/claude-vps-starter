# 01. Установка на свежий VPS

Инструкция для Ubuntu 22.04 / 24.04. На Debian 12 работает с минимальными правками.

## 0. Войти как root

Если провайдер даёт root по SSH — ок. Если нет, любой юзер с sudo тоже подойдёт, просто подставляй `sudo` перед системными командами.

```bash
ssh root@<ip-твоего-vps>
```

## 1. Обновить систему

```bash
apt update && apt upgrade -y
apt install -y curl git tmux build-essential ca-certificates gnupg python3 python3-pip
```

## 2. Docker + docker compose plugin

Официальный путь (обходит старые версии из apt):

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker --version
docker compose version
```

## 3. Node.js 20 LTS (нужен для некоторых MCP)

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
node --version   # ожидается v20.x
```

## 4. Bun (рантайм для plugin:telegram MCP)

```bash
curl -fsSL https://bun.sh/install | bash
export PATH=/root/.bun/bin:$PATH
echo 'export PATH=/root/.bun/bin:$PATH' >> ~/.bashrc
bun --version
```

## 5. Claude Code

Текущий способ установки уточни на [docs.claude.com/claude-code](https://docs.claude.com/en/docs/claude-code). Вероятные варианты:

```bash
# Вариант A — официальный инсталлер
curl -fsSL https://claude.ai/install.sh | bash

# Вариант B — через npm (глобально)
npm install -g @anthropic-ai/claude-code
```

Проверить:

```bash
claude --version
```

## 6. API-ключ

Получи ключ в [console.anthropic.com](https://console.anthropic.com/) или используй Claude Pro/Max подписку (если твоя версия Claude Code её поддерживает).

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-...твой-ключ...' >> ~/.bashrc
source ~/.bashrc
```

Либо клади ключи в `/root/.env` и подгружай его в нужных местах — как удобнее.

## 7. Первый запуск Claude

```bash
claude
```

Пройди логин (через API-ключ или auth-flow), подтверди workspace trust. Дальше выйди (`/exit` или Ctrl+C пару раз).

## 8. Клонировать этот репо

```bash
mkdir -p /root/projects
cd /root/projects
git clone <url-этого-репо> claude-vps-starter
cd claude-vps-starter
```

## 9. Прогнать install.sh

```bash
./scripts/install.sh
```

Он:
- проверит, что Docker / Bun / tmux на месте,
- симлинканёт `claude-tg-watchdog.sh` в `/root/`,
- добавит в crontab запуск watchdog (если его там ещё нет),
- поднимет Whisper-контейнер,
- напечатает, что осталось сделать руками (Telegram, первый запуск сессии).

## 10. Дальше

- [02-telegram.md](02-telegram.md) — создать бота, поставить plugin:telegram, настроить allowlist
- [03-whisper.md](03-whisper.md) — проверить расшифровку голоса
- [04-watchdog.md](04-watchdog.md) — убедиться, что cron крутится
- Положить `templates/CLAUDE.md` в `/root/CLAUDE.md` и адаптировать под свои проекты

## Типичные грабли

- **Bun не найден внутри watchdog** — в скрипте в `tmux new-session` явно прописан `export PATH=/root/.bun/bin:$PATH`. Если у тебя Bun лежит в другом месте, поправь там.
- **Docker требует перелогина** — если запускаешь не от root, надо добавить юзера в группу `docker` и перелогиниться.
- **Claude Code не видит ANTHROPIC_API_KEY** — убедись, что переменная экспортирована в том же shell, где запускается `claude` и `tmux`.
