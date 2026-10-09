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
MARKER = "[vps-starter patch: group-autojoin v3]"

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


def main(path: str) -> int:
    orig = path + ".vps-orig"
    current = open(path, encoding="utf-8").read()

    if MARKER in current:
        print(f"OK уже стоит: {path}")
        return 0

    # Есть старая версия нашего патча — начинаем с чистого оригинала.
    if "[vps-starter patch:" in current and os.path.exists(orig):
        current = open(orig, encoding="utf-8").read()

    for anchor, name in ((ANCHOR_BLOCK, "block"), (ANCHOR_META, "meta"), (ANCHOR_CONTENT, "content")):
        if current.count(anchor) != 1:
            print(f"FAIL якорь '{name}' найден {current.count(anchor)} раз: {path}")
            return 2

    block = open(os.path.join(HERE, "group-autojoin.ts"), encoding="utf-8").read()
    patched = current.replace(ANCHOR_BLOCK, block + "\n" + ANCHOR_BLOCK, 1)
    patched = patched.replace(ANCHOR_META, META_ADD, 1)
    patched = patched.replace(ANCHOR_CONTENT, CONTENT_NEW, 1)

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
