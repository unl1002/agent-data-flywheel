'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const zlib=require('node:zlib');
test('browser loader decompresses the same embedded envelope without modifying text or refs',async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../assets/dashboard.html'),'utf8');
  const code=html.slice(html.indexOf('/* DECODE_START */'),html.indexOf('/* DECODE_END */'));
  const context=vm.createContext({DecompressionStream,Blob,Response,Uint8Array,atob});
  vm.runInContext(code,context);
  const value={question:'中文</script>\u2028',refs:['dialogue:request:abc','#node:7']};
  const payload={encoding:'gzip-base64',data:zlib.gzipSync(Buffer.from(JSON.stringify(value))).toString('base64')};
  assert.equal(JSON.stringify(await context.decodeEmbedded(payload)),JSON.stringify(value));
  assert.equal(await context.decodeEmbedded(value),value);
  await assert.rejects(context.decodeEmbedded({encoding:'gzip-base64',data:'invalid'}));
});
