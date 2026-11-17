/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number|null,acceptance_case_id:number|null,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{number:number,size:number,alpha:number,acceptance_number:number|null,status:string,enrolled:number,survived:number,failed:number,pending:number,risk:number|null}} AcceptancePhase */
/** @typedef {{id:number,name:string,model:string,qualified_km:number,min_survival:number,total_alpha:number,status:string,result:string,current_phase:number,approved:boolean,close_reason:string,phases:AcceptancePhase[]}} AcceptanceCase */
export {};
