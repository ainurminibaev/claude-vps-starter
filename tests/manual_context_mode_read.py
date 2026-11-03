from pathlib import Path
import json,os,sys,subprocess,tempfile
home=Path.home();root=Path(tempfile.mkdtemp(prefix='native-read-routing-'))
node=home/'.nvm/versions/node/v22.15.0/bin/node'
current=home/'.claude/plugins/cache/context-mode/context-mode/1.0.25'
candidate=Path('/private/tmp/context-mode-native-read-candidate')
for variant,child in [(v,False) for v in ['current','candidate','none'] for _ in range(2)]+[('candidate',True),('none',True)]:
 case=root/(variant+('-child-' if child else '-main-')+str(len(list(root.iterdir()))));project=case/'project';nested=project/'nested';nested.mkdir(parents=True)
 (project/'CLAUDE.md').write_text('This is a local planning workspace. Follow instructions applicable to the files you work on.\n')
 (nested/'CLAUDE.md').write_text('Before creating a deliverable in this directory, read PROCESS.md completely and follow it.\n')
 (nested/'PROCESS.md').write_text('The deliverable must contain exactly the template fields plus approval_code. Set approval_code to LOCAL_RULE_8462. Keep the template layout. Do not add commentary, caveats, sources, or other fields.\n')
 (nested/'brief.txt').write_text('Customer: Example. Budget: 450. Prepare the plan.\n')
 (nested/'template.json').write_text('{"customer":"", "budget":0}\n')
 config=case/'config';config.mkdir();audit=case/'audit.jsonl'
 hook=case/'audit.py';hook.write_text('import sys,json\nfrom pathlib import Path\nwith Path('+repr(str(audit))+').open("a") as f:f.write(json.dumps(json.load(sys.stdin))+"\\n")\n')
 hooks={'InstructionsLoaded':[{'hooks':[{'type':'command','command':sys.executable+' '+str(hook)}]}]}
 plugin=candidate if variant=='candidate' else current
 if variant!='none':
  hooks['SessionStart']=[{'hooks':[{'type':'command','command':str(node)+' '+str(plugin/'hooks/sessionstart.mjs')}]}]
  hooks['PreToolUse']=[{'matcher':'Read|Agent|Task|Bash|Grep','hooks':[{'type':'command','command':str(node)+' '+str(plugin/'hooks/pretooluse.mjs')}]}]
 (config/'settings.json').write_text(json.dumps({'hooks':hooks}))
 env=os.environ.copy()
 for k in list(env):
  if k.startswith('ANTHROPIC_') or k in ['CLAUDE_CODE_OAUTH_TOKEN','CLAUDE_CODE_SIMPLE']:env.pop(k,None)
 env.update(CLAUDE_CONFIG_DIR=str(config),CLAUDE_PLUGIN_ROOT=str(plugin),CLAUDE_PROJECT_DIR=str(project),ANTHROPIC_BASE_URL='http://127.0.0.1:18418',ANTHROPIC_AUTH_TOKEN=(home/'.config/claude-backend/client-key').read_text().strip(),ANTHROPIC_API_KEY='',DISABLE_AUTOUPDATER='1')
 mcp={'mcpServers':{}} if variant=='none' else {'mcpServers':{'plugin_context-mode_context-mode':{'command':str(node),'args':[str(plugin/'start.mjs')]}}}
 prompt='Prepare nested/result.json from nested/brief.txt using nested/template.json. Follow project instructions. This is an isolated local test; do not access external services.'
 if child:prompt='Delegate this task to a general-purpose subagent, then report its result without editing the files yourself: '+prompt
 args=[str(home/'.local/bin/claude'),'-p',prompt,'--model','gpt-6-astra','--effort','medium','--tools','Read,Write,Edit,Glob,Grep,Agent','--allowedTools','Read,Write,Edit,Glob,Grep,Agent,mcp__plugin_context-mode_context-mode','--strict-mcp-config','--mcp-config',json.dumps(mcp),'--no-session-persistence','--output-format','stream-json','--verbose','--debug-file',str(case/'debug.log')]
 r=subprocess.run(args,cwd=project,env=env,text=True,capture_output=True,timeout=180)
 (case/'output.jsonl').write_text(r.stdout);(case/'stderr.log').write_text(r.stderr)
 events=[json.loads(l) for l in audit.read_text().splitlines()] if audit.exists() else []
 loaded=any(Path(e.get('file_path','')).resolve()==(nested/'CLAUDE.md').resolve() for e in events)
 result=None
 try:result=json.loads((nested/'result.json').read_text())
 except (OSError,ValueError):pass
 calls=[]
 for l in r.stdout.splitlines():
  try:d=json.loads(l)
  except:continue
  for c in d.get('message',{}).get('content',[]):
   if c.get('type')=='tool_use':calls.append({'tool':c.get('name'),'child':bool(d.get('parent_tool_use_id'))})
 print(json.dumps({'variant':variant,'child':child,'exit':r.returncode,'loaded':loaded,'passed':result=={'customer':'Example','budget':450,'approval_code':'LOCAL_RULE_8462'},'result':result,'calls':calls,'artifact':str(case)}),flush=True)
