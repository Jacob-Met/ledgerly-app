import {decodeIntakeFile, encodeIntakeFile, MAX_INTAKE_BYTES, type IntakeFile, type IntakeSnapshot} from './intake-file';

import {reviewLinesMarkup} from './review';

type IntakeSource = Pick<IntakeFile, 'source_text' | 'review_fields' | 'source_choices'>;
type Callbacks = {
  snapshot: () => IntakeSnapshot;
  replace: (file: IntakeSource) => Promise<boolean>;
  parseEmail: (bytes: ArrayBuffer) => Promise<string>;
};

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  return value;
}

/** Refuse imported strings the existing single-line/editor controls would silently alter. */
function checkEditorValues(file: IntakeSource): void {
  const source = document.createElement('textarea');
  source.value = file.source_text;
  if (source.value !== file.source_text) throw new Error('The source text cannot be restored exactly in the intake editor.');
  for(const choice of file.source_choices||[]){
    const input=document.createElement('input');input.value=choice.reason;
    if(input.value!==choice.reason)throw new Error('A saved source reason cannot be restored exactly in the editor.');
  }
  if (!file.review_fields) return;
  const fields = file.review_fields;
  const values: [string, string][] = [
    ['text', fields.client_name], ['email', fields.client_email],
    ['text', fields.due_days], ...(fields.due_date !== undefined ? [['date', fields.due_date] as [string, string]] : []), ['text', fields.amount_paid],
    ...fields.line_items.flatMap(row => Object.values(row).map(value => ['text', value] as [string, string])),
  ];
  const rendered = document.createElement('div');
  rendered.innerHTML = reviewLinesMarkup(fields.line_items, []);
  const restored = Array.from(rendered.querySelectorAll<HTMLElement>('[data-review-line]')).map(row =>
    Object.fromEntries(['desc', 'qty', 'unit_price', 'currency', 'unit'].map(key =>
      [key, row.querySelector<HTMLInputElement>('[data-field="' + key + '"]')!.value])));
  if (JSON.stringify(restored) !== JSON.stringify(fields.line_items)) {
    throw new Error('A saved line item cannot be restored exactly in the invoice editor.');
  }
  for (const [type, value] of values) {
    const input = document.createElement('input');
    input.type = type;
    input.value = value;
    if (input.value !== value) throw new Error('A saved field cannot be restored exactly in the invoice editor.');
  }
}

