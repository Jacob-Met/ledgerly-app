import type {ChargeChoice} from './intake-file';
/** One origin-local workspace. A transaction serializes competing browser tabs. */
export type WorkspaceRecord = {
  version: 1; revision: number; savedAt: string; checkpoint: string;
  intake: {source_text: string; review_fields: unknown; source_choices?: ChargeChoice[]} | null;
};
export type WorkspaceChange<T> = {record?:WorkspaceRecord;archivePrevious?:boolean;result:T};
export class WorkspaceStore {
  private database?: Promise<IDBDatabase>;
  constructor(private readonly namespace=''){}
  private key(name:string){return this.namespace?this.namespace+':'+name:name;}
  private open(): Promise<IDBDatabase> {
    if (!this.database) this.database = new Promise((resolve,reject) => {
      const request = indexedDB.open('ledgerly-browser-workspace',1);
      request.onupgradeneeded = () => request.result.createObjectStore('workspace');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => { this.database = undefined; reject(request.error); };
      request.onblocked = () => { this.database = undefined; reject(new Error('Close the other Ledgerly tab and retry loading.')); };
    });
    return this.database;
  }
  async transact<T>(change:(saved:WorkspaceRecord|null,previous:WorkspaceRecord|null)=>WorkspaceChange<T>, discardInvalid=false):Promise<T> {
    const db = await this.open();
    return new Promise((resolve,reject) => {
      const transaction = db.transaction('workspace','readwrite');
      const store = transaction.objectStore('workspace');
      let result:T, failure:unknown, reads=0;
      const read = store.get(this.key('current')), previous=store.get(this.key('previous'));
      const receive=()=>{
        if(++reads!==2)return;
        try {
          let saved = read.result ?? null;
          if (saved && (saved.version !== 1 || !Number.isSafeInteger(saved.revision) || saved.revision < 1 || typeof saved.checkpoint !== 'string')) {
            if (discardInvalid) saved = null;
            else throw new Error('The saved workspace cannot be read. Clear it only if you intend to discard its invoices.');
          }
          const changed = change(saved,previous.result??null);
          result = changed.result;
          if(changed.archivePrevious&&saved){
            // Keep the prior complete workspace in the same transaction as replacement.
            // Historical imports stay archived; returning swaps the most recent pair.
            store.put(saved,this.key('archive:'+Date.now()+':'+saved.revision));
            store.put(saved,this.key('previous'));
          }
          if (changed.record) store.put(changed.record,this.key('current'));
        } catch(error) { failure = error; transaction.abort(); }
      };
      read.onsuccess=receive;previous.onsuccess=receive;
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => { failure ??= transaction.error; };
      transaction.onabort = () => reject(failure || transaction.error || new Error('The browser did not save this workspace.'));
    });
  }
}
