import assert from 'node:assert/strict'
import axios from 'axios'
// Exercise the real service against an adapter: a stale local service may return
// the SPA HTML fallback for a newly introduced API route.
axios.defaults.adapter=async config=>({data:'<!doctype html><html></html>',status:200,statusText:'OK',headers:{'content-type':'text/html'},config})
const {mailMatchingApi}=await import('../../src/services/mailMatching.ts')
await assert.rejects(mailMatchingApi.snapshot({scope:'all',page:1,page_size:50,minimum_score:0,day_window:3,candidate_limit:3}),/重启程序/)
await assert.rejects(mailMatchingApi.matchState(3),/重启程序/)
console.log('Matching transport rejects an outdated service without treating HTML as saved results')
