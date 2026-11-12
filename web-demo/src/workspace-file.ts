import {decodeIntakeFile, encodeIntakeFile} from './intake-file';
import type {ReviewFields} from './review';
import type {WorkspaceRecord} from './workspace-store';

export const MAX_WORKSPACE_FILE_BYTES = 24_000_000;
const MAX_CHECKPOINT_BYTES = 10_000_000;
const encoder = new TextEncoder();
export type PortableWorkspace = {
 format:'ledgerly-workspace'; version:1|2; mode:'browser-simulation';
 saved_at:string; checkpoint:string; intake:WorkspaceRecord['intake'];
};
function bounded(raw:string,limit:number,message:string):string {
 if(encoder.encode(raw).byteLength>limit)throw new Error(message);
 return raw;
}
function detachedIntake(value:WorkspaceRecord['intake'],savedAt:string):WorkspaceRecord['intake'] {
 // Reuse the maintained intake format, including unfinished strings and editor fields.
 const file=decodeIntakeFile(encodeIntakeFile({
  sourceText:value?.source_text??'',fields:(value?.review_fields??null) as ReviewFields|null,
  ...(value?.source_choices!==undefined?{choices:value.source_choices}:{}),
 },savedAt).text);
 return value===null?null:{source_text:file.source_text,review_fields:file.review_fields,...(file.source_choices!==undefined?{source_choices:file.source_choices}:{})};
}
export function encodeWorkspaceFile(record:Pick<WorkspaceRecord,'checkpoint'|'intake'|'savedAt'>):{text:string;filename:string} {
 const checkpoint=bounded(record.checkpoint,MAX_CHECKPOINT_BYTES,'The workspace exceeds the supported 10 MB checkpoint size.');
 const value:PortableWorkspace={format:'ledgerly-workspace',version:record.intake?.source_choices!==undefined?2:1,mode:'browser-simulation',
  saved_at:record.savedAt,checkpoint,intake:detachedIntake(record.intake,record.savedAt)};
 return {text:bounded(JSON.stringify(value,null,2)+'\n',MAX_WORKSPACE_FILE_BYTES,'The complete workspace file is too large.'),
  filename:'ledgerly-workspace-'+record.savedAt.replace(/[-:.]/g,'')+'.json'};
}
export function decodeWorkspaceFile(raw:string):PortableWorkspace {
 if(typeof raw!=='string')throw new Error('Choose a saved Ledgerly workspace file.');
 let value:any;
 try{value=JSON.parse(bounded(raw,MAX_WORKSPACE_FILE_BYTES,'Choose a workspace file no larger than 24 MB.'));}
 catch(error){if(error instanceof SyntaxError)throw new Error('The workspace file is not valid JSON.');throw error;}
 const keys=['format','version','mode','saved_at','checkpoint','intake'];
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==keys.length
   ||!keys.every(key=>Object.prototype.hasOwnProperty.call(value,key))
   ||value.format!=='ledgerly-workspace'||(value.version!==1&&value.version!==2)||value.mode!=='browser-simulation'){
  throw new Error('Choose a supported Ledgerly browser workspace. Native provider records cannot be imported here.');
 }
 if(typeof value.saved_at!=='string'||typeof value.checkpoint!=='string')
  throw new Error('The saved workspace has incomplete metadata.');
 if(value.intake!==null&&(!value.intake||typeof value.intake!=='object'||Array.isArray(value.intake)
   ||Object.keys(value.intake).length!==(value.version===2?3:2)||typeof value.intake.source_text!=='string'
   ||(value.version===2&&!Object.prototype.hasOwnProperty.call(value.intake,'source_choices'))
   ||!Object.prototype.hasOwnProperty.call(value.intake,'review_fields')))
  throw new Error('The saved workspace has incomplete intake fields.');
 return {format:'ledgerly-workspace',version:value.version,mode:'browser-simulation',saved_at:value.saved_at,
  checkpoint:bounded(value.checkpoint,MAX_CHECKPOINT_BYTES,'The workspace checkpoint exceeds 10 MB.'),
  intake:detachedIntake(value.intake,value.saved_at)};
}
