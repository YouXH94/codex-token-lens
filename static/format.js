/* Pure display helpers. Accounting and exports always retain exact numeric values. */
(function (root) {
  'use strict';
  const steps = [[1e12,'万亿'],[1e8,'亿'],[1e7,'千万'],[1e6,'百万'],[1e5,'十万'],[1e4,'万'],[1e3,'千'],[1,'']];
  function parts(value, style='full', digits=2) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return {value:'—',unit:''};
    const n=Number(value), abs=Math.abs(n), units=style==='wan'?[[1e12,'万亿'],[1e8,'亿'],[1e4,'万'],[1e3,'千'],[1,'']]:steps;
    let index=units.findIndex(([scale])=>abs>=scale); if(index<0) index=units.length-1;
    const precision=Math.max(0,Math.min(4,digits));
    let rounded=Number((abs/units[index][0]).toFixed(precision));
    if(index>0 && rounded*units[index][0]>=units[index-1][0]) {index--;rounded=Number((abs/units[index][0]).toFixed(precision));}
    return {value:(n<0?'-':'')+String(rounded), unit:units[index][1]};
  }
  function compact(n, style='full', digits=2) {const p=parts(n,style,digits); return p.value+p.unit;}
  function exact(n) {return n==null?'未上报':Number(n).toLocaleString('zh-CN',{maximumFractionDigits:6});}
  function escape(s) {return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  const api={parts,compact,exact,escape};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  root.LensFormat=api;
})(typeof window!=='undefined'?window:globalThis);
