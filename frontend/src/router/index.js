export function route(){
 const [path,query='']=location.hash.replace(/^#\//,'').split('?');
 const [page='overview',id]=path.split('/');
 return {page,id,params:Object.fromEntries(new URLSearchParams(query))};
}
