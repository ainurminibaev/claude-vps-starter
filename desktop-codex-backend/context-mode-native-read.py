"""Patch context-mode guidance without changing permissions or execution code."""
from pathlib import Path
import argparse
import shutil

GUIDANCE = ('Use native Read to understand project files, including instructions, source code, '
            'briefs and templates. Native file access lets Claude Code discover scoped CLAUDE.md '
            'and rules. Read required instructions and referenced workflows completely, in chunks '
            'if necessary; search excerpts do not replace them. Use context-mode to compute, filter '
            'or aggregate large data after applicable project instructions are loaded. '
            'These rules apply equally to main agents and subagents.')

def patch(root, backup=None):
    paths = [root/'hooks/routing-block.mjs', root/'skills/context-mode/SKILL.md', root/'configs/claude-code/CLAUDE.md']
    for path in paths:
        text = path.read_text()
        if 'native-file-reading-v1' in text:
            continue
        if backup:
            target = backup/path.relative_to(root)
            if target.exists():
                raise ValueError(f'Refusing to overwrite backup: {target}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        if path.suffix == '.mjs':
            text = text.replace('Raw tool output floods your context window. You MUST use context-mode MCP tools to keep raw data in the sandbox.', GUIDANCE)
            text = text.replace('- Primary tool for research. Runs all commands, auto-indexes, and searches.', '- For large data collection and processing after project instructions are loaded.')
            text = text.replace('- Use for all follow-up questions. ONE call, many queries.', '- Search indexed data; use native Read for project instructions and source files.')
            text = text.replace('- DO NOT use Read for analysis (use execute_file). Read IS correct for files you intend to Edit.', '- '+GUIDANCE)
            if 'export function createReadGuidance(t)' in text:
                start = text.index('export function createReadGuidance(t)')
                end = text.index('\nexport function ', start+1)
                text = text[:start]+'export function createReadGuidance(t) {\n  return '+repr('<context_guidance>'+GUIDANCE+'</context_guidance>')+';\n}\n'+text[end:]
                text = text.replace('  <tool_selection_hierarchy>', '  <native_file_reading>\n    '+GUIDANCE+'\n  </native_file_reading>\n  <tool_selection_hierarchy>')
                text = text.replace('- Primary research tool.', '- Large-data research tool after applicable project instructions are loaded.')
                text = text.replace('- You want to analyze, summarize, or extract from a file → use ${t("ctx_execute_file")}. Read stays correct when you intend to Edit the file (Edit needs the exact bytes in your conversation to match against).', '- '+GUIDANCE)
            else:
                start = text.index('export const READ_GUIDANCE = ')
                end = text.index('\n', start)
                text = text[:start] + 'export const READ_GUIDANCE = '+repr('<context_guidance>'+GUIDANCE+'</context_guidance>')+';' + text[end:]
            text = '// native-file-reading-v1\n'+text
        elif path.name == 'SKILL.md':
            text = text.replace('Default to context-mode for ALL commands. Only use Bash for guaranteed-small-output operations.', GUIDANCE)
            text = text.replace('**Everything else → `ctx_execute` or `ctx_execute_file`.**', '**For large data processing → `ctx_execute` or `ctx_execute_file`.**')
            text = text.replace('Any command that reads, queries, fetches, lists, logs, tests, builds, diffs, inspects, or calls an external service.', 'Use native Read for project files and instructions; use context-mode for large data extraction and aggregation.')
            text = text.replace('**When uncertain, use context-mode.**', '**When reading project files or instructions, use native Read.**')
            text = text.replace('├── Output MIGHT be large or you\'re UNSURE?', '├── Project file, instruction, brief, source code or template?\n│   └── Use native Read; read required instructions completely\n│\n├── Processing large data after loading applicable project instructions?')
            text = text.replace('4. **For files you need to EDIT**: Use the normal Read tool. context-mode is for analysis, not editing.', '4. '+GUIDANCE)
            text = text.replace('Everything else goes through context-mode.', 'Native Read remains appropriate for understanding project files and instructions.')
            text += '\n<!-- native-file-reading-v1 -->\n'
        else:
            start = text.index('### Read (for analysis)')
            end = text.index('### Grep', start)
            text = text[:start]+'### Native file reading\n'+GUIDANCE+'\n\n'+text[end:]
            text += '\n<!-- native-file-reading-v1 -->\n'
        path.write_text(text)
        print(path)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('plugin', type=Path)
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    patch(args.plugin, args.backup_dir)
