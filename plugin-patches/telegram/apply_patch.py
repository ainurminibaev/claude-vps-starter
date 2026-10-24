#!/usr/bin/env python3
"""Накладывает патчи vps-starter на server.ts telegram-плагина.

Использование: apply_patch.py <path/to/server.ts>
Идемпотентно: если патч текущей версии уже стоит — ничего не делает.
Перед первой правкой сохраняет нетронутый оригинал как server.ts.vps-orig
и при повторном наложении всегда стартует с него (так обновляется версия патча).

Коды выхода: 0 — патч стоит (наложен сейчас или раньше),
             2 — якорь не найден (плагин изменился, патч не наложен, файл не тронут).
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER = "[vps-starter patch: group-autojoin v5]"
MARKER_LOCAL_API = "[vps-starter patch: local-bot-api v1]"

# Локальный Bot API сервер (файлы до 2 ГБ): адрес из TELEGRAM_API_ROOT, по умолчанию облако.
ANCHOR_BOT = "const bot = new Bot(TOKEN)\n"
BOT_NEW = """// [vps-starter patch: local-bot-api v1] Адрес Bot API — из TELEGRAM_API_ROOT (локальный сервер
// с лимитом файлов 2 ГБ вместо 20 МБ), иначе облако Telegram.
const VPS_API_ROOT = (process.env.TELEGRAM_API_ROOT ?? '').replace(/\\/+$/, '') || 'https://api.telegram.org'
const bot = new Bot(TOKEN, { client: { apiRoot: VPS_API_ROOT } })
// Локальный сервер в getFile отдаёт абсолютный путь на своём диске — в ссылку идёт часть после токена.
function vpsFileUrl(filePath: string): string {
  const marker = `/${TOKEN}/`
  const i = filePath.indexOf(marker)
  const rel = filePath.startsWith('/') && i >= 0 ? filePath.slice(i + marker.length) : filePath
  return `${VPS_API_ROOT}/file/bot${TOKEN}/${rel}`
}
"""
ANCHOR_FILE_URL = "const url = `https://api.telegram.org/file/bot${TOKEN}/${file.file_path}`"
FILE_URL_NEW = "const url = vpsFileUrl(file.file_path)"
# Большой файл не держим в памяти целиком — пишем потоком.
ANCHOR_DL_BUF = ("        const buf = Buffer.from(await res.arrayBuffer())\n"
                 "        // file_path is from Telegram (trusted), but strip to safe chars anyway\n")
DL_BUF_NEW = "        // file_path is from Telegram (trusted), but strip to safe chars anyway\n"
ANCHOR_DL_WRITE = "        writeFileSync(path, buf)\n        return { content: [{ type: 'text', text: path }] }\n"
DL_WRITE_NEW = "        await Bun.write(path, res)\n        return { content: [{ type: 'text', text: path }] }\n"

# Блок обработчиков вставляется перед обработчиком permission-кнопок:
# наш callback-обработчик должен стоять раньше, чтобы перехватить grp:* и
# передать остальное дальше через next().
ANCHOR_BLOCK = "// Inline-button handler for permission requests."

# Имя чата и тема — в meta входящего сообщения, чтобы бот знал, откуда оно.
ANCHOR_META = "        user_id: String(from.id),\n"
META_ADD = (
    "        user_id: String(from.id),\n"
    "        ...(ctx.chat && ctx.chat.type !== 'private' && 'title' in ctx.chat && ctx.chat.title\n"
    "          ? { chat_title: safeName(ctx.chat.title) } : {}),\n"
    "        ...(ctx.message?.is_topic_message && ctx.message.message_thread_id != null\n"
    "          ? { topic_id: String(ctx.message.message_thread_id) } : {}),\n"
    "        ...((n => n ? { topic_name: n } : {})(vpsTopicName(ctx))),\n"
)


# К обращению в группе прикладываем невиденную переписку (контекст).
ANCHOR_CONTENT = "      content: text,\n"
CONTENT_NEW = "      content: vpsWithContext(ctx, text),\n"

# Доп. инструменты (edit_caption, forward/copy_message, post_to_topic): определения —
# ДО списка инструментов (плагин делает await mcp.connect раньше основного блока),
# добавление в список и обработка в switch.
ANCHOR_TOOLS_DEF = "mcp.setRequestHandler(ListToolsRequestSchema, async () => ({\n"
ANCHOR_TOOLS_LIST = "  ],\n}))\n\nmcp.setRequestHandler(CallToolRequestSchema"
TOOLS_LIST_NEW = "    ...VPS_EXTRA_TOOLS,\n  ],\n}))\n\nmcp.setRequestHandler(CallToolRequestSchema"
ANCHOR_CALL = "      default:\n        return {\n          content: [{ type: 'text', text: `unknown tool: ${req.params.name}` }],"
CALL_ADD = (
    "      case 'edit_caption':\n"
    "      case 'forward_message':\n"
    "      case 'copy_message':\n"
    "      case 'post_to_topic':\n"
    "        return await vpsCallExtra(req.params.name, args)\n"
)


def main(path: str) -> int:
    orig = path + ".vps-orig"
    current = open(path, encoding="utf-8").read()

    if MARKER in current and MARKER_LOCAL_API in current:
        print(f"OK уже стоит: {path}")
        return 0

    # Есть старая версия нашего патча — начинаем с чистого оригинала.
    if "[vps-starter patch:" in current and os.path.exists(orig):
        current = open(orig, encoding="utf-8").read()

    for anchor, name in ((ANCHOR_BLOCK, "block"), (ANCHOR_META, "meta"), (ANCHOR_CONTENT, "content"),
                         (ANCHOR_TOOLS_DEF, "tools-def"), (ANCHOR_TOOLS_LIST, "tools-list"), (ANCHOR_CALL, "call")):
        if current.count(anchor) != 1:
            print(f"FAIL якорь '{name}' найден {current.count(anchor)} раз: {path}")
            return 2
    for anchor, name, n in ((ANCHOR_BOT, "bot", 1), (ANCHOR_FILE_URL, "file-url", 2),
                            (ANCHOR_DL_BUF, "dl-buf", 1), (ANCHOR_DL_WRITE, "dl-write", 1)):
        if current.count(anchor) != n:
            print(f"FAIL якорь '{name}' найден {current.count(anchor)} раз (ждали {n}): {path}")
            return 2

    block = open(os.path.join(HERE, "group-autojoin.ts"), encoding="utf-8").read()
    patched = current.replace(ANCHOR_BLOCK, block + "\n" + ANCHOR_BLOCK, 1)
    patched = patched.replace(ANCHOR_META, META_ADD, 1)
    patched = patched.replace(ANCHOR_CONTENT, CONTENT_NEW, 1)
    extra = open(os.path.join(HERE, "extra-tools.ts"), encoding="utf-8").read()
    patched = patched.replace(ANCHOR_TOOLS_DEF, extra + ANCHOR_TOOLS_DEF, 1)
    patched = patched.replace(ANCHOR_TOOLS_LIST, TOOLS_LIST_NEW, 1)
    patched = patched.replace(ANCHOR_CALL, CALL_ADD + ANCHOR_CALL, 1)
    patched = patched.replace(ANCHOR_BOT, BOT_NEW, 1)
    patched = patched.replace(ANCHOR_FILE_URL, FILE_URL_NEW)
    patched = patched.replace(ANCHOR_DL_BUF, DL_BUF_NEW, 1)
    patched = patched.replace(ANCHOR_DL_WRITE, DL_WRITE_NEW, 1)

    if not os.path.exists(orig):
        shutil.copy2(path, orig)
    st = os.stat(path)
    tmp = path + ".vps-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(patched)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode)
    os.replace(tmp, path)
    print(f"PATCHED {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
