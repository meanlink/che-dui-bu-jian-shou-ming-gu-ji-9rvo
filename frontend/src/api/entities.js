import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const acceptance={
 list:view=>api('/acceptance/cases'+(view&&view!=='all'?'?view='+encodeURIComponent(view):'')),
 get:id=>api('/acceptance/cases/'+id),
 create:data=>api('/acceptance/cases',{method:'POST',body:JSON.stringify(data)}),
 report:(id,data)=>api('/acceptance/cases/'+id+'/reports',{method:'POST',body:JSON.stringify(data)}),
 approve:id=>api('/acceptance/cases/'+id+'/approve',{method:'POST'}),
 config:()=>api('/acceptance/config'),
};
