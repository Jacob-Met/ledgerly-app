import { loadPyodide, type PyodideInterface } from 'pyodide';
import {copyChargeChoices} from './intake-file';
import {retryableLoader} from './retryable-loader';
import {WorkspaceStore, type WorkspaceRecord} from './workspace-store';
import {decodeWorkspaceFile,encodeWorkspaceFile,type PortableWorkspace} from './workspace-file';
type Request={id:number;action:string;payload?:Record<string,unknown>};
const scope=self as unknown as DedicatedWorkerGlobalScope;
const workspace = new WorkspaceStore();
let revision:number|undefined;
let pendingImport:{token:string;revision:number;intakeKey:string;file:PortableWorkspace}|undefined;
const boot=retryableLoader<PyodideInterface>(async()=>{
  const py=await loadPyodide({indexURL:new URL('../pyodide/',scope.location.href).href});
  py.FS.mkdirTree('/demo/ledgerly');
  for(const name of ['__init__.py','agent.py','extract.py','paypal.py','email_input.py','recovery.py','draft_correction.py','statement.py']){
   const r=await fetch(new URL('../python/ledgerly/'+name,scope.location.href));if(!r.ok)throw new Error('Python source '+name+': HTTP '+r.status);
   py.FS.writeFile('/demo/ledgerly/'+name,await r.text());
  }
  for(const name of ['bridge.py','review.py','invoice_details.py','workspace.py']){
   const source=await fetch(new URL('../python/'+name,scope.location.href));if(!source.ok)throw new Error('Python demo '+name+': HTTP '+source.status);
   py.FS.writeFile('/demo/'+name,await source.text());
  }
  await py.runPythonAsync("import sys; sys.path.insert(0, '/demo'); import bridge, workspace");
  scope.postMessage({type:'ready'});return py;
});
function dispatch(py:PyodideInterface,action:string,payload:Record<string,unknown>={}):any {
  py.globals.set('request_json',JSON.stringify({action,...payload}));
  try { return JSON.parse(String(py.runPython('bridge.handle_json(request_json)'))); }
  finally { py.globals.delete('request_json'); }
}
function checkpoint(py:PyodideInterface):string {
  return String(py.runPython('workspace.dump_session_json(bridge.SESSION)'));
}
function restore(py:PyodideInterface,value:string):void {
  py.globals.set('saved_workspace_json',value);
  try { py.runPython('workspace.restore_session_json(bridge.SESSION, saved_workspace_json)'); }
  finally { py.globals.delete('saved_workspace_json'); }
}
function inspect(py:PyodideInterface,value:string):any {
  py.globals.set('preview_workspace_json',value);
  try{return JSON.parse(String(py.runPython('workspace.preview_session_json(bridge.SESSION, preview_workspace_json)')));}
  finally{py.globals.delete('preview_workspace_json');}
}
function intake(value:unknown):WorkspaceRecord['intake'] {
  if (!value || typeof value !== 'object') return null;
  const v=value as Record<string,unknown>;
  if(typeof v.source_text !== 'string') return null;
  return {source_text:v.source_text,review_fields:v.review_fields??null,...(v.source_choices!==undefined?{source_choices:copyChargeChoices(v.source_choices)}:{})};
}
async function run(request:Request):Promise<void>{
  let py:PyodideInterface|undefined,before:string|undefined;
  const previousRevision=revision;
  try {
    py=await boot();before=checkpoint(py);
    const response=await workspace.transact<any>((saved,previous)=>{
      const savedRevision=saved?.revision??0;
      const metadata=(record:WorkspaceRecord|null,hasPrevious=Boolean(previous))=>({
        savedAt:record?.savedAt??null,restored:false,hasPrevious,
      });
      if(request.action==='init' && request.payload?.discard_saved!==true){
        pendingImport=undefined;
        if(saved)restore(py!,saved.checkpoint);
        revision=savedRevision;
        const result=dispatch(py!,'init');
        result.result.restored=Boolean(saved);
        result.result.restored_intake=saved?.intake??null;
        result.workspace={...metadata(saved),restored:Boolean(saved)};
        return {result};
      }
      if(request.action!=='init' && revision!==savedRevision){
        pendingImport=undefined;
        if(saved)restore(py!,saved.checkpoint);
        revision=savedRevision;
        const result=dispatch(py!,'init');
        return {result:{...result,ok:false,message:'This workspace changed in another tab. Reload the saved workspace and review its latest state before continuing.',
          workspace:{...metadata(saved),conflict:true}}};
      }
      const currentIntake=intake(request.payload?.workspace_intake)??saved?.intake??null;
      if(request.action==='workspace_restore'){
        const pending=pendingImport;
        if(!pending||pending.token!==request.payload?.preview_token||pending.revision!==savedRevision
          ||pending.intakeKey!==JSON.stringify(currentIntake)){
          pendingImport=undefined;
          return {result:{...dispatch(py!,'init'),ok:false,message:'The current workspace changed. Preview the saved file again before opening it.',workspace:metadata(saved)}};
        }
        restore(py!,pending.file.checkpoint);
        const result=dispatch(py!,'init');
        const record:WorkspaceRecord={version:1,revision:savedRevision+1,savedAt:new Date().toISOString(),
          checkpoint:checkpoint(py!),intake:pending.file.intake};
        result.result.restored=true;result.result.restored_intake=record.intake;
        result.result.workspace_replaced=true;
        result.message='Saved workspace opened. No invoice action was executed. The previous workspace remains available.';
        result.workspace={...metadata(record,Boolean(saved)||Boolean(previous)),restored:true};
        pendingImport=undefined;revision=record.revision;
        return {record,archivePrevious:true,result};
      }
      if(['workspace_export','workspace_preview','workspace_previous_preview'].includes(request.action)){
        // Validate a file using an isolated real Python session. A failed preview
        // leaves the current session, checked input and saved records untouched.
        let imported:PortableWorkspace|undefined,previewState:any;
        try{
          if(request.action==='workspace_preview')imported=decodeWorkspaceFile(request.payload?.text as string);
          if(request.action==='workspace_previous_preview'){
            if(!previous)throw new Error('There is no earlier workspace to reopen.');
            imported=decodeWorkspaceFile(encodeWorkspaceFile(previous).text);
          }
          if(imported)previewState=inspect(py!,imported.checkpoint);
          const record:WorkspaceRecord={version:1,revision:savedRevision+1,savedAt:new Date().toISOString(),
            checkpoint:checkpoint(py!),intake:currentIntake};
          const changed=!saved||record.checkpoint!==saved.checkpoint||JSON.stringify(record.intake)!==JSON.stringify(saved.intake);
          const current=changed?record:saved!;
          const result=dispatch(py!,'init');
          // Encoding also checks the current editable fields without changing them.
          const exported=encodeWorkspaceFile(current);
          if(request.action==='workspace_export'){
            result.result.workspace_file=exported;
            // Saving a copy of unchanged current work must not retire the file
            // the user just inspected before switching.
            if(pendingImport&&(pendingImport.revision!==current.revision
              ||pendingImport.intakeKey!==JSON.stringify(current.intake)))pendingImport=undefined;
          }else{
            const token=crypto.randomUUID();
            pendingImport={token,revision:current.revision,intakeKey:JSON.stringify(current.intake),file:imported!};
            result.result.preview={token,saved_at:imported!.saved_at,intake:imported!.intake,state:previewState,
              current_invoice_count:result.state.ledger.length};
          }
          revision=current.revision;result.workspace=metadata(current);
          return {record:changed?record:undefined,result};
        }catch(error){
          pendingImport=undefined;
          return {result:{...dispatch(py!,'init'),ok:false,
            message:error instanceof Error?error.message:String(error),workspace:metadata(saved)}};
        }
      }
      pendingImport=undefined;
      const clearing=request.action==='reset'||(request.action==='init'&&request.payload?.discard_saved===true);
      const result=dispatch(py!,clearing?'reset':request.action==='save_intake'?'init':request.action,request.payload);
      const record:WorkspaceRecord={
        version:1,revision:savedRevision+1,savedAt:new Date().toISOString(),
        checkpoint:checkpoint(py!),intake:clearing?null:currentIntake,
      };
      if(saved && !clearing && record.checkpoint===saved.checkpoint && JSON.stringify(record.intake)===JSON.stringify(saved.intake)){
        result.workspace=metadata(saved);return {result};
      }
      revision=record.revision;result.workspace=metadata(record);
      return {record,result};
    }, request.action==='init' && request.payload?.discard_saved===true);
    scope.postMessage({id:request.id,ok:true,data:response});
  } catch(error) {
    pendingImport=undefined;revision=previousRevision;
    if(py && before){
      try{restore(py,before);}catch{/* Report the original error; no action is replayed. */}
      const result=dispatch(py,'init');
      scope.postMessage({id:request.id,ok:true,data:{...result,ok:false,
        message:'Could not save or reopen the workspace: '+String(error)+'. The previous saved invoices were not replaced.',
        workspace:{error:true}}});
    } else scope.postMessage({id:request.id,ok:false,error:String(error)});
  }
}
let queue=Promise.resolve();scope.addEventListener('message',(event:MessageEvent<Request>)=>{
  const request=event.data;queue=queue.then(()=>run(request));
});
