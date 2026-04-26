#!/bin/bash
# Добавить второй инстанс Claude для другого Linux-юзера.
# Usage: ./add-second-user.sh <username>
#
# Что делает:
# 1. Создаёт Linux-юзера (если ещё нет).
# 2. Клонирует/копирует этот репо в его домашку.
# 3. Добавляет сессию в watchdog SESSIONS array (если ещё нет).
#
# Что НЕ делает (сделай руками под его учёткой):
# - установка Bun / Claude Code в его $HOME
# - конфигурация plugin:telegram (новый бот, новый токен, /telegram:configure)
# - получение его ANTHROPIC_API_KEY
#
# Смотри docs/05-multi-user.md.

set -e

if [ -z "${1:-}" ]; then
  echo "usage: $0 <username>"
  exit 1
fi

USER="$1"
HOME_DIR="/home/$USER"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WATCHDOG="$REPO_DIR/scripts/claude-tg-watchdog.sh"
SESSION_NAME="claude-tg-$USER"

echo "=== Adding second user: $USER ==="
echo

# 1. Юзер
if id "$USER" >/dev/null 2>&1; then
  echo "[ok] Linux-юзер $USER уже существует"
else
  echo "[>] useradd -m -s /bin/bash $USER"
  useradd -m -s /bin/bash "$USER"
  echo "[+] Создан юзер $USER. Задай пароль:"
  passwd "$USER"
fi
echo

# 2. Скопировать репо в домашку
TARGET="$HOME_DIR/claude-vps-starter"
if [ -d "$TARGET" ]; then
  echo "[ok] $TARGET уже существует"
else
  echo "[>] cp -r $REPO_DIR $TARGET"
  cp -r "$REPO_DIR" "$TARGET"
  chown -R "$USER:$USER" "$TARGET"
  echo "[+] Репо скопирован в $TARGET"
fi
echo

# 3. Добавить в watchdog SESSIONS
if grep -qF "$SESSION_NAME|$USER|$HOME_DIR" "$WATCHDOG"; then
  echo "[ok] $SESSION_NAME уже в watchdog SESSIONS"
else
  SESSION_UUID=$(uuidgen)
  echo "[>] добавляю $SESSION_NAME в $WATCHDOG (session-id: $SESSION_UUID)"
  # вставляем новую строку перед закрывающей скобкой массива SESSIONS=( ... )
  sed -i "/^SESSIONS=(/,/^)/ { /^)/ i\\
  \"$SESSION_NAME|$USER|$HOME_DIR|$SESSION_UUID\"
  }" "$WATCHDOG"
  echo "[+] Сессия добавлена. Проверь:"
  grep -A 10 "^SESSIONS=(" "$WATCHDOG" | head -15
fi
echo

# 4. Что руками
cat <<EOF
=== Что сделать руками под $USER ===

  su - $USER

Внутри его shell'а:

  # Bun
  curl -fsSL https://bun.sh/install | bash
  export PATH=\$HOME/.bun/bin:\$PATH
  echo 'export PATH=\$HOME/.bun/bin:\$PATH' >> ~/.bashrc

  # Claude Code — если глобально через npm, он уже доступен.
  # Иначе установи в его домашку тем же способом, что и у тебя.

  # Его ANTHROPIC_API_KEY
  echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc
  source ~/.bashrc

  # Первый запуск
  claude

  # plugin:telegram
  claude plugin install plugin:telegram@claude-plugins-official
  claude
  # внутри:
  #   /telegram:configure   (отдельный бот у @BotFather, отдельный токен)
  #   /telegram:access      (одобрить его Telegram user id)

  # CLAUDE.md
  cp $TARGET/templates/CLAUDE.md $HOME_DIR/CLAUDE.md
  # отредактируй под него

Watchdog уже знает про сессию $SESSION_NAME и поднимет её в следующий тик cron (до минуты).

EOF
