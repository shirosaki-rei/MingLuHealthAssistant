// 统一错误格式化
// 原因：uvue 里 String(错误对象) 会得到 "[object Object]"，排查时完全看不到信息

export function formatError (e: any): string {
  if (e === null || e === undefined) return '未知错误'
  if (typeof e === 'string') return e

  const parts: string[] = []
  if (e.errCode !== undefined && e.errCode !== null) parts.push('errCode=' + e.errCode)
  if (e.statusCode !== undefined && e.statusCode !== null) parts.push('status=' + e.statusCode)
  if (e.errMsg) parts.push(String(e.errMsg))
  if (e.message) parts.push(String(e.message))
  if (e.msg) parts.push(String(e.msg))

  if (parts.length > 0) return parts.join(' | ')

  try {
    const s = JSON.stringify(e)
    return s && s !== '{}' ? s : String(e)
  } catch {
    return String(e)
  }
}
