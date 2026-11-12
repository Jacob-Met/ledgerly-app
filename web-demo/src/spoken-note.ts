/** Turn a spoken or jotted job note into the "- 2 x Shoot days @ $950 each" lines the rules extractor reads. */
export type Rate={desc:string;unit_price:string;currency:string;unit?:string};
export type Known={name:string;email:string;rates:Rate[]};
const UNITS:Record<string,number>={zero:0,one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10,eleven:11,twelve:12,thirteen:13,fourteen:14,fifteen:15,sixteen:16,seventeen:17,eighteen:18,nineteen:19,a:1,an:1};
const TENS:Record<string,number>={twenty:20,thirty:30,forty:40,fourty:40,fifty:50,sixty:60,seventy:70,eighty:80,ninety:90};
const isNum=(w:string)=>/^\d+(\.\d+)?$/.test(w)||w in UNITS&&w!=='a'&&w!=='an'||w in TENS||w==='hundred'||w==='thousand';
/** Read a run of number words. "nine fifty" is read the way people say prices: 950. */
export function wordsToNumber(words:string[]):number|null{
 if(!words.length)return null;
 if(words.length===1&&/^\d+(\.\d+)?$/.test(words[0]))return Number(words[0]);
 let total=0,cur=0,seen=false,prev:number|null=null;
 for(const w of words){
  if(w in UNITS&&w!=='a'&&w!=='an'){
   // a lone digit followed by a tens word is a spoken price: nine fifty
   cur+=UNITS[w];prev=UNITS[w];seen=true;
  }else if(w in TENS){
   if(prev!==null&&prev>=1&&prev<=9&&cur===prev&&total===0){cur=prev*100+TENS[w];prev=null;}
   else{cur+=TENS[w];prev=null;}
   seen=true;
  }else if(w==='hundred'){cur=(cur||1)*100;prev=null;seen=true;}
  else if(w==='thousand'){total+=(cur||1)*1000;cur=0;prev=null;seen=true;}
  else if(/^\d+(\.\d+)?$/.test(w)){cur+=Number(w);seen=true;prev=null;}
  else return null;
 }
 return seen?total+cur:null;
}
const tokens=(s:string)=>s.toLowerCase().replace(/[$,]/g,' $ ').replace(/\s+/g,' ').trim().split(' ').filter(Boolean);
const title=(s:string)=>s.charAt(0).toUpperCase()+s.slice(1);
export function stem(w:string){return w.toLowerCase().replace(/ies$/,'y').replace(/s$/,'');}
export function matchClient(text:string,clients:Known[]):Known|null{
 const low=text.toLowerCase();let best:Known|null=null,bestLen=0;
 for(const c of clients){
  const first=c.name.toLowerCase().split(/\s+/)[0];
  for(const key of [c.name.toLowerCase(),first])if(key.length>2&&low.includes(key)&&key.length>bestLen){best=c;bestLen=key.length;}
 }
 return best;
}
export type Parsed={client:Known|null;clientWord:string;lines:{qty:number;desc:string;price:number|null;filled:boolean;text:string}[];text:string};
export function parseSpoken(raw:string,clients:Known[]):Parsed{
 let s=raw.replace(/\s+/g,' ').trim().replace(/[.!]$/,'');
 const client=matchClient(s,clients);
 let clientWord='';
 const m=/^(?:please\s+)?(?:invoice|bill)\s+(.+?)\s+for\s+(.+)$/i.exec(s);
 let body=s;
 if(m){clientWord=m[1];body=m[2];}
 const parts=body.split(/\s*(?:,|\band\b|\bplus\b)\s*(?=(?:\$|\d|a\b|an\b|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fourty|fifty|sixty|seventy|eighty|ninety))/i);
 const lines:Parsed['lines']=[];
 for(const part of parts){
  const t=tokens(part);if(!t.length)continue;
  let i=0;const q:string[]=[];
  while(i<t.length&&isNum(t[i])){q.push(t[i]);i++;}
  if(!q.length&&(t[0]==='a'||t[0]==='an')){q.push('one');i=1;}
  const qty=wordsToNumber(q);if(qty===null)continue;
  const atAt=t.findIndex((w,k)=>k>=i&&(w==='at'||w==='for'||w==='@'||w==='each'&&false));
  const descWords=t.slice(i,atAt<0?t.length:atAt).filter(w=>w!=='of'&&w!=='$');
  let price:number|null=null;
  if(atAt>=0){const pw=t.slice(atAt+1).filter(w=>w!=='$'&&w!=='each'&&w!=='apiece'&&w!=='dollars'&&w!=='bucks'&&w!=='per'&&w!=='a'&&w!=='day'&&w!=='image'&&w!=='hour');price=wordsToNumber(pw);}
  let desc=descWords.join(' ');if(!desc)continue;
  let filled=false;
  if(price===null&&client){
   const hit=client.rates.find(r=>{const rs=r.desc.toLowerCase().split(/\s+/).map(stem);return descWords.some(w=>rs.includes(stem(w)));});
   if(hit){price=Number(hit.unit_price);filled=true;}
  }
  desc=title(desc);
  const text='- '+qty+' x '+desc+(price!==null?' @ $'+price+' each':'');
  lines.push({qty,desc,price,filled,text});
 }
 const header=client?'From: '+client.name+' <'+client.email+'>\n':clientWord?'Client (said aloud): '+title(clientWord)+'\n':'';
 const text=header+(header?'\n':'')+lines.map(l=>l.text).join('\n')+(lines.length?'\n':'');
 return {client,clientWord,lines,text};
}
