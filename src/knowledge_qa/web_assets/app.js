"use strict";
const $=id=>document.getElementById(id);
const statusText={answered:"已核实回答",needs_clarification:"待确认",insufficient_evidence:"资料不足",failed:"工具失败",incomplete:"未完成"};
const toolText={inspect_question:"理解问题与上下文",search_documents:"检索制度文档",read_snippet:"读取原文片段",check_evidence:"核对证据与冲突",answer_question:"形成带引用回答",request_clarification:"请求用户确认",stop:"明确停止"};
const descriptions={document_conflict:"D01规定30天，D02规定15天；未给定适用范围或优先级。",clear_answer:"仅D01适用于报销期限；D02在本场景停用。",paraphrase:"使用“报账”等不同措辞，验证能否补充检索到制度术语。",context_followup:"预填上轮用户问题：报销期限是多久？本轮追问所需材料。",changed_source:"实际将D01原文从30天替换为45天，并停用D02。",tool_failure:"读取D02时注入失败；不能把只读到D01当作无冲突。",out_of_corpus:"资料不涵盖该问题，应该说明缺口，避免猜测。"};
let config=null,current=null,lastPayload=null,conversation=[],busy=false;
const element=(tag,text,className)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
const badge=(status,label)=>element("span",label||statusText[status]||status,"pill "+status);
async function api(path,options){const response=await fetch(path,options);const body=await response.json();if(!response.ok)throw new Error(body.error||"本机服务返回错误。");return body;}
function showError(text){$("error-banner").hidden=!text;$("error-banner").textContent=text||"";}
function setBusy(value){busy=value;document.body.classList.toggle("is-loading",value);$("result-area").setAttribute("aria-busy",String(value));$("ask-button").disabled=value||!config;$("ask-button").firstElementChild.textContent=value?"正在检索并核对原文…":"检索并核实答案";document.querySelectorAll(".preset").forEach(b=>b.disabled=value);$("ask-again").disabled=value||!lastPayload;$("download").disabled=value||!current;document.querySelectorAll(".confirm-button").forEach(b=>b.disabled=value);}
function contextNote(){
  const user=[...conversation].reverse().find(m=>m.role==="user");
  $("context-note").textContent=$("use-history").checked&&user?"已带入用户上下文："+user.content:"未带入对话历史。历史回答不会作为引用证据。";
}
function setCase(name){
  if(!config||busy)return;const item=config.cases.find(c=>c.id===name);if(!item)return;
  $("case").value=name;$("question").value=item.request.question;$("selected-doc").value=item.request.applicable_doc_id||"";$("scope").value=item.request.scope||"";$("max-steps").value=item.max_steps;
  conversation=structuredClone(item.request.history);$("use-history").checked=conversation.length>0;contextNote();
  $("case-help").textContent=descriptions[name]||"这是合成资料场景。点击“检索并核实答案”查看实际结果。";
  document.querySelectorAll(".preset").forEach(b=>b.classList.toggle("selected",b.dataset.case===name));showError("");
}
function formPayload(){return {case:$("case").value,max_steps:Number($("max-steps").value),request:{request_id:"web-"+crypto.randomUUID(),question:$("question").value.trim(),history:$("use-history").checked?structuredClone(conversation):[],applicable_doc_id:$("selected-doc").value||null,scope:$("scope").value||null}};}
async function execute(payload){
  if(busy)return;showError("");setBusy(true);
  try{const response=await api("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});lastPayload=structuredClone(payload);render(response);conversation=[...payload.request.history,{role:"user",content:payload.request.question},{role:"assistant",content:response.result.answer}].slice(-8);$("use-history").checked=true;contextNote();await refreshHistory();}
  catch(error){showError(error instanceof TypeError?"无法连接本机服务，请确认启动窗口仍在运行。":error.message);}
  finally{setBusy(false);}
}
function render(envelope){
  current=envelope;const r=envelope.result;
  $("metric-status").textContent=statusText[r.status]||r.status;$("metric-status").className="state-"+r.status;
  $("metric-mode").textContent=r.execution_mode==="mock"?"离线流程验证":"真实模型动作";
  $("metric-citations").textContent=new Set(r.citations.map(c=>c.doc_id)).size;$("metric-read").textContent=r.sources_read.length;$("metric-steps").textContent=r.steps_taken;$("metric-limit").textContent="执行上限 "+r.max_steps+" 步";
  $("answer-source").textContent=config?.cases.find(c=>c.id===envelope.case)?.label||envelope.case;
  renderAnswer(r);renderCitations(r);renderTrace(r);renderGaps(r);
  $("raw-json").textContent=JSON.stringify(r,null,2);$("trace-count").textContent=r.trace.length;selectTab("trace");setBusy(busy);
}
function renderAnswer(r){
  const box=$("answer-body");box.replaceChildren();
  box.append(badge(r.status),element("p",r.answer,"answer-text"));
  if(r.intent.used_user_history)box.append(element("p","本轮结合用户历史理解为："+r.intent.resolved_question,"outcome-description"));
  if(r.conflicts.length){
    const compare=element("div",undefined,"conflict-compare");const seen=new Set();
    r.citations.forEach(c=>{const key=c.doc_id+":"+c.value;if(seen.has(key))return;seen.add(key);const card=element("div",undefined,"conflict-value");card.append(element("span",c.doc_id+" · "+c.version_date),element("strong",c.value+c.unit),element("small",c.title));compare.append(card);});box.append(compare);
    box.append(element("p","版本较新不等于优先适用，需要明确的范围、替代关系或用户确认。","conflict-tip"));
  }
  if(r.clarification){box.append(element("p",r.clarification,"clarification-text"));const choices=element("div",undefined,"confirm-actions");
    if(r.assessment?.status==="needs_scope")for(const scope of ["总部","分公司"]){const b=element("button","我是"+scope+"员工","button secondary confirm-button");b.addEventListener("click",()=>confirm({scope}));choices.append(b);}
    else if(r.status==="needs_clarification"&&r.citations.length){for(const id of new Set(r.citations.map(c=>c.doc_id))){const b=element("button","确认采用 "+id,"button secondary confirm-button");b.addEventListener("click",()=>confirm({applicable_doc_id:id}));choices.append(b);}}
    if(choices.childNodes.length)box.append(choices);
  }
  if(r.status==="incomplete")box.append(element("p",r.stop_reason==="step_limit_reached"?"已达到步数上限，当前证据尚不足以形成结论。":"本轮中断原因："+r.stop_reason,"outcome-description"));
  if(["failed","insufficient_evidence"].includes(r.status))r.gaps.forEach(g=>box.append(element("p",g,"outcome-description")));
}
function confirm(changes){
  if(!lastPayload||busy)return;const payload=structuredClone(lastPayload);payload.request={...payload.request,...changes,request_id:"web-"+crypto.randomUUID()};
  if(changes.applicable_doc_id)$("selected-doc").value=changes.applicable_doc_id;if(changes.scope)$("scope").value=changes.scope;
  execute(payload);
}
function highlightedQuote(text,support){const quote=element("blockquote");const index=text.indexOf(support);if(index<0){quote.textContent=text;return quote;}quote.append(document.createTextNode(text.slice(0,index)),element("mark",support),document.createTextNode(text.slice(index+support.length)));return quote;}
function renderCitations(r){
  const box=$("citations");box.replaceChildren();$("citation-note").textContent=r.status==="answered"?"已核验引用":r.citations.length?"已读取 · 尚待统一结论":"03 / 证据";
  if(!r.citations.length){box.append(element("div",r.status==="failed"?"工具失败，本轮未形成可引用的完整依据。":"当前没有支持答案的引用；不会使用历史回答或未读取的摘要补足。","detail-placeholder"));return;}
  r.citations.forEach(c=>{const card=element("article",undefined,"citation-card");const heading=element("div",undefined,"citation-heading");heading.append(element("span",c.doc_id,"document-id"),element("span",c.title),element("small",c.version_date));card.append(heading,highlightedQuote(c.quote,c.supporting_quote),element("div","片段 "+c.chunk_id+" · 内容指纹 "+c.content_hash.slice(0,12)+(c.scope?" · 范围："+c.scope:""),"source-meta"));box.append(card);});
}
function renderTrace(r){
  const box=$("panel-trace");box.replaceChildren();r.trace.forEach(s=>{const item=element("details",undefined,"trace-item"),summary=element("summary");summary.append(element("span",String(s.step).padStart(2,"0"),"trace-number"),element("span",toolText[s.tool]||"模型调用异常"),badge(s.observation?.ok?"succeeded":"failed",s.observation?.ok?"已执行":"异常"));const body=element("div",undefined,"trace-body");body.append(element("p",s.decision_basis||s.error||"—"),element("b","工具参数"),element("pre",JSON.stringify(s.arguments||{},null,2)),element("b","实际返回"),element("pre",JSON.stringify(s.observation||{error:s.error},null,2)),element("p","后续动作："+(toolText[s.next_action]||(s.next_action==="end"?"结束":s.next_action||"停止"))));item.append(summary,body);box.append(item);});
}
function renderGaps(r){const box=$("panel-gaps");box.replaceChildren();if(!r.gaps.length)box.append(element("p","本轮所问事实已获得一致且可引用的依据。","outcome-description"));else r.gaps.forEach(g=>box.append(element("p",g,"gap-item")));if(r.conflicts.length)box.append(element("pre",JSON.stringify(r.conflicts,null,2),"script-text"));}
function selectTab(name){document.querySelectorAll(".tab").forEach(tab=>{const selected=tab.dataset.tab===name;tab.classList.toggle("active",selected);tab.setAttribute("aria-selected",String(selected));$("panel-"+tab.dataset.tab).hidden=!selected;});}
async function refreshHistory(){
  const data=await api("/api/history"),box=$("history-list");box.replaceChildren();if(!data.runs.length){box.append(element("div","还没有问答记录。","detail-placeholder"));return;}
  data.runs.forEach(run=>{const row=element("button",undefined,"history-row");row.type="button";row.append(element("span",run.question,"theme"),badge(run.status),element("span",run.citation_count+" 条引用"),element("span",new Date(run.generated_at).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}),"date"),element("span","查看 →","arrow"));row.addEventListener("click",async()=>{if(busy)return;try{const saved=await api("/api/results/"+run.run_id);lastPayload={request:structuredClone(saved.result.request),case:saved.case,max_steps:saved.result.max_steps};render(saved);$("result-area").scrollIntoView({behavior:"smooth",block:"start"});}catch(e){showError(e.message);}});box.append(row);});
}
async function openCorpus(){
  try{const data=await api("/api/corpus?case="+encodeURIComponent($("case").value));$("corpus-version").textContent="合成资料 · "+data.version+" · 当前所选场景";const box=$("corpus-documents");box.replaceChildren();data.documents.forEach(d=>{const card=element("article",undefined,"citation-card");const header=element("div",undefined,"citation-heading");header.append(element("span",d.id,"document-id"),element("span",d.title),badge(d.active===false?"inactive":"succeeded",d.active===false?"本场景停用":"有效资料"));card.append(header,element("p",d.version_date+(d.scope?" · "+d.scope:" · 未标注适用范围"),"source-meta"));d.chunks.forEach(c=>{card.append(element("div",c.id,"source-meta"),element("blockquote",c.text));});box.append(card);});$("corpus-dialog").showModal();}catch(e){showError(e.message);}
}
$("question-form").addEventListener("submit",e=>{e.preventDefault();execute(formPayload());});
document.querySelectorAll(".preset").forEach(b=>b.addEventListener("click",()=>setCase(b.dataset.case)));
$("case").addEventListener("change",()=>setCase($("case").value));
$("use-history").addEventListener("change",contextNote);$("clear-context").addEventListener("click",()=>{conversation=[];$("use-history").checked=false;contextNote();});
$("ask-again").addEventListener("click",()=>{if(lastPayload){const p=structuredClone(lastPayload);p.request.request_id="web-"+crypto.randomUUID();execute(p);}});
$("download").addEventListener("click",()=>{if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current.result,null,2)],{type:"application/json;charset=utf-8"}));const a=element("a");a.href=url;a.download="knowledge-answer-"+current.run_id.slice(0,8)+".json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>selectTab(b.dataset.tab)));
$("nav-work").addEventListener("click",()=>window.scrollTo({top:0,behavior:"smooth"}));$("nav-history").addEventListener("click",()=>$("history-area").scrollIntoView({behavior:"smooth"}));
$("view-corpus").addEventListener("click",openCorpus);$("close-corpus").addEventListener("click",()=>$("corpus-dialog").close());$("refresh-history").addEventListener("click",()=>refreshHistory().catch(e=>showError(e.message)));
async function initialize(){setBusy(false);try{config=await api("/api/config");config.cases.forEach(c=>{const option=element("option",c.label);option.value=c.id;$("case").append(option);});$("mode-tag").textContent=config.model==="mock"?"离线 Mock · 本机验证":"DeepSeek · 真实模型";if(config.model!=="mock")$("mode-notice").textContent="真实模型选择工具；制度、FAQ与业务工具仍为合成示例。";setCase(config.default_case);setBusy(false);await refreshHistory();}catch(e){$("mode-tag").textContent="连接失败";showError("无法连接本机服务，请确认已启动后刷新页面。");}}
initialize();
