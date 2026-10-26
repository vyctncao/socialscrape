"""Notification timing is user-controlled: hover or keyboard focus pauses dismissal."""
import json
import subprocess
from pathlib import Path


def test_toasts_pause_and_keep_queue_slot_until_exit_finishes():
    script = Path('socialscrape/static/portal.js').read_text()
    start = script.index('const toastQueue')
    end = script.index('\nfunction layout', start)
    harness = r'''
class Element { constructor(){ this.listeners={}; this.children=[]; this.classList={values:new Set(), add:v=>this.classList.values.add(v), remove:v=>this.classList.values.delete(v)}; this.isConnected=true; }
 addEventListener(n,f){this.listeners[n]=f} append(x){x.isConnected=true;this.children.push(x)} remove(){this.isConnected=false}
 setAttribute(){} matches(){return false} contains(x){return x===this.focused||this.children.includes(x)} querySelector(){return {textContent:''}} }
const timers=[]; globalThis.setTimeout=(f,ms)=>(timers.push({f,ms}), timers.length); globalThis.clearTimeout=id=>{if(timers[id-1]) timers[id-1].cancelled=true};
globalThis.requestAnimationFrame=f=>f(); globalThis.queueMicrotask=f=>f(); globalThis.performance={now:()=>0}; globalThis.matchMedia=()=>({matches:false});
globalThis.document={activeElement:null, createElement:()=>new Element()}; const region=new Element(); globalThis.$=()=>region; globalThis.icon=()=>''; globalThis.esc=s=>s;
''' + script[start:end] + r'''
toast('first'); toast('second', {type:'info'}); toast('third', {type:'info'}); toast('queued', {type:'info'});
const first=region.children[0]; first.listeners.focusin(); const pausedTimer=timers[0]; const pausedQueue=toastQueue.length;
first.children.at(-1).listeners.click(); const queuedBeforeExit=toastQueue.length; timers.at(-1).f();
console.log(JSON.stringify({pausedTimerCancelled:pausedTimer.cancelled, pausedQueue, queuedBeforeExit, queuedAfterExit:toastQueue.length, visible:region.children.filter(x=>x.isConnected).length}));
'''
    result = json.loads(subprocess.check_output(['node', '-e', harness], text=True))
    assert result == {'pausedTimerCancelled': True, 'pausedQueue': 1, 'queuedBeforeExit': 1, 'queuedAfterExit': 0, 'visible': 3}
