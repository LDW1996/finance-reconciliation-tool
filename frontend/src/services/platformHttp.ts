import axios, { type AxiosInstance } from 'axios'

// Install before business modules construct their own clients.
function configure(client: AxiosInstance) {
  client.interceptors.request.use(config => {
    config.headers.set('X-Finance-Local', '1')
    config.headers.set('X-Platform-Language', localStorage.getItem('finance-platform-language') === 'en' ? 'en' : 'zh-CN')
    return config
  })
  client.interceptors.response.use(response => response, error => {
    const url = `${error.config?.baseURL || ''}${error.config?.url || ''}`
    if (error.response?.status === 401 && !url.includes('/api/platform/')) {
      window.dispatchEvent(new Event('platform-session-expired'))
    }
    return Promise.reject(error)
  })
  return client
}
configure(axios)
const createClient = axios.create.bind(axios)
axios.create = config => configure(createClient(config))
