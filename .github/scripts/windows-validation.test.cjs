const assert = require('node:assert/strict');
const {test} = require('node:test');
const validate = require('./windows-validation.cjs');
const sha = 'current';
async function check(runs) {
  const errors=[],summaries=[];
  const github={rest:{actions:{listWorkflowRuns: async args=>{
    assert.equal(args.workflow_id,'windows-tests.yml');assert.equal(args.head_sha,sha);
    return {data:{workflow_runs:runs}};
  }}}};
  const core={setFailed: text=>errors.push(text),summary:{addRaw(text){summaries.push(text);return this},async write(){}}};
  await validate({github,context:{repo:{owner:'owner',repo:'repo'},sha},core});
  return {errors,summaries};
}
test('exact successful trusted SHA may skip duplicate tests',async()=>{
  for(const event of ['push','workflow_dispatch']){
    const result=await check([{head_sha:sha,event,conclusion:'success'}]);assert.equal(result.errors.length,0);
  }
});
test('missing failed unrelated and PR checks cannot bypass Windows validation',async()=>{
  for(const runs of [[],[{head_sha:'old',event:'push',conclusion:'success'}],[{head_sha:sha,event:'push',conclusion:'failure'}],[{head_sha:sha,event:'push',conclusion:null}],[{head_sha:sha,event:'pull_request',conclusion:'success'}]]){
    const result=await check(runs);assert.equal(result.errors.length,1);
  }
});
test('unavailable validation service fails closed',async()=>{
  const errors=[];
  await validate({github:{rest:{actions:{listWorkflowRuns:async()=>{throw new Error('unavailable')}}}},context:{repo:{},sha},core:{setFailed:text=>errors.push(text)}});
  assert.equal(errors.length,1);
});
