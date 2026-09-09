const assert=require('node:assert/strict');
const F=require('../static/format.js');
const cases=[
 [null,'—'],[undefined,'—'],[NaN,'—'],[Infinity,'—'],[0,'0'],[999,'999'],
 [1000,'1千'],[1234,'1.23千'],[10000,'1万'],[12345,'1.23万'],[100000,'1十万'],
 [124532,'1.25十万'],[1000000,'1百万'],[1862450,'1.86百万'],[10000000,'1千万'],
 [100000000,'1亿'],[1000000000000,'1万亿'],[999999,'1百万'],[9999.99,'1万'],[-1234,'-1.23千'],[150.5,'150.5'],[0.015,'0.01']
];
for(const [n,expected] of cases)assert.equal(F.compact(n),expected,`number ${n}`);
assert.equal(F.compact(124532,'wan'),'12.45万');assert.equal(F.compact(1862450,'wan'),'186.25万');
assert.equal(F.exact(124532),'124,532');assert.equal(F.escape('<img>'),'&lt;img&gt;');
console.log(JSON.stringify({passed:cases.length+4,description:'Chinese units, rounding boundaries, unknown values, traditional units and escaping'},null,2));
