import { requireSession } from "./session.mjs";
const state = await requireSession();
const roleLabel = role => ({admin:"管理员", reviewer:"审核员", analyst:"分析员"}[role] || role);
const caseLabel = status => ({draft:"待审核",published:"已发布",stale:"口径已过期"}[status] || status);
let editingSource = null;
const $ = s => document.querySelector(s);
const message = text => { $("#management-message").textContent = text; };
const text = value => { const span = document.createElement("span"); span.textContent = value; return span; };
async function api(url, method = "GET", body) {
  const r = await fetch(`/api/v1/${url}`, {method, headers:{"Content-Type":"application/json"}, body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await r.json(); if(!r.ok)throw new Error(typeof data.detail === "string" ? data.detail : data.message || "操作失败"); return data;
}
function action(label, fn) { const button=document.createElement("button");button.className="text-button";button.textContent=label;button.onclick=()=>run(fn);return button; }
async function run(fn){try{await fn();message("操作完成");}catch(e){message(e.message);}}
$("#account").textContent=`${state.user.username} · ${state.user.tenant_id} · ${roleLabel(state.user.role)}`;
for(const node of document.querySelectorAll("[data-admin]"))node.hidden=state.user.role!=="admin";
$("#user-form [name=tenant]").value=state.user.tenant_id;
$("#logout").onclick=()=>run(async()=>{await api("auth/logout","POST");sessionStorage.clear();location.assign("/login");});
async function refresh(){
  const sources=(await api("sources")).items;
  $("#sources").replaceChildren();$("#grant-form [name=source_id]").replaceChildren();
  for(const source of sources){const row=document.createElement("p");row.append(text(`${source.name} · ${Object.keys(source.schema).length || 30} 张表 `));if(source.id!=="demo"&&state.user.role==="admin"){row.append(action("修改连接",()=>{editingSource=source.id;const form=$("#source-form");for(const [key,value] of Object.entries({...source.config,name:source.name})){if(form.elements.namedItem(key))form.elements.namedItem(key).value=value||"";}form.elements.password.value="";form.closest("details").open=true;$("#cancel-source-edit").hidden=false;$("#schema-selection").replaceChildren();message("请重新输入密码并读取结构，保存前重新选择授权表字段。");}));row.append(action("停用",async()=>{await api(`sources/${source.id}`,"DELETE");await refresh();}));}$("#sources").append(row);if(source.id!=="demo")$("#grant-form [name=source_id]").add(new Option(source.name,source.id));}
  if(state.user.role==="admin"){
    const users=(await api("users")).items;$("#users").replaceChildren();$("#grant-form [name=user_id]").replaceChildren();
    for(const user of users){$("#grant-form [name=user_id]").add(new Option(user.username,user.id));const row=document.createElement("p");row.append(text(`${user.username} · ${roleLabel(user.role)} · ${user.active?"启用":"停用"} `));if(user.id!==state.user.user_id){const role=document.createElement("select");for(const value of ["analyst","reviewer","admin"])role.add(new Option(roleLabel(value),value,false,user.role===value));row.append(role,action("更新角色",async()=>{await api(`users/${user.id}`,"PATCH",{active:!!user.active,role:role.value});await refresh();}));row.append(action(user.active?"停用":"启用",async()=>{await api(`users/${user.id}`,"PATCH",{active:!user.active,role:user.role});await refresh();}));}$("#users").append(row);}
  }
  const cases=(await api("feedback-cases")).items;$("#feedback-list").replaceChildren();
  for(const item of cases){const row=document.createElement("p");row.append(text(`${item.question} · ${caseLabel(item.status)} `));if(item.status==="draft"&&["admin","reviewer"].includes(state.user.role))row.append(action("审核并发布",async()=>{await api(`feedback-cases/${item.id}/publish`,"POST");await refresh();}));if(item.status==="published")row.append(action("查看标准 SQL",()=>{message(item.sql);}));$("#feedback-list").append(row);}
}
const sourceData=()=>{const data=Object.fromEntries(new FormData($("#source-form")));data.port=Number(data.port);data.ssl_ca=data.ssl_ca||null;return data;};
$("#discover").onclick=()=>run(async()=>{const data=await api("sources/discover","POST",sourceData());const selection=$("#schema-selection");selection.replaceChildren();for(const [table,fields] of Object.entries(data.schema)){const details=document.createElement("details");const summary=document.createElement("summary");summary.textContent=table;details.append(summary);for(const field of fields){const label=document.createElement("label");const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.dataset.table=table;checkbox.value=field;label.append(checkbox,text(field));details.append(label);}selection.append(details);}message("连接成功，请选择授权表字段");});
$("#source-form").onsubmit=e=>{e.preventDefault();run(async()=>{const data=sourceData();data.tables={};for(const node of document.querySelectorAll("#schema-selection input:checked"))(data.tables[node.dataset.table]??=[]).push(node.value);await api(editingSource?`sources/${editingSource}`:"sources",editingSource?"PUT":"POST",data);editingSource=null;$("#cancel-source-edit").hidden=true;e.target.reset();$("#schema-selection").replaceChildren();await refresh();});};
$("#user-form").onsubmit=e=>{e.preventDefault();run(async()=>{await api("users","POST",Object.fromEntries(new FormData(e.target)));e.target.reset();await refresh();});};
$("#grant-form").onsubmit=e=>{e.preventDefault();run(async()=>{const data=Object.fromEntries(new FormData(e.target));await api(`sources/${data.source_id}/grants`,"POST",{user_id:data.user_id,policy:JSON.parse(data.policy)});});};
$("#feedback-form").onsubmit=e=>{e.preventDefault();run(async()=>{const data=Object.fromEntries(new FormData(e.target));data.expected=JSON.parse(data.expected);await api("feedback-cases","POST",data);e.target.reset();await refresh();});};
$("#export-feedback").onclick=()=>run(async()=>{const data=await api("feedback-cases/export");const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download="feedback-regressions.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
await run(refresh);

$("#cancel-source-edit").onclick=()=>{editingSource=null;$("#source-form").reset();$("#schema-selection").replaceChildren();$("#cancel-source-edit").hidden=true;};
$("#revoke-grant").onclick=()=>run(async()=>{const data=Object.fromEntries(new FormData($("#grant-form")));await api(`sources/${data.source_id}/grants/${data.user_id}`,"DELETE");});
