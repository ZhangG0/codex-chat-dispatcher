"""Run the original behavioral assertions against the TypeScript CLI."""
import json
from pathlib import Path
import subprocess
import tempfile

SCRIPT = Path(__file__).resolve().parents[1] / 'dispatch.ts'
class Conflict(ValueError): pass

def invoke(config, entry=None, host=None, thread=None, command=()):
    args=['node',str(SCRIPT),'--config',str(config)]
    for name,value in [('entry',entry),('caller-host',host),('caller-thread',thread)]:
        if value is not None: args += ['--'+name,value]
    result=subprocess.run(args+list(command),capture_output=True,text=True)
    if result.returncode: raise Conflict(json.loads(result.stderr.splitlines()[-1])['error'])
    return json.loads(result.stdout)

def read_config(path):
    data=invoke(path,command=['check'])
    return json.loads(Path(path).read_text()),Path(data['root']),Path(data['state'])

class Ledger:
    def __init__(self,config,entry=None,caller_host=None,caller_thread=None):
        self.args=(config,entry,caller_host,caller_thread)
        self.db=self
        code="import {Ledger} from " + json.dumps(SCRIPT.as_uri()) + "; try { const l=new Ledger(...JSON.parse(process.argv[1])); l.close(); } catch(e) { console.log(e.message); process.exit(1); }"
        result=subprocess.run(['node','--input-type=module','-e',code,json.dumps([str(config),entry,caller_host,caller_thread])],capture_output=True,text=True)
        if result.returncode: raise Conflict(result.stdout.strip())
    def close(self): pass
    def call(self,*args): return invoke(*self.args,command=args)
    def summary(self): return self.call('list')
    def get(self,task): return self.call('show',task)
    def create(self,key,project,title,scope,authorization,resources=()):
        args=['create','--key',key,'--project',project,'--title',title,'--scope',scope,'--authorization',authorization]
        for resource in resources: args+=['--resource',resource]
        return self.call(*args)
    def prepare(self,task): return self.call('prepare',task)
    def receipt(self,task,attempt,receipt): return self.call('sent',task,'--attempt',attempt,'--receipt',receipt)
    def uncertain(self,task,evidence): return self.call('uncertain',task,'--evidence',evidence)
    def reconcile_unsent(self,task,evidence): return self.call('reconcile-unsent',task,'--evidence',evidence)
    def progress(self,task,stage,evidence): return self.call('progress',task,'--stage',stage,'--evidence',evidence)
    def finish(self,task,evidence): return self.call('finish',task,'--evidence',evidence)
    def cancel(self,task,evidence): return self.call('cancel',task,'--evidence',evidence)
    def merge_cursors(self,polls):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'polls.json';p.write_text(json.dumps({'polls':polls}))
            return self.call('merge-polls',str(p))
    def visible_cursors(self): return self.summary()['cursors']
