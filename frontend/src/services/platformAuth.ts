import './platformHttp'
import axios from 'axios'
export interface PlatformSession { authenticated: boolean; username?: string; role?: string; capabilities: string[] }
const options = { headers: { 'X-Finance-Local': '1' } }
export const getPlatformSession = async (): Promise<PlatformSession> => (await axios.get('/api/platform/session')).data
export const loginPlatform = async (username: string, password: string) => { await axios.post('/api/platform/login', { username, password }, options); return getPlatformSession() }
export const logoutPlatform = async () => { await axios.post('/api/platform/logout', {}, options) }
