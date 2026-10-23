// ── Доп. инструменты для групп и каналов ──
// Штатный плагин умеет только reply/react/edit_message своих сообщений. Для каналов
// бот ходил в Bot API через curl, и на каждый канал и метод нужно было отдельное
// разрешение в settings.json. Здесь те же операции как MCP-инструменты — с той же
// проверкой доступа, что у reply: только чаты из allowlist / подключённые группы.
const VPS_EXTRA_TOOLS = [
  {
    name: 'edit_caption',
    description: 'Edit the caption of a photo/video/document post (e.g. a channel post). In channels the bot needs the "edit messages of others" admin right to edit posts it did not send.',
    inputSchema: {
      type: 'object',
      properties: {
        chat_id: { type: 'string' },
        message_id: { type: 'string' },
        caption: { type: 'string' },
        format: { type: 'string', enum: ['text', 'markdownv2'] },
      },
      required: ['chat_id', 'message_id', 'caption'],
    },
  },
  {
    name: 'forward_message',
    description: 'Forward a message from one allowed chat to another (shows "Forwarded from").',
    inputSchema: {
      type: 'object',
      properties: {
        from_chat_id: { type: 'string' },
        message_id: { type: 'string' },
        to_chat_id: { type: 'string' },
        topic_id: { type: 'string', description: 'Forum topic in the target chat (optional).' },
      },
      required: ['from_chat_id', 'message_id', 'to_chat_id'],
    },
  },
  {
    name: 'copy_message',
    description: 'Copy a message (with media) from one allowed chat to another without the "Forwarded from" header. Optionally replace the caption.',
    inputSchema: {
      type: 'object',
      properties: {
        from_chat_id: { type: 'string' },
        message_id: { type: 'string' },
        to_chat_id: { type: 'string' },
        topic_id: { type: 'string', description: 'Forum topic in the target chat (optional).' },
        caption: { type: 'string', description: 'New caption (optional).' },
      },
      required: ['from_chat_id', 'message_id', 'to_chat_id'],
    },
  },
  {
    name: 'post_to_topic',
    description: 'Send a message (and optional files) straight into a forum topic by its topic_id — no need to reply to a message from that topic.',
    inputSchema: {
      type: 'object',
      properties: {
        chat_id: { type: 'string' },
        topic_id: { type: 'string' },
        text: { type: 'string' },
        files: { type: 'array', items: { type: 'string' } },
        format: { type: 'string', enum: ['text', 'markdownv2'] },
      },
      required: ['chat_id', 'topic_id', 'text'],
    },
  },
]

async function vpsCallExtra(name: string, args: Record<string, unknown>) {
  const s = (k: string) => (args[k] == null ? undefined : String(args[k]))
  const parseMode = s('format') === 'markdownv2' ? { parse_mode: 'MarkdownV2' as const } : {}
  const thread = s('topic_id') ? { message_thread_id: Number(s('topic_id')) } : {}
  const ok = (text: string) => ({ content: [{ type: 'text', text }] })

  if (name === 'edit_caption') {
    assertAllowedChat(s('chat_id')!)
    await bot.api.editMessageCaption(s('chat_id')!, Number(s('message_id')), { caption: s('caption'), ...parseMode })
    return ok(`caption edited (id: ${s('message_id')})`)
  }
  if (name === 'forward_message' || name === 'copy_message') {
    assertAllowedChat(s('from_chat_id')!)
    assertAllowedChat(s('to_chat_id')!)
    if (name === 'forward_message') {
      const m = await bot.api.forwardMessage(s('to_chat_id')!, s('from_chat_id')!, Number(s('message_id')), thread)
      return ok(`forwarded (id: ${m.message_id})`)
    }
    const m = await bot.api.copyMessage(s('to_chat_id')!, s('from_chat_id')!, Number(s('message_id')), {
      ...thread,
      ...(s('caption') != null ? { caption: s('caption') } : {}),
    })
    return ok(`copied (id: ${m.message_id})`)
  }
  if (name === 'post_to_topic') {
    const chat_id = s('chat_id')!
    assertAllowedChat(chat_id)
    const files = (args.files as string[] | undefined) ?? []
    for (const f of files) {
      assertSendable(f)
      if (statSync(f).size > MAX_ATTACHMENT_BYTES) throw new Error(`file too large: ${f}`)
    }
    const ids: number[] = []
    const sent = await bot.api.sendMessage(chat_id, s('text')!, { ...thread, ...parseMode })
    ids.push(sent.message_id)
    for (const f of files) {
      const input = new InputFile(f)
      const m = PHOTO_EXTS.has(extname(f).toLowerCase())
        ? await bot.api.sendPhoto(chat_id, input, thread)
        : await bot.api.sendDocument(chat_id, input, thread)
      ids.push(m.message_id)
    }
    return ok(`sent to topic ${s('topic_id')} (ids: ${ids.join(', ')})`)
  }
  throw new Error(`unknown extra tool: ${name}`)
}

