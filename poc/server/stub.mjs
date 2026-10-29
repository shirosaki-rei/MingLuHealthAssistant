// 明路 PoC 桩服务（零依赖，Node 18+ 直接跑）
// 用途：验证三端流式对话、图片上传、OCR 回显，不接真实模型
// 启动：node server/stub.mjs
// 端口：8787

import http from 'node:http'
import { randomUUID } from 'node:crypto'

const PORT = 8787

// 模拟 AI 回复：故意包含句号、逗号，用来验证「按句缓冲」
const REPLY = [
  '先别急着下结论。',
  '头晕伴乏力五天，常见的方向有几类：',
  '一是血压波动，尤其是起床和转头时加重；',
  '二是贫血或甲状腺功能异常；',
  '三是睡眠不足与颈椎问题。',
  '这几类需要医生结合检查来判断，我没有资格替你下结论。',
  '你可以把最近的血压记录带去门诊，或者我先帮你把症状整理成一份病历。'
].join('')

const OCR_RESULT = {
  ok: true,
  items: [
    { name: '白细胞计数', value: '6.2', unit: '10^9/L', refLow: 3.5, refHigh: 9.5, flag: 'normal' },
    { name: '血红蛋白', value: '118', unit: 'g/L', refLow: 130, refHigh: 175, flag: 'low' },
    { name: '空腹血糖', value: '6.4', unit: 'mmol/L', refLow: 3.9, refHigh: 6.1, flag: 'high' }
  ],
  note: '本结果为 PoC 假数据，不用于任何真实判断'
}

function cors (res) {
  res.setHeader('Access-Control-Allow-Origin', '*')
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type')
}

function json (res, code, body) {
  cors(res)
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(body))
}

// SSE：逐字推送，约 60ms 一个字
function streamChat (res, delay = 60) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
    ...(res.getHeader('Access-Control-Allow-Origin') ? {} : { 'Access-Control-Allow-Origin': '*' })
  })

  const chars = Array.from(REPLY)
  let i = 0
  let closed = false

  const timer = setInterval(() => {
    if (closed) return
    if (i >= chars.length) {
      res.write('event: done\ndata: [DONE]\n\n')
      clearInterval(timer)
      res.end()
      return
    }
    // 每帧推 1~2 个字，模拟真实 token 到达
    const step = Math.random() > 0.7 ? 2 : 1
    const delta = chars.slice(i, i + step).join('')
    i += step
    res.write(`data: ${JSON.stringify({ delta })}\n\n`)
  }, delay)

  res.on('close', () => {
    closed = true
    clearInterval(timer)
  })
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)
  cors(res)

  if (req.method === 'OPTIONS') {
    res.writeHead(204)
    res.end()
    return
  }

  // 健康检查
  if (url.pathname === '/health') {
    return json(res, 200, { ok: true, ts: Date.now() })
  }

  // 流式对话
  if (url.pathname === '/api/chat/stream' && req.method === 'GET') {
    const delay = Number(url.searchParams.get('delay') || 60)
    return streamChat(res, delay)
  }

  // 图片上传（multipart，三端统一走 uni.uploadFile）
  if (url.pathname === '/api/upload-raw' && req.method === 'POST') {
    const chunks = []
    req.on('data', c => chunks.push(c))
    req.on('end', () => {
      const bytes = Buffer.concat(chunks).length
      json(res, 200, {
        ok: true,
        bytes,
        kb: +(bytes / 1024).toFixed(1),
        warn: bytes > 500 * 1024 ? '超过 500KB，需压缩' : null
      })
    })
    return
  }

  // 图片上传：收 base64，回显体积与 URL
  if (url.pathname === '/api/upload' && req.method === 'POST') {
    let body = ''
    req.on('data', c => { body += c })
    req.on('end', () => {
      try {
        const { dataUrl } = JSON.parse(body || '{}')
        const base64 = String(dataUrl || '').split(',')[1] || ''
        const bytes = Buffer.from(base64, 'base64').length
        const id = randomUUID()
        json(res, 200, {
          ok: true,
          id,
          bytes,
          kb: +(bytes / 1024).toFixed(1),
          url: `/mock/report/${id}.jpg`,
          warn: bytes > 500 * 1024 ? '超过 500KB，需压缩' : null
        })
      } catch (e) {
        json(res, 400, { ok: false, message: 'bad payload' })
      }
    })
    return
  }

  // OCR（假数据）
  if (url.pathname === '/api/ocr' && req.method === 'POST') {
    let body = ''
    req.on('data', c => { body += c })
    req.on('end', () => setTimeout(() => json(res, 200, OCR_RESULT), 600)) // 模拟 600ms 耗时
    return
  }

  json(res, 404, { ok: false, message: 'not found' })
})

server.listen(PORT, () => {
  console.log(`[stub] listening on http://localhost:${PORT}`)
  console.log('[stub] GET  /api/chat/stream?delay=60')
  console.log('[stub] POST /api/upload   { dataUrl }')
  console.log('[stub] POST /api/ocr      {}')
})
