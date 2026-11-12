import type {ReviewFields} from './review';

export const MAX_INTAKE_BYTES = 2 * 1024 * 1024;
export const MAX_INTAKE_LINES = 200;
export type ChargeChoice = {id:string;quote:string;selected:string;reason:string};
export type IntakeSnapshot = {sourceText: string; fields: ReviewFields | null; choices?:ChargeChoice[]};
export type IntakeFile = {
  format: 'ledgerly-intake';
  version: 1 | 2;
  saved_at: string;
  source_text: string;
  review_fields: ReviewFields | null;
  source_choices?:ChargeChoice[];
};

const encoder = new TextEncoder();
const fieldKeys = ['client_name', 'client_email', 'due_days', 'amount_paid', 'line_items'];
const lineKeys = ['desc', 'qty', 'unit_price', 'currency', 'unit'];

function record(value: unknown, keys: string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== keys.length
    || !keys.every(key => Object.prototype.hasOwnProperty.call(value, key))) {
    throw new Error(label + ' has missing or unexpected fields.');
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string') throw new Error(label + ' must be text.');
  return value;
}

function copyFields(value: unknown): ReviewFields | null {
  if (value === null) return null;
  const dated = Boolean(value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'due_date'));
  const noted = Boolean(value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'note'));
  const fields = record(value, [...fieldKeys, ...(dated ? ['due_date'] : []), ...(noted ? ['note'] : [])], 'Editable invoice fields');
  if (!Array.isArray(fields.line_items) || fields.line_items.length > MAX_INTAKE_LINES) {
    throw new Error('An intake file can contain at most ' + MAX_INTAKE_LINES + ' editable line items.');
  }
  return {
    client_name: text(fields.client_name, 'Client name'),
    client_email: text(fields.client_email, 'Recipient email'),
    due_days: text(fields.due_days, 'Payment terms'),
    ...(dated ? {due_date: text(fields.due_date, 'Due date')} : {}),
    ...(noted ? {note: text(fields.note, 'Invoice note')} : {}),
    amount_paid: text(fields.amount_paid, 'Prior payment'),
    line_items: fields.line_items.map((value, index) => {
      const row = record(value, lineKeys, 'Line item ' + (index + 1));
      return {
        desc: text(row.desc, 'Work description'), qty: text(row.qty, 'Quantity'),
        unit_price: text(row.unit_price, 'Unit price'), currency: text(row.currency, 'Currency'),
        unit: text(row.unit, 'Unit'),
      };
    }),
  };
}

export function copyChargeChoices(value:unknown):ChargeChoice[] {
 if(!Array.isArray(value)||value.length>MAX_INTAKE_LINES)throw new Error('Invalid saved source decisions.');
 const ids=new Set<string>();
 return value.map((choice:unknown)=>{
  const row=record(choice,['id','quote','selected','reason'],'Source decision');
  const id=text(row.id,'Source identity'),quote=text(row.quote,'Source quotation');
  const selected=text(row.selected,'Source choice'),reason=text(row.reason,'Source reason');
  if(!id||ids.has(id)||!(/^(?:|excluded|line:[1-9][0-9]*)$/).test(selected))
   throw new Error('Invalid saved source decision.');
  ids.add(id);return {id,quote,selected,reason};
 });
}

function admit(value: unknown): IntakeFile {
  const version=(value as {version?:unknown})?.version;
  const data = record(value, ['format', 'version', 'saved_at', 'source_text', 'review_fields',...(version===2?['source_choices']:[])], 'Intake file');
  if (data.format !== 'ledgerly-intake' || (data.version !== 1 && data.version !== 2)) {
    throw new Error('Choose a Ledgerly intake file in the supported version 1 or 2 format.');
  }
  const savedAt = text(data.saved_at, 'Saved time');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(savedAt)
    || !Number.isFinite(new Date(savedAt).getTime()) || new Date(savedAt).toISOString() !== savedAt) {
    throw new Error('The intake file needs an exact UTC saved time.');
  }
  return {
    format: 'ledgerly-intake', version: data.version as 1|2, saved_at: savedAt,
    ...(data.version===2?{source_choices:copyChargeChoices(data.source_choices)}:{}),
    source_text: text(data.source_text, 'Source email'),
    review_fields: copyFields(data.review_fields),
  };
}

function bounded(value: string): string {
  if (encoder.encode(value).byteLength > MAX_INTAKE_BYTES) {
    throw new Error('Choose an intake file no larger than 2 MiB.');
  }
  return value;
}

/** Preserve input strings, including unfinished values. Python owns invoice validation. */
export function encodeIntakeFile(snapshot: IntakeSnapshot, savedAt = new Date().toISOString()): {text: string; filename: string} {
  const file = admit({
    format: 'ledgerly-intake', version: snapshot.choices===undefined?1:2, saved_at: savedAt,
    ...(snapshot.choices===undefined?{}:{source_choices:snapshot.choices}),
    source_text: snapshot.sourceText, review_fields: snapshot.fields,
  });
  return {
    text: bounded(JSON.stringify(file, null, 2) + '\n'),
    filename: 'ledgerly-intake-' + savedAt.replace(/[-:.]/g, '') + '.json',
  };
}

/** Admit and detach the complete file. No approval, token or derived result is imported. */
export function decodeIntakeFile(raw: string): IntakeFile {
  if (typeof raw !== 'string') throw new Error('The intake file must be UTF-8 JSON text.');
  let value: unknown;
  try { value = JSON.parse(bounded(raw)); }
  catch (error) {
    if (error instanceof SyntaxError) throw new Error('The intake file is not valid JSON.');
    throw error;
  }
  return admit(value);
}

/** Preserve original MIME bytes, including non-UTF-8 bodies, for the Python parser. */
export function encodeEmailBytes(buffer: ArrayBuffer): string {
  if (!buffer.byteLength) throw new Error('The exported email is empty.');
  if (buffer.byteLength > MAX_INTAKE_BYTES) throw new Error('Choose an exported email no larger than 2 MiB.');
  const bytes = new Uint8Array(buffer);
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 16384) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 16384)));
  }
  return btoa(chunks.join(''));
}
