const {test}=require('node:test');
const assert=require('node:assert/strict');
const {conversationCalls}=require('../static/conversation.js');
test('pairs exact request and response bodies and ignores incomplete live writes',()=>{
 const request={model:'test',input:[{role:'user',content:'<script>hello</script>'}],tools:[]};
 const response={output:[{type:'function_call',name:'move_to',arguments:'{}'}]};
 const text=[{kind:'response',call:2,response},{kind:'request',call:1,request},{kind:'request',call:2,request},{kind:'joint_packet',call:2,waypoints:[]}].map(JSON.stringify).join('\n')+'\n{"kind":"response"';
 assert.deepEqual(conversationCalls(text),[{number:1,request},{number:2,response,request}]);
});
test('handles empty and malformed records without inventing a model conversation',()=>{
 assert.deepEqual(conversationCalls('null\n{}\n{"kind":"request"}\n'),[]);
});
