const TOKEN_KEY = 'eaststone_eqms_token'

export function getToken(){ return localStorage.getItem(TOKEN_KEY) }
export function setToken(token:string|null){ token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY) }

async function request(path:string, options:RequestInit={}){
  const headers = new Headers(options.headers || {})
  const token = getToken()
  if(token) headers.set('Authorization', `Bearer ${token}`)
  if(options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json')
  const response = await fetch(path,{...options,headers})
  if(!response.ok){
    let message = `${response.status} ${response.statusText}`
    try{ const data = await response.json(); message = data.detail || data.error || message }catch{}
    throw new Error(message)
  }
  const ct = response.headers.get('content-type') || ''
  return ct.includes('application/json') ? response.json() : response
}

export const api = {
  async login(username:string,password:string){
    const body = new URLSearchParams({username,password})
    const r = await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body})
    if(!r.ok) throw new Error('Invalid username or password')
    const data = await r.json(); setToken(data.access_token); return data
  },
  logout(){ setToken(null) },
  me:()=>request('/api/me'),
  dashboard:()=>request('/api/dashboard'),
  records:(kind:string)=>request(`/api/qms/${kind}`),
  createRecord:(kind:string,payload:any)=>request(`/api/qms/${kind}`,{method:'POST',body:JSON.stringify(payload)}),
  documents:(q='')=>request(`/api/documents?q=${encodeURIComponent(q)}`),
  syncDocuments:()=>request('/api/documents/sync',{method:'POST'}),
  training:()=>request('/api/training/me'),
  acknowledge:(id:number,payload:any)=>request(`/api/training/${id}/acknowledge`,{method:'POST',body:JSON.stringify(payload)}),
  users:()=>request('/api/users'),
  createUser:(payload:any)=>request('/api/users',{method:'POST',body:JSON.stringify(payload)}),
  audit:()=>request('/api/audit')
}