export function createIntakeFileControls(mount: HTMLElement, callbacks: Callbacks) {
  let ready = false, busy = false, applying = false, generation = 0;
  let preview: IntakeSource | null = null;
  let previewKind: 'saved' | 'email' = 'saved';
  const section = node('section');
  section.className = 'intake-files';
  section.setAttribute('aria-labelledby', 'intake-files-heading');
  const heading = node('h3', 'Open an email or saved intake');
  heading.id = 'intake-files-heading';
  const help = node('p', 'Preview an exported email, or reopen saved corrections.');
  const actions = node('div');
  actions.className = 'intake-file-actions';
  const save = node('button', 'Save intake');
  save.id = 'intake-save';
  save.type = 'button';
  save.className = 'button button-secondary';
  const open = node('button', 'Open intake');
  open.id = 'intake-open';
  open.type = 'button';
  open.className = 'button button-secondary';
  const input = node('input');
  input.id = 'intake-file';
  input.type = 'file';
  input.accept = '.json,application/json';
  input.hidden = true;
  const emailOpen = node('button', 'Open email (.eml)');
  emailOpen.id = 'email-open';
  emailOpen.type = 'button';
  emailOpen.className = 'button button-secondary';
  const emailInput = node('input');
  emailInput.id = 'email-file';
  emailInput.type = 'file';
  emailInput.accept = '.eml,message/rfc822';
  emailInput.hidden = true;
  const feedback = node('p');
  feedback.id = 'intake-file-status';
  feedback.setAttribute('role', 'status');
  feedback.setAttribute('aria-live', 'polite');
  const panel = node('section');
  panel.id = 'intake-preview';
  panel.hidden = true;
  panel.setAttribute('aria-labelledby', 'intake-preview-heading');
  const previewHeading = node('h4', 'Review the intake file');
  previewHeading.id = 'intake-preview-heading';
  previewHeading.tabIndex = -1;
  const metadata = node('p');
  metadata.id = 'intake-preview-summary';
  const source = node('pre');
  source.id = 'intake-preview-source';
  const sourceHelp = node('p');
  sourceHelp.hidden = true;
  sourceHelp.className = 'fine-print';
  const replace = node('button', 'Replace current intake');
  replace.id = 'intake-replace';
  replace.type = 'button';
  replace.className = 'button button-primary';
  const cancel = node('button', 'Keep current intake');
  cancel.id = 'intake-cancel';
  cancel.type = 'button';
  cancel.className = 'button button-secondary';
  const warning = node('p', 'Replaces the current request and edits.');
  warning.hidden = true;
  const previewActions = node('div');
  previewActions.className = 'intake-file-actions';
  previewActions.append(replace, cancel);
  panel.append(previewHeading, metadata, source, sourceHelp, warning, previewActions);
  actions.append(emailOpen, open, save, input, emailInput);
  section.append(heading, help, actions, feedback, panel);
  mount.insertBefore(section, mount.querySelector('.input-label'));

  function previewSource(text: string) {
    source.textContent = text.slice(0, 2000);
    sourceHelp.hidden = text.length <= 2000;
    sourceHelp.textContent = 'Showing the first 2,000 characters.';
    const current = callbacks.snapshot();
    warning.hidden = !current.sourceText && !current.fields;
  }
  function update() {
    const current = callbacks.snapshot();
    save.disabled = busy || applying || (!current.sourceText && !current.fields);
    open.disabled = busy || applying;
    input.disabled = busy || applying;
    emailOpen.disabled = !ready || busy || applying;
    emailInput.disabled = !ready || busy || applying;
    emailOpen.title = ready ? 'Preview an exported email locally' : 'Load the local engine to open an exported email';
    replace.disabled = !ready || busy || applying || !preview;
    cancel.disabled = applying;
  }
  function clear(message = '') {
    generation++;
    preview = null;
    panel.hidden = true;
    metadata.textContent = '';
    source.textContent = '';
    input.value = '';
    emailInput.value = '';
    previewKind = 'saved';
    previewHeading.textContent = 'Review the intake file';
    replace.textContent = 'Replace current intake';
    sourceHelp.hidden = true;
    warning.hidden = true;
    if (message) feedback.textContent = message;
    update();
  }
  function changed(message = 'Current intake changed. Open the file again to review its replacement.') {
    if (applying) return;
    const hadFile = preview !== null || feedback.dataset.reading === 'true';
    clear(hadFile ? message : '');
    delete feedback.dataset.reading;
  }

  save.addEventListener('click', () => {
    if (busy || applying || save.disabled) return;
    let objectUrl: string | undefined;
    let link: HTMLAnchorElement | undefined;
    try {
      const artifact = encodeIntakeFile(callbacks.snapshot());
      objectUrl = URL.createObjectURL(new Blob([artifact.text], {type: 'application/json;charset=utf-8'}));
      link = node('a');
      link.href = objectUrl;
      link.download = artifact.filename;
      link.hidden = true;
      section.append(link);
      link.click();
      feedback.textContent = 'Intake download prepared: ' + artifact.filename + '. Keep this file to reopen your unfinished input.';
    } catch (error) {
      feedback.textContent = error instanceof Error ? error.message : 'The intake download could not be prepared. Your input remains here.';
    } finally {
      link?.remove();
      if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl!), 1000);
    }
  });
  open.addEventListener('click', () => {
    if (busy || applying) return;
    clear();
    input.click();
  });
  input.addEventListener('cancel', () => clear('File selection cancelled. Current intake is unchanged.'));
  input.addEventListener('change', async () => {
    if (busy || applying) return;
    const file = input.files?.[0];
    clear();
    if (!file) return;
    const ticket = generation;
    feedback.textContent = 'Reading intake file...';
    feedback.dataset.reading = 'true';
    try {
      if (file.size > MAX_INTAKE_BYTES) throw new Error('Choose an intake file no larger than 2 MiB.');
      const bytes = await file.arrayBuffer();
      if (ticket !== generation) return;
      let raw: string;
      try { raw = new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
      catch { throw new Error('The intake file is not valid UTF-8.'); }
      const admitted = decodeIntakeFile(raw);
      checkEditorValues(admitted);
      if (ticket !== generation) return;
      preview = admitted;
      metadata.textContent = file.name + ' · saved ' + admitted.saved_at + ' · '
        + (admitted.review_fields ? admitted.review_fields.line_items.length + ' editable line items'
          + ' · recipient ' + (admitted.review_fields.client_email || '(unfinished)') : 'source email only') + (admitted.source_choices?.length ? ' · '+admitted.source_choices.length+' source decisions' : '');
      previewSource(admitted.source_text);
      panel.hidden = false;
      feedback.textContent = ready
        ? ''
        : 'File ready for review. Load the local Python engine before replacing the intake.';
      previewHeading.focus();
    } catch (error) {
      if (ticket === generation) feedback.textContent = (error instanceof Error ? error.message : 'The intake file could not be read.') + ' Current intake is unchanged.';
    } finally {
      if (ticket === generation) {
        delete feedback.dataset.reading;
        update();
      }
    }
  });
  emailOpen.addEventListener('click', () => {
    if (!ready || busy || applying) return;
    clear();
    emailInput.click();
  });
  emailInput.addEventListener('cancel', () => clear('File selection cancelled. Current intake is unchanged.'));
  emailInput.addEventListener('change', async () => {
    if (!ready || busy || applying) return;
    const file = emailInput.files?.[0];
    clear();
    if (!file) return;
    const ticket = generation;
    feedback.textContent = 'Reading exported email locally…';
    feedback.dataset.reading = 'true';
    try {
      if (file.size > MAX_INTAKE_BYTES) throw new Error('Choose an exported email no larger than 2 MiB.');
      const bytes = await file.arrayBuffer();
      if (ticket !== generation) return;
      const sourceText = await callbacks.parseEmail(bytes);
      if (ticket !== generation) return;
      const admitted = {source_text: sourceText, review_fields: null};
      checkEditorValues(admitted);
      preview = admitted;
      previewKind = 'email';
      previewHeading.textContent = 'Email preview';
      replace.textContent = 'Use this email';
      metadata.textContent = file.name;
      previewSource(sourceText);
      panel.hidden = false;
      feedback.textContent = '';
      previewHeading.focus();
    } catch (error) {
      if (ticket === generation) feedback.textContent = (error instanceof Error ? error.message : 'The exported email could not be read.') + ' Current intake is unchanged.';
    } finally {
      if (ticket === generation) {
        delete feedback.dataset.reading;
        update();
      }
    }
  });
  cancel.addEventListener('click', () => {
    if (applying) return;
    const returnTo = previewKind === 'email' ? emailOpen : open;
    clear('Kept the current intake. No file was applied.');
    returnTo.focus();
  });
  section.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden && !applying) {
      event.preventDefault();
      const returnTo = previewKind === 'email' ? emailOpen : open;
      clear('Kept the current intake. No file was applied.');
      returnTo.focus();
    }
  });
  replace.addEventListener('click', async () => {
    if (!ready || busy || applying || !preview) return;
    const file = preview, kind = previewKind;
    applying = true;
    generation++;
    update();
    feedback.textContent = 'Analyzing the saved source with Python before restoring its unfinished fields...';
    try {
      if (await callbacks.replace(file)) {
        preview = null;
        panel.hidden = true;
        source.textContent = '';
        metadata.textContent = '';
        feedback.textContent = file.review_fields
          ? 'Intake restored. Read the current Python warnings, confirm the unfinished fields and check them before drafting.'
          : kind === 'email'
            ? 'Email opened and analyzed. Check the recipient, amounts and terms before creating a draft.'
            : 'Source restored and freshly analyzed. Review the current Python result before drafting.';
      } else {
        feedback.textContent = 'The source could not be analyzed. Previous input is retained and its old review is retired. Retry explicitly or keep the current intake.';
      }
    } catch {
      feedback.textContent = 'The intake could not be restored. Inspect the current input and check it before drafting.';
    } finally {
      applying = false;
      update();
    }
  });
  update();
  return {
    setAvailability(nextReady: boolean, nextBusy: boolean) { ready = nextReady; busy = nextBusy; update(); },
    currentChanged: changed,
  };
}
