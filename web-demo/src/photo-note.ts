/** Read a photographed job note with the local vision model, or fall back to a canned sample. */
export type Region={text:string;box:[number,number,number,number]}; // box in 0..1 of the picture
export const OLLAMA='http://127.0.0.1:11434';
export const SAMPLE_REGIONS:Region[]=[
 {text:'Tidewater Studio - shoot job',box:[.07,.08,.93,.22]},
 {text:'2 x Shoot days @ $950 each',box:[.07,.30,.93,.44]},
 {text:'40 x Retouched images @ $18 each',box:[.07,.50,.93,.64]},
 {text:'Net 14',box:[.07,.72,.50,.86]},
];
/** A handwriting-style sample picture, drawn here so the demo needs no files. */
export function sampleNoteDataUrl():string{
 const rows=[['Tidewater Studio - shoot job',.15],['2 x Shoot days @ $950 each',.37],['40 x Retouched images @ $18 each',.57],['Net 14',.79]] as const;
 const svg='<svg xmlns="http://www.w3.org/2000/svg" width="800" height="560" viewBox="0 0 800 560"><rect width="800" height="560" fill="#f6efd8"/>'+
 Array.from({length:9},(_,i)=>'<line x1="0" x2="800" y1="'+(70+i*58)+'" y2="'+(70+i*58)+'" stroke="#a9c4d8" stroke-width="2"/>').join('')+
 '<line x1="56" x2="56" y1="0" y2="560" stroke="#d98b8b" stroke-width="2"/>'+
 rows.map(([t,y],i)=>'<text x="'+(76+(i%2)*4)+'" y="'+(y*560+10)+'" font-family="Bradley Hand,Segoe Print,Comic Sans MS,cursive" font-size="'+(i===0?38:36)+'" fill="#1d2f6b" transform="rotate('+(i%2?-.8:.6)+' 76 '+(y*560)+')">'+t.replace(/&/g,'&amp;')+'</text>').join('')+'</svg>';
 return 'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);
}
function clamp(n:number){return Math.max(0,Math.min(1,n));}
/** Downscale to at most 1000px and return base64 JPEG plus the sent size. */
async function prepare(blob:Blob):Promise<{b64:string;w:number;h:number}>{
 const bmp=await createImageBitmap(blob);
 const k=Math.min(1,1000/Math.max(bmp.width,bmp.height));
 const w=Math.round(bmp.width*k),h=Math.round(bmp.height*k);
 const c=document.createElement('canvas');c.width=w;c.height=h;
 const g=c.getContext('2d')!;g.fillStyle='#fff';g.fillRect(0,0,w,h);g.drawImage(bmp,0,0,w,h);
 return {b64:c.toDataURL('image/jpeg',.88).split(',')[1],w,h};
}
export async function readNote(blob:Blob,signal?:AbortSignal):Promise<Region[]>{
 const {b64,w,h}=await prepare(blob);
 const prompt='This is a photo of a handwritten job note or timesheet. Read it line by line. Reply with JSON only: {"lines":[{"text":"exact words on that line","box":[x1,y1,x2,y2]}]} where box is the pixel rectangle of that line in this '+w+'x'+h+' image.';
 const r=await fetch(OLLAMA+'/api/generate',{method:'POST',signal,headers:{'content-type':'application/json'},body:JSON.stringify({model:'qwen2.5vl:3b',prompt,images:[b64],stream:false,format:'json',options:{temperature:0}})});
 if(!r.ok)throw new Error('vision model HTTP '+r.status);
 const data=await r.json();
 let parsed:any;try{parsed=JSON.parse(data.response);}catch{throw new Error('vision model gave unreadable output');}
 const arr:any[]=Array.isArray(parsed)?parsed:Array.isArray(parsed.lines)?parsed.lines:[];
 const out:Region[]=[];
 for(const row of arr){
  const text=String(row?.text??'').trim();if(!text)continue;
  const b=Array.isArray(row.box)&&row.box.length===4?row.box.map(Number):null;
  const i=out.length,n=arr.length;
  let box:[number,number,number,number]=[.05,clamp(i/n),.95,clamp((i+.9)/n)];
  if(b&&b.every(Number.isFinite)){
   const big=Math.max(...b)>1.5;
   box=big?[clamp(b[0]/w),clamp(b[1]/h),clamp(b[2]/w),clamp(b[3]/h)]:[clamp(b[0]),clamp(b[1]),clamp(b[2]),clamp(b[3])];
   if(box[2]<=box[0]||box[3]<=box[1])box=[.05,clamp(i/n),.95,clamp((i+.9)/n)];
  }
  out.push({text,box});
 }
 if(!out.length)throw new Error('no writing found');
 return out;
}
