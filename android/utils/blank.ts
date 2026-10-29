// 契约里的「可选字段」运行时可能是 null。
// openapi-typescript 生成的类型只标了 `?` 没标 `| null`（Patient.birth_date 就是），
// 所以 `x !== undefined` 挡不住 null，`null.trim()` / `null.length` 会直接把渲染打断，
// 表现是界面冻在旧状态、怎么点都没反应。统一在这里判断。

export function hasText (v: string | null | undefined): boolean {
	return v !== undefined && v !== null && v !== ''
}

export function hasList (v: string[] | null | undefined): boolean {
	return v !== undefined && v !== null && v.length > 0
}

export function hasNum (v: number | null | undefined): boolean {
	return v !== undefined && v !== null
}
