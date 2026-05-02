import { writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";

if (process.cwd().includes("plugins/cache/claude-plugins-official/telegram") || process.argv.some(a => a.includes("plugins/cache/claude-plugins-official/telegram"))) {
  const hb = join(homedir(), ".claude", "channels", "telegram", "bot.heartbeat");
  const tick = () => { try { writeFileSync(hb, String(Date.now())); } catch {} };
  try { mkdirSync(dirname(hb), { recursive: true }); } catch {}
  tick();
  setInterval(tick, 10_000).unref();
}
