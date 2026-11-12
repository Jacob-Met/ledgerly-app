import {expect,it} from 'vitest';
import {decodeWorkspaceFile,encodeWorkspaceFile} from '../src/workspace-file';

const record={
 savedAt:'2026-11-10T19:00:00.000Z',
 checkpoint:JSON.stringify({format:'ledgerly-browser-sandbox',version:1,exact:'123.45',pending:['a-real-saved-id']}),
 intake:{source_text:'Unfinished request for Núria\n',review_fields:{
  client_name:'Núria',client_email:'nuria@example.test',due_days:'',amount_paid:'0',
  line_items:[{desc:'Editing',qty:'',unit_price:'90.',currency:'EUR',unit:'hour'}],
 }},
};
it('carries exact saved checkpoint and unfinished fields without a browser revision or review authorization',()=>{
 const artifact=encodeWorkspaceFile(record),restored=decodeWorkspaceFile(artifact.text);
 expect(restored.checkpoint).toBe(record.checkpoint);
 expect(restored.intake).toEqual(record.intake);
 expect(Object.keys(restored).sort()).toEqual(['checkpoint','format','intake','mode','saved_at','version']);
 expect(artifact.filename).toMatch(/^ledgerly-workspace-.*\.json$/);
});
it('refuses provider records and extra approval fields instead of presenting them as an importable simulation',()=>{
 const value=JSON.parse(encodeWorkspaceFile(record).text);
 expect(()=>decodeWorkspaceFile(JSON.stringify({...value,mode:'paypal-sandbox'}))).toThrow(/Native provider/);
 value.intake.review_fields.review_id='old-checked-token';
 expect(()=>decodeWorkspaceFile(JSON.stringify(value))).toThrow(/missing or unexpected/);
});

it('preserves validated unfinished source decisions in a versioned workspace without approval',()=>{
 const choices=[{id:'charge-1',quote:'Unapproved optional work EUR 85',selected:'excluded',reason:'Not approved'}];
 const withChoices={...record,intake:{...record.intake,source_choices:choices}};
 const artifact=encodeWorkspaceFile(withChoices),restored=decodeWorkspaceFile(artifact.text);
 expect(restored.version).toBe(2);
 expect(restored.intake).toEqual(withChoices.intake);
 choices[0].reason='Changed later';
 expect(restored.intake?.source_choices?.[0].reason).toBe('Not approved');
 const forged=JSON.parse(artifact.text);forged.intake.source_choices[0].review_id='old-approval';
 expect(()=>decodeWorkspaceFile(JSON.stringify(forged))).toThrow(/unexpected/);
});
